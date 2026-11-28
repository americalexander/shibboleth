"""Shared semantic data models.

These classes intentionally contain no protocol-specific terminology.
A driver converts its native protocol representation into these objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PowerState(str, Enum):
    OFF = "off"
    ON = "on"
    UNKNOWN = "unknown"


class MuteState(str, Enum):
    UNMUTED = "unmuted"
    MUTED = "muted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ZoneCapabilities:
    """Capabilities advertised by one logical receiver zone."""

    power: bool = False
    mute: bool = False
    volume: bool = False
    source: bool = False
    tuner: bool = False
    preset: bool = False
    listening_mode: bool = False
    tone: bool = False
    video: bool = False
    metadata: bool = False
    extras: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SourceCapabilities:
    """Capabilities of an independent source device.

    A source is not a receiver zone.  For example, the SR-H1000 is one
    physical Sirius source which can be selected by more than one zone.
    """

    power: bool = False
    mute: bool = False
    volume: bool = False
    channel: bool = False
    metadata: bool = False
    extras: frozenset[str] = frozenset()


@dataclass
class ZoneState:
    """Last device-confirmed state for one logical zone."""

    power: PowerState = PowerState.UNKNOWN
    mute: MuteState = MuteState.UNKNOWN
    volume: float | None = None
    source: str | None = None
    tuner_frequency: str | None = None
    preset: int | None = None
    listening_mode: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceState:
    """Last device-confirmed state for one physical source."""

    power: PowerState = PowerState.UNKNOWN
    mute: MuteState = MuteState.UNKNOWN
    volume: float | None = None
    channel: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReceiverState:
    """Device state plus connection status.

    `desired` state is intentionally not stored here. Desired state belongs
    to the command/request layer. This object represents observations made
    by the receiver.
    """

    connected: bool = False
    ready: bool = False
    zones: dict[str, ZoneState] = field(default_factory=dict)
    sources: dict[str, SourceState] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiverCapabilities:
    """Capabilities of a particular receiver model."""

    model: str
    zones: dict[str, ZoneCapabilities]
    protocol: str
    sources: dict[str, SourceCapabilities] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResult:
    """Result of a driver command.

    `state_changed` is only an indication that a device response or event
    confirms a change; it is not an optimistic echo of the request.
    """

    accepted: bool
    message: str = ""
    state_changed: bool = False
