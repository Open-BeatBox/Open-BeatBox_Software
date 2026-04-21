import logging
from pathlib import Path
from typing import List

from beatbox.S2_manager import S2Config
from beatbox.S3_manager import BeatBoxExperimentManagerS3
from beatbox.experiment_managers import SubjectInfo, Phase, PhaseSpec, Edge
from beatbox.utils import PinConfig

class S4Config(S2Config):
    s_level = 'S4'

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
        # Placeholder for S4-specific phase table changes (e.g. custom WAIT_TOUCH)
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
                next_phase_default=Phase.ITI,
                timeout_ms=self.config.error_flash_ms,  # same as incorrect for now
                relevant_pins=None
            )
        })

    def _phase_discovery_response(self):
        self._handle_screen_touch() #
        self.total_valid_trials += 1
        self._go_to_next_trial()
        self.log(logging.DEBUG, f'discovery response entry')

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
