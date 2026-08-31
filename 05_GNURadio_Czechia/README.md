# 05 — GNU Radio Czechia pipeline: cs16 IQ capture → wav → locate/decode → manual destuff

## What this is about

The recordings processed here (`Data/*.cs16`, raw 100 kHz complex-short IQ)
were shared by a Czech colleague — a separate source from `cut_first3.ogg`
and the earlier `appendix_gnuradio/` diagnostic session's own SatNOGS
captures, but the same satellite/protocol (SCION-X / RANGE-A, GFSK 9600,
AX.25/HDLC — see the repo root `README.md`).

`appendix_gnuradio/` is where the diagnostic *investigation* into these new
recordings happened (a running log: signal-strength survey, Symbol Sync
experiments, header-correlation localization, `Recovery_try/`'s clock-tone
timing-recovery attempt). This folder is the *result* of that investigation
turned into a clean, reusable, numbered pipeline — the same house style as
`01_frame_detection/` → `04_Beacon/`, applied to raw IQ instead of an
already-demodulated `.ogg`.

Only **Step 1 (05a)** needs GNU Radio (a radioconda install with PyQt5).
Steps 2–3 are pure Python (`numpy` + `soundfile`, the same minimal
dependency set as the rest of the repo) plus MATLAB for Step 2's
localization half — no GNU Radio needed once you have a `.wav`.

## Layout

```
05a_IQtoOgg_plot.grc / .py   Step 1: cs16 IQ -> 48k wav + periodic spectrogram PNGs
s05b_header_locate_decode.m  Step 2 (MATLAB half): locate every candidate header,
                              group by (30s "seg", polarity), call the Python half
05b_decode_at_grid.py        Step 2 (Python half): CRC-16/X.25 decode at a given
                              (sps, phase), optionally windowed to one candidate
05c_destuff_interactive.py   Step 3: crop a seg (optional) + interactive manual
                              destuff workbook, for whatever Step 2 didn't CRC-pass
grc_blocks/                  gr_plot_capture.py, gr_plot_sink.py (Step 1's plotting
                              library) + plot_capture.block.yml (the GRC block def)
gr_plot_capture.py           2-line shim so 05a's plain `import gr_plot_capture`
                              works regardless of GRC's own block-definition caching
                              (see Gotchas)
Data/   *.cs16 raw IQ captures (Step 1 input)
Figure/ periodic spectrogram PNGs (Step 1 output, one per 30s block)
Output/ wav / xlsx / fig / png (everything downstream of Step 1)
```

## Pipeline

### Step 1 (05a): IQ → wav + spectrograms

Edit `cs16_path` at the top of `05a_IQtoOgg_plot.grc` (or the variable of
the same name in `05a_IQtoOgg_plot.py` if running the `.py` directly) to
point at the recording, then run it:

```bash
C:\Users\USER\radioconda\python.exe -u 05a_IQtoOgg_plot.py
```

This is a **real, GUI, real-time-paced** flowgraph (a `Throttle` block holds
it to the recording's own sample rate) — processing a 771 s capture takes
~13 minutes of wall-clock time, and does **not** auto-close: close the
window yourself (or the wav's RIFF header won't be finalized correctly —
see Gotchas). Output: `Output/<rec_stem>_48k.wav` (float32, matches the
source's own dynamic range — GFSK-demodulated audio here reaches ±60–80,
nowhere near normalized ±1) and `Figure/<rec_stem>_seg###_spec_*.png`, one
per 30 s block.

### Step 2 (05b): locate + decode, against the WHOLE recording

Run against `05a`'s full, un-cropped output — no need to guess a segment
from the spectrograms first:

```matlab
WAV = 'Output/<rec_stem>_48k.wav';   % edit at the top of s05b_header_locate_decode.m
run('s05b_header_locate_decode.m')
```

This cross-correlates the known 720-sample header template (4 flags + the
14-byte Dest+Src address — constant for every frame from this satellite)
against the whole waveform, groups the resulting candidates by
`(30s-block "seg", polarity)`, and calls `05b_decode_at_grid.py` once per
group — windowed to ~22000 samples around that group's own anchor, which is
what makes "which seg does this decoded frame belong to" answerable at all
(the underlying HDLC deframer has no concept of bitstream position; a
windowed search is what pins a result to one candidate). Prints and plots a
per-seg PASS/FAIL summary.

### Step 3 (05c): crop (optional) + manual destuff, for whatever didn't pass

For a seg Step 2 flagged (found, but no CRC pass):

```bash
python 05c_destuff_interactive.py Output/<rec_stem>_48k.wav --seg 10
```

Crops that 30 s window (writing it to `Output/`, same naming Step 2's own
seg labels use) and immediately opens it in the same interactive, per-bit
destuff workbook `04_Beacon/04d_destuff_interactive_ver2.py` established —
`LowConfidence`/`CandidateStuffPoint` hints, `ExcludeThisBit`/`FlipThisBit`
dropdowns, a live Excel-formula CRC-16/X.25 check. Add `--crop-only` to just
get the wav (for `eye_fixed_grid.m`, `header_correlate_locate.m`, or
anything else that wants a small per-segment file, not this tool).

## Key findings

- **The pipeline decodes real frames end to end, with zero hand-supplied
  coordinates.** Run against a full 771 s / 37M-sample recording, Step 2
  found and CRC-decoded two frames (matching `REFERENCE_FRAME.md`'s header
  byte-for-byte, correct callsigns `BN0CU`/`BN0SCX`) purely from waveform
  cross-correlation — no prior knowledge of where they were. Total runtime:
  ~66 s (6 s correlation + 5 windowed decode calls at ~12 s each), against
  ~230 s for the earlier per-polarity, whole-file-scan design this replaced.
- **A wrong-polarity correlation sidelobe reliably fails to decode.** Two of
  the five candidate groups found in that same run were the opposite
  polarity of a real frame's own sidelobe (same seg, weaker z) — both
  correctly produced `NO_CRC_PASS`, evidence the grouping+decode step
  discriminates real signal from spurious correlation, not just echoing
  whatever the strongest candidate was.
- **Step 3's manual tool substantially improves on Step 2's blind search for
  the one candidate that still doesn't decode (seg010).** `Recovery_try/`'s
  earlier automated phase/polarity/slicer scan found a 41% header
  bit-error rate for this candidate. `05c_destuff_interactive.py`'s
  per-frame-calibrated threshold (fit from this exact candidate's own
  header samples, not an assumed sign threshold) plus a PHASE micro-sweep
  brought that down to **2.9% mismatch at the 1320 verifiable (header +
  zero-run) bit positions** — real progress, but still short of the
  near-zero raw bit-error rate AX.25's un-coded CRC needs to pass. Read as
  refining, not overturning, the earlier SNR-limited conclusion.

## Gotchas

- **GNU Radio Companion only re-scans `local_blocks_path` at startup, and a
  restart did not reliably pick up an edit either in practice.** `05a`'s
  Plot Capture block therefore does NOT rely on its own `imports` template
  pointing at `grc_blocks/` (tried that, fragile) — instead a 2-line shim
  file, `gr_plot_capture.py`, sits next to the flowgraph and redirects
  `import gr_plot_capture` into `grc_blocks/gr_plot_capture.py` regardless
  of which cached block definition GRC used to generate the `.py`. If
  `gr_plot_capture.py`/`gr_plot_sink.py` ever move again, update the shim,
  not `plot_capture.block.yml`.
- **`05a_IQtoOgg_plot.grc`'s own `id` can't carry the `05a` prefix** (it
  becomes the generated `.py`'s class name — and Python identifiers can't
  start with a digit), so GRC always writes `IQtoOgg_plot.py`; rename it to
  `05a_IQtoOgg_plot.py` by hand after regenerating. The same constraint is
  why `s05b_header_locate_decode.m` carries a leading `s` instead of a bare
  digit — a MATLAB script whose filename isn't a valid identifier cannot be
  run at all, confirmed empirically (`run('05z_test.m')` errors trying to
  parse `05z_test` as an expression).
- **`soundfile.write`'s default subtype for `.wav` is 16-bit PCM, which
  silently CLIPS to [-1, 1]** — these recordings carry real, unnormalized
  amplitudes (peaks past ±70), so a default write destroys the signal
  outright. Always pass `subtype="FLOAT"` (see `05c_destuff_interactive.py`'s
  `crop_segment()`), matching the source `05a` wav's own format.
- **Don't plot tens of millions of raw points directly.** An early version
  of Step 2's diagnostic plot embedded the full waveform + z-score trace at
  native resolution — a 995 MB `.fig` that was also visually useless (a
  solid block at that zoom level, no peaks distinguishable). Fixed with
  min/max envelope decimation (same idiom as
  `01_frame_detection/01a_waveform_power_overview.py`): ~8000 buckets,
  keep each bucket's min AND max so real peaks always survive, down to a
  466 KB file with the same information content.
- **`05a` cannot be safely automated to a clean stop from outside.** Its
  `Throttle` block paces it to the recording's real duration and it does
  not auto-quit on end-of-file (only a window close or SIGINT does, via the
  GRC-generated `sig_handler`) — and on Windows, `os.kill(pid, SIGTERM)`
  actually calls `TerminateProcess` (a hard kill), which bypasses that
  handler entirely and can leave the wav's RIFF header un-finalized. Run it
  interactively; don't script a kill-after-timeout.
