"""
This module contains the base classes for managing experiments in BeatBox.

It provides the `BaseExperimentManager` class, which serves as an abstract base class for all experiment managers,
All experiment managers should inherit from this class and implement the required methods.
They are typically called  BeatBoxExperimentManagerS<n> where <n> is the stage number of the experiment.
and the `BeatBoxExperimentManagerS1` and `BeatBoxExperimentManagerS2` classes, which implement the basic functionality

These classes handle everything related to the experiment management, such as starting trials,
retrieving pellets, and managing the experiment configuration.
The front-end GUI implemented in arduino_control_app will interact with these classes
using signals and slots to start trials, retrieve pellets, and update the experiment state.
The communication with the BeatBox hardware is handled by the `BeatBoxCommunicator` class.
"""

# TODO: improvement:
#   - Avoid timers colliding
from __future__ import annotations

import warnings
import logging
import csv
import time
from abc import ABCMeta
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Mapping, List, Final, Union, final, Dict, Optional

from configobj import ConfigObj
from configobj.validate import Validator
from qtpy import QtCore, QtWidgets

from beatbox.serial_com import BeatBoxCommunicator, SetPayloadOutputBus
from beatbox.utils import PinConfig, load_pin_config
from beatbox.validators import list_w_choice

ANY_PIN: Final = object()


class SubjectInfo:
    """
    Class to hold subject information.
    """

    def __init__(self, subject_id: str = '', weight: int = 0,
                 box: str = '', sex: str = '', light_cycle: str = ''):
        self.subject_id = subject_id
        self.weight = weight
        self.box = box
        self.sex = sex
        self.light_cycle = light_cycle


class SConfig:
    s_level: str = 'S0'

    def __init__(self, folder=Path(__file__).parent):
        self.folder = folder
        self.attributes: List[str] = self._collect_attributes()
        self._config: Optional[ConfigObj] = None
        self.validator = Validator(functions={'list_w_choice': list_w_choice})

    def _collect_attributes(self) -> List[str]:
        all_annotations = {}
        subclass_annotations = {}

        for cls in reversed(self.__class__.__mro__):
            annotations = getattr(cls, '__annotations__', {})
            all_annotations.update(annotations)
            if cls is self.__class__:
                subclass_annotations = annotations

        return list(subclass_annotations.keys())

    def load(self):
        self._config = ConfigObj(str(self.cfg_path), encoding='utf-8',
                                 configspec=str(self._spec_path), unrepr=True)

        self._validate()

        for attr in self.attributes:
            # Set specific stage attributes
            if self.s_level in self._config and attr in self._config[self.s_level]:
                setattr(self, attr, self._config[self.s_level][attr])

            # if not redefined in the spec, look for global value
            elif attr in self._config:
                setattr(self, attr, self._config[attr])

    def _validate(self):
        results = self._config.validate(self.validator, preserve_errors=True, copy=True)
        errors = []
        if results is not True:
            errors = {key: value for key, value in results.items() if value is not True}
            raise ValueError(f"Config validation failed. {errors}")

    @property
    def cfg_path(self) -> Path:
        return Path(self.folder) / 'config' / 'experiment.cfg'

    @property
    def _spec_path(self) -> Path:
        # return Path('~/.beatbox').expanduser() / 'config_specs' / f'{self.s_level}.config.spec'
        return Path(__file__).parent / 'config_specs' / f'{self.s_level}.config.spec'

    def write(self):
        self._config.write()

    def set(self, attribute, value):
        # Set attribute and update config object
        if self._config is not None:
            if self.s_level in self._config and attribute in self._config[self.s_level]:
                self._config[self.s_level][attribute] = value
            elif attribute in self._config:
                self._config[attribute] = value
        setattr(self, attribute, value)


class Edge(Enum):
    RISING = 'rising'
    FALLING = 'falling'
    BOTH = 'both'


RelevantPins = Union[
    ANY_PIN,
    None,  # ignore all pins
    Mapping[str, Edge],  # pin -> required edge
]


class Phase(Enum):
    """
    Enum representing the different phases of an experiment.
    Each phase corresponds to a specific state in the experiment flow.
    Add new phases here as needed for the experiment.
    """
    ITI = auto()
    WAIT_TUNNEL = auto()
    DELIVER_PELLET = auto()
    WAIT_PELLET = auto()
    WAIT_PELLET_RETRIEVAL = auto()
    SHOW_STIM = auto()
    WAIT_TOUCH = auto()
    CORRECT_RESPONSE = auto()
    INCORRECT_RESPONSE = auto()
    STOP_FLASH = auto()  # used to stop flashing the error light
    DONE = auto()


DecisionFn = Callable[['BaseExperimentManager'], Any]  # returns a key for the dict
# DecisionFn2 = Callable[[], Any]   # returns a key for the dict
OnEnterFn = Callable[[], None]  # No return as there is "nobody" to return to in the state machine


@dataclass
class PhaseSpec:
    """
    Specification for a phase in the experiment.
    This class defines the properties of a phase.
    Instances of this class are used to define a phase_table to detail the behavior
    of each phase in the experiment.

    Attributes:
    ----------
    timeout_ms: int | None
        The timeout after which the next action triggers (in milliseconds). If None, no timer is set.
    on_enter: OnEnterFn | None
        The method to call when entering this phase. If None, no method is called.
    decide: DecisionFn | None
        A function that decides the next phase based on the current state of the experiment manager.
    routes: Mapping[Any, Phase] | None
        A mapping of keys to phases, defining the possible transitions from this phase.
    next_phase_default: Phase | None
        The default phase to transition to if no other route matches.
    relevant_pins: ANY_PIN, None,  Mapping[str, Edge],
        A list of pin names that are relevant for this phase. If ANY_PIN, any pin is relevant.
        If None, no pins are considered relevant. Irrelevant pins are ignored.
    """
    on_enter: OnEnterFn | None = None  # method name
    next_phase_default: Phase | None = None  # fallback next phase if timer hit or no decide
    routes: Mapping[Any, Phase] | None = field(default_factory=dict)  # Map from e.g. bool or str to Phase spec
    timeout_ms: int | None = None  # default, None → no timer
    decide: DecisionFn | None = None
    relevant_pins: RelevantPins = ANY_PIN  # by default, ANY_PIN means any pin is relevant,

    # None means ignore all pins
    # FIXME: prefer properties instead maybe ?
    def set_timeout(self, value):
        if value is None or value < 0:
            return
        self.timeout_ms = value


# Grab the actual Qt metaclass that PyQt uses
QtMeta = type(QtCore.QObject)


class QABCMeta(QtMeta, ABCMeta):
    """Metaclass that satisfies both Qt (QObject) and ABC requirements."""
    pass


class BaseExperimentManager(QtCore.QObject, metaclass=QABCMeta):
    """
    Abstract base class for all experiment managers.

    This class defines the basic structure and functionality for managing experiments.
    It notably handles:
    - The state machine for the experiment phases
    - The communication with the BeatBox hardware (payload handling)
    - logging and signal handling
    """
    stage_number = None  # to be set in subclasses

    # shared signals
    pelletRetrieved = QtCore.Signal()  # GUI may connect
    sensorsStateChanged = QtCore.Signal(list)  # low-byte, LSB-first list[bool]
    actuatorsStateChanged = QtCore.Signal(list)  # hi/lo bytes
    setPayloadReady = QtCore.Signal(list, bool)  # final 2-byte mask and flush flag
    nextTrialRequested = QtCore.Signal()

    stageEnded = QtCore.Signal(str)  # stage name
    phaseChanged = QtCore.Signal(str)  # Emitted with the new phase name
    stageChanged = QtCore.Signal(str)  # Emitted with the new stage name
    pelletDeliveryAlert = QtCore.Signal(int)  # Emitted with the number of tries for pellet delivery

    def __init__(self, experiment_folder: str | Path, communicator: BeatBoxCommunicator,
                 main_window: QtWidgets.QMainWindow = None,
                 actuator_pins_cfg: PinConfig = None,
                 auto_start: bool = False, subject_info: SubjectInfo = None) -> None:
        QtCore.QObject.__init__(self)
        self.communicator = communicator
        self.communicator.stateValueReceived.connect(self._on_state_value)
        if actuator_pins_cfg is None:
            actuator_pins_cfg = load_pin_config('actuators')
        self.actuator_pins_cfg = actuator_pins_cfg
        self.sensors_pins_config = load_pin_config('sensors')

        self._out_word = self.actuator_pins_cfg.default_word()

        self.main_window = main_window
        self.experiment_folder = experiment_folder
        self.subject_info = subject_info
        self._last_state_value = 0
        self.reward_duration = 150  # time reward pin stays high in ms  # TODO: parametrize
        self.trial_no = 0
        self.waiting_for_pellet_delivery = False
        self.pellet_delivered = False
        self.n_pellets_retrieved = 0  # also the current trial number
        self.waiting_for_pellet_retrieval = False  # whether the manager is waiting for a pellet to be retrieved
        self.pellet_delivery_loop_count = 0

        # the config Dictionary read from the config file
        self.config = SConfig()  # will be set in set_experiment_folder()

        self.logger = None
        self.csv_logger = None
        self.light_logger = None

        if experiment_folder:
            self.set_experiment_folder(experiment_folder)
        # Use the bus to aggregate payloads within a small time window (merge_ms) and send them to the hardware.
        self.bus = SetPayloadOutputBus(merge_ms=5, parent=self)
        self.setPayloadReady.connect(self.bus.post)
        self.bus.sendMessage.connect(self.communicator.send_set_payload)

        self.emit_payload_ready(self.actuator_pins_cfg.default_mask(), flush=True)  # Enforce default state

        # Phase management
        self._current_phase = None
        self._timer = QtCore.QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._on_timeout)

        if auto_start:
            self.__post_init_phases()  # Start the state machine (manages the event sequence)

    def handshake_completed(self):
        # Set output to know state after handshake
        self.emit_payload_ready(self.actuator_pins_cfg.default_mask(), flush=True)

    def start_experiment(self):
        self.log_subject_info()
        self.start_state_machine()

    def end_experiment(self):
        if self._timer.isActive():
            self._timer.stop()  # No need to reset counter as timer.start(timeout_ms) will do it
        self._phase_iti()  # reset to a known state
        self.log(logging.INFO, 'Experiment ended.')
        if hasattr(self, 'logger') and isinstance(self.logger, ExperimentCSVLogger):
            self.logger.close()

    def _setup_logging(self):
        self.logger = logging.getLogger(f'ExperimentManagerS{self.stage_number}')
        self.logger.setLevel(logging.INFO)
        self.propagate = True

    def _setup_file_logging(self, with_light_logger: bool = True):
        self.csv_logger = ExperimentCSVLogger(
            experiment_folder=self.experiment_folder,
            stage_number=self.stage_number,
            sensor_pin_names=self.sensors_pins_config.pin_names,
            subject_info=self.subject_info
        )
        if with_light_logger:
            self.light_logger = LightExperimentCSVLogger(
                experiment_folder=self.experiment_folder,
                stage_number=self.stage_number,
                subject_info=self.subject_info
            )

    def log(self, level, message):
        """ Basic method to log a message with the given level.
        This method can be overridden in subclasses to implement custom logging behavior.
        """
        if self.logger:
            self.logger.log(level, message)

    def log_subject_info(self):
        if self.subject_info is not None:
            self.log(logging.INFO, f"Subject Info: ID={self.subject_info.subject_id}, "
                                   f"Weight={self.subject_info.weight}g, "
                                   f"Box={self.subject_info.box}, "
                                   f"Sex={self.subject_info.sex}, "
                                   f"Light Cycle={self.subject_info.light_cycle}")

    # ##################################  STATE MACHINE  ##################################

    def __post_init_phases(self):
        if hasattr(self, "_phase_table"):
            self.start_state_machine()
        else:
            warnings.warn(f"{self.__class__.__name__} does not have a _phase_table defined. "
                          "Please define it in the subclass to start the state machine.")

    def start_state_machine(self):
        # IMPROVEMENT: differentiate starting the state machine and experiment initialization
        if not self._phase_table:
            raise ValueError("Phase table is not defined.")
        for phase, spec in self._phase_table.items():
            if spec.timeout_ms is None and spec.decide is None:
                raise ValueError(f"Phase {phase} of {self.__class__.__name__} has neither timeout nor decide function. "
                                 f"This would create a deadlock and is therefore prohibited.")
        self.log(logging.INFO, f"Starting state machine for {self.__class__.__name__} with {self._phase_table_initial}")
        self.stageChanged.emit(str(self.config.s_level))  # Emit the stage change signal
        self._goto(self._phase_table_initial)
        self.phaseChanged.emit(str(phase))

    @final
    def _goto(self, phase: Phase, *, extra_ms: int = 0):
        """Low-level phase switch (DO NOT override)."""
        self.log(logging.INFO, f'Starting phase "{phase}"')
        self._current_phase = phase
        spec = self._phase_table[phase]

        # start / stop timer
        if spec.timeout_ms is not None:
            self._timer.start(spec.timeout_ms + extra_ms)
        else:
            self._timer.stop()

        # NOTE: this needs to come after timer start to be able to cancel it in the entry action if needed
        if spec.on_enter is not None:  # Entry action
            # spec.on_enter(self)
            spec.on_enter()

        self.phaseChanged.emit(str(phase))  # Emit the phase change signal

    def _on_timeout(self):
        """Handle phase timeout."""
        self._advance(timed_out=True)

    def _advance(self, timed_out=False):
        """Evaluate routing rules and move to next phase if needed."""
        spec = self._phase_table[self._current_phase]

        if spec.decide and not timed_out:
            key = spec.decide(self)
            # FIXME: crashes if next_phase_default is None
            # This can happen if decide returns a key not in routes and no default is set
            target = spec.routes.get(key, spec.next_phase_default)
        else:
            target = spec.next_phase_default

        # if target and target != self._current_phase:
        self._goto(target)

    # ##################################  PAYLOAD HANDLING  ##################################

    def emit_payload_ready(self, payload: list[int], flush: bool = False):
        """
        Emit the setPayloadReady signal with the given payload and flush flag.
        This method is used to send the payload to the BeatBox hardware.
        """
        self.setPayloadReady.emit(payload, flush)

    @QtCore.Slot(str, bool)
    def request_manual_action(self, name: str, on: bool):
        """
        GUI asks to toggle an output. The manager decides whether that
        is allowed right now and builds the mask.
        """
        self.set_outputs(on=[name], flush=False) if on else self.set_outputs(off=[name], flush=False)

    def request_manual_action_by_pins_and_rgb(self, checked_pins: list[bool], rgb_values: tuple[int, int, int]):
        """
        GUI asks to toggle multiple outputs with RGB colors.
        The manager builds the mask and emits it.
        """
        bitmask = sum(2 ** i for i, checked in enumerate(checked_pins) if checked)

        payload = [bitmask >> 8, bitmask & 0xff]

        if rgb_values is not None:
            payload.extend(rgb_values)
        self.emit_payload_ready(payload)

    def set_outputs(self, on: List[str] = (), off: List[str] = (), flatten: bool = False, flush: bool = False,
                    default: bool = False) -> None:
        """
        Set the outputs specified in `on` to high and those in `off` to low.
        If `flatten` is True, all other outputs are set to low.
        If `default` is True, all other outputs are set to their default value defined in config file.
        If `default` and `flatten` both are True, then `default` is ignored.

        Parameters
        ----------
        on : List[str]
            List of output names to set high.
        off : List[str]
            List of output names to set low.
        flatten : bool
            If True, all outputs not in `on` are set to low.
        flush : bool
            If True, the payload is sent immediately to the hardware.
        default : bool
            If True, the default pin configuration is taken as basis.
        """
        word = 0 if flatten else self.actuator_pins_cfg.default_word() if default else self._out_word
        to_bit_idx = self.actuator_pins_cfg.bit_idx
        for name in on:
            word |= (1 << to_bit_idx(name))
        for name in off:
            word &= ~(1 << to_bit_idx(name))

        self._out_word = word
        self.emit_payload_ready(self.actuator_pins_cfg.mask_from_word(word), flush)

    def _deliver_reward(self):
        self.set_outputs(on=['reward'], flatten=False, flush=False)
        QtCore.QTimer.singleShot(self.reward_duration, self._stop_reward_delivery)

    def _stop_reward_delivery(self):
        self.set_outputs(flatten=True, flush=True)

    def turn_off_screens(self):
        self.set_outputs(on=['left_Black_pattern', 'right_Black_pattern'], flatten=True, flush=False)

    # ##################################  INPUT HANDLERS  ##################################

    @QtCore.Slot(int)
    def _on_state_value(self, state_word: int):
        state_value = state_word & 0xFF
        self._last_state_value = state_value  # Store for logging
        sensor_bits = self._get_sensor_bits_from_state_value(state_value)
        self.sensorsStateChanged.emit(sensor_bits)
        # self.log(logging.INFO, f'State value: {state_value:#04x} ({state_value:03d}); state bits, {", ".join(str(bit) for bit in sensor_bits)}', state_bits=sensor_bits)
        changed_pins = {pin_name: bool(pin_on) for pin_name, pin_on in
                        zip(self.sensors_pins_config.pin_names, sensor_bits)
                        if bool(pin_on) != self.sensors_pins_config._current_states[pin_name]}
        self.log(logging.DEBUG, f'Changed pins: {changed_pins}', state_bits=sensor_bits)
        for pin_name, bit_state in changed_pins.items():
            self._handle_input_pin_changed(pin_name, bit_state)

    def _get_sensor_bits_from_state_value(self, state_value: int) -> list[int]:
        sensor_bits = [(state_value >> i) & 0x01 for i in range(len(self.sensors_pins_config.pin_names))]
        return sensor_bits

    def _handle_input_pin_changed(self, pin_name: str, bit: bool):
        """
        Handle a change in the state of an input pin.
        This method updates the current state of the pin and dispatches a changed pin event.
        The user should override _dispatch_changed_pin to handle specific pin changes.
        """
        prev_state = self.sensors_pins_config._current_states[pin_name]
        self.sensors_pins_config._current_states[pin_name] = bit
        if self._current_phase is None:  # Before program start
            return

        relevant_pins = self._phase_table[self._current_phase].relevant_pins
        if relevant_pins is None:  # If no relevant pins are defined, ignore all pin changes
            return
        elif relevant_pins is ANY_PIN:
            self._dispatch_changed_pin(pin_name, Edge.BOTH)
            self._advance()
            return
        elif pin_name not in relevant_pins.keys():  # some but not this one
            return
        else:  # a monitored pin
            edge = Edge.RISING if bit and not prev_state else Edge.FALLING if not bit and prev_state else None
            if edge is None:  # TODO: check when this happens
                raise ValueError("Input change triggered with same pin state")
                self._dispatch_changed_pin(pin_name, None)
            else:
                expected_edge = relevant_pins[pin_name]
                # if expected edge can be both we don't need edge direction
                if expected_edge in (edge, Edge.BOTH):
                    self._dispatch_changed_pin(pin_name, edge)
                    self._advance()
                    return
                else:
                    return

    def set_experiment_folder(self, experiment_folder):
        """
        Set the experiment folder for logging and load the configuration.
        This method should be overridden in subclasses to handle specific configurations.
        """

        self.experiment_folder = experiment_folder
        Path(experiment_folder).mkdir(parents=True, exist_ok=True)
        Path(experiment_folder).joinpath('config').mkdir(parents=True, exist_ok=True)

    # ############################ TEMPLATE METHODS ############################
    def _dispatch_changed_pin(self, pin_name: str, edge: Edge):
        """
        Dispatch a changed pin event along with the edge, i.e. the direction of the change.
        This method should be overridden in subclasses to handle specific pin changes.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def handle_pellet_retrieved(self):
        """
        Handle the pellet retrieval event.
        This method should be overridden in subclasses to handle specific pellet retrieval logic.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def handle_n_trials_changed(self, value):
        ...

    def handle_inter_trial_interval_changed(self, value):
        ...

    ############################# CSV loggers #############################


class ExperimentCSVLogger:
    def __init__(self, experiment_folder, stage_number, sensor_pin_names, subject_info=None):
        self.start_time = time.time()
        self.timestamp = datetime.now().isoformat()
        self.timestamp = self.timestamp.replace(":", "-")  # replace colons for filename compatibility
        if subject_info is not None:
            subject_id = subject_info.subject_id
            log_filename = f"FULL_{subject_id}_S{stage_number}_{self.timestamp}.csv"
        else:
            log_filename = f"FULL_S{stage_number}_{self.timestamp}.csv"
        self.file_path = Path(experiment_folder) / log_filename
        self.sensor_pin_names = sensor_pin_names
        self.file = open(self.file_path, "w", newline="")
        self.writer = csv.writer(self.file)
        header = [
                     "date", "timestamp_s", "stage", "log_level", "message",
                     "correct_side", "total_trials", "trial_number", "corrective_loop",
                     "left_performance", "right_performance"
                 ] + [f"state_{name}" for name in sensor_pin_names]
        self.writer.writerow(header)

    def log(self, stage, log_level, message, total_trials, trial_number, correct_side,
            corrective_loop, left_performance, right_performance, state_bits):
        timestamp_s = f"{(time.time() - self.start_time):.3f}"
        date = datetime.now().isoformat()
        row = [
                  date, timestamp_s, stage, log_level, message,
                  correct_side, total_trials, trial_number, corrective_loop,
                  left_performance, right_performance
              ] + list(state_bits)
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


# FIXME: this is a bit redundant with ExperimentCSVLogger, we could merge them and have a single logger with more flexible logging method
# FIXME: this mainly used by S2 and S3 so it should be defined there
# Maybe make an abstract CSV logger class that should be implemented in S managers?
class LightExperimentCSVLogger:
    def __init__(self, experiment_folder, stage_number, subject_info: SubjectInfo = None):
        self.start_time = time.time()
        self.timestamp = datetime.now().isoformat()
        self.timestamp = self.timestamp.replace(":", "-")  # replace colons for filename compatibility
        if subject_info is not None:
            subject_id = subject_info.subject_id
            log_filename = f"{subject_id}_S{stage_number}_{self.timestamp}.csv"
        else:
            log_filename = f"S{stage_number}_{self.timestamp}.csv"
        self.file_path = Path(experiment_folder) / log_filename
        self.file = open(self.file_path, "w", newline="")
        self.writer = csv.writer(self.file)
        header = ["date", "timestamp", "total_trials", "correct_side", "response", "reward", "correct",
                  "incorrect", "valid", "corrective_loop", "overall_perf", "perf_left", "perf_right"
                  ]
        self.writer.writerow(header)

    # Reward is just a bool and should be true when we transition to another trial
    # call this method at the end of each trial/when there is a response
    def log(self, trial_number, correct_side, response_side, reward, correct,
            incorrect, valid, corrective_loop, overall_performance, left_performance, right_performance):
        date = datetime.now().isoformat()
        timestamp_s = f"{(time.time() - self.start_time):.3f}"
        row = [
            date, timestamp_s, trial_number, correct_side, response_side, reward, correct,
            incorrect, valid, corrective_loop, overall_performance, left_performance, right_performance
        ]
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()
