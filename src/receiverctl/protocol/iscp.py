"""Onkyo/Integra Serial Control Protocol (ISCP) over RS-232C.

The supplied TX-SR706 workbook specifies 9600 8N1, no flow control, with
messages beginning with ``!1`` and terminated by CR, LF, or CR/LF.  The
receiver responds to commands/questions with status messages and may also send
unsolicited notifications when state changes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Final

from ..contracts import SerialTransport
from .base import (
    ProtocolEvent,
    ProtocolMessageError,
    ProtocolTimeout,
    StreamProtocol,
    TransactionRegistry,
)


CR: Final[bytes] = b"\r"
LF: Final[bytes] = b"\n"
EOF: Final[bytes] = b"\x1a"


@dataclass(frozen=True)
class ISCPMessage:
    """One decoded ISCP message."""

    command: str
    parameter: str
    unit_type: str = "1"

    @property
    def is_question(self) -> bool:
        return self.parameter.upper() == "QSTN"

    def encode(self, terminator: bytes = CR) -> bytes:
        if len(self.command) != 3:
            raise ValueError("ISCP command must contain exactly three characters")
        if len(self.unit_type) != 1:
            raise ValueError("ISCP unit_type must contain exactly one character")
        return (
            b"!" + self.unit_type.encode("ascii")
            + self.command.encode("ascii")
            + self.parameter.encode("ascii")
            + terminator
        )


class ISCPParser:
    """Incremental parser for the RS-232 ISCP byte stream."""

    def __init__(self, terminators: tuple[bytes, ...] = (CR, LF, b"\r\n", EOF)) -> None:
        self.terminators = tuple(sorted(terminators, key=len, reverse=True))
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[ISCPMessage]:
        self._buffer.extend(data)
        messages: list[ISCPMessage] = []
        while True:
            start = self._buffer.find(b"!")
            if start < 0:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]

            found = self._find_terminator()
            if found is None:
                break
            end, term_len = found
            raw = bytes(self._buffer[:end])
            del self._buffer[: end + term_len]
            messages.append(self.decode(raw))
        return messages

    def _find_terminator(self) -> tuple[int, int] | None:
        candidates: list[tuple[int, int]] = []
        for terminator in self.terminators:
            position = self._buffer.find(terminator)
            if position >= 0:
                candidates.append((position, len(terminator)))
        return min(candidates) if candidates else None

    @staticmethod
    def decode(raw: bytes) -> ISCPMessage:
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolMessageError("ISCP message is not ASCII") from exc
        if len(text) < 5 or text[0] != "!":
            raise ProtocolMessageError(f"invalid ISCP message: {raw!r}")
        return ISCPMessage(
            command=text[2:5],
            parameter=text[5:],
            unit_type=text[1],
        )


class ISCPProtocol(StreamProtocol):
    """Continuous ISCP reader with serialized command transactions."""

    RESPONSE_TIMEOUT = 0.050

    def __init__(
        self,
        transport: SerialTransport,
        *,
        tx_terminator: bytes = CR,
        response_terminators: tuple[bytes, ...] = (CR, LF, b"\r\n", EOF),
        event_sink: Callable[[ISCPMessage], None] | None = None,
    ) -> None:
        super().__init__(transport, self._protocol_event)
        self.tx_terminator = tx_terminator
        self.parser = ISCPParser(response_terminators)
        self._event_sink = event_sink
        self._tx_lock = threading.Lock()
        self._pending: TransactionRegistry[str, ISCPMessage] = TransactionRegistry()
        self._pending_key = "request"
        self._expected_command: tuple[str, str] | None = None

    def encode(self, command: str, parameter: str = "", unit_type: str = "1") -> bytes:
        return ISCPMessage(command, parameter, unit_type).encode(self.tx_terminator)

    def send(self, command: str, parameter: str = "") -> None:
        """Send a command without waiting for its status response."""
        self.transport.write(self.encode(command, parameter))

    def transact(
        self,
        command: str,
        parameter: str = "",
        *,
        timeout: float = RESPONSE_TIMEOUT,
    ) -> ISCPMessage:
        """Send a command/question and wait for its matching status message.

        ISCP has no sequence number.  Requests are therefore serialized and
        the response is correlated by command/unit type. Unrelated status
        notifications received while waiting are dispatched to the event sink.
        """
        with self._tx_lock:
            transaction = self._pending.create(self._pending_key)
            self._expected_command = (command, "1")
            try:
                self.transport.write(self.encode(command, parameter))
                return transaction.wait(timeout)
            finally:
                self._expected_command = None
                self._pending.pop(self._pending_key)

    def _feed(self, data: bytes) -> None:
        for message in self.parser.feed(data):
            expected = self._expected_command
            if expected is not None and (message.command, message.unit_type) == expected:
                if self._pending.complete(self._pending_key, message):
                    continue
            self._emit_event(ProtocolEvent(message=message, raw=message))

    def _protocol_event(self, event: ProtocolEvent) -> None:
        if self._event_sink is not None and isinstance(event.message, ISCPMessage):
            self._event_sink(event.message)

    def _fail_pending(self, error: BaseException) -> None:
        self._pending.fail_all(error)
