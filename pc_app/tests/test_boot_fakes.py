#!/usr/bin/env python
"""Boot the full GUI with mock devices — no real serial ports needed.

Usage: python -m tests.boot_fake_gui
"""

import sys
import shutil
import tempfile
from pathlib import Path

from qtpy.QtWidgets import QApplication
import qdarkstyle

from beatbox.experiment_managers.types import SubjectInfo
from tests.mock_device import make_mock_communicator


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet())

    # Set up experiment folder with configs
    tmp_dir = tempfile.mkdtemp(prefix='beatbox_fake_')
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

    # Also copy pins_config.cfg so PinConfig can find it
    pins_cfg = pkg_dir / 'config' / 'pins_config.cfg'
    if pins_cfg.exists():
        shutil.copy(pins_cfg, Path(tmp_dir) / 'pins_config.cfg')

    from beatbox.gui.arduino_control_app import MultiDeviceApp

    subjects = [
        SubjectInfo(subject_id='M42', weight=25, box='B1', sex='Male', light_cycle='Red'),
        SubjectInfo(subject_id='F17', weight=22, box='B2', sex='Female', light_cycle='White'),
        SubjectInfo(subject_id='M08', weight=27, box='B3', sex='Male', light_cycle='Red'),
    ]

    configs = []
    for i, subj in enumerate(subjects):
        configs.append(dict(
            stage='S1',
            name=f'Box {i + 1}',
            description=f'Rig {i + 1}, Room 4B',
            subject_info=subj,
            experiment_folder=tmp_dir,
            communicator=make_mock_communicator(),
            with_neopixels=False,
            n_trials=10,
            inter_trial_interval=5000,
            transition=True,
        ))

    window = MultiDeviceApp(configs)
    window.resize(1200, 800)
    window.show()

    ret = app.exec_()
    shutil.rmtree(tmp_dir, ignore_errors=True)
    sys.exit(ret)


if __name__ == '__main__':
    main()