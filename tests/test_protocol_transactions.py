import queue
import threading
import time

from receiverctl.protocol.iscp import ISCPMessage, ISCPProtocol
from receiverctl.protocol.ssp import LinkOp, SSPFrame, SSPLink, SSPStreamParser


class FakeTransport:
    def __init__(self, on_write=None):
        self._rx = queue.Queue()
        self._open = False
        self._on_write = on_write
        self.writes = []

    @property
    def is_open(self):
        return self._open

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def write(self, data):
        assert self._open
        self.writes.append(data)
        if self._on_write:
            self._on_write(data, self)

    def read(self, size=1):
        if not self._open:
            raise RuntimeError("closed")
        try:
            return self._rx.get(timeout=0.02)
        except queue.Empty:
            return b""

    def read_until(self, terminator, size=4096):
        raise NotImplementedError

    def inject(self, data):
        for b in data:
            self._rx.put(bytes([b]))


def test_ssp_stream_parser_uses_spec_escape_values():
    frame = SSPFrame(
        proto=0x03,
        modid=0x1B,
        seq=0xA4,
        linkop=LinkOp.MSG,
        payload=bytes([0xA4, 0x1B]),
    )
    wire = frame.encode()
    assert wire[0] == 0xA4
    assert b"\x1b\x53" in wire
    assert b"\x1b\x1b" in wire
    parser = SSPStreamParser()
    assert parser.feed(wire[:3]) == []
    assert parser.feed(wire[3:]) == [frame]


def test_ssp_transaction_handles_ack_response_and_put_event():
    events = []

    def on_write(data, transport):
        if data and data[0] == 0xA4:
            # Decode the outgoing frame with the public parser.
            from receiverctl.protocol.ssp import parse_frame
            outgoing = parse_frame(data)
            if outgoing.linkop == LinkOp.MSG:
                transport.inject(
                    SSPFrame(0x03, outgoing.modid, outgoing.seq, LinkOp.ACK).encode()
                )
                response = SSPFrame(
                    0x03,
                    outgoing.modid,
                    outgoing.seq,
                    LinkOp.MSG,
                    b"\x20\x02\x00\x00",
                )
                transport.inject(response.encode())

    transport = FakeTransport(on_write)
    link = SSPLink(transport, event_sink=events.append)
    link.start()
    try:
        payload = link.transact(b"\x00\x02\x00", response_opcode=0x2002)
        assert payload == b"\x20\x02\x00\x00"
        assert not events
    finally:
        link.stop()


def test_ssp_transaction_retries_after_timeout():
    attempts = []

    def on_write(data, transport):
        from receiverctl.protocol.ssp import parse_frame
        frame = parse_frame(data)
        if frame.linkop != LinkOp.MSG:
            return
        attempts.append(frame.seq)
        if len(attempts) == 2:
            transport.inject(SSPFrame(0x03, frame.modid, frame.seq, LinkOp.ACK).encode())
            transport.inject(
                SSPFrame(0x03, frame.modid, frame.seq, LinkOp.MSG, b"\x20\x02\x00\x00").encode()
            )

    transport = FakeTransport(on_write)
    link = SSPLink(transport)
    link.start()
    try:
        assert link.transact(b"\x00\x02\x00", response_opcode=0x2002) == b"\x20\x02\x00\x00"
        assert attempts == [1, 1]
    finally:
        link.stop()


def test_iscp_transaction_dispatches_unrelated_notification():
    events = []

    def on_write(data, transport):
        transport.inject(b"!1SLI03\r")
        transport.inject(b"!1PWR01\r")

    transport = FakeTransport(on_write)
    protocol = ISCPProtocol(transport, event_sink=events.append)
    protocol.start()
    try:
        message = protocol.transact("PWR", "01")
        assert message == ISCPMessage("PWR", "01")
        assert events == [ISCPMessage("SLI", "03")]
    finally:
        protocol.stop()


def test_tx_sr706_command_updates_state_only_from_response():
    from receiverctl.receiver.tx_sr706 import TXSR706Driver

    def on_write(data, transport):
        transport.inject(data)

    transport = FakeTransport(on_write)
    driver = TXSR706Driver(transport)
    driver.start()
    try:
        result = driver.set_volume("main", 50)
        assert result.accepted is True
        assert driver.state.zones["main"].volume == 50
    finally:
        driver.stop()


def test_sr_h1000_waits_for_module_ready_and_handles_command_response():
    from receiverctl.receiver.sr_h1000 import SRH1000Driver
    from receiverctl.protocol.ssp import parse_frame

    def on_write(data, transport):
        frame = parse_frame(data)
        if frame.linkop != LinkOp.MSG:
            return
        if frame.payload[:2] == b"\x00\x08":
            response_payload = b"\x20\x08\x00\x00"
        else:
            response_payload = b"\x20\x02\x00\x00"
        transport.inject(SSPFrame(0x03, frame.modid, frame.seq, LinkOp.ACK).encode())
        transport.inject(SSPFrame(0x03, frame.modid, frame.seq, LinkOp.MSG, response_payload).encode())

    transport = FakeTransport(on_write)
    original_open = transport.open
    def open_with_ready():
        original_open()
        transport.inject(SSPFrame(0x03, 0x00, 0x55, LinkOp.MSG, b"\x80\x00\x00").encode())
    transport.open = open_with_ready

    driver = SRH1000Driver(transport)
    driver.READY_TIMEOUT = 1.0
    driver.start()
    try:
        assert driver.state.connected is True
        assert driver.state.ready is True
        result = driver.set_source_power("sirius", True)
        assert result.accepted is True
        assert driver.state.sources["sirius"].power.value == "on"
    finally:
        driver.stop()
