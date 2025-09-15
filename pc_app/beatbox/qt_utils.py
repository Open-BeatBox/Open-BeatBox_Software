from PyQt5.QtGui import QPixmap, QPainter, QBrush, QColor
from PyQt5.QtWidgets import QLabel
from qtpy import QtCore


def create_led_pixmap(color: str, diameter: int = 20) -> QPixmap:
    """Create a QPixmap with a colored circle."""
    pixmap = QPixmap(diameter, diameter)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawEllipse(0, 0, diameter, diameter)
    painter.end()
    return pixmap


class LEDIndicator(QLabel):
    """A simple widget to display a colored LED indicator."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.set_state(False)
        self.state = False

    def set_state(self, state: bool):
        """
        Set the state of the LED indicator.
        True for green, False for red.
        """
        self.state = state
        color = "green" if state else "red"
        self.setPixmap(create_led_pixmap(color, diameter=20))
