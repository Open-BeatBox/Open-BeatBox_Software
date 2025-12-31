import time
import digitalio
import board

REPEATS = 100
SAMPLE_SIZE = 1000
TIMING_THRESHOLD = 1000000 # [ns] = 1 ms
# for scope
trigger_pin = digitalio.DigitalInOut(board.D25)
trigger_pin.direction = digitalio.Direction.OUTPUT


# Test IO write latency
# time.monotonic_ns() resolution seems to be ~ 30518ns
def test_interrupt_latency():
    times = []
    count = 0
    for i in range(SAMPLE_SIZE):
        start = time.monotonic_ns()
        end = time.monotonic_ns()
        times.append(end - start)
        # IO op took more than 1 ms
        if times[-1] > 1000000:
            count = count + 1
    return min(times), max(times), sum(times) / len(times), count

while True:
    min_times = []
    max_times = []
    mean_times = []
    overshoot_count = 0
    for i in range(REPEATS):
        results = test_interrupt_latency()
        min_times.append(results[0])
        max_times.append(results[1])
        mean_times.append(results[2])
        overshoot_count += results[3]
    print(f"{overshoot_count} on {REPEATS*SAMPLE_SIZE} measurements overshoot 1 ms, that's {100*overshoot_count/(REPEATS * SAMPLE_SIZE)}%")
    print(f"Min, max, mean: {min(min_times)}, {max(max_times)}, {sum(mean_times) / len(mean_times)}")
    # free memory
    min_times.clear()
    max_times.clear()
    mean_times.clear()
