from beatbox.experiment_managers.s1 import BeatBoxExperimentManagerS1
from beatbox.experiment_managers.s2 import BeatBoxExperimentManagerS2
from beatbox.experiment_managers.s3 import BeatBoxExperimentManagerS3
from beatbox.experiment_managers.s3_1 import BeatBoxExperimentManagerS3_1
from beatbox.experiment_managers.s4 import BeatBoxExperimentManagerS4

_STAGE_MANAGER_CLASSES: dict[str, type] = {
    'S1':   BeatBoxExperimentManagerS1,
    'S2':   BeatBoxExperimentManagerS2,
    'S3':   BeatBoxExperimentManagerS3,
    'S3.1': BeatBoxExperimentManagerS3_1,
    'S4':   BeatBoxExperimentManagerS4,
}
_STAGE_SEQUENCE: dict[str, str | None] = {
    'S1':   'S2',
    'S2':   'S3',
    'S3':   'S3.1',
    'S3.1': 'S4',
    'S4':   None,  # next_label is None for the terminal stage
}


def transition_stage(current_stage: str, experiment_folder, communicator, main_window, subject_info):
    """Create the next stage's manager, or return None if terminal.

    Returns (next_label, manager) or None.
    """
    if current_stage not in _STAGE_SEQUENCE:
        raise ValueError(f'Unknown stage "{current_stage}"')

    next_label = _STAGE_SEQUENCE[current_stage]
    if next_label is None:
        return None

    manager = _STAGE_MANAGER_CLASSES[next_label](experiment_folder, communicator,
                                                 main_window=main_window, subject_info=subject_info)
    return next_label, manager
