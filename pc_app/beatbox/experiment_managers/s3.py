from __future__ import annotations

from collections import deque
from pathlib import Path
import logging

from beatbox.experiment_managers.s2 import BeatBoxExperimentManagerS2, S2Config
from beatbox.experiment_managers.base import SubjectInfo, BaseExperimentManager
from beatbox.comm.pin_config import PinConfig


class BeatBoxExperimentManagerS3(BeatBoxExperimentManagerS2):
    """
    Stage 3 experiment manager (S2 bis).
    This class extends the S2 experiment manager, reusing its configuration specification.
    Any S3-specific behavior or overrides can be implemented here.
    """
    stage_number = 3

    def __init__(self, experiment_folder, communicator, main_window=None, actuator_pins_cfg: PinConfig = None,
                 subject_info: SubjectInfo = None):
        super().__init__(experiment_folder, communicator, main_window, actuator_pins_cfg, subject_info=subject_info)
        # Override config to use S2Config, but load S3-specific values from experiment.cfg
        self.config = S2Config(Path(__file__).parent)
        self.config.s_level = 'S3'  # For experiment.cfg section, but config spec is still S2
        self.config.load()
        n_trials = int(self.config.n_trials)
        self.left_responses = deque(maxlen=n_trials)  # values: 'valid', 'invalid', 'incorrect'
        self.right_responses = deque(maxlen=n_trials)

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

    def check_performance(self):
        """ Overrides S2 method to check both side"""
        left_performance = self.get_left_performance()
        right_performance = self.get_right_performance()

        return (len(self.left_responses) >= self.config.n_trials and
                len(self.right_responses) >= self.config.n_trials and
                left_performance >= self.config.learning_threshold and
                right_performance >= self.config.learning_threshold)
