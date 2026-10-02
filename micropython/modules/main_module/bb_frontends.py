"""Transport-facing adapters. Both expose on_event(event) and tasks()."""

import asyncio

import bb_events as ev
import bb_ticks
from bb_v2_map import intents_for_output_change, pin_updates_for_event
from bb_v2_comm import V2WireCodec
from can_protocol import ModuleMap
from cli_protocol import CommandError, parse_command


# ─── CLI rendering ───────────────────────────────────────────

def _module_name(event):
    return ModuleMap.get_name(event.module_id) or "M%d" % event.module_id


def render(event):
    name = event.name
    module = _module_name(event)
    fields = event.fields

    if name == ev.SCAN_REPLY:
        return "FOUND_%s" % module
    if name == ev.STATUS:
        return "%s_STATUS_%s" % (module, ev.status_name(fields.get("code")))
    if name == ev.ERROR:
        return "%s_ERROR_%s" % (module, fields.get("code"))
    if name == ev.CAN_LED_STATE:
        state = fields.get("state")
        if state == 255:
            return "%s_CAN_LED_STATE_UNKNOWN" % module
        return "%s_CAN_LED_STATE_%s" % (module, state)
    if name == ev.USER_BUTTON:
        return "%s_USER_BUTTON_PRESSED" % module
    if name == ev.REWARD_STATUS:
        return "%s_REWARD_%s" % (module, fields.get("state"))
    if name == ev.BEAM_EVENT:
        return "%s_EVENT_BEAM_%s" % (module, fields.get("state"))
    if name == ev.BEAM_STATUS:
        return "%s_BEAM_%s" % (module, fields.get("state"))
    if name == ev.TOUCH:
        return "%s_TOUCH" % module
    if name == ev.DUTY:
        return "%s_DUTY_W%s_R%s_IR%s" % (module, fields.get("white"),
                                         fields.get("red"), fields.get("ir"))
    if name == ev.BARRIER_EVENT:
        return "%s_EVENT_LINE_%s_%s" % (module, fields.get("line"),
                                        fields.get("state"))
    if name == ev.BARRIER_STATUS:
        return "%s_STATUS_MASK_%s" % (module, fields.get("mask"))
    return repr(event)


class CliFrontend:
    def __init__(self, serial_manager, send_intent, send_line=None, rx=True):
        """rx=False -> output-only mirror (e.g. REPL logging next to V2)."""
        self.send_intent = send_intent
        self.send_line = send_line or serial_manager.send_message
        if rx:
            serial_manager.register_rx_callback(self.on_line)

    def tasks(self):
        return ()

    def on_line(self, line):
        try:
            intent = parse_command(line)
        except CommandError as exc:
            return self.send_line("ERR %s" % exc)
        except Exception as exc:       # never let a bad line kill the handler
            return self.send_line("ERR %s" % exc)
        if intent is None:
            return
        if intent.is_local:
            return self.send_line(intent.local_reply)
        self.send_intent(intent)
        self.send_line("OK %s" % intent.name)

    def on_event(self, event):
        self.send_line(render(event))


class V2Frontend:
    # Must exceed a couple of serial_handler_task cycles (20 ms) plus the app's
    # polling granularity, or the two edges of a touch coalesce.
    PULSE_MS = 60
    SERVICE_PERIOD = 0.01

    def __init__(self, serial_manager, send_intent):
        self.serial = serial_manager
        self.send_intent = send_intent
        self.comm = V2WireCodec()
        self.comm.send_line = serial_manager.send_message
        self.comm.on_output_change = self.on_output_change
        serial_manager.register_rx_callback(self.on_line)
        self._pulse_until = {}        # pin -> (deadline, restore_level)

    def tasks(self):
        return (self.handshake_task(), self.service_task())

    # transport -> codec
    def on_line(self, line):
        self.comm.process_message(line)

    # codec -> CAN
    def on_output_change(self, states, changed):
        for intent in intents_for_output_change(states, changed):
            self.send_intent(intent)

    # CAN -> codec
    def on_event(self, event):
        updates, pulses = pin_updates_for_event(event)
        if updates:
            self.comm.on_input_pin_changes(updates)
        if pulses:
            deadline = bb_ticks.ticks_ms() + self.PULSE_MS
            for pin, restore in pulses.items():
                self._pulse_until[pin] = (deadline, restore)

    async def handshake_task(self):
        while not self.serial.running:
            await asyncio.sleep(0.02)
        await self.comm.handshaker()
        # Inputs idle high, so publish the initial levels explicitly.
        self.comm.send_state_frame()

    async def service_task(self):
        while True:
            self.comm.service()
            if self._pulse_until:
                now = bb_ticks.ticks_ms()
                due = [pin for pin, (deadline, _) in self._pulse_until.items()
                       if bb_ticks.ticks_diff(now, deadline) >= 0]
                if due:
                    restores = {pin: self._pulse_until.pop(pin)[1] for pin in due}
                    self.comm.on_input_pin_changes(restores)
            await asyncio.sleep(self.SERVICE_PERIOD)