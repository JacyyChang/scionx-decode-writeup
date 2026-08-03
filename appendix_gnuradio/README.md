# Appendix: GNU Radio tests -- checking our assumptions against a real implementation

## What this is about

**This folder is for GNU Radio testing, not signal diagnostics.** `01`/`02`/`03`
analyse the recording; `04` decodes telemetry fields out of it. This folder does
something different: it takes an assumption the rest of the repo depends on,
builds a signal whose correct answer is already known, and runs it through a
**real, independent GNU Radio implementation** to see whether the assumption
holds.

⚠️ Unlike everything else in this repo, **GNU Radio is required here** — that's
the point. Run it with a Python that has `gnuradio` + `gr-satellites`
(confirmed working with `radioconda`, GNU Radio 3.10.12 / gr-satellites 5.7.0).

| File | Content |
|---|---|
| `hdlc_bitorder_test.grc` | Flowgraph: two Vector Sources (the same known frame, serialized two different ways) -> two HDLC Deframers -> two Message Debugs |
| `ax25_wire.py` | Standalone, GNU-Radio-free version of the frame builder used by the flowgraph. Run it on its own to see what the two bit orders look like. |

## Test 1: which bit order does AX.25 actually use on the wire?

### The question

`scionx/hdlc.py`'s `bits_to_bytes` reassembles bytes from the destuffed
bitstream **LSB-first**, while `04_Beacon/04_beacon_field_decode.py`'s
`decode_raw_value` reads multi-bit telemetry fields out of that same stream
**MSB-first**. Both cannot be right, and it matters: for a byte that isn't a
bit-palindrome the two readings differ, e.g.

```
byte 0x7C = 01111100      (normal notation, MSB on the left)
  LSB-first on the wire :  00111110      <- 0x7C
  MSB-first on the wire :  01111100      <- read back as 0x3E
```

Rather than settle this by re-reading the AX.25 spec and trusting our own
interpretation of it, this test lets an independent implementation decide.

### The flowgraph

```
Vector Source (known frame, LSB-first)  ->  HDLC Deframer (check FCS)  ->  Message Debug
Vector Source (same frame,  MSB-first)  ->  HDLC Deframer (check FCS)  ->  Message Debug
```

Both branches are fed the **same 272-byte payload** — the ground-test reference
frame from `../REFERENCE_FRAME.md`, the decode oracle the whole repo scores
against — wrapped in flags, bit-stuffed, with a correctly computed CRC-16/X.25
FCS. The only difference between the two branches is the byte-serialization
order.

The HDLC flag `0x7E = 01111110` is itself a bit-palindrome, so it serializes
identically both ways and the deframer finds frame boundaries in *both*
streams. Only the frame body differs — so the deframer's **FCS check** is what
actually discriminates the two orders. That makes this a clean pass/fail test:
whichever branch emits a PDU is the correct on-wire order.

### Result

```
LSB-first branch : 1 PDU, 272 bytes, starting 84 9c 60 86 aa 40 60 84 ...
                   -> byte-for-byte identical to REFERENCE_FRAME.md
MSB-first branch : nothing at all (FCS fails, no frame emitted)
```

**LSB-first is the on-wire order.** `bits_to_bytes` is right; reading beacon
fields MSB-first out of that stream returns each byte bit-reversed.

## How to run

Open the flowgraph in GNU Radio Companion and press Run:

```bash
cd appendix_gnuradio
gnuradio-companion hdlc_bitorder_test.grc
```

or generate and run it headlessly:

```bash
cd appendix_gnuradio
grcc -o . hdlc_bitorder_test.grc      # writes hdlc_bitorder_test.py
python hdlc_bitorder_test.py
```

(`grcc`'s generated `.py` files are build artifacts and are gitignored.)

The frame builder is embedded in the `.grc` as a Python Module block, so the
flowgraph is self-contained and needs no `sys.path` setup. `ax25_wire.py` holds
the same code as a normal, fully commented script — run it on its own for a
GNU-Radio-free summary of what the two streams look like:

```bash
python ax25_wire.py
```
