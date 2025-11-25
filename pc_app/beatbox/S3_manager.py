from __future__ import annotations

from pathlib import Path
from qtpy import QtCore

from beatbox.S2_manager import BeatBoxExperimentManagerS2, S2Config
from beatbox.experiment_managers import SubjectInfo
from beatbox.utils import PinConfig

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
        # Any S3-specific initialization can go here

    def reset_state_machine(self):
        """
        Reset the state machine for S3. Override as needed for S3-specific logic.
        """
        # Example: clear trial responses, reset counters, etc.
        self.current_trial_responses = {'correct': [], 'incorrect': []}
        self.previous_trials_responses = {'correct': [], 'incorrect': []}
        self.left_performance = []
        self.right_performance = []
        self.tunnel_entered = False
        self._touch_result = None
        # If parent class has a reset method, call it as well
        if hasattr(super(), 'reset_state_machine'):
            super().reset_state_machine()

