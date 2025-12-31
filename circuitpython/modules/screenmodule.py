import asyncio

import board
import countio
import digitalio
import microcontroller

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

from beatbox_module import ModuleManager, InterruptSensor, PollingSensor
from bb_pin_map import ScreenPins as Pins

class BeatboxRegister:
    # Define Beatbox Register
    def __init__(self, name, addr, size_bytes):
        self._nvm = microcontroller.nvm
        self.name = name
        self.addr = addr
        self.size_bytes = size_bytes

    @property
    def value(self):
        return self._nvm[self.addr : self.addr + self.size_bytes]

    @value.setter
    def value(self, data):
        if len(data) != self.size_bytes:
            raise ValueError(f"Data length {len(data)} does not match register size {self.size_bytes}")
        self._nvm[self.addr : self.addr + self.size_bytes] = data

class BeatboxNVM:
    # NVM Register mapping, REG_NAME, REG_ADDR, REG_SIZE_BYTES
    reg_map = {
        "screen_side_info" : BeatboxRegister("SCREEN_SIDE_INFO", 0x00, 1),
        "name": BeatboxRegister("NAME", 0x01, 256)
    }

# TODO: move into a common utilities module

class ScreenModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="SCREEN")
        self.ir_beam = PollingSensor(pin=Pins.IR_BEAM, on_edge=self._on_touch)
        print(f"Screen side info: {self._read_side_info()}")
        self.tasks.append(asyncio.create_task(self.ir_beam.poll_status()))
        self.tasks.append(asyncio.create_task(self.reset_led()))


    @staticmethod
    def _read_side_info():
        side_info = BeatboxNVM.reg_map["screen_side_info"].value
        return side_info

    def _on_touch(self):
        self.led.value = True
        self.can_manager.send_normal(data=b"SC_TCH")

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.value = False
            await asyncio.sleep(0.1)

# Start tasks
if __name__ == "__main__":
    nose_poke = ScreenModule()
    asyncio.run(nose_poke.start_managers())