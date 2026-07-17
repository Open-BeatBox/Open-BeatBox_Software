from __future__ import annotations

from collections import deque
from pathlib import Path

from beatbox.experiment_managers.s2 import S2Config
from beatbox.experiment_managers.s3 import BeatBoxExperimentManagerS3
from beatbox.experiment_managers.base import SubjectInfo
from beatbox.experiment_managers.patterns import PATTERN_ID, get_pin_names_from_pattern
from beatbox.comm.pin_config import PinConfig


class BeatBoxExperimentManagerS3_1(BeatBoxExperimentManagerS3):
    """
    Stage 3.1 experiment manager.
    Inherits Stage 3 behavior and loads settings from the [S3.1] config section.
    """

    stage_number = 3.1

    def __init__(self, experiment_folder, communicator, main_window=None, actuator_pins_cfg: PinConfig = None,
                 subject_info: SubjectInfo = None):
        super().__init__(experiment_folder, communicator, main_window, actuator_pins_cfg, subject_info=subject_info)
        # Reuse S2/S3 config schema but bind runtime values to the S3.1 section.
        self.config = S2Config(Path(__file__).parent)
        self.config.s_level = 'S3.1'
        self.config.load()
        n_trials = int(self.config.n_trials)
        self.left_responses = deque(maxlen=n_trials)  # values: 'valid', 'invalid', 'incorrect'
        self.right_responses = deque(maxlen=n_trials)

    def _stop_flash(self):
        self.set_outputs(off=get_pin_names_from_pattern(PATTERN_ID.FULL_WHITE, 'both'),
                         flatten=True, flush=False)
        self._advance_trial()
        self.actuatorsStateChanged.emit(self._mask())
        # S3.1 has no corrective loop, but still emit trial data for the panel
        self._emit_trial_data()

