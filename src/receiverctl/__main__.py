"""Minimal command-line entry point.

Configuration parsing is intentionally kept small for the first revision.
The application can later consume YAML/TOML without changing any contract.
"""

from __future__ import annotations

import argparse
import time

from .app import AppConfig, ReceiverApplication
from .mqtt import MQTTConfig
from .transport import SerialConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receiver", choices=["sr_h1000", "tx_sr706"], required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--baud", type=int, default=None)
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    args = parser.parse_args()

    baud = args.baud or (57600 if args.receiver == "sr_h1000" else 9600)

    app = ReceiverApplication(
        AppConfig(
            receiver_type=args.receiver,
            serial=SerialConfig(args.serial, baudrate=baud),
            mqtt=MQTTConfig(args.mqtt_host, args.mqtt_port),
        )
    )

    app.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()
