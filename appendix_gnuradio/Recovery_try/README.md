# Recovery_try — feedforward clock-tone timing recovery (hybrid attempt)

A "last attempt" at decoding the two hard segments (seg010, seg007) by
combining the two things that each worked in isolation: the **fixed grid**
(which decodes seg017/seg002 but assumes exactly sps=5.0) and **timing
recovery** (which every real modem uses, but whose closed-loop PLL form was
*worse* than the fixed grid on all four segments here — it free-runs through
the long all-zero HDLC padding runs where the TED gets no error signal).

The hybrid is **feedforward, one-shot, open-loop**: estimate the symbol
period `T` and sampling phase `φ` once over the whole frame, then apply
`idx(n) = φ + T·n` as a corrected fixed grid. No feedback loop, no
Communications Toolbox.

## Files

| file | what it does |
|---|---|
| `clock_tone_locate.m` | the estimator. Squared-derivative Oerder–Meyr clock-tone recovery: `g = (Δx)²`, scan `S(T)=Σ g·e^{-j2π·pos/T}`, `T̂ = argmax\|S(T)\|`, phase from `∠S(T̂)`. 6-panel diagnostic + JSON per segment. |
| `decode_with_grid.py` | applies a `(sps, phase, polarity)` grid (from the JSON) to a wav, slices (fixed **or** min-max dynamic threshold), runs the HDLC deframer + CRC-16/X.25. Generalizes `../print_seg002_decode.py`. |
| `seg010_header_ber.py` | quantifies how close seg010 is: counts header Hamming distance (first 16 bytes are constant) of every 274-byte candidate. |
| `clock_tone_<seg>.png/.fig` | 6-panel diagnostics per segment. |
| `clock_tone_<seg>.json` | `{sps_est, phase_sample, polarity, z, ppm, opening_*}` handoff to the decoder. |

## Method (why squared-derivative, not `|x|²`)

A rectangular ±NRZ waveform has a spectral **null** at the symbol rate (its
PSD is sinc²), so there is no line to lock onto directly; `|x|²` is nearly
constant for ±A NRZ and regenerates nothing. The **squared derivative**
`(dx/dt)²` pulses at every transition and produces a strong line at `1/T`.
Flat zero-runs contribute `g≈0`, so they are weighted out automatically — no
gating logic needed. The estimator is exactly **amplitude-scale-invariant**
(immune to the DetectorGain bug that plagued the PLL) and every pick position
is a closed-form number (immune to the rate-changing-block position-mapping
bug). The `|S(T)|` curve, with a z-score against its own background, is also
the deliverable: it says whether a recoverable clock **exists** at all.

## Results

| segment | T̂ (samples/sym) | ppm | clock z | drift/frame | fixed eye | hybrid eye | CRC |
|---|---|---|---|---|---|---|---|
| seg017 (known-good) | 5.00074 | +149 | **7.0** | 1.64 | +5.77 | +5.52 | **PASS** |
| seg002 (known-good) | 4.99982 | −36  | **7.7** | −0.39 | +1.96 | +1.83 | **PASS** |
| seg010 | 4.99941 | −119 | 4.1 | −1.31 | +0.14 | +0.04 | fail |
| seg007 | 4.96483 | −7034 | 4.2 | −77 (garbage) | +0.13 | +0.07 | fail |

**Gate 1 (estimator validity):** the two known-good segments give a clear
clock line at `z≈7–7.7` with `T̂` within ±150 ppm of nominal; the two hard
segments sit at `z≈4` — a real, if modest, separation. Known-good clock
tones are ~2× above the hard ones.

**Gate 2 (non-regression):** `decode_with_grid.py` re-decodes **both**
seg017 and seg002 through the hybrid grid — 274-byte CRC-16/X.25 pass,
header byte-identical to REFERENCE_FRAME.md (`BN0CU`/`BN0SCX`), at
`sps=5.00074` / `4.99982` respectively (i.e. **not** exactly 5.0). The
hybrid does not regress.

**Gate 3 (hard segments):** neither passes CRC, but the *reason differs* and
is now pinned down:

- **seg007 — no recoverable clock.** Panel 1 shows no line above the
  background; the "peak" at `T=4.9648` is a false pick (`z=4.2`, split-half
  `T_first=4.900` pinned at the grid edge vs `T_second=5.056` — wildly
  inconsistent). Both eye diagrams are fully closed and the waveform has no
  clean ±rail structure. **This region is noise / SNR-limited; there is no
  frame timing to recover.**

- **seg010 — timing exists, but the eye is closed (SNR-limited).** A real
  clock line sits at `T̂≈5.000` (nominal, split-half consistent), so timing
  is **not** the blocker — but recovering timing is not enough. The waveform
  also rides on a large DC offset (amplitude **−8 to −10, not 0** — the DC
  step in `../header_correlate_locate.m`'s comment), so a fixed threshold-at-0
  mis-slices; but the min-max **dynamic** slicer removes that offset and the
  bits are *still* wrong. `seg010_header_ber.py` is the decisive test: the
  first 16 header bytes are constant, and across 26 recovered 274-byte
  candidates (any grid / any slicer / both polarities) the **best header is
  53 / 128 bits wrong — 41%, i.e. near-random**, every header byte wrong. So
  the 274-byte length matches are the destuffer finding flag-like patterns in
  noise, **not** a real frame with a few correctable errors. Baseline
  restoration is worth trying but on this evidence the eye is closed from
  **SNR**, and clean bits are unlikely to fall out of it.

## Verdict

Feedforward clock-tone recovery is the *correct* timing method for this
signal (it beats the PLL and matches the fixed grid on known-good data, and
it correctly recovers a slightly-off-nominal `T` — seg017 is genuinely at
+149 ppm). But **timing was never what blocked seg010/seg007.** The clock-
tone z-score cleanly separates the two failures: seg007 has no clock line at
all (dead/noise), while seg010 has a real clock line but a closed eye — its
best-recovered header is 41% wrong (near-random) even after baseline-tracking
dynamic slicing, so it is **SNR-limited**, not timing-limited. Baseline
restoration (`../../02_zero_run_baseline/`, `scionx/baseline.py`) is the one
remaining lever worth pulling on seg010, but the 41% header BER says the eye
is unlikely to open enough for a CRC pass. **Bottom line: neither hard
segment is blocked by timing; more timing work will not decode them.**

## Reproduce

```matlab
cd D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Recovery_try
clock_tone_locate          % writes clock_tone_<seg>.png/.fig/.json
```
```bash
cd Recovery_try
python decode_with_grid.py ../Output/20260723_091639_seg017_510-540s.wav \
    --json clock_tone_20260723_091639_seg017_510-540s.json          # PASS
python decode_with_grid.py ../Output/20260723_091639_seg010_300-330s.wav \
    --json clock_tone_20260723_091639_seg010_300-330s.json \
    --slicer both --scan-phase 2.5 --scan-phase-step 0.25 --dyn-window 16   # fail
python seg010_header_ber.py                                          # how close is seg010?
```

## Two bugs NOT to reintroduce (they cost a round of invalid results earlier)

1. **`comm.SymbolSynchronizer` needs unit-scale input** — these wavs are raw
   floats (RMS 9–11); the PLL's DetectorGain assumes ~1.0, so raw input made
   it track nothing. *This feedforward estimator is scale-invariant and does
   not care — but if anyone falls back to the PLL, it matters.*
2. **`comm.SymbolSynchronizer` is a rate-changing block with no per-symbol
   position output** — the naive `symbol k ↔ sample k·N/M` map drifts and
   puts picks off the waveform; a strobe lag of ~+1.5 samples plus a
   per-symbol `mu` refinement is needed. *This estimator's picks are
   `φ+T·n` in closed form — immune.*
