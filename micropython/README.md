Activate the conda environment.

mpremote

import the module through repl
exec main

main.py is called automatically if there's a main.py file in the root of the project.
Call a function from main.py to automatically run the code on boot. You can also use the REPL to call functions from main.py.

mpremote copies files to the device. You can use it to copy main.py to the root of the device, or copy a file from the device to your computer.

> mpremote ls
> mpremote cp `path/on/host` :`path/on/device`
> mpremote rm `path/on/device`

``` shell
mpremote
  import module.py
  module.function()
```

https://docs.micropython.org/en/latest/reference/reset_boot.html#boot-sequence

## Feeder module

`modules/feeder.py` implements the `FEEDER` CAN module and its A4988 stepper driver.

A reward is a twentieth of a revolution. On `FeederCommand.REQUEST_REWARD` the
module rotates 1/20 rev, waits 0.5 s for the `feeder_ir_barrier` interrupt
(`FeederPins.FOOD_SENSOR_IN`, active low, falling edge) and retries until a
pellet is detected or `MAX_REWARD_ATTEMPTS` (one full revolution) is reached.

- Success: `FeederCommand.REWARD_DELIVERED` with `[attempt_count]` as payload
- Failure: `CommonCommand.ERROR` at `Priority.ERROR`, module status `ERROR`

- Local host-side unit tests: `tests/test_feeder_stepper.py`
- Device entry point: `modules/feeder.py` (`main()`)

Quick checks:

```bash
python -m pytest tests/test_feeder_stepper.py -q
```

Run on target with mpremote (adapt port and copy flow to your board setup):

```bash
python deploy_module.py feeder
mpremote connect auto exec "import feeder; feeder.main()"
```

## CAN inter-module protocol

The current CAN inter-module protocol draft is documented in:

- `../beatbox_intermodule_protocol.MD`

The MicroPython implementation helpers for CAN identifier packing/parsing live in:

- `libs/can_protocol.py`
- `libs/beatbox_module.py`

Quick host-side validation:

```bash
python -m pytest tests/test_can_protocol.py tests/test_beatbox_can_module_protocol.py -q
```

## IR barrier module

`modules/ir_barrier.py` implements the `IR_BARRIER` CAN module.

- Monitors GPIO lines `0..7`
- Registers a falling-edge interrupt on every line
- Sends a CAN event as soon as any one line triggers

Current payloads:

- `IRBarrierCommand.BARRIER_EVENT` → `[line_index, 1]`
- `IRBarrierCommand.GET_BARRIER_STATUS` response → `[broken_mask, last_triggered_line]`

Host-side validation:

```bash
python -m pytest tests/test_ir_barrier_module.py -q
```

Deploy to a board using the existing deploy helper:

```bash
python deploy_module.py ir_barrier --dry-run
python deploy_module.py ir_barrier --as-main --port auto
```
`deploy_module.py` script can sometimes fail to copy files successively and leave the board in a
state where accessing raw REPL is not possible. To work around this you can reset the board by using the 
reset button or power cycling it.

## Use ISR with MPy

https://docs.micropython.org/en/latest/reference/isr_rules.html#isr-rules

Timer ISR :

https://github.com/orgs/micropython/discussions/10713

## Validation CLI

`tools/bb_cli.py` drives the main module from a PC to validate the other modules over CAN.
Text commands are sent on the main module UART; `MainModule.serial_msg_handler` translates
them into CAN frames and returns the responses on the same link.

Wiring: main module `UART0` (`tx=GP0`, `rx=GP1`, 921600 baud) to a USB-serial adapter,
which shows up as `/dev/ttyUSB0` by default.

Deploy the main module with `CLI_MODE = True` (top of `modules/main_module.py`). In this mode
the legacy BB Mega V2 emulation (`BeatBoxCommManager` handshake and framed commands) is not
started, so the link carries CLI traffic only. Debug logs stay on the USB REPL.

```bash
python deploy_module.py main_module --as-main --port auto
```

Usage:

```bash
python tools/bb_cli.py                        # interactive prompt (default /dev/ttyUSB0)
python tools/bb_cli.py -p /dev/ttyUSB1 -b 921600
python tools/bb_cli.py -c SCAN                # one-shot, repeatable
python tools/bb_cli.py -f my_scenario.txt     # replay a script (comments and `sleep <s>`)
python tools/bb_cli.py --list-commands        # command reference, no serial needed
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
bb> DUTY 255 0 0        # white=255, red=0, ir=0
bb> LIGHT ON WHITE
bb> FEED                # request a reward from the feeder
<< FEEDER_REWARD_DELIVERED
bb> CAN STATUS FEEDER REQ_MODULE 1 0x0A   # raw frame escape hatch
```

The main module answers `OK <COMMAND>` when a frame was queued, `ERR <reason>` on a bad
command, and forwards CAN replies (`FOUND_*`, `*_STATUS_*`, `LIGHTING_DUTY_*`,
`NOSEPOKE_EVENT_BEAM_*`, `IR_BARRIER_*`, ...).

The command table lives in `libs/cli_protocol.py` and is shared by the board and the CLI,
so both sides always agree on the grammar.

Host-side validation:

```bash
python -m pytest tests/test_cli_protocol.py -q
```
