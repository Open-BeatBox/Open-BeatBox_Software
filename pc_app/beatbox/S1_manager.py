from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from configobj import ConfigObj
from qtpy import QtWidgets, QtCore

from beatbox.experiment_managers import BaseExperimentManager, Phase, PhaseSpec, Edge, SConfig, SubjectInfo
from beatbox.utils import PinConfig

class S1Config(SConfig):
    s_level = 'S1'

    n_trials: int
    inter_trial_interval: int

class BeatBoxExperimentManagerS1(BaseExperimentManager):
    """
    Stage 1 experiment manager.
    This is the first concrete implementation of the BaseExperimentManager.
    It handles the basic functionality of the experiment, such as starting trials,
    retrieving pellets, and managing the experiment configuration.

    The skeleton of the state machine is defined in the _phase_table_initial and _phase_table attributes.

    Pellet delivery and pellet arrival logic have been decoupled for clarity.
    """
    stage_number = 1

    trialNumbersUpdated = QtCore.Signal(int, int)

    def __init__(self, experiment_folder, communicator,
                 main_window=None, actuator_pins_cfg: PinConfig = None,
                 subject_info: SubjectInfo = None):
        super().__init__(experiment_folder, communicator, main_window, actuator_pins_cfg, subject_info=subject_info)
        self.config = S1Config(Path(__file__).parent)
        self.config.load()

        # ----------------- phase table -----------------------------------
        self._phase_table_initial = Phase.ITI
        self._phase_table = {
            Phase.ITI: PhaseSpec(
                on_enter=self._phase_iti,
                next_phase_default=Phase.DELIVER_PELLET,
                timeout_ms=self.config.inter_trial_interval,
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

            Phase.DONE: PhaseSpec(
                on_enter=self._phase_done,
                routes=None, # TODO: add routing to next experiment
                decide= lambda instance: False,
                relevant_pins=None
            )
        }

    def _phase_iti(self):
        # TODO: check which one between defaults and screens off is more relevant
        # turn everything off
        # self.turn_off_screens()
        self.set_outputs(default=True, flush=True) # Force defaults
        self.waiting_for_pellet_retrieval = False
        self.trialNumbersUpdated.emit(self.trial_no, self.trial_no)

    def _phase_deliver_pellet(self):
        # don't deliver a reward if somehow there's already something there
        if not self.sensors_pins_config._current_states["nose_poke"]:
            self._force_advance_to_wait_pellet()
            return
        self.waiting_for_pellet_delivery = True
        self.pellet_delivery_loop_count += 1
        self.pelletDeliveryAlert.emit(self.pellet_delivery_loop_count)
        self._deliver_reward() # also resets screen

    # FIXME: this is ugly
    def _phase_wait_pellet(self):
        if not self.sensors_pins_config._current_states["nose_poke"]: # somehow there's already a pellet
            self._force_advance_to_wait_pellet()
            return

    # FIXME: this is ugly, but needed to handle the case where there's already a pellet
    def _force_advance_to_wait_pellet(self):
        if self._current_phase is not Phase.DELIVER_PELLET and self._current_phase is not Phase.WAIT_PELLET:
            return
        # Note: if timeout is not cancelled, it will be called for a phase that does not have a default route and crash
        if self._timer.isActive():
            self._timer.stop() # No need to reset as timer.start(timeout_ms) will do it
        self._goto(Phase.WAIT_PELLET_RETRIEVAL)

    def _phase_wait_pellet_retrieval(self):
        self.waiting_for_pellet_retrieval = True
        self.trial_no += 1
        self.pellet_delivery_loop_count = 0 # reset for next trial
        self.pelletDeliveryAlert.emit(self.pellet_delivery_loop_count)
        payload = self.actuator_pins_cfg.mask()  # screens off
        self.emit_payload_ready(payload)
        self.trialStarted.emit(self.trial_no)

        self.log(logging.DEBUG, f'Starting trial {self.n_pellets_retrieved + 1} of {self.config.n_trials}')

    def _phase_done(self):
        self.log(logging.INFO, f'{self.config.s_level} Experiment done.\n'
                                f'Number of trials: {self.trial_no - 1}, number of pellets retrieved: {self.n_pellets_retrieved}')
        self.trial_no = 0
        self.stageEnded.emit(self.config.s_level)

    def handle_pellet_delivered(self):
        if not self.waiting_for_pellet_delivery:
            self.log(logging.WARNING, 'Pellet delivered, but not waiting for it.')
            return
        self.waiting_for_pellet_delivery = False
        self.pellet_delivered = True

    # called from _dispatch_changed_pin -------------------------------
    def handle_pellet_retrieved(self):
        if not self.waiting_for_pellet_retrieval:
            self.log(logging.WARNING, 'Pellet retrieved, but not waiting for it.')
            return
        self.waiting_for_pellet_retrieval = False
        self.n_pellets_retrieved += 1
        self.pelletRetrieved.emit()

    def get_experiment_status(self):
        if self.n_pellets_retrieved < self.config.n_trials:
            return 'incomplete'
        else:
            dlg = QtWidgets.QMessageBox()
            dlg.setText(  # FIXME: end experiment, see how we could instanciate the next stage
                f"""All pellets have been retrieved.
                The stage {self.config.s_level} experiment is now complete.
                You can now move to next stage."""
            )
            return 'complete'


    def _dispatch_changed_pin(self, pin_name: str, edge: Edge = Edge.BOTH):
        # REFACTOR: we could use e.g. a dataclass to avoid string mismatches
        if pin_name == 'nose_poke':
            if edge == Edge.RISING:
                self.handle_pellet_retrieved()
            elif edge == Edge.FALLING:
                self.handle_pellet_delivered()
            elif edge == Edge.BOTH:
                return

    def set_experiment_folder(self, experiment_folder):
        self.experiment_folder = experiment_folder
        Path(experiment_folder).mkdir(parents=True, exist_ok=True)
        self._setup_logging()
        if self.config.s_level == 'S1':
            self._setup_file_logging(with_light_logger=False)
        else:
            self._setup_file_logging(with_light_logger=True)

    def log(self, level: int, message: str, state_bits: Optional[Dict[str, Any]] = None):
        BaseExperimentManager.log(self, level, message)
        state_bits = state_bits if state_bits is not None else self._get_sensor_bits_from_state_value(self._last_state_value)
        if self.csv_logger:
            self.csv_logger.log(
                stage=self.stage_number,
                log_level=logging.getLevelName(level),
                message=message,
                total_trials=self.n_pellets_retrieved + 1,
                trial_number=self.n_pellets_retrieved + 1,  # +1 for current trial
                correct_side='x',
                corrective_loop=0,
                left_performance=0,
                right_performance=0,
                state_bits=state_bits
            )

    # attach callbacks to stage specific widgets
    def link_widgets(self):
        if self.main_window is None:
            return
        self.main_window.n_trials_spinbox.valueChanged.connect(self.handle_n_trials_changed)
        self.main_window.inter_trial_spinbox.valueChanged.connect(self.handle_inter_trial_interval_changed)

    def _mask(self, *names: str) -> list[int]:
        """
        Wrapper around ``PinConfig.mask`` so event-handlers can build the
        two-byte Beat‐Box payload from **cfg names** (‘reward’, ‘left_pattern_1’ …)
        without touching PinConfig directly.
        """
        return self.actuator_pins_cfg.mask(*names)

    def handle_n_trials_changed(self, value):
        self.config.n_trials = value
        self.config.set('n_trials', value)
        self.config.write()

    def handle_inter_trial_interval_changed(self, value):
        if value is None:
            return
        self.config.inter_trial_interval = value
        self.config.set('inter_trial_interval', value)
        self.config.write()
        self._phase_table[Phase.ITI].set_timeout(value)
