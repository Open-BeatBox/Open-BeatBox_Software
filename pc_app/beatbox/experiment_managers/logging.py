from __future__ import annotations

import csv
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from beatbox.experiment_managers.types import SubjectInfo


class BaseCSVLogger(ABC):
    """Abstract base: manages file creation, flush, close and relative timestamps."""

    def __init__(self, experiment_folder: str | Path, stage_number: int | float,
                 subject_info: SubjectInfo | None = None, prefix: str = ''):
        self.start_time_ms = time.time() * 1000
        timestamp  = datetime.now().isoformat().replace(':', '-')
        subject_id = subject_info.subject_id if subject_info else ''
        parts      = [p for p in [prefix, subject_id, f'S{stage_number}', timestamp] if p]
        filename   = '_'.join(parts) + '.csv'

        self.file_path = Path(experiment_folder) / filename
        self._file     = open(self.file_path, 'w', newline='')
        self._writer   = csv.writer(self._file)
        self._writer.writerow(self._header())

    @abstractmethod
    def _header(self) -> list[str]: ...

    def _write(self, row: list) -> None:
        self._writer.writerow(row)
        self._file.flush()

    def _rel_s(self, ts_ms: float | None) -> float | str:
        """Absolute ms timestamp → relative seconds from logger creation."""
        return round((ts_ms - self.start_time_ms) / 1000, 3) if ts_ms is not None else 'NA'

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class EventCSVLogger(BaseCSVLogger):
    def __init__(self, experiment_folder, stage_number, sensor_pin_names, subject_info=None):
        self.sensor_pin_names = sensor_pin_names
        super().__init__(experiment_folder, stage_number, subject_info, prefix='FULL')

    def _header(self) -> list[str]:
        return [
            'date', 'timestamp_s', 'stage', 'log_level', 'message',
            'correct_side', 'total_trials', 'trial_number', 'corrective_loop',
            'left_performance', 'right_performance'
        ] + [f'state_{name}' for name in self.sensor_pin_names]

    def log(self, *, stage, log_level, message, total_trials, trial_number,
            correct_side, corrective_loop, left_performance, right_performance, state_bits: list) -> None:
        timestamp_s = f'{(time.time() * 1000 - self.start_time_ms) / 1000:.3f}'
        date = datetime.now().isoformat()
        self._write([date, timestamp_s, stage, log_level, message,
                     correct_side, total_trials, trial_number,
                     corrective_loop, left_performance, right_performance,
                     *state_bits])


class TrialCSVLogger(BaseCSVLogger):
    """One row per trial — timing and outcome summary.
       Used from S2 onwards."""
    def __init__(self, experiment_folder, stage_number, subject_info: SubjectInfo = None):
        super().__init__(experiment_folder, stage_number, subject_info, prefix='')  # Check if prefix should be Trial

    def _header(self) -> list[str]:
        return [
            'date', 'show_stim_ts', 'screen_touch_ts', 'reward_collect_ts',
            'total_trials', 'correct_side', 'response', 'reward', 'correct',
            'incorrect', 'valid', 'corrective_loop', 'overall_perf', 'perf_left', 'perf_right'
        ]

    # Reward is just a bool and should be true when we transition to another trial
    # call this method at the end of each trial/when there is a response
    def log(self, *, trial_number, correct_side, response_side, reward, correct,
            incorrect, valid, corrective_loop, overall_performance, left_performance, right_performance,
            show_stim_ts_ms=None, screen_touch_ts_ms=None, reward_collect_ts_ms=None, date=None) -> None:
        self._write([
            date or datetime.now().isoformat(),  # time when entering inter_trial_interval phase
            self._rel_s(show_stim_ts_ms),
            self._rel_s(screen_touch_ts_ms),
            self._rel_s(reward_collect_ts_ms),
            trial_number, correct_side, response_side, reward,
            correct, incorrect, valid, corrective_loop,
            overall_performance, left_performance, right_performance,
        ])
