import asyncio

from canio import Message
from machine import Pin, UART

import bb_events
import bb_intents as intents
from beatbox_module import ModuleManager
from bb_frontends import CliFrontend, V2Frontend
from serial_manager import SerialManager

BAUD_RATE = 921600
DEBUG = False
# Deploy-time mode selection. CLI_MODE=True gives the validation CLI
# (tools/bb_cli.py) the UART; False runs the legacy BB Mega V2 emulation.
CLI_MODE = False


class MainModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="MAIN")      # builds can_manager, tasks
        self.can_manager.reset_filters()
        self.can_manager.register_rx_callback(self.can_msg_handler)

        uart = UART(0, baudrate=BAUD_RATE, tx=Pin(0), rx=Pin(1))
        self.serial_manager = SerialManager(serial=uart)

        self.frontends = []
        if CLI_MODE:
            self.frontends.append(CliFrontend(self.serial_manager, self.send_intent))
        else:
            self.frontends.append(V2Frontend(self.serial_manager, self.send_intent))
            # human-readable mirror on the USB REPL, no UART traffic
            self.frontends.append(CliFrontend(self.serial_manager, self.send_intent,
                                              send_line=print, rx=False))

        for frontend in self.frontends:
            for coro in frontend.tasks():
                self.tasks.append(asyncio.create_task(coro))
        self.tasks.append(
            asyncio.create_task(self.serial_manager.serial_handler_task()))

        self.scan_modules()

    # ─── outbound ────────────────────────────────────────────

    def send_intent(self, intent):
        self.log("CAN_TX: ID=0x%03X data=%s" % (intent.can_id, list(intent.payload)))
        self.can_manager.send_normal(can_id=intent.can_id, data=intent.payload)

    def scan_modules(self):
        """Scan CAN bus for modules."""
        self.log("Scanning for modules...")
        self.send_intent(intents.scan())

    # ─── inbound ─────────────────────────────────────────────

    def can_msg_handler(self, msg):
        if not isinstance(msg, Message):
            self.log("CAN_RX: dropped non-Message %r" % (msg,))
            return
        self._handle_can_message(msg)

    def _handle_can_message(self, msg):
        event = bb_events.decode(msg.id, msg.data)
        self.log("CAN_RX: %r" % (event,))
        for frontend in self.frontends:
            try:
                frontend.on_event(event)
            except Exception as exc:
                print("Frontend error on %r: %s" % (event, exc))

    def log(self, message):
        """Debug logs stay on the USB REPL; the UART carries protocol only."""
        if DEBUG:
            print(message)


def main():
    beatbox = MainModule()
    print("Main Module started")
    asyncio.run(beatbox.start_managers())


main()