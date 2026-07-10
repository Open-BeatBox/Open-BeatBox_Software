"""
This module provides a GUI application to control the BeatBox Arduino device.

It allows users to connect to the device, control actuators and monitor sensors directly
as well as run experiments using classes derived from `BaseExperimentManager` like
`BeatBoxExperimentManagerS1` or `BeatBoxExperimentManagerS2`.
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
from qtpy.QtGui import QColor, QPixmap
from qtpy.QtWidgets import (
    QLabel, QMainWindow, QTextEdit, QComboBox, QPushButton, QSlider,
    QVBoxLayout, QWidget, QGroupBox, QHBoxLayout, QGridLayout, QFormLayout,
    QFileDialog, QMessageBox, QProgressDialog, QButtonGroup, QErrorMessage,
    QDialog, QLineEdit, QApplication
)
import qdarkstyle

from beatbox.bb_logging import qt_handler, logger
from beatbox.experiment_managers import SubjectInfo
from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.S2_manager import BeatBoxExperimentManagerS2, PerformanceData
from beatbox.S3_manager import BeatBoxExperimentManagerS3
from beatbox.S3_1_manager import BeatBoxExperimentManagerS3_1
from beatbox.S4_manager import BeatBoxExperimentManagerS4

from beatbox.qt_utils import (LEDIndicator, labeled_spinbox, labeled_double_spinbox, add_labeled_fields,
                              FieldSpec, color_swatch, push_button, check_box, swap_button, combo_box, selectable_label,
                              safe_delete, add_form_fields)
from beatbox.serial_com import get_platform_serial_ports, BeatBoxCommunicator, BeatBoxMessage, HandshakeWorker


SERIAL_TIMEOUT = 2

BAUD_RATE = 115200

_STAGE_MANAGER_CLASSES: dict[str, type] = {
    'S1': BeatBoxExperimentManagerS1,
    'S2': BeatBoxExperimentManagerS2,
    'S3': BeatBoxExperimentManagerS3,
    'S3.1': BeatBoxExperimentManagerS3_1,
    'S4': BeatBoxExperimentManagerS4,
}


class StageTransition:
    """All information needed to move from one stage to the next.

    ``manager_cls`` is derived from ``next_label`` via ``_STAGE_MANAGER_CLASSES``
    so the two dicts cannot drift out of sync.
    ``next_label=None`` signals the terminal stage.
    """

    def __init__(self, next_label: str | None, ui_updater: str | None):
        self.next_label = next_label
        self.ui_updater = ui_updater

    @property
    def manager_cls(self) -> type | None:
        return _STAGE_MANAGER_CLASSES.get(self.next_label)  # None if last


_STAGE_TRANSITIONS: dict[str, StageTransition] = {
    'S1': StageTransition('S2', '_ui_update_s1_to_s2'),
    'S2': StageTransition('S3', '_ui_update_s2_to_s3'),
    'S3': StageTransition('S3.1', '_ui_update_s2_to_s3_1'),
    'S3.1': StageTransition('S4', '_ui_update_s2_to_s3'),  # FIXME: check updater
    'S4': StageTransition(None, None),
}


class ArduinoControlApp(QMainWindow):
    def __init__(self, pins_config_file, experiment_folder='', with_neopixels=False, n_trials=20,
                 inter_trial_interval=10000, stage='S1', transition=False, subject_info=None):
        super().__init__()

        # First instantiate QtObjects needed by communicator
        self.log_text = QTextEdit('Log:')

        # Instantiate communicator and connect signals.
        self.communicator = BeatBoxCommunicator(baud_rate=BAUD_RATE, serial_timeout=SERIAL_TIMEOUT)
        self.communicator.connected.connect(self.on_connected)
        self.communicator.handshakeCompleted.connect(self.on_handshake)
        self.communicator.errorOccurred.connect(self.on_error)

        self.with_neopixels = with_neopixels

        # FIXME:SOLID: This stage selection logic violates OCP. Adding new stages requires modifying this code.
        if stage in _STAGE_MANAGER_CLASSES:
            self.experiment_manager = _STAGE_MANAGER_CLASSES[stage](experiment_folder, self.communicator,
                                                                    main_window=self, subject_info=subject_info)
        else:
            raise ValueError(f'Unknown stage: {stage}')
        self.subject_info = subject_info # store for later stages
        self.connect_signal_from_experiment_manager()
        self.enable_transitions = transition

        # UI Elements
        self.port_combobox = QComboBox(self)
        self.port_combobox.setMinimumWidth(100)
        self.connect_button = QPushButton('Connect')
        self.send_button = QPushButton('Send Set Command')
        self.log_text.setReadOnly(True)
        self.actuators_controls = {}
        self.actuators_indicators = {}
        self.sensors_indicators = {}
        self.left_buttons = []  # To be able to control them from the code
        self.right_buttons = []

        # Optional neopixel UI elements
        if self.with_neopixels:
            self.colors = {'red': QColor(255,0,0), 'green': QColor(0,255,0), 'blue': QColor(0,0,255)}
            self.sliders = {col: QSlider(Qt.Horizontal) for col in self.colors.keys()}
            self.color_preview = QLabel()

        self.setWindowTitle('Beatbox Controller')
        self.main_layout = self.setup_ui()
        self.experiment_manager.link_widgets()

        # Connect global log signals to our UI append function.
        qt_handler.logSignal.connect(self.append_log)

        self._handshake_q_thread = QtCore.QThread(self)  # stop with the window
        self._handshake_worker = HandshakeWorker(self.communicator)

        self._connect_progress = None

        # Flags for connection and folder selection
        self._serial_connected = False
        self._folder_selected = False

    def connect_signal_from_experiment_manager(self):
        # FIXME:SOLID: This method assumes experiment_manager has specific signals/attributes, violating LSP and ISP.
        self.experiment_manager.sensorsStateChanged.connect(self._paint_sensor_leds)
        self.experiment_manager.actuatorsStateChanged.connect(self._paint_actuator_leds)
        self.experiment_manager.stageEnded.connect(self.handle_stage_end)
        if self.experiment_manager.stage_number == 1:
            self.experiment_manager.trialNumbersUpdated.connect(
                lambda current, total: (self.s1_current_trial_value_label.setText(str(current))))
        if self.experiment_manager.stage_number >= 2:
            self.experiment_manager.performanceUpdated.connect(self._update_performance)
            self.experiment_manager.trialNumbersUpdated.connect(self._update_trial_numbers)
        if self.experiment_manager.stage_number >= 3:
            self.experiment_manager.correctiveLoopUpdated.connect(self._update_corrective_loop)
        self.experiment_manager.phaseChanged.connect(self._update_phase_label)
        self.experiment_manager.stageChanged.connect(self._update_stage_label)
        self.experiment_manager.pelletDeliveryAlert.connect(self._handle_pellet_delivery_alert)

    def setup_ui(self):
        """Set up the main UI layout and widgets."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Serial connection controls
        connection_group = QGroupBox('Serial Connection')
        connection_layout = QHBoxLayout(connection_group)
        self.refresh_ports()
        self.connect_button.clicked.connect(self.connect_serial)
        self.refresh_ports_btn = push_button('Refresh', on_clicked=self.refresh_ports)
        connection_layout.addWidget(QLabel('Port:'))
        connection_layout.addWidget(self.port_combobox)
        connection_layout.addWidget(self.refresh_ports_btn)
        connection_layout.addWidget(self.connect_button)
        main_layout.addWidget(connection_group)

        ##########################   Experiment   #########################

        experiment_group = QGroupBox('Experiment Setup')
        experiment_group.setObjectName('experiment_group')
        experiment_layout = QVBoxLayout(experiment_group)

        ######### Overall experiment widgets
        # Experiment folder selection
        overall_group = QGroupBox('Overall setup')
        overall_experiment_layout = QHBoxLayout(overall_group)
        self.experiment_button = push_button('Select Folder', on_clicked=self.select_experiment_folder)
        overall_experiment_layout.addWidget(self.experiment_button)

        # Controls for n_trials and inter_trial_interval
        lbl, self.n_trials_spinbox = labeled_spinbox('n_trials:', lo=1, hi=9999,
                                                     value=self.experiment_manager.n_trials)
        overall_experiment_layout.addWidget(lbl)
        overall_experiment_layout.addWidget(self.n_trials_spinbox)

        lbl, self.inter_trial_spinbox = labeled_spinbox('Inter trial (ms):', lo=1, hi=999_999,
                                                        value=self.experiment_manager.inter_trial_interval)
        overall_experiment_layout.addWidget(lbl)
        overall_experiment_layout.addWidget(self.inter_trial_spinbox)

        self.experiment_state_btn = push_button('Start Experiment', on_clicked=self.start_experiment, enabled=False)
        overall_experiment_layout.addWidget(self.experiment_state_btn)

        experiment_layout.addWidget(overall_group)

        ###### Experiment specific widgets
        match self.experiment_manager.stage_number:
            case 1:
                experiment_data_group = self._ui_setup_s1_data()
            case 2:
                experiment_layout.addWidget(self._ui_setup_s2_controller())
                experiment_data_group = self._ui_setup_s2_data()
            case 3 | 4:
                experiment_layout.addWidget(self._ui_setup_s2_controller())
                experiment_data_group = self._ui_setup_s3_data()
            case 3.1:
                experiment_layout.addWidget(self._ui_setup_s2_controller())
                experiment_data_group = self._ui_setup_s3_1_data()
            case _:
                raise ValueError(f'No UI setup defined for stage "{self.experiment_manager.stage_number}"')

        experiment_data_group.setObjectName('experiment_data_group')
        experiment_layout.addWidget(experiment_data_group)
        main_layout.addWidget(experiment_group)

        ####################################################################

        # SENSORS
        sensors_group = QGroupBox('Sensors')
        sensors_layout = QGridLayout(sensors_group)
        for row, pin_name in enumerate(self.experiment_manager.sensors_pins_config.pin_names):
            led = LEDIndicator()
            sensors_layout.addWidget(QLabel(pin_name.replace('_', ' ').title()),
                                     row, 0, alignment=Qt.AlignVCenter)
            sensors_layout.addWidget(led, row, 1, alignment=Qt.AlignVCenter)
            self.sensors_indicators[pin_name] = led
        main_layout.addWidget(sensors_group)

        # Blinking alert for pellet delivery (now after sensors, before actuators)
        self.blinking_alert_label = QLabel('')
        self.blinking_alert_label.setStyleSheet('color: red; font-weight: bold;')
        self.blinking_alert_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.blinking_alert_label)

        # Actuators (move after alert)
        self.__setup_actuators(main_layout)

        if self.with_neopixels:
            self.setup_neopixels_ui(main_layout)

        # Send command button
        self.send_button.clicked.connect(self.send_set_command)
        main_layout.addWidget(self.send_button)

        # Logging area
        # Animal information and stage/phase info section
        info_row_widget = QWidget()
        info_row_layout = QHBoxLayout(info_row_widget)
        info_row_layout.setContentsMargins(0, 0, 0, 0)
        # Left: Animal info
        info_row_layout.addWidget(self._create_animal_info_group())

        # Right: Stage and phase info
        stage_phase_group = QGroupBox('Stage/Phase Info')
        stage_phase_layout = QFormLayout(stage_phase_group)
        self.stage_label = selectable_label(str(self.experiment_manager.config.s_level))
        self.phase_label = selectable_label()
        stage_phase_layout.addRow('Current Stage:', self.stage_label)
        stage_phase_layout.addRow('Current Phase:', self.phase_label)
        info_row_layout.addWidget(stage_phase_group)
        main_layout.addWidget(info_row_widget)

        main_layout.addWidget(self.log_text)

        return main_layout

    def _swap_experiment_data_group(self, new_data_group: QWidget, controller: QWidget | None = None):
        experiment_group = self.findChild(QGroupBox, 'experiment_group')
        if not experiment_group:
            return
        experiment_layout = experiment_group.layout()
        # Remove experiment_data_group if present
        for i in range(experiment_layout.count()):
            item = experiment_layout.itemAt(i)
            widget = item.widget()
            if widget and widget.objectName() == 'experiment_data_group':
                experiment_layout.removeWidget(widget)
                safe_delete(widget)  # schedule C++ deletion, not just reparent
                break

        if controller is not None:
            experiment_layout.addWidget(controller)
        new_data_group.setObjectName('experiment_data_group')
        experiment_layout.addWidget(new_data_group)

    def _ui_update_s1_to_s2(self):
        """
        Remove S1-specific widgets and add S2-specific widgets and data group.
        """
        self._swap_experiment_data_group(self._ui_setup_s2_data(), self._ui_setup_s2_controller())

    def _ui_update_s2_to_s3(self):
        """
        Remove S2-specific widgets and add S3-specific widgets and data group.
        """
        self._swap_experiment_data_group(self._ui_setup_s3_data())

    def _ui_update_s2_to_s3_1(self):
        self._swap_experiment_data_group(self._ui_setup_s3_1_data())
        # remove corrective loop count as it is irrelevant in s3.1
        self.corrective_loop_value_label = safe_delete(getattr(self, 'corrective_loop_value_label', None))

    def _ui_setup_s2_data(self):
        """Creates and returns the S2 experiment data group."""
        s2_data_group = QGroupBox('Experiment Data')
        s2_data_layout = QVBoxLayout(s2_data_group)

        # Performance row (S2: overall only, no left/right split)
        performance_layout = QHBoxLayout()
        add_labeled_fields(performance_layout, self, [
            FieldSpec('Overall performance:', 'overall_value_label', '0.00'),
        ])
        self.left_value_label = None   # not used in S2, guarded in update methods
        self.right_value_label = None
        s2_data_layout.addLayout(performance_layout)

        # Trial info row
        trial_info_layout = QHBoxLayout()

        add_labeled_fields(trial_info_layout, self, [
            FieldSpec('Total trials:', 'total_trials_value_label'),            # Total trials
            FieldSpec('Valid & correct trials:', 'valid_correct_value_label'), # Valid & correct trials (against time pressure)
            FieldSpec('Left touches:', 'left_touches_value_label'),            # Left screen touches
            FieldSpec('Right touches:', 'right_touches_value_label'),          # Right screen touches
        ])

        s2_data_layout.addLayout(trial_info_layout)
        return s2_data_group

    def _ui_setup_s1_data(self):
        """Creates and returns a QWidget for S1 experiment data, showing current trial number."""
        s1_data_group = QGroupBox('Experiment Data')
        s1_data_layout = QVBoxLayout(s1_data_group)
        trial_layout = QHBoxLayout()
        add_labeled_fields(trial_layout, self, [
            FieldSpec('Current trial:', 's1_current_trial_value_label'),
        ])
        s1_data_layout.addLayout(trial_layout)
        return s1_data_group

    def _ui_setup_s2_controller(self):
        s2_experiment_group = QGroupBox('S2/S3 setup')
        s2_layout = QHBoxLayout(s2_experiment_group)

        # Error flash duration
        lbl, self.error_flash_spinbox = labeled_spinbox('Blink duration (ms):', lo=1, hi=999_999,
                                                        value=self.experiment_manager.error_flash_ms)
        s2_layout.addWidget(lbl)
        s2_layout.addWidget(self.error_flash_spinbox)

        # Learning threshold
        lbl, self.learning_threshold_spinbox = labeled_double_spinbox(
            'Learning threshold:', value=self.experiment_manager.config.learning_threshold)
        s2_layout.addWidget(lbl)
        s2_layout.addWidget(self.learning_threshold_spinbox)
        return s2_experiment_group

    def _ui_setup_s3_data(self):
        s3_data_group, s3_data_layout, trial_info_layout = self._ui_setup_s3_basic_data()
        add_labeled_fields(trial_info_layout, self, [
            FieldSpec('Corrective loop count:', 'corrective_loop_value_label'),
        ])
        s3_data_layout.addLayout(trial_info_layout)
        return s3_data_group

    def _ui_setup_s3_1_data(self):
        s3_data_group, s3_data_layout, trial_info_layout = self._ui_setup_s3_basic_data()
        s3_data_layout.addLayout(trial_info_layout)
        return s3_data_group

    def _ui_setup_s3_basic_data(self) -> tuple[QGroupBox, QVBoxLayout, QHBoxLayout]:
        """Creates the S3 base data group: left/right performance + trial info row."""
        s3_data_group = QGroupBox('Experiment Data')
        s3_data_layout = QVBoxLayout(s3_data_group)

        # Performance row
        performance_layout = QHBoxLayout()
        add_labeled_fields(performance_layout, self, [
            FieldSpec('Left performance:', 'left_value_label', '0.00'),
            FieldSpec('Right performance:', 'right_value_label', '0.00'),
        ])
        self.overall_value_label = None
        s3_data_layout.addLayout(performance_layout)

        # Trial info row (returned so callers can append more fields before adding to layout)
        trial_info_layout = QHBoxLayout()
        add_labeled_fields(trial_info_layout, self, [
            FieldSpec('Total trials:', 'total_trials_value_label'),
            FieldSpec('Valid & correct trials:', 'valid_correct_value_label'),
            FieldSpec('Correct responses count:', 'correct_count_value_label'),
        ])

        return s3_data_group, s3_data_layout, trial_info_layout

    def __setup_actuators(self, main_layout):
        actuators_group = QGroupBox('Actuators')
        actuators_layout = QVBoxLayout(actuators_group)

        # General controls (neither left_ nor right_)
        general_layout = QGridLayout()
        for row, pin_name in enumerate(p for p in self.experiment_manager.actuator_pins_cfg.pin_names
                                       if not p.startswith(('left_', 'right_'))):
            self.__set_actuator_pin(general_layout, pin_name, row)
        actuators_layout.addLayout(general_layout)

        # Left and right displays side by side
        lr_layout = QHBoxLayout()
        for side in ('left', 'right'):
            lr_layout.addWidget(self.__setup_side_display(side))
        actuators_layout.addLayout(lr_layout)

        main_layout.addWidget(actuators_group)

    def __setup_side_display(self, side: str) -> QGroupBox:
        """Build one side's display group ('left' or 'right') and return it."""
        group = QGroupBox(f'{side.title()} Display')
        layout = QGridLayout(group)
        setattr(self, f'{side}_button_group', QButtonGroup())
        for row, pin_name in enumerate(p for p in self.experiment_manager.actuator_pins_cfg.pin_names
                                       if p.startswith(f'{side}_')):
            self.__set_actuator_pin(layout, pin_name, row)
        return group

    def __set_actuator_pin(self, layout, pin_name, row):
        title = pin_name.replace('_', ' ').replace('left ', '').replace('right ', '').title()

        if pin_name.startswith(('left_', 'right_')):
            widget = check_box(title, on_changed=self.send_set_command)
            side_list = self.left_buttons if pin_name.startswith('left_') else self.right_buttons
            side_list.append(widget)
        elif pin_name == 'reward':
            widget = push_button(title, on_clicked=self.send_set_command, checkable=True)
        else:
            widget = check_box(title, on_changed=self.send_set_command)

        led = LEDIndicator()
        layout.addWidget(widget, row, 0, alignment=Qt.AlignVCenter)
        layout.addWidget(led, row, 1, alignment=Qt.AlignVCenter)
        self.actuators_controls[pin_name] = widget
        self.actuators_indicators[pin_name] = led

    def select_experiment_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select Experiment Folder')
        if folder:
            self.experiment_manager.set_experiment_folder(folder)
            self._folder_selected = True
            self._update_experiment_state_btn()

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

    def start_experiment(self):
        self.experiment_manager.start_experiment()
        swap_button(self.experiment_state_btn, 'Stop Experiment', self.start_experiment, self.stop_experiment)

    def stop_experiment(self):
        self.experiment_manager.end_experiment()
        swap_button(self.experiment_state_btn, 'Start Experiment', self.stop_experiment, self.start_experiment)
        self.experiment_state_btn.setEnabled(self._serial_connected and self._folder_selected)
        logger.debug('Stopping experiment.')

    def handle_stage_end(self, current_stage: str):
        # FIXME:SOLID: This method handles experiment logic and UI transitions, violating SRP. Should delegate to separate controller/manager.
        if not self.enable_transitions:
            return

        transition = _STAGE_TRANSITIONS.get(current_stage)
        if transition is None:
            raise ValueError(f'Unknown stage "{current_stage}"')

        if transition.manager_cls is None:  # terminal stage — nothing follows S4
            QMessageBox.information(self, f'Stage {current_stage} complete', 'Experiment complete.')
            return

        self.append_log(
            f'Stage {current_stage} complete. Moving to Stage "{transition.next_label}".', logging.INFO)

        manager = transition.manager_cls(self.experiment_manager.experiment_folder, self.communicator,
                                         main_window=self, subject_info=self.subject_info)
        self.experiment_manager.deleteLater()
        self.experiment_manager = manager

        if transition.ui_updater is not None:
            getattr(self, transition.ui_updater)()

        self._ui_update_base_experiment_params()
        self.connect_signal_from_experiment_manager()
        self.experiment_manager.link_widgets()
        self.experiment_manager.start_experiment()
        if current_stage != 'S1':
            self._reset_performance_gui()

    def setup_neopixels_ui(self, main_layout):
        neopixel_group = QGroupBox('Neopixel Control')
        layout = QVBoxLayout(neopixel_group)
        grid = QGridLayout()

        for row, col in enumerate(self.colors.keys()):
            grid.addWidget(QLabel(f'{col.title()}:'), row, 0, alignment=Qt.AlignVCenter)
            grid.addWidget(color_swatch(QColor(0,0,0)), row, 1, alignment=Qt.AlignVCenter)
            grid.addWidget(self.sliders[col], row, 2, alignment=Qt.AlignVCenter)
            grid.addWidget(color_swatch(self.colors[col]), row, 3, alignment=Qt.AlignVCenter)

        layout.addLayout(grid)
        preview_layout = QHBoxLayout()
        preview_layout.addWidget(QLabel('Preview:'))
        self.color_preview.setFixedSize(50,50)
        preview_layout.addWidget(self.color_preview)
        layout.addLayout(preview_layout)
        for slider in self.sliders.values():
            slider.valueChanged.connect(self.update_color_preview)
        self.update_color_preview()
        main_layout.addWidget(neopixel_group)

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

        self._connect_progress = self.__create_connection_p_bar()

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

    def __create_connection_p_bar(self):
        dlg = QProgressDialog('Connecting to BeatBox device…', cancelButtonText=None,
                              minimum=0, maximum=0, parent=self)
        dlg.setWindowTitle('Connecting')
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setMinimumDuration(0)  # show immediately
        dlg.setAutoClose(False)
        return dlg

    def get_rgb_values(self):
        if self.with_neopixels:
            return [slider.value() for slider in self.sliders.values()]
        else:
            return None

    def update_color_preview(self):
        pixmap = QPixmap(self.color_preview.size())
        pixmap.fill(QColor(*self.get_rgb_values()))
        self.color_preview.setPixmap(pixmap)

    def on_connected(self):
        self.append_log('Serial port connected.', logging.INFO)

    def on_handshake(self):
        self.append_log('Handshake completed.', logging.INFO)
        self._serial_connected = True
        # Set screen to default state
        self._update_experiment_state_btn()
        self.experiment_manager.handshake_completed()

    def send_set_command(self, checked_pins=None):
        """
        Send a set command to the BeatBox device based on the current state of the actuator controls.
        Parameters
        ----------
        checked_pins : list[bool] | None
            Optional list of boolean values indicating which pins should be set to ON.
            If None, the current state of the actuator controls will be used.
        """
        # FIXME:SOLID: This method mixes UI state, hardware logic, and experiment logic. Violates SRP and DIP.
        if not isinstance(checked_pins, (list, tuple)):
            checked_pins = None  # A status value was passed instead. Overwrite
        ordered_pin_names = self.experiment_manager.actuator_pins_cfg.pin_names
        if checked_pins is None:
            checked_pins = [self.actuators_controls[name].isChecked() for name in ordered_pin_names]
        else:
            checked_pins = [self.actuators_controls[pin_name].isChecked() or pin_checked for pin_name, pin_checked in
                            zip(ordered_pin_names, checked_pins)]
        self.actuators_controls['reward'].setChecked(False)

        # self.communicator.send_set_command(checked_pins, self.get_rgb_values())
        if checked_pins[0]:
            # FIXME: reward delivery should be handled by the firmware
            QtCore.QTimer.singleShot(self.experiment_manager.reward_duration, self.send_set_command)
        logger.debug(f'Sending set command with checked pins: {checked_pins}')
        self.experiment_manager.request_manual_action_by_pins_and_rgb(checked_pins, self.get_rgb_values())

    @QtCore.Slot(list)
    def _paint_sensor_leds(self, sensor_bits: list[int]):
        """
        Turn the LED indicators on or off based on the payload.
        The payload is a list of bool values (0 or 1) indicating the state of each sensor.

        Parameters
        ----------
        sensor_bits : list[int]
            A list of integers (0 or 1) representing the state of each sensor.
            The order should match the order of sensors in the configuration.
        """
        for pin_name, sensor_state in zip(self.sensors_indicators.keys(), sensor_bits):
            indicator = self.sensors_indicators[pin_name]
            if indicator.state != sensor_state:
                indicator.set_state(sensor_state)

    def _paint_actuator_leds(self, payload: list[int]):
        word = (payload[0] << 8) | payload[1]
        for idx, pin_name in enumerate(self.actuators_indicators):
            self.actuators_indicators[pin_name].set_state(bool((word >> idx) & 1))

    def _ui_update_base_experiment_params(self):
        # Update base info widgets from the new manager's config
        self.n_trials_spinbox.setValue(self.experiment_manager.config.n_trials)
        self.inter_trial_spinbox.setValue(self.experiment_manager.config.inter_trial_interval)

        if self.experiment_manager.stage_number >= 2:
            self.error_flash_spinbox.setValue(self.experiment_manager.config.error_flash_ms)
            self.learning_threshold_spinbox.setValue(self.experiment_manager.config.learning_threshold)

    def _update_performance(self, perf: PerformanceData):
        if self.experiment_manager.stage_number == 2:
            val = perf.overall if perf.overall is not None else 0.0
            if self.overall_value_label:
                self.overall_value_label.setText(f'{val:.2f}')
        else:
            left = perf.left if perf.left is not None else perf.overall
            right = perf.right if perf.right is not None else perf.overall
            if self.left_value_label:
                self.left_value_label.setText(f'{left:.2f}' if left is not None else '-')
            if self.right_value_label:
                self.right_value_label.setText(f'{right:.2f}' if right is not None else '-')

    def _update_corrective_loop(self, corrective_count: int):
        if getattr(self, 'corrective_loop_value_label', None) is not None:
            self.corrective_loop_value_label.setText(str(corrective_count))

    # FIXME:SOLID: tightly coupled to experiment manager, does not use the signal slot pattern efficiently
    def _update_trial_numbers(self, correct_count: int, total_trials: int):
        # Update total trials
        self.total_trials_value_label.setText(str(total_trials))
        # Update valid & correct trials
        if hasattr(self, 'valid_correct_value_label'):
            valid_correct = getattr(self.experiment_manager, 'total_correct_trials', 0)
            self.valid_correct_value_label.setText(str(valid_correct))
        # Update left/right touches if present
        if (hasattr(self, 'left_touches_value_label')
                and hasattr(self, 'right_touches_value_label')
                and self.experiment_manager.stage_number == 2):
            left_touches = getattr(self.experiment_manager, 'left_touches', 0)
            right_touches = getattr(self.experiment_manager, 'right_touches', 0)
            self.left_touches_value_label.setText(str(left_touches))
            self.right_touches_value_label.setText(str(right_touches))
        # Update correct count if present
        if hasattr(self, 'correct_count_value_label'):
            self.correct_count_value_label.setText(str(correct_count))

    def _reset_performance_gui(self):
        if self.experiment_manager.stage_number == 2:
            if self.overall_value_label:
                self.overall_value_label.setText('0.00')
        else:
            if self.left_value_label:
                self.left_value_label.setText('0.00')
            if self.right_value_label:
                self.right_value_label.setText('0.00')
        self.total_trials_value_label.setText('0')
        if hasattr(self, 'correct_count_value_label'):
            self.correct_count_value_label.setText('0')
        if getattr(self, 'corrective_loop_value_label', None) is not None:
            self.corrective_loop_value_label.setText('0')
        # If there are other fields to reset, add them here
        if getattr(self, 'valid_correct_value_label', None) is not None:
            self.valid_correct_value_label.setText('0')

    def _update_experiment_state_btn(self):
        self.experiment_state_btn.setEnabled(self._serial_connected and self._folder_selected)

    def on_error(self, msg: BeatBoxMessage | str):
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

    def _create_animal_info_group(self):
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

    def _update_phase_label(self, phase_name: str):
        self.phase_label.setText(phase_name)

    def _update_stage_label(self, stage_name: str):
        self.stage_label.setText(stage_name)

    def show_blinking_alert(self, show: bool):
        txt = '⚠️ Pellet delivery loop > 5! Check hardware!' if show else ''
        self.blinking_alert_label.setText(txt)

    def _handle_pellet_delivery_alert(self, loop_count: int):
        threshold = 15
        txt = f'⚠️ Pellet delivery loop: {loop_count}! Check hardware!' if  loop_count > threshold else ''
        self.blinking_alert_label.setText(txt)

class StageSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Select Experiment Stage')
        layout = QVBoxLayout(self)

        form = QFormLayout()
        # FIXME:SOLID: Hardcoded stage names. Consider using an enum or abstraction for stages (OCP).
        self.combo = combo_box(['All', 'S1', 'S2', 'S3', 'S3.1', 'S4'])
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
    should_transition = False
    if 'All' in selected_stage:
        selected_stage = 'S1'  # Start from S1 if All is selected
        should_transition = True

    kwargs = dict(stage=selected_stage, transition=should_transition, subject_info=dialog.get_subject_info(),
                  with_neopixels=args.with_neopixels, n_trials=args.n_trials,
                  inter_trial_interval=args.inter_trial_interval, experiment_folder='')

    # Now instantiate ArduinoControlApp with the correct stage
    if args.from_config_file and os.path.exists(args.from_config_file):
        params = ConfigObj(args.from_config_file, encoding='utf-8',  unrepr=True)
        kwargs.update(experiment_folder=str(Path(args.from_config_file).parent), with_neopixels=params['with_neopixels'],
                      n_trials=params['n_trials'], inter_trial_interval=params['inter_trial_interval_ms'])

    window = ArduinoControlApp(str(config_path), **kwargs)

    window.refresh_ports()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
