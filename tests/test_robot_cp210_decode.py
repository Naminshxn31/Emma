from scripts.decode_robot_cp210 import decode_stream


def packet(io):
    p = bytes([0xa5, 1, 0x2a, 4, 0, 0, 17, 9, 0, io, 2])
    return p + bytes([(-sum(p)) & 255])


def test_io7_bit_with_transport_prefix_and_multiple_packets():
    data = b'READER_READY\n' + packet(0) + packet(64)
    out = decode_stream(data)
    assert out['skipped_bytes'] == len(b'READER_READY\n')
    assert out['valid_serial_bytes'] == 24
    assert not out['frames'][0]['io_bits']['io7']
    assert out['frames'][1]['io_bits']['io7']
    assert sum(out['frames'][1]['io_bits'].values()) == 1


def test_corrupt_checksum_cannot_become_a_sensor_measurement():
    bad = bytearray(packet(64))
    bad[-1] ^= 1
    out = decode_stream(bytes(bad) + packet(0))
    assert len(out['frames']) == 1
    assert out['frames'][0]['io_raw'] == 0
    assert out['rejected_candidates'] == 1


def test_truncated_packet_is_retained_not_decoded():
    data = packet(64)[:-2]
    out = decode_stream(data)
    assert not out['frames']
    assert out['trailing_hex'] == data.hex()
