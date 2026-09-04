# Beatbox inter-module CAN protocol

This document defines the Beatbox CAN protocol exchanged between main and peripheral modules.

## 1) Identifier layout (11-bit standard CAN)

Beatbox uses standard 11-bit CAN IDs.

### 1.1 Field ordering (MSB -> LSB)

| Bits  | Size | Field  | Meaning |
|-------|------|--------|---------|
| 10..8 | 3    | PRIO   | Arbitration priority class (`0` highest) |
| 7..4  | 4    | MODULE | Target module for request, source module for response |
| 3..2  | 2    | TYPE   | Message class and direction |
| 1..0  | 2    | CMD    | Command index inside the selected TYPE namespace |

Once a module is selected by `MODULE`, the message meaning is described by 3 protocol fields: `PRIO`, `TYPE`, `CMD` (plus payload).

Payload is 0..8 bytes. RTR is not used.

### 1.2 Encoding formula

`CAN_ID = (PRIO << 8) | (MODULE << 4) | (TYPE << 2) | CMD`

| PRIO | Class         | Typical usage |

## 2) Global dictionaries

### 2.1 Priority classes (`PRIO`)

| PRIO | Class         | Typical usage |
|------|---------------|---------------|
| 000  | P0 highest    | Critical errors, safety urgent |
| 001  | P1 high       | Control commands (start/stop/reset/reward) |
| 010  | P2 medium     | Real-time events (nosepoke, touch, barrier edge) |
| 011  | P3 normal     | Status and ACK traffic |
| 100  | P4 background | Telemetry / periodic reporting |
| 101..111 | Reserved  | Future use |

### 2.2 Type classes (`TYPE`)

| TYPE | Meaning |
|------|---------|
| 00   | Common request |
| 01   | Common response |
| 10   | Module-specific request |
| 11   | Module-specific response |

### 2.3 Module IDs (`MODULE`)

| Module            | Value |
|-------------------|-------|
| GENERIC           | 0x0 |
| MAIN              | 0x1 |
| FEEDER            | 0x2 |
| NOSEPOKE          | 0x3 |
| SCREEN_LEFT       | 0x4 |
| SCREEN_RIGHT      | 0x5 |
| LIGHTING          | 0x6 |
| IR_BARRIER        | 0x7 |
| BROADCAST target  | 0xF |

No response may use `MODULE=0xF`.

## 3) Common namespace (`TYPE=00` / `TYPE=01`)

`CMD` values are global in this namespace.

| TYPE | CMD | Name       | Direction | Default PRIO | Payload |
|------|-----|------------|-----------|--------------|---------|
| 00   | 00  | SCAN       | Request   | P3           | none |
| 01   | 00  | SCAN_REPLY | Response  | P3           | `status_code:uint8` |
| 00   | 01  | GET_STATUS | Request   | P3           | none |
| 01   | 01  | STATUS     | Response  | P3           | `status_code:uint8` |
| 01   | 10  | ERROR      | Response  | P0           | `error_code:uint8` |
| 00   | 11  | RESET      | Request   | P1           | `delay_ms:uint16` big-endian |

Status values:
- `0`: INIT
- `1`: IDLE
- `2`: ACTIVE
- `3`: ERROR

## 4) Module-specific namespace (`TYPE=10` / `TYPE=11`)

`CMD` is module-local in this namespace.

### 4.0 Generic (`MODULE=0x0`)

| TYPE | CMD | Name               | Direction | Default PRIO | Payload |
|------|-----|--------------------|-----------|--------------|---------|
| 10   | 00  | TOGGLE_CAN_LED     | Request   | P1           | none |
| 11   | 00  | CAN_LED_STATE      | Response  | P3           | `state:uint8` (`0` off, `1` on, `255` unknown) |
| 11   | 01  | USER_BUTTON_EVENT  | Response  | P2           | `pressed:uint8` (`1` pressed) |

### 4.1 Feeder (`MODULE=0x2`)

| TYPE | CMD | Name             | Direction | Default PRIO | Payload |
|------|-----|------------------|-----------|--------------|---------|
| 10   | 01  | REQUEST_REWARD   | Request   | P1           | none |
| 11   | 01  | REWARD_DELIVERED | Response  | P3           | none |

### 4.2 Nosepoke (`MODULE=0x3`)

| TYPE | CMD | Name            | Direction | Default PRIO | Payload |
|------|-----|-----------------|-----------|--------------|---------|
| 10   | 00  | GET_BEAM_STATUS | Request   | P3           | none |
| 11   | 00  | BEAM_STATUS     | Response  | P3           | `beam:uint8` (`0` clear, `1` broken) |
| 11   | 01  | BEAM_EVENT      | Response  | P2           | `beam:uint8` (`1` when poke event detected) |

### 4.3 Screen left/right (`MODULE=0x4` / `0x5`)

Screen side is encoded by module ID, so payload has no side field.

| TYPE | CMD | Name                | Direction | Default PRIO | Payload |
|------|-----|---------------------|-----------|--------------|---------|
| 10   | 00  | DISPLAY_PATTERN     | Request   | P1           | `pattern_id:uint8` |
| 11   | 00  | DISPLAY_PATTERN_ACK | Response  | P3           | `pattern_id:uint8` |
| 11   | 01  | TOUCH_EVENT         | Response  | P2           | `touch:uint8` (`1` touched) |

### 4.4 Lighting (`MODULE=0x6`)

| TYPE | CMD | Name           | Direction | Default PRIO | Payload |
|------|-----|----------------|-----------|--------------|---------|
| 10   | 00  | SET_DUTY       | Request   | P1           | `white:uint8, red:uint8, ir:uint8` |
| 11   | 00  | SET_DUTY_ACK   | Response  | P3           | `white:uint8, red:uint8, ir:uint8` |
| 10   | 01  | GET_DUTY       | Request   | P3           | none |
| 11   | 01  | DUTY_STATUS    | Response  | P3           | `white:uint8, red:uint8, ir:uint8` |
| 10   | 10  | TURN_ON_GROUP  | Request   | P1           | `group:uint8` (`0` IR, `1` RED, `2` WHITE) |
| 10   | 11  | TURN_OFF       | Request   | P1           | optional `group:uint8` (empty = all) |

### 4.5 IR barrier (`MODULE=0x7`)

| TYPE | CMD | Name               | Direction | Default PRIO | Payload |
|------|-----|--------------------|-----------|--------------|---------|
| 10   | 00  | GET_BARRIER_STATUS | Request   | P3           | none |
| 11   | 00  | BARRIER_STATUS     | Response  | P3           | `barrier:uint8` (`0` clear, `1` blocked) |
| 11   | 01  | BARRIER_EVENT      | Response  | P2           | `barrier:uint8` (`1` on edge event) |

## 5) Arbitration behavior

With `PRIO` in the top bits, arbitration follows functional urgency before module identity:

1. lower `PRIO` wins first,
2. then lower `MODULE`,
3. then lower `TYPE`,
4. then lower `CMD`.

This avoids permanent dominance by low module IDs and matches the target policy: `error > control > event > status/telemetry`.

## 6) Worked examples

- Broadcast scan request (`P3`, `BROADCAST`, common request, `SCAN`):
  - bits: `011 1111 00 00`
- Nosepoke event (`P2`, `NOSEPOKE`, module response, `BEAM_EVENT`):
  - bits: `010 0011 11 01`
- Lighting set duty (`P1`, `LIGHTING`, module request, `SET_DUTY`):
  - bits: `001 0110 10 00`

## 7) Capacity note

This layout provides `2` command bits (`CMD=0..3`) per TYPE namespace.
If a module later needs more than 4 module-specific operations, use either:

- one `CMD` value as a payload sub-opcode container, or
- a protocol v2 based on 29-bit IDs.
