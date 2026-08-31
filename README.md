# SCIONX Signal Decoding Roadblock — a Writeup for the Community

## Background

This is a **GFSK/9600bps** audio recording (`.ogg`) of a CubeSat
(SCIONX/RANGE A) downlink, downloaded from the
[SatNOGS Network](https://network.satnogs.org/) (a crowd-sourced open
satellite ground station network), from which we need to decode
**HDLC/AX.25 frames**. The recording is known to contain 2-3 complete frames
(one of which has had its header verified against a ground-test reference
packet, see `REFERENCE_FRAME.md`), but the pipeline currently stalls at the
CRC check: **0 frames pass CRC**.

## Method

The overall processing is split into three steps:

1. **Frame detection**: locate the packet's position, then cut the whole
   signal into header/data/zero-run/FCS segments.
2. **Zero-run segments**: process the padding-region data and flag the
   isolated "1"s inside it.
3. **Data segments**: process the actual telemetry-content data, apply a
   threshold, and mark the decision line.

We've already run a fairly exhaustive round of step-by-step diagnostics,
ruling out several hypotheses (baseline curvature, timing/sampling phase,
AGC gain tracking, ...), but still haven't found a fix that actually gets a
frame past CRC. This is the most critical part of that diagnostic work,
split into three independent, re-runnable folders, in the hope that the
community can offer a different angle.

The fixed offset threshold currently being tried in
`03_data_segment_processing/` (calibrated from known header bits) only goes
as far as "draw a decision line for visual inspection" -- it hasn't actually
been verified to get a frame past CRC (the data segments' content changes
frame to frame, so there's no reliable ground truth to compute a bit error
rate against).

**Questions for the community**:
- What else could be done to improve the decision method for the data segments?
- Is there a better overall approach to this signal-processing pipeline?

## The three diagnostic folders

| Folder | Topic | Original naming |
|---|---|---|
| [`01_frame_detection/`](01_frame_detection/) | Automatically detects packet starts (normalized cross-correlation), then cuts the whole signal into header/data/zero-run/FCS segments; outputs one figure per frame in the recording | Phase 1e / 1e2 |
| [`02_zero_run_baseline/`](02_zero_run_baseline/) | After auto-detecting each frame, uses decision-directed baseline restoration + two decision methods to flag the isolated "1"s inside the zero-run segments; outputs one figure per frame | Phase 7 |
| [`03_data_segment_processing/`](03_data_segment_processing/) | After auto-detecting each frame, calibrates a fixed offset threshold from known header bits: shows the asymmetry itself (03a) + applies the decision line (03b); outputs one figure per frame | Phase 9 / 10 |

That folder also has a 4th script, `03c_symbol_sync_timing.py`, trying GNU
Radio's real `symbol_sync` (Gardner/M&M timing recovery) as a replacement for
the fixed-rate/fixed-phase sampling the other three folders all use -- see
"Current results" below. It's the one script in this repo that needs GNU
Radio; everything else stays dependency-light on purpose.

## Beacon field reference (`04_Beacon/`)

[`04_Beacon/`](04_Beacon/) is a different kind of folder: not a diagnostic
method, but a beacon telemetry field-layout reference (`SCIONX_TLMnew.xlsx` +
`SCIONX_enums.json`, both user-supplied) plus a script,
`04_beacon_field_decode.py`, that maps each detected frame's decoded Info
field (the 256-byte telemetry payload) onto that layout and writes a
per-frame, per-field, per-bit spreadsheet -- with the low-confidence bits
colored so it's visible at a glance which decoded values are trustworthy.
Frame detection works the same way as 01/02/03 and isn't hardcoded to
`cut_first3.ogg`, so it can be pointed at any other recording of this
satellite too -- but the detection threshold (`Z_THRESHOLD`) is: it was only
ever calibrated on `cut_first3.ogg` and doesn't carry over. A companion
script, `04a_zscore_visualization.py`, plots the detection z-score curve for
any recording so the right threshold can simply be read off the plot; both
scripts take a `--z-threshold` override, and the recommended workflow is to
plot with `04a` first, then decode with `04_beacon_field_decode.py`. See
[`04_Beacon/README.md`](04_Beacon/README.md) for details and example output.

## Investigating new recordings (`05_GNURadio_Czechia/`)

[`05_GNURadio_Czechia/`](05_GNURadio_Czechia/) applies this project's decode
approach to a separate batch of recordings from a different source (raw IQ
captures shared by a Czech colleague, not SatNOGS `.ogg` downloads), through
a clean, numbered three-step pipeline: **05a** decodes the raw IQ capture to
a `.wav` (needs GNU Radio -- the only step that does), **05b** cross-
correlates the whole recording for candidate frames and attempts an
automated CRC-16/X.25 decode grouped by 30-second segment and polarity, and
**05c** crops a segment and opens an interactive manual destuffing workbook
for whatever **05b** couldn't decode automatically. See
[`05_GNURadio_Czechia/README.md`](05_GNURadio_Czechia/README.md) for the
full pipeline and validated results.

This folder's own investigation history (GNU Radio Symbol Sync experiments,
header-correlation localization, a feedforward clock-tone timing-recovery
attempt) previously lived in `appendix_gnuradio/`, which has since been
superseded by the pipeline above and removed from this branch; it's
preserved in full on the `archive/appendix_gnuradio` branch.

## How to run

Only four packages are needed -- **GNU Radio is not required** (see `requirements.txt`):

```bash
pip install -r requirements.txt   # numpy, matplotlib, soundfile, openpyxl
cd 01_frame_detection    # or 02_.../ 03_.../ 04_Beacon/
python 01_frame_detection.py    # swap in whichever script you want to run
```

(The one exception is `03_data_segment_processing/03c_symbol_sync_timing.py`,
which needs a separate Python with GNU Radio installed -- see that folder's
README.)

Every script is independently runnable -- it reads `Data/cut_first3.ogg`
directly and recomputes everything itself, with no dependency on any other
script's intermediate output. Running one will save a new PNG (or, for
`04_Beacon/04_beacon_field_decode.py`, an `.xlsx`) in **whatever folder
you're currently in** (overwriting/adding a file with the same name) and
print the measured numbers to the terminal.

The shared code (the `scionx/` package, `_style.py`'s plotting setup) lives
in this folder's (`Share/`) root; scripts in all category folders find it
automatically, with no need to copy it separately.

## Other reference files

- [`REFERENCE_FRAME.md`](REFERENCE_FRAME.md): the ground-truth packet hex
  dump from the same satellite's ground test. The first 16 bytes
  (address+control+PID) and the CRC should be identical across any packet
  and can be used as a hard alignment/verification baseline; the Info field
  (telemetry content), however, **differs from packet to packet** and can't
  be used as ground truth for a data segment's bit errors -- this caveat is
  repeated in each category's own README as well.

## Current results

- **Frame segmentation/detection is quite successful**: `01_frame_detection/`'s
  normalized cross-correlation method lands exactly on the 3 known frame
  starts (z-score 12-13, far above the noise floor's z<5, no false
  positives), and after overlaying the plot, the header/data/zero-run/FCS
  boundary labels all line up with the raw waveform -- the segmentation step
  itself isn't the problem.
- **Zero-run segments: combined with the frame's internal structure, a human
  can filter out the misdetections**: `02_zero_run_baseline/` shows that
  using the current fixed threshold alone (Method 1), the zero-run segments'
  false-positive-as-1 rate is only 0.8-2.5%, and these misdetected points sit
  at discrete, sparse positions. Since these two segments should theoretically
  be all zero, using that known structure as a baseline, together with the
  handful of "decided as 1" candidate points, there's a real chance to check
  each one by hand and filter out the incorrect bits.
- **Data segment data is still quite messy**: `03_data_segment_processing/`
  shows that even after applying the header-calibrated fixed offset
  threshold, about 2-7% of the decisions within data1/data2 still change when
  the threshold changes, and the asymmetry's magnitude keeps drifting slowly
  with position within the segment (03a's "invisible curve"). The most
  workable direction right now seems to be: first identify the bits sitting
  "close to the decision line (threshold)" -- these are the most
  error-prone, most suspicious candidates -- then use an actually-decoded
  frame (e.g. the parts with known correct answers, like header/address/CRC)
  as a baseline to manually check and filter out the errors caused by these
  borderline cases.
- **Decoding all 3 frames' Info fields against the beacon's actual telemetry
  layout confirms the header is clean but the payload isn't**:
  `04_Beacon/04_beacon_field_decode.py` decodes each frame's 256-byte Info
  field against `SCIONX_TLMnew.xlsx`'s field layout. In all 3 frames, Dest/Src
  address + Control + PID decode to exactly the expected ground-test values
  (`'BN0CU '` / `'BN0SCX'` / `0x03` / `0xF0`) -- so the header is fully intact
  and nothing is missing in front of the Info field. But only 1.2-3.4% of the
  bits per frame (26-75 out of 2192, header+data+zero-run+FCS) are flagged as
  low-confidence, and none of the 3 frames' transmitted FCS matches the
  computed CRC-16/X.25 of its payload -- i.e. most of each frame is being
  decided *confidently*, just not all of it *correctly*. See
  [`04_Beacon/README.md`](04_Beacon/README.md).
- **A separate batch of recordings (`05_GNURadio_Czechia/`) decodes two real
  frames end to end, with zero hand-supplied coordinates.** Run against a
  full, un-cropped 771s/37M-sample recording, the pipeline's locate+decode
  step found and CRC-decoded two frames purely from waveform
  cross-correlation (header byte-identical to `REFERENCE_FRAME.md`, correct
  callsigns) -- no location or timing parameters supplied by hand. A third
  candidate with comparably strong header correlation still doesn't decode:
  an earlier automated phase/polarity search found a 41% raw bit-error rate
  for it, and this project's manual, per-bit interactive tool (calibrated
  per-frame threshold + phase micro-sweep) brought that down to 2.9% -- real
  progress, but still short of the near-zero bit-error rate AX.25's uncoded
  CRC needs. See
  [`05_GNURadio_Czechia/README.md`](05_GNURadio_Czechia/README.md#key-findings).
