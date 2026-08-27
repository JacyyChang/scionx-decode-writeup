#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_pickpoints_comparison.py -- for one near-miss candidate frame found by
symbol_sync_sweep.py, visualize WHERE its symbol decisions actually land on
the raw waveform, compared to the fixed-grid picking scheme used by
01/03 (idx(n) = start + SPS*n [+ PHASE]) on cut_first3.ogg.

Why this comparison matters: 01/03's pipeline assumes a perfectly constant
SPS=5 (=48000/9600) with a fixed PHASE offset, read straight off the sample
clock -- valid for cut_first3.ogg's SatNOGS-style capture, but these newer
recordings go through a raw SDR chain with no Doppler/clock correction (see
this folder's README). digital.symbol_sync_ff exists precisely to track a
*varying* effective SPS instead of assuming a fixed one -- this script makes
that difference visible instead of just asserted.

⚠️ IMPORTANT CAVEAT -- the "drift" this script draws is largely an artefact.

symbol_sync_ff does not expose its instantaneous per-symbol timing phase at
the Python level (checked -- no accessor on the block for this). So the
"Symbol Sync" grid here is an APPROXIMATION: a uniform grid at
avg_sps = (input samples consumed) / (output symbols produced) over the
WHOLE FILE, anchored at the candidate's estimated start.

That whole-file average is the problem. On these recordings ~29 of every 30
seconds are noise, where the loop free-runs, so avg_sps mostly describes the
loop's behaviour in noise -- NOT the frame's symbol rate. For seg017 it
reads 4.9234, which looks like a 1.5 % clock offset and produces a dramatic
"168 samples of cumulative drift" curve. Measured properly (fold the frame
into an eye diagram at candidate rates and compare decision margin at the
best vs worst sampling phase), the true in-frame rate is 5.000 -- only 5.000
opens an eye at all, and 4.9234 is indistinguishable from flat. See
README.md, "The symbol rate really is 5.000 samples/symbol".

So: this script is still useful for seeing WHERE the decisions land, but do
not read the divergence between the two grids as evidence of real clock
drift within a frame. For judging sampling phase quality, use
eye_fixed_grid.m instead (eye diagram + the corrected eye-opening metric:
weakest '1' minus strongest '0'), which measures the signal directly instead
of inferring from a loop statistic -- and, per README.md's "Fixed grid alone
decodes seg017 and seg002", is often enough on its own without running
Symbol Sync or this script at all.

Usage (run with the radioconda Python -- see this folder's README):
    python plot_pickpoints_comparison.py Output/some_segment.wav \\
        --ted GMSK --loop-bw 0.01 --damping 1.0 --max-dev 1.0
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)

from scionx.hdlc import find_candidate_bit_ranges  # noqa: E402
from scionx.audio_io import read_audio  # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE, GRAY  # noqa: E402
from symbol_sync_sweep import (  # noqa: E402
    run_symbol_sync, offline_deframe, _ted_value, TED_CHOICES, pack,
)


def _range_is_consistent(bits_used, r, frame_bytes):
    """find_candidate_bit_ranges' [start, end] is documented as an
    APPROXIMATE boundary (see its docstring) -- normally off by at most a
    couple bits, harmless for plotting. But on badly-corrupted/lost-lock
    data it can fail outright: a long stuck run of consecutive '1's (which
    can never happen in real bit-stuffed data -- stuffing guarantees at most
    5 in a row) gets misread by the flag/destuff state machine as if the
    *start* of that run were a flag immediately before the frame, producing
    a near-zero-width range paired with a frame hundreds of bytes long.
    Confirmed 2026-08-26 on loop_bw=0.001 (Symbol Sync fully lost lock).
    Verify by re-destuffing the raw slice and checking it reconstructs the
    same frame bytes offline_deframe already produced."""
    s, e = r
    raw = bits_used[s:e + 1]
    out, ones = [], 0
    for b in raw:
        if b:
            ones += 1
            out.append(1)
        else:
            if ones != 5:
                out.append(0)
            ones = 0
    pad = (-len(out)) % 8
    if pad:
        out = [0] * pad + out
    return pack(out) == frame_bytes


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--ted", choices=TED_CHOICES, required=True)
    ap.add_argument("--loop-bw", type=float, required=True)
    ap.add_argument("--damping", type=float, required=True)
    ap.add_argument("--max-dev", type=float, required=True)
    ap.add_argument("--sps", type=float, default=5.0,
                     help="nominal samples/symbol for the fixed-grid comparison "
                          "(default 5.0 = 48000/9600, same as 01/03)")
    ap.add_argument("--ted-gain", type=float, default=1.0)
    ap.add_argument("--frame-len", type=int, default=274)
    ap.add_argument("--near-slack", type=int, default=20)
    ap.add_argument("--polarity", choices=["auto", "norm", "inv"], default="auto",
                     help="which bit polarity to search (default auto = try both, "
                          "keep whichever finds more near-length candidates)")
    ap.add_argument("--n-edge-symbols", type=int, default=40,
                     help="how many symbols to show in each zoomed panel (default 40, "
                          "ignored when --whole is given)")
    ap.add_argument("--whole", action="store_true",
                     help="plot the entire candidate frame span instead of zoomed "
                          "start/end windows: full waveform+both grids overlaid, plus "
                          "a drift-vs-symbol-index panel showing the two grids' "
                          "cumulative divergence across the whole frame")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force-range", type=int, nargs=2, metavar=("BIT_START", "BIT_END"),
                     help="bypass the near-frame auto-detection and plot exactly this "
                          "raw bit range (0-based, inclusive) -- for inspecting a span "
                          "that _range_is_consistent rejected (e.g. a lost-lock stuck-bit "
                          "run misidentified as a frame by the flag parser), or any other "
                          "arbitrary bit span. Requires --polarity norm or inv (not auto).")
    return ap


def pick_polarity_and_frame(bits, near_lo, near_hi, polarity="auto"):
    """Same best-of-both-polarities logic as symbol_sync_sweep.score_bits,
    but also returns which bit array and which candidate frame/range to use
    for plotting (score_bits itself only returns summary counts).
    polarity="norm"/"inv" restricts the search to just that one instead of
    trying both."""
    candidates = {"norm": ("norm", bits), "inv": ("inv", 1 - bits)}
    to_try = [candidates[polarity]] if polarity in ("norm", "inv") else candidates.values()
    best = None
    for label, b in to_try:
        frames = offline_deframe(b)
        ranges = find_candidate_bit_ranges(b)
        if len(frames) != len(ranges):
            raise RuntimeError(
                f"offline_deframe found {len(frames)} frames but "
                f"find_candidate_bit_ranges found {len(ranges)} -- their state "
                f"machines should stay in lockstep, this needs investigating "
                f"before trusting the bit ranges below.")
        near = [(f, r) for f, r in zip(frames, ranges) if near_lo <= len(f) <= near_hi]
        # Drop candidates whose reported range doesn't actually reconstruct
        # their frame bytes -- see _range_is_consistent's docstring. Usually
        # none are dropped; on badly lost-lock data (e.g. loop_bw way too
        # narrow) most/all of them can be.
        consistent = [(f, r) for f, r in near if _range_is_consistent(b, r, f)]
        dropped = len(near) - len(consistent)
        if dropped:
            print(f"[{label}] dropped {dropped}/{len(near)} near-length candidate(s) "
                  f"whose bit range doesn't reconstruct their frame -- see "
                  f"_range_is_consistent's docstring (likely a lost-lock artifact).")
        if not consistent:
            continue
        # closest-to-frame_len candidate within this polarity
        target = (near_lo + near_hi) // 2
        f, r = min(consistent, key=lambda fr: abs(len(fr[0]) - target))
        score = len(consistent)
        if best is None or score > best[0]:
            best = (score, label, b, f, r)
    return best  # None, or (score, polarity_label, bits_used, frame_bytes, [start,end])


def main():
    args = build_parser().parse_args()
    wav = os.path.abspath(args.wav)
    near_lo, near_hi = args.frame_len - args.near_slack, args.frame_len + args.near_slack

    print(f"Running symbol_sync: ted={args.ted} loop_bw={args.loop_bw} "
          f"damping={args.damping} max_dev={args.max_dev} on {wav}")
    bits = run_symbol_sync(wav, _ted_value(args.ted), args.sps, args.loop_bw,
                            args.damping, args.ted_gain, args.max_dev)

    if args.force_range:
        if args.polarity not in ("norm", "inv"):
            print("--force-range requires --polarity norm or inv (not auto).")
            return
        bit_start, bit_end = args.force_range
        polarity = args.polarity
        bits_used = bits if polarity == "norm" else (1 - bits)
        frame_bytes = b""  # unknown/not meaningful -- we're bypassing frame detection
        print(f"Forced range: polarity={polarity}  bit range=[{bit_start}, {bit_end}]  "
              f"({bit_end - bit_start + 1} raw bits, out of {bits_used.size} total symbols) "
              f"-- not validated as a real frame.")
    else:
        result = pick_polarity_and_frame(bits, near_lo, near_hi, args.polarity)
        if result is None:
            print(f"No candidate frame in [{near_lo}, {near_hi}] bytes found with these "
                  f"parameters -- nothing to plot.")
            return
        _, polarity, bits_used, frame_bytes, (bit_start, bit_end) = result
        print(f"Best candidate: polarity={polarity}  len={len(frame_bytes)} bytes  "
              f"bit range=[{bit_start}, {bit_end}]  (out of {bits_used.size} total symbols)")

    y, fs = read_audio(wav)
    n_out_bits = bits.size
    avg_sps = len(y) / n_out_bits
    drift_per_symbol = avg_sps - args.sps
    n_frame_symbols = bit_end - bit_start
    total_drift = drift_per_symbol * n_frame_symbols
    print(f"avg_sps (this run) = {avg_sps:.5f}  vs nominal sps = {args.sps:.5f}  "
          f"-> drift/symbol = {drift_per_symbol:+.5f} samples\n"
          f"Over this {n_frame_symbols}-symbol candidate: cumulative drift "
          f"= {total_drift:+.1f} samples ({total_drift/args.sps:+.1f} symbol-widths)")

    sample_start = bit_start * avg_sps  # anchor: same for both grids
    import numpy as np

    def fixed_grid(n0, n1):
        return sample_start + args.sps * (n0 + np.arange(n1 - n0))

    def symsync_grid(n0, n1):
        return sample_start + avg_sps * np.arange(n0, n1)

    plt = setup_mpl()
    frame_desc = (f"FORCED RANGE (not a validated frame), polarity={polarity}"
                  if args.force_range else
                  f"candidate len={len(frame_bytes)}B, polarity={polarity}")
    suptitle = (
        f"{os.path.basename(wav)}  --  {args.ted} loop_bw={args.loop_bw} "
        f"damping={args.damping} max_dev={args.max_dev}  --  {frame_desc}\n"
        f"avg_sps={avg_sps:.4f} vs nominal {args.sps:.3f} -> "
        f"{total_drift:+.1f} sample drift over {n_frame_symbols} symbols "
        f"(approximate -- see script docstring)")

    if args.whole:
        idx_fix = fixed_grid(0, n_frame_symbols)
        idx_ss = symsync_grid(0, n_frame_symbols)
        lo = int(min(idx_fix.min(), idx_ss.min())) - 5
        hi = int(max(idx_fix.max(), idx_ss.max())) + 5
        lo, hi = max(0, lo), min(len(y), hi)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7),
                                        gridspec_kw={"height_ratios": [3, 1]})
        t = np.arange(lo, hi)
        ax1.plot(t, y[lo:hi], color=GRAY, lw=0.4, zorder=1, alpha=0.7, label="waveform")
        ax1.scatter(idx_fix, y[idx_fix.astype(int)], c=[BLUE], s=6, zorder=3,
                    marker="o", alpha=0.7, label=f"01/03 fixed grid (sps={args.sps:.3f})")
        ax1.scatter(idx_ss, y[idx_ss.astype(int)], c=[ORANGE], s=6, zorder=4,
                    marker="^", alpha=0.7, label=f"Symbol Sync avg grid (sps={avg_sps:.3f})")
        ax1.set_title(f"whole candidate frame ({n_frame_symbols} symbols)")
        ax1.set_xlabel("sample index")
        ax1.legend(loc="upper right", fontsize=8, markerscale=2)

        drift = idx_ss - idx_fix
        ax2.plot(np.arange(n_frame_symbols), drift, color=ORANGE, lw=1.5)
        ax2.axhline(0, color=GRAY, lw=0.8, ls="--")
        ax2.set_title("cumulative divergence between the two grids (Symbol Sync - fixed)")
        ax2.set_xlabel("symbol index within candidate frame")
        ax2.set_ylabel("sample offset")

        fig.suptitle(suptitle, fontsize=9)
        stem = os.path.splitext(os.path.basename(wav))[0]
        out_name = args.out or (f"Output/{stem}_pickpoints_whole_{args.ted}_"
                                 f"lb{args.loop_bw}_d{args.damping}_md{args.max_dev}.png")
    else:
        fig, axes = plt.subplots(2, 1, figsize=(11, 7))
        n_edge = args.n_edge_symbols
        windows = [
            (axes[0], 0, min(n_edge, n_frame_symbols), "start of candidate frame"),
            (axes[1], max(0, n_frame_symbols - n_edge), n_frame_symbols, "end of candidate frame"),
        ]
        for ax, n0, n1, label in windows:
            idx_fix = fixed_grid(n0, n1)
            idx_ss = symsync_grid(n0, n1)
            lo = int(min(idx_fix.min(), idx_ss.min())) - 5
            hi = int(max(idx_fix.max(), idx_ss.max())) + 5
            lo, hi = max(0, lo), min(len(y), hi)
            t = np.arange(lo, hi)
            ax.plot(t, y[lo:hi], color=GRAY, lw=0.8, zorder=1, label="waveform")
            ok = (idx_fix >= lo) & (idx_fix < hi)
            ax.scatter(idx_fix[ok], y[idx_fix[ok].astype(int)], c=[BLUE], s=30, zorder=3,
                       marker="o", edgecolors="k", linewidths=0.4,
                       label=f"01/03 fixed grid (sps={args.sps:.3f})")
            ok = (idx_ss >= lo) & (idx_ss < hi)
            ax.scatter(idx_ss[ok], y[idx_ss[ok].astype(int)], c=[ORANGE], s=30, zorder=4,
                       marker="^", edgecolors="k", linewidths=0.4,
                       label=f"Symbol Sync avg grid (sps={avg_sps:.3f})")
            ax.set_title(f"{label}  (symbols {n0}-{n1} of candidate)")
            ax.set_xlabel("sample index")
            ax.legend(loc="upper right", fontsize=8)
        fig.suptitle(suptitle, fontsize=9)
        stem = os.path.splitext(os.path.basename(wav))[0]
        out_name = args.out or (f"Output/{stem}_pickpoints_{args.ted}_"
                                 f"lb{args.loop_bw}_d{args.damping}_md{args.max_dev}.png")

    path = save(fig, out_name)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
