import machine
from bb_pin_map import PinMap

class Led:
    def __init__(self, pin_num=PinMap.USER_LED):
        self.led = machine.Pin(pin_num, machine.Pin.OUT)

    def on(self):
        self.led.high()

    def off(self):
        self.led.low()

    def toggle(self):
        self.led.value(not self.led.value())