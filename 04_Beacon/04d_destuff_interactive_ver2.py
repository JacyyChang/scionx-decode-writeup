# -*- coding: utf-8 -*-
"""
04d_destuff_interactive_ver2.py -- EXPERIMENTAL FORK of 04d_destuff_interactive.py,
kept side by side on purpose so the two presentation choices below can be
compared directly against the same underlying data, rather than one
replacing the other. Everything not called out below is identical to the
main version -- see that file's own docstring for the shared mechanism
(RawBits sheet with per-bit ExcludeThisBit/FlipThisBit dropdowns, live-
connected to other sheets), applied to a REAL raw bit stream: one detected
frame of cut_first3.ogg (default frame 2).

What's different from 04d_destuff_interactive.py, and why: the main version
keeps every RawBits row fixed to its raw sample (BitIndex never moves) and
makes Subsystem/ItemName/ReadableValue/LookupTable LIVE lookups keyed on
that row's current DestuffedSeqNo -- so a row's *labels* change as earlier
rows get excluded, but the row itself never moves. This fork instead makes
_DestuffedBits (the "stuffing removed" compacted sequence) VISIBLE instead
of hidden, right next to RawBits: there, Position p always means "the p-th
surviving bit" and it's the SOURCE reference (SourceRawBitIndex) that shifts
underneath a fixed Position as you exclude bits -- the literal
"011100010 -> 01100010, everything after the removed bit shifts left"
mental model. Same data, opposite thing held fixed. See write_rawbits() and
write_destuffed_hidden()'s docstrings, and 使用說明's notes, for the
full comparison.

v2 (this version): ExcludeThisBit/FlipThisBit default to "No" everywhere --
no destuff/flip answer is pre-filled. RawBits only surfaces two signals
(LowConfidence, CandidateStuffPoint, both in red font when Yes) and leaves
the decision to the user. REFERENCE_FRAME.md (272-byte ground-test payload,
same satellite, different transmission) is still used internally, but only
for two things that don't involve pre-deciding anything: (a) simulating
standard HDLC bit-stuffing on it to know which decision rule (theta*
threshold vs. yc>0) applies at each raw position -- see decide_raw_bits()'s
docstring -- and (b) a small offset-alignment search (search_best_offset()).
Per CLAUDE.md's ground-truth rule, only the header + long all-zero padding
runs are structurally constant across any packet from this satellite, so
only those bytes factor into (a)/(b); the two telemetry segments
(data1/data2, which legitimately differ frame to frame) don't. The live
CRC-16/X.25 check (CRC_Check sheet, computed with Excel bitwise formulas
chained bit-by-bit) is the only oracle that covers those telemetry bits, and
it's also the only pass/fail signal that matters end to end: PASS means the
entire 272-byte payload was destuffed and decoded correctly.

Two other sheets are live views onto RawBits' current E/F choices: Fields
(hidden -- one row per SCIONX_TLMnew.xlsx beacon field, backing computation
for the field decode) and Beacon Decode (visible -- same field-level values,
laid out like the old cut_first3_frame2_beacon_decode.xlsx output). A third
hidden sheet, _DestuffedBits, is the single INDEX/MATCH join (RawBits'
DestuffedSeqNo -> destuffed bit value) that CRC_Check/Fields/Beacon Decode
all read from, so there's exactly one place doing that join, not four.

Usage: `python 04d_destuff_interactive_ver2.py [audio.ogg] [--frame N] [--z-threshold Z]`
Same CLI convention as 04c. Output:
`Output/<audio stem>_frame<N>_destuff_interactive_ver2.xlsx` -- a DIFFERENT
filename from the main version's own output, so running either one never
overwrites the other.
"""

import argparse
import json
import os
import re
import sys

import numpy as np
import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

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
OUT_DIR = os.path.join(HERE, "Output")
XLSX_PATH = os.path.join(HERE, "SCIONX_TLMnew.xlsx")     # beacon field layout, same file 04c uses
ENUMS_PATH = os.path.join(HERE, "SCIONX_enums.json")
INFO_BYTE0 = 16          # Info field starts at payload byte 16 (after Dest+Src+Control+PID)
INFO_NBYTES = 256        # 2048 bits -- matches SCIONX_TLMnew.xlsx's max OffsetBit+BitLen

FLAGS4 = bytes([0x7E] * 4)
HEADER_ADDR = bytes.fromhex("849c6086aa4060849c60a686b0e1")   # Dest+Src address, 14 bytes

Z_THRESHOLD = 12.0
MIN_FRAME_GAP = 100_000
SEARCH_SAMPLES = 19211
MARGIN_FRAC = 0.10

TRAIL_EXTRA = 40    # extra raw bits shown past the payload+FCS, for eyeballing the closing flag only

# 272-byte payload from REFERENCE_FRAME.md (ground test); FCS is NOT included
# here -- it's recomputed below since it depends on the payload only.
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
REF_PAYLOAD = bytes.fromhex(REF_ROWS)     # 272 bytes
REF_FCS = crc16_x25(REF_PAYLOAD).to_bytes(2, "little")
REF_FULL = REF_PAYLOAD + REF_FCS          # 274 bytes = 2192 bits


# ============================================================================
# Signal-side helpers -- duplicated from 04_beacon_field_decode.py / 04c
# (this repo's self-contained-script convention, see CLAUDE.md)
# ============================================================================

def bits_lsb_first(data):
    out = []
    for b in data:
        for k in range(8):
            out.append((b >> k) & 1)
    return out


def bits_to_bytes(bits):
    n = len(bits) // 8
    out = bytearray()
    for i in range(n):
        v = 0
        for k in range(8):
            v |= int(bits[i * 8 + k]) << k
        out.append(v)
    return bytes(out)


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


def calibrate_offset_threshold(y, start):
    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    idx_hdr = start + SPS * np.arange(0, 144) + PHASE
    y_hdr = y[idx_hdr]
    v1, v0 = y_hdr[t_bits == 1], y_hdr[t_bits == 0]
    grid = np.linspace(min(v0.min(), v1.min()), max(v0.max(), v1.max()), 2000)
    errs = [(v1 <= t).sum() + (v0 > t).sum() for t in grid]
    thr = grid[int(np.argmin(errs))]
    return float(thr), int(min(errs))


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


def build_segment_map():
    """Byte-range map over the 274-byte full frame (272 payload + 2 FCS).

    NOTE: this covers the FULL 16-byte header (Dest+Src+Control+PID, bytes
    0-15) as "address", matching INFO_BYTE0=16 (where the beacon xlsx's
    fields actually start) and the Beacon Decode sheet's 4 HEADER rows.
    04_beacon_field_decode.py / 04c's own build_segment_map only covers
    bytes 0-13 (Dest+Src only) and lumps Control+PID into "data1" -- fine
    for the 04c per-byte table structure, but confusing here: RawBits'
    SegmentGuess/row-shading would switch from address(blue) to data1
    (peach) two bytes too early, right in the middle of what every other
    part of this file (and REFERENCE_FRAME.md) calls "header"."""
    zero_runs = find_zero_runs(REF_PAYLOAD)
    segments = [("address", 0, 16)]
    prev = 16
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


SEGMENTS = build_segment_map()


def segment_for_byte(byte_idx):
    for label, b0, b1 in SEGMENTS:
        if b0 <= byte_idx < b1:
            return label
    return None


def build_verifiable_byte_mask():
    """True for bytes whose value is structurally constant across any packet
    from this satellite: the 16-byte header (address+control+PID) and the
    long all-zero padding runs in the Info field. False for the two
    telemetry segments (data1/data2) and the FCS (both depend on payload
    content that legitimately differs frame to frame -- see module docstring)."""
    mask = [False] * 274
    for b in range(16):
        mask[b] = True
    for (z0, z1) in find_zero_runs(REF_PAYLOAD):
        for b in range(z0, z1):
            mask[b] = True
    return mask


VERIFIABLE_BYTE = build_verifiable_byte_mask()


def stuff_bits(bits):
    """Standard HDLC bit-stuffing: insert a 0 after every run of 5
    consecutive 1s. Returns (stuffed_bits, raw_to_ref_idx) where
    raw_to_ref_idx[i] is the index into `bits` that stuffed position i came
    from, or None if position i is an inserted stuff bit."""
    stuffed, raw_to_ref_idx = [], []
    run = 0
    for j, b in enumerate(bits):
        stuffed.append(b)
        raw_to_ref_idx.append(j)
        if b == 1:
            run += 1
            if run == 5:
                stuffed.append(0)
                raw_to_ref_idx.append(None)
                run = 0
        else:
            run = 0
    return stuffed, raw_to_ref_idx


REF_BITS = bits_lsb_first(REF_FULL)                       # 2192 bits
REF_STUFFED, RAW_TO_REF_IDX = stuff_bits(REF_BITS)         # ~2200 bits


def decide_raw_bits(y_at_bit, yc_at_bit, offset_thr, margin, offset, n_out):
    """Decide n_out raw bits starting at signal-sample-index `offset` bits
    before REF_STUFFED position 0 (offset=0 means "no shift needed" -- raw
    bit i lines up with REF_STUFFED[i]). Segment for each position comes
    from RAW_TO_REF_IDX (i.e. this "cheats" using the reference to decide
    WHICH rule -- theta* vs yc>0 -- applies at each raw position; the bit
    VALUE itself still comes from the real y/yc samples. This is only valid
    as a diagnostic aid because REFERENCE_FRAME.md exists as an oracle --
    see module docstring).

    seg_label="stuff" is only ever produced where the reference itself is
    trustworthy (VERIFIABLE_BYTE -- header/zero-run), even though
    REF_STUFFED's own simulation predicts stuff-bit positions everywhere,
    including inside data1/data2. A stuff bit's existence is entirely a
    function of the 5 real payload bits immediately before it, so inside
    data1/data2 -- where REFERENCE_FRAME.md's byte content is a DIFFERENT
    telemetry snapshot, not frame 2's actual values (see module docstring)
    -- a "stuff predicted here" from the reference doesn't mean frame 2's
    real signal has 5 ones there too; it commonly doesn't. Falling back to
    the preceding byte's normal segment label (data1/data2) instead of
    "stuff" in that case keeps SegmentGuess only asserting what it can
    actually back up; RawBits' own CandidateStuffPoint column (computed
    purely from the live RawBitValue column, no reference involved) is the
    only signal that should be trusted for "is this really a stuff bit"
    inside data1/data2."""
    decided = np.zeros(n_out, dtype=np.uint8)
    ambiguous = np.zeros(n_out, dtype=bool)
    seg_label = [None] * n_out
    for i in range(n_out):
        ref_pos = i - offset
        if 0 <= ref_pos < len(RAW_TO_REF_IDX):
            ridx = RAW_TO_REF_IDX[ref_pos]
            if ridx is not None:
                lbl = segment_for_byte(ridx // 8)
            else:
                prev_ridx = RAW_TO_REF_IDX[ref_pos - 1] if ref_pos > 0 else None
                if prev_ridx is not None and VERIFIABLE_BYTE[prev_ridx // 8]:
                    lbl = "stuff"
                else:
                    lbl = segment_for_byte(prev_ridx // 8) if prev_ridx is not None else None
        else:
            lbl = "trailing"
        seg_label[i] = lbl
        if lbl is not None and lbl.startswith("zero-run"):
            decided[i] = 1 if yc_at_bit[i] > 0 else 0
            ambiguous[i] = decided[i] == 1
        else:
            decided[i] = 1 if y_at_bit[i] > offset_thr else 0
            ambiguous[i] = abs(y_at_bit[i] - offset_thr) <= margin
    return decided, ambiguous, seg_label


def ref_lookup(i, offset):
    """For raw position i (given a candidate `offset`), return
    (ref_pos, ridx, expected_wire_bit): ref_pos is the index into
    REF_STUFFED/RAW_TO_REF_IDX that raw position i lines up with; ridx is
    the index into REF_BITS that ref_pos's payload bit came from (None if
    ref_pos is a stuff bit or out of range); expected_wire_bit is
    REF_STUFFED[ref_pos] (None if out of range). Centralized here so the
    offset shift is applied exactly once, the same way, everywhere it's
    used -- comparing decide_raw_bits()'s per-i output against anything
    indexed directly by i (instead of i - offset) silently ignores the
    offset entirely."""
    ref_pos = i - offset
    if not (0 <= ref_pos < len(RAW_TO_REF_IDX)):
        return ref_pos, None, None
    ridx = RAW_TO_REF_IDX[ref_pos]
    return ref_pos, ridx, REF_STUFFED[ref_pos]


def search_best_offset(y_at_bit, yc_at_bit, offset_thr, margin):
    """Try small raw-bit offsets and keep the one that best matches
    REFERENCE_FRAME.md at VERIFIABLE positions only (header + zero-run
    padding). Scoring against the full stream (including the two telemetry
    segments, which are legitimately different content -- see module
    docstring) doesn't work: telemetry mismatches swamp the signal and the
    minimum stops being sharp. Restricting to verifiable positions gives a
    clean minimum (near 0 mismatches at the true offset, ~50% elsewhere).
    REFERENCE_FRAME.md's own "Correction" note found offset=0 for frame#2;
    this re-derives it rather than assuming it, so the script still does
    something sensible if pointed at a different frame."""
    best = None
    L = len(REF_STUFFED)
    for off in range(-16, 17):
        n_out = min(len(y_at_bit) - max(off, 0), L)
        if n_out <= 0:
            continue
        decided, _, _ = decide_raw_bits(y_at_bit, yc_at_bit, offset_thr, margin, off, n_out)
        n_verif = mism = 0
        for i in range(n_out):
            _, ridx, _ = ref_lookup(i, off)
            if ridx is None or not VERIFIABLE_BYTE[ridx // 8]:
                continue
            n_verif += 1
            if decided[i] != REF_BITS[ridx]:
                mism += 1
        if n_verif == 0:
            continue
        if best is None or mism < best[1]:
            best = (off, mism, n_verif)
    return best


# ============================================================================
# Beacon field layout (xlsx) + enum/transform lookup (json) -- duplicated
# verbatim from 04_beacon_field_decode.py / 04c (this repo's self-contained-
# script convention, see CLAUDE.md), plus new formula-generation helpers so
# each field's decoded value can be shown live, right next to the raw bits
# that make it up, in RawBits.
# ============================================================================

def load_beacon_fields(xlsx_path):
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


def field_bit_weights(bitlen, endian):
    """Weight of each transmission-order bit (k=0..bitlen-1, k=0 is the
    first bit of the field as it appears in Destuffed) in the field's
    raw_unsigned integer value. Must match decode_raw_value() in
    04_beacon_field_decode.py / 04c EXACTLY (including its MSB-first
    interpretation of the bit string): for multi-byte little-endian fields,
    whole BYTES get reordered first (last byte becomes first), THEN the
    resulting string is read as a plain binary number left-to-right (MSB
    first) -- so within a byte, bit order is untouched by the LE swap."""
    if bitlen % 8 == 0 and bitlen > 8 and endian == "LE":
        nbytes = bitlen // 8
        new_pos = [(nbytes - 1 - (k // 8)) * 8 + (k % 8) for k in range(bitlen)]
    else:
        new_pos = list(range(bitlen))
    return [2 ** (bitlen - 1 - new_pos[k]) for k in range(bitlen)]


# ============================================================================
# Workbook construction
# ============================================================================

YES_NO_DV = lambda: DataValidation(type="list", formula1='"Yes,No"', allow_blank=False)  # noqa: E731

HEADER_FILL = PatternFill("solid", fgColor="FFDCE9F5")
RED_FONT = Font(color="FFCC0000")
GREEN_FONT = Font(color="FF006600", bold=True)
SEG_FILLS = {
    "address": PatternFill("solid", fgColor="FFDCEAF7"),
    "data1": PatternFill("solid", fgColor="FFFDEBD3"),
    "zero-run1": PatternFill("solid", fgColor="FFECECEC"),
    "data2": PatternFill("solid", fgColor="FFFBE0E0"),
    "zero-run2": PatternFill("solid", fgColor="FFE1E1E1"),
    "FCS": PatternFill("solid", fgColor="FFE6DFF7"),
    "stuff": PatternFill("solid", fgColor="FFFFF3B0"),
    "trailing": PatternFill("solid", fgColor="FFDFF3E6"),
    "mixed": PatternFill("solid", fgColor="FFFFF3B0"),
}


def write_instructions(wb, frame_no, n_raw, best_offset, n_verifiable_bits, n_telemetry_bits):
    ws = wb.create_sheet("使用說明")
    lines = [
        "SCIONX Destuff 互動練習 -- 真實 Frame 版 ver2 實驗版 "
        "(延伸自 scionx_destuff_demo.xlsx 的機制，跟 04d_destuff_interactive.py 主版本平行比較用)",
        "",
        f"這份檔案把 scionx_destuff_demo.xlsx 驗證過的機制"
        "（RawBits 下拉選單 destuff/flip -> 其他分頁即時連動）"
        f"套用到 cut_first3.ogg 真實訊號解出的 Frame #{frame_no}，"
        f"共 {n_raw} 個原始判決 bit（不含結尾額外顯示的 {TRAIL_EXTRA} bit）。",
        "",
        "這是 04d_destuff_interactive_ver2.py 產生的檔案，跟主版本 04d_destuff_interactive.py "
        "（輸出檔名沒有 _ver2）內容大部分一樣，差別只在下面 ver2 專屬的第 5、6 點 -- 兩個檔案可以"
        "同時開著比較。",
        "",
        "改動（相對於最早的手動 demo 版）：",
        "1. ExcludeThisBit / FlipThisBit 這兩欄現在全部預設 No，不再由程式先猜答案 -- "
        "要 destuff/flip 哪個 bit，完全由你自己判斷、手動勾選。",
        "2. LowConfidence、CandidateStuffPoint 這兩欄，只要是 Yes 就用紅字標出來"
        "（這兩欄是根據這一列固定的採樣點/RawBitValue 算的，不會因為你調整別的列而變動）。",
        "3. 拿掉 Destuffed、Bytes_Hex 兩個分頁（比對介面太雜，容易搞混）。CRC_Check 保留。",
        "4. 新增 Beacon Decode 分頁，樣式跟舊的 cut_first3_frame2_beacon_decode.xlsx 一樣"
        "（Subsystem/ItemName/OffsetBit/BitLen/Bits/ReadableValue/LookupTable，同欄位的 bit "
        "合併成一列），會隨你在 RawBits 的調整即時連動。",
        "5.（ver2 專屬實驗）_DestuffedBits 分頁改成不隱藏，直接放在 RawBits 右邊。這是「移位後的"
        "壓縮序列」本尊：Position 欄永遠代表『目前存活的第幾個 bit』，你在 RawBits 排除一個 bit 後，"
        "後面所有 Position 對應到的 RawBits 來源列（B欄 SourceRawBitIndex）跟數值都會往前挪一格 -- "
        "這是跟 RawBits 本身『每列固定代表一個採樣點、Subsystem/ItemName/ReadableValue 隨 "
        "DestuffedSeqNo 查表變動』相反的呈現方式，同一份資料兩種看法，可以互相對照。"
        "D/E 欄（Subsystem/ItemName）是額外加的，純粹讓你在看數值滑動時知道滑到哪個欄位了。",
        "6.（ver2 專屬）header 區段的邊界從 14 bytes 修正成 16 bytes（含 Control+PID），"
        "跟 Beacon Decode 的 HEADER 四列對齊。",
        "7.（ver2 專屬實驗）RawBits 的整列背景色改成用 Excel 條件式格式設定即時算，不再是產生檔案"
        "當下就寫死的固定色：ExcludeThisBit=Yes 的列會整列變黃色（優先權最高，一眼就能看出你已經"
        "處理過哪些列）；其餘列的顏色跟著 I 欄 SegmentGuess 走 -- 而 SegmentGuess 本身現在也是"
        "活公式，根據這一列『當下』的 H 欄（DestuffedSeqNo）落在哪個結構段（address/data1/"
        "zero-run1/data2/zero-run2/FCS/trailing）決定，跟 J~M 欄同一套邏輯。"
        "結果就是：你在前面排除一個 bit 之後，後面每一列的『顏色』會跟著『欄位名稱』一起往前移動"
        "一格，不是只有文字變、顏色沒動。你還沒排除任何東西之前，顏色顯示的是『假設完全沒 "
        "destuff』的樸素結構猜測（跟 J~M 欄一開始的行為一樣），這是預期行為，不是 bug。",
        "",
        "比對用的參考資料是 REFERENCE_FRAME.md 的 272-byte 地面測試封包（同一顆衛星的另一次下傳）。"
        "根據 CLAUDE.md 的規則：header（Dest/Src address + Control + PID，前 16 bytes）"
        "以及 Info 欄位裡的長段全零 padding，在任何一次下傳中理論上都應該相同；"
        "但 Info 欄位裡真正的遙測數值（data1/data2 兩段）每個 frame 都不一樣，"
        f"這份參考資料涵蓋 {n_verifiable_bits} bit 的 header/zero-run，"
        f"另外 {n_telemetry_bits} bit 的 telemetry 沒有真值可比對，只能靠 CRC_Check 驗證。"
        "這份參考資料目前只用在：(a) 內部訊號判決時決定 zero-run 段要用哪一種門檻規則"
        "（RawBits 的 SegmentGuess 欄），(b) 對齊 offset 的小範圍搜尋。"
        "不會用來預先決定你的 ExcludeThisBit / FlipThisBit。",
        "",
        f"RawBits 對齊offset：程式對 -16~+16 bit 做了小範圍搜尋，"
        f"目前資料最佳對齊為 offset={best_offset:+d}"
        "（REFERENCE_FRAME.md 的『Correction』一節說 frame#2 應該是 offset=0；"
        "這裡是重新算一次，不是照抄那個結論）。",
        "",
        "分頁說明：",
        "1. RawBits：每一列一個原始判決 bit。"
        "B=RawBitValue（訊號量出來的 0/1，不可編輯）、"
        "C=LowConfidence（|y-theta*|在門檻內、接近判決線 -- Yes 時紅字）、"
        "D=CandidateStuffPoint（公式偵測：B 欄裡，前面剛好連續 5 個 1 -- Yes 時紅字。"
        "注意這是純粹依照『目前 RawBitValue 的 0/1 排列』算出來的，不是依照參考序列算的，"
        "所以有時候你會看到某個位置『看起來』該是 stuff bit（I 欄 SegmentGuess=stuff），"
        "但因為前面剛好有個 bit 被判成 0 而不是 1，連續 5 個 1 的條件沒達成，D 欄就不會是 Yes -- "
        "這種落差本身就是訊號有低信心 bit 的線索，值得對照 C 欄一起看）、"
        "E=ExcludeThisBit（**下拉選單，預設 No** -- 你判斷這個 bit 是 stuffing 插入的就改 Yes，"
        "destuff 時會被移除）、"
        "F=FlipThisBit（**下拉選單，預設 No** -- 你判斷這是判決錯誤就改 Yes，反轉 0/1）、"
        "G=EffectiveBitValue（=IF(F=Yes,1-B,B)）、"
        "H=DestuffedSeqNo（排除 E=Yes 的 bit 後，這個 bit 在去除 stuffing 序列裡排第幾號，"
        "後面幾個分頁都是靠這欄位對齊/連動的）、"
        "I=SegmentGuess（這個位置對應到哪個結構段：header/data1/zero-run1/data2/zero-run2/FCS/"
        "stuff/trailing -- 是依照參考序列算好寫死的參考資訊，不會隨你調整 E/F 重算，"
        "純粹讓你知道『這裡大概是什麼』）、"
        "J=Subsystem、K=ItemName（這個 bit 屬於 SCIONX_TLMnew.xlsx 哪一個遙測欄位 -- 只有落在 "
        "Info 欄位範圍內的 bit 才有值，header/FCS/stuff/結尾多顯示的幾個 bit 這兩欄是空的）、"
        "L=ReadableValue（該欄位『目前』解出來的值，活公式，隨你調整同一欄位任何一個 bit 的 E/F "
        "即時變動，可以用來判斷這次調整合不合理，例如電壓/電流欄位變成負值或超大值通常代表調錯了）、"
        "M=LookupTable（enum 對照表或 scale/unit 換算說明，同一欄位每個 bit 都顯示同樣內容）。",
        "2. CRC_Check：把目前 destuff 完的 payload bits（前 2176 bit = 272 bytes）"
        "用 Excel 的 BITAND/BITXOR/BITRSHIFT 公式即時算 CRC-16/X.25"
        "（跟 scionx/hdlc.py 的 crc16_x25 演算法一致，逐 bit 版本），"
        "和訊號解出來的 FCS（最後 16 bit）比對，即時顯示 PASS/FAIL -- 這是唯一涵蓋整個 272-byte "
        "payload（包含 telemetry）的檢查，PASS 才代表這個 frame 真的解對了。",
        "3. Beacon Decode：跟舊版 cut_first3_frame2_beacon_decode.xlsx 一樣的欄位配置"
        "（Subsystem/ItemName/OffsetBit/BitLen/Bits/ReadableValue/LookupTable），"
        "同一個欄位的所有 bit 合併成一列顯示，比 RawBits 逐 bit看更容易抓整體脈絡；"
        "Bits/ReadableValue 都是活公式，隨 RawBits 的調整即時連動。",
        "",
        "建議操作方式：先看 RawBits 的 C/D 兩欄紅字（低信心 / 候選 stuff 點），"
        "配合 I 欄 SegmentGuess 判斷這個位置『應該』是什麼，自己決定 E/F 要不要改成 Yes；"
        "改完可以切到 Beacon Decode 分頁看對應欄位的 ReadableValue 合不合理，"
        "最終目標是讓 CRC_Check 顯示 PASS。",
        "",
        "效能提醒：這個檔案有兩千多列公式，Excel 開啟、或每次改動下拉選單重新計算，"
        "都會比 demo 檔慢一點，屬正常現象。另外有兩個分頁（_DestuffedBits、Fields）被隱藏了，"
        "是內部串接用的輔助分頁（RawBits/Beacon Decode/CRC_Check 都是靠它們對齊資料），"
        "好奇可以在分頁標籤按右鍵「取消隱藏」看，但不需要去動裡面的內容。",
    ]
    for i, text in enumerate(lines, start=1):
        ws.cell(i, 1).value = text
        if i == 1:
            ws.cell(i, 1).font = Font(bold=True, size=12)
    ws.column_dimensions["A"].width = 120
    for r in range(1, len(lines) + 1):
        ws.cell(r, 1).alignment = Alignment(wrap_text=True, vertical="top")
    return ws


def write_rawbits(wb, decided, ambiguous, n_raw, n_fields):
    """Subsystem/ItemName/ReadableValue/LookupTable (J/K/L/M) are LIVE
    formulas keyed on the row's CURRENT DestuffedSeqNo (H), not a
    Python-precomputed guess -- this is the point: as you change
    ExcludeThisBit on an earlier row, every later row's DestuffedSeqNo
    shifts (H already did this), and now J/K/L/M shift WITH it, always
    showing whichever field this row's bit currently, actually lands in.
    Before you've excluded anything, H{r}=BitIndex+1 for every row (nothing
    has been removed yet), so J/K/L/M initially show the same
    "naive/un-destuffed" field guess Beacon Decode shows at that stage --
    that's expected, not a bug. A previous version computed a FIXED field
    assignment once at generation time (based on the reference-derived
    offset, independent of your E/F choices) and only made ReadableValue
    live; that meant Subsystem/ItemName could go stale relative to what a
    row's bit actually contributes to as soon as any earlier row got
    excluded. The lookup itself is INDEX/MATCH against Fields' OffsetBit
    column (approximate match -- Fields is sorted by OffsetBit and gapless,
    since SCIONX_TLMnew.xlsx has explicit "(reserved)" filler rows over any
    unused bit ranges), only valid for H in [129,2176] (the Info field;
    positions 1-128 are header, 2177-2192 are FCS, neither has per-field
    entries in Fields).

    ExcludeThisBit / FlipThisBit both default to "No" for every row --
    deciding which bits are real stuff bits or slicer errors is left
    entirely to manual judgment (see 使用說明); this script does not
    pre-fill an answer. LowConfidence and CandidateStuffPoint are shown in
    red when Yes so those two signals are easy to scan for.

    ver2 EXPERIMENT (see 使用說明's notes): row fill is now LIVE too, via
    conditional formatting instead of static per-cell fills, so it shifts
    the same way J/K/L/M do. SegmentGuess (I) became a live formula on H
    (same [129,2176]-style boundary check as J/K/L/M, just against the
    fixed byte-range boundaries in SEGMENTS instead of Fields' OffsetBit
    column), and conditional-formatting rules key off I's live value --
    excluded rows (E="Yes") get a flat yellow highlight (highest priority,
    so you can spot at a glance which rows you've already handled),
    everything else gets colored by whatever segment its CURRENT
    DestuffedSeqNo lands in. Before any excludes, that's the same
    "naive/un-destuffed" picture as J/K/L/M start with -- expected, not a
    bug (see write_fields_sheet-adjacent notes above). This replaces the
    static per-row SEG_FILLS loop the main version still uses; there is no
    more special-cased "stuff" fill exemption here since a live SegmentGuess
    only ever reports where a row's bit CURRENTLY sits, never an assumed
    stuff-bit identity -- once you exclude a real stuff bit it simply drops
    off the destuffed sequence (H becomes blank) and the row goes yellow
    instead of whatever color it had."""
    ws = wb.create_sheet("RawBits")
    headers = ["BitIndex", "RawBitValue", "LowConfidence", "CandidateStuffPoint",
               "ExcludeThisBit", "FlipThisBit", "EffectiveBitValue", "DestuffedSeqNo",
               "SegmentGuess", "Subsystem", "ItemName", "ReadableValue", "LookupTable"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(1, c).fill = HEADER_FILL

    dv_exclude = YES_NO_DV()
    dv_flip = YES_NO_DV()
    ws.add_data_validation(dv_exclude)
    ws.add_data_validation(dv_flip)

    last = n_fields + 1   # Fields sheet's last data row (row1 is its header)

    # Fixed bit-position boundaries (1-indexed, inclusive) for each structural
    # segment, derived from SEGMENTS (byte ranges) -- used to build the live
    # SegmentGuess formula below. "trailing" (H > last FCS bit) is appended
    # separately since SEGMENTS itself only covers the 274-byte frame.
    seg_bit_ranges = [(label, b0 * 8 + 1, b1 * 8) for (label, b0, b1) in SEGMENTS]
    last_fcs_bit = seg_bit_ranges[-1][2]

    for i in range(n_raw):
        r = i + 2
        low_conf = bool(ambiguous[i])
        is_candidate = i >= 5 and all(decided[i - 5:i] == 1)
        ws.append([
            i, int(decided[i]), "Yes" if low_conf else "No", None,   # D filled below with formula
            "No", "No", None, None,                                  # G/H filled below with formula
            None,                                                    # I filled below (live formula)
            None, None, None, None,                                  # J/K/L/M filled below
        ])
        if i < 5:
            ws.cell(r, 4).value = ""
        else:
            ws.cell(r, 4).value = (
                f'=IF(AND(B{r-5}=1,B{r-4}=1,B{r-3}=1,B{r-2}=1,B{r-1}=1),"Yes","")'
            )
        ws.cell(r, 7).value = f'=IF(F{r}="Yes",1-B{r},B{r})'
        ws.cell(r, 8).value = f'=IF(E{r}="Yes","",COUNTIFS($E$2:E{r},"No"))'

        seg_chain = f'"trailing"'
        for label, lo, hi in reversed(seg_bit_ranges):
            seg_chain = f'IF(H{r}<={hi},"{label}",{seg_chain})'
        ws.cell(r, 9).value = f'=IF(H{r}="","",{seg_chain})'

        in_info = f'AND(H{r}<>"",H{r}>=129,H{r}<=2176)'
        match_expr = f'MATCH(H{r}-129,Fields!$D$2:$D${last},1)'
        for col, field_col in ((10, "B"), (11, "C"), (12, "J"), (13, "K")):
            ws.cell(r, col).value = (
                f'=IFERROR(IF({in_info},INDEX(Fields!${field_col}$2:${field_col}${last},{match_expr}),""),"")'
            )

        if low_conf:
            ws.cell(r, 3).font = RED_FONT
        if is_candidate:
            ws.cell(r, 4).font = RED_FONT
        dv_exclude.add(ws.cell(r, 5))
        dv_flip.add(ws.cell(r, 6))

    last_row = n_raw + 1
    full_range = f"A2:M{last_row}"
    # NOTE: conditional-formatting fills use a DIFFERENT color slot than normal cell
    # fills -- Excel's differential-style (dxf) records show a solid pattern's
    # bgColor, not its fgColor (the opposite of a plain PatternFill used directly on
    # a cell, which is why SEG_FILLS -- built for normal cell fills elsewhere in this
    # file -- has to be re-wrapped here instead of reused as-is; reusing it directly
    # silently renders as no fill at all).
    def cf_fill(hexcolor):
        return PatternFill(fill_type="solid", bgColor=hexcolor)

    ws.conditional_formatting.add(
        full_range, FormulaRule(formula=['$E2="Yes"'], fill=cf_fill("FFFFF2A8"), stopIfTrue=True))
    for label in [lbl for lbl, _, _ in seg_bit_ranges] + ["trailing"]:
        fill = SEG_FILLS.get(label)
        if fill:
            ws.conditional_formatting.add(
                full_range,
                FormulaRule(formula=[f'$I2="{label}"'], fill=cf_fill(fill.fgColor.rgb), stopIfTrue=True))

    widths = {"A": 10, "B": 12, "C": 14, "D": 18, "E": 15, "F": 13, "G": 16, "H": 15, "I": 12,
              "J": 12, "K": 30, "L": 18, "M": 46}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return ws


def write_destuffed_hidden(wb, n_ref_bits, n_fields):
    """ver2 EXPERIMENT (see 使用說明's ver2 note): this sheet is now VISIBLE,
    not hidden -- it's the literal "shift everything after a removed bit
    left by one" view (Position p always means the same THING conceptually,
    "the p-th surviving bit", but which RawBits row currently supplies that
    bit changes as you toggle ExcludeThisBit). Compare this against
    RawBits' own J/K/L/M columns (ver1's approach: keep every row fixed to
    its raw sample, look up the CURRENT field for that sample's
    DestuffedSeqNo) -- same underlying data, opposite way of presenting the
    "things move as you destuff" idea. D/E (Subsystem/ItemName) added here
    purely as orientation so watching values slide up/down this sheet is
    actually legible, using the exact same live INDEX/MATCH-against-Fields
    approach as RawBits' J/K (see write_rawbits' docstring for why the
    match only applies to Position in [129,2176]).

    For each Position p=1..n_ref_bits (the bit's index in the "stuffing
    removed" sequence), look up which RawBits row currently has that
    DestuffedSeqNo and pull its EffectiveBitValue. This is the ONLY place
    that re-derives "destuffed payload bit p" from RawBits' current E/F
    choices; CRC_Check, Fields, and Beacon Decode all read from here (row
    for Position p is always p+1) rather than re-deriving it themselves, so
    there's exactly one INDEX/MATCH join, not four."""
    ws = wb.create_sheet("_DestuffedBits")
    headers = ["Position", "SourceRawBitIndex", "DestuffedBitValue", "Subsystem", "ItemName"]
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c).value = h
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(1, c).fill = HEADER_FILL

    last = n_fields + 1   # Fields sheet's last data row (row1 is its header)
    for pos in range(1, n_ref_bits + 1):
        r = pos + 1
        ws.cell(r, 1).value = pos
        ws.cell(r, 2).value = f'=IFERROR(INDEX(RawBits!$A:$A,MATCH($A{r},RawBits!$H:$H,0)),"?")'
        ws.cell(r, 3).value = f'=IFERROR(INDEX(RawBits!$G:$G,MATCH($A{r},RawBits!$H:$H,0)),"")'
        in_info = f'AND(A{r}>=129,A{r}<=2176)'
        match_expr = f'MATCH(A{r}-129,Fields!$D$2:$D${last},1)'
        for col, field_col in ((4, "B"), (5, "C")):
            ws.cell(r, col).value = (
                f'=IFERROR(IF({in_info},INDEX(Fields!${field_col}$2:${field_col}${last},{match_expr}),""),"")'
            )
    widths = {"A": 10, "B": 18, "C": 18, "D": 12, "E": 30}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return ws


def write_crc_check(wb, n_payload_bits=2176, n_fcs_bits=16):
    """Bit-serial CRC-16/X.25 (reflected, poly 0x8408, init 0xFFFF, xorout
    0xFFFF) implemented with live Excel formulas, chained down the sheet,
    reading _DestuffedBits!C (DestuffedBitValue) for the payload bits so it
    recomputes whenever a RawBits dropdown changes. Equivalent to
    scionx.hdlc.crc16_x25 -- verified against it offline (see module
    docstring / dev notes) before being turned into formulas."""
    ws = wb.create_sheet("CRC_Check")
    ws["A1"] = "Computed CRC-16/X.25 over destuffed payload bits (positions 1..2176):"
    ws["B1"] = f"=DEC2HEX(G{2 + n_payload_bits},4)"
    ws["A2"] = "Transmitted FCS (destuffed positions 2177..2192, little-endian):"
    ws["B2"] = "=DEC2HEX(D2,4)"
    ws["D2"] = "=E2+F2*256"
    ws["A3"] = "CRC check:"
    ws["B3"] = '=IF(B1=B2,"PASS","FAIL")'
    for addr in ("A1", "A2", "A3"):
        ws[addr].font = Font(bold=True)
    ws["B3"].font = Font(bold=True)

    fcs_row0 = 1 + n_payload_bits + 1   # _DestuffedBits row for Position (n_payload_bits+1) = 2177
    byte0_terms = "+".join(f"_DestuffedBits!C{fcs_row0 + k}*{2 ** k}" for k in range(8))
    byte1_terms = "+".join(f"_DestuffedBits!C{fcs_row0 + 8 + k}*{2 ** k}" for k in range(8))
    ws["E2"] = f"={byte0_terms}"
    ws["F2"] = f"={byte1_terms}"

    headers = ["BitPos(payload)", "PayloadBit", "crc_prev", "lsb", "shifted", "xorflag", "crc_new"]
    hdr_row = 5
    for c, h in enumerate(headers, start=1):
        ws.cell(hdr_row, c).value = h
        ws.cell(hdr_row, c).font = Font(bold=True)
        ws.cell(hdr_row, c).fill = HEADER_FILL

    ws.cell(hdr_row + 1, 1).value = 0
    ws.cell(hdr_row + 1, 7).value = 65535   # 0xFFFF initial register, in column G ("crc_new" of row 0)

    for pos in range(1, n_payload_bits + 1):
        r = hdr_row + 1 + pos
        prev_r = r - 1
        ws.cell(r, 1).value = pos
        ws.cell(r, 2).value = f"=_DestuffedBits!C{1 + pos}"   # _DestuffedBits row for Position pos = pos+1
        ws.cell(r, 3).value = f"=G{prev_r}"
        # NOTE: BITAND/BITXOR/BITRSHIFT are post-2007 Excel functions -- when a
        # formula string is written directly into the XML (as openpyxl does,
        # rather than typed into Excel's UI), Excel only recognizes them with
        # the internal "_xlfn." prefix; without it every cell shows #NAME?.
        ws.cell(r, 4).value = f"=_xlfn.BITAND(C{r},1)"
        ws.cell(r, 5).value = f"=_xlfn.BITRSHIFT(C{r},1)"
        ws.cell(r, 6).value = f"=_xlfn.BITXOR(D{r},B{r})"
        ws.cell(r, 7).value = f'=IF(F{r}=1,_xlfn.BITXOR(E{r},33800),E{r})'

    # final xorout 0xFFFF, referenced by B1 above via G{2+n_payload_bits} -- patch: that
    # references the LAST crc_new row directly (register before xorout), so fold xorout in here:
    ws["B1"] = f"=DEC2HEX(_xlfn.BITXOR(G{hdr_row + 1 + n_payload_bits},65535),4)"

    widths = {"A": 16, "B": 22, "C": 10, "D": 8, "E": 10, "F": 9, "G": 10}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = f"A{hdr_row + 2}"
    return ws


def write_fields_sheet(wb, fields, enums_ctx):
    """One row per SCIONX_TLMnew.xlsx beacon field. RawUnsigned/Value/
    ReadableValue are live formulas chained back to _DestuffedBits!C for that
    field's bits, so they update immediately when a RawBits ExcludeThisBit /
    FlipThisBit dropdown changes -- this is what RawBits' and Beacon Decode's
    Subsystem/ItemName/ReadableValue/LookupTable columns point at (one INDEX
    per row would be slower and less transparent than just writing the row
    number directly, since the row layout is fully known at generation time).
    This sheet is hidden by default (see build_workbook) -- Beacon Decode is
    the intended way to look at per-field values; this is its backing data.

    ReadableValue replicates 04_beacon_field_decode.py / 04c's
    decode_raw_value()+format_readable() as formulas: enum tables become a
    nested IF chain (keyed on RawUnsigned, matching format_readable's
    `parsed_enum.get(raw_unsigned, ...)`), scale/unit transforms become
    ROUND(value*scale)&unit, signed ints get 2's-complement correction, and
    the one float32 field gets a manual IEEE-754 decode (sign/exponent/
    mantissa via BITAND/BITRSHIFT) since Excel has no bit-to-float cast.
    display_format/timezone entries in SCIONX_enums.json's transforms are
    NOT applied -- 04c's own format_readable() doesn't use them either (see
    04d's module-level comments); this stays byte-for-byte consistent with
    that, rather than adding formatting 04c itself doesn't have."""
    ws = wb.create_sheet("Fields")
    headers = ["FieldIndex", "Subsystem", "ItemName", "OffsetBit(Info)", "BitLen", "Endian", "DType",
               "RawUnsigned", "Value(signed/float)", "ReadableValue", "LookupTable"]
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c).value = h
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(1, c).fill = HEADER_FILL

    field_row = {}
    for fi, f in enumerate(fields):
        r = fi + 2
        field_row[fi] = r
        bitlen, endian, dtype = f["bitlen"], f["endian"], f["dtype"]
        weights = field_bit_weights(bitlen, endian)
        # _DestuffedBits row for absolute destuffed bit index idx0 (0-indexed) is idx0+2
        # (Position = idx0+1, row = Position+1, since _DestuffedBits has a single header row).
        destuffed_rows = [(INFO_BYTE0 * 8 + f["offsetbit"] + k) + 2 for k in range(bitlen)]

        ws.cell(r, 1).value = fi
        ws.cell(r, 2).value = f["subsystem"]
        ws.cell(r, 3).value = "(reserved)" if f["item_name"] == "NaN" else f["item_name"]
        ws.cell(r, 4).value = f["offsetbit"]
        ws.cell(r, 5).value = bitlen
        ws.cell(r, 6).value = endian
        ws.cell(r, 7).value = dtype

        raw_terms = "+".join(f"_DestuffedBits!C{destuffed_rows[k]}*{weights[k]}" for k in range(bitlen))
        ws.cell(r, 8).value = f"={raw_terms}"

        if dtype == "float" and bitlen == 32:
            exp = f"_xlfn.BITAND(_xlfn.BITRSHIFT(H{r},23),255)"
            mant = f"_xlfn.BITAND(H{r},8388607)"
            sign = f"IF(_xlfn.BITAND(H{r},2147483648)=0,1,-1)"
            ws.cell(r, 9).value = (
                f"={sign}*IF({exp}=0,({mant}/8388608)*POWER(2,-126),"
                f"(1+{mant}/8388608)*POWER(2,{exp}-127))"
            )
        elif dtype.startswith("int"):
            half, full = 2 ** (bitlen - 1), 2 ** bitlen
            ws.cell(r, 9).value = f"=IF(H{r}>={half},H{r}-{full},H{r})"
        else:
            ws.cell(r, 9).value = f"=H{r}"

        parsed_enum, transform, lookup_text = resolve_lookup(f["item_name"], f["longdesc"], bitlen, enums_ctx)
        if parsed_enum:
            chain = f'H{r}&" (unlisted)"'
            for key, label in sorted(parsed_enum.items(), reverse=True):
                esc = str(label).replace('"', '""').replace("\n", " / ")
                chain = f'IF(H{r}={key},"{esc}",{chain})'
            ws.cell(r, 10).value = "=" + chain
        elif transform:
            scale = transform.get("scale")
            unit = str(transform.get("unit", "") or "").replace('"', '""')
            if scale is not None:
                ws.cell(r, 10).value = f'=ROUND(I{r}*{scale},4)&" {unit}"'
            else:
                ws.cell(r, 10).value = f'=I{r}&" {unit}"'
        elif dtype == "float":
            ws.cell(r, 10).value = f"=ROUND(I{r},6)"
        else:
            ws.cell(r, 10).value = f"=I{r}"

        ws.cell(r, 11).value = lookup_text.replace("\n", " / ") if lookup_text else "-"
        ws.cell(r, 11).alignment = Alignment(wrap_text=True, vertical="top")

    widths = {"A": 10, "B": 12, "C": 32, "D": 15, "E": 8, "F": 8, "G": 10,
              "H": 14, "I": 16, "J": 26, "K": 46}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return field_row


def _byte_value_terms(idx0_byte_start):
    """LSB-first weighted-sum terms for one byte's 8 bits, starting at
    absolute destuffed bit index idx0_byte_start -- same weight convention
    as bits_to_bytes() (transmission order, first bit = weight 1)."""
    return "+".join(f"_DestuffedBits!C{idx0_byte_start + k + 2}*{2 ** k}" for k in range(8))


def _bits_formula(idx0_start, bitlen):
    """Live concatenation of a field's raw bit VALUES in transmission order
    (no MSB-first reinterpretation, no LE byte-swap -- those only apply to
    the numeric decode in Fields, not to this plain display string; matches
    04_beacon_field_decode.py / 04c's own 'Bits' column, which is just
    str(int(b)) joined in order)."""
    return "=" + "&".join(f"_DestuffedBits!C{idx0_start + k + 2}" for k in range(bitlen))


def write_beacon_decode_sheet(wb, fields, field_row):
    """Per-field view styled like the old cut_first3_frame2_beacon_decode.xlsx
    output (Subsystem/ItemName/OffsetBit/BitLen/Bits/ReadableValue/
    LookupTable, one row per field with all of that field's bits merged),
    instead of RawBits' one-row-per-bit layout. ReadableValue/LookupTable
    just reference the Fields sheet (same live formulas RawBits points at);
    Bits and the 4 header-row / FCS-row values are computed fresh here since
    Fields doesn't cover the header or FCS.

    Known gap vs. the old static xlsx: the old version colored individual
    LOW-CONFIDENCE CHARACTERS red within the Bits string. Excel formulas
    can't return partially-colored text (rich text is only possible for
    static values), and this sheet needs to be live (re-derived from
    RawBits' current E/F choices), so that per-character highlighting is
    dropped here -- check RawBits' own LowConfidence column (red) for that,
    per-bit, instead."""
    ws = wb.create_sheet("Beacon Decode")
    headers = ["Subsystem", "ItemName", "OffsetBit", "BitLen", "Bits", "ReadableValue", "LookupTable"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(1, c).fill = HEADER_FILL

    row = 2
    header_defs = [
        ("HEADER", "Dest Address", 0, 56, "expect 'BN0CU ' (>>1 of each byte, ground-test reference)"),
        ("HEADER", "Src Address", 56, 56, "expect 'BN0SCX' (>>1 of each byte, ground-test reference)"),
        ("HEADER", "Control", 112, 8, "expect 0x03 (UI frame)"),
        ("HEADER", "PID", 120, 8, "expect 0xF0 (no layer-3)"),
    ]
    for subsystem, name, offset0, bitlen, lut in header_defs:
        ws.cell(row, 1).value = subsystem
        ws.cell(row, 2).value = name
        ws.cell(row, 3).value = offset0
        ws.cell(row, 4).value = bitlen
        ws.cell(row, 5).value = _bits_formula(offset0, bitlen)
        ws.cell(row, 5).font = Font(name="Consolas")
        if bitlen == 56:
            char_terms = []
            for b in range(6):
                bv = _byte_value_terms(offset0 + b * 8)
                char_terms.append(f'IF(AND(INT(({bv})/2)>=32,INT(({bv})/2)<127),CHAR(INT(({bv})/2)),"?")')
            ssid_bv = _byte_value_terms(offset0 + 6 * 8)
            ws.cell(row, 6).value = (
                '="\'"&' + "&".join(char_terms) + '&"\' (SSID byte=0x"&DEC2HEX(' + ssid_bv + ',2)&")"'
            )
        else:
            ws.cell(row, 6).value = f'="0x"&DEC2HEX({_byte_value_terms(offset0)},2)'
        ws.cell(row, 7).value = lut
        for c in range(1, 8):
            ws.cell(row, c).fill = SEG_FILLS["address"]
        row += 1

    for fi, f in enumerate(fields):
        frow = field_row[fi]
        idx0_start = INFO_BYTE0 * 8 + f["offsetbit"]
        bitlen = f["bitlen"]
        ws.cell(row, 1).value = f["subsystem"]
        ws.cell(row, 2).value = "(reserved)" if f["item_name"] == "NaN" else f["item_name"]
        ws.cell(row, 3).value = f["offsetbit"]
        ws.cell(row, 4).value = bitlen
        ws.cell(row, 5).value = _bits_formula(idx0_start, bitlen)
        ws.cell(row, 5).font = Font(name="Consolas")
        ws.cell(row, 6).value = f"=Fields!J{frow}"
        ws.cell(row, 7).value = f"=Fields!K{frow}"
        labels = {segment_for_byte((idx0_start + k) // 8) for k in range(bitlen)}
        fill = SEG_FILLS.get(next(iter(labels))) if len(labels) == 1 else SEG_FILLS["mixed"]
        if fill:
            for c in range(1, 8):
                ws.cell(row, c).fill = fill
        row += 1

    fcs_idx0 = 2176
    ws.cell(row, 1).value = "FCS"
    ws.cell(row, 2).value = "FCS (CRC-16/X.25, little-endian)"
    ws.cell(row, 3).value = fcs_idx0
    ws.cell(row, 4).value = 16
    ws.cell(row, 5).value = _bits_formula(fcs_idx0, 16)
    ws.cell(row, 5).font = Font(name="Consolas")
    ws.cell(row, 6).value = '="0x"&DEC2HEX(CRC_Check!D2,4)'
    ws.cell(row, 7).value = '="computed CRC="&CRC_Check!B1&"  transmitted FCS="&CRC_Check!B2&"  -> "&CRC_Check!B3'
    for c in range(1, 8):
        ws.cell(row, c).fill = SEG_FILLS["FCS"]

    widths = {"A": 12, "B": 32, "C": 12, "D": 8, "E": 40, "F": 26, "G": 50}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return ws


def build_workbook(out_path, frame_no, y, yc, start):
    offset_thr, hdr_err = calibrate_offset_threshold(y, start)
    A = float(np.median(np.abs(y)))
    margin = MARGIN_FRAC * A

    span_j1 = int(SEARCH_SAMPLES / SPS)
    idx_all = start + SPS * np.arange(0, span_j1) + PHASE
    idx_all = idx_all[idx_all < y.size]
    body_idx = idx_all[32:]                 # skip the 4 leading flag bytes
    y_at_bit = y[body_idx]
    yc_at_bit = yc[body_idx]

    best_offset, best_mism, best_n = search_best_offset(y_at_bit, yc_at_bit, offset_thr, margin)
    print(f"Frame#{frame_no}: best raw-bit offset={best_offset:+d}  "
          f"mismatches at VERIFIABLE positions only={best_mism}/{best_n}  "
          f"(REFERENCE_FRAME.md's own note expects offset=0 for frame#2)")

    n_core = len(REF_STUFFED)               # payload+FCS raw bits, incl. real stuff bits
    n_raw = min(len(y_at_bit) - max(best_offset, 0), n_core + TRAIL_EXTRA)
    decided, ambiguous, _ = decide_raw_bits(y_at_bit, yc_at_bit, offset_thr, margin, best_offset, n_raw)

    fields = load_beacon_fields(XLSX_PATH)
    with open(ENUMS_PATH, "r", encoding="utf-8") as fh:
        enums_ctx = json.load(fh)

    wb = openpyxl.Workbook()
    default_sheet = wb.active   # openpyxl always creates one empty "Sheet" up front
    write_instructions(
        wb, frame_no, n_raw, best_offset,
        n_verifiable_bits=sum(1 for p in range(len(REF_BITS)) if VERIFIABLE_BYTE[p // 8]),
        n_telemetry_bits=sum(1 for p in range(len(REF_BITS)) if not VERIFIABLE_BYTE[p // 8]),
    )
    wb.remove(default_sheet)
    field_row = write_fields_sheet(wb, fields, enums_ctx)
    write_rawbits(wb, decided, ambiguous, n_raw, len(fields))
    write_destuffed_hidden(wb, len(REF_BITS), len(fields))
    write_crc_check(wb)
    write_beacon_decode_sheet(wb, fields, field_row)

    # ver2 EXPERIMENT: _DestuffedBits is now VISIBLE (placed right after RawBits, so
    # it's easy to flip between "my per-bit decisions" and "the shifted/compacted
    # result" side by side) -- see write_destuffed_hidden's docstring and 使用說明's
    # ver2 note. Fields stays hidden; it's pure backing computation either way, not
    # meant to be read directly (Beacon Decode is the readable version of it).
    order = ["使用說明", "RawBits", "_DestuffedBits", "Beacon Decode", "CRC_Check", "Fields"]
    wb._sheets = [wb[name] for name in order]
    wb["Fields"].sheet_state = "hidden"

    os.makedirs(OUT_DIR, exist_ok=True)
    wb.save(out_path)
    print(f"Workbook saved: {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("audio", nargs="?", default=AUDIO,
                   help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    p.add_argument("--frame", type=int, default=2,
                   help="1-based frame index to build the workbook for (default: 2, the frame "
                        "REFERENCE_FRAME.md's alignment note was written against)")
    p.add_argument("--z-threshold", type=float, default=Z_THRESHOLD,
                   help=f"frame-detection z-score threshold (default: {Z_THRESHOLD})")
    return p.parse_args()


def main():
    global Z_THRESHOLD
    args = parse_args()
    Z_THRESHOLD = args.z_threshold

    y, fs = audio_io.read_audio(args.audio, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]

    template = build_header_template()
    starts, zs = detect_frame_starts(yc, template)
    print(f"Detected {len(starts)} frame(s): " +
          ", ".join(f"frame#{i}@{s} (z={z:.2f})" for i, (s, z) in enumerate(zip(starts, zs), start=1)))
    if not (1 <= args.frame <= len(starts)):
        raise SystemExit(f"--frame {args.frame} out of range: only {len(starts)} frame(s) detected")

    start = starts[args.frame - 1]
    stem = os.path.splitext(os.path.basename(args.audio))[0]
    out_path = os.path.join(OUT_DIR, f"{stem}_frame{args.frame}_destuff_interactive_ver2.xlsx")
    build_workbook(out_path, args.frame, y, yc, start)


if __name__ == "__main__":
    main()
