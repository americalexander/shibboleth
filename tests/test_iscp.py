from receiverctl.protocol.iscp import CR, ISCPMessage, ISCPParser


def test_iscp_encoding():
    msg = ISCPMessage("PWR", "01")
    assert msg.encode(CR) == b"!1PWR01\r"


def test_incremental_iscp_parsing():
    parser = ISCPParser()
    assert parser.feed(b"!1PWR") == []
    assert parser.feed(b"01\r") == [ISCPMessage("PWR", "01")]


def test_two_messages_in_one_read():
    parser = ISCPParser()
    messages = parser.feed(b"!1PWR01\r!1AMT00\r")
    assert messages == [
        ISCPMessage("PWR", "01"),
        ISCPMessage("AMT", "00"),
    ]
