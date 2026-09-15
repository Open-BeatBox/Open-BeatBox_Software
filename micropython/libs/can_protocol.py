"""Beatbox CAN identifier and command helpers.

Identifier layout (11-bit standard CAN):
- bits 10..8: priority (3 bits)
- bits 7..4: module ID (4 bits)
- bits 3..2: message type (2 bits)
- bits 1..0: command/sub-command (2 bits)
"""

PRIORITY_MASK = 0x07
MODULE_ID_MASK = 0x0F
MESSAGE_TYPE_MASK = 0x03
COMMAND_MASK = 0x03

PRIO_SHIFT = 0x8
MODULE_ID_SHIFT = 0x4
MSG_TYPE_SHIFT = 0x2

class Priority:
    ERROR = 0b000
    CONTROL = 0b001
    EVENT = 0b010
    STATUS = 0b011
    TELEMETRY = 0b100


class MessageType:
    REQUEST_COMMON = 0b00
    RESPONSE_COMMON = 0b01
    REQUEST_MODULE = 0b10
    RESPONSE_MODULE = 0b11

    @classmethod
    def is_request(cls, msg_type):
        return msg_type in (cls.REQUEST_COMMON, cls.REQUEST_MODULE)

    @classmethod
    def is_response(cls, msg_type):
        return msg_type in (cls.RESPONSE_COMMON, cls.RESPONSE_MODULE)


class CommonCommand:
    SCAN = 0x0
    GET_STATUS = 0x1
    ERROR = 0x2
    RESET = 0x3


class GenericCommand:
    TOGGLE_CAN_LED = 0x0
    USER_BUTTON_EVENT = 0x1


class NosepokeCommand:
    GET_BEAM_STATUS = 0x0
    BEAM_EVENT = 0x1


class ScreenCommand:
    DISPLAY_PATTERN = 0x0
    TOUCH_EVENT = 0x1


class LightingCommand:
    SET_DUTY = 0x0
    GET_DUTY = 0x1
    TURN_ON = 0x2
    TURN_OFF = 0x3


class FeederCommand:
    REQUEST_REWARD = 0x1
    REWARD_DELIVERED = 0x1


class IRBarrierCommand:
    GET_BARRIER_STATUS = 0x0
    BARRIER_EVENT = 0x1


def build_identifier(priority, module_id, msg_type, command):
    priority_bits = priority & PRIORITY_MASK
    module_nibble = module_id & MODULE_ID_MASK
    msg_bits = msg_type & MESSAGE_TYPE_MASK
    cmd_nibble = command & COMMAND_MASK
    return (priority_bits << PRIO_SHIFT) | (module_nibble << MODULE_ID_SHIFT) | (
                msg_bits << MSG_TYPE_SHIFT) | cmd_nibble


def parse_identifier(can_id):
    return (
        (can_id >> 8) & PRIORITY_MASK,
        (can_id >> 4) & MODULE_ID_MASK,
        (can_id >> 2) & MESSAGE_TYPE_MASK,
        can_id & COMMAND_MASK,
    )


def normalize_payload(payload):
    if payload is None:
        return bytearray()
    if isinstance(payload, bytearray):
        out = payload
    elif isinstance(payload, bytes):
        out = bytearray(payload)
    else:
        out = bytearray(payload)
    if len(out) > 8:
        raise ValueError("CAN payload must be <= 8 bytes")
    return out
