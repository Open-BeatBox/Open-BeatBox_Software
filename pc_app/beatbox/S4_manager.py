import logging
from pathlib import Path
from typing import List

from beatbox.S2_manager import S2Config
from beatbox.S3_manager import BeatBoxExperimentManagerS3
from beatbox.experiment_managers import SubjectInfo, Phase, PhaseSpec, Edge
from beatbox.utils import PinConfig

class S4Config(S2Config):
    s_level = 'S4'

    ## FIXME: code duplication, without this SConfig.load won't properly work
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

    discovery_pattern: str
    reward_prob: float
    nb_rewards: int


class BeatBoxExperimentManagerS4(BeatBoxExperimentManagerS3):
    """
    Stage 4 experiment manager, inherits from S3. Will diverge as S4 logic evolves.
    """
    stage_number = 4

    def __init__(self, experiment_folder, communicator, main_window=None, actuator_pins_cfg: PinConfig = None,
                 subject_info: SubjectInfo = None):
        super().__init__(experiment_folder, communicator, main_window, actuator_pins_cfg, subject_info=subject_info)
        # Override config to use S2Config, but load S4-specific values from experiment.cfg
        self.config = S4Config(Path(__file__).parent)
        self.config.s_level = 'S4'  # For experiment.cfg section, but config spec is S4
        self.config.load()

        self.stage_status = None

        self._reward_left = 0

        # Discovery response tracking
        self._total_discovery_responses = 0
        self._total_disc_rewards = 0
        self._disc_vs_incorrect_trials = 0
        self._disc_vs_correct_trials = 0
        # Stats
        self._disc_vs_correct_ratio = 0.0
        self._disc_vs_incorrect_ratio = 0.0
        self._disc_average_reward = 0

        # Add discovery response handling
        self._phase_table[Phase.WAIT_TOUCH] = PhaseSpec(
                routes={'correct': Phase.CORRECT_RESPONSE,
                        'incorrect': Phase.INCORRECT_RESPONSE,
                        'discovery': Phase.DISCOVERY_RESPONSE},
                decide=lambda instance: instance._get_touch_valence(),  # 'correct'/'incorrect'/'discovery'/None
                relevant_pins={'left_response': Edge.RISING,
                               'right_response': Edge.RISING}
        )

        # add new routes
        self._phase_table.update({
            Phase.DISCOVERY_RESPONSE: PhaseSpec(
                on_enter=self._phase_discovery_response,
                routes={'success_roll': Phase.SUCCESS_ROLL,
                        'failed_roll': Phase.ITI},
                decide=lambda instance: instance._roll_result,  # 'success_roll'/'failed_roll'/None
                relevant_pins=None
            ),
            Phase.SUCCESS_ROLL: PhaseSpec(
                on_enter=self._phase_success_roll,
                next_phase_default=Phase.DELIVER_PELLET,
                timeout_ms=0,
                relevant_pins=None
            )
        })

        # Handle looping to reward for multiple pellet delivery
        self._phase_table[Phase.WAIT_PELLET_RETRIEVAL] = PhaseSpec(
            on_enter=self._phase_wait_pellet_retrieval,
            routes={'loop_reward': Phase.SUCCESS_ROLL,
                    'incomplete': Phase.ITI,
                    'complete': Phase.DONE},
            decide=lambda instance: instance.get_experiment_status(),
            relevant_pins={'nose_poke': Edge.RISING}
        )

    def _update_experiment_status(self):
        if self._reward_left:
            self.stage_status = 'loop_reward'
        else:
            # TODO: add condition for S4 and experiment end
            self.stage_status = 'incomplete'

    def _roll_for_reward(self):
        import random
        roll = random.random()
        if roll <= self.config.reward_prob:
            self._roll_result = 'success_roll'
            self._reward_left = self.config.nb_rewards  # set rewards left for looping in pellet delivery
            self.log(logging.DEBUG, f'Roll success! (roll={roll:.3f} <= reward_prob={self.config.reward_prob})')
            self.log(logging.INFO, f'S4_random_roll = S4_roll_success')
        else:
            self._roll_result = 'failed_roll'
            self._reward_left = 0
            self.log(logging.DEBUG, f'Roll failed! (roll={roll:.3f} > reward_prob={self.config.reward_prob})')
            self.log(logging.INFO, f'S4_random_roll = S4_roll_failed')

    def _phase_discovery_response(self):
        self.log(logging.DEBUG, f'Discovery response entry')
        # FIXME: call order should not influence response registration
        # FIXME: _handle_screen_touch does too much and should be broken into smaller bits and redefined in each stages maybe
        self._register_discovery_response() # needs to be called before _handle_screen_touch

        self._handle_screen_touch()
        self.total_valid_trials += 1
        self._total_discovery_responses += 1
        self._roll_for_reward()
        if self._roll_result == 'failed_roll':
            self._advance_trial() # rewards are not delivered, so move to ITI. If success, advancement will be done in retrieval
        self._advance() # force state update to trigger transition based on roll result

    def _phase_success_roll(self):
        self.log(logging.INFO, f'Entering success roll phase. Rewards left: {self._reward_left}')
        self._total_disc_rewards += 1

    def _register_discovery_response(self):
        # check what was the other pattern
        disc_trial = self._is_discovery_trial()
        if not disc_trial['isDisc']:
            return
        disc_side = disc_trial['side']
        other_side_type = self._get_pattern_type('l' if disc_side == 'r' else 'r')
        if other_side_type == 'correct':
            self._disc_vs_correct_trials += 1
        elif other_side_type == 'incorrect':
            self._disc_vs_incorrect_trials += 1
        elif other_side_type == 'discovery':
            self.log(logging.WARNING, f'Disc vs disc trial')

    def _is_discovery_trial(self) -> dict[str, bool | str]:
        # check if one of the pattern is a discovery pattern and returns the side
        left_seq = self.config.left_screen_sequence
        right_seq = self.config.right_screen_sequence
        left_pattern = left_seq[self.trial_no % len(left_seq)]
        right_pattern = right_seq[self.trial_no % len(right_seq)]
        if self.config.discovery_pattern == left_pattern:
                # return a dict to be more extensible in case we want to track more pattern types in the future
                return {'isDisc': True, 'side':'l'}
        elif self.config.discovery_pattern == right_pattern:
                return {'isDisc': True, 'side':'r'}
        else:
            return {'isDisc': False, 'side':'x'}

    def _get_discovery_pattern_side(self):
        # overlaps with update_screen_side, TODO: refactor update_screen_side and/or redefine to avoid duplication
        # XXX: assumes that discovery pattern is only on one side at a time
        left_seq = self.config.left_screen_sequence
        right_seq = self.config.right_screen_sequence
        left_pattern = left_seq[self.trial_no % len(left_seq)]
        right_pattern = right_seq[self.trial_no % len(right_seq)]
        if self.config.discovery_pattern == left_pattern:
                return 'l'
        elif self.config.discovery_pattern == right_pattern:
                return 'r'
        else:
            raise ValueError('Discovery pattern not found on either side for current trial. Check config and screen sequence logic.')

    def _get_pattern_type(self, side) -> str:
        pattern = self._get_pattern_from_side(side)
        if pattern == self.config.correct_pattern:
            return 'correct'
        elif pattern == self.config.discovery_pattern:
            return 'discovery'
        else:
            return 'incorrect'

    def _update_discovery_metrics(self):
        # Track number of register responses, ratio of disc vs correct pattern, disc vs incorrect pattern,
        # and average reward per discovery response
        total_disc = self._total_discovery_responses
        self._disc_vs_correct_ratio = self._disc_vs_correct_trials / total_disc if total_disc > 0 else 0
        self._disc_vs_incorrect_ratio = self._disc_vs_incorrect_trials / total_disc if total_disc > 0 else 0

    def _average_reward_in_discovery(self):
        return self._total_disc_rewards / self._total_discovery_responses

    def _log_trial(self):
        """
        Log trial information following this pattern:
        > S4_Trial:
        > left_type_pattern | right_type_pattern
        > response_side
        left_type_pattern and right_type_pattern can be 'correct', 'incorrect' or 'discovery'
        depending on the pattern shown on each side for the current trial
        response_side can be 'l' or 'r'
        """
        left_type = self._get_pattern_type('l')
        right_type = self._get_pattern_type('r')
        response_side = 'l' if self._touch_result[1].startswith('left_') else 'r'
        self.log(logging.INFO, f"""S4_Trial
                               {left_type} | {right_type}
                               Response: {response_side}""")

    def _handle_screen_touch(self):
        self._log_trial()
        super()._handle_screen_touch()

    def check_response(self, pin_name):
        """
        if pin_name.startswith('left_'):
            is_correct = self.config.correct_pattern == self._get_pattern_from_side('l')
        elif pin_name.startswith('right_'):
            is_correct = self.config.correct_pattern == self._get_pattern_from_side('r')
        else:
            raise ValueError('Screen touch triggered from incorrect pin state change')
        self._touch_result = ('correct' if is_correct else 'incorrect', pin_name)
        """
        side = 'l' if pin_name.startswith('left_') else 'r'

        if self.config.correct_pattern == self._get_pattern_from_side(side):
            result = 'correct'
        elif self.config.discovery_pattern == self._get_pattern_from_side(side):
            result = 'discovery'
        else:
            result = 'incorrect'
        self._touch_result = (result, pin_name)

    def get_experiment_status(self):
        self._update_experiment_status()
        return self.stage_status
