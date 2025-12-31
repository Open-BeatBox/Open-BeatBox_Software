import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI

from beatbox_module import ModuleManager
from bb_pin_map import ScreenPins as Pin_map

class ScreenModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="SCREEN")
        # Pass the callback to send CAN message immediately
        self._init_ir_beam()
        self.tasks.append(asyncio.create_task(self.reset_led()))

    def _init_ir_beam(self):
        self.sensor = Pin(Pin_map.IR_BEAM, Pin.IN)
        self.sensor.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        self.led.on()
        self.can_manager.send_normal(data=b"SCT")

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.off()
            await asyncio.sleep(0.1)

# Start tasks
def main():
    screen = ScreenModule()
    asyncio.run(screen.start_managers())