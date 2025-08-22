import asyncio
import board
import countio
import digitalio
import time

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

from can_manager import CanManager

nose_poke = time.monotonic()
time_ref = time.monotonic_ns()

class PhotoInterrupter:
    def __init__(self, in_pin, out_pin):
        self.sensor_in = digitalio.DigitalInOut(in_pin)
        self.sensor_in.direction = digitalio.Direction.OUTPUT
        self.sensor_in.value = True
        self.sensor_out = countio.Counter(out_pin, edge=countio.Edge.FALL)

    async def catch_interrupt(self):
        """Print a message when pin goes low."""
        global nose_poke
        last_count = 0
        with self.sensor_out as interrupt:
            while True:
                if interrupt.count != last_count:
                    print(f"In catch interrupt #{interrupt.count - last_count}")
                    last_count = interrupt.count
                    nose_poke = time.monotonic()
                # Let another task run.
                await asyncio.sleep(0.02)

class ModuleManager:
    def __init__(self):
        self.led = digitalio.DigitalInOut(board.D25)
        self.led.direction = digitalio.Direction.OUTPUT
        self.led.value = True

        self.can_manager = CanManager()
        self.can_manager.register_rx_callback(self.can_msg_handler)

        self.sensor = PhotoInterrupter(in_pin=board.D10, out_pin=board.D9)

    def can_msg_handler(self, msg):
        if not isinstance(msg, Message):
            return
        print(f'CAN_RX: ID={msg.id}, DATA={list(msg.data)}')
        if b"0x0000" in msg.data:
            self.can_manager.send_urgent(can_id = 0x169, data=b"ACK")

    async def check_poke(self):
        global nose_poke
        last_poke = nose_poke
        while True:
            if last_poke != nose_poke:
                print("Nose Poke")
                last_poke = nose_poke
                self.led.value = True
                self.can_manager.send_normal(can_id=0x169, data=b"NPK")
            else:
                self.led.value = False
            await asyncio.sleep(0.1)

    async def start_managers(self):
        global time_ref
        time_ref = time.monotonic_ns()

        tasks = [
            asyncio.create_task(self.can_manager.can_rx_task()),
            asyncio.create_task(self.can_manager.can_tx_task()),
            asyncio.create_task(self.sensor.catch_interrupt()),
            asyncio.create_task(self.check_poke())
        ]
        await asyncio.gather(*tasks)

# Start tasks
if __name__ == "__main__":
    nose_poke = ModuleManager()
    print("Nose Poke started")
    asyncio.run(nose_poke.start_managers())