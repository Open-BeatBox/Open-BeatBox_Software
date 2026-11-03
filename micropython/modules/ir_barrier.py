import asyncio

import bb_ticks

from machine import Pin
from bb_pin_map import IRBarrierPins as PinMap
from beatbox_module import ModuleManager, ModuleMap
from can_protocol import IRBarrierCommand, MessageType, Priority, parse_identifier


class IRBarrierModule(ModuleManager):
    # debounce: minimum time (us) between accepted state changes per line
    DEBOUNCE_US = 10_000
    SEND_COALESCE_MS = 10 # drain/flush cadence
    BUFF_SIZE = 64

    def __init__(self):
        super().__init__(module_name="IR_BARRIER")

        self.sensors = []

        # last accepted state per line: True means "broken"
        self._last_broken = []

        # last time we accepted a state change per line
        self._last_accept_us = []

        self.last_triggered_line = 0xFF

        # small buffer of raw edge events: (ts_us, line_index, broken_bool)
        # ring buffer
        self._buf = [(0, 0, False)] * self.BUFF_SIZE
        self._buf_head = 0
        self._buf_tail = 0

        self._init_ir_barriers()

        self.tasks.append(asyncio.create_task(self._event_sender_task()))
        self.tasks.append(asyncio.create_task(self.reset_led()))

    def _init_ir_barriers(self):
        for line_index, pin_number in enumerate(PinMap.IR_BARRIER_LINES):
            sensor = Pin(pin_number, Pin.IN, Pin.PULL_UP)
            # init state
            broken = (sensor.value() == 0)
            self._last_broken.append(broken)

            self._last_accept_us.append(bb_ticks.ticks_us())

            sensor.irq(
                trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING,
                handler=self._make_irq_handler(line_index),
            )
            self.sensors.append(sensor)

    def _buf_is_empty(self):
        return self._buf_head == self._buf_tail

    def _buf_push(self, item):
        # non-allocating push, drop if full
        nxt = (self._buf_tail + 1) % len(self._buf)
        if nxt == self._buf_head:
            return
        self._buf[self._buf_tail] = item
        self._buf_tail = nxt

    def _buf_pop_all(self):
        items = []
        # items list allocates; happens in async context
        while not self._buf_is_empty():
            item = self._buf[self._buf_head]
            self._buf_head = (self._buf_head + 1) % len(self._buf)
            items.append(item)
        return items

    def _make_irq_handler(self, line_index):
        # IRQ handler: only record timestamp + new state (broken/clear)
        def _handler(_pin):
            ts = bb_ticks.ticks_us()
            broken = (self.sensors[line_index].value() == 0)
            # record event; payload will be sent later in async task
            self._buf_push((ts, line_index, broken))
        return _handler

    def _read_barrier_mask(self):
        # Active-low sensors: 0 means beam is broken.
        mask = 0
        for line_index, sensor in enumerate(self.sensors):
            if sensor.value() == 0:
                mask |= 1 << line_index
        return mask

    async def _event_sender_task(self):
        while True:
            await asyncio.sleep(self.SEND_COALESCE_MS / 1000)

            events = self._buf_pop_all()
            if not events:
                continue

            # ensure chronological order if multiple IRQs landed close together
            events.sort(key=lambda e: e[0])
            mask_changed = 0

            for ts_us, line_index, broken in events:
                # debounce/state-change filter per line:
                if broken == self._last_broken[line_index]:
                    continue  # no logical state change

                # require minimum time between accepted changes for this line
                if bb_ticks.ticks_diff(ts_us, self._last_accept_us[line_index]) < self.DEBOUNCE_US:
                    continue

                # accept change
                self._last_broken[line_index] = broken
                self._last_accept_us[line_index] = ts_us
                self.last_triggered_line = line_index
                mask_changed |= (1 << line_index)

                # send an EVENT for the accepted transition in order
                # payload: [line_index, 1] for broken, [line_index, 0] for clear
                self._send_frame(
                    priority=Priority.EVENT,
                    msg_type=MessageType.RESPONSE_MODULE,
                    command=IRBarrierCommand.BARRIER_EVENT,
                    payload=bytearray([line_index, 1 if broken else 0]),
                )

    def _send_status(self):
        # payload: [mask, last_triggered_line]
        self._send_frame(
            priority=Priority.STATUS,
            msg_type=MessageType.RESPONSE_MODULE,
            command=IRBarrierCommand.GET_BARRIER_STATUS,
            payload=bytearray([self._read_barrier_mask(), self.last_triggered_line]),
        )

    def command_handler(self, msg):
        super().command_handler(msg)

        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE:
            return
        if target_module_id != self.id and target_module_id != ModuleMap.BROADCAST_ID:
            return

        if command == IRBarrierCommand.GET_BARRIER_STATUS:
            self._send_status()

    async def reset_led(self):
        while True:
            self.led.off()
            await asyncio.sleep(0.1)


def main():
    ir_barrier = IRBarrierModule()
    asyncio.run(ir_barrier.start_managers())


# Deployed as :main.py the board runs this as __main__, so the module still
# auto-starts; importing it (tests, REPL) no longer touches the hardware.
if __name__ == "__main__":
    main()