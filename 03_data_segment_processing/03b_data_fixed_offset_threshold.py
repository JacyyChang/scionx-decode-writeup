# -*- coding: utf-8 -*-
"""
03b_data_fixed_offset_threshold.py -- automatically detect every frame in the
whole recording, and for each frame apply the header-calibrated fixed offset
threshold to data1/data2, drawing the decision line for visual inspection.
Saved separately as Figure/03b_frame1.png, Figure/03b_frame2.png, ...
(numbered in order of sample position).

Frame detection is identical to 01_frame_detection.py (normalized
cross-correlation to find header correlation peaks; see that folder's
README). Once each frame's start is found:

  1. Use this frame's own header+callsign address (144 bits, the only
     segment with a known correct answer) to measure the bit=1/bit=0 signal
     distribution asymmetry, and scan for the best fixed threshold (the one
     that minimizes the error count over the known 144 bits).
  2. Use the "bypass method" (sample the raw y at a fixed rate and destuff it
     directly, without going through restore_baseline) to track each output
     bit's original sample index, and compute this frame's own exact
     data1/data2 sample ranges.
  3. Apply step 1's threshold to data1/data2 unchanged (assuming the
     asymmetry is a fixed property of the channel/demodulator, not dependent
     on content, and shared across the whole frame), overlaying the decision
     line and the resulting decisions on the plot -- no BER is computed
     (the data content changes frame to frame, so comparing against the
     reference frame wouldn't be meaningful).
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE, GRAY, GREEN, PURPLE  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS, SPS = 48000, 5
PHASE = 2
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")

FLAGS4 = bytes([0x7E] * 4)
HEADER_ADDR = bytes.fromhex("849c6086aa4060849c60a686b0e1")   # Dest+Src address, 14 bytes

Z_THRESHOLD = 12.0        # frame detection threshold (see 01_frame_detection.py)
MIN_FRAME_GAP = 100_000
SEARCH_SAMPLES = 19211    # search range covering the header through the trailing flags (relative to frame start)

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


def bits_lsb_first(data):
    out = []
    for b in data:
        for k in range(8):
            out.append((b >> k) & 1)
    return out


def build_header_template():
    """Build the flags+address NRZ template (+/-1), upsampled by SPS --
    used only for frame-start detection."""
    bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.float64)
    nrz = 2 * bits - 1
    return np.repeat(nrz, SPS)


def detect_frame_starts(yc, template):
    """Normalized cross-correlation to find header correlation peaks. Returns
    (list of starts sorted by sample position, corresponding z-score list)."""
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


def destuff_with_map(bits, raw_idx):
    out_bits, out_idx = [], []
    run = 0
    for b, si in zip(bits, raw_idx):
        if run == 5:
            if b == 0:
                run = 0
                continue
            run = 0
        out_bits.append(b)
        out_idx.append(si)
        run = run + 1 if b == 1 else 0
    return np.array(out_bits, dtype=np.uint8), np.array(out_idx)


def calibrate_offset_threshold(y, start):
    """Re-scan this frame's own header+callsign address (144 known bits) for
    the best fixed offset threshold."""
    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    idx_hdr = start + SPS * np.arange(0, 144) + PHASE
    y_hdr = y[idx_hdr]
    v1, v0 = y_hdr[t_bits == 1], y_hdr[t_bits == 0]
    grid = np.linspace(min(v0.min(), v1.min()), max(v0.max(), v1.max()), 2000)
    errs = [(v1 <= t).sum() + (v0 > t).sum() for t in grid]
    thr = grid[int(np.argmin(errs))]
    return thr, min(errs)


def get_data_segments(y, start):
    """Bypass method (sample the raw y at a fixed rate and destuff directly,
    without going through restore_baseline) to compute this frame's exact
    data1/data2 sample ranges. Returns [(name, s0, s1), ...]."""
    span_j1 = int(SEARCH_SAMPLES / SPS)
    idx_all = start + SPS * np.arange(0, span_j1) + PHASE
    ok = idx_all < y.size
    idx_all = idx_all[ok]
    body_bits_raw = (y[idx_all[32:]] > 0).astype(np.uint8)   # skip the leading 4 flags
    body_idx_raw = idx_all[32:]
    body_bits, body_sample_idx = destuff_with_map(body_bits_raw, body_idx_raw)

    zero_runs = find_zero_runs(REF)
    segments = [("address", 0, 14)]
    prev = 14
    seg_id = 1
    for (z0, z1) in zero_runs:
        if z0 > prev:
            segments.append((f"data{seg_id}", prev, z0))
        segments.append((f"zero-run{seg_id}", z0, z1))
        prev = z1
        seg_id += 1
    if prev < 272:
        segments.append((f"data{seg_id}", prev, 272))

    data_segs = []
    for label, b0, b1 in segments:
        if not label.startswith("data"):
            continue
        bit0, bit1 = b0 * 8, min(b1 * 8, body_sample_idx.size)
        if bit0 >= body_sample_idx.size:
            continue
        s0 = int(body_sample_idx[bit0])
        s1 = int(body_sample_idx[min(bit1, body_sample_idx.size) - 1]) + SPS
        data_segs.append((label, s0, s1))
    return data_segs[:2]   # data1, data2


def plot_frame(plt, y, start, frame_no, z_score, data_segs, offset_thr, hdr_err):
    print("=" * 78)
    print(f"[Frame #{frame_no}] start sample={start}  z-score={z_score:.2f}")
    print("=" * 78)
    print(f"Best fixed threshold re-scanned from header+address = {offset_thr:+.4f}"
          f" (header 144-bit error count={hdr_err})")

    fig, axes = plt.subplots(2, 1, figsize=(16, 9))
    for ax, (name, s0, s1) in zip(axes, data_segs):
        j0 = round((s0 - start - PHASE) / SPS)
        j1 = round((s1 - start - PHASE) / SPS)
        idx_sym = start + SPS * np.arange(j0, j1) + PHASE
        y_sym = y[idx_sym]
        bits_fixed0 = (y_sym > 0).astype(np.uint8)
        bits_offset = (y_sym > offset_thr).astype(np.uint8)
        n_diff = (bits_fixed0 != bits_offset).sum()
        print(f"\n{name}: sample[{s0},{s1})  {j1-j0} symbols  "
              f"threshold-0 vs threshold-{offset_thr:+.3f}: symbols with a different decision = {n_diff} "
              f"({100*n_diff/(j1-j0):.1f}%)")

        xs_sample = np.arange(s0, s1)
        ax.plot(xs_sample, y[s0:s1], "-", color=GRAY, lw=0.5, alpha=0.7, label="raw y")
        ax.axhline(0, color="k", lw=0.8, ls="--", label="old threshold=0")
        ax.axhline(offset_thr, color=PURPLE, lw=1.8,
                  label=f"new threshold (fixed offset)={offset_thr:+.3f}")

        xs_sym = idx_sym
        colors = [BLUE if b == 1 else ORANGE for b in bits_offset]
        ax.scatter(xs_sym, y_sym, c=colors, s=22, zorder=4, edgecolors="k", linewidths=0.3,
                  label="decided as 1 (blue) / decided as 0 (orange) -- new threshold")
        changed = bits_fixed0 != bits_offset
        if changed.any():
            ax.scatter(xs_sym[changed], y_sym[changed], s=80, facecolors="none",
                      edgecolors="red", linewidths=1.5, zorder=5,
                      label=f"decision differs from old threshold ({changed.sum()} total)")

        ax.set_xlim(s0, s1)
        ax.set_xlabel("sample index")
        ax.set_ylabel("raw y")
        ax.set_title(f"{name}: sample[{s0},{s1})")
        ax.legend(loc="upper right", fontsize=7.5, ncol=2)

    fig.suptitle(f"Frame #{frame_no} (start sample={start}): fixed offset threshold"
                 f" (={offset_thr:+.3f}, calibrated from the known header 144 bits) applied to data1/data2")
    p = save(fig, os.path.join("Figure", f"03b_frame{frame_no}.png"))
    plt.close(fig)
    print(f"\nFigure saved: {p}")


def main():
    plt = setup_mpl()
    y, fs = audio_io.read_audio(AUDIO, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]     # only used to detect frame starts; the actual analysis uses raw y (bypass method)

    template = build_header_template()
    starts, zs = detect_frame_starts(yc, template)

    print("=" * 78)
    print(f"Detected {len(starts)} frame(s) (z-score threshold={Z_THRESHOLD}):")
    for i, (s, z) in enumerate(zip(starts, zs), start=1):
        print(f"  frame#{i}: sample={s}  z-score={z:.2f}")
    print("=" * 78)

    for i, (s, z) in enumerate(zip(starts, zs), start=1):
        offset_thr, hdr_err = calibrate_offset_threshold(y, s)
        data_segs = get_data_segments(y, s)
        plot_frame(plt, y, s, i, z, data_segs, offset_thr, hdr_err)


if __name__ == "__main__":
    main()
