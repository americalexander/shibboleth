"""Polk Audio SR-H1000 driver using Sirius Standard Protocol.

The SSP specification supplies the link-layer framing and the following
control opcodes used here:

    SetGain      0x0002
    GetGain      0x4002
    SetMute      0x0003
    GetMute      0x4003
    SetPowerMode 0x0008
    GetPowerMode 0x4007
    SetChannel   0x000A
    GetChannel   0x4008

This first implementation deliberately keeps the SSP payload codec small and
explicit. Additional SR-H1000 functions can be added without changing the
receiver/MQTT contracts.
"""

from __future__ import annotations

import struct
import threading

from ..models import (
    CommandResult,
    MuteState,
    PowerState,
    ReceiverCapabilities,
    SourceCapabilities,
)
from ..protocol.ssp import SSPFrame, SSPLink
from .base import BaseReceiverDriver


SR_H1000_CAPABILITIES = ReceiverCapabilities(
    model="SR-H1000",
    protocol="SSP",
    zones={},
    sources={
        "sirius": SourceCapabilities(
            power=True, mute=True, volume=True, channel=True, metadata=True,
            extras=frozenset({"category", "signal_quality"})
        )
    },
)


class SRH1000Driver(BaseReceiverDriver):
    """Semantic SR-H1000 driver."""

    READY_TIMEOUT = 45.0

    def __init__(self, transport, event_sink=None) -> None:
        super().__init__(SR_H1000_CAPABILITIES, event_sink)
        self._module_ready = threading.Event()
        self.link = SSPLink(transport, event_sink=self._handle_ssp_frame)

    def start(self) -> None:
        self._module_ready.clear()
        self.link.start()
        self._set_connected(True, False)
        try:
            if not self._module_ready.wait(self.READY_TIMEOUT):
                raise TimeoutError("timed out waiting for SSP PutModuleReady status=0")
            self._state.ready = True
        except Exception:
            self.link.stop()
            self._set_connected(False)
            raise

    def stop(self) -> None:
        self.link.stop()
        self._module_ready.clear()
        self._set_connected(False)

    def _request(self, opcode: int, params: bytes = b"", *, response_opcode: int | None = None) -> bytes:
        payload = struct.pack(">H", opcode) + params
        return self.link.transact(payload, response_opcode=response_opcode)

    def _handle_ssp_frame(self, frame: SSPFrame) -> None:
        payload = frame.payload
        if len(payload) < 2:
            return
        opcode = struct.unpack(">H", payload[:2])[0]
        if opcode == 0x8000 and len(payload) >= 3:
            status = payload[2]
            if status == 0:
                self._module_ready.set()
                self._set_connected(True, True)
            elif status == 1:
                self._module_ready.clear()
                self._set_connected(True, False)
            return
        if opcode == 0x8004 and len(payload) >= 5:
            system, code, subcode = payload[2:5]
            # System 0 is tune status. The specification reports channel ID
            # in subcode when tune status reaches code=0 (tune complete).
            if system == 0 and code == 0:
                self._set_source_field("sirius", "channel", subcode)
            return
        # Other Put messages are deliberately left at the protocol layer for
        # future source metadata/status support.

    def set_power(self, zone: str, on: bool) -> CommandResult:
        raise ValueError("SR-H1000 power is a source-level control, not a zone control")

    def set_mute(self, zone: str, muted: bool) -> CommandResult:
        raise ValueError("SR-H1000 mute is a source-level control, not a zone control")

    def set_volume(self, zone: str, value: float) -> CommandResult:
        raise ValueError("SR-H1000 gain is a source-level control, not a zone control")

    def set_source(self, zone: str, source: str) -> CommandResult:
        raise NotImplementedError("SR-H1000 does not select itself as a zone source")

    def set_source_power(self, source: str, on: bool) -> CommandResult:
        self._require_sirius(source)
        mode = 3 if on else 0
        response = self._request(0x0008, bytes([mode]), response_opcode=0x2008)
        self._check_set_response(response, 0x2008)
        self._set_source_field(source, "power", PowerState.ON if on else PowerState.OFF)
        return CommandResult(True, "source power command accepted", True)

    def set_source_mute(self, source: str, muted: bool) -> CommandResult:
        self._require_sirius(source)
        response = self._request(0x0003, bytes([1 if muted else 0]), response_opcode=0x2003)
        self._check_set_response(response, 0x2003)
        self._set_source_field(
            source, "mute", MuteState.MUTED if muted else MuteState.UNMUTED
        )
        return CommandResult(True, "source mute command accepted", True)

    def set_source_volume(self, source: str, value: float) -> CommandResult:
        self._require_sirius(source)
        if not -76 <= value <= 5:
            raise ValueError("SR-H1000 SSP gain range is -76..+5 dB for G3")
        response = self._request(0x0002, struct.pack(">b", int(value)), response_opcode=0x2002)
        self._check_set_response(response, 0x2002)
        self._set_source_field(source, "volume", value)
        return CommandResult(True, "source gain command accepted", True)

    def set_source_channel(self, source: str, channel: int) -> CommandResult:
        self._require_sirius(source)
        if not 0 <= channel <= 255:
            raise ValueError("SSP channel identifier must be 0..255")
        response = self._request(0x000A, bytes([channel, 0]), response_opcode=0x200A)
        self._check_set_response(response, 0x200A)
        self._set_source_field(source, "channel", channel)
        return CommandResult(True, "source channel command accepted", True)

    def query_all(self) -> None:
        for opcode, expected, field in (
            (0x4007, 0x6007, "power"),
            (0x4003, 0x6003, "mute"),
            (0x4002, 0x6002, "volume"),
            (0x4008, 0x6008, "channel"),
        ):
            response = self._request(opcode, response_opcode=expected)
            if len(response) < 4:
                raise ValueError("short SSP response")
            actual = struct.unpack(">H", response[:2])[0]
            if actual != expected:
                raise ValueError(f"unexpected SSP response opcode 0x{actual:04x}")

            err = struct.unpack(">H", response[2:4])[0]
            if err:
                raise ValueError(f"SSP command failed with error 0x{err:04x}")

            if field == "power" and len(response) >= 5:
                mode = response[4]
                self._set_source_field("sirius", "power",
                                       PowerState.OFF if mode == 0 else PowerState.ON)
            elif field == "mute" and len(response) >= 5:
                self._set_source_field("sirius", "mute",
                                       MuteState.MUTED if response[4] else MuteState.UNMUTED)
            elif field == "volume" and len(response) >= 5:
                self._set_source_field("sirius", "volume", struct.unpack(">b", response[4:5])[0])
            elif field == "channel" and len(response) >= 5:
                self._set_source_field("sirius", "channel", response[4])

    @staticmethod
    def _check_set_response(response: bytes, expected_opcode: int) -> None:
        if len(response) < 4:
            raise ValueError("short SSP set response")
        opcode, err = struct.unpack(">HH", response[:4])
        if opcode != expected_opcode:
            raise ValueError(
                f"unexpected SSP response opcode 0x{opcode:04x}; "
                f"expected 0x{expected_opcode:04x}"
            )
        if err:
            raise ValueError(f"SSP command failed with error 0x{err:04x}")

    @staticmethod
    def _require_sirius(source: str) -> None:
        if source != "sirius":
            raise ValueError(f"unsupported SR-H1000 source: {source}")
