"""Shared protocol transaction and event infrastructure.

The wire protocols supported by the project have very different framing and
correlation rules.  What they have in common is the runtime plumbing:

* one reader continuously consumes the serial stream;
* synchronous requests wait on a pending transaction rather than reading the
  serial port themselves;
* asynchronous protocol messages are dispatched independently of requests;
* stopping a protocol wakes all blocked requests and terminates the reader.

Protocol-specific modules supply the framing/parser and the rules for deciding
whether an incoming message completes a transaction or is an asynchronous
notification.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from ..contracts import SerialTransport


class ProtocolError(Exception):
    """Base exception for protocol errors."""


class ProtocolTimeout(ProtocolError):
    """Expected response was not received in time."""


class ProtocolMessageError(ProtocolError):
    """A received message is malformed or semantically invalid."""


T = TypeVar("T")
K = TypeVar("K")


class PendingTransaction(Generic[K, T]):
    """A single blocking transaction completed by the protocol reader."""

    def __init__(self, key: K) -> None:
        self.key = key
        self._condition = threading.Condition()
        self._result: T | None = None
        self._error: BaseException | None = None
        self._done = False

    def complete(self, result: T) -> None:
        with self._condition:
            if self._done:
                return
            self._result = result
            self._done = True
            self._condition.notify_all()

    def fail(self, error: BaseException) -> None:
        with self._condition:
            if self._done:
                return
            self._error = error
            self._done = True
            self._condition.notify_all()

    def wait(self, timeout: float | None = None) -> T:
        with self._condition:
            if not self._done:
                self._condition.wait(timeout)
            if not self._done:
                raise ProtocolTimeout("transaction timed out")
            if self._error is not None:
                raise self._error
            return self._result  # type: ignore[return-value]


class TransactionRegistry(Generic[K, T]):
    """Thread-safe registry for protocol transactions.

    SSP uses the wire sequence number as ``K``.  ISCP has no transaction ID,
    so its protocol implementation uses a single pending transaction key.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[K, PendingTransaction[K, T]] = {}

    def create(self, key: K) -> PendingTransaction[K, T]:
        with self._lock:
            if key in self._pending:
                raise ProtocolError(f"transaction key already pending: {key!r}")
            transaction = PendingTransaction[K, T](key)
            self._pending[key] = transaction
            return transaction

    def pop(self, key: K) -> PendingTransaction[K, T] | None:
        with self._lock:
            return self._pending.pop(key, None)

    def complete(self, key: K, result: T) -> bool:
        transaction = self.pop(key)
        if transaction is None:
            return False
        transaction.complete(result)
        return True

    def fail(self, key: K, error: BaseException) -> bool:
        transaction = self.pop(key)
        if transaction is None:
            return False
        transaction.fail(error)
        return True

    def fail_all(self, error: BaseException) -> None:
        with self._lock:
            transactions = list(self._pending.values())
            self._pending.clear()
        for transaction in transactions:
            transaction.fail(error)


@dataclass(frozen=True)
class ProtocolEvent:
    """Protocol-layer event delivered independently of a request response."""

    message: Any
    raw: Any = None


ProtocolEventSink = Callable[[ProtocolEvent], None]


class StreamProtocol(ABC):
    """Common lifecycle and reader-thread infrastructure for serial protocols."""

    def __init__(
        self,
        transport: SerialTransport,
        event_sink: ProtocolEventSink | None = None,
    ) -> None:
        self.transport = transport
        self.event_sink = event_sink
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._reader is not None and self._reader.is_alive():
                return
            self.transport.open()
            self._stop.clear()
            self._reader = threading.Thread(
                target=self._reader_loop,
                name=f"{self.__class__.__name__}-reader",
                daemon=True,
            )
            self._reader.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            self._fail_pending(ProtocolError("protocol stopped"))
            self.transport.close()
            reader = self._reader
            self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def _reader_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    data = self.transport.read(1)
                except Exception as exc:
                    if not self._stop.is_set():
                        self._fail_pending(ProtocolError("serial read failed"))
                        self._emit_event(ProtocolEvent(message=exc, raw=exc))
                    return
                if not data:
                    continue
                self._feed(data)
        finally:
            self._fail_pending(ProtocolError("protocol reader stopped"))

    def _emit_event(self, event: ProtocolEvent) -> None:
        if self.event_sink is not None:
            self.event_sink(event)

    @abstractmethod
    def _feed(self, data: bytes) -> None:
        """Consume one or more bytes from the transport."""
        raise NotImplementedError

    @abstractmethod
    def _fail_pending(self, error: BaseException) -> None:
        """Fail protocol-specific pending transactions."""
        raise NotImplementedError
