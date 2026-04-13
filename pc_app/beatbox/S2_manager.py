from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
import time

from typing import Dict, Any, List, Optional

from qtpy import QtCore

from beatbox.experiment_managers import SConfig, BaseExperimentManager, SubjectInfo


class S2Config(SConfig):
    s_level = 'S2'

    n_trials: int
    inter_trial_interval: int
    learning_threshold: float
    corrective_loop_timeout: int
    error_flash_ms: int
    screen_sequence: List[str]
    time_pressure_ms: int
    advanced: bool
    correct_pattern: str
    incorrect_pattern: str
    left_screen_sequence: List[str]
    right_screen_sequence: List[str]


from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.experiment_managers import Phase, PhaseSpec, Edge
from beatbox.utils import PinConfig, PATTERN_LUT, bitmask_from_pattern, get_pin_names_from_pattern, PATTERN_ID


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
                 main_window=None, actuator_pins_cfg: PinConfig = None,
                 subject_info: SubjectInfo = None):
        super().__init__(experiment_folder, communicator, main_window, actuator_pins_cfg, subject_info)
        self.corrective_loop_count = 0
        self.total_trials = 0
        self.total_corrective_loop_trials = 0
        self.total_valid_trials = 0  # valid trial
        self.total_correct_trials = 0  # correct response and valid trial
        self.total_incorrect_trials = 0  # incorrect response, regardless of valid/invalid
        self.total_invalid_trials = 0  # correct response and invalid trial
        self.response_side = None
        self.tunnel_entered = False
        self._touch_result = None
        self._screen_side = None
        self._last_trial_side = None
        self._last_response_type = None
        # Store only the last N responses, where N = n_trials from config
        self.config = S2Config(Path(__file__).parent)
        self.config.load()
        n_trials = int(self.config.n_trials)
        self.left_responses = deque(maxlen=n_trials)  # values: 'valid', 'invalid', 'incorrect'
        self.right_responses = deque(maxlen=n_trials)
        self._left_perf = 0.0
        self._right_perf = 0.0
        self._overall_perf = 0.0
        # Timestamps for logging
        self._stimulus_show_ts = None  # Timestamp when stimulus is shown
        self._screen_touch_ts = None
        self._pellet_retrieval_ts = None
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
                relevant_pins={'left_tunnel_crossing': Edge.FALLING,
                               'right_tunnel_crossing': Edge.FALLING}
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
                relevant_pins={'left_response': Edge.RISING,
                               'right_response': Edge.RISING}
            ),
            Phase.CORRECT_RESPONSE: PhaseSpec(
                on_enter=self._handle_correct_response,
                next_phase_default=Phase.DELIVER_PELLET,
                # FIXME: BUG: we need to delay before going to the next phase, as somehow, even though the payload is
                # properly sent and acked, the effect is unseen unless some delay is inserted here
                timeout_ms=500,
                relevant_pins=None
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
                on_enter=self._phase_wait_pellet,
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
                routes=None,
                decide=lambda instance: False,
                relevant_pins=None
            )
        }

    def _derive_pattern_name(self, side: str, pattern):
        full_pattern = self.screen_pattern_suffixes[pattern]
        match side:
            case 'l':
                return f'left{full_pattern}'
            case 'r':
                return f'right{full_pattern}'
            case _:
                raise ValueError(f'Could not derive pattern name for side {side}')  # supposedly never executed

    def _get_pattern_from_side(self, side: str):
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

    def _get_current_pattern_name(self, side: str):
        if side not in ('l', 'r'):
            raise ValueError('Side name must be "l" or "r"')

        pattern = self._get_pattern_from_side(side)
        return self._derive_pattern_name(side, pattern)

    def _get_touch_valence(self):
        return self._touch_result[0] if self._touch_result is not None else None

    def _phase_iti(self):
        super()._phase_iti()
        # Set ITI start timestamp (in ms since experiment start)
        self._inter_trial_interval_start_ts = int(time.time() * 1000)
        self.update_screen_side()
        self.check_performance()
        self.log_experiment_data()

    def _update_gui(self):
        """ Override parent method to update the GUI """
        self.trialNumbersUpdated.emit(self.get_correct_count(), self.get_total_trial_count())

    def _phase_wait_tunnel(self):
        self.tunnel_entered = False

    def _phase_show_stim(self):
        self._touch_result = None
        self._set_stimulus_show_timestamp()
        left_pattern = self._get_side_pattern('l')
        right_pattern = self._get_side_pattern('r')
        left_names = get_pin_names_from_pattern(left_pattern, 'left')
        right_names = get_pin_names_from_pattern(right_pattern, 'right')
        names = [*left_names, *right_names]
        self._activate_stimulus_outputs(names)

    def _set_stimulus_show_timestamp(self):
        """Set the timestamp for when the stimulus is shown."""
        self._stimulus_show_ts = int(time.time() * 1000)

    def _get_side_pattern(self, side: str):
        """Get the pattern for a given side ('l' or 'r')."""
        pattern_key = self._get_pattern_from_side(side)
        return PATTERN_LUT[pattern_key]

    def _activate_stimulus_outputs(self, names):
        """Set the outputs for the given pin names."""
        self.set_outputs(on=names, flatten=True, flush=True)

    def _phase_done(self):
        self.log(logging.INFO, f'Stage S{self.stage_number} is complete.')
        self.log_experiment_data(full=True)
        self.stageEnded.emit(self.config.s_level)

    def _flash_error(self):
        names = get_pin_names_from_pattern(PATTERN_ID.BLINK, 'both')
        # Setting both White and Black patterns makes the screen blink
        self.set_outputs(on=names, flatten=True, flush=True)
        self.actuatorsStateChanged.emit(self._mask(*names))  # tell GUI to show

    def _stop_flash(self):
        self.set_outputs(off=get_pin_names_from_pattern(PATTERN_ID.FULL_WHITE, 'both'),
                         flatten=False, flush=False)
        self.corrective_loop_count += 1
        self.total_corrective_loop_trials += 1
        self.correctiveLoopUpdated.emit(self.corrective_loop_count)
        self.actuatorsStateChanged.emit(self._mask())  # tell GUI to show  # TODO: check

    def _phase_wait_pellet_retrieval(self):
        super()._phase_wait_pellet_retrieval()
        self.corrective_loop_count = 0  # reset for next trial
        self.correctiveLoopUpdated.emit(self.corrective_loop_count)

    # Handle screen touch, register response, compute performance and log it
    def _handle_screen_touch(self):
        import time
        response_type, pin_name = self._touch_result
        self._touch_result = None
        self._last_trial_side = self.get_screen_side()
        self._last_response_type = response_type
        self.total_trials += 1
        # Record the timestamp of the screen touch (in ms since experiment start)
        self._screen_touch_ts = int(time.time() * 1000)
        if response_type == 'correct':
            # Do not append yet, wait for pellet retrieval
            pass
        else:
            # Incorrect response, append as 'incorrect'
            self.total_incorrect_trials += 1
            self.total_valid_trials += 1  # incorrect are valid trials by definition
            if self._last_trial_side == 'l':
                self.left_responses.append('incorrect')
            elif self._last_trial_side == 'r':
                self.right_responses.append('incorrect')
            # Update performance and GUI after incorrect response
            self.compute_performance()
            self.performanceUpdated.emit(self.get_left_performance(), self.get_right_performance())
            self.trialNumbersUpdated.emit(self.get_correct_count(), self.get_total_trial_count())

        log_levels = {'correct': logging.INFO, 'incorrect': logging.INFO}
        self.log(log_levels[response_type], f"{response_type.title()} response on {pin_name}.")

    def handle_pellet_retrieved(self):
        self._pellet_retrieval_ts = time.time() * 1000  # ms
        # Only update if last response was correct
        if self._last_trial_side and self._last_response_type == 'correct':
            delay = self._pellet_retrieval_ts - self._screen_touch_ts
            time_pressure = int(self.config.time_pressure_ms)
            responses = self.left_responses if self._last_trial_side == 'l' else self.right_responses
            if delay <= time_pressure:
                responses.append('valid')
                self.total_valid_trials += 1
                self.total_correct_trials += 1
            else:
                # invalid responses are not taken into account
                # FIXME:HACK: in S2 invalid trials are the only way to have incorrect response, this
                # hack enables us to compute perf for S2 without redesigning S2 and S3 too much but should
                # be changed as soon as possible
                if self.stage_number == 2:
                    responses.append('incorrect') # HACK
                self.total_invalid_trials += 1
            # After updating, recompute performance
            self.compute_performance()
            ## TODO: find a more flexible to display either overall performance or side performances
            ## This is a quick hack to not update the GUI fields
            if self.stage_number == 2:
                self.performanceUpdated.emit(self.get_overall_performance(), self.get_overall_performance())
            else:
                self.performanceUpdated.emit(self.get_left_performance(), self.get_right_performance())
            self.trialNumbersUpdated.emit(self.get_correct_count(), self.get_total_trial_count())
        # Reset last response info
        self._last_trial_side = None
        self._last_response_type = None
        super().handle_pellet_retrieved()

    def _handle_correct_response(self):
        """Turn screens off, drop pellet, wait ITI, start next trial."""
        self._handle_screen_touch()
        self.turn_off_screens()

    def _handle_incorrect_response(self):
        """Flash white, then wait the **same** ITI before retrying."""
        self._handle_screen_touch()
        self._flash_error()

    def handle_tunnel_crossing_out(self):
        """Nothing changes visually; simply keep the same buttons lit."""
        self.tunnel_entered = True
        # TODO: update the GUI buttons

    def check_response(self, pin_name):
        if pin_name.startswith('left_'):
            is_correct = self.config.correct_pattern == self._get_pattern_from_side('l')
        elif pin_name.startswith('right_'):
            is_correct = self.config.correct_pattern == self._get_pattern_from_side('r')
        else:
            raise ValueError('Screen touch triggered from incorrect pin state change')
        self._touch_result = ('correct' if is_correct else 'incorrect', pin_name)

    def compute_performance(self):
        # Only 'valid' counts as correct, 'invalid' and 'incorrect' as incorrect
        def perf(responses):
            valid = sum(1 for r in responses if r == 'valid')
            total = sum(1 for r in responses if r in ('valid', 'incorrect'))
            return valid / total if total > 0 else 0.0

        all_responses = list(self.left_responses) + list(self.right_responses)
        self._left_perf = perf(self.left_responses)
        self._right_perf = perf(self.right_responses)
        self._overall_perf = perf(all_responses)
        self.light_log()

    def get_overall_performance(self):
        return self._overall_perf

    def get_left_performance(self):
        return self._left_perf

    def get_right_performance(self):
        return self._right_perf

    def get_trial_count(self):  # legacy
        return self.trial_no + 1

    def get_correct_count(self):
        return self.trial_no

    def get_total_trial_count(self) -> int:
        return self.total_trials

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
                self.handle_tunnel_crossing_out()
            case 'right_tunnel_crossing':
                self.handle_tunnel_crossing_out()
            case 'left_response' | 'right_response':
                self.response_side = 'l' if pin_name.startswith('left_') else 'r'
                self.check_response(pin_name)

    def get_screen_side(self):
        return self._screen_side

    def update_screen_side(self):
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
                correct_side = 'x'  # invalid
                self.log(logging.ERROR, 'In advanced mode, left and right screen sequences must be complementary.')
        self._screen_side = correct_side

    def check_performance(self):
        return ((len(self.left_responses) + len(self.right_responses)) >= self.config.n_trials and
                self.get_overall_performance() >= self.config.learning_threshold)

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

    def log(self, level: int, message: str, state_bits: Optional[Dict[str, Any]] = None, summary: bool = False):
        BaseExperimentManager.log(self, level, message)
        state_bits = state_bits if state_bits is not None else self._get_sensor_bits_from_state_value(
            self._last_state_value)
        if self.csv_logger and not summary:  # full logging is already done at each pin stage change
            total_trials = self.get_total_trial_count()
            self.csv_logger.log(
                stage=self.stage_number,
                log_level=logging.getLevelName(level),
                message=message,
                total_trials=total_trials,
                trial_number=self.get_trial_count(),
                correct_side=self.get_screen_side(),
                corrective_loop=self.corrective_loop_count,
                left_performance=self.get_left_performance(),
                right_performance=self.get_right_performance(),
                state_bits=state_bits
            )

    # Summary logging at the end of each trial
    def light_log(self):
        # Summary logging at the end of each trial
        self.light_logger.log(
            trial_number=self.get_total_trial_count(),
            correct_side=self.get_screen_side(),
            response_side=self.response_side,
            reward=1 if self.get_screen_side() == self.response_side else 0,
            incorrect=self.total_incorrect_trials,
            valid=self.total_valid_trials,
            correct=self.total_correct_trials,
            corrective_loop=self.corrective_loop_count,
            overall_performance=self.get_overall_performance(),
            left_performance=self.get_left_performance(),
            right_performance=self.get_right_performance(),
            show_stim_ts=self._stimulus_show_ts,
            screen_touch_ts=self._screen_touch_ts,
            reward_collect_ts=self._pellet_retrieval_ts
        )
        # Reset timestamps for next trial
        self._stimulus_show_ts = None
        self._screen_touch_ts = None
        self._pellet_retrieval_ts = None

    def log_experiment_data(self, full=False):
        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()
        corrective_loops_count = self.corrective_loop_count
        total_trials = self.get_total_trial_count()
        if full:
            self.log(logging.INFO,
                     f"""EXPERIMENT SETUP\nMinimum number of trials: {self.config.n_trials}\nLearning threshold: {self.config.learning_threshold}""")
        self.log(logging.INFO,
                 f"""EXPERIMENT DATA
                 Total trial count: {total_trials}
                 Incorrect trial count: {self.total_incorrect_trials}
                 Correct and valid trial count: {self.total_correct_trials}
                 Valid trial count: {self.total_valid_trials}
                 Corrective loops count: {corrective_loops_count}
                 Left performance: {left_performance:.2f}
                 Right performance: {right_performance:.2f}""")

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

    def _goto(self, phase: Phase, *, extra_ms: int = 0):
        super()._goto(phase, extra_ms=extra_ms)
        # TODO: this is a bit hacky, we should find a better way to track this
        if phase == Phase.DELIVER_PELLET:
            self.pellet_delivery_loop_count += 1
            if self.pellet_delivery_loop_count > 20:
                self.pelletDeliveryAlert.emit(self.pellet_delivery_loop_count)
        else:
            if self.pellet_delivery_loop_count > 0:
                self.pellet_delivery_loop_count = 0
                self.pelletDeliveryAlert.emit(0)
