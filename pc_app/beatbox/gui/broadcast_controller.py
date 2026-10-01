"""Routes manual actions to a selected set of DeviceUnits."""

from qtpy.QtCore import QObject


class BroadcastController(QObject):
    """Routes actions to selected devices.

    When no devices are ticked, actions go only to the currently
    viewed device. When devices are ticked, actions fan out to all
    of them.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices:       list  = []
        self._broadcast_set: set   = set()
        self._viewed:        object | None = None

    # ─── Registration ─────────────────────────────────────────────

    def register(self, unit) -> None:
        if unit not in self._devices:
            self._devices.append(unit)

    # ─── Selection ────────────────────────────────────────────────

    def set_viewed(self, unit) -> None:
        self._viewed = unit

    def set_broadcast_target(self, unit, on: bool) -> None:
        if on:
            self._broadcast_set.add(id(unit))
        else:
            self._broadcast_set.discard(id(unit))

    def select_all(self) -> None:
        for unit in self._devices:
            self._broadcast_set.add(id(unit))

    def select_none(self) -> None:
        self._broadcast_set.clear()

    @property
    def targets(self) -> list:
        if self._broadcast_set:
            return [u for u in self._devices if id(u) in self._broadcast_set]
        elif self._viewed is not None:
            return [self._viewed]
        return []

    # ─── Broadcast actions ────────────────────────────────────────

    def broadcast_manual_set(self, checked: list, rgb) -> None:
        for unit in self.targets:
            unit.experiment_manager.request_manual_action_by_pins_and_rgb(
                checked, rgb)

    def broadcast_start(self) -> None:
        for unit in self.targets:
            unit.start_experiment()

    def broadcast_stop(self) -> None:
        for unit in self.targets:
            unit.stop_experiment()

    def broadcast_set_folder(self, folder: str) -> None:
        for unit in self.targets:
            unit.experiment_manager.set_experiment_folder(folder)