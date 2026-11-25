"""Concrete serial transport.

The rest of the application receives a byte-stream abstraction rather than a
pyserial object. This makes protocol tests independent of real hardware.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import serial

from .contracts import SerialTransport


@dataclass(frozen=True)
class SerialConfig:
    device: str
    baudrate: int
    timeout: float = 0.05
    write_timeout: float = 1.0


class PySerialTransport(SerialTransport):
    """Thread-safe pyserial-backed byte-stream transport."""

    def __init__(self, config: SerialConfig) -> None:
        self.config = config
        self._serial: serial.Serial | None = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return
            self._serial = serial.Serial(
                port=self.config.device,
                baudrate=self.config.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.config.timeout,
                write_timeout=self.config.write_timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                self._serial.close()
                self._serial = None

    def write(self, data: bytes) -> None:
        with self._lock:
            if not self.is_open:
                raise RuntimeError("serial transport is not open")
            assert self._serial is not None
            self._serial.write(data)
            self._serial.flush()

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            if not self.is_open:
                raise RuntimeError("serial transport is not open")
            assert self._serial is not None
            return self._serial.read(size)

    def read_until(self, terminator: bytes, size: int = 4096) -> bytes:
        """Read through a terminator.

        This uses a bounded manual loop rather than assuming every protocol
        has pyserial's exact framing behavior.
        """
        if not terminator:
            raise ValueError("terminator must not be empty")

        buf = bytearray()
        deadline = time.monotonic() + max(self.config.timeout, 0.01) * max(size, 1)

        while len(buf) < size:
            if time.monotonic() > deadline:
                break
            chunk = self.read(1)
            if not chunk:
                continue
            buf.extend(chunk)
            if buf.endswith(terminator):
                break

        return bytes(buf)
