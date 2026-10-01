# Beatbox — software architecture

Beatbox is an autonomous behavioural box. A host application runs the
experimental protocol; the box executes it and reports what the animal does.
This repository holds both sides: the PC application and the module firmware.

---

## ⚠️ Disclaimer

This is an ongoing development version. Interfaces, CAN payloads and the host
wire protocol are still moving. See [Roadmap](#roadmap) for the changes that are
already planned.

In the Micropython code especially, the previous prototype version is referred as Beatbox V2 or bbv2. 

---

## Roadmap

Many devlopment tasks are already planned, in a rough order of priority:
  - Fix the application handshake failing inadvertently
  - There appears to be a bug in the CAN bus that causes some messages to be dropped by the main if there
    are too many messages (which can happen in response of a SCAN for example). This needs to be investigated and fixed.
  - The performance of the Micropython code needs to be evaluated and improved. The current implementation can feel laggy
    under load, especially when spamming IR barrier events for example.
      - The CAN bus is not the bottleneck, but the Micropython code is. Micropython Viper and native code can be used to speed up the code.
        The critical sections are the preferred target of such optimizations.
      - The CAN bus is actually handled as an async task. It might be worth investigating an interrupt-driven approach.
      - To improve performance, debounce need to be optimized too.
  - Graph plotting is to be add on the PC application.
  - Storing logs needs to be add in the main module and logging needs to be improved to be more consistent.
  - The repo structure needs to be improved. The current structure is a bit messy and needs to be cleaned up.
  - Naming of the modules and some files might be confusing because of the previous version. When transitioning to the new version
    for the app and beatbox firmware, the naming should be improved to avoid confusion and old nomenclature dropped where appropriate.

---

## The chain

```
┌────────────┐   serial    ┌─────────────┐    CAN bus    ┌──────────────────┐
│  host app  │ ◄─────────► │ MAIN module │ ◄───────────► │ peripheral       │
│  (pc_app)  │   UART0     │  (RP2040)   │   11-bit ID   │ modules (RP2040) │
└────────────┘             └─────────────┘               └──────────────────┘
                                                          FEEDER, NOSEPOKE,
                                                          SCREEN_L/R, LIGHTING,
                                                          IR_BARRIER, ...
```

The host application talks to exactly one module, `MAIN`. Every other module is
reached over CAN, and `MAIN` is the only translator between the two worlds.

Peripheral modules are autonomous: they own their sensors, their timing and
their retry logic, and they report state changes on the bus without being
polled. `MAIN` routes, it does not run the experiment.

---

## Two host protocols, one core

| Mode | Host speaks | Purpose |
|---|---|---|
| **V2 emulation** (default) | the legacy BB Mega V2 wire protocol | run the existing application unchanged |
| **CLI** (secondary) | plain-text commands | bring-up and validation from a PC, no app needed |

The previous Beatbox generation used an ATmega as the main module, and its
protocol was tied to hardware: the app drove TTL output pins and read input pins
back as a bitfield. That protocol is emulated, not reimplemented — the
application sees the same pin words it always did, while the pins themselves are
now CAN messages.

CLI mode is a development tool, selected at deploy time. It replaces the V2
link, so it is never active during an experiment.

---

## Intents and events

The two host protocols must not each grow their own copy of the CAN encoding.
So `MAIN` is built around two in-process value types that form the only
interface between transport-specific code and the CAN core:

- **Intent** — *what to emit on the bus*. It already carries the encoded
  identifier and payload; it is not a wire format, it is the thing a wire format
  gets turned into. Produced by the CLI parser and by the V2 pin map, consumed
  by `MainModule.send_intent`.
- **Event** — *what happened on the bus*. Produced by a single decoder, fanned
  out to every frontend.

```
  many producers            narrow, pure            many consumers
 ┌──────────────┐                                 ┌──────────────┐
 │ cli_protocol │──┐                           ┌──│ CliFrontend  │
 │ bb_v2_map    │──┼── Intent ──► CAN ──► Event ──┤ V2Frontend   │
 │ (future)     │──┘   bb_intents     bb_events   │ (future)     │
 └──────────────┘                                 └──────────────┘
```

Adding a host protocol means adding a frontend plus a mapping table. Changing
the CAN layout means touching `bb_intents` and `bb_events`. Neither change
propagates past the waist.

An event name is only defined when something dispatches on it: it names payload
bytes (`attempts=3` rather than `data[0]`), merges several wire encodings into
one logical meaning, or describes something with no wire origin at all. Frames
that need none of that decode to a generic event and are only logged.

---

## Layers inside the main module

Each layer translates exactly one representation into the next, owns the layer
below it, and reaches upward only through injected callables.

| Layer | Translates | Notes |
|---|---|---|
| `SerialManager` | bytes ⇄ lines | generic transport, takes a UART by injection |
| `V2WireCodec` | lines ⇄ V2 pin words | framing, CRC, sequence, handshake; owns the pin-state emulator |
| `V2Frontend` / `CliFrontend` | pins or text ⇄ Intent / Event | mapping policy only |
| `MainModule` | Intent / Event ⇄ CAN | owns the frontends, the transport and the CAN manager |

Everything below `main_module.py` is free of `machine` and `canio` imports, so
the parsers, decoders, mapping tables and the V2 codec are all importable and
testable on CPython.

---

## Repository layout

```
./
├── pc_app/                  host application (PC)
├── micropython/             module firmware — see its own README
│   └── libs/beatbox-protocol/   shared host wire format (git submodule)
└── README.md
```

`beatbox_protocol` (crc.py, message.py) is the single definition of the host
frame format. It lives in its own repository, shared by the application and the
firmware: `pc_app` installs it as a dependency, and the firmware gets it through
the `micropython/libs/beatbox-protocol` submodule, with a flat fallback for the
on-board layout. Neither side can drift from the other.

The application is tested against `beatbox-protocol` commit `d76b83b7`; the
submodule is pinned to that commit.

---

## Where to read next

- **CAN protocol** — identifier layout, per-module commands and payloads:
  [`micropython/beatbox_intermodule_protocol.MD`](micropython/beatbox_intermodule_protocol.MD)
- **Host command grammar** — `micropython/libs/cli_protocol.py`, shared verbatim
  by the board and `micropython/tools/bb_cli.py`
- **Firmware workflow** — flashing, deployment, running the tests:
  [`micropython/README.md`](micropython/README.md)
```
