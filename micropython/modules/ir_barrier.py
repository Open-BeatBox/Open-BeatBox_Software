import asyncio

from machine import Pin
from canio import Match
from bb_pin_map import IRBarrierPins as PinMap
from beatbox_module import ModuleManager, ModuleMap
from can_protocol import IRBarrierCommand, MessageType, Priority, parse_identifier


class IRBarrierModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="IR_BARRIER")
        self.sensors = []
        self.last_triggered_line = 0xFF
        # self._init_ir_barriers()
        self._init_ir_barrier()
        self.tasks.append(asyncio.create_task(self.reset_led()))

    def _init_ir_barriers(self):
        for line_index, pin_number in enumerate(PinMap.IR_BARRIER_LINES):
            sensor = Pin(pin_number, Pin.IN)
            sensor.irq(
                trigger=Pin.IRQ_FALLING,
                handler=self._make_irq_handler(line_index),
            )
            self.sensors.append(sensor)

    def _init_ir_barrier(self):
        sensor = Pin(0, Pin.IN)
        sensor.irq(
            trigger=Pin.IRQ_FALLING,
            handler=self._make_irq_handler(1),
        )
        self.sensors.append(sensor)

    def _make_irq_handler(self, line_index):
        """ Create an IRQ handler for a specific line index."""
        def _handler(_pin):
            self._on_barrier(line_index)

        return _handler

    def _read_barrier_mask(self):
        # Active-low sensors: 0 means beam is broken.
        mask = 0
        for line_index, sensor in enumerate(self.sensors):
            if sensor.value() == 0:
                mask |= 1 << line_index
        return mask

    def _on_barrier(self, line_index):
        # self.led.on()
        self.last_triggered_line = line_index
        self._send_frame(
            priority=Priority.EVENT,
            msg_type=MessageType.RESPONSE_MODULE,
            command=IRBarrierCommand.BARRIER_EVENT,
            payload=bytearray([line_index, 1]),
        )
        print(f"IR Barrier line {line_index} triggered. Current mask: {self._read_barrier_mask():08b}")

    def command_handler(self, msg):
        super().command_handler(msg)

        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE:
            return
        if target_module_id != self.id and target_module_id != ModuleMap.BROADCAST_ID:
            return
        if command == IRBarrierCommand.GET_BARRIER_STATUS:
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_MODULE,
                command=IRBarrierCommand.GET_BARRIER_STATUS,
                payload=bytearray([self._read_barrier_mask(), self.last_triggered_line]),
            )

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.off()
            await asyncio.sleep(0.1)


def main():
    ir_barrier = IRBarrierModule()
    asyncio.run(ir_barrier.start_managers())

