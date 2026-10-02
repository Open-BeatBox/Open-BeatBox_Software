class BeatBoxV2Emulator:
    """Keep V2 pin states in the exact BB Mega firmware bit ordering.
       Also keep track of which pins have been updated.
    """

    # Ordering copied from V2 firmware arrays.
    INPUT_PIN_ORDER = (
        "nose_poke",
        "left_response",
        "right_response",
        "tunnel_1",
        "tunnel_2",
        "feeder_alert",
    )
    OUTPUT_PIN_ORDER = (
        "reward",
        "blink_left",
        "fig_1_left",
        "fig_2_left",
        "fig_3_left",
        "blink_right",
        "fig_1_right",
        "fig_2_right",
        "fig_3_right",
        "light",
        "red_light",
    )

    # Beam / contact sensors idle high: 1 = clear or untouched, 0 = broken or
    # touched. feeder_alert is a firmware flag, not a sensor, so it idles low.
    ACTIVE_LOW_INPUTS = (
        "nose_poke",        # food well beam
        "left_response",
        "right_response",
        "tunnel_1",
        "tunnel_2",
    )

    def __init__(self):
        self.input_pin_states = {name: self.idle_level(name)
                                 for name in self.INPUT_PIN_ORDER}
        self.output_pin_states = {name: 0 for name in self.OUTPUT_PIN_ORDER}

    # ─── polarity ────────────────────────────────────────────

    @classmethod
    def idle_level(cls, pin_name):
        """Pin level when nothing is happening."""
        return 1 if pin_name in cls.ACTIVE_LOW_INPUTS else 0

    @classmethod
    def active_level(cls, pin_name):
        """Pin level when the sensor is triggered."""
        return 0 if pin_name in cls.ACTIVE_LOW_INPUTS else 1

    @staticmethod
    def _as_bit(value):
        return 1 if value else 0

    # ─── outputs ─────────────────────────────────────────────

    def set_output_states_from_word(self, pin_states_word):
        """Set all output pins from one word. Returns {pin_name: new_bit} delta."""
        changed = {}
        for index, pin_name in enumerate(self.OUTPUT_PIN_ORDER):
            bit = self._as_bit((pin_states_word >> index) & 0x01)
            if bit != self.output_pin_states[pin_name]:
                self.output_pin_states[pin_name] = bit
                changed[pin_name] = bit
        return changed

    def get_output_pin_states_as_word(self):
        return "".join(
            "1" if self._as_bit(self.output_pin_states[pin_name]) else "0"
            for pin_name in self.OUTPUT_PIN_ORDER
        )

    # ─── inputs ──────────────────────────────────────────────

    def update_input_pin_states(self, pin_updates):
        """Update a subset of input pins. Returns {pin_name: new_bit} delta."""
        changed = {}
        for pin_name, value in pin_updates.items():
            if pin_name not in self.input_pin_states:
                continue
            bit = self._as_bit(value)
            if bit != self.input_pin_states[pin_name]:
                self.input_pin_states[pin_name] = bit
                changed[pin_name] = bit
        return changed

    def get_input_state_byte(self):
        state_byte = 0
        for index, pin_name in enumerate(self.INPUT_PIN_ORDER):
            state_byte |= (self._as_bit(self.input_pin_states[pin_name]) << index)
        return state_byte & 0xFF