import time

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

import board
import digitalio
import supervisor

cs = digitalio.DigitalInOut(board.CAN_CS)
cs.switch_to_output()
spi = board.SPI()

led = digitalio.DigitalInOut(board.D25)
led.direction = digitalio.Direction.OUTPUT


can_bus = CAN(
    spi, cs, loopback=False, silent=False
)  # use loopback to test without another device


while True:
    start = time.monotonic_ns()
    led.value = True
    time.sleep(0.0005)
    led.value = False
    duration = time.monotonic_ns() - start
    # Print on USB Serial, 115200 8 N 1 by default
    print(f"Led ON {duration}")
    time.sleep(0.0005)
    message = Message(id=0x1234ABCD, data=b"Hello Worlds", extended=True)
    send_success = can_bus.send(message)
    print("Send success:", send_success)
    time.sleep(1)
