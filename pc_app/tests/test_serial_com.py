"""Integration tests using MockArduino (real pty serial ports)."""

import time

import pytest

from beatbox.comm.serial_com import BeatBoxCommunicator

from beatbox.gui.bb_logging import logger, QtHandler

# Remove Qt handler to avoid signal issues in non-Qt tests
for h in logger.handlers[:]:
    if isinstance(h, QtHandler):
        logger.removeHandler(h)
        h.close()


def test_serial_com_handshake(fake_arduino):
    com = BeatBoxCommunicator(baud_rate=fake_arduino.baud_rate,
                              serial_timeout=fake_arduino.serial_timeout)

    received = []
    def handle_message(message):
        received.append(message)
        print(f'Pytest received message: {message}')

    com.messageReceived.connect(handle_message)

    com.connect_port(fake_arduino.slave_path)
    time.sleep(1)
    print('Ready for handshake')
    assert com.handshake(), 'Handshake failed'

    # automatic connect and handshake if port supplied
    # com.send_message('set', [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1])  # TODO: test
    com.send_set_command([1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1], None)
    time.sleep(2)  # Wait for the message to be received and acknowledged

    com.close()