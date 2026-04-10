import logging
from pathlib import Path
from typing import Union, Dict, List
from enum import Enum

from configobj import ConfigObj

class PATTERN_ID(Enum):
    FULL_BLACK = 0
    BLINK = 0x1
    H_LINES = 0x2
    V_LINES = 0x4
    GRID = 0x6
    DIAG_TRBL = 0x8
    DIAG_TLBR = 0x9
    FULL_WHITE = 0xF
    UNDEFINED = 0xFF

PATTERN_LUT = {
    'b': PATTERN_ID.FULL_BLACK,
    'blink': PATTERN_ID.BLINK,
    'h': PATTERN_ID.H_LINES,
    'v': PATTERN_ID.V_LINES,
    'g': PATTERN_ID.GRID,
    'trbl': PATTERN_ID.DIAG_TRBL,
    'tlbr': PATTERN_ID.DIAG_TLBR,
    'w': PATTERN_ID.FULL_WHITE,
}

def bitmask_from_pattern(pattern : PATTERN_ID) -> List[int]:
    """Convert a pattern ID (int) to an array of booleans with 4 elements,
    representing the 4 bits of the pattern. LSB first."""
    if not isinstance(pattern, PATTERN_ID):
        raise ValueError(f"Expected a pattern ID, got {type(pattern)}")
    return [(pattern.value >> i) & 1 for i in range(0, 4)]


def _get_pin_names_from_pattern(bitarray : List[int], side: str) -> List[str]:
    """Given a bitarray of 4 bits and a side ('left' or 'right'), return the corresponding pin names."""
    if side not in ['left', 'right']:
        raise ValueError(f"Side must be 'left' or 'right', got {side}")
    if len(bitarray) != 4:
        raise ValueError(f"Expected a bitarray of length 4, got {len(bitarray)}")
    pin_names = []
    for i, bit in enumerate(bitarray):
        if bit:
            pin_names.append(f"{side}_pattern_bit_{i}")
    return pin_names

def get_pin_names_from_pattern(pattern : PATTERN_ID, side: str) -> List[str]:
    """Convert a pattern to a bitmask and get pin names for a side.
    Side can be either 'left' or 'right' or  'both'.
    """
    bitmask = bitmask_from_pattern(pattern)
    if side == 'both':
        right_names = _get_pin_names_from_pattern(bitmask, 'right')
        left_names = _get_pin_names_from_pattern(bitmask, 'left')
        ret = [*left_names, *right_names]
    else:
        ret = _get_pin_names_from_pattern(bitmask, side)
    return ret

# FIXME: check whether we always supply the message without the CRC
def crc8(data: Union[str, bytes], polynomial: int = 0x07, init: int = 0) -> int:
    """Calculate CRC-8 using polynomial 0x07."""
    crc = init
    for byte in data:  # FIXME: what if string?
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ polynomial) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

class PinConfig:
    """
    Load pins_config.cfg and expose

    * pin.bit_idx(name)  –>  bit index (0..15) used in the 16-bit BeatBox mask
    * pin.num(name)  –>  physical Arduino pin number (22, 23 …)
    * pin.mask(names…) –> [hi, lo] bytes ready for BeatBox 'set'

    Bit positions are assigned by the **order** of the keys inside the
    [self.pin_type] section, *not* by the numeric value written in the file.
    """

    def __init__(self, cfg_path: str | Path = "pins_config.cfg", pin_type: str = 'actuators') -> None:
        """
        Load the pin configuration from the given INI file.

        Parameters
        ----------
        cfg_path : str or Path
            Path to the configuration file. It will be expanded using `Path.expanduser()`.
        pin_type : str
            Type of pin configuration to load. One of 'sensors' or 'actuators'. (default: 'actuators')
        """
        self.cfg_path = Path(cfg_path).expanduser()
        if not self.cfg_path.exists():
            self.cfg_path = Path(__file__).parent / 'pins_config.cfg'
        self.pin_type = pin_type
        self.pin_names = []  # list of pin names in the order they are defined in the INI file
        self._bit_index: [str, int] = {}   # name → 0..15
        self._pin_number: Dict[str, int] = {}  # name → pin value (22..)
        self._current_states: Dict[str, int] = {}  # name → current state (0 or 1)
        self._default_states: Dict[str, int] = {}  # name → default state (0 or 1)
        self._load()

    def info(self) -> str:
        """Return a string with the pin configuration info."""
        return f"PinConfig: {self.pin_type} pins loaded from {self.cfg_path}\n" + \
               "\n".join(f"{name}: bit {self._bit_index[name]}, pin {self._pin_number[name]}"
                         for name in self.pin_names)

    def default_word(self) -> int:
        """Return the default 16-bit word as defined in the config file."""
        word = 0
        for name, state in self._default_states.items():
            if state:
                word |= (1 << self._bit_index[name])
        return word

    def bit_idx(self, name: str) -> int:
        """Return the bit position (0–15) for a given *output* name."""
        return self._bit_index[name]

    def num(self, name: str) -> int:
        """Return the physical Arduino pin number (int) for that output."""
        return self._pin_number[name]

    def default_mask(self) -> List[int]:
        """Return the default mask as [hi, lo] bytes."""
        return self.mask_from_word(self.default_word())

    def mask_from_word(self, word: int) -> List[int]:
        """Return the [hi, lo] bytes for a given 16-bit word."""
        return [(word >> 8) & 0xFF, word & 0xFF]

    def mask(self, *names: str) -> List[int]:
        """
        Return a two-element list [hi, lo] suitable for BeatBox 'set'.

        Examples
        --------
        >>> pin.mask('left_pattern_1', 'reward')
        [0x80, 0x01]   # assuming 'reward' is the first entry, left_pattern_1 is bit 15
        """
        bitmask = 0
        for n in names:
            bitmask |= 1 << self.bit_idx(n)
        return [bitmask >> 8, bitmask & 0xFF]

    def _load(self) -> None:
        """Read the INI file and fill _bit_index / _pin_number dictionaries."""
        if not self.cfg_path.exists():
            raise FileNotFoundError(self.cfg_path)

        cfg = ConfigObj(str(self.cfg_path), encoding="utf-8", unrepr=True, indent_type='    ')

        # We rely on dict preserving insertion order (true since 3.2)
        for idx, (name, pin_value) in enumerate(cfg[self.pin_type].items()):
            default = False
            if isinstance(pin_value, dict):
                pin_nb = int(pin_value.get('pin'))
                default = bool(pin_value.get('default', False))
            else:
                pin_nb = int(pin_value)

            self.pin_names.append(name)
            self._bit_index[name]  = idx                # 0..15
            self._pin_number[name] = pin_nb             # e.g. 22, 23 …
            self._current_states[name] = default  # Start with default states
            self._default_states[name] = default


def load_pin_config(pin_type: str) -> PinConfig:
    """
    Load the pin configuration from the default or specified path.
    """
    cfg_path = Path(__file__).parent / 'pins_config.cfg'
    config = PinConfig(cfg_path, pin_type=pin_type)
    logging.debug(config.info())
    return config

def pattern_to_mask(pattern: PATTERN_ID) -> List[int]:
    """
    Convert a PATTERN_ID to the corresponding [hi, lo] mask for BeatBox 'set'.

    """
    mask = []


