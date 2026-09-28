import asyncio

from can_manager import CanManager

# Raw CAN IDs for link validation (not Beatbox protocol IDs).
PING_CAN_ID = 0x321
PONG_CAN_ID = 0x322


class CanDummyTxModule:
    def __init__(self):
        self.can_manager = CanManager(loopback=False, silent=False)
        self.sequence = 0
        self.can_manager.register_rx_callback(self._on_can_message)

    def _on_can_message(self, message):
        if message.id != PONG_CAN_ID:
            return

        seq = message.data[0] if message.data else -1
        print(f"RX PONG id=0x{message.id:03X} seq={seq} data={list(message.data)}")

    async def ping_task(self):
        while True:
            payload = bytearray([self.sequence])
            self.can_manager.send_normal(can_id=PING_CAN_ID, data=payload)
            print(f"TX PING id=0x{PING_CAN_ID:03X} seq={self.sequence}")
            self.sequence = (self.sequence + 1) & 0xFF
            await asyncio.sleep(1.0)

    async def run(self):
        await asyncio.gather(
            self.can_manager.can_rx_task(),
            self.can_manager.can_tx_task(),
            self.ping_task(),
        )


def main():
    module = CanDummyTxModule()
    print("CAN dummy TX started")
    asyncio.run(module.run())


if __name__ == "__main__":
    main()


