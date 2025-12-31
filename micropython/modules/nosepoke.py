import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI

from beatbox_module import ModuleManager
from bb_pin_map import NosepokePins as Pin_map

class NosepokeModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="NOSEPOKE")
        # Pass the callback to send CAN message immediately
        self._init_ir_beam()
        self.tasks.append(asyncio.create_task(self.reset_led()))


    def _init_ir_beam(self):
        self.sensor_in = Pin(Pin_map.IR_BEAM_IN, Pin.OUT)
        self.sensor_in.high()
        self.sensor_out = Pin(Pin_map.IR_BEAM_OUT, Pin.IN)
        self.sensor_out.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        self.led.on()
        self.can_manager.send_normal(data=b"NPK")

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.off()
            await asyncio.sleep(0.1)

# Start tasks
def main():
    nose_poke = NosepokeModule()
    asyncio.run(nose_poke.start_managers())