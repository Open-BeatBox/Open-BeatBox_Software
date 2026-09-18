# Recording outputs, logs, and metrics

This document describes what the desktop Open-BEATBox application currently records
during an experiment session. It is based on the implementation in
`pc_app/beatbox/experiment_managers.py` and the S1-S4 stage managers.

## Where recordings are written

The operator selects an experiment folder in the GUI. The stage manager writes
CSV files directly into that folder.

For each stage, the application creates a full event log:

```text
FULL_<subject_id>_S<stage>_<timestamp>.csv
FULL_S<stage>_<timestamp>.csv
```

If subject information is available, `<subject_id>` is included in the file
name. Otherwise the shorter form is used. `<timestamp>` is an ISO timestamp
with colons replaced by hyphens for Windows filename compatibility.

For S2 and later stages, the application also creates a trial summary log:

```text
<subject_id>_S<stage>_<timestamp>.csv
S<stage>_<timestamp>.csv
```

S1 currently writes only the full event log.

## Full event CSV

The full event CSV is written by `ExperimentCSVLogger`. It records every call
to the stage manager's `log(...)` method, including phase transitions, input
changes, setup messages, response messages, warnings, errors, and summary
messages emitted by the stage logic.

Columns:

| Column | Meaning |
| --- | --- |
| `date` | Wall-clock ISO timestamp when the row was written. |
| `timestamp_s` | Seconds since this CSV logger was created, formatted to 3 decimals. |
| `stage` | Numeric stage identifier: `1`, `2`, `3`, `3.1`, or `4`. |
| `log_level` | Python logging level name, for example `INFO`, `DEBUG`, `WARNING`, or `ERROR`. |
| `message` | Human-readable event or summary message. Multi-line messages can appear. |
| `correct_side` | Current correct side according to the stage logic. Usually `l`, `r`, or `x`; S1 uses `x`. |
| `total_trials` | Stage-specific total trial counter at log time. In S1 this is `n_pellets_retrieved + 1`; in S2+ this is total screen-touch trials. |
| `trial_number` | Current trial number reported by the stage. S1 uses `n_pellets_retrieved + 1`; S2+ uses `trial_no + 1`. |
| `corrective_loop` | Current corrective-loop count. S1 writes `0`; S2+ writes `corrective_loop_count`. |
| `left_performance` | Current left-side performance ratio. S1 writes `0`; S2+ computes this from recent left responses. |
| `right_performance` | Current right-side performance ratio. S1 writes `0`; S2+ computes this from recent right responses. |
| `state_<sensor>` | One column per configured sensor pin, using the ordered sensor names from `pins_config.cfg`. Values are the current decoded sensor bits. |

The sensor columns are dynamic. With the current pin configuration, expect one
`state_...` column for each configured sensor input. The bit order comes from
the order of the `sensors` section in `pins_config.cfg`, not from physical pin
numbers.

## Trial summary CSV

The trial summary CSV is written by `LightExperimentCSVLogger`. It is used by
S2, S3, S3.1, and S4. It is intended to provide one compact row per scored
response or performance update.

Columns:

| Column | Meaning |
| --- | --- |
| `date` | Wall-clock ISO timestamp when the row was written. |
| `show_stim_ts` | Seconds since this CSV logger was created when the stimulus was shown, or `NA`. |
| `screen_touch_ts` | Seconds since this CSV logger was created when the response screen was touched, or `NA`. |
| `reward_collect_ts` | Seconds since this CSV logger was created when the pellet was retrieved, or `NA`. |
| `total_trials` | Total number of screen-touch trials recorded by the stage. |
| `correct_side` | Correct side for the trial: `l`, `r`, or `x` if invalid/unknown. |
| `response` | Response side selected by the subject: `l`, `r`, or `None` if not set. |
| `reward` | `1` if `correct_side == response`, otherwise `0`. This is side-match based, not a direct hardware reward-delivery audit. |
| `correct` | Cumulative count of correct and valid trials. |
| `incorrect` | Cumulative count of incorrect trials. |
| `valid` | Cumulative count of valid trials. |
| `corrective_loop` | Current corrective-loop count. |
| `overall_perf` | Current overall performance ratio. |
| `perf_left` | Current left-side performance ratio. |
| `perf_right` | Current right-side performance ratio. |

The relative timestamp columns are floats in seconds. They are calculated from
millisecond timestamps captured in the stage manager.

## Metrics

### Trial counters

S1 tracks pellet retrieval training:

| Metric | Source | Meaning |
| --- | --- | --- |
| `trial_no` | S1 manager | Number of completed S1 trials. Incremented when a pellet retrieval completes and no extra reward remains. |
| `n_pellets_retrieved` | S1 manager | Number of pellets detected as retrieved from the nose-poke sensor. |
| `pellet_delivery_loop_count` | S1/S2+ managers | Number of repeated pellet-delivery attempts or delivery-loop alerts. |

S2 and later stages track screen-response trials:

| Metric | Source | Meaning |
| --- | --- | --- |
| `total_trials` | S2+ managers | Number of screen touches handled by `_handle_screen_touch()`. |
| `total_correct_trials` | S2+ managers | Count of correct screen responses before validity/time-pressure filtering. |
| `total_correct_valid_trials` | S2+ managers | Count of correct responses whose pellet retrieval was within `time_pressure_ms`. |
| `total_incorrect_trials` | S2+ managers | Count of incorrect screen responses. |
| `total_valid_trials` | S2+ managers | Count of trials considered valid. Incorrect responses are counted as valid. Correct responses become valid only if pellet retrieval is within `time_pressure_ms`. |
| `total_invalid_trials` | S2+ managers | Count of correct responses where pellet retrieval exceeded `time_pressure_ms`. |
| `total_corrective_loop_trials` | S2+ managers | Count of trials that entered the corrective-loop path. |

### Response and validity

For S2/S3/S3.1, a response is classified by comparing the touched side's
pattern with `correct_pattern`.

For S4, a response can be `correct`, `incorrect`, or `discovery`. Discovery
responses use `discovery_pattern` and can trigger a probabilistic reward roll.

A correct response is only counted as valid if:

```text
pellet_retrieval_time - screen_touch_time <= time_pressure_ms
```

Incorrect responses are counted as valid immediately.

### Performance

Performance is computed from recent responses stored in fixed-length deques.
The maximum deque length is `n_trials` from the stage config.

For each side:

```text
performance = valid_count / (valid_count + incorrect_count)
```

where:

- `valid` means a correct response followed by pellet retrieval within
  `time_pressure_ms`.
- `incorrect` means an incorrect response. For S2 only, late correct responses
  are also appended as `incorrect` as a compatibility hack in the current code.
- invalid late correct responses are otherwise not included in the denominator.

Overall performance is computed with the same formula over left and right
response histories combined.

Stage completion currently uses:

| Stage | Completion rule |
| --- | --- |
| S1 | `n_pellets_retrieved >= n_trials`. |
| S2 | At least `n_trials` combined responses and `overall_perf >= learning_threshold`. |
| S3 | At least `n_trials` left responses, at least `n_trials` right responses, and both side performances above `learning_threshold`. |
| S3.1 | Inherits the S3 completion check. |
| S4 | Currently returns `loop_reward` while discovery rewards remain, otherwise `incomplete`; no final S4 completion rule is implemented. |

### S4 discovery metrics

S4 maintains additional discovery counters:

| Metric | Meaning |
| --- | --- |
| `_total_discovery_responses` | Number of discovery responses. |
| `_total_disc_rewards` | Number of successful discovery reward phases entered. |
| `_disc_vs_correct_trials` | Discovery responses when the opposite side carried the correct pattern. |
| `_disc_vs_incorrect_trials` | Discovery responses when the opposite side carried the incorrect pattern. |
| `_disc_vs_correct_ratio` | `_disc_vs_correct_trials / _total_discovery_responses`. |
| `_disc_vs_incorrect_ratio` | `_disc_vs_incorrect_trials / _total_discovery_responses`. |
| `_disc_average_reward` | Intended average reward metric, but the stored field is not currently updated. |

These discovery metrics are not written as dedicated CSV columns. They appear
only indirectly through full-log messages such as `S4_Trial`,
`S4_random_roll = S4_roll_success`, and `S4_random_roll = S4_roll_failed`.

## Runtime logs

The application configures the Python root logger in `bb_logging.py`.

Runtime log destinations:

| Destination | Format | Purpose |
| --- | --- | --- |
| Standard output | `%(asctime)s - %(levelname)s - %(message)s` | Console diagnostics while running the desktop app. |
| GUI log panel | Same formatted message emitted through `QtHandler` | Operator-visible status, warnings, and errors. |
| Full event CSV | Structured CSV rows from stage-manager log calls | Recording artifact for experiment analysis and debugging. |

Serial communication also logs diagnostic messages through the root logger,
including connection attempts, handshakes, sent frames, received frames, ACKs,
warnings, and errors. These messages are visible in the console and GUI log
panel. They are only written into the full event CSV when they pass through a
stage manager's `log(...)` method; low-level communicator log records are not
automatically copied into the experiment CSV.

## Recorded events by stage

### S1

S1 records pellet-delivery and retrieval training. The full event CSV includes:

- stage start and config dump;
- subject information, if provided;
- phase transitions;
- pellet delivered/retrieved warnings when events happen outside the expected phase;
- final experiment-done message with trial and pellet counts;
- sensor-state columns for each logged row.

### S2

S2 adds tunnel entry, stimulus display, left/right responses, correctness,
validity, pellet retrieval timing, corrective loops, and performance.

The full event CSV includes phase transitions, response messages, experiment
data summaries, and current performance values. The trial summary CSV records
the stimulus, touch, and reward-collection timestamps plus cumulative trial and
performance metrics.

### S3 and S3.1

S3 and S3.1 reuse the S2 logging outputs and metrics. The main behavioral
difference for documentation purposes is the completion criterion: both left
and right side histories must independently satisfy `n_trials` and
`learning_threshold`.

### S4

S4 reuses the S2/S3 CSV schemas and adds discovery-trial messages to the full
event log. The CSV schema does not currently have explicit S4 columns for
discovery pattern type, random roll value, reward probability, or number of
discovery rewards.

## Current caveats

- The CSV schemas are implicit in code. There is no version column in either
  CSV file.
- S1's full CSV uses `total_trials` and `trial_number` values based on
  `n_pellets_retrieved + 1`, so early rows can look like the next trial rather
  than a completed-trial count.
- The trial summary `reward` column is computed from side equality, not from a
  confirmed actuator pulse or pellet sensor event.
- Low-level serial logs are not persisted to a separate text file by default.
- S4 discovery metrics are tracked in memory but are not exported as structured
  CSV columns.
- CSV files flush after every row, but the current `end_experiment()` close path
  only checks `self.logger` and does not explicitly close `csv_logger` or
  `light_logger`.
