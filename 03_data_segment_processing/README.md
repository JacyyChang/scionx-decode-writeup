# 03 — Processing the data segments: header-calibrated threshold + two ways of presenting it

## What this is about

`data1`/`data2` are the actual telemetry content in the payload (it changes
frame to frame, with no known correct answer). The `header` + callsign
address (144 bits, fixed protocol content, the only segment with a known
correct answer) is used to measure the bit=1/bit=0 signal distribution
asymmetry: bit=1's mean sits close to +A, while bit=0's mean is noticeably
off from -A. This folder applies the fixed offset threshold calibrated from
the header to data1/data2, presented via two scripts:

| Script | Content | Output |
|---|---|---|
| `03a_data_asymmetry_check.py` | Shows the asymmetry itself: per-point color coding + each group's local moving average, to see whether the asymmetry drifts slowly with position | `Figure/03a_frame1.png`, `03a_frame2.png`, `03a_frame3.png` |
| `03b_data_fixed_offset_threshold.py` | Applies the calibrated fixed offset threshold to data1/data2, overlaying the decision line, and rings every symbol sitting close to that line -- below **or** above it | `Figure/03b_frame1.png`, `03b_frame2.png`, `03b_frame3.png` |

Both scripts' frame detection uses the same method as
[`01_frame_detection/`](../01_frame_detection/) (normalized
cross-correlation to find header correlation peaks), outputting one figure
per detected frame (numbered in order of sample position).

★ **Bypass method**: when locating the data1/data2 sample ranges and when
computing the threshold, both scripts sample the **raw y** directly at a
fixed rate and destuff it, without going through `restore_baseline` -- header
alignment was verified to be cleanest this way. `restore_baseline` is only
used in the frame-start-detection step (it needs `y_comp_final` to get clean
correlation peaks); the actual threshold calibration and data-segment
analysis both use raw y only.

## Algorithm

### Notation

| Symbol | Meaning |
|---|---|
| $Y[m]$ | the raw signal (sample $m$, the value read in by `audio_io.read_audio`, without baseline compensation) |
| $\mathrm{start}$ | this frame's start sample (detected by the same method as `01_frame_detection/`) |
| $\mathrm{SPS},\mathrm{PHASE}$ | same as `01_frame_detection/`: samples per symbol (=5), sampling phase (=2) |
| $t[i]\in\{0,1\}$ | the header+address's known true value for bit $i$, $i=0,\dots,143$ |
| $v_1, v_0$ | the sets of symbol-center signal values in the header whose true value is 1 / 0 |
| $\theta$ | a candidate threshold |
| $\theta^*$ | the calibrated, best fixed offset threshold |
| $y[n]$ | symbol $n$'s center signal value within data1/data2 |
| $\hat b[n]$ | the data segment's decision result (0 or 1; ★ this is a threshold decision, not ground truth) |
| $A$ | amplitude estimate, $A=\mathrm{median}(\lvert Y\rvert)$ (over the whole recording) |
| $m$ | "near the decision line" half-band, $m = \text{MARGIN\_FRAC}\cdot A$ (default $\text{MARGIN\_FRAC}=0.10$, i.e. $\pm 10\%A$) |

### 1. Frame detection

Exactly the same normalized cross-correlation method as
`01_frame_detection/` -- see that folder's README "Algorithm" section for
details, not repeated here.

### 2. Calibrate the fixed offset threshold from the header

Take the symbol-center values of the header+address's 144 known bits, and
split them into two groups by their true value:

$$
v_1 = \{\, Y[\mathrm{start}+\mathrm{SPS}\cdot i+\mathrm{PHASE}] \mid t[i]=1 \,\},
\qquad
v_0 = \{\, Y[\mathrm{start}+\mathrm{SPS}\cdot i+\mathrm{PHASE}] \mid t[i]=0 \,\}
$$

Scan candidate thresholds $\theta$ over $[\min(v_0\cup v_1),\ \max(v_0\cup
v_1)]$, and take the one with the fewest errors as the best fixed offset
threshold:

$$
\theta^* = \arg\min_{\theta}\ \Big(\ \bigl|\{x\in v_1 \mid x \le \theta\}\bigr|\ +\ \bigl|\{x\in v_0 \mid x > \theta\}\bigr|\ \Big)
$$

This threshold is re-calibrated from this frame's **own** header, not
borrowed from a value computed for another frame.

### 3. Locate data1/data2 (bypass method)

After skipping the leading 4 flags, sample the raw $Y$ at a fixed rate
($\mathrm{SPS}$ and $\mathrm{PHASE}$ unchanged), slice it into 0/1 at a
threshold of $0$, and destuff it (HDLC), recording each output bit's
original sample index along the way. Then cross-reference the reference
packet's (`../REFERENCE_FRAME.md`) known field boundaries (data1 = payload
byte `[14,75)`, data2 = payload byte `[158,206)`) and convert them back into
sample ranges, giving this frame's own exact `data1`/`data2` sample ranges.

### 4a. Show the asymmetry (`03a`)

Use $\theta^*$ to split each symbol in data1/data2 into "decided as 1" /
"decided as 0" groups (★ this is a threshold decision, not ground truth):

$$
\hat b[n] = \begin{cases} 1, & y[n] > \theta^* \\ 0, & \text{otherwise} \end{cases}
$$

Two views are drawn: (1) a per-point scatter plot color-coded by
$\hat b[n]$; (2) each group's local moving average (the window is counted
in "points within the group"), to see whether the two groups' means drift
slowly with position within the segment.

### 4b. Apply the decision line, and flag the low-confidence symbols (`03b`)

Draw the same $\theta^*$ directly as a decision line over the raw waveform,
keeping the old threshold's (=0) line for reference. Then ring every symbol
whose distance to $\theta^*$ falls inside a symmetric half-band, **on either
side**:

$$
\bigl|\,y[n]-\theta^*\,\bigr| \le m
$$

This is a different (and broader) set than "symbols whose decision changed
between $\theta=0$ and $\theta=\theta^*$": that comparison only catches
symbols sitting in the strip between the two threshold values, so it misses
low-confidence symbols that land just *above* $\theta^*$ -- those never
disagreed with the old threshold, but they're just as close to the current
decision line. Ringing $\lvert y[n]-\theta^*\rvert \le m$ instead catches
both sides symmetrically, matching the "Possible future updates" idea in the
main README of identifying the bits closest to the decision line as the most
error-prone candidates. No bit error rate is computed -- the data segments'
content changes frame to frame, so there's no reliable ground truth to
compare against.

## Key findings

- **The asymmetry reproduces across all three frames**, though the
  calibrated threshold differs (frame#1 +0.151, frame#2 +0.200, frame#3
  +0.075) -- the asymmetry's existence is stable, but its exact magnitude
  varies by frame; it isn't a single global constant.
- **`03a`'s local moving average shows the asymmetry isn't a constant
  offset, but drifts slowly with position within the segment** (the
  "invisible curve"): the decided-as-0 group's average drifts closer to 0
  around the middle of the segment, then back away near both ends, while the
  decided-as-1 group stays relatively stable. This means a single fixed
  offset threshold can only correct the mean -- it can't fully remove this
  position-dependent component.
- **`03b` shows the old-vs-new threshold decision difference is small but
  nonzero**: across the three frames, roughly 2-7% of data1/data2's symbols
  have their decision changed by switching thresholds; the new threshold
  pushes the "decided as 0" group's mean further from -A (see 03a's output),
  but there's no CRC or other independent verification confirming whether
  this actually improves accuracy.
- **The symmetric near-decision-line band (±10%A around $\theta^*$) flags a
  similar-sized but not identical group**: roughly 1.6-6.7% of data1/data2's
  symbols per segment, across the three frames -- including some that sit
  just *above* $\theta^*$ and therefore never showed up in the old-vs-new
  comparison at all. These are the natural starting point for the "check
  against an actually-decoded frame" direction in the main README, since
  they're low-confidence independent of which exact threshold is correct.

## How to run

```bash
pip install numpy matplotlib soundfile
python 03a_data_asymmetry_check.py
python 03b_data_fixed_offset_threshold.py
```

Needs `../scionx/` (`audio_io.py` / `baseline.py`) and `../_style.py`, which
live one directory up -- the scripts find them automatically. Running them
saves one new PNG per detected frame into this folder's `Figure/` subfolder,
and prints each frame's calibrated threshold, header error count, and
data1/data2 decided-as-1/decided-as-0 statistics to the terminal.
