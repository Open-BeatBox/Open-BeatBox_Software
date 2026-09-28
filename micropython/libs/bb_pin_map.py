import machine

"""
As we are using the Adafruit Feather RP2040 CAN as the model board,
pin definitions are based on its layout and naming might be confusing.
TODO: define a custom Micropython board for each BeatBox module to avoid confusion.
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

class MainModulePins(PinMap):
    """Pin mapping for main module."""
    UART_TX = 0
    UART_RX = 1
    UART_RTS = 2
    UART_CTS = 3
    RTC_I2C_SDA = 9
    RTC_I2C_SCL = 10
    USER_BTN = 11
    CAN_LED = 28
    USER_LED = 29
    SD_MISO = 4
    SD_CS = 5
    SD_SCLK = 6
    SD_MOSI = 7
    SD_CD = 8

class NosepokePins(PinMap):
    """Pin mapping for nosepoke module."""
    UART_TX = 0
    UART_RX = 1
    IR_BEAM_OUT = 9
    IR_BEAM_IN = 10
    USER_BTN = 4

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
    USER_BTN = 21
    LCD_BACKLIGHT = 28
    LCD_CSS = 29

class LightingPins(PinMap):
    """Pin mapping for lighting module."""
    PCA9955B_I2C_SDA = 2
    PCA9955B_I2C_SCL = 3
    USER_BTN = 21

class FeederPins(PinMap):
    """Pin mapping for feeder module."""
    UART_TX = 0
    UART_RX = 1
    MOTOR_EN = 5
    MOTOR_RST = 6
    MOTOR_SLEEP = 7
    MOTOR_DIR = 9
    MOTOR_STEP = 10
    MOTOR_MS3 = 11
    MOTOR_MS2 = 12
    MOTOR_MS1 = 13
    FOOD_SENSOR_IN = 20
    USER_BTN = 4
    CAN_LED = 28
    USER_LED = 27

class IRBarrierPins(PinMap):
    """ Pin mapping for IR barrier module.
        Each line of the IR barrier is connected to a specific GPIO pin.
        The IR_BARRIER_LINES tuple defines the mapping of line indices to GPIO pins.
    """
    # IR_BARRIER_LINES = (3, 4, 2, 5, 1, 6, 0, 7)
    IR_BARRIER_LINES = (1,)
    I2C_SDA = 9
    I2C_SCL = 10
    IO_11 = 11
    IO_12 = 12
    IO_13 = 13
    IO_20 = 20
    IO_28 = 28
    IO_29 = 29