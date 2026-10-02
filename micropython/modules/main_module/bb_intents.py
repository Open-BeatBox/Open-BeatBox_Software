"""Intents: in-process descriptions of a CAN frame to emit.

An intent already carries the encoded ``can_id`` and ``payload``; it is not a
wire format. Producers: cli_protocol, bb_v2_map. Consumer: MainModule.send_intent.
"""

from can_protocol import (
    CommonCommand,
    FeederCommand,
    GenericCommand,
    IRBarrierCommand,
    LIGHTING_GROUPS,
    LightingCommand,
    MessageType,
    ModuleMap,
    NosepokeCommand,
    Priority,
    ScreenCommand,
    build_identifier,
)

MAX_PAYLOAD_LEN = 8


class Intent:
    def __init__(self, name, can_id=None, payload=None, local_reply=None):
        self.name = name
        self.can_id = can_id
        self.payload = payload
        self.local_reply = local_reply

    @property
    def is_local(self):
        return self.local_reply is not None

    def __repr__(self):
        if self.is_local:
            return "Intent(%s, local=%r)" % (self.name, self.local_reply)
        return "Intent(%s, can_id=0x%03X, payload=%s)" % (
            self.name, self.can_id, list(self.payload))


def _payload(values):
    if len(values) > MAX_PAYLOAD_LEN:
        raise ValueError("payload must be <= %d bytes" % MAX_PAYLOAD_LEN)
    return bytearray(values)


def _frame(name, priority, module, msg_type, command, values=()):
    module_id = ModuleMap.get_id(module)
    if module_id is None:
        raise ValueError("unknown module %r" % (module,))
    can_id = build_identifier(priority=priority, module_id=module_id,
                              msg_type=msg_type, command=command)
    return Intent(name, can_id=can_id, payload=_payload(values))


def local(name, reply):
    return Intent(name, local_reply=reply)


# ─── common namespace ────────────────────────────────────────

def scan():
    return _frame("SCAN", Priority.STATUS, "BROADCAST",
                  MessageType.REQUEST_COMMON, CommonCommand.SCAN)


def status(module="BROADCAST"):
    return _frame("STATUS", Priority.STATUS, module,
                  MessageType.REQUEST_COMMON, CommonCommand.GET_STATUS)


def reset(module="BROADCAST", delay_ms=0):
    return _frame("RESET", Priority.CONTROL, module,
                  MessageType.REQUEST_COMMON, CommonCommand.RESET,
                  ((delay_ms >> 8) & 0xFF, delay_ms & 0xFF))


# ─── generic ─────────────────────────────────────────────────

def toggle_led(module="GENERIC"):
    return _frame("LED", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, GenericCommand.TOGGLE_CAN_LED)


# ─── feeder ──────────────────────────────────────────────────

def request_reward(module="FEEDER"):
    """Autonomous delivery: the feeder owns the retry loop."""
    return _frame("FEED", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, FeederCommand.REQUEST_REWARD)


def reward(on, module="FEEDER"):
    """Direct motor control, mirroring the V2 reward TTL line."""
    command = FeederCommand.REWARD_ON if on else FeederCommand.REWARD_OFF
    return _frame("REWARD", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, command)


def get_reward_status(module="FEEDER"):
    return _frame("REWARDSTATUS", Priority.STATUS, module,
                  MessageType.REQUEST_MODULE, FeederCommand.GET_REWARD_STATUS)


# ─── nosepoke / barrier ──────────────────────────────────────

def beam(module="NOSEPOKE"):
    return _frame("BEAM", Priority.STATUS, module,
                  MessageType.REQUEST_MODULE, NosepokeCommand.GET_BEAM_STATUS)


def barrier(module="IR_BARRIER"):
    return _frame("BARRIER", Priority.STATUS, module,
                  MessageType.REQUEST_MODULE, IRBarrierCommand.GET_BARRIER_STATUS)


# ─── screens ─────────────────────────────────────────────────

def pattern(module, pattern_id):
    return _frame("PATTERN", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, ScreenCommand.DISPLAY_PATTERN,
                  (pattern_id & 0xFF,))


# ─── lighting ────────────────────────────────────────────────

def set_duty(module, values):
    return _frame("DUTY", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, LightingCommand.SET_DUTY,
                  [v & 0xFF for v in values])


def get_duty(module="LIGHTING"):
    return _frame("GETDUTY", Priority.STATUS, module,
                  MessageType.REQUEST_MODULE, LightingCommand.GET_DUTY)


def light(group, on, module="LIGHTING"):
    """Always sends the group byte: V2 drives white and red independently,
    so TURN_OFF must never fall back to the 'all groups' empty form."""
    index = LIGHTING_GROUPS.index(group) if isinstance(group, str) else group
    command = LightingCommand.TURN_ON if on else LightingCommand.TURN_OFF
    return _frame("LIGHT", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, command, (index,))


def light_off_all(module="LIGHTING"):
    """CLI-only: bare 'LIGHT OFF', empty payload == all groups."""
    return _frame("LIGHT", Priority.CONTROL, module,
                  MessageType.REQUEST_MODULE, LightingCommand.TURN_OFF)


# ─── raw ─────────────────────────────────────────────────────

def raw(priority, module, msg_type, command, values=()):
    return _frame("CAN", priority, module, msg_type, command, values)