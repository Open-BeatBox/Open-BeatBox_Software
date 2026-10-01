"""Generic line-oriented serial transport. No protocol knowledge."""

import asyncio

import bb_ticks


class SerialManager:
    def __init__(self, serial=None):
        self.tx_queue = []
        self.command_callbacks = []
        self.last_activity = bb_ticks.ticks_us()
        self.serial = serial
        self.rx_buffer = ""
        self.running = False        # True once serial_handler_task is live
        self.status = "OK"          # OK / STARTED / ERROR, informational only

    def register_rx_callback(self, callback):
        self.command_callbacks.append(callback)

    def send_message(self, message):
        """Queue message for transmission (newline is added in the writer)."""
        if isinstance(message, bytes):
            self.tx_queue.append(message)
        else:
            self.tx_queue.append(str(message).encode())

    def _write_to_data_port(self, message):
        try:
            if self.serial is not None:
                if isinstance(message, str):
                    message = message.encode()
                if not message.endswith(b"\n"):
                    if not message.endswith(b"\r"):
                        message += b"\r"
                    message += b"\n"
                self.serial.write(message)
                return True
        except Exception as exc:
            print("Data port write error: %s" % exc)
        return False

    def _read_from_data_port(self):
        try:
            if self.serial and self.serial.any():
                data = self.serial.read()
                if isinstance(data, bytes):
                    return data.decode()
                if data is None:
                    return ""
                return str(data)
        except Exception as exc:
            print("Data port read error: %s" % exc)
        return ""

    async def serial_handler_task(self):
        print("Serial Handler started")
        self.running = True
        self.status = "STARTED"
        while True:
            start_time = bb_ticks.ticks_us()

            try:
                while self.tx_queue:
                    msg = self.tx_queue.pop(0)
                    if not self._write_to_data_port(msg):
                        print("Error sending message")
                    await asyncio.sleep(0.01)

                incoming_data = self._read_from_data_port()
                if incoming_data:
                    self.rx_buffer += incoming_data
                    self.last_activity = bb_ticks.ticks_us()

                    while "\n" in self.rx_buffer:
                        line, self.rx_buffer = self.rx_buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        for callback in self.command_callbacks:
                            try:
                                callback(line)
                            except Exception as exc:
                                self.status = "ERROR"
                                print("Error in command callback: %s" % exc)

                processing_time = bb_ticks.ticks_diff(
                    bb_ticks.ticks_us(), start_time) / 1000
                if processing_time > 40:
                    self.status = "ERROR"
                    print("Warning: Serial processing took %.1fms" % processing_time)

            except Exception as exc:
                print("Serial handler error: %s" % exc)

            if bb_ticks.ticks_diff(bb_ticks.ticks_us(),
                                   self.last_activity) > 1_000_000:
                self.status = "STARTED"

            await asyncio.sleep(0.020)