# -*- coding: utf-8 -*-
"""
01_frame_detection.py -- automatically detect every frame start in the whole
recording, and for each detected frame draw its own packet structure map
(flags/address/data/zero-run/FCS segment spans + byte boundary labels),
saved separately as Figure/01_frame1.png, Figure/01_frame2.png, ... (numbered
in order of sample position).

Detection method (normalized cross-correlation, replacing a hardcoded sample
start):
  1. Build an NRZ template ({0,1} -> {-1,+1}) from the known header + callsign
     address (4x flag + 14 bytes of address = 144 bits, fixed protocol
     content unaffected by telemetry), upsampled to SPS=5 samples per symbol
     for a 720-sample-long template.
  2. Compute the normalized cross-correlation coefficient R between the
     template and `restore_baseline`'s `y_comp_final` (how well the template
     matches the signal window at each position; value range roughly [-1,1]).
  3. Use the z-score (R divided by the standard deviation of R over the whole
     signal) to find correlation peaks far above the noise floor: real frame
     starts have z around 12-13, while the noise floor mostly stays below 5 --
     the two are well separated. Peaks must be at least `MIN_FRAME_GAP`
     samples apart, to avoid a single peak's neighboring samples being
     counted as several separate candidates.
  4. Each detected peak is treated as one frame start; sort by sample position
     and number them frame#1/2/3...

Verified on `Data/cut_first3.ogg` (3 known frames): detection lands exactly on
the three known starts 235719 / 796789 / 1357824, with no false positives.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from scionx import audio_io, baseline               # noqa: E402
from scionx.hdlc import crc16_x25                    # noqa: E402
from _style import setup_mpl, save, BLUE, ORANGE, GRAY, GREEN, PURPLE, YELLOW  # noqa: E402

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

# fixed offsets (relative to the frame start) for the plot window and search range
# (magnitudes validated on the 3 known frames)
G0_OFFSET, G1_OFFSET = -1289, 19711
SEARCH_SAMPLES = 19211   # trailing-flag search range (relative to frame start), converted to a symbol count

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


def bits_to_bytes(bits):
    n = bits.size // 8
    out = bytearray()
    for i in range(n):
        v = 0
        for k in range(8):
            v |= int(bits[i * 8 + k]) << k
        out.append(v)
    return bytes(out)


def plot_frame(plt, y, yc, start, frame_no, z_score):
    """Draw a single frame's structure map + byte boundary labels, saved as
    Figure/01_frame{frame_no}.png."""
    span_j1 = int(SEARCH_SAMPLES / SPS)
    body_bits_raw, body_idx_raw = slice_bits(yc, start, 32, span_j1)
    body_bits, body_sample_idx = destuff_with_map(body_bits_raw, body_idx_raw)

    payload_bits = body_bits[:272 * 8]
    payload_idx = body_sample_idx[:272 * 8]

    ref_bits = np.unpackbits(np.frombuffer(REF, dtype=np.uint8), bitorder="little")
    n = min(payload_bits.size, ref_bits.size)
    err = payload_bits[:n] != ref_bits[:n]

    fcs_bit0, fcs_bit1 = 272 * 8, 272 * 8 + 16
    fcs_s0 = fcs_s1 = fcs_bytes = expect_bytes = fcs_err = None
    if fcs_bit1 <= body_sample_idx.size:
        fcs_bits = body_bits[fcs_bit0:fcs_bit1]
        fcs_idx = body_sample_idx[fcs_bit0:fcs_bit1]
        fcs_bytes = bits_to_bytes(fcs_bits)
        fcs_s0, fcs_s1 = int(fcs_idx[0]), int(fcs_idx[-1]) + SPS
        expect_bytes = crc16_x25(REF).to_bytes(2, "little")
        fcs_err = sum(bin(a ^ b).count("1") for a, b in zip(fcs_bytes, expect_bytes))

    # the header's green block (4x flag + address) overlaps data1 (which
    # starts counting from byte0), so the callsign address isn't shown
    # separately. Split it into flags/address, and start data1 from byte14.
    zero_runs = find_zero_runs(REF)
    segments = [("address (callsign, 14 bytes)", 0, 14)]
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

    seg_info = []
    boundary_points = [(0, int(payload_idx[0]))]   # (payload byte index, sample index)
    for label, b0, b1 in segments:
        bit0, bit1 = b0 * 8, min(b1 * 8, payload_idx.size)
        if bit0 >= payload_idx.size:
            continue
        s0, s1 = int(payload_idx[bit0]), int(payload_idx[min(bit1, payload_idx.size) - 1]) + SPS
        seg_err = err[bit0:bit1].sum()
        seg_n = bit1 - bit0
        seg_info.append((label, b0, b1, s0, s1, seg_err, seg_n))
        boundary_points.append((b1, s1))

    flag_pat = np.array([0, 1, 1, 1, 1, 1, 1, 0], dtype=np.uint8)
    raw_bits_full, raw_idx_full = slice_bits(yc, start, 0, span_j1)
    flag_hits = [i for i in range(raw_bits_full.size - 8)
                 if np.array_equal(raw_bits_full[i:i + 8], flag_pat)]
    tail_hits = [i for i in flag_hits if i > 2000]
    if tail_hits:
        tail_s0 = start + tail_hits[0] * SPS
        tail_s1 = start + (tail_hits[-1] + 8) * SPS
    else:
        tail_s0 = tail_s1 = None

    # ---------------- figure (overlaid, color-coded segments + byte boundary labels) ----------------
    fig, ax = plt.subplots(figsize=(18, 7.5))
    g0, g1 = start + G0_OFFSET, start + G1_OFFSET
    xx = np.arange(g0, g1)
    ax.plot(xx, y[g0:g1], "-", color=GRAY, lw=0.35, alpha=0.55, label="raw y")
    ax.plot(xx, yc[g0:g1], "-", color=BLUE, lw=0.5, alpha=0.8, label="y_comp")

    ax.axvspan(start, start + 32 * SPS, color=GREEN, alpha=0.40, lw=0,
              label="flags (4x0x7E, detection template)")

    colors_cycle = [PURPLE, YELLOW]
    zi = 0
    for label, b0, b1, s0, s1, seg_err, seg_n in seg_info:
        if label.startswith("address"):
            ax.axvspan(s0, s1, color="teal", alpha=0.30, lw=0,
                      label=f"{label}: byte[{b0},{b1}) BER={100*seg_err/seg_n:.0f}% (known, constant across frames)")
        elif label.startswith("zero-run"):
            c = colors_cycle[zi % len(colors_cycle)]
            zi += 1
            ax.axvspan(s0, s1, color=c, alpha=0.28, lw=0,
                      label=f"{label}: byte[{b0},{b1}) BER={100*seg_err/seg_n:.0f}%")
        else:
            ax.axvspan(s0, s1, color=ORANGE, alpha=0.15, lw=0,
                      label=f"{label} (real telemetry): byte[{b0},{b1}) BER={100*seg_err/seg_n:.0f}%")

    if fcs_s0 is not None:
        ax.axvspan(fcs_s0, fcs_s1, color="cyan", alpha=0.35, lw=0,
                  label=f"FCS (2-byte CRC): decoded {fcs_bytes.hex()} "
                        f"expected {expect_bytes.hex()} err={fcs_err}/16")
        boundary_points.append((274, fcs_s1))

    if tail_s0 is not None:
        ax.axvspan(tail_s0, tail_s1, color="brown", alpha=0.35, lw=0,
                  label=f"trailing flags ({len(tail_hits)}x0x7E)")
        ax.axvspan(tail_s1, g1, color="red", alpha=0.10, lw=0,
                  label="outside the frame (instrument idle noise, not part of this frame)")

    ylo, yhi = ax.get_ylim()
    for k, (byte_idx, s) in enumerate(boundary_points):
        ax.axvline(s, color="black", lw=0.9, ls=":", alpha=0.6)
        y_text = yhi - 0.08 * (yhi - ylo) if k % 2 == 0 else yhi - 0.16 * (yhi - ylo)
        ax.annotate(f"byte {byte_idx}\n(0x{byte_idx:02x})", xy=(s, y_text),
                   fontsize=7.5, ha="center", va="top", rotation=0,
                   bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="gray", alpha=0.85))

    ax.set_xlim(g0, g1)
    ax.set_xlabel("sample index")
    ax.set_ylabel("amplitude")
    ax.set_title(f"Frame #{frame_no} (start sample={start}, detection z-score={z_score:.1f})"
                 f" structure map + payload byte boundary labels"
                 f" (for comparison against REFERENCE_FRAME.md's hex dump; whole-payload BER={100*err.sum()/n:.1f}%)")
    ax.legend(loc="lower right", fontsize=7, ncol=2)
    p = save(fig, os.path.join("Figure", f"01_frame{frame_no}.png"))
    plt.close(fig)

    print(f"\n[Frame #{frame_no}] start sample={start}  z-score={z_score:.2f}")
    print(f"Figure saved: {p}")
    print("Boundary list (payload byte index -> sample index):")
    for byte_idx, s in boundary_points:
        print(f"  byte {byte_idx:4d} (0x{byte_idx:02x})  ->  sample {s}")


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
        plot_frame(plt, y, yc, s, i, z)


if __name__ == "__main__":
    main()
