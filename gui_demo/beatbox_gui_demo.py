"""Interactive Open-BEATBox GUI front-end demo with realistic mock data.

This prototype is intentionally hardware-free. It models the software surfaces
of the real desktop controller as documented in:

- ``TECHNICAL_GUIDE.md``  -- architecture, connection-state contract, USB serial
  protocol (115200 baud framed ``ready``/``set``/``ack``/``state`` messages with
  CRC-8), and the ``pins_config.cfg`` bit-ordered actuator mask.
- ``RECORDING_OUTPUTS.md`` -- the FULL event CSV and trial-summary CSV schemas,
  the per-stage metrics (trials, correct/incorrect/valid, corrective loops,
  per-side and overall performance), and the S1/S4 specialised counters.

The production app is PyQt; this mock uses only the Python standard library
(``tkinter``) so it runs with no extra dependencies. The realism here is in the
*data, protocol, metrics, and recording artifacts*, not the widget toolkit.

Run with:
    python beatbox_gui_demo.py
"""

from __future__ import annotations

import random
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from tkinter import messagebox, ttk


# --- palette ---------------------------------------------------------------
BG = "#101923"
PANEL = "#172331"
PANEL_2 = "#1f2d3c"
BORDER = "#314357"
TEXT = "#e8f1f8"
MUTED = "#91a6b7"
GREEN = "#14b84a"
RED = "#ff2d2d"
YELLOW = "#f2c94c"
BLUE = "#5c7cfa"


# --- protocol / hardware contract (from TECHNICAL_GUIDE.md) ----------------
BAUD = 115200

# Sensor inputs in pins_config.cfg order. The bit order of the device `state`
# byte and of the recording `state_<sensor>` CSV columns follows this order.
SENSOR_ORDER = [
    "nose_poke",
    "left_response",
    "right_response",
    "left_tunnel",
    "right_tunnel",
    "food_alert",
]
SENSOR_LABELS = {
    "nose_poke": "Nose poke",
    "left_response": "Left response",
    "right_response": "Right response",
    "left_tunnel": "Left tunnel crossing",
    "right_tunnel": "Right tunnel crossing",
    "food_alert": "Food alert",
}

# Actuator outputs and their mask bit positions. Per TECHNICAL_GUIDE.md the
# current configuration uses bits 0-10 for reward, the left/right pattern bits,
# light, and red_light. Order defines the wire meaning of every bit.
ACTUATOR_BITS = [
    ("reward", 0),
    ("left_visual", 1),
    ("left_lines", 2),
    ("left_white", 3),
    ("left_black", 4),
    ("right_visual", 5),
    ("right_lines", 6),
    ("right_white", 7),
    ("right_black", 8),
    ("light", 9),
    ("red_light", 10),
]
ACTUATOR_LABELS = {
    "reward": "Reward",
    "light": "Lights",
    "red_light": "Red light",
    "left_visual": "Visual",
    "left_lines": "Lines",
    "left_white": "White",
    "left_black": "Black",
    "right_visual": "Visual",
    "right_lines": "Lines",
    "right_white": "White",
    "right_black": "Black",
}

# Connection-state machine that TECHNICAL_GUIDE.md section 6.2 asks the UI to
# surface explicitly.
ST_DISCONNECTED = "DISCONNECTED"
ST_PORT_OPEN = "PORT OPEN"
ST_HANDSHAKING = "HANDSHAKING"
ST_CONNECTED = "CONNECTED"
ST_FAULT = "COMMUNICATION FAULT"
ST_STOPPED = "STOPPED"

STATE_COLORS = {
    ST_DISCONNECTED: MUTED,
    ST_PORT_OPEN: YELLOW,
    ST_HANDSHAKING: YELLOW,
    ST_CONNECTED: GREEN,
    ST_FAULT: RED,
    ST_STOPPED: MUTED,
}

# Valid stages (RECORDING_OUTPUTS.md): 1, 2, 3, 3.1, 4.
STAGES = ["1", "2", "3", "3.1", "4"]


def crc8(data: bytes) -> int:
    """CRC-8 with polynomial 0x07 / init 0x00 (TECHNICAL_GUIDE.md section 4)."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_frame(command: str, payload: bytes, seq: int, encoding: str = "x") -> str:
    """Build a normal protocol frame: <command>:<enc><seq><payload><crc8>.

    Mirrors BeatBoxMessage: two lowercase hex chars per byte, two-hex sequence,
    CRC-8 computed over the characters before the CRC.
    """
    body = f"{command}:{encoding}{seq:02x}" + "".join(f"{b:02x}" for b in payload)
    return f"{body}{crc8(body.encode('ascii')):02x}"


def sensors_to_byte(sensors: dict[str, bool]) -> int:
    byte = 0
    for bit, name in enumerate(SENSOR_ORDER):
        if sensors.get(name):
            byte |= 1 << bit
    return byte


def actuators_to_mask(actuators: dict[str, bool]) -> int:
    mask = 0
    for name, bit in ACTUATOR_BITS:
        if actuators.get(name):
            mask |= 1 << bit
    return mask


# --- per-trial outcome -----------------------------------------------------
@dataclass
class TrialPlan:
    correct_side: str  # 'l' / 'r' / 'x'
    response: str | None  # 'l' / 'r' / 'discovery' / None
    correct: bool
    valid: bool
    corrective: bool
    discovery: bool
    roll_success: bool
    show_stim_ts: float = 0.0
    screen_touch_ts: float = 0.0
    reward_collect_ts: float = 0.0


@dataclass
class BoxState:
    box_id: int
    subject_id: str
    weight_g: int
    stage: str
    sex: str = "Male"
    light_cycle: str = "White"
    current_phase: str = "PHASE_INIT"

    # Config (mirrors the stage config_specs fields referenced in the docs).
    n_trials: int = 50
    learning_threshold: float = 0.75
    time_pressure_ms: int = 5000
    intertrial_s: int = 5

    # S2+ trial counters (RECORDING_OUTPUTS.md "Metrics").
    total_trials: int = 0
    total_correct_trials: int = 0
    total_correct_valid_trials: int = 0
    total_incorrect_trials: int = 0
    total_valid_trials: int = 0
    total_invalid_trials: int = 0
    total_corrective_loop_trials: int = 0
    corrective_loop_count: int = 0

    # S1 counters.
    trial_no: int = 0
    n_pellets_retrieved: int = 0
    pellet_delivery_loop_count: int = 0

    # S4 discovery counters.
    total_discovery_responses: int = 0
    total_disc_rewards: int = 0

    # Performance.
    correct_side: str = "x"
    overall_perf: float = 0.0
    perf_left: float = 0.0
    perf_right: float = 0.0

    total_rewards: int = 0
    rewards_24h: int = 0
    last_action: str = "-"
    feeder_alert: bool = False

    sensors: dict[str, bool] = field(default_factory=dict)
    actuators: dict[str, bool] = field(default_factory=dict)

    # History for plots and logs.
    success_hist: list[float] = field(default_factory=list)
    lateral_hist: list[float] = field(default_factory=list)
    reversal_hist: list[float] = field(default_factory=list)
    reward_minutes: list[int] = field(default_factory=list)
    full_log: list[dict] = field(default_factory=list)  # FULL event CSV rows
    trial_log: list[dict] = field(default_factory=list)  # trial-summary rows

    def __post_init__(self) -> None:
        self._t0 = time.monotonic()
        self._left_hist: deque[str] = deque(maxlen=self.n_trials)
        self._right_hist: deque[str] = deque(maxlen=self.n_trials)
        self._plan: list[dict] | None = None
        self._step_idx = 0
        self._trial: TrialPlan | None = None
        for name in SENSOR_ORDER:
            self.sensors.setdefault(name, False)
        for name, _ in ACTUATOR_BITS:
            self.actuators.setdefault(name, False)
        if not self.reward_minutes:
            self.reward_minutes = sorted(random.sample(range(0, 720), 28))
        # Seed a little plausible history so plots/metrics are not empty.
        self._seed_history()

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _side_perf(hist: deque[str]) -> float:
        v = hist.count("valid")
        i = hist.count("incorrect")
        return v / (v + i) if (v + i) else 0.0

    def now_s(self) -> float:
        return time.monotonic() - self._t0

    @property
    def is_s1(self) -> bool:
        return self.stage == "1"

    @property
    def is_s4(self) -> bool:
        return self.stage == "4"

    def _seed_history(self) -> None:
        if self.is_s1:
            self.n_pellets_retrieved = random.randint(8, 40)
            self.trial_no = self.n_pellets_retrieved
            self.total_rewards = self.n_pellets_retrieved
            return
        base = 0.15 + 0.5 * random.random()
        for _ in range(18):
            base = min(0.95, max(0.05, base + random.uniform(-0.08, 0.12)))
            self.success_hist.append(round(base, 3))
            self.lateral_hist.append(round(0.5 + random.uniform(-0.18, 0.18), 3))
            self.reversal_hist.append(round(min(0.95, max(0.05, base + random.uniform(-0.2, 0.1))), 3))
        self.overall_perf = self.success_hist[-1]
        # Pre-fill the side response deques so live performance continues
        # smoothly from the seeded value instead of jumping on the first trial.
        for hist in (self._left_hist, self._right_hist):
            for _ in range(min(self.n_trials, 14)):
                hist.append("valid" if random.random() < self.overall_perf else "incorrect")
        self.perf_left = self._side_perf(self._left_hist)
        self.perf_right = self._side_perf(self._right_hist)
        self.total_trials = random.randint(60, 240)
        self.total_correct_valid_trials = int(self.total_trials * self.overall_perf)
        self.total_correct_trials = self.total_correct_valid_trials + random.randint(2, 10)
        self.total_incorrect_trials = self.total_trials - self.total_correct_trials
        self.total_valid_trials = self.total_correct_valid_trials + self.total_incorrect_trials
        self.total_invalid_trials = self.total_correct_trials - self.total_correct_valid_trials
        # Every incorrect trial enters the corrective path, so keep the
        # cumulative corrective counter consistent with the seeded incorrects.
        self.total_corrective_loop_trials = self.total_incorrect_trials
        self.total_rewards = self.total_correct_valid_trials

    def _full_row(self, level: str, message: str) -> dict:
        """One FULL event CSV row with all documented columns."""
        row = {
            "date": datetime.now().isoformat(timespec="seconds"),
            "timestamp_s": f"{self.now_s():.3f}",
            "stage": self.stage,
            "log_level": level,
            "message": message,
            "correct_side": self.correct_side,
            "total_trials": self.n_pellets_retrieved + 1 if self.is_s1 else self.total_trials,
            "trial_number": self.n_pellets_retrieved + 1 if self.is_s1 else self.total_trials + 1,
            "corrective_loop": 0 if self.is_s1 else self.corrective_loop_count,
            "left_performance": 0 if self.is_s1 else round(self.perf_left, 3),
            "right_performance": 0 if self.is_s1 else round(self.perf_right, 3),
        }
        for name in SENSOR_ORDER:
            row[f"state_{name}"] = int(self.sensors.get(name, False))
        self.full_log.append(row)
        self.full_log = self.full_log[-200:]
        return row

    # -- simulation ---------------------------------------------------------
    def _build_plan(self) -> list[dict]:
        """Create the ordered phase steps for the next trial."""
        if self.is_s1:
            self._trial = TrialPlan("x", None, False, False, False, False, False)
            return [
                {"phase": "PHASE_DELIVER_PELLET", "level": "INFO",
                 "msg": "S1 pellet delivered", "act": ("reward", True)},
                {"phase": "PHASE_WAIT_RETRIEVAL", "level": "INFO",
                 "msg": "S1 pellet retrieved (nose poke)", "sensor": "nose_poke",
                 "act": ("reward", False), "rec": "reward_collect"},
                {"phase": "PHASE_INTERTRIAL", "level": "DEBUG",
                 "msg": f"Inter-trial interval ({self.intertrial_s}s)"},
            ]

        correct_side = random.choice(["l", "r"])
        self.correct_side = correct_side
        # Better-performing subjects respond correctly more often.
        p_correct = 0.45 + 0.5 * self.overall_perf
        discovery = self.is_s4 and random.random() < 0.18
        if discovery:
            response = "discovery"
            correct = False
            roll_success = random.random() < 0.3
        else:
            correct = random.random() < p_correct
            response = correct_side if correct else ("r" if correct_side == "l" else "l")
            roll_success = False
        valid = True if not correct else random.random() < 0.85  # time-pressure pass
        corrective = (not correct) and not discovery
        self._trial = TrialPlan(correct_side, response, correct, valid, corrective,
                                discovery, roll_success)

        pat = f"{'left' if correct_side == 'l' else 'right'}_visual"
        steps: list[dict] = [
            {"phase": "PHASE_WAIT_TUNNEL", "level": "INFO", "msg": "Tunnel entry detected",
             "sensor": "left_tunnel" if correct_side == "l" else "right_tunnel"},
            {"phase": "PHASE_SHOW_STIMULUS", "level": "INFO",
             "msg": f"show_stim  correct_side={correct_side}", "act": (pat, True),
             "rec": "show_stim"},
            {"phase": "PHASE_WAIT_RESPONSE", "level": "INFO",
             "msg": f"Screen touched: {response}",
             "sensor": "left_response" if response == "l" else "right_response",
             "rec": "screen_touch", "act": (pat, False)},
        ]
        if discovery:
            steps.append({"phase": "PHASE_SCORE_RESPONSE", "level": "INFO",
                          "msg": "S4_Trial  response=discovery"})
            roll = "S4_roll_success" if roll_success else "S4_roll_failed"
            steps.append({"phase": "PHASE_DELIVER_REWARD" if roll_success else "PHASE_CORRECTIVE_LOOP",
                          "level": "INFO", "msg": f"S4_random_roll = {roll}",
                          "act": ("reward", roll_success) if roll_success else ("red_light", True),
                          "rec": "reward_collect" if roll_success else None,
                          "sensor": "nose_poke" if roll_success else None})
        elif correct:
            steps.append({"phase": "PHASE_SCORE_RESPONSE", "level": "INFO",
                          "msg": f"Response correct  (valid={valid})"})
            steps.append({"phase": "PHASE_DELIVER_REWARD", "level": "INFO",
                          "msg": "Reward delivered, pellet retrieved", "act": ("reward", True),
                          "sensor": "nose_poke", "rec": "reward_collect"})
        else:
            steps.append({"phase": "PHASE_SCORE_RESPONSE", "level": "WARNING",
                          "msg": "Response incorrect"})
            steps.append({"phase": "PHASE_CORRECTIVE_LOOP", "level": "WARNING",
                          "msg": "Aversive light, corrective loop", "act": ("red_light", True)})
        steps.append({"phase": "PHASE_INTERTRIAL", "level": "DEBUG",
                      "msg": f"Inter-trial interval ({self.intertrial_s}s)",
                      "act": ("red_light", False)})
        return steps

    def step(self) -> dict:
        """Advance one phase. Returns the FULL row emitted this step."""
        if self._plan is None or self._step_idx >= len(self._plan):
            self._plan = self._build_plan()
            self._step_idx = 0

        # Clear transient sensors from the previous step.
        for name in ("left_tunnel", "right_tunnel", "left_response", "right_response", "nose_poke"):
            self.sensors[name] = False

        step = self._plan[self._step_idx]
        self.current_phase = step["phase"]
        # corrective_loop is a *current* depth: 0 at trial start, 1 while in
        # the corrective path (RECORDING_OUTPUTS.md "Current corrective-loop count").
        if step["phase"] in ("PHASE_WAIT_TUNNEL", "PHASE_DELIVER_PELLET"):
            self.corrective_loop_count = 0
        elif step["phase"] == "PHASE_CORRECTIVE_LOOP":
            self.corrective_loop_count = 1
        if step.get("sensor"):
            self.sensors[step["sensor"]] = True
        if step.get("act"):
            name, value = step["act"]
            self.actuators[name] = value
        if step.get("rec") and self._trial is not None:
            setattr(self._trial, f"{step['rec']}_ts", round(self.now_s(), 3))

        row = self._full_row(step["level"], step["msg"])
        self._step_idx += 1
        if self._step_idx >= len(self._plan):
            self._finalize_trial()
        return row

    def _finalize_trial(self) -> None:
        plan = self._trial
        if plan is None:
            return
        if self.is_s1:
            self.n_pellets_retrieved += 1
            self.trial_no += 1
            self.pellet_delivery_loop_count += random.random() < 0.05
            self.total_rewards += 1
            self.rewards_24h += 1
            self.last_action = "pellet retrieved"
            self._register_reward_minute()
            return

        self.total_trials += 1
        side_hist = self._left_hist if plan.correct_side == "l" else self._right_hist
        if plan.discovery:
            self.total_discovery_responses += 1
            self.last_action = "discovery"
            if plan.roll_success:
                self.total_disc_rewards += 1
                self.total_rewards += 1
                self.rewards_24h += 1
                self._register_reward_minute()
        elif plan.correct:
            self.total_correct_trials += 1
            self.last_action = "correct"
            if plan.valid:
                self.total_correct_valid_trials += 1
                self.total_valid_trials += 1
                self.total_rewards += 1
                self.rewards_24h += 1
                self._register_reward_minute()
                side_hist.append("valid")
            else:
                self.total_invalid_trials += 1
                if self.stage == "2":  # S2 compatibility hack: late-correct -> incorrect
                    side_hist.append("incorrect")
        else:
            self.total_incorrect_trials += 1
            self.total_valid_trials += 1
            self.last_action = "incorrect"
            side_hist.append("incorrect")
        if plan.corrective:
            self.total_corrective_loop_trials += 1

        # Append the trial-summary CSV row (LightExperimentCSVLogger schema).
        reward = 1 if plan.correct_side == plan.response else 0
        self.trial_log.append({
            "date": datetime.now().isoformat(timespec="seconds"),
            "show_stim_ts": plan.show_stim_ts or "NA",
            "screen_touch_ts": plan.screen_touch_ts or "NA",
            "reward_collect_ts": plan.reward_collect_ts or "NA",
            "total_trials": self.total_trials,
            "correct_side": plan.correct_side,
            "response": plan.response if plan.response in ("l", "r") else "None",
            "reward": reward,
            "correct": self.total_correct_valid_trials,
            "incorrect": self.total_incorrect_trials,
            "valid": self.total_valid_trials,
            "corrective_loop": self.corrective_loop_count,
            "overall_perf": round(self.overall_perf, 3),
            "perf_left": round(self.perf_left, 3),
            "perf_right": round(self.perf_right, 3),
        })
        self.trial_log = self.trial_log[-200:]
        self._recompute_performance()

    def _register_reward_minute(self) -> None:
        minute = (self.reward_minutes[-1] + random.randint(4, 22)) % 720 if self.reward_minutes else 0
        self.reward_minutes.append(minute)
        self.reward_minutes = self.reward_minutes[-50:]

    def _recompute_performance(self) -> None:
        self.perf_left = self._side_perf(self._left_hist)
        self.perf_right = self._side_perf(self._right_hist)
        both = list(self._left_hist) + list(self._right_hist)
        v = both.count("valid")
        i = both.count("incorrect")
        self.overall_perf = v / (v + i) if (v + i) else self.overall_perf

        self.success_hist.append(round(self.overall_perf, 3))
        self.lateral_hist.append(round(0.5 + (self.perf_left - self.perf_right) / 2, 3))
        self.reversal_hist.append(round(self.overall_perf, 3))
        for h in (self.success_hist, self.lateral_hist, self.reversal_hist):
            del h[:-60]

    def stage_complete(self) -> bool:
        if self.is_s1:
            return self.n_pellets_retrieved >= self.n_trials
        if self.stage == "2":
            return self.total_trials >= self.n_trials and self.overall_perf >= self.learning_threshold
        if self.stage in ("3", "3.1"):
            return (len(self._left_hist) >= self.n_trials and len(self._right_hist) >= self.n_trials
                    and self.perf_left >= self.learning_threshold
                    and self.perf_right >= self.learning_threshold)
        return False  # S4 has no final completion rule yet


# --- plots -----------------------------------------------------------------
class PlotCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        super().__init__(master, bg="#f9fbff", highlightthickness=1,
                         highlightbackground="#c7ccd3", **kwargs)

    def draw_line_plot(self, values: list[float], threshold: float | None = None,
                       ylabel: str = "") -> None:
        self.delete("all")
        width = max(self.winfo_width(), 260)
        height = max(self.winfo_height(), 190)
        pad_l, pad_r, pad_t, pad_b = 42, 18, 16, 26
        plot_w = width - pad_l - pad_r
        plot_h = height - pad_t - pad_b

        for i in range(6):
            y = pad_t + i * plot_h / 5
            self.create_line(pad_l, y, width - pad_r, y, fill="#e8edf4")
            self.create_text(20, y, text=f"{1 - i / 5:.1f}", fill="#687782", font=("Segoe UI", 9))
        if threshold is not None:
            y = pad_t + (1 - threshold) * plot_h
            self.create_line(pad_l, y, width - pad_r, y, fill="#d23", width=1, dash=(4, 3))
            self.create_text(width - pad_r - 4, y - 8, text=f"thr {threshold:.2f}",
                             fill="#d23", anchor="e", font=("Segoe UI", 8))
        if ylabel:
            self.create_text(width - pad_r, pad_t, text=ylabel, fill="#5d6b75",
                             anchor="ne", font=("Segoe UI", 9, "bold"))
        if len(values) > 1:
            points: list[float] = []
            for idx, value in enumerate(values):
                x = pad_l + idx * plot_w / (len(values) - 1)
                points.extend([x, pad_t + (1 - value) * plot_h])
            self.create_line(points, fill="#414fa3", width=3, smooth=True)
        elif not values:
            self.create_text(width / 2, height / 2, text="no data yet",
                             fill="#9aa6b1", font=("Segoe UI", 10))
        self.create_text(width / 2, height - 8, text="trial -->", fill="#5d6b75",
                         font=("Segoe UI", 8))

    def draw_reward_raster(self, reward_minutes: list[int]) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 300)
        height = max(self.winfo_height(), 84)
        pad_l, pad_r, pad_t, pad_b = 24, 16, 16, 22
        self.create_rectangle(pad_l, pad_t, width - pad_r, height - pad_b,
                              fill="#ffffff", outline="#cbd5df")
        for minute in reward_minutes[-50:]:
            x = pad_l + (minute / 720) * (width - pad_l - pad_r)
            self.create_oval(x - 2, height / 2 - 2, x + 2, height / 2 + 2, fill="#18a34a", outline="")
        for frac, label in [(2 / 12, "2h"), (4 / 12, "4h"), (6 / 12, "6h"),
                            (8 / 12, "8h"), (10 / 12, "10h")]:
            x = pad_l + frac * (width - pad_l - pad_r)
            self.create_text(x, height - 8, text=label, fill="#6b7280", font=("Segoe UI", 8))
        self.create_text(width / 2, 8, text="Latest 50 rewards (12h window)",
                         fill="#1f2937", font=("Segoe UI", 9, "bold"))


# --- monitoring window -----------------------------------------------------
class MonitoringWindow(tk.Toplevel):
    GENERAL_FIELDS = [
        ("Subject:", "subject"),
        ("Stage / phase:", "stagephase"),
        ("Overall time:", "overall"),
        ("Total trials:", "total"),
        ("Correct / incorrect:", "ci"),
        ("Valid / invalid:", "vi"),
        ("Corrective loops:", "corr"),
        ("Overall perf:", "perf"),
        ("Perf left / right:", "lr"),
        ("Pellets retrieved:", "pellets"),
        ("Discovery rewards:", "disc"),
        ("Total rewards:", "rewards"),
        ("Stage complete:", "complete"),
    ]

    def __init__(self, app: "BeatboxDemo") -> None:
        super().__init__(app.root)
        self.app = app
        self.title("Monitoring window")
        self.geometry("1180x760")
        self.minsize(900, 650)
        self.configure(bg="#edf3ea")
        self.plots: dict[tuple[int, str], PlotCanvas] = {}
        self.log_texts: dict[int, tk.Text] = {}
        self.reward_plots: dict[int, PlotCanvas] = {}
        self.metric_vars: dict[tuple[int, str], tk.StringVar] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("Monitor.TNotebook", background="#e7eee4")
        style.configure("Monitor.TFrame", background="#e7eee4")
        style.configure("Monitor.TLabel", background="#e7eee4", foreground="#263238")
        style.configure("Monitor.TLabelframe", background="#e7eee4")
        style.configure("Monitor.TLabelframe.Label", background="#e7eee4",
                        foreground="#263238", font=("Segoe UI", 10, "bold"))

        toolbar = ttk.Frame(self, style="Monitor.TFrame")
        toolbar.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(toolbar, text="Refresh", command=self.refresh).pack(side="right")

        box_tabs = ttk.Notebook(self, style="Monitor.TNotebook")
        box_tabs.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        for state in self.app.boxes:
            box_frame = ttk.Frame(box_tabs, style="Monitor.TFrame")
            box_tabs.add(box_frame, text=f"Box {state.box_id}")
            box_frame.columnconfigure(0, weight=1)
            box_frame.rowconfigure(0, weight=3)
            box_frame.rowconfigure(1, weight=2)

            general = ttk.LabelFrame(box_frame, text="General",
                                     style="Monitor.TLabelframe")
            general.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 3))
            self._build_general(general, state)

            graphs = ttk.Frame(box_frame, style="Monitor.TFrame")
            graphs.grid(row=1, column=0, sticky="nsew", padx=6, pady=(3, 6))
            graphs.rowconfigure(0, weight=1)
            for column in range(3):
                graphs.columnconfigure(column, weight=1, uniform="monitor-graphs")

            for column, (label, key, thr, ylab) in enumerate([
                ("Success rate", "success", True, "valid / (valid+incorrect)"),
                ("Lateralization", "lateral", False, "left-right bias"),
                ("Reversal task", "reversal", True, "perf after reversal"),
            ]):
                card = ttk.LabelFrame(graphs, text=label, style="Monitor.TLabelframe")
                card.grid(row=0, column=column, sticky="nsew",
                          padx=(0 if column == 0 else 3, 0 if column == 2 else 3))
                self._build_plot_tab(card, state, key, thr, ylab)

    def _build_general(self, parent: ttk.Frame, state: BoxState) -> None:
        grid = ttk.Frame(parent, style="Monitor.TFrame")
        grid.pack(fill="both", expand=True, padx=8, pady=8)
        grid.columnconfigure(2, weight=1)
        grid.rowconfigure(0, weight=1)

        metrics = ttk.Frame(grid, style="Monitor.TFrame")
        metrics.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        for row, (label, key) in enumerate(self.GENERAL_FIELDS):
            ttk.Label(metrics, text=label, style="Monitor.TLabel").grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self.metric_vars[(state.box_id, key)] = var
            tk.Label(metrics, textvariable=var, width=18, anchor="e", bg="#f7faf5",
                     relief="sunken").grid(row=row, column=1, sticky="ew", pady=2)
        reward_plot = PlotCanvas(metrics, height=92, width=300)
        reward_plot.grid(row=len(self.GENERAL_FIELDS), column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.reward_plots[state.box_id] = reward_plot

        log_frame = ttk.Frame(grid, style="Monitor.TFrame")
        log_frame.grid(row=0, column=2, sticky="nsew")
        ttk.Label(log_frame, text="FULL event log  (timestamp_s | stage | level | message):",
                  style="Monitor.TLabel").pack(anchor="w")
        text = tk.Text(log_frame, height=20, width=60, font=("Consolas", 8),
                       bg="#f7faf5", fg="#263238")
        text.pack(fill="both", expand=True)
        self.log_texts[state.box_id] = text

    def _build_plot_tab(self, parent: ttk.Frame, state: BoxState, key: str,
                        threshold: bool, ylabel: str) -> None:
        controls = ttk.Frame(parent, style="Monitor.TFrame")
        controls.pack(fill="x", padx=8, pady=8)
        ttk.Label(controls, text="Number of trials to display:", style="Monitor.TLabel").pack(side="left")
        spin = ttk.Spinbox(controls, from_=10, to=5000, increment=10, width=9)
        spin.set(str(state.n_trials))
        spin.pack(side="left", padx=8)
        plot = PlotCanvas(parent, height=210, width=280)
        plot.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.plots[(state.box_id, key)] = (plot, threshold, ylabel)

    def refresh(self) -> None:
        for state in self.app.boxes:
            values = {
                "subject": f"{state.subject_id}",
                "stagephase": f"S{state.stage} / {state.current_phase.replace('PHASE_', '')}",
                "overall": self.app.elapsed_str(),
                "total": str(state.total_trials),
                "ci": f"{state.total_correct_trials} / {state.total_incorrect_trials}",
                "vi": f"{state.total_valid_trials} / {state.total_invalid_trials}",
                "corr": str(state.total_corrective_loop_trials),
                "perf": f"{state.overall_perf:.2f}",
                "lr": f"{state.perf_left:.2f} / {state.perf_right:.2f}",
                "pellets": str(state.n_pellets_retrieved),
                "disc": str(state.total_disc_rewards),
                "rewards": str(state.total_rewards),
                "complete": "yes" if state.stage_complete() else "no",
            }
            for key, value in values.items():
                self.metric_vars[(state.box_id, key)].set(value)
            self.reward_plots[state.box_id].draw_reward_raster(state.reward_minutes)

            text = self.log_texts[state.box_id]
            text.configure(state="normal")
            text.delete("1.0", "end")
            lines = [
                f"{r['timestamp_s']:>9} | {r['stage']:>3} | {r['log_level']:<7} | {r['message']}"
                for r in state.full_log[-60:]
            ]
            text.insert("end", "\n".join(lines))
            text.configure(state="disabled")
            text.see("end")

            for key, hist in [("success", state.success_hist),
                              ("lateral", state.lateral_hist),
                              ("reversal", state.reversal_hist)]:
                plot, want_thr, ylabel = self.plots[(state.box_id, key)]
                thr = state.learning_threshold if want_thr else None
                plot.draw_line_plot(hist[-state.n_trials:], thr, ylabel)


# --- main window -----------------------------------------------------------
class BeatboxDemo:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Open-BEATBox GUI front-end demo")
        self.root.geometry("1120x780")
        self.root.minsize(980, 700)
        self.root.configure(bg=BG)

        self.conn_state = ST_DISCONNECTED
        self.running = tk.BooleanVar(value=False)
        self.selected_box = tk.IntVar(value=1)
        self.monitor: MonitoringWindow | None = None
        self.activity_log: list[str] = []
        self.seq = 0
        self._exp_t0: float | None = None

        self.boxes = [
            BoxState(1, "Pikachu", 23, "4", n_trials=50, learning_threshold=0.75),
            BoxState(2, "Eevee", 21, "2", n_trials=40, learning_threshold=0.70),
            BoxState(3, "Mew", 24, "3", n_trials=60, learning_threshold=0.80),
            BoxState(4, "Bulbi", 20, "1", n_trials=30),
        ]
        self.sensor_labels: dict[str, tk.Label] = {}
        self.actuator_labels: dict[str, tk.Label] = {}
        self.actuator_buttons: list[tk.Widget] = []

        box0 = self.current_box
        self.port_var = tk.StringVar(value="/dev/ttyACM0")
        self.state_var = tk.StringVar(value=self.conn_state)
        self.stage_var = tk.StringVar(value=box0.stage)
        self.trials_var = tk.StringVar(value=str(box0.n_trials))
        self.interval_var = tk.StringVar(value=str(box0.intertrial_s))
        self.thr_var = tk.StringVar(value=str(box0.learning_threshold))
        self.tp_var = tk.StringVar(value=str(box0.time_pressure_ms))
        self.folder_var = tk.StringVar(value="(no folder selected)")
        self.current_trial_var = tk.StringVar(value="0")
        self.phase_var = tk.StringVar(value=box0.current_phase)
        self.mask_var = tk.StringVar(value="0x0000  ->  [0x00, 0x00]")
        self.tx_var = tk.StringVar(value="-")
        self.rx_var = tk.StringVar(value="-")
        self.resp_var = tk.StringVar(value="-")
        self.full_csv_var = tk.StringVar(value="-")
        self.trial_csv_var = tk.StringVar(value="-")
        self.rows_var = tk.StringVar(value="0 full / 0 trial rows")

        self._setup_styles()
        self._build()
        self._update_connection_dependent_state()
        self._refresh_ui()
        self._tick()

    # -- properties ---------------------------------------------------------
    @property
    def current_box(self) -> BoxState:
        return self.boxes[self.selected_box.get() - 1]

    @property
    def connected(self) -> bool:
        return self.conn_state == ST_CONNECTED

    def elapsed_str(self) -> str:
        if self._exp_t0 is None:
            return "00:00:00"
        secs = int(time.monotonic() - self._exp_t0)
        return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"

    def next_seq(self) -> int:
        self.seq = (self.seq + 1) % 256
        return self.seq

    # -- styling / layout ---------------------------------------------------
    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Dark.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL, relief="flat")
        style.configure("Dark.TLabel", background=BG, foreground=TEXT)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Header.TLabel", background=PANEL_2, foreground=TEXT, font=("Segoe UI", 9, "bold"))
        style.configure("Dark.TButton", background=PANEL_2, foreground=TEXT, bordercolor=BORDER)
        style.map("Dark.TButton", background=[("active", "#28384a")])
        style.configure("Accent.TButton", background="#294b65", foreground=TEXT)
        style.configure("Red.TButton", background="#643030", foreground=TEXT)
        style.configure("Dark.TEntry", fieldbackground="#223244", foreground=TEXT, insertcolor=TEXT)
        style.configure("Dark.TCombobox", fieldbackground="#223244", background=PANEL_2, foreground=TEXT)
        style.configure("Dark.TCheckbutton", background=PANEL, foreground=TEXT)

    def _panel(self, parent: tk.Misc, title: str, *, expand: bool = False) -> ttk.Frame:
        outer = ttk.Frame(parent, style="Panel.TFrame")
        outer.pack(fill="both" if expand else "x", expand=expand, pady=3)
        ttk.Label(outer, text=title, style="Header.TLabel").pack(fill="x", padx=1, pady=(1, 3))
        return outer

    def _build(self) -> None:
        # A two-column dashboard keeps every panel visible without scrolling.
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=5)
        body.columnconfigure(0, weight=1, uniform="main-columns")
        body.columnconfigure(1, weight=1, uniform="main-columns")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self._build_serial(left)
        self._build_diagnostics(left)
        self._build_command(left)
        self._build_experiment_setup(left)
        self._build_recording(left)
        self._build_data(left)

        self._build_sensors(right)
        self._build_actuators(right)
        self._build_info(right)
        self._build_logs(right)

    def _build_serial(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Serial connection  (USB, 115200 baud)")
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(row, text="Port:", style="Panel.TLabel").pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.port_var,
                             values=["/dev/ttyACM0", "COM3", "COM4"], width=15, style="Dark.TCombobox")
        combo.pack(side="left", padx=5)
        ttk.Button(row, text="Refresh", command=self._scan_ports, style="Dark.TButton").pack(side="left", padx=3)
        self.connect_btn = ttk.Button(row, text="Connect", command=self._toggle_connect, style="Accent.TButton")
        self.connect_btn.pack(side="left", padx=3)
        ttk.Button(row, text="Monitoring window", command=self._open_monitor,
                   style="Dark.TButton").pack(side="right")

        state_row = ttk.Frame(panel, style="Panel.TFrame")
        state_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(state_row, text="State:", style="Panel.TLabel").pack(side="left")
        self.state_dot = tk.Label(state_row, text="●", bg=PANEL, fg=MUTED, font=("Segoe UI", 13))
        self.state_dot.pack(side="left", padx=(6, 4))
        tk.Label(state_row, textvariable=self.state_var, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side="left")

    def _build_diagnostics(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Diagnostics")
        grid = ttk.Frame(panel, style="Panel.TFrame")
        grid.pack(fill="x", padx=8, pady=(0, 8))
        grid.columnconfigure(1, weight=1)
        rows = [
            ("Active stage / phase:", self.phase_var),
            ("Output mask:", self.mask_var),
            ("Last TX frame:", self.tx_var),
            ("Last RX frame:", self.rx_var),
            ("Last device response:", self.resp_var),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(row=i, column=0, sticky="w", pady=1)
            tk.Label(grid, textvariable=var, bg="#0b141d", fg="#d7e5ee", anchor="w",
                     font=("Consolas", 8)).grid(row=i, column=1, sticky="ew", padx=(8, 0), pady=1)

    def _build_experiment_setup(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Experiment setup")
        row1 = ttk.Frame(panel, style="Panel.TFrame")
        row1.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(row1, text="Select folder", command=self._select_folder, style="Dark.TButton").pack(side="left")
        ttk.Label(row1, text="Stage", style="Panel.TLabel").pack(side="left", padx=(12, 4))
        ttk.Combobox(row1, textvariable=self.stage_var, values=STAGES, width=5,
                     style="Dark.TCombobox").pack(side="left")
        ttk.Label(row1, text="Trials (n_trials)", style="Panel.TLabel").pack(side="left", padx=(12, 4))
        ttk.Entry(row1, textvariable=self.trials_var, width=6, style="Dark.TEntry").pack(side="left")

        row2 = ttk.Frame(panel, style="Panel.TFrame")
        row2.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(row2, text="Inter-trial (s)", style="Panel.TLabel").pack(side="left", padx=(0, 4))
        ttk.Spinbox(row2, from_=1, to=60, textvariable=self.interval_var, width=4, style="Dark.TEntry").pack(side="left")
        ttk.Label(row2, text="Learning thr.", style="Panel.TLabel").pack(side="left", padx=(12, 4))
        ttk.Entry(row2, textvariable=self.thr_var, width=5, style="Dark.TEntry").pack(side="left")
        ttk.Label(row2, text="Time pressure (ms)", style="Panel.TLabel").pack(side="left", padx=(12, 4))
        ttk.Entry(row2, textvariable=self.tp_var, width=6, style="Dark.TEntry").pack(side="left")
        self.start_btn = ttk.Button(row2, text="Start", command=self._toggle_experiment, style="Accent.TButton")
        self.start_btn.pack(side="left", padx=(12, 3))
        ttk.Button(row2, text="Stop", command=self._stop_experiment, style="Red.TButton").pack(side="left")

    def _build_recording(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Recording  (CSV artifacts)")
        grid = ttk.Frame(panel, style="Panel.TFrame")
        grid.pack(fill="x", padx=8, pady=(0, 8))
        grid.columnconfigure(1, weight=1)
        for i, (label, var) in enumerate([
            ("Folder:", self.folder_var),
            ("FULL event CSV:", self.full_csv_var),
            ("Trial summary CSV:", self.trial_csv_var),
            ("Rows written:", self.rows_var),
        ]):
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(row=i, column=0, sticky="w", pady=1)
            tk.Label(grid, textvariable=var, bg=PANEL, fg=TEXT, anchor="w",
                     font=("Consolas", 8)).grid(row=i, column=1, sticky="ew", padx=(8, 0), pady=1)

    def _build_data(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Experiment data")
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(row, text="Box:", style="Panel.TLabel").pack(side="left")
        ttk.Combobox(row, textvariable=self.selected_box, values=[1, 2, 3, 4], width=3,
                     style="Dark.TCombobox", state="readonly").pack(side="left", padx=(4, 16))
        ttk.Label(row, text="Current trial:", style="Panel.TLabel").pack(side="left")
        ttk.Label(row, textvariable=self.current_trial_var, style="Muted.TLabel").pack(side="left", padx=8)
        self.selected_box.trace_add("write", lambda *_: self._on_box_change())

        perf_row = ttk.Frame(panel, style="Panel.TFrame")
        perf_row.pack(fill="x", padx=8, pady=(0, 8))
        self.perf_var = tk.StringVar()
        ttk.Label(perf_row, textvariable=self.perf_var, style="Muted.TLabel").pack(side="left")

    def _build_sensors(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Sensors  (device 'state' inputs)")
        grid = ttk.Frame(panel, style="Panel.TFrame")
        grid.pack(fill="x", padx=8, pady=(0, 5))
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="sensors")
        for index, name in enumerate(SENSOR_ORDER):
            cell = ttk.Frame(grid, style="Panel.TFrame")
            cell.grid(row=index // 3, column=index % 3, sticky="ew", padx=4, pady=1)
            ttk.Label(cell, text=SENSOR_LABELS[name], style="Panel.TLabel").pack(side="left")
            led = tk.Label(cell, text="●", bg=PANEL, fg=RED, font=("Segoe UI", 13, "bold"))
            led.pack(side="right")
            self.sensor_labels[name] = led

    def _build_actuators(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Actuators  ('set' output mask, bits 0-10)")
        top = ttk.Frame(panel, style="Panel.TFrame")
        top.pack(fill="x", padx=8, pady=3)
        reward_btn = ttk.Button(top, text="Reward (manual)",
                                command=lambda: self._manual_actuator("reward"), style="Dark.TButton")
        reward_btn.pack(fill="x")
        self.actuator_buttons.append(reward_btn)
        self.actuator_labels["reward"] = tk.Label(top, text="●", bg=PANEL, fg=RED, font=("Segoe UI", 16, "bold"))
        self.actuator_labels["reward"].place(relx=0.98, rely=0.5, anchor="e")

        checks = ttk.Frame(panel, style="Panel.TFrame")
        checks.pack(fill="x", padx=8, pady=(0, 5))
        for name in ["light", "red_light"]:
            cb = ttk.Checkbutton(checks, text=ACTUATOR_LABELS[name], style="Dark.TCheckbutton",
                                 command=lambda n=name: self._manual_actuator(n))
            cb.pack(side="left", padx=(0, 16))
            self.actuator_buttons.append(cb)

        displays = ttk.Frame(panel, style="Panel.TFrame")
        displays.pack(fill="x", padx=8, pady=(3, 8))
        for col, (side, prefix) in enumerate([("Left display", "left"), ("Right display", "right")]):
            frame = ttk.Frame(displays, style="Panel.TFrame")
            frame.grid(row=0, column=col, sticky="nsew", padx=(0, 12) if col == 0 else (12, 0))
            displays.columnconfigure(col, weight=1)
            ttk.Label(frame, text=side, style="Panel.TLabel").pack(anchor="w")
            for pat in ["visual", "lines", "white", "black"]:
                name = f"{prefix}_{pat}"
                row = ttk.Frame(frame, style="Panel.TFrame")
                row.pack(fill="x", pady=1)
                btn = ttk.Button(row, text=ACTUATOR_LABELS[name],
                                 command=lambda n=name: self._manual_actuator(n), style="Dark.TButton")
                btn.pack(side="left", fill="x", expand=True)
                self.actuator_buttons.append(btn)
                led = tk.Label(row, text="●", bg=PANEL, fg=RED, font=("Segoe UI", 13, "bold"))
                led.pack(side="right")
                self.actuator_labels[name] = led

    def _build_command(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Serial send command")
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(row, text="cmd:", style="Panel.TLabel").pack(side="left")
        self.cmd_combo = ttk.Combobox(row, values=["set (current mask)", "ready (handshake)"],
                                      width=20, style="Dark.TCombobox", state="readonly")
        self.cmd_combo.set("set (current mask)")
        self.cmd_combo.pack(side="left", padx=5)
        self.send_btn = ttk.Button(row, text="Send frame", command=self._send_command, style="Dark.TButton")
        self.send_btn.pack(side="left", padx=(5, 0))

    def _build_info(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Animal information")
        wrap = ttk.Frame(panel, style="Panel.TFrame")
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        wrap.columnconfigure(0, weight=1)
        wrap.columnconfigure(1, weight=1)
        self.animal_text = tk.Text(wrap, height=4, bg=PANEL, fg=TEXT, relief="flat", font=("Consolas", 9))
        self.animal_text.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.stage_text = tk.Text(wrap, height=4, bg=PANEL, fg=TEXT, relief="flat", font=("Consolas", 9))
        self.stage_text.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

    def _build_logs(self, parent: tk.Misc) -> None:
        panel = self._panel(parent, "Logs  (GUI panel: asctime - level - message)", expand=True)
        self.log_box = tk.Text(panel, height=6, bg="#0b141d", fg="#d7e5ee",
                               insertbackground=TEXT, font=("Consolas", 8))
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # -- connection flow ----------------------------------------------------
    def _set_state(self, state: str) -> None:
        self.conn_state = state
        self.state_var.set(state)
        self.state_dot.configure(fg=STATE_COLORS.get(state, MUTED))
        self._update_connection_dependent_state()

    def _toggle_connect(self) -> None:
        if self.conn_state in (ST_DISCONNECTED, ST_FAULT, ST_STOPPED):
            self._begin_handshake()
        else:
            self.running.set(False)
            self._set_state(ST_DISCONNECTED)
            self.connect_btn.configure(text="Connect")
            self._add_log("INFO", f"Closed port {self.port_var.get()}")

    def _begin_handshake(self) -> None:
        self.connect_btn.configure(text="Disconnect")
        self._set_state(ST_PORT_OPEN)
        self._add_log("INFO", f"Opened {self.port_var.get()} at {BAUD} baud")
        self.root.after(450, self._handshake_step1)

    def _handshake_step1(self) -> None:
        self._set_state(ST_HANDSHAKING)
        self._tx("ready", b"")
        self._add_log("INFO", "TX ready -- starting handshake")
        self.root.after(400, self._handshake_step2)

    def _handshake_step2(self) -> None:
        self._rx("starting", b"")
        self._rx("inited", b"")
        self._add_log("INFO", "RX starting / inited")
        self.root.after(400, self._handshake_step3)

    def _handshake_step3(self) -> None:
        self._rx("ready", b"")
        self.resp_var.set("ready (handshake complete)")
        self._set_state(ST_CONNECTED)
        self._add_log("INFO", "RX ready -- handshake complete, reader thread started")

    def _update_connection_dependent_state(self) -> None:
        ok = self.conn_state == ST_CONNECTED
        gate = "normal" if ok else "disabled"
        for widget in (*self.actuator_buttons, getattr(self, "start_btn", None),
                       getattr(self, "send_btn", None)):
            if widget is not None:
                try:
                    widget.configure(state=gate)
                except tk.TclError:
                    pass

    # -- protocol mock ------------------------------------------------------
    def _tx(self, command: str, payload: bytes) -> str:
        frame = build_frame(command, payload, self.next_seq())
        self.tx_var.set(frame)
        return frame

    def _rx(self, command: str, payload: bytes) -> str:
        frame = build_frame(command, payload, self.seq)
        self.rx_var.set(frame)
        return frame

    def _push_output_mask(self) -> None:
        mask = actuators_to_mask(self.current_box.actuators)
        high, low = (mask >> 8) & 0xFF, mask & 0xFF
        self.mask_var.set(f"0x{mask:04X}  ->  [0x{high:02X}, 0x{low:02X}]")
        if self.connected:
            self._tx("set", bytes([high, low]))
            self.resp_var.set("ack")

    def _send_command(self) -> None:
        if not self.connected:
            return
        if self.cmd_combo.get().startswith("set"):
            self._push_output_mask()
            self._add_log("INFO", f"Sent set frame: {self.tx_var.get()}")
        else:
            self._tx("ready", b"")
            self._add_log("INFO", f"Sent ready frame: {self.tx_var.get()}")

    # -- experiment control -------------------------------------------------
    def _select_folder(self) -> None:
        box = self.current_box
        self.folder_var.set("C:/beatbox_data/2026-06-30_session")
        self._update_recording_filenames(box)
        self._add_log("INFO", "Selected experiment folder")

    def _update_recording_filenames(self, box: BoxState) -> None:
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.full_csv_var.set(f"FULL_{box.subject_id}_S{box.stage}_{ts}.csv")
        self.trial_csv_var.set(
            "(S1: full log only)" if box.is_s1 else f"{box.subject_id}_S{box.stage}_{ts}.csv")

    def _toggle_experiment(self) -> None:
        if not self.connected:
            self._add_log("WARNING", "Cannot start: device not connected (handshake required)")
            return
        if self.running.get():
            self.running.set(False)
            self._set_state(ST_CONNECTED)
            self._add_log("INFO", "Experiment paused")
            return
        box = self.current_box
        box.stage = self.stage_var.get()
        box.n_trials = int(self.trials_var.get() or box.n_trials)
        box.intertrial_s = int(self.interval_var.get() or box.intertrial_s)
        box.learning_threshold = float(self.thr_var.get() or box.learning_threshold)
        box.time_pressure_ms = int(self.tp_var.get() or box.time_pressure_ms)
        self.running.set(True)
        self._exp_t0 = self._exp_t0 or time.monotonic()
        self._update_recording_filenames(box)
        self._add_log("INFO", f"Experiment started -- stage S{box.stage}, n_trials={box.n_trials}, "
                              f"thr={box.learning_threshold}, time_pressure={box.time_pressure_ms}ms")

    def _stop_experiment(self) -> None:
        self.running.set(False)
        if self.connected:
            self._set_state(ST_STOPPED)
        self._add_log("INFO", "Experiment stopped -- loggers closed")

    def _manual_actuator(self, name: str) -> None:
        if not self.connected:
            return
        box = self.current_box
        if name == "reward" and not box.actuators.get(name):
            if not messagebox.askyesno("Confirm manual reward",
                                       "Deliver a manual reward pulse to this box?"):
                return
        box.actuators[name] = not box.actuators.get(name, False)
        if name == "reward" and box.actuators[name]:
            box.total_rewards += 1
            box.rewards_24h += 1
            self._add_log("INFO", "Manual reward delivered")
        else:
            self._add_log("INFO", f"{ACTUATOR_LABELS[name]} ({name}) -> {'ON' if box.actuators[name] else 'OFF'}")
        self._push_output_mask()
        self._refresh_ui()

    def _scan_ports(self) -> None:
        self._add_log("INFO", "Serial ports scanned: /dev/ttyACM0, COM3, COM4")

    def _open_monitor(self) -> None:
        if self.monitor and self.monitor.winfo_exists():
            self.monitor.lift()
            self.monitor.refresh()
            return
        self.monitor = MonitoringWindow(self)

    def _on_box_change(self) -> None:
        box = self.current_box
        self.stage_var.set(box.stage)
        self.trials_var.set(str(box.n_trials))
        self.interval_var.set(str(box.intertrial_s))
        self.thr_var.set(str(box.learning_threshold))
        self.tp_var.set(str(box.time_pressure_ms))
        self._update_recording_filenames(box)
        self._refresh_ui()

    # -- logging / refresh --------------------------------------------------
    def _add_log(self, level: str, message: str) -> None:
        entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {level} - {message}"
        self.activity_log.append(entry)
        self.activity_log = self.activity_log[-200:]
        if hasattr(self, "log_box"):
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("end", "\n".join(self.activity_log[-60:]))
            self.log_box.configure(state="disabled")
            self.log_box.see("end")

    def _refresh_ui(self) -> None:
        box = self.current_box
        self.phase_var.set(f"S{box.stage} / {box.current_phase}")
        for name, led in self.sensor_labels.items():
            led.configure(fg=GREEN if box.sensors.get(name) else RED)
        for name, led in self.actuator_labels.items():
            led.configure(fg=GREEN if box.actuators.get(name, False) else RED)
        self.perf_var.set(
            f"trials {box.total_trials}  |  correct {box.total_correct_valid_trials}"
            f"  |  perf {box.overall_perf:.2f}  (L {box.perf_left:.2f} / R {box.perf_right:.2f})")
        self.rows_var.set(f"{len(box.full_log)} full / {len(box.trial_log)} trial rows")

        self.animal_text.configure(state="normal")
        self.animal_text.delete("1.0", "end")
        self.animal_text.insert("end",
            f"Subject ID:   {box.subject_id}\n"
            f"Box:          {box.box_id}\n"
            f"Weight (g):   {box.weight_g}\n"
            f"Sex:          {box.sex}\n"
            f"Light cycle:  {box.light_cycle}")
        self.animal_text.configure(state="disabled")

        self.stage_text.configure(state="normal")
        self.stage_text.delete("1.0", "end")
        self.stage_text.insert("end",
            f"Stage / phase info\n"
            f"Stage:        S{box.stage}\n"
            f"Phase:        {box.current_phase}\n"
            f"Correct side: {box.correct_side}\n"
            f"Corr. loops:  {box.corrective_loop_count}\n"
            f"Complete:     {'yes' if box.stage_complete() else 'no'}")
        self.stage_text.configure(state="disabled")

    def _tick(self) -> None:
        if self.running.get() and self.connected:
            for box in self.boxes:
                row = box.step()
                # The device reports its sensor 'state' byte after the step.
                if box is self.current_box:
                    self._rx("state", bytes([sensors_to_byte(box.sensors)]))
                    self._push_output_mask()
                    self.current_trial_var.set(str(box.total_trials if not box.is_s1 else box.trial_no))
                    self._add_log(row["log_level"], f"Box{box.box_id} S{box.stage} {row['message']}")
                if box.stage_complete():
                    pass  # would advance stage in the real app
            self.rows_var.set(
                f"{len(self.current_box.full_log)} full / {len(self.current_box.trial_log)} trial rows")
        if self.monitor and self.monitor.winfo_exists():
            self.monitor.refresh()
        self._refresh_ui()
        self.root.after(1200, self._tick)

    def run(self) -> None:
        self._add_log("INFO", "Open-BEATBox GUI demo loaded with mock data")
        self._add_log("INFO", "Connect the (mock) device to enable experiment + actuator controls")
        self.root.mainloop()


def main() -> None:
    BeatboxDemo().run()


if __name__ == "__main__":
    main()
