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
| `04a_zscore_visualization.py` | Plots the frame-detection z-score curve for a whole recording against `Z_THRESHOLD`, so a new recording's noise ceiling can be checked *before* trusting detection on it. **Run this first on any recording other than `cut_first3.ogg`.** |
| `04_beacon_field_decode.py` | Decodes every frame detected in a recording (default: `../Data/cut_first3.ogg`, all 3 frames) against the layout above; writes one `Output/<audio stem>_frame<N>_beacon_decode.xlsx` per frame |
| `Output/` | Generated workbooks (not regenerated automatically -- re-run the script to refresh) |
| `Figure/` | Generated `04a_zscore_<audio stem>.png` plots |

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

## Key findings (all 3 frames in `cut_first3.ogg`)

- **The header decodes exactly right in all 3 frames**: Dest Address =
  `'BN0CU '` (SSID byte=0x60), Src Address = `'BN0SCX'` (SSID byte=0xE1),
  Control=`0x03`, PID=`0xF0` -- matching the ground-test reference, confirming
  nothing is missing between the header and the Info field's first bit
  (`APID`, OffsetBit=0).
- **Possibly-wrong (red) bits by segment, out of 2192 total**:

  | Frame | header | data1 | zero-run1 | data2 | zero-run2 | FCS | **total** |
  |---|---|---|---|---|---|---|---|
  | #1 | 3/112 | 25/488 | 6/664 | 13/384 | 14/528 | 2/16 | **63/2192 (2.9%)** |
  | #2 | 1/112 | 11/488 | 5/664 | 6/384 | 3/528 | 0/16 | **26/2192 (1.2%)** |
  | #3 | 1/112 | 33/488 | 10/664 | 21/384 | 8/528 | 2/16 | **75/2192 (3.4%)** |

  Frame#2 (the highest detection z-score of the three, and also the one with
  0/144 header errors) has the fewest low-confidence bits by a clear margin --
  consistent with it just being the cleanest reception of the three, not
  anything specific to this decode step.
- **None of the 3 frames pass CRC either way it's decided** (raw
  threshold=0 baseline, or the mixed theta*/yc decision this script uses) --
  and the low red-bit counts above mean most of each frame is being decided
  *confidently*, just not all of it *correctly*. Low decision-confidence
  alone doesn't explain the CRC failures.

## Workflow for a new recording: check the threshold before trusting detection

`Z_THRESHOLD=12.0` (both scripts) was calibrated on `cut_first3.ogg` alone,
where it happens to sit very comfortably above the noise: noise ceiling 4.25,
7.75 margin. That margin is **not** a general property of the detector --
run on the full-pass recording this clip was cut from
(`satnogs_14459039_2026-07-07T09-57-46.ogg`, 303s vs. 35.5s), the noise
ceiling comes out to **11.96**, just **0.04** below the same 12.0 threshold.
Same detector, same constant, wildly different safety margin -- because a
longer recording simply has more chances for a noise spike to land near the
template's correlation peak.

So for any recording that isn't `cut_first3.ogg`, check first, decode second:

```bash
pip install numpy matplotlib soundfile openpyxl

# 1. Check where the threshold sits for this specific recording
python 04a_zscore_visualization.py path/to/other.ogg
# -> read the printed noise-ceiling / margin numbers (and the plot) --
#    if the margin to the noise ceiling looks too thin, or too many/few
#    frames got flagged, retry with a different --z-threshold:
python 04a_zscore_visualization.py path/to/other.ogg --z-threshold 13.5

# 2. Decode using whichever threshold you settled on in step 1
python 04_beacon_field_decode.py path/to/other.ogg --z-threshold 13.5
```

## How to run

```bash
python 04_beacon_field_decode.py                       # every frame in ../Data/cut_first3.ogg
python 04_beacon_field_decode.py --frame 2              # just frame#2
python 04_beacon_field_decode.py path/to/other.ogg --z-threshold 13.5
python 04_beacon_field_decode.py path/to/other.ogg --frame 1 --z-threshold 13.5
```

GNU-Radio-free. Needs `../scionx/` (`audio_io.py`, `baseline.py`, `hdlc.py`),
found automatically. Frame numbering is purely by sample position within
whichever recording is given (earliest = frame#1), the same convention
01/02/03 use -- it isn't hardcoded to `cut_first3.ogg`'s 3 known frames.
Output filenames are prefixed with the audio file's name (e.g.
`cut_first3_frame2_beacon_decode.xlsx`) so runs against different recordings
don't overwrite each other. If a previous run's output file is still open in
Excel, saving over it will fail with a permission error; close it first.
