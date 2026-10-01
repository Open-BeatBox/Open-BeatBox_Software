# tests/test_gui_boot.py
"""Smoke test: boot the full GUI with mock devices.

Run interactively:  python -m tests.test_gui_boot
Run as test:        pytest tests/test_gui_boot.py -v
"""

import sys

import pytest

from beatbox.experiment_managers.types import SubjectInfo
from tests.mock_device import make_mock_device


def _make_app_with_mock_devices(experiment_folder: str, n_devices: int = 2):
    """Create a MultiDeviceApp with mock communicators — no real serial ports."""
    from beatbox.gui.arduino_control_app import MultiDeviceApp

    device_configs = []
    for i in range(n_devices):
        device_configs.append(dict(
            stage='S1',
            name=f'Mock Box {i + 1}',
            description=f'Simulated device {i + 1}',
            subject_info=SubjectInfo(subject_id=f'MOCK_{i + 1}'),
            experiment_folder=experiment_folder,
            communicator=make_mock_device.__wrapped__
                if hasattr(make_mock_device, '__wrapped__')
                else None,  # handled below
            with_neopixels=False,
            n_trials=10,
            inter_trial_interval=5000,
            transition=True,
        ))
    return device_configs


class TestGUIBoot:
    """Smoke tests — verify the GUI builds without crashing."""

    def test_single_device_boot(self, qtbot, experiment_folder):
        from beatbox.gui.arduino_control_app import MultiDeviceApp
        from tests.mock_device import make_mock_communicator

        configs = [dict(
            stage='S1',
            name='Mock Box 1',
            subject_info=SubjectInfo(subject_id='MOCK_1'),
            experiment_folder=str(experiment_folder),
            communicator=make_mock_communicator(),
            with_neopixels=False,
            n_trials=10,
            inter_trial_interval=5000,
            transition=False,
        )]
        window = MultiDeviceApp(configs)
        qtbot.addWidget(window)
        assert window._devices[0].name == 'Mock Box 1'

    def test_multi_device_boot(self, qtbot, experiment_folder):
        from beatbox.gui.arduino_control_app import MultiDeviceApp
        from tests.mock_device import make_mock_communicator

        configs = []
        for i in range(3):
            configs.append(dict(
                stage='S1',
                name=f'Mock Box {i + 1}',
                description=f'Simulated device {i + 1}',
                subject_info=SubjectInfo(subject_id=f'MOCK_{i + 1}'),
                experiment_folder=str(experiment_folder),
                communicator=make_mock_communicator(),
                with_neopixels=False,
                n_trials=10,
                inter_trial_interval=5000,
                transition=True,
            ))
        window = MultiDeviceApp(configs)
        qtbot.addWidget(window)

        assert len(window._devices) == 3
        assert len(window._cards) == 3
        assert window._devices[0].name == 'Mock Box 1'
        assert window._devices[2].name == 'Mock Box 3'

    def test_card_selection(self, qtbot, experiment_folder):
        from beatbox.gui.arduino_control_app import MultiDeviceApp
        from tests.mock_device import make_mock_communicator

        configs = []
        for i in range(2):
            configs.append(dict(
                stage='S1',
                name=f'Mock Box {i + 1}',
                subject_info=SubjectInfo(subject_id=f'MOCK_{i + 1}'),
                experiment_folder=str(experiment_folder),
                communicator=make_mock_communicator(),
            ))
        window = MultiDeviceApp(configs)
        qtbot.addWidget(window)

        # Click second card
        window._show_unit(window._devices[1])
        assert window._current_detail is window._devices[1]


# ─── Interactive mode ─────────────────────────────────────────────

if __name__ == '__main__':
    """Run this file directly to see the GUI with mock devices.

    Usage: python -m tests.test_gui_boot
    """
    import shutil
    import tempfile
    from pathlib import Path
    from qtpy.QtWidgets import QApplication
    import qdarkstyle
    from tests.mock_device import make_mock_communicator

    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet())

    # Set up experiment folder with configs
    tmp_dir = tempfile.mkdtemp(prefix='beatbox_test_')
    pkg_dir = Path(__file__).parent.parent / 'beatbox'
    config_dir = Path(tmp_dir) / 'config'
    config_dir.mkdir()
    src_cfg = pkg_dir / 'config' / 'experiment.cfg'
    if src_cfg.exists():
        shutil.copy(src_cfg, config_dir / 'experiment.cfg')
    specs_dir = config_dir / 'config_specs'
    specs_dir.mkdir()
    src_specs = pkg_dir / 'config' / 'config_specs'
    if src_specs.exists():
        for f in src_specs.glob('*.config.spec'):
            shutil.copy(f, specs_dir / f.name)

    from beatbox.gui.arduino_control_app import MultiDeviceApp

    configs = []
    for i in range(3):
        configs.append(dict(
            stage='S1',
            name=f'Mock Box {i + 1}',
            description=f'Simulated device {i + 1}',
            subject_info=SubjectInfo(subject_id=f'MOUSE_{i + 1}',
                                     weight=25 + i,
                                     box=f'B{i + 1}',
                                     sex='Male',
                                     light_cycle='Red'),
            experiment_folder=tmp_dir,
            communicator=make_mock_communicator(),
            with_neopixels=False,
            n_trials=10,
            inter_trial_interval=5000,
            transition=True,
        ))

    window = MultiDeviceApp(configs)
    window.show()
    ret = app.exec_()

    # Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)
    sys.exit(ret)