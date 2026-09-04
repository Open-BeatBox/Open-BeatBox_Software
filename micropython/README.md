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

## Feeder stepper validation

`modules/feeder.py` now includes a minimal A4988 timer-based step generator.
The validation scenario loops one full revolution (200 full steps) at
different speeds: 200, 400, 800 and 1200 steps/s.

- Local host-side unit tests: `tests/test_feeder_stepper.py`
- Device entry point: `modules/feeder.py` (`main()`)

Quick checks:

```bash
python -m pytest tests/test_feeder_stepper.py -q
```

Run on target with mpremote (adapt port and copy flow to your board setup):

```bash
mpremote connect auto fs cp modules/feeder.py :modules/feeder.py
mpremote connect auto exec "import modules.feeder as f; f.main()"
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


## Use ISR with MPy

https://docs.micropython.org/en/latest/reference/isr_rules.html#isr-rules
