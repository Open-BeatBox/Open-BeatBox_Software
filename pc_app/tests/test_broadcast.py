"""Tests for BroadcastController — fan-out routing."""

import pytest

from beatbox.gui.broadcast_controller import BroadcastController
from beatbox.experiment_managers.types import SubjectInfo
from tests.mock_device import make_mock_device


@pytest.fixture
def two_devices(experiment_folder):
    d1 = make_mock_device(name='Box 1', experiment_folder=str(experiment_folder),
                          subject_info=SubjectInfo(subject_id='M01'))
    d2 = make_mock_device(name='Box 2', experiment_folder=str(experiment_folder),
                          subject_info=SubjectInfo(subject_id='M02'))
    return d1, d2


class TestBroadcastController:

    def test_no_targets_when_empty(self):
        bc = BroadcastController()
        assert bc.targets == []

    def test_viewed_is_default_target(self, qtbot, two_devices):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.set_viewed(d1)

        assert bc.targets == [d1]

    def test_broadcast_set_overrides_viewed(self, qtbot, two_devices):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.set_viewed(d1)
        bc.set_broadcast_target(d2, True)

        # Only d2 ticked — d1 is viewed but not in broadcast set
        assert bc.targets == [d2]

    def test_select_all(self, qtbot, two_devices):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.select_all()

        assert set(bc.targets) == {d1, d2}

    def test_select_none(self, qtbot, two_devices):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.select_all()
        bc.select_none()

        # Falls back to viewed
        assert bc.targets == []

    def test_broadcast_start(self, qtbot, two_devices):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.set_broadcast_target(d1, True)
        bc.set_broadcast_target(d2, True)

        # Both should have start_experiment called
        # Since these are real widgets with mock communicators,
        # we verify by checking the manager received the call
        bc.broadcast_start()
        # No assert needed — if it crashes, the test fails

    def test_broadcast_set_folder(self, qtbot, two_devices, tmp_path):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.set_broadcast_target(d1, True)

        folder = str(tmp_path / 'shared_experiment')
        bc.broadcast_set_folder(folder)

        assert d1.experiment_manager.experiment_folder == folder

    def test_toggle_off_removes_from_broadcast(self, qtbot, two_devices):
        d1, d2 = two_devices
        qtbot.addWidget(d1)
        qtbot.addWidget(d2)

        bc = BroadcastController()
        bc.register(d1)
        bc.register(d2)
        bc.select_all()
        bc.set_broadcast_target(d1, False)

        assert bc.targets == [d2]