import board
import digitalio
from adafruit_mcp2515.canio import Message, RemoteTransmissionRequest
from adafruit_mcp2515 import MCP2515 as CAN
import time

cs = digitalio.DigitalInOut(board.CAN_CS)
cs.switch_to_output()
spi = board.SPI()

led = digitalio.DigitalInOut(board.D25)
led.direction = digitalio.Direction.OUTPUT

sensorIn = digitalio.DigitalInOut(board.D10)
sensorIn.direction = digitalio.Direction.OUTPUT

sensorOut = digitalio.DigitalInOut(board.D9)
sensorOut.direction = digitalio.Direction.INPUT

sensorIn.value = True
led.value = True

can_bus = CAN(
    spi, cs, loopback=False, silent=False
)  # use loopback to test without another device

while True:
    with can_bus.listen(timeout=1.0) as listener:
        message_count = listener.in_waiting()
        print(message_count, "messages available")
        for _i in range(message_count):
            msg = listener.receive()
            print("Message from ", hex(msg.id))
            if isinstance(msg, Message):
                print("message data:", msg.data.decode())
            if isinstance(msg, RemoteTransmissionRequest):
                print("RTR length:", msg.length)
    time.sleep(1)
