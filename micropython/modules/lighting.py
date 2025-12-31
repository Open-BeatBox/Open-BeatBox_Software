import asyncio

from beatbox_module import ModuleManager
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
        self._active_group_index = None
        self._configure_led_driver()
        self.tasks.append(asyncio.create_task(self.blink()))
        self.tasks.append(asyncio.create_task(self.cycle_channels()))

    async def blink(self):
        while True:
            self.led.toggle()
            await asyncio.sleep(0.5)

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