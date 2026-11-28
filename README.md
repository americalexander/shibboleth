# Receiver Serial Controller

A protocol-independent Python controller for RS-232 audio receivers.

The first supported devices are:

* Polk Audio SR-H1000, using Sirius Standard Protocol (SSP).
* Onkyo TX-SR706, using Onkyo/Integra Serial Control Protocol (ISCP).

The architectural rule is:

    MQTT -> normalized receiver contract -> device driver
         -> protocol implementation -> generic serial transport

MQTT and the normalized receiver state model do not contain SSP or ISCP
syntax.

## Current implementation status

The project deliberately starts with the contractual layers before adding
device-specific behavior.

Implemented:

* Generic serial transport contract and pyserial implementation.
* Normalized zone/state/capability data model, including independent source
  devices that may be selected by multiple zones.
* Receiver-driver contract.
* Protocol contract.
* ISCP stream parser and command builder.
* SSP frame builder/parser, checksum, byte stuffing, ACK/NACK frame support.
* TX-SR706 driver for the common Main and Zone 2 controls:
  power, mute, volume, source, tuner frequency, preset, and listening mode
  where the workbook documents those controls for the TX-SR706.
* SR-H1000 driver for the SSP power, mute, gain, and channel controls for
  which the SSP specification supplies the required opcodes. The SR-H1000 is
  modeled as the independent `sirius` source, not as a Main-zone device; the
  same source can therefore be selected by Main and Zone 2 independently on
  a receiver such as the TX-SR706.

The MQTT adapter is intentionally kept thin. It maps normalized commands and
state to MQTT topics; it does not know receiver-specific command strings.

## RS-232 profiles

The Onkyo workbook specifies:

    9600 8N1, no flow control

and a three-wire RS-232C connection.

The SSP specification specifies:

    57600 8N1

for the SSP serial interface.

Use a real RS-232 interface or a USB-to-RS-232 adapter. A Raspberry Pi UART
must not be connected directly to true RS-232 voltage levels.

## Configuration

A future application configuration can select exactly one receiver:

    receiver:
      type: tx_sr706

    serial:
      device: /dev/serial/by-id/usb-...
      baudrate: 9600

or:

    receiver:
      type: sr_h1000

    serial:
      device: /dev/serial/by-id/usb-...
      baudrate: 57600

The driver can also supply its protocol-default serial profile so that the
baud rate need not be duplicated in ordinary configurations.

## Design principle

Do not force ISCP to imitate SSP.

SSP has a binary link layer with framing, sequence numbers, checksums,
acknowledgements, and retransmission. ISCP is an ASCII command/status stream
with questions and unsolicited notifications. The common abstraction is the
receiver semantic API, not the wire protocol.
