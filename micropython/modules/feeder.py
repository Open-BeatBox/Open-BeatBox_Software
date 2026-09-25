import asyncio

from machine import Pin, Timer, PWM
from beatbox_module import ModuleManager, ModuleStatus
from can_protocol import CommonCommand, FeederCommand, MessageType, Priority, parse_identifier
from bb_pin_map import FeederPins as PinMap

STEP_ANGLE = 0.9 # [degrees/step] as per the 14HM11-0404S stepper motor datasheet.

# A reward in beatbox is delivered by turning the feeder disc by a twentieth of a revolution.
REWARD_REV_FRACTION = 1 / 25
# Time given to a pellet to fall through the IR barrier after one reward step.
PELLET_WAIT_S = 0.5
# Number of reward steps attempted before declaring the delivery failed (one full revolution).
MAX_REWARD_ATTEMPTS = 20
# Polling period used to observe the IR barrier flag set from the IRQ handler.
POLL_PERIOD_S = 0.01
# Safety margin on top of the theoretical rotation duration.
ROTATION_TIMEOUT_S = 2.0

# number of steps for a full revolution
FULL_REV_STEPS = 360 / STEP_ANGLE
# This is the number of steps for a full revolution of the stepper motor.
# The 14HM11-0404S stepper motor has 0.9 degrees per step, which means it takes 400 steps for a full revolution.
# However, with microstepping enabled (e.g., 1/16 microstepping), the number of steps per revolution increases to 6400.
# Adjust this value based on your specific motor and microstepping configuration.

microstep_modes = {
    1 : (0, 0, 0),    # Full step
    2 : (1, 0, 0),    # Half step
    4 : (0, 1, 0),    # Quarter step
    8 : (1, 1, 0),    # Eighth step
    16 : (1, 1, 1),   # Sixteenth step
}

class A4988StepperDriver:
    """Minimal A4988 stepper driver using a PWM for STEP pulses.
        Expects a PWM-capable pin for the STEP signal.
    """
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
        step_fraction: int = 8,
        timer_id: int = -1,
        default_step_hz: int = 1000,
    ):
        self.enable = Pin(enable_pin, Pin.OUT, value=1)  # A4988 EN is active low
        self.direction = Pin(dir_pin, Pin.OUT, value=0)
        self.reset = Pin(reset_pin, Pin.OUT, value=1)
        self.sleep = Pin(sleep_pin, Pin.OUT, value=1)
        self.ms1 = Pin(ms1_pin, Pin.OUT, value=0)
        self.ms2 = Pin(ms2_pin, Pin.OUT, value=0)
        self.ms3 = Pin(ms3_pin, Pin.OUT, value=0)
        self.step_fraction = step_fraction  # default to 8
        self.set_microstep(step_fraction)
        self.step_pwm = PWM(step_pin, freq=default_step_hz, duty_u16=0) # 1kHz frequency and no output
        # used for precise timing of steps
        self.timer = Timer(timer_id) # -1 virtual timer is the only supported value for RP2040

        self._running = False

    @property
    def is_running(self):
        return self._running

    def rotate(self, step_hz: int | None = None, rev_n: float | None = None, forward: bool = True):
        """Rotate the stepper motor at a given frequency for a given number of revolutions.
           `If rev_n is 0, rotate indefinitely until stopped.
        """
        if self._running:
            return False
        steps = None
        if rev_n is not None:
            if rev_n == 0:
                steps = 0
            else:
                steps = int(FULL_REV_STEPS * self.step_fraction * rev_n)
        self._rotate(step_hz=step_hz, steps=steps, forward=forward)
        return True

    def _rotate(self, step_hz: int | None = None, steps: int | None = None, forward: bool = True):
        if steps is not None and steps < 0:
            forward = not forward
            steps = -steps
        self.set_direction(forward=forward)
        if steps is not None and steps > 0:
            self._running = True
            # calculate the time in seconds to complete the steps
            on_time = steps / (step_hz if step_hz is not None else self.step_pwm.freq())
            # start the timer to stop after on_time seconds
            self.timer.init(mode=Timer.ONE_SHOT, period=int(on_time * 1000), callback=self.stop_rotation)
        self._start_stepping(step_hz=step_hz, duty_cycle=50)

    def _start_stepping(self, step_hz: int | None = None, duty_cycle: int = 50):
        self.enable_driver()
        self.wake()
        # set freq if different
        if step_hz is not None and step_hz != self.step_pwm.freq():
            self.step_pwm.freq(step_hz)
        # set duty cycle to 50%
        duty_u16 = duty_cycle * 2**16 // 100
        self.step_pwm.duty_u16(duty_u16)

    def _stop_stepping(self):
        # set duty cycle to 0
        self.step_pwm.duty_u16(0)
        self.standby()

    def stop_rotation(self, _timer=None):
        """Stop the current rotation. Also used as the one-shot timer callback."""
        self.timer.deinit()
        self._stop_stepping()
        self._running = False

    def set_microstep(self, step_fraction: int):
        try:
            if step_fraction not in microstep_modes:
                raise ValueError(f"Invalid step fraction: {step_fraction}. Must be one of {list(microstep_modes.keys())}")
            self.step_fraction = step_fraction
            ms1, ms2, ms3 = microstep_modes[self.step_fraction]
            self.ms1.value(ms1)
            self.ms2.value(ms2)
            self.ms3.value(ms3)
        except Exception as e:
            print(f"Error setting microstep: {e}, defaulting to 1/8 microstepping")
            self.set_microstep(8)

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

    def stop(self):
        self.timer.deinit()
        self._stop_stepping()
        self.disable_driver()
        self._running = False

class FeederModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="FEEDER")
        self.steps_per_revolution: int = 400
        self.step_hz: int = 4000
        self.max_reward_attempts: int = MAX_REWARD_ATTEMPTS
        self.reward_in_progress = False
        self._reward_requested = False
        self._pellet_detected = False
        self.stepper: A4988StepperDriver = A4988StepperDriver(
            enable_pin=PinMap.MOTOR_EN,
            step_pin=PinMap.MOTOR_STEP,
            dir_pin=PinMap.MOTOR_DIR,
            reset_pin=PinMap.MOTOR_RST,
            sleep_pin=PinMap.MOTOR_SLEEP,
            ms1_pin=PinMap.MOTOR_MS1,
            ms2_pin=PinMap.MOTOR_MS2,
            ms3_pin=PinMap.MOTOR_MS3,
            step_fraction=8,
            timer_id=-1,
            default_step_hz=self.step_hz,
        )
        self.stepper.stop()
        self._init_pellet_sensor()
        self.tasks.append(asyncio.create_task(self.deliver_reward()))

    def _init_pellet_sensor(self):
        """IR barrier placed under the feeder disc, it is broken when a pellet falls through."""
        # Open drain sensor output: needs a pull-up, beam broken drives the line low.
        self.feeder_ir_barrier = Pin(PinMap.FOOD_SENSOR_IN, Pin.IN, Pin.PULL_UP)
        self.feeder_ir_barrier.irq(
            trigger=Pin.IRQ_FALLING,
            handler=self._on_pellet_irq,
        )

    def _on_pellet_irq(self, _pin):
        # Keep the IRQ handler minimal: the flag is consumed by the delivery task.
        self._pellet_detected = True

    @property
    def pellet_present(self):
        return self.feeder_ir_barrier.value() == 0

    # in beatox a reward is 1/20th of a revolution
    def reward(self):
        """Request a reward delivery, the actual work is done by the deliver_reward task."""
        print("FeederModule: reward() called")
        if self.reward_in_progress:
            print("FeederModule: reward already in progress, ignoring request")
            return False
        self.reward_in_progress = True
        self._reward_requested = True
        self.status = ModuleStatus.ACTIVE
        return True

    def command_handler(self, msg):
        super().command_handler(msg)
        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE or target_module_id != self.id:
            return
        if command == FeederCommand.REQUEST_REWARD:
            self.reward()

    async def deliver_reward(self):
        """Turn the feeder by 1/20th of a revolution until the IR barrier sees a pellet."""
        while True:
            if not self._reward_requested:
                await asyncio.sleep(POLL_PERIOD_S)
                continue

            if self.feeder_ir_barrier.value() == 0:
                print("FeederModule: pellet already present, skipping reward")
                self.reward_in_progress = False
                self._reward_requested = False
                self._send_frame(
                    priority=Priority.STATUS,
                    msg_type=MessageType.RESPONSE_MODULE,
                    command=FeederCommand.REWARD_DELIVERED,
                    payload=bytearray([0]),
                )
                continue

            self._reward_requested = False
            attempts = await self._run_reward_cycle()
            self.reward_in_progress = False

            if attempts:
                self.status = ModuleStatus.IDLE
                print(f"FeederModule: pellet delivered after {attempts} attempt(s)")
                self._send_frame(
                    priority=Priority.STATUS,
                    msg_type=MessageType.RESPONSE_MODULE,
                    command=FeederCommand.REWARD_DELIVERED,
                    payload=bytearray([attempts]),
                )
            else:
                self.status = ModuleStatus.ERROR
                print(f"FeederModule: no pellet after {self.max_reward_attempts} attempts")
                self._send_frame(
                    priority=Priority.ERROR,
                    msg_type=MessageType.RESPONSE_COMMON,
                    command=CommonCommand.ERROR,
                    payload=bytearray([FeederCommand.REQUEST_REWARD, self.max_reward_attempts]),
                )

    async def _run_reward_cycle(self):
        """Return the number of attempts needed to deliver a pellet, 0 if none was delivered."""
        self._pellet_detected = False
        for attempt in range(1, self.max_reward_attempts + 1):
            self.stepper.rotate(step_hz=self.step_hz, rev_n=REWARD_REV_FRACTION, forward=True)
            await self._wait_rotation_end()
            if await self._wait_for_pellet(PELLET_WAIT_S):
                self.stepper.stop()
                return attempt
        self.stepper.stop()
        return 0

    async def _wait_rotation_end(self):
        """Wait for the current rotation to complete, stopping early on pellet arrival."""
        elapsed = 0.0
        while self.stepper.is_running and elapsed < ROTATION_TIMEOUT_S:
            if self._pellet_detected:
                break
            await asyncio.sleep(POLL_PERIOD_S)
            elapsed += POLL_PERIOD_S
        if self.stepper.is_running:
            self.stepper.stop_rotation()

    async def _wait_for_pellet(self, timeout_s):
        """Poll the flag set by the IR barrier IRQ until timeout_s elapsed."""
        elapsed = 0.0
        while elapsed < timeout_s:
            if self._pellet_detected:
                return True
            await asyncio.sleep(POLL_PERIOD_S)
            elapsed += POLL_PERIOD_S
        return self._pellet_detected


def main():
    feeder = FeederModule()
    asyncio.run(feeder.start_managers())
