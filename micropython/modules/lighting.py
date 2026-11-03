import asyncio

from beatbox_module import ModuleManager, ModuleMap
from can_protocol import LightingCommand, MessageType, Priority, parse_identifier
from pca9955b import PCA9955, LedChannel


class LightingChannelGroup:
    def __init__(self, name, channel_indexes):
        self.name = name
        self.channel_indexes = tuple(channel_indexes)
        self.is_on = False

    def set_state(self, led_driver, state):
        for channel_index in self.channel_indexes:
            led_driver.channels[channel_index].state = state
        self.is_on = state == LedChannel.ON


IR_CHANNEL_GROUP = LightingChannelGroup("IR", (0,))
RED_CHANNEL_GROUP = LightingChannelGroup("RED", (2, 3))
WHITE_CHANNEL_GROUP = LightingChannelGroup("WHITE", (4, 5))
CHANNEL_GROUPS = (IR_CHANNEL_GROUP, RED_CHANNEL_GROUP, WHITE_CHANNEL_GROUP)


class LightingModule(ModuleManager):
    def __init__(self):
        super().__init__(module_name="LIGHTING")
        self.led_driver = PCA9955(None, address=0x65)
        self.channel_groups = CHANNEL_GROUPS
        self.group_to_duty = {
            "WHITE": 255,
            "RED": 255,
            "IR": 255,
        }
        self._active_group_index = None
        self._configure_led_driver()
        self.led.off() # turn off user led

    def _configure_led_driver(self):
        self.led_driver.reset()
        self.led_driver.output_enable = True
        self.led_driver.gain = 0xFF
        self.led_driver.brightness = 0xFF

        for group in self.channel_groups:
            group.set_state(self.led_driver, LedChannel.OFF)

    def _get_active_group(self):
        if self._active_group_index is None:
            return None
        return self.channel_groups[self._active_group_index]

    def _set_group_duty(self, group_name, duty):
        duty = max(0, min(255, duty))
        self.group_to_duty[group_name] = duty
        target = None
        for group in self.channel_groups:
            if group.name == group_name:
                target = group
                break
        if target is None:
            return
        for channel_index in target.channel_indexes:
            self.led_driver.channels[channel_index].brightness = duty

    def _apply_duty_payload(self, payload):
        length = len(payload)
        if length >= 1:
            self._set_group_duty("WHITE", payload[0])
        if length >= 2:
            self._set_group_duty("RED", payload[1])
        if length >= 3:
            self._set_group_duty("IR", payload[2])

    def command_handler(self, msg):
        super().command_handler(msg)

        _priority, target_module_id, msg_type, command = parse_identifier(msg.id)
        if msg_type != MessageType.REQUEST_MODULE:
            return
        if target_module_id != self.id and target_module_id != ModuleMap.BROADCAST_ID:
            return

        if command == LightingCommand.SET_DUTY:
            self._apply_duty_payload(msg.data)
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_MODULE,
                command=LightingCommand.SET_DUTY,
                payload=bytearray([
                    self.group_to_duty["WHITE"],
                    self.group_to_duty["RED"],
                    self.group_to_duty["IR"],
                ]),
            )
            return

        if command == LightingCommand.GET_DUTY:
            self._send_frame(
                priority=Priority.STATUS,
                msg_type=MessageType.RESPONSE_MODULE,
                command=LightingCommand.GET_DUTY,
                payload=bytearray([
                    self.group_to_duty["WHITE"],
                    self.group_to_duty["RED"],
                    self.group_to_duty["IR"],
                ]),
            )
            return

        if command == LightingCommand.TURN_ON:
            group_index = msg.data[0] if msg.data else 0
            if group_index < len(self.channel_groups):
                self.channel_groups[group_index].set_state(self.led_driver, LedChannel.ON)
            return

        if command == LightingCommand.TURN_OFF:
            if msg.data:
                group_index = msg.data[0]
                if group_index < len(self.channel_groups):
                    self.channel_groups[group_index].set_state(self.led_driver, LedChannel.OFF)
            else:
                for group in self.channel_groups:
                    group.set_state(self.led_driver, LedChannel.OFF)

    async def cycle_channels(self):
        while True:
            active_group = self._get_active_group()
            if active_group is not None and active_group.is_on:
                active_group.set_state(self.led_driver, LedChannel.OFF)

            if self._active_group_index is None:
                self._active_group_index = 0
            else:
                self._active_group_index = (self._active_group_index + 1) % len(self.channel_groups)

            next_group = self.channel_groups[self._active_group_index]
            next_group.set_state(self.led_driver, LedChannel.ON)
            await asyncio.sleep(1)

def main():
    lighting = LightingModule()
    asyncio.run(lighting.start_managers())

# Deployed as :main.py the board runs this as __main__, so the module still
# auto-starts; importing it (tests, REPL) no longer touches the hardware.
if __name__ == "__main__":
    main()