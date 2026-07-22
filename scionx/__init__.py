# -*- coding: utf-8 -*-
"""
scionx -- pure-Python package for the SCIONX ver2 pipeline.

Decodes HDLC/AX.25 frames from a .ogg audio recording. Full pipeline:
    audio_io -> baseline -> gnuradio_blocks (low_pass + symbol_sync)
             -> slicer -> gnuradio_blocks (hdlc_deframer + CRC) -> scoring / plotting

Design: the pure-logic modules (audio_io / baseline / slicer / hdlc) do not
depend on gnuradio and can be unit-tested offline. The gnuradio-dependent
modules (gnuradio_blocks.py) and the scoring module live in the full internal
project and are NOT part of this shared diagnostic subset -- everything needed
to run the scripts in this folder is here and gnuradio-free.
"""

__all__ = [
    "audio_io",
    "baseline",
    "slicer",
    "hdlc",
]
