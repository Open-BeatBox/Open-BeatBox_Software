"""Compact status card for one DeviceUnit, used in the cards row."""

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QCheckBox


class DeviceSummaryCard(QFrame):
    """Compact card: connection status, performance, phase.

    Clicking the card body emits ``viewRequested``.
    Toggling the checkbox emits ``broadcastToggled``.
    """
    viewRequested    = Signal(object)
    broadcastToggled = Signal(object, bool)

    def __init__(self, unit, parent=None):
        super().__init__(parent)
        self._unit = unit
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setMinimumHeight(120)
        self.setMinimumWidth(200)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # Broadcast checkbox
        self._broadcast_cb = QCheckBox()
        self._broadcast_cb.setToolTip('Include in broadcast')
        self._broadcast_cb.stateChanged.connect(
            lambda state: self.broadcastToggled.emit(self._unit, bool(state)))
        lay.addWidget(self._broadcast_cb, alignment=Qt.AlignTop)

        # Info column
        info = QVBoxLayout()
        info.setSpacing(4)
        subject = unit.subject_info
        name = subject.subject_id if subject else '?'
        self._name_label = QLabel(name)
        self._name_label.setStyleSheet('font-weight: bold; font-size: 13px;')
        self._desc_label = QLabel(unit.description)
        self._desc_label.setStyleSheet('color: grey; font-size: 10px;')
        self._conn_label   = QLabel('⚫ Disconnected')
        self._conn_label.setMinimumHeight(20)
        self._status_label = QLabel('—')
        self._phase_label  = QLabel('')
        for lbl in (self._name_label, self._desc_label, self._conn_label,
                    self._status_label, self._phase_label):
            info.addWidget(lbl)
        lay.addLayout(info)

        # Wire unit signals
        unit.connectionStatusChanged.connect(self._on_connection)
        unit.trialDataChanged.connect(self._on_trial_data)
        unit.performanceChanged.connect(self._on_performance)
        unit.phaseChanged.connect(self._on_phase)

    # ─── Public API ───────────────────────────────────────────────

    @property
    def is_broadcast_target(self) -> bool:
        return self._broadcast_cb.isChecked()

    def set_broadcast_target(self, on: bool) -> None:
        self._broadcast_cb.setChecked(on)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet('DeviceSummaryCard { border: 2px solid #00BFFF; border-radius: 4px; }')
        else:
            self.setStyleSheet('')

    # ─── Signal handlers ──────────────────────────────────────────

    def _on_connection(self, connected: bool):
        self._conn_label.setText(
            '🟢 Connected' if connected else '⚫ Disconnected')

    def _on_trial_data(self, data: dict):
        total   = data.get('total_trials', data.get('current_trial', '?'))
        correct = data.get('total_correct', '')
        parts = [f'Trials: {total}']
        if correct:
            parts.append(f'Correct: {correct}')
        self._status_label.setText('  '.join(parts))

    def _on_performance(self, perf):
        val = perf.overall
        txt = f'{val:.2f}' if val is not None else '—'
        self._status_label.setText(f'Perf: {txt}')

    def _on_phase(self, phase_name: str):
        self._phase_label.setText(phase_name)

    # ─── Mouse handling ───────────────────────────────────────────

    def mousePressEvent(self, event):
        if not self._broadcast_cb.geometry().contains(event.pos()):
            self.viewRequested.emit(self._unit)
        super().mousePressEvent(event)