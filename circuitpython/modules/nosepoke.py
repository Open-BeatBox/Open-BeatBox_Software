import asyncio
import board
import countio
import digitalio
import time

from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN

is_init = False
nose_poke = False
time_ref = time.monotonic_ns()

class PhotoInterrupter:
    def __init__(self, in_pin, out_pin):
        self.sensor_in = digitalio.DigitalInOut(in_pin)
        self.sensor_in.direction = digitalio.Direction.OUTPUT
        self.sensor_in.value = True
        self.sensor_out = countio.Counter(out_pin, edge=countio.Edge.FALL)

    async def catch_interrupt(self):
        """Print a message when pin goes low."""
        global nose_poke
        with self.sensor_out as interrupt:
            while True:
                print(f"In catch interrupt #{interrupt.count}")
                if interrupt.count > 0:
                    interrupt.count = 0
                    nose_poke = True
                # Let another task run.
                await asyncio.sleep(1)

def can_init(can_cs):
    cs = digitalio.DigitalInOut(can_cs)
    cs.switch_to_output()
    spi = board.SPI()
    can_bus = CAN(
        spi, cs, silent=False
    )
    return can_bus

def init():
    # Status led
    global is_init
    led = digitalio.DigitalInOut(board.D25)
    led.direction = digitalio.Direction.OUTPUT

    can_bus = can_init(board.CAN_CS)

    led.value = True
    is_init = True
    sensor = PhotoInterrupter(in_pin=board.D10, out_pin=board.D9)
    return led, can_bus, sensor

async def can_rx(can_bus):
    global is_init
    if not is_init :
        return
    last_time = 0
    while True:
        time_now = time.monotonic_ns()
        print(f"CAN RX {(time_now - last_time) / 1_000_000} ms since last exec")
        last_time = time_now
        await asyncio.sleep(1)


async def can_tx(can_bus):
    global is_init
    if not is_init :
        return
    last_time = 0
    while True:
        time_now = time.monotonic_ns()
        print(f"CAN TX {(time_now - last_time)/1_000_000} ms since last exec")
        last_time = time_now
        await asyncio.sleep(1)

async def led_manager(led):
    global nose_poke
    if nose_poke:
        print("Nose Poke")
        nose_poke = False
        led.value = True
    else:
        led.value = False
    await asyncio.sleep(0.1)

async def main():
    global time_ref
    time_ref = time.monotonic_ns()
    led, can_bus, sensor = init()
    tasks = [
        asyncio.create_task(can_rx(can_bus)),
        asyncio.create_task(can_tx(can_bus)),
        asyncio.create_task(sensor.catch_interrupt()),
        asyncio.create_task(led_manager(led))
    ]
    await asyncio.gather(*tasks)

# Start tasks
print("Let's go!")
asyncio.run(main())