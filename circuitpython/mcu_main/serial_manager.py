import asyncio
import time
import supervisor

class SerialManager:
    def __init__(self):
        self.tx_queue = asyncio.Queue()
        self.command_callbacks = []
        self.last_activity = time.monotonic()

    def register_command_callback(self, callback):
        """Register callback for received commands"""
        self.command_callbacks.append(callback)

    async def send_message(self, message):
        """Queue message for transmission"""
        await self.tx_queue.put(message)

    async def serial_handler_task(self):
        """Serial task - 50ms max processing time"""
        print("Serial Handler started - 50ms max processing time")

        while True:
            start_time = time.monotonic()

            try:
                # Send queued messages
                while not self.tx_queue.empty():
                    try:
                        msg = self.tx_queue.get_nowait()
                        print(msg.strip())  # Send to PC
                    except asyncio.QueueEmpty:
                        break

                # Check for incoming data
                if supervisor.runtime.serial_bytes_available:
                    try:
                        data = input().strip()
                        if data:
                            # Process command through callbacks
                            for callback in self.command_callbacks:
                                try:
                                    await callback(data)
                                except Exception as e:
                                    print(f"Serial command callback error: {e}")

                            self.last_activity = time.monotonic()
                    except:
                        pass  # No data available

                # Check processing time
                processing_time = (time.monotonic() - start_time) * 1000
                if processing_time > 40:  # Warn if approaching limit
                    print(f"Warning: Serial processing took {processing_time:.1f}ms")

            except Exception as e:
                print(f"Serial error: {e}")

            # Longer sleep since serial is lower priority
            await asyncio.sleep(0.020)  # 20ms between checks
