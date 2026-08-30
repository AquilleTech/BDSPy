"""
Bedrock NBT (little-endian numbers, varint-length-prefixed strings/arrays/lists).
This is the flavor used by canonical_block_states.nbt and by "nbt" fields inside
game packets (StartGame.property_data, item lists, etc).
"""

import struct

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class Reader:
    def __init__(self, data, offset=0):
        self.data = data
        self.offset = offset

    def u8(self):
        v = self.data[self.offset]
        self.offset += 1
        return v

    def varint(self):
        result = 0
        shift = 0
        while True:
            b = self.data[self.offset]
            self.offset += 1
            result |= (b & 0x7f) << shift
            if not (b & 0x80):
                break
            shift += 7
        return result

    def zigzag_varint(self, bits=32):
        raw = self.varint()
        if raw & 1:
            return -((raw + 1) >> 1)
        return raw >> 1

    def string(self):
        length = self.varint()
        s = self.data[self.offset:self.offset + length].decode("utf-8")
        self.offset += length
        return s

    def fixed(self, fmt):
        size = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, self.data, self.offset)[0]
        self.offset += size
        return v

    def payload(self, tag_type):
        if tag_type == TAG_BYTE:
            return self.fixed("<b")
        if tag_type == TAG_SHORT:
            return self.fixed("<h")
        if tag_type == TAG_INT:
            return self.zigzag_varint(32)
        if tag_type == TAG_LONG:
            return self.zigzag_varint(64)
        if tag_type == TAG_FLOAT:
            return self.fixed("<f")
        if tag_type == TAG_DOUBLE:
            return self.fixed("<d")
        if tag_type == TAG_STRING:
            return self.string()
        if tag_type == TAG_BYTE_ARRAY:
            n = self.varint()
            v = self.data[self.offset:self.offset + n]
            self.offset += n
            return v
        if tag_type == TAG_INT_ARRAY:
            n = self.varint()
            return [self.fixed("<i") for _ in range(n)]
        if tag_type == TAG_LONG_ARRAY:
            n = self.varint()
            return [self.fixed("<q") for _ in range(n)]
        if tag_type == TAG_LIST:
            elem_type = self.u8()
            n = self.varint()
            return {"_list_type": elem_type, "value": [self.payload(elem_type) for _ in range(n)]}
        if tag_type == TAG_COMPOUND:
            out = {}
            while True:
                t = self.u8()
                if t == TAG_END:
                    break
                name = self.string()
                out[name] = self.payload(t)
            return out
        raise ValueError(f"unknown tag type {tag_type} at offset {self.offset}")

    def named_tag(self):
        t = self.u8()
        if t == TAG_END:
            return None, None
        name = self.string()
        return name, self.payload(t)


def load_all_compounds(data):
    """canonical_block_states.nbt is a raw concatenation of unnamed root compounds."""
    r = Reader(data)
    out = []
    while r.offset < len(data):
        t = r.u8()
        assert t == TAG_COMPOUND
        r.string()  # root name, always empty
        out.append(r.payload(TAG_COMPOUND))
    return out


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def u8(self, v):
        self.buf.append(v & 0xff)

    def varint(self, v):
        while True:
            b = v & 0x7f
            v >>= 7
            if v:
                self.buf.append(b | 0x80)
            else:
                self.buf.append(b)
                break

    def zigzag_varint(self, v):
        zz = (v << 1) if v >= 0 else ((-v << 1) - 1)
        self.varint(zz)

    def string(self, s):
        encoded = s.encode("utf-8")
        self.varint(len(encoded))
        self.buf += encoded

    def fixed(self, fmt, v):
        self.buf += struct.pack(fmt, v)

    def payload(self, tag_type, value):
        if tag_type == TAG_BYTE:
            self.fixed("<b", value)
        elif tag_type == TAG_SHORT:
            self.fixed("<h", value)
        elif tag_type == TAG_INT:
            self.zigzag_varint(value)
        elif tag_type == TAG_LONG:
            self.zigzag_varint(value)
        elif tag_type == TAG_FLOAT:
            self.fixed("<f", value)
        elif tag_type == TAG_DOUBLE:
            self.fixed("<d", value)
        elif tag_type == TAG_STRING:
            self.string(value)
        elif tag_type == TAG_BYTE_ARRAY:
            self.varint(len(value))
            self.buf += bytes(value)
        elif tag_type == TAG_INT_ARRAY:
            self.varint(len(value))
            for x in value:
                self.fixed("<i", x)
        elif tag_type == TAG_LONG_ARRAY:
            self.varint(len(value))
            for x in value:
                self.fixed("<q", x)
        elif tag_type == TAG_LIST:
            elem_type = value.get("_list_type", TAG_COMPOUND)
            items = value["value"]
            self.u8(elem_type)
            self.varint(len(items))
            for item in items:
                self.payload(elem_type, item)
        elif tag_type == TAG_COMPOUND:
            for name, (t, v) in value.items():
                self.u8(t)
                self.string(name)
                self.payload(t, v)
            self.u8(TAG_END)
        else:
            raise ValueError(f"unknown tag type {tag_type}")

    def root_compound(self, typed_dict, name=""):
        """typed_dict: {key: (tag_type, value)}"""
        self.u8(TAG_COMPOUND)
        self.string(name)
        self.payload(TAG_COMPOUND, typed_dict)
        return bytes(self.buf)
