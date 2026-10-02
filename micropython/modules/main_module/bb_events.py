"""Events: in-process descriptions of what happened on the CAN bus.

Producer: MainModule._handle_can_message. Consumers: frontends.

A branch here must either name payload bytes or merge several wire encodings
into one logical event. If it does neither, fall through to _unknown.
"""

from can_protocol import (
    CommonCommand,
    FeederCommand,
    GenericCommand,
    IRBarrierCommand,
    LightingCommand,
    MessageType,
    ModuleMap,
    NosepokeCommand,
    ScreenCommand,
    parse_identifier,
)

SCAN_REPLY = "SCAN_REPLY"
STATUS = "STATUS"
ERROR = "ERROR"
BEAM_EVENT = "BEAM_EVENT"
BEAM_STATUS = "BEAM_STATUS"
REWARD_STATUS = "REWARD_STATUS"
TOUCH = "TOUCH"
DUTY = "DUTY"
BARRIER_EVENT = "BARRIER_EVENT"
BARRIER_STATUS = "BARRIER_STATUS"
CAN_LED_STATE = "CAN_LED_STATE"
USER_BUTTON = "USER_BUTTON"
REQUEST = "REQUEST"       # any request seen on the bus; observability only
UNKNOWN = "UNKNOWN"

STATUS_NAMES = {0: "INIT", 1: "IDLE", 2: "ACTIVE", 3: "ERROR"}
STATUS_ERROR_CODE = 3

_MODULE_RESPONSE = {}     # module_id -> decoder, populated at the bottom


class Event:
    def __init__(self, name, module_id, **fields):
        self.name = name
        self.module_id = module_id
        self.fields = fields

    def __repr__(self):
        return "Event(%s, mod=%s, %r)" % (self.name, self.module_id, self.fields)


def status_name(code):
    return STATUS_NAMES.get(code, "UNKNOWN")


def decode(can_id, data):
    prio, module_id, msg_type, command = parse_identifier(can_id)
    if msg_type == MessageType.RESPONSE_COMMON:
        return _decode_common_response(prio, module_id, command, data)
    if msg_type == MessageType.RESPONSE_MODULE:
        return _decode_module_response(prio, module_id, command, data)
    return _decode_request(prio, module_id, command, data, msg_type)


def _unknown(module_id, command, data):
    return Event(UNKNOWN, module_id, command=command, data=bytes(data))


def _unknown_decoder(prio, module_id, command, data):
    return _unknown(module_id, command, data)


def _decode_request(prio, module_id, command, data, msg_type):
    return Event(REQUEST, module_id, msg_type=msg_type,
                 command=command, data=bytes(data))


def _decode_common_response(prio, module_id, command, data):
    if command == CommonCommand.SCAN:
        return Event(SCAN_REPLY, module_id, code=data[0] if data else 255)
    if command == CommonCommand.GET_STATUS:
        return Event(STATUS, module_id, code=data[0] if data else 255)
    if command == CommonCommand.ERROR:
        return Event(ERROR, module_id,
                     code=data[0] if data else 255, context=bytes(data[1:]))
    return _unknown(module_id, command, data)


def _decode_module_response(prio, module_id, command, data):
    return _MODULE_RESPONSE.get(module_id, _unknown_decoder)(
        prio, module_id, command, data)


def _decode_generic_response(prio, module_id, command, data):
    if command == GenericCommand.TOGGLE_CAN_LED:
        return Event(CAN_LED_STATE, module_id, state=data[0] if data else 255)
    if command == GenericCommand.USER_BUTTON_EVENT:
        return Event(USER_BUTTON, module_id, state=data[0] if data else 1)
    return _unknown(module_id, command, data)


def _decode_feeder_response(prio, module_id, command, data):
    if command == FeederCommand.REWARD_STATUS:
        return Event(REWARD_STATUS, module_id,
                     state=data[0] if data else 0,
                     attempts=data[1] if len(data) > 1 else 0)
    return _unknown(module_id, command, data)


def _decode_nosepoke_response(prio, module_id, command, data):
    if command == NosepokeCommand.BEAM_EVENT:
        return Event(BEAM_EVENT, module_id, state=data[0] if data else 1)
    if command == NosepokeCommand.GET_BEAM_STATUS:
        return Event(BEAM_STATUS, module_id, state=data[0] if data else 0)
    return _unknown(module_id, command, data)


def _decode_screen_response(prio, module_id, command, data):
    if command == ScreenCommand.TOUCH_EVENT:
        return Event(TOUCH, module_id, state=data[0] if data else 1)
    return _unknown(module_id, command, data)


def _decode_lighting_response(prio, module_id, command, data):
    if command == LightingCommand.GET_DUTY and len(data) >= 3:
        return Event(DUTY, module_id, white=data[0], red=data[1], ir=data[2])
    return _unknown(module_id, command, data)


def _decode_barrier_response(prio, module_id, command, data):
    if command == IRBarrierCommand.BARRIER_EVENT and len(data) >= 2:
        return Event(BARRIER_EVENT, module_id, line=data[0], state=data[1])
    if command == IRBarrierCommand.GET_BARRIER_STATUS:
        return Event(BARRIER_STATUS, module_id, mask=data[0] if data else 0)
    return _unknown(module_id, command, data)


_MODULE_RESPONSE[ModuleMap.get_id("GENERIC")] = _decode_generic_response
_MODULE_RESPONSE[ModuleMap.get_id("FEEDER")] = _decode_feeder_response
_MODULE_RESPONSE[ModuleMap.get_id("NOSEPOKE")] = _decode_nosepoke_response
_MODULE_RESPONSE[ModuleMap.get_id("SCREEN_LEFT")] = _decode_screen_response
_MODULE_RESPONSE[ModuleMap.get_id("SCREEN_RIGHT")] = _decode_screen_response
_MODULE_RESPONSE[ModuleMap.get_id("LIGHTING")] = _decode_lighting_response
_MODULE_RESPONSE[ModuleMap.get_id("IR_BARRIER")] = _decode_barrier_response