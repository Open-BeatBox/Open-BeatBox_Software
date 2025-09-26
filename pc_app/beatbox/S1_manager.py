from __future__ import annotations

import logging
from pathlib import Path

from configobj import ConfigObj
from qtpy import QtWidgets

from beatbox.experiment_managers import BaseExperimentManager, Phase, PhaseSpec, Edge
from beatbox.utils import PinConfig


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

    def __init__(self, experiment_folder, communicator, n_trials=20, inter_trial_interval=10000,
                 main_window=None, actuator_pins_cfg: PinConfig = None):
        super().__init__(experiment_folder, communicator, n_trials, inter_trial_interval, main_window, actuator_pins_cfg)


        # ----------------- phase table -----------------------------------
        self._phase_table_initial = Phase.ITI
        self._phase_table = {
            Phase.ITI: PhaseSpec(
                on_enter=self._phase_iti,
                next_phase_default=Phase.DELIVER_PELLET,
                timeout_ms=self.inter_trial_interval,
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
                relevant_pins={'nose_poke': Edge.RISING}  # FIXME: set proper edge,
            ),

            Phase.DONE: PhaseSpec(
                on_enter=self._phase_done,
                routes=None, # TODO: add routing to next experiment
                decide= lambda instance: False,
                relevant_pins=None
            )
        }

    def _phase_iti(self):
        # turn everything off
        self.set_outputs(flatten=True, flush=True)  # Force defaults
        self.waiting_for_pellet_retrieval = False
        self.last_pellet_retrieved = False

    def _phase_deliver_pellet(self):
        # don't deliver a reward if somehow there's already something there
        if not self.sensors_pins_config._current_states["nose_poke"]:
            self._force_advance_to_wait_pellet()
            return
        self.waiting_for_pellet_delivery = True
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
        self.trial_no += 1  #  TODO: check if here or part of handle_pellet_retrieved  # FIXME: part of pellet retrieval
        self.last_pellet_retrieved = False

        payload = self.actuator_pins_cfg.mask()  # screens off
        self.emit_payload_ready(payload)
        self.trialStarted.emit(self.trial_no)

        self.log(logging.DEBUG, f'Starting trial {self.n_pellets_retrieved + 1} of {self.n_trials}')

    def _phase_done(self):
        self.log(logging.INFO, f'S1 Experiment done.\n'
                                f'Number of trials: {self.trial_no}, number of pellets retrieved: {self.n_pellets_retrieved}')
        self.trial_no = 0

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
        self.last_pellet_retrieved = True # TODO: remove as it is not used anymore by decide
        self.pelletRetrieved.emit()

    def get_experiment_status(self):
        if self.n_pellets_retrieved < self.n_trials:
            return 'incomplete'
        else:
            dlg = QtWidgets.QMessageBox()
            dlg.setText(  # FIXME: end experiment, see how we could instanciate the next stage
                f"""All pellets have been retrieved.
                The stage S{self.stage_number} experiment is now complete.
                You can now move to S{self.stage_number + 1}."""
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
        config_file_path = Path(experiment_folder) / 'config.cfg'
        self.config = ConfigObj(str(config_file_path), encoding='utf-8', unrepr=True)
        self.parse_experiment_config()
        self._setup_file_logging()

    def parse_experiment_config(self):
        self._read_or_write_config('n_trials')
        self._read_or_write_config('inter_trial_interval')
        # FIXME: should we send a signal to the main window to update the UI instead of setting it directly?
        self.main_window.n_trials_spinbox.setValue(self.n_trials)
        self.main_window.n_trials_spinbox.valueChanged.connect(self.handle_n_trials_changed)
        self.main_window.inter_trial_spinbox.setValue(self.inter_trial_interval)
        self.main_window.inter_trial_spinbox.valueChanged.connect(self.handle_inter_trial_interval_changed)

    def _mask(self, *names: str) -> list[int]:
        """
        Wrapper around ``PinConfig.mask`` so event-handlers can build the
        two-byte Beat‐Box payload from **cfg names** (‘reward’, ‘left_pattern_1’ …)
        without touching PinConfig directly.
        """
        return self.actuator_pins_cfg.mask(*names)

    def handle_n_trials_changed(self, value):
        self.n_trials = value
        self.config['n_trials'] = value
        self.config.write()

    def handle_inter_trial_interval_changed(self, value):
        if value is None:
            return
        self.inter_trial_interval = value
        self.config['inter_trial_interval'] = value
        self.config.write()
        self._phase_table[Phase.ITI].set_timeout(value) # FIXME: should this rather be done with a setter ?
