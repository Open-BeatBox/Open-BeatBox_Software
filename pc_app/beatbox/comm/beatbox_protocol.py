"""BeatBox serial message encoding and decoding.

Compatible with both CPython (3.9+) and MicroPython.
No dependencies beyond the standard library.

Message format::

    <command>:<data type char><sequence><payload><crc8>\\n

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

try:
    from beatbox_protocol.crc import crc8
except ImportError:
    # MicroPython: package might be frozen or in a flat layout
    from crc import crc8


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

    .. NOTE::
        This class is intentionally kept free of type annotations using
        Python 3.10+ syntax (e.g. str | None, list[int]) so it can run
        on MicroPython. Use older-style annotations in docstrings only.
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

    def __init__(self, message: str, fmt = '{:02x}'):
        """
        Parameters
        ----------
        message : str
            Raw message string (without trailing newline).
        fmt : str or None
            Format string (e.g. '{:02x}'). If None, auto-detected from message.
            Default = '{:02x}'
        """
        self.message = message
        if fmt is None:
            fmt = self.parse_format()
        self.format = fmt
        self.format_base = self.__get_format_base()
        self.n_bytes_encoding = self.__get_n_bytes_encoding()
        self.command, self.sequence, self.payload, self.crc = self._parse_message()

    @staticmethod
    def from_bytes(data: bytes, fmt = None):
        """Create from a bytes object (decodes to str first)."""
        return BeatBoxMessage(data.decode(), fmt)

    @staticmethod
    def from_parts(command: str, sequence: int, payload='', fmt='{:02x}') -> 'BeatBoxMessage':
        """Build a message from its components.

        Parameters
        ----------
        command : str
            Command name (e.g. 'set', 'ready'). Trailing ':' is stripped.
        sequence : int
            Sequence number (0-255).
        payload : str or list of int
            Payload data. If a list, each int is encoded using *fmt*.
        fmt : str
            Encoding format string.

        Returns
        -------
        BeatBoxMessage
        """
        if command.endswith(':'):
            command = command[:-1]
        if isinstance(payload, list):
            payload = ''.join(BeatBoxMessage.encode_int(x, fmt) for x in payload)
        format_char = BeatBoxMessage.__format_to_format_char(fmt)
        sequence_chars = BeatBoxMessage.sequence_format.format(sequence)
        msg_bytes = f'{command}:{format_char}{sequence_chars}{payload}'

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
            raise ValueError('Invalid message format: no payload found.')
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

    def decode_payload(self, payload=None):
        """
        Decode the payload into a list of integers.

        Parameters
        ----------
        payload : str or bytes or None
            If None, uses self.payload.

        Returns
        -------
        list of int
        """
        if payload is None:
            payload = self.payload
        if isinstance(payload, bytes):
            payload = payload.decode()
        step_size = self.n_bytes_encoding
        if self.format_base is None:  # If the format base is None, it's a string
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
    def get_format_base(format_str: str):
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

    def _parse_message(self):
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

        Parameters
        ----------
        message_body : str or bytes
            The message body to extract the sequence number from.
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
        """Compute CRC-8 over the message (excluding the CRC field)."""
        return crc8(self.message_without_crc.encode())

    # ─── Validation ───────────────────────────────────────────

    def is_valid(self) -> bool:
        """Check whether the message is structurally valid and CRC matches."""
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
        """Return a human-readable error description, or 'Message OK.'"""
        if not self.message:
            return 'Empty message.'
        # if not self.message.endswith('\n'):
        #     return "Message does not end with newline."
        if ':' not in self.message:
            return 'No command separator found.'

        command, message_body = self.message.split(':', maxsplit=1)
        if command in self.handshake_commands:
            return 'Message OK.'
        format_char = message_body[0]
        message_body = message_body[1:]
        seq = self.extract_seq_nb(message_body)
        if not seq == self.last_sequence + 1:
            return f'Invalid sequence number: {seq}, expected {self.last_sequence + 1}.'
        if self.extract_crc() != self.compute_crc():
            return f'Invalid CRC: {self.extract_crc()}, expected {self.compute_crc()}.'
        return 'Unknown error.'

    # ─── Serialisation ────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """
        Return the wire-ready byte sequence:
          - add a newline if it is missing;
          - if the message *needs* a CRC (format not 'r') and none is present,
            calculate and append it.
        """
        format_char = self.__format_to_format_char(self.format)
        skip_crc = format_char == 'r' or self.command == 'error' or self.command in BeatBoxMessage.handshake_commands
        if self.crc is None and not skip_crc:
            crc_val = crc8(self.message.encode())
            self.crc = crc_val  # keep object consistent for a second call to to_bytes()
            self.message = f'{self.message}{self.crc_format.format(crc_val)}'

        full = f'{self.message}\n' if not self.message.endswith('\n') else self.message
        return full.encode()

    def __str__(self):
        msg = self.message
        if not msg.endswith('\n'):
            msg += '\n'
        return msg

    def __repr__(self):
        return f'BeatBoxMessage({self.message!r})'