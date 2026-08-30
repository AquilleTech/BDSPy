import struct

from protocol.varint import read_varint, zigzag_decode


class Reader:
    def __init__(self, data, offset=0):
        self.data = data
        self.offset = offset

    def remaining(self):
        return len(self.data) - self.offset

    def u8(self):
        v = self.data[self.offset]
        self.offset += 1
        return v

    def i8(self):
        v = struct.unpack_from("<b", self.data, self.offset)[0]
        self.offset += 1
        return v

    def bool(self):
        return self.u8() != 0

    def raw(self, n):
        v = self.data[self.offset:self.offset + n]
        self.offset += n
        return v

    def li16(self):
        v = struct.unpack_from("<h", self.data, self.offset)[0]
        self.offset += 2
        return v

    def lu16(self):
        v = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return v

    def li32(self):
        v = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return v

    def lu32(self):
        v = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return v

    def li64(self):
        v = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return v

    def lf32(self):
        v = struct.unpack_from("<f", self.data, self.offset)[0]
        self.offset += 4
        return v

    def varint(self):
        v, new_offset = read_varint(self.data, self.offset)
        self.offset = new_offset
        return v

    def zigzag32(self):
        return zigzag_decode(self.varint())

    def string(self):
        n = self.varint()
        s = self.data[self.offset:self.offset + n].decode("utf-8", errors="replace")
        self.offset += n
        return s

    def byte_array(self):
        n = self.varint()
        v = self.data[self.offset:self.offset + n]
        self.offset += n
        return v
