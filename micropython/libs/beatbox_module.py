import asyncio

from bb_mpy_module import Led
try:
    from canio import Match
except ImportError:
    class Match:
        def __init__(self, address, mask):
            self.address = address
            self.mask = mask

from can_manager import CanManager
from can_protocol import (
    CommonCommand,
    GenericCommand,
    MessageType,
    ModuleMap,
    build_identifier,
    normalize_payload,
    parse_identifier,
    Priority,
    MODULE_ID_MASK,
    MODULE_ID_SHIFT,
)

try:
    from machine import Pin as _MachinePin
except ImportError:
    _MachinePin = None

try:
    import bb_pin_map as _bb_pin_map
except Exception:
    _bb_pin_map = None

_DefaultPinMap = getattr(_bb_pin_map, "PinMap", None)
_FeederPins = getattr(_bb_pin_map, "FeederPins", None)
_IRBarrierPins = getattr(_bb_pin_map, "IRBarrierPins", None)
_MainModulePins = getattr(_bb_pin_map, "MainModulePins", None)
_NosepokePins = getattr(_bb_pin_map, "NosepokePins", None)
_ScreenPins = getattr(_bb_pin_map, "ScreenPins", None)

# Module registry for seamless ID, ID_NAME, CAN_ID conversion.
# Defined in can_protocol so it can be used without the MicroPython runtime;
# re-exported here for backward compatibility.

class ModuleStatus:
    INIT = "INIT" # not configured
    IDLE = "IDLE" # configured but not active
    ACTIVE = "ACTIVE" # actively running
    ERROR = "ERROR" # error state

    _ALL = (INIT, IDLE, ACTIVE, ERROR)
    _TO_CODE = {INIT: 0, IDLE: 1, ACTIVE: 2, ERROR: 3}
    _FROM_CODE = {value: key for key, value in _TO_CODE.items()}

    @classmethod
    def is_valid(cls, value):
        return value in cls._ALL

    @classmethod
    def to_code(cls, status):
        return cls._TO_CODE.get(status, 255)

    @classmethod
    def from_code(cls, code):
        return cls._FROM_CODE.get(code, cls.ERROR)

# TODO: add log method/class, to handle debug and file logging
class ModuleManager:
    """
    Base module manager for Beatbox system.
    Handles CAN communication and provides a structure for module-specific functionality.
    Child classes must call super().__init__() with the corresponding module name (string)
    and can extend functionality by overriding methods.
    """
    def __init__(self, module_name: str = "GENERIC"):
        self.can_led = None
        self._can_led_state = False
        self.user_button = None

        # Use ModuleRegistry for all lookups
        self.id = ModuleMap.get_id(module_name)
        if self.id is None:
            print(f"Unknown module '{module_name}', defaulting to GENERIC")
            module_name = "GENERIC"
            self.id = ModuleMap.get_id(module_name)

        self.module_name = module_name
        self.pin_map = self._create_pin_map(module_name)
        user_led_pin = None if self.pin_map is None else getattr(self.pin_map, "USER_LED", None)
        self.led = Led(pin_num=user_led_pin)
        self.led.on()
        self.can_led = Led(pin_num=getattr(self.pin_map, "CAN_LED", None))

        # FIXME: ID describes a CAN message not a module, remove this and use ModuleMap.build_can_id() for all CAN messages
        self.can_id = ModuleMap.build_can_id(
            module=self.id,
            priority=Priority.STATUS,
            msg_type=MessageType.RESPONSE_COMMON,
            command=CommonCommand.GET_STATUS,
        )

        self.can_manager = CanManager(can_id=self.can_id, debug=True, led=self.can_led)
        assert self.id is not None
        module_match = self.build_module_match(self.id)
        broadcast_match = self.build_module_match(ModuleMap.BROADCAST_ID)
        configure_filters = getattr(self.can_manager, "configure_filters", None)
        if callable(configure_filters):
            configure_filters([module_match, broadcast_match])

        print(f"Module initialized: NAME={module_name}, ID={self.id}")
        self.can_manager.register_rx_callback(self.can_msg_echo_on_serial)

        self._setup_generic_io()

        self.status = ModuleStatus.INIT
        self.tasks = [
            asyncio.create_task(self.can_manager.can_rx_task()),
            asyncio.create_task(self.can_manager.can_tx_task()),
            # asyncio.create_task(self.heartbeat_task()),
        ]
        self.heartbeat_interval = 0.5 # 20 ms

    @staticmethod
    def _create_pin_map(module_name):
        if module_name == "MAIN" and _MainModulePins is not None:
            return _MainModulePins()
        if module_name == "FEEDER" and _FeederPins is not None:
            return _FeederPins()
        if module_name == "NOSEPOKE" and _NosepokePins is not None:
            return _NosepokePins()
        if module_name in ("SCREEN_LEFT", "SCREEN_RIGHT") and _ScreenPins is not None:
            return _ScreenPins()
        if module_name == "IR_BARRIER" and _IRBarrierPins is not None:
            return _IRBarrierPins()
        if _DefaultPinMap is not None:
            return _DefaultPinMap()
        return None

    @staticmethod
    def build_module_match(module_id):
        """Build a Match filter for a specific module ID"""
        address = (module_id & MODULE_ID_MASK) << MODULE_ID_SHIFT
        mask = MODULE_ID_MASK << MODULE_ID_SHIFT
        return Match(address=address, mask=mask)

    # Main loop task to handle module logic
    async def heartbeat_task(self):
        while True:
            self.led.toggle()
            # Placeholder for module-specific periodic tasks
            await asyncio.sleep(self.heartbeat_interval)

    def can_msg_echo_on_serial(self, msg):
        # TODO: check if serial manager exists and available
        print(f'In {self.status}, received from CAN_ID={msg.id}, data={list(msg.data)}')
        self.command_handler(msg)

    def _is_targeted_to_me(self, target_module_id):
        return target_module_id in (self.id, ModuleMap.BROADCAST_ID)

    def _send_frame(self, priority, msg_type, command, payload=None):
        can_id = ModuleMap.build_can_id(self.id, priority, msg_type, command)
        self.can_manager.send_normal(can_id=can_id, data=normalize_payload(payload))

    def _setup_generic_io(self):
        pin_map = self.pin_map
        pin_cls = _MachinePin
        if pin_map is None:
            return

        try:
            self.can_led = Led(pin_num=getattr(pin_map, "CAN_LED", None))
            self.can_led.off()
            self._can_led_state = False
        except Exception:
            self.can_led = None

        if self.id != ModuleMap.get_id("GENERIC"):
            return

        if pin_cls is None:
            return

        try:
            pull_up = pin_cls.PULL_UP if hasattr(pin_cls, "PULL_UP") else None
            if pull_up is None:
                self.user_button = pin_cls(pin_map.USER_BTN, pin_cls.IN)
            else:
                self.user_button = pin_cls(pin_map.USER_BTN, pin_cls.IN, pull_up)
            self.user_button.irq(trigger=pin_cls.IRQ_FALLING, handler=self._on_user_button_irq)
        except Exception:
            self.user_button = None

    def _on_user_button_irq(self, _pin):
        self.emit_user_button_event()

    def emit_user_button_event(self):
        # if self.id != ModuleMap.get_id("GENERIC"):
        #     return
        self.led.on()
        self._send_frame(
            priority=Priority.EVENT,
            msg_type=MessageType.RESPONSE_MODULE,
            command=GenericCommand.USER_BUTTON_EVENT,
            payload=bytearray([1]),
        )

    def _toggle_can_led(self):
        if self.can_led is None:
            return None
        self.can_led.toggle()
        self._can_led_state = not self._can_led_state
        return self._can_led_state

    def on_reset_requested(self, delay_ms):
        print(f"RESET_REQUESTED delay_ms={delay_ms}")

    def command_handler(self, msg):
        """Handle incoming commands - to be overridden by child classes"""
        priority, target_module_id, msg_type, command = parse_identifier(msg.id)

        if not MessageType.is_request(msg_type) or not self._is_targeted_to_me(target_module_id):
            return

        if msg_type == MessageType.REQUEST_COMMON and command == CommonCommand.SCAN:
            if self.status == ModuleStatus.INIT:
                self.status = ModuleStatus.IDLE
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_COMMON,
                command=CommonCommand.SCAN,
                payload=bytearray([ModuleStatus.to_code(self.status)]),
            )
            return

        if msg_type == MessageType.REQUEST_COMMON and command == CommonCommand.GET_STATUS:
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_COMMON,
                command=CommonCommand.GET_STATUS,
                payload=bytearray([ModuleStatus.to_code(self.status)]),
            )
            return

        if msg_type == MessageType.REQUEST_COMMON and command == CommonCommand.RESET:
            delay_ms = 0
            if len(msg.data) >= 2:
                delay_ms = (msg.data[0] << 8) | msg.data[1]
            self.on_reset_requested(delay_ms)
            return

        if (
            self.id == ModuleMap.get_id("GENERIC")
            and msg_type == MessageType.REQUEST_MODULE
            and command == GenericCommand.TOGGLE_CAN_LED
        ):
            is_on = self._toggle_can_led()
            state_code = 255 if is_on is None else int(bool(is_on))
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_MODULE,
                command=GenericCommand.TOGGLE_CAN_LED,
                payload=bytearray([state_code]),
            )

    async def start_managers(self):
        """
        Start all module manager tasks
        Child classes should append their tasks to self.tasks before calling super().start_managers()
        """
        await asyncio.gather(*self.tasks)

    #@abstractmethod
    #def print(self): ...

