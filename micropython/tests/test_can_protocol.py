import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))

from can_protocol import (  # noqa: E402
    CommonCommand,
    GenericCommand,
    MessageType,
    Priority,
    build_identifier,
    normalize_payload,
    parse_identifier,
)


def test_build_and_parse_identifier_round_trip():
    can_id = build_identifier(
        priority=Priority.CONTROL,
        module_id=0x6,
        msg_type=MessageType.REQUEST_MODULE,
        command=0x3,
    )
    priority, module_id, msg_type, command = parse_identifier(can_id)

    assert can_id == 0b001_0110_10_11
    assert priority == Priority.CONTROL
    assert module_id == 0x6
    assert msg_type == MessageType.REQUEST_MODULE
    assert command == 0x3


def test_common_scan_request_encoding():
    can_id = build_identifier(
        priority=Priority.STATUS,
        module_id=0xF,
        msg_type=MessageType.REQUEST_COMMON,
        command=CommonCommand.SCAN,
    )
    assert can_id == 0b011_1111_00_00


def test_common_status_request_encoding():
    can_id = build_identifier(
        priority=Priority.STATUS,
        module_id=0x6,
        msg_type=MessageType.REQUEST_COMMON,
        command=CommonCommand.GET_STATUS,
    )
    assert can_id == 0b011_0110_00_01


def test_generic_toggle_command_encoding():
    can_id = build_identifier(
        priority=Priority.CONTROL,
        module_id=0x0,
        msg_type=MessageType.REQUEST_MODULE,
        command=GenericCommand.TOGGLE_CAN_LED,
    )
    assert can_id == 0b001_0000_10_00


def test_normalize_payload_limits_to_8_bytes():
    assert normalize_payload(None) == bytearray()
    assert normalize_payload(b"abc") == bytearray(b"abc")

    try:
        normalize_payload(b"123456789")
        assert False, "Expected ValueError for payload > 8 bytes"
    except ValueError:
        pass

