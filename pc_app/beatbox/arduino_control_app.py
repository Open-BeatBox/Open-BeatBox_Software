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

from PyQt5.QtWidgets import QPushButton
from configobj import ConfigObj
from qtpy import QtWidgets, QtCore, QtGui
import qdarkstyle

from beatbox.bb_logging import qt_handler, logger
from beatbox.experiment_managers import SubjectInfo
from beatbox.S1_manager import BeatBoxExperimentManagerS1
from beatbox.S2_manager import BeatBoxExperimentManagerS2
from beatbox.S3_manager import BeatBoxExperimentManagerS3

from beatbox.qt_utils import LEDIndicator
from beatbox.serial_com import get_platform_serial_ports, BeatBoxCommunicator, BeatBoxMessage, HandshakeWorker


SERIAL_TIMEOUT = 2

BAUD_RATE = 115200

class ArduinoControlApp(QtWidgets.QMainWindow):
    def __init__(self, pins_config_file, experiment_folder='', with_neopixels=False, n_trials=20,
                 inter_trial_interval=10000, stage='S1', transition=False, subject_info=None):
        super().__init__()

        # First instantiate QtObjects needed by communicator
        self.log_text = QtWidgets.QTextEdit('Log:')

        # Instantiate communicator and connect signals.
        self.communicator = BeatBoxCommunicator(baud_rate=BAUD_RATE, serial_timeout=SERIAL_TIMEOUT)
        self.communicator.connected.connect(self.on_connected)
        self.communicator.handshakeCompleted.connect(self.on_handshake)
        self.communicator.errorOccurred.connect(self.on_error)

        self.with_neopixels = with_neopixels
        manager_classes = {
            'S1': BeatBoxExperimentManagerS1,
            'S2': BeatBoxExperimentManagerS2,
            'S3': BeatBoxExperimentManagerS3
        }
        if stage in manager_classes:
            self.experiment_manager = manager_classes[stage](experiment_folder, self.communicator, main_window=self, subject_info=subject_info)
        else:
            raise ValueError(f"Unknown stage: {stage}")
        self.subject_info = subject_info # store for later stages
        self.connect_signal_from_experiment_manager()
        self.enable_transitions = transition

        # UI Elements
        self.port_combobox = QtWidgets.QComboBox(self)
        self.port_combobox.setMinimumWidth(100)
        self.connect_button = QtWidgets.QPushButton('Connect')
        self.send_button = QtWidgets.QPushButton('Send Set Command')
        self.log_text.setReadOnly(True)
        self.actuators_controls = {}
        self.actuators_indicators = {}
        self.sensors_indicators = {}
        self.left_buttons = []  # To be able to control them from the code
        self.right_buttons = []

        # Optional neopixel UI elements
        if self.with_neopixels:
            self.colors = {'red': QtGui.QColor(255,0,0),
                           'green': QtGui.QColor(0,255,0),
                           'blue': QtGui.QColor(0,0,255)}
            self.sliders = {col: QtWidgets.QSlider(QtCore.Qt.Horizontal) for col in self.colors.keys()}
            self.color_preview = QtWidgets.QLabel()

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
        self.experiment_manager.sensorsStateChanged.connect(self._paint_sensor_leds)
        self.experiment_manager.actuatorsStateChanged.connect(self._paint_actuator_leds)
        self.experiment_manager.stageEnded.connect(self.handle_stage_end)
        if self.experiment_manager.stage_number == 1:
            self.experiment_manager.trialNumbersUpdated.connect(
                lambda current, total: (self.s1_current_trial_value_label.setText(str(current))))
        if self.experiment_manager.stage_number >= 2:
            self.experiment_manager.performanceUpdated.connect(self._update_performance)
            self.experiment_manager.correctiveLoopUpdated.connect(self._update_corrective_loop)
            self.experiment_manager.trialNumbersUpdated.connect(self._update_trial_numbers)
        self.experiment_manager.phaseChanged.connect(self._update_phase_label)
        self.experiment_manager.stageChanged.connect(self._update_stage_label)
        self.experiment_manager.pelletDeliveryAlert.connect(self._handle_pellet_delivery_alert)

    def setup_ui(self):
        """ Set up the main UI layout and widgets. """
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # Serial connection controls
        connection_group = QtWidgets.QGroupBox("Serial Connection")
        connection_layout = QtWidgets.QHBoxLayout(connection_group)
        self.refresh_ports()
        self.connect_button.clicked.connect(self.connect_serial)
        connection_layout.addWidget(QtWidgets.QLabel("Port:"))
        connection_layout.addWidget(self.port_combobox)
        self.refresh_ports_btn = QPushButton('Refresh')
        self.refresh_ports_btn.clicked.connect(self.refresh_ports)
        connection_layout.addWidget(self.refresh_ports_btn)
        connection_layout.addWidget(self.connect_button)
        main_layout.addWidget(connection_group)

        ##########################   Experiment   #########################

        experiment_group = QtWidgets.QGroupBox("Experiment Setup")
        experiment_group.setObjectName("experiment_group")
        experiment_layout = QtWidgets.QVBoxLayout(experiment_group)

        ######### Overall experiment widgets
        # Experiment folder selection
        overall_group = QtWidgets.QGroupBox("Overall setup")
        overall_experiment_layout = QtWidgets.QHBoxLayout(overall_group)
        self.experiment_button = QtWidgets.QPushButton("Select Folder")
        self.experiment_button.clicked.connect(self.select_experiment_folder)
        overall_experiment_layout.addWidget(self.experiment_button)

        # Controls for n_trials and inter_trial_interval
        self.n_trials_spinbox = QtWidgets.QSpinBox()
        self.n_trials_spinbox.setRange(1, 9999)
        self.n_trials_spinbox.setValue(self.experiment_manager.config.n_trials)
        overall_experiment_layout.addWidget(QtWidgets.QLabel("n_trials:"))
        overall_experiment_layout.addWidget(self.n_trials_spinbox)

        self.inter_trial_spinbox = QtWidgets.QSpinBox()
        self.inter_trial_spinbox.setRange(1, 999999)
        self.inter_trial_spinbox.setValue(self.experiment_manager.config.inter_trial_interval)
        overall_experiment_layout.addWidget(QtWidgets.QLabel("Inter trial (ms):"))
        overall_experiment_layout.addWidget(self.inter_trial_spinbox)

        self.experiment_state_btn = QtWidgets.QPushButton("Start Experiment")
        self.experiment_state_btn.setEnabled(False)
        self.experiment_state_btn.clicked.connect(self.start_experiment)
        overall_experiment_layout.addWidget(self.experiment_state_btn)

        experiment_layout.addWidget(overall_group)

        ###### S2 experiment specific widgets
        if self.experiment_manager.stage_number == 1:
            experiment_data_group = self._ui_setup_s1_data()
        else:
            s2_experiment_group = self._ui_setup_s2_controller()
            experiment_data_group = self._ui_setup_s2_data()
            experiment_layout.addWidget(s2_experiment_group)

        experiment_data_group.setObjectName("experiment_data_group")
        experiment_layout.addWidget(experiment_data_group)
        main_layout.addWidget(experiment_group)

        ####################################################################

        # SENSORS
        sensors_group = QtWidgets.QGroupBox('Sensors')
        sensors_layout = QtWidgets.QGridLayout(sensors_group)
        for row, pin_name in enumerate(self.experiment_manager.sensors_pins_config.pin_names):
            label = QtWidgets.QLabel(pin_name.replace("_", " ").title())
            led = LEDIndicator()
            sensors_layout.addWidget(label, row, 0, alignment=QtCore.Qt.AlignVCenter)
            sensors_layout.addWidget(led, row, 1, alignment=QtCore.Qt.AlignVCenter)
            self.sensors_indicators[pin_name] = led
        main_layout.addWidget(sensors_group)

        # Blinking alert for pellet delivery (now after sensors, before actuators)
        self.blinking_alert_label = QtWidgets.QLabel()
        self.blinking_alert_label.setText("")
        self.blinking_alert_label.setStyleSheet("color: red; font-weight: bold;")
        self.blinking_alert_label.setAlignment(QtCore.Qt.AlignCenter)
        main_layout.addWidget(self.blinking_alert_label)

        # Actuators (move after alert)
        self.__setup_actuators(main_layout)

        # Send command button
        self.send_button.clicked.connect(self.send_set_command)
        main_layout.addWidget(self.send_button)

        # Logging area
        # Animal information and stage/phase info section
        info_row_widget = QtWidgets.QWidget()
        info_row_layout = QtWidgets.QHBoxLayout(info_row_widget)
        info_row_layout.setContentsMargins(0, 0, 0, 0)
        # Left: Animal info
        animal_info_group = self._create_animal_info_group()
        info_row_layout.addWidget(animal_info_group)
        # Right: Stage and phase info
        stage_phase_group = QtWidgets.QGroupBox("Stage/Phase Info")
        stage_phase_layout = QtWidgets.QFormLayout(stage_phase_group)
        self.stage_label = QtWidgets.QLabel(str(self.experiment_manager.config.s_level))
        self.phase_label = QtWidgets.QLabel("")  # To be updated dynamically
        self.stage_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.phase_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        stage_phase_layout.addRow("Current Stage:", self.stage_label)
        stage_phase_layout.addRow("Current Phase:", self.phase_label)
        info_row_layout.addWidget(stage_phase_group)
        main_layout.addWidget(info_row_widget)

        main_layout.addWidget(self.log_text)

        return main_layout

    def _ui_update_s1_to_s2(self):
        """
        Remove S1-specific widgets and add S2-specific widgets and data group.
        """
        # Find experiment_group
        experiment_group = self.findChild(QtWidgets.QGroupBox, "experiment_group")
        if not experiment_group:
            return
        experiment_layout = experiment_group.layout()
        # Remove experiment_data_group if present
        for i in range(experiment_layout.count()):
            item = experiment_layout.itemAt(i)
            widget = item.widget()
            if widget and widget.objectName() == "experiment_data_group":
                experiment_layout.removeWidget(widget)
                widget.setParent(None)
                break
        # Add S2-specific widgets
        s2_experiment_group = self._ui_setup_s2_controller()
        experiment_data_group = self._ui_setup_s2_data()
        experiment_data_group.setObjectName("experiment_data_group")
        experiment_layout.addWidget(s2_experiment_group)
        experiment_layout.addWidget(experiment_data_group)

    def _ui_setup_s2_data(self):
        # Experiment Data group
        s2_data_group = QtWidgets.QGroupBox("Experiment Data")
        s2_data_layout = QtWidgets.QVBoxLayout(s2_data_group)

        # Performance row
        performance_layout = QtWidgets.QHBoxLayout()
        left_label = QtWidgets.QLabel("Left performance:")
        self.left_value_label = QtWidgets.QLabel(f"{0:.2f}")
        self.left_value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        performance_layout.addWidget(left_label)
        performance_layout.addWidget(self.left_value_label)
        right_label = QtWidgets.QLabel("Right performance:")
        self.right_value_label = QtWidgets.QLabel(f"{0:.2f}")
        self.right_value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        performance_layout.addWidget(right_label)
        performance_layout.addWidget(self.right_value_label)
        s2_data_layout.addLayout(performance_layout)

        # Trial info row
        trial_info_layout = QtWidgets.QHBoxLayout()
        total_trials_label = QtWidgets.QLabel("Total trials:")
        self.total_trials_value_label = QtWidgets.QLabel("0")
        self.total_trials_value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        trial_info_layout.addWidget(total_trials_label)
        trial_info_layout.addWidget(self.total_trials_value_label)
        correct_count_label = QtWidgets.QLabel("Correct responses count:")
        self.correct_count_value_label = QtWidgets.QLabel("0")
        self.correct_count_value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        trial_info_layout.addWidget(correct_count_label)
        trial_info_layout.addWidget(self.correct_count_value_label)
        corrective_loop_label = QtWidgets.QLabel("Corrective loop count:")
        self.corrective_loop_value_label = QtWidgets.QLabel("0")
        self.corrective_loop_value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        trial_info_layout.addWidget(corrective_loop_label)
        trial_info_layout.addWidget(self.corrective_loop_value_label)
        s2_data_layout.addLayout(trial_info_layout)
        return s2_data_group

    def _ui_setup_s1_data(self):
        """
        Creates and returns a QWidget for S1 experiment data, showing current trial number.
        """
        s1_data_group = QtWidgets.QGroupBox("Experiment Data")
        s1_data_layout = QtWidgets.QVBoxLayout(s1_data_group)
        trial_layout = QtWidgets.QHBoxLayout()
        current_trial_label = QtWidgets.QLabel("Current trial:")
        self.s1_current_trial_value_label = QtWidgets.QLabel("0")
        trial_layout.addWidget(current_trial_label)
        trial_layout.addWidget(self.s1_current_trial_value_label)
        s1_data_layout.addLayout(trial_layout)
        return s1_data_group

    def _ui_setup_s2_controller(self):
        s2_experiment_group = QtWidgets.QGroupBox("S2/S3 setup")
        s2_layout = QtWidgets.QHBoxLayout(s2_experiment_group)

        # Error flash duration
        self.error_flash_spinbox = QtWidgets.QSpinBox()
        self.error_flash_spinbox.setRange(1, 999999)
        self.error_flash_spinbox.setValue(
            self.experiment_manager.config.error_flash_ms)
        s2_layout.addWidget(QtWidgets.QLabel("Blink duration (ms):"))
        s2_layout.addWidget(self.error_flash_spinbox)

        # Learning threshold
        self.learning_threshold_spinbox = QtWidgets.QDoubleSpinBox()
        self.learning_threshold_spinbox.setRange(0, 1)
        self.learning_threshold_spinbox.setSingleStep(0.01)
        self.learning_threshold_spinbox.setDecimals(2)
        self.learning_threshold_spinbox.setValue(
            self.experiment_manager.config.learning_threshold)
        s2_layout.addWidget(QtWidgets.QLabel("Learning threshold:"))
        s2_layout.addWidget(self.learning_threshold_spinbox)
        return s2_experiment_group

    def __setup_actuators(self, main_layout):
        actuators_group = QtWidgets.QGroupBox("Actuators")
        actuators_layout = QtWidgets.QVBoxLayout(actuators_group)

        # General controls
        general_layout = QtWidgets.QGridLayout()
        row = 0
        for pin_name in self.experiment_manager.actuator_pins_cfg.pin_names:
            if not pin_name.startswith("left_") and not pin_name.startswith("right_"):
                self.__set_actuator_pin(general_layout, pin_name, row)
                row += 1
        actuators_layout.addLayout(general_layout)

        # Left display
        left_group = QtWidgets.QGroupBox("Left Display")
        left_layout = QtWidgets.QGridLayout(left_group)
        self.left_button_group = QtWidgets.QButtonGroup()
        row = 0
        for pin_name in self.experiment_manager.actuator_pins_cfg.pin_names:
            if pin_name.startswith("left_"):
                self.__set_actuator_pin(left_layout, pin_name, row)
                row += 1

        # Right display
        right_group = QtWidgets.QGroupBox("Right Display")
        right_layout = QtWidgets.QGridLayout(right_group)
        self.right_button_group = QtWidgets.QButtonGroup()
        row = 0
        for pin_name in self.experiment_manager.actuator_pins_cfg.pin_names:
            if pin_name.startswith("right_"):
                self.__set_actuator_pin(right_layout, pin_name, row)
                row += 1

        # Place left and right side by side
        lr_layout = QtWidgets.QHBoxLayout()
        lr_layout.addWidget(left_group)
        lr_layout.addWidget(right_group)
        actuators_layout.addLayout(lr_layout)

        main_layout.addWidget(actuators_group)

    def __set_actuator_pin(self, layout, pin_name, row):
        title = pin_name.replace("_", " ").replace("left ", "").replace("right ", "").title()

        if pin_name.startswith("left_"):
            widget = QtWidgets.QRadioButton(title)
            self.left_button_group.addButton(widget)
            widget.toggled.connect(lambda checked: self.send_set_command() if checked else None)
            self.left_buttons.append(widget)  # TODO: maybe dictionary with pattern number
        elif pin_name.startswith("right_"):
            widget = QtWidgets.QRadioButton(title)
            self.right_button_group.addButton(widget)
            widget.toggled.connect(lambda checked: self.send_set_command() if checked else None)
            self.right_buttons.append(widget)
        else:
            if pin_name == 'reward':
                widget = QtWidgets.QPushButton(title)
                widget.setCheckable(True)
                widget.clicked.connect(self.send_set_command)
            else:
                widget= QtWidgets.QCheckBox(title)
                widget.stateChanged.connect(self.send_set_command)

        led = LEDIndicator()
        # layout.addWidget(label, row, 0, alignment=QtCore.Qt.AlignVCenter)
        layout.addWidget(widget, row, 0, alignment=QtCore.Qt.AlignVCenter)
        layout.addWidget(led, row, 1, alignment=QtCore.Qt.AlignVCenter)
        self.actuators_controls[pin_name] = widget
        self.actuators_indicators[pin_name] = led
        row += 1

    def select_experiment_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Experiment Folder")
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
        self.experiment_state_btn.setText("Stop Experiment")
        self.experiment_state_btn.clicked.disconnect(self.start_experiment)
        self.experiment_state_btn.clicked.connect(self.stop_experiment)

    def stop_experiment(self):
        self.experiment_manager.end_experiment()
        self.experiment_state_btn.setText("Start Experiment")
        self.experiment_state_btn.clicked.disconnect(self.stop_experiment)
        self.experiment_state_btn.clicked.connect(self.start_experiment)
        self.experiment_state_btn.setEnabled(self._serial_connected and self._folder_selected)
        logger.debug("Stopping experiment.")

    def handle_stage_end(self, reason: str):
        if not self.enable_transitions:
            return
        match reason:
            case 'S1':
                self.append_log(
                    f"Stage {self.experiment_manager.config.s_level} Complete. "
                    "Stage S1 complete. Moving to Stage S2.",
                    logging.INFO
                )
                s1_folder = self.experiment_manager.experiment_folder
                s2_manager = BeatBoxExperimentManagerS2(
                    s1_folder,
                    self.communicator,
                    main_window=self,
                    subject_info=self.subject_info
                )
                self.experiment_manager.deleteLater()
                self.experiment_manager = s2_manager
                self._ui_update_s1_to_s2()
                self._ui_update_base_experiment_params()
                self.connect_signal_from_experiment_manager()
                self.experiment_manager.link_widgets()
                self.experiment_manager.start_experiment()
            case 'S2':
                self.append_log(
                    f"Stage {self.experiment_manager.config.s_level} Complete. "
                    "Stage S2 complete. Moving to Stage S3.",
                    logging.INFO
                )
                s2_folder = self.experiment_manager.experiment_folder
                s3_manager = BeatBoxExperimentManagerS3(
                    s2_folder,
                    self.communicator,
                    main_window=self,
                    subject_info=self.subject_info
                )
                self.experiment_manager.deleteLater()
                self.experiment_manager = s3_manager
                self._ui_update_base_experiment_params()
                self.connect_signal_from_experiment_manager()
                self.experiment_manager.link_widgets()
                self.experiment_manager.start_experiment()
                self._reset_performance_gui()
            case _:
                QtWidgets.QMessageBox.information(
                    self, f"Stage {self.experiment_manager.config.s_level} Complete",
                    "Experiment complete.")

    def setup_neopixels_ui(self, main_layout):
        neopixel_group = QtWidgets.QGroupBox("Neopixel Control")
        layout = QtWidgets.QVBoxLayout(neopixel_group)
        grid = QtWidgets.QGridLayout()

        def create_label(color: QtGui.QColor):
            lbl = QtWidgets.QLabel()
            lbl.setFixedSize(20,20)
            pixmap = QtGui.QPixmap(20,20)
            pixmap.fill(color)
            lbl.setPixmap(pixmap)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            return lbl

        for row, col in enumerate(self.colors.keys()):
            grid.addWidget(QtWidgets.QLabel(f"{col.title()}:"), row, 0, alignment=QtCore.Qt.AlignVCenter)
            grid.addWidget(create_label(QtGui.QColor(0,0,0)), row, 1, alignment=QtCore.Qt.AlignVCenter)
            grid.addWidget(self.sliders[col], row, 2, alignment=QtCore.Qt.AlignVCenter)
            grid.addWidget(create_label(self.colors[col]), row, 3, alignment=QtCore.Qt.AlignVCenter)

        layout.addLayout(grid)
        preview_layout = QtWidgets.QHBoxLayout()
        preview_layout.addWidget(QtWidgets.QLabel("Preview:"))
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
            QtWidgets.QMessageBox.information(self, "BeatBox", "Connection established!")
        else:
            error_message = (f"Could not establish communication with port {self.port_combobox.currentText()}. "
                             f"Please check the serial port and try again.")
            QtWidgets.QMessageBox.critical(self, "BeatBox", error_message)
            self.connect_button.setEnabled(True)

    def __create_connection_p_bar(self):
        dlg = QtWidgets.QProgressDialog(
            "Connecting to BeatBox device…",
            None,  # no Cancel button
            0, 0,  # range 0-0 -> just spinning busy indicator
            self
        )
        dlg.setWindowTitle("Connecting")
        dlg.setWindowModality(QtCore.Qt.ApplicationModal)
        dlg.setMinimumDuration(0)  # show immediately
        dlg.setAutoClose(False)
        return dlg

    def get_rgb_values(self):
        if self.with_neopixels:
            return [slider.value() for slider in self.sliders.values()]
        else:
            return None

    def update_color_preview(self):
        pixmap = QtGui.QPixmap(self.color_preview.size())
        pixmap.fill(QtGui.QColor(*self.get_rgb_values()))
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
        if not isinstance(checked_pins, (list, tuple)):
            checked_pins = None  # A status value was passed instead. Overwrite
        logger.debug(f'Sending set command with checked pins: {checked_pins}')
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

    def _update_performance(self, left_performance: float, right_performance: float):
        self.left_value_label.setText(f"{left_performance:.2f}")
        self.right_value_label.setText(f"{right_performance:.2f}")

    def _update_corrective_loop(self, corrective_count: int):
        self.corrective_loop_value_label.setText(str(corrective_count))

    def _update_trial_numbers(self, correct_count: int, total_trials: int):
        self.correct_count_value_label.setText(str(correct_count))
        self.total_trials_value_label.setText(str(total_trials))

    def _reset_performance_gui(self):
        """Reset performance and trial GUI fields to initial state when transitioning stages."""
        self.left_value_label.setText(f"{0:.2f}")
        self.right_value_label.setText(f"{0:.2f}")
        self.total_trials_value_label.setText("0")
        self.correct_count_value_label.setText("0")
        self.corrective_loop_value_label.setText("0")
        # If there are other fields to reset, add them here

    def _update_experiment_state_btn(self):
        self.experiment_state_btn.setEnabled(self._serial_connected and self._folder_selected)

    def on_error(self, msg: BeatBoxMessage | str):
        err = msg.payload if isinstance(msg, BeatBoxMessage) else msg
        self.append_log(f'Error: {err}', logging.ERROR)
        dialog = QtWidgets.QErrorMessage()
        dialog.showMessage(err)
        dialog.exec_()

    def append_log(self, message: str, level: int = logging.DEBUG):
        colors = {logging.INFO: "yellow", logging.WARNING: "orange", logging.ERROR: "red"}
        color = colors.get(level, "white")
        formatted = f'<font color="{color}">{message}</font>'
        self.log_text.append(formatted)

    def _create_animal_info_group(self):
        """Create a group box displaying animal information."""
        group = QtWidgets.QGroupBox("Animal Information")
        layout = QtWidgets.QFormLayout(group)
        # Use subject_info if available, else empty
        info = self.subject_info if hasattr(self, 'subject_info') and self.subject_info else None
        self.animal_name_label = QtWidgets.QLabel(info.subject_id if info else "")
        self.weight_label = QtWidgets.QLabel(str(info.weight) if info else "")
        self.box_label = QtWidgets.QLabel(info.box if info else "")
        self.sex_label = QtWidgets.QLabel(info.sex if info else "")
        self.light_cycle_label = QtWidgets.QLabel(info.light_cycle if info else "")
        for label in [self.animal_name_label, self.weight_label, self.box_label, self.sex_label, self.light_cycle_label]:
            label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addRow("Animal Name:", self.animal_name_label)
        layout.addRow("Weight (g):", self.weight_label)
        layout.addRow("Box:", self.box_label)
        layout.addRow("Sex:", self.sex_label)
        layout.addRow("Light Cycle:", self.light_cycle_label)
        return group

    def _update_phase_label(self, phase_name: str):
        self.phase_label.setText(phase_name)

    def _update_stage_label(self, stage_name: str):
        self.stage_label.setText(stage_name)

    def show_blinking_alert(self, show: bool):
        if show:
            self.blinking_alert_label.setText("⚠️ Pellet delivery loop > 5! Check hardware!")
        else:
            self.blinking_alert_label.setText("")

    def _handle_pellet_delivery_alert(self, loop_count: int):
        threshold = 15
        if loop_count > threshold:
            self.blinking_alert_label.setText(f"⚠️ Pellet delivery loop: {loop_count}! Check hardware!")
        else:
            self.blinking_alert_label.setText("")

class StageSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Experiment Stage")
        layout = QtWidgets.QVBoxLayout(self)
        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(["All", "S1", "S2", "S3"]) # FIXME: maybe use enum for stages instead
        layout.addWidget(QtWidgets.QLabel("Choose experiment stage:"))
        layout.addWidget(self.combo)
        # Animal info fields
        self.animal_name_edit = QtWidgets.QLineEdit()
        layout.addWidget(QtWidgets.QLabel("Animal name:"))
        layout.addWidget(self.animal_name_edit)
        self.weight_edit = QtWidgets.QLineEdit()
        layout.addWidget(QtWidgets.QLabel("Weight (g):"))
        layout.addWidget(self.weight_edit)
        self.box_edit = QtWidgets.QLineEdit()
        layout.addWidget(QtWidgets.QLabel("Box:"))
        layout.addWidget(self.box_edit)
        self.sex_combo = QtWidgets.QComboBox()
        self.sex_combo.addItems(["X", "Male", "Female"])  # Default unset at X
        layout.addWidget(QtWidgets.QLabel("Sex:"))
        layout.addWidget(self.sex_combo)
        self.light_cycle_combo = QtWidgets.QComboBox()
        self.light_cycle_combo.addItems(["X", "Red", "White"])  # Default unset at X
        layout.addWidget(QtWidgets.QLabel("Light cycle:"))
        layout.addWidget(self.light_cycle_combo)

        btn = QtWidgets.QPushButton("OK")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

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

    parser = argparse.ArgumentParser("BeatBox Arduino Control App",
                                     description="This app allows you to control the BeatBox from "
                                                 "a python GUI.")
    parser.add_argument("--with-neopixels", action="store_true", default=False,
                        help="Include neopixel controls in the GUI.")
    parser.add_argument("--n-trials", type=int, default=20, help="Number of trials to run.")
    parser.add_argument("--inter-trial-interval", type=int, default=10000,
                        help="Interval between trials in milliseconds.")
    parser.add_argument("--pins-config-file", dest='pins_config_file',
                        type=str, default="~/.beatbox/pins_config.cfg",
                        help="Path to the pins configuration file. You should not have to modify this.")
    parser.add_argument("--from-config-file", type=str, default='',
                        help="Path to a configuration file to load settings from.")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet())
    config_path = Path(args.pins_config_file).expanduser()
    if not config_path.exists():
        warnings.warn(f"Config file {config_path} not installed. "
                      f"Defaulting to the one in the package.")
        config_path = Path(__file__).parent / 'pins_config.cfg'

    # Prompt for stage selection before instantiating ArduinoControlApp
    dialog = StageSelectDialog()
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        sys.exit(0)
    selected_stage = dialog.selected_stage()
    should_transition = False
    if "All" in selected_stage:
        selected_stage = "S1"  # Start from S1 if All is selected
        should_transition = True


    # Now instantiate ArduinoControlApp with the correct stage
    if args.from_config_file and os.path.exists(args.from_config_file):
        params_path = Path(args.from_config_file)
        if not params_path.exists():
            raise FileNotFoundError(f"Configuration file {params_path} not found.")
        params = ConfigObj(args.from_config_file, encoding='utf-8',  unrepr=True)
        window = ArduinoControlApp(str(config_path), experiment_folder=str(Path(args.from_config_file).parent),
                                   with_neopixels=params['with_neopixels'],
                                   n_trials=params['n_trials'], inter_trial_interval=params['inter_trial_interval_ms'],
                                   stage=selected_stage, transition=should_transition, subject_info=dialog.get_subject_info())
    else:
        window = ArduinoControlApp(str(config_path), experiment_folder='', with_neopixels=args.with_neopixels,
                                   n_trials=args.n_trials, inter_trial_interval=args.inter_trial_interval,
                                   stage=selected_stage, transition=should_transition, subject_info=dialog.get_subject_info())

    window.refresh_ports()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
