from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final, Union, Mapping, Callable, Any

ANY_PIN: Final = object()


class SubjectInfo:
    """
    Class to hold subject information.
    """

    def __init__(self, subject_id: str = '', weight: int = 0,
                 box: str = '', sex: str = '', light_cycle: str = ''):
        self.subject_id = subject_id
        self.weight = weight
        self.box = box
        self.sex = sex
        self.light_cycle = light_cycle

    def as_string(self) -> str:
        return (f"Subject Info: ID={self.subject_id}, Weight={self.weight}g, Box={self.box}, "
                f"Sex={self.sex}, Light Cycle={self.light_cycle}")


class Edge(Enum):
    RISING = 'rising'
    FALLING = 'falling'
    BOTH = 'both'


RelevantPins = Union[
    ANY_PIN,
    None,  # ignore all pins
    Mapping[str, Edge],  # pin -> required edge
]


class Phase(Enum):
    """
    Enum representing the different phases of an experiment.
    Each phase corresponds to a specific state in the experiment flow.
    Add new phases here as needed for the experiment.
    """
    INIT = auto()
    ITI = auto()
    WAIT_TUNNEL = auto()
    DELIVER_PELLET = auto()
    WAIT_PELLET = auto()
    WAIT_PELLET_RETRIEVAL = auto()
    SHOW_STIM = auto()
    WAIT_TOUCH = auto()
    CORRECT_RESPONSE = auto()
    INCORRECT_RESPONSE = auto()
    DISCOVERY_RESPONSE = auto()
    SUCCESS_ROLL = auto()  # specific to S4, reward in case of random success after discovery response
    STOP_FLASH = auto()  # used to stop flashing the error light
    DONE = auto()


DecisionFn = Callable[['BaseExperimentManager'], Any]  # returns a key for the dict
OnEnterFn = Callable[[], None]  # No return as there is "nobody" to return to in the state machine


@dataclass
class PhaseSpec:
    """
    Specification for a phase in the experiment.
    This class defines the properties of a phase.
    Instances of this class are used to define a phase_table to detail the behavior
    of each phase in the experiment.

    Attributes:
    ----------
    timeout_ms: int | None
        The timeout after which the next action triggers (in milliseconds). If None, no timer is set.
    on_enter: OnEnterFn | None
        The method to call when entering this phase. If None, no method is called.
    decide: DecisionFn | None
        A function that decides the next phase based on the current state of the experiment manager.
    routes: Mapping[Any, Phase] | None
        A mapping of keys to phases, defining the possible transitions from this phase.
    next_phase_default: Phase | None
        The default phase to transition to if no other route matches.
    relevant_pins: ANY_PIN, None,  Mapping[str, Edge],
        A list of pin names that are relevant for this phase. If ANY_PIN, any pin is relevant.
        If None, no pins are considered relevant. Irrelevant pins are ignored.
    """
    on_enter: OnEnterFn | None = None  # method name
    next_phase_default: Phase | None = None  # fallback next phase if timer hit or no decide
    routes: Mapping[Any, Phase] | None = field(default_factory=dict)  # Map from e.g. bool or str to Phase spec
    timeout_ms: int | None = None  # default, None → no timer
    decide: DecisionFn | None = None
    relevant_pins: RelevantPins = ANY_PIN  # by default, ANY_PIN means any pin is relevant,

    # None means ignore all pins
    # FIXME: prefer properties instead maybe ?
    def set_timeout(self, value):
        if value is None or value < 0:
            return
        self.timeout_ms = value


@dataclass
class PerformanceData:
    overall: float = None
    left: float = None
    right: float = None

    @property
    def display_left(self) -> float | None:
        """Resolved value for display — fallback to overall if absent."""
        return self.left if self.left is not None else self.overall

    @property
    def display_right(self) -> float | None:
        """Resolved value for display — fallback to overall if absent."""
        return self.right if self.right is not None else self.overall
