# Open-BEATBox GUI Front-End Demo

This is a hardware-free Python mock-up of the Open-BEATBox graphical interface. It is
intended for design review and software planning before the real hardware/API
integration is finalized.

The mock is deliberately faithful to the two reference documents in this folder:

- **`TECHNICAL_GUIDE.md`** — architecture, the explicit connection-state
  contract, the USB serial protocol (115200 baud framed `ready`/`set`/`ack`/
  `state` messages with CRC-8), and the `pins_config.cfg` bit-ordered actuator
  mask.
- **`RECORDING_OUTPUTS.md`** — the FULL event CSV and trial-summary CSV schemas,
  the per-stage metrics, and the S1/S4 specialised counters.

The production application is PyQt; this demo uses only the Python standard
library (`tkinter`) so it runs with no extra dependencies. The realism is in the
*data, protocol, metrics, and recording artifacts*, not the widget toolkit.

Run from this folder:

```powershell
python beatbox_gui_demo.py
```

## What is modelled

**Connection state machine** (Technical Guide §6.2) — the Connect button walks
through `DISCONNECTED → PORT OPEN → HANDSHAKING → CONNECTED`, with a colour-coded
status indicator. Experiment and actuator controls stay disabled until the
handshake completes.

**USB serial protocol** (Technical Guide §4) — handshake (`ready` → `starting`/
`inited` → `ready`), `set` output frames, `ack`, and device `state` input
frames. Frames are built with the real shape `<command>:<enc><seq><payload><crc8>`
using CRC-8 (poly `0x07`, init `0x00`). The Diagnostics panel shows the live
output mask, last TX/RX frame, and last device response.

**Pin-ordered actuator mask** (Technical Guide §3) — reward, left/right visual
pattern bits, light, and red light map to mask bits 0–10 in `pins_config.cfg`
order. The 16-bit mask is shown as `0xHHHH → [0xHH, 0xLL]`.

**Stages and phases** — the valid stages S1, S2, S3, S3.1, S4 with realistic
phase transitions (pellet delivery/retrieval for S1; tunnel entry, stimulus
display, screen response, scoring, reward/corrective loop for S2+; discovery
trials and probabilistic reward rolls for S4).

**Metrics** (Recording Outputs) — total/correct/incorrect/valid/invalid trials,
corrective loops, per-side and overall performance computed as
`valid / (valid + incorrect)` against the learning threshold, plus S1 pellet
counters and S4 discovery rewards.

**Recording artifacts** — the Recording panel shows the experiment folder and the
generated `FULL_<subject>_S<stage>_<ts>.csv` and trial-summary file names, with a
live count of rows written. The monitoring window's General tab renders recent
FULL event-log rows in `timestamp_s | stage | level | message` form.

**Monitoring window** — Box 1–4 tabs, each with General, Success-rate,
Lateralization, and Reversal-task views; the success and reversal plots draw the
learning threshold, and a reward raster shows the latest 50 rewards.

## What is interactive

- Mock serial connect/disconnect with the full handshake sequence
- Per-box stage/n_trials/inter-trial/threshold/time-pressure setup, start/stop
- Live sensor `state` lights and the computed output mask
- Manual actuator controls (with a confirmation prompt for manual reward)
- Manual `set`/`ready` frame send
- Animal and stage/phase metadata panels
- Activity log in the GUI format `asctime - level - message`

The demo does not connect to hardware and writes no files; the CSV artifacts are
named and counted but only simulated.
