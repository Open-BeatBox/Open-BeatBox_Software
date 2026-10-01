import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI, UART
from beatbox_module import ModuleManager
from bb_mpy_module import Led

class CommModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="COMM")
        self.serial = UART(0, baudrate=115200, tx=Pin(12), rx=Pin(13))
        self.tasks.append(asyncio.create_task(self.blink()))
        self.tasks.append(asyncio.create_task(self.read()))

    async def blink(self):
        while True:
            self.led.toggle()
            await asyncio.sleep(0.5)

    async def read(self):
        while True:
            buf = []
            while True and ('\n' not in buf):
                count = self.serial.any()
                if count != 0:
                    buf.append(self.serial.read(count))
                    print(f"Serial RX: {buf}")
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.02)

def main():
    comm = CommModule()
    asyncio.run(comm.start_managers())