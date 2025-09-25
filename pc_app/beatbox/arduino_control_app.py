"""
This module provides a GUI application to control the BeatBox Arduino device.

It allows users to connect to the device, control actuators and monitor sensors directly
as well as run experiments using classes derived from `BaseExperimentManager` like
`BeatBoxExperimentManagerS1` or `BeatBoxExperimentManagerS2`.
"""

import os
import sys
import argparse
import warnings
from pathlib import Path
import logging

from PyQt5.QtWidgets import QPushButton
from configobj import ConfigObj
from qtpy import QtWidgets, QtCore, QtGui
import qdarkstyle

from beatbox.bb_logging import qt_handler, logger
from beatbox.experiment_managers import BeatBoxExperimentManagerS1, BeatBoxExperimentManagerS2
from beatbox.qt_utils import LEDIndicator
from beatbox.serial_com import get_platform_serial_ports, BeatBoxCommunicator, BeatBoxMessage, HandshakeWorker


SERIAL_TIMEOUT = 60 #2  # FIXME: change back to 2

BAUD_RATE = 115200


class ArduinoControlApp(QtWidgets.QMainWindow):
    def __init__(self, pins_config_file, experiment_folder='',
                 with_neopixels=False, n_trials=20, inter_trial_interval=10000):
        super().__init__()

        # First instantiate QtObjects needed by communicator
        self.log_text = QtWidgets.QTextEdit('Log:')

        # Instantiate communicator and connect signals.
        self.communicator = BeatBoxCommunicator(baud_rate=BAUD_RATE, serial_timeout=SERIAL_TIMEOUT)
        self.communicator.connected.connect(self.on_connected)
        self.communicator.handshakeCompleted.connect(self.on_handshake)
        self.communicator.errorOccurred.connect(self.on_error)

        self.with_neopixels = with_neopixels
        self.experiment_manager = BeatBoxExperimentManagerS1(experiment_folder, self.communicator,
                                                             n_trials=n_trials,
                                                             inter_trial_interval=inter_trial_interval,
                                                             main_window=self)

        self.experiment_manager.sensorsStateChanged.connect(self._paint_sensor_leds)
        self.experiment_manager.actuatorsStateChanged.connect(self._paint_actuator_leds)

        # UI Elements
        self.port_combobox = QtWidgets.QComboBox()
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
        self.setup_ui()

        # Connect global log signals to our UI append function.
        qt_handler.logSignal.connect(self.append_log)

        self._handshake_q_thread = QtCore.QThread(self)  # stop with the window
        self._handshake_worker = HandshakeWorker(self.communicator)

        self._connect_progress = None

    def setup_ui(self):
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

        # Experiment folder selection
        experiment_group = QtWidgets.QGroupBox("Experiment Setup")
        experiment_layout = QtWidgets.QHBoxLayout(experiment_group)
        self.experiment_button = QtWidgets.QPushButton("Select Folder")
        self.experiment_button.clicked.connect(self.select_experiment_folder)
        experiment_layout.addWidget(self.experiment_button)

        # Controls for n_trials and inter_trial_interval
        self.n_trials_spinbox = QtWidgets.QSpinBox()
        self.n_trials_spinbox.setRange(1, 9999)
        self.n_trials_spinbox.setValue(self.experiment_manager.n_trials)
        experiment_layout.addWidget(QtWidgets.QLabel("n_trials:"))
        experiment_layout.addWidget(self.n_trials_spinbox)

        self.inter_trial_spinbox = QtWidgets.QSpinBox()
        self.inter_trial_spinbox.setRange(1, 999999)
        self.inter_trial_spinbox.setValue(self.experiment_manager.inter_trial_interval)
        experiment_layout.addWidget(QtWidgets.QLabel("Inter trial (ms):"))
        experiment_layout.addWidget(self.inter_trial_spinbox)

        self.experiment_state_btn = QtWidgets.QPushButton("Start Experiment")
        self.experiment_state_btn.setEnabled(False)
        self.experiment_state_btn.clicked.connect(self.start_experiment)
        experiment_layout.addWidget(self.experiment_state_btn)

        main_layout.addWidget(experiment_group)

        ####################################################################

        self.__setup_actuators(main_layout)

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

        if self.with_neopixels:
            self.setup_neopixels_ui(main_layout)

        # Send command button
        self.send_button.clicked.connect(self.send_set_command)
        main_layout.addWidget(self.send_button)

        # Logging area
        main_layout.addWidget(self.log_text)

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

    def closeEvent(self, event):
        """
        Triggers when window is closed.
        Ensure the communicator shuts down cleanly.
        """
        self.communicator.close()
        super().closeEvent(event)

    def start_experiment(self):
        self.experiment_manager.start_experiment()
        self.experiment_state_btn.setText("Stop Experiment")
        self.experiment_state_btn.clicked.disconnect(self.start_experiment)
        self.experiment_state_btn.clicked.connect(self.stop_experiment)

    def stop_experiment(self):
        # TODO: add end of experiment logging
        self.experiment_manager.end_experiment()
        self.experiment_state_btn.setText("Start Experiment")
        self.experiment_state_btn.clicked.disconnect(self.stop_experiment)
        self.experiment_state_btn.clicked.connect(self.start_experiment)
        logger.debug("Stopping experiment.")

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
            # self._setup_after_connect()  # TODO: see if we need this
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
        # Now we can start experiments
        self.experiment_state_btn.setEnabled(True)
        # Set screen to default state
        self.experiment_manager.handshake_completed()

    def send_set_command(self, checked_pins=None):
        # TODO: add comments, e.g. why use a timer to send a command if it's a reward ?
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
        if checked_pins[0]:  # reward
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

    def on_error(self, msg: BeatBoxMessage | str):
        err = msg.payload if isinstance(msg, BeatBoxMessage) else msg
        self.append_log(f'Error: {err}', logging.ERROR)
        dialog = QtWidgets.QErrorMessage()
        dialog.showMessage(err)
        dialog.exec_()

    def append_log(self, message: str, level: int = logging.DEBUG):
        colors = {logging.WARNING: "orange", logging.ERROR: "red"}
        color = colors.get(level, "white")
        formatted = f'<font color="{color}">{message}</font>'
        self.log_text.append(formatted)


def main():

    parser = argparse.ArgumentParser("BeatBox Arduino Control App",
                                     description="This app allows you to control the BeatBox from "
                                                 "a python GUI.")
    parser.add_argument("--with-neopixels", action="store_true", default=False,
                        help="Include neopixel controls in the GUI.")
    parser.add_argument("--n-trials", type=int, default=20, help="Number of trials to run.")
    parser.add_argument("--inter-trial-interval", type=int, default=10000,
                        help="Interval between trials in milliseconds.")
    # FIXME: look inside the working directory rather than user space
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

    if args.from_config_file and os.path.exists(args.from_config_file):
        params_path = Path(args.from_config_file)
        if not params_path.exists():
            raise FileNotFoundError(f"Configuration file {params_path} not found.")
        params = ConfigObj(args.from_config_file, encoding='utf-8',  unrepr=True)
        window = ArduinoControlApp(str(config_path), experiment_folder=str(Path(args.from_config_file).parent),
                                   with_neopixels=params['with_neopixels'],
                                   n_trials=params['n_trials'], inter_trial_interval=params['inter_trial_interval_ms'])
    else:
        window = ArduinoControlApp(str(config_path), experiment_folder='',
                                   with_neopixels=args.with_neopixels,
                                   n_trials=args.n_trials, inter_trial_interval=args.inter_trial_interval)

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
