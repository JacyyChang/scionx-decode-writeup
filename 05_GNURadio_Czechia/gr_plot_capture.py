# -*- coding: utf-8 -*-
"""
gr_plot_capture.py -- SHIM. The real implementation lives in
grc_blocks/gr_plot_capture.py (moved there 2026-08-31 to keep the flowgraph's
own directory uncluttered). This 2-line file exists so that plain
`import gr_plot_capture` -- what GNU Radio Companion's "Plot Capture" block
(grc_blocks/plot_capture.block.yml) has always generated, and will keep
generating even from a stale/cached copy of that block definition -- keeps
working no matter what: GRC only re-scans local_blocks_path at STARTUP, and
in practice a restart did not reliably pick up an edited block.yml either, so
fixing this via the block's own `imports` template proved fragile. A plain
sibling-file shim has no such caching dependency at all -- it is just an
ordinary Python import, resolved the same way every time.

If gr_plot_capture.py/gr_plot_sink.py ever move again, update the path below,
not any block .block.yml file.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "grc_blocks"))
del sys.modules[__name__]          # drop this shim's own entry so the real
import gr_plot_capture             # module (now first on sys.path) replaces it
sys.modules[__name__] = gr_plot_capture
