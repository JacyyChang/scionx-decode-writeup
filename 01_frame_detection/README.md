# 01 — Frame detection: auto-detect packet starts + segment the structure

## What this is about

The raw recording is one continuous stream of audio hiding a handful of
frames inside it. First we need to automatically find "where in the signal
is there a packet", then cut each packet into its flags (0x7E)/callsign
address/data/zero-run (padding)/FCS (CRC) segments.

`01_frame_detection.py` does two things:

1. **Detection**: builds an NRZ template from the known header + callsign
   address (4x flag + 14 bytes of address = 144 bits, fixed protocol content
   unaffected by telemetry), and runs a normalized cross-correlation against
   `restore_baseline`'s `y_comp_final` output, locating positions where the
   correlation coefficient is far above the noise floor -- these are the
   frame starts. It doesn't need to know how many frames there are ahead of
   time, and doesn't hardcode any sample position.
2. **Segmentation and labeling**: for each detected frame, does an HDLC
   destuff bit by bit and **records which original recording sample each
   output bit corresponds to**, then cross-references `../REFERENCE_FRAME.md`'s
   known field boundaries to precisely convert the whole structure back into
   sample indices, color-code it on the plot, and label each boundary with
   "this is payload byte N".

## Algorithm

### Notation

| Symbol | Meaning |
|---|---|
| $b[i]\in\{0,1\}$ | the header's raw bit sequence (bit $i$, $i=0,\dots,143$) |
| $s[i]\in\{-1,+1\}$ | the NRZ polarity converted from $b[i]$ |
| $\mathrm{SPS}$ | samples per symbol (=5) |
| $t[k]$ | the upsampled matching template, $k=0,\dots,L-1$ |
| $L$ | template length (samples), $L=144\times\mathrm{SPS}=720$ |
| $y[n]$ | `restore_baseline`'s `y_comp_final` output (sample $n$) |
| $y[n{:}n{+}L]$ | the signal window of length $L$ starting at position $n$ |
| $R[n]$ | the normalized cross-correlation coefficient at position $n$, range $[-1,1]$ |
| $\sigma_R$ | the standard deviation of $R$ over the whole signal (the noise-floor scale) |
| $z[n]$ | the z-score of $R[n]$ |
| $Z_{\text{th}}$ | the z-score detection threshold (default 12) |
| $\mathcal{C}$ | the set of candidate positions passing the threshold |
| $S$ | the final set of retained frame starts |
| $G_{\min}$ | the minimum sample gap between two retained starts (default 100000) |

### 1. Build the matching template

Convert the header's fixed content (4 0x7E flags + 14 bytes of callsign
address = 32+112 = 144 bits, fixed by protocol, unaffected by telemetry)
into NRZ polarity:

$$
s[i] = 2b[i] - 1 ,\qquad i = 0,\dots,143
$$

then upsample by repeating each symbol across $\mathrm{SPS}$ samples
(repetition instead of interpolation):

$$
t[k] = s\!\left(\left\lfloor \frac{k}{\mathrm{SPS}} \right\rfloor\right),
\qquad k = 0,\dots,L-1,\quad L = 144 \times \mathrm{SPS} = 720
$$

### 2. Normalized cross-correlation

Scan the signal $y$ point by point, computing the normalized cross-correlation
coefficient between the template $t$ and the signal window $y[n{:}n{+}L]$ at
each position $n$:

$$
R[n] = \frac{\displaystyle\sum_{k=0}^{L-1} t[k]\,y[n+k]}
             {\lVert t \rVert \, \lVert y[n{:}n{+}L] \rVert},
\qquad
\lVert t \rVert = \sqrt{\sum_{k=0}^{L-1} t[k]^2},\quad
\lVert y[n{:}n{+}L] \rVert = \sqrt{\sum_{k=0}^{L-1} y[n+k]^2}
$$

$R[n]$ ranges roughly over $[-1,1]$: the more the window resembles the
template's waveform, the closer $R[n]$ gets to 1. In the implementation, the
numerator (a dot product) is computed for all $n$ in one call to
`np.correlate(y, t, mode="valid")`; the denominator's window norm uses the
prefix sum of $y^2$, $\mathrm{csum}[m]=\sum_{j<m}y[j]^2$, to compute the
sliding window energy
$\lVert y[n{:}n{+}L]\rVert^2 = \mathrm{csum}[n{+}L]-\mathrm{csum}[n]$ --
neither needs a per-point loop.

### 3. Filter correlation peaks by z-score

$$
z[n] = \frac{R[n]}{\sigma_R}, \qquad \sigma_R = \mathrm{std}(R)
$$

Real frame starts stand out clearly in $z$ (measured at $z\approx12\sim13$
on `cut_first3.ogg`), while the noise floor mostly stays at $z<5$ -- the two
are well separated. Take the positions passing the threshold as the
candidate set:

$$
\mathcal{C} = \{\, n \mid z[n] > Z_{\text{th}} \,\}
$$

### 4. Deduplicate, sort, and number

Sort $\mathcal{C}$ by $z[n]$ descending, and greedily add candidates in that
order into the start set $S$ (initially empty):

$$
n \in \mathcal{C}\ (\text{sorted by } z[n] \text{ descending}):\quad
n \to S \iff \min_{s \in S} |n - s| > G_{\min}
$$

$G_{\min}$ is much smaller than the actual frame spacing (about 561000
samples), which avoids a single peak's neighboring samples being counted as
several separate candidates. Finally, sort $S$ by sample position ascending
and number them frame#1, frame#2, frame#3, ...

## Scripts

| Script | Output |
|---|---|
| `01_frame_detection.py` | Automatically detects every frame in the recording, saving one figure per frame: `Figure/01_frame1.png`, `Figure/01_frame2.png`, `Figure/01_frame3.png`... (numbered in order of sample position) |
| `01a_waveform_power_overview.py` | Fast first look at a NEW recording, before running the real detector above: raw waveform + block-wise RMS power envelope over time, to eyeball where candidate bursts are. Does no baseline restoration or frame detection, so it stays fast even on multi-minute files. `Figure/01a_overview_<audio stem>[_<start>-<end>s].png` |
| `01b_weak_signal_candidate_scan.py` | Two-stage candidate scan for frames too weak for `01_frame_detection.py`'s coherent correlation to catch: stage 1 finds windows whose mean power sits well below the local median (a matched filter sized to one frame's duration, not a naive per-block threshold -- see below); stage 2 scores each survivor's zero-crossing-interval structure against a same-length noise control window. Prints a ranked candidate table to the console and saves `Figure/01b_candidate<rank>_<audio stem>.png` for the top few |

Verified on `Data/cut_first3.ogg` (known to contain 3 frames): detection
lands exactly on the three known starts 235719 / 796789 / 1357824, with
z-scores around 12-13, far above the noise floor (z<5), and no false
positives.

### `01a`: for long recordings, narrow the range with `--start`/`--end`

```bash
python 01a_waveform_power_overview.py path/to/long.ogg                    # whole file, prints its duration
python 01a_waveform_power_overview.py path/to/long.ogg --start 30 --end 90   # just 30-90s
```

`--start`/`--end` are seconds, passed straight through to
`scionx.audio_io.read_audio`'s `start_sec`/`end_sec` (soundfile seeks and
decodes only that range, rather than reading the whole file and slicing
after -- narrowing the range actually skips decode work, not just plot
work). The waveform panel is also min/max-decimated to `--max-points`
columns (default 6000) regardless of range length, so even a whole-file plot
stays fast; short bursts survive the decimation because both the min and the
max of each column are kept, not just one sample per bucket.

**Caveat found on a real long recording**
(`satnogs_14674078_2026-08-03T07-50-10.ogg`, 606s): AFSK is a constant-
envelope modulation, so the RMS power panel can come back essentially FLAT
for the entire file, whether or not real packets are present -- amplitude
alone doesn't distinguish "signal" from "just noise/idle carrier" the way it
would for an OOK/on-off signal. A brief dip (not a raised plateau) usually
means a receiver dropout, not a packet. Treat this script as an orientation
tool (how long is the file, are there any obvious anomalies), not a
substitute for `01_frame_detection.py`'s correlation-based detector, which
looks at the header's actual bit structure rather than raw amplitude.

### `01b`: catching frames too weak for the coherent correlation detector

`01_frame_detection.py`'s 144-symbol correlation needs every symbol in phase
to accumulate gain; at low SNR that gain collapses to the noise floor even
when cropped tightly around a real packet (verified on
`satnogs_14674078_2026-08-03T07-50-10.ogg`: max z=5.1 across the whole 606s
recording, indistinguishable from "no frame here"). `01b` looks for
independent, cheaper evidence instead of coherent phase alignment:

```bash
python 01b_weak_signal_candidate_scan.py path/to/long.ogg
python 01b_weak_signal_candidate_scan.py path/to/long.ogg --dip-ratio 0.6 --top 15
```

Stage 1 computes block-wise RMS over the whole recording (no baseline
restoration -- the fast, coarse pass) and slides a window sized to one
frame's duration (`--window`, default matches `cut_first3.ogg`'s own
0.400s), looking for windows whose MEAN power sits below `--dip-ratio` times
the LOCAL median (chunked, not global, to tolerate slow AGC/elevation
drift). This has to be a windowed mean, not "every block in a run must
individually be below threshold": AFSK's own tone-driven envelope wobbles in
and out of any fixed ratio block to block, even inside a real frame -- a
naive per-block threshold finds ZERO candidates in `cut_first3.ogg`'s 3
known-good frames, which is why this is a matched filter instead (verified
to recover all 3, within 11ms of their true starts, before trusting it on
new data). Stage 2 crops tightly around each stage-1 survivor, runs real
baseline restoration on just that small window, and checks whether
zero-crossing intervals cluster at multiples of SPS the way real AFSK
symbols do, scored relative to a same-length noise window immediately
before the candidate (self-calibrating per recording, since an absolute
fraction threshold doesn't transfer across different SNRs). Nothing here
decodes a frame -- it only narrows down where to point
`01_frame_detection.py`'s own correlation search (optionally at a lower
`--z-threshold`) or `04_Beacon/`'s tools next.

## Key findings

- **Frame detection is quite accurate**: the 3 starts caught by the z-score
  threshold match the known 235719/796789/1357824 exactly, with no misses or
  false positives.
- **Visually, the structural segmentation (header/data/zero-run/FCS
  boundaries) is also quite accurate** -- the overlaid color coding lines up
  with the raw waveform. But this only means "the position was cut
  correctly", not "the bit content that was cut out is correct": the bit
  error rate of the data segments (real telemetry content) is still high
  (15-43%), which is the problem `03_data_segment_processing/` is meant to
  address.
- **`01b`'s two-stage scan found a plausible weak candidate that the coherent
  detector completely misses**: on `satnogs_14674078_2026-08-03T07-50-10.ogg`
  (606s, z-score detector finds nothing anywhere, max z=5.1), `01b` at
  `--dip-ratio` 0.45 through 0.75 consistently returns exactly ONE
  candidate, at t=183.52s, depth-ratio 0.45 -- squarely inside the
  known-good range (0.44-0.52) measured on `cut_first3.ogg`'s 3 real frames.
  Not yet confirmed by CRC or a successful decode, but a strong enough lead
  to be worth pointing the rest of the pipeline at.

## How to run

```bash
pip install numpy matplotlib soundfile
python 01_frame_detection.py
```

Needs `../scionx/` (`audio_io.py` / `baseline.py` / `hdlc.py`) and
`../_style.py`, which live one directory up -- the script finds them
automatically. Running it saves one new PNG per detected frame into this
folder's `Figure/` subfolder, and prints the detected frame list (sample
start + z-score) plus a "payload byte index -> sample index" lookup table
for each frame to the terminal.
