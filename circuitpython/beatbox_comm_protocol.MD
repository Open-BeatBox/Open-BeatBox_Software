# Beatbox communication protocol

The Beatbox communication protocol defines the structure and format of messages exchanged between Beatbox main MCU and the modules.
This protocol is based on the CAN bus system and uses specific message IDs and data formats to ensure proper communication.

## Message Structure
Each message consists of the following components:

## List of the current modules in Beatbox system

| Module Name     | Module ID | Description                        |
|-----------------|-----------|------------------------------------|
| Main MCU        | 0x01      | Central controller of the Beatbox  |
| Feeder Module   | 0x02      | Deliver the pellets to the animal  |
| Nosepoke Module | 0x03      | Detects nosepoke from the animal   |
| Screen Module   | 0x04      | Displays visual stimuli            |
| Lighting Module | 0x05      | Provides ambient lights to the box |


## Generic messages

This protocol exists outside the CAN protocol.

In case the CAN bus is used to implement this protocol, these messages 
represents the data payload of CAN message.

Message format for all modules:
 - Message ID / Header | Data Bytes

Payload is a string sent as bytes encoded in UTF-8.

Read messages
Write messages
Command messages
Acknowledgment messages


Messages common to all modules:

| Message ID / Header | Message / Data Bytes                                 | Description                                 |
|---------------------|------------------------------------------------------|---------------------------------------------|
| SCAN                |                                                      | Sent by the MAIN module to SCAN for modules |
| ID                  | Module ID                                            | Sent in response to SCAN message            |
| R_STATUS            |                                                      | Sent to a module to request STATUS info     |
| STATUS              | Current status as defined per the ModuleStatus class | Sent in response to R_STATUS message        |

## Module specific messages

### Feeder 

| Message ID / Header | Message / Data Bytes | Description             |
|---------------------|----------------------|-------------------------|
| REWARD              |                      | Request reward delivery |
| 

### Nosepoke

| Message ID / Header | Message / Data Bytes | Description        |
|---------------------|----------------------|--------------------|
| BEAM_STATUS         | BROKEN/UNBROKEN      | Notify beam status |

### Screen

Screen side configuration is saved in non-volatile memory.
At the first power on, no side is defined, and it must be set by the user.
It is then saved in NVM (Non-Volatile Memory) and used for subsequent power-on.

| Message ID / Header | Message / Data Bytes | Description                         |
|---------------------|----------------------|-------------------------------------|
| DISPLAY PATTERN     | PATTERN ID           | Request specific pattern to display |
| DISPLAY SIDE        | LEFT/RIGHT           | Request screen side                 |
| SCREEN TOUCH        | LEFT/RIGHT           | Notify touch on left or right side  |


### Lighting

| Message         | Message ID         | Description                           |
|-----------------|--------------------|---------------------------------------|
| SET DUTY CYCLE  | WHITE/RED/IR RATIO | Set white, red and IR LEDs duty cycle |
| DUTY CYCLE INFO |                    | Request current duty cycles           |
| TURN ON         |                    | Turn on the lighting module           |
| TURN OFF        |                    | Turn off the lighting module          |
