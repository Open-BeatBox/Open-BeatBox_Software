from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from typing import Dict, Any, List, Optional

from configobj import ConfigObj


class SConfig:
    s_level: str
    def __init__(self, folder):
        self.folder = folder
        self.regular_attrs: List[str] = []
        self.advanced_attrs: List[str] = []
        self.config: Optional[ConfigObj] = None

    def load(self):
        self.config = ConfigObj(self.cfg_path, encoding='utf-8',  # WARNING: defaults in spec !!
                                configspec=self._spec_path, unrepr=True)

        for attr in self.regular_attrs + self.advanced_attrs:
            setattr(self, attr, self.config[attr])

    @property
    def cfg_path(self) -> Path:
        return Path(self.folder) / f'experiment.cfg'

    @property
    def _spec_path(self) -> Path:
        # return Path('~/.beatbox').expanduser() / 'config_specs' / f'{self.s_level}.config.spec'
        return Path(__file__).parent / 'config_specs' / f'{self.s_level}.config.spec'

    def write(self):
        self.config.write()


class _S2Config(SConfig):
    s_level = 'S2'

    learning_threshold: float
    error_flash_ms: int
    screen_sequence: List[str]
    correct_pattern: str
    incorrect_pattern: str
    left_screen_sequence: List[str]
    right_screen_sequence: List[str]


# The object that will contain S2 related attributes
class S2Config:
    allowed_chars = {'b', 'h', 'v', 'w'}
    allowed_patterns = allowed_chars # TODO: add red light support when implemented

    learning_threshold: float

    def __init__(self):
        self.learning_threshold = 0.0
        self.error_flash_ms = 0
        self.screen_sequence = []
        self.correct_pattern = 'w'
        self.incorrect_pattern = 'h'
        self.advanced = False # default to False
        self.validate_with_sequence = False
        self.left_screen_sequence = []
        self.right_screen_sequence = []
        self.correct_screen_sequence = []

    def is_valid_pattern(self, data, allowed=None):
        if allowed is None:
            allowed = S2Config.allowed_chars

        if isinstance(data, str):
            return all(char in allowed for char in data)
        elif isinstance(data, Iterable):
            return all(self.is_valid_pattern(item, allowed) for item in data)
        else:
            # Non-iterable, non-string found — return False or raise error
            raise ValueError(f'Invalid pattern for {data}, please select from {allowed}')

    def is_valid_config(self):
        if not 0 <= self.learning_threshold <= 1:
            raise ValueError('Threshold must be between 0 and 1')

        if not isinstance(self.advanced, bool):
            raise ValueError('Advanced must be boolean value, i.e. True or False')

        if not 0 <= self.error_flash_ms:
            raise ValueError('Error_flash_ms must be greater than 0')

        if not self.advanced:
            self.is_valid_pattern(self.screen_sequence, {'l', 'r'})
            self.is_valid_pattern(self.correct_pattern, {'b', 'h', 'v', 'w'})
            return

        self.is_valid_pattern(self.left_screen_sequence)
        self.is_valid_pattern(self.right_screen_sequence)

        l_size = len(self.left_screen_sequence)
        r_size = len(self.right_screen_sequence)
        if l_size != r_size:
            raise ValueError('Config size mismatch')

        for index, left_pattern, right_pattern in enumerate(zip(self.left_screen_sequence, self.right_screen_sequence)):
            if left_pattern != self.correct_pattern and right_pattern != self.correct_pattern:
                raise ValueError(f'Config error at index {index}, there should be at least pattern matching the correct pattern')

        return

    def parse_config(self, config : Dict[str, Any] = None):
        if config is None:
            raise ValueError('Config cannot be None')
        # First read or write the config
        _read_or_write_config(self, key='learning_threshold', config=config)
        _read_or_write_config(self, key='error_flash_ms', config=config)
        _read_or_write_config(self, key='screen_sequence', config=config)
        _read_or_write_config(self, key='correct_pattern', config=config)
        _read_or_write_config(self, key='advanced', config=config)

        if self.advanced:
            _read_or_write_config(self, key='validate_with_sequence', config=config)
            _read_or_write_config(self, key='left_screen_sequence', config=config)
            _read_or_write_config(self, key='right_screen_sequence', config=config)
            _read_or_write_config(self, key='correct_screen_sequence', config=config)

        # Now validate the config
        self.is_valid_config()

from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.experiment_managers import Phase, PhaseSpec, Edge, _read_or_write_config
from beatbox.utils import PinConfig


class BeatBoxExperimentManagerS2(BeatBoxExperimentManagerS1):
    """
    Stage 2 experiment manager.
    Inherits from BeatBoxExperimentManagerS1 and adds functionality

    for handling the left and right screens, patterns, and responses.

    The experiment logic is defined in the _phase_table attribute,
    which specifies the link between phases, timeouts, and actions to take on entering each phase.
    """
    stage_number = 2

    def __init__(self, experiment_folder, communicator, n_trials=20,
                 inter_trial_interval=10000, error_trial_interval=200,
                 main_window=None, actuator_pins_cfg: PinConfig=None):
        super().__init__(experiment_folder, communicator, n_trials, inter_trial_interval, main_window, actuator_pins_cfg)
        self.error_trial_interval = error_trial_interval
        self.learning_threshold = 0.8 # FIXME: remove
        self.responses = {'correct': [], 'incorrect': []} # TODO: split by screen
        self.left_performance = []
        self.right_performance = []
        self.tunnel_entered = False  # whether the tunnel has been entered
        self.touch_result = None  # 'correct', 'incorrect', or None
        self.reward_ms = 20000   # FIXME: random, TODO: parametrize
        # self.s2_config = S2Config()
        self.s2_config = _S2Config(Path(__file__).parent)
        # Exactly alternating sides for the screens
        # self.screen_sides = ['right' if i % 2 else 'left' for i in range(1, n_trials + 1)]
        self.screen_pattern_suffixes = {
            'w': "_White_pattern",
            'b': "_Black_pattern",
            'v': "_V_lines_pattern",
            'h': "_H_lines_pattern",
        }
        self.screen_pattern = {
            'l': [self._derive_pattern_name('l', self.s2_config.correct_pattern), 'right_Black_pattern'],
            'r': ['left_Black_pattern', self._derive_pattern_name('r', self.s2_config.correct_pattern)]
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
                relevant_pins={'left_tunnel_crossing': Edge.FALLING}
            ),
            Phase.SHOW_STIM: PhaseSpec(
                on_enter=self._phase_show_stim,
                next_phase_default=Phase.WAIT_TOUCH,
                timeout_ms=0,
                relevant_pins=None
            ),
            Phase.WAIT_TOUCH: PhaseSpec(
                routes={'correct': Phase.CORRECT_RESPONSE,
                        'incorrect': Phase.INCORRECT_RESPONSE},
                decide=lambda instance: instance.get_touch_valence(),  # 'correct'/'incorrect'/None
                relevant_pins= {'left_response': Edge.RISING,
                                'right_response': Edge.RISING}
            ),
            Phase.CORRECT_RESPONSE: PhaseSpec(
                on_enter=self._handle_correct_response,
                next_phase_default=Phase.DELIVER_PELLET,
                # FIXME: BUG: we need to delay before going to the next phase, as somehow, even though the payload is
                # properly sent and acked, the effect is unseen unless some delay is inserted here
                timeout_ms=500,
                relevant_pins= None
            ),
            Phase.INCORRECT_RESPONSE: PhaseSpec(
                on_enter=self._handle_incorrect_response,
                next_phase_default=Phase.STOP_FLASH,
                timeout_ms=self.s2_config.error_flash_ms,
                relevant_pins=None
            ),
            Phase.DELIVER_PELLET: PhaseSpec(
                on_enter=self._phase_deliver_pellet,
                next_phase_default=Phase.WAIT_PELLET,
                timeout_ms=0,
                relevant_pins=None
            ),
            Phase.WAIT_PELLET: PhaseSpec(
                on_enter= self._phase_wait_pellet,
                routes={True: Phase.WAIT_PELLET_RETRIEVAL},
                next_phase_default=Phase.DELIVER_PELLET,
                timeout_ms=1000,
                decide=lambda instance: instance.pellet_delivered,
                relevant_pins={'nose_poke': Edge.FALLING}
            ),
            Phase.WAIT_PELLET_RETRIEVAL: PhaseSpec(
                on_enter=self._phase_wait_pellet_retrieval,
                routes={'incomplete': Phase.ITI,
                        'complete': Phase.DONE},  # go back to ITI
                decide=lambda instance: instance.get_experiment_status(),
                relevant_pins={'nose_poke': Edge.RISING}
            ),
            Phase.STOP_FLASH: PhaseSpec(
                on_enter=self.stop_flash,
                next_phase_default=Phase.ITI,
                timeout_ms=0,
                relevant_pins=None
            ),
            Phase.DONE: PhaseSpec(
                on_enter=self._phase_done,
                routes=None,  # TODO: add routing to next experiment
                decide=lambda instance: False,
                relevant_pins=None
            )
        }

    def _derive_pattern_name(self, side : str, pattern):
        if pattern not in S2Config.allowed_patterns :
            raise ValueError(f'Pattern name must be {self.allowed_char}')

        full_pattern = self.screen_pattern_suffixes[pattern]
        match side:
            case 'l':
                return f'left{full_pattern}'
            case 'r':
                return f'right{full_pattern}'
            case _:
                raise ValueError(f'Could not derive pattern name for side {side}') # supposedly never executed

    def get_pattern_from_side(self, side : str):
        if self.s2_config.advanced:
            if side == 'l':
                left_seq = self.s2_config.left_screen_sequence
                return left_seq[self.trial_no % len(left_seq)]
            elif side == 'r':
                right_seq = self.s2_config.right_screen_sequence
                return right_seq[self.trial_no % len(right_seq)]
            else:
                raise ValueError('Side name must be "l" or "r"')
        else:
            screen_seq = self.s2_config.screen_sequence
            correct_side = self.s2_config.screen_sequence[self.trial_no % len(screen_seq)]
            if correct_side == side:
                return self.s2_config.correct_pattern
            else:
                return self.s2_config.incorrect_pattern

    def get_current_pattern_name(self, side : str):
        if side not in ('l', 'r'):
            raise ValueError('Side name must be "l" or "r"')

        pattern = self.get_pattern_from_side(side)
        return self._derive_pattern_name(side, pattern)

    def get_touch_valence(self):
        return self.touch_result[0] if self.touch_result is not None else None

    def _phase_iti(self):
        super()._phase_iti()
        self.check_performance()
        self.log_experiment_data()

    def _phase_wait_tunnel(self):
        self.tunnel_entered = False

    def _phase_show_stim(self):
        self.touch_result = None
        names = [self.get_current_pattern_name('l'), self.get_current_pattern_name('r')]
        # if self.s2_config.advanced:
        #     left_seq = self.s2_config.left_screen_sequence
        #     right_seq = self.s2_config.right_screen_sequence
        #     patterns = { 'left': left_seq[self.trial_no % len(left_seq)], 'right': right_seq[self.trial_no % len(right_seq)]}
        #     names = [self._derive_pattern_name('l', patterns['left']),self._derive_pattern_name('r', patterns['right'])]
        # else:
        #     screen_seq = self.s2_config.screen_sequence
        #     names = self.screen_pattern[screen_seq[self.trial_no % len(screen_seq)]]
        # # names = self.screen_pattern_names[self.get_screen_side()]
        self.set_outputs(on=names, flatten=True, flush=True)

    def _phase_done(self):
        if len(self.performance) > 0:
            performance = self.performance[-1]
        else:
            performance = 0
        self.log(logging.INFO, f'Performance: {performance:.2f} - Learning threshold reached. '
                               f'Stage S{self.stage_number} is complete.')
        self.log_experiment_data(full=True)

    def _flash_error(self):
        # Setting both White and Black patterns makes the screen blink
        self.set_outputs(on=['left_White_pattern',
                             'left_Black_pattern',
                             'right_White_pattern',
                             'right_Black_pattern'], flatten=True, flush=True)
        self.actuatorsStateChanged.emit(self._mask('left_White_pattern',
                             'left_Black_pattern',
                             'right_White_pattern',
                             'right_Black_pattern'))               # tell GUI to show

    def stop_flash(self):
        self.set_outputs(off=['left_White_pattern','right_White_pattern'],
                         flatten=False, flush=False)
        self.actuatorsStateChanged.emit(self._mask())  # tell GUI to show  # TODO: check

    def _handle_screen_touch(self):
        response_type,  pin_name = self.touch_result
        self.touch_result = None  # reset touch result
        self.responses[response_type].append(pin_name)  # TODO: check why pin_name is needed here
        log_levels = {
            'correct': logging.INFO,
            'incorrect': logging.INFO
        }
        self.log(log_levels[response_type], f"{response_type.title()} response on {pin_name}.")

    def _handle_correct_response(self):
        """Turn screens off, drop pellet, wait ITI, start next trial."""
        self._handle_screen_touch()
        self.turn_off_screens()

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
        if pin_name.startswith('left_'):
            is_correct = self.s2_config.correct_pattern == self.get_pattern_from_side('l')
        elif pin_name.startswith('right_'):
            is_correct = self.s2_config.correct_pattern == self.get_pattern_from_side('r')
        else:
            raise ValueError('Screen touch triggered from incorrect pin state change')
        self.touch_result = ('correct' if is_correct else 'incorrect', pin_name)

    def _dispatch_changed_pin(self, pin_name: str, edge: Edge = Edge.BOTH):
        match pin_name:
            case 'nose_poke':
                match edge:
                    case Edge.RISING:
                        self.handle_pellet_retrieved()
                    case Edge.FALLING:
                        self.handle_pellet_delivered()
                    case Edge.BOTH:
                        return
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
        self.log(logging.DEBUG, f'Parsing experiment config: {self.config}')
        self._read_or_write_config('learning_threshold')
        if "S2" in self.config:
            self.s2_config.parse_config(config=self.config['S2'])
            # FIXME: maybe use Qt signal to watch for this value
            self._phase_table[Phase.INCORRECT_RESPONSE].set_timeout(self.s2_config.error_flash_ms)

    def compute_left_performance(self):
        left_correct_count = self.responses['correct'].count('left_response')
        incorrect = len(self.responses['incorrect'])
        if self.trial_no == 0:
            return
        performance = left_correct_count / (self.trial_no + incorrect)
        self.left_performance.append(performance)

    def compute_right_performance(self):
        right_correct_count = self.responses['correct'].count('right_response')
        incorrect = len(self.responses['incorrect'])
        if self.trial_no == 0:
            return
        performance = right_correct_count / (self.trial_no + incorrect)
        self.right_performance.append(performance)

    def check_performance(self):
        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()
        if ((len(self.responses['correct']) >= self.n_trials) and
                left_performance >= self.learning_threshold and
                right_performance >= self.learning_threshold):
            return True
        else:
            return False

    def get_right_performance(self) -> int | Any:
        right_performance = self.right_performance[-1] if len(self.right_performance) > 0 else 0
        return right_performance

    def get_left_performance(self) -> int | Any:
        left_performance = self.left_performance[-1] if len(self.left_performance) > 0 else 0
        return left_performance

    def get_experiment_status(self):
        self.compute_performance()

        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()

        if self.check_performance():
            return 'complete'
        else:
            self.log(logging.DEBUG, f'Left performance: {left_performance:.2f} -'
                                    f'Right performance: {right_performance:.2f} -' 
                                    f'Learning threshold not reached.'
                                    f'Stage S{self.stage_number} is not complete.')
            return 'incomplete'

    def log_experiment_data(self, full=False):
        performance = self.get_right_performance()

        if full:
            self.log(logging.INFO,
                    f"""EXPERIMENT SETUP
                    Minimum number of trials: {self.n_trials}
                    Learning threshold: {self.learning_threshold}""")

        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()
        self.log(logging.INFO,
                 f"""EXPERIMENT DATA
                 Trial count: {len(self.responses['correct'])}
                 Corrective loops count: {len(self.responses['incorrect'])}
                 Left performance: {left_performance:.2f}
                 Right performance: {right_performance:.2f}""")
