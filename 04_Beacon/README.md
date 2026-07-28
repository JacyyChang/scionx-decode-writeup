# 04 — Beacon field reference: decoding a frame's telemetry against the actual field layout

## What this is about

`01`/`02`/`03` all work at the signal level (samples, symbols, decided bits) and
never ask what those bits actually *mean*. This folder is different: it's the
beacon's actual telemetry field layout, supplied by the user, plus a script
that decodes one frame's Info field (payload byte `[16,272)`, 256 bytes /
2048 bits -- see `../REFERENCE_FRAME.md`) against it.

| File | Content |
|---|---|
| `SCIONX_TLMnew.xlsx` | Field layout: one row per telemetry field (`Subsystem`, `ItemName`, `DataType`, `BitLen`, `OffsetBit`, `Endian`, `LongDescription`), 205 fields, tightly packed, `OffsetBit` cumulative from 0 = the Info field's first bit |
| `SCIONX_enums.json` | Enum tables (`enums`), unit/scale conversions (`transforms`), and regex-based name-to-lookup rules (`nameRules`) for a subset of the fields |
| `04_beacon_field_decode.py` | Decodes one frame (default: frame#2) against the layout above; writes `Output/frame<N>_beacon_decode.xlsx` |
| `Output/` | Generated workbooks (not regenerated automatically -- re-run the script after changing `FRAME_INDEX`) |

## Decision rule (one consistent bitstream, reusing 02/03's own methods)

HDLC destuffing/frame structure is always derived from raw `y` at a plain
threshold=0 (the "bypass method" `03b`/`03c` use for clean header alignment).
That fixes which samples are data vs. stuffed bits and gives every surviving
bit its original sample index. From there, each segment's *reported bit
value* is independently re-decided:

- **Header (Dest/Src address, Control, PID) + data1/data2 + FCS**: re-decided
  using this frame's own header-calibrated fixed offset threshold theta*
  (same theta* `03b`/`03c` calibrate from the known 144-bit header). FCS rides
  along with the data segments per `../GNURADIO_MIGRATION.md`.
- **zero-run1/zero-run2** (should be all `0x00` padding): re-decided from the
  *baseline-restored* `yc` at a plain threshold=0 -- this is exactly
  `02_zero_run_baseline`'s Method 1. (Raw y was tried here first and gave
  ~42% 1s in the zero-runs; baseline restoration turned out to be
  load-bearing for this segment specifically, unlike data1/data2 where raw y
  gives the cleaner alignment -- see the script's docstring for the full
  reasoning.)

**"Possibly wrong" (red text in the `Bits` column)**:
- header/data/FCS bits: `|y - theta*| <= MARGIN_FRAC*A` (03b/03c's own
  "near decision line" criterion, `MARGIN_FRAC=0.10`).
- zero-run bits: decided as 1 (the padding should be all-zero, so any 1 is
  exactly 02's "isolated 1" -- **every** such bit is flagged here, not just
  the small set 02's own figures ring).

**Row background color** (segment the field's bits came from): blue=header,
peach=data1, pink=data2, gray/dark-gray=zero-run1/zero-run2, purple=FCS,
yellow=field straddles a segment boundary (2 of 205 fields do: `CMD Loss
Timer` crosses data1/zero-run1, `EPS UHF7V Current` crosses data2/zero-run2 --
byte-level decision is still correct per-bit, only the single-color row
shading breaks down there).

## Field lookup ("ReadableValue" / last column)

Numeric decode (`uint8_t`/`uint16_t`/`uint32_t`/`int*_t`/`float`, `Endian`
byte-order for multi-byte fields) is exact. The *meaning* lookup is
best-effort, since `SCIONX_TLMnew.xlsx`'s `ItemName` strings and
`SCIONX_enums.json`'s lookup keys were authored somewhat independently:

1. If the field's own `LongDescription` contains a parseable `key: label`
   table (handles `0:off/1:on`, comma-separated, and newline-separated hex-key
   forms -- see `parse_enum_from_longdesc`), that's used verbatim as the
   lookup column and to resolve the decoded value to a label.
2. Else, `SCIONX_enums.json`'s `nameRules` (regex against `ItemName` with
   spaces turned into underscores) or a direct name match against `enums`/
   `transforms` is tried.
3. Else, 1-bit fields default to an assumed `0=OFF/1=ON` (flagged as
   assumed, not verified).
4. Else the raw decoded value is shown with no lookup (`-`).

Out of 205 fields (frame#2 run): 52 resolved via their own `LongDescription`,
13 via `SCIONX_enums.json`, 23 via the assumed on/off default, 117 with no
lookup at all (still correctly decoded, just nothing to cross-reference
against).

## Key findings (frame#2)

- **The header decodes exactly right**: Dest Address = `'BN0CU '`
  (SSID byte=0x60), Src Address = `'BN0SCX'` (SSID byte=0xE1), Control=`0x03`,
  PID=`0xF0` -- all match the ground-test reference exactly (0/144 header bit
  errors), confirming nothing is missing between the header and the Info
  field's first bit (`APID`, OffsetBit=0).
- **Only 26/2192 bits across the whole frame are flagged low-confidence**
  (header=1/112, data1=11/488, zero-run1=5/664, data2=6/384,
  zero-run2=3/528, FCS=0/16) -- yet the transmitted FCS does not match the
  computed CRC-16/X.25 of the payload. Most of the frame is being decided
  *confidently*, just not all of it *correctly*: low decision-confidence
  alone doesn't explain the CRC failure.

## How to run

```bash
pip install numpy matplotlib soundfile openpyxl
python 04_beacon_field_decode.py
```

GNU-Radio-free. Needs `../scionx/` (`audio_io.py`, `baseline.py`, `hdlc.py`),
found automatically. `FRAME_INDEX` (top of the script, default `2`) selects
which of the 3 detected frames to decode -- change it and re-run to get
`Output/frame1_beacon_decode.xlsx` or `frame3_...`. If the previous run's
output file is still open in Excel, saving will fail with a permission
error; close it first.
