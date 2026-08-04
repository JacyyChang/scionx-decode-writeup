# -*- coding: utf-8 -*-
"""
audio_io.py -- read an audio file into single-channel float samples.

Provides:
  - read_audio: read .ogg/.wav etc. (any format soundfile supports) -> (samples, fs).
  - read_raw_f32: read a raw little-endian float32 file (fallback path that
    doesn't need soundfile; e.g. when some other tool has already decoded the
    .ogg into a .f32 beforehand).

samples is always channel 0, cast to a 1D float64 array.
"""

import numpy as np


def read_audio(path, expected_fs=None, start_sec=None, end_sec=None):
    """
    Read an audio file -> (samples, fs). samples is 1D float64 (channel 0 of
    a multi-channel file).

    Args:
        path         audio file path (.ogg/.wav/..., format is detected by soundfile)
        expected_fs  if given and it doesn't match the file's sample rate, print
                     a warning (not enforced)
        start_sec    if given, only decode from this time onward (seconds,
                     clamped to [0, file duration]) -- for long recordings,
                     this is what actually saves time: soundfile seeks and
                     decodes just the requested range instead of the whole
                     file, rather than reading everything and slicing after.
        end_sec      if given, stop decoding at this time (seconds); None = to
                     end of file. Both None (the default) preserves the exact
                     old behavior: read the entire file, no probing step.

    Raises: ImportError with a hint if soundfile isn't installed (tells the
            caller to `pip install soundfile`, or use read_raw_f32 instead).
    """
    try:
        import soundfile as sf
    except ImportError as exc:  # noqa: BLE001
        raise ImportError(
            "Reading .ogg requires soundfile. Please `pip install soundfile`, "
            "or use read_raw_f32 to read a pre-decoded .f32 file instead."
        ) from exc

    if start_sec is not None or end_sec is not None:
        info = sf.info(path)
        n_total = info.frames
        start = max(0, int((start_sec or 0.0) * info.samplerate))
        stop = n_total if end_sec is None else min(n_total, int(end_sec * info.samplerate))
        data, fs = sf.read(path, start=start, stop=stop, dtype="float64", always_2d=True)
    else:
        data, fs = sf.read(path, dtype="float64", always_2d=True)
    samples = np.asarray(data[:, 0], dtype=np.float64)   # take channel 0
    if expected_fs is not None and fs != expected_fs:
        print(f"[audio_io] warning: file sample rate {fs} Hz differs from "
              f"expected {expected_fs} Hz -- check your baud/sps settings.")
    return samples, fs


def read_raw_f32(path):
    """
    Read a raw little-endian float32 file -> 1D float64 array.

    A fallback for when soundfile isn't available: decode the .ogg with some
    other tool into a .f32 file first, then read it with this function.
    """
    return np.fromfile(path, dtype="<f4").astype(np.float64)
