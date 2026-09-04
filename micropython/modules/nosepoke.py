import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI

from beatbox_module import ModuleManager, ModuleMap
from can_protocol import MessageType, NosepokeCommand, Priority, parse_identifier
from bb_pin_map import NosepokePins as PinMap

class NosepokeModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="NOSEPOKE")
        self.beam_broken = False
        # Pass the callback to send CAN message immediately
        self._init_ir_beam()
        self.tasks.append(asyncio.create_task(self.reset_led()))


    def _init_ir_beam(self):
        self.sensor_in = Pin(PinMap.IR_BEAM_IN, Pin.OUT)
        self.sensor_in.high()
        self.sensor_out = Pin(PinMap.IR_BEAM_OUT, Pin.IN)
        self.sensor_out.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        self.led.on()
        self.beam_broken = True
        self._send_frame(
            priority=Priority.EVENT,
            msg_type=MessageType.RESPONSE_MODULE,
            command=NosepokeCommand.BEAM_EVENT,
            payload=bytearray([1]),
        )

    def command_handler(self, msg):
        super().command_handler(msg)

        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE:
            return
        if target_module_id != self.id and target_module_id != ModuleMap.BROADCAST_ID:
            return
        if command == NosepokeCommand.GET_BEAM_STATUS:
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_MODULE,
                command=NosepokeCommand.GET_BEAM_STATUS,
                payload=bytearray([1 if self.beam_broken else 0]),
            )

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.off()
            await asyncio.sleep(0.1)

# Start tasks
def main():
    nose_poke = NosepokeModule()
    asyncio.run(nose_poke.start_managers())