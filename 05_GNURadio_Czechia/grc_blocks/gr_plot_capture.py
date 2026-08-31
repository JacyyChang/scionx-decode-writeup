# -*- coding: utf-8 -*-
"""
gr_plot_capture.py -- a GNU Radio hierarchical block that bundles the
"bound a capture, then render it" pattern from gr_plot_sink.py into a single
draggable block: one wire in, one property dialog (input type, capture length,
which panels to draw), no second Python script.

Why a hier block, and not a real sink block
--------------------------------------------
This project's radioconda install segfaults on *any* streaming
`gr.sync_block` -- see the crash note in gr_plot_sink.py's module docstring
and CLAUDE.md. A `gr.hier_block2`, by contrast, has no `work()` of its own: it
is just a wiring container around ordinary C++ blocks (`blocks.vector_sink_*`
here), so it never goes through the Python gateway callback that crashes. That
is the whole reason this class exists instead of just fixing up the original
`blk` sink.

Why there is no `blocks.head` inside, despite the "bound the capture" framing
------------------------------------------------------------------------------
The first version of this file used `Head(nsamples) -> Vector Sink`. That
deadlocks the *entire* flowgraph once Head hits its limit, if the tap point is
shared with any other still-running consumer (e.g. a QT GUI sink also fed by
the same upstream block) -- confirmed by direct reproduction 2026-08:
a Head-capped branch and a plain Vector Sink branch off the same Throttle
output, run for 5x longer than Head's limit; the plain branch's item count
froze at ~Head's limit too, instead of reaching 5x that. Symptom in practice:
everything downstream of the shared tap (waterfall, freq sink, other Plot
Capture blocks) stops updating at exactly `nsamples / samp_rate` seconds and
never recovers.

Why: once `blocks.head` reaches its limit it stops calling work(), which means
it permanently stops draining its read pointer on the shared ring buffer that
feeds it. GNU Radio's buffer can't reclaim space still claimed by an unread
pointer, even a permanently-stalled one, so the buffer fills and the upstream
writer (the block feeding the shared tap) blocks trying to produce more --
freezing every other reader of that same output, healthy or not.

A bare Vector Sink never triggers this: it never returns WORK_DONE, so it
never stops draining, so it never stalls anything upstream. The trade-off is
that `nsamples` is now enforced by *truncating in Python* (`data()`) rather
than by the scheduler -- the Vector Sink keeps growing for as long as the
flowgraph keeps running, past `nsamples` if you don't stop it. For this
project's actual use (finite recordings, watched for seconds to a couple of
minutes before closing the window) that's the right trade: silently capped
memory growth beats a hard freeze of every other display in the flowgraph.
If you leave a capture running unusually long, close the window sooner rather
than counting on `nsamples` to protect RAM.

The trade-off is that a hier block's own Python methods are never called by
the scheduler -- there is no lifecycle hook equivalent to `gr.sync_block.stop()`
here. Something still has to call `.plot()` after the flowgraph stops, and in
GRC that "something" has to be a Python Snippet block (`section:
main_after_stop`) -- that's the one piece this file cannot absorb. It shrinks
that snippet to one call per capture point, e.g.::

    self.plotcap_iq.plot()

instead of hand-writing the head/vector-sink wiring and the render() call.

Usage in GRC
------------
Requires `local_blocks_path` in `~/.gnuradio/config.conf` (or the roaming
equivalent on Windows) to include `appendix_gnuradio/grc_blocks` -- see
`grc_blocks/plot_capture.block.yml`. Once GRC picks that up, "Plot Capture"
appears in the block tree like any built-in block: drag it, wire one input,
set `type` / `mode` / `nsamples` / ... in its properties, and reference
`self.<name>.plot()` from a Snippet.

Periodic (rolling) capture
---------------------------
`start_periodic_plot(interval_sec)` saves one PNG per `interval_sec` of new
data as it arrives, instead of a single shot at the end -- "one figure every
30s until the recording runs out" style monitoring. It's a `PyQt5.QtCore.QTimer`
polling `self._vsink.data()` on the Qt main thread, the same mechanism the QT
GUI sinks already use -- not a streaming block, so it doesn't touch the
Python-gateway crash path at all. Wire it up with two Snippets:

    # section: Main - After Start
    self.plotcap_iq.start_periodic_plot(30)

    # section: Main - After Stop  (flushes whatever partial segment remains
    # if you close the window before the source runs out on its own)
    self.plotcap_iq.stop_periodic_plot(flush_partial=True)

It also self-stops (with a final partial flush) once the capture stops
growing for a few polls in a row -- the signal that the upstream source (e.g.
a non-repeating File Source) has run out, so "until the signal finishes" needs
no extra wiring.

`nsamples` (the GRC "Capture Samples" property) and `interval_sec` are
**independent, and it's fine if they don't match** -- confirmed by direct
test 2026-08 (1 s worth of `nsamples` against 2 s `interval_sec` segments
still produced clean, correctly-sized 2 s segments). `_on_seg_tick` reads
`self._vsink.data()` directly (the raw, unbounded, still-growing array), never
through `self.data()` (which is what applies the `nsamples` truncation) --
so periodic segment length is governed by `interval_sec` alone. `nsamples`
only still matters if something *also* calls the one-shot `.plot()` on the
same block instance (it would show just the first `nsamples`, independent of
how far periodic capture has progressed) -- and it sizes the Vector Sink's
`reserve_items`, which is a preallocation hint only, not a cap either way.
"""

import os
import sys

import numpy as np
from gnuradio import gr, blocks

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gr_plot_sink

# GRC block dtype name -> (blocks.vector_sink_* suffix, itemsize)
_VSINK_SUFFIX = {"complex": "c", "float": "f", "int": "i", "short": "s", "byte": "b"}
_ITEMSIZE = {
    "complex": gr.sizeof_gr_complex,
    "float": gr.sizeof_float,
    "int": gr.sizeof_int,
    "short": gr.sizeof_short,
    "byte": gr.sizeof_char,
}


class plot_capture(gr.hier_block2):
    """Plot Capture -- a Vector Sink behind one input port, plus
    `data()` / `plot()` to pull the capture into gr_plot_sink.render().

    Args mirror gr_plot_sink.render()'s panel/output options; `dtype` sizes
    the internal Vector Sink's item type, `nsamples` bounds what `data()`
    returns (see the module docstring for why that's a Python-side truncation
    and not a `blocks.head` in the wiring).
    """

    def __init__(self, dtype="complex", samp_rate=100000.0, nsamples=200000,
                 skip=0, mode="all", sps=0, nfft=1024,
                 out_dir="Figure", prefix="plot", title="", save_npy=False):
        dtype = str(dtype).strip().lower()
        if dtype not in _ITEMSIZE:
            raise ValueError("dtype must be one of {0}, got {1!r}".format(
                sorted(_ITEMSIZE), dtype))
        itemsize = _ITEMSIZE[dtype]
        nsamples = max(1, int(nsamples))

        gr.hier_block2.__init__(
            self, "Plot Capture",
            gr.io_signature(1, 1, itemsize),
            gr.io_signature(0, 0, 0))

        self.samp_rate = float(samp_rate)
        self.nsamples = nsamples
        self.skip = max(0, int(skip))
        self.mode = str(mode)
        self.sps = int(sps)
        self.nfft = int(nfft)
        self.out_dir = str(out_dir)
        self.prefix = str(prefix)
        self.title = str(title)
        self.save_npy = bool(save_npy)

        # reserve_items is only a preallocation hint -- the sink keeps growing
        # past it for as long as the flowgraph runs. See the module docstring.
        vsink_ctor = getattr(blocks, "vector_sink_" + _VSINK_SUFFIX[dtype])
        self._vsink = vsink_ctor(1, nsamples)

        self.connect((self, 0), (self._vsink, 0))

        self._seg_timer = None
        self._seg_samples = 0
        self._seg_start = 0
        self._seg_index = 0
        self._seg_overrides = {}
        self._seg_last_len = -1
        self._seg_stable_polls = 0

    def data(self):
        """The first `nsamples` of the capture, with `skip` applied first."""
        d = np.asarray(self._vsink.data())
        if self.skip:
            d = d[self.skip:]
        return d[:self.nsamples]

    def plot(self, **overrides):
        """Render this capture. Uses the block's own GRC-set properties as
        defaults; pass keyword args (e.g. mode='iq') from the Snippet to
        override any of them for one call without touching the properties
        dialog."""
        kwargs = dict(samp_rate=self.samp_rate, mode=self.mode, sps=self.sps,
                      nfft=self.nfft, out_dir=self.out_dir, prefix=self.prefix,
                      title=self.title, save_npy=self.save_npy)
        kwargs.update(overrides)
        return gr_plot_sink.render(self.data(), **kwargs)

    # -- periodic (rolling) capture -----------------------------------------

    def start_periodic_plot(self, interval_sec, stable_polls=5, **overrides):
        """Start saving one PNG per `interval_sec` of newly-arrived data.

        `overrides` behaves like `plot()`'s -- anything not given falls back
        to this block's own properties. The segment index is appended to
        `prefix` automatically (`prefix_seg000`, `prefix_seg001`, ...).

        Auto-stops (flushing whatever partial segment remains) once the
        capture goes `stable_polls` polls in a row without growing -- the
        signal that the upstream source has run out. Call
        `stop_periodic_plot()` yourself if you want to end it early (e.g. from
        a `main_after_stop` Snippet, in case the window gets closed before
        that point).
        """
        from PyQt5 import QtCore

        interval_sec = float(interval_sec)
        if interval_sec <= 0:
            raise ValueError("interval_sec must be > 0")
        self._seg_samples = max(1, int(round(interval_sec * self.samp_rate)))
        self._seg_start = 0
        self._seg_index = 0
        self._seg_overrides = dict(overrides)
        self._seg_last_len = -1
        self._seg_stable_polls = 0
        self._seg_stable_target = max(2, int(stable_polls))

        # Poll several times per segment so "source ran out" is detected
        # promptly rather than waiting a full interval past the last sample.
        poll_ms = max(200, int(interval_sec * 1000 / 10))
        if self._seg_timer is None:
            self._seg_timer = QtCore.QTimer()
            self._seg_timer.timeout.connect(self._on_seg_tick)
        self._seg_timer.start(poll_ms)

    def stop_periodic_plot(self, flush_partial=False):
        """Stop the periodic timer. With `flush_partial=True`, render whatever
        samples have accumulated since the last full segment before stopping
        -- use this from a `main_after_stop` Snippet so an early manual stop
        doesn't silently drop the tail end."""
        if self._seg_timer is not None:
            self._seg_timer.stop()
        if flush_partial and self._seg_samples:
            d = np.asarray(self._vsink.data())
            tail = d[self._seg_start:]
            if len(tail):
                self._render_segment(tail)
                self._seg_start = len(d)

    def _on_seg_tick(self):
        d = np.asarray(self._vsink.data())
        n = len(d)

        while self._seg_start + self._seg_samples <= n:
            seg = d[self._seg_start:self._seg_start + self._seg_samples]
            self._render_segment(seg)
            self._seg_start += self._seg_samples
            self._seg_index += 1

        if n == self._seg_last_len:
            self._seg_stable_polls += 1
        else:
            self._seg_stable_polls = 0
        self._seg_last_len = n

        if self._seg_stable_polls >= self._seg_stable_target:
            self.stop_periodic_plot(flush_partial=True)

    def _render_segment(self, seg):
        kwargs = dict(samp_rate=self.samp_rate, mode=self.mode, sps=self.sps,
                      nfft=self.nfft, out_dir=self.out_dir,
                      prefix="{0}_seg{1:03d}".format(self.prefix, self._seg_index),
                      title=self.title, save_npy=self.save_npy)
        kwargs.update(self._seg_overrides)
        gr_plot_sink.render(seg, **kwargs)
