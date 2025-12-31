# Beatbox communication protocol

The Beatbox communication protocol defines the structure and format of messages exchanged between Beatbox main MCU and the modules.
This protocol is based on the CAN bus system and uses specific message IDs and data formats to ensure proper communication.

## Message Structure
Each message consists of the following components:

- **ID** (uint8): 1 byte, module ID
- **CMD** (3 chars): 3 bytes, ASCII command
- **DATA** (4 bytes): 4 bytes, any type (raw bytes)

Total message size: 8 bytes

**Encoding:**
- ID: Unsigned integer (0-255)
- CMD: 3 ASCII characters (e.g., 'ID ', 'STA', 'RWD')
- DATA: 4 bytes, can be any type (integer, string, status, etc.), encoded as needed

**Example:**

| Byte Offset | Field | Example Value       |
|-------------|-------|---------------------|
| 0           | ID    | 0x01                |
| 1-3         | CMD   | 'STA'               |
| 4-7         | DATA  | 0x00 0x00 0x00 0x01 |

## List of the current modules in Beatbox system

| Module Name     | Module ID | Description                        |
|-----------------|-----------|------------------------------------|
| Main MCU        | 0x01      | Central controller of the Beatbox  |
| Feeder Module   | 0x02      | Deliver the pellets to the animal  |
| Nosepoke Module | 0x03      | Detects nosepoke from the animal   |
| Screen Module   | 0x04      | Displays visual stimuli            |
| Lighting Module | 0x05      | Provides ambient lights to the box |

## Generic messages

All messages follow the structure:
- **ID (uint8) | CMD (3 chars) | DATA (4 bytes)**

Payload is encoded as bytes. For string data, use UTF-8 encoding and pad/truncate to 4 bytes.

### Messages common to all modules:

| CMD | DATA (4 bytes)                       | Description                                      |
|-----|--------------------------------------|--------------------------------------------------|
| SCN | None (pad with zeros)                | Sent by MAIN to scan for modules                 |
| ID  | Module ID (uint8, pad with zeros)    | Sent in response to SCN                          |
| RST | None (pad with zeros)                | Request status info                              |
| STA | Status value (uint8, pad with zeros) | Sent in response to RST                          |
| PIN | Pin number (uint8, pad with zeros)   | Sent with the pin number and value when relevant |
| ERR | Error code (uint8, pad with zeros)   | Sent when an error occurs                        |


## Module specific messages

### Feeder 

| CMD  | DATA (4 bytes) | Description             |
|------|----------------|-------------------------|
| RWD  | None           | Request reward delivery |

### Nosepoke

| CMD | DATA (4 bytes) | Description                       |
|-----|----------------|-----------------------------------|
| BMS | 0x00/0x01      | Beam status: 0=UNBROKEN, 1=BROKEN |

### Screen

Screen side configuration is saved in non-volatile memory.
At the first power on, no side is defined, and it must be set by the user.

It is then saved in NVM (Non-Volatile Memory) and used for subsequent power-on.

| CMD | DATA (4 bytes) | Description                          |
|-----|----------------|--------------------------------------|
| DIS | Pattern ID     | Request specific pattern to display  |
| DSD | 0x00/0x01      | Request screen side: 0=LEFT, 1=RIGHT |
| TCH | 0x00/0x01      | Notify touch: 0=LEFT, 1=RIGHT        |

### Lighting

| CMD  | DATA (4 bytes) | Description                           |
|------|----------------|---------------------------------------|
| SDC  | Duty cycles    | Set white, red, IR LEDs duty cycle    |
| DCI  | None           | Request current duty cycles           |
| TON  | None           | Turn on the lighting module           |
| TOF  | None           | Turn off the lighting module          |

## Notes
- All messages are 8 bytes: 1 byte ID, 3 bytes CMD, 4 bytes DATA.
- For commands with no data, pad DATA with zeros.
- For status or other values, encode as uint8 or appropriate type and pad/truncate to 4 bytes.
- For string data, use UTF-8 and pad/truncate to 4 bytes.

## Example encoding/decoding

To encode a message:
- ID: 0x02 (Feeder)
- CMD: 'RWD'
- DATA: b'\x00\x00\x00\x00'

Result: b'\x02RWD\x00\x00\x00\x00'

To decode, split the bytes accordingly.

