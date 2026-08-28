# -*- coding: utf-8 -*-
"""
seg010_header_ber.py -- quantify HOW CLOSE seg010 is to a decode, rather than
just "CRC fail". The first 16 bytes of every SCION-X frame (address + control
+ PID) are constant (REFERENCE_FRAME.md), so for any recovered 274-byte
candidate frame we can count the Hamming distance of its 16-byte header
(128 bits) to the reference. That distance is a direct, SNR-facing proxy for
the raw bit error rate at the decision instants:

  * a few bits off  -> the frame is real and nearly recoverable; a better
    slicer / baseline removal might cross the line.
  * dozens off      -> the eye is genuinely closed (noise/baseline limited)
    and no timing tweak will help.

Scans the clock-tone grid (small phase neighbourhood) x both polarities x the
min-max dynamic-threshold slicer (window sweep), and reports the minimum
header Hamming distance seen, plus the byte-level diff of the best candidate.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
APPENDIX = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(APPENDIX)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, APPENDIX)

from scionx.audio_io import read_audio                            # noqa: E402
from scionx.slicer import binary_slice, min_max_dynamic_threshold # noqa: E402
from symbol_sync_sweep import offline_deframe, fcs_ok             # noqa: E402

REF = bytes.fromhex("849c6086aa4060849c60a686b0e103f0")
WAV = os.path.join(APPENDIX, "Output", "20260723_091639_seg010_300-330s.wav")
SPS_EST, PHASE_EST = 4.99941, 1.622   # from clock_tone_20260723...seg010.json


def slice_grid(samples, sps, phase):
    n = int((len(samples) - 1 - phase) / sps)
    pos = phase + sps * np.arange(n + 1)
    pos = pos[(pos >= 0) & (pos <= len(samples) - 1)]
    return np.interp(pos, np.arange(len(samples)), samples)


def hamming16(frame):
    """min header Hamming distance over the 16-byte header of a 274-byte frame."""
    hdr = frame[:16]
    return sum(bin(hdr[i] ^ REF[i]).count("1") for i in range(16))


def main():
    samples, fs = read_audio(WAV)
    print(f"{WAV}\n{len(samples)} samples @ {fs} Hz\n")

    best = (999, None, None)   # (hdist, params, frame)
    n274 = 0
    crc_pass = 0
    for sps in [SPS_EST, 5.0, 5.0006, 4.9994]:
        for phase in np.arange(0.0, sps, 0.25):
            vals = slice_grid(samples, sps, phase)
            for pol_name, pol in [("norm", +1), ("inv", -1)]:
                v = pol * vals
                slicers = {"fixed": binary_slice(v)}
                for w in [8, 12, 16, 24, 32]:
                    slicers[f"dyn{w}"] = min_max_dynamic_threshold(v, w)[1]
                for sname, bits in slicers.items():
                    for frame in offline_deframe(bits):
                        if len(frame) != 274:
                            continue
                        n274 += 1
                        if fcs_ok(frame):
                            crc_pass += 1
                        hd = hamming16(frame)
                        if hd < best[0]:
                            best = (hd, dict(sps=round(sps, 5),
                                             phase=round(float(phase), 3),
                                             pol=pol_name, slicer=sname), frame)

    print(f"274-byte candidates seen: {n274}   CRC passes: {crc_pass}\n")
    hd, params, frame = best
    if frame is None:
        print("No 274-byte candidate frame produced at all.")
        return
    print(f"Best header match: {hd} / 128 bits wrong  ({100*hd/128:.1f}% header BER)")
    print(f"  at {params}\n")
    print("  header decoded:  " + frame[:16].hex(" "))
    print("  header expected: " + REF.hex(" "))
    diff = " ".join("^^" if frame[i] != REF[i] else ".." for i in range(16))
    print("  byte diff:       " + diff)


if __name__ == "__main__":
    main()
