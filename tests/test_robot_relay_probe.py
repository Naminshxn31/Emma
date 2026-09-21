import pytest
from scripts.probe_robot_arm_relay import RELAY_ON, RELAY_OFF, require_released_io


def frame(io):
    data = bytes([0xa5,1,0x2a,4,0,0,0,9,0,io,2])
    return data+bytes([-sum(data)&255])


def test_any_pressed_sample_prevents_power_test():
    with pytest.raises(RuntimeError):
        require_released_io(frame(0)*25+frame(64))


def test_no_valid_status_prevents_power_test():
    with pytest.raises(RuntimeError):
        require_released_io(b'READER_READY\n')
    assert require_released_io(frame(0)*25) == 25


def test_relay_frames_match_recovered_active_low_payload():
    assert RELAY_ON[8] == 0 and RELAY_OFF[8] == 1
    assert len(RELAY_ON) == len(RELAY_OFF) == 10
    assert sum(RELAY_ON) % 256 == sum(RELAY_OFF) % 256 == 0
