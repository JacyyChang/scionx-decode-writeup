# Appendix: GNU Radio tests -- checking our assumptions against a real implementation

## What this is about

**This folder started as GNU Radio testing, not signal diagnostics** — `01`/`02`/`03`
analyse the recording; `04` decodes telemetry fields out of it. Originally this
folder did something different: it took an assumption the rest of the repo
depends on, built a signal whose correct answer is already known, and ran it
through a **real, independent GNU Radio implementation** to see whether the
assumption holds (the `hdlc_bitorder_test.grc` test below). It has since grown
a second use: decoding *new*, not-yet-processed `.cs16` IQ captures end to end
(`IQtoOgg_plot.grc` onward) — see "New recordings: signal-strength survey"
further down.

⚠️ Unlike everything else in this repo, **GNU Radio is required here** — that's
the point. Run it with a Python that has `gnuradio` + `gr-satellites`
(confirmed working with `radioconda`, GNU Radio 3.10.12 / gr-satellites 5.7.0).

| File | Content |
|---|---|
| `hdlc_bitorder_test.grc` | Flowgraph: two Vector Sources (the same known frame, serialized two different ways) -> two HDLC Deframers -> two Message Debugs |
| `ax25_wire.py` | Standalone, GNU-Radio-free version of the frame builder used by the flowgraph. Run it on its own to see what the two bit orders look like. |
| `gr_plot_sink.py` | Renders matplotlib PNGs straight out of a flowgraph — replaces the "File Sink -> `.bin` -> separate plotting script" round trip. See below. |
| `gr_plot_capture.py` | `plot_capture`, a hier block bundling Head+Vector Sink+`gr_plot_sink` behind one input port — drag one block instead of wiring two. |
| `grc_blocks/plot_capture.block.yml` | GRC block definition for `plot_capture`, so it shows up in the GRC block tree like a built-in block. |
| `IQtoOgg_plot.grc` | The cs16 -> resampled/demodulated-audio decode chain, with two `plot_capture` taps and a two-line Python Snippet that fires them on exit. Worked example. |
| `view_segment.m` | MATLAB: waveform + block-wise RMS power (dB) for one or more `plot_capture` segments, linked-axis zoom. Real frames show up as a power *dip*, not a rise. Candidate/confirmed segment list built into the script -- edit and re-run. |
| `run_02_03_on_segment.py` | Reuses `02_zero_run_baseline.py`/`03a`/`03b`'s per-frame plotting functions against a forced frame start, for a recording whose z-score never clears those scripts' hardcoded detection threshold. See "New recordings" below. |
| `Wav_TimingSync.grc` | Alternate decode path: GNU Radio's own `Symbol Sync` (Mueller & Muller TED) + `HDLC Deframer` (FCS-checked), instead of this repo's fixed-SPS/PHASE `scionx` pipeline. **Confirmed decode of seg017 as of 2026-08-25** — see below. |
| `symbol_sync_sweep.py` | Reusable CLI tool: grid-searches `symbol_sync_ff`'s timing-recovery parameters against any wav file, offline-scored (graded, not pass/fail) — the tool that decoded seg017 below. `python symbol_sync_sweep.py Output/some_segment.wav`, then `--verify` to confirm the winner against the real `hdlc_deframer`. |
| `plot_pickpoints_comparison.py` | Plots where a candidate frame's symbol decisions land on the waveform, vs `01`/`03`'s fixed `SPS`-grid. `--whole` for the whole frame. ⚠️ its drift curve is misleading — see the docstring and the retraction below. |
| `plot_pickpoints_seg017.m` | ⚠️ **SUPERSEDED by `eye_fixed_grid.m`** — kept for its zoomable pick-point comparison view, but its "Symbol Sync avg grid" and drift figure rest on the retracted `avg_sps`-as-clock-offset reading (see below). Don't cite its numbers. |
| `tune_decision_points.m` | ⚠️ **SUPERSEDED by `eye_fixed_grid.m`** — its "margin" metric averages `\|y-threshold\|` over every symbol including ~1880 zero-padding ones, which drowns out the real signal and reported "barely open" for what is actually a wide-open eye. Kept only for the phase-sweep UI shape; do not trust its margin numbers. |
| `eye_fixed_grid.m` | MATLAB, zoomable: fixed-grid (no Symbol Sync) eye diagram, using the corrected **eye-opening** metric (weakest '1' − strongest '0'), plus a decision-sample histogram and the waveform with the header span shaded (`HEADER_START`/`HEADER_LEN`). The tool that established `sps=5.0` is correct and that seg010/seg007 have real but unopened eyes. |
| `power_dip_locate.m` | MATLAB: coarse packet localization via RMS-power matched filter (same principle as `01a`/`01b`). Validated to ~1.5-1.9% of frame width on seg017/seg002 after a half-frame-width correction — good enough to point a search, not good enough to decide a decode. |
| `header_correlate_locate.m` | MATLAB: fine packet localization via normalized cross-correlation against a 4-flags+14-byte-address template (ported from `01_frame_detection.py`). Precise to a few samples; caught seg010's power-dip location being off by more than a full frame width. |
| `MATLAB_ANALYSIS.zh-TW.md` | Walkthrough of the three current MATLAB scripts above — what each block of code computes, the formulas behind it, and a glossary of the timing-recovery terminology. Chinese, like `PLOT_CAPTURE.zh-TW.md`. |

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

## Plotting from inside a flowgraph (`gr_plot_sink.py` / `gr_plot_capture.py`)

The usual loop for looking at a signal mid-flowgraph is: add a File Sink, run,
then open a second Python script that re-derives the dtype and sample rate and
draws the figure. This pair of files collapses that into the flowgraph itself —
one run, a timestamped PNG on disk, no reader script.

### The recommended wiring: one `Plot Capture` block per observation point

```
... -> Plot Capture
```

One block, one wire. Set `type` (complex/float/int/short/byte — must match the
wire it's tapping), `nsamples`, and `mode` in its properties dialog, then add
**one line per tap** in a **Python Snippet** block (*Section = `Main - After
Stop`*):

```python
# inside a Snippet the flowgraph object is bound to `self`, not `tb`
self.plotcap_iq.plot()
self.plotcap_af.plot()
```

`IQtoOgg_plot.grc` is exactly this, applied to the cs16 -> audio decode
chain: a `plotcap_iq` (`type=complex`, `mode=rf`) tapped off the Throttle, and a
`plotcap_af` (`type=float`, `mode=time,psd`) tapped off the Quadrature Demod.
`plot()` renders using whatever the block's own properties say; pass overrides
from the snippet for a one-off look without touching the dialog, e.g.
`self.plotcap_iq.plot(mode='iq')`.

The snippet still fires from `closeEvent()`, so in a QT GUI run you just close
the window and the PNGs are waiting in `Figure/`. This is the one part
`plot_capture` cannot absorb into the block itself — GRC only calls Python at
block-instantiation and stream time, never "after the flowgraph stops, per
block instance", so something still has to make that call. The snippet is now
one line per tap instead of hand-wiring Head + Vector Sink + a `render()` call.

### Periodic capture: one PNG every N seconds until the signal runs out

For "keep saving snapshots as the recording plays, don't wait for the end",
use `start_periodic_plot(interval_sec)` / `stop_periodic_plot()` instead of a
single `plot()` call, wired from **two** Snippets:

```python
# Section = Main - After Start
self.plotcap_iq.start_periodic_plot(30)

# Section = Main - After Stop -- flushes a partial last segment if you close
# the window early; start_periodic_plot's own stall-detection already fires
# this automatically once the source runs dry on its own.
self.plotcap_iq.stop_periodic_plot(flush_partial=True)
```

This is a `PyQt5.QtCore.QTimer` polling the block's Vector Sink on the Qt main
thread — the same mechanism the QT GUI sinks already use, not a streaming
block, so it never goes near the crash path described below. Every time
`interval_sec` worth of *new* samples has arrived it renders one PNG
(`prefix_seg000.png`, `prefix_seg001.png`, ...); once the capture goes 5 polls
in a row without growing (the source has run out — e.g. a non-repeating File
Source hitting EOF), it stops itself and flushes whatever partial segment is
left, so "until the signal finishes" needs no extra wiring. `IQtoOgg_plot.grc`
is wired this way against `plotcap_iq` with `interval_sec=30`.

**`nsamples` (the "Capture Samples" property) and `interval_sec` don't need to
match — they're independent controls**, confirmed by direct test: segment
length in periodic mode comes from `interval_sec` alone, because
`start_periodic_plot` reads the Vector Sink's raw, still-growing data directly
and never goes through the `nsamples`-truncated `data()`/`plot()` path.
`nsamples` only still matters if something *also* calls the one-shot `.plot()`
on the same block (it would show just the first `nsamples`, regardless of how
far the periodic capture has actually progressed), and as the Vector Sink's
preallocation hint either way (harmless if it doesn't match how long the
capture actually runs).

**One-time setup**: `plot_capture` is a *local block* (like an out-of-tree
module, but scoped to this folder) — it needs
`grc_blocks/` on GRC's block search path:

```bash
python -c "
from gnuradio import gr
p = gr.prefs()
p.set_string('grc', 'local_blocks_path', r'D:\path\to\appendix_gnuradio\grc_blocks')
p.save()
"
```

This writes to `~/.gnuradio/config.conf` (on Windows, that's
`C:\Users\<you>\.gnuradio\config.conf` — **not** the `.gnuradio` folder some
tools put under `AppData\Roaming`; `gr.prefs()` follows `%USERPROFILE%`/`$HOME`
directly and ignores that one). Do it via `gr.prefs()` rather than editing the
file by hand — this is the value GNU Radio itself resolves `~` to, and it's
also the officially supported way to point both `gnuradio-companion` and
`grcc` at the same custom-block folder. Restart GRC afterward. Once set, `Plot
Capture` shows up in the GRC block tree like any built-in block.

### The lower-level path (`gr_plot_sink.plot_vector_sink`), if you'd rather not touch prefs

Skip the custom block and wire a plain Vector Sink yourself:

```
... -> Vector Sink
```

```python
import sys
sys.path.insert(0, r'D:\path\to\appendix_gnuradio')
import gr_plot_sink

gr_plot_sink.plot_vector_sink(
    self.blocks_vector_sink_iq, samp_rate=self.samp_rate,
    mode='rf', out_dir='Figure', prefix='iq',
    skip=0)   # truncate to your capture length yourself, e.g. data[:n]
```

Same renderer underneath (`plot_capture.plot()` just calls this for you), only
difference is one block and one `sys.path` line instead of one block and a
prefs edit. **Do not add a `Head` block in front of the Vector Sink** — see the
⚠️ warning below for why.

### Panels

`mode` takes a comma-separated list, or one of the aliases
`all` (= `time,psd,spec`), `rf` (= `psd,spec`), `af` (= `time,psd,eye`):

| Panel | Shows |
|---|---|
| `time` | waveform vs. time; I and Q separately for a complex input. Min/max decimated above ~8000 points, so a 4 s capture at 100 kHz still draws in a second and keeps every excursion |
| `psd` | averaged periodogram (Hann, 50 % overlap), pure numpy |
| `spec` | spectrogram; color scale clipped to the 5th/99.8th percentile, because on a full auto-scale the noise floor eats the whole colormap and a real carrier goes invisible |
| `hist` | amplitude (or magnitude) histogram |
| `iq` | I-vs-Q 2-D histogram, complex input only |
| `eye` | eye diagram — needs `sps` |

Other arguments: `skip` (drop leading samples past the filter settling
transient), `nfft`, `title`, and `save_npy=True` to also dump the capture as
`.npy`. That last one is the real File Sink replacement: `.npy` carries its own
dtype and shape, so a later re-plot with `gr_plot_sink.plot_file(...)` never has
to guess either.

### ⚠️ Why `plot_capture` is a hier block, and a Snippet call is still required

`gr_plot_sink.py` also contains `blk`, a proper sink block for use as an
Embedded Python Block — but **streaming Python blocks segfault on this
project's radioconda install** (GNU Radio 3.10.12 / Python 3.12 / numpy 2.2,
verified 2026-08). Any `gr.sync_block` with a stream port kills the C++ gateway
thread with an access violation the instant the flowgraph starts; a stock
three-line pass-through block crashes identically, so it is the environment and
not this file. Message-only Python blocks (`in_sig=None, out_sig=None`, like
`hdlc_check_epy_block_0_0.py`) are unaffected, which is why this had never
surfaced here.

`gr_plot_capture.py`'s `plot_capture` sidesteps this entirely: it's a
`gr.hier_block2`, not a `gr.sync_block` — it has no `work()` of its own, it's
just a container wiring together an ordinary C++ block (`blocks.vector_sink_*`),
so it never goes through the Python gateway callback that crashes. Confirmed
safe by direct test.

The trade-off: a hier block's Python methods are never called by the
scheduler, so there's still no lifecycle hook equivalent to
`gr.sync_block.stop()` to trigger the render automatically. That's the one
piece neither `blk` nor `plot_capture` can absorb — something external still
has to call `.plot()` after the flowgraph stops, and a GRC Snippet
(`main_after_stop`) is the only mechanism GRC exposes for that. `plot_capture`
shrinks it to one line per tap instead of hand-wiring the capture and the
`render()` call.

### ⚠️ Why there's no `Head` block bounding the capture, despite `nsamples`

The first version of `plot_capture` used `Head(nsamples) -> Vector Sink`
internally, on the reasoning that a Vector Sink alone grows without limit.
That design **deadlocks the whole flowgraph** once Head hits its limit, any
time the tap point is shared with another still-running consumer (e.g. a QT
GUI sink fed by the same upstream block) — confirmed by direct reproduction
2026-08: symptom is every display sharing that tap freezing at exactly
`nsamples / samp_rate` seconds and never recovering (e.g. `n_capture=400000`
@ 100 kHz froze everything at 4.0 s).

Why: once `blocks.head` reaches its limit it stops calling `work()`, which
means it permanently stops draining its read pointer on the shared ring
buffer feeding it. GNU Radio's buffer can't reclaim space still claimed by an
unread pointer — even a permanently-stalled one — so the buffer fills and the
upstream writer blocks trying to produce more, freezing every other reader of
that same output.

`plot_capture` now wires the input straight to a bare Vector Sink and enforces
`nsamples` by **truncating in Python** (`data()` returns only the first
`nsamples` items). The sink keeps draining — and growing — for as long as the
flowgraph runs; if you leave a capture running far longer than you intended,
close the window sooner rather than relying on `nsamples` to cap RAM. For this
project's actual use (finite recordings, watched for seconds to a couple of
minutes) that trade is the right one: unbounded-but-controllable memory growth
beats a hard freeze of every other display in the flowgraph.

## New recordings: signal-strength survey (2026-08-24/25)

The `.cs16` files under `Data/` are 100 kHz-sample-rate SDR captures of
SCION-X passes, provided by a colleague in the Czech Republic — longer
(600-800 s) than the shared `cut_first3.ogg`, and going through a raw
GNU Radio chain (`IQtoOgg_plot.grc`) with **no Doppler/AFC correction**,
unlike whatever SatNOGS-style ground-station chain produced `cut_first3.ogg`.

**Workflow**: `IQtoOgg_plot.grc`'s periodic `plot_capture` (30 s/segment)
scans a whole recording into `Figure/<stem>_seg###_spec_*.png` spectrograms;
candidates are picked by eye, then confirmed with `view_segment.m` (MATLAB —
waveform + block-wise RMS power in dB, same method as
`01a_waveform_power_overview.py`). A real GFSK frame shows up as a power
**dip**, not a rise (FM quieting suppresses receiver noise under a real
carrier) — look for the RMS panel dropping several dB below its own noise
floor, not for a peak.

**Segments found so far** (30 s each; dip depth = the segment's noise-floor
median minus its minimum, both in dB RMS):

| Recording | Segment | Time range | Dip depth | Status (updated 2026-08-27, see sections below) |
|---|---|---|---|---|
| `20260720_220206_..._98266_x.cs16` | seg007 | 210-240s | ~12.2 dB | Location confirmed (header corr. z=8.1) — **not decoded, SNR-limited** |
| `20260723_091639_..._98266_x.cs16` | seg002 | 60-90s   | ~13.1 dB | **Decoded** — fixed grid, no Symbol Sync needed |
| `20260723_091639_..._98266_x.cs16` | seg010 | 300-330s | ~16.1 dB | Location confirmed (header corr. z=13.7, stronger than the decoded frames) — **not decoded, SNR-limited** |
| `20260723_091639_..._98266_x.cs16` | seg017 | 510-540s | ~13.7 dB | **Decoded** — fixed grid, no Symbol Sync needed |
| `20260810_220940_..._69910_x.cs16` | seg004 | 120-150s | ~12.3 dB | Candidate — not yet run through any of the tools below |
| `20260810_220940_..._69910_x.cs16` | seg018 | 540-570s | ~9.4 dB  | Candidate — not yet run through any of the tools below |

Note on "Status" here vs. earlier revisions of this table: this column originally
tracked only "does a real power dip exist" (all six do). It now tracks the
much stronger "has this been decoded" question, which is why seg007/seg010
read as less resolved than their dip depth alone would suggest — a real,
independently-confirmed packet location is not the same as a successful
decode, see "Locating packets precisely" below for exactly that distinction.

All six dips are comparable to or deeper than `cut_first3.ogg`'s known-good
frames (~6-7 dB) — **consistent with real signal, not noise, across all three
recordings.** Each segment is also exported standalone to
`Output/<stem>_seg###_<start>-<end>s.wav` for feeding straight into a decoder.

**seg007 is confirmed, but not fully decoded yet.**
`04_Beacon/04a_zscore_visualization.py`'s fixed-template correlator (tuned on
`cut_first3.ogg`) finds nothing on any of these recordings, even down to
z=5.5 — most likely because this raw SDR chain has no Doppler/clock
correction. Forcing `04d_destuff_interactive_ver2.py --z-threshold 5.5` on
seg007 anyway found a frame with only 30/1320 mismatches at verifiable
(header/padding) bit positions — far better than chance, so almost certainly
a real frame, but **the CRC/FCS check has not been confirmed passing yet.**
`run_02_03_on_segment.py`, reusing `02`/`03a`/`03b`'s plotting functions
against that same forced frame start, produces consistent-looking results
(zero-run regions decide ~0% as 1-bits; header bit=1/bit=0 amplitude
separation looks clean) — more evidence of a real, roughly-aligned frame, not
yet a full decode.

**Update (2026-08-27)**: seg017 and seg002 are now confirmed decodes (see
below) — and neither needed `Wav_TimingSync.grc`'s Symbol Sync path in the
end; a plain fixed-rate grid decodes both. seg007's frame (paragraph above)
and seg010 both have an independently-confirmed packet location now (via
header cross-correlation, stronger signal evidence than the original power
dip alone) but still do not decode — see "Locating packets precisely:
power dip (coarse) + header correlation (fine)" further down for why that is
a different, weaker claim than "decoded."

## seg017 decoded via `Wav_TimingSync.grc`'s Symbol Sync path (2026-08-25)

`Wav_TimingSync.grc` is a proper-timing-recovery decode path — GNU Radio's
`Symbol Sync` block (Mueller & Muller TED, MMSE 8-tap resampler) feeding
`satellites_hdlc_deframer` (FCS-checked) directly, instead of this repo's
fixed-SPS/PHASE `scionx` pipeline reading the timing straight off the sample
clock — aimed at exactly the kind of residual frequency/clock offset
suspected above (`IQtoOgg_plot.grc` has no Doppler/AFC correction).

**`20260723_091639_..._98266_x.cs16` seg017 (510-540s), previously CANDIDATE
ONLY, is now a confirmed decode**: a full AX.25 frame, FCS-checked, its
16-byte header byte-for-byte identical to `REFERENCE_FRAME.md`'s constant
address+control+PID (`84 9c 60 86 aa 40 60 84 9c 60 a6 86 b0 e1 03 f0`) — same
satellite, different pass, so the telemetry payload itself differs from
`REFERENCE_FRAME.md`, as expected.

### What was changed: `digital_symbol_sync_xx_0.damping` 1.0 → 0.7

Everything else in `Wav_TimingSync.grc` is unchanged from its original
values: TED = Mueller & Muller, `loop_bw=0.045`, `max_dev=1.5`, `sps=5`
(= 48000/9600, matching `IQtoOgg_plot.grc`'s resampler output).

⚠️ **`damping=0.7` is NOT "the correct value"** — that framing (which an
earlier version of this section used) was wrong, and a follow-up sweep
disproved it. See "How wide is the working window" below: `damping` 0.4,
0.5, 0.85 and 1.0 all decode this same frame at other `loop_bw` values. The
original settings simply happened to land on a failing point and
`damping=0.7` on a passing one. Do not carry 0.7 to another recording
expecting it to be meaningful.

### How this was found: an offline-scored parameter sweep, not trial and error

The sweep itself is now `symbol_sync_sweep.py` in this folder — reusable
against any other wav file, not a one-off:

```bash
python symbol_sync_sweep.py Output/some_other_segment.wav
python symbol_sync_sweep.py Output/some_other_segment.wav --verify   # after a hit
```

Running the real `satellites_hdlc_deframer` block once per candidate
parameter set only gives a binary pass/fail — on a weak, likely-borderline
signal that gives no sense of "getting warmer" across ~100 combinations to
search. Instead, two stages:

1. **Stage 1 (search)**: a headless (no GUI, no custom Python streaming
   block — see the segfault warning above) `gr.top_block` per combo, built
   from only compiled blocks: `wavfile_source -> symbol_sync_ff ->
   binary_slicer_fb -> vector_sink_b`. The output bits are then scored
   **offline in pure Python** — bit-exactly replaying
   `satellites.hdlc_deframer.work()`'s flag/destuff state machine and LSB-first
   byte packing (per the "Test 1" bit-order result above), then checking
   CRC-16/X.25 on every candidate frame, trying both bit polarities per
   combo. This gives a graded score per combination (flag count / near-length
   candidates / actual CRC passes) instead of a single yes/no per run.
   Grid: `ted_type` in {M&M, Gardner, `TED_MENGALI_AND_DANDREA_GMSK`,
   `TED_DANDREA_AND_MENGALI_GEN_MSK`} (the latter two are GMSK/GFSK-specific
   TEDs available in this GNU Radio build, more appropriate to this
   modulation than M&M in principle — turned out not to win here) ×
   `loop_bw` in {0.01, 0.045, 0.08, 0.15} × `damping` in {0.7, 1.3} ×
   `max_dev` in {1.0, 1.5, 2.5} — 96 combos, ~0.3-1s each.
2. **Stage 2 (confirm)**: re-run the winning combo through the *real*
   `satellites.hdlc_deframer` + `blocks.message_debug` — not the offline
   reimplementation — for GNU Radio's own FCS check as independent
   confirmation. Both agree: one message, 272-byte payload, header matches,
   CRC passes.

Only **one** combo out of 96 produced any CRC pass:
`M&M, loop_bw=0.045, damping=0.7, max_dev=1.5`. Several neighboring combos
landed a near-274-byte candidate frame without passing CRC. That looked at
the time like a narrow-but-real working point; the finer sweep below shows
the picture is different.

### How wide is the working window (2026-08-26)

A finer sweep centred on that combo — `MM`, `loop_bw` in 11 steps from 0.02
to 0.08, `damping` in {0.4, 0.5, 0.6, 0.7, 0.85, 1.0}, `max_dev` in {1.2,
1.5, 1.8}, 198 combos, no timeouts — found **12 CRC passes (6 %)**, every
one of them a 274-byte frame:

| `max_dev` | passing (`loop_bw`, `damping`) pairs |
|---|---|
| 1.2 | (0.03, 0.4) (0.04, 0.7) (0.05, 1.0) (0.055, 0.7) |
| 1.5 | (0.025, 0.4) (0.045, 0.7) (0.045, 0.85) (0.05, 1.0) (0.06, 1.0) |
| 1.8 | (0.03, 0.5) (0.04, 0.4) (0.04, 0.5) |

**The passes are scattered, not a contiguous region.** A genuine parameter
optimum would show a connected blob; instead neighbouring cells flip between
pass and fail with no visible structure. Read that as: the loop is working
right at its threshold on this signal — the frame *is* decodable, but only
just, and whether a given parameter set carries the timing cleanly through
all 2201 symbols is close to a coin flip.

Consequences worth keeping in mind:

- **The decode is real.** Twelve independent parameter sets each produce a
  CRC-passing 274-byte frame whose 16-byte header matches
  `REFERENCE_FRAME.md` exactly. That is not chance.
- **No individual parameter value is "the answer."** Don't carry
  `damping=0.7` (or any of these) to a new recording as if it were tuned.
  Sweep instead.
- **On a new file, expect to need a grid, and expect a low hit rate even
  when the signal is decodable.** A handful of hits scattered through a
  sweep is what success looks like here; one hit is not obviously luckier
  than twelve.

**`ted_gain` is not a useful independent axis** (checked 2026-08-26, on a
hypothesis that turned out to be wrong). Because a slicer makes ±1 decisions
while this signal's in-frame RMS is ≈4.55, the M&M error scales with
amplitude, which suggested `ted_gain` ≈ 0.22 should work better than 1.0.
It does not: sweeping `ted_gain` over 0.05…4.5 at the anchor, **only 1.0
passed**. Holding `loop_bw`/`ted_gain` fixed at 0.045 while moving both also
fails in 4 of 6 cases, because GNU Radio's loop coefficients go as
α ∝ `loop_bw`/`ted_gain` but β ∝ `loop_bw²`/`ted_gain` — the two cannot be
traded off by a single ratio. `symbol_sync_sweep.py --ted-gain` accepts a
list if you want to re-check, but leave it at 1.0 and sweep `loop_bw`.

**Gotcha hit during the sweep**: `TED_GARDNER` hung several parameter
combinations indefinitely on this signal (12 of the 96 combos never
returned; confirmed unkillable from inside its own process/thread). The
sweep driver therefore runs each combo as a subprocess with a hard timeout,
recording and skipping any combo that doesn't finish in 15s. This is
unrelated to the epy_block segfault warning above — `satellites.hdlc_deframer`
itself (a Python `gr.sync_block` with a stream port) runs fine in this
environment, as already shown by the bit-order test above; the Gardner hang
is TED-specific and apparently signal-dependent.

### The symbol rate really is 5.000 samples/symbol — and a retracted claim

`plot_pickpoints_comparison.py` reports an `avg_sps` computed as
(input samples) / (output symbols) over the whole file. For this run that
comes out at **4.9234**, and an earlier version of this section read that as
a real ~1.5 % clock offset, complete with a "168 samples of cumulative drift
across the frame" figure. **That was wrong, and is retracted.** `avg_sps` is
a whole-file average, but ~29 of these 30 seconds are noise, where the loop
free-runs — so the number describes the loop's behaviour in noise, not the
frame's symbol rate.

Measured directly instead, by folding the confirmed frame into an eye
diagram at several candidate rates:

| `sps` | eye opening (weakest '1' − strongest '0') |
|---|---|
| **5.00000** | **+5.04** |
| 4.98 – 4.90 | ≤ 0 (no eye at all — the two bit clusters overlap) |

(Eye **opening** = `min(samples sliced as 1) - max(samples sliced as 0)`;
positive means every single decision in the frame sits on the correct side
of the threshold with that much amplitude to spare — see
`eye_fixed_grid.m` / `MATLAB_ANALYSIS.zh-TW.md` for the exact definition and
why an earlier version of this analysis used a different, misleading
"margin" number here: it averaged `|y - threshold|` over *all* 2202 symbols,
~1880 of which are constant zero-run padding sitting far from the threshold
regardless of sampling phase — so that average barely moved with phase and
reported "eye is barely open" (ratio 1.094) for what a direct measurement
shows is a wide-open eye. `eye_fixed_grid.m` replaced that metric entirely.)

Only 5.000 produces an eye at all; everything else is flat, i.e. the
sampling instants smear across the symbol period. **`sps = 5.0` stands**
(= 48000/9600), and the fixed-rate grid `01`/`03` use does *not* drift
relative to the true symbol instants within a frame. Whatever stops `01`/`03`
from decoding these recordings, it is not clock drift.

### Fixed grid alone decodes seg017 *and* seg002 — Symbol Sync was unnecessary (seg017) or actively harmful (seg002)

Given the eye is wide open at a constant `sps=5.0`, the obvious next question
is whether a Symbol Sync loop is even needed. Tested directly: slice the
*entire* 30 s file on a plain uniform grid (`idx(n) = phase + 5.0*n`, no
feedback, no adaptation at all — exactly `01`/`03`'s method), scanning
`phase` across one full symbol period and both bit polarities, and check
every candidate frame `offline_deframe()` finds for a 274-byte CRC pass.

**Result: both seg017 and seg002 decode this way, with a wide margin.**

| Segment | fixed-grid decode phase window | width (% of one symbol) |
|---|---|---|
| seg017 | 1.4 – 4.7 | 67 % |
| seg002 | 0.2 – 4.7 | 90 % |

For seg002 this is a direct contradiction of the earlier `symbol_sync_sweep.py`
result — **144 parameter combinations, zero CRC passes** — which means the
Symbol Sync loop was not failing to help seg002, it was **actively destroying
an already-decodable signal** by dragging the timing estimate away from the
constant, correct `sps=5.0` it should have just left alone. This reframes the
whole "how wide is the working window" finding two sections up: that 12/198
scattered-pass pattern for seg017 was never about whether the *signal* could
tolerate a working point — it was about how often the *loop's own drift*
stayed close enough to the fixed answer that was correct the entire time.

Practical consequence: **for any new segment, try a plain fixed-grid decode
scan before reaching for `symbol_sync_sweep.py` at all.** It is simpler, has
no TED_GARDNER-style hang risk, and two out of two segments tried so far
needed nothing else.

The best sampling phase for seg017's confirmed frame sits 2.0 samples away
from where the tooling anchors the frame, worth ~6% more decision margin —
most likely an artefact of the anchor position itself rather than a real
phase error; see the next section for a much more precise way to pin frame
starts down.

## Locating packets precisely: power dip (coarse) + header correlation (fine) — 2026-08-27

seg010 and seg007 have real, deep power dips (see the segment table at the
top) but no decode, even after the discoveries above. The obvious question —
is there really a packet at the location being tested? — turned out to have
a genuinely wrong answer for seg010, caught only by cross-checking two
independent localization methods against each other.

### Stage 1 (coarse): power-dip matched filter — `power_dip_locate.m`

Same principle as `01a`/`01b`: a real frame is an RMS-power **dip** (FM
quieting), not a rise. `power_dip_locate.m` computes 20 ms-block RMS in dB,
then runs a **matched filter** — a moving average over one frame-duration
window (~229 ms, from the two confirmed frames' own span) — and takes the
global minimum as the candidate location.

Validated against seg017/seg002's already-known content-start position
first: the raw dip-center location misses by **exactly ~half a frame
width** in both cases (+5667 / +5715 samples) — expected, since a matched
filter's minimum lands at the *center* of the boxcar window (flags + content
+ surrounding padding), not the content's start. Correcting for that half-
frame offset brings both down to **+166 / +214 samples (1.5–1.9% of frame
width)** — solid validation of the coarse method, but "solid" here still
means potentially hundreds of samples off, nowhere near precise enough to
decide whether a *specific* fixed phase decodes.

Applied to seg010/seg007, the corrected candidate positions produced power
dips **deeper than seg017/seg002's own confirmed dips** — strong evidence of
a real signal, not noise — but a full fixed-grid CRC search around each
(±2 frame-widths, all phases, both polarities) still found **zero passes**.

### Stage 2 (fine): header cross-correlation — `header_correlate_locate.m`

Ported from `01_frame_detection.py`'s `detect_frame_starts()`: build an NRZ
template from the constant part of every frame — 4 leading HDLC flags
(`0x7E`×4) + the 14-byte Dest+Src address — upsample by `sps`, and slide it
across the waveform computing a **normalized cross-correlation z-score**.

One deliberate change from the Python original: this MATLAB version
zero-means *only the template*, not the window, before correlating (a
Pearson-style correlation). Because a zero-mean template makes the window's
own mean drop out of the numerator algebraically, this removes sensitivity
to a DC offset in the window **without** a separate baseline-restoration
pass — worth doing here because seg010 shows a visible DC step mid-segment
(see `eye_fixed_grid.m`'s waveform panel). `01_frame_detection.py`'s original
correlates raw dot products against already baseline-*restored* audio
instead; the two approaches address the same DC-sensitivity issue by
different means.

Validated the same way: run it on seg017/seg002 first.

| Segment | z-score | header peak (sample) | vs. known content-start |
|---|---|---|---|
| seg017 | 13.08 | 916252 | +161 samples (32.2 symbols) |
| seg002 | 13.47 | 1159085 | +160 samples (**32.0 symbols, exact**) |

32 symbols = 4 flags × 8 bits — the calibrated flags→content offset matches
the protocol's own structure exactly, in both cases. This is a precise,
theory-confirming localization, not just a plausible-looking peak.

Applied to seg010/seg007 with that same 32.1-symbol calibrated offset:

| Segment | z-score | header peak (sample) | vs. power-dip's coarse candidate |
|---|---|---|---|
| **seg010** | **13.71** (*stronger than either confirmed decode*) | 316927 | **7332 samples off** — over a full frame-width away |
| seg007 | 8.12 (clearly above the ~5 noise floor, but weaker) | 1382708 | close |

**seg010's coarse power-dip location was simply wrong** — off by more than
one frame width, caught only because header correlation is precise enough
to disagree with it sharply. Both header peaks are visually confirmable too:
in `eye_fixed_grid.m`'s bottom-right panel (now with the header span shaded
— see `HEADER_START`/`HEADER_LEN`), the waveform visibly narrows from
wide/noisy to quiet **exactly** at the shaded header boundary, for all four
segments including the two undecoded ones — independent, non-statistical
confirmation that the header really is there.

### Status at the corrected locations: real signal, still not decodable

Re-running `eye_fixed_grid.m` at these header-correlation-corrected
positions: the decision-sample histograms for seg010/seg007 now show
**genuine two-cluster structure** (unlike the single unimodal noise blob
seen at the earlier, wrong power-dip locations) — but heavily overlapping,
with eye openings of only **+0.03 to +0.27** vs. seg017's +5.04. A fixed-grid
CRC search at the corrected locations (± a few samples, both polarities)
still finds **zero passes**.

**Conclusion: seg010 and seg007 both have a real, precisely-located packet —
this is now a genuine per-bit SNR problem, not a localization problem.**
Whatever fixes this (if anything can), it is not "look in the right place"
— that part is solved.

**Not yet done**: whether an adaptive Symbol Sync loop (anchored at these
*correct* locations, unlike the blind whole-file sweeps in
`symbol_sync_sweep.py` that already failed) can claw back enough SNR to
decode seg010/seg007 has not been tried. seg004/seg018 haven't been
localized or tested with any of these tools at all.

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
