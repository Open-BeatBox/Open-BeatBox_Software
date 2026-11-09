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

## Getting the code

Two firmware libraries are Git submodules, so clone with:

```
git clone --recurse-submodules https://github.com/Open-BeatBox/Open-BeatBox_Software.git
```

Archive downloads (the GitHub *Download ZIP* button, release archives and the
Zenodo record) do **not** contain submodules: the two folders below arrive
empty. Fetch them at the pinned commits if you start from an archive:

| Folder | Source | Commit |
| --- | --- | --- |
| `micropython/libs/beatbox-protocol` | [ICM `beatbox-protocol`](https://gitlab.com/icm-institute/rnd-unit-public/rnd_unit_libs/beatbox-protocol) | `fbb6d08` |
| `micropython/libs/ILI9341-parallel-TFT-driver-for-micropython` | [HughMaingauche/ILI9341-parallel-TFT-driver-for-micropython](https://github.com/HughMaingauche/ILI9341-parallel-TFT-driver-for-micropython) | `6c82d2f` |

`pc_app` installs the same `beatbox-protocol` commit from its `pyproject.toml`.

## Status

This is an ongoing development version. Interfaces, CAN payloads and the host
wire protocol are still moving; see the roadmap in
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Licence

GNU Affero General Public License v3.0 or later (**AGPL-3.0-or-later**). See
[`LICENSE`](./LICENSE).

Third-party libraries under `micropython/libs/` keep their own licences:

| Library | Licence |
| --- | --- |
| `microPython_MCP2515` (CAN driver, from capella-ben, with project changes) | MIT |
| `CircuitPython_PCA9955B` (LED driver, from noelanderson, with a MicroPython port) | MIT |
| `ILI9341-parallel-TFT-driver-for-micropython` (screen driver, submodule) | GPL-3.0 |
| `beatbox-protocol` (host wire format, ICM RnD unit, submodule) | MIT |

## Citing

If you use this software in published work, please cite **both**:

1. the reference preprint of the platform:

   > Euvrard M, Benzina N, Domingues D, Mondragón-González SL, Huzard D, Penderia G, Travert-Jouanneau Y, Lavielle O, Lousada E, Schreiweis C, Lorenz A, Pavlov P, Ajili Z, Lebegue L, Tissier P, Burguière E. "Open-BEATBox: an open-source autonomous operant chamber for high-throughput data acquisition of complex behaviour." bioRxiv, 2026 (DOI to be added when the preprint is posted).

2. the Zenodo record of the release you used. Each release of this repository
   is archived on Zenodo with its own DOI; the version described in the
   preprint is release `biorxiv-v1` (DOI: to be added once release `biorxiv-v1`
   is published on Zenodo). `CITATION.cff` carries the metadata (GitHub's
   *Cite this repository* button). The firmware has no record of its own: it
   is part of this repository, so cite this record for it too.

## Related repositories

- [Open-BeatBox_Hardware](https://github.com/Open-BeatBox/Open-BeatBox_Hardware) — CAD, 3D-printing and laser-cutting files, KiCad boards (CERN-OHL-S-2.0)
- [Open-BeatBox_Documentation](https://github.com/Open-BeatBox/Open-BeatBox_Documentation) — manual, assembly tutorials, Master BOM (CC-BY-4.0)
- [Open-BeatBox.github.io](https://github.com/Open-BeatBox/Open-BeatBox.github.io) — project website and manual build pipeline
- [Open-BeatBox_Firmware](https://github.com/Open-BeatBox/Open-BeatBox_Firmware) — placeholder that points to `micropython/` here

## Project links

- Project website: https://open-beatbox.github.io/
- Manual: https://open-beatbox.github.io/docs/manual/

## Issues

Please report problems with this software in this repository's
[issue tracker](https://github.com/Open-BeatBox/Open-BeatBox_Software/issues).
