# -*- coding: utf-8 -*-
"""
04_beacon_field_decode.py -- map one frame's decoded Info-field bits onto the
beacon's actual telemetry field layout (`SCIONX_TLMnew.xlsx`), and write a
per-field, per-bit spreadsheet that highlights the low-confidence bits.

Why: `02_zero_run_baseline/` and `03_data_segment_processing/` each produce a
decision (0/1) for every symbol in the zero-run / data segments, but neither
folder ever shows what those bits actually *mean* -- the Info field
(payload byte [16,272), 256 bytes / 2048 bits) is telemetry, and
`04_Beacon/SCIONX_TLMnew.xlsx` + `SCIONX_enums.json` are the field-layout /
enum reference the user supplied for it (ItemName/DataType/BitLen/OffsetBit/
Endian, plus enum tables and unit/scale transforms). This script is the first
thing in the repo that actually decodes a frame against that layout.

Decision rule per bit (one consistent bitstream, no domain-mixing):
  - HDLC destuffing/frame structure is always derived from raw y at a plain
    threshold=0 (the same "bypass method" 03b/03c use to get clean alignment;
    see CLAUDE.md/03's README). This fixes which samples are data vs. stuffed
    bits and gives each surviving bit its original sample index.
  - header (address/control/PID) + data1/data2 + FCS: re-decided using this
    frame's own header-calibrated fixed offset threshold theta*, exactly like
    `03b_data_fixed_offset_threshold.py` / `03c_symbol_sync_timing.py`. theta*
    was calibrated *from* the header, so applying it there is the same decision
    rule the reported header error count already reflects; FCS rides along with
    the data segments per GNURADIO_MIGRATION.md.
  - zero-run1/zero-run2 bits (should be all-0x00 padding): re-decided from
    the baseline-restored `yc` at a plain threshold=0 -- this *is*
    02_zero_run_baseline's "Method 1" (static threshold), used exactly as
    that folder runs it. (Raw y was tried here first and gave ~42% 1s in the
    zero-runs -- baseline restoration is load-bearing for this segment type,
    unlike data1/data2 where 03's README found raw y gives the cleanest
    header alignment. So structure/destuffing still comes from the raw-y
    threshold=0 pass -- see 03's "bypass method" -- but the reported bit
    *value* in zero-run positions is independently re-sampled from yc, the
    same way data-segment bits are independently re-sampled at theta*.)
    Method 2 (sample-to-sample diff) has no ground-truth threshold of its
    own (02's README), so it isn't used here.

"Ambiguous / red" flag (what gets colored red in the Bits column):
  - data-segment bits: |y[sample] - theta*| <= MARGIN_FRAC*A (03b/03c's own
    "near decision line" criterion).
  - zero-run-segment bits: the bit decided as 1 (the padding should be all
    0x00, so any 1 here is exactly the "isolated 1" 02_zero_run_baseline
    flags as suspicious) -- per the user's explicit request, ALL such 1s are
    marked, not just Method 1's already-small set.
  - address/control+PID/FCS bits are outside the Info field and are not part
    of this output at all.

Field decode is necessarily best-effort: `SCIONX_TLMnew.xlsx`'s ItemName
strings and `SCIONX_enums.json`'s lookup keys were authored somewhat
independently, so name matching (see `resolve_lookup`) succeeds for many
fields (nameRules regexes were clearly written to match `ItemName` with
spaces turned into underscores) but not all. Where nothing matches, the
field's own `LongDescription` column (verbatim) is used as the lookup
column instead -- it already IS the field's own cross-reference table for
~96/205 fields in this sheet, and is authoritative where present.

Usage: `python 04_beacon_field_decode.py [audio.ogg] [--frame N] [--z-threshold Z]`.
With no arguments, decodes every frame `detect_frame_starts` finds in
`../Data/cut_first3.ogg` (frame detection is the same normalized
cross-correlation method 01/02/03 use -- frame numbering is purely by sample
position, earliest-in-the-recording = frame#1, and is not hardcoded to this
one file: point it at any other recording of this satellite and it detects
however many frames pass `Z_THRESHOLD`). `--frame N` restricts the run to a
single 1-based frame index instead of decoding all of them. `--z-threshold`
overrides `Z_THRESHOLD=12.0`, which was only ever calibrated on
`cut_first3.ogg` -- for any other recording, run
`04a_zscore_visualization.py` on it first (it prints/plots the noise
ceiling and the margins to a candidate threshold) before trusting the
default here.

Output: `Output/<audio stem>_frame<N>_beacon_decode.xlsx` per decoded frame
(one row per beacon field, in OffsetBit order) -- GNU-Radio-free, needs only
numpy/soundfile/openpyxl.
"""

import argparse
import os
import re
import sys
import json

import numpy as np
import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Font, PatternFill, Alignment

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402
from scionx.hdlc import crc16_x25                    # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS, SPS = 48000, 5
PHASE = 2
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")
XLSX_PATH = os.path.join(HERE, "SCIONX_TLMnew.xlsx")
ENUMS_PATH = os.path.join(HERE, "SCIONX_enums.json")
OUT_DIR = os.path.join(HERE, "Output")

FLAGS4 = bytes([0x7E] * 4)
HEADER_ADDR = bytes.fromhex("849c6086aa4060849c60a686b0e1")   # Dest+Src address, 14 bytes

Z_THRESHOLD = 12.0
MIN_FRAME_GAP = 100_000
SEARCH_SAMPLES = 19211    # header through the trailing flags, same window 01/02/03 use
MARGIN_FRAC = 0.10        # "near decision line" half-band, fraction of A=median(|y|) -- same as 03

INFO_BYTE0 = 16           # Info field starts at payload byte 16 (after Dest+Src+Control+PID)
INFO_NBYTES = 256         # REFERENCE_FRAME.md: Info = 0x10-0x10F = 256 bytes = 2048 bits (matches xlsx's max OffsetBit+BitLen)

REF_ROWS = (
    "849c6086aa4060849c60a686b0e103f0"
    "007c083c81336a000201000000000020"
    "0000002b81336a000000000000030200"
    "0044090020100000c3add00099bf4e04"
    "8bc5780561d7f608d4a11f0000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000826"
    "08260826282620262000200020002000"
    "2000a03d78ff770f9bff8a1f161515e7"
    "b0b0b0b019181ae80c4002481b180000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
)
REF = bytes.fromhex(REF_ROWS)


# ============================================================================
# Signal-side helpers, duplicated from 03b/03c (this repo's convention: every
# script is self-contained and independently runnable, see CLAUDE.md)
# ============================================================================

def bits_lsb_first(data):
    out = []
    for b in data:
        for k in range(8):
            out.append((b >> k) & 1)
    return out


def build_header_template():
    bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.float64)
    nrz = 2 * bits - 1
    return np.repeat(nrz, SPS)


def detect_frame_starts(yc, template):
    L = template.size
    corr = np.correlate(yc, template, mode="valid")
    csum2 = np.concatenate(([0.0], np.cumsum(yc.astype(np.float64) ** 2)))
    window_energy = csum2[L:] - csum2[:-L]
    template_norm = np.sqrt(np.sum(template ** 2))
    R = corr / (template_norm * np.sqrt(window_energy) + 1e-12)

    z = R / np.std(R)
    candidates = np.where(z > Z_THRESHOLD)[0]
    candidates = candidates[np.argsort(-z[candidates])]

    starts = []
    for idx in candidates:
        if all(abs(int(idx) - s) > MIN_FRAME_GAP for s in starts):
            starts.append(int(idx))
    order = np.argsort(starts)
    starts = [starts[i] for i in order]
    zs = [float(z[s]) for s in starts]
    return starts, zs


def find_zero_runs(buf, min_len=8):
    runs, i = [], 0
    while i < len(buf):
        if buf[i] == 0:
            j = i
            while j < len(buf) and buf[j] == 0:
                j += 1
            if j - i >= min_len:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def destuff_with_map(bits, raw_idx):
    """HDLC bit destuffing, keeping each surviving bit's original sample index.

    Also returns `dropped_at`: for every stuffed 0 removed, the number of output
    bits emitted before it -- i.e. the position, in payload-bit coordinates,
    where that removal happened. `check_frame_alignment` uses it to locate where
    a mis-destuffed bit first threw the byte alignment off."""
    out_bits, out_idx, dropped_at = [], [], []
    run = 0
    for b, si in zip(bits, raw_idx):
        if run == 5:
            if b == 0:
                run = 0
                dropped_at.append(len(out_bits))
                continue
            run = 0
        out_bits.append(b)
        out_idx.append(si)
        run = run + 1 if b == 1 else 0
    return np.array(out_bits, dtype=np.uint8), np.array(out_idx), dropped_at


def bits_to_bytes(bits):
    n = bits.size // 8
    out = bytearray()
    for i in range(n):
        v = 0
        for k in range(8):
            v |= int(bits[i * 8 + k]) << k
        out.append(v)
    return bytes(out)


def build_segment_map():
    """Byte-range map (address/data1/zero-run1/data2/zero-run2/FCS), derived
    from REFERENCE_FRAME.md's known zero-runs -- identical logic to 03b/03c."""
    zero_runs = find_zero_runs(REF)
    segments = [("address", 0, 14)]
    prev = 14
    seg_id = 1
    for (z0, z1) in zero_runs:
        if z0 > prev:
            segments.append((f"data{seg_id}", prev, z0))
        segments.append((f"zero-run{seg_id}", z0, z1))
        prev = z1
        seg_id += 1
    if prev < 272:
        segments.append((f"data{seg_id}", prev, 272))
    segments.append(("FCS", 272, 274))
    return segments


FLAG_BITS = [0, 1, 1, 1, 1, 1, 1, 0]     # 0x7E as it appears on the wire
REF_N_STUFFED = 8                        # stuffed 0s the reference frame needs (see 05_gnuradio_test/)
FRAME_END_BIT = 274 * 8                  # 272-byte payload + 2-byte FCS
TRAIL_BYTES = 16                         # bytes of trailing-flag region to decode and show


def find_flag_run(bits, lo, hi, min_run=3):
    """First position p in [lo,hi) that starts a run of `min_run` back-to-back
    0x7E flags -- i.e. the start of the trailing idle-flag sequence. Requiring a
    *run* (not a single match) matters: an isolated 01111110 can occur by chance
    inside the payload, but flags spaced exactly 8 bits apart cannot."""
    seq = bits.tolist() if hasattr(bits, "tolist") else list(bits)
    n = len(seq)
    for p in range(max(0, lo), min(hi, n - 8 * min_run + 1)):
        if all(seq[p + 8 * k: p + 8 * k + 8] == FLAG_BITS for k in range(min_run)):
            return p
    return None


def trailing_expected_pattern(align):
    """The 8-bit pattern a trailing flag byte should show at payload bit
    FRAME_END_BIT, given the detected slip. If the frame ends `slip` bits away
    from where it should, the flags sit at phase (-slip) mod 8 relative to the
    byte grid, so the expected byte is 0x7E rotated by that much. Falls back to
    the unrotated flag when no slip could be measured."""
    slip = align.get("slip_bits")
    phase = (-slip) % 8 if slip is not None else 0
    rot = FLAG_BITS[phase:] + FLAG_BITS[:phase]
    return "".join(map(str, rot))


def check_frame_alignment(body_bits_full, dropped_at, segments):
    """Independent check that the destuffed stream is cut *exactly* at the frame
    boundary -- i.e. that the 5-consecutive-1s destuffing consumed neither too
    many nor too few bits on the way through the frame.

    The anchor is structural, not CRC-based: a valid HDLC frame is immediately
    followed by the closing flag, so bit FRAME_END_BIT of the destuffed stream
    must be the start of a 0x7E run. If that run starts early or late, the
    difference is exactly the net number of bits the destuffer got wrong, and
    every byte boundary after the slip point is shifted.

    Locating *where* it went wrong uses the one region with known content: the
    zero-run padding is all 0x00, and a stuffed 0 can only ever follow five
    consecutive 1s -- which cannot occur inside all-zero padding. So any removal
    landing inside a zero-run is provably spurious (a bit error faked a 5-ones
    run), and the first one is the first detectable slip. A slip inside a data
    segment can't be localized this way, since there's no ground truth there --
    reported honestly as "not localizable" rather than guessed at."""
    n_avail = body_bits_full.size

    closing_ok, flag_run_start, slip_bits = None, None, None
    if n_avail >= FRAME_END_BIT + 8:
        closing_ok = body_bits_full[FRAME_END_BIT:FRAME_END_BIT + 8].tolist() == FLAG_BITS
        flag_run_start = find_flag_run(body_bits_full, FRAME_END_BIT - 48, FRAME_END_BIT + 128)
        if flag_run_start is not None:
            slip_bits = flag_run_start - FRAME_END_BIT

    # zero-run byte ranges, where a stuffed bit is impossible by construction.
    # Skip the first 8 bits of each: a genuine 5-ones run can straddle in from
    # the end of the preceding data segment.
    zero_bit_ranges = [(b0 * 8 + 8, b1 * 8) for lbl, b0, b1 in segments
                       if lbl.startswith("zero-run")]
    spurious = [p for p in dropped_at
                if any(lo <= p < hi for lo, hi in zero_bit_ranges)]

    first_slip_bit = spurious[0] if spurious else None
    return {
        "n_stuffed_removed": len(dropped_at),
        "ref_n_stuffed": REF_N_STUFFED,
        "closing_flag_ok": closing_ok,
        "flag_run_start_bit": flag_run_start,
        "slip_bits": slip_bits,
        "n_spurious_removals": len(spurious),
        "first_slip_bit": first_slip_bit,
        "first_slip_byte": (first_slip_bit // 8) if first_slip_bit is not None else None,
        "aligned": bool(closing_ok) and slip_bits == 0,
    }


def calibrate_offset_threshold(y, start):
    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    idx_hdr = start + SPS * np.arange(0, 144) + PHASE
    y_hdr = y[idx_hdr]
    v1, v0 = y_hdr[t_bits == 1], y_hdr[t_bits == 0]
    grid = np.linspace(min(v0.min(), v1.min()), max(v0.max(), v1.max()), 2000)
    errs = [(v1 <= t).sum() + (v0 > t).sum() for t in grid]
    thr = grid[int(np.argmin(errs))]
    return float(thr), int(min(errs))


def decode_frame(y, yc, start):
    """Destuff this frame (raw y, threshold=0, for structure), then produce
    per-bit (decided_bit, is_ambiguous, segment_label, sample_index) arrays
    covering payload+FCS (up to 274*8 bits) -- see module docstring for the
    per-segment decision/ambiguous rules."""
    offset_thr, hdr_err = calibrate_offset_threshold(y, start)
    A = float(np.median(np.abs(y)))
    margin = MARGIN_FRAC * A

    span_j1 = int(SEARCH_SAMPLES / SPS)
    idx_all = start + SPS * np.arange(0, span_j1) + PHASE
    idx_all = idx_all[idx_all < y.size]
    body_bits_raw = (y[idx_all[32:]] > 0).astype(np.uint8)     # skip the 4 leading flags; threshold=0 structure pass
    body_idx_raw = idx_all[32:]
    body_bits_full, body_idx_full, dropped_at = destuff_with_map(body_bits_raw, body_idx_raw)

    segments = build_segment_map()
    # Alignment check needs the stream *past* the frame end (that's where the
    # closing flag lives), so run it before truncating to the frame.
    align = check_frame_alignment(body_bits_full, dropped_at, segments)

    # Keep TRAIL_BYTES past the frame end as well: the trailing flags are known
    # content (0x7E repeated), so unlike data1/data2 they can be eyeballed
    # against a correct answer. Bit stuffing never touches a flag (the 6th
    # consecutive 1 resets the run instead of inserting a 0), so what the
    # destuffer emits here is the raw decided bits, untouched.
    n_bits = min(body_bits_full.size, FRAME_END_BIT + TRAIL_BYTES * 8)
    body_bits = body_bits_full[:n_bits]
    body_sample_idx = body_idx_full[:n_bits]

    seg_label = np.empty(n_bits, dtype=object)
    for label, b0, b1 in segments:
        bit0, bit1 = b0 * 8, min(b1 * 8, n_bits)
        if bit0 < bit1:
            seg_label[bit0:bit1] = label
    if n_bits > FRAME_END_BIT:
        seg_label[FRAME_END_BIT:n_bits] = "trailing-flag"

    is_zero_run = np.array([lbl is not None and lbl.startswith("zero-run") for lbl in seg_label])
    is_theta = ~is_zero_run    # address + data1/data2 + FCS: same theta*-decision domain
                               # (address/FCS get theta* too, not just threshold=0 -- theta* was
                               # calibrated *from* the header, so this is the same decision rule
                               # the header_err=0/144 count above already reflects; FCS rides
                               # along with the data segments per GNURADIO_MIGRATION.md)

    y_at_bit = y[body_sample_idx]
    yc_at_bit = yc[body_sample_idx]
    decided_bit = body_bits.copy()
    decided_bit[is_theta] = (y_at_bit[is_theta] > offset_thr).astype(np.uint8)
    decided_bit[is_zero_run] = (yc_at_bit[is_zero_run] > 0).astype(np.uint8)   # 02's Method 1, on yc

    ambiguous = np.zeros(n_bits, dtype=bool)
    ambiguous[is_theta] = np.abs(y_at_bit[is_theta] - offset_thr) <= margin
    ambiguous[is_zero_run] = decided_bit[is_zero_run] == 1

    # informational CRC checks (not required for the field decode itself,
    # but cheap and consistent with 03b/03c's own reporting)
    crc_baseline = crc_mixed = None
    if n_bits >= 274 * 8:
        fcs_bytes = bits_to_bytes(body_bits[272 * 8:274 * 8])
        crc_baseline = (crc16_x25(bits_to_bytes(body_bits[:272 * 8])).to_bytes(2, "little") == fcs_bytes)
        crc_mixed = (crc16_x25(bits_to_bytes(decided_bit[:272 * 8])).to_bytes(2, "little") == fcs_bytes)

    seg_counts = {}
    for lbl in ("address", "data1", "zero-run1", "data2", "zero-run2", "FCS"):
        mask = seg_label == lbl
        seg_counts[lbl] = (int(ambiguous[mask].sum()), int(mask.sum()))

    return {
        "decided_bit": decided_bit, "ambiguous": ambiguous, "seg_label": seg_label,
        "offset_thr": offset_thr, "hdr_err": hdr_err, "A": A, "margin": margin,
        "crc_baseline": crc_baseline, "crc_mixed": crc_mixed, "n_bits": n_bits,
        "seg_counts": seg_counts, "align": align,
        "bits_thr0": body_bits,          # the plain threshold=0 pass, kept for comparison
    }


# ============================================================================
# Beacon field layout (xlsx) + enum/transform lookup (json)
# ============================================================================

def load_beacon_fields(xlsx_path):
    """Read SCIONX_TLMnew.xlsx -> list of field dicts, in sheet (=OffsetBit)
    order. Subsystem is forward-filled (it's only populated on each group's
    first row in the sheet). Rows with no ItemName are skipped."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else "" for h in rows[0]]
    col = {name: i for i, name in enumerate(header)}

    fields = []
    subsystem = ""
    for r in rows[1:]:
        item_name = r[col["ItemName"]]
        if item_name is None:
            continue
        if r[col["Subsystem"]]:
            subsystem = str(r[col["Subsystem"]]).strip()
        fields.append({
            "subsystem": subsystem,
            "item_name": str(item_name).strip(),
            "dtype": (r[col["DataType"]] or "").strip(),
            "bitlen": int(r[col["BitLen"]]),
            "offsetbit": int(r[col["OffsetBit"]]),
            "endian": (r[col["Endian"]] or "BE").strip().upper(),
            "longdesc": (str(r[col["LongDescription"]]).strip() if r[col["LongDescription"]] else ""),
        })
    fields.sort(key=lambda f: f["offsetbit"])
    return fields


def normalize_name(name):
    return re.sub(r"\s+", "_", name.strip())


def split_top_level(s, sep):
    """Split on `sep`, but only outside of any (...) parentheses -- needed
    because several LongDescription enum tables embed the separator inside a
    parenthetical example, e.g. '1: nominal state(charging, COMM, SCI), 0: ...'."""
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def parse_enum_from_longdesc(longdesc):
    """Best-effort parse of a LongDescription enum table into {int: label}.
    Handles the three patterns observed in SCIONX_TLMnew.xlsx:
      '0:off/1:on'                              (slash-separated)
      '1: label(...), 0: label(...)'             (comma-separated, top-level only)
      'header:\\n0 - label\\n1 - label\\n...'     (newline-separated, hex keys A/B/FF)
    Returns {} if nothing matches (caller falls back to showing the raw value)."""
    if not longdesc:
        return {}
    lines = [ln for ln in longdesc.split("\n") if ln.strip()]
    chunks = []
    if len(lines) > 1:
        chunks = lines
    elif "/" in longdesc and longdesc.count(":") >= 2:
        chunks = longdesc.split("/")
    else:
        chunks = split_top_level(longdesc, ",")

    out = {}
    pat = re.compile(r"^\s*([0-9A-Fa-f]{1,4})\s*[:\-]\s*(.+?)\s*$")
    for chunk in chunks:
        m = pat.match(chunk)
        if not m:
            continue
        key_str, label = m.group(1), m.group(2)
        try:
            key = int(key_str, 16) if re.search(r"[A-Fa-f]", key_str) else int(key_str, 10)
        except ValueError:
            continue
        out[key] = label
    return out


def resolve_lookup(item_name, longdesc, bitlen, enums_ctx):
    """Return (parsed_enum: {int:label} or None, transform: dict or None,
    lookup_text: str for the last column)."""
    norm = normalize_name(item_name)
    norm_lower = norm.lower()

    parsed = parse_enum_from_longdesc(longdesc)
    if parsed:
        return parsed, None, longdesc

    for rule in enums_ctx["nameRules"]:
        if re.match(rule["pattern"], norm):
            if "transform" in rule and rule["transform"] in enums_ctx["transforms"]:
                tr = enums_ctx["transforms"][rule["transform"]]
                return None, tr, f"transform:{rule['transform']} {tr}"
            if "enum" in rule:
                enum_name = rule["enum"]
                table = enums_ctx["enums"].get(enum_name) or enums_ctx["enums"].get(norm)
                if table:
                    table_int = {int(k, 16) if re.search(r"[A-Fa-f]", k) else int(k): v
                                 for k, v in table.items()}
                    return table_int, None, ", ".join(f"{k}={v}" for k, v in table.items())

    for key, table in enums_ctx["enums"].items():
        if key.lower() == norm_lower:
            table_int = {int(k, 16) if re.search(r"[A-Fa-f]", k) else int(k): v
                         for k, v in table.items()}
            return table_int, None, ", ".join(f"{k}={v}" for k, v in table.items())

    for key, tr in enums_ctx["transforms"].items():
        if key.lower() == norm_lower:
            return None, tr, f"transform:{key} {tr}"

    if bitlen == 1:
        default = {0: "OFF", 1: "ON"}
        return default, None, "(assumed 0=OFF/1=ON, no field-specific description)"

    return None, None, "-"


def decode_raw_value(bits_str, dtype, bitlen, endian):
    """bits_str: MSB-first string of '0'/'1', length == bitlen (tight,
    CCSDS-style packing, matching the xlsx's cumulative OffsetBit column)."""
    if bitlen % 8 == 0 and bitlen > 8 and endian == "LE":
        byte_chunks = [bits_str[i:i + 8] for i in range(0, bitlen, 8)]
        bits_str = "".join(reversed(byte_chunks))

    raw_unsigned = int(bits_str, 2)

    if dtype == "float" and bitlen == 32:
        import struct
        as_bytes = raw_unsigned.to_bytes(4, "big")
        return struct.unpack(">f", as_bytes)[0], raw_unsigned

    if dtype.startswith("int") and (raw_unsigned & (1 << (bitlen - 1))):
        return raw_unsigned - (1 << bitlen), raw_unsigned

    return raw_unsigned, raw_unsigned


def format_readable(value, raw_unsigned, parsed_enum, transform):
    if parsed_enum is not None:
        return str(parsed_enum.get(raw_unsigned, f"{raw_unsigned} (unlisted)"))
    if transform is not None:
        scale = transform.get("scale")
        unit = transform.get("unit", "")
        v = value * scale if scale is not None else value
        if isinstance(v, float):
            return f"{v:.4g} {unit}".strip()
        return f"{v} {unit}".strip()
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


# ============================================================================
# Output
# ============================================================================

RED = InlineFont(color="FFCC0000")
HEADER_FILL = PatternFill("solid", fgColor="FFDCE9F5")   # column-header row (row 2), not to be confused with SEGMENT_FILLS["header"]

# Light/pastel background per segment type, so the source (header / data /
# zero-run / FCS) is visible at a glance without reading the ItemName column.
SEGMENT_FILLS = {
    "header":    PatternFill("solid", fgColor="FFDCEAF7"),   # light blue  -- Dest/Src address, Control, PID (known/fixed)
    "data1":     PatternFill("solid", fgColor="FFFDEBD3"),   # light peach -- telemetry
    "zero-run1": PatternFill("solid", fgColor="FFECECEC"),   # light gray  -- expected all-0x00 padding
    "data2":     PatternFill("solid", fgColor="FFFBE0E0"),   # light pink  -- telemetry
    "zero-run2": PatternFill("solid", fgColor="FFE1E1E1"),   # slightly darker light gray -- expected all-0x00 padding
    "FCS":       PatternFill("solid", fgColor="FFE6DFF7"),   # light purple -- 2-byte CRC-16/X.25
    "trailing-flag": PatternFill("solid", fgColor="FFDFF3E6"),  # light green -- 0x7E idle flags after the frame
    "mixed":     PatternFill("solid", fgColor="FFFFF3B0"),   # light yellow -- field straddles a segment boundary
}


def bits_richtext(bits_str, ambig_slice):
    """0/1 string -> CellRichText with ambiguous positions in red, everything
    else left as plain (black) text -- built as runs, not per-character, to
    keep the .xlsx small."""
    runs, cur_color, cur_text = [], None, ""
    for ch, is_red in zip(bits_str, ambig_slice):
        color = "red" if is_red else "black"
        if color != cur_color and cur_text:
            runs.append((cur_color, cur_text))
            cur_text = ""
        cur_color, cur_text = color, cur_text + ch
    if cur_text:
        runs.append((cur_color, cur_text))
    parts = [TextBlock(RED, t) if c == "red" else t for c, t in runs]
    return CellRichText(*parts) if parts else CellRichText("")


def append_row(ws, cells, bits_str, ambig_slice, fill):
    ws.append(cells)
    r = ws.max_row
    ws.cell(r, 5).value = bits_richtext(bits_str, ambig_slice)
    ws.cell(r, 5).font = Font(name="Consolas")
    ws.cell(r, 7).alignment = Alignment(wrap_text=True, vertical="top")
    for c in range(1, len(cells) + 1):
        ws.cell(r, c).fill = fill
    return r


def segment_fill_for_range(seg_label_slice):
    """seg_label_slice: the seg_label array (payload-bit domain) restricted
    to one field's [b0,b1) range. Two fields in this xlsx straddle a segment
    boundary (CMD Loss Timer over data1/zero-run1, EPS UHF7V Current over
    data2/zero-run2) -- those get the "mixed" fill instead of picking one
    segment arbitrarily."""
    labels = sorted(set(lbl for lbl in seg_label_slice if lbl is not None))
    if len(labels) == 1:
        return SEGMENT_FILLS.get(labels[0], SEGMENT_FILLS["mixed"]), labels[0]
    return SEGMENT_FILLS["mixed"], "+".join(labels)


def write_workbook(out_path, frame_no, start, z, decode, fields, enums_ctx, audio_path):
    decided_bit = decode["decided_bit"]
    ambiguous = decode["ambiguous"]
    seg_label = decode["seg_label"]
    n_total = decided_bit.size

    info_bits = decided_bit[INFO_BYTE0 * 8: (INFO_BYTE0 + INFO_NBYTES) * 8]
    info_ambig = ambiguous[INFO_BYTE0 * 8: (INFO_BYTE0 + INFO_NBYTES) * 8]
    info_seg = seg_label[INFO_BYTE0 * 8: (INFO_BYTE0 + INFO_NBYTES) * 8]
    n_avail = info_bits.size

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Frame{frame_no}_Beacon"

    align = decode["align"]
    # Info-field bit offset of the first detected slip (the table is indexed from
    # the Info field, the alignment check from the payload), or None.
    slip_info_bit = None
    if align["first_slip_bit"] is not None:
        slip_info_bit = align["first_slip_bit"] - INFO_BYTE0 * 8

    headers = ["Subsystem", "ItemName", "OffsetBit", "BitLen", "Bits", "ReadableValue", "LookupTable"]
    legend = ("Red text = data/header/FCS: near decision line (|y-theta*|<=" + f"{decode['margin']:.3f}"
              ") | zero-run: decided-as-1 (should be 0x00).  "
              "Row shading = segment: blue=header(Dest/Src/Control/PID) peach=data1 pink=data2 "
              "gray=zero-run1/2 purple=FCS green=trailing 0x7E flags yellow=straddles a segment "
              "boundary.")
    ws.append([legend])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    ws.cell(1, 1).font = Font(italic=True, size=9)

    # Alignment banner -- if the frame isn't cut exactly at the closing flag, say
    # so loudly at the top, because it means byte boundaries drift mid-frame.
    if align["aligned"]:
        banner = (f"ALIGNMENT OK: closing flag 0x7E starts exactly at payload bit {FRAME_END_BIT} "
                  f"(byte 274) -- destuffing consumed exactly the right bits, byte boundaries "
                  f"below are trustworthy.")
        banner_font = Font(bold=True, size=9, color="FF006600")
    else:
        slip = align["slip_bits"]
        where = (f"first detectable slip at payload byte {align['first_slip_byte']}"
                 if align["first_slip_byte"] is not None
                 else "slip not localizable (it falls inside a data segment, where there is no "
                      "known-content anchor to detect it)")
        banner = (f"ALIGNMENT FAILED: closing flag 0x7E is {slip:+d} bits from where it should be "
                  f"(payload bit {FRAME_END_BIT}) -- destuffing lost/gained bits mid-frame, so byte "
                  f"boundaries DRIFT from that point on and rows past it are shifted. {where}.")
        banner_font = Font(bold=True, size=9, color="FFCC0000")
    ws.append([banner])
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    ws.cell(2, 1).font = banner_font

    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(3, c)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
    ws.freeze_panes = "A4"

    # ---- Header block (Dest/Src address, Control, PID -- payload byte[0,16), all known/fixed) ----
    # Shown so APID's own starting point (Info bit 0 = payload byte 16) can be checked against
    # this frame's actual decoded header, instead of just assuming nothing is missing in front of it.
    dest_bits = decided_bit[0:56]
    src_bits = decided_bit[56:112]
    ctrl_bits = decided_bit[112:120]
    pid_bits = decided_bit[120:128]

    def ascii_shift1(bits56):
        """First 6 bytes = callsign (>>1 each = ASCII); 7th byte = SSID
        (separate field, not part of the callsign text)."""
        by = bits_to_bytes(bits56)
        callsign = "".join(chr(b >> 1) if 0x20 <= (b >> 1) < 0x7F else "?" for b in by[:6])
        return f"'{callsign}' (SSID byte=0x{by[6]:02X})"

    append_row(ws, ["HEADER", "Dest Address", 0, 56, None,
                    ascii_shift1(dest_bits), "expect 'BN0CU ' (>>1 of each byte, ground-test reference)"],
               "".join(str(int(b)) for b in dest_bits), ambiguous[0:56], SEGMENT_FILLS["header"])
    append_row(ws, ["HEADER", "Src Address", 56, 56, None,
                    ascii_shift1(src_bits), "expect 'BN0SCX' (>>1 of each byte, ground-test reference)"],
               "".join(str(int(b)) for b in src_bits), ambiguous[56:112], SEGMENT_FILLS["header"])
    ctrl_byte = bits_to_bytes(ctrl_bits)[0]
    append_row(ws, ["HEADER", "Control", 112, 8, None, f"0x{ctrl_byte:02X}", "expect 0x03 (UI frame)"],
               "".join(str(int(b)) for b in ctrl_bits), ambiguous[112:120], SEGMENT_FILLS["header"])
    pid_byte = bits_to_bytes(pid_bits)[0]
    append_row(ws, ["HEADER", "PID", 120, 8, None, f"0x{pid_byte:02X}", "expect 0xF0 (no layer-3)"],
               "".join(str(int(b)) for b in pid_bits), ambiguous[120:128], SEGMENT_FILLS["header"])

    # ---- Info field, one row per beacon field (OffsetBit order) ----
    n_missing = 0
    slip_marked = False          # only the first field at/after the slip gets the marker
    slip_row, slip_field_name = None, None
    for f in fields:
        b0, b1 = f["offsetbit"], f["offsetbit"] + f["bitlen"]
        fill, seg_name = segment_fill_for_range(info_seg[b0:min(b1, n_avail)]) if b0 < n_avail \
            else (SEGMENT_FILLS["mixed"], "?")
        if b1 > n_avail:
            n_missing += 1
            bits_str = "?" * f["bitlen"]
            ambig_slice = np.zeros(f["bitlen"], dtype=bool)
            readable = "(no symbols recovered this far)"
            lookup_text = "-"
        else:
            bit_slice = info_bits[b0:b1]
            ambig_slice = info_ambig[b0:b1]
            bits_str = "".join(str(int(b)) for b in bit_slice)
            value, raw_unsigned = decode_raw_value(bits_str, f["dtype"], f["bitlen"], f["endian"])
            parsed_enum, transform, lookup_text = resolve_lookup(
                f["item_name"], f["longdesc"], f["bitlen"], enums_ctx)
            readable = format_readable(value, raw_unsigned, parsed_enum, transform)

        item_display = "(reserved)" if f["item_name"] == "NaN" else f["item_name"]

        # Mark the first field at or after the detected slip: everything from
        # here down sits on shifted byte boundaries.
        if slip_info_bit is not None and not slip_marked and b1 > slip_info_bit:
            lookup_text = (f"<<< BYTE ALIGNMENT SLIPS AT OR BEFORE THIS FIELD "
                           f"(payload byte {align['first_slip_byte']}) -- rows from here down are shifted >>> "
                           + lookup_text)
            slip_marked = True
            slip_field_name = item_display

        r = append_row(ws, [f["subsystem"], item_display, f["offsetbit"], f["bitlen"], None,
                            readable, lookup_text], bits_str, ambig_slice, fill)
        if slip_marked and slip_row is None:
            slip_row = r
            ws.cell(r, 7).font = Font(bold=True, color="FFCC0000")

    # ---- FCS (2-byte CRC-16/X.25, appended after the 272-byte payload) ----
    fcs_bits = decided_bit[272 * 8:274 * 8]
    fcs_ambig = ambiguous[272 * 8:274 * 8]
    fcs_bytes = bits_to_bytes(fcs_bits)
    transmitted_crc = int.from_bytes(fcs_bytes, "little")
    expected_crc = crc16_x25(bits_to_bytes(decided_bit[:272 * 8]))
    crc_note = (f"computed CRC16/X25(payload)=0x{expected_crc:04X} vs transmitted=0x{transmitted_crc:04X} "
                f"-> {'MATCH' if expected_crc == transmitted_crc else 'MISMATCH'}")
    append_row(ws, ["FCS", "FCS (CRC-16/X.25, little-endian)", 272 * 8, 16, None,
                    f"0x{transmitted_crc:04X}", crc_note],
               "".join(str(int(b)) for b in fcs_bits), fcs_ambig, SEGMENT_FILLS["FCS"])

    # ---- Trailing flags (known content: 0x7E repeated) ----
    # Decoded with exactly the same rule as FCS/data (theta*, same red band), but
    # unlike data1/data2 the correct answer IS known here -- which makes this the
    # only ground truth at the *end* of the frame (the header is ground truth at
    # the start). The expectation is the flag rotated by the detected slip, not a
    # naive 01111110: with a known -3 bit slip, comparing against phase 0 would
    # report bogus errors on bits that are actually correct.
    expected_trail = trailing_expected_pattern(align)
    trail_thr0 = decode["bits_thr0"]
    trail_err_theta = trail_err_thr0 = trail_n = 0
    for k in range(TRAIL_BYTES):
        b0, b1 = FRAME_END_BIT + k * 8, FRAME_END_BIT + k * 8 + 8
        if b1 > decided_bit.size:
            break
        bits_str = "".join(str(int(b)) for b in decided_bit[b0:b1])
        thr0_str = "".join(str(int(b)) for b in trail_thr0[b0:b1])
        ambig = ambiguous[b0:b1]
        e_theta = sum(1 for a, e in zip(bits_str, expected_trail) if a != e)
        e_thr0 = sum(1 for a, e in zip(thr0_str, expected_trail) if a != e)
        trail_err_theta += e_theta
        trail_err_thr0 += e_thr0
        trail_n += 8
        byte_val = bits_to_bytes(decided_bit[b0:b1])[0]
        note = (f"expect {expected_trail} (0x7E at the detected slip phase) -> "
                f"{'OK' if e_theta == 0 else f'{e_theta}/8 differ'};  "
                f"same bits at plain threshold=0: {thr0_str} -> "
                f"{'OK' if e_thr0 == 0 else f'{e_thr0}/8 differ'}")
        append_row(ws, ["TRAILING", f"trailing flag byte {k}", b0, 8, None,
                        f"0x{byte_val:02X}", note],
                   bits_str, ambig, SEGMENT_FILLS["trailing-flag"])

    # ---- Summary row: total possibly-wrong (red) bits across the whole frame ----
    seg_counts = decode["seg_counts"]
    total_ambig = sum(a for a, _ in seg_counts.values())
    total_bits = sum(n for _, n in seg_counts.values())
    breakdown = "  ".join(f"{lbl}={a}/{n}" for lbl, (a, n) in seg_counts.items())
    ws.append([])
    r = ws.max_row + 1
    ws.cell(r, 1).value = f"TOTAL possibly-wrong bits (red): {total_ambig} / {total_bits}"
    ws.cell(r, 1).font = Font(bold=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    ws.cell(r + 1, 1).value = f"Breakdown: {breakdown}"
    ws.cell(r + 1, 1).font = Font(italic=True, size=9)
    ws.merge_cells(start_row=r + 1, start_column=1, end_row=r + 1, end_column=7)
    ws.cell(r + 2, 1).value = (
        f"Frame alignment: stuffed 0s removed = {align['n_stuffed_removed']} "
        f"(reference frame needs {align['ref_n_stuffed']});  "
        f"closing flag 0x7E at payload bit {align['flag_run_start_bit']} "
        f"(expected {FRAME_END_BIT}, slip = {align['slip_bits']:+d} bits)"
        if align["slip_bits"] is not None else
        f"Frame alignment: stuffed 0s removed = {align['n_stuffed_removed']}; "
        f"closing flag not found near the frame end")
    ws.cell(r + 2, 1).font = Font(italic=True, size=9,
                                   color="FF006600" if align["aligned"] else "FFCC0000")
    ws.merge_cells(start_row=r + 2, start_column=1, end_row=r + 2, end_column=7)

    widths = {"A": 12, "B": 30, "C": 10, "D": 8, "E": 36, "F": 26, "G": 60}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    ws2 = wb.create_sheet("Frame_Info")
    info_rows = [
        ("frame_index", frame_no),
        ("start_sample", start),
        ("z_score", round(z, 3)),
        ("header_err_144", decode["hdr_err"]),
        ("offset_threshold", round(decode["offset_thr"], 5)),
        ("A_median_abs_y", round(decode["A"], 5)),
        ("margin_fraction", MARGIN_FRAC),
        ("margin_abs", round(decode["margin"], 5)),
        ("crc_pass_threshold0_baseline", decode["crc_baseline"]),
        ("crc_pass_mixed_threshold", decode["crc_mixed"]),
        ("z_threshold_used", Z_THRESHOLD),
        ("align_stuffed_removed", align["n_stuffed_removed"]),
        ("align_stuffed_reference", align["ref_n_stuffed"]),
        ("align_closing_flag_ok", align["closing_flag_ok"]),
        ("align_flag_run_start_bit", align["flag_run_start_bit"]),
        ("align_expected_flag_bit", FRAME_END_BIT),
        ("align_slip_bits", align["slip_bits"]),
        ("align_spurious_removals", align["n_spurious_removals"]),
        ("align_first_slip_payload_byte", align["first_slip_byte"]),
        ("align_first_slip_field", slip_field_name),
        ("align_ok", align["aligned"]),
        ("info_bits_recovered", int(n_avail)),
        ("info_bits_expected", INFO_NBYTES * 8),
        ("fields_with_missing_bits", n_missing),
        ("total_ambiguous_bits", total_ambig),
        ("total_bits_all_segments", total_bits),
        ("audio_file", audio_path),
        ("xlsx_source", os.path.relpath(XLSX_PATH, HERE)),
        ("enums_source", os.path.relpath(ENUMS_PATH, HERE)),
    ]
    ws2.append(["key", "value"])
    for k, v in info_rows:
        ws2.append([k, v])
    ws2.append([])
    ws2.append(["segment", "ambiguous_bits", "total_bits"])
    for lbl, (a, n) in seg_counts.items():
        ws2.append([lbl, a, n])
    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 50

    os.makedirs(OUT_DIR, exist_ok=True)
    wb.save(out_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Decode a recording's beacon telemetry fields into one .xlsx per detected frame.")
    parser.add_argument("audio", nargs="?", default=AUDIO,
                         help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    parser.add_argument("--frame", type=int, default=None,
                         help="1-based frame index to decode (default: decode every detected frame)")
    parser.add_argument("--z-threshold", type=float, default=Z_THRESHOLD,
                         help=f"frame-detection z-score threshold (default: {Z_THRESHOLD}, calibrated on "
                              "cut_first3.ogg). For any other recording, run "
                              "04a_zscore_visualization.py on it first to check where its noise ceiling "
                              "sits before trusting this default.")
    return parser.parse_args()


def main():
    global Z_THRESHOLD
    args = parse_args()
    Z_THRESHOLD = args.z_threshold

    with open(ENUMS_PATH, "r", encoding="utf-8") as fh:
        enums_ctx = json.load(fh)
    fields = load_beacon_fields(XLSX_PATH)
    print(f"Loaded {len(fields)} beacon fields from {os.path.basename(XLSX_PATH)} "
          f"(spanning {fields[-1]['offsetbit'] + fields[-1]['bitlen']} bits)")

    y, fs = audio_io.read_audio(args.audio, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]     # frame-start detection only; decoding uses raw y (bypass method)

    template = build_header_template()
    print(f"Using Z_THRESHOLD={Z_THRESHOLD}")
    starts, zs = detect_frame_starts(yc, template)
    print(f"Detected {len(starts)} frame(s) in {os.path.basename(args.audio)}: " +
          ", ".join(f"frame#{i}@{s} (z={z:.2f})" for i, (s, z) in enumerate(zip(starts, zs), start=1)))

    if args.frame is not None:
        if not (1 <= args.frame <= len(starts)):
            raise SystemExit(f"--frame {args.frame} out of range: only {len(starts)} frame(s) detected")
        frame_indices = [args.frame]
    else:
        frame_indices = list(range(1, len(starts) + 1))

    stem = os.path.splitext(os.path.basename(args.audio))[0]

    for frame_no in frame_indices:
        start, z = starts[frame_no - 1], zs[frame_no - 1]
        print(f"\n{'=' * 78}\nFrame#{frame_no} at sample={start} (z-score={z:.2f})\n{'=' * 78}")

        decode = decode_frame(y, yc, start)
        print(f"offset threshold theta*={decode['offset_thr']:+.4f}  header err={decode['hdr_err']}/144  "
              f"A={decode['A']:.4f}  margin=+/-{decode['margin']:.4f}")
        print(f"CRC (threshold=0 baseline): {'PASS' if decode['crc_baseline'] else 'fail'}   "
              f"CRC (mixed: theta* in data segs): {'PASS' if decode['crc_mixed'] else 'fail'}")

        seg_counts = decode["seg_counts"]
        total_ambig = sum(a for a, _ in seg_counts.values())
        total_bits = sum(n for _, n in seg_counts.values())
        print("Possibly-wrong (red) bits by segment: " +
              "  ".join(f"{lbl}={a}/{n}" for lbl, (a, n) in seg_counts.items()))
        print(f"TOTAL possibly-wrong bits: {total_ambig}/{total_bits}")

        a = decode["align"]
        if a["aligned"]:
            print(f"Frame alignment: OK -- closing flag 0x7E starts exactly at payload bit "
                  f"{FRAME_END_BIT} ({a['n_stuffed_removed']} stuffed 0s removed)")
        else:
            print(f"Frame alignment: FAILED -- stuffed 0s removed={a['n_stuffed_removed']} "
                  f"(reference needs {a['ref_n_stuffed']}), closing flag at payload bit "
                  f"{a['flag_run_start_bit']} vs expected {FRAME_END_BIT} "
                  f"(slip={a['slip_bits']:+d} bits)"
                  if a["slip_bits"] is not None else
                  f"Frame alignment: FAILED -- closing flag not found near the frame end")
            if a["first_slip_byte"] is not None:
                print(f"  -> first detectable slip at payload byte {a['first_slip_byte']} "
                      f"({a['n_spurious_removals']} provably-spurious removal(s) inside zero-run "
                      f"padding); rows past it are on shifted byte boundaries")
            else:
                print("  -> slip not localizable: it falls inside a data segment, where there is "
                      "no known-content anchor to detect it")

        # Trailing flags are known content (0x7E), so print them for eyeballing:
        # the one stretch after the header where the slicer can be checked
        # against a correct answer -- and the only one at the END of the frame.
        db, amb, thr0 = decode["decided_bit"], decode["ambiguous"], decode["bits_thr0"]
        expect = trailing_expected_pattern(a)
        print(f"Trailing 0x7E flags after FCS -- known content, so this is ground truth at the "
              f"END of the frame.\n  Expected pattern = {expect} (0x7E rotated to the detected "
              f"slip phase). '!' = differs, '*' = near decision line.")
        print(f"  {'payload bit':>11} {'byte':>4} | {'theta* (as FCS/data)':^20} | {'threshold=0':^11} | errors")
        e_theta = e_thr0 = 0
        for k in range(TRAIL_BYTES):
            b0, b1 = FRAME_END_BIT + k * 8, FRAME_END_BIT + k * 8 + 8
            if b1 > db.size:
                break
            bs = "".join(str(int(v)) for v in db[b0:b1])
            s0 = "".join(str(int(v)) for v in thr0[b0:b1])
            marks = "".join("!" if x != e else ("*" if m else " ")
                            for x, e, m in zip(bs, expect, amb[b0:b1]))
            n1 = sum(1 for x, e in zip(bs, expect) if x != e)
            n0 = sum(1 for x, e in zip(s0, expect) if x != e)
            e_theta += n1
            e_thr0 += n0
            print(f"  {b0:>11} {b0//8:>4} | {bs} {marks} | {s0}  | theta*={n1}  thr0={n0}")
        print(f"  TOTAL over {TRAIL_BYTES} bytes: theta* = {e_theta} bits off, "
              f"threshold=0 = {e_thr0} bits off")

        out_path = os.path.join(OUT_DIR, f"{stem}_frame{frame_no}_beacon_decode.xlsx")
        write_workbook(out_path, frame_no, start, z, decode, fields, enums_ctx,
                       os.path.relpath(args.audio, HERE))
        print(f"Workbook saved: {out_path}")


if __name__ == "__main__":
    main()
