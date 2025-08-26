## About CircuitPython and SPI baudrate/frequency configuration

From [circuitpython.org](circuitpython.org) :

> *CircuitPython is a programming language designed to simplify experimenting and learning to code on low-cost microcontroller boards.*

However, this comes with a tradeoff regarding execution speed AND the ability to configure it.

More specifically, let's tackle a small issue regarding SPI bus speed configuration. Note that this paragraph can be skipped to the TL;DR for a quick answer on how to configure SPI bus speed.

The microcontroller (RP2040 here) uses the SPI bus to communicate with the CAN MCP25625 chip (which integrates a CAN Controller and a CAN Transceiver).

There are thus two bus frequencies that are relevant here: SPI bus frequency and CAN bus frequency.
From the microcontroller's perspective, the CAN bus is fairly autonomous after it has been configured, so it's pretty straightforward to use.

The SPI bus, however, is handled by the MCU, and each transaction must be initiated by the MCU as the latter is the controller on the bus (in contrast to the MCP25625, which is the peripheral).

The SPI bus driver that handles SPI communication is included in the built-in library packaged with the given CircuitPython firmware. This means that the driver is not easily modifiable by the user.

On another note, when initializing an instance of the MCP2515 (which is the controller chip of the MCP25625), the library expects an SPI bus instance and uses it to initialize an SPIDevice object.

This SPIDevice object is then used for SPI communication. Before each transaction, the SPIDevice takes a lock and reconfigures the SPI bus. **This means that any configuration made by the user is overridden by the MCP2515 initialization.**

Unfortunately, the MCP2515 [does not take a parameter to set the SPI bus speed](https://docs.circuitpython.org/projects/mcp2515/en/latest/api.html#adafruit_mcp2515.MCP2515). In order to be able to set a specific speed, a solution one has is to modify the MCP2515 library to set the baudrate to the desired one when initializing the SPIDevice.

However, libraries in CircuitPython are distributed in the form of `.mpy` files, which are precompiled bytecode libraries. This enables faster startup and less memory usage.
In order to use a custom version of the MCP2515, one must delete the `.mpy` file, retrieve the `.py` equivalent, and copy it to the same path with the same name (but with a `.py` extension). One can also modify the library `.py` file and then compile it to an `.mpy` using [mpy-cross](https://learn.adafruit.com/welcome-to-circuitpython/library-file-types-and-frozen-libraries#creating-an-mpy-file-3118108).

One could also access the MCP2515 object instance to modify it's SPIDevice member, overriding it with a new SPIDevice object initialized with the correct baudrate.

```python
def CanManager
```

#### TL;DR 

Set the correct baudrate directly in the MCP2515 `__init__.py` file and replace the `__init__.mpy` with it in the `lib/adafruit_mcp2515` folder on the CIRCUITPY device.

