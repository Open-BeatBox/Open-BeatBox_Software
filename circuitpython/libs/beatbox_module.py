import asyncio
# from abc import ABC, abstractmethod

import board
import countio
import digitalio

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN
from bb_mpy_module import LED

from can_manager import CanManager

# Module registry for seamless ID, ID_NAME, CAN_ID conversion
class ModuleMap:
    _entries = [
        ("GENERIC", 0xFF, 0xFFF),
        ("MAIN", 0x00, 0x100),
        ("SCREEN", 0x10, 0x200),
        ("FEEDER", 0x20, 0x202),
        ("LIGHTING", 0x30, 0x203),
        ("NOSEPOKE", 0x40, 0x204),
        ("IR_BARRIER", 0x05, 0x205),
    ]
    name_to_id = {name: mid for name, mid, _ in _entries}
    id_to_name = {mid: name for name, mid, _ in _entries}
    id_to_can = {mid: canid for name, mid, canid in _entries}
    can_to_id = {canid: mid for name, mid, canid in _entries}
    name_to_can = {name: canid for name, mid, canid in _entries}
    can_to_name = {canid: name for name, mid, canid in _entries}

    @classmethod
    def get_id(cls, value):
        if isinstance(value, int):
            if value in cls.id_to_name:
                return value
            if value in cls.can_to_id:
                return cls.can_to_id[value]
        elif isinstance(value, str):
            return cls.name_to_id.get(value)
        return None

    @classmethod
    def get_name(cls, value):
        if isinstance(value, int):
            if value in cls.id_to_name:
                return cls.id_to_name[value]
            if value in cls.can_to_name:
                return cls.can_to_name[value]
        elif isinstance(value, str):
            if value in cls.name_to_id:
                return value
        return None

    @classmethod
    def get_can_id(cls, value):
        if isinstance(value, int):
            if value in cls.id_to_can:
                return cls.id_to_can[value]
            if value in cls.can_to_id:
                return value
        elif isinstance(value, str):
            return cls.name_to_can.get(value)
        return None

class ModuleStatus:
    INIT = "INIT" # not configured
    IDLE = "IDLE" # configured but not active
    ACTIVE = "ACTIVE" # actively running
    ERROR = "ERROR" # error state

    _ALL = (INIT, IDLE, ACTIVE, ERROR)

    @classmethod
    def is_valid(cls, value):
        return value in cls._ALL

class InterruptSensor:
    """ Base class for sensor based on edge interrupt."""
    def __init__(self, interrupt_pin=None, edge=countio.Edge.FALL, on_edge=None, schedule_period_s=0.02):
        if interrupt_pin is not None:
            self.sensor_out = countio.Counter(interrupt_pin, edge=edge)
        self.on_edge = on_edge  # Callback to call on poke event
        self.schedule_period_s = schedule_period_s

    async def catch_interrupt(self):
        """Call callback/send CAN as soon as pin goes low."""
        last_count = 0
        if self.sensor_out is None:
            return
        with self.sensor_out as interrupt:
            while True:
                if interrupt.count != last_count:
                    last_count = interrupt.count
                    if self.on_edge:
                        self.on_edge()  # Call the callback immediately
                await asyncio.sleep(schedule_period_s)

class PollingSensor:
    def __init__(self, pin=None, edge=countio.Edge.RISE_AND_FALL, on_edge=None, schedule_period_s=0.01):
        if pin is not None:
            self.pin = digitalio.DigitalInOut(pin)
            self.pin.direction = digitalio.Direction.INPUT
        self.edge = edge
        self.on_edge = on_edge
        self.schedule_period_s = schedule_period_s

    @property
    def value(self):
        if self.pin is not None:
            return self.pin.value
        return None

    async def poll_status(self):
        """Call callback/send CAN as soon as pin goes low."""
        last_state = self.value
        while True:


            if last_state != self.value:
                if not self.on_edge:
                    await asyncio.sleep(self.schedule_period_s)
                else:
                    if self.edge == countio.Edge.RISE_AND_FALL:
                        self.on_edge()  # Call the callback immediately
                    elif self.edge == countio.Edge.RISE and self.value:
                        self.on_edge()
                    elif self.edge == countio.Edge.FALL and not self.value:
                        self.on_edge()
            last_state = self.value
            await asyncio.sleep(self.schedule_period_s)

"""
class StatusLEDManager:
    def __init__(self, led_pin=board.D25):
        self.status_led = digitalio.DigitalInOut(led_pin)
        self.status_led.direction = digitalio.Direction.OUTPUT
        self.status_led.value = False
        self.ticks = 0
        self.mode: dict[str, str] = {
            ModuleStatus.INIT: "OFF",
            ModuleStatus.IDLE: "SLOW_BLINK",
            ModuleStatus.ACTIVE: "ON",
            ModuleStatus.ERROR: "FAST_BLINK",
        }
        self.current_mode = self.mode[ModuleStatus.INIT]
        self.schedule_period = 0.1  # seconds

    async def update_led(self, status: str):
        if not ModuleStatus.is_valid(status):
            self.current_mode = self.mode[ModuleStatus.ERROR]

        if self.current_mode != self.mode[status]:
            self.current_mode = self.mode[status]
            self.ticks = 0  # Reset ticks on mode change
        while True:
            match self.current_mode:
                case "OFF":
                    self.status_led.value = False
                case "ON":
                    self.status_led.value = True
                case "SLOW_BLINK":  # 1 Hz
                    if self.ticks % (1 / self.schedule_period) == 0:
                        self.status_led.value = not self.status_led.value
                case "FAST_BLINK":  # 5 Hz
                    if self.ticks % (1 / (self.schedule_period * 5)) == 0:
                        self.status_led.value = not self.status_led.value
            self.ticks += 1
            await asyncio.sleep(self.schedule_period)
"""

# TODO: add log method/class, to handle debug and file logging
class ModuleManager():
    """
    Base module manager for Beatbox system.
    Handles CAN communication and provides a structure for module-specific functionality.
    Child classes must call super().__init__() with the corresponding module name (string)
    and can extend functionality by overriding methods.
    """
    def __init__(self, module_name: str = "GENERIC"):
        self.led = digitalio.DigitalInOut(USER_LED)
        self.led.direction = digitalio.Direction.OUTPUT
        self.led.value = True

        # Use ModuleRegistry for all lookups
        # TODO: handle invalid module_name
        self.id = ModuleMap.get_id(module_name)
        self.can_id = ModuleMap.get_can_id(self.id)
        print(f"Module initialized: NAME={module_name}, ID={self.id}, CAN_ID={self.can_id}")
        self.can_manager = CanManager(can_id=self.can_id)
        self.can_manager.register_rx_callback(self.can_msg_echo_on_serial)

        self.status = ModuleStatus.INIT
        self.tasks = [
            asyncio.create_task(self.can_manager.can_rx_task()),
            asyncio.create_task(self.can_manager.can_tx_task()),
            # asyncio.create_task(self.heartbeat_task()),
        ]
        self.heartbeat_interval = 0.02 # 20 ms

    """
        # Main loop task to handle module logic
        async def heartbeat_task(self):
            while True:
                # Placeholder for module-specific periodic tasks

                await asyncio.sleep(self.heartbeat_interval) 
    """

    def can_msg_echo_on_serial(self, msg):
        # TODO: check if serial manager exists and available
        print(f'{self.status}, received from ID={msg.id}, data={list(msg.data)}')
        self.command_handler(msg.data)

    def command_handler(self, command):
        """Handle incoming commands - to be overridden by child classes"""
        if b"SCAN" in command:
            # Respond to scan request with module ID
            self.can_manager.send_normal(data=bytearray(f"ID_{self.id}", 'utf-8'))
            self.status = ModuleStatus.IDLE
        if b"R_STATUS" in command:
                self.can_manager.send_normal(data=bytearray(f"ST_{self.status}", 'utf-8'))

    async def start_managers(self):
        """
        Start all module manager tasks
        Child classes should append their tasks to self.tasks before calling super().start_managers()
        """
        await asyncio.gather(*self.tasks)

    #@abstractmethod
    #def print(self): ...

