"""Tick helpers working on both CPython and MicroPython."""

import time

try:
    ticks_ms = time.ticks_ms
    ticks_us = time.ticks_us
    ticks_diff = time.ticks_diff
except AttributeError:  # CPython
    # perf_counter rather than monotonic: monotonic only advances every
    # ~15 ms on Windows, which is too coarse for the microsecond helpers.
    def ticks_ms():
        return int(time.perf_counter() * 1000)

    def ticks_us():
        return int(time.perf_counter() * 1_000_000)

    def ticks_diff(a, b):
        return a - b