# -*- coding: utf-8 -*-
"""
04a_zscore_visualization.py -- visualize what Z_THRESHOLD=12.0 actually means:
plot the normalized-cross-correlation z-score curve (`detect_frame_starts`'s
`z` array, from 01/02/03/04's shared frame-detection method) across the whole
recording, with the threshold line and the noise floor drawn in, plus a
zoomed-in view of each detected peak.

Why this exists: Z_THRESHOLD is an empirical constant (see every folder's
`detect_frame_starts`) chosen because real frame-start peaks sit around
z~12-13 while the noise floor stays below ~5 -- but that claim is only ever
reported as printed numbers, never actually shown. This script computes the
exact same z array `04_beacon_field_decode.py` uses for frame detection and
plots it, so the gap between "noise" and "signal" -- and where 12.0 sits in
that gap -- is visible instead of asserted.

Usage: `python 04a_zscore_visualization.py [audio.ogg] [--z-threshold Z]`.
`--z-threshold` overrides Z_THRESHOLD=12.0 (calibrated on cut_first3.ogg only)
-- run this script first on any new recording, read the noise-ceiling/margin
numbers it prints, and pass a suitable value on to
`04_beacon_field_decode.py --z-threshold Z` for the actual decode. Don't
assume 12.0 carries over: a longer/noisier recording can have a noise
ceiling sitting right next to it (see the SatNOGS full-pass example in
04_Beacon/README.md).

Output: Figure/04a_zscore_<audio stem>.png
  - top panel: z-score vs. time for the whole recording (max-envelope
    downsampled for plotting speed -- this only ever *raises* a bin's value
    toward a nearby peak, so no true peak can be hidden by decimation),
    threshold line, noise-floor band, detected peaks marked.
  - bottom row: one zoomed panel per detected frame (full resolution, no
    downsampling), showing the actual peak shape against the threshold.
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE, GRAY, PURPLE, GREEN  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS, SPS = 48000, 5
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")

FLAGS4 = bytes([0x7E] * 4)
HEADER_ADDR = bytes.fromhex("849c6086aa4060849c60a686b0e1")   # Dest+Src address, 14 bytes

Z_THRESHOLD = 12.0
MIN_FRAME_GAP = 100_000
ZOOM_HALF_WIDTH = 4000     # samples shown on either side of each peak in the bottom row
ENVELOPE_BIN = 400         # max-pool bin width (samples) for the top panel's overview curve


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


def compute_zscore_curve(yc, template):
    """Same math as every folder's detect_frame_starts, but returning the
    full z array (not just the peaks) so it can be plotted."""
    L = template.size
    corr = np.correlate(yc, template, mode="valid")
    csum2 = np.concatenate(([0.0], np.cumsum(yc.astype(np.float64) ** 2)))
    window_energy = csum2[L:] - csum2[:-L]
    template_norm = np.sqrt(np.sum(template ** 2))
    R = corr / (template_norm * np.sqrt(window_energy) + 1e-12)
    z = R / np.std(R)
    return z


def find_peaks(z):
    """Identical peak-picking logic to detect_frame_starts: z > Z_THRESHOLD,
    deduped within MIN_FRAME_GAP, sorted by sample position."""
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


def max_envelope(z, bin_width):
    """Max-pool downsample for the overview plot: never hides a true peak
    (a bin's value can only be pulled up toward a peak inside it, never
    down), just cheaper to render than 1.7M raw points."""
    n_bins = z.size // bin_width
    trimmed = z[:n_bins * bin_width].reshape(n_bins, bin_width)
    env = trimmed.max(axis=1)
    env_x = np.arange(n_bins) * bin_width + bin_width // 2
    return env_x, env


def compute_noise_ceiling(z, starts):
    """Max z-score outside every detected peak's neighborhood -- the
    empirical "noise floor ceiling" the README/CLAUDE.md claim is ~5."""
    is_peak_nbhd = np.zeros(z.size, dtype=bool)
    for s in starts:
        lo, hi = max(0, s - MIN_FRAME_GAP // 2), min(z.size, s + MIN_FRAME_GAP // 2)
        is_peak_nbhd[lo:hi] = True
    outside = z[~is_peak_nbhd]
    return float(outside.max()) if outside.size else float(z.max())


MAX_ZOOM_COLS = 6   # wrap the zoom panels into multiple rows past this many frames


def plot_zscore(plt, z, starts, zs, fs, out_name, audio_path):
    noise_ceiling = compute_noise_ceiling(z, starts)
    n = len(starts)
    ncols = min(n, MAX_ZOOM_COLS) if n else 1
    nrows_zoom = -(-n // ncols) if n else 1   # ceil division

    fig = plt.figure(figsize=(max(18, 3 * ncols), 6 + 2.6 * nrows_zoom))
    gs = fig.add_gridspec(1 + nrows_zoom, ncols, height_ratios=[1.3] + [1] * nrows_zoom)
    ax_top = fig.add_subplot(gs[0, :])

    env_x, env = max_envelope(z, ENVELOPE_BIN)
    t = env_x / fs
    noise_mask = env < Z_THRESHOLD
    ax_top.fill_between(t, 0, env, where=noise_mask, color=GRAY, alpha=0.5,
                         step="mid", label="below threshold (noise floor)")
    ax_top.fill_between(t, 0, env, where=~noise_mask, color=BLUE, alpha=0.7,
                         step="mid", label="above threshold (detected frame)")
    ax_top.axhline(Z_THRESHOLD, color=ORANGE, lw=1.8, ls="--",
                    label=f"Z_THRESHOLD={Z_THRESHOLD:.1f}")
    ax_top.axhline(noise_ceiling, color=GRAY, lw=1.2, ls=":",
                    label=f"noise ceiling (max z outside any detected frame) = {noise_ceiling:.2f}")

    # stagger annotation height (2 levels) so closely-spaced peaks don't collide
    for i, (s, zv) in enumerate(zip(starts, zs), start=1):
        ax_top.plot(s / fs, zv, "o", color=PURPLE, ms=7, mec="k", mew=0.6, zorder=5)
        y_off = 10 if i % 2 else 26
        ax_top.annotate(f"#{i} z={zv:.2f}", xy=(s / fs, zv), xytext=(0, y_off),
                         textcoords="offset points", ha="center", fontsize=7.5, color=PURPLE,
                         bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=PURPLE, alpha=0.9))

    ax_top.set_xlim(t[0], t[-1])
    top_pad = max(env.max(), max(zs, default=Z_THRESHOLD)) + 5.0   # headroom for the staggered annotation boxes
    ax_top.set_ylim(min(0, env.min()), top_pad)
    ax_top.set_xlabel("time (s)")
    ax_top.set_ylabel("z-score = R / std(R)")
    ax_top.set_title("Normalized cross-correlation z-score across the whole recording "
                      f"({os.path.basename(audio_path)}) -- max-envelope, {ENVELOPE_BIN}-sample bins")
    ax_top.legend(loc="upper right", fontsize=8, ncol=2)

    for i, s in enumerate(starts):
        row, col = 1 + i // ncols, i % ncols
        ax = fig.add_subplot(gs[row, col])
        lo, hi = max(0, s - ZOOM_HALF_WIDTH), min(z.size, s + ZOOM_HALF_WIDTH)
        xs = np.arange(lo, hi)
        zz = z[lo:hi]
        ax.plot(xs, zz, "-", color=BLUE, lw=0.9)
        ax.axhline(Z_THRESHOLD, color=ORANGE, lw=1.5, ls="--")
        ax.fill_between(xs, Z_THRESHOLD, zz, where=zz > Z_THRESHOLD, color=BLUE, alpha=0.25)
        ax.plot(s, z[s], "o", color=PURPLE, ms=6, mec="k", mew=0.6, zorder=5)
        ax.set_xlim(lo, hi)
        ax.tick_params(axis="x", labelsize=7, rotation=20)
        ax.set_title(f"frame#{i + 1}  z={zs[i]:.2f}  @{s}", fontsize=8.5)
        ax.set_xlabel("sample index", fontsize=8)
        if col == 0:
            ax.set_ylabel("z-score", fontsize=8)

    fig.suptitle(f"Why Z_THRESHOLD={Z_THRESHOLD:.1f}: noise floor vs. the {n} detected frame-start peak(s)",
                 fontsize=13)
    p = save(fig, os.path.join("Figure", out_name))
    plt.close(fig)
    return p, noise_ceiling


def main():
    global Z_THRESHOLD

    parser = argparse.ArgumentParser(
        description="Visualize the frame-detection z-score curve and Z_THRESHOLD.")
    parser.add_argument("audio", nargs="?", default=AUDIO,
                         help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    parser.add_argument("--z-threshold", type=float, default=Z_THRESHOLD,
                         help=f"frame-detection z-score threshold (default: {Z_THRESHOLD}). "
                              "Calibrated on cut_first3.ogg -- a new recording's noise ceiling can sit "
                              "much closer to it (see the printed margins below); rerun with a value "
                              "read off this script's own plot/margins before trusting detection on it.")
    args = parser.parse_args()
    Z_THRESHOLD = args.z_threshold

    plt = setup_mpl()
    y, fs = audio_io.read_audio(args.audio, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]

    template = build_header_template()
    z = compute_zscore_curve(yc, template)
    starts, zs = find_peaks(z)

    print(f"{os.path.basename(args.audio)}: {y.size} samples ({y.size/fs:.1f}s), "
          f"z array length={z.size}")
    print(f"Detected {len(starts)} frame(s) above Z_THRESHOLD={Z_THRESHOLD}: " +
          ", ".join(f"frame#{i}@{s} (z={zv:.2f})" for i, (s, zv) in enumerate(zip(starts, zs), start=1)))

    stem = os.path.splitext(os.path.basename(args.audio))[0]
    out_name = f"04a_zscore_{stem}.png"
    p, noise_ceiling = plot_zscore(plt, z, starts, zs, fs, out_name, args.audio)
    print(f"Noise ceiling (max z outside any detected frame's neighborhood): {noise_ceiling:.2f}")
    print(f"Margin between noise ceiling and Z_THRESHOLD: {Z_THRESHOLD - noise_ceiling:.2f}")
    if starts:
        print(f"Margin between weakest real peak and Z_THRESHOLD: {min(zs) - Z_THRESHOLD:.2f}")
    print(f"\nFigure saved: {p}")
    print("\nIf the margins above look too tight (or too many/few frames got detected), rerun with "
          "--z-threshold set to a value that better separates the noise ceiling from the real peaks "
          "in the plot before running 04_beacon_field_decode.py on this recording.")


if __name__ == "__main__":
    main()
