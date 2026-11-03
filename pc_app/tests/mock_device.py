"""Mock BBDeviceWidget factory for testing without real serial ports."""

from unittest.mock import MagicMock
from pathlib import Path

from beatbox.experiment_managers.types import SubjectInfo


def make_mock_communicator():
    """Return a MagicMock that quacks like BeatBoxCommunicator.

    Signals are mocked so BBDeviceWidget.__init__ can wire them.
    Real Qt signals can't be mocked easily, so we use MagicMock
    with .connect/.disconnect/.emit stubs.
    """
    comm = MagicMock()
    for sig_name in ('connected', 'handshakeCompleted', 'errorOccurred',
                     'stateValueReceived', 'messageReceived'):
        sig = MagicMock()
        sig.connect    = MagicMock()
        sig.disconnect = MagicMock()
        sig.emit       = MagicMock()
        setattr(comm, sig_name, sig)
    comm.serial_port = MagicMock()
    comm.serial_port.is_open = True
    comm.send_message     = MagicMock()
    comm.send_set_payload = MagicMock()
    comm.close            = MagicMock()
    return comm


def make_mock_device(stage='S1', name='Test Box',
                     experiment_folder='', subject_info=None,
                     **kwargs) -> "BBDeviceWidget":
    """Create a BBDeviceWidget with a mock communicator.

    No real serial port needed. ``experiment_folder`` should point
    to a directory with valid config files (use the ``experiment_folder``
    fixture from conftest.py).
    """
    # Defer import to avoid circular issues at collection time
    from beatbox.gui.bb_device_widget import BBDeviceWidget

    return BBDeviceWidget(
        stage=stage,
        name=name,
        subject_info=subject_info or SubjectInfo(subject_id='TEST'),
        experiment_folder=experiment_folder,
        communicator=make_mock_communicator(),
        **kwargs,
    )