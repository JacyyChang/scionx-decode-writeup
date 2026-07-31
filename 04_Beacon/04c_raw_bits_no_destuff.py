# -*- coding: utf-8 -*-
"""
04c_raw_bits_no_destuff.py -- dump one frame's RAW symbol decisions, grouped
into bytes, with NO destuffing applied at all -- so the accumulated bit
offset can be judged by eye, independent of whatever `destuff_with_map` /
`destuff_with_events` decided.

Why this exists: 04_beacon_field_decode.py's alignment check (and
04b_stuffing_events.py's per-event view) both trust the destuffing state
machine to say how many bits were removed and where. This script trusts
nothing -- it's the plain threshold=0 decision at every symbol from frame
start through well past the trailing flags, with stuffed 0s left IN. That
makes two things directly visible without relying on any of this repo's own
interpretation:
  - Byte 0 onward should match REFERENCE_FRAME.md's plain (unstuffed) bytes
    exactly, for as long as no real HDLC stuffing has occurred yet (address/
    header realistically never needs it). The "Ref (unstuffed)" column shows
    this for the first len(REF)+2 bytes as a direct check.
  - Once real stuffing starts, raw bytes stop matching REF (expected -- REF
    has no stuffed bits, the wire does) and drift by however many stuffed
    bits have accumulated. Scanning down to where a repeating 01111110
    pattern visibly starts (flagged in the Note column) and comparing that
    raw byte index to 274 + (bits/8) tells you the total accumulated offset
    without trusting any destuffing code at all.

Deliberately minimal/read-only: no destuffing, no theta* re-decision, no
field lookup. Just the raw bits and enough context to eyeball them.

Usage: `python 04c_raw_bits_no_destuff.py [audio.ogg] [--frame N] [--z-threshold Z]`.
Defaults to frame#2 of ../Data/cut_first3.ogg (same default as 04b).

Output: `Output/<audio stem>_frame<N>_raw_no_destuff.xlsx` -- a NEW file,
never overwrites the regular <audio stem>_frame<N>_beacon_decode.xlsx.
"""

import argparse
import os
import sys

import numpy as np
import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Font, PatternFill

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS, SPS = 48000, 5
PHASE = 2
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")
OUT_DIR = os.path.join(HERE, "Output")

FLAGS4 = bytes([0x7E] * 4)
HEADER_ADDR = bytes.fromhex("849c6086aa4060849c60a686b0e1")

Z_THRESHOLD = 12.0
MIN_FRAME_GAP = 100_000
SEARCH_SAMPLES = 19211
MARGIN_FRAC = 0.10

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
FRAME_END_BYTE = 274      # 272-byte payload + 2-byte FCS, in DESTUFFED terms -- see docstring
FLAG_BITS = [0, 1, 1, 1, 1, 1, 1, 0]


def find_flag_run(bits, lo, hi, min_run=3):
    """Search at EVERY bit offset (not just byte boundaries) for a run of
    `min_run` back-to-back 0x7E flags. Byte-boundary scanning alone can miss
    the real trailing flags entirely if the accumulated raw offset isn't a
    multiple of 8 -- which it usually won't be, since real HDLC stuffing
    inserts single bits, not whole bytes."""
    seq = bits.tolist() if hasattr(bits, "tolist") else list(bits)
    n = len(seq)
    for p in range(max(0, lo), min(hi, n - 8 * min_run + 1)):
        if all(seq[p + 8 * k: p + 8 * k + 8] == FLAG_BITS for k in range(min_run)):
            return p
    return None


def bits_lsb_first(data):
    return [(b >> k) & 1 for b in data for k in range(8)]


def bits_to_byte(bits8):
    v = 0
    for k in range(8):
        v |= int(bits8[k]) << k
    return v


def build_header_template():
    bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.float64)
    return np.repeat(2 * bits - 1, SPS)


def detect_frame_starts(yc, template):
    L = template.size
    corr = np.correlate(yc, template, mode="valid")
    csum2 = np.concatenate(([0.0], np.cumsum(yc.astype(np.float64) ** 2)))
    R = corr / (np.sqrt(np.sum(template ** 2)) * np.sqrt(csum2[L:] - csum2[:-L]) + 1e-12)
    z = R / np.std(R)
    candidates = np.where(z > Z_THRESHOLD)[0]
    candidates = candidates[np.argsort(-z[candidates])]
    starts = []
    for idx in candidates:
        if all(abs(int(idx) - s) > MIN_FRAME_GAP for s in starts):
            starts.append(int(idx))
    order = np.argsort(starts)
    starts = [starts[i] for i in order]
    return starts, [float(z[s]) for s in starts]


RED = InlineFont(color="FFCC0000")
FLAG_FILL = PatternFill("solid", fgColor="FFDFF3E6")     # byte == exactly 0x7E
REF_FILL = PatternFill("solid", fgColor="FFDCEAF7")      # still inside the REF comparison range
HEADER_FILL = PatternFill("solid", fgColor="FFDCE9F5")


def bits_richtext(bits_str, ambig):
    runs, cur_c, cur_t = [], None, ""
    for ch, is_red in zip(bits_str, ambig):
        c = "red" if is_red else "black"
        if c != cur_c and cur_t:
            runs.append((cur_c, cur_t))
            cur_t = ""
        cur_c, cur_t = c, cur_t + ch
    if cur_t:
        runs.append((cur_c, cur_t))
    parts = [TextBlock(RED, t) if c == "red" else t for c, t in runs]
    return CellRichText(*parts) if parts else CellRichText("")


def main():
    global Z_THRESHOLD
    parser = argparse.ArgumentParser(
        description="Dump one frame's raw (un-destuffed) symbol decisions, byte by byte.")
    parser.add_argument("audio", nargs="?", default=AUDIO,
                         help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    parser.add_argument("--frame", type=int, default=2,
                         help="1-based frame index (default: 2)")
    parser.add_argument("--z-threshold", type=float, default=Z_THRESHOLD,
                         help=f"frame-detection z-score threshold (default: {Z_THRESHOLD})")
    args = parser.parse_args()
    Z_THRESHOLD = args.z_threshold

    y, fs = audio_io.read_audio(args.audio, expected_fs=FS)
    yc = baseline.restore_baseline(y, num_iters=7, W=1000)["y_comp_final"]
    template = build_header_template()
    starts, zs = detect_frame_starts(yc, template)
    print(f"Detected {len(starts)} frame(s): " +
          ", ".join(f"frame#{i}@{s} (z={z:.2f})" for i, (s, z) in enumerate(zip(starts, zs), start=1)))
    if not (1 <= args.frame <= len(starts)):
        raise SystemExit(f"--frame {args.frame} out of range: only {len(starts)} frame(s) detected")
    start = starts[args.frame - 1]
    print(f"Using frame#{args.frame} at sample={start}")

    A = float(np.median(np.abs(y)))
    margin = MARGIN_FRAC * A

    # Raw symbols, threshold=0, NO destuffing -- byte 0 = the first symbol right
    # after the 4 leading flags (same skip convention as every other script here,
    # an exact 32-bit/4-byte skip so this alignment is unaffected by stuffing).
    span_j1 = int(SEARCH_SAMPLES / SPS)
    idx_all = start + SPS * np.arange(0, span_j1) + PHASE
    idx_all = idx_all[idx_all < y.size]
    idx_all = idx_all[32:]
    raw_bits = (y[idx_all] > 0).astype(np.uint8)
    n_bytes = raw_bits.size // 8
    print(f"{n_bytes} raw bytes available (no destuffing), covering byte 0 (=address start) "
          f"through byte {n_bytes - 1}")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Frame{args.frame}_RawNoDestuff"
    headers = ["ByteIndex", "Bits", "Hex", "ASCII", "Ref (unstuffed)", "Note"]
    ws.append([f"Raw threshold=0 decisions, grouped into bytes, NO destuffing applied. "
              f"Red = |y|<={margin:.3f} (near threshold=0, low confidence). "
              f"Once real HDLC stuffing occurs the Ref column stops matching -- expected, "
              f"REF has no stuffed bits, the wire does."])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    ws.cell(1, 1).font = Font(italic=True, size=9)
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(2, c)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
    ws.freeze_panes = "A3"

    ref_ext = REF + b"\x00\x00"   # placeholder length-match for the FCS bytes; real FCS differs per frame
    for i in range(n_bytes):
        bits8 = raw_bits[i * 8:(i + 1) * 8]
        bits_str = "".join(str(int(b)) for b in bits8)
        ambig = np.abs(y[idx_all[i * 8:(i + 1) * 8]]) <= margin
        byte_val = bits_to_byte(bits8)
        ascii_c = chr(byte_val >> 1) if 0x20 <= (byte_val >> 1) < 0x7F else ""
        ref_hex = f"0x{ref_ext[i]:02X}" if i < len(ref_ext) else ""
        is_flag = "<< looks like 0x7E" if byte_val == 0x7E else ""

        ws.append([i, None, f"0x{byte_val:02X}", ascii_c, ref_hex, is_flag])
        r = ws.max_row
        ws.cell(r, 2).value = bits_richtext(bits_str, ambig)
        ws.cell(r, 2).font = Font(name="Consolas")
        if byte_val == 0x7E:
            for c in range(1, len(headers) + 1):
                ws.cell(r, c).fill = FLAG_FILL
        elif i < len(REF):
            for c in range(1, len(headers) + 1):
                ws.cell(r, c).fill = REF_FILL

    ws.append([])
    r = ws.max_row + 1
    ws.cell(r, 1).value = (f"Expected (if destuffing were correct): closing flag run starts at "
                           f"raw byte {FRAME_END_BYTE} + (stuffed bits actually on the wire)/8. "
                           f"Scan down to where 0x7E repeats and compare its byte index to that.")
    ws.cell(r, 1).font = Font(italic=True, size=9, bold=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(headers))

    widths = {"A": 10, "B": 34, "C": 8, "D": 8, "E": 14, "F": 20}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    # Answer "how many bits offset, ignoring destuffing entirely" directly:
    # search the raw bit stream at every bit phase (not just byte boundaries)
    # for a run of real flags, independent of any destuffing code.
    flag_bit = find_flag_run(raw_bits, 0, raw_bits.size)
    print()
    if flag_bit is None:
        print("No run of 3+ back-to-back 0x7E flags found anywhere in the raw stream.")
    else:
        expected_no_stuff = FRAME_END_BYTE * 8      # if there were zero stuffed bits at all
        print(f"Trailing flag run found at RAW bit {flag_bit} (byte {flag_bit/8:.2f}, "
              f"phase {flag_bit % 8} within the byte grid -- non-zero phase means it does NOT "
              f"land on a byte boundary, so it won't show up as a clean 0x7E row in the sheet).")
        print(f"If there were zero stuffed bits anywhere, this would be at raw bit "
              f"{expected_no_stuff} (byte {FRAME_END_BYTE}). Difference = "
              f"{flag_bit - expected_no_stuff:+d} bits -- this is the total number of bits actually "
              f"stuffed onto the wire before the closing flag, measured directly from the raw "
              f"signal with NO destuffing code involved at all.")

    stem = os.path.splitext(os.path.basename(args.audio))[0]
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{stem}_frame{args.frame}_raw_no_destuff.xlsx")
    wb.save(out_path)
    print(f"Workbook saved: {out_path}")


if __name__ == "__main__":
    main()
