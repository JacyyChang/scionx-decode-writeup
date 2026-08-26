#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
symbol_sync_sweep.py -- grid-search digital.symbol_sync_ff's timing-recovery
parameters against one wav file, scored OFFLINE so every combination gets a
graded result instead of a binary pass/fail.

Why offline scoring instead of running the real satellites.hdlc_deframer
once per combo: on a weak/borderline signal, "did a frame come out" is
almost always "no" for the wrong parameters, which gives no sense of
"getting warmer" across a large grid. Instead, each combo's recovered bits
are deframed OFFLINE in pure Python -- a bit-exact replay of
satellites.hdlc_deframer.work()'s flag/destuff state machine and LSB-first
byte packing (LSB-first confirmed as the correct on-wire order by
hdlc_bitorder_test.grc, see this folder's README) -- so every combo reports
flag count / near-length candidate count / actual CRC-16 passes. Only the
final winner needs to be re-confirmed through the real
satellites.hdlc_deframer block (--verify).

Validated on 2026-08-25 against
Output/20260723_091639_seg017_510-540s.wav: 96-combo grid found
(M&M, loop_bw=0.045, damping=0.7, max_dev=1.5) as the only CRC-passing
combo, independently confirmed against the real satellites.hdlc_deframer --
see appendix_gnuradio/README.md's "seg017 decoded" section for the full
writeup, including why Gardner needs the subprocess+timeout design below
(it hangs indefinitely on some parameter combos, on this signal, confirmed
unkillable from inside its own process/thread).

Usage (run with the Python that has gnuradio + gr-satellites, e.g. the
project's radioconda install -- see this folder's README):

    python symbol_sync_sweep.py Output/some_segment.wav
    python symbol_sync_sweep.py Output/some_segment.wav --ted MM GMSK --timeout 10
    python symbol_sync_sweep.py Output/some_segment.wav --frame-len 274 --near-slack 15
    python symbol_sync_sweep.py Output/some_segment.wav --verify   # after a sweep, re-run
                                                                    # the top hit through the
                                                                    # real hdlc_deframer

Resumable: re-running with the same --out just skips combos already present
in the .jsonl, so a run interrupted by Ctrl-C or a timeout can continue.
"""
import argparse
import itertools
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
from scionx.hdlc import crc16_x25  # noqa: E402

# TED types available on digital.symbol_sync_ff in this GNU Radio build.
# MM/GARDNER/EARLY_LATE/ZERO_CROSSING are general-purpose; GMSK/GENMSK are
# specifically tuned for GMSK/GFSK-family modulations (this project's signal
# is GFSK/9600) but did not win the seg017 sweep -- still worth trying on
# other data since the "right" detector can be signal-dependent.
TED_CHOICES = [
    "MM", "MOD_MM", "GARDNER", "EARLY_LATE", "ZERO_CROSSING", "GMSK", "GENMSK",
]


def _ted_value(name):
    # deferred import so --help works without gnuradio installed
    from gnuradio import digital
    return {
        "MM": digital.TED_MUELLER_AND_MULLER,
        "MOD_MM": digital.TED_MOD_MUELLER_AND_MULLER,
        "GARDNER": digital.TED_GARDNER,
        "EARLY_LATE": digital.TED_EARLY_LATE,
        "ZERO_CROSSING": digital.TED_ZERO_CROSSING,
        "GMSK": digital.TED_MENGALI_AND_DANDREA_GMSK,
        "GENMSK": digital.TED_DANDREA_AND_MENGALI_GEN_MSK,
    }[name]


def pack(bits):
    """Same LSB-first byte packing as satellites.hdlc_deframer.pack()."""
    n = len(bits) - (len(bits) % 8)
    d = bytearray(n // 8)
    for i in range(0, n, 8):
        x = 0
        for j in range(7, -1, -1):
            x = (x << 1) | bits[i + j]
        d[i // 8] = x
    return bytes(d)


def fcs_ok(frame):
    if len(frame) <= 2:
        return False
    out = crc16_x25(frame[:-2])
    return frame[-2] == (out & 0xff) and frame[-1] == ((out >> 8) & 0xff)


def offline_deframe(bits):
    """Bit-exact replay of satellites.hdlc_deframer.work()'s state machine
    (flag detection, bit-destuffing, frame packing) -- see
    C:\\...\\radioconda\\Lib\\site-packages\\satellites\\hdlc_deframer.py for
    the original this mirrors."""
    frames = []
    buf = []
    ones = 0
    for x in bits:
        if x:
            ones += 1
            buf.append(1)
        else:
            if ones == 5:
                pass  # stuffed 0, drop it
            elif ones > 5:
                for _ in range(min(7, len(buf))):
                    buf.pop()
                pad = (-len(buf)) % 8
                if pad:
                    buf = [0] * pad + buf
                if buf:
                    frame = pack(buf)
                    if frame:
                        frames.append(frame)
                buf = []
            else:
                buf.append(0)
            ones = 0
    return frames


def score_bits(bits, near_lo, near_hi):
    """Best-of-both-polarities score for one bit array (FM-demod baseline
    polarity can come out either way)."""
    best = None
    for label, b in (("norm", bits), ("inv", 1 - bits)):
        frames = offline_deframe(b)
        near = [f for f in frames if near_lo <= len(f) <= near_hi]
        passed = [f for f in near if fcs_ok(f)]
        s = dict(polarity=label, n_frames=len(frames), n_near=len(near),
                 n_pass=len(passed),
                 best_near_len=(min(near, key=lambda f: len(f)).__len__()
                                if near else None))
        if (best is None
                or s["n_pass"] > best["n_pass"]
                or (s["n_pass"] == best["n_pass"] and s["n_near"] > best["n_near"])):
            best = s
    return best


def run_symbol_sync(wav, ted_type, sps, loop_bw, damping, ted_gain, max_dev, osps=1):
    from gnuradio import gr, blocks, digital
    import numpy as np
    tb = gr.top_block()
    src = blocks.wavfile_source(wav, False)
    ss = digital.symbol_sync_ff(
        ted_type, sps, loop_bw, damping, ted_gain, max_dev, osps,
        digital.constellation_bpsk().base(), digital.IR_MMSE_8TAP, 128, [])
    slicer = digital.binary_slicer_fb()
    sink = blocks.vector_sink_b()
    tb.connect(src, ss, slicer, sink)
    tb.run()
    return np.frombuffer(bytes(sink.data()), dtype=np.uint8)


def verify_real_deframer(wav, ted_type, sps, loop_bw, damping, ted_gain, max_dev):
    """Stage 2: re-run one combo through the REAL satellites.hdlc_deframer +
    blocks.message_debug, headless (no GUI, no custom Python streaming
    block), for independent confirmation of an offline-scored hit."""
    from gnuradio import gr, blocks, digital
    import satellites
    import pmt
    tb = gr.top_block()
    src = blocks.wavfile_source(wav, False)
    ss = digital.symbol_sync_ff(
        ted_type, sps, loop_bw, damping, ted_gain, max_dev, 1,
        digital.constellation_bpsk().base(), digital.IR_MMSE_8TAP, 128, [])
    slicer = digital.binary_slicer_fb()
    deframer = satellites.hdlc_deframer(check_fcs=True, max_length=10000)
    msg_dbg = blocks.message_debug(True, gr.log_levels.info)
    tb.connect(src, ss, slicer, deframer)
    tb.msg_connect((deframer, "out"), (msg_dbg, "store"))
    tb.run()
    frames = []
    for i in range(msg_dbg.num_messages()):
        frames.append(bytes(pmt.u8vector_elements(pmt.cdr(msg_dbg.get_message(i)))))
    return frames


def key(row):
    # ted_gain was added as a swept dimension later; rows written before that
    # have no such field and were all produced at the old fixed default of
    # 1.0, so default to 1.0 rather than dropping those results on resume.
    return (row["ted"], row["loop_bw"], row["damping"], row["max_dev"],
            row.get("ted_gain", 1.0))


def load_done(jsonl_path):
    done = {}
    if os.path.exists(jsonl_path):
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Only a genuine "timeout" (a real hang, worth remembering so
                # a resume doesn't hang again) counts as done. Any other
                # error (e.g. the wrong Python / missing gnuradio) is an
                # environment failure, not a result about the combo -- retry
                # it once the environment is fixed instead of skipping it
                # forever. Later lines override earlier ones for the same
                # key, so a fixed-environment rerun naturally supersedes an
                # old failed attempt without needing to edit the .jsonl.
                if row.get("error") not in (None, "timeout"):
                    done.pop(key(row), None)
                    continue
                done[key(row)] = row
    return done


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav", help="input .wav (float, mono -- e.g. IQtoOgg_plot.grc's "
                                 "48k demodulated-audio output)")
    ap.add_argument("--frame-len", type=int, default=274,
                     help="expected destuffed frame length in bytes, payload+2-byte FCS "
                          "(default 274 = REFERENCE_FRAME.md's 272-byte payload + FCS)")
    ap.add_argument("--near-slack", type=int, default=20,
                     help="+/- byte slack around --frame-len counted as a 'near miss' "
                          "candidate (default 20)")
    ap.add_argument("--sps", type=float, default=5.0,
                     help="samples per symbol (default 5.0 = 48000/9600)")
    ap.add_argument("--ted-gain", type=float, nargs="+", default=[1.0],
                     help="TED gain (default 1.0). symbol_sync_ff uses this to "
                          "normalize out the detector's expected slope when deriving "
                          "loop coefficients, so it scales the EFFECTIVE loop bandwidth: "
                          "with a slicer making +/-1 decisions, the M&M error is "
                          "proportional to input amplitude, so a signal whose RMS is A "
                          "behaves as if loop_bw were A times larger than requested.")
    ap.add_argument("--loop-bw", type=float, nargs="+", default=[0.01, 0.045, 0.08, 0.15])
    ap.add_argument("--damping", type=float, nargs="+", default=[0.7, 1.0, 1.3])
    ap.add_argument("--max-dev", type=float, nargs="+", default=[1.0, 1.5, 2.5])
    ap.add_argument("--ted", nargs="+", choices=TED_CHOICES,
                     default=["MM", "GARDNER", "GMSK", "GENMSK"])
    ap.add_argument("--timeout", type=float, default=15.0,
                     help="per-combo subprocess timeout in seconds (default 15) -- "
                          "TED_GARDNER is known to hang indefinitely on some combos")
    ap.add_argument("--out", default=None,
                     help="results .jsonl path (default: <wav>_symsync_sweep.jsonl)")
    ap.add_argument("--top", type=int, default=15, help="leaderboard rows to print")
    ap.add_argument("--no-resume", action="store_true",
                     help="ignore/overwrite any existing --out instead of skipping "
                          "already-completed combos")
    ap.add_argument("--verify", action="store_true",
                     help="skip the sweep; re-run the current best combo from --out "
                          "through the real satellites.hdlc_deframer and print the result")
    # internal: one combo, invoked as a subprocess by the driver so a hung
    # combo (see TED_GARDNER above) can be killed without taking the whole
    # sweep down with it -- not meant to be passed by hand.
    ap.add_argument("--_one", nargs=5,
                     metavar=("TED", "LOOP_BW", "DAMPING", "MAX_DEV", "TED_GAIN"),
                     help=argparse.SUPPRESS)
    return ap


def _ensure_gnuradio():
    """Fail fast with one clear message instead of letting every subprocess
    combo silently fail with the same ImportError -- happened 2026-08-26 when
    this script was launched with plain `python` instead of the radioconda
    interpreter that actually has gnuradio + gr-satellites installed. The
    driver re-invokes itself via sys.executable, so whichever Python starts
    this script is the one every combo (and --verify) runs under."""
    try:
        import gnuradio  # noqa: F401
    except ImportError:
        print(
            f"ERROR: {sys.executable!r} has no `gnuradio` module.\n"
            "This script needs the Python that has gnuradio + gr-satellites "
            "installed (this project's radioconda environment) -- run it from "
            "a shell where that install's `python` is the one on PATH, or "
            "invoke this script with that installation's python.exe directly, "
            "e.g.:\n"
            "    <path to radioconda>\\python.exe symbol_sync_sweep.py ...",
            file=sys.stderr)
        sys.exit(1)


def main():
    args = build_parser().parse_args()
    _ensure_gnuradio()
    wav = os.path.abspath(args.wav)
    out = args.out or (os.path.splitext(wav)[0] + "_symsync_sweep.jsonl")
    near_lo, near_hi = args.frame_len - args.near_slack, args.frame_len + args.near_slack

    if args._one:
        ted_name, lb, damp, md, tg = args._one
        lb, damp, md, tg = float(lb), float(damp), float(md), float(tg)
        try:
            bits = run_symbol_sync(wav, _ted_value(ted_name), args.sps, lb, damp,
                                    tg, md)
            s = score_bits(bits, near_lo, near_hi)
            row = dict(ted=ted_name, loop_bw=lb, damping=damp, max_dev=md, ted_gain=tg,
                       n_out_bits=int(bits.size), **s)
        except Exception as e:
            row = dict(ted=ted_name, loop_bw=lb, damping=damp, max_dev=md, ted_gain=tg,
                       error=str(e))
        with open(out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
        return

    if args.verify:
        done = load_done(out)
        ok = [r for r in done.values() if "error" not in r]
        if not ok:
            print(f"No completed combos in {out} -- run the sweep first.")
            return
        ok.sort(key=lambda r: (-r["n_pass"], -r["n_near"]))
        best = ok[0]
        print(f"Verifying best combo via the real satellites.hdlc_deframer: {best}")
        frames = verify_real_deframer(wav, _ted_value(best["ted"]), args.sps,
                                       best["loop_bw"], best["damping"],
                                       best.get("ted_gain", 1.0), best["max_dev"])
        print(f"\nmessages received: {len(frames)}")
        for i, data in enumerate(frames):
            print(f"\nmessage {i}: {len(data)} bytes")
            for j in range(0, len(data), 16):
                print(f"{j:04x}: " + data[j:j + 16].hex(' '))
        return

    combos = list(itertools.product(args.ted, args.loop_bw, args.damping, args.max_dev,
                                     args.ted_gain))
    done = {} if args.no_resume else load_done(out)
    if args.no_resume and os.path.exists(out):
        os.remove(out)
    todo = [c for c in combos if c not in done]
    print(f"{len(combos)} total combos, {len(done)} already done, {len(todo)} to run "
          f"-> {out}", flush=True)

    t0 = time.time()
    for i, (ted, lb, damp, md, tg) in enumerate(combos):
        if (ted, lb, damp, md, tg) in done:
            continue
        t1 = time.time()
        cmd = [sys.executable, "-u", os.path.abspath(__file__), wav,
               "--sps", str(args.sps),
               "--out", out, "--_one", ted, str(lb), str(damp), str(md), str(tg)]
        try:
            proc = subprocess.run(cmd, timeout=args.timeout, capture_output=True, text=True)
            dt = time.time() - t1
            if proc.returncode != 0:
                print(f"[{i+1}/{len(combos)}] {ted:8s} lb={lb:<6} damp={damp:<4} "
                      f"md={md:<4} tg={tg:<5} NONZERO EXIT {proc.returncode} ({dt:.1f}s) "
                      f"stderr_tail={proc.stderr[-300:]!r}", flush=True)
                with open(out, "a") as f:
                    f.write(json.dumps(dict(ted=ted, loop_bw=lb, damping=damp, max_dev=md,
                                             ted_gain=tg,
                                             error=f"nonzero exit {proc.returncode}: "
                                                   f"{proc.stderr[-300:]}")) + "\n")
            else:
                print(f"[{i+1}/{len(combos)}] {proc.stdout.strip()} ({dt:.1f}s)", flush=True)
        except subprocess.TimeoutExpired:
            dt = time.time() - t1
            print(f"[{i+1}/{len(combos)}] {ted:8s} lb={lb:<6} damp={damp:<4} "
                  f"md={md:<4} tg={tg:<5} TIMEOUT after {dt:.1f}s -- killed, skipping",
                  flush=True)
            with open(out, "a") as f:
                f.write(json.dumps(dict(ted=ted, loop_bw=lb, damping=damp, max_dev=md,
                                         ted_gain=tg, error="timeout")) + "\n")

    print(f"\nSweep done in {time.time()-t0:.1f}s", flush=True)

    final = load_done(out)
    ok = [r for r in final.values() if "error" not in r]
    ok.sort(key=lambda r: (-r["n_pass"], -r["n_near"]))
    print(f"\n=== Top {args.top} (by n_pass, then n_near) ===")
    for r in ok[:args.top]:
        print(r)
    passing = [r for r in ok if r["n_pass"] > 0]
    if passing:
        best = passing[0]
        print(f"\n>>> {len(passing)} combo(s) with a CRC pass. Best:")
        print(f"    ted={best['ted']}  loop_bw={best['loop_bw']}  "
              f"damping={best['damping']}  max_dev={best['max_dev']}  "
              f"ted_gain={best.get('ted_gain', 1.0)}  "
              f"sps={args.sps}  (polarity={best['polarity']}, "
              f"frame_len={best['best_near_len']})")
        if len(passing) > 1:
            print("    other passing combos:")
            for r in passing[1:]:
                print(f"    ted={r['ted']}  loop_bw={r['loop_bw']}  "
                      f"damping={r['damping']}  max_dev={r['max_dev']}  "
                      f"ted_gain={r.get('ted_gain', 1.0)}")
        print("    Re-run with --verify to confirm this against the real "
              "satellites.hdlc_deframer.")
    else:
        print("\n>>> No combo passed CRC. Try widening --loop-bw/--damping/--max-dev, "
              "or a different --frame-len if this isn't a 272-byte-payload AX.25 frame.")


if __name__ == "__main__":
    main()
