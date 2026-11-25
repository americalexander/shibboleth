"""Sirius Standard Protocol (SSP) link layer.

The SSP specification defines a binary 57.6-kbps protocol with:

    SYNC PROTO MODID SEQ LINKOP LEN PAYLOAD SUM

The link layer uses ACK/NACK/NACKBUSY/NACKSUM operations and sequence
numbers. SUM is the two's-complement negative sum of the bytes from SYNC
through PAYLOAD before byte stuffing; after de-stuffing, the sum through SUM
must be zero modulo 256.

This module implements the reusable link-layer mechanics. Receiver-specific
SSP opcodes belong in the SR-H1000 driver rather than in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .base import ProtocolMessageError, ProtocolTimeout


SYNC = 0xA4


class LinkOp(IntEnum):
    MSG = 0x00
    ACK = 0x80
    NACK = 0x81
    NACKBUSY = 0x82
    NACKSUM = 0x83


@dataclass(frozen=True)
class SSPFrame:
    proto: int
    modid: int
    seq: int
    linkop: LinkOp
    payload: bytes = b""

    def encode(self) -> bytes:
        if not 0 <= self.proto <= 0xFF:
            raise ValueError("proto must fit in one byte")
        for name, value in (("modid", self.modid), ("seq", self.seq)):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"{name} must fit in one byte")
        if len(self.payload) > 255:
            raise ValueError("SSP payload cannot exceed 255 bytes")

        body = bytes([
            SYNC,
            self.proto,
            self.modid,
            self.seq,
            int(self.linkop),
            len(self.payload),
        ]) + self.payload

        checksum = (-sum(body)) & 0xFF
        raw = body + bytes([checksum])
        return byte_stuff(raw)


def byte_stuff(data: bytes) -> bytes:
    """Apply SSP byte stuffing.

    The SSP specification's reserved/stuffed byte rules should be kept in one
    place. The exact reserved-byte set is represented here explicitly rather
    than scattered through frame construction/parsing.
    """
    # SSP reserves SYNC as a framing byte. A literal SYNC in the body is
    # represented by a two-byte escape sequence. The implementation is kept
    # isolated because this is the one area where the exact revision of the
    # SSP spec matters most.
    out = bytearray()
    for b in data:
        if b == SYNC:
            out.extend((0xA3, 0x01))
        elif b == 0xA3:
            out.extend((0xA3, 0x00))
        else:
            out.append(b)
    return bytes(out)


def byte_destuff(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b != 0xA3:
            out.append(b)
            i += 1
            continue
        if i + 1 >= len(data):
            raise ProtocolMessageError("truncated SSP escape sequence")
        code = data[i + 1]
        if code == 0x00:
            out.append(0xA3)
        elif code == 0x01:
            out.append(SYNC)
        else:
            raise ProtocolMessageError(f"invalid SSP escape code 0x{code:02x}")
        i += 2
    return bytes(out)


def parse_frame(data: bytes) -> SSPFrame:
    """Parse one complete, de-stuffed SSP frame."""
    raw = byte_destuff(data)
    if len(raw) < 7:
        raise ProtocolMessageError("SSP frame too short")
    if raw[0] != SYNC:
        raise ProtocolMessageError("invalid SSP SYNC")

    length = raw[5]
    expected = 7 + length
    if len(raw) != expected:
        raise ProtocolMessageError(
            f"SSP length mismatch: expected {expected}, got {len(raw)}"
        )
    if sum(raw) & 0xFF:
        raise ProtocolMessageError("SSP checksum failure")

    return SSPFrame(
        proto=raw[1],
        modid=raw[2],
        seq=raw[3],
        linkop=LinkOp(raw[4]),
        payload=raw[6:-1],
    )


class SSPLink:
    """Small synchronous SSP link-layer implementation.

    A production integration should put this behind a single serialized
    transaction queue because SSP permits one master controller.
    """

    def __init__(self, transport, *, proto: int = 0, modid: int = 0) -> None:
        self.transport = transport
        self.proto = proto
        self.modid = modid
        self._seq = 0

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def send(self, payload: bytes) -> int:
        seq = self.next_seq()
        frame = SSPFrame(
            proto=self.proto,
            modid=self.modid,
            seq=seq,
            linkop=LinkOp.MSG,
            payload=payload,
        )
        self.transport.write(frame.encode())
        return seq

    def build_ack(self, seq: int) -> bytes:
        return SSPFrame(
            proto=self.proto,
            modid=self.modid,
            seq=seq,
            linkop=LinkOp.ACK,
        ).encode()
