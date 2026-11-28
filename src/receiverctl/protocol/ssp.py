"""Sirius Standard Protocol (SSP) link and transaction layer.

SSP is a binary 57.6-kbps protocol with:

    SYNC PROTO MODID SEQ LINKOP LEN PAYLOAD SUM

The implementation below keeps the wire framing, reliable link transaction,
and asynchronous Put-message handling together, while leaving SSP payload
opcodes to the SR-H1000 driver.
"""

from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable

from ..contracts import SerialTransport
from .base import (
    PendingTransaction,
    ProtocolError,
    ProtocolEvent,
    ProtocolMessageError,
    ProtocolTimeout,
    StreamProtocol,
    TransactionRegistry,
)


SYNC = 0xA4
ESC = 0x1B


class LinkOp(IntEnum):
    MSG = 0x00
    ACK = 0x80
    NACK = 0x81  # Reserved by the current SSP spec; retained for robustness.
    NACKBUSY = 0x82
    NACKSUM = 0x83


@dataclass(frozen=True)
class SSPFrame:
    proto: int
    modid: int
    seq: int
    linkop: LinkOp
    payload: bytes = b""
    checksum_valid: bool = True

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
        return byte_stuff(body + bytes([checksum]))


def byte_stuff(data: bytes) -> bytes:
    """Apply SSP byte stuffing to fields after SYNC."""
    if not data or data[0] != SYNC:
        raise ValueError("SSP encoded data must begin with SYNC")
    out = bytearray([SYNC])
    for b in data[1:]:
        if b == SYNC:
            out.extend((ESC, 0x53))
        elif b == ESC:
            out.extend((ESC, ESC))
        else:
            out.append(b)
    return bytes(out)


def byte_destuff(data: bytes) -> bytes:
    """Destuff one complete SSP frame."""
    if not data or data[0] != SYNC:
        raise ProtocolMessageError("invalid SSP SYNC")
    out = bytearray([SYNC])
    i = 1
    while i < len(data):
        b = data[i]
        if b != ESC:
            if b == SYNC:
                raise ProtocolMessageError("unexpected SSP SYNC inside frame")
            out.append(b)
            i += 1
            continue
        if i + 1 >= len(data):
            raise ProtocolMessageError("truncated SSP escape sequence")
        code = data[i + 1]
        if code == 0x53:
            out.append(SYNC)
        elif code == ESC:
            out.append(ESC)
        else:
            raise ProtocolMessageError(f"invalid SSP escape code 0x{code:02x}")
        i += 2
    return bytes(out)


def _decode_frame(data: bytes, *, allow_bad_checksum: bool = False) -> SSPFrame:
    raw = byte_destuff(data)
    if len(raw) < 7:
        raise ProtocolMessageError("SSP frame too short")
    length = raw[5]
    expected = 7 + length
    if len(raw) != expected:
        raise ProtocolMessageError(
            f"SSP length mismatch: expected {expected}, got {len(raw)}"
        )
    checksum_valid = not (sum(raw) & 0xFF)
    if not checksum_valid and not allow_bad_checksum:
        raise ProtocolMessageError("SSP checksum failure")
    try:
        linkop = LinkOp(raw[4])
    except ValueError as exc:
        raise ProtocolMessageError(f"unknown SSP link operation 0x{raw[4]:02x}") from exc
    return SSPFrame(
        proto=raw[1],
        modid=raw[2],
        seq=raw[3],
        linkop=linkop,
        payload=raw[6:-1],
        checksum_valid=checksum_valid,
    )


def parse_frame(data: bytes) -> SSPFrame:
    """Parse one complete, wire-encoded SSP frame."""
    return _decode_frame(data)


class SSPStreamParser:
    """Incremental SSP parser that resynchronizes on every literal SYNC."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._escape = False

    def feed(self, data: bytes) -> list[SSPFrame]:
        frames: list[SSPFrame] = []
        for b in data:
            if not self._buffer:
                if b == SYNC:
                    self._buffer.append(b)
                continue

            # A literal SYNC always begins a new frame, even if the previous
            # frame was incomplete, per SSP's stream resynchronization rule.
            if b == SYNC:
                self._buffer = bytearray([SYNC])
                self._escape = False
                continue

            self._buffer.append(b)
            if self._escape:
                if b not in (ESC, 0x53):
                    self._buffer.clear()
                    self._escape = False
                    continue
                self._escape = False
            elif b == ESC:
                self._escape = True

            # Decode enough of the stream to know the unstuffed payload length.
            try:
                candidate = byte_destuff(bytes(self._buffer))
            except ProtocolMessageError:
                # Incomplete escape sequences are normal. Other invalid escape
                # sequences are discarded and the next SYNC will resynchronize.
                if self._escape:
                    continue
                self._buffer.clear()
                continue

            if len(candidate) >= 6:
                expected = 7 + candidate[5]
                if len(candidate) == expected:
                    try:
                        frames.append(_decode_frame(bytes(self._buffer), allow_bad_checksum=True))
                    except ProtocolMessageError:
                        # Bad structural frame: discard and wait for the next
                        # SYNC rather than poisoning the stream.
                        pass
                    self._buffer.clear()
                    self._escape = False
                elif len(candidate) > expected:
                    self._buffer.clear()
                    self._escape = False
        return frames


class SSPLink(StreamProtocol):
    """Reliable SSP link with serialized request/response transactions."""

    ACK_TIMEOUT = 0.100
    RESPONSE_TIMEOUT = 0.100
    MAX_RETRANSMISSIONS = 3
    BUSY_RETRY_DELAY = 0.100

    def __init__(
        self,
        transport: SerialTransport,
        *,
        proto: int = 0x03,
        modid: int = 0x00,
        event_sink: Callable[[SSPFrame], None] | None = None,
    ) -> None:
        super().__init__(transport, self._protocol_event)
        self.proto = proto
        self.modid = modid
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._tx_lock = threading.Lock()
        self._pending: TransactionRegistry[int, SSPFrame] = TransactionRegistry()
        self._link_pending_registry: TransactionRegistry[tuple[int, str], SSPFrame] = TransactionRegistry()
        self._parser = SSPStreamParser()
        self._event_sink = event_sink
        self._recent_incoming: dict[tuple[int, int], tuple[bytes, float]] = {}

    def next_seq(self) -> int:
        with self._seq_lock:
            self._seq = (self._seq + 1) & 0xFF
            return self._seq

    def send(self, payload: bytes) -> int:
        """Compatibility helper: send one MSG without waiting for a response."""
        seq = self.next_seq()
        frame = SSPFrame(self.proto, self.modid, seq, LinkOp.MSG, payload)
        self.transport.write(frame.encode())
        return seq

    def transact(
        self,
        payload: bytes,
        *,
        response_opcode: int | None = None,
        timeout: float = RESPONSE_TIMEOUT,
    ) -> bytes:
        """Send one SSP MSG and wait for its link ACK and protocol response.

        SSP permits one master controller and defines reliable link-layer
        retransmission.  Requests are therefore serialized.  A NACKSUM causes
        immediate retransmission; NACKBUSY causes a 100-ms delay before retry.
        Once an ACK has been received, the link transaction is complete and
        only the higher-level response is awaited.
        """
        with self._tx_lock:
            seq = self.next_seq()
            frame = SSPFrame(self.proto, self.modid, seq, LinkOp.MSG, payload)
            transaction = self._pending.create(seq)
            try:
                for attempt in range(self.MAX_RETRANSMISSIONS + 1):
                    link_transaction = self._link_pending_registry.create((seq, "link"))
                    self.transport.write(frame.encode())
                    try:
                        link_frame = link_transaction.wait(self.ACK_TIMEOUT)
                    except ProtocolTimeout:
                        if attempt >= self.MAX_RETRANSMISSIONS:
                            raise
                        continue
                    finally:
                        self._link_pending_registry.pop((seq, "link"))

                    if link_frame.linkop == LinkOp.ACK:
                        break
                    if link_frame.linkop == LinkOp.NACKBUSY:
                        if attempt >= self.MAX_RETRANSMISSIONS:
                            raise ProtocolError("SSP peer remained busy after retries")
                        time.sleep(self.BUSY_RETRY_DELAY)
                        continue
                    if link_frame.linkop == LinkOp.NACKSUM:
                        if attempt >= self.MAX_RETRANSMISSIONS:
                            raise ProtocolError("SSP checksum rejected after retries")
                        continue
                    raise ProtocolError(f"unexpected SSP link response: {link_frame.linkop!r}")
                else:
                    raise ProtocolTimeout("SSP link transaction timed out")

                response = transaction.wait(timeout)
                if response_opcode is not None:
                    if len(response.payload) < 2:
                        raise ProtocolMessageError("short SSP response payload")
                    actual = struct.unpack(">H", response.payload[:2])[0]
                    if actual != response_opcode:
                        raise ProtocolMessageError(
                            f"unexpected SSP response opcode 0x{actual:04x}; "
                            f"expected 0x{response_opcode:04x}"
                        )
                return response.payload
            finally:
                self._pending.pop(seq)

    def build_ack(self, seq: int, *, modid: int | None = None) -> bytes:
        return SSPFrame(
            proto=self.proto,
            modid=self.modid if modid is None else modid,
            seq=seq,
            linkop=LinkOp.ACK,
        ).encode()

    def _feed(self, data: bytes) -> None:
        for frame in self._parser.feed(data):
            if frame.proto != self.proto:
                continue
            if frame.linkop in {LinkOp.ACK, LinkOp.NACKBUSY, LinkOp.NACKSUM, LinkOp.NACK}:
                self._link_pending_registry.complete((frame.seq, "link"), frame)
                continue
            if frame.linkop != LinkOp.MSG:
                continue

            if not frame.checksum_valid:
                try:
                    self.transport.write(
                        SSPFrame(self.proto, frame.modid, frame.seq, LinkOp.NACKSUM).encode()
                    )
                except Exception:
                    return
                continue

            # Every valid received MSG is acknowledged at the link layer.
            # A retransmitted MSG (normally caused by a lost ACK) must be
            # acknowledged again but must not be processed twice.
            try:
                self.transport.write(
                    SSPFrame(self.proto, frame.modid, frame.seq, LinkOp.ACK).encode()
                )
            except Exception:
                # A lost ACK will cause the peer to retransmit; there is no
                # useful local recovery to perform here.
                return

            key = (frame.modid, frame.seq)
            now = time.monotonic()
            previous = self._recent_incoming.get(key)
            if previous is not None and previous[0] == frame.payload and now - previous[1] < 1.0:
                continue
            self._recent_incoming[key] = (frame.payload, now)
            if len(self._recent_incoming) > 64:
                cutoff = now - 1.0
                self._recent_incoming = {
                    k: v for k, v in self._recent_incoming.items() if v[1] >= cutoff
                }

            if frame.payload and _is_put(frame.payload):
                self._emit_event(ProtocolEvent(message=frame, raw=frame.payload))
            elif self._pending.complete(frame.seq, frame):
                pass
            else:
                self._emit_event(ProtocolEvent(message=frame, raw=frame.payload))

    def _protocol_event(self, event: ProtocolEvent) -> None:
        if self._event_sink is not None and isinstance(event.message, SSPFrame):
            self._event_sink(event.message)

    def _fail_pending(self, error: BaseException) -> None:
        self._pending.fail_all(error)
        self._link_pending_registry.fail_all(error)


def _is_put(payload: bytes) -> bool:
    return len(payload) >= 2 and bool(struct.unpack(">H", payload[:2])[0] & 0x8000)
