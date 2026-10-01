import asyncio
import board
import countio
import digitalio
import time

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

from beatbox_module import ModuleRegistry, ModuleStatus, ModuleManager

class BlinkyModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="GENERIC")
        self.tasks.append(asyncio.create_task(self.blink()))

    async def blink(self):
        while True:
            self.led.value = not self.led.value
            await asyncio.sleep(0.5)

# Start tasks
if __name__ == "__main__":
    blinky = BlinkyModule()
    asyncio.run(blinky.start_managers())