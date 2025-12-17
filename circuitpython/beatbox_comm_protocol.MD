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

Messages common to all modules:

| Message ID / Header | Message / Data Bytes | Description                                 |
|---------------------|----------------------|---------------------------------------------|
| SCAN                |                      | Sent by the MAIN module to SCAN for modules |
| ID                  | Module ID (          | Sent in response to SCAN message            |

## Module specific messages

### Feeder 

| Message | Message ID | Description |
|---------|------------|-------------|
|   |            |             |



### Nosepoke

### Screen

### Lighting

