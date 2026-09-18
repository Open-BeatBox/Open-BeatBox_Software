# Open-BEATBox — Software

Software for the Open-BEATBox apparatus.

## Contents

`gui_demo/` — a hardware-free mock-up of the Open-BEATBox graphical interface,
written for design review and software planning before the hardware/API
integration is finalised.

- `beatbox_gui_demo.py` — the demo application. It uses only the Python
  standard library (`tkinter`), so it runs with no extra dependencies. It does
  not connect to hardware and writes no files; recording outputs are simulated.
- `TECHNICAL_GUIDE.md` — architecture notes, the connection-state contract, the
  USB serial protocol, and the `pins_config.cfg` actuator mask ordering.
- `RECORDING_OUTPUTS.md` — the event-log and trial-summary CSV schemas and the
  per-stage metrics.
- `README.md` — what the demo models and which parts are interactive.

Run it with:

    cd gui_demo
    python beatbox_gui_demo.py

## Version

The production application is planned in PyQt. What is published here is a
pre-release `tkinter` demonstration; it carries no version number of its own
and is not tied to a declared hardware version.

## Licence

GNU Affero General Public License v3.0 (**AGPL-3.0**). See [`LICENSE`](./LICENSE).

## Project links

- Project website: https://open-beatbox.github.io/
- Manual: https://open-beatbox.github.io/docs/manual/

## Issues

Please report problems with this software in this repository's
[issue tracker](https://github.com/Open-BeatBox/Open-BeatBox_Software/issues).
