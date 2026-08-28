# -*- coding: utf-8 -*-
"""
print_seg002_decode.py -- reproduce and print the fixed-grid decode of
Output/20260723_091639_seg002_60-90s.wav (segment "seg002", one of the two
confirmed decodes from the "New recordings" survey -- see this folder's
README, "Fixed grid alone decodes seg017 *and* seg002").

Pure numpy + soundfile, NO GNU Radio needed: this is exactly the
`01`/`03`-style fixed-rate grid (idx(n) = phase + SPS*n, SPS=5.0, no timing
feedback) that the README found sufficient for seg002 -- Symbol Sync was
shown to be unnecessary (actively harmful for this segment, see README). The
HDLC destuff/CRC state machine reused here (`offline_deframe`/`pack`/
`fcs_ok`) is copied from `symbol_sync_sweep.py`'s bit-exact replay of
satellites.hdlc_deframer.work(), confirmed against the real GNU Radio block
via `hdlc_bitorder_test.grc` (LSB-first is the on-wire byte order).

Scans every integer-tenth-of-a-sample phase across one whole symbol period
(0.0 .. 4.9 in steps of 0.1) and both bit polarities (FM-demod baseline
polarity can come out either way), stops at the first 274-byte CRC-16/X.25
pass, and prints:
  - the full hex dump of the decoded frame (272-byte payload + 2-byte FCS)
  - the 16-byte address+control+PID header vs. REFERENCE_FRAME.md's constant
    header (same satellite, so this should match byte-for-byte even though
    the telemetry payload itself differs frame to frame)
  - the two AX.25 callsigns recovered by >>1'ing the address bytes, as a
    human-readable sanity check alongside the raw hex

Usage:
    python print_seg002_decode.py
    python print_seg002_decode.py Output/some_other_segment.wav
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)

from scionx.audio_io import read_audio       # noqa: E402
from scionx.slicer import binary_slice       # noqa: E402
from symbol_sync_sweep import offline_deframe, fcs_ok  # noqa: E402

DEFAULT_WAV = os.path.join(HERE, "Output", "20260723_091639_seg002_60-90s.wav")
SPS = 5.0
FRAME_LEN = 274          # 272-byte payload + 2-byte FCS, per REFERENCE_FRAME.md
REFERENCE_HEADER = bytes.fromhex(
    "84 9c 60 86 aa 40 60 84 9c 60 a6 86 b0 e1 03 f0".replace(" ", ""))


def slice_at_phase(samples, phase):
    """idx(n) = phase + SPS*n, rounded to the nearest sample -- the same
    fixed-rate grid as 01/03, just parameterized by a scanned phase instead
    of the pipeline's fixed PHASE=2."""
    n_symbols = int((len(samples) - phase) / SPS)
    idx = np.round(phase + SPS * np.arange(n_symbols)).astype(int)
    idx = idx[(idx >= 0) & (idx < len(samples))]
    return binary_slice(samples[idx])


def ax25_callsign(addr7):
    """7 address bytes (6 callsign + 1 SSID) -> printable callsign, each
    byte >>1 per AX.25 address encoding (see REFERENCE_FRAME.md)."""
    chars = bytes((b >> 1) for b in addr7[:6])
    return chars.decode("ascii", errors="replace")


def find_decode(samples):
    for phase in np.arange(0.0, SPS, 0.1):
        bits = slice_at_phase(samples, phase)
        for polarity, b in (("norm", bits), ("inv", 1 - bits)):
            for frame in offline_deframe(b):
                if len(frame) == FRAME_LEN and fcs_ok(frame):
                    return phase, polarity, frame
    return None


def main():
    wav = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV
    print(f"Reading {wav} ...")
    samples, fs = read_audio(wav)
    print(f"{len(samples)} samples @ {fs} Hz ({len(samples)/fs:.2f} s)\n")

    print(f"Scanning phase 0.0-4.9 (step 0.1 sample) x 2 polarities, "
          f"sps={SPS} ...")
    result = find_decode(samples)
    if result is None:
        print("No 274-byte CRC-16/X.25 pass found.")
        sys.exit(1)

    phase, polarity, frame = result
    payload, fcs = frame[:-2], frame[-2:]
    header = payload[:16]

    print(f"\n>>> DECODED: phase={phase:.1f}, polarity={polarity}, "
          f"{len(frame)} bytes, FCS OK\n")

    print("Full frame hex dump (272-byte payload + 2-byte FCS):")
    for j in range(0, len(frame), 16):
        print(f"{j:04x}: " + frame[j:j + 16].hex(' '))

    print(f"\nFCS bytes: {fcs.hex(' ')}")

    print("\nHeader check (bytes 0x00-0x0F, addr+control+PID) vs. "
          "REFERENCE_FRAME.md:")
    print(f"  decoded:  {header.hex(' ')}")
    print(f"  expected: {REFERENCE_HEADER.hex(' ')}")
    print(f"  match: {header == REFERENCE_HEADER}")

    dest, src = payload[0:7], payload[7:14]
    print(f"\nDest callsign: {ax25_callsign(dest)!r} (SSID byte "
          f"{dest[6]:#04x})")
    print(f"Src  callsign: {ax25_callsign(src)!r} (SSID byte "
          f"{src[6]:#04x})")
    print(f"Control: {payload[14]:#04x}   PID: {payload[15]:#04x}")


if __name__ == "__main__":
    main()
