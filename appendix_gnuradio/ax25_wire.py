# -*- coding: utf-8 -*-
"""
ax25_wire.py -- build the on-wire bit stream for a known AX.25/HDLC frame, in
both possible byte-serialization orders, so a GNU Radio flowgraph can be fed a
signal whose correct answer is known in advance.

Why this exists: `04_Beacon/04_beacon_field_decode.py` reads multi-bit telemetry
fields out of the destuffed bitstream MSB-first, while `scionx/hdlc.py`'s
`bits_to_bytes` (and everything in 01/02/03 that reassembles bytes) reads it
LSB-first. Those two cannot both be right. Rather than settle it by reading the
AX.25 spec and trusting our own reimplementation of it, this module builds the
*same* known frame both ways and lets gr-satellites' real `hdlc_deframer` --
an independent, widely-used implementation -- be the judge: whichever bit order
it accepts (FCS pass) and returns as the original bytes is the on-wire order.

This is a pure-numpy/stdlib module with no GNU Radio import, so it can be
sanity-checked on its own (`python ax25_wire.py`); the flowgraph
`hdlc_bitorder_test.grc` embeds this same logic in its own Python Module
block (rather than importing this file) so it stays self-contained with no
`sys.path` setup -- see that file's README for why.

Frame built here = REFERENCE_FRAME.md's 272-byte ground-test payload, which is
also the decode oracle the rest of the repo scores against.
"""

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

N_FLAGS = 4          # leading/trailing 0x7E flags, same count 01/02/03's template uses


def crc16_x25(data):
    """CRC-16/X.25 (= HDLC FCS). Same implementation as scionx/hdlc.py:
    poly 0x1021 reflected to 0x8408, init 0xFFFF, refin/refout, xorout 0xFFFF."""
    crc = 0xFFFF
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def serialize(data, lsb_first):
    """bytes -> list of 0/1, one bit per element (the "unpacked bits" format
    GNU Radio's byte-stream blocks and hdlc_deframer expect).

    lsb_first=True  -> byte 0x7C goes out as 0,0,1,1,1,1,1,0   (AX.25 spec)
    lsb_first=False -> byte 0x7C goes out as 0,1,1,1,1,1,0,0   (the other order)
    """
    out = []
    for b in data:
        for k in (range(8) if lsb_first else range(7, -1, -1)):
            out.append((b >> k) & 1)
    return out


def bit_stuff(bits):
    """HDLC bit stuffing: insert a 0 after every run of five consecutive 1s, so
    the flag pattern 0x7E (six 1s) can never occur inside the frame body."""
    out, ones = [], 0
    for b in bits:
        out.append(b)
        ones = ones + 1 if b == 1 else 0
        if ones == 5:
            out.append(0)
            ones = 0
    return out


def build_wire(payload, lsb_first, n_flags=N_FLAGS):
    """Full on-wire bit stream: flags + bit-stuffed(payload + FCS) + flags.

    Note the flag byte 0x7E = 01111110 is a bit-palindrome, so it serializes
    identically in both orders -- meaning hdlc_deframer will find frame
    boundaries in *both* streams. What differs is only the body, so the
    deframer's FCS check is what actually discriminates the two orders (and
    that's exactly the point of this test)."""
    fcs = crc16_x25(payload).to_bytes(2, "little")     # FCS appended little-endian
    body = bit_stuff(serialize(payload + fcs, lsb_first))
    flag = [0, 1, 1, 1, 1, 1, 1, 0]                    # 0x7E
    return flag * n_flags + body + flag * n_flags


# The two candidate streams the flowgraph feeds to hdlc_deframer.
WIRE_LSB = build_wire(REF, lsb_first=True)     # AX.25 spec order -- expected to decode
WIRE_MSB = build_wire(REF, lsb_first=False)    # the other order -- expected to fail FCS


if __name__ == "__main__":
    fcs = crc16_x25(REF)
    print(f"reference payload : {len(REF)} bytes, first 16 = {REF[:16].hex(' ')}")
    print(f"computed FCS      : 0x{fcs:04X} -> on-wire bytes {fcs.to_bytes(2,'little').hex(' ')}")
    print(f"WIRE_LSB          : {len(WIRE_LSB)} bits")
    print(f"WIRE_MSB          : {len(WIRE_MSB)} bits")
    print()
    demo = bytes([0x7C])
    lsb = "".join(map(str, serialize(demo, True)))
    msb = "".join(map(str, serialize(demo, False)))
    print("Single-byte illustration of what the two orders mean (0x7C):")
    print(f"  value 0x7C = {0x7C:08b} (MSB on the left, normal notation)")
    print(f"  LSB-first on the wire : {lsb}")
    print(f"  MSB-first on the wire : {msb}")
    print()
    print("Run hdlc_bitorder_test.grc (or its generated .py) to see which one")
    print("gr-satellites' hdlc_deframer actually accepts.")
