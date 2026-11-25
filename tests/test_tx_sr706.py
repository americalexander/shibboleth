from receiverctl.receiver.tx_sr706 import TXSR706Driver


def test_tx_sr706_volume_encoding():
    assert TXSR706Driver._volume_parameter(0) == "00"
    assert TXSR706Driver._volume_parameter(100) == "64"


def test_tx_sr706_volume_decoding():
    assert TXSR706Driver._volume_value("00") == 0
    assert TXSR706Driver._volume_value("64") == 100
