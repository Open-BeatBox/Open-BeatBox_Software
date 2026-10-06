"""Tests for the IR barrier module (8 active-low beams on GPIO IRQs).

What the firmware is supposed to do:
- register an IRQ on both edges for every line declared in the pin map,
- buffer raw edges in the IRQ and publish them from an async task, debounced
  per line, as EVENT BARRIER_EVENT frames ([line_index, broken]),
- answer a STATUS REQUEST_MODULE / GET_BARRIER_STATUS with
  [mask_of_broken_lines, last_triggered_line].
"""

import asyncio
import importlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "modules"))


def _line(module, preferred_index):
    """Return `preferred_index`, or the last line if the board has fewer.

    The wiring changes between benches (a single line on the prototype, eight
    on the final board), so the tests follow the pin map rather than hardcode a
    line count. This lets a test read "use line 3" and still run on a one-line
    board.
    """
    line_count = len(module.sensors)
    return preferred_index if preferred_index < line_count else line_count - 1


# --------------------------------------------------------------------------
# MicroPython hardware fakes
# --------------------------------------------------------------------------

class FakeLed:
    def __init__(self, pin_num=None):
        self.pin_num = pin_num

    def on(self):
        pass

    def off(self):
        pass

    def toggle(self):
        pass


class FakeCanManager:
    def __init__(self, can_id=None, *args, **kwargs):
        self.can_id = can_id
        self.sent = []
        self.callbacks = []

    def register_rx_callback(self, callback):
        self.callbacks.append(callback)

    def send_normal(self, can_id=None, data=b""):
        self.sent.append({"can_id": can_id, "data": bytearray(data)})

    async def can_rx_task(self):
        return None

    async def can_tx_task(self):
        return None


class FakePin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4
    IRQ_RISING = 8

    def __init__(self, pin_number, mode=None, pull=None, value=None):
        self.pin_number = pin_number
        self.mode = mode
        self.pull = pull
        self._value = 1 if value is None else value
        self.irq_trigger = None
        self.irq_handler = None

    def value(self, v=None):
        if v is None:
            return self._value
        self._value = v

    def irq(self, trigger=None, handler=None):
        self.irq_trigger = trigger
        self.irq_handler = handler

    # -- test helpers, the receivers are open drain and active low --
    def break_beam(self):
        """Something enters the beam: the line goes low and the IRQ fires."""
        self._value = 0
        if self.irq_handler:
            self.irq_handler(self)

    def clear_beam(self):
        """The beam is restored: the line goes high and the IRQ fires."""
        self._value = 1
        if self.irq_handler:
            self.irq_handler(self)


class _FakeMachine(types.ModuleType):
    def __init__(self):
        super().__init__("machine")
        self.Pin = FakePin


class FakeClock:
    """Monotonic microsecond clock the test drives by hand."""

    def __init__(self):
        self.now_us = 0

    def advance_us(self, delta_us):
        self.now_us += delta_us

    def ticks_us(self):
        return self.now_us

    @staticmethod
    def ticks_diff(a, b):
        return a - b


# --------------------------------------------------------------------------
# module loading / helpers
# --------------------------------------------------------------------------

def _fake_message(can_id, data=b""):
    return types.SimpleNamespace(id=can_id, data=bytearray(data))


@pytest.fixture
def ir_barrier_env(monkeypatch):
    """Import `ir_barrier` against fake hardware.

    Returns (beatbox_module, ir_barrier, clock). The fakes must be installed in
    sys.modules before the import, since `beatbox_module` does
    `from machine import Pin` at import time.
    """
    fake_bb_mpy_module = types.ModuleType("bb_mpy_module")
    fake_bb_mpy_module.Led = FakeLed

    fake_can_manager = types.ModuleType("can_manager")
    fake_can_manager.CanManager = FakeCanManager

    monkeypatch.setitem(sys.modules, "bb_mpy_module", fake_bb_mpy_module)
    monkeypatch.setitem(sys.modules, "can_manager", fake_can_manager)
    monkeypatch.setitem(sys.modules, "machine", _FakeMachine())

    # The module is built outside a running loop, so its tasks cannot be real.
    def fake_create_task(coro, **_kwargs):
        coro.close()
        return object()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    for module_name in ("beatbox_module", "ir_barrier"):
        sys.modules.pop(module_name, None)

    beatbox_module = importlib.import_module("beatbox_module")
    ir_barrier = importlib.import_module("ir_barrier")

    clock = FakeClock()
    monkeypatch.setattr(ir_barrier, "bb_ticks", clock)

    # Keep the coalescing loop fast.
    monkeypatch.setattr(ir_barrier.IRBarrierModule, "SEND_COALESCE_MS", 1)

    yield beatbox_module, ir_barrier, clock

    for module_name in ("beatbox_module", "ir_barrier"):
        sys.modules.pop(module_name, None)


def _run(coro, timeout=5.0):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout))
    finally:
        loop.close()


def _flush_events(module, expected_frames=1):
    """Let the async sender drain the IRQ buffer and emit its frames."""

    async def runner():
        task = asyncio.ensure_future(module._event_sender_task())
        try:
            for _ in range(200):
                if len(module.can_manager.sent) >= expected_frames:
                    break
                await asyncio.sleep(0.001)
        finally:
            task.cancel()

    _run(runner())


def _expected_frame(beatbox_module, ir_barrier, priority, command, payload):
    return {
        "can_id": beatbox_module.ModuleMap.build_can_id(
            beatbox_module.ModuleMap.get_id("IR_BARRIER"),
            priority,
            beatbox_module.MessageType.RESPONSE_MODULE,
            command,
        ),
        "data": bytearray(payload),
    }


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_init_registers_interrupt_on_all_lines(ir_barrier_env):
    _beatbox_module, ir_barrier, _clock = ir_barrier_env

    module = ir_barrier.IRBarrierModule()

    expected_pins = list(ir_barrier.PinMap.IR_BARRIER_LINES)
    assert expected_pins, "the pin map declares no IR barrier line"
    # line index != GPIO number: the mapping is the pin map's tuple order
    assert [sensor.pin_number for sensor in module.sensors] == expected_pins
    assert all(sensor.mode == FakePin.IN for sensor in module.sensors)
    assert all(sensor.pull == FakePin.PULL_UP for sensor in module.sensors)
    # both edges are needed to report "broken" and "clear" transitions
    assert all(
        sensor.irq_trigger == FakePin.IRQ_FALLING | FakePin.IRQ_RISING
        for sensor in module.sensors
    )
    assert module.last_triggered_line == 0xFF


def test_any_line_interrupt_sends_barrier_event_frame(ir_barrier_env):
    beatbox_module, ir_barrier, clock = ir_barrier_env

    module = ir_barrier.IRBarrierModule()
    line = _line(module, 3)

    clock.advance_us(module.DEBOUNCE_US)
    module.sensors[line].break_beam()
    _flush_events(module)

    assert module.can_manager.sent == [
        _expected_frame(
            beatbox_module,
            ir_barrier,
            beatbox_module.Priority.EVENT,
            ir_barrier.IRBarrierCommand.BARRIER_EVENT,
            [line, 1],
        )
    ]
    assert module.last_triggered_line == line

    # the beam clearing is reported as well
    module.can_manager.sent.clear()
    clock.advance_us(module.DEBOUNCE_US)
    module.sensors[line].clear_beam()
    _flush_events(module)

    assert module.can_manager.sent == [
        _expected_frame(
            beatbox_module,
            ir_barrier,
            beatbox_module.Priority.EVENT,
            ir_barrier.IRBarrierCommand.BARRIER_EVENT,
            [line, 0],
        )
    ]


def test_bouncing_edges_are_filtered_out(ir_barrier_env):
    _beatbox_module, ir_barrier, clock = ir_barrier_env

    module = ir_barrier.IRBarrierModule()
    line = _line(module, 2)

    clock.advance_us(module.DEBOUNCE_US)
    module.sensors[line].break_beam()
    # contact bounce: the line flaps back before the debounce window elapsed
    clock.advance_us(module.DEBOUNCE_US // 10)
    module.sensors[line].clear_beam()
    clock.advance_us(module.DEBOUNCE_US // 10)
    module.sensors[line].break_beam()
    _flush_events(module)

    assert [bytes(frame["data"]) for frame in module.can_manager.sent] == [bytes([line, 1])]


def test_status_request_returns_mask_and_last_triggered_line(ir_barrier_env):
    beatbox_module, ir_barrier, clock = ir_barrier_env

    module = ir_barrier.IRBarrierModule()
    triggered = _line(module, 6)
    broken = sorted({_line(module, 1), _line(module, 4)})
    expected_mask = sum(1 << index for index in broken)

    clock.advance_us(module.DEBOUNCE_US)
    module.sensors[triggered].break_beam()
    _flush_events(module)
    module.can_manager.sent.clear()

    # Simulate broken beams on a fresh set of lines (active-low inputs).
    module.sensors[triggered].value(1)
    for index in broken:
        module.sensors[index].value(0)

    request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.get_id("IR_BARRIER"),
        beatbox_module.Priority.STATUS,
        beatbox_module.MessageType.REQUEST_MODULE,
        ir_barrier.IRBarrierCommand.GET_BARRIER_STATUS,
    )
    module.command_handler(_fake_message(request_id))

    assert module.can_manager.sent == [
        _expected_frame(
            beatbox_module,
            ir_barrier,
            beatbox_module.Priority.STATUS,
            ir_barrier.IRBarrierCommand.GET_BARRIER_STATUS,
            [expected_mask, triggered],
        )
    ]


def test_status_request_for_another_module_is_ignored(ir_barrier_env):
    beatbox_module, ir_barrier, _clock = ir_barrier_env

    module = ir_barrier.IRBarrierModule()

    request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.get_id("FEEDER"),
        beatbox_module.Priority.STATUS,
        beatbox_module.MessageType.REQUEST_MODULE,
        ir_barrier.IRBarrierCommand.GET_BARRIER_STATUS,
    )
    module.command_handler(_fake_message(request_id))

    assert module.can_manager.sent == []
