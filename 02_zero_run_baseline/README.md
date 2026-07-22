# 02 — Decision method for the zero-run segments: baseline + decisions, flagging isolated "1"s

## What this is about

`zero-run1`/`zero-run2` are two segments in the payload known to be all
`0x00` padding (payload byte `[75,158)` and `[206,272)`, fixed by protocol
and unaffected by telemetry). In theory these two segments should sit flat,
close to -A, but in practice a few isolated "decided as 1" symbols show up
inside them -- this is the hardest kind of bit to decide once the channel/ISI
has caused the signal to drift (after a long run of the same polarity, the
signal slowly drifts toward the midline, and the amplitude of a genuinely
isolated, opposite-polarity bit ends up compressed).

`02_zero_run_baseline.py` does two things:

1. **Detect frames + locate the zero-runs**: frame-start detection uses
   exactly the same method as
   [`01_frame_detection/`](../01_frame_detection/) (normalized
   cross-correlation to find header correlation peaks; see that folder's
   README "Algorithm" section for details). Once each frame's start is
   found, the same bit-level destuff mapping (output bit <-> original sample
   index) is used to compute that frame's exact zero-run1/zero-run2 sample
   ranges -- no frame's sample position is hardcoded.
2. **Compare two decision methods**, applied only to zero-run1/zero-run2
   (header/data/FCS are untouched).

## Algorithm (decision-method part)

### Notation

| Symbol | Meaning |
|---|---|
| $Y[m]$ | `restore_baseline`'s `y_comp_final` output (raw sample $m$) |
| $\mathrm{start}$ | this frame's start sample (detected by the same method as `01_frame_detection/`) |
| $\mathrm{SPS}$ | samples per symbol (=5) |
| $\mathrm{PHASE}$ | sampling phase (=2, the offset among each symbol's 5 samples closest to the symbol center, verified on the header to give the lowest error) |
| $\mathrm{idx}(n)$ | the raw sample index corresponding to the center of symbol $n$ within the zero-run segment |
| $y[n]$ | that symbol center's signal value, $y[n]=Y[\mathrm{idx}(n)]$ |
| $\hat b[n]$ | the decision result (0 or 1) |
| $d[n]$ | the difference between adjacent symbols, $d[n] = y[n]-y[n-1]$ |
| $\theta$ | Method 2's decision threshold |
| $p$ | percentile (50/70/80/90) |

### 0. Sampling: from waveform to $y[n]$

`restore_baseline` performs decision-directed baseline restoration on the raw
signal, producing $Y$. For symbol $n$ (0-based index within the segment)
inside a zero-run segment, its sampling point's sample index is:

$$
\mathrm{idx}(n) = \mathrm{start} + \mathrm{SPS}\cdot n + \mathrm{PHASE}
\qquad\Longrightarrow\qquad
y[n] = Y[\mathrm{idx}(n)]
$$

In other words, each symbol is represented by just the single sample point
"closest to its center", with no averaging or interpolation -- both decision
methods below decide based on this single sampled value $y[n]$.

**Method 1 (static threshold)**: same fixed threshold of 0 used in
`01_frame_detection/`:

$$
\hat b[n] = \begin{cases} 1, & y[n] > 0 \\ 0, & \text{otherwise} \end{cases}
$$

**Method 2 (sample-to-sample change)**: the idea is that zero-run segments
are mostly flat, so a genuinely isolated 1 might stand out more in
"change" than in "absolute value":

$$
\hat b[n] = \begin{cases} 1, & |d[n]| > \theta \\ 0, & \text{otherwise} \end{cases}
$$

$\theta$ has no prior value, so candidates are taken from the percentiles
$p\in\{50,70,80,90\}$ of the $|d[n]|$ distribution within the zero-run
segment (not picked arbitrarily), corresponding to roughly a
50%/30%/20%/10% decided-as-1 rate respectively.

## Scripts

| Script | Output |
|---|---|
| `02_zero_run_baseline.py` | Automatically detects every frame in the recording, saving one figure per frame: `Figure/02_frame1.png`, `Figure/02_frame2.png`, `Figure/02_frame3.png`... (numbered in order of sample position) |

## Key findings

**Method 1 (static threshold) wins clearly**: across all three frames'
zero-run segments, Method 1's decided-as-1 rate is only 0.8-2.5%; Method 2 is
9-53% at every threshold tested -- meaning that, for this signal,
**the absolute value is more trustworthy than the local rate of change**.
This looks counterintuitive at first (a local diff is usually more robust
against slow drift), but the reason is that the zero-run segments'
absolute-value fluctuation isn't actually that large, whereas the small
sample-to-sample noise that's always present between adjacent symbols gets
amplified by the diff into false positives. The conclusion is consistent
across all three frames -- it isn't a coincidence tied to a single packet.

The output figure overlays where each method decided a "1", and labels each
decided-as-1 symbol's global index and its relative position within the
segment, making it easy to check against the raw waveform by eye.

## How to run

```bash
pip install numpy matplotlib soundfile
python 02_zero_run_baseline.py
```

Needs `../scionx/` (`audio_io.py` / `baseline.py`) and `../_style.py`, which
live one directory up -- the script finds them automatically. Running it
saves one new PNG per detected frame into this folder's `Figure/` subfolder,
and prints each frame's detected zero-run ranges, both methods'
decided-as-1 rates at each threshold, and the exact positions where Method 1
decided "1", to the terminal.
