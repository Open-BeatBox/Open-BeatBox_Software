"""Self-contained widget for one BeatBox device.

Contains connection controls, experiment controls, hardware panel,
and stage panel. Emits status signals for ``DeviceSummaryCard``.
"""
import logging

from qtpy.QtCore import Qt, Signal, Slot, QThread
from qtpy.QtWidgets import (QWidget, QGroupBox, QHBoxLayout, QVBoxLayout, QFormLayout, QComboBox, QPushButton,
                            QLabel, QStackedWidget, QFileDialog, QMessageBox, QProgressDialog)

from beatbox.comm.serial_com import BeatBoxCommunicator, HandshakeWorker, get_platform_serial_ports
from beatbox.experiment_managers.types import SubjectInfo
from beatbox.experiment_managers.registry import _STAGE_MANAGER_CLASSES, transition_stage

from beatbox.gui.hardware_panel import HardwarePanel
from beatbox.gui.stage_panels import create_stage_panel
from beatbox.gui.qt_utils import (labeled_spinbox, push_button, swap_button,
                                  selectable_label, safe_delete, add_form_fields, FieldSpec)
from beatbox.gui.bb_logging import logger

BAUD_RATE = 921600
SERIAL_TIMEOUT = 20


class BBDeviceWidget(QWidget):
    """One BeatBox device: communicator + manager + panels.

    The unit is self-contained — it owns its serial connection,
    experiment manager, hardware panel and stage panel.

    Parameters
    ----------
    stage : str
        Initial stage label ('S1', 'S2', …).
    subject_info : SubjectInfo, optional
        Animal metadata.
    with_neopixels : bool
        Whether to show neopixel controls.
    experiment_folder : str
        Path to the experiment folder.
    n_trials : int
        Number of trials.
    inter_trial_interval : int
        ITI in milliseconds.
    transition : bool
        Whether to auto-transition between stages.
    name : str
        Human-readable name for this device (e.g. 'Box 1').
    description : str
        Optional description (e.g. 'Left rig, room 4B').
    communicator : BeatBoxCommunicator, optional
        Injectable for testing. If None, a real one is created.
    """
    connectionStatusChanged = Signal(bool)
    portClaimed             = Signal()
    trialDataChanged        = Signal(dict)
    performanceChanged      = Signal(object)
    phaseChanged            = Signal(str)

    def __init__(self, stage='S1', subject_info=None, with_neopixels=False,
                 experiment_folder='', n_trials=20, inter_trial_interval=10000,
                 transition=False, name='', description='', pins_config_file='',
                 communicator=None, parent=None):
        super().__init__(parent)
        self.name              = name or f'Device {id(self)}'
        self.description       = description
        self.subject_info      = subject_info
        self.enable_transitions = transition
        self.port              = ''

        # ── Communicator (injectable for tests) ───────────────────
        self.communicator = communicator or  BeatBoxCommunicator(baud_rate=BAUD_RATE, serial_timeout=SERIAL_TIMEOUT)
        self.communicator.connected.connect(self._on_connected)
        self.communicator.handshakeCompleted.connect(self._on_handshake)
        self.communicator.errorOccurred.connect(self._on_error)

        # ── Experiment manager ────────────────────────────────────
        if stage not in _STAGE_MANAGER_CLASSES:
            raise ValueError(f'Unknown stage "{stage}"')
        self._experiment_manager = _STAGE_MANAGER_CLASSES[stage](experiment_folder, self.communicator,
                                                                 main_window=self, subject_info=subject_info)

        # ── Panels ────────────────────────────────────────────────
        self._hw_panel    = HardwarePanel(self._experiment_manager, with_neopixels=with_neopixels)
        self._stage_stack = QStackedWidget()

        # ── State ─────────────────────────────────────────────────
        self._serial_connected = False
        self._folder_selected  = bool(experiment_folder)

        # ── Handshake thread ──────────────────────────────────────
        self._handshake_q_thread = QThread(self)  # stop life with the widget
        self._handshake_worker   = HandshakeWorker(self.communicator)
        self._connect_progress   = None

        self._build_ui()
        self._set_stage_panel(stage)
        self._connect_manager_signals()
        self.experiment_manager.link_widgets()

    # ─── Manager property ─────────────────────────────────────────

    @property
    def experiment_manager(self):
        return self._experiment_manager

    @experiment_manager.setter
    def experiment_manager(self, manager):
        self._disconnect_manager_signals()
        old = self._experiment_manager
        self._experiment_manager = manager
        self._hw_panel.set_manager(manager)
        if old is not None:
            old.deleteLater()

    @property
    def claimed_port(self) -> str:
        """Return the port this device is connected to, or '' if not connected."""
        return self.port if self._serial_connected else ''

    @property
    def hw_panel(self) -> HardwarePanel:
        return self._hw_panel

    # ─── UI construction ──────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_experiment_controls())
        layout.addWidget(self._stage_stack)
        layout.addWidget(self._hw_panel)
        layout.addWidget(self._build_info_row())

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox('Serial Connection')
        lay   = QHBoxLayout(group)

        self.port_combobox = QComboBox(self)
        self.port_combobox.setMinimumWidth(100)
        self.refresh_ports()

        self.connect_button = QPushButton('Connect')
        self.connect_button.clicked.connect(self.connect_serial)
        self.refresh_ports_btn = push_button('Refresh', on_clicked=self.refresh_ports)

        lay.addWidget(QLabel('Port:'))
        lay.addWidget(self.port_combobox)
        lay.addWidget(self.refresh_ports_btn)
        lay.addWidget(self.connect_button)
        return group

    def _build_experiment_controls(self) -> QGroupBox:
        group = QGroupBox('Experiment Setup')
        group.setObjectName('experiment_group')
        lay   = QHBoxLayout(group)

        self.experiment_button = push_button('Select Folder', on_clicked=self.select_experiment_folder)
        lay.addWidget(self.experiment_button)

        lbl, self.n_trials_spinbox = labeled_spinbox('n_trials:', lo=1, hi=9999,
                                                     value=self.experiment_manager.config.n_trials)
        lay.addWidget(lbl)
        lay.addWidget(self.n_trials_spinbox)

        lbl, self.inter_trial_spinbox = labeled_spinbox('Inter trial (ms):', lo=1, hi=999_999,
                                                        value=self.experiment_manager.config.inter_trial_interval)
        lay.addWidget(lbl)
        lay.addWidget(self.inter_trial_spinbox)

        self.experiment_state_btn = push_button('Start Experiment', on_clicked=self.start_experiment, enabled=False)
        lay.addWidget(self.experiment_state_btn)
        return group

    def _build_info_row(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._build_animal_info())

        stage_phase = QGroupBox('Stage/Phase Info')
        form = QFormLayout(stage_phase)
        self.stage_label = selectable_label(str(self.experiment_manager.config.s_level))
        self.phase_label = selectable_label()
        form.addRow('Current Stage:', self.stage_label)
        form.addRow('Current Phase:', self.phase_label)
        lay.addWidget(stage_phase)
        return row

    def _build_animal_info(self) -> QGroupBox:
        """Create a group box displaying animal information."""
        group  = QGroupBox('Animal Information')
        layout = QFormLayout(group)
        info   = self.subject_info or SubjectInfo()
        add_form_fields(layout, self, [
            FieldSpec('Animal name:', 'animal_name_label', info.subject_id),
            FieldSpec('Weight (g):',  'weight_label',      str(info.weight)),
            FieldSpec('Box:',         'box_label',          info.box),
            FieldSpec('Sex:',         'sex_label',          info.sex),
            FieldSpec('Light cycle:', 'light_cycle_label',  info.light_cycle),
        ])
        return group

    # ─── Stage panel management ───────────────────────────────────

    def _set_stage_panel(self, stage_label: str):
        """Replace the current stage panel in the stack."""
        while self._stage_stack.count():
            old = self._stage_stack.widget(0)
            self._stage_stack.removeWidget(old)
            safe_delete(old)
        panel = create_stage_panel(stage_label, self.experiment_manager)
        self._stage_stack.addWidget(panel)
        self._stage_stack.setCurrentWidget(panel)

    # ─── Signal wiring ────────────────────────────────────────────

    def _connect_manager_signals(self):
        """Wire experiment manager signals to panels and main window slots.

        Stage-specific signal connections are handled inside each
        ``StagePanel._connect_signals`` — this method only does
        stage-agnostic wiring.
        """
        mgr = self.experiment_manager

        # Internal routing
        mgr.sensorsStateChanged.connect(self._hw_panel.paint_sensors)
        mgr.actuatorsStateChanged.connect(self._hw_panel.paint_actuators)
        mgr.pelletDeliveryAlert.connect(self._hw_panel.show_pellet_alert)

        self._hw_panel.manualSetRequested.connect(mgr.request_manual_action_by_pins_and_rgb)

        # Main window routing
        mgr.stageEnded.connect(self.handle_stage_end)
        mgr.phaseChanged.connect(self._update_phase_label)
        mgr.stageChanged.connect(self._update_stage_label)

        # Forward to summary card
        mgr.trialDataUpdated.connect(self.trialDataChanged)
        mgr.performanceUpdated.connect(self.performanceChanged)
        mgr.phaseChanged.connect(self.phaseChanged)

    def _disconnect_manager_signals(self):
        """Disconnect current manager before swapping."""
        mgr = self.experiment_manager
        pairs = [
            (mgr.sensorsStateChanged,   self._hw_panel.paint_sensors),
            (mgr.actuatorsStateChanged, self._hw_panel.paint_actuators),
            (mgr.pelletDeliveryAlert,   self._hw_panel.show_pellet_alert),
            (mgr.stageEnded,            self.handle_stage_end),
            (mgr.phaseChanged,          self._update_phase_label),
            (mgr.stageChanged,          self._update_stage_label),
            (mgr.trialDataUpdated,      self.trialDataChanged),
            (mgr.performanceUpdated,    self.performanceChanged),
            (mgr.phaseChanged,          self.phaseChanged),
        ]
        for sig, slot in pairs:
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        try:
            self._hw_panel.manualSetRequested.disconnect(mgr.request_manual_action_by_pins_and_rgb)
        except (TypeError, RuntimeError):
            pass

    # ─── Serial connection ────────────────────────────────────────

    def refresh_ports(self, exclude: set[str] = None):
        """Scan for available serial ports, excluding already-claimed ones."""
        exclude = exclude or set()
        self.port_combobox.clear()
        for p in get_platform_serial_ports():
            if p.device not in exclude:
                self.port_combobox.addItem(p.device)

    def connect_serial(self):
        """
        Connect to the selected serial port and initiate a handshake with the BeatBox device.
        The connection process is handled in a separate thread to avoid blocking the UI.
        """
        self.connect_button.setEnabled(False)
        port_name = self.port_combobox.currentText()
        if not port_name:
            logger.warning(f'[{self.name}] No serial port selected.')
            self.connect_button.setEnabled(True)
            return

        self._connect_progress = self._create_connection_progress()
        self.communicator.connect_port(port_name)
        self.port = port_name

        # offload the handshake to a worker thread
        self._handshake_worker.moveToThread(self._handshake_q_thread)
        self._handshake_q_thread.started.connect(self._handshake_worker.run)
        self._handshake_worker.finished.connect(self._on_handshake_done)
        self._handshake_worker.finished.connect(self._handshake_q_thread.quit)
        self._handshake_q_thread.start()

        # Initiate handshake
        # self._handshake_q_thread.start()


    def _create_connection_progress(self) -> QProgressDialog:
        dlg = QProgressDialog('Connecting to BeatBox device…', None,
                              0, 0, self)
        dlg.setWindowTitle('Connecting')
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setMinimumDuration(0)  # show immediately
        dlg.setAutoClose(False)
        return dlg

    def _on_connected(self):
        logger.info(f'[{self.name}] Serial port connected.')

    def _on_handshake(self):  # TODO: check if we need commented code
        # Set screen to default state
        # self._update_experiment_state_btn()
        self.experiment_manager.handshake_completed()

    @Slot(bool)
    def _on_handshake_done(self, ok: bool):
        self._connect_progress.close()
        if ok:
            self._serial_connected = True
            self._update_experiment_state_btn()
            self.portClaimed.emit()  # tell the app to refresh other devices
            logger.info(f'[{self.name}] Handshake successful on "{self.port}".')
        else:
            self.connect_button.setEnabled(True)
            logger.warning(f'[{self.name}] Handshake failed on "{self.port}".')
        self.connectionStatusChanged.emit(ok)

    def _on_error(self, msg):
        logger.error(f'[{self.name}] {msg}')

    # ─── Experiment lifecycle ─────────────────────────────────────

    def select_experiment_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select Experiment Folder')
        if folder:
            self.experiment_manager.set_experiment_folder(folder)
            self._folder_selected = True
            self._update_experiment_state_btn()

    def start_experiment(self):
        self.experiment_manager.start_experiment()
        swap_button(self.experiment_state_btn, 'Stop Experiment', self.start_experiment, self.stop_experiment)

    def stop_experiment(self):
        self.experiment_manager.end_experiment()
        swap_button(self.experiment_state_btn, 'Start Experiment', self.stop_experiment, self.start_experiment)
        self._update_experiment_state_btn()
        logger.debug('Stopping experiment.')

    def handle_stage_end(self, current_stage: str):
        """Transition to the next stage or show completion message."""
        if not self.enable_transitions:
            return

        result = transition_stage(current_stage, self.experiment_manager.experiment_folder, self.communicator,
                                  main_window=self, subject_info=self.subject_info)
        if result is None:  # terminal stage: stop
            QMessageBox.information(self, f'Stage {current_stage} complete', 'Experiment complete.')
            return

        next_label, manager = result
        logger.info(f'[{self.name}] Stage {current_stage} → {next_label}')

        # Property setter handles disconnect, deleteLater, hw_panel update
        self.experiment_manager = manager

        # Swap panel and rewire
        self._set_stage_panel(next_label)
        self._connect_manager_signals()
        self._hw_panel.clear_pellet_alert()

        # Sync base spinboxes
        self.n_trials_spinbox.setValue(self.experiment_manager.config.n_trials)
        self.inter_trial_spinbox.setValue(self.experiment_manager.config.inter_trial_interval)

        self.experiment_manager.link_widgets()
        self.experiment_manager.start_experiment()

    def _update_experiment_state_btn(self):
        self.experiment_state_btn.setEnabled(self._serial_connected and self._folder_selected)

    def _update_phase_label(self, phase_name: str):
        self.phase_label.setText(phase_name)

    def _update_stage_label(self, stage_name: str):
        self.stage_label.setText(stage_name)

    def close(self):
        self._serial_connected = False
        self._folder_selected = False
        self.experiment_state_btn.setEnabled(False)
        self.communicator.close()
        super().close()
