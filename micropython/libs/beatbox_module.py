import asyncio

from bb_mpy_module import Led

from can_manager import CanManager

# Module registry for seamless ID, ID_NAME, CAN_ID conversion
class ModuleMap:
    _entries = [
        ("GENERIC", 0xFF, 0xFFF),
        ("MAIN", 0x00, 0x100),
        ("SCREEN", 0x10, 0x200),
        ("FEEDER", 0x20, 0x202),
        ("LIGHTING", 0x30, 0x203),
        ("NOSEPOKE", 0x40, 0x204),
        ("IR_BARRIER", 0x05, 0x205),
    ]
    name_to_id = {name: mid for name, mid, _ in _entries}
    id_to_name = {mid: name for name, mid, _ in _entries}
    id_to_can = {mid: canid for name, mid, canid in _entries}
    can_to_id = {canid: mid for name, mid, canid in _entries}
    name_to_can = {name: canid for name, mid, canid in _entries}
    can_to_name = {canid: name for name, mid, canid in _entries}

    @classmethod
    def get_id(cls, value):
        if isinstance(value, int):
            if value in cls.id_to_name:
                return value
            if value in cls.can_to_id:
                return cls.can_to_id[value]
        elif isinstance(value, str):
            return cls.name_to_id.get(value)
        return None

    @classmethod
    def get_name(cls, value):
        if isinstance(value, int):
            if value in cls.id_to_name:
                return cls.id_to_name[value]
            if value in cls.can_to_name:
                return cls.can_to_name[value]
        elif isinstance(value, str):
            if value in cls.name_to_id:
                return value
        return None

    @classmethod
    def get_can_id(cls, value):
        if isinstance(value, int):
            if value in cls.id_to_can:
                return cls.id_to_can[value]
            if value in cls.can_to_id:
                return value
        elif isinstance(value, str):
            return cls.name_to_can.get(value)
        return None

class ModuleStatus:
    INIT = "INIT" # not configured
    IDLE = "IDLE" # configured but not active
    ACTIVE = "ACTIVE" # actively running
    ERROR = "ERROR" # error state

    _ALL = (INIT, IDLE, ACTIVE, ERROR)

    @classmethod
    def is_valid(cls, value):
        return value in cls._ALL

# TODO: add log method/class, to handle debug and file logging
class ModuleManager:
    """
    Base module manager for Beatbox system.
    Handles CAN communication and provides a structure for module-specific functionality.
    Child classes must call super().__init__() with the corresponding module name (string)
    and can extend functionality by overriding methods.
    """
    def __init__(self, module_name: str = "GENERIC"):
        self.led = Led()
        self.led.on()

        # Use ModuleRegistry for all lookups
        # TODO: handle invalid module_name
        self.id = ModuleMap.get_id(module_name)
        self.can_id = ModuleMap.get_can_id(self.id)
        print(f"Module initialized: NAME={module_name}, ID={self.id}, CAN_ID={self.can_id}")
        self.can_manager = CanManager(can_id=self.can_id)
        self.can_manager.register_rx_callback(self.can_msg_echo_on_serial)

        self.status = ModuleStatus.INIT
        self.tasks = [
            asyncio.create_task(self.can_manager.can_rx_task()),
            asyncio.create_task(self.can_manager.can_tx_task()),
            # asyncio.create_task(self.heartbeat_task()),
        ]
        self.heartbeat_interval = 0.02 # 20 ms

    """
        # Main loop task to handle module logic
        async def heartbeat_task(self):
            while True:
                # Placeholder for module-specific periodic tasks

                await asyncio.sleep(self.heartbeat_interval) 
    """

    def can_msg_echo_on_serial(self, msg):
        # TODO: check if serial manager exists and available
        print(f'{self.status}, received from ID={msg.id}, data={list(msg.data)}')
        self.command_handler(msg.data)

    def command_handler(self, command):
        """Handle incoming commands - to be overridden by child classes"""
        if b"SCAN" in command:
            # Respond to scan request with module ID
            self.can_manager.send_normal(data=bytearray(f"ID_{self.id}", 'utf-8'))
            self.status = ModuleStatus.IDLE
        if b"R_STATUS" in command:
                self.can_manager.send_normal(data=bytearray(f"ST_{self.status}", 'utf-8'))

    async def start_managers(self):
        """
        Start all module manager tasks
        Child classes should append their tasks to self.tasks before calling super().start_managers()
        """
        await asyncio.gather(*self.tasks)

    #@abstractmethod
    #def print(self): ...

