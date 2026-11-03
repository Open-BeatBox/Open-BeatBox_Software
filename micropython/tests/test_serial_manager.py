"""Tests for the generic line-oriented serial transport.

SerialManager must stay protocol agnostic: it queues outgoing messages,
terminates them with a newline, reassembles incoming lines (even when they
are split across reads) and hands complete, stripped lines to the registered
callbacks without ever dying on a faulty callback or a faulty UART.
"""

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))

import bb_ticks  # noqa: E402
from serial_manager import SerialManager  # noqa: E402


class FakeSerial:
    """Minimal UART stub: `chunks` are served one per read() call."""

    def __init__(self, chunks=None, fail_write=False, fail_read=False):
        self.chunks = list(chunks or [])
        self.written = []
        self.fail_write = fail_write
        self.fail_read = fail_read

    def any(self):
        if self.fail_read:
            raise OSError("read failure")
        return len(self.chunks)

    def read(self):
        return self.chunks.pop(0) if self.chunks else None

    def write(self, data):
        if self.fail_write:
            raise OSError("write failure")
        self.written.append(data)
        return len(data)


def run_handler(manager, timeout=0.15):
    """Run serial_handler_task for a short while, then cancel it."""

    async def _run():
        task = asyncio.ensure_future(manager.serial_handler_task())
        try:
            await asyncio.wait_for(task, timeout)
        except asyncio.TimeoutError:
            pass

    asyncio.new_event_loop().run_until_complete(_run())


# --------------------------------------------------------------------------
# send_message / _write_to_data_port
# --------------------------------------------------------------------------

def test_send_message_queues_bytes_untouched_and_encodes_everything_else():
    manager = SerialManager()

    manager.send_message(b"already:bytes")
    manager.send_message("a:string")
    manager.send_message(42)

    assert manager.tx_queue == [b"already:bytes", b"a:string", b"42"]


def test_write_terminates_messages_with_crlf():
    serial = FakeSerial()
    manager = SerialManager(serial=serial)

    assert manager._write_to_data_port(b"hello") is True
    assert serial.written == [b"hello\r\n"]


def test_write_accepts_str_and_does_not_duplicate_existing_terminators():
    serial = FakeSerial()
    manager = SerialManager(serial=serial)

    manager._write_to_data_port("plain")
    manager._write_to_data_port(b"has_cr\r")
    manager._write_to_data_port(b"has_crlf\r\n")
    manager._write_to_data_port(b"has_lf\n")

    assert serial.written == [b"plain\r\n", b"has_cr\r\n",
                              b"has_crlf\r\n", b"has_lf\n"]


def test_write_fails_gracefully_without_serial_or_on_uart_error():
    assert SerialManager()._write_to_data_port(b"no serial") is False
    assert SerialManager(serial=FakeSerial(fail_write=True))._write_to_data_port(
        b"boom") is False


# --------------------------------------------------------------------------
# _read_from_data_port
# --------------------------------------------------------------------------

def test_read_decodes_bytes_and_returns_empty_string_when_idle():
    manager = SerialManager(serial=FakeSerial([b"data\n"]))

    assert manager._read_from_data_port() == "data\n"
    assert manager._read_from_data_port() == ""
    assert SerialManager()._read_from_data_port() == ""


def test_read_swallows_uart_errors():
    manager = SerialManager(serial=FakeSerial(fail_read=True))

    assert manager._read_from_data_port() == ""


# --------------------------------------------------------------------------
# serial_handler_task
# --------------------------------------------------------------------------

def test_handler_marks_itself_running_and_started():
    manager = SerialManager(serial=FakeSerial())
    assert manager.running is False
    assert manager.status == "OK"

    run_handler(manager, timeout=0.05)

    assert manager.running is True
    assert manager.status == "STARTED"


def test_handler_drains_tx_queue_in_order():
    serial = FakeSerial()
    manager = SerialManager(serial=serial)
    manager.send_message("first")
    manager.send_message("second")
    manager.send_message(b"third")

    run_handler(manager)

    assert serial.written == [b"first\r\n", b"second\r\n", b"third\r\n"]
    assert manager.tx_queue == []


def test_handler_dispatches_complete_lines_to_every_callback():
    manager = SerialManager(serial=FakeSerial([b"one:cmd\ntwo:cmd\n"]))
    first, second = [], []
    manager.register_rx_callback(first.append)
    manager.register_rx_callback(second.append)

    run_handler(manager)

    assert first == ["one:cmd", "two:cmd"]
    assert second == ["one:cmd", "two:cmd"]
    assert manager.status != "ERROR"


def test_handler_reassembles_lines_split_across_reads():
    manager = SerialManager(serial=FakeSerial([b"split", b":com", b"mand\n"]))
    received = []
    manager.register_rx_callback(received.append)

    run_handler(manager)

    assert received == ["split:command"]


def test_handler_keeps_incomplete_line_buffered_and_strips_whitespace():
    manager = SerialManager(serial=FakeSerial([b"  done:cmd  \r\n\n\npartial"]))
    received = []
    manager.register_rx_callback(received.append)

    run_handler(manager)

    # Blank lines are dropped, trailing \r stripped, partial line kept for later
    assert received == ["done:cmd"]
    assert manager.rx_buffer == "partial"


def test_handler_survives_a_failing_callback_and_flags_an_error():
    def broken(_line):
        raise ValueError("callback blew up")

    manager = SerialManager(serial=FakeSerial([b"cmd:one\n", b"cmd:two\n"]))
    received = []
    manager.register_rx_callback(broken)
    manager.register_rx_callback(received.append)

    run_handler(manager)

    assert manager.status == "ERROR"
    # A broken callback must not stop the other callbacks nor the loop
    assert received == ["cmd:one", "cmd:two"]


def test_handler_updates_last_activity_only_on_incoming_data():
    manager = SerialManager(serial=FakeSerial())
    idle_stamp = manager.last_activity
    run_handler(manager, timeout=0.05)
    assert manager.last_activity == idle_stamp

    manager = SerialManager(serial=FakeSerial([b"cmd\n"]))
    before = manager.last_activity
    run_handler(manager, timeout=0.05)
    assert bb_ticks.ticks_diff(manager.last_activity, before) > 0


@pytest.mark.parametrize("step_us, expected_status", [
    (45_000, "ERROR"),       # one pass takes 45ms > 40ms budget
    (2_000_000, "STARTED"),  # more than 1s without rx: back to idle status
])
def test_handler_reports_slow_processing_and_resets_when_idle(
        monkeypatch, step_us, expected_status):
    clock = {"now": 0}

    def fake_ticks_us():
        clock["now"] += step_us
        return clock["now"]

    monkeypatch.setattr(bb_ticks, "ticks_us", fake_ticks_us)

    manager = SerialManager(serial=FakeSerial())
    run_handler(manager, timeout=0.05)

    assert manager.status == expected_status
