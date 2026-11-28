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

from ..models import (
    CommandResult,
    MuteState,
    PowerState,
    ReceiverCapabilities,
    SourceCapabilities,
)
from ..protocol.ssp import LinkOp, SSPFrame, SSPLink, parse_frame
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

    def __init__(self, transport, event_sink=None) -> None:
        super().__init__(SR_H1000_CAPABILITIES, event_sink)
        self.link = SSPLink(transport)

    def start(self) -> None:
        self.link.transport.open()
        self._set_connected(True)

    def stop(self) -> None:
        self.link.transport.close()
        self._set_connected(False)

    def _request(self, opcode: int, params: bytes = b"") -> bytes:
        payload = struct.pack(">H", opcode) + params
        seq = self.link.send(payload)

        # A complete SSP implementation should use a dedicated reader/queue
        # and handle ACKs and asynchronous Put messages independently. This
        # synchronous helper is deliberately the first concrete layer.
        raw = self.link.transport.read(1)
        if raw != bytes([0xA4]):
            raise TimeoutError(f"expected SSP SYNC, got {raw!r}")

        # Read the fixed header through LEN.
        header = bytearray(raw)
        while len(header) < 6:
            chunk = self.link.transport.read(1)
            if not chunk:
                raise TimeoutError("timed out reading SSP header")
            header.extend(chunk)

        length = header[5]
        body = bytearray(header)
        while len(body) < 7 + length:
            chunk = self.link.transport.read(1)
            if not chunk:
                raise TimeoutError("timed out reading SSP payload")
            body.extend(chunk)

        frame = parse_frame(bytes(body))
        if frame.seq != seq:
            raise ValueError(f"SSP sequence mismatch: expected {seq}, got {frame.seq}")
        if frame.linkop not in {LinkOp.MSG, LinkOp.ACK}:
            raise ValueError(f"unexpected SSP link operation: {frame.linkop!r}")
        return frame.payload

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
        response = self._request(0x0008, bytes([mode]))
        self._check_set_response(response, 0x2008)
        self._set_source_field(source, "power", PowerState.ON if on else PowerState.OFF)
        return CommandResult(True, "source power command accepted", True)

    def set_source_mute(self, source: str, muted: bool) -> CommandResult:
        self._require_sirius(source)
        response = self._request(0x0003, bytes([1 if muted else 0]))
        self._check_set_response(response, 0x2003)
        self._set_source_field(
            source, "mute", MuteState.MUTED if muted else MuteState.UNMUTED
        )
        return CommandResult(True, "source mute command accepted", True)

    def set_source_volume(self, source: str, value: float) -> CommandResult:
        self._require_sirius(source)
        if not -76 <= value <= 5:
            raise ValueError("SR-H1000 SSP gain range is -76..+5 dB for G3")
        response = self._request(0x0002, struct.pack(">b", int(value)))
        self._check_set_response(response, 0x2002)
        self._set_source_field(source, "volume", value)
        return CommandResult(True, "source gain command accepted", True)

    def set_source_channel(self, source: str, channel: int) -> CommandResult:
        self._require_sirius(source)
        if not 0 <= channel <= 255:
            raise ValueError("SSP channel identifier must be 0..255")
        response = self._request(0x000A, bytes([channel, 0]))
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
            response = self._request(opcode)
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
