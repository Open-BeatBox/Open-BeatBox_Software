import asyncio

from canio import Message

from machine import Pin, UART
from beatbox_module import ModuleStatus, ModuleManager, ModuleMap
from can_protocol import (
    CommonCommand,
    FeederCommand,
    GenericCommand,
    IRBarrierCommand,
    LightingCommand,
    MessageType,
    NosepokeCommand,
    Priority,
    ScreenCommand,
    build_identifier,
    parse_identifier,
)
from serial_manager import SerialManager, BeatBoxCommManager

is_init = False

BAUD_RATE = 921600
DEBUG = False

class MainModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="MAIN")
        # self.serial_manager = SerialManager(serial=UART(0, baudrate=115200, tx=Pin(12), rx=Pin(13)))
        self.comm = BeatBoxCommManager(serial=UART(0, baudrate=BAUD_RATE, tx=Pin(0), rx=Pin(1)))
        self.serial_manager = self.comm.serial_manager
        # Create tasks for serial manager
        self.tasks.append(asyncio.create_task(self.comm.handshaker()))
        self.tasks.append(asyncio.create_task(self.serial_manager.serial_handler_task()))
        # Register RX callbacks
        self.can_manager.register_rx_callback(self.can_msg_handler)
        self.serial_manager.register_rx_callback(self.serial_msg_handler)
        self.scan_modules()

    def serial_msg_handler(self, msg):
        if msg == "SCAN":
            self.scan_modules()

    def can_msg_handler(self, msg):
        if not isinstance(msg, Message):
            # TODO: log error
            return
        priority, module_id, msg_type, command = parse_identifier(msg.id)
        module_name = ModuleMap.get_name(module_id) or f"M{module_id}"
        self.log(f'CAN_RX: ID={msg.id}, prio={priority}, module={module_name}, type={msg_type}, cmd={command}, data={list(msg.data)}')

        if msg_type == MessageType.RESPONSE_COMMON and command == CommonCommand.SCAN:
            print(f"FOUND_{module_name}")
            return

        if msg_type == MessageType.RESPONSE_COMMON and command == CommonCommand.GET_STATUS:
            status_code = msg.data[0] if msg.data else 255
            status = ModuleStatus.from_code(status_code)
            print(f"{module_name}_STATUS_{status}")
            return

        if msg_type == MessageType.RESPONSE_MODULE and module_id == ModuleMap.get_id("GENERIC"):
            if command == GenericCommand.TOGGLE_CAN_LED:
                state = msg.data[0] if msg.data else 255
                if state == 255:
                    print("GENERIC_CAN_LED_STATE_UNKNOWN")
                else:
                    print(f"GENERIC_CAN_LED_STATE_{state}")
            if command == GenericCommand.USER_BUTTON_EVENT:
                print("GENERIC_USER_BUTTON_PRESSED")
            return

        if msg_type == MessageType.RESPONSE_MODULE and module_id == ModuleMap.get_id("NOSEPOKE"):
            if command == NosepokeCommand.BEAM_EVENT:
                beam_state = msg.data[0] if msg.data else 0
                print(f"NOSEPOKE_EVENT_BEAM_{beam_state}")
            return

        if msg_type == MessageType.RESPONSE_MODULE and module_id == ModuleMap.get_id("FEEDER"):
            if command == FeederCommand.REWARD_DELIVERED:
                print("FEEDER_REWARD_DELIVERED")
            return

        if msg_type == MessageType.RESPONSE_MODULE and module_id == ModuleMap.get_id("IR_BARRIER"):
            if command == IRBarrierCommand.BARRIER_EVENT:
                line = msg.data[0] if len(msg.data) >= 1 else 255
                print(f"IR_BARRIER_EVENT_LINE_{line}")
            if command == IRBarrierCommand.GET_BARRIER_STATUS and len(msg.data) >= 2:
                print(f"IR_BARRIER_STATUS_MASK_{msg.data[0]}_LAST_{msg.data[1]}")
            return

        if msg_type == MessageType.RESPONSE_MODULE and module_id in (
            ModuleMap.get_id("SCREEN_LEFT"),
            ModuleMap.get_id("SCREEN_RIGHT"),
        ):
            if command == ScreenCommand.TOUCH_EVENT:
                print(f"{module_name}_TOUCH")
            return

        if msg_type == MessageType.RESPONSE_MODULE and module_id == ModuleMap.get_id("LIGHTING"):
            if command == LightingCommand.GET_DUTY and len(msg.data) >= 3:
                print(f"LIGHTING_DUTY_W{msg.data[0]}_R{msg.data[1]}_IR{msg.data[2]}")
            return

    def scan_modules(self):
        """Scan CAN bus for modules"""
        print(f"Scanning for modules...")
        scan_id = build_identifier(
            priority=Priority.STATUS,
            module_id=ModuleMap.BROADCAST_ID,
            msg_type=MessageType.REQUEST_COMMON,
            command=CommonCommand.SCAN,
        )
        self.can_manager.send_normal(can_id=scan_id, data=bytearray())

    def log(self, message: str):
        """TODO: implement logging method, just print for now"""
        if DEBUG:
            self.serial_manager.send_message(f"DEBUG: {message}")
        else:
            print(message)

# Start tasks
def main():
    beatbox = MainModule()
    print("Main Module started")
    asyncio.run(beatbox.start_managers())

main()