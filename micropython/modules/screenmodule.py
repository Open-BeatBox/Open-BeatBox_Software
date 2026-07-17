import asyncio

from MCP2515 import MCP2515 as CAN
from machine import Pin, SPI

from beatbox_module import ModuleManager
from bb_pin_map import ScreenPins as pinMap

from random import getrandbits
from time import sleep_ms,ticks_ms, ticks_diff
import ILI9341

XP = pinMap.LCD_D0
YM = pinMap.LCD_D1
YP = pinMap.LCD_CS
XM = pinMap.LCD_RS

# Init the LCD screen
tft = ILI9341.screen(pinMap.LCD_RD, pinMap.LCD_WR, pinMap.LCD_RS, pinMap.LCD_CS, pinMap.LCD_RST, pinMap.LCD_D0,
                     pinMap.LCD_D1, pinMap.LCD_D2, pinMap.LCD_D3, pinMap.LCD_D4, pinMap.LCD_D5, pinMap.LCD_D6,
                     pinMap.LCD_D7)

#Assign human-readable names to some common 16-bit color values:
BLACK       =   0x0000
BLUE        =   0x001F
RED         =   0xF800
GREEN       =   0x07E0
CYAN        =   0x07FF
MAGENTA     =   0xF81F
YELLOW      =   0xFFE0
WHITE       =   0xFFFF
GRAY        =   0x8410

def testgreen():
    test(32,10,64)

def testblue():
    test(0,10,1)

def testred():
    test(2048,11,2048)

def test(colini,step,coladd):
    col=colini
    for i in range(0,319,step):
        #print("i= ",i,"\t color=",col)
        tft.fillRect(i,0,step,319,col)
        col=col+coladd

def testinit():
    global t1,t2,t3,t4
    start = ticks_ms()
    tft.fillscreen(getrandbits(16))
    tft.fillRect(10,10,80,80,getrandbits(16))
    tft.fillRect(230,10,80,80,getrandbits(16))
    tft.fillRect(10,150,80,80,getrandbits(16))
    tft.fillRect(230,150,80,80,getrandbits(16))
    tft.drawLine(0,0,319,239,getrandbits(16))
    tft.drawLine(0,239,319,0,getrandbits(16))
    color=getrandbits(16)
    tft.fillRect(155,90,10,60,color)
    tft.fillRect(130,115,60,10,color)
    tft.drawCircle(160,120,100,getrandbits(16))
    tft.drawDisk(160,120,50,60,getrandbits(16))
    end = ticks_ms()
    t1=ticks_diff(end,start)
    start = ticks_ms()
    testblue()
    end = ticks_ms()
    t2=ticks_diff(end,start)
    start = ticks_ms()
    testgreen()
    end = ticks_ms()
    t3=ticks_diff(end,start)
    start = ticks_ms()
    testred()
    end = ticks_ms()
    t4=ticks_diff(end,start)

def printv(mess):
    for i in mess:
        tft.drawchar(i)
        if tft.x_cursor>tft._width:
            tft.x_cursor=0
            tft.y_cursor = tft.y_cursor+tft.Char_height

class ScreenModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="SCREEN")
        # Pass the callback to send CAN message immediately
        self._init_ir_beam()
        self.tasks.append(asyncio.create_task(self.reset_led()))

    def _init_ir_beam(self):
        self.sensor = Pin(pinMap.IR_BEAM, Pin.IN)
        self.sensor.irq(trigger=Pin.IRQ_FALLING, handler=lambda pin: self._on_poke())

    def _on_poke(self):
        self.led.on()
        self.can_manager.send_normal(data=b"SCT")

    async def reset_led(self):
        """Reset LED after a short delay."""
        while True:
            self.led.off()
            await asyncio.sleep(0.1)

# Start tasks
def main():
    screen = ScreenModule()
    tft.setrotation(3)
    testinit()
    asyncio.run(screen.start_managers())