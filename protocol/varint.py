"""Unsigned LEB128 varints and zigzag-encoded signed variants, as used
throughout the Bedrock protocol for counts, enums, and signed integers."""


def read_varint(data, offset):
    result = 0
    shift = 0
    while True:
        b = data[offset]
        offset += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, offset


def write_varint(v):
    v &= 0xffffffffffffffff
    out = bytearray()
    while True:
        b = v & 0x7f
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def zigzag_encode(v):
    return (v << 1) if v >= 0 else ((-v << 1) - 1)


def zigzag_decode(raw):
    if raw & 1:
        return -((raw + 1) >> 1)
    return raw >> 1
