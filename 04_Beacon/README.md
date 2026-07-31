# 04 — Beacon field reference: decoding a frame's telemetry against the actual field layout

> 中文版：[`README.zh-TW.md`](README.zh-TW.md)

## What this is about

`01`/`02`/`03` all work at the signal level (samples, symbols, decided bits)
and never ask what those bits actually *mean*. This folder is different:
it's the beacon's actual telemetry field layout, supplied by the user, plus
two scripts that check frame detection against it and decode a frame's Info
field (payload byte `[16,272)`, 256 bytes / 2048 bits -- see
`../REFERENCE_FRAME.md`) against that layout.

| File | Content |
|---|---|
| `SCIONX_TLMnew.xlsx` | Field layout: one row per telemetry field (`Subsystem`, `ItemName`, `DataType`, `BitLen`, `OffsetBit`, `Endian`, `LongDescription`), 205 fields, tightly packed, `OffsetBit` cumulative from 0 = the Info field's first bit |
| `SCIONX_enums.json` | Enum tables (`enums`), unit/scale conversions (`transforms`), and regex-based name-to-lookup rules (`nameRules`) for a subset of the fields |
| `Output/` | Generated workbooks (not regenerated automatically -- re-run the relevant script to refresh) |
| `Figure/` | Generated `04a_zscore_<audio stem>.png` plots |

## Algorithm

### Notation

| Symbol | Meaning |
|---|---|
| $Y[m]$ | the raw signal (sample $m$), as in `03`'s notation |
| $y_c[m]$ | `restore_baseline`'s `y_comp_final` output (sample $m$) |
| $\mathrm{start}$ | this frame's start sample (detected the same way as `01_frame_detection/`) |
| $\mathrm{SPS},\mathrm{PHASE}$ | samples per symbol (=5), sampling phase (=2) |
| $Z_{\text{th}}$ | frame-detection z-score threshold (default 12, see `01`'s notation -- **not** a fixed property of the detector, see "Workflow" below) |
| $\theta^*$ | this frame's header-calibrated fixed offset threshold (same $\theta^*$ as `03`) |
| $A$ | amplitude estimate, $A=\mathrm{median}(\lvert Y\rvert)$ |
| $m$ | "near decision line" half-band, $m=\text{MARGIN\_FRAC}\cdot A$, $\text{MARGIN\_FRAC}=0.10$ |
| $\mathrm{OffsetBit},\mathrm{BitLen}$ | a beacon field's bit position/width within the Info field, from `SCIONX_TLMnew.xlsx` |

### 1. Frame detection (same method as `01`, but the threshold isn't portable)

Frame starts are found exactly as in `01_frame_detection/`'s Algorithm
section (normalized cross-correlation of $y_c$ against the known
header+flags template, filtered by $z[n] > Z_{\text{th}}$). The difference
here: $Z_{\text{th}}=12.0$ was only ever calibrated on `cut_first3.ogg`, and
`04a_zscore_visualization.py` exists specifically to check whether that
value still makes sense on a *different* recording before trusting its
frame count -- see "Workflow for a new recording" below.

### 2. HDLC destuff (bypass method) for structure only

Exactly like `03`'s "bypass method": destuff bit-by-bit directly from raw
$Y$ at a plain threshold of 0, recording each surviving bit's original
sample index. This fixes which samples are data vs. stuffed bits and gives
the byte alignment used by every later step -- it does **not** decide the
bit's final reported value (that's step 3).

### 3. Per-segment bit decision (one consistent bitstream, reusing 02/03's own methods)

Each segment's *reported* bit value is independently re-decided from the
structure in step 2:

- **Header (Dest/Src address, Control, PID) + data1/data2 + FCS**:
  re-decided at $y[n] > \theta^*$, the same calibrated threshold `03b`/`03c`
  use. FCS rides along with the data segments per
  `../GNURADIO_MIGRATION.md`.
- **zero-run1/zero-run2** (should be all `0x00` padding): re-decided from
  $y_c[n] > 0$ -- this *is* `02_zero_run_baseline`'s Method 1, just
  evaluated here on the same frame's mapped sample indices. (Raw $Y$ was
  tried here first and gave ~42% 1s in the zero-runs -- baseline
  restoration turned out to be load-bearing for this segment specifically,
  unlike data1/data2 where raw $Y$ gives the cleaner alignment. See the
  script's docstring for the full reasoning.)

### 4. Flag low-confidence bits ("possibly wrong" / red text in the `Bits` column)

$$
\text{header/data/FCS bit flagged} \iff \lvert y[n]-\theta^*\rvert \le m
$$

(the same "near decision line" criterion `03b`/`03c` use), while

$$
\text{zero-run bit flagged} \iff \hat b[n] = 1
$$

(the padding should be all-zero, so any 1 is exactly `02`'s "isolated 1" --
**every** such bit is flagged here, not just the small set `02`'s own
figures ring).

### 5. Map bits onto beacon fields

The Info field's 2048 decided bits are sliced at
$[\mathrm{OffsetBit}, \mathrm{OffsetBit}+\mathrm{BitLen})$ per
`SCIONX_TLMnew.xlsx` row, MSB-first (tightly packed, no gaps). Multi-byte
fields ($\mathrm{BitLen}>8$) are byte-swapped first when `Endian=LE`. Two of
the 205 fields straddle a segment boundary (`CMD Loss Timer` crosses
data1/zero-run1, `EPS UHF7V Current` crosses data2/zero-run2) -- the
per-bit decision from step 3 is still correct there, only the output's
single-color row shading (segment origin: blue=header, peach=data1,
pink=data2, gray/dark-gray=zero-run1/zero-run2, purple=FCS, yellow=mixed)
can't represent a two-segment field and falls back to yellow.

### 6. Resolve each field's meaning (`ReadableValue` / last column)

The numeric decode itself is exact; `ReadableValue` is then looked up by
matching the field against `SCIONX_enums.json` and the field's own
`LongDescription` when present (see `resolve_lookup` in the script for the
exact matching order). Coverage is partial -- many fields have no match and
fall back to the raw decoded number with no lookup. Treat this column as a
**reference aid, not an authoritative decode**.

## Frame-alignment self-check (independent of CRC)

Steps 2 and 5 both assume the destuffed stream is cut **exactly** at the frame
boundary -- that the 5-consecutive-1s rule consumed neither too many nor too
few bits on the way through. If it didn't, every byte boundary after the slip
point is shifted, and the field table silently reads the wrong bits. CRC can't
tell you *where* that happened (it just fails), so the decoder runs a separate
structural check:

**The anchor**: a valid HDLC frame is immediately followed by the closing flag.
So payload bit $274\times8 = 2192$ of the destuffed stream must be the start of
a `0x7E` run. The check looks for a run of at least 3 back-to-back flags (an
isolated `01111110` can occur by chance inside the payload; flags spaced
*exactly* 8 bits apart cannot), and reports

$$\text{slip} = (\text{flag run start}) - 2192$$

which is exactly the net number of bits the destuffing got wrong.

**Locating the slip**: the zero-run padding is all `0x00`, and a stuffed 0 can
only ever follow five consecutive 1s -- which cannot occur inside all-zero
padding. So any removal landing inside a zero-run is *provably* spurious (a bit
error faked a 5-ones run), and the first one is the first detectable slip. A
slip inside a data segment can't be localized this way, since there's no known
content there to check against; that case is reported as "not localizable"
rather than guessed at.

The result appears in three places: a banner at the top of the sheet, a red
`<<< BYTE ALIGNMENT SLIPS AT OR BEFORE THIS FIELD >>>` marker on the first
affected field row, and the `align_*` keys in the `Frame_Info` sheet.

## Scripts

| Script | Output |
|---|---|
| `04a_zscore_visualization.py` | Plots the frame-detection z-score curve for a whole recording against $Z_{\text{th}}$: `Figure/04a_zscore_<audio stem>.png` |
| `04_beacon_field_decode.py` | Decodes every frame detected in a recording (default: `../Data/cut_first3.ogg`, all 3 frames) against `SCIONX_TLMnew.xlsx`; writes one workbook per frame: `Output/<audio stem>_frame<N>_beacon_decode.xlsx` |

Both scripts share the same frame-detection code (step 1 above) and take a
recording path as their first positional argument, so neither is hardcoded
to `cut_first3.ogg`.

## Workflow for a new recording: look at the plot, then pick a threshold

$Z_{\text{th}}=12.0$ was calibrated on `cut_first3.ogg` alone and **does not
carry over to other recordings**. On the full-length SatNOGS pass this clip
was cut from (`satnogs_14459039_2026-07-07T09-57-46.ogg`, 303s vs. 35.5s),
that same 12.0 lands in the middle of the real frame population and misses
several frames that a lower threshold picks up cleanly.

So for any recording that isn't `cut_first3.ogg`, plot first, decode second:

```bash
pip install numpy matplotlib soundfile openpyxl

# 1. Plot this recording's z-score curve and look at Figure/04a_zscore_<stem>.png
python 04a_zscore_visualization.py path/to/other.ogg

# 2. Pick a threshold that cleanly separates the peaks from the background,
#    and re-plot to confirm it catches what you expect
python 04a_zscore_visualization.py path/to/other.ogg --z-threshold 10.5

# 3. Decode with the threshold you settled on
python 04_beacon_field_decode.py path/to/other.ogg --z-threshold 10.5
```

The plot is the thing to trust here: real frame peaks are tall, narrow and
visually obvious against the background, so where to put the line is easier
to see than to compute. A useful cross-check once frames are detected: this
satellite beacons on a fixed cadence (~561035 samples between frames in
`cut_first3.ogg`), so a correct threshold tends to yield starts spaced at
integer multiples of that.

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
- **None of the 3 frames are byte-aligned all the way to the end** -- the
  closing flag never lands on payload bit 2192:

  | Frame | stuffed 0s removed | closing flag at | slip | first detectable slip |
  |---|---|---|---|---|
  | #1 | 13 | bit 2196 | **+4 bits** | payload byte 144 |
  | #2 | 11 | bit 2189 | **−3 bits** | payload byte 144 |
  | #3 | 8 | bit 2209 | **+17 bits** | payload byte 111 |

  (the reference frame needs exactly **8** stuffed 0s, so frames #1/#2 are
  removing extra bits that aren't really stuffing). The trailing flags are
  clearly there -- they come in runs spaced exactly 8 bits apart -- just not
  where a correctly destuffed 274-byte frame would put them. Bit errors in the
  data segments fake five-consecutive-1s runs, the destuffer drops a 0 that
  was never stuffed, and the byte grid shifts from that point on.

  **This matters more than the per-bit confidence numbers above**: it means the
  field table is trustworthy only up to the first slip, and where that is
  differs frame to frame. It also plausibly explains the CRC failures on its
  own -- a frame whose byte boundaries drift mid-way cannot produce a matching
  FCS no matter how confidently the individual symbols were sliced.

## Tips for judging content correctness by eye

Beyond the automated red-flagging (step 4 above), a row's background color
tells you which segment its bits came from (step 5 above) -- useful to have
on hand while applying the three rules of thumb below:

- blue = header (Dest/Src address, Control, PID)
- peach = data1
- pink = data2
- gray = zero-run1
- dark gray = zero-run2
- purple = FCS
- yellow = field straddles a segment boundary (2 of 205 fields)

0. **Check the alignment banner at the top of the sheet first.** If it says
   ALIGNMENT FAILED, only the rows *above* the red slip marker sit on correct
   byte boundaries -- everything below it is reading shifted bits, and no amount
   of per-bit scrutiny will fix that.
1. **data1/data2 are where the review effort belongs.** This is the segment
   type with the least reliable signal (`03`'s "invisible curve" asymmetry
   drift, 2-7% of decisions flipping with the threshold) -- header and
   zero-run are comparatively well-behaved, so scrutiny is best concentrated
   on data1/data2.
2. **Don't bother re-checking the header.** Dest/Src address, Control, PID
   are fixed protocol content, identical in every packet from this satellite
   (see `../REFERENCE_FRAME.md`). If it ever decodes wrong, that's a
   frame-alignment problem elsewhere in the pipeline, not something to chase
   by eyeballing this particular header.
3. **In zero-run1/zero-run2, judge an isolated "1" by its neighbors, not on
   its own.** A lone "1" with no other "1"s nearby is almost always a
   decision error -- the padding there really is all-zero. But a "1" that
   sits among several other nearby "1"s is more likely to be a genuinely
   non-zero part of the signal, not noise (consistent with
   `02_zero_run_baseline`'s own finding that true padding decides very
   cleanly -- an isolated positive is the anomaly, a cluster of them isn't).

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
