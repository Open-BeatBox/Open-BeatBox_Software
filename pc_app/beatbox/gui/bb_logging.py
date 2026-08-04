import logging
import sys
import atexit
from qtpy.QtCore import QObject, Signal


class QtHandler(logging.Handler, QObject):
    """
    Custom logging handler that emits a Qt signal for each log message.
    This can be used to connect the log messages to a Qt slot.
    """
    logSignal = Signal(str, int)
    # Let logging.shutdown skip flush for this QObject-backed handler.
    flushOnClose = False

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record):
        msg = self.format(record)
        try:
            self.logSignal.emit(msg, record.levelno)
        except RuntimeError:
            # QObject may already be deleted during shutdown.
            pass


# Set up logging
# logging.basicConfig(level=logging.DEBUG)
# logger = logging.getLogger(__name__)

# Configure the root logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
#logger.setLevel(logging.INFO)

# Console handler (or file handler)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# Qt handler that we can connect to UI slots
qt_handler = QtHandler()
qt_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(qt_handler)


def _teardown_qt_handler() -> None:
    try:
        logger.removeHandler(qt_handler)
    except Exception:
        pass
    try:
        qt_handler.close()
    except RuntimeError:
        pass


atexit.register(_teardown_qt_handler)

