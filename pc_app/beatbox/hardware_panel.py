"""Actuator controls, sensor LEDs, optional neopixels.

Emits ``manualSetRequested`` when the user toggles any control.
The parent window wires this signal to the experiment manager.
"""

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor, QPixmap
from qtpy.QtWidgets import (QWidget, QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout,
                            QLabel, QSlider, QButtonGroup)

from beatbox.qt_utils import LEDIndicator, check_box, push_button, color_swatch
from beatbox.utils import PinConfig


class HardwarePanel(QWidget):
    """Sensor LEDs + actuator controls + optional neopixels.

    This widget is stage-agnostic. It reports user actions via
    ``manualSetRequested`` — all timing and business logic lives
    in the experiment manager.
    """
    manualSetRequested = Signal(list, object)  # checked list[bool], rgb tuple|None

    def __init__(self, manager, with_neopixels=False, parent=None):
        super().__init__(parent)
        self._mgr = manager
        self._with_neopixels = with_neopixels

        self.actuator_controls: dict[str, QWidget] = {}
        self.actuator_leds: dict[str, LEDIndicator] = {}
        self.sensor_leds: dict[str, LEDIndicator] = {}
        self.left_buttons: list = []
        self.right_buttons: list = []

        # Neopixel state (populated only if enabled)
        self._colors = None
        self._sliders = None
        self._color_preview = None

        self._build_ui()

    def set_manager(self, manager):
        """Point at a new manager after a stage transition."""
        self._mgr = manager

    # ─── Delegate to manager properties ────────────────────────────────────────

    @property
    def _act_cfg(self) -> PinConfig:
        return self._mgr.actuator_pins_cfg

    @property
    def _sen_cfg(self) -> PinConfig:
        return self._mgr.sensors_pins_config

    # ─── UI construction ──────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_sensors())

        self.blinking_alert_label = QLabel('')
        self.blinking_alert_label.setStyleSheet('color: red; font-weight: bold;')
        self.blinking_alert_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.blinking_alert_label)

        layout.addWidget(self._build_actuators())

        if self._with_neopixels:
            self._colors = {
                'red':   QColor(255, 0, 0),
                'green': QColor(0, 255, 0),
                'blue':  QColor(0, 0, 255),
            }
            self._sliders = {col: QSlider(Qt.Horizontal) for col in self._colors}
            self._color_preview = QLabel()
            layout.addWidget(self._build_neopixels())

        self.send_button = push_button('Send Set Command', on_clicked=self._on_control_changed)
        layout.addWidget(self.send_button)

    def _build_sensors(self) -> QGroupBox:
        group = QGroupBox('Sensors')
        grid = QGridLayout(group)
        for row, pin_name in enumerate(self._sen_cfg.pin_names):
            led = LEDIndicator()
            grid.addWidget(QLabel(pin_name.replace('_', ' ').title()),
                           row, 0, alignment=Qt.AlignVCenter)
            grid.addWidget(led, row, 1, alignment=Qt.AlignVCenter)
            self.sensor_leds[pin_name] = led
        return group

    def _build_actuators(self) -> QGroupBox:
        group = QGroupBox('Actuators')
        layout = QVBoxLayout(group)

        general = QGridLayout()
        for row, pin_name in enumerate(p for p in self._act_cfg.pin_names if not p.startswith(('left_', 'right_'))):
            self._add_actuator_pin(general, pin_name, row)
        layout.addLayout(general)

        lr = QHBoxLayout()
        for side in ('left', 'right'):
            lr.addWidget(self._build_side_display(side))
        layout.addLayout(lr)

        return group

    def _build_side_display(self, side: str) -> QGroupBox:
        group = QGroupBox(f'{side.title()} Display')
        grid = QGridLayout(group)
        setattr(self, f'{side}_button_group', QButtonGroup())
        for row, pin_name in enumerate(p for p in self._act_cfg.pin_names if p.startswith(f'{side}_')):
            self._add_actuator_pin(grid, pin_name, row)
        return group

    def _add_actuator_pin(self, layout, pin_name, row):
        title = pin_name.replace('_', ' ').replace('left ', '').replace('right ', '').title()

        if pin_name.startswith(('left_', 'right_')):
            widget = check_box(title, on_changed=self._on_control_changed)
            side_list = self.left_buttons if pin_name.startswith('left_') else self.right_buttons
            side_list.append(widget)
        elif pin_name == 'reward':
            widget = push_button(title, on_clicked=self._on_control_changed, checkable=True)
        else:
            widget = check_box(title, on_changed=self._on_control_changed)

        led = LEDIndicator()
        layout.addWidget(widget, row, 0, alignment=Qt.AlignVCenter)
        layout.addWidget(led, row, 1, alignment=Qt.AlignVCenter)
        self.actuator_controls[pin_name] = widget
        self.actuator_leds[pin_name] = led

    def _build_neopixels(self) -> QGroupBox:
        group = QGroupBox('Neopixel Control')
        layout = QVBoxLayout(group)
        grid = QGridLayout()

        for row, col in enumerate(self._colors):
            grid.addWidget(QLabel(f'{col.title()}:'), row, 0, alignment=Qt.AlignVCenter)
            grid.addWidget(color_swatch(QColor(0, 0, 0)), row, 1, alignment=Qt.AlignVCenter)
            grid.addWidget(self._sliders[col], row, 2, alignment=Qt.AlignVCenter)
            grid.addWidget(color_swatch(self._colors[col]), row, 3, alignment=Qt.AlignVCenter)

        layout.addLayout(grid)
        preview = QHBoxLayout()
        preview.addWidget(QLabel('Preview:'))
        self._color_preview.setFixedSize(50, 50)
        preview.addWidget(self._color_preview)
        layout.addLayout(preview)

        for slider in self._sliders.values():
            slider.valueChanged.connect(self._update_color_preview)
        self._update_color_preview()
        return group

    # ─── Control change → signal ──────────────────────────────────

    def _on_control_changed(self, checked_pins=None):
        """Read all actuator states, merge with explicit overrides, and emit.

        Report user actions, the manager handles reward auto-off, pulse duration, etc.
        """
        ordered_names = self._act_cfg.pin_names

        if not isinstance(checked_pins, (list, tuple, None)):
            raise ValueError(f'checked_pins should be list, tuple or None, got {type(checked_pins)}')

        if checked_pins is None:
            checked = [self.actuator_controls[name].isChecked() for name in ordered_names]
        else:
            checked = [self.actuator_controls[name].isChecked() or pin_on
                       for name, pin_on in zip(ordered_names, checked_pins)]

        # Uncheck reward visually — manager will handle the pulse
        reward_ctrl = self.actuator_controls.get('reward')
        if reward_ctrl:
            reward_ctrl.setChecked(False)

        self.manualSetRequested.emit(checked, self.get_rgb_values())

    def get_rgb_values(self):
        if self._with_neopixels and self._sliders:
            return [s.value() for s in self._sliders.values()]
        return None

    def _update_color_preview(self):
        if self._color_preview and self._sliders:
            pixmap = QPixmap(self._color_preview.size())
            rgb = self.get_rgb_values()
            pixmap.fill(QColor(*rgb) if rgb else QColor(0, 0, 0))
            self._color_preview.setPixmap(pixmap)

    # ─── External update slots ────────────────────────────────────────

    def paint_sensors(self, sensor_bits: list[int]):
        """Update sensor LEDs from an incoming state word."""
        for pin_name, state in zip(self.sensor_leds, sensor_bits):
            led = self.sensor_leds[pin_name]
            if led.state != state:
                led.set_state(state)

    def paint_actuators(self, payload: list[int]):
        """Update actuator LEDs from an outgoing [hi, lo] payload."""
        word = (payload[0] << 8) | payload[1]
        for idx, pin_name in enumerate(self.actuator_leds):
            self.actuator_leds[pin_name].set_state(bool((word >> idx) & 1))  # FIXME: looks like business logic

    def show_pellet_alert(self, loop_count: int):
        """Show or clear the pellet delivery warning based on loop count."""
        threshold = 15
        txt = f'⚠️ Pellet delivery loop: {loop_count}! Check hardware!' if loop_count > threshold else ''
        self.blinking_alert_label.setText(txt)

    def clear_pellet_alert(self):
        """Explicitly clear the pellet delivery warning."""
        self.blinking_alert_label.setText('')