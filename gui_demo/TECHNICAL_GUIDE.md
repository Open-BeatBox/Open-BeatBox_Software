# Open-BEATBox technical guide and maintenance plan

This document describes the code currently on the `dev` branch.  It separates
what is implemented for the desktop controller from the CircuitPython CAN
prototype.  These are **not currently one end-to-end protocol stack**.

## 1. System scope

Open-BEATBox is a behavioural-experiment controller.  The desktop application
provides a PyQt GUI, runs stage-specific experiment state machines, sends
actuator masks to a device, receives input-state changes, and writes CSV
experiment logs.

```
operator
  -> PyQt desktop application
  -> BeatBoxCommunicator (USB serial, 115200 baud)
  -> device firmware [not present as the authoritative implementation here]
  -> sensors and actuators

separately:
CircuitPython USB/CAN prototype -> CAN modules
```

The CircuitPython code is useful experimental material, but it accepts `GO`
and sends `DONE`, while the desktop application expects framed `ready`, `set`,
`ack`, and `state` messages.  Do not deploy it as the desktop app's firmware
without first defining and implementing a shared protocol adapter.

## 2. Repository map

| Location | Responsibility |
| --- | --- |
| `04_code/pc_app/beatbox/arduino_control_app.py` | Application entry point and current PyQt GUI.  It also wires hardware and experiment logic together. |
| `04_code/pc_app/beatbox/serial_com.py` | USB serial protocol codec, serial reader thread, handshake, and output coalescing. |
| `04_code/pc_app/beatbox/experiment_managers.py` | Shared experiment state-machine and logging abstractions. |
| `04_code/pc_app/beatbox/S1_manager.py` through `S4_manager.py` | Stage-specific behavioural rules.  S2 derives from S1; S3 derives from S2; S3.1 derives from S3; S4 derives from S3. |
| `04_code/pc_app/beatbox/utils.py` | CRC-8, stimulus pattern IDs, and the pin-configuration loader. |
| `04_code/pc_app/beatbox/pins_config.cfg` | Ordered mapping of logical sensors/actuators to physical pins.  Order defines mask bit positions. |
| `04_code/pc_app/beatbox/config_specs/` | ConfigObj schemas for experiment stages. |
| `04_code/pc_app/tests/` | Serial test attempts and a simulated Arduino.  This is not yet a dependable test suite. |
| `04_code/circuitpython/` | Experimental CircuitPython USB CDC and CAN implementation, timing experiments, and peripheral sketches. |

## 3. Desktop backend architecture

### Startup and GUI

Run from `04_code/pc_app` after creating the declared Conda environment:

```powershell
conda env create -f environment.yml
conda activate beatbox
python -m beatbox.arduino_control_app
```

`arduino_control_app.py` creates `ArduinoControlApp`, asks the operator to
select a stage, scans serial ports, and starts the Qt event loop.  The class
currently owns GUI construction, serial connection flow, stage selection, and
some hardware interaction.  This is the main frontend-improvement boundary:
new work should progressively extract views, presenters/controllers, and
hardware-facing services from this class instead of adding more responsibilities
to it.

### Experiment flow

`BaseExperimentManager` is a Qt object with a phase table.  A phase has a
timeout, a callback, and monitored sensor-edge rules.  The manager:

1. receives a `state` value from `BeatBoxCommunicator`;
2. maps its input bits through the ordered `sensors` configuration;
3. detects expected rising/falling edges;
4. advances the state machine or invokes the stage-specific handler;
5. produces actuator masks and logs events.

Stage managers build on this base:

- **S1**: pellet delivery / retrieval training.
- **S2**: adds tunnel and screen-response logic, stimulus patterns, accuracy,
  corrective loops, and performance metrics.
- **S3** and **S3.1**: specialise S2 performance/progression behaviour.
- **S4**: adds discovery trials and probabilistic reward logic.

`ExperimentCSVLogger` writes detailed state/event CSV files and
`LightExperimentCSVLogger` writes a trial-focused CSV file.  Stage code should
close both loggers at every normal and error termination path.

See `04_code/RECORDING_OUTPUTS.md` for the current recording artifacts, CSV
schemas, metrics, runtime logs, and known export caveats.

### Pin masks

`PinConfig` assigns bit positions by the order of entries in
`pins_config.cfg`, not by physical pin number.  `set_outputs()` creates a
16-bit word and sends it as `[high_byte, low_byte]`.  Changing the order of
the configuration file changes the wire meaning of every subsequent bit and is
a protocol-breaking change.

The current actuator configuration uses bits 0--10 for `reward`, left/right
pattern bits, `light`, and `red_light`.  The desktop code can optionally append
three RGB bytes for NeoPixels; the device contract for those bytes is not
documented in this repository.

## 4. USB serial protocol: observed desktop contract

The protocol implementation is in `BeatBoxMessage` and
`BeatBoxCommunicator`.  Treat the following as the current *implementation
contract*, to be verified against the deployed firmware before relying on it.

### Transport

- USB serial at **115200 baud** with a two-second default timeout.
- UTF-8/ASCII text frames, delimited by `\n`; readers also tolerate leading
  CR/LF during handshake.
- The desktop reader runs in a background Python thread and emits Qt signals.

### Normal frame shape

```
<command>:<encoding><sequence><encoded payload><crc8>\n
```

The default encoding is `x`: two lowercase hexadecimal characters per byte.
`sequence` is always two hexadecimal characters and wraps from 255 to 0.
For non-raw normal frames, CRC-8 uses polynomial `0x07` and initial value
`0x00`, calculated over the characters before the CRC.

Example shape only (the final CRC must be computed, not copied):

```
set:x070100<crc>\n
```

This represents sequence `0x07` and the two-byte payload `0x01, 0x00`.

### Commands handled by the desktop application

| Direction | Command | Desktop behaviour |
| --- | --- | --- |
| host -> device | `ready` | Starts the handshake; it waits for a `ready` response. |
| device -> host | `starting`, `inited` | Accepted only as startup/handshake status. |
| device -> host | `ready` | Completes the handshake and starts the reader thread. |
| host -> device | `set` | Sends actuator payloads. |
| device -> host | `ack` | Logged as an acknowledgement; no retry or delivery guarantee exists. |
| device -> host | `state` | Decodes the first payload byte and forwards it to the experiment manager. |
| device -> host | other valid command | Emitted to generic UI listeners. |

There are important ambiguities to resolve before calling this protocol
validated:

- Handshake frames are exempted from normal validation, but their construction
  and parsing do not share a separately specified handshake grammar.
- Sequence validation is deliberately disabled in `is_valid()`; a sequence
  mismatch currently returns valid.
- ACK comparison uses the communicator's next sequence value rather than an
  explicit pending-message record.
- Invalid input terminates the reader thread rather than recovering and
  resynchronising.
- `state` consumes only payload byte zero even though actuator masks are
  16-bit and the physical input contract is not documented here.

Resolve these points in a versioned protocol specification and test vectors
before changing device or desktop code.

## 5. Test status and priority plan

### Current status

The project declares `pytest` in `environment.yml`, but the Python executable
used for the initial check did not have it installed (`No module named pytest`).
After activating the Conda environment, run:

```powershell
cd 04_code\pc_app
python -m pytest tests -q
```

Do not interpret a passing result as full validation yet.  The two current
desktop test files are duplicate threaded smoke tests.  They use Unix-only
`pty`, while the primary development platform is Windows, and refer to stale
interfaces (`send_set_command` and `message.data`) that do not exist in the
current production classes.

### Recommended order

1. **Pure protocol tests first.** Add platform-independent tests for frame
   construction/parsing, CRC pass/fail, each encoding, raw frames, empty and
   malformed frames, sequence wrap, payload-length rules, and known handshake
   vectors.  Store request/response examples in the test suite.
2. **Firmware contract test.** Use a physical device or a serial-loopback
   harness to prove the exact handshake, `set`, `ack`, and `state` exchanges.
   Capture the raw byte transcript and make it a regression fixture.
3. **Desktop integration test.** Replace `pty` with an abstraction that has a
   Windows-compatible fake serial port.  Assert signals, timeout handling,
   reconnection, malformed-frame recovery, and clean thread shutdown.
4. **State-machine tests.** Drive S1--S4 with synthetic state transitions and
   fake timers.  Assert phases, outputs, rewards, performance calculations,
   and CSV rows.
5. **GUI tests.** Use Qt's test tools to cover stage selection, disabled
   controls before handshake, connection failures, manual actuator controls,
   and visible experiment status.
6. **Bench acceptance test.** With hardware connected, verify every sensor,
   every actuator, safe startup/shutdown states, timing limits, and a complete
   trial per stage.

No user-facing frontend work should bypass the connection state or directly
write serial frames; it should call a tested controller/service interface.

## 6. Frontend improvement path

Prioritise safety and observability before visual redesign:

1. Split `ArduinoControlApp` into a view, connection controller, experiment
   controller, and hardware adapter while keeping current behaviour covered by
   tests.
2. Display explicit connection state: disconnected, port open, handshaking,
   connected, communication fault, and stopped.
3. Make active stage, phase, subject, sensor values, current output mask, last
   frame, and last device response visible in one diagnostics panel.
4. Require confirmation for manual reward/actuator actions and disable them
   when experiment policy disallows them.
5. Replace HTML exception dumps in the operator UI with a short actionable
   error plus an exportable technical log.

## 7. Maintenance-agent design

Use narrowly scoped agents with explicit evidence requirements.  They should
never be allowed to invent device behaviour or silently change a pin map.

| Agent | Scope | Required output / guardrail |
| --- | --- | --- |
| Protocol agent | `serial_com.py`, fixtures, protocol spec | Updates the specification and golden byte vectors; reports compatibility impact; no change without codec tests. |
| Test agent | unit, integration, and hardware-harness tests | Reproduces a failing test before a fix; reports exact command and result; does not change behavioural requirements. |
| Backend agent | experiment managers, config validation, logging | Adds state-machine tests for any transition changed; preserves CSV schema unless a migration is documented. |
| Frontend agent | GUI/view layer only | Uses controller interfaces rather than serial access; supplies GUI tests and screenshots/acceptance steps. |
| Release/quality agent | CI, dependencies, docs | Runs the full suite, checks protocol-fixture compatibility, and blocks releases without recorded bench-test evidence. |

Suggested task prompt for any maintenance agent:

```
Work only in <named scope>. Read 04_code/TECHNICAL_GUIDE.md first. State the
protocol and safety impact before editing. Preserve pins_config.cfg ordering.
Add or update a test that fails before the change. Run the relevant test command
and report the result. If the task requires an assumption about device firmware,
stop and request a captured serial transcript or firmware source.
```

Start with the protocol and test agents.  The frontend agent should work after
the connection-state contract is covered, otherwise UI changes will conceal
communication failures rather than make them safer to operate.
