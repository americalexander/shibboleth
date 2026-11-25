"""Onkyo/Integra Serial Control Protocol (ISCP) over RS-232C.

The workbook specifies ISCP over RS-232C as:

    9600 baud, 8 data bits, 1 stop bit, no parity, no flow control

An RS-232 message begins with `!`, followed by a unit type (`1` for a
receiver), three command characters, and a variable-length parameter.
RS-232 messages terminate with CR, LF, or CR/LF.

The workbook's communication model distinguishes:
* command messages,
* question messages using the QSTN parameter, and
* unsolicited status/event notifications.

This implementation keeps those distinctions explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..protocol.base import ProtocolMessageError


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

    def __init__(self, terminators: tuple[bytes, ...] = (CR, LF, b"\\r\\n")) -> None:
        self.terminators = tuple(sorted(terminators, key=len, reverse=True))
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[ISCPMessage]:
        self._buffer.extend(data)
        messages: list[ISCPMessage] = []

        while True:
            start = self._buffer.find(b"!")
            if start < 0:
                # Keep a partial start marker impossible to miss; there is
                # only one byte in the marker, so the whole buffer can go.
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
        # `raw` is the message without its terminating character.
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolMessageError("ISCP message is not ASCII") from exc

        if len(text) < 5 or text[0] != "!":
            raise ProtocolMessageError(f"invalid ISCP message: {raw!r}")

        unit_type = text[1]
        command = text[2:5]
        parameter = text[5:]
        return ISCPMessage(command=command, parameter=parameter, unit_type=unit_type)


class ISCPProtocol:
    """Synchronous ISCP request/response helper.

    The driver is responsible for serialization of requests and for deciding
    whether a received status message is the response to a particular
    request. The parser is intentionally usable independently for unsolicited
    events.
    """

    def __init__(
        self,
        transport,
        *,
        tx_terminator: bytes = CR,
        response_terminators: tuple[bytes, ...] = (CR, LF, b"\r\n"),
    ) -> None:
        self.transport = transport
        self.tx_terminator = tx_terminator
        self.parser = ISCPParser(response_terminators)

    def encode(self, command: str, parameter: str = "", unit_type: str = "1") -> bytes:
        return ISCPMessage(command, parameter, unit_type).encode(self.tx_terminator)

    def send(self, command: str, parameter: str = "") -> None:
        self.transport.write(self.encode(command, parameter))

    def read_message(self, timeout_reads: int = 100) -> ISCPMessage:
        """Read until a complete message is available.

        The transport timeout controls the underlying read cadence. The
        bounded loop prevents a permanently dead receiver from blocking
        forever.
        """
        for _ in range(timeout_reads):
            data = self.transport.read(1)
            if not data:
                continue
            messages = self.parser.feed(data)
            if messages:
                return messages[0]
        raise TimeoutError("timed out waiting for ISCP response")

    def transact(self, command: str, parameter: str = "") -> ISCPMessage:
        self.send(command, parameter)
        return self.read_message()
