"""Decode captured CP2102 packets using the recovered vendor receive format.

Offline only. A digital IO bit is not a voltage, relay feedback, or servo angle.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def decode_stream(data):
    frames = []
    rejected = 0
    skipped = 0
    offset = 0
    while offset < len(data):
        if data[offset] != 0xA5:
            skipped += 1
            offset += 1
            continue
        if len(data)-offset < 5:
            break
        payload_length = int.from_bytes(data[offset+3:offset+5], 'little')
        if not 2 <= payload_length <= 100:
            rejected += 1
            offset += 1
            continue
        size = payload_length + 8
        if len(data)-offset < size:
            break
        packet = data[offset:offset+size]
        if sum(packet) & 255:
            rejected += 1
            offset += 1
            continue
        f = {'offset':offset, 'size':size, 'hex':packet.hex(),
             'sequence_bytes':packet[5:7].hex(), 'function_byte':packet[7]}
        # handlePacketByType dispatches length 12 to handleTouchAndRemotePacket.
        if size == 12:
            f['remote_raw'] = packet[8]
            f['io_raw'] = packet[9]
            f['io_bits'] = {f'io{i+1}':bool(packet[9] & (1 << i)) for i in range(8)}
            f['extra_byte_10_raw'] = packet[10]
        frames.append(f)
        offset += size
    return {'frames':frames, 'valid_serial_bytes':sum(f['size'] for f in frames),
            'skipped_bytes':skipped, 'rejected_candidates':rejected,
            'trailing_hex':data[offset:].hex()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    source = json.loads(args.capture.read_text(encoding='utf-8'))
    result = decode_stream(bytes.fromhex(source['rx_hex']))
    result['source'] = args.capture.name
    result['frame_counts_by_size'] = dict(Counter(f['size'] for f in result['frames']))
    result['io_counts'] = dict(Counter(f['io_raw'] for f in result['frames'] if 'io_raw' in f))
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'frames'}))


if __name__ == '__main__':
    main()
