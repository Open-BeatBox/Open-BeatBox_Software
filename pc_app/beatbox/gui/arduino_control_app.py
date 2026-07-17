"""BeatBox multi-device GUI application.

``MultiDeviceApp`` manages N ``BBDeviceWidget`` widgets in a card + detail layout.
Single-device use is just ``--n-devices 1`` (the default).

It allows users to connect to the device, control actuators and monitor sensors directly
as well as run experiments
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import warnings
import logging
from pathlib import Path

from configobj import ConfigObj

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QMainWindow, QWidget, QTextEdit, QLineEdit, QLabel, QStackedWidget,
                            QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
                            QDialog, QFileDialog, QMessageBox, QProgressDialog, QErrorMessage, QApplication)
import qdarkstyle

from beatbox.gui.bb_logging import qt_handler
from beatbox.experiment_managers.registry import _STAGE_MANAGER_CLASSES
from beatbox.experiment_managers.types import SubjectInfo
from beatbox.gui.bb_device_widget import BBDeviceWidget
from beatbox.gui.device_summary_card import DeviceSummaryCard
from beatbox.gui.broadcast_controller import BroadcastController
from beatbox.gui.qt_utils import push_button, combo_box
from beatbox.comm.serial_com import BeatBoxMessage


class MultiDeviceApp(QMainWindow):
    """Main window: cards row + detail panel + shared log + broadcast bar."""

    def __init__(self, device_configs: list[dict]):
        super().__init__()
        self.setWindowTitle('BeatBox Controller')

        # Log area — created early so error handlers can use it
        self.log_text = QTextEdit('Log:')
        self.log_text.setReadOnly(True)
        qt_handler.logSignal.connect(self._append_log)

        # Build devices
        self._devices: list[BBDeviceWidget] = []
        for i, cfg in enumerate(device_configs):
            cfg.setdefault('name', f'Box {i + 1}')
            self._devices.append(BBDeviceWidget(**cfg, parent=self))

        self._cards: list[DeviceSummaryCard] = []
        self._broadcast = BroadcastController(parent=self)
        for unit in self._devices:
            self._broadcast.register(unit)

        self._current_detail: BBDeviceWidget | None = None

        self._build_ui()

        # Show first device by default
        if self._devices:
            self._show_unit(self._devices[0])

        for unit in self._devices:
            unit.communicator.errorOccurred.connect(self._on_error)

    # ─── UI construction ──────────────────────────────────────────

    def _build_ui(self):
        """Compose the main window from panels — no stage-specific logic."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Only show broadcast bar for multi-device
        if len(self._devices) > 1:
            main_layout.addWidget(self._build_broadcast_bar())

        main_layout.addLayout(self._build_cards_row())

        self._detail_stack = QStackedWidget()
        for unit in self._devices:
            self._detail_stack.addWidget(unit)
        main_layout.addWidget(self._detail_stack)

        main_layout.addWidget(self.log_text)

    def _build_broadcast_bar(self) -> QGroupBox:
        group = QGroupBox('Broadcast Controls')
        lay = QHBoxLayout(group)
        lay.addWidget(push_button('Select All', on_clicked=self._select_all))
        lay.addWidget(push_button('Select None', on_clicked=self._select_none))
        lay.addWidget(push_button('▶ Start Selected', on_clicked=self._broadcast.broadcast_start))
        lay.addWidget(push_button('■ Stop Selected', on_clicked=self._broadcast.broadcast_stop))
        lay.addWidget(push_button('📁 Set Folder for Selected', on_clicked=self._broadcast_select_folder))
        return group

    def _build_cards_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        for unit in self._devices:
            card = DeviceSummaryCard(unit)
            card.viewRequested.connect(self._show_unit)
            card.broadcastToggled.connect(self._on_broadcast_toggled)
            self._cards.append(card)
            row.addWidget(card)
        row.addStretch()
        return row

    # ─── Detail panel routing ─────────────────────────────────────

    def _show_unit(self, unit: BBDeviceWidget):
        # Disconnect previous detail panel from broadcast
        if self._current_detail is not None:
            try:
                self._current_detail.hw_panel.manualSetRequested.disconnect(self._broadcast.broadcast_manual_set)
            except (TypeError, RuntimeError):
                pass

        self._detail_stack.setCurrentWidget(unit)
        self._broadcast.set_viewed(unit)
        self._current_detail = unit

        # Route this panel's manual actions through broadcast
        unit.hw_panel.manualSetRequested.connect(self._broadcast.broadcast_manual_set)

        # Highlight selected card
        for i, d in enumerate(self._devices):
            self._cards[i].set_selected(d is unit)

    # ─── Broadcast helpers ────────────────────────────────────────

    def _on_broadcast_toggled(self, unit: BBDeviceWidget, on: bool):
        self._broadcast.set_broadcast_target(unit, on)

    def _select_all(self):
        self._broadcast.select_all()
        for card in self._cards:
            card.set_broadcast_target(True)

    def _select_none(self):
        self._broadcast.select_none()
        for card in self._cards:
            card.set_broadcast_target(False)

    def _broadcast_select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Set Experiment Folder for Selected Devices')
        if folder:
            self._broadcast.broadcast_set_folder(folder)

    def _on_error(self, msg: BeatBoxMessage | str):
        err = msg.payload if isinstance(msg, BeatBoxMessage) else msg
        self._append_log(f'Error: {err}', logging.ERROR)
        dialog = QErrorMessage()
        dialog.showMessage(err)
        dialog.exec_()

    # ─── Logging ──────────────────────────────────────────────────

    def _append_log(self, message: str, level: int = logging.DEBUG):
        colors = {logging.INFO: 'yellow', logging.WARNING: 'orange', logging.ERROR: 'red'}
        color = colors.get(level, 'white')
        escaped = message.replace('\n', '<br>')  # So html formats the new lines correctly
        formatted = f'<font color="{color}">{escaped}</font>'
        self.log_text.append(formatted)

    def closeEvent(self, event):
        """
        Triggers when window is closed.
        Ensure the communicator shuts down cleanly.
        """
        for unit in self._devices:
            unit.close()
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

    def get_subject_info(self) -> SubjectInfo:
        return SubjectInfo(
            subject_id=self.animal_name(),
            weight=self.weight(),
            box=self.box(),
            sex=self.sex(),
            light_cycle=self.light_cycle()
        )

    def selected_stage(self) -> str:
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
    parser = argparse.ArgumentParser('BeatBox Controller', description='Control one or more BeatBox devices from a Python GUI.')
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
    parser.add_argument('--n-devices', type=int, default=1,
                        help='Number of device units (for multi-box use).')
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
                  inter_trial_interval=args.inter_trial_interval, experiment_folder='',
                  pins_config_file=str(config_path))

    # Now instantiate ArduinoControlApp with the correct stage
    if args.from_config_file and os.path.exists(args.from_config_file):
        params = ConfigObj(args.from_config_file, encoding='utf-8', unrepr=True)
        kwargs.update(experiment_folder=str(Path(args.from_config_file).parent), with_neopixels=params['with_neopixels'],
                      n_trials=params['n_trials'], inter_trial_interval=params['inter_trial_interval_ms'])

    device_configs = [kwargs.copy() for _ in range(args.n_devices)]
    for i, cfg in enumerate(device_configs):
        cfg['name'] = f'Box {i + 1}'

    window = MultiDeviceApp(device_configs)
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
