# -*- coding: utf-8 -*-
"""
01a_waveform_power_overview.py -- fast first look at a NEW recording, before
running 01_frame_detection.py's real (slower, correlation-based) detector:
plots (1) the raw waveform and (2) a block-wise RMS power envelope over time,
so candidate signal bursts are visible by eye at a glance.

Why this exists: 01_frame_detection.py is hardcoded to cut_first3.ogg and
runs `baseline.restore_baseline`'s iterative decision-directed pass (needed
for correct symbol decisions, but not needed just to see WHERE the bursts
are). For a brand new, possibly multi-minute recording, that's the wrong
first step -- you want a cheap plot to orient yourself (how many bursts, how
long, do they even look like a 9600bps GFSK packet) before spending time on
the real per-frame pipeline. This script does no baseline restoration at all
and no frame detection; it's purely "what does this file look like".

Speed, for long recordings:
  - `--start`/`--end` (seconds) are passed straight through to
    `scionx.audio_io.read_audio`'s new start_sec/end_sec params, which seek
    with soundfile and decode ONLY that time range -- not "read everything,
    then slice", so narrowing the range actually skips decoding work, not
    just plotting work.
  - The waveform subplot decimates with a min/max-per-pixel-column bucketing
    (the standard audio-waveform-viewer trick, controlled by --max-points) so
    short bursts/spikes stay visible even though most samples in a bucket are
    dropped -- but only once the loaded range is at least --no-decimate-below
    seconds long (default 5.0). Below that, every sample is plotted directly:
    you've zoomed in to look at detail, which is exactly what decimation
    would throw away. A multi-minute range with the default 6000-column
    decimation stays fast; a several-second range plotted at full resolution
    is still fine for matplotlib, just no longer instant.
  - The power subplot is naturally already a downsampled view (one value per
    --window-ms block), so no separate decimation is needed there.

Usage:
    python 01a_waveform_power_overview.py [audio.ogg]
    python 01a_waveform_power_overview.py path/to/long.ogg --start 30 --end 90
    python 01a_waveform_power_overview.py path/to/long.ogg --window-ms 10 --max-points 10000

Output: Figure/01a_overview_<audio stem>[_<start>-<end>s].png
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io                          # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE      # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS = 48000
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")
MAX_DIRECT_POINTS = 50_000   # hard safety cap on plain plot()'d points, regardless of
                              # --no-decimate-below -- protects against a slow machine or an
                              # accidentally-wide "short" window turning into a very slow render;
                              # still ~8x more detail than the default 6000-column decimation


def minmax_decimate(y, fs, max_points, no_decimate_below_s):
    """Bucket y into ~max_points columns and keep each bucket's (min, max) --
    preserves narrow spikes/bursts that plain every-Nth-sample decimation
    would silently skip over, while keeping the point count (and therefore
    render time) roughly constant regardless of input length.

    Below no_decimate_below_s AND under MAX_DIRECT_POINTS samples, skip
    bucketing entirely and return every sample: at that point you're zoomed
    in to look at detail (individual cycles, edges of a dip), which is
    exactly what decimation throws away. Below no_decimate_below_s but OVER
    MAX_DIRECT_POINTS (e.g. --no-decimate-below set high, or just a wide
    "short" window), fall back to bucketing at MAX_DIRECT_POINTS columns
    instead of the default max_points -- still far more detail than a normal
    long-range view, just with a bounded, predictable render cost."""
    n = y.size
    if n / fs < no_decimate_below_s and n <= MAX_DIRECT_POINTS:
        return np.arange(n), y, y   # short enough to just plot directly, full resolution
    target = MAX_DIRECT_POINTS if n / fs < no_decimate_below_s else max_points
    bucket = int(np.ceil(n / target))
    n_buckets = int(np.ceil(n / bucket))
    pad = n_buckets * bucket - n
    y_pad = np.pad(y, (0, pad), mode="edge") if pad else y
    blocks = y_pad.reshape(n_buckets, bucket)
    x = (np.arange(n_buckets) + 0.5) * bucket
    return x, blocks.min(axis=1), blocks.max(axis=1)


def power_envelope(y, fs, window_ms):
    """Block-wise RMS power (linear, then also returned in dB) -- one value
    per window, no overlap. window_ms=20 at 48kHz is ~960 samples, i.e. ~192
    GFSK 9600bps symbol periods per block: coarse enough to be fast and show
    burst boundaries clearly, fine enough not to blur separate short bursts
    together."""
    win = max(1, int(fs * window_ms / 1000))
    n_blocks = y.size // win
    if n_blocks == 0:
        return np.array([]), np.array([]), np.array([])
    blocks = y[:n_blocks * win].reshape(n_blocks, win)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
    t = (np.arange(n_blocks) + 0.5) * win / fs
    rms_db = 20 * np.log10(np.maximum(rms, 1e-12))
    return t, rms, rms_db


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("audio", nargs="?", default=AUDIO,
                   help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    p.add_argument("--start", type=float, default=None,
                   help="only decode/plot from this time onward, in seconds (default: 0, whole file)")
    p.add_argument("--end", type=float, default=None,
                   help="stop decoding/plotting at this time, in seconds (default: end of file)")
    p.add_argument("--window-ms", type=float, default=20.0,
                   help="RMS power block size in milliseconds (default: 20)")
    p.add_argument("--max-points", type=int, default=6000,
                   help="waveform decimation target column count (default: 6000)")
    p.add_argument("--no-decimate-below", type=float, default=5.0,
                   help="skip waveform decimation entirely (plot every sample) when the loaded "
                        "range is shorter than this many seconds (default: 5.0) -- lower this if "
                        "you want full resolution on longer zooms too, or raise it if a wide zoom "
                        "renders too slowly")
    return p.parse_args()


def main():
    args = parse_args()

    if args.start is None and args.end is None:
        import soundfile as sf
        info = sf.info(args.audio)
        print(f"File duration: {info.frames / info.samplerate:.1f}s "
              f"({info.frames} samples @ {info.samplerate} Hz).")
        if info.frames / info.samplerate > 60:
            print("This is a long recording -- if plotting is slow, narrow the range with "
                  "--start/--end (seconds), e.g.:\n"
                  f"  python {os.path.basename(__file__)} {args.audio} --start 30 --end 90")

    y, fs = audio_io.read_audio(args.audio, expected_fs=FS, start_sec=args.start, end_sec=args.end)
    t0 = args.start or 0.0
    duration = y.size / fs
    print(f"Loaded {duration:.1f}s ({y.size} samples) starting at t={t0:.1f}s.")
    if y.size == 0:
        print(f"ERROR: --start {args.start} --end {args.end} selects 0 samples "
              "(check start < end, and both are within the file's duration). Nothing to plot.")
        return

    plt = setup_mpl()
    # Rendering a plain plot() with tens of thousands of points is much slower than it
    # needs to be under matplotlib's default path settings; these two only affect this
    # script's process (rcParams, not touching shared _style.py), and only matter for the
    # non-decimated (direct) waveform path below.
    plt.rcParams["path.simplify"] = True
    plt.rcParams["path.simplify_threshold"] = 1.0
    plt.rcParams["agg.path.chunksize"] = 20000
    fig, (ax_wave, ax_power) = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    t_wave = t0 + np.arange(y.size) / fs
    xw, ylo, yhi = minmax_decimate(y, fs, args.max_points, args.no_decimate_below)
    tw = t0 + xw / fs
    decimated = xw.size != y.size
    if decimated:
        # ylo/yhi genuinely differ per column here (block min/max), so fill_between
        # draws a real band.
        ax_wave.fill_between(tw, ylo, yhi, color=BLUE, lw=0, alpha=0.9)
    else:
        # minmax_decimate returns the SAME array for ylo and yhi when the range is short
        # enough to plot directly (no bucketing) -- fill_between(t, y, y) has zero band
        # thickness at every point and renders as nothing. Draw an actual line instead.
        ax_wave.plot(tw, ylo, color=BLUE, lw=0.6)
    ax_wave.set_ylabel("amplitude")
    ax_wave.set_title(f"{os.path.basename(args.audio)} -- waveform "
                       f"({'full file' if not decimated else f'min/max-decimated to {xw.size} columns'})")

    t_pow, rms, rms_db = power_envelope(y, fs, args.window_ms)
    ax_power.plot(t0 + t_pow, rms_db, color=ORANGE, lw=0.8)
    ax_power.set_xlabel("time (s)")
    ax_power.set_ylabel("power (dB, RMS)")
    ax_power.set_title(f"RMS power envelope ({args.window_ms:.0f}ms blocks) -- "
                        "look for sustained raised plateaus: candidate signal bursts")

    for ax in (ax_wave, ax_power):
        ax.set_xlim(t_wave[0], t_wave[-1])

    stem = os.path.splitext(os.path.basename(args.audio))[0]
    # NOTE: was `.0f` (whole seconds) -- collided silently for any sub-second-precision
    # --start/--end (e.g. 183.52-183.90 and 183.6-183.8 both rounded to "184-184s" and
    # overwrote each other; confirmed happening already, see e.g. Figure/*_327-327s.png).
    # `.2f` keeps distinct ranges distinct down to hundredths of a second.
    range_tag = f"_{t0:.2f}-{t0 + duration:.2f}s" if (args.start is not None or args.end is not None) else ""
    out_name = os.path.join("Figure", f"01a_overview_{stem}{range_tag}.png")
    try:
        out_path = save(fig, out_name)
    except PermissionError:
        plt.close(fig)
        print(f"ERROR: couldn't write {out_name} -- it's probably open in another program "
              "(an image viewer, Explorer preview pane, etc.). Close it and re-run.")
        return
    plt.close(fig)
    print(f"Figure saved: {out_path}")


if __name__ == "__main__":
    main()
