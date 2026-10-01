import board

"""
As we are using the Adafruit Feather RP2040 CAN as the model board,
pin definitions are based on its layout and naming might be confusing.
TODO: define a custom board for each BeatBox module to avoid confusion.
"""

class PinMap:
    """Base class for pin mappings. Define common pins."""
    CAN_CS = board.CAN_CS
    USER_BTN = board.NEOPIXEL # GPIO21 is NEOPIXEL on Feather RP2040 CAN
    CAN_INT = board.CAN_INTERRUPT
    CAN_LED = board.D24
    USER_LED = board.D25

class NosepokePins(PinMap):
    """Nosepoke module specific pins"""
    IR_BEAM_IN = board.D10
    IR_BEAM_OUT = board.D9

class ScreenPins(PinMap):
    """Screen module specific pins"""
    IR_BEAM = board.NEOPIXEL_POWER # GPIO20 is NEOPIXEL_POWER on Feather RP2040 CAN
