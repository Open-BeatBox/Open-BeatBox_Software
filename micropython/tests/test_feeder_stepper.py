import importlib
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))


class FakePin:
    OUT = 1

    def __init__(self, pin_id, mode=None, value=0):
        self.pin_id = pin_id
        self.mode = mode
        self._value = value

    def value(self, v=None):
        if v is None:
            return self._value
        self._value = v


class FakeTimer:
    PERIODIC = 0

    def __init__(self, timer_id):
        self.timer_id = timer_id
        self.callback = None
        self.mode = None
        self.freq = None
        self.active = False

    def init(self, mode, freq, callback):
        self.mode = mode
        self.freq = freq
        self.callback = callback
        self.active = True

    def deinit(self):
        self.active = False
        self.callback = None

    def tick(self, count=1):
        for _ in range(count):
            if not self.active or self.callback is None:
                break
            self.callback(self)


class FakeModuleManager:
    def __init__(self, module_name="GENERIC"):
        self.module_name = module_name
        self.tasks = []
        self.led = types.SimpleNamespace(toggle=lambda: None)


def _load_driver_module(monkeypatch):
    fake_machine = types.ModuleType("machine")
    fake_machine.Pin = FakePin
    fake_machine.Timer = FakeTimer

    fake_beatbox = types.ModuleType("beatbox_module")
    fake_beatbox.ModuleManager = FakeModuleManager
    fake_beatbox.ModuleStatus = types.SimpleNamespace(INIT="INIT", IDLE="IDLE", ACTIVE="ACTIVE", ERROR="ERROR")

    fake_pin_map = types.ModuleType("bb_pin_map")
    fake_pin_map.FeederPins = types.SimpleNamespace(
        MOTOR_EN=5,
        MOTOR_RST=6,
        MOTOR_SLEEP=7,
        MOTOR_DIR=9,
        MOTOR_STEP=10,
        MOTOR_MS3=11,
        MOTOR_MS2=12,
        MOTOR_MS1=13,
    )

    monkeypatch.setitem(sys.modules, "machine", fake_machine)
    monkeypatch.setitem(sys.modules, "beatbox_module", fake_beatbox)
    monkeypatch.setitem(sys.modules, "bb_pin_map", fake_pin_map)

    if "modules.feeder" in sys.modules:
        del sys.modules["modules.feeder"]

    return importlib.import_module("modules.feeder")


def test_timer_generates_expected_number_of_steps(monkeypatch):
    feeder_module = _load_driver_module(monkeypatch)
    driver = feeder_module.A4988StepperDriver(5, 10, 9, 6, 7, 13, 12, 11, timer_id=0)

    driver.start(step_hz=500, steps=3)

    # 3 steps -> 6 timer edges for a full high/low pulse on STEP.
    driver.timer.tick(6)

    assert driver.steps_done == 3
    assert driver.is_running is False
    assert driver.step.value() == 0


def test_timer_frequency_is_double_step_frequency(monkeypatch):
    feeder_module = _load_driver_module(monkeypatch)
    driver = feeder_module.A4988StepperDriver(5, 10, 9, 6, 7, 13, 12, 11, timer_id=1)

    driver.start(step_hz=250, steps=1)

    assert driver.timer.freq == 500
    assert driver.timer.mode == FakeTimer.PERIODIC


def test_invalid_parameters_raise_value_error(monkeypatch):
    feeder_module = _load_driver_module(monkeypatch)
    driver = feeder_module.A4988StepperDriver(5, 10, 9, 6, 7, 13, 12, 11)

    try:
        driver.start(step_hz=0, steps=10)
        assert False, "Expected ValueError for invalid step_hz"
    except ValueError:
        pass

    try:
        driver.start(step_hz=100, steps=0)
        assert False, "Expected ValueError for invalid steps"
    except ValueError:
        pass

