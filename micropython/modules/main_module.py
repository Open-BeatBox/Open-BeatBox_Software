import asyncio

from canio import Message

from machine import Pin, UART
from beatbox_module import ModuleStatus, ModuleManager, ModuleMap
from serial_manager import SerialManager, BeatBoxCommManager

is_init = False

BAUD_RATE = 921600

class MainModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="MAIN")
        # self.serial_manager = SerialManager(serial=UART(0, baudrate=115200, tx=Pin(12), rx=Pin(13)))
        self.comm = BeatBoxCommManager(serial=UART(0, baudrate=BAUD_RATE, tx=Pin(12), rx=Pin(13)))
        self.serial_manager = self.comm.serial_manager
        # Create tasks for serial manager
        self.tasks.append(asyncio.create_task(self.comm.handshaker()))
        self.tasks.append(asyncio.create_task(self.serial_manager.serial_handler_task()))
        # Register RX callbacks
        self.can_manager.register_rx_callback(self.can_msg_handler)
        self.serial_manager.register_rx_callback(self.serial_msg_handler)

    def serial_msg_handler(self, msg):
        if msg == "SCAN":
            self.scan_modules()

    def can_msg_handler(self, msg):
        if not isinstance(msg, Message):
            # TODO: log error
            return
        self.log(f'CAN_RX: ID={msg.id}, DATA={msg.data.decode("utf-8")}')
        text = msg.data.decode("utf-8")

        if text.startswith("ID_"):
            module_id = int(text[3:])
            print(f"FOUND_{ModuleMap.get_name(module_id)}")
            # self.serial_manager.send_message(f"FOUND_{ModuleID.id_to_name[module_id]}")

        if text.startswith("ST_"):
            status = text[3:]
            print(f"{ModuleMap.get_name(msg.id)}_STATUS_{status}")

    def scan_modules(self):
        """Scan CAN bus for modules"""
        print(f"Scanning for modules...")
        self.can_manager.send_normal(data=b"SCAN")

    def log(self, message: str):
        """TODO: implement logging method, just print for now"""
        print(message)

# Start tasks
def main():
    beatbox = MainModule()
    print("Main Module started")
    asyncio.run(beatbox.start_managers())

main()