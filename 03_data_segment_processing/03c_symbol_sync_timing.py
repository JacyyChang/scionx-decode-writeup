# -*- coding: utf-8 -*-
"""
03c_symbol_sync_timing.py -- try GNU Radio's real digital.symbol_sync_ff (with
an optional front-end filter) as a replacement for the fixed-PHASE/SPS sampling
used everywhere else in this repo, scoped to frame1 only.

*** GNU RADIO REQUIRED -- this is the one script in Share/ that isn't
    GNU-Radio-free. It must be run with a Python that has gnuradio installed
    (confirmed present in `C:\\Users\\USER\\radioconda\\python.exe`, GNU Radio
    3.10.12). All other scripts in this repo stay dependency-light on
    purpose (see the root CLAUDE.md); this one is the deliberate exception. ***

Why: 01/02/03 all sample the signal at a fixed rate/phase (SPS=5, PHASE=2)
with no real timing recovery -- the main README lists switching to
`symbol_sync` (Gardner/M&M timing-error-detector loops) as an open future
direction. This script tries it for real, sweeps its parameters (plus a
front-end filter, since a TED's performance depends heavily on how
eye-diagram-shaped the signal already is), and scores every combination by:

  1. CRC-16/X.25 pass/fail on the full destuffed frame (the actual ground
     truth the whole project is chasing) -- if anything ever passes this,
     it's the headline result, full stop.
  2. header+address bit-error count over the 144 known bits (real ground
     truth, just narrower) -- primary tie-break.
  3. count of data1/data2 symbols sitting within MARGIN_FRAC*A of the
     decision line (no ground truth there, so this is a proxy for "how open
     is the eye", used only to break remaining ties).

Pipeline per swept config:
  - Cut a raw-y window around frame1 (found the same way 01/03b do: a
    baseline-restored normalized cross-correlation, used *only* to locate
    the frame -- the window fed to symbol_sync is raw y, unmodified).
  - Optionally FIR-filter the window (none / one-symbol boxcar matched
    filter / a low-pass at 0.5, 0.75, or 1.0x the symbol rate).
  - Run it through `digital.symbol_sync_ff(ted_type, sps=5.0, loop_bw, ...)`
    inside a tiny GR top_block (vector_source_f -> [filter] -> symbol_sync_ff
    -> vector_sink_f).
  - symbol_sync resamples to ~1 sample/symbol, so the sample-index tracking
    the rest of 03 uses (SPS*n+PHASE) no longer applies. Instead, the known
    144-bit header+address NRZ pattern is normalized-cross-correlated
    against the recovered symbol stream to find the frame's start offset
    k* (also fixes polarity, in case the loop/filter inverted it) --
    the symbol-domain equivalent of what 01_frame_detection.py does in the
    sample domain.
  - From there: calibrate a threshold from the header, destuff, locate
    data1/data2/zero-run2/FCS by the same REFERENCE_FRAME.md byte map 03b
    uses, and score as above.

Output: Figure/03c_frame1.png (eye diagram of the winning filter + a 03b-style
annotated symbol trace) and a full sweep ranking printed to the terminal,
including a live-recomputed comparison against the current fixed-PHASE method.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

try:
    from gnuradio import gr, blocks, digital
    from gnuradio import filter as grfilter
except ImportError as exc:
    raise ImportError(
        "This script needs GNU Radio, which the default Python here doesn't have.\n"
        "Run it with the radioconda Python instead, e.g.:\n"
        r"  C:\Users\USER\radioconda\python.exe 03c_symbol_sync_timing.py"
    ) from exc

from scionx import audio_io, baseline               # noqa: E402
from scionx.hdlc import crc16_x25, metrics_from_bits  # noqa: E402
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
SEARCH_SAMPLES = 19211    # same window 01/03b use: header through the trailing flags
MARGIN_FRAC = 0.10        # "close to the decision line" half-band, fraction of A=median(|y|)

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
# Shared helpers, duplicated from 03b (this repo's convention: every script
# is self-contained and independently runnable, see CLAUDE.md) -- used here
# to (a) locate frame1 and (b) live-recompute the current fixed-PHASE
# baseline for comparison, exactly as 03b would report it.
# ============================================================================

def bits_lsb_first(data):
    out = []
    for b in data:
        for k in range(8):
            out.append((b >> k) & 1)
    return out


def build_header_template():
    """Flags+address NRZ template (+/-1), upsampled by SPS -- used only for
    frame-start detection (sample domain)."""
    bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.float64)
    nrz = 2 * bits - 1
    return np.repeat(nrz, SPS)


def detect_frame_starts(yc, template):
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
    """Replay the HDLC bit-destuffing state machine, keeping each surviving
    bit's original position (a sample index in the sample domain, or a
    symbol index in the symbol domain -- the function doesn't care)."""
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


def build_segment_map():
    """Byte-range map (address/data1/zero-run1/data2/zero-run2/FCS), derived
    from REFERENCE_FRAME.md's known zero-runs -- identical logic to 03b."""
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
    segments.append(("FCS", 272, 274))
    return segments


def calibrate_offset_threshold(vals, t_bits):
    """Best fixed offset threshold over a set of symbol-center values whose
    true bit values are known (header+address, 144 bits)."""
    v1, v0 = vals[t_bits == 1], vals[t_bits == 0]
    grid = np.linspace(min(v0.min(), v1.min()), max(v0.max(), v1.max()), 2000)
    errs = [(v1 <= t).sum() + (v0 > t).sum() for t in grid]
    thr = grid[int(np.argmin(errs))]
    return thr, int(min(errs))


def fixed_phase_baseline(y, start):
    """Live-recompute what the *current* fixed-PHASE/SPS method (03b) gets
    for frame1, for a direct, non-stale comparison against symbol_sync."""
    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    idx_hdr = start + SPS * np.arange(0, 144) + PHASE
    offset_thr, hdr_err = calibrate_offset_threshold(y[idx_hdr], t_bits)
    A = np.median(np.abs(y))
    margin = MARGIN_FRAC * A

    span_j1 = int(SEARCH_SAMPLES / SPS)
    idx_all = start + SPS * np.arange(0, span_j1) + PHASE
    idx_all = idx_all[idx_all < y.size]
    body_bits_raw = (y[idx_all[32:]] > 0).astype(np.uint8)
    body_idx_raw = idx_all[32:]
    body_bits, body_sample_idx = destuff_with_map(body_bits_raw, body_idx_raw)

    n_near, n_data = 0, 0
    for label, b0, b1 in build_segment_map():
        if not label.startswith("data"):
            continue
        bit0, bit1 = b0 * 8, min(b1 * 8, body_sample_idx.size)
        if bit0 >= bit1:
            continue
        idxs = body_sample_idx[bit0:bit1]
        vals = y[idxs]
        n_near += (np.abs(vals - offset_thr) <= margin).sum()
        n_data += vals.size

    # CRC check on the raw fixed-phase destuffed bits, for the same headline metric
    crc_ok = False
    if body_bits.size >= 274 * 8:
        payload_bits = body_bits[:272 * 8]
        fcs_bits = body_bits[272 * 8:274 * 8]
        frame_bytes = bits_to_bytes(payload_bits)
        fcs_bytes = bits_to_bytes(fcs_bits)
        crc_ok = (crc16_x25(frame_bytes).to_bytes(2, "little") == fcs_bytes)

    return {"hdr_err": hdr_err, "near": int(n_near), "n_data": int(n_data), "crc_ok": crc_ok}


# ============================================================================
# symbol_sync-domain analysis (the new part)
# ============================================================================

TED_CHOICES = {
    "GARDNER": digital.TED_GARDNER,
    "MUELLER_AND_MULLER": digital.TED_MUELLER_AND_MULLER,
    "MOD_MUELLER_AND_MULLER": digital.TED_MOD_MUELLER_AND_MULLER,
    "ZERO_CROSSING": digital.TED_ZERO_CROSSING,
    "EARLY_LATE": digital.TED_EARLY_LATE,
}

LOOP_BW_CHOICES = [0.005, 0.01, 0.02, 0.04, 0.08]

FILTER_CHOICES = ["none", "boxcar", "lpf_0.5", "lpf_0.75", "lpf_1.0"]


def build_filter_taps(kind):
    """Front-end filter options, all evaluated at the raw sample rate before
    symbol_sync sees the signal. AX.25/AFSK here is unshaped NRZ (no RRC
    pulse shaping), so the "textbook optimal" choice is a matched filter =
    one-symbol-wide boxcar; the low-pass variants (cutoff as a multiple of
    the symbol rate Rs=FS/SPS) are the standard alternative. Rather than
    picking one by eye, this is swept and scored like every other parameter
    -- see the "Filter design" note in the plan this script implements."""
    if kind == "none":
        return None
    if kind == "boxcar":
        return (np.ones(SPS) / SPS).tolist()
    rs = FS / SPS
    mult = {"lpf_0.5": 0.5, "lpf_0.75": 0.75, "lpf_1.0": 1.0}[kind]
    cutoff = mult * rs
    transition = 0.2 * rs
    return list(grfilter.firdes.low_pass(1.0, FS, cutoff, transition))


_BPSK_SLICER = digital.constellation_bpsk().base()  # NRZ = one bit/symbol = BPSK-shaped decision;
                                                     # required by the decision-directed TEDs
                                                     # (M&M / mod-M&M / zero-crossing / early-late),
                                                     # harmless for Gardner (which ignores it)


def run_symbol_sync(window, ted_type, loop_bw, taps, damping=1.0, ted_gain=1.0, max_dev=1.5):
    """vector_source_f -> [FIR filter] -> symbol_sync_ff -> vector_sink_f,
    run once over a finite cut buffer (fine for a single-frame experiment;
    see GNURADIO_MIGRATION.md for how this would sit in a per-frame PDU
    stage of a real streaming flowgraph)."""
    tb = gr.top_block()
    src = blocks.vector_source_f(window.astype(np.float64).tolist(), False)
    last = src
    if taps is not None:
        filt = grfilter.fir_filter_fff(1, taps)
        tb.connect(last, filt)
        last = filt
    ss = digital.symbol_sync_ff(ted_type, float(SPS), float(loop_bw), damping, ted_gain, max_dev, 1,
                                 _BPSK_SLICER)
    tb.connect(last, ss)
    sink = blocks.vector_sink_f()
    tb.connect(ss, sink)
    tb.run()
    return np.array(sink.data(), dtype=np.float64)


def gr_filtered_signal(window, taps):
    """Return exactly the signal symbol_sync saw at its input -- i.e. `window`
    pushed through the *same* causal `fir_filter_fff(1, taps)` used in
    run_symbol_sync (not a zero-phase np.convolve). Used for the eye diagram so
    its phase matches reality regardless of the filter's group delay. With
    taps=None the signal is unfiltered, so `window` is returned as-is."""
    if taps is None:
        return window.astype(np.float64)
    tb = gr.top_block()
    src = blocks.vector_source_f(window.astype(np.float64).tolist(), False)
    filt = grfilter.fir_filter_fff(1, taps)
    sink = blocks.vector_sink_f()
    tb.connect(src, filt, sink)
    tb.run()
    return np.array(sink.data(), dtype=np.float64)


def align_header(output, template):
    """Normalized cross-correlation of the known 144-bit header+address NRZ
    pattern against the recovered (1 sample/symbol) stream -- the
    symbol-domain equivalent of 01_frame_detection.py's frame-start search.
    Also resolves polarity (the loop or filter may have inverted the sign).
    Returns (k_star, polarity, corr) or (None, 1.0, 0.0) if the stream is too
    short to contain a full header."""
    n, L = output.size, template.size
    if n <= L + 1:
        return None, 1.0, 0.0
    tmpl_c = template - template.mean()
    tmpl_norm = np.sqrt(np.sum(tmpl_c ** 2))

    best_k, best_corr = None, 0.0
    for k in range(n - L):
        seg = output[k:k + L]
        seg_c = seg - seg.mean()
        denom = np.sqrt(np.sum(seg_c ** 2)) * tmpl_norm
        if denom < 1e-9:
            continue
        c = float(np.dot(seg_c, tmpl_c) / denom)
        if abs(c) > abs(best_corr):
            best_corr, best_k = c, k
    if best_k is None:
        return None, 1.0, 0.0
    polarity = 1.0 if best_corr >= 0 else -1.0
    return best_k, polarity, best_corr


def score_config(aligned, t_bits, A):
    """Given the polarity-corrected, header-aligned symbol stream (index 0 =
    first flag bit), calibrate a threshold from the header, destuff, locate
    data1/data2/zero-run2/FCS, and return every metric needed to rank and
    plot this config."""
    margin = MARGIN_FRAC * A
    header_syms = aligned[:144]
    offset_thr, hdr_err = calibrate_offset_threshold(header_syms, t_bits)

    decided_bits = (aligned > offset_thr).astype(np.uint8)
    body_bits_raw = decided_bits[32:]                       # skip the 4 leading flags
    body_idx_raw = np.arange(32, decided_bits.size)          # positions *within aligned*
    body_bits, body_sym_idx = destuff_with_map(body_bits_raw, body_idx_raw)

    crc_ok = False
    if body_bits.size >= 274 * 8:
        payload_bits = body_bits[:272 * 8]
        fcs_bits = body_bits[272 * 8:274 * 8]
        frame_bytes = bits_to_bytes(payload_bits)
        fcs_bytes = bits_to_bytes(fcs_bits)
        crc_ok = (crc16_x25(frame_bytes).to_bytes(2, "little") == fcs_bytes)

    segs = []
    n_near_total, n_data_total = 0, 0
    for label, b0, b1 in build_segment_map():
        bit0, bit1 = b0 * 8, min(b1 * 8, body_sym_idx.size)
        if bit0 >= bit1:
            continue
        s0, s1 = int(body_sym_idx[bit0]), int(body_sym_idx[bit1 - 1]) + 1
        segs.append((label, s0, s1))
        if label.startswith("data"):
            vals = aligned[s0:s1]
            near = np.abs(vals - offset_thr) <= margin
            n_near_total += int(near.sum())
            n_data_total += vals.size

    flags = metrics_from_bits(decided_bits)

    return {
        "hdr_err": hdr_err, "crc_ok": crc_ok,
        "near": n_near_total, "n_data": n_data_total,
        "offset_thr": offset_thr, "margin": margin,
        "segs": segs, "flags": flags,
        "decided_bits": decided_bits,
    }


def sweep_configs():
    for ted_name, ted_type in TED_CHOICES.items():
        for loop_bw in LOOP_BW_CHOICES:
            for filt_name in FILTER_CHOICES:
                yield {"ted_name": ted_name, "ted_type": ted_type,
                       "loop_bw": loop_bw, "filt_name": filt_name}


def rank_key(result):
    """CRC pass beats everything; then header BER; then near-decision-line
    count in data1/data2 as the final tie-break (user-confirmed objective).
    Failed/no-lock configs are pushed to the very bottom."""
    if result is None:
        return (2, 999, 999999)
    return (0 if result["crc_ok"] else 1, result["hdr_err"], result["near"])


def plot_best(plt, y, start, window, best_cfg, best_result, baseline_result):
    A = np.median(np.abs(y))
    aligned = None  # recomputed below for plotting (score_config discards it)

    taps = build_filter_taps(best_cfg["filt_name"])
    output = run_symbol_sync(window, best_cfg["ted_type"], best_cfg["loop_bw"], taps)
    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    nrz_template = 2 * t_bits.astype(np.float64) - 1
    k_star, polarity, corr = align_header(output, nrz_template)
    aligned = output[k_star:] * polarity

    fig, (ax_eye, ax_sym) = plt.subplots(2, 1, figsize=(16, 10),
                                          gridspec_kw={"height_ratios": [1, 1.4]})

    # ---- Panel A: eye diagram of the chosen filter, fixed-phase folding ----
    # Use the *actual* GR-filtered signal symbol_sync saw (causal fir_filter,
    # group delay included), not a zero-phase np.convolve -- otherwise the eye's
    # phase can silently disagree with reality whenever the filter's group delay
    # isn't an integer number of symbols (it happens to be exactly 6 symbols for
    # the 61-tap LPFs, but 0.4 symbols for the boxcar).
    filt_signal = gr_filtered_signal(window, taps)
    n_periods = filt_signal.size // SPS
    trace_len = 2 * SPS
    n_traces = max(0, n_periods - 2)
    for i in range(n_traces):
        seg = filt_signal[i * SPS: i * SPS + trace_len]
        if seg.size == trace_len:
            ax_eye.plot(np.arange(trace_len), seg, color=BLUE, alpha=0.04, lw=0.8)
    ax_eye.axvline(SPS, color="k", lw=1.0, ls="--", label="symbol boundary")
    ax_eye.axvline(PHASE, color=PURPLE, lw=1.2, ls=":", label=f"old fixed PHASE={PHASE}")
    ax_eye.set_title(f"Eye diagram after front-end filter = {best_cfg['filt_name']} "
                      f"(fixed-phase folding, {n_traces} overlaid symbol periods)")
    ax_eye.set_xlabel("sample within a 2-symbol window")
    ax_eye.set_ylabel("amplitude")
    ax_eye.legend(loc="upper right", fontsize=8)

    # ---- Panel B: recovered symbol stream, 03b visual style ----
    # Clip to the actual frame span (through FCS + a small guard) *before*
    # computing anything -- otherwise "near" would silently include whatever
    # noise/trailing-flags sit beyond FCS in `aligned` (it runs to the end of
    # the whole cut window), inflating the count shown in the legend past the
    # data1/data2-only figure already reported in the terminal.
    seg_map = dict((label, (s0, s1)) for label, s0, s1 in best_result["segs"])
    plot_end = min(seg_map.get("FCS", (0, aligned.size))[1] + 20, aligned.size)

    offset_thr, margin = best_result["offset_thr"], best_result["margin"]
    xs_p = np.arange(plot_end)
    aligned_p = aligned[:plot_end]
    bits_p = best_result["decided_bits"][:plot_end]
    near_p = np.abs(aligned_p - offset_thr) <= margin

    # Only ring near-threshold points that actually fall inside data1/data2 --
    # matches the official near-count used for ranking (best_result["near"])
    # exactly, rather than a different, uncontrolled quantity that also covers
    # the header/zero-runs/FCS.
    in_data = np.zeros(plot_end, dtype=bool)
    for label, s0, s1 in best_result["segs"]:
        if label.startswith("data"):
            in_data[s0:min(s1, plot_end)] = True
    near_p = near_p & in_data

    if "zero-run2" in seg_map and "FCS" in seg_map:
        zr2_s0, _ = seg_map["zero-run2"]
        fcs_s0, fcs_s1 = seg_map["FCS"]
        ax_sym.axvspan(zr2_s0, fcs_s0, color=GRAY, alpha=0.3, zorder=0,
                       label="zero-run2 (expected all-zero) -- masked out")
        ax_sym.axvspan(fcs_s0, fcs_s1, color="cyan", alpha=0.15, zorder=0,
                       label="FCS (2-byte CRC)")
    if "address" in seg_map:
        addr_s0, addr_s1 = seg_map["address"]
        ax_sym.axvspan(0, addr_s1, color="teal", alpha=0.12, zorder=0,
                       label="header+address (known, 144 bits)")

    ax_sym.plot(xs_p, aligned_p, "-", color=GRAY, lw=0.5, alpha=0.6, label="recovered symbols (raw)")
    ax_sym.axhline(0, color="k", lw=0.8, ls="--", label="old threshold=0")
    ax_sym.axhline(offset_thr, color=PURPLE, lw=1.8, label=f"threshold={offset_thr:+.3f}")
    ax_sym.axhline(offset_thr - margin, color=PURPLE, lw=0.8, ls=":", alpha=0.7,
                   label=f"±{MARGIN_FRAC*100:.0f}%A band")
    ax_sym.axhline(offset_thr + margin, color=PURPLE, lw=0.8, ls=":", alpha=0.7)

    colors = [BLUE if b == 1 else ORANGE for b in bits_p]
    ax_sym.scatter(xs_p, aligned_p, c=colors, s=16, zorder=4, edgecolors="k", linewidths=0.25,
                   label="decided as 1 (blue) / decided as 0 (orange)")
    if near_p.any():
        ax_sym.scatter(xs_p[near_p], aligned_p[near_p], s=60, facecolors="none",
                       edgecolors="red", linewidths=1.3, zorder=5,
                       label=f"near decision line, data1/data2 only ({near_p.sum()} total)")

    ax_sym.set_xlim(0, plot_end)
    ax_sym.set_xlabel("recovered symbol index (0 = first flag bit, after header alignment)")
    ax_sym.set_ylabel("recovered amplitude")
    ax_sym.set_title(
        f"symbol_sync recovered stream -- ted={best_cfg['ted_name']}  loop_bw={best_cfg['loop_bw']}  "
        f"filter={best_cfg['filt_name']}  |  header err={best_result['hdr_err']}/144  "
        f"CRC {'PASS' if best_result['crc_ok'] else 'fail'}")
    ax_sym.legend(loc="upper right", fontsize=7, ncol=2)

    fig.suptitle(
        f"Frame #1 (start sample={start}): best symbol_sync config vs. current fixed-PHASE "
        f"(header err {baseline_result['hdr_err']}/144, near={baseline_result['near']}/{baseline_result['n_data']})",
        fontsize=12)
    p = save(fig, os.path.join("Figure", "03c_frame1.png"))
    plt.close(fig)
    return p


def main():
    plt = setup_mpl()
    y, fs = audio_io.read_audio(AUDIO, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]     # only used to locate the frame; symbol_sync gets raw y

    template = build_header_template()
    starts, zs = detect_frame_starts(yc, template)
    start = starts[0]
    print(f"Frame #1 located at sample={start} (z-score={zs[0]:.2f})")

    baseline_result = fixed_phase_baseline(y, start)
    print("=" * 78)
    print(f"Current fixed-PHASE method (03b), frame1: header err={baseline_result['hdr_err']}/144  "
          f"near-decision-line={baseline_result['near']}/{baseline_result['n_data']}  "
          f"CRC={'PASS' if baseline_result['crc_ok'] else 'fail'}")
    print("=" * 78)

    w0 = max(0, start - 200)
    w1 = min(y.size, start + SEARCH_SAMPLES)
    window = y[w0:w1]

    t_bits = np.array(bits_lsb_first(FLAGS4) + bits_lsb_first(HEADER_ADDR), dtype=np.uint8)
    nrz_template = 2 * t_bits.astype(np.float64) - 1
    A = np.median(np.abs(y))

    results = []
    configs = list(sweep_configs())
    print(f"Sweeping {len(configs)} symbol_sync configs (ted x loop_bw x filter) on frame1...")
    for cfg in configs:
        taps = build_filter_taps(cfg["filt_name"])
        try:
            output = run_symbol_sync(window, cfg["ted_type"], cfg["loop_bw"], taps)
            k_star, polarity, corr = align_header(output, nrz_template)
            if k_star is None or abs(corr) < 0.3:
                results.append((cfg, None))
                continue
            aligned = output[k_star:] * polarity
            if aligned.size < 274 * 8:      # not enough recovered symbols to reach FCS
                results.append((cfg, None))
                continue
            result = score_config(aligned, t_bits, A)
            results.append((cfg, result))
        except Exception as exc:   # noqa: BLE001 -- one bad config must not kill the sweep
            print(f"  [skip] {cfg['ted_name']} loop_bw={cfg['loop_bw']} filter={cfg['filt_name']}: {exc}")
            results.append((cfg, None))

    results.sort(key=lambda cr: rank_key(cr[1]))

    print("\nTop 10 configs (CRC pass > lower header err > fewer near-threshold bits):")
    print(f"{'rank':>4} {'ted':<24}{'loop_bw':>9} {'filter':<10}{'hdr_err':>9}{'near':>8}{'crc':>6}")
    for i, (cfg, res) in enumerate(results[:10], start=1):
        if res is None:
            print(f"{i:>4} {cfg['ted_name']:<24}{cfg['loop_bw']:>9} {cfg['filt_name']:<10}"
                  f"{'--':>9}{'--':>8}{'no-lock':>8}")
        else:
            print(f"{i:>4} {cfg['ted_name']:<24}{cfg['loop_bw']:>9} {cfg['filt_name']:<10}"
                  f"{res['hdr_err']:>9}{res['near']:>8}{'PASS' if res['crc_ok'] else '-':>6}")

    best_cfg, best_result = results[0]
    if best_result is None:
        print("\nNo config achieved header lock (corr >= 0.3) -- symbol_sync did not "
              "produce a usable result on frame1 with this grid.")
        return

    print("\n" + "=" * 78)
    print(f"BEST: ted={best_cfg['ted_name']}  loop_bw={best_cfg['loop_bw']}  filter={best_cfg['filt_name']}")
    print(f"  header err = {best_result['hdr_err']}/144   "
          f"(fixed-PHASE baseline: {baseline_result['hdr_err']}/144)")
    print(f"  data1+data2 near-decision-line = {best_result['near']}/{best_result['n_data']} "
          f"({100*best_result['near']/max(1,best_result['n_data']):.1f}%)   "
          f"(fixed-PHASE baseline: {baseline_result['near']}/{baseline_result['n_data']} "
          f"({100*baseline_result['near']/max(1,baseline_result['n_data']):.1f}%))")
    print(f"  CRC-16/X.25: {'PASS' if best_result['crc_ok'] else 'fail'}   "
          f"(fixed-PHASE baseline: {'PASS' if baseline_result['crc_ok'] else 'fail'})")
    print(f"  flag counts (decided bitstream): {best_result['flags']}")
    print("=" * 78)

    p = plot_best(plt, y, start, window, best_cfg, best_result, baseline_result)
    print(f"\nFigure saved: {p}")


if __name__ == "__main__":
    main()
