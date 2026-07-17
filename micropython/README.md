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


## Use ISR with MPy

https://docs.micropython.org/en/latest/reference/isr_rules.html#isr-rules
