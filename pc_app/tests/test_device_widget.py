"""Tests for BBDeviceWidget — GUI logic, signals, stage transitions."""

from unittest.mock import MagicMock

import pytest

from beatbox.experiment_managers.types import SubjectInfo
from tests.mock_device import make_mock_device


@pytest.fixture
def mock_device(experiment_folder):
    """Create a mock device with valid config files."""
    return make_mock_device(
        stage='S1',
        name='Test Box',
        experiment_folder=str(experiment_folder),
        subject_info=SubjectInfo(subject_id='M42'),
    )


class TestBBDeviceWidget:

    def test_creation(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        assert mock_device.name == 'Test Box'
        assert mock_device.experiment_manager is not None

    def test_trial_data_forwarded(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        received = []
        mock_device.trialDataChanged.connect(received.append)

        mock_device.experiment_manager._emit_trial_data()

        assert len(received) == 1
        assert 'current_trial' in received[0]

    def test_stage_transition(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        mock_device.enable_transitions = True

        old_manager = mock_device.experiment_manager
        mock_device.handle_stage_end('S1')

        assert mock_device.experiment_manager is not old_manager
        assert mock_device.experiment_manager.stage_number == 2

    def test_connection_status_signal(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        received = []
        mock_device.connectionStatusChanged.connect(received.append)

        # Simulate successful handshake
        mock_device._connect_progress = MagicMock()
        mock_device._on_handshake_done(True)

        assert received == [True]
        assert mock_device._serial_connected is True

    def test_connection_failure(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        received = []
        mock_device.connectionStatusChanged.connect(received.append)

        mock_device._connect_progress = MagicMock()  # avoid real dialog
        mock_device._on_handshake_done(False)

        assert received == [False]
        assert mock_device._serial_connected is False

    def test_experiment_folder_updates_button(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        mock_device._serial_connected = True
        mock_device._folder_selected = True
        mock_device._update_experiment_state_btn()

        assert mock_device.experiment_state_btn.isEnabled()

    def test_button_disabled_without_connection(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        mock_device._serial_connected = False
        mock_device._folder_selected = True
        mock_device._update_experiment_state_btn()

        assert not mock_device.experiment_state_btn.isEnabled()

    def test_button_disabled_without_folder(self, qtbot, mock_device):
        qtbot.addWidget(mock_device)
        mock_device._serial_connected = True
        mock_device._folder_selected = False
        mock_device._update_experiment_state_btn()

        assert not mock_device.experiment_state_btn.isEnabled()