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

- Can monitor GPIO lines `0..7`, make sure the lines are all defined in bb_pin_map.py
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
the legacy BB Mega V2 emulation (`V2WireCodec` handshake and framed commands) is not
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


## Beatbox modules flashing

After receiving your Beatbox modules, when connecting a USB cable to the module, it should appear as a USB RPI-RP2 device.
The device is running the UF2 bootloader, which allows you to flash firmware onto it.

If the board does not go into bootloader mode, you can press the reset button quickly twice while pressing the boot button to enter bootloader mode.
If you still cannot enter into bootloader, but can access the REPL, you can use the `machine.bootloader()` command to enter bootloader mode or
`mpremote bootloader` to enter bootloader mode from the host.

To make it MicroPython compatible, you need to flash the MicroPython firmware on it.
Download the MicroPython firmware and drag and drop the `.uf2` file to the RPI-RP2 device. 
The module will reboot and appear as a USB serial device.

The MicroPython firmware use for this project is 'ADAFRUIT_FEATHER_RP2040-20260406-v1.28.0.uf2'.
Note that the ADAFRUIT_FEATHER_RP2040 board is used as the base for the Beatbox custom modules, so the MicroPython firmware for this board is compatible with the Beatbox modules.
In order to use the MicroPython firmware with different BeatboxModules using a different pinout or base, you will need either a custom MicroPython firmware or a custom pin mapping file.

Now that the MicroPython firmware is flashed, you can use `mpremote` to copy the module files to the device, or use the `deploy_module.py` script to deploy a module to the device.

'mpremote' is a command line tool that allows you to interact with MicroPython devices over USB or serial. You can use it to copy files, run scripts, and access the REPL.

