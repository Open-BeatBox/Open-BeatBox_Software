import os
import pty
import time
import threading

from beatbox.comm.beatbox_protocol import BeatBoxMessage


class MockArduino:
    def __init__(self):
        master_fd, slave_fd = pty.openpty()
        print("Requesting device path...")
        # self.device_path = os.ttyname(master_fd)
        self.slave_path = os.ttyname(slave_fd)
        # print(f"Device path: {self.device_path}")
        self.baud_rate = 115200
        self.serial_timeout = 2
        self.fd = master_fd
        print(f'Setting up serial port with baud rate {self.baud_rate} and timeout {self.serial_timeout}')
        # self.port = serial.Serial(self.device_path, baudrate=self.baud_rate, timeout=self.serial_timeout)
        self.stop_flag = False
        self.rx_buffer = b''
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.pin_states = [0] * 11

    def _run(self):
        while not self.stop_flag:
            try:
                print(f'Checking {self.__class__.__name__} for data')
                data = os.read(self.fd, 128)
                if data:
                    self.rx_buffer += data
                    # Split on newlines
                    while b'\n' in self.rx_buffer:
                        line, self.rx_buffer = self.rx_buffer.split(b'\n', 1)
                        line = line.strip()
                        if line:
                            self._handle_line(line)
            except OSError:
                # If the master_fd is closed or no data
                pass

            time.sleep(0.01)  # avoid busy loop

    def _handle_line(self, line):
        print(f'{self.__class__.__name__} received: "{line}"')
        message = BeatBoxMessage.from_bytes(line)
        if message.command == 'ready':
            self.send(b'ready:00')
        elif message.command == 'set':
            self.acknowledge(message)
            self.send_state(message)
        elif message.command == 'wait':
            self.acknowledge(message)
            time.sleep(1)
        else:
            print("Unknown command:", message.command)
        time.sleep(0.1)

    def send(self, bytes_str: str | bytes):
        if isinstance(bytes_str, str):
            bytes_str = bytes_str.encode('utf-8')
        if not bytes_str.endswith(b'\n'):
            bytes_str += b'\n'
        print(f'{self.__class__.__name__} sending: "{bytes_str}"')
        os.write(self.fd, bytes_str)

    def acknowledge(self, message):
        response = BeatBoxMessage.from_parts('ack:', message.sequence, message.command)
        self.send(response)

    def send_state(self, message):
        print(f'Changing pins from {self.pin_states} to {message.data}')
        self.pin_states = message.data
        response = BeatBoxMessage.from_parts('done:', message.sequence)
        self.send(response)

    def close(self):
        self.stop_flag = True
        self.thread.join()
        # if self.fd.is_open:
        #     self.fd.close()

# Usage:
# device = MockArduino()
# print("Connect your real app to:", device.device_path)
# ... test your communication ...
# device.close()