# Timing measurements

## Setup

Two modules, powered by shield and connected through the DB-9 port.
The modules are connected to computer by USB. 

## Jitter

### No op between two time measurement

REPEATS = 100
SAMPLE_SIZE = 1000
TIMING_THRESHOLD = 1000000 # [ns] = 1 ms

36 on 100000 measurements overshoot 1 ms, that's 0.036%
Min, max, mean: 0, 8148198, 18639.1

Maximum delay is 8 ms. As there is no code between the two measurements,
the delay is propobably coming from background interrupts or operations.
This could be USB communication (
