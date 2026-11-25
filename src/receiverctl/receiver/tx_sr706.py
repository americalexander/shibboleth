"""Onkyo TX-SR706 receiver driver.

The command tables are derived from the supplied Onkyo workbook.

The workbook identifies the TX-SR706/TX-SA706 column on Main and Zone 2.
The driver intentionally exposes the semantic controls first and keeps the
native ISCP strings here.

Main controls implemented in this first pass:
* PWR
* AMT
* MVL
* SLI
* TUN
* PRS
* LMD

Zone 2 controls implemented:
* ZPW
* ZMT
* ZVL
* SLZ
* TUZ
* PRZ

The workbook documents hexadecimal volume parameters. The driver therefore
uses a normalized 0..100 percentage-like level for volume and converts it to
the receiver's documented 00..64 hexadecimal representation.
"""

from __future__ import annotations

from ..models import (
    CommandResult,
    MuteState,
    PowerState,
    ReceiverCapabilities,
    ZoneCapabilities,
)
from ..protocol.iscp import ISCPMessage, ISCPProtocol
from .base import BaseReceiverDriver


TX_SR706_CAPABILITIES = ReceiverCapabilities(
    model="TX-SR706",
    protocol="ISCP/RS-232C",
    zones={
        "main": ZoneCapabilities(
            power=True, mute=True, volume=True, source=True, tuner=True,
            preset=True, listening_mode=True, video=True
        ),
        "zone2": ZoneCapabilities(
            power=True, mute=True, volume=True, source=True, tuner=True,
            preset=True
        ),
    },
)


class TXSR706Driver(BaseReceiverDriver):
    """Semantic TX-SR706 driver."""

    def __init__(self, transport, event_sink=None) -> None:
        super().__init__(TX_SR706_CAPABILITIES, event_sink)
        self.protocol = ISCPProtocol(transport)

    def start(self) -> None:
        self.protocol.transport.open()
        self._set_connected(True)

    def stop(self) -> None:
        self.protocol.transport.close()
        self._set_connected(False)

    def _command(self, command: str, parameter: str) -> CommandResult:
        self.protocol.send(command, parameter)
        return CommandResult(accepted=True, message=f"{command}{parameter}")

    def set_power(self, zone: str, on: bool) -> CommandResult:
        if zone == "main":
            result = self._command("PWR", "01" if on else "00")
        elif zone == "zone2":
            result = self._command("ZPW", "01" if on else "00")
        else:
            raise ValueError(f"unsupported zone: {zone}")

        self._set_zone_field(zone, "power", PowerState.ON if on else PowerState.OFF)
        return result

    def set_mute(self, zone: str, muted: bool) -> CommandResult:
        command = {"main": "AMT", "zone2": "ZMT"}.get(zone)
        if command is None:
            raise ValueError(f"unsupported zone: {zone}")
        result = self._command(command, "01" if muted else "00")
        self._set_zone_field(
            zone, "mute", MuteState.MUTED if muted else MuteState.UNMUTED
        )
        return result

    @staticmethod
    def _volume_parameter(value: float) -> str:
        if not 0 <= value <= 100:
            raise ValueError("TX-SR706 normalized volume must be 0..100")
        return f"{round(value):02X}"

    @staticmethod
    def _volume_value(parameter: str) -> float:
        return float(int(parameter, 16))

    def set_volume(self, zone: str, value: float) -> CommandResult:
        command = {"main": "MVL", "zone2": "ZVL"}.get(zone)
        if command is None:
            raise ValueError(f"unsupported zone: {zone}")
        parameter = self._volume_parameter(value)
        result = self._command(command, parameter)
        self._set_zone_field(zone, "volume", value)
        return result

    def set_source(self, zone: str, source: str) -> CommandResult:
        command = {"main": "SLI", "zone2": "SLZ"}.get(zone)
        if command is None:
            raise ValueError(f"unsupported zone: {zone}")
        result = self._command(command, source.upper())
        self._set_zone_field(zone, "source", source.upper())
        return result

    def query(self, command: str, zone: str) -> ISCPMessage:
        return self.protocol.transact(command, "QSTN")

    def query_all(self) -> None:
        # Questions documented by the workbook. Response interpretation is
        # centralized below so the same parser is usable for unsolicited
        # notifications.
        for command, zone in (
            ("PWR", "main"),
            ("AMT", "main"),
            ("MVL", "main"),
            ("SLI", "main"),
            ("ZPW", "zone2"),
            ("ZMT", "zone2"),
            ("ZVL", "zone2"),
            ("SLZ", "zone2"),
        ):
            message = self.query(command, zone)
            self.apply_message(message, zone_hint=zone)

    def apply_message(self, message: ISCPMessage, zone_hint: str | None = None) -> None:
        """Translate a native ISCP status message into normalized state."""
        command = message.command
        parameter = message.parameter
        zone = zone_hint or {
            "PST": "main",
            "AMT": "main",
            "MVL": "main",
            "SLI": "main",
            "ZPW": "zone2",
            "ZMT": "zone2",
            "ZVL": "zone2",
            "SLZ": "zone2",
        }.get(command)

        if zone is None:
            return

        if command in {"PWR", "PST", "ZPW"}:
            # The workbook shows PWR command/status examples. Some receiver
            # firmware uses the command family itself for status; retain this
            # parser as a tolerant model-level translation.
            self._set_zone_field(
                zone, "power",
                PowerState.ON if parameter == "01" else PowerState.OFF
            )
        elif command in {"AMT", "ZMT"}:
            self._set_zone_field(
                zone, "mute",
                MuteState.MUTED if parameter == "01" else MuteState.UNMUTED
            )
        elif command in {"MVL", "ZVL"}:
            self._set_zone_field(zone, "volume", self._volume_value(parameter))
        elif command in {"SLI", "SLZ"}:
            self._set_zone_field(zone, "source", parameter)
        elif command in {"TUN", "TUZ"}:
            self._set_zone_field(zone, "tuner_frequency", parameter)
        elif command in {"PRS", "PRZ"}:
            try:
                self._set_zone_field(zone, "preset", int(parameter, 16))
            except ValueError:
                self._set_zone_extra(zone, "preset_raw", parameter)
        elif command == "LMD":
            self._set_zone_field(zone, "listening_mode", parameter)
