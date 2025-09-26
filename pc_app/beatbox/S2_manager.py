from __future__ import annotations

import logging

from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.experiment_managers import Phase, PhaseSpec, Edge
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

    def _dispatch_changed_pin(self, pin_name: str, edge: Edge = Edge.BOTH):
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
