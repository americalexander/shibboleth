"""Generic protocol-layer contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..contracts import SerialTransport


class ProtocolError(Exception):
    """Base exception for protocol errors."""


class ProtocolTimeout(ProtocolError):
    """Expected response was not received in time."""


class ProtocolMessageError(ProtocolError):
    """A received message is malformed or semantically invalid."""


class WireProtocol(ABC):
    """Base class for a receiver wire protocol."""

    def __init__(self, transport: SerialTransport) -> None:
        self.transport = transport

    @abstractmethod
    def start(self) -> None:
        """Start protocol processing."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop protocol processing."""
        raise NotImplementedError

    @abstractmethod
    def send(self, message: Any) -> Any:
        """Send one protocol-level request and return its response."""
        raise NotImplementedError
