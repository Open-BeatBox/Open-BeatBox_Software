"""Per-stage experiment data display and controller widgets.

Each stage gets a ``StagePanel`` subclass. A factory function
``create_stage_panel`` maps stage labels to the correct class.
The main window uses a ``QStackedWidget`` to swap panels on stage transitions.
"""

from abc import abstractmethod

from PyQt5.QtWidgets import QBoxLayout
from qtpy.QtWidgets import QWidget, QGroupBox, QVBoxLayout, QHBoxLayout

from beatbox.qt_utils import add_labeled_fields, FieldSpec, labeled_spinbox, labeled_double_spinbox, _derive_attr


class StagePanel(QWidget):
    """Abstract base for per-stage experiment data + optional controllers.

    Subclass contract:
      * ``_populate_data(layout)``  — add field rows to the data group
      * ``_connect_signals()``      — wire manager signals → update methods
      * ``reset_display()``         — set all fields back to initial values
    """
    _stage_label: str | None = None

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._mgr = manager
        self._field_specs: list[FieldSpec] = []
        self._build_ui()
        self._connect_signals()

    def _add_fields(self, layout: QBoxLayout, specs: list[FieldSpec]) -> None:
        """Thin wrapper: calls ``add_labeled_fields`` and accumulates specs."""
        self._field_specs.extend(add_labeled_fields(layout, self, specs))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        ctrl = self._build_controller()
        if ctrl is not None:
            layout.addWidget(ctrl)
        layout.addWidget(self._build_data())

    def _build_controller(self) -> QWidget | None:
        """Override in stages that expose config knobs (blink duration, threshold)."""
        return None

    def _build_data(self) -> QGroupBox:
        """Template method: creates the group, delegates content to the hook."""
        group = QGroupBox('Experiment Data')
        layout = QVBoxLayout(group)
        self._populate_data(layout)
        return group

    @abstractmethod
    def _populate_data(self, layout: QVBoxLayout) -> None: ...

    @abstractmethod
    def _connect_signals(self) -> None: ...

    def reset_display(self) -> None:
        """Reset all fields to their initial values as declared in FieldSpec."""
        for spec in self._field_specs:
            attr = spec.attr or _derive_attr(spec.label)
            label = getattr(self, attr, None)
            if label is not None:
                label.setText(spec.initial)

    def update_params_from_manager(self) -> None:
        """Refresh controller widgets from the current manager. Override as needed."""


class S1Panel(StagePanel):
    _stage_label = 'S1'

    def _populate_data(self, layout):
        row = QHBoxLayout()
        self._add_fields(row, [FieldSpec('Current trial:', 'current_trial_value_label')])
        layout.addLayout(row)

    def _connect_signals(self):
        self._mgr.trialNumbersUpdated.connect(self._update_trial)

    def _update_trial(self, current, _total):
        self.current_trial_value_label.setText(str(current))


class S2Panel(StagePanel):
    _stage_label = 'S2'

    def _build_controller(self):
        group = QGroupBox(f'{self._stage_label} setup')
        lay = QHBoxLayout(group)

        lbl, self.error_flash_spinbox = labeled_spinbox('Blink duration (ms):', lo=1, hi=999_999,
                                                        value=self._mgr.error_flash_ms)
        lay.addWidget(lbl)
        lay.addWidget(self.error_flash_spinbox)
        lbl, self.learning_threshold_spinbox = labeled_double_spinbox('Learning threshold:',
                                                                      value=self._mgr.config.learning_threshold)
        lay.addWidget(lbl)
        lay.addWidget(self.learning_threshold_spinbox)
        return group

    def _populate_data(self, layout):
        perf = QHBoxLayout()
        self._add_fields(perf, [FieldSpec('Overall performance:', 'overall_value_label', '0.00')])
        layout.addLayout(perf)

        info = QHBoxLayout()
        self._add_fields(info, [
            FieldSpec('Total trials:',            'total_trials_value_label'),
            FieldSpec('Valid & correct trials:',  'valid_correct_value_label'),
            FieldSpec('Left touches:',            'left_touches_value_label'),
            FieldSpec('Right touches:',           'right_touches_value_label'),
        ])
        layout.addLayout(info)

    def _connect_signals(self):
        self._mgr.performanceUpdated.connect(self._update_performance)
        self._mgr.trialNumbersUpdated.connect(self._update_trial_numbers)

    def _update_performance(self, perf):
        val = perf.overall if perf.overall is not None else 0.0
        self.overall_value_label.setText(f'{val:.2f}')

    def _update_trial_numbers(self, correct_count, total_trials):
        self.total_trials_value_label.setText(str(total_trials))
        self.valid_correct_value_label.setText(str(getattr(self._mgr, 'total_correct_trials', 0)))
        self.left_touches_value_label.setText(str(getattr(self._mgr, 'left_touches', 0)))
        self.right_touches_value_label.setText(str(getattr(self._mgr, 'right_touches', 0)))

    def update_params_from_manager(self):
        self.error_flash_spinbox.setValue(self._mgr.error_flash_ms)
        self.learning_threshold_spinbox.setValue(self._mgr.config.learning_threshold)


class S3Panel(S2Panel):
    """S3/S4: left/right performance split, corrective loop counter."""

    _stage_label = 'S3'


    def _populate_data(self, layout):
        perf = QHBoxLayout()
        self._add_fields(perf, [
            FieldSpec('Left performance:',  'left_value_label',  '0.00'),
            FieldSpec('Right performance:', 'right_value_label', '0.00'),
        ])
        layout.addLayout(perf)

        info = QHBoxLayout()
        self._add_fields(info, [
            FieldSpec('Total trials:',            'total_trials_value_label'),
            FieldSpec('Valid & correct trials:',  'valid_correct_value_label'),
            FieldSpec('Correct responses count:', 'correct_count_value_label'),
            FieldSpec('Corrective loop count:',   'corrective_loop_value_label'),
        ])
        layout.addLayout(info)

    def _connect_signals(self):
        self._mgr.performanceUpdated.connect(self._update_performance)
        self._mgr.trialNumbersUpdated.connect(self._update_trial_numbers)
        self._mgr.correctiveLoopUpdated.connect(self._update_corrective_loop)

    def _update_performance(self, perf):
        left = perf.left if perf.left is not None else perf.overall
        right = perf.right if perf.right is not None else perf.overall
        self.left_value_label.setText(f'{left:.2f}' if left is not None else '-')
        self.right_value_label.setText(f'{right:.2f}' if right is not None else '-')

    def _update_trial_numbers(self, correct_count, total_trials):
        self.total_trials_value_label.setText(str(total_trials))
        self.valid_correct_value_label.setText(str(getattr(self._mgr, 'total_correct_trials', 0)))
        self.correct_count_value_label.setText(str(correct_count))

    def _update_corrective_loop(self, count):
        self.corrective_loop_value_label.setText(str(count))


class S3_1Panel(S3Panel):
    """Same as S3 but without corrective loop counter."""

    _stage_label = 'S3.1'

    def _populate_data(self, layout):
        perf = QHBoxLayout()
        self._add_fields(perf, [
            FieldSpec('Left performance:',  'left_value_label',  '0.00'),
            FieldSpec('Right performance:', 'right_value_label', '0.00'),
        ])
        layout.addLayout(perf)

        info = QHBoxLayout()
        self._add_fields(info, [
            FieldSpec('Total trials:',            'total_trials_value_label'),
            FieldSpec('Valid & correct trials:',  'valid_correct_value_label'),
            FieldSpec('Correct responses count:', 'correct_count_value_label'),
        ])
        layout.addLayout(info)

    def _connect_signals(self):
        # No corrective loop signal
        self._mgr.performanceUpdated.connect(self._update_performance)
        self._mgr.trialNumbersUpdated.connect(self._update_trial_numbers)


# ─── Factory ─────────────────────────────────────────────────────────


_PANEL_CLASSES: dict[str, type[StagePanel]] = {
    'S1':   S1Panel,
    'S2':   S2Panel,
    'S3':   S3Panel,
    'S3.1': S3_1Panel,
    'S4':   S3Panel,      # S4 uses the same layout as S3
}


def create_stage_panel(stage_label: str, manager, parent=None) -> StagePanel:
    """Create the appropriate ``StagePanel`` subclass for *stage_label*."""
    cls = _PANEL_CLASSES.get(stage_label)
    if cls is None:
        raise ValueError(f'No panel registered for stage "{stage_label}"')
    return cls(manager, parent)
