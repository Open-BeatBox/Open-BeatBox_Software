import time

import pytest

from beatbox.comm.serial_com import BeatBoxCommunicator
from tests.fake_arduino import MockArduino

from beatbox.gui.bb_logging import logger, QtHandler

for h in logger.handlers[:]:
    if isinstance(h, QtHandler):
        logger.removeHandler(h)
        h.close()

@pytest.fixture
def fake_arduino_device():
    device = MockArduino()

    yield device
    device.close()


def test_serial_com_threaded(fake_arduino_device):
    fake_dev_path = fake_arduino_device.slave_path

    com = BeatBoxCommunicator(baud_rate=fake_arduino_device.baud_rate,
                              serial_timeout=fake_arduino_device.serial_timeout)

    def handle_message(message):
        print(f"Pytest received message: {message}")

    # def handle_error(error):
    #     assert False, f"Error: {error}"

    com.messageReceived.connect(handle_message)
    # com.errorOccurred.connect(handle_error)
    com.connect_port(fake_dev_path)
    time.sleep(1)
    print('Ready for handshake')
    assert com.handshake(), "Handshake failed"
    # automatic connect and handshake if port supplied
    com.send_set_command([1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1], None)

    time.sleep(2)  # Wait for the message to be received and acknowledged
