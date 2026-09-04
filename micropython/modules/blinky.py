import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI
from beatbox_module import ModuleManager
from bb_mpy_module import Led
from bb_pin_map import PinMap
from can_protocol import MessageType, NosepokeCommand, Priority, parse_identifier

class BlinkyModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="GENERIC")
        self._init_ir_beam()
        self.tasks.append(asyncio.create_task(self.blink()))


    def _init_ir_beam(self):
        self.sensor_out = Pin(PinMap.USER_BTN, Pin.IN)
        self.sensor_out.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        # self.led.on()
        self._send_frame(
            priority=Priority.EVENT,
            msg_type=MessageType.RESPONSE_MODULE,
            command=NosepokeCommand.BEAM_EVENT,
            payload=bytearray([1]),
        )

    async def blink(self):
        while True:
            self.led.toggle()
            await asyncio.sleep(0.5)

def main():
    blinky = BlinkyModule()
    asyncio.run(blinky.start_managers())