import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))

import cli_protocol  # noqa: E402
from can_protocol import (  # noqa: E402
    CommonCommand,
    FeederCommand,
    GenericCommand,
    IRBarrierCommand,
    LightingCommand,
    MessageType,
    ModuleMap,
    NosepokeCommand,
    Priority,
    ScreenCommand,
    build_identifier,
    parse_identifier,
)


def parse(line):
    return cli_protocol.parse_command(line)


def expected_id(module, priority, msg_type, command):
    return build_identifier(
        priority=priority,
        module_id=ModuleMap.get_id(module),
        msg_type=msg_type,
        command=command,
    )


def test_scan_broadcasts_common_scan():
    parsed = parse("SCAN")

    assert parsed.can_id == expected_id(
        "BROADCAST", Priority.STATUS, MessageType.REQUEST_COMMON, CommonCommand.SCAN
    )
    assert parsed.payload == bytearray()
    assert not parsed.is_local


def test_status_defaults_to_broadcast_and_accepts_module():
    assert parse("STATUS").can_id == expected_id(
        "BROADCAST", Priority.STATUS, MessageType.REQUEST_COMMON, CommonCommand.GET_STATUS
    )
    assert parse("status feeder").can_id == expected_id(
        "FEEDER", Priority.STATUS, MessageType.REQUEST_COMMON, CommonCommand.GET_STATUS
    )


def test_reset_encodes_delay_big_endian():
    parsed = parse("RESET NOSEPOKE 500")

    assert parsed.can_id == expected_id(
        "NOSEPOKE", Priority.CONTROL, MessageType.REQUEST_COMMON, CommonCommand.RESET
    )
    assert parsed.payload == bytearray([0x01, 0xF4])
    assert parse("RESET").payload == bytearray([0, 0])


def test_reset_without_module_still_accepts_delay():
    parsed = parse("RESET 500")

    assert parsed.can_id == expected_id(
        "BROADCAST", Priority.CONTROL, MessageType.REQUEST_COMMON, CommonCommand.RESET
    )
    assert parsed.payload == bytearray([0x01, 0xF4])


def test_led_defaults_to_generic():
    assert parse("LED").can_id == expected_id(
        "GENERIC", Priority.CONTROL, MessageType.REQUEST_MODULE, GenericCommand.TOGGLE_CAN_LED
    )


def test_feed_defaults_to_feeder_module():
    parsed = parse("FEED")

    assert parsed.can_id == expected_id(
        "FEEDER", Priority.CONTROL, MessageType.REQUEST_MODULE, FeederCommand.REQUEST_REWARD
    )
    _priority, module_id, msg_type, _command = parse_identifier(parsed.can_id)
    # FeederModule ignores broadcast requests, so the frame must be addressed directly.
    assert module_id == ModuleMap.get_id("FEEDER")
    assert msg_type == MessageType.REQUEST_MODULE


def test_beam_and_barrier_defaults():
    assert parse("BEAM").can_id == expected_id(
        "NOSEPOKE", Priority.STATUS, MessageType.REQUEST_MODULE, NosepokeCommand.GET_BEAM_STATUS
    )
    assert parse("BARRIER").can_id == expected_id(
        "IR_BARRIER", Priority.STATUS, MessageType.REQUEST_MODULE,
        IRBarrierCommand.GET_BARRIER_STATUS,
    )


def test_pattern_targets_screens():
    parsed = parse("PATTERN SCREEN_RIGHT 3")

    assert parsed.can_id == expected_id(
        "SCREEN_RIGHT", Priority.CONTROL, MessageType.REQUEST_MODULE,
        ScreenCommand.DISPLAY_PATTERN,
    )
    assert parsed.payload == bytearray([3])
    # Without a module the request is broadcast to both screens.
    assert parse("PATTERN 3").can_id == expected_id(
        "BROADCAST", Priority.CONTROL, MessageType.REQUEST_MODULE,
        ScreenCommand.DISPLAY_PATTERN,
    )


def test_duty_builds_white_red_ir_payload():
    parsed = parse("DUTY 100 0 0xFF")

    assert parsed.can_id == expected_id(
        "LIGHTING", Priority.CONTROL, MessageType.REQUEST_MODULE, LightingCommand.SET_DUTY
    )
    assert parsed.payload == bytearray([100, 0, 255])
    assert parse("DUTY LIGHTING 10").payload == bytearray([10])


def test_getduty_requests_lighting_duty():
    assert parse("GETDUTY").can_id == expected_id(
        "LIGHTING", Priority.STATUS, MessageType.REQUEST_MODULE, LightingCommand.GET_DUTY
    )


def test_light_on_requires_group_and_maps_names_to_indexes():
    parsed = parse("LIGHT ON WHITE")

    assert parsed.can_id == expected_id(
        "LIGHTING", Priority.CONTROL, MessageType.REQUEST_MODULE, LightingCommand.TURN_ON
    )
    assert parsed.payload == bytearray([cli_protocol.LIGHTING_GROUPS.index("WHITE")])
    assert parse("LIGHT ON 0").payload == bytearray([0])


def test_light_off_without_group_turns_everything_off():
    parsed = parse("LIGHT OFF")

    assert parsed.can_id == expected_id(
        "LIGHTING", Priority.CONTROL, MessageType.REQUEST_MODULE, LightingCommand.TURN_OFF
    )
    assert parsed.payload == bytearray()


def test_raw_can_accepts_names_numbers_and_hex_payload():
    parsed = parse("CAN STATUS FEEDER REQ_MODULE 1 0x0A 255")

    assert parsed.can_id == expected_id(
        "FEEDER", Priority.STATUS, MessageType.REQUEST_MODULE, 0x1
    )
    assert parsed.payload == bytearray([0x0A, 0xFF])
    assert parse("CAN 3 2 2 1").can_id == parse("CAN STATUS FEEDER REQ_MODULE 1").can_id


def test_local_commands_do_not_produce_can_frames():
    ping = parse("PING")
    assert ping.is_local and ping.local_reply == "PONG"
    assert ping.can_id is None

    assert parse("MODULES").local_reply.startswith("MODULES GENERIC=0")
    assert "SCAN" in parse("HELP").local_reply


def test_blank_and_comment_lines_are_ignored():
    assert parse("") is None
    assert parse("   ") is None
    assert parse("# just a comment") is None
    assert parse("SCAN # discover modules").name == "SCAN"


@pytest.mark.parametrize(
    "line",
    [
        "NOPE",
        "SCAN EXTRA",
        "STATUS FEEDER LIGHTING",
        "DUTY",
        "DUTY 300",
        "DUTY -1",
        "LIGHT ON",
        "LIGHT SIDEWAYS",
        "LIGHT ON PURPLE",
        "PATTERN",
        "CAN 1 2 3",
        "CAN 8 FEEDER REQ_MODULE 1",
        "CAN STATUS FEEDER REQ_MODULE 4",
        "CAN STATUS 16 REQ_MODULE 1",
        "RESET FEEDER 70000",
    ],
)
def test_invalid_commands_raise_command_error(line):
    with pytest.raises(cli_protocol.CommandError):
        parse(line)


def test_payload_length_is_capped_to_eight_bytes():
    with pytest.raises(cli_protocol.CommandError):
        parse("CAN STATUS FEEDER REQ_MODULE 1 1 2 3 4 5 6 7 8 9")


def test_every_command_has_usage_and_description():
    assert set(cli_protocol.COMMAND_NAMES) == set(cli_protocol.COMMANDS)
    for usage, description in cli_protocol.help_lines():
        assert usage and description
