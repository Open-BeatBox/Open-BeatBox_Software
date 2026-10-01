"""Text command protocol: tokens -> Intent.

Grammar (case insensitive, ``#`` starts a comment)::

    SCAN                                    broadcast common SCAN
    STATUS [module]                         common GET_STATUS
    RESET [module] [delay_ms]               common RESET
    LED [module]                            GenericCommand.TOGGLE_CAN_LED
    FEED [module]                           FeederCommand.REQUEST_REWARD
    REWARD [module] ON|OFF                  FeederCommand.REWARD_ON / REWARD_OFF
    REWARDSTATUS [module]                   FeederCommand.GET_REWARD_STATUS
    BEAM [module]                           NosepokeCommand.GET_BEAM_STATUS
    BARRIER [module]                        IRBarrierCommand.GET_BARRIER_STATUS
    PATTERN [module] <pattern_id>           ScreenCommand.DISPLAY_PATTERN
    DUTY [module] <white> <red> <ir>        LightingCommand.SET_DUTY
    GETDUTY [module]                        LightingCommand.GET_DUTY
    LIGHT [module] ON|OFF [group]           LightingCommand.TURN_ON / TURN_OFF
    CAN <prio> <module> <type> <cmd> [b..]  raw frame
    MODULES / HELP / PING                   answered locally, no CAN traffic

An optional leading module token is consumed only when it is a known module name
(or ``ALL``/``BROADCAST``), so tokens such as ``ON`` or numbers are never mistaken
for a module. Every handler delegates the CAN encoding to bb_intents, so the CLI
and the V2 pin map cannot drift apart.
"""

import bb_intents as intents
from bb_intents import Intent
from can_protocol import (
    COMMAND_MASK,
    LIGHTING_GROUPS,
    MODULE_ID_MASK,
    MessageType,
    ModuleMap,
    PRIORITY_MASK,
    Priority,
)

ParsedCommand = Intent  # backwards-compatible alias

PRIORITY_NAMES = {
    "ERROR": Priority.ERROR,
    "CONTROL": Priority.CONTROL,
    "EVENT": Priority.EVENT,
    "STATUS": Priority.STATUS,
    "TELEMETRY": Priority.TELEMETRY,
}

MESSAGE_TYPE_NAMES = {
    "REQUEST_COMMON": MessageType.REQUEST_COMMON,
    "REQ_COMMON": MessageType.REQUEST_COMMON,
    "RESPONSE_COMMON": MessageType.RESPONSE_COMMON,
    "RSP_COMMON": MessageType.RESPONSE_COMMON,
    "REQUEST_MODULE": MessageType.REQUEST_MODULE,
    "REQ_MODULE": MessageType.REQUEST_MODULE,
    "RESPONSE_MODULE": MessageType.RESPONSE_MODULE,
    "RSP_MODULE": MessageType.RESPONSE_MODULE,
}

MODULE_ALIASES = {"ALL": "BROADCAST", "BROADCAST": "BROADCAST"}


class CommandError(Exception):
    """Raised when a command line cannot be translated into an Intent."""


# ─── token helpers ───────────────────────────────────────────

def _parse_int(token, low, high, what):
    text = token.strip()
    try:
        if text[:2].lower() == "0x":
            value = int(text[2:], 16)
        elif text[:2].lower() == "0b":
            value = int(text[2:], 2)
        else:
            value = int(text, 10)
    except (ValueError, IndexError):
        raise CommandError("invalid %s '%s'" % (what, token))
    if value < low or value > high:
        raise CommandError("%s '%s' out of range [%d..%d]" % (what, token, low, high))
    return value


def _parse_byte(token):
    return _parse_int(token, 0, 255, "byte")


def _lookup_module(token):
    """Return the module id for a name token, or None if it is not a module name."""
    name = token.upper()
    name = MODULE_ALIASES.get(name, name)
    return ModuleMap.get_id(name)


def _parse_module(token):
    module_id = _lookup_module(token)
    if module_id is None:
        module_id = _parse_int(token, 0, MODULE_ID_MASK, "module")
    return module_id


def _take_module(args, default_module):
    """Pop a leading module token when present, else fall back to the default."""
    if args and _lookup_module(args[0]) is not None:
        return _lookup_module(args[0]), args[1:]
    return ModuleMap.get_id(default_module), args


def _expect_arg_count(name, args, minimum, maximum):
    if len(args) < minimum or len(args) > maximum:
        raise CommandError("%s expects %s argument(s), got %d" % (
            name,
            str(minimum) if minimum == maximum else "%d..%d" % (minimum, maximum),
            len(args),
        ))


def _expect_on_off(name, token):
    state = token.upper()
    if state not in ("ON", "OFF"):
        raise CommandError("%s expects ON or OFF, got '%s'" % (name, token))
    return state == "ON"


def _parse_group(token):
    upper = token.upper()
    if upper in LIGHTING_GROUPS:
        return LIGHTING_GROUPS.index(upper)
    return _parse_int(token, 0, len(LIGHTING_GROUPS) - 1, "group")


def _parse_priority(token):
    value = PRIORITY_NAMES.get(token.upper())
    if value is None:
        value = _parse_int(token, 0, PRIORITY_MASK, "priority")
    return value


def _parse_msg_type(token):
    value = MESSAGE_TYPE_NAMES.get(token.upper())
    if value is None:
        value = _parse_int(token, 0, 0x3, "message type")
    return value


# ─── handlers ────────────────────────────────────────────────

def _cmd_scan(name, args):
    _expect_arg_count(name, args, 0, 0)
    return intents.scan()


def _cmd_status(name, args):
    module_id, args = _take_module(args, "BROADCAST")
    _expect_arg_count(name, args, 0, 0)
    return intents.status(module_id)


def _cmd_reset(name, args):
    module_id, args = _take_module(args, "BROADCAST")
    _expect_arg_count(name, args, 0, 1)
    delay_ms = _parse_int(args[0], 0, 0xFFFF, "delay_ms") if args else 0
    return intents.reset(module_id, delay_ms)


def _cmd_led(name, args):
    module_id, args = _take_module(args, "GENERIC")
    _expect_arg_count(name, args, 0, 0)
    return intents.toggle_led(module_id)


def _cmd_feed(name, args):
    # The feeder ignores REQUEST_REWARD on BROADCAST, so it must be addressed.
    module_id, args = _take_module(args, "FEEDER")
    _expect_arg_count(name, args, 0, 0)
    return intents.request_reward(module_id)


def _cmd_reward(name, args):
    module_id, args = _take_module(args, "FEEDER")
    _expect_arg_count(name, args, 1, 1)
    return intents.reward(_expect_on_off(name, args[0]), module_id)


def _cmd_rewardstatus(name, args):
    module_id, args = _take_module(args, "FEEDER")
    _expect_arg_count(name, args, 0, 0)
    return intents.get_reward_status(module_id)


def _cmd_beam(name, args):
    module_id, args = _take_module(args, "NOSEPOKE")
    _expect_arg_count(name, args, 0, 0)
    return intents.beam(module_id)


def _cmd_barrier(name, args):
    module_id, args = _take_module(args, "IR_BARRIER")
    _expect_arg_count(name, args, 0, 0)
    return intents.barrier(module_id)


def _cmd_pattern(name, args):
    module_id, args = _take_module(args, "BROADCAST")
    _expect_arg_count(name, args, 1, 1)
    return intents.pattern(module_id, _parse_byte(args[0]))


def _cmd_duty(name, args):
    module_id, args = _take_module(args, "LIGHTING")
    _expect_arg_count(name, args, 1, 3)
    return intents.set_duty(module_id, [_parse_byte(a) for a in args])


def _cmd_getduty(name, args):
    module_id, args = _take_module(args, "LIGHTING")
    _expect_arg_count(name, args, 0, 0)
    return intents.get_duty(module_id)


def _cmd_light(name, args):
    module_id, args = _take_module(args, "LIGHTING")
    _expect_arg_count(name, args, 1, 2)
    on = _expect_on_off(name, args[0])
    group = _parse_group(args[1]) if len(args) > 1 else None
    if on:
        if group is None:
            raise CommandError("LIGHT ON expects a group (%s or index)"
                               % "/".join(LIGHTING_GROUPS))
        return intents.light(group, True, module_id)
    if group is None:
        return intents.light_off_all(module_id)
    return intents.light(group, False, module_id)


def _cmd_can(name, args):
    if len(args) < 4:
        raise CommandError("CAN expects <prio> <module> <type> <cmd> [bytes...]")
    return intents.raw(
        _parse_priority(args[0]),
        _parse_module(args[1]),
        _parse_msg_type(args[2]),
        _parse_int(args[3], 0, COMMAND_MASK, "command"),
        [_parse_byte(a) for a in args[4:]],
    )


def _cmd_ping(name, args):
    _expect_arg_count(name, args, 0, 0)
    return intents.local(name, "PONG")


def _cmd_modules(name, args):
    _expect_arg_count(name, args, 0, 0)
    entries = ["%s=%d" % (mod_name, mod_id) for mod_name, mod_id in ModuleMap._entries]
    return intents.local(name, "MODULES " + " ".join(entries))


def _cmd_help(name, args):
    _expect_arg_count(name, args, 0, 0)
    return intents.local(name, "HELP " + " | ".join(usage_lines()))


# name -> (handler, usage, description)
COMMANDS = {
    "SCAN": (_cmd_scan, "SCAN", "Broadcast a module discovery request"),
    "STATUS": (_cmd_status, "STATUS [module]", "Request module status (default: all)"),
    "RESET": (_cmd_reset, "RESET [module] [delay_ms]", "Request a module reset"),
    "LED": (_cmd_led, "LED [module]", "Toggle the CAN LED (default: GENERIC)"),
    "FEED": (_cmd_feed, "FEED [module]", "Request one reward (feeder-managed retries)"),
    "REWARD": (_cmd_reward, "REWARD [module] ON|OFF", "Drive the feeder motor directly"),
    "REWARDSTATUS": (_cmd_rewardstatus, "REWARDSTATUS [module]", "Read pellet presence"),
    "BEAM": (_cmd_beam, "BEAM [module]", "Read nosepoke beam status"),
    "BARRIER": (_cmd_barrier, "BARRIER [module]", "Read IR barrier status"),
    "PATTERN": (_cmd_pattern, "PATTERN [module] <pattern_id>", "Display a screen pattern"),
    "DUTY": (_cmd_duty, "DUTY [module] <white> [red] [ir]", "Set lighting duty cycles"),
    "GETDUTY": (_cmd_getduty, "GETDUTY [module]", "Read lighting duty cycles"),
    "LIGHT": (_cmd_light, "LIGHT [module] ON|OFF [group]", "Turn a lighting group on/off"),
    "CAN": (_cmd_can, "CAN <prio> <module> <type> <cmd> [bytes...]", "Send a raw CAN frame"),
    "MODULES": (_cmd_modules, "MODULES", "List known module names and ids"),
    "HELP": (_cmd_help, "HELP", "List supported commands"),
    "PING": (_cmd_ping, "PING", "Check that the main module is alive"),
}

COMMAND_NAMES = tuple(sorted(COMMANDS.keys()))


def usage_lines():
    """Return the usage string of every supported command, sorted by name."""
    return [COMMANDS[name][1] for name in COMMAND_NAMES]


def help_lines():
    """Return ``(usage, description)`` pairs for every supported command."""
    return [(COMMANDS[name][1], COMMANDS[name][2]) for name in COMMAND_NAMES]


def strip_comment(line):
    """Remove a trailing ``#`` comment and surrounding whitespace."""
    index = line.find("#")
    if index >= 0:
        line = line[:index]
    return line.strip()


def parse_command(line):
    """Translate a command line into an Intent.

    Returns ``None`` for empty or comment-only lines.
    Raises :class:`CommandError` for unknown commands or invalid arguments.
    """
    if isinstance(line, bytes):
        line = line.decode()
    line = strip_comment(line)
    if not line:
        return None

    tokens = line.split()
    name = tokens[0].upper()
    entry = COMMANDS.get(name)
    if entry is None:
        raise CommandError("unknown command '%s'" % tokens[0])
    try:
        return entry[0](name, tokens[1:])
    except ValueError as exc:      # raised by bb_intents / build_identifier
        raise CommandError(str(exc))