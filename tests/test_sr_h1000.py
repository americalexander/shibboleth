from receiverctl.models import MuteState, PowerState
from receiverctl.receiver.sr_h1000 import SRH1000Driver


class DummyTransport:
    def open(self):
        pass

    def close(self):
        pass

    @property
    def is_open(self):
        return True

    def write(self, data):
        pass

    def read(self, size=1):
        raise TimeoutError

    def read_until(self, terminator, size=4096):
        raise TimeoutError


def test_sr_h1000_is_an_independent_sirius_source():
    driver = SRH1000Driver(DummyTransport())

    assert driver.capabilities.zones == {}
    assert "sirius" in driver.capabilities.sources
    assert "sirius" in driver.state.sources
    assert driver.state.zones == {}


def test_sr_h1000_source_state_is_not_zone_state():
    driver = SRH1000Driver(DummyTransport())

    driver._set_source_field("sirius", "power", PowerState.ON)
    driver._set_source_field("sirius", "mute", MuteState.MUTED)
    driver._set_source_field("sirius", "volume", -12)
    driver._set_source_field("sirius", "channel", 42)

    state = driver.state
    assert state.sources["sirius"].power == PowerState.ON
    assert state.sources["sirius"].mute == MuteState.MUTED
    assert state.sources["sirius"].volume == -12
    assert state.sources["sirius"].channel == 42
    assert state.zones == {}
