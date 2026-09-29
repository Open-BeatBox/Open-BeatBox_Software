"""V2 pin <-> Intent/Event mapping. Pure: no hardware, no transport.

Output rules:
- level pins re-emit their intent on every change (reward, lights),
- screen bits form a group mapped to a single pattern_id nibble.

Input rules:
- beam / contact sensors are active-low, so levels go through _level(),
- pulses carry an explicit restore level (idle, not necessarily 0).

The reward path is a pure level in both directions, mirroring V2: the app turns
the reward pin on, waits, turns it off; the pellet breaking the food-well beam
is what the app reads as "delivered".
"""

import bb_events as ev
import bb_intents as intents
from bb_v2_emulator import BeatBoxV2Emulator as Emu
from can_protocol import ModuleMap

NOSEPOKE = ModuleMap.get_id("NOSEPOKE")
FEEDER = ModuleMap.get_id("FEEDER")
SCREEN_L = ModuleMap.get_id("SCREEN_LEFT")
SCREEN_R = ModuleMap.get_id("SCREEN_RIGHT")
IR_BARRIER = ModuleMap.get_id("IR_BARRIER")

SCREEN_LEFT_BITS = ("fig_1_left", "fig_2_left", "fig_3_left", "blink_left")
SCREEN_RIGHT_BITS = ("fig_1_right", "fig_2_right", "fig_3_right", "blink_right")

# Which CAN module owns the V2 'nose_poke' pin. The food-well sensor lives on
# the feeder, so FEEDER.REWARD_STATUS drives it. Set to True only if a separate
# nose-poke port exists AND the feeder does not report the same beam, otherwise
# the two sources fight and the last frame wins.
NOSEPOKE_MODULE_DRIVES_FOOD_WELL = False

FOOD_WELL_PIN = "nose_poke"


def _level(pin, active):
    """active: True = beam broken / touched / asserted."""
    return Emu.active_level(pin) if active else Emu.idle_level(pin)


# ─── outputs: app -> CAN ─────────────────────────────────────

def _nibble(states, bits):
    value = 0
    for index, name in enumerate(bits):
        value |= (1 if states[name] else 0) << index
    return value


def _reward(states, changed):
    return intents.reward(bool(states["reward"]))


def _white(states, changed):
    return intents.light("WHITE", bool(states["light"]))


def _red(states, changed):
    return intents.light("RED", bool(states["red_light"]))


def _screen_left(states, changed):
    return intents.pattern("SCREEN_LEFT", _nibble(states, SCREEN_LEFT_BITS))


def _screen_right(states, changed):
    return intents.pattern("SCREEN_RIGHT", _nibble(states, SCREEN_RIGHT_BITS))


OUTPUT_GROUPS = (
    (("reward",), _reward),
    (("light",), _white),
    (("red_light",), _red),
    (SCREEN_LEFT_BITS, _screen_left),
    (SCREEN_RIGHT_BITS, _screen_right),
)


def intents_for_output_change(states, changed):
    """states: full output pin dict. changed: delta dict. -> list of Intent."""
    out = []
    for bits, handler in OUTPUT_GROUPS:
        if not any(bit in changed for bit in bits):
            continue
        intent = handler(states, changed)
        if intent is not None:
            out.append(intent)
    return out


# ─── inputs: CAN -> app ──────────────────────────────────────

LEVEL_INPUTS = {}
if NOSEPOKE_MODULE_DRIVES_FOOD_WELL:
    LEVEL_INPUTS[(ev.BEAM_EVENT, NOSEPOKE)] = FOOD_WELL_PIN
    LEVEL_INPUTS[(ev.BEAM_STATUS, NOSEPOKE)] = FOOD_WELL_PIN

PULSE_INPUTS = {
    (ev.TOUCH, SCREEN_L): "left_response",
    (ev.TOUCH, SCREEN_R): "right_response",
}

BARRIER_LINES = {0: "tunnel_1", 1: "tunnel_2"}


def pin_updates_for_event(event):
    """-> ({pin: level}, {pin: restore_level})

    The second dict lists pins to restore after V2Frontend.PULSE_MS.
    """
    key = (event.name, event.module_id)

    pin = LEVEL_INPUTS.get(key)
    if pin is not None:
        return {pin: _level(pin, bool(event.fields.get("state", 1)))}, {}

    pin = PULSE_INPUTS.get(key)
    if pin is not None:
        return {pin: _level(pin, True)}, {pin: _level(pin, False)}

    if event.module_id == IR_BARRIER:
        if event.name == ev.BARRIER_EVENT:
            pin = BARRIER_LINES.get(event.fields.get("line"))
            if pin is None:
                return {}, {}
            return {pin: _level(pin, bool(event.fields.get("state", 1)))}, {}
        if event.name == ev.BARRIER_STATUS:
            mask = event.fields.get("mask", 0)
            return {pin: _level(pin, bool((mask >> line) & 1))
                    for line, pin in BARRIER_LINES.items()}, {}

    if event.module_id == FEEDER:
        if event.name == ev.REWARD_STATUS:
            # Level, no restore: the pellet holds the beam broken until the
            # animal takes it, then the feeder reports state=0.
            present = bool(event.fields.get("state", 0))
            return {FOOD_WELL_PIN: _level(FOOD_WELL_PIN, present)}, {}
        if event.name == ev.ERROR:
            return {"feeder_alert": 1}, {}
        if event.name == ev.STATUS:
            alert = 1 if event.fields.get("code") == ev.STATUS_ERROR_CODE else 0
            return {"feeder_alert": alert}, {}

    return {}, {}