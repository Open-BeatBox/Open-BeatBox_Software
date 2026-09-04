# Screen module – drawing functions ported from screen_tasks.ino
# Uses the 8-bit parallel ILI9341 driver (libs/ILI9341-parallel-TFT-driver-for-micropython/ILI9341.py)

import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI

from beatbox_module import ModuleManager, ModuleMap
from can_protocol import MessageType, Priority, ScreenCommand, parse_identifier
from bb_pin_map import ScreenPins as pinMap

from random import getrandbits
from time import sleep_ms, ticks_ms, ticks_diff
import ILI9341

try:
    import micropython
except ImportError:
    # Host-side fallback so this module can still be imported outside MicroPython.
    class _MicroPythonFallback:
        @staticmethod
        def viper(func):
            return func

    micropython = _MicroPythonFallback()
    ptr8 = int

# ── Display init (parallel 8-bit) ─────────────────────────────────────────────
tft = ILI9341.screen(
    pinMap.LCD_RD, pinMap.LCD_WR, pinMap.LCD_RS, pinMap.LCD_CS, pinMap.LCD_RST,
    pinMap.LCD_D0, pinMap.LCD_D1, pinMap.LCD_D2, pinMap.LCD_D3,
    pinMap.LCD_D4, pinMap.LCD_D5, pinMap.LCD_D6, pinMap.LCD_D7,
)

# ── Screen dimensions ─────────────────────────────────────────────────────────
# Adjust if rotation changes the logical width/height (tft.setrotation swaps them).
WIDTH  = 240
HEIGHT = 320

# ── Color constants (16-bit RGB565) ───────────────────────────────────────────
BLACK   = 0x0000
BLUE    = 0x001F
RED     = 0xF800
GREEN   = 0x07E0
CYAN    = 0x07FF
MAGENTA = 0xF81F
YELLOW  = 0xFFE0
WHITE   = 0xFFFF
GRAY    = 0x8410

# ── Diagonal pattern constants ────────────────────────────────────────────────
# 8 px white stripe, 8 px black stripe → 45° diagonals when shift_per_row = 1
_DIAGONAL_PATTERN = bytes([0xFF, 0x00])
_PATTERN_LENGTH   = 16

# Direction constants (mirror of the C++ DiagonalDirection enum)
TRBL = 0   # Top-right → bottom-left
TLBR = 1   # Top-left  → bottom-right

_WHITE_HI = (WHITE >> 8) & 0xFF
_WHITE_LO = WHITE & 0xFF
_BLACK_HI = (BLACK >> 8) & 0xFF
_BLACK_LO = BLACK & 0xFF


@micropython.viper
def _build_diagonal_row_viper(
    row_buf: ptr8,
    width: int,
    pattern_pos: int,
    pattern_len: int,
    direction: int,
    is_white: ptr8,
    white_hi: int,
    white_lo: int,
    black_hi: int,
    black_lo: int,
):
    """Fill one RGB565 row buffer using viper integer loops."""
    x = 0
    idx = 0
    while x < width:
        # Keep this as a literal inside viper code (global lookups are objects).
        if direction == 0:
            px = (x + pattern_pos) % pattern_len
        else:
            px = (x - pattern_pos + pattern_len) % pattern_len

        if is_white[px]:
            row_buf[idx] = white_hi
            row_buf[idx + 1] = white_lo
        else:
            row_buf[idx] = black_hi
            row_buf[idx + 1] = black_lo

        x += 1
        idx += 2


def _write_row_rgb565(y: int, row_buf: bytearray) -> None:
    """Write one full RGB565 row in a single address window transaction."""
    tft.setAddrWindow(0, y, WIDTH - 1, y)
    tft.CS_ACTIVE()
    tft.WriteCmd(tft._MW)
    for b in row_buf:
        tft.write8(b)
    tft.CS_IDLE()


# ── Functions ported from screen_tasks.ino ────────────────────────────────────

def draw_vlines(thick: int = 1,
                num_lines: int = 10,
                edge_margin: int = 10) -> None:
    """Draw evenly-spaced white vertical lines on a black background.
    """
    tft.fillRect(0, 0, WIDTH, HEIGHT, BLACK)

    available_width = WIDTH - 2 * edge_margin
    spacing = available_width // (num_lines - 1)

    for i in range(num_lines):
        center = edge_margin + i * spacing
        for t in range(thick + 1):
            tft.drawFastVLine(center + t, 0, HEIGHT, WHITE)
            tft.drawFastVLine(center - t, 0, HEIGHT, WHITE)


def draw_hlines(thick: int = 1,
                num_lines: int = 15,
                edge_margin: int = 10) -> None:
    """Draw evenly-spaced white horizontal lines on a black background.
    """
    tft.fillRect(0, 0, WIDTH, HEIGHT, BLACK)

    available_height = HEIGHT - 2 * edge_margin
    spacing = available_height // (num_lines - 1)

    for i in range(num_lines):
        center = edge_margin + i * spacing
        for t in range(thick + 1):
            tft.drawFastHLine(0, center + t, WIDTH, WHITE)
            tft.drawFastHLine(0, center - t, WIDTH, WHITE)


def draw_grid(thick: int = 1,
              num_vlines: int = 10,
              num_hlines: int = 15,
              edge_margin: int = 10) -> None:
    """Draw a white grid on a black background.

    Combines vertical and horizontal lines in a single screen clear.
    """
    tft.fillRect(0, 0, WIDTH, HEIGHT, BLACK)

    # Vertical lines
    av_w = WIDTH - 2 * edge_margin
    v_spacing = av_w // (num_vlines - 1)
    for i in range(num_vlines):
        center = edge_margin + i * v_spacing
        for t in range(thick + 1):
            tft.drawFastVLine(center + t, 0, HEIGHT, WHITE)
            tft.drawFastVLine(center - t, 0, HEIGHT, WHITE)

    # Horizontal lines
    av_h = HEIGHT - 2 * edge_margin
    h_spacing = av_h // (num_hlines - 1)
    for i in range(num_hlines):
        center = edge_margin + i * h_spacing
        for t in range(thick + 1):
            tft.drawFastHLine(0, center + t, WIDTH, WHITE)
            tft.drawFastHLine(0, center - t, WIDTH, WHITE)


def draw_diagonal_pattern(direction: int = TRBL,
                           pattern: bytes = _DIAGONAL_PATTERN,
                           pattern_length: int = _PATTERN_LENGTH,
                           shift_per_row: int = 1) -> None:
    """Draw a diagonal stripe pattern across the full WIDTH×HEIGHT screen.

    Implemented performance improvements:
      1) The per-pixel row generation loop now runs in a @micropython.viper
         function (`_build_diagonal_row_viper`).
      2) Each line is pre-built as an RGB565 bytearray and pushed with a single
         address-window transaction (`_write_row_rgb565`).

    Remaining options to explore for improved performance:
      3) Move the row write itself to viper/native code (avoid Python `for b`).
      4) Use the RP2040 PIO path (`ILI9341_RP2_PIO`) to stream writes via DMA.

    Args:
        direction:      TRBL or TLBR – controls which way the stripe leans.
        pattern:        Bytes where each bit (MSB first) = 1 means white pixel.
        pattern_length: Total number of bits in the repeating pattern.
        shift_per_row:  Pixels the pattern shifts horizontally per row.
    """
    tft.fillscreen(BLACK)

    # Decode bit pattern once into 0/1 bytes for viper-friendly lookup.
    is_white = bytearray(pattern_length)
    for i in range(pattern_length):
        byte_val = pattern[i // 8]
        bit = 7 - (i % 8)   # MSB first – matches the Arduino original
        is_white[i] = 1 if (byte_val & (1 << bit)) else 0

    row_buf = bytearray(WIDTH * 2)
    pattern_pos = 0
    for y in range(HEIGHT):
        _build_diagonal_row_viper(
            row_buf,
            WIDTH,
            pattern_pos,
            pattern_length,
            direction,
            is_white,
            _WHITE_HI,
            _WHITE_LO,
            _BLACK_HI,
            _BLACK_LO,
        )
        _write_row_rgb565(y, row_buf)

        pattern_pos = (pattern_pos + shift_per_row) % pattern_length


def draw_diagonal_lines_trbl(shift_per_row: int = 1) -> None:
    """Diagonal stripes leaning top-right → bottom-left.
    """
    draw_diagonal_pattern(TRBL, shift_per_row=shift_per_row)


def draw_diagonal_lines_tlbr(shift_per_row: int = 1) -> None:
    """Diagonal stripes leaning top-left → bottom-right.
    """
    draw_diagonal_pattern(TLBR, shift_per_row=shift_per_row)

class ScreenModule(ModuleManager):
    def __init__(self, module_name="SCREEN_LEFT"):
        super().__init__(module_name=module_name)
        # Pass the callback to send CAN message immediately
        self._init_ir_beam()
        self.tasks.append(asyncio.create_task(self.reset_led()))

    def _init_ir_beam(self):
        self.sensor = Pin(pinMap.IR_BEAM, Pin.IN)
        self.sensor.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        self.led.on()
        self._send_frame(
            priority=Priority.EVENT,
            msg_type=MessageType.RESPONSE_MODULE,
            command=ScreenCommand.TOUCH_EVENT,
            payload=bytearray([1]),
        )

    def command_handler(self, msg):
        super().command_handler(msg)

        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE:
            return
        if target_module_id != self.id and target_module_id != ModuleMap.BROADCAST_ID:
            return
        if command == ScreenCommand.DISPLAY_PATTERN:
            pattern_id = msg.data[0] if msg.data else 0
            patterns = (
                draw_vlines,
                draw_hlines,
                draw_grid,
                draw_diagonal_lines_trbl,
                draw_diagonal_lines_tlbr,
            )
            patterns[pattern_id % len(patterns)]()
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_MODULE,
                command=ScreenCommand.DISPLAY_PATTERN,
                payload=bytearray([pattern_id]),
            )

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.off()
            await asyncio.sleep(0.1)

def main():
    screen = ScreenModule(module_name="SCREEN_LEFT")
    tft.begin()
    # Visual validation: cycle through all ported patterns
    draw_vlines()
    draw_hlines()
    draw_grid()
    draw_diagonal_lines_trbl()
    draw_diagonal_lines_tlbr()
    asyncio.run(screen.start_managers())


