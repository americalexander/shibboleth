"""Thin MQTT adapter.

The MQTT adapter exposes normalized receiver semantics. It does not know
ISCP command strings or SSP opcodes.

Topic layout:

    receiver/availability
    receiver/<zone>/<field>/set
    receiver/<zone>/<field>/state

Examples:

    receiver/main/power/set      -> on
    receiver/main/volume/set     -> 35
    receiver/zone2/source/set    -> CD
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import paho.mqtt.client as mqtt

from .contracts import ReceiverDriver, ReceiverEvent


@dataclass(frozen=True)
class MQTTConfig:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    base_topic: str = "receiver"


class MQTTAdapter:
    """Translate MQTT messages to normalized driver calls."""

    def __init__(
        self,
        config: MQTTConfig,
        driver: ReceiverDriver,
        event_sink: Callable[[ReceiverEvent], None],
    ) -> None:
        self.config = config
        self.driver = driver
        self.event_sink = event_sink
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if config.username is not None:
            self.client.username_pw_set(config.username, config.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _topic(self, suffix: str) -> str:
        return f"{self.config.base_topic}/{suffix}"

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        client.publish(self._topic("availability"), "online", retain=True)
        client.subscribe(self._topic("+/+/set"))

    def _on_message(self, client, userdata, msg) -> None:
        prefix = self.config.base_topic + "/"
        if not msg.topic.startswith(prefix):
            return
        parts = msg.topic[len(prefix):].split("/")
        if len(parts) != 3 or parts[2] != "set":
            return

        zone, field = parts[:2]
        value = msg.payload.decode("utf-8").strip()

        if field == "power":
            self.driver.set_power(zone, value.lower() in {"on", "1", "true"})
        elif field == "mute":
            self.driver.set_mute(zone, value.lower() in {"on", "1", "true"})
        elif field == "volume":
            self.driver.set_volume(zone, float(value))
        elif field == "source":
            self.driver.set_source(zone, value)
        else:
            # Device-specific extensions can be routed through a future
            # command registry without changing this adapter's contract.
            return

    def publish_event(self, event: ReceiverEvent) -> None:
        suffix = f"{event.zone}/{event.field}/state" if event.zone else f"{event.field}/state"
        self.client.publish(self._topic(suffix), str(event.value), retain=True)

    def start(self) -> None:
        self.client.will_set(self._topic("availability"), "offline", retain=True)
        self.client.connect(self.config.host, self.config.port)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.publish(self._topic("availability"), "offline", retain=True)
        self.client.loop_stop()
        self.client.disconnect()
