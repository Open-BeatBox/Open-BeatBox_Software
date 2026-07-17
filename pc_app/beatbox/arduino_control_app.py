"""
This module provides a GUI application to control the BeatBox Arduino device.

It allows users to connect to the device, control actuators and monitor sensors directly
as well as run experiments using classes derived from `BaseExperimentManager` like
`BeatBoxExperimentManagerS1` or `BeatBoxExperimentManagerS2`.

The main window is composed of three panels:
  * ``HardwarePanel``  — sensors, actuators, neopixels (stage-agnostic)
  * ``StagePanel``     — experiment data display and stage controllers
  * connection / experiment chrome — serial port, folder, start/stop
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import warnings
from pathlib import Path
import logging

from configobj import ConfigObj

from qtpy import QtCore
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QMainWindow, QTextEdit, QComboBox, QPushButton,
    QVBoxLayout, QWidget, QGroupBox, QHBoxLayout, QFormLayout,
    QFileDialog, QMessageBox, QProgressDialog, QErrorMessage,
    QDialog, QLineEdit, QApplication, QLabel, QStackedWidget,
)
import qdarkstyle

from beatbox.bb_logging import qt_handler, logger
from beatbox.experiment_managers import SubjectInfo
from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.S2_manager import BeatBoxExperimentManagerS2
from beatbox.S3_manager import BeatBoxExperimentManagerS3
from beatbox.S3_1_manager import BeatBoxExperimentManagerS3_1
from beatbox.S4_manager import BeatBoxExperimentManagerS4
from beatbox.qt_utils import (labeled_spinbox, push_button, swap_button, combo_box,
                              selectable_label, safe_delete, add_form_fields, FieldSpec)
from beatbox.serial_com import get_platform_serial_ports, BeatBoxCommunicator, BeatBoxMessage, HandshakeWorker
from beatbox.hardware_panel import HardwarePanel
from beatbox.stage_panels import create_stage_panel


SERIAL_TIMEOUT = 2
BAUD_RATE = 115200


_STAGE_MANAGER_CLASSES: dict[str, type] = {
    'S1':   BeatBoxExperimentManagerS1,
    'S2':   BeatBoxExperimentManagerS2,
    'S3':   BeatBoxExperimentManagerS3,
    'S3.1': BeatBoxExperimentManagerS3_1,
    'S4':   BeatBoxExperimentManagerS4,
}

# next_label is None for the terminal stage
_STAGE_SEQUENCE: dict[str, str | None] = {
    'S1':   'S2',
    'S2':   'S3',
    'S3':   'S3.1',
    'S3.1': 'S4',
    'S4':   None,
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



class ArduinoControlApp(QMainWindow):

    def __init__(self, pins_config_file, experiment_folder='', with_neopixels=False,
                 n_trials=20, inter_trial_interval=10000, stage='S1',
                 transition=False, subject_info=None):
        super().__init__()
        self.subject_info = subject_info
        self.enable_transitions = transition

        # Log area — created early so error handlers can use it
        self.log_text = QTextEdit('Log:')
        self.log_text.setReadOnly(True)

        # Communicator
        self.communicator = BeatBoxCommunicator(baud_rate=BAUD_RATE, serial_timeout=SERIAL_TIMEOUT)
        self.communicator.connected.connect(self._on_connected)
        self.communicator.handshakeCompleted.connect(self._on_handshake)
        self.communicator.errorOccurred.connect(self._on_error)

        # Experiment manager
        if stage in _STAGE_MANAGER_CLASSES:
            self._experiment_manager = _STAGE_MANAGER_CLASSES[stage](experiment_folder, self.communicator,
                                                                    main_window=self, subject_info=subject_info)
        else:
            raise ValueError(f'Unknown stage "{stage}"')

        # Panels
        self._hw_panel = HardwarePanel(self._experiment_manager, with_neopixels=with_neopixels)
        self._stage_stack = QStackedWidget()

        # Build UI shell, then populate stage panel and wire signals
        self.setWindowTitle('Beatbox Controller')
        self._build_ui()
        self._set_stage_panel(stage)
        self._connect_manager_signals()
        self.experiment_manager.link_widgets()

        # Connect global log signals to our UI append function.
        qt_handler.logSignal.connect(self.append_log)

        # Handshake thread
        self._handshake_q_thread = QtCore.QThread(self)  # stop with the window
        self._handshake_worker = HandshakeWorker(self.communicator)

        self._connect_progress = None

        # State
        self._serial_connected = False
        self._folder_selected = False

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

    def _build_ui(self):
        """Compose the main window from panels — no stage-specific logic."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        main_layout.addWidget(self._build_connection_group())
        main_layout.addWidget(self._build_experiment_group())
        main_layout.addWidget(self._stage_stack)
        main_layout.addWidget(self._hw_panel)
        main_layout.addWidget(self._build_info_row())
        main_layout.addWidget(self.log_text)

    def _build_connection_group(self) -> QGroupBox:
        connection_group = QGroupBox('Serial Connection')
        connection_layout = QHBoxLayout(connection_group)

        self.port_combobox = QComboBox(self)
        self.port_combobox.setMinimumWidth(100)
        self.refresh_ports()

        self.connect_button = QPushButton('Connect')
        self.connect_button.clicked.connect(self.connect_serial)
        self.refresh_ports_btn = push_button('Refresh', on_clicked=self.refresh_ports)

        connection_layout.addWidget(QLabel('Port:'))
        connection_layout.addWidget(self.port_combobox)
        connection_layout.addWidget(self.refresh_ports_btn)
        connection_layout.addWidget(self.connect_button)
        return connection_group

    def _build_experiment_group(self) -> QGroupBox:
        group = QGroupBox('Experiment Setup')
        group.setObjectName('experiment_group')
        lay = QHBoxLayout(group)

        self.experiment_button = push_button('Select Folder', on_clicked=self.select_experiment_folder)
        lay.addWidget(self.experiment_button)

        # Controls for n_trials and inter_trial_interval
        lbl, self.n_trials_spinbox = labeled_spinbox('n_trials:', lo=1, hi=9999,
                                                     value=self.experiment_manager.n_trials)
        lay.addWidget(lbl)
        lay.addWidget(self.n_trials_spinbox)

        lbl, self.inter_trial_spinbox = labeled_spinbox('Inter trial (ms):', lo=1, hi=999_999,
                                                        value=self.experiment_manager.inter_trial_interval)
        lay.addWidget(lbl)
        lay.addWidget(self.inter_trial_spinbox)

        self.experiment_state_btn = push_button('Start Experiment', on_clicked=self.start_experiment, enabled=False)
        lay.addWidget(self.experiment_state_btn)
        return group

    def _build_info_row(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        # Left: Animal info
        lay.addWidget(self._build_animal_info())

        # Right: Stage and phase info
        stage_phase_group = QGroupBox('Stage/Phase Info')
        form = QFormLayout(stage_phase_group)
        self.stage_label = selectable_label(str(self.experiment_manager.config.s_level))
        self.phase_label = selectable_label()
        form.addRow('Current Stage:', self.stage_label)
        form.addRow('Current Phase:', self.phase_label)
        lay.addWidget(stage_phase_group)
        return row

    def _build_animal_info(self) -> QGroupBox:
        """Create a group box displaying animal information."""
        group = QGroupBox('Animal Information')
        layout = QFormLayout(group)
        info = self.subject_info or SubjectInfo()
        add_form_fields(layout, self, [
            FieldSpec('Animal name:', 'animal_name_label', info.subject_id),
            FieldSpec('Weight (g):',  'weight_label',      str(info.weight)),
            FieldSpec('Box:',         'box_label',          info.box),
            FieldSpec('Sex:',         'sex_label',          info.sex),
            FieldSpec('Light cycle:', 'light_cycle_label',  info.light_cycle),
        ])
        return group

    # ═══════════════════════════════════════════════════════════════════
    #  STAGE PANEL MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════

    def _set_stage_panel(self, stage_label: str):
        """Replace the current stage panel in the stack."""
        while self._stage_stack.count():
            old = self._stage_stack.widget(0)
            self._stage_stack.removeWidget(old)
            safe_delete(old)
        panel = create_stage_panel(stage_label, self.experiment_manager)
        self._stage_stack.addWidget(panel)
        self._stage_stack.setCurrentWidget(panel)

    # ═══════════════════════════════════════════════════════════════════
    #  SIGNAL WIRING
    # ═══════════════════════════════════════════════════════════════════

    def _connect_manager_signals(self):
        """Wire experiment manager signals to panels and main window slots.

        Stage-specific signal connections are handled inside each
        ``StagePanel._connect_signals`` — this method only does
        stage-agnostic wiring.
        """
        mgr = self.experiment_manager

        # Hardware panel
        mgr.sensorsStateChanged.connect(self._hw_panel.paint_sensors)
        mgr.actuatorsStateChanged.connect(self._hw_panel.paint_actuators)
        mgr.pelletDeliveryAlert.connect(self._hw_panel.show_pellet_alert)

        # Hardware panel → manager
        self._hw_panel.manualSetRequested.connect(mgr.request_manual_action_by_pins_and_rgb)

        # Main window
        mgr.stageEnded.connect(self.handle_stage_end)
        mgr.phaseChanged.connect(self._update_phase_label)
        mgr.stageChanged.connect(self._update_stage_label)

    def _disconnect_manager_signals(self):
        """Disconnect current manager before swapping."""
        mgr = self.experiment_manager
        for sig, slot in [
            (mgr.sensorsStateChanged,  self._hw_panel.paint_sensors),
            (mgr.actuatorsStateChanged,self._hw_panel.paint_actuators),
            (mgr.pelletDeliveryAlert,  self._hw_panel.show_pellet_alert),
            (mgr.stageEnded,           self.handle_stage_end),
            (mgr.phaseChanged,         self._update_phase_label),
            (mgr.stageChanged,         self._update_stage_label),
        ]:
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        try:
            self._hw_panel.manualSetRequested.disconnect(mgr.request_manual_action_by_pins_and_rgb)
        except (TypeError, RuntimeError):
            pass

    # ═══════════════════════════════════════════════════════════════════
    #  SERIAL CONNECTION
    # ═══════════════════════════════════════════════════════════════════

    def refresh_ports(self):
        """
        Scan for available serial ports and populate the port_combobox.
        """
        self.port_combobox.clear()
        for port in get_platform_serial_ports():
            self.port_combobox.addItem(port.device)

    def connect_serial(self):
        """
        Connect to the selected serial port and initiate a handshake with the BeatBox device.
        The connection process is handled in a separate thread to avoid blocking the UI.
        """
        self.connect_button.setEnabled(False)
        port_name = self.port_combobox.currentText()
        if not port_name:
            self.append_log('No serial port selected.', logging.WARNING)
            self.connect_button.setEnabled(True)
            return

        self._connect_progress = self._create_connection_progress()
        self.communicator.connect_port(port_name)

        # offload the handshake to a worker thread
        self._handshake_worker.moveToThread(self._handshake_q_thread)
        self._handshake_q_thread.started.connect(self._handshake_worker.run)

        self._handshake_worker.finished.connect(self._on_handshake_done)
        self._handshake_worker.finished.connect(self._handshake_q_thread.quit)

        # Initiate handshake
        self._handshake_q_thread.start()

    @QtCore.Slot(bool)
    def _on_handshake_done(self, ok: bool):
        self._connect_progress.close()

        if ok:            # connect btn stays disabled
            QMessageBox.information(self, 'BeatBox', 'Connection established!')
        else:
            error_message = (f'Could not establish communication with port {self.port_combobox.currentText()}. '
                             f'Please check the serial port and try again.')
            QMessageBox.critical(self, 'BeatBox', error_message)
            self.connect_button.setEnabled(True)

    def _create_connection_progress(self):
        dlg = QProgressDialog('Connecting to BeatBox device…', cancelButtonText=None,
                              minimum=0, maximum=0, parent=self)
        dlg.setWindowTitle('Connecting')
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setMinimumDuration(0)  # show immediately
        dlg.setAutoClose(False)
        return dlg

    def _on_connected(self):
        self.append_log('Serial port connected.', logging.INFO)

    def _on_handshake(self):
        self.append_log('Handshake completed.', logging.INFO)
        self._serial_connected = True
        # Set screen to default state
        self._update_experiment_state_btn()
        self.experiment_manager.handshake_completed()

    # ═══════════════════════════════════════════════════════════════════
    #  EXPERIMENT LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

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
        self.append_log(f'Stage {current_stage} complete. Moving to "{next_label}".', logging.INFO)

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

    def _on_error(self, msg: BeatBoxMessage | str):
        err = msg.payload if isinstance(msg, BeatBoxMessage) else msg
        self.append_log(f'Error: {err}', logging.ERROR)
        dialog = QErrorMessage()
        dialog.showMessage(err)
        dialog.exec_()

    def append_log(self, message: str, level: int = logging.DEBUG):
        colors = {logging.INFO: 'yellow', logging.WARNING: 'orange', logging.ERROR: 'red'}
        color = colors.get(level, 'white')
        formatted = f'<font color="{color}">{message}</font>'
        self.log_text.append(formatted)

    def closeEvent(self, event):
        """
        Triggers when window is closed.
        Ensure the communicator shuts down cleanly.
        """
        self._serial_connected = False
        self._folder_selected = False
        self.experiment_state_btn.setEnabled(False)
        self.communicator.close()
        super().closeEvent(event)


class StageSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Select Experiment Stage')
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.combo = combo_box(list(_STAGE_MANAGER_CLASSES.keys()))
        self.combo.insertItem(0, 'All')
        self.combo.setCurrentIndex(0)

        # Animal info fields
        self.animal_name_edit = QLineEdit()
        self.weight_edit = QLineEdit()
        self.box_edit = QLineEdit()
        self.sex_combo = combo_box(['X', 'Male', 'Female'])  # Default unset at X
        self.light_cycle_combo = combo_box(['X', 'Red', 'White'])  # Default unset at X

        form.addRow('Experiment stage:', self.combo)
        form.addRow('Animal name:',      self.animal_name_edit)
        form.addRow('Weight (g):',       self.weight_edit)
        form.addRow('Box:',              self.box_edit)
        form.addRow('Sex:',              self.sex_combo)
        form.addRow('Light cycle:',      self.light_cycle_combo)

        layout.addLayout(form)
        layout.addWidget(push_button('OK', on_clicked=self.accept))

    def get_subject_info(self):
        return SubjectInfo(
            subject_id=self.animal_name(),
            weight=self.weight(),
            box=self.box(),
            sex=self.sex(),
            light_cycle=self.light_cycle()
        )

    def selected_stage(self):
        return self.combo.currentText()
    def animal_name(self):
        return self.animal_name_edit.text()
    def weight(self):
        return self.weight_edit.text()
    def box(self):
        return self.box_edit.text()
    def sex(self):
        return self.sex_combo.currentText()
    def light_cycle(self):
        return self.light_cycle_combo.currentText()


def main():

    parser = argparse.ArgumentParser('BeatBox Arduino Control App',
                                     description='This app allows you to control the BeatBox from a python GUI.')
    parser.add_argument('--with-neopixels', action='store_true', default=False,
                        help='Include neopixel controls in the GUI.')
    parser.add_argument('--n-trials', type=int, default=20, help='Number of trials to run.')
    parser.add_argument('--inter-trial-interval', type=int, default=10000,
                        help='Interval between trials in milliseconds.')
    parser.add_argument('--pins-config-file', dest='pins_config_file',
                        type=str, default='~/.beatbox/pins_config.cfg',
                        help='Path to the pins configuration file. You should not have to modify this.')
    parser.add_argument('--from-config-file', type=str, default='',
                        help='Path to a configuration file to load settings from.')
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet())

    config_path = Path(args.pins_config_file).expanduser()
    if not config_path.exists():
        warnings.warn(f'Config file {config_path} not installed. Defaulting to the one in the package.')
        config_path = Path(__file__).parent / 'pins_config.cfg'

    # Prompt for stage selection before instantiating ArduinoControlApp
    dialog = StageSelectDialog()
    if dialog.exec_() != QDialog.Accepted:
        sys.exit(0)

    selected_stage = dialog.selected_stage()
    should_transition = 'All' in selected_stage
    if should_transition:
        selected_stage = 'S1'  # Start from S1 if All is selected

    kwargs = dict(stage=selected_stage, transition=should_transition, subject_info=dialog.get_subject_info(),
                  with_neopixels=args.with_neopixels, n_trials=args.n_trials,
                  inter_trial_interval=args.inter_trial_interval, experiment_folder='')

    # Now instantiate ArduinoControlApp with the correct stage
    if args.from_config_file and os.path.exists(args.from_config_file):
        params = ConfigObj(args.from_config_file, encoding='utf-8', unrepr=True)
        kwargs.update(experiment_folder=str(Path(args.from_config_file).parent), with_neopixels=params['with_neopixels'],
                      n_trials=params['n_trials'], inter_trial_interval=params['inter_trial_interval_ms'])

    window = ArduinoControlApp(str(config_path), **kwargs)

    window.refresh_ports()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
