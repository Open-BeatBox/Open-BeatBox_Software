import asyncio
import importlib
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "modules"))


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

    instances = []

    def __init__(self, pin_number, mode=None, pull=None, value=None):
        self.pin_number = pin_number
        self.mode = mode
        self.pull = pull
        self._value = 1 if value is None else value
        self.irq_trigger = None
        self.irq_handler = None
        FakePin.instances.append(self)

    def value(self, v=None):
        if v is None:
            return self._value
        self._value = v

    def irq(self, trigger, handler):
        self.irq_trigger = trigger
        self.irq_handler = handler

    def trigger_irq(self):
        """Simulate the beam being broken by a falling pellet."""
        self._value = 0
        if self.irq_handler:
            self.irq_handler(self)


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
        """Simulate the one-shot expiring, MicroPython passes the timer to the callback."""
        if self.active and self.callback is not None:
            callback = self.callback
            self.active = False
            callback(self)


class _FakeMachine(types.ModuleType):
    def __init__(self):
        super().__init__("machine")
        self.Pin = FakePin
        self.Timer = FakeTimer
        self.PWM = FakePWM


def _fake_message(can_id, data=b""):
    return types.SimpleNamespace(id=can_id, data=bytearray(data))


def _load_feeder_module(monkeypatch):
    FakePin.instances = []

    fake_bb_mpy_module = types.ModuleType("bb_mpy_module")
    fake_bb_mpy_module.Led = FakeLed

    fake_can_manager = types.ModuleType("can_manager")
    fake_can_manager.CanManager = FakeCanManager

    monkeypatch.setitem(sys.modules, "bb_mpy_module", fake_bb_mpy_module)
    monkeypatch.setitem(sys.modules, "can_manager", fake_can_manager)
    monkeypatch.setitem(sys.modules, "machine", _FakeMachine())

    for module_name in ("beatbox_module", "feeder"):
        if module_name in sys.modules:
            del sys.modules[module_name]

    beatbox_module = importlib.import_module("beatbox_module")
    feeder = importlib.import_module("feeder")

    def fake_create_task(coro):
        coro.close()
        return object()

    monkeypatch.setattr(beatbox_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(feeder.asyncio, "create_task", fake_create_task)
    return beatbox_module, feeder


def _sent_commands(module):
    from can_protocol import parse_identifier

    return [parse_identifier(frame["can_id"]) + (bytes(frame["data"]),)
            for frame in module.can_manager.sent]


async def _run_reward(module, feeder, pellet_on_attempt, max_attempts=4):
    """Drive the delivery task, firing the stepper timer and the IR barrier IRQ."""
    module.max_reward_attempts = max_attempts
    task = asyncio.ensure_future(module.deliver_reward())
    attempts = 0
    for _ in range(2000):
        if module.can_manager.sent:
            break
        if module.stepper.is_running:
            attempts += 1
            module.stepper.timer.fire()
            if pellet_on_attempt and attempts == pellet_on_attempt:
                module.feeder_ir_barrier.trigger_irq()
        await asyncio.sleep(0)
        await asyncio.sleep(feeder.POLL_PERIOD_S)
    task.cancel()
    return attempts


def test_reward_rotates_one_twentieth_of_a_revolution(monkeypatch):
    _beatbox_module, feeder = _load_feeder_module(monkeypatch)
    module = feeder.FeederModule()

    module.stepper.rotate(step_hz=400, rev_n=feeder.REWARD_REV_FRACTION, forward=True)

    # 360/0.8 = 450 full steps per revolution, 1/8 microstepping, 1/20 revolution -> 180 microsteps.
    assert module.stepper.is_running is True
    assert module.stepper.timer.mode == FakeTimer.ONE_SHOT
    assert module.stepper.timer.period == 450  # 180 microsteps at 400 Hz
    assert module.stepper.step_pwm.duty_u16() > 0

    module.stepper.timer.fire()

    assert module.stepper.is_running is False
    assert module.stepper.step_pwm.duty_u16() == 0


def test_ir_barrier_irq_is_registered_on_food_sensor_pin(monkeypatch):
    _beatbox_module, feeder = _load_feeder_module(monkeypatch)
    module = feeder.FeederModule()

    from bb_pin_map import FeederPins

    assert module.feeder_ir_barrier.pin_number == FeederPins.FOOD_SENSOR_IN
    assert module.feeder_ir_barrier.pull == FakePin.PULL_UP
    assert module.feeder_ir_barrier.irq_trigger == FakePin.IRQ_FALLING

    assert module.pellet_present is False
    module.feeder_ir_barrier.trigger_irq()
    assert module._pellet_detected is True
    assert module.pellet_present is True


def test_request_reward_command_starts_a_delivery(monkeypatch):
    beatbox_module, feeder = _load_feeder_module(monkeypatch)
    from can_protocol import FeederCommand, MessageType, Priority, build_identifier

    module = feeder.FeederModule()
    can_id = build_identifier(Priority.CONTROL, module.id, MessageType.REQUEST_MODULE,
                              FeederCommand.REQUEST_REWARD)

    module.command_handler(_fake_message(can_id))

    assert module.reward_in_progress is True
    assert module.status == beatbox_module.ModuleStatus.ACTIVE
    # A concurrent request is rejected while a delivery is running.
    assert module.reward() is False


def test_delivery_retries_until_pellet_is_detected(monkeypatch):
    beatbox_module, feeder = _load_feeder_module(monkeypatch)
    from can_protocol import FeederCommand, MessageType

    module = feeder.FeederModule()
    module.reward()
    attempts = asyncio.new_event_loop().run_until_complete(
        _run_reward(module, feeder, pellet_on_attempt=3)
    )

    assert attempts == 3
    _priority, module_id, msg_type, command, payload = _sent_commands(module)[0]
    assert module_id == module.id
    assert msg_type == MessageType.RESPONSE_MODULE
    assert command == FeederCommand.REWARD_DELIVERED
    assert payload == bytes([3])
    assert module.reward_in_progress is False
    assert module.status == beatbox_module.ModuleStatus.IDLE
    assert module.stepper.is_running is False


def test_delivery_reports_an_error_when_no_pellet_arrives(monkeypatch):
    beatbox_module, feeder = _load_feeder_module(monkeypatch)
    from can_protocol import CommonCommand, MessageType, Priority

    module = feeder.FeederModule()
    module.reward()
    attempts = asyncio.new_event_loop().run_until_complete(
        _run_reward(module, feeder, pellet_on_attempt=0, max_attempts=3)
    )

    assert attempts == 3
    priority, _module_id, msg_type, command, _payload = _sent_commands(module)[0]
    assert (priority, msg_type, command) == (Priority.ERROR, MessageType.RESPONSE_COMMON,
                                             CommonCommand.ERROR)
    assert module.status == beatbox_module.ModuleStatus.ERROR
    assert module.reward_in_progress is False
    # A new reward can still be requested after a failed delivery.
    assert module.reward() is True
