"""Serial communication utilities and BB Mega V2 wire-protocol emulation."""

import asyncio
import time

try:
    from beatbox_protocol.crc import crc8
    from beatbox_protocol.message import BeatBoxMessage
except ImportError:
    from message import BeatBoxMessage
    from crc import crc8


def _ticks_us():
    """MicroPython/CPython compatible microsecond tick helper."""
    ticks = getattr(time, "ticks_us", None)
    if callable(ticks):
        return ticks()
    return int(time.monotonic() * 1_000_000)


class SerialManager:
    def __init__(self, serial=None):
        self.tx_queue = []
        self.command_callbacks = []
        self.last_activity = _ticks_us()
        self.serial = serial
        self.rx_buffer = ""
        self.status = "OK"  # OK or STARTED or ERROR

    def register_rx_callback(self, callback):
        self.command_callbacks.append(callback)

    def send_message(self, message):
        """Queue message for transmission (newline is added in writer)."""
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
            print(f"Data port write error: {exc}")
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
            print(f"Data port read error: {exc}")
        return ""

    async def serial_handler_task(self):
        print("Serial Handler started")
        self.status = "STARTED"
        while True:
            start_time = _ticks_us()

            try:
                # Send queued messages to data port
                while self.tx_queue:
                    msg = self.tx_queue.pop(0)
                    if not self._write_to_data_port(msg):
                        print("Error sending message")
                    await asyncio.sleep(0.01)

                incoming_data = self._read_from_data_port()
                if incoming_data:
                    self.rx_buffer += incoming_data
                    self.last_activity = _ticks_us()

                    while "\n" in self.rx_buffer:
                        line, self.rx_buffer = self.rx_buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        print(f"Received via serial comm port:\n'{line}'")
                        for callback in self.command_callbacks:
                            try:
                                callback(line)
                            except Exception as exc:
                                self.status = "ERROR"
                                print(f"Error in command callback: {exc}")

                processing_time = (_ticks_us() - start_time) / 1000
                if processing_time > 40:
                    self.status = "ERROR"
                    print("Warning: Serial processing took %.1fms" % processing_time)

            except Exception as exc:
                print(f"Serial handler error: {exc}")

            if _ticks_us() - self.last_activity > 1_000_000:
                self.status = "STARTED"

            await asyncio.sleep(0.020)


class BeatBoxV2Emulator:
    """Keep V2 pin states in the exact BB Mega firmware bit ordering."""

    # Ordering copied from BB_mega firmware arrays.
    INPUT_PIN_ORDER = (
        "nose_poke",
        "left_response",
        "right_response",
        "tunnel_1",
        "tunnel_2",
        "feeder_alert",
    )
    OUTPUT_PIN_ORDER = (
        "reward",
        "blink_left",
        "fig_1_left",
        "fig_2_left",
        "fig_3_left",
        "blink_right",
        "fig_1_right",
        "fig_2_right",
        "fig_3_right",
        "light",
        "red_light",
    )

    def __init__(self):
        self.input_pin_states = {name: 0 for name in self.INPUT_PIN_ORDER}
        self.output_pin_states = {name: 0 for name in self.OUTPUT_PIN_ORDER}

    @staticmethod
    def _as_bit(value):
        return 1 if value else 0

    def set_output_states_from_word(self, pin_states_word):
        # should check if change in output states and convert to appropriate V3 command
        for index, pin_name in enumerate(self.OUTPUT_PIN_ORDER):
            self.output_pin_states[pin_name] = self._as_bit((pin_states_word >> index) & 0x01)

    def update_input_pin_states(self, pin_updates):
        """Update a subset of input pins, return True if any state changed."""
        changed = False
        for pin_name, value in pin_updates.items():
            if pin_name not in self.input_pin_states:
                continue
            bit = self._as_bit(value)
            if bit != self.input_pin_states[pin_name]:
                self.input_pin_states[pin_name] = bit
                changed = True
        return changed

    def get_input_state_byte(self):
        state_byte = 0
        for index, pin_name in enumerate(self.INPUT_PIN_ORDER):
            state_byte |= (self._as_bit(self.input_pin_states[pin_name]) << index)
        return state_byte & 0xFF

    def get_output_pin_states_as_word(self):
        return "".join(
            "1" if self._as_bit(self.output_pin_states[pin_name]) else "0"
            for pin_name in self.OUTPUT_PIN_ORDER
        )


class BeatBoxCommManager:
    """Emulate BB Mega V2 serial behavior so legacy app can talk unchanged."""

    STARTING_STATUS = "ON_GOING"
    HANDSHAKE_RECEIVED_STATUS = "RECEIVED"
    HANDSHAKE_DONE_STATUS = "DONE"

    def __init__(self, serial=None):
        self.serial_manager = SerialManager(serial=serial)
        self.serial_manager.register_rx_callback(self.process_message)
        self.handshake_status = self.STARTING_STATUS
        self.status = ""
        self.emulator = BeatBoxV2Emulator()
        self.seq_nb = 0
        self._startup_frames_sent = False

    @staticmethod
    def _to_hex(value):
        return f"{value & 0xFF:02x}"

    @staticmethod
    def _split_command(command_with_payload):
        if ":" not in command_with_payload:
            return command_with_payload, ""
        command_name, payload = command_with_payload.split(":", 1)
        return command_name, payload

    def _build_raw(self, command_with_payload):
        command_name, payload = self._split_command(command_with_payload)
        return f"{command_name}:r00{payload}"

    def _build_framed(self, command_with_payload, with_seq_nb=False, data_type="s"):
        command_name, payload = self._split_command(command_with_payload)
        msg = f"{command_name}:{data_type}{payload}"
        if with_seq_nb:
            msg = f"{command_name}:{data_type}{self._to_hex(self.seq_nb)}{payload}"
        checksum = self._to_hex(crc8(msg.encode()))
        return f"{msg}{checksum}"

    def _send_raw(self, command_with_payload):
        self.serial_manager.send_message(self._build_raw(command_with_payload))

    def _send_framed(self, command_with_payload, with_seq_nb=False, data_type="s"):
        self.serial_manager.send_message(
            self._build_framed(command_with_payload, with_seq_nb=with_seq_nb, data_type=data_type)
        )

    @staticmethod
    def _is_crc_valid(message):
        if len(message) < 2:
            return False
        body = message[:-2]
        expected = message[-2:]
        try:
            expected_value = int(expected, 16)
        except ValueError:
            return False
        computed = crc8(body.encode())
        return computed == expected_value

    @staticmethod
    def _extract_seq_nb(message):
        colon_idx = message.find(":")
        if colon_idx < 0 or len(message) < colon_idx + 4:
            raise ValueError("Invalid framed message")
        return int(message[colon_idx + 2:colon_idx + 4], 16)

    @staticmethod
    def _command_name(message):
        return message.split(":", 1)[0]

    def increment_seq_nb(self):
        self.seq_nb = (self.seq_nb + 1) % 256

    def send_startup_frames(self):
        if self._startup_frames_sent:
            return
        self._send_raw("starting:")
        self._send_raw("inited:")
        self._startup_frames_sent = True

    def process_message(self, message):
        """Process incoming line received from serial task."""
        if isinstance(message, bytes):
            message = message.decode()
        message = message.strip()
        if not message:
            return

        if self.handshake_status != self.HANDSHAKE_DONE_STATUS:
            self.process_handshake(message)
            return

        self._process_framed_command(message)

    def process_handshake(self, message):
        """Firmware parity: handshake only checks incoming startswith('ready:')."""
        if message.startswith("ready:"):
            self.handshake_status = self.HANDSHAKE_RECEIVED_STATUS
            print("Handshake received!")
        else:
            print(f"Received in handshake:\n{message}\n")

    async def handshaker(self):
        """Emit startup and ready frames with BB Mega timing semantics."""
        while self.serial_manager.status != "STARTED":
            await asyncio.sleep(0.02)

        self.send_startup_frames()

        while self.handshake_status != self.HANDSHAKE_DONE_STATUS:
            # Ensure we send at least a handshake
            self._send_raw(f"ready:handshake(status={self.status})")
            if self.handshake_status == self.HANDSHAKE_RECEIVED_STATUS:
                self.handshake_status = self.HANDSHAKE_DONE_STATUS
                print("Handshake completed")
                break

            await asyncio.sleep(0.1)

    def _signal_error(self, error_info, command):
        payload = f"error:{error_info}({command})"
        self._send_framed(payload, with_seq_nb=True, data_type="s")

    def _ack_command(self, command_name):
        payload = f"ack:{command_name}"
        if command_name == "set":
            payload += f"_from{self.emulator.get_output_pin_states_as_word()}"
        self._send_framed(payload, with_seq_nb=True, data_type="s")

    def _parse_and_set_output_states(self, full_message):
        payload_start = len("set:") + 3  # datatype + two-char sequence
        if len(full_message) < payload_start + 4:
            return

        high_byte = int(full_message[payload_start:payload_start + 2], 16)
        low_byte = int(full_message[payload_start + 2:payload_start + 4], 16)
        pin_states_word = (high_byte << 8) | low_byte
        self.emulator.set_output_states_from_word(pin_states_word)

    def _process_framed_command(self, message):
        # Keep parser usage on message.py for framing compatibility and validation symmetry.
        try:
            parsed_message = BeatBoxMessage(message, None)
            _ = parsed_message.command
        except Exception:
            self._signal_error("Invalid format", message)
            return

        if not self._is_crc_valid(message):
            self._signal_error("CRC mismatch", message)
            return

        try:
            seq_nb = self._extract_seq_nb(message)
        except Exception:
            self._signal_error("Seq mismatch", message)
            return

        if seq_nb != self.seq_nb:
            self._signal_error("Seq mismatch", message)
            self.seq_nb = seq_nb

        command_name = self._command_name(message)
        self._ack_command(command_name)
        self.increment_seq_nb()

        if message.startswith("ready:"):
            self._send_framed("ready:loop", with_seq_nb=False, data_type="s")
            return

        if message.startswith("wait:"):
            sleep_ms = getattr(time, "sleep_ms", None)
            if callable(sleep_ms):
                sleep_ms(1000)
            else:
                time.sleep(1)
            return

        if message.startswith("set:"):
            self._parse_and_set_output_states(message)
            self._send_framed(f"done:{self._to_hex(self.seq_nb - 1)}", with_seq_nb=False, data_type="s")

    def on_input_pin_changes(self, pin_updates):
        """Send a state frame when emulated inputs change, mirroring firmware loop behavior."""
        changed = self.emulator.update_input_pin_states(pin_updates)
        if not changed:
            return
        state_byte = self.emulator.get_input_state_byte()
        self._send_framed(f"state:{self._to_hex(state_byte)}", with_seq_nb=True, data_type="x")
