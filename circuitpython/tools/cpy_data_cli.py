#!/usr/bin/env python3
import sys
import serial
import argparse


def main():
    parser = argparse.ArgumentParser(description='Send and receive raw UTF-8 data to a CircuitPython Data port (e.g., /dev/ttyACM1).')
    parser.add_argument('port', help='Serial port (e.g., /dev/ttyACM1)')
    parser.add_argument('-b', '--baudrate', type=int, default=115200, help='Baudrate (default: 115200)')
    args = parser.parse_args()

    try:
        ser = serial.Serial(args.port, args.baudrate, timeout=0.1)
    except Exception as e:
        print(f'Error opening serial port: {e}')
        sys.exit(1)

    print(f'Connected to {args.port}. Type messages and press Enter to send. Ctrl+C to exit.')
    try:
        while True:
            # Read from stdin and send to serial
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                ser.write(line.encode('utf-8'))
            # Read from serial and print to stdout
            data = ser.read(1024)
            if data:
                try:
                    print(data.decode('utf-8'), end='', flush=True)
                except UnicodeDecodeError:
                    print(data, end='', flush=True)
    except KeyboardInterrupt:
        print('\nExiting.')
    finally:
        ser.close()

if __name__ == '__main__':
    import select
    main()

