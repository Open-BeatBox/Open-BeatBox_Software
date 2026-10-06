"""BB Mega V2 wire codec: lines <-> pin words.

Knows nothing about CAN, intents or transports. Injected by V2Frontend:
  send_line(str)                      -> emit one frame
  on_output_change(states, changed)   -> the app wrote the output pin word
"""

import asyncio

import bb_ticks
from bb_v2_emulator import BeatBoxV2Emulator

try:
    from beatbox_protocol.crc import crc8
    from beatbox_protocol.message import BeatBoxMessage
except ImportError:
    from crc import crc8
    from message import BeatBoxMessage

WAIT_MS = 1000


class V2WireCodec:
    """Emulate BB Mega V2 serial behaviour so the legacy app can talk unchanged."""

    STARTING_STATUS = "ON_GOING"
    HANDSHAKE_RECEIVED_STATUS = "RECEIVED"
    HANDSHAKE_DONE_STATUS = "DONE"

    def __init__(self):
        self.emulator = BeatBoxV2Emulator()
        self.send_line = None          # injected by V2Frontend
        self.on_output_change = None   # injected by V2Frontend
        self.handshake_status = self.STARTING_STATUS
        self.status = ""
        self.seq_nb = 0
        self._startup_frames_sent = False
        self._wait_until = None
        self._deferred = []

    # ─── framing helpers ─────────────────────────────────────

    @staticmethod
    def _to_hex(value):
        return "%02x" % (value & 0xFF)

    @staticmethod
    def _split_command(command_with_payload):
        if ":" not in command_with_payload:
            return command_with_payload, ""
        return command_with_payload.split(":", 1)

    def _emit(self, line):
        if self.send_line:
            self.send_line(line)

    def _build_raw(self, command_with_payload):
        command_name, payload = self._split_command(command_with_payload)
        return "%s:r00%s" % (command_name, payload)

    def _build_framed(self, command_with_payload, with_seq_nb=False, data_type="s"):
        command_name, payload = self._split_command(command_with_payload)
        if with_seq_nb:
            msg = "%s:%s%s%s" % (command_name, data_type,
                                 self._to_hex(self.seq_nb), payload)
        else:
            msg = "%s:%s%s" % (command_name, data_type, payload)
        return "%s%s" % (msg, self._to_hex(crc8(msg.encode())))

    def _send_raw(self, command_with_payload):
        self._emit(self._build_raw(command_with_payload))

    def _send_framed(self, command_with_payload, with_seq_nb=False, data_type="s"):
        self._emit(self._build_framed(command_with_payload,
                                      with_seq_nb=with_seq_nb, data_type=data_type))

    @staticmethod
    def _is_crc_valid(message):
        if len(message) < 2:
            return False
        try:
            expected_value = int(message[-2:], 16)
        except ValueError:
            return False
        return crc8(message[:-2].encode()) == expected_value

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

    # ─── handshake ───────────────────────────────────────────

    def send_startup_frames(self):
        if self._startup_frames_sent:
            return
        self._send_raw("starting:")
        self._send_raw("inited:")
        self._startup_frames_sent = True

    async def handshaker(self):
        """Emit startup and ready frames. Caller gates on transport readiness."""
        self.send_startup_frames()
        while self.handshake_status != self.HANDSHAKE_DONE_STATUS:
            self._send_raw("ready:handshake(status=%s)" % self.status)
            if self.handshake_status == self.HANDSHAKE_RECEIVED_STATUS:
                self.handshake_status = self.HANDSHAKE_DONE_STATUS
                print("Handshake completed")
                break
            await asyncio.sleep(0.5)

    def process_handshake(self, message):
        """Firmware parity: handshake only checks incoming startswith('ready:')."""
        if message.startswith("ready:"):
            self.handshake_status = self.HANDSHAKE_RECEIVED_STATUS
            print("Handshake received!")
        else:
            print("Received in handshake:\n%s\n" % message)

    # ─── inbound ─────────────────────────────────────────────

    def process_message(self, message):
        """Process one incoming line."""
        if isinstance(message, bytes):
            message = message.decode()
        message = message.strip()
        if not message:
            return
        if self._wait_until is not None:      # emulated blocking 'wait:'
            self._deferred.append(message)
            return
        self._dispatch(message)

    def service(self):
        """Call periodically: releases messages deferred by a 'wait:' frame."""
        if self._wait_until is None:
            return
        if bb_ticks.ticks_diff(bb_ticks.ticks_ms(), self._wait_until) < 0:
            return
        self._wait_until = None
        pending, self._deferred = self._deferred, []
        for message in pending:
            self.process_message(message)

    def _dispatch(self, message):
        if self.handshake_status != self.HANDSHAKE_DONE_STATUS:
            self.process_handshake(message)
            return
        self._process_framed_command(message)

    def _signal_error(self, error_info, command):
        self._send_framed("error:%s(%s)" % (error_info, command),
                          with_seq_nb=True, data_type="s")

    def _ack_command(self, command_name):
        payload = "ack:%s" % command_name
        if command_name == "set":
            payload += "_from%s" % self.emulator.get_output_pin_states_as_word()
        self._send_framed(payload, with_seq_nb=True, data_type="s")

    def _parse_and_set_output_states(self, full_message):
        payload_start = len("set:") + 3   # datatype + two-char sequence
        if len(full_message) < payload_start + 4:
            return
        high_byte = int(full_message[payload_start:payload_start + 2], 16)
        low_byte = int(full_message[payload_start + 2:payload_start + 4], 16)
        pin_states_word = (high_byte << 8) | low_byte
        changed = self.emulator.set_output_states_from_word(pin_states_word)
        if changed and self.on_output_change:
            self.on_output_change(dict(self.emulator.output_pin_states), changed)

    def _process_framed_command(self, message):
        # Keep parser usage on message.py for framing compatibility.
        try:
            _ = BeatBoxMessage(message, None).command
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

        # The ack deliberately echoes the OLD output word, so it must come
        # before the emulator is updated.
        self._ack_command(self._command_name(message))
        self.increment_seq_nb()

        if message.startswith("ready:"):
            self._send_framed("ready:loop", with_seq_nb=False, data_type="s")
            return

        if message.startswith("wait:"):
            # V2 blocked 1 s here; defer instead of stalling the CAN tasks.
            self._wait_until = bb_ticks.ticks_ms() + WAIT_MS
            return

        if message.startswith("set:"):
            self._parse_and_set_output_states(message)
            self._send_framed("done:%s" % self._to_hex(self.seq_nb - 1),
                              with_seq_nb=False, data_type="s")

    # ─── outbound ────────────────────────────────────────────

    def send_state_frame(self):
        """Emit the current input state unconditionally (post-handshake resync).

        Inputs no longer idle at zero (beam sensors are active-low), so the app
        must be told the initial levels explicitly.
        """
        self._send_framed(
            "state:%s" % self._to_hex(self.emulator.get_input_state_byte()),
            with_seq_nb=True, data_type="x")

    def on_input_pin_changes(self, pin_updates):
        """Emit a state frame when emulated inputs change."""
        if not self.emulator.update_input_pin_states(pin_updates):
            return
        self.send_state_frame()