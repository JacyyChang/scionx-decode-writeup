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
| `gr_plot_sink.py` | Renders matplotlib PNGs straight out of a flowgraph — replaces the "File Sink -> `.bin` -> separate plotting script" round trip. See below. |
| `gr_plot_capture.py` | `plot_capture`, a hier block bundling Head+Vector Sink+`gr_plot_sink` behind one input port — drag one block instead of wiring two. |
| `grc_blocks/plot_capture.block.yml` | GRC block definition for `plot_capture`, so it shows up in the GRC block tree like a built-in block. |
| `IQtoOgg_plot.grc` | The cs16 -> resampled/demodulated-audio decode chain, with two `plot_capture` taps and a two-line Python Snippet that fires them on exit. Worked example. |

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
