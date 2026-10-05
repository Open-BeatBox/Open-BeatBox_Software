from time import sleep
from canio import Message, RemoteTransmissionRequest
from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI


sck = Pin(14)
mosi = Pin(15)
miso = Pin(8)
cs = 19 # Pin initialization is made in MCP2515 class

spi_1 = SPI(1, baudrate=1_000_000, polarity=0, phase=0, sck=sck, mosi=mosi, miso=miso)

canBus = CAN(1, cs, spi_bus=spi_1, loopback=True, silent=True, debug=False)  # use loopback to test without another device

i = 0
while True:
    with canBus.listen(timeout=1.0) as listener:

        message = Message(id=0x1234ABCD, data=b"data" + bytes([i]), extended=True)
        sendResult = canBus.send(message)
        print("Send success:", sendResult)
        
        
        message_count = listener.in_waiting()
        print(message_count, "messages available")
        for _i in range(message_count):
            msg = listener.receive()
            print("Message from ", hex(msg.id))
            if isinstance(msg, Message):
                print("message data:", msg.data)
            if isinstance(msg, RemoteTransmissionRequest):
                print("RTR length:", msg.length)
    sleep(1)
    i = i + 1
