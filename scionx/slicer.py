# -*- coding: utf-8 -*-
"""
slicer.py -- slice the analog amplitude coming out of symbol_sync into 0/1 bits.

Two slicers:
  - binary_slice: fixed threshold at 0 (equivalent to GNU Radio's
    digital.binary_slicer_fb, x >= 0 -> 1).
  - min_max_dynamic_threshold: min-max dynamic threshold (the ver2 core method,
    replacing the fixed threshold).

Units note: this stage sits on top of symbol_sync's output, and symbol_sync is
configured with osps=1 -> its output is already "1 sample = 1 symbol". So
min_max_dynamic_threshold's window_size unit is "symbols", independent of sps.
HDLC allows at most 6 consecutive 1s, so a window of roughly 8-24 symbols is
reasonable.

This module is pure numpy with no gnuradio dependency and can be tested offline.
"""

import numpy as np


def binary_slice(symbols):
    """
    Fixed threshold-at-0 slicing (equivalent to GNU Radio's binary_slicer_fb).

    Input: symbols  1D array
    Returns: decided_bits  uint8 (0/1), decision rule symbols >= 0
    Note: uses >= (not >), matching binary_slicer_fb's x>=0->1 behavior.
    """
    x = np.asarray(symbols, dtype=np.float64)
    return (x >= 0).astype(np.uint8)


def min_max_dynamic_threshold(signal, window_size):
    """
    Min-max envelope-tracking dynamic threshold.

    Idea: use the midpoint of the sliding window's upper/lower envelope
    (max+min)/2 as a "local threshold", so the threshold tracks residual
    baseline wander (deterministic, no feedback loop).

    Args:
        signal      1D array (baseband signal, e.g. symbol_sync's output)
        window_size sliding window length (unit: symbols; centered window.
                    For an even window_size: half_before = window_size // 2,
                    half_after = window_size - half_before - 1)
    Returns: (threshold, decided_bits)
        threshold    dynamic threshold, same length as signal (float64)
        decided_bits sliced result, uint8 (0/1), decision rule
                     signal > threshold (strictly greater than)

    Boundary handling: pads the edges by replication (np.pad(mode='edge'))
    before taking the sliding max/min. For max/min (unlike mean) this is
    equivalent to a shrinking boundary window -- repeating the edge value
    doesn't change the window's extrema, so both approaches give the same result.
    """
    x = np.asarray(signal, dtype=np.float64)
    n = x.size
    if n == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.uint8)
    if window_size < 1:
        raise ValueError("window_size must be >= 1")

    half_before = window_size // 2
    half_after = window_size - half_before - 1
    padded = np.pad(x, (half_before, half_after), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window_size)
    v_max = windows.max(axis=1)
    v_min = windows.min(axis=1)

    threshold = (v_max + v_min) / 2.0
    decided_bits = (x > threshold).astype(np.uint8)   # strict >
    return threshold, decided_bits
