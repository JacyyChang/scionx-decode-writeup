# -*- coding: utf-8 -*-
"""
gr_plot_sink.py -- render matplotlib figures straight out of a GNU Radio
flowgraph, so an experiment no longer needs the
"File Sink -> .bin -> separate Python script" round trip.

Why this exists
---------------
The usual way to look at a signal inside a flowgraph is a QT GUI sink, but a
QT GUI sink is *live* only: nothing is saved, it is unusable in a headless
(no-gui) flowgraph, and it repaints far faster than the eye can latch onto a
0.4 s packet. The usual way to get a *saved* result is a File Sink plus an
offline plotting script, which costs two runs and a hand-written reader that
has to re-derive dtype and sample rate every time.

Two entry points, same renderer:

  1. `plot_vector_sink(tb.<a vector sink>, ...)` -- call it from a GRC
     **Snippet** block set to `main_after_stop`. This is the recommended path
     on Windows/radioconda, see the warning below.
  2. `blk` -- a real sink block (Embedded Python Block / `gr.sync_block`) that
     captures a bounded window and renders it in-stream.

     ⚠ **Streaming Python blocks segfault on this project's radioconda
     install** (GNU Radio 3.10.12 / Python 3.12 / numpy 2.2). Verified 2026-08:
     *any* `gr.sync_block` with a stream port crashes the C++ gateway thread
     with an access violation the moment the flowgraph starts -- a stock
     3-line pass-through block crashes identically, so this is the environment,
     not this file. Message-only Python blocks (`in_sig=None, out_sig=None`,
     like `hdlc_check_epy_block_0_0.py`) are unaffected, which is why the
     problem had never surfaced here. Use path 1 until that install is fixed.

Design notes / what was tried and rejected
------------------------------------------
- **Agg is forced, and pyplot is imported lazily.** A QT GUI flowgraph has
  already imported PyQt5; if pyplot picked Qt5Agg it would try to build widgets
  off the main thread. Importing late also keeps GRC's block introspection
  cheap -- GRC instantiates `blk` on every edit just to read its io signature.
- **Bounded capture, never streaming-forever.** A repeating File Source would
  otherwise grow the buffer without limit. Pair a Vector Sink with a Head block
  (or use `blk`'s `nsamples`); `skip` jumps past the filter settling transient.
- **Output filenames always carry a timestamp.** Sweeping a parameter and
  having each run silently overwrite the previous PNG is the exact failure mode
  this file exists to remove.
- **`.npy`, not raw `.bin`, for the optional data dump.** It already carries
  dtype and shape, so a later re-plot needs no reader that guesses either.
"""

import os
import sys
import time

import numpy as np


# --------------------------------------------------------------------------
# Shared plotting style. The diagnostic scripts one directory up all go through
# _style.py so every figure in the writeup shares a palette and DPI; pick it up
# if it is reachable, but stay functional when this file is copied elsewhere
# (e.g. pasted into an Embedded Python Block on another machine).
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

try:
    import _style

    _PALETTE = [_style.BLUE, _style.ORANGE, _style.GREEN, _style.PURPLE]
    _GRAY = _style.GRAY
except Exception:  # pragma: no cover - only when copied out of the repo
    _style = None
    _PALETTE = [(0.0, 0.447, 0.741), (0.85, 0.325, 0.098),
                (0.466, 0.674, 0.188), (0.494, 0.184, 0.556)]
    _GRAY = (0.5, 0.5, 0.5)


# `mode` accepts a comma-separated list; these are the shorthand expansions.
_MODE_ALIASES = {
    "all": "time,psd,spec",
    "rf": "psd,spec",
    "af": "time,psd,eye",
}

_VALID_PANELS = ("time", "psd", "spec", "hist", "iq", "eye")


# ==========================================================================
# numpy helpers
# ==========================================================================

def _minmax_decimate(y, max_buckets=4000):
    """Bucket `y` into <= max_buckets groups and return (lo, hi) envelopes.

    Returns None when the array is short enough to draw honestly as a line.
    Pushing 10^7 points through matplotlib takes minutes and renders as a solid
    block; the min/max envelope keeps every excursion at a readable cost.
    """
    n = len(y)
    if n <= max_buckets * 2:
        return None
    bucket = n // max_buckets
    usable = (n // bucket) * bucket
    r = y[:usable].reshape(-1, bucket)
    return r.min(axis=1), r.max(axis=1)


def _draw_trace(ax, t, y, color, label):
    """Draw one real-valued trace, decimating only when it is worth it."""
    env = _minmax_decimate(y)
    if env is None:
        ax.plot(t, y, color=color, linewidth=0.8, label=label)
    else:
        lo, hi = env
        # Bucket centers, so the envelope lines up with the true time axis.
        tc = np.linspace(t[0], t[-1], len(lo))
        ax.fill_between(tc, lo, hi, color=color, linewidth=0.0, label=label)


def _welch_psd(x, nfft, fs, complex_input):
    """Averaged periodogram (Hann window, 50% overlap), pure numpy.

    scipy.signal.welch would be a one-liner, but the offline scripts in this
    project are deliberately numpy-only and this file is meant to stay
    pasteable into a machine that has nothing beyond GNU Radio's own deps.
    """
    nfft = int(min(nfft, len(x)))
    if nfft < 8:
        raise ValueError("need at least 8 samples for a PSD")
    win = np.hanning(nfft)
    hop = max(1, nfft // 2)
    nseg = max(1, min(1 + (len(x) - nfft) // hop, 4000))  # cap work on long captures
    acc = np.zeros(nfft)
    for i in range(nseg):
        acc += np.abs(np.fft.fft(x[i * hop:i * hop + nfft] * win, nfft)) ** 2
    psd = acc / (nseg * (win ** 2).sum() * fs)

    if complex_input:
        psd = np.fft.fftshift(psd)
        freq = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / fs))
    else:
        psd = psd[:nfft // 2 + 1]
        freq = np.fft.rfftfreq(nfft, 1.0 / fs)
    return freq, 10.0 * np.log10(psd + 1e-20)


def _spectrogram(x, nfft, fs, complex_input, max_cols=800):
    """Stacked FFT magnitudes in dB, plus the imshow `extent` tuple."""
    nfft = int(min(nfft, len(x)))
    if nfft < 8:
        raise ValueError("need at least 8 samples for a spectrogram")
    hop = max(1, (len(x) - nfft) // max_cols)
    ncols = max(1, 1 + (len(x) - nfft) // hop)
    win = np.hanning(nfft)
    cols = np.empty((ncols, nfft))
    for i in range(ncols):
        cols[i] = np.abs(np.fft.fft(x[i * hop:i * hop + nfft] * win, nfft)) ** 2

    if complex_input:
        cols = np.fft.fftshift(cols, axes=1)
        f0, f1 = -fs / 2.0, fs / 2.0
    else:
        cols = cols[:, :nfft // 2 + 1]
        f0, f1 = 0.0, fs / 2.0
    return 10.0 * np.log10(cols.T + 1e-20), (0.0, (ncols * hop + nfft) / fs, f0, f1)


# ==========================================================================
# the renderer -- everything below shares this one function
# ==========================================================================

def expand_mode(mode):
    """'all' / 'rf' / 'af' or a comma-separated panel list -> list of panels."""
    m = _MODE_ALIASES.get(str(mode).strip().lower(), str(mode))
    out = [p.strip().lower() for p in m.split(",") if p.strip()]
    bad = [p for p in out if p not in _VALID_PANELS]
    if bad:
        raise ValueError("unknown panel(s) {0}; valid: {1}".format(bad, _VALID_PANELS))
    return out or ["time"]


def render(data, samp_rate=1.0, mode="all", sps=0, nfft=1024,
           out_dir="Figure", prefix="plot", title="", save_npy=False,
           verbose=True):
    """Render `data` (1-D numpy array, real or complex) to a timestamped PNG.

    Returns the path written. This is the single implementation used both by
    the Snippet path and by the `blk` sink block.
    """
    data = np.asarray(data)
    if data.ndim != 1:
        data = data.reshape(-1)
    if data.size == 0:
        if verbose:
            print("[Plot Sink] nothing captured, no figure written", flush=True)
        return None

    # Late import, Agg forced before pyplot -- see the module docstring.
    import matplotlib
    matplotlib.use("Agg", force=True)
    if _style is not None:
        plt = _style.setup_mpl()
    else:
        import matplotlib.pyplot as plt
        plt.rcParams["figure.dpi"] = 110
        plt.rcParams["savefig.dpi"] = 130
        plt.rcParams["axes.grid"] = True
        plt.rcParams["grid.alpha"] = 0.3

    is_complex = np.iscomplexobj(data)
    # Work in float64 downstream: an int16/int8 port would otherwise overflow
    # the squaring inside the PSD.
    xc = data.astype(np.complex128) if is_complex else data.astype(np.float64)
    xr = np.real(xc)
    fs = float(samp_rate) if samp_rate and samp_rate > 0 else 1.0
    t = np.arange(len(xc)) / fs

    panels = expand_mode(mode)
    fig, axes = plt.subplots(len(panels), 1,
                             figsize=(11, 3.0 * len(panels) + 0.6), squeeze=False)
    for ax, panel in zip(axes[:, 0], panels):
        try:
            _draw_panel(ax, panel, xc, xr, t, fs, is_complex, sps, nfft)
        except Exception as exc:  # a bad panel must never discard the capture
            ax.text(0.5, 0.5, "{0}: {1}".format(panel, exc),
                    ha="center", va="center", transform=ax.transAxes)

    fig.suptitle("{0}  |  {1} samples @ {2:g} Hz  ({3:.4g} s)".format(
        title or prefix, len(xc), fs, len(xc) / fs), fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    base = "{0}_{1}_{2}".format(prefix, "-".join(panels), time.strftime("%Y%m%d_%H%M%S"))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, base + ".png")
    fig.savefig(png)
    plt.close(fig)
    if verbose:
        print("[Plot Sink] wrote {0}".format(os.path.abspath(png)), flush=True)

    if save_npy:
        npy = os.path.join(out_dir, base + ".npy")
        np.save(npy, data)
        if verbose:
            print("[Plot Sink] wrote {0}".format(os.path.abspath(npy)), flush=True)
    return png


def _draw_panel(ax, panel, xc, xr, t, fs, is_complex, sps, nfft):
    if panel == "time":
        if is_complex:
            _draw_trace(ax, t, np.real(xc), _PALETTE[0], "I")
            _draw_trace(ax, t, np.imag(xc), _PALETTE[1], "Q")
            ax.legend(loc="upper right", fontsize=8)
        else:
            _draw_trace(ax, t, xr, _PALETTE[0], None)
        ax.set_xlabel("time [s]")
        ax.set_ylabel("amplitude")
        ax.set_xlim(t[0], t[-1])

    elif panel == "psd":
        freq, db = _welch_psd(xc, nfft, fs, is_complex)
        ax.plot(freq / 1e3, db, color=_PALETTE[0], linewidth=0.9)
        ax.set_xlabel("frequency [kHz]")
        ax.set_ylabel("PSD [dB/Hz]")
        ax.set_xlim(freq[0] / 1e3, freq[-1] / 1e3)

    elif panel == "spec":
        img, ext = _spectrogram(xc, nfft, fs, is_complex)
        # Clip the color scale to percentiles. On a full auto-scale the noise
        # floor eats the whole colormap and a real carrier is invisible, which
        # is the single most common reason a home-made waterfall looks blank.
        vmin, vmax = np.percentile(img, (5.0, 99.8))
        im = ax.imshow(img, aspect="auto", origin="lower",
                       extent=(ext[0], ext[1], ext[2] / 1e3, ext[3] / 1e3),
                       cmap="viridis", vmin=vmin, vmax=vmax)
        ax.grid(False)
        ax.set_xlabel("time [s]")
        ax.set_ylabel("frequency [kHz]")
        ax.figure.colorbar(im, ax=ax, pad=0.01, label="dB")

    elif panel == "hist":
        ax.hist(np.abs(xc) if is_complex else xr, bins=256, color=_PALETTE[0])
        ax.set_xlabel("magnitude" if is_complex else "amplitude")
        ax.set_ylabel("count")

    elif panel == "iq":
        if not is_complex:
            raise ValueError("iq panel needs a complex input")
        ax.hist2d(np.real(xc), np.imag(xc), bins=200, cmap="viridis")
        ax.set_xlabel("I")
        ax.set_ylabel("Q")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(False)

    elif panel == "eye":
        if int(sps) < 2:
            raise ValueError("eye panel needs sps >= 2")
        sps = int(sps)
        span = 2 * sps
        ntr = min(len(xr) // span, 400)  # 400 traces already gives a solid eye
        if ntr < 1:
            raise ValueError("not enough samples for one eye trace")
        traces = xr[:ntr * span].reshape(ntr, span)
        te = (np.arange(span) - sps) / float(sps)
        ax.plot(te, traces.T, color=_PALETTE[0], linewidth=0.4, alpha=0.25)
        ax.axvline(0.0, color=_GRAY, linewidth=0.8)
        ax.set_xlabel("symbol periods")
        ax.set_ylabel("amplitude")
        ax.set_xlim(te[0], te[-1])


# ==========================================================================
# entry point 1: GRC Snippet + a C++ Vector Sink  (recommended here)
# ==========================================================================

def plot_vector_sink(vector_sink, samp_rate=1.0, mode="all", skip=0, **kwargs):
    """Pull the samples out of a `blocks.vector_sink_*` and render them.

    Meant to be called from a GRC **Snippet** block with section
    `main_after_stop`, where the generated flowgraph object is bound to `tb`::

        import sys; sys.path.insert(0, r'D:\\...\\appendix_gnuradio')
        import gr_plot_sink
        gr_plot_sink.plot_vector_sink(tb.blocks_vector_sink_x_0,
                                      samp_rate=100000, mode='rf',
                                      prefix='iq', skip=20000)

    `skip` drops leading samples (filter/PLL settling). Everything else is
    forwarded to render(): mode, sps, nfft, out_dir, prefix, title, save_npy.

    Note this path uses only C++ blocks in the flowgraph itself, so it is
    immune to the streaming-Python-block crash described in the module
    docstring.
    """
    data = np.asarray(vector_sink.data())
    if skip:
        data = data[int(skip):]
    return render(data, samp_rate=samp_rate, mode=mode, **kwargs)


def plot_file(path, dtype="complex64", samp_rate=1.0, mode="all", skip=0, **kwargs):
    """Render an existing File Sink dump / .npy, for flowgraphs already wired
    the old way. `dtype` is the File Sink's item type ('complex64', 'float32',
    'int16', 'int8'); .npy files carry their own dtype and ignore it."""
    if path.lower().endswith(".npy"):
        data = np.load(path)
    else:
        data = np.fromfile(path, dtype=np.dtype(dtype))
    if skip:
        data = data[int(skip):]
    return render(data, samp_rate=samp_rate, mode=mode,
                  prefix=kwargs.pop("prefix", os.path.splitext(os.path.basename(path))[0]),
                  **kwargs)


# ==========================================================================
# entry point 2: a real sink block
#   (Embedded Python Block -- see the crash warning in the module docstring)
# ==========================================================================

# Input type name (as typed in GRC) -> numpy dtype for the input port.
_DTYPES = {
    "complex": np.complex64,
    "float": np.float32,
    "int": np.int32,
    "short": np.int16,
    "byte": np.int8,
}

try:
    from gnuradio import gr
except ImportError:  # allow offline use of render()/plot_file() without GNU Radio
    gr = None


if gr is not None:

    class blk(gr.sync_block):
        """Plot Sink (PNG)

        Captures `nsamples` samples (after skipping `skip`) and writes a
        matplotlib PNG -- no File Sink, no second script.

        Args:
            dtype:      input port type: complex / float / int / short / byte
            samp_rate:  sample rate at this point in the flowgraph, for the axes
            mode:       comma-separated panels, or an alias: all / rf / af.
                        Panels: time, psd, spec, hist, iq, eye
            nsamples:   how many samples to capture (hard RAM cap)
            skip:       samples to discard first (settling transient)
            sps:        samples per symbol -- only the 'eye' panel needs it
            nfft:       FFT size for the psd / spec panels
            out_dir:    output directory, created if missing (relative to cwd)
            prefix:     leading part of the output filename
            title:      optional figure title
            save_npy:   also dump the capture as .npy for offline re-plots
            stop_when_full: signal WORK_DONE once full, so a headless
                        flowgraph terminates by itself
        """

        def __init__(self, dtype="complex", samp_rate=100000.0, mode="all",
                     nsamples=200000, skip=0, sps=0, nfft=1024,
                     out_dir="Figure", prefix="plot", title="",
                     save_npy=False, stop_when_full=True):
            self.np_dtype = _DTYPES.get(str(dtype).strip().lower(), np.complex64)
            gr.sync_block.__init__(self, name="Plot Sink (PNG)",
                                   in_sig=[self.np_dtype], out_sig=None)

            self.samp_rate = float(samp_rate)
            self.mode = str(mode)
            self.capacity = max(1, int(nsamples))
            self.skip = max(0, int(skip))
            self.sps = int(sps)
            self.nfft = max(8, int(nfft))
            self.out_dir = str(out_dir)
            self.prefix = str(prefix)
            self.title = str(title)
            self.save_npy = bool(save_npy)
            self.stop_when_full = bool(stop_when_full)

            # Allocated on the first work() call: `nsamples` may be tens of
            # millions and GRC re-instantiates the block on every dialog edit.
            self._buf = None
            self._filled = 0
            self._skipped = 0
            self._rendered = False

        def work(self, input_items, output_items):
            x = input_items[0]
            n = len(x)
            if self._rendered:
                return n

            if self._skipped < self.skip:
                drop = min(self.skip - self._skipped, n)
                self._skipped += drop
                x = x[drop:]
                if len(x) == 0:
                    return n

            if self._buf is None:
                self._buf = np.empty(self.capacity, dtype=self.np_dtype)

            take = min(self.capacity - self._filled, len(x))
            if take > 0:
                self._buf[self._filled:self._filled + take] = x[:take]
                self._filled += take

            if self._filled >= self.capacity:
                self._flush()
                if self.stop_when_full:
                    # Ends this branch; a no-gui flowgraph with no other live
                    # sink then shuts itself down.
                    return gr.WORK_DONE
            return n

        def stop(self):
            """Flush a partial capture when the flowgraph is closed early.

            This is the QT GUI path: watch the waterfall, close the window, and
            still get the PNG for whatever had been captured."""
            self._flush()
            return True

        def _flush(self):
            if self._rendered or self._filled <= 0:
                return
            self._rendered = True
            render(self._buf[:self._filled], samp_rate=self.samp_rate,
                   mode=self.mode, sps=self.sps, nfft=self.nfft,
                   out_dir=self.out_dir, prefix=self.prefix,
                   title=self.title or self._block_label(),
                   save_npy=self.save_npy)

        def _block_label(self):
            """Best available human name: the GRC alias if one was set, else
            the block's unique symbol name, else the filename prefix."""
            for getter in ("alias", "symbol_name"):
                try:
                    v = getattr(self, getter)()
                    if v:
                        return str(v)
                except Exception:
                    pass
            return self.prefix
