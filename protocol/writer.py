import struct
import uuid as uuidlib

from protocol.varint import write_varint, zigzag_encode


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def bytes(self):
        return bytes(self.buf)

    def u8(self, v):
        self.buf.append(v & 0xff)

    def i8(self, v):
        self.buf += struct.pack("<b", v)

    def bool(self, v):
        self.buf.append(1 if v else 0)

    def raw(self, b):
        self.buf += b

    def li16(self, v):
        self.buf += struct.pack("<h", v)

    def lu16(self, v):
        self.buf += struct.pack("<H", v)

    def li32(self, v):
        self.buf += struct.pack("<i", v)

    def lu32(self, v):
        self.buf += struct.pack("<I", v)

    def li64(self, v):
        self.buf += struct.pack("<q", v)

    def lu64(self, v):
        self.buf += struct.pack("<Q", v)

    def lf32(self, v):
        self.buf += struct.pack("<f", v)

    def lf64(self, v):
        self.buf += struct.pack("<d", v)

    def varint(self, v):
        self.buf += write_varint(v)

    def zigzag32(self, v):
        self.varint(zigzag_encode(v) & 0xffffffff)

    def zigzag64(self, v):
        self.varint(zigzag_encode(v))

    def varint64(self, v):
        self.varint(v)

    def string(self, s):
        encoded = s.encode("utf-8")
        self.varint(len(encoded))
        self.buf += encoded

    def byte_array(self, b):
        self.varint(len(b))
        self.buf += b

    def uuid(self, u):
        b = uuidlib.UUID(u).bytes if isinstance(u, str) else u.bytes
        msb = int.from_bytes(b[0:8], "big")
        lsb = int.from_bytes(b[8:16], "big")
        self.li64(msb if msb < 2**63 else msb - 2**64)
        self.li64(lsb if lsb < 2**63 else lsb - 2**64)

    def vec3f(self, x, y, z):
        self.lf32(x)
        self.lf32(y)
        self.lf32(z)

    def vec2f(self, x, y):
        self.lf32(x)
        self.lf32(y)

    def block_coords(self, x, y, z):
        self.zigzag32(x)
        self.zigzag32(y)
        self.zigzag32(z)
