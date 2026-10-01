import importlib
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))


class FakeLed:
    def __init__(self, *args, **kwargs):
        self._state = False

    def on(self):
        self._state = True

    def off(self):
        self._state = False

    def toggle(self):
        self._state = not self._state


class FakeCanManager:
    def __init__(self, can_id=None, *args, **kwargs):
        self.can_id = can_id
        self.sent = []
        self.callbacks = []

    def register_rx_callback(self, callback):
        self.callbacks.append(callback)

    def send_normal(self, data, can_id=None):
        self.sent.append({"can_id": can_id, "data": bytearray(data)})

    async def can_rx_task(self):
        return None

    async def can_tx_task(self):
        return None


def _load_module(monkeypatch):
    fake_bb_mpy_module = types.ModuleType("bb_mpy_module")
    fake_bb_mpy_module.Led = FakeLed

    fake_can_manager = types.ModuleType("can_manager")
    fake_can_manager.CanManager = FakeCanManager

    fake_bb_pin_map = types.ModuleType("bb_pin_map")

    class FakePinMap:
        USER_BTN = 21
        CAN_LED = 24

    fake_bb_pin_map.PinMap = FakePinMap

    monkeypatch.setitem(sys.modules, "bb_mpy_module", fake_bb_mpy_module)
    monkeypatch.setitem(sys.modules, "can_manager", fake_can_manager)
    monkeypatch.setitem(sys.modules, "bb_pin_map", fake_bb_pin_map)

    if "beatbox_module" in sys.modules:
        del sys.modules["beatbox_module"]

    module = importlib.import_module("beatbox_module")

    def fake_create_task(coro):
        coro.close()
        return object()

    monkeypatch.setattr(module.asyncio, "create_task", fake_create_task)
    return module


def _fake_message(can_id, data=b""):
    return types.SimpleNamespace(id=can_id, data=bytearray(data))


def test_scan_request_replies_with_idle_status(monkeypatch):
    beatbox_module = _load_module(monkeypatch)
    manager = beatbox_module.ModuleManager(module_name="NOSEPOKE")

    scan_request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.BROADCAST_ID,
        beatbox_module.Priority.STATUS,
        beatbox_module.MessageType.REQUEST_COMMON,
        beatbox_module.CommonCommand.SCAN,
    )
    manager.command_handler(_fake_message(scan_request_id))

    assert manager.status == beatbox_module.ModuleStatus.IDLE
    assert manager.can_manager.sent == [
        {
            "can_id": beatbox_module.ModuleMap.build_can_id(
                beatbox_module.ModuleMap.get_id("NOSEPOKE"),
                beatbox_module.Priority.STATUS,
                beatbox_module.MessageType.RESPONSE_COMMON,
                beatbox_module.CommonCommand.SCAN,
            ),
            "data": bytearray([beatbox_module.ModuleStatus.to_code(beatbox_module.ModuleStatus.IDLE)]),
        }
    ]


def test_status_request_replies_with_current_status(monkeypatch):
    beatbox_module = _load_module(monkeypatch)
    manager = beatbox_module.ModuleManager(module_name="LIGHTING")
    manager.status = beatbox_module.ModuleStatus.ACTIVE

    status_request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.get_id("LIGHTING"),
        beatbox_module.Priority.STATUS,
        beatbox_module.MessageType.REQUEST_COMMON,
        beatbox_module.CommonCommand.GET_STATUS,
    )
    manager.command_handler(_fake_message(status_request_id))

    assert manager.can_manager.sent == [
        {
            "can_id": beatbox_module.ModuleMap.build_can_id(
                beatbox_module.ModuleMap.get_id("LIGHTING"),
                beatbox_module.Priority.STATUS,
                beatbox_module.MessageType.RESPONSE_COMMON,
                beatbox_module.CommonCommand.GET_STATUS,
            ),
            "data": bytearray([beatbox_module.ModuleStatus.to_code(beatbox_module.ModuleStatus.ACTIVE)]),
        }
    ]


def test_reset_request_calls_hook_with_big_endian_delay(monkeypatch):
    beatbox_module = _load_module(monkeypatch)
    manager = beatbox_module.ModuleManager(module_name="SCREEN_LEFT")
    seen = []
    manager.on_reset_requested = seen.append

    reset_request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.get_id("SCREEN_LEFT"),
        beatbox_module.Priority.CONTROL,
        beatbox_module.MessageType.REQUEST_COMMON,
        beatbox_module.CommonCommand.RESET,
    )
    manager.command_handler(_fake_message(reset_request_id, data=b"\x01\xF4"))

    assert seen == [500]


def test_generic_toggle_can_led_request_replies_with_state(monkeypatch):
    beatbox_module = _load_module(monkeypatch)
    manager = beatbox_module.ModuleManager(module_name="GENERIC")

    toggle_request_id = beatbox_module.ModuleMap.build_can_id(
        beatbox_module.ModuleMap.get_id("GENERIC"),
        beatbox_module.Priority.CONTROL,
        beatbox_module.MessageType.REQUEST_MODULE,
        beatbox_module.GenericCommand.TOGGLE_CAN_LED,
    )
    manager.command_handler(_fake_message(toggle_request_id))

    assert manager.can_manager.sent[-1] == {
        "can_id": beatbox_module.ModuleMap.build_can_id(
            beatbox_module.ModuleMap.get_id("GENERIC"),
            beatbox_module.Priority.STATUS,
            beatbox_module.MessageType.RESPONSE_MODULE,
            beatbox_module.GenericCommand.TOGGLE_CAN_LED,
        ),
        "data": bytearray([1]),
    }


def test_generic_module_emits_user_button_event(monkeypatch):
    beatbox_module = _load_module(monkeypatch)
    manager = beatbox_module.ModuleManager(module_name="GENERIC")

    manager.emit_user_button_event()

    assert manager.can_manager.sent[-1] == {
        "can_id": beatbox_module.ModuleMap.build_can_id(
            beatbox_module.ModuleMap.get_id("GENERIC"),
            beatbox_module.Priority.EVENT,
            beatbox_module.MessageType.RESPONSE_MODULE,
            beatbox_module.GenericCommand.USER_BUTTON_EVENT,
        ),
        "data": bytearray([1]),
    }


