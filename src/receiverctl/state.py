"""Normalized state manager.

This component is intentionally independent of MQTT and of wire protocols.
It receives confirmed state events from a driver and publishes them to
interested application components.
"""

from __future__ import annotations

from collections.abc import Callable

from .contracts import ReceiverEvent
from .models import ReceiverState


class StateManager:
    def __init__(self, initial: ReceiverState | None = None) -> None:
        self.state = initial or ReceiverState()
        self._listeners: list[Callable[[ReceiverEvent], None]] = []

    def add_listener(self, listener: Callable[[ReceiverEvent], None]) -> None:
        self._listeners.append(listener)

    def apply(self, event: ReceiverEvent) -> None:
        if event.zone is None:
            self.state.extras[event.field] = event.value
        else:
            zone = self.state.zones[event.zone]
            if event.field.startswith("extras/"):
                zone.extras[event.field.removeprefix("extras/")] = event.value
            else:
                setattr(zone, event.field, event.value)

        for listener in self._listeners:
            listener(event)
