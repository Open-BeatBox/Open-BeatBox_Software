"""Tests for the feeder module (A4988 stepper driver + pellet delivery logic).

What the firmware is supposed to do:
- rotate the disc by exactly REWARD_REV_FRACTION of a revolution per attempt,
- retry until the IR barrier sees a pellet, then report REWARD_STATUS with the
  number of attempts, or an ERROR frame after max_reward_attempts,
- expose a V2-style manual mode (REWARD_ON / REWARD_OFF) that must never run
  concurrently with an autonomous delivery and must self-stop on timeout,
- publish every pellet presence change as an EVENT REWARD_STATUS frame.
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

    # -- test helpers, the sensor output is open drain and active low --
    def break_beam(self):
        """A pellet enters the beam: line goes low and the IRQ fires."""
        self._value = 0
        if self.irq_handler:
            self.irq_handler(self)

    def clear_beam(self):
        self._value = 1

    def transit(self):
        """A pellet falls straight through: the IRQ fires, the beam clears."""
        self.break_beam()
        self.clear_beam()


class FakePWM:
    def __init__(self, pin, freq=1000, duty_u16=0):
        self.pin = pin
        self._freq = freq
        self._duty = duty_u16

    def freq(self, value=None):
        if value is None:
            return self._freq
        self._freq = value

    def duty_u16(self, value=None):
        if value is None:
            return self._duty
        self._duty = value


class FakeTimer:
    ONE_SHOT = 0
    PERIODIC = 1

    def __init__(self, timer_id):
        self.timer_id = timer_id
        self.mode = None
        self.period = None
        self.callback = None
        self.active = False

    def init(self, mode=None, period=None, callback=None):
        self.mode = mode
        self.period = period
        self.callback = callback
        self.active = True

    def deinit(self):
        self.active = False
        self.callback = None

    def fire(self):
        """Simulate the one-shot expiring (MicroPython passes the timer back)."""
        if self.active and self.callback is not None:
            callback = self.callback
            self.active = False
            callback(self)
            return True
        return False


class _FakeMachine(types.ModuleType):
    def __init__(self):
        super().__init__("machine")
        self.Pin = FakePin
        self.Timer = FakeTimer
        self.PWM = FakePWM


# --------------------------------------------------------------------------
# module loading / helpers
# --------------------------------------------------------------------------

def _fake_message(can_id, data=b""):
    return types.SimpleNamespace(id=can_id, data=bytearray(data))


@pytest.fixture
def feeder_env(monkeypatch):
    """Import `feeder` against fake hardware and return (beatbox_module, feeder).

    The fakes must be installed in sys.modules before the import, since
    `beatbox_module` does `from machine import Pin` at import time.
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

    for module_name in ("beatbox_module", "feeder"):
        sys.modules.pop(module_name, None)

    beatbox_module = importlib.import_module("beatbox_module")
    feeder = importlib.import_module("feeder")

    # Keep the timing-driven loops fast; the firmware reads these as globals.
    monkeypatch.setattr(feeder, "POLL_PERIOD_S", 0.001)
    monkeypatch.setattr(feeder, "PELLET_WAIT_S", 0.02)
    monkeypatch.setattr(feeder, "PELLET_MONITOR_PERIOD_S", 0.001)
    monkeypatch.setattr(feeder, "PELLET_CLEAR_MS", 10)

    yield beatbox_module, feeder

    for module_name in ("beatbox_module", "feeder"):
        sys.modules.pop(module_name, None)


def _sent_frames(module):
    from can_protocol import parse_identifier

    return [parse_identifier(frame["can_id"]) + (bytes(frame["data"]),)
            for frame in module.can_manager.sent]


def _request(module, command, msg_type=None, module_id=None):
    from can_protocol import MessageType, ModuleMap, Priority

    return _fake_message(ModuleMap.build_can_id(
        module=module.id if module_id is None else module_id,
        priority=Priority.CONTROL,
        msg_type=MessageType.REQUEST_MODULE if msg_type is None else msg_type,
        command=command,
    ))


def _run(coro, timeout=5.0):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout))
    finally:
        loop.close()


async def _run_delivery(module, pellet_on_attempt, hold_beam=False):
    """Drive deliver_reward(), firing the rotation timer and the IR barrier.

    Returns the number of rotation attempts the firmware performed.
    """
    task = asyncio.ensure_future(module.deliver_reward())
    attempts = 0
    try:
        while not module.can_manager.sent:
            if module.stepper.is_running and module.stepper.timer.active:
                attempts += 1
                module.stepper.timer.fire()
                if pellet_on_attempt and attempts == pellet_on_attempt:
                    if hold_beam:
                        module.feeder_ir_barrier.break_beam()
                    else:
                        module.feeder_ir_barrier.transit()
            await asyncio.sleep(0.001)
    finally:
        task.cancel()
    return attempts


async def _run_monitor(module, duration_s, script=None):
    """Run pellet_monitor for duration_s, applying `script` steps along the way."""
    task = asyncio.ensure_future(module.pellet_monitor())
    try:
        for delay, action in (script or []):
            await asyncio.sleep(delay)
            action()
        await asyncio.sleep(duration_s)
    finally:
        task.cancel()


# --------------------------------------------------------------------------
# A4988 stepper driver
# --------------------------------------------------------------------------

def test_reward_rotation_arms_the_timer_for_the_exact_step_count(feeder_env):
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    assert module.stepper.rotate(step_hz=400, rev_n=feeder.REWARD_REV_FRACTION,
                                 forward=True) is True

    # 360/0.9 = 400 full steps per revolution, 1/8 microstepping, 1/25 of a
    # revolution -> 128 microsteps -> 320 ms at 400 Hz.
    assert module.stepper.is_running is True
    assert module.stepper.direction.value() == 1
    assert module.stepper.timer.mode == FakeTimer.ONE_SHOT
    assert module.stepper.timer.period == 320
    assert module.stepper.step_pwm.freq() == 400
    assert module.stepper.step_pwm.duty_u16() == 32768  # 50% duty
    assert module.stepper.enable.value() == 0  # EN is active low
    assert module.stepper.sleep.value() == 1

    module.stepper.timer.fire()

    assert module.stepper.is_running is False
    assert module.stepper.step_pwm.duty_u16() == 0
    assert module.stepper.sleep.value() == 0  # back to standby


def test_rotation_is_refused_while_the_disc_is_already_turning(feeder_env):
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    module.stepper.rotate(step_hz=400, rev_n=feeder.REWARD_REV_FRACTION)
    armed_period = module.stepper.timer.period

    assert module.stepper.rotate(step_hz=800, rev_n=1.0) is False
    assert module.stepper.timer.period == armed_period  # not re-armed


def test_zero_revolutions_rotates_indefinitely_until_stop(feeder_env):
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    module.stepper.rotate(step_hz=200, rev_n=0, forward=False)

    assert module.stepper.is_running is True
    assert module.stepper.timer.active is False  # no auto-stop
    assert module.stepper.direction.value() == 0  # backwards
    assert module.stepper.step_pwm.duty_u16() > 0

    module.stepper.stop()

    assert module.stepper.is_running is False
    assert module.stepper.step_pwm.duty_u16() == 0
    assert module.stepper.enable.value() == 1  # driver disabled


@pytest.mark.parametrize("fraction, expected", [
    (1, (0, 0, 0)),
    (8, (1, 1, 0)),
    (16, (1, 1, 1)),
])
def test_microstep_pins_match_the_selected_mode(feeder_env, fraction, expected):
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    module.stepper.set_microstep(fraction)

    assert module.stepper.step_fraction == fraction
    assert (module.stepper.ms1.value(), module.stepper.ms2.value(),
            module.stepper.ms3.value()) == expected


def test_invalid_microstep_mode_falls_back_to_eighth_stepping(feeder_env):
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    module.stepper.set_microstep(3)

    assert module.stepper.step_fraction == 8
    assert (module.stepper.ms1.value(), module.stepper.ms2.value(),
            module.stepper.ms3.value()) == (1, 1, 0)


# --------------------------------------------------------------------------
# pellet sensor wiring
# --------------------------------------------------------------------------

def test_ir_barrier_irq_is_registered_on_the_food_sensor_pin(feeder_env):
    _bb, feeder = feeder_env
    from bb_pin_map import FeederPins

    module = feeder.FeederModule()

    assert module.feeder_ir_barrier.pin_number == FeederPins.FOOD_SENSOR_IN
    assert module.feeder_ir_barrier.mode == FakePin.IN
    assert module.feeder_ir_barrier.pull == FakePin.PULL_UP
    assert module.feeder_ir_barrier.irq_trigger == FakePin.IRQ_FALLING

    assert module.pellet_present is False
    assert module._pellet_edge_pending("delivery") is False

    module.feeder_ir_barrier.break_beam()
    assert module._pellet_edge_pending("delivery") is True
    assert module.pellet_present is True

    # The edge must survive a pellet that only transits the beam.
    module.feeder_ir_barrier.clear_beam()
    assert module.pellet_present is False
    assert module._pellet_edge_pending("delivery") is True


# --------------------------------------------------------------------------
# bus interface
# --------------------------------------------------------------------------

def test_request_reward_starts_a_delivery_and_rejects_concurrent_requests(feeder_env):
    beatbox_module, feeder = feeder_env
    from can_protocol import FeederCommand

    module = feeder.FeederModule()
    module.command_handler(_request(module, FeederCommand.REQUEST_REWARD))

    assert module.reward_in_progress is True
    assert module._reward_requested is True
    assert module.status == beatbox_module.ModuleStatus.ACTIVE
    assert module.reward() is False
    assert module.reward_on() is False  # manual mode must not cut in


def test_broadcast_request_reward_is_ignored(feeder_env):
    beatbox_module, feeder = feeder_env
    from can_protocol import FeederCommand, ModuleMap

    module = feeder.FeederModule()
    module.command_handler(_request(module, FeederCommand.REQUEST_REWARD,
                                    module_id=ModuleMap.BROADCAST_ID))

    assert module.reward_in_progress is False
    assert module.status == beatbox_module.ModuleStatus.INIT


def test_get_reward_status_answers_with_state_and_attempt_count(feeder_env):
    _bb, feeder = feeder_env
    from can_protocol import FeederCommand, MessageType, Priority

    module = feeder.FeederModule()
    module._reported_pellet_state = 1
    module._last_attempts = 4

    module.command_handler(_request(module, FeederCommand.GET_REWARD_STATUS))

    priority, module_id, msg_type, command, payload = _sent_frames(module)[0]
    assert (priority, module_id, msg_type, command) == (
        Priority.STATUS, module.id, MessageType.RESPONSE_MODULE,
        FeederCommand.REWARD_STATUS)
    assert payload == bytes([1, 4])


def test_reward_on_runs_the_motor_and_reward_off_stops_it(feeder_env):
    beatbox_module, feeder = feeder_env
    from can_protocol import FeederCommand

    module = feeder.FeederModule()
    module.command_handler(_request(module, FeederCommand.REWARD_ON))

    assert module.stepper.is_running is True
    assert module.stepper.timer.active is False  # runs until told to stop
    assert module.stepper.step_pwm.freq() == module.step_hz // 5
    assert module.status == beatbox_module.ModuleStatus.ACTIVE
    assert module._manual_deadline_ms is not None
    assert module.reward() is False  # autonomous request refused meanwhile

    module.command_handler(_request(module, FeederCommand.REWARD_OFF))

    assert module.stepper.is_running is False
    assert module.stepper.step_pwm.duty_u16() == 0
    assert module._manual_deadline_ms is None
    assert module.status == beatbox_module.ModuleStatus.IDLE


def test_reward_on_is_idempotent(feeder_env):
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    assert module.reward_on() is True
    deadline = module._manual_deadline_ms
    assert module.reward_on() is True
    assert module._manual_deadline_ms == deadline
    assert module.stepper.is_running is True


# --------------------------------------------------------------------------
# autonomous delivery
# --------------------------------------------------------------------------

def test_delivery_retries_until_a_pellet_is_detected(feeder_env):
    beatbox_module, feeder = feeder_env
    from can_protocol import FeederCommand, MessageType, Priority

    module = feeder.FeederModule()
    module.max_reward_attempts = 5
    assert module.reward() is True

    attempts = _run(_run_delivery(module, pellet_on_attempt=3))

    assert attempts == 3
    priority, module_id, msg_type, command, payload = _sent_frames(module)[0]
    assert (priority, module_id, msg_type, command) == (
        Priority.STATUS, module.id, MessageType.RESPONSE_MODULE,
        FeederCommand.REWARD_STATUS)
    assert payload[1] == 3  # attempt count
    assert module._last_attempts == 3
    assert module.reward_in_progress is False
    assert module.status == beatbox_module.ModuleStatus.IDLE
    assert module.stepper.is_running is False


def test_delivery_reports_an_error_when_no_pellet_arrives(feeder_env):
    beatbox_module, feeder = feeder_env
    from can_protocol import CommonCommand, FeederCommand, MessageType, Priority

    module = feeder.FeederModule()
    module.max_reward_attempts = 3
    module.reward()

    attempts = _run(_run_delivery(module, pellet_on_attempt=0))

    assert attempts == 3
    priority, _module_id, msg_type, command, payload = _sent_frames(module)[0]
    assert (priority, msg_type, command) == (
        Priority.ERROR, MessageType.RESPONSE_COMMON, CommonCommand.ERROR)
    assert payload == bytes([FeederCommand.REQUEST_REWARD, 3])
    assert module.status == beatbox_module.ModuleStatus.ERROR
    assert module.reward_in_progress is False
    assert module.stepper.is_running is False
    # A new reward can still be requested after a failed delivery.
    assert module.reward() is True


def test_delivery_is_skipped_when_a_pellet_is_already_in_the_well(feeder_env):
    beatbox_module, feeder = feeder_env
    from can_protocol import FeederCommand, Priority

    module = feeder.FeederModule()
    module.feeder_ir_barrier.value(0)  # pellet already present, no new IRQ
    module.reward()

    _run(_run_delivery(module, pellet_on_attempt=0))

    priority, _module_id, _msg_type, command, payload = _sent_frames(module)[0]
    assert (priority, command) == (Priority.STATUS, FeederCommand.REWARD_STATUS)
    assert payload[1] == 0  # no rotation attempt was needed
    assert module._last_attempts == 0
    assert module.reward_in_progress is False
    assert module.status == beatbox_module.ModuleStatus.IDLE
    assert module.stepper.is_running is False


def test_reward_off_aborts_a_running_delivery_without_error(feeder_env):
    beatbox_module, feeder = feeder_env

    module = feeder.FeederModule()
    module.max_reward_attempts = 20
    module.reward()

    async def scenario():
        task = asyncio.ensure_future(module.deliver_reward())
        for _ in range(200):
            if module.stepper.is_running:
                break
            await asyncio.sleep(0.001)
        module.reward_off()
        await asyncio.sleep(0.05)
        task.cancel()

    _run(scenario())

    assert module.stepper.is_running is False
    assert module.reward_in_progress is False
    assert module._abort_delivery is False
    assert module.status == beatbox_module.ModuleStatus.IDLE
    assert _sent_frames(module) == []  # an abort is not an error


def test_rotation_wait_gives_up_after_the_safety_timeout(feeder_env):
    _bb, feeder = feeder_env

    module = feeder.FeederModule()
    module.stepper.rotate(step_hz=module.step_hz, rev_n=feeder.REWARD_REV_FRACTION)

    # The timer never fires (stuck driver): the task must stop the motor itself.
    _run(_run_rotation_wait(module, feeder))

    assert module.stepper.is_running is False
    assert module.stepper.step_pwm.duty_u16() == 0


async def _run_rotation_wait(module, feeder):
    original = feeder.ROTATION_TIMEOUT_S
    feeder.ROTATION_TIMEOUT_S = 0.02
    try:
        await module._wait_rotation_end()
    finally:
        feeder.ROTATION_TIMEOUT_S = original


# --------------------------------------------------------------------------
# pellet monitor
# --------------------------------------------------------------------------

def test_pellet_edges_are_consumed_independently_by_each_task(feeder_env):
    """pellet_monitor must not swallow the beam break the delivery waits for.

    Both tasks run concurrently and poll on the same period, so a single shared
    latch made the winner arbitrary: the delivery then retried (dispensing an
    extra pellet) or reported an error for a pellet that was actually there.
    """
    _bb, feeder = feeder_env
    module = feeder.FeederModule()

    async def scenario():
        monitor = asyncio.ensure_future(module.pellet_monitor())
        await asyncio.sleep(0.005)
        module.feeder_ir_barrier.transit()     # fast pellet, IRQ only
        await asyncio.sleep(0.02)              # let the monitor poll first
        try:
            return await module._wait_for_pellet(0.05)
        finally:
            monitor.cancel()

    assert _run(scenario()) is True
    # The monitor still saw and published its own arrival edge.
    assert [frame[4][0] for frame in _sent_frames(module)][0] == 1


def test_delivery_detects_the_pellet_while_the_monitor_is_running(feeder_env):
    beatbox_module, feeder = feeder_env

    module = feeder.FeederModule()
    module.max_reward_attempts = 5
    module.reward()

    async def scenario():
        monitor = asyncio.ensure_future(module.pellet_monitor())
        try:
            return await _run_delivery(module, pellet_on_attempt=2)
        finally:
            monitor.cancel()

    attempts = _run(scenario())

    assert attempts == 2
    assert module._last_attempts == 2
    assert module.status == beatbox_module.ModuleStatus.IDLE


def test_monitor_publishes_arrival_and_removal_edges(feeder_env):
    _bb, feeder = feeder_env
    from can_protocol import FeederCommand, MessageType, Priority

    module = feeder.FeederModule()
    module._last_attempts = 2

    _run(_run_monitor(module, 0.05, script=[
        (0.01, module.feeder_ir_barrier.break_beam),
        (0.02, module.feeder_ir_barrier.clear_beam),
    ]))

    frames = _sent_frames(module)
    assert len(frames) == 2, frames
    for priority, module_id, msg_type, command, _payload in frames:
        assert (priority, module_id, msg_type, command) == (
            Priority.EVENT, module.id, MessageType.RESPONSE_MODULE,
            FeederCommand.REWARD_STATUS)
    assert frames[0][4] == bytes([1, 2])  # arrival
    assert frames[1][4] == bytes([0, 2])  # removal, after PELLET_CLEAR_MS


def test_monitor_reports_a_fast_transit_as_an_arrival(feeder_env):
    _bb, feeder = feeder_env

    module = feeder.FeederModule()

    # Pellet falls through between two polls: only the IRQ latch sees it.
    _run(_run_monitor(module, 0.05, script=[
        (0.005, module.feeder_ir_barrier.transit),
    ]))

    frames = _sent_frames(module)
    assert [frame[4][0] for frame in frames] == [1, 0]


def test_monitor_does_not_republish_a_steady_state(feeder_env):
    _bb, feeder = feeder_env

    module = feeder.FeederModule()
    module.feeder_ir_barrier.break_beam()

    _run(_run_monitor(module, 0.05))

    assert len(_sent_frames(module)) == 1


def test_monitor_stops_a_manual_reward_that_outlives_its_timeout(feeder_env):
    beatbox_module, feeder = feeder_env

    module = feeder.FeederModule()
    monkey_timeout = 10
    original = feeder.REWARD_ON_TIMEOUT_MS
    feeder.REWARD_ON_TIMEOUT_MS = monkey_timeout
    try:
        module.reward_on()
        assert module.stepper.is_running is True
        _run(_run_monitor(module, 0.08))
    finally:
        feeder.REWARD_ON_TIMEOUT_MS = original

    assert module.stepper.is_running is False
    assert module._manual_deadline_ms is None
    assert module.status == beatbox_module.ModuleStatus.IDLE
