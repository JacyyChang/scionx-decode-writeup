# -*- coding: utf-8 -*-
"""
03a_data_asymmetry_check.py -- automatically detect every frame in the whole
recording, and for each frame measure the asymmetry between the 1/0 decision
values in data1/data2 (the real telemetry content). Saved separately as
Figure/03a_frame1.png, Figure/03a_frame2.png, ... (numbered in order of
sample position).

Frame detection is identical to 01_frame_detection.py (normalized
cross-correlation to find header correlation peaks; see that folder's
README). Once each frame's start is found:

  1. Re-calibrate a fixed offset threshold from this frame's own
     header+callsign address (144 known bits, protocol-fixed with a known
     answer) -- not borrowing a threshold computed from another frame.
  2. Use the "bypass method" (sample the raw y at a fixed rate and destuff it
     directly, without going through restore_baseline) to track each output
     bit's original sample index, and compute this frame's own exact
     data1/data2 sample ranges.
  3. Use step 1's threshold to split data1/data2 into "decided as 1" /
     "decided as 0" groups (this is the current best guess, not ground truth
     -- the data segments' content has no known correct answer), and produce
     two views: a scatter plot color-coded by decision, and each group's
     local moving average (to see whether the asymmetry drifts slowly with
     position, an "invisible curve").

Important distinction: the header uses known-ground-truth grouping (fixed
protocol content, known answer); data1/data2 have no known answer, so the
grouping label here is whatever step 1's threshold decides. Plots and output
consistently say "decided as 1/0", never "known bit=1/0", to avoid misleading.
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
    v1h, v0h = y_hdr[t_bits == 1], y_hdr[t_bits == 0]
    grid = np.linspace(min(v0h.min(), v1h.min()), max(v0h.max(), v1h.max()), 2000)
    errs = [(v1h <= t).sum() + (v0h > t).sum() for t in grid]
    thr = grid[int(np.argmin(errs))]
    return thr, v1h, v0h, min(errs)


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


def smooth_by_group(xs, vals, mask, win):
    """Local moving average over the points selected by mask (the window is
    counted in "points within the group", not absolute index)."""
    sub_x = xs[mask]
    sub_v = vals[mask]
    sm = np.full(sub_x.size, np.nan)
    for i in range(sub_x.size):
        lo, hi = max(0, i - win), min(sub_x.size, i + win + 1)
        sm[i] = sub_v[lo:hi].mean()
    return sub_x, sm


def plot_frame(plt, y, start, frame_no, z_score, data_segs, offset_thr, v1h, v0h):
    A = np.median(np.abs(y))

    print("=" * 78)
    print(f"[Frame #{frame_no}] start sample={start}  z-score={z_score:.2f}")
    print("=" * 78)
    print(f"A = {A:.4f}   fixed offset threshold (header-calibrated) = {offset_thr:+.4f}")
    print(f"  known bit=1 mean={v1h.mean():+.4f} (distance from +A: {100*(A-v1h.mean())/A:.1f}%A)")
    print(f"  known bit=0 mean={v0h.mean():+.4f} (distance from -A: {100*(v0h.mean()-(-A))/A:.1f}%A)")

    fig, axes = plt.subplots(2, 2, figsize=(18, 10))
    all_v1, all_v0 = [], []

    for row, (name, s0, s1) in enumerate(data_segs):
        j0 = round((s0 - start - PHASE) / SPS)
        j1 = round((s1 - start - PHASE) / SPS)
        idx_sym = start + SPS * np.arange(j0, j1) + PHASE
        y_sym = y[idx_sym]
        bits = (y_sym > offset_thr).astype(np.uint8)
        is1, is0 = bits == 1, bits == 0
        v1, v0 = y_sym[is1], y_sym[is0]
        all_v1.append(v1)
        all_v0.append(v0)

        print(f"\n{name}: sample[{s0},{s1})  n={j1-j0} symbols, decided-as-1={is1.sum()}, "
              f"decided-as-0={is0.sum()}:")
        print(f"  decided-as-1 mean={v1.mean():+.4f} (distance from +A: {100*(A-v1.mean())/A:.1f}%A)  std={v1.std():.4f}")
        print(f"  decided-as-0 mean={v0.mean():+.4f} (distance from -A: {100*(v0.mean()-(-A))/A:.1f}%A)  std={v0.std():.4f}")

        xs = np.arange(j1 - j0)
        ax_sc, ax_sm = axes[row, 0], axes[row, 1]

        ax_sc.scatter(xs[is1], y_sym[is1], color=BLUE, s=18, alpha=0.6,
                     label=f"decided as 1 (n={is1.sum()})", zorder=3)
        ax_sc.scatter(xs[is0], y_sym[is0], color=ORANGE, s=18, alpha=0.6,
                     label=f"decided as 0 (n={is0.sum()})", zorder=3)
        ax_sc.axhline(A, color=BLUE, ls=":", lw=1.0, label=f"+A={A:.3f}")
        ax_sc.axhline(-A, color=ORANGE, ls=":", lw=1.0, label=f"-A={-A:.3f}")
        ax_sc.axhline(v1.mean(), color=BLUE, ls="--", lw=1.5, label=f"decided-as-1 mean={v1.mean():+.3f}")
        ax_sc.axhline(v0.mean(), color=ORANGE, ls="--", lw=1.5, label=f"decided-as-0 mean={v0.mean():+.3f}")
        ax_sc.axhline(0, color="k", lw=0.7)
        ax_sc.axhline(offset_thr, color=PURPLE, lw=1.3, label=f"threshold={offset_thr:+.3f}")
        ax_sc.set_title(f"{name}: per-point color coding (n={j1-j0})")
        ax_sc.set_xlabel("symbol index within segment")
        ax_sc.set_ylabel("raw y")
        ax_sc.legend(loc="upper right", fontsize=6.5, ncol=2)

        win = max(10, (j1 - j0) // 20)
        x1s, sm1 = smooth_by_group(xs, y_sym, is1, win)
        x0s, sm0 = smooth_by_group(xs, y_sym, is0, win)
        ax_sm.scatter(xs[is1], y_sym[is1], color=BLUE, s=10, alpha=0.25)
        ax_sm.scatter(xs[is0], y_sym[is0], color=ORANGE, s=10, alpha=0.25)
        ax_sm.plot(x1s, sm1, "-", color=BLUE, lw=2.2, label=f"decided-as-1 local moving average (win=+/-{win})")
        ax_sm.plot(x0s, sm0, "-", color=ORANGE, lw=2.2, label=f"decided-as-0 local moving average (win=+/-{win})")
        ax_sm.axhline(A, color=BLUE, ls=":", lw=1.0)
        ax_sm.axhline(-A, color=ORANGE, ls=":", lw=1.0)
        ax_sm.axhline(0, color="k", lw=0.7)
        ax_sm.set_title(f"{name}: per-group local moving average -- does the asymmetry drift slowly with position?")
        ax_sm.set_xlabel("symbol index within segment")
        ax_sm.set_ylabel("raw y")
        ax_sm.legend(loc="upper right", fontsize=7)

    fig.suptitle(f"Frame #{frame_no} (start sample={start}): data1/data2 1/0 asymmetry"
                f" (grouping = threshold decision, not ground truth; threshold={offset_thr:+.3f})", fontsize=13)

    all_v1 = np.concatenate(all_v1)
    all_v0 = np.concatenate(all_v0)
    print(f"\ndata1+data2 combined (n={all_v1.size+all_v0.size}):")
    print(f"  decided-as-1 mean={all_v1.mean():+.4f} (distance from +A: {100*(A-all_v1.mean())/A:.1f}%A)")
    print(f"  decided-as-0 mean={all_v0.mean():+.4f} (distance from -A: {100*(all_v0.mean()-(-A))/A:.1f}%A)")
    print(f"  compared to header: bit=1 gap {100*(A-v1h.mean())/A:.1f}%A / bit=0 gap "
          f"{100*(v0h.mean()-(-A))/A:.1f}%A")

    p = save(fig, os.path.join("Figure", f"03a_frame{frame_no}.png"))
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
        offset_thr, v1h, v0h, hdr_err = calibrate_offset_threshold(y, s)
        data_segs = get_data_segments(y, s)
        plot_frame(plt, y, s, i, z, data_segs, offset_thr, v1h, v0h)


if __name__ == "__main__":
    main()
