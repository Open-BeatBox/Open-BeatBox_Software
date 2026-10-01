# Beatbox firmware (MicroPython)

Firmware for the RP2040-based Beatbox modules. For the overall architecture —
how the host application, the main module and the peripheral modules fit
together, and what *intents* and *events* are — see the
[architecture document](../ARCHITECTURE.md).

---

## ⚠️ Disclaimer


Ongoing development version; see the roadmap in the [architecture document](../ARCHITECTURE.md).
Tests scripts and the CLI are not yet stable and some might be broken.

---

## Layout

```
libs/        shared infrastructure
             can_protocol    identifier packing, per-module command tables
             beatbox_module  ModuleManager base class, status codes
             cli_protocol    text command grammar (shared with tools/bb_cli.py)
             bb_pin_map      per-board pin assignments

modules/     module roles and the main module's protocol layers
             main_module     host-facing module: wiring and CAN fan-out
             feeder, ir_barrier, ...   one file per peripheral role
             bb_intents      Intent + one builder per logical action
             bb_events       Event + the CAN decoder
             frontends       CliFrontend, V2Frontend
             bb_v2_comm      V2WireCodec — legacy host wire protocol
             bb_v2_emulator  V2 pin-state model
             bb_v2_map       V2 pin ↔ intent/event mapping tables
             bb_ticks        tick helpers, CPython and MicroPython

tools/       bb_cli.py, the PC-side validation client
tests/       host-side test suite, runs on CPython
```

The payloads each peripheral role implements are specified in
[`beatbox_intermodule_protocol.MD`](beatbox_intermodule_protocol.MD), which is
the reference for anything module-specific.

---

## Flashing a board

A new module appears as a USB **RPI-RP2** drive, running the UF2 bootloader.
Drag the MicroPython `.uf2` onto it; the board reboots as a USB serial device.

Firmware in use: `ADAFRUIT_FEATHER_RP2040-20260406-v1.28.0.uf2`. The Beatbox
custom modules use the Adafruit Feather RP2040 as their base, so that build is
compatible. A different base board or pinout needs a custom build or a new
`bb_pin_map` entry.

If the board does not enter bootloader mode:

- press reset twice quickly while holding boot, or
- from the REPL: `machine.bootloader()`, or
- from the host: `mpremote bootloader`

---

## Deploying code

Activate the conda environment first.

```bash
python deploy_module.py <module> --dry-run       # show what would be copied
python deploy_module.py <module> --as-main --port auto
```

`--as-main` installs the module as `main.py`, which MicroPython runs
automatically after `boot.py`
([boot sequence](https://docs.micropython.org/en/latest/reference/reset_boot.html#boot-sequence)).

`deploy_module.py` occasionally fails partway through and leaves the board
unable to enter raw REPL. Reset it with the button or a power cycle and retry.

Manual file operations and the REPL go through `mpremote`:

```bash
mpremote                      # REPL
mpremote ls
mpremote cp path/on/host :path/on/device
mpremote rm :path/on/device
```

---

## Host-side tests

The protocol layers import no hardware modules, so they run on CPython:

```bash
python -m pytest tests/ -q
```

This covers identifier packing, the CAN decoder, the command grammar and the V2
mapping. Prefer adding a test here over testing on a board — it is the reason
those layers are kept free of `machine` and `canio`.

---

## Operating modes

`modules/main_module.py` has one deploy-time switch:

```python
CLI_MODE = False   # default: legacy V2 emulation
```

**V2 emulation (default).** The main module speaks the legacy BB Mega V2 wire
protocol on `UART0`: startup and handshake frames, sequence numbers, CRC, pin
words in and pin states out. The existing `pc_app` connects unchanged. A
human-readable mirror of all CAN traffic is printed on the USB REPL.

**CLI mode (secondary).** `UART0` carries plain-text commands instead, and the
V2 emulation is not started — the code is still there, it simply has no
transport, so no handshake frames pollute the link. For bring-up and validation
only.

Wiring in both modes: main module `UART0` (`tx=GP0`, `rx=GP1`, 921600 baud) to a
USB-serial adapter, typically `/dev/ttyUSB0`. Debug logging always stays on the
USB REPL, never on the UART.

---

## Validation CLI

`tools/bb_cli.py` drives the main module from a PC to exercise the other modules
over CAN. Deploy with `CLI_MODE = True`:

```bash
python deploy_module.py main_module --as-main --port auto
```

```bash
python tools/bb_cli.py                        # interactive prompt
python tools/bb_cli.py -p /dev/ttyUSB1 -b 921600
python tools/bb_cli.py -c SCAN                # one-shot, repeatable
python tools/bb_cli.py -f my_scenario.txt     # replay a script (comments, `sleep <s>`)
python tools/bb_cli.py --list-commands        # reference, no serial needed
```

Example session:

```
bb> SCAN
>> SCAN
<< OK SCAN
<< FOUND_FEEDER
<< FOUND_LIGHTING
bb> STATUS FEEDER
<< FEEDER_STATUS_IDLE
bb> DUTY 255 0 0            # white=255, red=0, ir=0
bb> LIGHT ON WHITE
bb> FEED                    # one reward, feeder owns the retries
<< FEEDER_REWARD_1          # pellet present
<< FEEDER_REWARD_0          # pellet taken
bb> REWARD ON               # direct motor control, for maintenance
bb> REWARD OFF
bb> CAN STATUS FEEDER REQ_MODULE 1 0x0A   # raw frame escape hatch
```

The board answers `OK <COMMAND>` when a frame was queued and `ERR <reason>` on a
bad command, then forwards decoded CAN replies (`FOUND_*`, `*_STATUS_*`,
`*_REWARD_*`, `LIGHTING_DUTY_*`, `NOSEPOKE_EVENT_BEAM_*`, `IR_BARRIER_*`, ...).

The command table lives in `libs/cli_protocol.py` and is imported by both the
board and the CLI, so the two sides cannot disagree on the grammar. Each command
resolves to an *intent*, the same objects the V2 frontend produces — so the CLI
and the application exercise identical code paths down to the bus.

---

## MicroPython constraints worth knowing

- **No 3.10+ typing syntax** in shared code (`str | None`, `list[int]`) and no
  `typing` import: anything the host tests import must load on both runtimes.
- **Ticks wrap.** `time.ticks_ms/us` roll over (~17.9 min for µs on RP2040).
  Always compare with `bb_ticks.ticks_diff`, never with `-`.
- **ISRs must be minimal**: set a flag, allocate nothing, never touch the bus.
  The owning task consumes the flag.
  [ISR rules](https://docs.micropython.org/en/latest/reference/isr_rules.html#isr-rules) ·
  [timer ISRs](https://github.com/orgs/micropython/discussions/10713)
- **Cooperative scheduling.** Every task shares one loop: a blocking sleep in a
  callback stalls CAN RX and the serial handler too. Defer instead of blocking.