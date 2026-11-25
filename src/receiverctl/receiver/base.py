"""Reusable base class for receiver drivers."""

from __future__ import annotations

import threading

from ..contracts import ReceiverDriver, ReceiverEvent
from ..models import ReceiverCapabilities, ReceiverState, ZoneState


class BaseReceiverDriver(ReceiverDriver):
    """Common state/event plumbing.

    Protocol-specific drivers only need to translate native messages into
    normalized fields and invoke `_emit`.
    """

    def __init__(self, capabilities: ReceiverCapabilities, event_sink) -> None:
        self._capabilities = capabilities
        self._state = ReceiverState(
            connected=False,
            ready=False,
            zones={zone: ZoneState() for zone in capabilities.zones}
        )
        self._event_sink = event_sink
        self._lock = threading.RLock()

    @property
    def capabilities(self) -> ReceiverCapabilities:
        return self._capabilities

    @property
    def state(self) -> ReceiverState:
        with self._lock:
            # State objects are mutable; a real application may prefer a
            # deep-copy snapshot. For now this is a documented read model.
            return self._state

    def _set_connected(self, value: bool) -> None:
        with self._lock:
            self._state.connected = value
            self._state.ready = value

    def _set_zone_field(self, zone: str, field: str, value) -> None:
        with self._lock:
            setattr(self._state.zones[zone], field, value)
        self._emit(ReceiverEvent(zone=zone, field=field, value=value))

    def _set_zone_extra(self, zone: str, key: str, value) -> None:
        with self._lock:
            self._state.zones[zone].extras[key] = value
        self._emit(ReceiverEvent(zone=zone, field=f"extras/{key}", value=value))

    def _emit(self, event: ReceiverEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)
