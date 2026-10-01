# Open-BEATBox — Software

Software for the Open-BEATBox apparatus: the PC application and the firmware
of the box modules.

## Contents

- [`pc_app/`](./pc_app) — the host application (PC), which runs the
  experimental protocol and talks to the `MAIN` module over serial.
- [`micropython/`](./micropython) — the module firmware (RP2040). See its own
  [README](./micropython/README.md) for flashing, deployment and tests.
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — how the host application, the main
  module and the peripheral modules fit together.

An early, hardware-free `tkinter` mock-up of the interface is kept for
reference on the
[`mock_gui`](https://github.com/Open-BeatBox/Open-BeatBox_Software/tree/mock_gui)
branch. It is deprecated.

## Status

This is an ongoing development version. Interfaces, CAN payloads and the host
wire protocol are still moving; see the roadmap in
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Licence

GNU Affero General Public License v3.0 (**AGPL-3.0**). See [`LICENSE`](./LICENSE).

## Project links

- Project website: https://open-beatbox.github.io/
- Manual: https://open-beatbox.github.io/docs/manual/

## Issues

Please report problems with this software in this repository's
[issue tracker](https://github.com/Open-BeatBox/Open-BeatBox_Software/issues).
