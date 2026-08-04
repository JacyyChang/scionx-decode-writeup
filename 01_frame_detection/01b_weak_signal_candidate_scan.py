# -*- coding: utf-8 -*-
"""
01b_weak_signal_candidate_scan.py -- two-stage candidate scan for frames too
weak for 01_frame_detection.py's coherent 144-symbol correlation to catch.

Why this exists: on satnogs_14674078_2026-08-03T07-50-10.ogg, the z-score
correlation detector (01_frame_detection.py) finds nothing anywhere in the
606s recording -- max z=5.1, indistinguishable from the noise floor (z<5 on
cut_first3.ogg). But a manually-inspected candidate at t=183.5-183.9s shows
independent evidence of being a real (very low-SNR) packet: it's an FM-
quieting POWER DIP (not a rise -- confirmed on all 3 known-good cut_first3
frames: RMS ratio 0.43-0.51 inside vs. outside), duration 0.375s (vs. the
known frame length 19211/48000=0.400s), and its zero-crossing intervals
cluster at multiples of SPS=5 the way a real symbol stream does, unlike the
surrounding noise. Individually none of these is definitive; the coherent
correlation needs all 144 symbols in phase to accumulate gain, which a single
bit error already starts to erode, and at low SNR that gain collapses
entirely (z drops to the noise floor even when cropped tightly around the
candidate -- verified, this isn't a windowing/normalization artifact).
Combining independent, cheaper evidence first is the way around that:

  Stage 1 (find_power_dips): block-wise RMS over the WHOLE recording (no
  baseline restoration -- this is the fast, coarse pass), looking for
  contiguous dips below a fraction of the LOCAL median power (chunked, not
  global, so slow AGC/elevation-related drift across a pass doesn't bias the
  threshold) lasting roughly one frame's duration. This is what actually
  narrows a multi-minute recording down to a short candidate list cheaply.

  Stage 2 (score_symbol_structure): for each stage-1 candidate only, run the
  real baseline.restore_baseline on just that small window (cheap now that
  it's not the whole file) and check whether zero-crossing intervals cluster
  at multiples of SPS the way real AFSK symbols do, scored RELATIVE to a
  same-length noise control window immediately before the candidate (so this
  self-calibrates per-recording instead of relying on an absolute threshold
  that may not transfer between recordings/SNRs).

Candidates are ranked by combining both scores; nothing here decodes a
frame -- it only narrows down WHERE to point 01_frame_detection.py's own
correlation search (optionally at a lower --z-threshold) or 04_Beacon's
tools next.

Usage:
    python 01b_weak_signal_candidate_scan.py [audio.ogg] [--start SEC] [--end SEC]
    python 01b_weak_signal_candidate_scan.py path/to/long.ogg --dip-ratio 0.6 --top 15

Output: console ranking table + Figure/01b_candidate<rank>_<audio stem>.png
for the top few candidates (RMS context + raw waveform, for eyeballing).
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE, GRAY   # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS, SPS = 48000, 5
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")

FRAME_DURATION_S = 19211 / FS   # known cut_first3 frame length, ~0.400s -- the duration a real dip should be near
BLOCK_MS = 20.0                  # RMS block size for stage 1 -- must be coarse enough to average OVER a
                                  # symbol period's own tone-driven amplitude wobble (AFSK's 1200/2200Hz
                                  # tones alone make raw RMS fluctuate a lot cycle-to-cycle at finer
                                  # resolutions -- 5ms was tried first and fragmented even the 3 KNOWN-GOOD
                                  # cut_first3.ogg frames into many sub-threshold-duration pieces, finding
                                  # ZERO candidates there; this is a mandatory sanity check, see main())
LOCAL_MEDIAN_CHUNK_S = 10.0     # window for the "local" (not global) median power, to tolerate slow AGC/elevation drift


def block_rms(y, fs, block_ms):
    win = max(1, int(fs * block_ms / 1000))
    n_blocks = y.size // win
    if n_blocks == 0:
        return np.array([]), win
    blocks = y[:n_blocks * win].reshape(n_blocks, win)
    return np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1)), win


def local_median(rms, chunk_blocks):
    """Per-chunk median, broadcast back to per-block resolution (blocky, not
    smoothly interpolated -- fine for a threshold reference, and avoids
    pulling in scipy for a proper rolling median)."""
    n = rms.size
    n_chunks = int(np.ceil(n / chunk_blocks))
    med = np.empty(n)
    for c in range(n_chunks):
        lo, hi = c * chunk_blocks, min(n, (c + 1) * chunk_blocks)
        med[lo:hi] = np.median(rms[lo:hi])
    return med


def moving_average(x, k):
    """Cumsum-based moving average, window k, no padding (output is
    x.size-k+1 long -- index i covers x[i:i+k])."""
    if k <= 1:
        return x.copy()
    if x.size < k:
        return np.array([])
    csum = np.concatenate(([0.0], np.cumsum(x)))
    return (csum[k:] - csum[:-k]) / k


def find_power_dips(y, fs, dip_ratio, window_s):
    """Stage 1: find window_s-long windows whose MEAN RMS is well below the
    local median. Deliberately NOT "every single block in a run must
    individually be below threshold" -- that was tried first and finds ZERO
    candidates even in cut_first3.ogg's 3 KNOWN-GOOD frames, because AFSK's
    own tone-driven envelope wobbles in and out of any fixed ratio from
    block to block (verified: even 20ms blocks swing between ~0.3 and ~0.8
    of the local median WITHIN a single real frame -- there's no genuinely
    flat-bottomed dip to threshold block-by-block). Averaging over a window
    sized to one frame's duration is what actually recovers the signal,
    matching how the very first manual check that found this candidate
    worked (a single mean over the whole span, not a per-block test) -- this
    is functionally a matched filter for "a window_s-long region of reduced
    power" (window_s should be set to the expected frame duration; see
    --window / FRAME_DURATION_S).

    Returns list of dicts (start_s, end_s, duration_s, depth_ratio=
    mean_inside/local_median -- lower is a deeper, more frame-like dip),
    deduplicated the same way 01_frame_detection.py's own z-score peaks are
    (greedy, best-first, minimum separation)."""
    rms, win = block_rms(y, fs, BLOCK_MS)
    if rms.size == 0:
        return []
    chunk_blocks = max(1, int(LOCAL_MEDIAN_CHUNK_S * 1000 / BLOCK_MS))
    med = local_median(rms, chunk_blocks)

    k = max(1, int(round(window_s * 1000 / BLOCK_MS)))
    mavg = moving_average(rms, k)
    mavg_med = moving_average(med, k)
    if mavg.size == 0:
        return []
    ratio = mavg / np.maximum(mavg_med, 1e-12)

    below = np.where(ratio < dip_ratio)[0]
    below = below[np.argsort(ratio[below])]   # lowest (deepest) ratio first
    picked = []
    for idx in below:
        if all(abs(int(idx) - p) >= k for p in picked):
            picked.append(int(idx))
    picked.sort()

    return [{
        "start_s": i * win / fs, "end_s": (i + k) * win / fs,
        "duration_s": k * win / fs, "depth_ratio": float(ratio[i]),
    } for i in picked]


def zero_cross_multiple5_fraction(yc, sps=SPS, max_gap=40):
    """Fraction of zero-crossing intervals landing exactly on a multiple of
    sps (capped at max_gap) -- real symbol transitions land on SPS-spaced
    sample boundaries; uncorrelated noise doesn't prefer any particular
    spacing, so this fraction is a cheap tell without needing full symbol
    sync."""
    s = np.sign(yc)
    s[s == 0] = 1
    zc = np.where(np.diff(s) != 0)[0]
    if zc.size < 2:
        return 0.0, 0
    d = np.diff(zc)
    d = d[d <= max_gap]
    if d.size == 0:
        return 0.0, 0
    mult = d % sps == 0
    return float(mult.sum() / d.size), int(d.size)


def score_symbol_structure(y_full, fs, cand, pad_s=0.05):
    """Stage 2: crop tightly around the candidate (plus a small pad), run
    real baseline restoration on just that crop (cheap -- a few thousand
    samples, not the whole recording), and compare its multiple-of-SPS
    zero-crossing fraction against a same-length noise control window taken
    immediately before the candidate. Returns (cand_frac, noise_frac, ratio)
    -- ratio > 1 means the candidate looks more symbol-structured than the
    noise right next to it, which is the self-calibrating signal this relies
    on (an absolute fraction threshold doesn't transfer across recordings at
    different SNR, but "more structured than my own neighboring noise" does)."""
    s0 = max(0, int((cand["start_s"] - pad_s) * fs))
    s1 = min(y_full.size, int((cand["end_s"] + pad_s) * fs))
    seg = y_full[s0:s1]
    bl = baseline.restore_baseline(seg, num_iters=7, W=1000)
    cand_frac, cand_n = zero_cross_multiple5_fraction(bl["y_comp_final"])

    ctrl_len = s1 - s0
    c1 = max(0, s0 - ctrl_len)
    if c1 >= s0:
        return cand_frac, None, None
    ctrl = y_full[c1:s0]
    bl_ctrl = baseline.restore_baseline(ctrl, num_iters=7, W=1000)
    noise_frac, noise_n = zero_cross_multiple5_fraction(bl_ctrl["y_comp_final"])

    ratio = cand_frac / noise_frac if noise_frac > 1e-9 else float("inf")
    return cand_frac, noise_frac, ratio


def plot_candidate(plt, y, fs, cand, rank, stem, t_offset):
    ctx = 1.0   # seconds of context on each side
    s0 = max(0, int((cand["start_s"] - ctx) * fs))
    s1 = min(y.size, int((cand["end_s"] + ctx) * fs))
    seg = y[s0:s1]
    t = t_offset + (s0 + np.arange(seg.size)) / fs

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t, seg, color=GRAY, lw=0.3, alpha=0.7)
    ax.axvspan(t_offset + cand["start_s"], t_offset + cand["end_s"], color=ORANGE, alpha=0.25,
               label=f"candidate dip: {cand['duration_s']:.3f}s, depth={cand['depth_ratio']:.2f}")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("amplitude")
    ax.set_title(f"Candidate #{rank}: t={t_offset+cand['start_s']:.3f}-{t_offset+cand['end_s']:.3f}s  "
                 f"struct_ratio={cand.get('struct_ratio', float('nan')):.2f}")
    ax.legend(loc="upper right", fontsize=8)
    p = save(fig, os.path.join("Figure", f"01b_candidate{rank}_{stem}.png"))
    plt.close(fig)
    return p


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("audio", nargs="?", default=AUDIO,
                   help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    p.add_argument("--start", type=float, default=None, help="only scan from this time onward (seconds)")
    p.add_argument("--end", type=float, default=None, help="stop scanning at this time (seconds)")
    p.add_argument("--dip-ratio", type=float, default=0.6,
                   help="stage-1 threshold: RMS below this fraction of the local median counts as a dip (default: 0.6)")
    p.add_argument("--window", type=float, default=FRAME_DURATION_S,
                    help="matched-filter window length, seconds -- should match the expected frame "
                         f"duration (default: {FRAME_DURATION_S:.3f}, cut_first3.ogg's own frame length)")
    p.add_argument("--top", type=int, default=10, help="how many top-ranked candidates to report/plot (default: 10)")
    return p.parse_args()


def main():
    args = parse_args()
    t0 = args.start or 0.0

    y, fs = audio_io.read_audio(args.audio, expected_fs=FS, start_sec=args.start, end_sec=args.end)
    print(f"Loaded {y.size/fs:.1f}s starting at t={t0:.1f}s.")
    print(f"Reference frame duration (cut_first3.ogg): {FRAME_DURATION_S:.3f}s")

    print("\n[Stage 1] scanning for power dips "
          f"(ratio<{args.dip_ratio}, window={args.window:.3f}s)...")
    dips = find_power_dips(y, fs, args.dip_ratio, args.window)
    print(f"Found {len(dips)} stage-1 candidate(s).")
    if not dips:
        print("No candidates -- try raising --dip-ratio.")
        return

    dips.sort(key=lambda c: c["depth_ratio"])   # deepest (most frame-like) first
    dips = dips[:max(args.top * 3, args.top)]   # cap stage-2 work; stage-2 re-sorts anyway

    print(f"\n[Stage 2] scoring symbol structure for top {len(dips)} stage-1 candidate(s)...")
    for i, cand in enumerate(dips, start=1):
        cand_frac, noise_frac, ratio = score_symbol_structure(y, fs, cand)
        cand["struct_frac"] = cand_frac
        cand["noise_frac"] = noise_frac
        cand["struct_ratio"] = ratio if ratio is not None else float("nan")
        print(f"  [{i}/{len(dips)}] t={t0+cand['start_s']:7.3f}s  dur={cand['duration_s']:.3f}s  "
              f"depth={cand['depth_ratio']:.2f}  struct_frac={cand_frac:.2f}  "
              f"noise_frac={noise_frac if noise_frac is not None else float('nan'):.2f}  "
              f"ratio={cand['struct_ratio']:.2f}")

    dips.sort(key=lambda c: (-(c["struct_ratio"] if c["struct_ratio"] == c["struct_ratio"] else -1),
                              c["depth_ratio"]))

    print("\n" + "=" * 96)
    print(f"Ranked candidates (top {min(args.top, len(dips))}), best evidence first:")
    print(f"{'rank':>4} {'time(s)':>10} {'dur(s)':>7} {'depth':>6} {'struct%':>8} "
          f"{'noise%':>7} {'ratio':>6}")
    for i, cand in enumerate(dips[:args.top], start=1):
        print(f"{i:>4} {t0+cand['start_s']:>10.3f} {cand['duration_s']:>7.3f} "
              f"{cand['depth_ratio']:>6.2f} {100*cand['struct_frac']:>7.1f}% "
              f"{100*(cand['noise_frac'] or 0):>6.1f}% {cand['struct_ratio']:>6.2f}")
    print("=" * 96)
    print("depth: RMS(inside)/RMS(local median) -- lower = deeper dip, closer to known-good 0.43-0.51")
    print("struct%/noise%: fraction of zero-crossing intervals at multiples of SPS=5, candidate vs. "
          "a same-length noise window right before it")
    print("ratio: struct% / noise% -- >1 means more symbol-structured than its own local noise "
          "(the self-calibrating signal this scan actually relies on)")

    plt = setup_mpl()
    stem = os.path.splitext(os.path.basename(args.audio))[0]
    for i, cand in enumerate(dips[:args.top], start=1):
        p = plot_candidate(plt, y, fs, cand, i, stem, t0)
        print(f"Figure saved: {p}")


if __name__ == "__main__":
    main()
