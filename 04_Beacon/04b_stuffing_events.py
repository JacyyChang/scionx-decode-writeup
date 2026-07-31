# -*- coding: utf-8 -*-
"""
04b_stuffing_events.py -- locate every bit-stuffing REMOVAL event that happens
while destuffing one frame, and show enough context (raw waveform window,
segment label, "is this bit itself low-confidence" check) to manually judge
whether each one is a genuine HDLC stuffed 0, or a false "five consecutive 1s"
run created by a bit error.

Why this exists: `04_beacon_field_decode.py`'s frame-alignment check (see its
docstring / 04_Beacon/README.md "Frame-alignment self-check") found that every
frame in cut_first3.ogg removes more/fewer stuffed 0s than the reference frame
needs, so the destuffed byte grid drifts mid-frame. That check can prove ONE
removal is spurious when it lands inside zero-run padding (which is
structurally known to be all-zero, so a genuine 5-ones run can't occur there),
but a removal inside a data segment has no such ground truth. This script is
the next step: for every removal, dump the actual signal around it so a human
can look at the five "1" symbols plus the dropped "0" symbol and judge by eye
whether the slicer got them right.

Split into its own file (rather than added to 04_beacon_field_decode.py)
because that file was already large; this one is read-only diagnostics, no
change to the decode pipeline.

Two independent thresholds are in play, and this script keeps them separate on
purpose:
  - Destuffing structure ALWAYS uses raw y at threshold=0 (03's "bypass
    method"), regardless of segment -- that's what actually produced these
    removal events, so that's the decision shown here.
  - "Is this bit itself low-confidence" uses |y| <= MARGIN_FRAC*A -- the same
    margin used everywhere else in 04_Beacon, just centered on 0 (the
    threshold destuffing actually used) instead of theta* (the threshold used
    later, only for the *reported* value of header/data/FCS bits). A removal
    event where any of the 6 bits involved is low-confidence is flagged
    UNCERTAIN, separately from the zero-run-based SPURIOUS proof.

Usage: `python 04b_stuffing_events.py [audio.ogg] [--frame N] [--z-threshold Z]`.
Defaults to frame#2 of ../Data/cut_first3.ogg.

Output: prints one line per removal event (segment, verdict, sample window),
plus Figure/04b_stuffing_frame<N>.png -- one panel per event, raw y zoomed to
a window around it, symbol decisions color-coded, the 5 "1"s and the dropped
"0" marked, low-confidence symbols ringed in red.
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE, GRAY, PURPLE  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

FS, SPS = 48000, 5
PHASE = 2
AUDIO = os.path.join(os.path.dirname(HERE), "Data", "cut_first3.ogg")

FLAGS4 = bytes([0x7E] * 4)
HEADER_ADDR = bytes.fromhex("849c6086aa4060849c60a686b0e1")   # Dest+Src address, 14 bytes

Z_THRESHOLD = 12.0
MIN_FRAME_GAP = 100_000
SEARCH_SAMPLES = 19211
MARGIN_FRAC = 0.10        # same "near decision line" fraction used throughout 04_Beacon

FRAME_END_BIT = 274 * 8
CONTEXT_SYMBOLS = 8        # symbols of context shown on each side of an event in the figure

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


# ============================================================================
# Signal-side helpers, duplicated from 04_beacon_field_decode.py (repo
# convention: every script is self-contained, see CLAUDE.md)
# ============================================================================

def bits_lsb_first(data):
    return [(b >> k) & 1 for b in data for k in range(8)]


def build_header_template():
    bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.float64)
    return np.repeat(2 * bits - 1, SPS)


def detect_frame_starts(yc, template):
    L = template.size
    corr = np.correlate(yc, template, mode="valid")
    csum2 = np.concatenate(([0.0], np.cumsum(yc.astype(np.float64) ** 2)))
    window_energy = csum2[L:] - csum2[:-L]
    R = corr / (np.sqrt(np.sum(template ** 2)) * np.sqrt(window_energy) + 1e-12)
    z = R / np.std(R)
    candidates = np.where(z > Z_THRESHOLD)[0]
    candidates = candidates[np.argsort(-z[candidates])]
    starts = []
    for idx in candidates:
        if all(abs(int(idx) - s) > MIN_FRAME_GAP for s in starts):
            starts.append(int(idx))
    order = np.argsort(starts)
    starts = [starts[i] for i in order]
    return starts, [float(z[s]) for s in starts]


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


def build_segment_map():
    zero_runs = find_zero_runs(REF)
    segments = [("address", 0, 14)]
    prev, seg_id = 14, 1
    for (z0, z1) in zero_runs:
        if z0 > prev:
            segments.append((f"data{seg_id}", prev, z0))
        segments.append((f"zero-run{seg_id}", z0, z1))
        prev = z1
        seg_id += 1
    if prev < 272:
        segments.append((f"data{seg_id}", prev, 272))
    segments.append(("FCS", 272, 274))
    return segments


def segment_label_at(bit_pos, segments):
    """Which named segment does destuffed-stream bit `bit_pos` fall in?
    Anything past FCS is the trailing-flag region; anything negative is inside
    the leading flags (shouldn't happen here, included for completeness)."""
    if bit_pos < 0:
        return "leading-flag"
    for label, b0, b1 in segments:
        if b0 * 8 <= bit_pos < b1 * 8:
            return label
    if bit_pos >= FRAME_END_BIT:
        return "trailing-flag"
    return "?"


def calibrate_offset_threshold(y, start):
    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    idx_hdr = start + SPS * np.arange(0, 144) + PHASE
    y_hdr = y[idx_hdr]
    v1, v0 = y_hdr[t_bits == 1], y_hdr[t_bits == 0]
    grid = np.linspace(min(v0.min(), v1.min()), max(v0.max(), v1.max()), 2000)
    errs = [(v1 <= t).sum() + (v0 > t).sum() for t in grid]
    return float(grid[int(np.argmin(errs))]), int(min(errs))


# ============================================================================
# The new part: destuffing that also records every removal event, with full
# context (which raw positions the 5 "1"s and the dropped "0" came from).
# ============================================================================

def destuff_with_events(bits, raw_idx):
    """Same state machine as destuff_with_map, but additionally returns, for
    every removed stuffed 0, a dict with everything needed to inspect it:
    where it landed in the OUTPUT (destuffed) stream (for segment lookup),
    and the original sample indices of the 5 "1" symbols plus the dropped "0"
    symbol (for pulling the raw waveform back up)."""
    out_bits, out_idx = [], []
    events = []
    run = 0
    ones_raw_positions = []   # raw-stream positions of the current run of 1s
    for raw_pos, (b, si) in enumerate(zip(bits, raw_idx)):
        if run == 5:
            if b == 0:
                events.append({
                    "out_pos": len(out_bits),
                    "dropped_raw_pos": raw_pos,
                    "dropped_sample": int(si),
                    "ones_raw_pos": list(ones_raw_positions),
                    "ones_samples": [int(raw_idx[p]) for p in ones_raw_positions],
                })
                run = 0
                ones_raw_positions = []
                continue
            run = 0
            ones_raw_positions = []
        out_bits.append(b)
        out_idx.append(si)
        if b == 1:
            run += 1
            ones_raw_positions.append(raw_pos)
        else:
            run = 0
            ones_raw_positions = []
    return np.array(out_bits, dtype=np.uint8), np.array(out_idx), events


# ============================================================================
# Reporting
# ============================================================================

def classify_event(ev, seg_label, y, theta_margin):
    """SPURIOUS if provably impossible (inside zero-run padding, which is
    structurally all-zero -- no genuine 5-ones run can occur there).
    UNCERTAIN if any of the 6 bits involved (5 ones + the dropped 0) sits
    within `theta_margin` of 0 -- the actual threshold destuffing used -- i.e.
    a bit error could plausibly have flipped it and this run might not be
    real. Otherwise LOOKS GENUINE: all 6 samples confidently sliced."""
    if seg_label.startswith("zero-run"):
        return "SPURIOUS (proven -- inside all-zero padding)", True
    samples = ev["ones_samples"] + [ev["dropped_sample"]]
    near = [s for s in samples if abs(y[s]) <= theta_margin]
    if near:
        return f"UNCERTAIN ({len(near)}/6 bits near threshold=0)", False
    return "LOOKS GENUINE (all 6 bits confidently sliced)", False


def print_report(events, segments, y, margin, frame_no):
    print(f"\n{'=' * 78}\nFrame#{frame_no}: {len(events)} stuffed-0 removal event(s) "
          f"during destuffing\n{'=' * 78}")
    print(f"(threshold=0 is what destuffing actually uses; a bit is flagged 'near threshold=0' "
          f"here if |y| <= {margin:.4f} = MARGIN_FRAC*A)\n")
    n_spurious = n_uncertain = n_genuine = 0
    for i, ev in enumerate(events, 1):
        seg = segment_label_at(ev["out_pos"], segments)
        verdict, is_spurious = classify_event(ev, seg, y, margin)
        if is_spurious:
            n_spurious += 1
        elif verdict.startswith("UNCERTAIN"):
            n_uncertain += 1
        else:
            n_genuine += 1

        ones_y = [y[s] for s in ev["ones_samples"]]
        dropped_y = y[ev["dropped_sample"]]
        print(f"[{i}] destuffed-stream bit {ev['out_pos']:>5}  (payload byte {ev['out_pos']//8}, "
              f"segment={seg})  sample={ev['dropped_sample']}")
        print(f"     5 ones' y values   : " +
              "  ".join(f"{v:+.3f}" for v in ones_y))
        print(f"     dropped 0's y value: {dropped_y:+.3f}")
        print(f"     verdict: {verdict}")
    print(f"\nSummary: {n_spurious} spurious (proven) / {n_uncertain} uncertain "
          f"(low-confidence bit involved) / {n_genuine} look genuine  "
          f"[total {len(events)}]")


def plot_events(plt, events, segments, y, margin, start, frame_no):
    n = len(events)
    if n == 0:
        return None
    ncols = min(n, 4)
    nrows = -(-n // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.5 * ncols, 3.6 * nrows), squeeze=False)

    for i, ev in enumerate(events):
        ax = axes[i // ncols][i % ncols]
        seg = segment_label_at(ev["out_pos"], segments)
        verdict, is_spurious = classify_event(ev, seg, y, margin)

        ones_pos = ev["ones_raw_pos"]
        # window: CONTEXT_SYMBOLS before the first "1" to CONTEXT_SYMBOLS after the dropped "0",
        # expressed as sample indices via the known SPS/PHASE spacing around the dropped sample
        s_dropped = ev["dropped_sample"]
        lo = s_dropped - (5 + CONTEXT_SYMBOLS) * SPS
        hi = s_dropped + (CONTEXT_SYMBOLS + 1) * SPS
        xs = np.arange(lo, hi)
        ax.plot(xs, y[lo:hi], "-", color=GRAY, lw=0.7, alpha=0.7, zorder=1)
        ax.axhline(0, color="k", lw=1.0, ls="--", zorder=1, label="threshold=0 (destuffing)")
        ax.axhspan(-margin, margin, color=PURPLE, alpha=0.08, zorder=0)

        # every symbol center in the window, decided at threshold=0. s_dropped is
        # itself an exact grid point (start + SPS*n + PHASE), so the grid within
        # [lo,hi) is s_dropped's residue mod SPS, walked forward from lo.
        first_grid = lo + ((s_dropped - lo) % SPS)
        sym_samples = np.arange(first_grid, hi, SPS)
        bit_at = (y[sym_samples] > 0).astype(int)
        colors = [BLUE if b else ORANGE for b in bit_at]
        ax.scatter(sym_samples, y[sym_samples], c=colors, s=26, zorder=3,
                   edgecolors="k", linewidths=0.4)

        # highlight the 5 "1"s (squares) and the dropped "0" (X)
        ones_y = [y[s] for s in ev["ones_samples"]]
        ax.scatter(ev["ones_samples"], ones_y, marker="s", s=90, facecolors="none",
                   edgecolors=BLUE, linewidths=1.6, zorder=4, label="the 5 ones")
        ax.scatter([s_dropped], [y[s_dropped]], marker="x", s=140, color="red",
                   linewidths=2.2, zorder=5, label="dropped as stuffed 0")

        # ring any symbol near threshold=0 in this window
        near_mask = np.abs(y[sym_samples]) <= margin
        if near_mask.any():
            ax.scatter(sym_samples[near_mask], y[sym_samples][near_mask], s=140,
                      facecolors="none", edgecolors="red", linewidths=1.4, zorder=6,
                      label="near threshold=0")

        ax.set_xlim(lo, hi)
        short = verdict.split(" ")[0]
        ax.set_title(f"#{i+1}  bit{ev['out_pos']}  {seg}\n{short}", fontsize=8.5)
        ax.tick_params(labelsize=7)

    for j in range(n, nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.suptitle(f"Frame#{frame_no} (start sample={start}): every stuffed-0 removal event "
                 f"during destuffing -- squares=the 5 ones, red X=dropped 0, red ring=|y|<=margin",
                 fontsize=11)
    p = save(fig, os.path.join("Figure", f"04b_stuffing_frame{frame_no}.png"))
    plt.close(fig)
    return p


def main():
    global Z_THRESHOLD
    parser = argparse.ArgumentParser(
        description="List and plot every bit-stuffing removal event for one frame.")
    parser.add_argument("audio", nargs="?", default=AUDIO,
                         help=f"path to the .ogg recording (default: {os.path.relpath(AUDIO, HERE)})")
    parser.add_argument("--frame", type=int, default=2,
                         help="1-based frame index to inspect (default: 2)")
    parser.add_argument("--z-threshold", type=float, default=Z_THRESHOLD,
                         help=f"frame-detection z-score threshold (default: {Z_THRESHOLD})")
    args = parser.parse_args()
    Z_THRESHOLD = args.z_threshold

    plt = setup_mpl()
    y, fs = audio_io.read_audio(args.audio, expected_fs=FS)
    yc = baseline.restore_baseline(y, num_iters=7, W=1000)["y_comp_final"]

    template = build_header_template()
    starts, zs = detect_frame_starts(yc, template)
    print(f"Detected {len(starts)} frame(s): " +
          ", ".join(f"frame#{i}@{s} (z={z:.2f})" for i, (s, z) in enumerate(zip(starts, zs), start=1)))
    if not (1 <= args.frame <= len(starts)):
        raise SystemExit(f"--frame {args.frame} out of range: only {len(starts)} frame(s) detected")
    start = starts[args.frame - 1]

    A = float(np.median(np.abs(y)))
    margin = MARGIN_FRAC * A
    theta_star, hdr_err = calibrate_offset_threshold(y, start)
    print(f"Frame#{args.frame} at sample={start}.  A={A:.4f}  margin=+/-{margin:.4f}  "
          f"(theta*={theta_star:+.4f}, header err={hdr_err}/144 -- shown for context only; "
          f"destuffing itself always uses threshold=0)")

    span_j1 = int(SEARCH_SAMPLES / SPS)
    idx_all = start + SPS * np.arange(0, span_j1) + PHASE
    idx_all = idx_all[idx_all < y.size]
    body_bits_raw = (y[idx_all[32:]] > 0).astype(np.uint8)
    body_idx_raw = idx_all[32:]

    _, _, events = destuff_with_events(body_bits_raw, body_idx_raw)
    segments = build_segment_map()

    print_report(events, segments, y, margin, args.frame)
    p = plot_events(plt, events, segments, y, margin, start, args.frame)
    if p:
        print(f"\nFigure saved: {p}")


if __name__ == "__main__":
    main()
