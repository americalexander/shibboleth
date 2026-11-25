"""Application composition root.

This is where configuration chooses exactly one receiver implementation.
Nothing below this point needs to know the wire protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mqtt import MQTTAdapter, MQTTConfig
from .receiver.sr_h1000 import SRH1000Driver
from .receiver.tx_sr706 import TXSR706Driver
from .state import StateManager
from .transport import PySerialTransport, SerialConfig


@dataclass(frozen=True)
class AppConfig:
    receiver_type: str
    serial: SerialConfig
    mqtt: MQTTConfig


class ReceiverApplication:
    def __init__(self, config: AppConfig) -> None:
        self.state = StateManager()
        transport = PySerialTransport(config.serial)

        if config.receiver_type == "sr_h1000":
            self.driver = SRH1000Driver(transport, self.state.apply)
        elif config.receiver_type == "tx_sr706":
            self.driver = TXSR706Driver(transport, self.state.apply)
        else:
            raise ValueError(f"unsupported receiver type: {config.receiver_type}")

        self.mqtt = MQTTAdapter(config.mqtt, self.driver, self.state.apply)
        self.state.add_listener(self.mqtt.publish_event)

    def start(self) -> None:
        self.driver.start()
        self.mqtt.start()
        self.driver.query_all()

    def stop(self) -> None:
        self.mqtt.stop()
        self.driver.stop()
