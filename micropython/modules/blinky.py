import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI
from beatbox_module import ModuleManager
from bb_mpy_module import Led

class BlinkyModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="GENERIC")
        self.tasks.append(asyncio.create_task(self.blink()))
    async def blink(self):
        while True:
            self.led.toggle()
            await asyncio.sleep(0.5)

def main():
    blinky = BlinkyModule()
    asyncio.run(blinky.start_managers())