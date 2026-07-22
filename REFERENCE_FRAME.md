# REFERENCE_FRAME — ground-truth frame from a ground test

The packet transmitted by the same satellite (SCIONX/RANGE A) during a
**ground test**, decoded via GNU Radio's `hdlc_deframer`. Provided by the
user on 2026-07-10, used as the **ground-truth reference** for decoding
`cut_first3.ogg`.

- **payload = 272 bytes** (the hdlc_deframer has already stripped the 2-byte
  FCS) -> full frame = 272 + 2 = **274 bytes**.
- This explains why our candidate payloads were 269/270 bytes -- 2-3 bytes
  short of the correct 272.

## Structure (AX.25 UI beacon)

| Field | bytes (offset) | Content |
|---|---|---|
| Dest address | 0x00-0x06 `84 9c 60 86 aa 40` + SSID `60` | each `>>1` = **"BN0CU "** |
| Src address  | 0x07-0x0D `84 9c 60 a6 86 b0` + SSID `e1` | each `>>1` = **"BN0SCX"** |
| Control   | 0x0E `03` | UI frame |
| PID       | 0x0F `f0` | no layer-3 |
| Info      | 0x10-0x10F (256 bytes) | telemetry, mostly `00` padding |

**Key anchor**: because this is the **same satellite**, the first 16 bytes
(address+control+PID)
`84 9c 60 86 aa 40 60 84 9c 60 a6 86 b0 e1 03 f0` should be **exactly
identical** in any packet, unaffected by changes in telemetry content -- so
it can be used as a hard alignment/scoring baseline.
The large blocks of `00` in the Info field (fixed buffer padding) are also
likely identical across packets; sparse regions like `08 26 / 20 00` are
"isolated 1s within a long 0-run" (the hardest bits to decide once the
channel/ISI has caused the signal to drift).

## Full hex dump (272 bytes)

```
0000: 84 9c 60 86 aa 40 60 84 9c 60 a6 86 b0 e1 03 f0
0010: 00 7c 08 3c 81 33 6a 00 02 01 00 00 00 00 00 20
0020: 00 00 00 2b 81 33 6a 00 00 00 00 00 00 03 02 00
0030: 00 44 09 00 20 10 00 00 c3 ad d0 00 99 bf 4e 04
0040: 8b c5 78 05 61 d7 f6 08 d4 a1 1f 00 00 00 00 00
0050: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0060: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0070: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0080: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0090: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 08 26
00a0: 08 26 08 26 28 26 20 26 20 00 20 00 20 00 20 00
00b0: 20 00 a0 3d 78 ff 77 0f 9b ff 8a 1f 16 15 15 e7
00c0: b0 b0 b0 b0 19 18 1a e8 0c 40 02 48 1b 18 00 00
00d0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00e0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00f0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0100: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

## Correction (2026-07-10, Step 1): no +2/+4 offset

An earlier note claiming "frame#2 needs a +4 bit offset to align the header"
turned out to be **an artifact of comparing against the post-destuff
payload**. Comparing at the raw slicer bits level (before destuffing) with a
correctly reconstructed reference (LSB-first + bit-stuffing), the header in
frame#2 **aligns precisely at offset=0**, with only 3 low-confidence bits
wrong. (See the internal project history for details -- not included in this
package.) **So any comparison must be done at the raw bits level, not
against the post-destuff payload.**

## Usage (further directions)

1. **Alignment comparison**: reconstruct the reference on-wire bits and align
   them at the **raw slicer bits** level (not the post-destuff payload), and
   check whether errors are "concentrated in long 0-runs (baseline)" or
   "scattered at high-transition, low-confidence bits (timing/threshold)".
   Already done: it's the latter.
2. **As a scoring oracle**: compared to a proxy like 7E7E, the bit-error
   count over "the first 16 bytes (a hard anchor) + the known `00` regions"
   can be used as the objective function for a parameter search, converging
   directly toward a working decode.
