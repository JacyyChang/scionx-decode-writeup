# -*- coding: utf-8 -*-
"""
run_02_03_on_segment.py -- apply 02_zero_run_baseline / 03a_data_asymmetry_check /
03b_data_fixed_offset_threshold's per-frame analysis to the GNU-Radio-captured
candidate frame in Figure/20260720_220206_seg007_235-245s.wav, instead of their
hardcoded cut_first3.ogg pipeline.

Why a separate driver instead of editing those three scripts: they have no CLI
at all (AUDIO/Z_THRESHOLD are hardcoded module constants, unlike 01/01a/01b/04/
04a-04d's uniform CLI), and this recording's frame doesn't clear their hardcoded
Z_THRESHOLD=12.0 (max z found was 5.74 -- see appendix_gnuradio's investigation
log). The frame start (sample 182707 in this file) was instead found by forcing
04d_destuff_interactive_ver2.py --z-threshold 5.5, which decoded it with 30/1320
mismatches at verifiable (header/padding) bit positions -- far better than chance,
so it's very likely a real frame, just weaker than cut_first3.ogg's.

Rather than hand-editing three README-documented, checked-in scripts to bolt on
a frame-start override (risking their cut_first3.ogg example output and verified
formulas), this driver imports their already-decoupled per-frame functions
directly -- get_zero_run_segments/plot_frame (02), calibrate_offset_threshold/
get_data_segments/plot_frame (03a, 03b) -- and calls them with the known frame
start. Zero duplicated math; the three canonical scripts are untouched.

Output: appendix_gnuradio/Output/{02,03a,03b}_frame1.png
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from scionx import audio_io, baseline   # noqa: E402
from _style import setup_mpl            # noqa: E402

AUDIO = os.path.join(HERE, "Figure", "20260720_220206_seg007_235-245s.wav")
FRAME_START = 182707   # sample index within AUDIO; found via 04d_destuff_interactive_ver2.py --z-threshold 5.5
Z_SCORE = 5.74          # this recording's actual detection z-score -- forced, below these scripts' own 12.0 threshold
OUT_DIR = os.path.join(HERE, "Output")
FS = 48000


def load_module(name, relpath):
    """Load one of the numeric-prefixed 02/03 scripts as an importable module
    (their filenames aren't valid `import` identifiers) without touching them."""
    path = os.path.join(REPO_ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def redirect_to_output(mod):
    """These scripts hardcode save(fig, os.path.join("Figure", ...)). `save` is
    looked up as a module global at call time (not bound at def time), so
    rebinding mod.save after import safely redirects every plot_frame() call
    from that module into appendix_gnuradio/Output/ instead of ./Figure/."""
    orig_save = mod.save

    def save_to_output(fig, path):
        name = os.path.basename(path)
        return orig_save(fig, os.path.join(OUT_DIR, name))
    mod.save = save_to_output


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    plt = setup_mpl()
    y, fs = audio_io.read_audio(AUDIO, expected_fs=FS)
    bl = baseline.restore_baseline(y, num_iters=7, W=1000)
    yc = bl["y_comp_final"]

    print(f"Forcing frame_start={FRAME_START} (z={Z_SCORE}, below the 12.0 auto-detect "
          "threshold -- found via 04d_destuff_interactive_ver2.py --z-threshold 5.5)")

    print("\n" + "#" * 30 + " 02_zero_run_baseline " + "#" * 30)
    m02 = load_module("m02_zero_run_baseline", "02_zero_run_baseline/02_zero_run_baseline.py")
    redirect_to_output(m02)
    zero_runs = m02.get_zero_run_segments(yc, FRAME_START)
    m02.plot_frame(plt, yc, FRAME_START, 1, Z_SCORE, zero_runs)

    print("\n" + "#" * 30 + " 03a_data_asymmetry_check " + "#" * 30)
    m03a = load_module("m03a_data_asymmetry_check", "03_data_segment_processing/03a_data_asymmetry_check.py")
    redirect_to_output(m03a)
    offset_thr, v1h, v0h, hdr_err = m03a.calibrate_offset_threshold(y, FRAME_START)
    data_segs = m03a.get_data_segments(y, FRAME_START)
    m03a.plot_frame(plt, y, FRAME_START, 1, Z_SCORE, data_segs, offset_thr, v1h, v0h)

    print("\n" + "#" * 30 + " 03b_data_fixed_offset_threshold " + "#" * 30)
    m03b = load_module("m03b_data_fixed_offset_threshold",
                        "03_data_segment_processing/03b_data_fixed_offset_threshold.py")
    redirect_to_output(m03b)
    offset_thr_b, hdr_err_b = m03b.calibrate_offset_threshold(y, FRAME_START)
    data_segs_b = m03b.get_data_segments(y, FRAME_START)
    m03b.plot_frame(plt, y, FRAME_START, 1, Z_SCORE, data_segs_b, offset_thr_b, hdr_err_b)

    print(f"\nDone. See {OUT_DIR}")


if __name__ == "__main__":
    main()
