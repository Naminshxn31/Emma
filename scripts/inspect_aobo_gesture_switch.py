"""Print Aobo getActionName switch targets from a local DEX. No robot access.

Resolve the class and method through DEX tables rather than guessing a file
offset. This is a narrow inspector for the recovered standard DEX format.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys


def inspect(path):
    data = path.read_bytes()
    if data[:8] not in (b'dex\n035\0', b'dex\n037\0', b'dex\n038\0', b'dex\n039\0'):
        raise ValueError('Expected a supported standard DEX file')

    def u32(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def leb(offset):
        value = 0
        for i in range(5):
            part = data[offset]
            offset += 1
            value |= (part & 127) << (7 * i)
            if not part & 128:
                return value, offset
        raise ValueError('Invalid ULEB128')

    def string(index):
        offset = u32(u32(60) + 4 * index)
        _, offset = leb(offset)
        return data[offset:data.index(b'\0', offset)].decode('utf-8')

    def type_name(index):
        return string(u32(u32(68) + 4 * index))

    matches = []
    owner = 'Lcom/aobo/aibot/services/IdleActionService;'
    for i in range(u32(96)):
        class_def = u32(100) + 32 * i
        if type_name(u32(class_def)) != owner:
            continue
        offset = u32(class_def + 24)
        if not offset:
            raise ValueError('Class has no class_data')
        counts = []
        for _ in range(4):
            count, offset = leb(offset)
            counts.append(count)
        for count in counts[:2]:
            for _ in range(count):
                _, offset = leb(offset)
                _, offset = leb(offset)
        for count in counts[2:]:
            method_index = 0
            for _ in range(count):
                delta, offset = leb(offset)
                method_index += delta
                _, offset = leb(offset)
                code_offset, offset = leb(offset)
                method_id = u32(92) + 8 * method_index
                if string(u32(method_id + 4)) != 'getActionName':
                    continue
                proto_index = struct.unpack_from('<H', data, method_id + 2)[0]
                proto = u32(76) + 12 * proto_index
                params = u32(proto + 8)
                if (type_name(u32(proto + 4)) == 'Ljava/lang/String;'
                        and params and u32(params) == 1
                        and type_name(struct.unpack_from('<H', data, params + 4)[0]) == 'I'):
                    matches.append(code_offset)
    if len(matches) != 1 or not matches[0]:
        raise ValueError('Expected one getActionName(int) code_item')
    code = matches[0]
    start = code + 16
    instruction_end = start + u32(code + 12) * 2
    if instruction_end > len(data) or data[start] != 0x2b:
        raise ValueError('Expected packed-switch at start of this method')
    payload_delta = struct.unpack_from('<i', data, start + 2)[0]
    payload = start + payload_delta * 2
    ident, count, first = struct.unpack_from('<HHi', data, payload)
    if ident != 0x100 or payload + 8 + count * 4 > instruction_end:
        raise ValueError('Invalid packed-switch payload')
    rows = []
    for i in range(count):
        entry = payload + 8 + 4 * i
        delta = struct.unpack_from('<i', data, entry)[0]
        target = start + delta * 2
        if not start <= target < instruction_end:
            raise ValueError('Switch target outside method instructions')
        opcode = data[target]
        if opcode not in (0x1a, 0x1b):
            raise ValueError('Target does not directly load a string')
        size = 6 if opcode == 0x1b else 4
        string_id = struct.unpack_from('<I' if size == 6 else '<H', data, target + 2)[0]
        if data[target + size] != 0x11 or data[target + size + 1] != data[target + 1]:
            raise ValueError('Expected return-object of the loaded string register')
        rows.append({
            'key': first + i,
            'target_delta_code_units': delta,
            'target_byte_offset_in_method': delta * 2,
            'target_file_offset_hex': hex(target),
            'payload_entry_hex': data[entry:entry + 4].hex(' '),
            'target_instruction_and_return_hex': data[target:target + size + 2].hex(' '),
            'string_id': string_id,
            'label': string(string_id),
        })
    return {
        'dex_sha256': hashlib.sha256(data).hexdigest(),
        'method': owner + '->getActionName(I)Ljava/lang/String;',
        'code_item_offset': code,
        'code_item_offset_hex': hex(code),
        'instruction_start_hex': hex(start),
        'switch_instruction_hex': data[start:start + 6].hex(' '),
        'payload_delta_code_units': payload_delta,
        'payload_file_offset_hex': hex(payload),
        'payload_hex': data[payload:payload + 8 + count * 4].hex(' '),
        'offset_rule': 'target = switch_instruction_address + signed_target_delta * 2',
        'hardware_verified': False,
        'targets': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dex', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = inspect(args.dex)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        args.out.write_text(serialized, encoding='utf-8')
    print(serialized, end='')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
