"""Shared fixtures for all tests."""

import shutil
from pathlib import Path

import pytest

from beatbox.experiment_managers.types import SubjectInfo


@pytest.fixture
def fake_arduino():
    from tests.fake_arduino import MockArduino
    device = MockArduino()
    yield device
    device.close()


@pytest.fixture
def experiment_folder(tmp_path):
    """Create a minimal experiment folder with required config files."""
    # Copy default configs into tmp_path so managers can load them
    pkg_dir = Path(__file__).parent.parent / 'beatbox'

    # Copy experiment.cfg
    config_dir = tmp_path / 'config'
    config_dir.mkdir()
    src_cfg = pkg_dir / 'config' / 'experiment.cfg'
    if src_cfg.exists():
        shutil.copy(src_cfg, config_dir / 'experiment.cfg')

    # Copy config specs
    specs_dir = config_dir / 'config_specs'
    specs_dir.mkdir()
    src_specs = pkg_dir / 'config' / 'config_specs'
    if src_specs.exists():
        for spec_file in src_specs.glob('*.config.spec'):
            shutil.copy(spec_file, specs_dir / spec_file.name)

    return tmp_path