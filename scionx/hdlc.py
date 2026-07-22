# -*- coding: utf-8 -*-
"""
hdlc.py -- pure HDLC/flag-related logic (no gnuradio dependency, testable offline).

Contains:
  - FLAG constants (7E / 7E7E / 7E7E7E).
  - count_overlap: overlapping flag counting (matches MATLAB's strfind behavior).
  - metrics_from_bits: computes flag1/flag2/flag3 + inverted-polarity versions
    in one pass (used for sweep scoring).
  - find_candidate_bit_ranges: replays the hdlc_deframer's flag/destuff state
    machine over a bit string to find candidate frame bit ranges (plotting
    only -- does not participate in CRC).
  - crc16_x25: CRC-16/X.25 (for tests/offline verification; the real
    pipeline's CRC is decided by gr-satellites' hdlc_deframer, see
    gnuradio_blocks.py).

The real HDLC deframing (flag detection, bit destuffing, CRC) in the pipeline
uses the actual satellites.hdlc_deframer; the state machine in this file only
exists to recompute flag boundaries for plotting, and to provide a CRC that
can be tested offline.
"""

import re
import numpy as np

# HDLC flag bit patterns (on-wire, after LSB-first)
FLAG1 = "01111110"                          # 0x7E
FLAG2 = "0111111001111110"                  # 0x7E 0x7E
FLAG3 = "011111100111111001111110"          # 0x7E 0x7E 0x7E
_INV = str.maketrans("01", "10")            # bit-inversion table


def _bits_to_str(bits):
    """0/1 array -> string, for regex-based counting."""
    arr = np.asarray(bits)
    return "".join(np.where(arr != 0, "1", "0").tolist())


def count_overlap(bitstr, pat):
    """
    Overlapping count, matching MATLAB strfind / a Python lookahead regex.
    e.g. count_overlap("0101010", "010") == 3.
    """
    return len(re.findall("(?=" + pat + ")", bitstr))


def find_overlapping_positions(bits, pat):
    """
    Return the list of (overlapping) start indices where pat occurs in the
    bit string (0-based). Used to mark flag positions on plots (matches what
    MATLAB's strfind would return).
    """
    bits_str = _bits_to_str(bits)
    return [m.start() for m in re.finditer("(?=" + pat + ")", bits_str)]


def metrics_from_bits(bits):
    """
    Compute flag metrics from a sliced bit array (0/1), including
    inverted-polarity versions.

    Returns dict: flag1/flag1_inv/flag2/flag2_inv/flag3/flag3_inv.
    The inverted versions handle the case where baseline restoration flips
    the whole signal's polarity.
    """
    bits_str = _bits_to_str(bits)
    inv = bits_str.translate(_INV)
    return {
        "flag1": count_overlap(bits_str, FLAG1),
        "flag1_inv": count_overlap(inv, FLAG1),
        "flag2": count_overlap(bits_str, FLAG2),
        "flag2_inv": count_overlap(inv, FLAG2),
        "flag3": count_overlap(bits_str, FLAG3),
        "flag3_inv": count_overlap(inv, FLAG3),
    }


def find_candidate_bit_ranges(bits):
    """
    Replay satellites.hdlc_deframer.work()'s flag/destuff state machine over
    bits (0/1 array) to find each candidate frame's [start, end] (0-based,
    inclusive) bit range.

    Plotting only -- does not participate in, or affect, any CRC decision
    (the CRC is produced by the real hdlc_deframer).

    Whether a candidate gets recorded is decided by "bufLen > 0 after pop"
    (equivalent to the Python hdlc_deframer's `if frame:`), not a fixed
    offset guess -- this keeps the candidate count and order exactly matching
    the real crc_records. The range boundaries themselves are approximate
    (for plotting only).

    Returns: list of [start, end] (0-based indices, inclusive), in the same
    order as crc_records.
    """
    b = np.asarray(bits).reshape(-1)
    n = b.size
    ranges = []
    ones_run = 0          # length of the current run of consecutive 1s
    ones_run_start = 0    # start of this run of 1s (0-based)
    buf_len = 0           # mirrors the length of the Python deframer's self.bits
    seg_start = 0         # start of the current candidate segment (0-based)

    for i in range(n):
        if b[i] == 1:
            if ones_run == 0:
                ones_run_start = i
            ones_run += 1
            buf_len += 1                     # every 1 is always accumulated
        else:
            if ones_run == 5:
                # stuffed 0 (bit-stuffing): don't append it, doesn't count as
                # a flag or a data-boundary event
                ones_run = 0
            elif ones_run > 5:
                # a flag was detected (0111111...0): pop min(7, bufLen)
                # (the leading 0 plus six 1s)
                pop_n = min(7, buf_len)
                buf_len -= pop_n
                if buf_len > 0:
                    # only record a non-empty candidate (equivalent to the
                    # Python side's `if frame:`)
                    seg_end = max(seg_start, ones_run_start - 2)   # approximate boundary, for plotting only
                    ranges.append([seg_start, seg_end])
                seg_start = i + 1
                buf_len = 0
                ones_run = 0
            else:
                buf_len += 1                 # ordinary data 0
                ones_run = 0
    return ranges


def crc16_x25(data):
    """
    CRC-16/X.25 (= HDLC FCS).

    Spec: polynomial 0x1021, LSB-first so the reflected value 0x8408 is used;
    init 0xFFFF; refin/refout=true; xorout 0xFFFF. The FCS is appended after
    the payload in little-endian order.

    Args: data  bytes / bytearray / iterable of int (0..255)
    Returns: int   16-bit CRC value

    Note: the CRC-16 space only has 65536 values, so any input has roughly a
    1/65536 chance of coincidentally passing; one special case is
    crc16_x25(b'\\xff\\xff') == 0xFFFF (an idle line of all-0xFF bytes
    coincidentally passes the CRC).
    """
    crc = 0xFFFF
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc ^ 0xFFFF
