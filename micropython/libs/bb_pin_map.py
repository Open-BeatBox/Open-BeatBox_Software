import machine

"""
As we are using the Adafruit Feather RP2040 CAN as the model board,
pin definitions are based on its layout and naming might be confusing.
TODO: define a custom board for each BeatBox module to avoid confusion.
"""

class PinMap:
    """Base class for pin mappings. Define common pins."""
    CAN_CS = 19
    CAN_SCK = 14
    CAN_MOSI = 15
    CAN_MISO = 8
    USER_BTN = 21
    CAN_INT = 22
    CAN_LED = 24
    USER_LED = 25

class NosepokePins(PinMap):
    """Pin mapping for nosepoke module."""
    IR_BEAM_OUT = 9
    IR_BEAM_IN = 10

class ScreenPins(PinMap):
    """Pin mapping for screen module."""
    IR_BEAM = 20
    LCD_RD = 12
    LCD_WR = 11
    LCD_RS = 10
    LCD_CS = 9
    LCD_RST = 13
    LCD_D0 = 0
    LCD_D1 = 1
    LCD_D2 = 2
    LCD_D3 = 3
    LCD_D4 = 4
    LCD_D5 = 5
    LCD_D6 = 6
    LCD_D7 = 7

class FeederPins(PinMap):
    """Pin mapping for feeder module."""
    MOTOR_EN = 5
    MOTOR_RST = 6
    MOTOR_SLEEP = 7
    MOTOR_DIR = 9
    MOTOR_STEP = 10
    MOTOR_MS3 = 11
    MOTOR_MS2 = 12
    MOTOR_MS1 = 13
