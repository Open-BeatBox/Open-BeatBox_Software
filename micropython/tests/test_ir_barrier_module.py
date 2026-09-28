import importlib
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "modules"))


class FakeLed:
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
    IRQ_FALLING = 2

    instances = []

    def __init__(self, pin_number, mode, value=None):
        self.pin_number = pin_number
        self.mode = mode
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
        if self.irq_handler:
            self.irq_handler(self)


class _FakeMachine(types.ModuleType):
    def __init__(self):
        super().__init__("machine")
        self.Pin = FakePin


def _fake_message(can_id, data=b""):
    return types.SimpleNamespace(id=can_id, data=bytearray(data))


def _load_ir_barrier_module(monkeypatch):
    FakePin.instances = []

    fake_bb_mpy_module = types.ModuleType("bb_mpy_module")
    fake_bb_mpy_module.Led = FakeLed

    fake_can_manager = types.ModuleType("can_manager")
    fake_can_manager.CanManager = FakeCanManager

    monkeypatch.setitem(sys.modules, "bb_mpy_module", fake_bb_mpy_module)
    monkeypatch.setitem(sys.modules, "can_manager", fake_can_manager)
    monkeypatch.setitem(sys.modules, "machine", _FakeMachine())

    for module_name in ("beatbox_module", "ir_barrier"):
        if module_name in sys.modules:
            del sys.modules[module_name]

    beatbox_module = importlib.import_module("beatbox_module")
    ir_barrier = importlib.import_module("ir_barrier")

    def fake_create_task(coro):
        coro.close()
        return object()

    monkeypatch.setattr(beatbox_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(ir_barrier.asyncio, "create_task", fake_create_task)
    return beatbox_module, ir_barrier


def test_init_registers_interrupt_on_all_lines(monkeypatch):
    _beatbox_module, ir_barrier = _load_ir_barrier_module(monkeypatch)

    module = ir_barrier.IRBarrierModule()

    assert len(module.sensors) == 8
    assert [sensor.pin_number for sensor in module.sensors] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert all(sensor.irq_trigger == FakePin.IRQ_FALLING for sensor in module.sensors)


def test_any_line_interrupt_sends_barrier_event_frame(monkeypatch):
    beatbox_module, ir_barrier = _load_ir_barrier_module(monkeypatch)

    module = ir_barrier.IRBarrierModule()
    module.sensors[3].trigger_irq()

    assert module.can_manager.sent == [
        {
            "can_id": beatbox_module.ModuleMap.build_can_id(
                beatbox_module.ModuleMap.get_id("IR_BARRIER"),
                beatbox_module.Priority.EVENT,
                beatbox_module.MessageType.RESPONSE_MODULE,
                ir_barrier.IRBarrierCommand.BARRIER_EVENT,
            ),
            "data": bytearray([3, 1]),
        }
    ]


def test_status_request_returns_mask_and_last_triggered_line(monkeypatch):
    beatbox_module, ir_barrier = _load_ir_barrier_module(monkeypatch)

    module = ir_barrier.IRBarrierModule()
    module.sensors[6].trigger_irq()
    module.can_manager.sent.clear()

    # Simulate broken beams on line 1 and 4 (active-low inputs).
    module.sensors[1].value(0)
    module.sensors[4].value(0)

    request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.get_id("IR_BARRIER"),
        beatbox_module.Priority.STATUS,
        beatbox_module.MessageType.REQUEST_MODULE,
        ir_barrier.IRBarrierCommand.GET_BARRIER_STATUS,
    )
    module.command_handler(_fake_message(request_id))

    assert module.can_manager.sent == [
        {
            "can_id": beatbox_module.ModuleMap.build_can_id(
                beatbox_module.ModuleMap.get_id("IR_BARRIER"),
                beatbox_module.Priority.STATUS,
                beatbox_module.MessageType.RESPONSE_MODULE,
                ir_barrier.IRBarrierCommand.GET_BARRIER_STATUS,
            ),
            "data": bytearray([0b00010010, 6]),
        }
    ]

