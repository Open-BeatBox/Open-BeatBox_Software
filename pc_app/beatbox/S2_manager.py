from __future__ import annotations

import logging
from pathlib import Path

from typing import Dict, Any, List, Optional, get_type_hints

from numpy.ma.core import left_shift
from qtpy import QtCore, QtWidgets

from beatbox.experiment_managers import SConfig, BaseExperimentManager


class S2Config(SConfig):
    s_level = 'S2'

    n_trials: int
    inter_trial_interval: int
    learning_threshold: float
    corrective_loop_timeout: int
    error_flash_ms: int
    screen_sequence: List[str]
    advanced: bool
    correct_pattern: str
    incorrect_pattern: str
    left_screen_sequence: List[str]
    right_screen_sequence: List[str]

from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.experiment_managers import Phase, PhaseSpec, Edge, SConfig
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

    performanceUpdated = QtCore.Signal(float, float)
    correctiveLoopUpdated = QtCore.Signal(int)


    def __init__(self, experiment_folder, communicator,
                 main_window=None, actuator_pins_cfg: PinConfig=None):
        super().__init__(experiment_folder, communicator, main_window, actuator_pins_cfg)
        # NOTE: Trials are categorized as left or right based on the correct side for that trial
        self.current_trial_responses = {'correct': [], 'incorrect': []}  # Each entry: (response_type, trial_type, side, pin_name)
        self.previous_trials_responses = {'correct': [], 'incorrect': []}  # Accumulated responses
        self.left_performance = []
        self.right_performance = []
        self.tunnel_entered = False
        self._touch_result = None
        self.config = S2Config(Path(__file__).parent)
        self.config.load()
        # FIXME: should not be here, this is the adapter between pins_config and the experiment configspec
        self.screen_pattern_suffixes = {
            'w': "_White_pattern",
            'b': "_Black_pattern",
            'v': "_V_lines_pattern",
            'h': "_H_lines_pattern",
        }
        self._phase_table_initial = Phase.ITI
        self._phase_table = {
            Phase.ITI: PhaseSpec(
                on_enter=self._phase_iti,
                next_phase_default=Phase.WAIT_TUNNEL,
                timeout_ms=self.config.inter_trial_interval,
                relevant_pins=None
            ),
            Phase.WAIT_TUNNEL: PhaseSpec(
                on_enter=self._phase_wait_tunnel,
                routes={True: Phase.SHOW_STIM},
                decide=lambda instance: instance.tunnel_entered,  # bool attr set by handle_tunnel_crossing_in()
                relevant_pins={'right_tunnel_crossing': Edge.FALLING}
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
                decide=lambda instance: instance._get_touch_valence(),  # 'correct'/'incorrect'/None
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
                timeout_ms=self.config.error_flash_ms,
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
                on_enter=self._stop_flash,
                next_phase_default=Phase.ITI,
                timeout_ms=self.config.corrective_loop_timeout,
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
        full_pattern = self.screen_pattern_suffixes[pattern]
        match side:
            case 'l':
                return f'left{full_pattern}'
            case 'r':
                return f'right{full_pattern}'
            case _:
                raise ValueError(f'Could not derive pattern name for side {side}') # supposedly never executed

    def _get_pattern_from_side(self, side : str):
        if self.config.advanced:
            if side == 'l':
                left_seq = self.config.left_screen_sequence
                return left_seq[self.trial_no % len(left_seq)]
            elif side == 'r':
                right_seq = self.config.right_screen_sequence
                return right_seq[self.trial_no % len(right_seq)]
            else:
                raise ValueError('Side name must be "l" or "r"')
        else:
            screen_seq = self.config.screen_sequence
            correct_side = self.config.screen_sequence[self.trial_no % len(screen_seq)]
            if correct_side == side:
                return self.config.correct_pattern
            else:
                return self.config.incorrect_pattern

    def _get_current_pattern_name(self, side : str):
        if side not in ('l', 'r'):
            raise ValueError('Side name must be "l" or "r"')

        pattern = self._get_pattern_from_side(side)
        return self._derive_pattern_name(side, pattern)

    def _get_touch_valence(self):
        return self._touch_result[0] if self._touch_result is not None else None

    def _phase_iti(self):
        super()._phase_iti()
        self.check_performance()
        self.log_experiment_data()

    def _phase_wait_tunnel(self):
        self.tunnel_entered = False

    def _phase_show_stim(self):
        self._touch_result = None
        names = [self._get_current_pattern_name('l'), self._get_current_pattern_name('r')]
        self.set_outputs(on=names, flatten=True, flush=True)

    def _phase_done(self):
        self.log(logging.INFO, f'Stage S{self.stage_number} is complete.')
        self.log_experiment_data(full=True)
        self.stageEnded.emit(self.config.s_level)

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

    def _stop_flash(self):
        self.set_outputs(off=['left_White_pattern','right_White_pattern'],
                         flatten=False, flush=False)
        self.actuatorsStateChanged.emit(self._mask())  # tell GUI to show  # TODO: check

    def _handle_screen_touch(self):
        response_type, pin_name = self._touch_result
        self._touch_result = None
        response_side = 'l' if pin_name.startswith('left_') else 'r'
        if response_type == 'correct':
            side = response_side
        else: # incorrect response, the "trial side" is the opposite of the response side
            side = 'l' if response_side == 'r' else 'r'
        # Register response given the correct side for this trial
        trial_type = 'trial' if not self.current_trial_responses['correct'] and not self.current_trial_responses['incorrect'] else 'corrective_loop'
        self.current_trial_responses[response_type].append((response_type, trial_type, side, pin_name))
        log_levels = {'correct': logging.INFO, 'incorrect': logging.INFO}
        self.log(log_levels[response_type], f"{response_type.title()} response on {pin_name}.")

    def _handle_correct_response(self):
        """Turn screens off, drop pellet, wait ITI, start next trial."""
        self._handle_screen_touch()
        self.turn_off_screens()
        # Merge current_trial_responses into previous_trials_responses
        for key in ['correct', 'incorrect']:
            self.previous_trials_responses[key].extend(self.current_trial_responses[key])
        self.current_trial_responses = {'correct': [], 'incorrect': []}
        self.compute_performance() # compute performance after add current trial responses
        self.correctiveLoopUpdated.emit(0)
        # Emit trial numbers
        current_trial_number = self.count_responses(response_type='trial', previous=True)
        corrective_loops = self.count_responses(result='incorrect', previous=False)
        total_trials = self.count_responses(previous=True) + corrective_loops # all responses
        self.trialNumbersUpdated.emit(current_trial_number, total_trials)

    def _handle_incorrect_response(self):
        """Flash white, then wait the **same** ITI before retrying."""
        self._handle_screen_touch()
        self._flash_error()
        # Update corrective loop count for GUI (count corrective_loop responses in current trial)
        corrective_loops = self.count_responses(result='incorrect', previous=False)
        self.correctiveLoopUpdated.emit(corrective_loops)  # +1 for the current one
        total_trials = self.count_responses(previous=True) + corrective_loops  # all responses
        self.trialNumbersUpdated.emit(self.count_responses(response_type='trial', previous=True), total_trials)

    def handle_tunnel_crossing_in(self): # NOTE: not used
        """
        First IR-beam break inside the tunnel.
        """
        pass
        # TODO: update the GUI buttons

    def handle_tunnel_crossing_out(self):
        """Nothing changes visually; simply keep the same buttons lit."""
        self.tunnel_entered = True
        # TODO: update the GUI buttons

    def handle_screen_touch(self, pin_name):
        if pin_name.startswith('left_'):
            is_correct = self.config.correct_pattern == self._get_pattern_from_side('l')
        elif pin_name.startswith('right_'):
            is_correct = self.config.correct_pattern == self._get_pattern_from_side('r')
        else:
            raise ValueError('Screen touch triggered from incorrect pin state change')
        self._touch_result = ('correct' if is_correct else 'incorrect', pin_name)

    def _dispatch_changed_pin(self, pin_name: str, edge: Edge = Edge.BOTH):
        """
        Dispatch pin changes to the appropriate handlers.
        1. nose_poke: handle pellet delivery/retrieval
        2. left_tunnel_crossing: handle tunnel crossing in
        3. right_tunnel_crossing: handle tunnel crossing out
        4. left_response/right_response: handle screen touch

        Parameters
        ----------
        pin_name : str
            The name of the pin that changed state.
        edge : Edge
            The edge type (RISING, FALLING, BOTH).
        """
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
        if not self.config.advanced:
            screen_seq = self.config.screen_sequence
            correct_side = screen_seq[self.trial_no % len(screen_seq)]
        else:
            left_seq = self.config.left_screen_sequence
            right_seq = self.config.right_screen_sequence
            left_side = left_seq[self.trial_no % len(left_seq)]
            right_side = right_seq[self.trial_no % len(right_seq)]
            if left_side == self.config.correct_pattern:
                correct_side = 'l'
            elif right_side == self.config.correct_pattern:
                correct_side = 'r'
            else:
                correct_side = 'x' # invalid
                self.log(logging.ERROR, 'In advanced mode, left and right screen sequences must be complementary.')
        return correct_side

    def compute_performance(self):
        self._compute_left_performance()
        self._compute_right_performance()

    def count_responses(self, result: str = None, response_type: str = None, side: str = None, previous: bool = True):
        """
        Count responses matching the given criteria.
        - result: 'correct' or 'incorrect' (or None for both)
        - response_type: 'trial' or 'corrective_loop' (or None for both)
        - side: 'l' or 'r' (or None for both)
        - previous: search in previous_trials_responses if True, else current_trial_responses
        """
        responses = self.previous_trials_responses if previous else self.current_trial_responses
        keys = ['correct', 'incorrect'] if result is None else [result]
        count = 0
        for key in keys:
            for r in responses[key]:
                if ((response_type is None or r[1] == response_type) and
                    (side is None or r[2] == side)):
                    count += 1
        return count

    def _compute_left_performance(self):
        left_correct = self.count_responses('correct', 'trial', 'l')
        left_total = left_correct + self.count_responses('incorrect', 'trial', 'l')
        if not left_total:
            return
        performance = left_correct / left_total
        self.left_performance.append(performance)
        self.performanceUpdated.emit(self.get_left_performance(), self.get_right_performance())

    def _compute_right_performance(self):
        right_correct = self.count_responses('correct', 'trial', 'r')
        right_total = right_correct + self.count_responses('incorrect', 'trial', 'r')
        if not right_total:
            return
        performance = right_correct / right_total
        self.right_performance.append(performance)
        self.performanceUpdated.emit(self.get_left_performance(), self.get_right_performance())

    def check_performance(self):
        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()
        if ((len(self.previous_trials_responses['correct']) >= self.config.n_trials) and
                left_performance >= self.config.learning_threshold and
                right_performance >= self.config.learning_threshold):
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
        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()

        if self.check_performance():
            return 'complete'
        else:
            # Use base logger to avoid keep CSV logging clean
            BaseExperimentManager.log(self, logging.DEBUG, f'Left performance: {left_performance:.2f} -'
                                    f'Right performance: {right_performance:.2f} -' 
                                    f'Learning threshold not reached.'
                                    f'Stage S{self.stage_number} is not complete.')
            return 'incomplete'

    def log(self, level: int, message: str, state_bits: Optional[Dict[str, Any]] = None):
        BaseExperimentManager.log(self, level, message)
        state_bits = state_bits if state_bits is not None else self._get_sensor_bits_from_state_value(self._last_state_value)
        if self.csv_logger:
            total_trials = self.count_responses() + self.count_responses(previous=False)
            self.csv_logger.log(
                stage=self.stage_number,
                log_level=logging.getLevelName(level),
                message=message,
                total_trials=total_trials,
                trial_number=self.count_responses(response_type='trial') + 1,  # +1 for current trial
                correct_side=self.get_screen_side(),
                corrective_loop=self.count_responses(result='incorrect', previous=False),
                left_performance=self.get_left_performance(),
                right_performance=self.get_right_performance(),
                state_bits=state_bits
            )

    def log_experiment_data(self, full=False):
        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()
        corrective_loops_count = self.count_responses(result='incorrect', previous=False)
        trial_count = self.count_responses(response_type='trial') + 1
        total_trials = self.count_responses()
        if full:
            self.log(logging.INFO,
                    f"""EXPERIMENT SETUP\nMinimum number of trials: {self.config.n_trials}\nLearning threshold: {self.config.learning_threshold}""")
        self.log(logging.INFO,
                 f"""EXPERIMENT DATA\nTrial count (current included): {trial_count}\nTotal trials: {total_trials}\nCorrective loops count: {corrective_loops_count}\nLeft performance: {left_performance:.2f}\nRight performance: {right_performance:.2f}""")

    def set_experiment_folder(self, experiment_folder):
        super().set_experiment_folder(experiment_folder)

    def handle_error_flash_ms_changed(self, value):
        if value is None:
            return
        self.inter_trial_interval = value
        self.config.set('error_flash_ms', value)
        self.config.write()
        self._phase_table[Phase.INCORRECT_RESPONSE].set_timeout(value)

    def handle_corrective_loop_timeout_changed(self, value):
        if value is None:
            return
        self.inter_trial_interval = value
        self.config.set('corrective_loop_timeout', value)
        self.config.write()
        self._phase_table[Phase.STOP_FLASH].set_timeout(value)
