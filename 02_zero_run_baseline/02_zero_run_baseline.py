# -*- coding: utf-8 -*-
"""
02_zero_run_baseline.py -- automatically detect every frame in the whole
recording, and for each frame's zero-run1/zero-run2 segments (payload
byte[75,158) and [206,272), a padding region known to be all 0x00), compare
two decision methods and flag the isolated "1"s inside them. Saved separately
as Figure/02_frame1.png, Figure/02_frame2.png, ... (numbered in order of
sample position).

Frame detection is identical to 01_frame_detection.py (see that folder's
README, "Algorithm" section): build an NRZ template from the known
header+callsign address, run a normalized cross-correlation against
`restore_baseline`'s `y_comp_final` to find correlation peaks, filter by
z-score. Once each frame's start is found, use the same bit-level destuff
mapping (output bit <-> original sample index) to compute that frame's exact
zero-run1/zero-run2 sample ranges -- no longer hardcoding each frame's sample
range as a constant like the old version did.

Two decision methods (applied only to zero-run1/zero-run2 -- header/data/FCS
are untouched):

  Method 1 (static threshold): bit[n] = 1 if yc[symbol n center] > 0 else 0.

  Method 2 (sample-to-sample change): diff[n] = yc[symbol n] - yc[symbol n-1]
                    (compared to the previous symbol), bit[n] = 1 if
                    |diff[n]| > threshold else 0. There's no prior value for
                    threshold, so candidates are taken from percentiles of
                    the |diff| distribution within the zero-run segment
                    (not picked arbitrarily).

Scoring: compared against the known structure that these two segments should
be all 0x00 (caveat: this is a structural padding region, more trustworthy
than data1/data2, but may still contain a few genuine isolated 1-bits -- it
is not an absolute ground truth).
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

Z_THRESHOLD = 12.0        # noise floor z < ~5, real peaks z ~ 12-13; use a value in between as the detection threshold
MIN_FRAME_GAP = 100_000   # samples; actual frame spacing is ~561000, far larger than this, so no double-detection
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
    """Build the flags+address NRZ template (+/-1), upsampled by SPS."""
    bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.float64)
    nrz = 2 * bits - 1
    return np.repeat(nrz, SPS)


def detect_frame_starts(yc, template):
    """Normalized cross-correlation to find header correlation peaks. Returns
    (list of starts sorted by sample position, corresponding z-score list)."""
    L = template.size
    corr = np.correlate(yc, template, mode="valid")                     # numerator
    csum2 = np.concatenate(([0.0], np.cumsum(yc.astype(np.float64) ** 2)))
    window_energy = csum2[L:] - csum2[:-L]                               # denominator: sliding sum of squares
    template_norm = np.sqrt(np.sum(template ** 2))
    R = corr / (template_norm * np.sqrt(window_energy) + 1e-12)

    z = R / np.std(R)
    candidates = np.where(z > Z_THRESHOLD)[0]
    candidates = candidates[np.argsort(-z[candidates])]   # z descending

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


def slice_bits(y_comp, start, j0, j1):
    idx = start + SPS * np.arange(j0, j1) + PHASE
    ok = (idx >= 0) & (idx < y_comp.size)
    return (y_comp[idx[ok]] > 0).astype(np.uint8), idx[ok]


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


def get_zero_run_segments(yc, start):
    """For a given frame start, compute the exact zero-run1/zero-run2 sample
    ranges (same destuff-mapping technique as 01_frame_detection.py, only
    keeping these two zero-run segments).
    Returns [(name, s0, s1, payload_byte0, payload_byte1), ...]."""
    span_j1 = int(SEARCH_SAMPLES / SPS)
    body_bits_raw, body_idx_raw = slice_bits(yc, start, 32, span_j1)
    body_bits, body_sample_idx = destuff_with_map(body_bits_raw, body_idx_raw)
    payload_idx = body_sample_idx[:272 * 8]

    zero_runs = find_zero_runs(REF)
    segments = []
    for seg_id, (z0, z1) in enumerate(zero_runs, start=1):
        bit0, bit1 = z0 * 8, min(z1 * 8, payload_idx.size)
        if bit0 >= payload_idx.size:
            continue
        s0 = int(payload_idx[bit0])
        s1 = int(payload_idx[min(bit1, payload_idx.size) - 1]) + SPS
        segments.append((f"zero-run{seg_id}", s0, s1, z0, z1))
    return segments


def plot_frame(plt, yc, start, frame_no, z_score, zero_runs):
    """Compare the two decision methods on a single frame's
    zero-run1/zero-run2, saved as Figure/02_frame{frame_no}.png."""
    A = np.median(np.abs(yc))

    print("=" * 78)
    print(f"[Frame #{frame_no}] start sample={start}  z-score={z_score:.2f}")
    print("=" * 78)
    print(f"A = {A:.4f}")

    j_max = int(SEARCH_SAMPLES / SPS)
    idx = start + SPS * np.arange(0, j_max) + PHASE
    y_sym = yc[idx]                        # symbol-center values

    # ---- Method 1: static threshold (computed for the whole span first, then scored over the zero-run range) ----
    bits_static = (y_sym > 0).astype(np.uint8)

    # ---- Method 2: sample-to-sample change (diff over the whole symbol sequence, then scored over the zero-run range) ----
    diff = np.zeros_like(y_sym)
    diff[1:] = y_sym[1:] - y_sym[:-1]
    diff[0] = diff[1]

    def zero_run_symbol_slice(s0, s1):
        j0 = round((s0 - start - PHASE) / SPS)
        j1 = round((s1 - start - PHASE) / SPS)
        return j0, j1

    all_zr_diff = []
    for name, s0, s1, b0, b1 in zero_runs:
        j0, j1 = zero_run_symbol_slice(s0, s1)
        all_zr_diff.append(np.abs(diff[j0:j1]))
    all_zr_diff = np.concatenate(all_zr_diff)
    candidates = {
        f"p{p}(={np.percentile(all_zr_diff, p):.3f})": np.percentile(all_zr_diff, p)
        for p in (50, 70, 80, 90)
    }

    print(f"\n|diff| distribution within the zero-run regions: median={np.median(all_zr_diff):.4f}  "
          f"p70={np.percentile(all_zr_diff,70):.4f}  p90={np.percentile(all_zr_diff,90):.4f}")
    print(f"(Method 2 has no prior threshold, so the candidates below are percentiles of this "
          f"distribution, giving decided-as-1 rates of roughly ~50%/~30%/~20%/~10%)")

    results = {}
    for name, s0, s1, b0, b1 in zero_runs:
        j0, j1 = zero_run_symbol_slice(s0, s1)
        n_bits = j1 - j0
        print(f"\n{'='*70}")
        print(f"{name}: sample [{s0},{s1})  symbol [{j0},{j1})  {n_bits} bits  "
              f"= payload byte [{b0},{b1})  (reference: should be all 0x00, but may contain a few genuine 1-bits)")
        print(f"{'='*70}")

        m1 = bits_static[j0:j1]
        frac1 = m1.mean()
        print(f"  Method 1 (static threshold)      : fraction decided as 1 = {frac1:.1%}  "
              f"(ideally close to 0%, aside from a few genuine 1-bits)")
        results[(name, "static")] = dict(bits=m1, frac1=frac1)

        seg_diff = np.abs(diff[j0:j1])
        print(f"  Method 2 (sample-to-sample change, threshold sweep):")
        for label, thr in candidates.items():
            m2 = (seg_diff > thr).astype(np.uint8)
            frac2 = m2.mean()
            print(f"    threshold={label:<18} fraction decided as 1 = {frac2:.1%}")
            results[(name, f"diff_{label}")] = dict(bits=m2, frac1=frac2)

    # ---- figure: zero-run1/zero-run2 per-symbol, overlaying both methods' decisions ----
    fig, axes = plt.subplots(2, 1, figsize=(16, 9))
    for ax, (name, s0, s1, b0, b1) in zip(axes, zero_runs):
        j0, j1 = zero_run_symbol_slice(s0, s1)
        xs = np.arange(j0, j1)
        ax.plot(xs, y_sym[j0:j1] / A, "-", color=GRAY, lw=0.8, label="yc (symbol center) / A")
        ax.axhline(0, color="k", lw=0.6)

        m1 = bits_static[j0:j1]
        ax.plot(xs[m1 == 1], np.full(m1.sum(), 1.3), "|", color=BLUE, markersize=8,
                label=f"Method 1 decided as 1 ({m1.sum()} total)")

        m1_positions = np.where(m1 == 1)[0]   # relative position within the segment (0-based)
        print(f"\n{name} exact positions Method 1 (static threshold) decided as 1:")
        min_gap = (j1 - j0) * 0.02
        last_x = -np.inf
        level = 0
        for rel in m1_positions:
            global_sym = j0 + rel
            s_at = start + SPS * global_sym + PHASE
            print(f"  global symbol={global_sym}  #{rel} within segment (0-based, {j1-j0} total)  "
                  f"sample~={s_at}")
            level = level + 1 if (global_sym - last_x) < min_gap else 0
            last_x = global_sym
            y_txt = 1.40 + 0.22 * level
            ax.annotate(f"symbol {global_sym} (#{rel} in segment)",
                       xy=(global_sym, 1.32), xytext=(global_sym, y_txt),
                       fontsize=6.5, ha="center", va="bottom", color=BLUE,
                       bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=BLUE, alpha=0.9),
                       arrowprops=dict(arrowstyle="-", color=BLUE, lw=0.6))

        thr_mid = candidates[list(candidates.keys())[1]]   # p70 as the representative threshold for plotting
        m2 = (np.abs(diff[j0:j1]) > thr_mid).astype(np.uint8)
        ax.plot(xs[m2 == 1], np.full(m2.sum(), 1.35), "|", color=ORANGE, markersize=8,
                label=f"Method 2 decided as 1 (threshold=p70, {m2.sum()} total)")

        ax.set_ylim(-1.6, 2.8)
        ax.set_title(f"{name}: sample [{s0},{s1})")
        ax.set_xlabel("on-wire symbol index (global)")
        ax.legend(loc="upper right", fontsize=8, ncol=3)
    fig.suptitle(f"Frame #{frame_no} (start sample={start}, detection z-score={z_score:.1f}):"
                 f" zero-run1/zero-run2, both decision methods overlaid")
    p = save(fig, os.path.join("Figure", f"02_frame{frame_no}.png"))
    plt.close(fig)
    print(f"\nFigure saved: {p}")

    print("\n" + "=" * 78)
    print("Summary")
    print("=" * 78)
    frac1_list = [(name, results[(name, "static")]["frac1"]) for name, *_ in zero_runs]
    print("Method 1 (static threshold) fraction decided as 1, by segment:",
          ", ".join(f"{name}={f:.1%}" for name, f in frac1_list))
    print("Method 2's rates at each threshold are shown above -- choosing a threshold means "
          "choosing how many false positives to tolerate; there's no prior optimal value, and "
          "settling on one would need CRC or some other independent verification.")


def main():
    plt = setup_mpl()
    y, fs = audio_io.read_audio(AUDIO, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]

    template = build_header_template()
    starts, zs = detect_frame_starts(yc, template)

    print("=" * 78)
    print(f"Detected {len(starts)} frame(s) (z-score threshold={Z_THRESHOLD}):")
    for i, (s, z) in enumerate(zip(starts, zs), start=1):
        print(f"  frame#{i}: sample={s}  z-score={z:.2f}")
    print("=" * 78)

    for i, (s, z) in enumerate(zip(starts, zs), start=1):
        zero_runs = get_zero_run_segments(yc, s)
        plot_frame(plt, yc, s, i, z, zero_runs)


if __name__ == "__main__":
    main()
