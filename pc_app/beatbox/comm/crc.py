"""CRC-8 calculation — compatible with CPython and MicroPython."""


def crc8(data: bytes, polynomial: int = 0x07, init: int = 0) -> int:
    """Calculate CRC-8.

    Parameters
    ----------
    data : bytes or str
        Input data. Must be bytes (preferably) or str.
    polynomial : int
        CRC polynomial (default 0x07).
    init : int
        Initial CRC value (default 0).

    Returns
    -------
    int
        CRC-8 checksum (0–255).
    """
    if isinstance(data, str):
        data = data.encode()
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ polynomial) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc
