import asyncio
import board
import digitalio
import time

from adafruit_mcp2515.canio import Message

from can_manager import CanManager
from serial_manager import SerialManager

is_init = False
time_ref = time.monotonic_ns()

class BeatboxManager:
    def __init__(self):
        self.led = digitalio.DigitalInOut(board.D24)
        self.led.direction = digitalio.Direction.OUTPUT
        self.led.value = True

        self.can_manager = CanManager()
        self.can_manager.register_rx_callback(self.can_msg_handler)

        self.serial_manager = SerialManager()
        self.serial_manager.register_command_callback(self.serial_msg_handler)

    async def start_managers(self):
        global time_ref
        time_ref = time.monotonic_ns()
        tasks = [
            asyncio.create_task(self.can_manager.can_rx_task()),
            asyncio.create_task(self.can_manager.can_tx_task()),
            asyncio.create_task(self.serial_manager.serial_handler_task()),
        ]
        await asyncio.gather(*tasks)

    def serial_msg_handler(self, msg):
        if msg == "GO":
            self.can_manager.send_normal(can_id=0x111, data=b"0x0000")
        print(f"Received {msg}")

    def can_msg_handler(self, msg):
        if not isinstance(msg, Message):
            return
        if b"ACK" in msg.data:
            self.serial_manager.send_message("DONE")
        print(f'CAN_RX: ID={msg.id}, DATA={list(msg.data)}')


# Start tasks
if __name__ == "__main__":
    beatbox = BeatboxManager()
    print("Main Module started")
    asyncio.run(beatbox.start_managers())