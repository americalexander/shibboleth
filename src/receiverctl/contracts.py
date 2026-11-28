"""High-level contractual interfaces.

This file is the architectural boundary of the application. Higher layers
depend on these contracts rather than on SSP or ISCP implementation details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .models import CommandResult, ReceiverCapabilities, ReceiverState


class SerialTransport(Protocol):
    """Byte-stream transport.

    The transport deliberately knows nothing about framing or commands.
    """

    def open(self) -> None: ...
    def close(self) -> None: ...
    @property
    def is_open(self) -> bool: ...
    def write(self, data: bytes) -> None: ...
    def read(self, size: int = 1) -> bytes: ...
    def read_until(self, terminator: bytes, size: int = 4096) -> bytes: ...


class ProtocolEventSink(Protocol):
    """Callback used by protocol implementations for asynchronous messages."""

    def __call__(self, event: Any) -> None: ...


class ReceiverDriver(ABC):
    """Semantic interface implemented by every receiver model."""

    @property
    @abstractmethod
    def capabilities(self) -> ReceiverCapabilities:
        """Return static capabilities of this receiver."""
        raise NotImplementedError

    @property
    @abstractmethod
    def state(self) -> ReceiverState:
        """Return the latest device-confirmed state."""
        raise NotImplementedError

    @abstractmethod
    def start(self) -> None:
        """Open the protocol and begin receiving asynchronous events."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop protocol activity and release the transport."""
        raise NotImplementedError

    @abstractmethod
    def set_power(self, zone: str, on: bool) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def set_mute(self, zone: str, muted: bool) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def set_volume(self, zone: str, value: float) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def set_source(self, zone: str, source: str) -> CommandResult:
        raise NotImplementedError

    def set_source_power(self, source: str, on: bool) -> CommandResult:
        raise NotImplementedError

    def set_source_mute(self, source: str, muted: bool) -> CommandResult:
        raise NotImplementedError

    def set_source_volume(self, source: str, value: float) -> CommandResult:
        raise NotImplementedError

    def set_source_channel(self, source: str, channel: int) -> CommandResult:
        raise NotImplementedError

    def query_all(self) -> None:
        """Refresh state from the device.

        A device may implement this as a series of protocol questions/gets.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class ReceiverEvent:
    """Normalized event emitted by a driver.

    Exactly one of ``zone`` or ``source`` is normally populated for a
    device-specific state event.  Source events describe the physical source
    itself rather than a zone's selection of that source.
    """

    zone: str | None
    field: str
    value: Any
    raw: Any = None
    source: str | None = None


class DriverFactory(Protocol):
    """Factory type used by application/configuration code."""

    def __call__(self, transport: SerialTransport,
                 event_sink: Callable[[ReceiverEvent], None]) -> ReceiverDriver: ...
