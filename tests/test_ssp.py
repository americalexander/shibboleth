from receiverctl.protocol.ssp import LinkOp, SSPFrame, parse_frame


def test_ssp_round_trip():
    frame = SSPFrame(
        proto=0,
        modid=0,
        seq=7,
        linkop=LinkOp.MSG,
        payload=b"\\x00\\x08\\x03",
    )
    assert parse_frame(frame.encode()) == frame


def test_ack_frame():
    frame = SSPFrame(proto=0, modid=0, seq=9, linkop=LinkOp.ACK)
    assert parse_frame(frame.encode()) == frame
