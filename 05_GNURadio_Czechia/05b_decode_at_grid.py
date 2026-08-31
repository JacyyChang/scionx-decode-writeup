# -*- coding: utf-8 -*-
"""
05b_decode_at_grid.py -- find and decode EVERY distinct HDLC/AX.25
CRC-16/X.25-passing frame in a wav (or a restricted sample-index window of
one), scanning a phase/polarity/slicer neighbourhood around a given
(sps, phase) sampling grid. This is the "decode" half of the 05b step; the
"locate" half (s05b_header_locate_decode.m) finds candidate header positions
via waveform cross-correlation and calls this script once per candidate
cluster with a phase estimate AND a position window to test.

Order note: this is Step 2 of the pipeline, run BEFORE 05c_destuff_interactive.py
(Step 3, which absorbed the old standalone crop tool 2026-08-31: `--seg N`
crops a window before destuffing it, or `--seg N --crop-only` just crops)
now -- run this first against the whole 05a output to see which seg(s)
actually decode, THEN crop/inspect only the ones worth a closer look.

Scans the WHOLE requested grid (no early exit on the first pass) and
deduplicates hits by frame byte content -- one real frame typically
CRC-passes across many nearby phase values (the eye can stay open for
60-90% of the symbol period, per eye_fixed_grid.m's findings), so without
dedup a single frame would be reported dozens of times; two genuinely
different frames always differ in content, so every distinct frame in the
scanned region is still surfaced.

--start-sample/--end-sample restrict which ABSOLUTE sample positions get
sliced into symbols (phase stays in the WHOLE FILE's coordinate system
either way -- no shifting needed). This is what makes per-candidate,
per-seg decoding fast: s05b_header_locate_decode.m calls this once per
(seg, polarity) group with a ~22000-sample window around that group's best
correlation candidate instead of the whole file, cutting offline_deframe's
pure-Python per-bit loop from ~37M samples down to ~22000 -- seconds instead
of ~2 minutes per call. --seg-label is a pure passthrough string (e.g.
"seg017") so a CRC_PASS line says which seg it came from without this
script needing to know anything about the 30s-block convention itself.

Adapted from appendix_gnuradio/Recovery_try/decode_with_grid.py (validated
2026-08-31: reproduces seg017's and seg002's confirmed fixed-grid CRC-passing
decode bit-identically) -- same logic, relocated into 05_GNURadio_Czechia and
with the --json option dropped (the MATLAB half passes --sps/--phase
directly instead of reading a clock-tone estimate file).

idx(n) = phase + sps*n is applied by linear interpolation (never rounded --
at sps=5 a rounded position injects up to half a sample, 10% of a symbol
period, of phase error), and the HDLC flag search (offline_deframe) finds
frames wherever they land within the (possibly windowed) bit range -- so
this does not need to know exactly where the candidate is; it only needs a
phase (mod sps) close enough for --scan-phase to cover, and a window wide
enough to contain one whole frame (~11000 samples at sps=5).

Usage:
    python 05b_decode_at_grid.py <wav> --sps 5.0 --phase 2.3
    python 05b_decode_at_grid.py <wav> --sps 5.0 --phase 2.3 --scan-phase 2.5 --scan-phase-step 0.1
    python 05b_decode_at_grid.py <wav> --sps 5.0 --phase 2.3 --polarity both --slicer both
    python 05b_decode_at_grid.py <wav> --sps 5.0 --phase 2.3 \\
        --start-sample 25394251 --end-sample 25416251 --seg-label seg017
"""
import argparse
import os
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
APPENDIX = os.path.join(REPO_ROOT, "appendix_gnuradio")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, APPENDIX)   # for symbol_sync_sweep.py's offline_deframe/fcs_ok/pack

from scionx.audio_io import read_audio                        # noqa: E402
from scionx.slicer import binary_slice, min_max_dynamic_threshold  # noqa: E402
from symbol_sync_sweep import offline_deframe, fcs_ok          # noqa: E402

FRAME_LEN = 274          # 272-byte payload + 2-byte FCS, per REFERENCE_FRAME.md
REFERENCE_HEADER = bytes.fromhex(
    "84 9c 60 86 aa 40 60 84 9c 60 a6 86 b0 e1 03 f0".replace(" ", ""))


def slice_grid(samples, sps, phase, lo=None, hi=None):
    """idx(n) = phase + sps*n, restricted to absolute sample range [lo, hi]
    if given. phase stays in the WHOLE FILE's absolute coordinate system
    either way -- only which n's get emitted is restricted, so a caller
    windowing the search never needs to shift phase to compensate."""
    n_total = len(samples)
    hi = n_total - 1 if hi is None else min(hi, n_total - 1)
    lo = 0 if lo is None else max(lo, 0)
    if hi < lo:
        return np.array([])
    n0 = int(np.ceil((lo - phase) / sps))
    n1 = int(np.floor((hi - phase) / sps))
    if n1 < n0:
        return np.array([])
    pos = phase + sps * np.arange(n0, n1 + 1)
    return np.interp(pos, np.arange(n_total), samples)


def bits_from_vals(vals, polarity, slicer, dyn_window):
    v = polarity * vals
    if slicer == "fixed":
        return binary_slice(v)
    return min_max_dynamic_threshold(v, dyn_window)[1]


def ax25_callsign(addr7):
    return bytes((b >> 1) for b in addr7[:6]).decode("ascii", errors="replace")


def frange(center, half_width, step):
    if half_width <= 0 or step <= 0:
        return [center]
    k = int(round(half_width / step))
    return [center + i * step for i in range(-k, k + 1)]


def try_decode(samples, sps_list, phase_list, pol_list, slicer_list, dyn_window, lo=None, hi=None):
    """Scan the WHOLE grid (no early exit) and collect every DISTINCT
    CRC-passing frame found, deduplicated by byte content -- the same
    physical frame typically CRC-passes across many nearby phase values
    (the eye can stay open for 60-90% of the symbol period, per
    eye_fixed_grid.m's findings), so without dedup a single real frame would
    be reported dozens of times. Two genuinely different frames (different
    position/content) always produce different bytes, so this also
    naturally surfaces every distinct frame in the scanned range, not just
    the first one found."""
    lengths = Counter()
    n_combos = 0
    hits = {}   # frame bytes -> first (sps, phase, pol, slicer) that produced it
    for sps in sps_list:
        for phase in phase_list:
            vals = slice_grid(samples, sps, phase, lo, hi)
            for pol_name, pol in pol_list:
                for slicer in slicer_list:
                    n_combos += 1
                    bits = bits_from_vals(vals, pol, slicer, dyn_window)
                    for frame in offline_deframe(bits):
                        lengths[len(frame)] += 1
                        if len(frame) == FRAME_LEN and fcs_ok(frame) and frame not in hits:
                            hits[frame] = dict(sps=sps, phase=phase, pol=pol_name, slicer=slicer)
    return hits, lengths, n_combos


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--sps", type=float, required=True, help="symbol period in samples")
    ap.add_argument("--phase", type=float, required=True, help="absolute sampling phase mod sps")
    ap.add_argument("--polarity", choices=["norm", "inv", "both"], default="both")
    ap.add_argument("--scan-phase", type=float, default=2.5, help="+/- half-width to scan around phase")
    ap.add_argument("--scan-phase-step", type=float, default=0.1)
    ap.add_argument("--scan-sps", type=float, default=0.0, help="+/- half-width to scan around sps")
    ap.add_argument("--scan-sps-step", type=float, default=0.0005)
    ap.add_argument("--slicer", choices=["fixed", "dynamic", "both"], default="fixed")
    ap.add_argument("--dyn-window", type=int, default=16, help="min-max dynamic-threshold window (symbols)")
    ap.add_argument("--start-sample", type=int, default=None,
                     help="restrict slicing to this absolute sample index onward (whole-file "
                          "coordinates; phase is unaffected). Makes offline_deframe's per-bit "
                          "scan fast enough for per-candidate use instead of whole-file scans.")
    ap.add_argument("--end-sample", type=int, default=None,
                     help="restrict slicing up to this absolute sample index")
    ap.add_argument("--seg-label", default=None,
                     help="pure passthrough string (e.g. 'seg017') echoed in CRC_PASS/NO_CRC_PASS "
                          "lines -- this script doesn't compute it, the caller already knows it")
    args = ap.parse_args()

    pol_list = {"both": [("norm", +1), ("inv", -1)],
                "norm": [("norm", +1)], "inv": [("inv", -1)]}[args.polarity]
    slicer_list = ["fixed", "dynamic"] if args.slicer == "both" else [args.slicer]
    sps_list = frange(args.sps, args.scan_sps, args.scan_sps_step)
    phase_list = frange(args.phase, args.scan_phase, args.scan_phase_step)
    seg_tag = f"[{args.seg_label}] " if args.seg_label else ""

    print(f"Reading {args.wav} ...")
    samples, fs = read_audio(args.wav)
    print(f"{len(samples)} samples @ {fs} Hz ({len(samples)/fs:.2f} s)\n")
    if args.start_sample is not None or args.end_sample is not None:
        print(f"{seg_tag}Window: samples [{args.start_sample}, {args.end_sample}]")
    print(f"{seg_tag}Grid: sps={args.sps:.6f} (+/-{args.scan_sps}), phase={args.phase:.4f} (+/-{args.scan_phase})")
    print(f"{seg_tag}Scanning {len(sps_list)} sps x {len(phase_list)} phase x "
          f"{len(pol_list)} pol x {len(slicer_list)} slicer ...\n")

    t0 = time.time()
    hits, lengths, n_combos = try_decode(
        samples, sps_list, phase_list, pol_list, slicer_list, args.dyn_window,
        lo=args.start_sample, hi=args.end_sample)
    print(f"{seg_tag}Scanned {n_combos} combinations in {time.time()-t0:.1f}s.\n")

    if not hits:
        print(f"{seg_tag}NO_CRC_PASS.")
        if lengths:
            print("Candidate frame lengths seen (len: count):")
            for L in sorted(lengths):
                print(f"  {L:4d} bytes : {lengths[L]}")
        else:
            print("offline_deframe emitted NO candidate frames at all "
                  "-- no HDLC flags found under any tested grid/slicer.")
        sys.exit(1)

    print(f"{seg_tag}{len(hits)} distinct CRC-passing frame(s) found "
          f"(deduplicated by byte content across the scanned range):\n")
    for i, (frame, result) in enumerate(hits.items(), 1):
        payload, fcs = frame[:-2], frame[-2:]
        header = payload[:16]
        print(f"===== frame {i}/{len(hits)} =====")
        print(f"{seg_tag}CRC_PASS sps={result['sps']:.6f} phase={result['phase']:.4f} "
              f"polarity={result['pol']} slicer={result['slicer']}\n")
        print("Full frame hex dump:")
        for j in range(0, len(frame), 16):
            print(f"{j:04x}: " + frame[j:j + 16].hex(' '))
        print(f"\nHeader match vs REFERENCE_FRAME.md: {header == REFERENCE_HEADER}")
        dest, src = payload[0:7], payload[7:14]
        print(f"Dest: {ax25_callsign(dest)!r}  Src: {ax25_callsign(src)!r}\n")


if __name__ == "__main__":
    main()
