import asyncio
import board
import countio
import digitalio

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

from beatbox_module import ModuleManager, InterruptSensor
from bb_pin_map import NosepokePins as Pins

class NosepokeModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="NOSEPOKE")
        # Pass the callback to send CAN message immediately
        self._init_ir_beam()
        self.nosepoke = InterruptSensor(interrupt_pin=Pins.IR_BEAM_OUT, on_edge=self._on_poke)
        self.tasks.append(asyncio.create_task(self.nosepoke.catch_interrupt()))
        self.tasks.append(asyncio.create_task(self.reset_led()))

    def _init_ir_beam(self):
        self.sensor_in = digitalio.DigitalInOut(Pins.IR_BEAM_IN)
        self.sensor_in.direction = digitalio.Direction.OUTPUT # Sensor input as output to power the IR LED
        self.sensor_in.value = True

    def _on_poke(self):
        self.led.value = True
        self.can_manager.send_normal(data=b"NPK")

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.value = False
            await asyncio.sleep(0.1)

# Start tasks
if __name__ == "__main__":
    nose_poke = NosepokeModule()
    asyncio.run(nose_poke.start_managers())