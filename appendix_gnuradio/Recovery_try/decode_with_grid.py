# -*- coding: utf-8 -*-
"""
decode_with_grid.py -- attempt an HDLC/AX.25 decode using a corrected,
open-loop sampling grid idx(n) = phase + sps*n, where `sps` and `phase` come
from clock_tone_locate.m's feedforward clock-tone estimate (its JSON output)
rather than being assumed to be exactly 5.0.

This generalizes print_seg002_decode.py in three ways:
  1. `sps` is a free parameter (not hardcoded 5.0) so a per-segment corrected
     symbol period from the clock-tone estimator can be applied.
  2. Sample positions are taken by LINEAR INTERPOLATION, never np.round --
     at sps=5 a rounded position injects up to half a sample (10% of a
     symbol period) of phase error, the very quantity being corrected here.
  3. It can slice with EITHER a fixed threshold-at-0 (scionx.slicer.
     binary_slice, the oracle used for the confirmed seg002 decode) OR a
     min-max dynamic threshold (scionx.slicer.min_max_dynamic_threshold),
     which tracks baseline wander -- needed for segments like seg010 whose
     demodulated waveform rides on a large DC offset that a fixed 0 threshold
     mis-slices.

The whole file is sliced (idx(n) marches across all 1.44M samples) and handed
to offline_deframe, so the HDLC flag search -- not any assumed frame_start --
finds the frame. A pass is a 274-byte frame (272 payload + 2 FCS) whose
CRC-16/X.25 checks out; the script then prints the same report as
print_seg002_decode.py (hex dump, FCS, 16-byte header vs REFERENCE_FRAME.md,
both AX.25 callsigns). On no pass it prints how many (sps, phase, polarity,
slicer) combinations were tried and a length histogram of every candidate
frame offline_deframe emitted -- which tells you whether flags were found at
all, far more useful than a bare "no decode".

Usage:
    python decode_with_grid.py Output/<seg>.wav --sps 5.0007 --phase 0.88
    python decode_with_grid.py Output/<seg>.wav --json Recovery_try/clock_tone_<stem>.json
    python decode_with_grid.py Output/<seg>.wav --json ... --slicer dynamic --dyn-window 16
    # scan a neighbourhood around the estimate:
    python decode_with_grid.py Output/<seg>.wav --json ... \
        --scan-phase 0.5 --scan-phase-step 0.05 --scan-sps 0.002 --scan-sps-step 0.0005
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
APPENDIX = os.path.dirname(HERE)               # appendix_gnuradio
REPO_ROOT = os.path.dirname(APPENDIX)          # 1_Share
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, APPENDIX)

from scionx.audio_io import read_audio                        # noqa: E402
from scionx.slicer import binary_slice, min_max_dynamic_threshold  # noqa: E402
from symbol_sync_sweep import offline_deframe, fcs_ok         # noqa: E402

FRAME_LEN = 274          # 272-byte payload + 2-byte FCS, per REFERENCE_FRAME.md
REFERENCE_HEADER = bytes.fromhex(
    "84 9c 60 86 aa 40 60 84 9c 60 a6 86 b0 e1 03 f0".replace(" ", ""))


def slice_grid(samples, sps, phase):
    """idx(n) = phase + sps*n across the whole file, taken by linear
    interpolation (never rounded). Returns the interpolated symbol
    amplitudes (NOT yet sliced), so a polarity or a dynamic threshold can be
    applied afterwards."""
    n_syms = int((len(samples) - 1 - phase) / sps)
    pos = phase + sps * np.arange(n_syms + 1)
    pos = pos[(pos >= 0) & (pos <= len(samples) - 1)]
    return np.interp(pos, np.arange(len(samples)), samples)


def bits_from_vals(vals, polarity, slicer, dyn_window):
    """vals -> 0/1 bits, applying polarity and the chosen slicer."""
    v = polarity * vals
    if slicer == "fixed":
        return binary_slice(v)
    return min_max_dynamic_threshold(v, dyn_window)[1]


def ax25_callsign(addr7):
    """7 address bytes (6 callsign + 1 SSID) -> printable callsign, each byte
    >>1 per AX.25 address encoding (see REFERENCE_FRAME.md)."""
    return bytes((b >> 1) for b in addr7[:6]).decode("ascii", errors="replace")


def try_decode(samples, sps_list, phase_list, pol_list, slicer_list, dyn_window):
    """Scan every (sps, phase, polarity, slicer); return the first CRC-passing
    274-byte frame plus its parameters, or None. Also returns a Counter-like
    dict of every candidate frame length seen (diagnostic)."""
    from collections import Counter
    lengths = Counter()
    n_combos = 0
    for sps in sps_list:
        for phase in phase_list:
            vals = slice_grid(samples, sps, phase)
            for pol_name, pol in pol_list:
                for slicer in slicer_list:
                    n_combos += 1
                    bits = bits_from_vals(vals, pol, slicer, dyn_window)
                    for frame in offline_deframe(bits):
                        lengths[len(frame)] += 1
                        if len(frame) == FRAME_LEN and fcs_ok(frame):
                            return (dict(sps=sps, phase=phase, pol=pol_name,
                                         slicer=slicer, frame=frame),
                                    lengths, n_combos)
    return None, lengths, n_combos


def frange(center, half_width, step):
    """center, or center +/- half_width in `step` increments if half_width>0."""
    if half_width <= 0 or step <= 0:
        return [center]
    k = int(round(half_width / step))
    return [center + i * step for i in range(-k, k + 1)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--json", help="clock_tone_<stem>.json to read sps/phase/polarity from")
    ap.add_argument("--sps", type=float, help="symbol period in samples (overrides --json)")
    ap.add_argument("--phase", type=float, help="absolute sampling phase mod sps (overrides --json)")
    ap.add_argument("--polarity", choices=["norm", "inv", "both"], default="both")
    ap.add_argument("--scan-phase", type=float, default=0.0, help="+/- half-width to scan around phase")
    ap.add_argument("--scan-phase-step", type=float, default=0.05)
    ap.add_argument("--scan-sps", type=float, default=0.0, help="+/- half-width to scan around sps")
    ap.add_argument("--scan-sps-step", type=float, default=0.0005)
    ap.add_argument("--slicer", choices=["fixed", "dynamic", "both"], default="fixed")
    ap.add_argument("--dyn-window", type=int, default=16, help="min-max dynamic-threshold window (symbols)")
    args = ap.parse_args()

    # resolve wav path (accept relative-to-cwd or relative-to-appendix)
    wav = args.wav
    if not os.path.isfile(wav):
        alt = os.path.join(APPENDIX, wav)
        if os.path.isfile(alt):
            wav = alt

    sps, phase, pol_json = args.sps, args.phase, None
    if args.json:
        with open(args.json) as f:
            j = json.load(f)
        if sps is None:
            sps = j["sps_est"]
        if phase is None:
            phase = j["phase_sample"]
        pol_json = "norm" if j.get("polarity", 1) >= 0 else "inv"
    if sps is None:
        sps = 5.0
    if phase is None:
        phase = 0.0

    # polarity list
    if args.polarity == "both":
        pol_list = [("norm", +1), ("inv", -1)]
    elif args.polarity == "norm":
        pol_list = [("norm", +1)]
    else:
        pol_list = [("inv", -1)]

    slicer_list = ["fixed", "dynamic"] if args.slicer == "both" else [args.slicer]

    sps_list = frange(sps, args.scan_sps, args.scan_sps_step)
    phase_list = frange(phase, args.scan_phase, args.scan_phase_step)

    print(f"Reading {wav} ...")
    samples, fs = read_audio(wav)
    print(f"{len(samples)} samples @ {fs} Hz ({len(samples)/fs:.2f} s)\n")

    print(f"Grid: sps={sps:.6f} (scan +/-{args.scan_sps} step {args.scan_sps_step}), "
          f"phase={phase:.4f} (scan +/-{args.scan_phase} step {args.scan_phase_step})")
    print(f"Polarity: {args.polarity}   Slicer: {args.slicer}"
          f"{' (window %d)' % args.dyn_window if args.slicer in ('dynamic','both') else ''}")
    print(f"Scanning {len(sps_list)} sps x {len(phase_list)} phase x "
          f"{len(pol_list)} pol x {len(slicer_list)} slicer ...\n")

    result, lengths, n_combos = try_decode(
        samples, sps_list, phase_list, pol_list, slicer_list, args.dyn_window)

    if result is None:
        print(f"No 274-byte CRC-16/X.25 pass found across {n_combos} combinations.")
        if lengths:
            print("\nCandidate frame lengths emitted by offline_deframe "
                  "(len: count) -- flags WERE found where this is non-empty:")
            for L in sorted(lengths):
                print(f"  {L:4d} bytes : {lengths[L]}")
        else:
            print("\noffline_deframe emitted NO candidate frames at all -- "
                  "no HDLC flags detected under any tested grid/slicer.")
        sys.exit(1)

    frame = result["frame"]
    payload, fcs = frame[:-2], frame[-2:]
    header = payload[:16]

    print(f">>> DECODED: sps={result['sps']:.6f}, phase={result['phase']:.4f}, "
          f"polarity={result['pol']}, slicer={result['slicer']}, "
          f"{len(frame)} bytes, FCS OK\n")

    print("Full frame hex dump (272-byte payload + 2-byte FCS):")
    for jrow in range(0, len(frame), 16):
        print(f"{jrow:04x}: " + frame[jrow:jrow + 16].hex(' '))

    print(f"\nFCS bytes: {fcs.hex(' ')}")
    print("\nHeader check (bytes 0x00-0x0F) vs. REFERENCE_FRAME.md:")
    print(f"  decoded:  {header.hex(' ')}")
    print(f"  expected: {REFERENCE_HEADER.hex(' ')}")
    print(f"  match: {header == REFERENCE_HEADER}")

    dest, src = payload[0:7], payload[7:14]
    print(f"\nDest callsign: {ax25_callsign(dest)!r} (SSID byte {dest[6]:#04x})")
    print(f"Src  callsign: {ax25_callsign(src)!r} (SSID byte {src[6]:#04x})")
    print(f"Control: {payload[14]:#04x}   PID: {payload[15]:#04x}")


if __name__ == "__main__":
    main()
