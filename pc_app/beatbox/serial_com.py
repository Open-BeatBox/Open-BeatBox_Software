import functools
import logging
import time
import platform
import threading
from datetime import datetime

import serial
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal
from serial.tools import list_ports

from beatbox.bb_logging import logger  # shared logger
from beatbox.utils import crc8


IN_TEST = 'pytest' in globals()
DEBUG = True

def get_platform_serial_ports():
    comports = list_ports.comports()
    if platform.system() == "Windows":
        ports = [p for p in comports if 'COM' in p.device]
    elif platform.system() == "Darwin":
        ports = [p for p in comports if 'usbserial' in p.device]
    elif platform.system() == "Linux":
        ports = [p for p in comports if 'ACM' in p.device]  # TODO: check if USB[0-9] is better
    else:
        raise NotImplementedError(f"Platform {platform.system()} not supported.")
    return ports


class BeatBoxMessage:
    """
    Class to handle encoding and decoding of messages to/from the BeatBox.
    The message format is as follows:
    <command>:<data type char><sequence><payload><crc8>\n
    where:
    - command is a string identifying the command (e.g. 'set', 'ack', 'ready')
    - sequence is a byte indicating the sequence number of the message
    - payload is a string of bytes representing the data
    - crc is a byte representing the CRC-8 checksum of the message

    The message can be encoded in different formats (e.g. hexadecimal, binary, octal, decimal).
    The format is specified using a format string, e.g. '{:02x}' for hexadecimal passed to
    the constructor.
    The default format is hexadecimal with 2 digits per byte.
    """
    last_sequence = 0
    sequence_format = '{:02x}'
    n_bytes_seq = 2  # Always hexadecimal
    crc_format = '{:02x}'
    n_bytes_crc = 2  # Always hexadecimal
    handshake_commands = ('ready', 'starting', 'inited')

    format_bases = {
        'b': 2,
        'o': 8,
        'd': 10,
        'x': 16,
        's': None,
        'r': None  # raw string, no CRC
    }

    def __init__(self, message: str, fmt: str | None = '{:02x}'):
        self.message = message
        if fmt is None:
            fmt = self.parse_format()
        self.format = fmt
        self.format_base = self.__get_format_base()
        self.n_bytes_encoding = self.__get_n_bytes_encoding()
        self.command, self.sequence, self.payload, self.crc = self._parse_message()

    @staticmethod
    def from_bytes(data: bytes, fmt: str | None = None):
        return BeatBoxMessage(data.decode(), fmt)

    @staticmethod
    def from_parts(command: str, sequence: int, payload: str | list[int] = "",
                   fmt: str | None = '{:02x}') -> 'BeatBoxMessage':
        if command.endswith(':'):
            command = command[:-1]
        encode = functools.partial(BeatBoxMessage.encode_int, fmt=fmt)
        if isinstance(payload, list):
            payload = ''.join(map(encode, payload))
        format_char = BeatBoxMessage.__format_to_format_char(fmt)
        sequence_chars = BeatBoxMessage.sequence_format.format(sequence)
        msg_bytes = f"{command}:{format_char}{sequence_chars}{payload}"

        # Add CRC unless this is a raw frame
        if format_char != 'r':
            crc = crc8(msg_bytes.encode())
            msg_bytes += BeatBoxMessage.crc_format.format(crc)

        return BeatBoxMessage(msg_bytes, fmt=fmt)

    @staticmethod
    def encode_int(x: int, fmt: str = '{:02x}') -> str:
        """
        Encode an integer as a string using the specified format.
        """
        return fmt.format(x)

    def decode_int(self, x: str, format_base: int=0) -> int:
        """
        Decode an integer from a string using the specified format.
        """
        if format_base == 0:
            format_base = self.format_base
        return int(x, format_base)

    def parse_format(self):
        command, message_body = self.message.split(':', maxsplit=1)
        if not message_body:
            raise ValueError("Invalid message format: no payload found.")
        else:
            return self.__format_char_to_format(message_body[0])

    @staticmethod
    def __format_char_to_format(format_char: str) -> str:
        """
        Convert a format character to a format string.
        e.g. 'x' -> '{:02x}'
        """
        formats = {
            'b': '{:08b}',
            'o': '{:03o}',
            'd': '{:02d}',
            'x': '{:02x}',
            's': '{}',
            'r': '{}',  # raw string, no CRC
        }
        return formats.get(format_char, '{:02x}')

    @staticmethod
    def __format_to_format_char(fmt: str) -> str:
        """
        Convert a format string to a format character.
        e.g. '{:02x}' -> 'x'
        """
        formats = {
            '{:08b}': 'b',
            '{:03o}': 'o',
            '{:02d}': 'd',
            '{:02x}': 'x',
            '{}': 's'
        }
        return formats[fmt]

    def decode_payload(self, payload: str | bytes | None = None) -> list[int]:
        if payload is None:
            payload = self.payload
        if isinstance(payload, bytes):
            payload = payload.decode()
        step_size = self.n_bytes_encoding
        if self.format_base is None: # If the format base is None, it's a string
            return [ord(c) for c in payload]
        else:
            return [int(payload[i:i + step_size], self.format_base) for i in range(0, len(payload), step_size)]

    def __get_format_base(self):
        """
        Get the base for the format string.
        e.g. '{:02x}' -> 16. Default to decimal
        """
        return self.get_format_base(self.format)

    @staticmethod
    def get_format_base(format_str: str) -> int | None:
        """
        Get the base for the format string.
        e.g. '{:02x}' -> 16. Default to decimal
        """
        if format_str == '{}':
            return None
        format_char = format_str[-2]
        return BeatBoxMessage.format_bases.get(format_char, 10)

    def __get_n_bytes_encoding(self):
        """
        Get the number of bytes used to encode the data (payload, sequence, and CRC).
        e.g. '{:02x}' -> 2.
        """
        if self.format_base is None:
            return None
        else:
            return int(self.format[-3])

    def _parse_message(self) -> tuple[str, int | None, str, int | None]:
        command, message_body = self.message.split(':', maxsplit=1)

        if not message_body:
            return command, None, '', None

        format_char = message_body[0]
        self.format = self.parse_format()
        self.format_base = self.__get_format_base()
        self.n_bytes_encoding = self.__get_n_bytes_encoding()
        message_body = message_body[1:]
        sequence = self.extract_seq_nb(message_body)
        payload = message_body[BeatBoxMessage.n_bytes_seq:]
        if format_char == 'r' or command == 'error':
            return command, sequence, payload, None
        else:
            payload = payload[:-BeatBoxMessage.n_bytes_crc]
        crc = self.extract_crc()
        return command, sequence, payload, crc

    def extract_seq_nb(self, message_body):
        """
        Extract the sequence number from the message body.
        The format is specified by BeatBoxMessage.sequence_format and is thus independent of the format
        used to encode the payload.
        
        :param str | bytes message_body: The message body to extract the sequence number from.
        """
        seq_format_size_bytes = BeatBoxMessage.get_format_base(BeatBoxMessage.sequence_format)  # FIXME: cache
        return self.decode_int(message_body[:BeatBoxMessage.n_bytes_seq], seq_format_size_bytes)  # TODO: implement proper fix instead of hardcoding 2

    def __extract_crc_bytes(self):
        n_bytes_crc_encoding = BeatBoxMessage.n_bytes_crc
        return self.message[-n_bytes_crc_encoding:]

    @property
    def message_without_crc(self):
        n_bytes_crc_encoding = BeatBoxMessage.n_bytes_crc
        return self.message[:-n_bytes_crc_encoding]

    def extract_crc(self):
        crc_format_size_bytes = BeatBoxMessage.get_format_base(BeatBoxMessage.crc_format)
        return self.decode_int(self.__extract_crc_bytes(), crc_format_size_bytes)

    def compute_crc(self):
        return crc8(self.message_without_crc.encode())

    def is_valid(self) -> bool:
        msg = self.message
        if not msg:
            return False
        # if not msg.endswith('\n'):
        #     return False
        if ':' not in msg:
            return False

        command, message_body = self.message.split(':', maxsplit=1)
        format_char = message_body[0]
        message_body = message_body[1:]
        if command in self.handshake_commands or format_char == 'r':  # Those don't have a seq and CRC
            return True
        else:
            seq = self.extract_seq_nb(message_body)
            if not seq == self.last_sequence + 1:  # FIXME: increment sequence number
                return True
                # return False  # FIXME: reinstate this check
            computed_crc = self.compute_crc()  # As computed from the message
            expected_crc = self.extract_crc()  # As read from the message
            return expected_crc == computed_crc

    def get_error(self) -> str:
        if not self.message:
            return "Empty message."
        # if not self.message.endswith('\n'):
        #     return "Message does not end with newline."
        if not ':' in self.message:
            return "No command separator found."

        command, message_body = self.message.split(':', maxsplit=1)
        if command in self.handshake_commands:
            return 'Message OK.'
        format_char = message_body[0]
        message_body = message_body[1:]
        seq = self.extract_seq_nb(message_body)
        if not seq == self.last_sequence + 1:
            return f"Invalid sequence number: {seq}, expected {self.last_sequence + 1}."
        if self.extract_crc() != self.compute_crc():
            return f"Invalid CRC: {self.extract_crc()}, expected {self.compute_crc()}."
        return "Unknown error."

    def to_bytes(self) -> bytes:
        """
        Return the wire-ready byte sequence:
          • add a newline if it is missing;
          • if the message *needs* a CRC (format not 'r') and none is present,
            calculate and append it.
        """
        format_char = self.__format_to_format_char(self.format)
        skip_crc = format_char == 'r' or self.command == 'error' or self.command in BeatBoxMessage.handshake_commands
        if self.crc is None and not skip_crc:
            crc_val = crc8(self.message.encode())
            self.crc = crc_val  # keep object consistent for a second call to to_bytes()
            self.message = f"{self.message}{self.crc_format.format(crc_val)}"

        full = f"{self.message}\n" if not self.message.endswith('\n') else self.message
        return full.encode()

    def __str__(self):
        msg = self.message
        if not msg.endswith('\n'):
            msg += '\n'
        return msg


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
