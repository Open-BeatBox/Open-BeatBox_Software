# boot.py
# Enable both console (for debugging) and data (for communication)
import usb_cdc

# Enable both console and data ports
# Console = /dev/ttyACM0 (for REPL/debugging)  
# Data = /dev/ttyACM1 (for communication)
usb_cdc.enable(console=False, data=True)

print("USB CDC enabled - console and data ports active")
