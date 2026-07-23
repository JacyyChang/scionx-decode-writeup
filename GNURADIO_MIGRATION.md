# GNU Radio Migration Proposal (Draft v2) — forked by segment type

> **Status: draft for review, not implemented.** Proposes one way to move the `scionx/`
> diagnostic pipeline (see the main [README.md](README.md)) into a GNU Radio Companion (GRC)
> flowgraph, once a decision method for the data/zero-run segments is settled.

## Why this shape

GNU Radio's stream abstraction is built for continuous, per-sample, real-time processing.
Two of the three diagnostic folders in this repo are exactly that kind of thing — but the
middle one isn't:

- `01_frame_detection/`'s normalized cross-correlation needs to see a whole candidate window
  before it can decide anything — not a per-sample operation.
- `scionx/baseline.py`'s `restore_baseline` re-scans a whole ~2001-sample window, seven times,
  per frame — an iterative, whole-buffer algorithm, not a causal filter.
- `02_zero_run_baseline/` and `03_data_segment_processing/` each apply a *different* decision
  method, to a *different* subset of the frame's bytes. FCS (the 2-byte CRC) rides along with
  the data segments rather than getting its own pass-through: its bits look like arbitrary
  signal content, not the all-zero padding `zero_run_processor` is tuned for, so
  `data_segment_processor`'s asymmetry-corrected threshold is the better fit for it too.

So instead of forcing everything into per-sample streaming blocks, the flowgraph below brackets
the frame-level diagnostic work off as a **per-frame message/PDU stage**, sandwiched between a
real-time streaming front end and a real-time streaming back end (`hdlc_deframer`, CRC).
Inside that message stage, the frame is **forked by segment type** — data segments go to the
processor built for them, zero-run segments go to the processor built for them — then merged
back into one ordered bit stream before CRC.

## Flowgraph

```mermaid
flowchart TD
    subgraph ROW1["① Streaming domain — continuous, real-time"]
        A["wavfile_source<br/><small>Fs=48000, cut_first3.wav</small>"]
        B["low_pass_filter<br/><small>band-limit</small>"]
        C["symbol_sync<br/><small>Gardner/M&amp;M TED, osps=1</small>"]
        D["frame_detector<br/><small>normalized xcorr vs. flag/preamble<br/>ports 01_frame_detection; adds tags</small>"]
        E["tagged_stream_to_pdu<br/><small>stream → PDU bridge</small>"]
        A --> B --> C --> D --> E
    end

    subgraph ROW2["② Message/PDU domain — forked by segment type, then merged"]
        F["segment_splitter<br/><small>fixed byte-offset map from 01_frame_detection:<br/>address 0–13 / data1 14–74 / zero-run1 75–157<br/>/ data2 158–205 / zero-run2 206–271 / FCS 272–273</small>"]
        G["data_segment_processor<br/><small>wraps 03_data_segment_processing:<br/>fixed-offset threshold, calibrated<br/>from this frame's own known header bits</small>"]
        H["zero_run_processor<br/><small>wraps 02_zero_run_baseline:<br/>decision-directed restore + isolated-1<br/>flagging, zero-run1 ⊕ zero-run2 pooled</small>"]
        I["segment_merger<br/><small>new — reassembles address+data1+zr1<br/>+data2+zr2+FCS, original byte order</small>"]
        F -- "data1+data2+FCS" --> G
        F -- "zero-run1+zero-run2" --> H
        F -. "address+ctrl+PID (known) — pass-through" .-> I
        G -- "decided bits" --> I
        H -- "decided bits" --> I
    end

    subgraph ROW3["③ Streaming domain — bit-level destuffing + CRC"]
        J["pdu_to_tagged_stream<br/><small>PDU → stream bridge</small>"]
        K["hdlc_deframer<br/><small>gr-satellites<br/>flag detect + destuff + CRC-16/X.25</small>"]
        L["message_debug / file_sink<br/><small>decoded AX.25 frames, CRC-pass only</small>"]
        J --> K --> L
    end

    E --> F
    I --> J

    M["qt_gui_time_sink<br/><small>raw baseband</small>"]
    N["qt_gui_time_sink<br/><small>symbol-rate signal</small>"]
    O["frame_plotter<br/><small>reassembled bits + per-segment overlay<br/>mirrors 02_/03_ PNG figures</small>"]
    B -.-> M
    C -.-> N
    I -.-> O

    classDef nativeGR fill:#DCE9F5,stroke:#2B6CB5,color:#1C4A78;
    classDef satellites fill:#DCF0E7,stroke:#2F8F6F,color:#1E5F49;
    classDef custom fill:#FBE9D4,stroke:#C4741A,color:#8A4E10;
    classDef viz fill:#EEEDF2,stroke:#7B7B8C,stroke-dasharray: 4 3,color:#4B4B5A;

    class A,B,C,E,J,L nativeGR
    class D,F,G,H,I custom
    class K satellites
    class M,N,O viz
```

**Legend**: blue = stock GNU Radio block, unmodified · green = `gr-satellites` (already used) ·
amber = custom Python block that reuses `scionx/*.py` numpy code as-is · grey dashed =
visualization/debug sink, not on the decode path · solid arrow = stream connection
(continuous) · dashed arrow = message/PDU connection (one shot per frame).

## Block reference

| Block | Kind | Role |
|---|---|---|
| `wavfile_source` | native GR | Reads `cut_first3.wav` (pre-converted once from `.ogg`), Fs=48000 Hz |
| `low_pass_filter` | native GR | Band-limits before timing recovery |
| `symbol_sync` | native GR | Gardner/M&M TED, `osps=1` — replaces the current fixed `PHASE`/`SPS` sampling (see README "Possible future updates") |
| `frame_detector` | custom | Ports `01_frame_detection/`'s normalized cross-correlation; on detection, tags `frame_start`/`frame_len` |
| `tagged_stream_to_pdu` | native GR | Buffers samples between tags into one PDU per detected frame |
| `segment_splitter` | custom | Applies the fixed byte-offset segment map (same one `01_frame_detection/` derives from `REFERENCE_FRAME.md`) to route each span; address is the only span still passed straight through |
| `data_segment_processor` | custom | Wraps `03_data_segment_processing/`'s fixed-offset threshold + asymmetry check; also carries the FCS bytes (272–273), treated as data rather than pass-through |
| `zero_run_processor` | custom | Wraps `02_zero_run_baseline/`'s decision-directed baseline restoration + isolated-1 flagging (zero-run1/zero-run2 pooled, as the current script does) |
| `segment_merger` | custom, new | The one genuinely new piece — reassembles all spans into one bit stream in original byte order |
| `pdu_to_tagged_stream` | native GR | Converts the merged decided-bit PDU back into a bit stream |
| `hdlc_deframer` | gr-satellites | Real flag detection + bit-destuffing + CRC-16/X.25 (existing OOT module) |
| `message_debug` / `file_sink` | native GR | Emits only when a frame passes CRC |
| `qt_gui_time_sink` ×2, `frame_plotter` | viz/debug | Live inspection taps; `frame_plotter` mirrors the current `02_`/`03_` static PNG figures |

## Open question

The fork in `segment_splitter` runs on a **fixed** byte-offset map, derived once from
`REFERENCE_FRAME.md`'s ground-test frame. That's valid only if every frame from this satellite
keeps telemetry fields at the same byte positions. If the layout ever shifts between frames,
`segment_splitter` would silently route bytes to the wrong processor *before* either
`data_segment_processor` or `zero_run_processor` gets a chance to run — worth confirming this
assumption holds across more than the 2–3 frames currently on hand.
