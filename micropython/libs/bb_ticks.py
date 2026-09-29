"""Tick helpers working on both CPython and MicroPython."""

import time

try:
    ticks_ms = time.ticks_ms
    ticks_us = time.ticks_us
    ticks_diff = time.ticks_diff
except AttributeError:  # CPython
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_us():
        return int(time.monotonic() * 1_000_000)

    def ticks_diff(a, b):
        return a - b