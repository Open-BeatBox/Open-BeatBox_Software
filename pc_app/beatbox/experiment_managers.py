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
from abc import ABCMeta
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Any, Mapping, List, Final, Union, final

from configobj import ConfigObj
from qtpy import QtCore, QtWidgets

from beatbox.serial_com import BeatBoxCommunicator, SetPayloadOutputBus
from beatbox.utils import PinConfig, load_pin_config


ANY_PIN: Final = object()

class Edge(Enum):
    RISING  = 'rising'
    FALLING = 'falling'
    BOTH    = 'both'


RelevantPins = Union[
    ANY_PIN,
    None,                   # ignore all pins
    Mapping[str, Edge],     # pin -> required edge
]


class Phase(Enum):
    """
    Enum representing the different phases of an experiment.
    Each phase corresponds to a specific state in the experiment flow.
    Add new phases here as needed for the experiment.
    """
    ITI         = auto()
    WAIT_TUNNEL = auto()
    WAIT_PELLET = auto()
    SHOW_STIM   = auto()
    WAIT_TOUCH  = auto()
    CORRECT_RESPONSE = auto()
    INCORRECT_RESPONSE = auto()
    STOP_FLASH = auto()  # used to stop flashing the error light
    DONE = auto()


DecisionFn = Callable[['BaseExperimentManager'], Any]   # returns a key for the dict
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
    on_enter:   OnEnterFn | None           = None           # method name
    next_phase_default: Phase | None       = None          # fallback next phase if timer hit or no decide
    routes:     Mapping[Any, Phase] | None = field(default_factory=dict)   # Map from e.g. bool or str to Phase spec
    timeout_ms: int | None                 = None          # default, None → no timer
    decide: DecisionFn | None          = None
    relevant_pins: RelevantPins   = ANY_PIN # by default, ANY_PIN means any pin is relevant,
                                                             # None means ignore all pins

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
    pelletRetrieved = QtCore.Signal()          # GUI may connect
    sensorsStateChanged = QtCore.Signal(list)  # low-byte, LSB-first list[bool]
    actuatorsStateChanged = QtCore.Signal(list)  # hi/lo bytes
    setPayloadReady = QtCore.Signal(list, bool)  # final 2-byte mask and flush flag
    trialStarted = QtCore.Signal(int)  # WARNING: unbound
    nextTrialRequested = QtCore.Signal()


    def __init__(self, experiment_folder: str | Path, communicator: BeatBoxCommunicator,
                 n_trials: int = 20, inter_trial_interval: int = 10000,
                 main_window: QtWidgets.QMainWindow = None,
                 actuator_pins_cfg: PinConfig = None,
                 auto_start: bool = False):
        QtCore.QObject.__init__(self)
        self.communicator = communicator
        self.communicator.stateValueReceived.connect(self._on_state_value)
        if actuator_pins_cfg is None:
            actuator_pins_cfg = load_pin_config('actuators')
        self.actuator_pins_cfg = actuator_pins_cfg
        self.sensors_pins_config = load_pin_config('sensors') # TODO: argument

        self._out_word = self.actuator_pins_cfg.default_word()

        self.experiment_folder = experiment_folder
        self.n_trials = n_trials
        self.inter_trial_interval = inter_trial_interval
        self.reward_duration = 150  # time reward pin stays high in ms  # TODO: parametrize
        self.main_window = main_window
        self._n_pellets_retrieved = 0
        self.waiting_for_pellet_retrieval = False  # whether the manager is waiting for a pellet to be retrieved
        self.last_pellet_retrieved = False
        self.config = ConfigObj()  # will be set in set_experiment_folder()
        if experiment_folder:
            self.set_experiment_folder(experiment_folder)

        self.logger = None

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
        self.start_state_machine()

    def end_experiment(self):
        # TODO: close file handler ? set new file logging, log summary experiment infos
        if self._timer.isActive():
            self._timer.stop()
        self._timer = None # reset any ongoing timer
        self._phase_iti() # reset to a known state
        self.log(logging.INFO, 'Experiment ended.')

    def _setup_file_logging(self):
        log_dir = Path(self.experiment_folder)
        log_path = log_dir / 'experiment_status.csv'

        self.logger = logging.getLogger(f'ExperimentManagerS{self.stage_number}')
        self.logger.setLevel(logging.INFO)

        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s; %(levelname)s; %(message)s')
        file_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.propagate = True

    def log(self, level, message):
        if self.logger:
            self.logger.log(level, message)

    # ##################################  STATE MACHINE  ##################################

    def __post_init_phases(self):
        if hasattr(self, "_phase_table"):
            self.start_state_machine()
        else:
            warnings.warn(f"{self.__class__.__name__} does not have a _phase_table defined. "
                          "Please define it in the subclass to start the state machine.")

    def start_state_machine(self):
        # IMPROVEMENT: differentiate starting the state machine experiment initialization
        if not self._phase_table:
            raise ValueError("Phase table is not defined.")
        for phase, spec in self._phase_table.items():
            if spec.timeout_ms is None and spec.decide is None:
                raise ValueError(f"Phase {phase} of {self.__class__.__name__} has neither timeout nor decide function. "
                                 f"This would create a deadlock and is therefore prohibited.")
        self.trial_no = 0
        self.log(logging.INFO, f"Starting state machine for {self.__class__.__name__} with {self._phase_table_initial}")
        self._goto(self._phase_table_initial)

    @final
    def _goto(self, phase: Phase, *, extra_ms: int = 0):
        """Low-level phase switch (DO NOT override)."""
        self.log(logging.INFO, f'Starting phase "{phase}"')
        self._current_phase = phase
        spec = self._phase_table[phase]

        if spec.on_enter is not None:  # Entry action
            #spec.on_enter(self)
            spec.on_enter()

        # start / stop timer
        if spec.timeout_ms is not None:
            self._timer.start(spec.timeout_ms + extra_ms)
        else:
            self._timer.stop()

    def _on_timeout(self):
        """Handle phase timeout."""
        self._advance()

    def _advance(self):
        """Evaluate routing rules and move to next phase if needed."""
        spec = self._phase_table[self._current_phase]

        if spec.decide:
            key = spec.decide(self)
            target = spec.routes.get(key, spec.next_phase_default)
        else:
            target = spec.next_phase_default

        if target and target != self._current_phase:
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
        bitmask = sum(2**i for i, checked in enumerate(checked_pins) if checked)

        payload = [bitmask >> 8, bitmask & 0xff]

        if rgb_values is not None:
            payload.extend(rgb_values)
        self.emit_payload_ready(payload)

    def set_outputs(self, on: List[str] = (), off: List[str] = (), flatten: bool = False, flush: bool = False) -> None:
        """
        Set the outputs specified in `on` to high and those in `off` to low.
        If `flatten` is True, all other outputs are set to low.

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
        """
        word = self.actuator_pins_cfg.default_word() if flatten else self._out_word
        to_bit_idx = self.actuator_pins_cfg.bit_idx
        for name in on:
            word |= (1 << to_bit_idx(name))
        for name in off:
            word &= ~(1 << to_bit_idx(name))

        self._out_word = word
        self.emit_payload_ready(self.actuator_pins_cfg.mask_from_word(word), flush)

    # ##################################  INPUT HANDLERS  ##################################

    @QtCore.Slot(int)
    def _on_state_value(self, state_word: int):
        """
        Decode MCU state; subclasses override *only* what changes.
        """
        state_value = state_word & 0xFF  # ignore upper byte
        # state_value = (state_word[0] << 8) | state_word[1]  # Decode the payload into a single integer.
        sensor_bits = [(state_value >> i) & 0x01
                       for i in range(len(self.sensors_pins_config.pin_names))]
        self.sensorsStateChanged.emit(sensor_bits)
        self.log(logging.DEBUG, f'State value: {state_value:#04x} ({state_value:03d}), state bits: {sensor_bits}')

        changed_pins = {pin_name: bool(pin_on) for pin_name, pin_on in zip(self.sensors_pins_config.pin_names, sensor_bits)
                        if bool(pin_on) != self.sensors_pins_config._current_states[pin_name]}
        self.log(logging.DEBUG, f'Changed pins: {changed_pins}')
        for pin_name, bit_state in changed_pins.items():
            self._handle_input_pin_changed(pin_name, bit_state)

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
            self._dispatch_changed_pin(pin_name)
            self._advance()
            return
        elif pin_name not in relevant_pins.keys():  # some but not this one
            return
        else:  # a monitored pin
            edge = Edge.RISING if bit and not prev_state else Edge.FALLING if not bit and prev_state else None
            if edge is None:  # TODO: check when this happens
                raise ValueError("Input change triggered with same pin state")
                self._dispatch_changed_pin(pin_name)
            else:
                expected_edge = relevant_pins[pin_name]
                # if expected edge can be both we don't need edge direction
                if expected_edge in (edge, Edge.BOTH):
                    self._dispatch_changed_pin(pin_name)
                    self._advance()
                    return
                else:
                    return

    # ############################ TEMPLATE METHODS ############################
    def _dispatch_changed_pin(self, pin_name: str):
        """
        Dispatch a changed pin event.
        This method should be overridden in subclasses to handle specific pin changes.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def set_experiment_folder(self, experiment_folder):
        """
        Set the experiment folder and load the configuration.
        This method should be overridden in subclasses to handle specific configurations.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def handle_pellet_retrieved(self):
        """
        Handle the pellet retrieval event.
        This method should be overridden in subclasses to handle specific pellet retrieval logic.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def handle_n_trials_changed(self, value):...
    def handle_inter_trial_interval_changed(self, value): ...

    def _read_or_write_config(self, key: str, attribute: str = ''):
        """
        Read or write a configuration value for the experiment.

        If the key exists in the config, set the attribute to its value.
        If the key does not exist, set the config value to the attribute's value.

        Parameters
        ----------
        key : str
            The key in the configuration dictionary.
        attribute : str
            The attribute name to set or get the value from.
            If not provided, it is assumed to be the same as the key.
        """
        if not attribute:
            attribute = key
        if val:= self.config.get(key):
            setattr(self, attribute, val)
        else:
            self.config[key] = getattr(self, attribute)


# ######################################################################
# S level manager classes. These are the actual experiment managers
# ######################################################################

class BeatBoxExperimentManagerS1(BaseExperimentManager):
    """
    Stage 1 experiment manager.
    This is the first concrete implementation of the BaseExperimentManager.
    It handles the basic functionality of the experiment, such as starting trials,
    retrieving pellets, and managing the experiment configuration.

    The skeleton of the state machine is defined in the _phase_table_initial and _phase_table attributes.
    """
    stage_number = 1

    def __init__(self, experiment_folder, communicator, n_trials=20, inter_trial_interval=10000,
                 main_window=None, actuator_pins_cfg: PinConfig = None):
        super().__init__(experiment_folder, communicator, n_trials, inter_trial_interval, main_window, actuator_pins_cfg)


        # ----------------- phase table -----------------------------------
        self._phase_table_initial = Phase.ITI
        self._phase_table = {
            Phase.ITI: PhaseSpec(
                on_enter=self._phase_iti,
                next_phase_default=Phase.WAIT_PELLET,
                timeout_ms=self.inter_trial_interval,
                relevant_pins=None
            ),

            Phase.WAIT_PELLET: PhaseSpec(
                on_enter=self._phase_wait_pellet_retrieval,
                routes={True: Phase.ITI},  # go back to ITI
                decide=lambda instance: instance.last_pellet_retrieved,
                relevant_pins={'nose_poke': Edge.RISING}  # FIXME: set proper edge,
            ),
        }

    def _phase_iti(self):
        # turn everything off
        self.set_outputs(flatten=True, flush=True)  # Force defaults
        self.waiting_for_pellet_retrieval = False
        self.last_pellet_retrieved = False

    def _phase_wait_pellet_retrieval(self):
        self.waiting_for_pellet_retrieval = True
        self.trial_no += 1  #  TODO: check if here or part of handle_pellet_retrieved  # FIXME: part of pellet retrieval
        self.last_pellet_retrieved = False

        payload = self.actuator_pins_cfg.mask()  # screens off
        self.emit_payload_ready(payload)
        self.trialStarted.emit(self.trial_no)

        self.log(logging.DEBUG, f'Starting trial {self.n_pellets_retrieved + 1} of {self.n_trials}')

    # called from _dispatch_changed_pin -------------------------------
    def handle_pellet_retrieved(self):
        if not self.waiting_for_pellet_retrieval:
            self.log(logging.WARNING, 'Pellet retrieved, but not waiting for it.')
            return
        self.waiting_for_pellet_retrieval = False
        self.n_pellets_retrieved += 1
        self.last_pellet_retrieved = True
        self.pelletRetrieved.emit()

    def _dispatch_changed_pin(self, pin_name: str):
        if pin_name == 'nose_poke':  # REFACTOR: we could use e.g. a dataclass to avoid string mismatches
            self.handle_pellet_retrieved()

    def set_experiment_folder(self, experiment_folder):
        self.experiment_folder = experiment_folder
        Path(experiment_folder).mkdir(parents=True, exist_ok=True)
        config_file_path = Path(experiment_folder) / 'config.cfg'
        self.config = ConfigObj(str(config_file_path), encoding='utf-8', unrepr=True)
        self.parse_experiment_config()
        self._setup_file_logging()

    def parse_experiment_config(self):
        self._read_or_write_config('n_trials')
        self._read_or_write_config('inter_trial_interval')
        # FIXME: should we send a signal to the main window to update the UI instead of setting it directly?
        self.main_window.n_trials_spinbox.setValue(self.n_trials)
        self.main_window.n_trials_spinbox.valueChanged.connect(self.handle_n_trials_changed)
        self.main_window.inter_trial_spinbox.setValue(self.inter_trial_interval)
        self.main_window.inter_trial_spinbox.valueChanged.connect(self.handle_inter_trial_interval_changed)

    def _mask(self, *names: str) -> list[int]:
        """
        Wrapper around ``PinConfig.mask`` so event-handlers can build the
        two-byte Beat‐Box payload from **cfg names** (‘reward’, ‘left_pattern_1’ …)
        without touching PinConfig directly.
        """
        return self.actuator_pins_cfg.mask(*names)

    def handle_n_trials_changed(self, value):
        self.n_trials = value
        self.config['n_trials'] = value
        self.config.write()

    def handle_inter_trial_interval_changed(self, value):
        self.inter_trial_interval = value
        self.config['inter_trial_interval'] = value
        self.config.write()

    @property
    def n_pellets_retrieved(self):
        return self._n_pellets_retrieved

    @n_pellets_retrieved.setter
    def n_pellets_retrieved(self, value):
        if value < self.n_trials:
            self._n_pellets_retrieved = value
        else:
            dlg = QtWidgets.QMessageBox()
            dlg.setText(  # FIXME: end experiment, see how we could instanciate the next stage
                f"""All pellets have been retrieved.
                The stage S{self.stage_number} experiment is now complete.
                You can now move to S{self.stage_number + 1}."""
            )


class BeatBoxExperimentManagerS2(BeatBoxExperimentManagerS1):
    """
    Stage 2 experiment manager.
    Inherits from BeatBoxExperimentManagerS1 and adds functionality

    for handling the left and right screens, patterns, and responses.

    The experiment logic is defined in the _phase_table attribute,
    which specifies the link between phases, timeouts, and actions to take on entering each phase.
    """
    stage_number = 2
    error_flash_ms = 200

    def __init__(self, experiment_folder, communicator, n_trials=20,
                 inter_trial_interval=10000, error_trial_interval=200,
                 main_window=None, actuator_pins_cfg: PinConfig=None):
        super().__init__(experiment_folder, communicator, n_trials, inter_trial_interval, main_window, actuator_pins_cfg)
        self.error_trial_interval = error_trial_interval
        self.learning_threshold = 0.8
        self.responses = {'correct': [], 'incorrect': []}
        self.performance = []
        self.tunnel_entered = False  # whether the tunnel has been entered
        self.touch_result = None  # 'correct', 'incorrect', or None
        self.reward_ms = 20000   # FIXME: random, TODO: parametrize

        # Exactly alternating sides for the screens
        self.screen_sides = ['right' if i % 2 else 'left' for i in range(1, n_trials + 1)]
        self.screen_pattern_names = {
            'left': ['left_pattern_2', 'right_pattern_3'],
            'right': ['left_pattern_3', 'right_pattern_2']
        }

        self._phase_table_initial = Phase.ITI
        self._phase_table = {
            Phase.ITI: PhaseSpec(
                on_enter=self._phase_iti,
                next_phase_default=Phase.WAIT_TUNNEL,
                timeout_ms=self.inter_trial_interval,
                relevant_pins=None
            ),

            Phase.WAIT_TUNNEL: PhaseSpec(
                on_enter=self._phase_wait_tunnel,
                routes={True: Phase.SHOW_STIM},
                decide=lambda instance: instance.tunnel_entered,  # bool attr set by handle_tunnel_crossing_in()
                relevant_pins={'left_tunnel_crossing': Edge.RISING,
                               'right_tunnel_crossing': Edge.RISING} # FIXME: set proper edges
            ),

            Phase.SHOW_STIM: PhaseSpec(
                on_enter=self._phase_show_stim,
                next_phase_default=Phase.WAIT_TOUCH,
                timeout_ms=2000,
                relevant_pins=None
            ),

            Phase.WAIT_TOUCH: PhaseSpec(
                routes={'correct': Phase.CORRECT_RESPONSE,
                        'incorrect': Phase.INCORRECT_RESPONSE},
                decide=lambda instance: instance.get_touch_valence(),  # 'correct'/'incorrect'/None
                relevant_pins= {'left_response': Edge.RISING,
                                'right_response': Edge.RISING}  # FIXME: set proper edges
            ),

            Phase.CORRECT_RESPONSE: PhaseSpec(
                on_enter=self._handle_correct_response,
                next_phase_default=Phase.WAIT_PELLET,
                timeout_ms=0,  # immediate  # WARNING: maybe put self.merge_ms here as a minimum delay to ensure the payload is sent
                relevant_pins= None
            ),
            Phase.INCORRECT_RESPONSE: PhaseSpec(
                on_enter=self._handle_incorrect_response,
                next_phase_default=Phase.STOP_FLASH,
                timeout_ms=self.error_flash_ms,
                relevant_pins=None
            ),
            Phase.WAIT_PELLET: PhaseSpec(
                on_enter=self._phase_wait_pellet_retrieval,
                routes={True: Phase.ITI},  # go back to ITI
                decide=lambda instance: instance.last_pellet_retrieved,
                next_phase_default=Phase.ITI,
                timeout_ms=self.reward_ms,  # TODO: check what logic we want here
                relevant_pins={'nose_poke': Edge.RISING}
            ),
            Phase.STOP_FLASH: PhaseSpec(
                on_enter=self.stop_flash,
                next_phase_default=Phase.ITI,
                timeout_ms=0,
                relevant_pins=None
            ),
        }

    def get_touch_valence(self):
        return self.touch_result[0] if self.touch_result is not None else None

    def _phase_iti(self):
        # turn everything off
        self.set_outputs(flatten=True, flush=True)

    def _phase_wait_tunnel(self):
        self.tunnel_entered = False  # reset flag

    def _phase_show_stim(self):
        self.touch_result = None
        names = self.screen_pattern_names[self.get_screen_side()]
        self.set_outputs(on=names, flatten=False, flush=False)

    def _deliver_reward(self):  # TODO: check if this shouldn't be in the base class
        self.set_outputs(on=['reward'], flatten=True, flush=True)

    def _flash_error(self):
        self.set_outputs(on=['light'], flatten=False, flush=False)
        self.actuatorsStateChanged.emit(self._mask('light'))               # tell GUI to show

    def stop_flash(self):
        self.set_outputs(off=['light'], flatten=False, flush=False)
        self.actuatorsStateChanged.emit(self._mask())  # tell GUI to show  # TODO: check

    def _handle_screen_touch(self):
        response_type,  pin_name = self.touch_result
        self.touch_result = None  # reset touch result
        self.responses[response_type].append(pin_name)  # TODO: check why pin_name is needed here
        log_levels = {
            'correct': logging.INFO,
            'incorrect': logging.WARNING
        }
        self.log(log_levels[response_type], f"{response_type.title()} response on {pin_name}.")

    def _handle_correct_response(self):
        """Turn screens off, drop pellet, wait ITI, start next trial."""
        self._handle_screen_touch()
        self._deliver_reward()

    def _handle_incorrect_response(self):
        """Flash white, then wait the **same** ITI before retrying."""
        self._handle_screen_touch()
        self._flash_error()  # flash white

    def handle_tunnel_crossing_in(self):
        """
        First IR-beam break inside the tunnel.
        """
        self.tunnel_entered = True
        # TODO: update the GUI buttons
        # if left_on: self.left_buttons[1] = True  # Display pattern 1
        # else: self.left_buttons[2] = True  # Display pattern 2

        # if right_on: self.right_buttons[1] = True
        # else: self.right_buttons[2] = True

    def handle_tunnel_crossing_out(self):  # FIXME: not used
        """Nothing changes visually; simply keep the same buttons lit."""
        side = self.get_screen_side()

        # TODO: update the GUI buttons
        # if side == 'left': self.left_buttons[1] = True

    def handle_screen_touch(self, pin_name):
        is_correct = pin_name.startswith(self.get_screen_side())
        self.touch_result = ('correct' if is_correct else 'incorrect', pin_name)

    def _dispatch_changed_pin(self, pin_name: str):
        super()._dispatch_changed_pin(pin_name)  # call the base class method first
        match pin_name:
            case 'nose_poke':
                self.handle_pellet_retrieved()
            case 'left_tunnel_crossing':
                self.handle_tunnel_crossing_in()
            case 'right_tunnel_crossing':
                self.handle_tunnel_crossing_out()
            case 'left_response' | 'right_response':
                self.handle_screen_touch(pin_name)

    def get_screen_side(self):
        return self.screen_sides[self.n_pellets_retrieved]

    def parse_experiment_config(self):
        super().parse_experiment_config()
        self._read_or_write_config('learning_threshold')
        if "timing" in self.config:
            self.error_flash_ms = self.config["timing"].get("error_flash_ms", self.error_flash_ms)

    @property
    def n_pellets_retrieved(self):
        return self._n_pellets_retrieved

    @n_pellets_retrieved.setter
    def n_pellets_retrieved(self, value):
        if value < self.n_trials:
            self._n_pellets_retrieved = value
        else:
            dlg = QtWidgets.QMessageBox()
            dlg.setText(  # TODO: change message for max reached in S2
                f"""All pellets have been retrieved.
                The stage S{self.stage_number} experiment is now complete.
                You can now move to S{self.stage_number + 1}."""
            )

        self.check_performance()

    def check_performance(self):
        correct = len(self.responses['correct'])
        incorrect = len(self.responses['incorrect'])
        performance = correct / (correct + incorrect)
        self.performance.append(performance)
        # sliding_performance =
        if performance >= self.learning_threshold:
            self.log(logging.INFO, f'Performance: {performance:.2f} - Learning threshold reached. '
                                   f'Stage S{self.stage_number} is complete.')
        else:
            self.log(logging.WARNING, f'Performance: {performance:.2f} - Learning threshold not reached. '
                                      f'Stage S{self.stage_number} is not complete.')
