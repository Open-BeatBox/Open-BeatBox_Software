import asyncio

from machine import Pin, Timer
from beatbox_module import ModuleManager, ModuleStatus
from can_protocol import FeederCommand, MessageType, Priority, parse_identifier

from bb_pin_map import FeederPins as PinMap


class A4988StepperDriver:
    """Minimal A4988 stepper driver using a periodic Timer for STEP pulses."""

    def __init__(
        self,
        enable_pin,
        step_pin,
        dir_pin,
        reset_pin,
        sleep_pin,
        ms1_pin,
        ms2_pin,
        ms3_pin,
        timer_id: int = -1,
        default_step_hz: int = 400,
    ):
        self.enable = Pin(enable_pin, Pin.OUT, value=1)  # A4988 EN is active low
        self.step = Pin(step_pin, Pin.OUT, value=0)
        self.direction = Pin(dir_pin, Pin.OUT, value=0)
        self.reset = Pin(reset_pin, Pin.OUT, value=1)
        self.sleep = Pin(sleep_pin, Pin.OUT, value=1)
        self.ms1 = Pin(ms1_pin, Pin.OUT, value=0)
        self.ms2 = Pin(ms2_pin, Pin.OUT, value=0)
        self.ms3 = Pin(ms3_pin, Pin.OUT, value=0)

        self.timer = Timer(timer_id) # -1 virtual timer is the only supported value for RP2040
        self.default_step_hz = default_step_hz

        self._running = False
        self._step_level = 0
        self._steps_done = 0
        self._target_steps = None

    @property
    def is_running(self):
        return self._running

    @property
    def steps_done(self):
        return self._steps_done

    def set_microstep(self, ms1=0, ms2=0, ms3=0):
        self.ms1.value(1 if ms1 else 0)
        self.ms2.value(1 if ms2 else 0)
        self.ms3.value(1 if ms3 else 0)

    def set_direction(self, forward=True):
        self.direction.value(1 if forward else 0)

    def wake(self):
        self.sleep.value(1)
        self.reset.value(1)

    def standby(self):
        self.sleep.value(0)

    def enable_driver(self):
        self.enable.value(0)

    def disable_driver(self):
        self.enable.value(1)

    def _timer_cb(self, _):
        self._step_level ^= 1
        self.step.value(self._step_level)
        if self._step_level:
            self._steps_done += 1
            if self._target_steps is not None and self._steps_done >= self._target_steps:
                self.stop()

    def start(self, step_hz: int | None = None, steps: int | None = None):
        if step_hz is None:
            step_hz = self.default_step_hz
        if step_hz <= 0:
            raise ValueError("step_hz must be > 0")
        if steps is not None and steps <= 0:
            raise ValueError("steps must be > 0 when provided")

        self.enable_driver()
        self.wake()
        self._steps_done = 0
        self._target_steps = steps
        self._step_level = 0
        self.step.value(0)
        self._running = True

        # One complete STEP pulse requires two edges: high then low.
        try:
            self.timer.init(mode=Timer.PERIODIC, freq=step_hz * 2, callback=self._timer_cb, hard=True)
        except TypeError:
            self.timer.init(mode=Timer.PERIODIC, freq=step_hz * 2, callback=self._timer_cb)

    def stop(self):
        self.timer.deinit()
        self.step.value(0)
        self._running = False

    def move_steps_blocking(
        self, steps: int, step_hz: int | None = None, forward: bool = True
    ):
        self.set_direction(forward)
        self.start(step_hz=step_hz, steps=steps)
        while self.is_running:
            pass


class FeederModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="FEEDER")
        self.steps_per_revolution: int = 400
        self.reward_in_progress = False
        self.stepper: A4988StepperDriver = A4988StepperDriver(
            enable_pin=PinMap.MOTOR_EN,
            step_pin=PinMap.MOTOR_STEP,
            dir_pin=PinMap.MOTOR_DIR,
            reset_pin=PinMap.MOTOR_RST,
            sleep_pin=PinMap.MOTOR_SLEEP,
            ms1_pin=PinMap.MOTOR_MS1,
            ms2_pin=PinMap.MOTOR_MS2,
            ms3_pin=PinMap.MOTOR_MS3,
            timer_id=-1,
            default_step_hz=400,
        )
        # A4988 full-step mode is MS1/MS2/MS3 = 0/0/0.
        self.stepper.set_microstep(0, 0, 0)
        self.tasks.append(asyncio.create_task(self.blink()))
        self.tasks.append(asyncio.create_task(self.report_reward_completion()))

    async def blink(self):
        while True:
            self.led.toggle()
            await asyncio.sleep(0.5)

    async def rotate_periodic(self):
        while True:
            self.rotate()
            await asyncio.sleep(5)

    def rotate(self):
        if self.stepper.is_running:
            return False
        self.status = ModuleStatus.ACTIVE
        self.stepper.set_direction(forward=True)
        self.stepper.start(step_hz=200, steps=self.steps_per_revolution)
        self.reward_in_progress = True
        return True

    def command_handler(self, msg):
        super().command_handler(msg)

        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE or target_module_id != self.id:
            return
        if command == FeederCommand.REQUEST_REWARD:
            self.rotate()

    async def report_reward_completion(self):
        while True:
            if self.reward_in_progress and not self.stepper.is_running:
                self.reward_in_progress = False
                self.status = ModuleStatus.IDLE
                self._send_frame(
                    priority=Priority.STATUS,
                    msg_type=MessageType.RESPONSE_MODULE,
                    command=FeederCommand.REWARD_DELIVERED,
                    payload=bytearray(),
                )
            await asyncio.sleep(0.01)


def main():
    feeder = FeederModule()
    feeder.tasks.append(asyncio.create_task(feeder.rotate_periodic()))
    asyncio.run(feeder.start_managers())