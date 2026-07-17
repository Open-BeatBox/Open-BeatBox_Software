from typing import Union


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
