#!/usr/bin/env python3
"""Beatbox validation CLI.

Connects to the main module UART (``/dev/ttyUSB0`` by default) and sends text commands.
The main module translates them into CAN frames (see ``libs/cli_protocol.py``) and echoes
responses back on the same link.

The main module must be deployed with ``CLI_MODE = True`` in ``modules/main_module.py``.

Examples::

    python tools/bb_cli.py                        # interactive prompt
    python tools/bb_cli.py -c SCAN                # one-shot command
    python tools/bb_cli.py -f scripts/feeder.txt  # replay a command script
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))

import cli_protocol  # noqa: E402

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover - depends on the environment
    sys.exit("pyserial is required: pip install pyserial (or conda install pyserial)")

try:
    import readline  # noqa: F401  (enables history/edition in input())
except ImportError:  # pragma: no cover - not available on every platform
    readline = None

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 921600
DEFAULT_SETTLE = 1.0
PROMPT = "bb> "


class SerialLink:
    """Serial connection with a background reader printing incoming lines."""

    def __init__(self, port, baud, log_file=None):
        self.port = port
        self.baud = baud
        self.log_file = log_file
        self.serial = None
        self._reader = None
        self._stop = threading.Event()
        self._start_time = time.monotonic()

    def open(self):
        self.serial = serial.Serial(self.port, self.baud, timeout=0.1)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return self

    def close(self):
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        if self.serial is not None and self.serial.is_open:
            self.serial.close()
        if self.log_file is not None:
            self.log_file.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, *_exc):
        self.close()

    def _timestamp(self):
        return "%8.3f" % (time.monotonic() - self._start_time)

    def _emit(self, direction, text):
        line = "[%s] %s %s" % (self._timestamp(), direction, text)
        print(line, flush=True)
        if self.log_file is not None:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    def _read_loop(self):
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = self.serial.read(256)
            except Exception as exc:  # port unplugged, closed, ...
                if not self._stop.is_set():
                    self._emit("!!", "serial read error: %s" % exc)
                return
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    self._emit("<<", text)

    def send(self, command):
        self._emit(">>", command)
        self.serial.write((command + "\r\n").encode())
        self.serial.flush()


def validate_locally(command):
    """Parse a command PC-side to catch typos before hitting the wire."""
    try:
        cli_protocol.parse_command(command)
    except cli_protocol.CommandError as exc:
        return str(exc)
    return None


def print_help():
    print("Commands (sent to the main module unless noted):")
    for usage, description in cli_protocol.help_lines():
        print("  %-42s %s" % (usage, description))
    print("Client-side only:")
    print("  %-42s %s" % ("sleep <seconds>", "Pause before the next command"))
    print("  %-42s %s" % ("quit / exit", "Leave the CLI"))


class Completer:
    """Readline completer over the shared command table."""

    def __init__(self, names):
        self.names = list(names) + ["sleep", "quit", "exit"]
        self.matches = []

    def complete(self, text, state):
        if state == 0:
            upper = text.upper()
            self.matches = [name for name in self.names if name.upper().startswith(upper)]
        try:
            return self.matches[state]
        except IndexError:
            return None


def handle_client_command(command):
    """Handle commands interpreted by the CLI itself.

    Returns ``"handled"``, ``"quit"``, or ``None`` when the command must be sent.
    """
    tokens = command.split()
    keyword = tokens[0].lower()

    if keyword in ("quit", "exit"):
        return "quit"

    if keyword == "sleep":
        if len(tokens) != 2:
            print("usage: sleep <seconds>")
            return "handled"
        try:
            time.sleep(float(tokens[1]))
        except ValueError:
            print("invalid duration '%s'" % tokens[1])
        return "handled"

    if keyword == "help" and len(tokens) == 1:
        print_help()
        return "handled"

    return None


def send_command(link, command, settle, strict=True):
    """Validate then send one command, leaving time for the responses to arrive."""
    error = validate_locally(command)
    if error is not None:
        print("local error: %s" % error)
        if strict:
            return False
    link.send(command)
    time.sleep(settle)
    return True


def run_script(link, path, settle):
    ok = True
    with open(path, "r") as handle:
        for raw_line in handle:
            command = cli_protocol.strip_comment(raw_line)
            if not command:
                continue
            action = handle_client_command(command)
            if action == "quit":
                break
            if action == "handled":
                continue
            if not send_command(link, command, settle):
                ok = False
    return ok


def run_interactive(link, settle):
    if readline is not None:
        readline.set_completer(Completer(cli_protocol.COMMAND_NAMES).complete)
        readline.parse_and_bind("tab: complete")
    print("Connected to %s @ %d baud. Type 'help' for commands, 'quit' to exit."
          % (link.port, link.baud))
    while True:
        try:
            command = cli_protocol.strip_comment(input(PROMPT))
        except (EOFError, KeyboardInterrupt):
            print()
            return True
        if not command:
            continue
        action = handle_client_command(command)
        if action == "quit":
            return True
        if action == "handled":
            continue
        # Interactive mode stays permissive: unknown commands are still sent so the
        # firmware has the final word on what it supports.
        send_command(link, command, settle, strict=False)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-p", "--port", default=DEFAULT_PORT,
                        help="serial port (default: %(default)s)")
    parser.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD,
                        help="baud rate, must match main_module.BAUD_RATE (default: %(default)s)")
    parser.add_argument("-c", "--command", action="append",
                        help="send a command and exit (repeatable)")
    parser.add_argument("-f", "--file", help="replay a command script (one command per line)")
    parser.add_argument("-t", "--settle", type=float, default=DEFAULT_SETTLE,
                        help="seconds spent collecting responses after each command "
                             "(default: %(default)s)")
    parser.add_argument("--log", help="append the session transcript to this file")
    parser.add_argument("--list-commands", action="store_true",
                        help="print the supported commands and exit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.list_commands:
        print_help()
        return 0

    log_file = open(args.log, "a") if args.log else None

    try:
        link = SerialLink(args.port, args.baud, log_file=log_file).open()
    except serial.SerialException as exc:
        if log_file is not None:
            log_file.close()
        print("cannot open %s: %s" % (args.port, exc), file=sys.stderr)
        return 1

    ok = True
    try:
        if args.command:
            for command in args.command:
                cleaned = cli_protocol.strip_comment(command)
                if not cleaned:
                    continue
                if handle_client_command(cleaned) is not None:
                    continue
                ok = send_command(link, cleaned, args.settle) and ok
        if args.file:
            ok = run_script(link, args.file, args.settle) and ok
        if not args.command and not args.file:
            ok = run_interactive(link, args.settle)
    except KeyboardInterrupt:
        print()
    finally:
        link.close()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
