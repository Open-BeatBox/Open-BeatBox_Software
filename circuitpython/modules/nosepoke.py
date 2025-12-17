import asyncio
import board
import countio
import digitalio
import time

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

from beatbox_module import ModuleRegistry, ModuleStatus, ModuleManager

nose_poke = time.monotonic()
time_ref = time.monotonic_ns()

# TODO: move into a common utilities module
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
                    last_count = interrupt.count
                    nose_poke = time.monotonic()
                # Let another task run.
                await asyncio.sleep(0.02)

class NosepokeModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="NOSEPOKE")
        self.sensor = PhotoInterrupter(in_pin=board.D10, out_pin=board.D9)
        self.tasks.append(asyncio.create_task(self.sensor.catch_interrupt()))
        self.tasks.append(asyncio.create_task(self.check_poke()))

    async def check_poke(self):
        global nose_poke
        last_poke = nose_poke
        while True:
            if last_poke != nose_poke:
                last_poke = nose_poke
                self.led.value = True
                self.can_manager.send_normal(self.can_id, data=b"NPK")
            else:
                self.led.value = False
            await asyncio.sleep(0.1)

# Start tasks
if __name__ == "__main__":
    nose_poke = NosepokeModule()
    asyncio.run(nose_poke.start_managers())