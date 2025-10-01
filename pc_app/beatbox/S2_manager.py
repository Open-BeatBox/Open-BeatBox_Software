from __future__ import annotations

import logging
from dataclasses import dataclass

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
    error_flash_ms = 2000

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
            'left': ['left_White_pattern', 'right_Black_pattern'], # left screen blank, right screen black
            'right': ['left_Black_pattern', 'right_White_pattern']
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
                timeout_ms=self.error_flash_ms,
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
        names = self.screen_pattern_names[self.get_screen_side()]
        self.set_outputs(on=names, flatten=True, flush=True)

    def _phase_done(self):
        if len(self.performance) > 0:
            performance = self.performance[-1]
        else:
            performance = 0
        self.log(logging.INFO, f'Performance: {performance:.2f} - Learning threshold reached. '
                               f'Stage S{self.stage_number} is complete.')
        self.log_experiment_data(True)

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
        is_correct = pin_name.startswith(self.get_screen_side())
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
        self._read_or_write_config('learning_threshold')
        if "timing" in self.config:
            self.error_flash_ms = self.config["timing"].get("error_flash_ms", self.error_flash_ms)

    def compute_performance(self):
        correct = len(self.responses['correct'])
        incorrect = len(self.responses['incorrect'])
        if correct == 0 and incorrect == 0:
            return
        performance = correct / (correct + incorrect)
        self.performance.append(performance)

    def check_performance(self):
        performance = self.performance[-1] if len(self.performance) > 0 else None
        if performance is None: return False
        if performance >= self.learning_threshold and len(self.responses['correct']) >= self.n_trials:
            return True
        else:
            return False

    def get_experiment_status(self):
        self.compute_performance()

        performance = self.performance[-1] if len(self.performance) > 0 else 0

        if self.check_performance():
            return 'complete'
        else:
            self.log(logging.DEBUG, f'Performance: {performance:.2f} - Learning threshold not reached. '
                                      f'Stage S{self.stage_number} is not complete.')
            return 'incomplete'

    def log_experiment_data(self, full=False):
        performance = self.performance[-1] if len(self.performance) > 0 else None

        if full:
            self.log(logging.INFO,
                    f"""EXPERIMENT SETUP
                    Minimum number of trials: {self.n_trials}
                    Learning threshold: {self.learning_threshold}""")

        self.log(logging.INFO, f"")
        self.log(logging.INFO,
                 f"""EXPERIMENT DATA
                 Trial count: {len(self.responses['correct'])}
                 Corrective loops count: {len(self.responses['incorrect'])}
                 Performance: {performance:.2f}""")
