import time

import board
import digitalio
import supervisor

pin_name = board.L

led = digitalio.DigitalInOut(pin_name)
led.direction = digitalio.Direction.OUTPUT

while True:
    start = supervisor.ticks_ms()
    led.value = True
    time.sleep(0.0005)
    led.value = False
    duration = supervisor.ticks_ms() - start
    # Print on USB Serial, 115200 8 N 1 by default
    print(f"Led ON {duration}")
    time.sleep(0.0005)
