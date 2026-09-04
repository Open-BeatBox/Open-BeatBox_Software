import asyncio

from can_manager import CanManager

# Raw CAN IDs for link validation (not Beatbox protocol IDs).
PING_CAN_ID = 0x321
PONG_CAN_ID = 0x322


class CanDummyRxModule:
    def __init__(self):
        self.can_manager = CanManager(loopback=False, silent=False)
        self.can_manager.register_rx_callback(self._on_can_message)

    def _on_can_message(self, message):
        if message.id != PING_CAN_ID:
            return

        seq = message.data[0] if message.data else 0
        print(f"RX PING id=0x{message.id:03X} seq={seq} data={list(message.data)}")

        # Echo back sequence so sender can confirm full round-trip.
        self.can_manager.send_urgent(can_id=PONG_CAN_ID, data=bytearray([seq]))
        print(f"TX PONG id=0x{PONG_CAN_ID:03X} seq={seq}")

    async def heartbeat_task(self):
        while True:
            print("CAN dummy RX alive")
            await asyncio.sleep(5.0)

    async def run(self):
        await asyncio.gather(
            self.can_manager.can_rx_task(),
            self.can_manager.can_tx_task(),
            self.heartbeat_task(),
        )


def main():
    module = CanDummyRxModule()
    print("CAN dummy RX started")
    asyncio.run(module.run())


if __name__ == "__main__":
    main()


