from enum import Enum
from typing import List


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


def pattern_to_mask(pattern: PATTERN_ID) -> List[int]:  # FIXME: dead code
    """
    Convert a PATTERN_ID to the corresponding [hi, lo] mask for BeatBox 'set'.

    """
    mask = []
