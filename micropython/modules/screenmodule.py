# Screen module – drawing functions ported from screen_tasks.ino
# Uses the 8-bit parallel ILI9341 driver (libs/ILI9341-parallel-TFT-driver-for-micropython/ILI9341.py)

import asyncio
import time

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI, Timer

from beatbox_module import ModuleManager, ModuleMap
from can_protocol import MessageType, Priority, ScreenCommand, parse_identifier
from bb_pin_map import ScreenPins as pinMap

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

# D0..D7 verified contiguous from GPIO0 on this board.
_D0_BASE  = pinMap.LCD_D0
_BUS_MASK = 0xFF << _D0_BASE
_WR_MASK  = 1 << pinMap.LCD_WR

@micropython.viper
def _blit_sio(buf: ptr8, n: int, bus_mask: int, d0: int, wr: int):
    """4 stores/byte; skips the bus update when the byte is unchanged.
    Measured 0.701 us/byte -> 336 us per 480-byte row."""
    setr = ptr32(0xd0000014)      # SIO GPIO_OUT_SET
    clrr = ptr32(0xd0000018)      # SIO GPIO_OUT_CLR
    last = -1
    i = 0
    while i < n:
        b = int(buf[i])
        if b != last:
            clrr[0] = bus_mask
            setr[0] = b << d0
            last = b
        clrr[0] = wr
        setr[0] = wr
        i += 1


@micropython.viper
def _build_diag_row(row_buf: ptr8, width: int, v0: int, step: int,
                    thick: int, inc: int,
                    fg_hi: int, fg_lo: int, bg_hi: int, bg_lo: int):
    """Scanline fill of v = x*H + y*W; stripes are v mod step."""
    v = v0
    x = 0
    idx = 0
    while x < width:
        if v < thick:
            row_buf[idx] = fg_hi
            row_buf[idx + 1] = fg_lo
        else:
            row_buf[idx] = bg_hi
            row_buf[idx + 1] = bg_lo
        v += inc
        if v >= step:
            v -= step
        x += 1
        idx += 2


def _write_row_sio(y: int, row_buf: bytearray) -> None:
    tft.setAddrWindow(0, y, WIDTH - 1, y)
    tft.CS_ACTIVE()
    tft.WriteCmd(tft._MW)
    tft.write8(row_buf[0])          # puts RS in data state via the driver
    _blit_sio(row_buf, WIDTH * 2, _BUS_MASK, _D0_BASE, _WR_MASK)
    tft.CS_IDLE()                   # note: re-sends pixel 0 hi byte (1-byte error)


def draw_diag_sio(stripes: int = 21, thick_px: int = 5, direction: int = TRBL,
                  fg: int = WHITE, bg: int = BLACK) -> None:
    """Diagonal stripes at the screen's corner-to-corner slope. ~803 ms.

    stripes:  number of stripes across the diagonal; must be < 2*WIDTH
              so that step > HEIGHT (single-subtraction reduction).
    thick_px: horizontal run of a stripe. Perpendicular width is
              thick_px * W/sqrt(W^2+H^2) ~= 0.6*thick_px at 240x320,
              so 3 gives a ~2 px stripe.
    """
    step  = (2 * WIDTH * HEIGHT) // stripes
    thick = thick_px * HEIGHT
    fg_hi, fg_lo = (fg >> 8) & 0xFF, fg & 0xFF
    bg_hi, bg_lo = (bg >> 8) & 0xFF, bg & 0xFF

    row_buf = bytearray(WIDTH * 2)
    for y in range(HEIGHT):
        v0 = (y * WIDTH) % step if direction == TRBL else (-y * WIDTH) % step
        _build_diag_row(row_buf, WIDTH, v0, step, thick, HEIGHT,
                        fg_hi, fg_lo, bg_hi, bg_lo)
        _write_row_sio(y, row_buf)


def draw_diagonal_lines_trbl(shift_per_row: int = 1) -> None:
    draw_diag_sio(direction=TRBL)


def draw_diagonal_lines_tlbr(shift_per_row: int = 1) -> None:
    draw_diag_sio(direction=TLBR)

# ── Functions ported from screen_tasks.ino ────────────────────────────────────

def draw_white():
    tft.fillscreen(WHITE)

def draw_black():
    tft.fillscreen(BLACK)

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


############################ SCREEN MODULE CLASS ############################

TOUCH_DEBOUNCE_PERIOD_MS = 50  # debounce period in milliseconds

class ScreenModule(ModuleManager):
    PAT_BLACK = 0
    PAT_WHITE = 1
    PAT_BLINK = 2
    PAT_VERTICAL = 3
    PAT_HORIZONTAL = 4
    PAT_GRID = 5
    PAT_TRBL = 6
    PAT_TLBR = 7

    def __init__(self, module_name="SCREEN_LEFT"):
        super().__init__(module_name=module_name)
        self.previous_pattern = None
        self.current_pattern = self.PAT_BLACK
        self.blink_period_ms = 200
        self._blink_on = False
        self._blink_next = time.ticks_ms()
        self.touch_pending = False
        self.touch_detected = False
        self.debounce_ms = TOUCH_DEBOUNCE_PERIOD_MS
        self._last_touch = time.ticks_ms()

        self.patterns = {
            self.PAT_BLACK: draw_black,
            self.PAT_WHITE: draw_white,
            self.PAT_BLINK: draw_black,          # first frame; task drives the rest
            self.PAT_VERTICAL: draw_vlines,
            self.PAT_HORIZONTAL: draw_hlines,
            self.PAT_GRID: draw_grid,
            self.PAT_TRBL: draw_diagonal_lines_trbl,
            self.PAT_TLBR: draw_diagonal_lines_tlbr,
        }

        self.tasks.append(asyncio.create_task(self.update_pattern()))
        self.tasks.append(asyncio.create_task(self.touch_reporter()))
        self._init_ir_beam()          # last: everything it needs now exists

    def _init_ir_beam(self):
        self.sensor = Pin(pinMap.IR_BEAM, Pin.IN)
        self.sensor.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_touch) < self.debounce_ms:
            return
        self._last_touch = now
        self.touch_pending = True

    async def touch_reporter(self):
        while True:
            if self.touch_pending:
                self.touch_pending = False
                self.led.on()
                self._send_frame(priority=Priority.EVENT,
                                 msg_type=MessageType.RESPONSE_MODULE,
                                 command=ScreenCommand.TOUCH_EVENT,
                                 payload=bytearray([1]))
                await asyncio.sleep(0.05)
                self.led.off()
            await asyncio.sleep(0.01)

    def command_handler(self, msg):
        super().command_handler(msg)
        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE:
            return
        if target_module_id != self.id and target_module_id != ModuleMap.BROADCAST_ID:
            return
        if command == ScreenCommand.DISPLAY_PATTERN:
            self.current_pattern = msg.data[0] if msg.data else 0

    async def update_pattern(self):
        while True:
            pattern = self.current_pattern          # snapshot, ISR-safe
            if pattern != self.previous_pattern:
                print("Updating pattern to", pattern)
                self.previous_pattern = pattern
                self._blink_on = False
                self._blink_next = time.ticks_ms()
                self.patterns[pattern]()
                self._send_frame(priority=Priority.STATUS,
                                 msg_type=MessageType.RESPONSE_MODULE,
                                 command=ScreenCommand.DISPLAY_PATTERN,
                                 payload=bytearray([pattern]))
            elif pattern == self.PAT_BLINK and \
                 time.ticks_diff(time.ticks_ms(), self._blink_next) >= 0:
                self._blink_next = time.ticks_ms() + self.blink_period_ms
                self._blink_on = not self._blink_on
                draw_white() if self._blink_on else draw_black()
            await asyncio.sleep(0.01)

def main():
    tft.begin()
    screen = ScreenModule(module_name="SCREEN_RIGHT")
    asyncio.run(screen.start_managers())

main()