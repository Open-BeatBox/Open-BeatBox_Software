from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

from beatbox.experiment_managers.base import SubjectInfo


class EventCSVLogger:
    def __init__(self, experiment_folder, stage_number, sensor_pin_names, subject_info=None):
        self.start_time = time.time()
        self.timestamp = datetime.now().isoformat()
        self.timestamp = self.timestamp.replace(":", "-")  # replace colons for filename compatibility
        if subject_info is not None:
            subject_id = subject_info.subject_id
            log_filename = f'FULL_{subject_id}_S{stage_number}_{self.timestamp}.csv'
        else:
            log_filename = f'FULL_S{stage_number}_{self.timestamp}.csv'
        self.file_path = Path(experiment_folder) / log_filename
        self.sensor_pin_names = sensor_pin_names
        self.file = open(self.file_path, "w", newline="")
        self.writer = csv.writer(self.file)
        header = [
                     'date', 'timestamp_s', 'stage', 'log_level', 'message',
                     'correct_side', 'total_trials', 'trial_number', 'corrective_loop',
                     'left_performance', 'right_performance'
                 ] + [f'state_{name}' for name in sensor_pin_names]
        self.writer.writerow(header)

    def log(self, stage, log_level, message, total_trials, trial_number, correct_side,
            corrective_loop, left_performance, right_performance, state_bits):
        timestamp_s = f'{(time.time() - self.start_time):.3f}'
        date = datetime.now().isoformat()
        row = [
                  date, timestamp_s, stage, log_level, message,
                  correct_side, total_trials, trial_number, corrective_loop,
                  left_performance, right_performance
              ] + list(state_bits)
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


class TrialCSVLogger:
    """One row per trial — timing and outcome summary.
       Used from S2 onwards."""
    def __init__(self, experiment_folder, stage_number, subject_info: SubjectInfo = None):
        self.start_time = time.time() * 1000
        self.timestamp = datetime.now().isoformat()
        self.timestamp = self.timestamp.replace(":", "-")  # replace colons for filename compatibility
        if subject_info is not None:
            subject_id = subject_info.subject_id
            log_filename = f"{subject_id}_S{stage_number}_{self.timestamp}.csv"
        else:
            log_filename = f"S{stage_number}_{self.timestamp}.csv"
        self.file_path = Path(experiment_folder) / log_filename
        self.file = open(self.file_path, "w", newline="")
        self.writer = csv.writer(self.file)
        header = [
            'date', 'show_stim_ts', 'screen_touch_ts', 'reward_collect_ts',
            'total_trials', 'correct_side', 'response', 'reward', 'correct',
            'incorrect', 'valid', 'corrective_loop', 'overall_perf', 'perf_left', 'perf_right'
        ]
        self.writer.writerow(header)

    # Reward is just a bool and should be true when we transition to another trial
    # call this method at the end of each trial/when there is a response
    def log(self, trial_number, correct_side, response_side, reward, correct,
            incorrect, valid, corrective_loop, overall_performance, left_performance, right_performance,
            show_stim_ts=None, screen_touch_ts=None, reward_collect_ts=None, date=None):
        # 'date' should be the time when entering inter_trial_interval phase
        if date is None:
            date = datetime.now().isoformat()
        # Output relative timestamps as float seconds (not string)
        def rel(ts):
            return round((ts - self.start_time)/1000, 3) if ts is not None else "NA"
        row = [
            date,
            rel(show_stim_ts),  # seconds (float)
            rel(screen_touch_ts),                # seconds (float)
            rel(reward_collect_ts),              # seconds (float)
            trial_number, correct_side, response_side, reward, correct,
            incorrect, valid, corrective_loop, overall_performance, left_performance, right_performance
        ]
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()
