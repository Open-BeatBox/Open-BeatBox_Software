from __future__ import annotations

import re
from typing import NamedTuple

from qtpy.QtGui import QColor, QPixmap, QPainter, QBrush
from qtpy.QtWidgets import (QLabel, QPushButton, QBoxLayout, QDoubleSpinBox, QSpinBox, QWidget, QCheckBox,
                            QComboBox, QFormLayout)
from qtpy import QtCore


# ─── LED helpers ──────────────────────────────────────────────────────────────

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


def color_swatch(color: QColor, size: int = 20) -> QLabel:
    """A fixed-size ``QLabel`` filled with *color*, useful for color previews.

    Unlike :class:`LEDIndicator` which shows a circle, this renders a solid
    square — typically used to display a reference or preview color patch.
    """
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    pixmap = QPixmap(size, size)
    pixmap.fill(color)
    lbl.setPixmap(pixmap)
    lbl.setAlignment(QtCore.Qt.AlignCenter)
    return lbl


class LEDIndicator(QLabel):
    """A simple widget to display a colored LED indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.state = False
        self.set_state(False)

    def set_state(self, state: bool):
        """
        Set the state of the LED indicator.
        True for green, False for red.
        """
        self.state = state
        color = "green" if state else "red"
        self.setPixmap(create_led_pixmap(color, diameter=20))

# ─── Labeled widget helpers ───────────────────────────────────────────────────

class FieldSpec(NamedTuple):
    """Declarative description of one labeled read-only display field.

    Attributes
    ----------
    label : str
        Human-readable caption shown to the user, e.g. ``"Total trials:"``.
    attr : str
        Attribute name set on the owner object, e.g. ``"total_trials_val"``.
        If omitted or empty, derived automatically from *label*:
        ``"Total trials:"`` → ``"total_trials_val"``.
    initial : str
        Text the value ``QLabel`` is initialised with (default ``"0"``).
    """
    label:   str
    attr:    str = ''
    initial: str = '0'


def labeled_widget(label_text: str, widget: QWidget) -> tuple[QLabel, QWidget]:
    """Pair *any* widget with a descriptive ``QLabel``.

    This is the primitive on which every other helper in this module is built.

    Returns
    -------
    tuple[QLabel, QWidget]
        Ready to unpack and add to a layout::

            lbl, spin = labeled_widget("Speed:", my_spinbox)
            layout.addWidget(lbl)
            layout.addWidget(spin)
    """
    return QLabel(label_text), widget


def selectable_label(initial: str = '') -> QLabel:
    """A ``QLabel`` whose text can be selected by the user.

    This is the primitive that :func:`labeled_value` builds on,
    exposed separately for ``QFormLayout.addRow`` callsites where
    the caption is handled by the layout itself.
    """
    lbl = QLabel(initial)
    lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    return lbl


def labeled_value(label_text: str, initial: str = '0') -> tuple[QLabel, QLabel]:
    """Read-only display field: a caption ``QLabel`` + a selectable value ``QLabel``.

    Parameters
    ----------
    label_text : str
        Caption shown to the left of the value.
    initial : str
        Starting text for the value label (default ``"0"``).
    """
    return labeled_widget(label_text, selectable_label(initial))


def check_box(text: str, *, on_changed=None) -> QCheckBox:
    """Construct a ``QCheckBox``, optionally pre-connected to ``stateChanged``.

    Parameters
    ----------
    text : str
        Label shown next to the checkbox.
    on_changed : callable, optional
        Connected to ``stateChanged(int)`` when provided.
    """
    cb = QCheckBox(text)
    if on_changed is not None:
        cb.stateChanged.connect(on_changed)
    return cb


def labeled_spinbox(label_text: str, *, lo: int = 0, hi: int = 999_999,
                    value: int = 0, on_changed=None) -> tuple[QLabel, QSpinBox]:
    """Caption ``QLabel`` + configured ``QSpinBox``.

    Parameters
    ----------
    label_text : str
        Caption shown to the left of the spinbox.
    lo, hi : int
        Allowed range (defaults: 0 … 999 999).
    value : int
        Initial value.
    on_changed : callable, optional
        Connected to ``valueChanged(int)`` when provided.
    """
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setValue(value)
    if on_changed is not None:
        spin.valueChanged.connect(on_changed)
    return labeled_widget(label_text, spin)


def labeled_double_spinbox(label_text: str, *, lo: float = 0.0, hi: float = 1.0,
                           step: float = 0.01, decimals: int = 2, value: float = 0.0,
                           on_changed=None) -> tuple[QLabel, QDoubleSpinBox]:
    """Caption ``QLabel`` + configured ``QDoubleSpinBox``.

    Parameters
    ----------
    label_text : str
        Caption shown to the left of the spinbox.
    lo, hi : float
        Allowed range (defaults: 0.0 … 1.0).
    step : float
        Single-step increment (default 0.01).
    decimals : int
        Decimal places displayed (default 2).
    value : float
        Initial value.
    on_changed : callable, optional
        Connected to ``valueChanged(float)`` when provided.
    """
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setValue(value)
    if on_changed is not None:
        spin.valueChanged.connect(on_changed)
    return labeled_widget(label_text, spin)


def _derive_attr(label: str) -> str:
    """``"Valid & correct trials:"`` → ``"valid_correct_trials_value_label"``."""
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', label)
    slug = re.sub(r'\s+', '_', slug.strip().lower())
    slug = re.sub(r'_+', '_', slug)
    return f"{slug}_value_label"


def add_labeled_fields(layout: QBoxLayout, owner: object, specs: list[FieldSpec]) -> None:
    """Batch-create read-only fields from *specs* and wire them to *owner*.

    For each :class:`FieldSpec`:

    1. Creates a ``(caption QLabel, value QLabel)`` pair.
    2. Appends both to *layout*.
    3. Calls ``setattr(owner, spec.attr, value_label)`` so the caller
       can later update the display with ``self.total_val.setText("42")``.

    Parameters
    ----------
    layout : QBoxLayout
        Target layout (typically a ``QHBoxLayout`` inside a data group).
    owner : object
        Object that will receive the value label as an attribute.
    specs : list[FieldSpec]
        Ordered list of field descriptions.
    """
    for spec in specs:
        attr = spec.attr or _derive_attr(spec.label)
        lbl, val = labeled_value(spec.label, spec.initial)
        setattr(owner, attr, val)
        layout.addWidget(lbl)
        layout.addWidget(val)
    return specs


def add_form_fields(layout: QFormLayout, owner: object, specs: list[FieldSpec]) -> None:
    """Batch-create read-only fields into a ``QFormLayout``.

    Companion to :func:`add_labeled_fields` for form layouts where
    ``addRow`` handles the caption/widget pairing.
    """
    for spec in specs:
        attr = spec.attr or _derive_attr(spec.label)
        val = selectable_label(spec.initial)
        setattr(owner, attr, val)
        layout.addRow(spec.label, val)


def push_button(text: str, *, on_clicked=None, checkable: bool = False, enabled: bool = True) -> QPushButton:
    """Construct a ``QPushButton``, optionally checkable and pre-connected.

    Parameters
    ----------
    text : str
        Button label.
    on_clicked : callable, optional
        Connected to ``clicked`` when provided.
    checkable : bool
        Whether the button toggles (default ``False``).
    """
    btn = QPushButton(text)
    if checkable:
        btn.setCheckable(True)
    if not enabled:
        btn.setEnabled(False)
    if on_clicked is not None:
        btn.clicked.connect(on_clicked)
    return btn


def swap_button(btn: QPushButton, new_text: str, old_slot, new_slot) -> None:
    """Swap a button's label and reconnect its ``clicked`` signal in one step.

    Useful for toggle-style buttons (Start ↔ Stop) where the same widget
    drives two mutually exclusive actions.
    """
    btn.setText(new_text)
    btn.clicked.disconnect(old_slot)
    btn.clicked.connect(new_slot)


def combo_box(items: list[str], *, on_changed=None) -> QComboBox:
    """Construct a ``QComboBox`` pre-populated with *items*.

    Parameters
    ----------
    items : list[str]
        Options added in order via ``addItems``.
    on_changed : callable, optional
        Connected to ``currentTextChanged(str)`` when provided.
    """
    cb = QComboBox()
    cb.addItems(items)
    if on_changed is not None:
        cb.currentTextChanged.connect(on_changed)
    return cb


def safe_delete(widget: QWidget | None) -> None:
    """Schedule *widget* for deletion and return ``None`` to clear the reference.

    Safe to call with ``None`` (no-op). Designed to be used as an assignment
    so the reference is nulled and the C++ object is scheduled for deletion
    in the same expression::

        self.my_label = safe_delete(self.my_label)

    Using ``deleteLater`` rather than direct deletion ensures Qt has finished
    processing any pending events for the widget before it is destroyed.
    """
    if widget is not None:
        widget.deleteLater()
    return None
