# -*- coding: utf-8 -*-
"""
baseline.py -- decision-directed baseline restoration (numpy port of restore_baseline).

This is the one DSP algorithm in the whole pipeline that was actually ported
from an earlier MATLAB implementation (everything else -- low_pass /
symbol_sync / hdlc_deframer -- uses real GNU Radio blocks directly).

Idea: the recording channel has slow baseline wander/droop. Signal model:
    y[n] = A*s[n] + b[n] + v[n]
where s[n] in {-1,+1} is the NRZ symbol polarity, b[n] is the slowly varying
baseline, and v[n] is noise + ISI. If s were known, b = LPF(y - A*s). Since s
is unknown, hard-decide s from the currently baseline-corrected signal and
iterate (decision-directed). The LPF is a moving average with a window much
longer than the symbol period, so only the slow baseline survives.

Three details that were kept intentionally to stay numerically identical to
the original MATLAB port:
  1. A = median(|y|) (median, robust to outliers -- not RMS/mean).
  2. sign(0) is remapped to +1 (numpy's np.sign gives 0 for input 0).
  3. movmean is centered with a shrinking window at the boundaries (MATLAB
     truncates the window at the array edges and divides by the actual count
     of points, rather than zero-padding) -- see _movmean.
  4. The returned y_comp is computed at the "start of the last loop
     iteration" (= y - b^(K-1)), i.e. one iteration older than the final b^(K)
     (an off-by-one carried over from the original MATLAB code). This is
     reproduced faithfully here; the final b is also returned separately so
     callers can build a "one more subtraction" front end, y - b_final.
"""

import numpy as np


def _movmean(x, window_size):
    """
    Centered moving average matching MATLAB's movmean(x, window_size), with
    the window shrinking at the array boundaries.

    MATLAB's definition for an odd window_size = 2W+1: each output point is
    the average of the window centered on itself (W points before, W after);
    at the head/tail of the array the window gets truncated, and the divisor
    is the *actual* number of points in the (truncated) window, not a fixed
    window length padded with zeros. This differs from a plain convolution or
    uniform_filter1d(mode='nearest'), so it has to be computed explicitly.

    Approach: use a prefix sum to compute each point's window sum in O(n),
    then divide by that point's actual window length. For an even window_size,
    match MATLAB's convention: half_before = window_size // 2, half_after =
    window_size - window_size // 2 - 1.
    """
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n == 0:
        return x.copy()
    half_before = window_size // 2
    half_after = window_size - half_before - 1

    # prefix sum: csum[i] = sum(x[0:i]), length n+1, so any range sum [lo, hi) is easy
    csum = np.concatenate(([0.0], np.cumsum(x)))
    idx = np.arange(n)
    lo = np.maximum(0, idx - half_before)          # window left edge (inclusive)
    hi = np.minimum(n, idx + half_after + 1)        # window right edge (exclusive)
    window_sum = csum[hi] - csum[lo]
    window_len = (hi - lo).astype(np.float64)       # < window_size near the edges (shrunk)
    return window_sum / window_len


def restore_baseline(y, num_iters=7, W=1000):
    """
    Decision-directed baseline restoration.

    Args:
        y          raw signal (1D array, e.g. samples from audio_io)
        num_iters  number of iterations (default 7)
        W          movmean half-window radius, window length = 2W+1
                   (default 1000 -> 2001 samples ~= 41.7 ms @ 48 kHz)

    Returns: dict
        "d"           final hard decisions {-1,+1} (same length as y)
        "y_comp"      compensated signal = y - b^(K-1) (kept intentionally at
                      the "start of last iteration" baseline, one iteration
                      older than the final b -- an off-by-one carried over
                      from the original MATLAB port)
        "b"           final baseline estimate b^(K)
        "y_comp_final" = y - b^(K) (one more subtraction than y_comp; this is
                      the best-known front end, y minus the final baseline --
                      callers can pick whichever they need)
        "A"           amplitude estimate, median(|y|)

    Note: returns a dict rather than several positional values so the choice
    between "y_comp (one iteration behind)" and "y_comp_final" is explicit at
    the call site, instead of risking the off-by-one confusion again.
    """
    y = np.asarray(y, dtype=np.float64).reshape(-1)   # force to a 1D column-equivalent
    A = np.median(np.abs(y))                          # (1) amplitude = median absolute value
    b = np.zeros_like(y)                              # baseline estimate starts at 0

    y_comp = y - b                                    # default (degenerate num_iters=0 case)
    d = np.ones_like(y)
    for _ in range(num_iters):
        y_comp = y - b                                # uses the *previous* iteration's b (source of the off-by-one)
        d = np.sign(y_comp)                           # hard decision {-1,0,+1}
        d[d == 0] = 1                                 # (2) sign(0) -> +1
        e = y - A * d                                 # residual after removing the ideal symbol
        b = _movmean(e, 2 * W + 1)                    # (3) slow baseline = moving average of the residual

    # loop finished: y_comp is left at the last iteration's start value (= y - b^(K-1)); b is the final b^(K).
    return {
        "d": d,
        "y_comp": y_comp,        # one iteration behind (= what the original restore_baseline returned)
        "b": b,                  # final baseline
        "y_comp_final": y - b,   # one more subtraction (= y - b_final, the best-known front end uses this)
        "A": A,
    }
