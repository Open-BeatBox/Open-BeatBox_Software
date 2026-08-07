import logging
import time
import platform
import threading
from datetime import datetime

import serial
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal
from serial.tools import list_ports

from beatbox_protocol import BeatBoxMessage
from beatbox.gui.bb_logging import logger  # shared logger

IN_TEST = 'pytest' in globals()
DEBUG = True

from serial.tools import list_ports

# Known BeatBox-compatible USB identifiers
_BEATBOX_USB_IDS: set[tuple[int, int]] = {
    (0x2341, 0x0042),  # Arduino SA — Mega 2560 R3
    (0x2A03, 0x0042),  # dog hunter AG — Mega 2560 Rev3
}

# Broader fallback: any board from these vendors
_FTDI_VID = 0x0403  # FTDI — always included regardless of PID or other matches
_KNOWN_ARDUINO_VIDS: set[int] = {
    0x2341,  # Arduino SA
    0x2A03,  # dog hunter AG / Arduino.org
    0x1A86,  # CH340 (cheap clones)
    _FTDI_VID,
    0x10C4,  # Silicon Labs CP210x
}


def get_platform_serial_ports(beatbox_only: bool = True) -> list:
    """Return serial ports, optionally filtered to BeatBox-compatible devices.

    Parameters
    ----------
    beatbox_only : bool
        If True, only return ports matching known BeatBox USB VID/PID pairs.
        Falls back to known Arduino vendor IDs if no exact matches found.
        If False, return all serial ports.
    """
    all_ports = list_ports.comports()

    if not beatbox_only:
        return all_ports

    # Try exact VID/PID match first
    candidates = [p for p in all_ports
             if p.vid is not None and (p.vid, p.pid) in _BEATBOX_USB_IDS]

    # Always include FTDI ports (any PID), merged with exact matches.
    ftdi_ports = [p for p in all_ports if p.vid == _FTDI_VID]
    combined = list({p.device: p for p in candidates + ftdi_ports}.values())
    if combined:
        return combined

    # Fall back to known Arduino vendors
    vendor = [p for p in all_ports
              if p.vid is not None and p.vid in _KNOWN_ARDUINO_VIDS]
    if vendor:
        return vendor

    # Last resort: return everything (user can pick manually)
    return all_ports

class BeatBoxCommunicator(QtCore.QObject):  # needs to inherit from QObject to use signals and slots
    """
    Class to handle serial communication with the BeatBox.
    This class manages the serial port connection, reading and writing messages,
    and handling the handshake process.
    The messages are encoded and decoded using the BeatBoxMessage class (see above).

    Signals:
    - messageReceived: emitted when a complete message is received.
    - errorOccurred: emitted when an error occurs during communication.
    - handshakeCompleted: emitted when the handshake is completed successfully.
    - connected: emitted when the serial port is successfully connected.

    Attributes:
    - baud_rate: the baud rate for the serial connection (default is 115200).
    - serial_timeout: the timeout for the serial connection (default is 2 seconds).
    - serial_port: the serial port object (initialized to None).
    - rx_buffer: the buffer for received data (initialized to an empty byte string).
    - sequence: the current sequence number for messages (initialized to 0).
    - last_message: the last message sent or received (initialized to an empty byte string).
    - verbose: whether to print debug messages (default is True).
    """
    # Signals for communication events.
    messageReceived = pyqtSignal(object)  # Emitting a BeatBoxMessage object
    errorOccurred = pyqtSignal(str)
    handshakeCompleted = pyqtSignal()
    connected = pyqtSignal()
    stateValueReceived = pyqtSignal(int)  # the word representing the state of the BeatBox digital inputs

    def __init__(self, baud_rate=115200, serial_timeout=2, port='', parent=None):
        super().__init__(parent)
        self.baud_rate = baud_rate
        self.serial_timeout = serial_timeout
        self.serial_port = None
        self.rx_buffer = b""
        self.sequence = 0
        self.last_message = b""
        self._stop_flag = False
        self._reader_thread = None
        self.verbose = DEBUG

        if port:
            self.connect_port(port)

    def connect_port(self, port_name: str):
        logger.debug(f"Connecting to {port_name}...")
        try:
            self.serial_port = serial.Serial(
                port=port_name,
                baudrate=self.baud_rate,
                timeout=self.serial_timeout)
            # self.flush()
            self.connected.emit()
            logger.debug(f"Connection established to {port_name}, starting reader thread.")
        except Exception as e:
            if IN_TEST:
                logging.warning(f"Error connecting to {port_name}: {e}")
            else:
                self.errorOccurred.emit(f"Error connecting to {port_name}: {e}")

    def start_reader_thread(self):
        self._stop_flag = False
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _next_sequence(self) -> int:
        seq = self.sequence
        self.sequence = (self.sequence + 1) % 256  # Wrap around at 256  (FIXME: derive from n_bytes_seq and seq_format)
        return seq

    def send_message(self, command_name: str, payload: str | list[int] = ""):
        self.last_message = BeatBoxMessage.from_parts(command_name, self._next_sequence(), payload)  # TODO: use for ack handling
        self._write(self.last_message.to_bytes())

    def _write(self, message: bytes):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(message)
                logger.debug(f"Sent: {message.decode()}")
            except Exception as e:
                self.errorOccurred.emit(f"Error sending message: {e}")
        else:
            self.errorOccurred.emit("Serial port not open.")

    def handshake(self) -> bool:
        # Flush input buffer
        if not (self.serial_port and self.serial_port.is_open):
            if IN_TEST:
                logging.warning("Serial port not open for handshake.")
            else:
                self.errorOccurred.emit("Serial port not open for handshake.")
            return False

        # Clear out any junk already in the buffer
        self.rx_buffer = b''
        self.serial_port.reset_input_buffer()

        seq = self._next_sequence()
        command = str(BeatBoxMessage.from_parts('ready', seq)).encode()
        try:
            self.serial_port.write(command)
            logger.debug(f"Sent handshake: {command.decode()}")
            start = datetime.now().timestamp()

            while (datetime.now().timestamp() - start) < self.serial_timeout:
                logging.debug(f'{self.__class__.__name__} waiting for handshake')
                time.sleep(0.1)  # short sleep to yield thread time  # FIXME: diminish this
                data = self.serial_port.read(self.serial_port.in_waiting or 1)
                if data:
                    self.rx_buffer += data

                # if more than 1 handshake in the queue take the latest one
                if self.rx_buffer.count(b'ready') > 1:
                    logger.debug("Multiple handshake messages received, taking the latest one.")
                    self.rx_buffer = self.rx_buffer.rsplit(b'ready:', 1)[-1]

                while b'\n' in self.rx_buffer:
                    if self.rx_buffer.startswith(b'\r') or self.rx_buffer.startswith(b'\n'):
                        # Remove the leading \r
                        self.rx_buffer = self.rx_buffer[1:]
                        continue
                    if len(self.rx_buffer) > 2:
                        line, self.rx_buffer = self.rx_buffer.split(b'\n', 1)
                        if not line:
                            continue
                    elif self.rx_buffer == b'\r\n':
                        self.rx_buffer = b""
                        continue
                    else:
                        raise ValueError(f"Unexpected data during handshake: {self.rx_buffer}")

                    # TODO check if we need line.strip()
                    msg_string = line.decode()
                    msg = BeatBoxMessage(msg_string)
                    if msg.command == "ready":
                        logger.debug(f"Received handshake response: {line.decode()}")
                        self.handshakeCompleted.emit()
                        self.start_reader_thread()
                        return True
                    elif msg.command in ('starting', 'inited'):
                        logger.debug(f'Received MCU startup command {msg.command} message: {line.decode()}')
                        continue
                    else:
                        logging.warning(f"Unexpected message during handshake: {line.decode()}")

            if IN_TEST:
                logging.error("Handshake timeout.")
            else:
                self.errorOccurred.emit("Handshake timeout.")
            return False
        except Exception as e:
            if IN_TEST:
                logging.critical(f"Error during handshake: {e}")
            else:
                self.errorOccurred.emit(f"Error during handshake: {e}")
            return False

    def flush(self):
        """Flush the serial port input buffer."""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.reset_input_buffer()
            self.rx_buffer = b""
            logger.debug("Serial port input buffer flushed.")
        else:
            logger.error("Serial port not open, cannot flush input buffer.")

    def _read_loop(self):
        """Dedicated thread function that blocks on reading incoming data."""
        while not self._stop_flag:
            line = None  # For error reporting
            message = None  # For error reporting
            try:
                # blocks until at least one byte is available or timeout occurs.
                data = self.serial_port.read(self.serial_port.in_waiting or 1)
                if not data:
                    raise BufferError("No byte available or EOF reached.")

                self.rx_buffer += data

                if self.rx_buffer.count(b'ready:') > 1:
                    logger.debug("Multiple handshake messages received, taking the latest one.")
                    self.rx_buffer = self.rx_buffer.rsplit(b'ready:', 1)[-1]

                # Process complete lines
                while b'\n' in self.rx_buffer:
                    line = None  # For error reporting
                    message = None  # For error reporting
                    line, self.rx_buffer = self.rx_buffer.split(b'\n', 1)
                    line = line.strip()
                    if not line:
                        continue # empty line
                    # Parse the line into a BeatBoxMessage
                    message = BeatBoxMessage.from_bytes(line)
                    if not message.is_valid():
                        raise Exception(f"Invalid message received: {line}, {message.get_error()}")

                    logger.debug(f"Received: {line.decode()}")
                    # TODO: refactor using switch case
                    if message.command == 'ack':
                        self.handle_ack(message)
                    elif message.command == 'ready':
                        logging.warning(f'Received ready message "{line.decode()}" outside of handshake.')
                        self.send_message('ready:')
                        self.handshakeCompleted.emit()
                    elif message.command in ('starting', 'inited'):
                        logging.warning(f"Warning, got {message.command}, this should be reserved to handshake.")
                    else:
                        if message.command == 'state':
                            state_value = message.decode_payload()[0]
                            self.stateValueReceived.emit(state_value)  # tell the ExperimentMgr
                        else:
                            self.messageReceived.emit(message)

            except BufferError as e:
                pass # FIXME: not sure if this exception is really needed
                # logger.debug(f"Serial read_loop: {e}")

            except Exception as e:  # Detailed error message for debugging
                html = f"""
                <b>Error reading serial:</b>
                <ul>
                    <li><b>Exception:</b> {e}</li>
                    <li><b>Context:</b>
                        <ul>
                            <li><b>Buffer:</b> {self.rx_buffer.decode() if self.rx_buffer else 'empty buffer'}</li>
                            <li><b>Latest line:</b> {line.decode() if line is not None else 'N/A'}</li>
                            <li><b>Latest message:</b> {message if message is not None else 'N/A'}</li>
                        </ul>
                    </li>
                </ul>
                """

                self.errorOccurred.emit(html)
                break  # or continue, depending on how you want to handle persistent errors
            time.sleep(0.001)  # slight delay to avoid busy looping

    def handle_ack(self, message: BeatBoxMessage):
        if message.sequence == self.sequence:
            logger.debug("Received ACK for last message.")
        else:
            logger.warning("Received ACK for unknown message.") # FIXME: handle this case
            logger.debug(f"Received sequence: {message.sequence}, self.sequence: {self.sequence}")
    # def send_set_command(self, checked_pins, rgb_values):
    #     bitmask = sum(2**i for i, checked in enumerate(checked_pins) if checked)
    #
    #     payload = [bitmask >> 8, bitmask & 0xff]
    #
    #     if rgb_values is not None:
    #         payload.extend(rgb_values)
    #     self.send_message('set', payload)

    @QtCore.pyqtSlot(list)  # NEW
    def send_set_payload(self, payload: list[int]):
        """Wire-ready helper for ExperimentManagers."""
        self.send_message('set', payload)

    def close(self):
        """Stop the reader thread and close the serial port."""
        self._stop_flag = True
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1)
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()


class SetPayloadOutputBus(QtCore.QObject):
    """
    Collects masks from the experiment logic.
    This assumes a payload of 2 bytes, where the first byte is the high byte
    This is meant to be used for sending 'set' commands to the BeatBox.

    The messages are sent from the client code using the `sendMessage` signal.

    if `flush=True`:
        send immediately.
    else:
        OR them for ≤merge_ms and send once.
    """
    sendMessage = pyqtSignal(list)        # hi/lo bytes

    def __init__(self, merge_ms=5, parent=None):
        """
        Initialize the SetPayloadOutputBus with a merge delay in milliseconds.

        Parameters
        ----------
        merge_ms : int
            The time in milliseconds to wait before sending the accumulated messages.
            If set to 0, messages are sent immediately without merging (same as flush=True).
        parent : QObject, optional
            The parent QObject for this SetPayloadOutputBus. Defaults to None.
        """
        super().__init__(parent)
        self._pending = None                 # type: list[int] | None
        self._timer   = QtCore.QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._on_timeout)
        self.merge_ms = merge_ms

    #@QtCore.pyqtSlot(list, bool)
    def post(self, payload: list[int], flush: bool = False):
        """
        Post a payload to the SetPayloadOutputBus.
        If `flush` is True, send the payload immediately.
        If `flush` is False, accumulate the payloads for `merge_ms` milliseconds
        using a `or` operation on the masks.
        """
        if flush or self.merge_ms == 0:
            self.sendMessage.emit(payload)
            return

        if self._pending is None:
            self._pending = payload[:]       # copy
            self._timer.start(self.merge_ms)
        else:                                # OR the masks
            self._pending[0] |= payload[0]
            self._pending[1] |= payload[1]

    def _on_timeout(self):
        if self._pending is not None:
            self.sendMessage.emit(self._pending)
            self._pending = None


class HandshakeWorker(QtCore.QObject):
    """
    Runs BeatBoxCommunicator.handshake() in a separate thread so the
    Qt event-loop never blocks.  Emits one signal when finished.
    """
    finished = pyqtSignal(bool)          # True = success

    def __init__(self, communicator: BeatBoxCommunicator):
        super().__init__()
        self._comm = communicator

    @QtCore.pyqtSlot()
    def run(self):
        ok = self._comm.handshake()             # <-- your existing blocking call
        self.finished.emit(ok)
