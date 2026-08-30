"""
Pure encode/decode functions for RakNet's datagram+frame wire format.
No state lives here -- session.py owns sequence/reliable/order counters and
calls into these functions to turn that state into bytes and back.
"""

import struct

DATAGRAM_FLAG = 0x80
ACK_FLAG = 0x40
NAK_FLAG = 0x20
SPLIT_FLAG = 0x10

RELIABILITY_UNRELIABLE = 0
RELIABILITY_RELIABLE = 2
RELIABILITY_RELIABLE_ORDERED = 3


def read_triad(buf, offset):
    b = buf[offset:offset + 3] + b"\x00"
    return struct.unpack("<I", b)[0], offset + 3


def write_triad(n):
    return struct.pack("<I", n)[:3]


def parse_datagram(data):
    """Returns (sequence_number, [frame_dict, ...]). Each frame_dict has keys:
    reliability, is_split, order_index (or None), split_count/split_id/split_index
    (or None), payload (bytes)."""
    flags = data[0]
    if not (flags & DATAGRAM_FLAG) or (flags & ACK_FLAG) or (flags & NAK_FLAG):
        return None, []

    seq, offset = read_triad(data, 1)
    frames = []

    while offset < len(data):
        frame_flags = data[offset]
        reliability = (frame_flags >> 5) & 0x07
        is_split = bool(frame_flags & SPLIT_FLAG)
        offset += 1
        length_bits = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        length_bytes = (length_bits + 7) // 8

        if reliability in (RELIABILITY_RELIABLE, RELIABILITY_RELIABLE_ORDERED):
            _, offset = read_triad(data, offset)  # reliable message index (no NAK retransmit yet)

        order_index = None
        if reliability == RELIABILITY_RELIABLE_ORDERED:
            order_index, offset = read_triad(data, offset)
            offset += 1  # order channel

        split_count = split_id = split_index = None
        if is_split:
            split_count = struct.unpack(">I", data[offset:offset + 4])[0]
            offset += 4
            split_id = struct.unpack(">H", data[offset:offset + 2])[0]
            offset += 2
            split_index = struct.unpack(">I", data[offset:offset + 4])[0]
            offset += 4

        payload = data[offset:offset + length_bytes]
        offset += length_bytes

        frames.append({
            "reliability": reliability,
            "is_split": is_split,
            "order_index": order_index,
            "split_count": split_count,
            "split_id": split_id,
            "split_index": split_index,
            "payload": payload,
        })

    return seq, frames


def build_frame(payload, reliability, reliable_index=None, order_index=None,
                 split=None):
    """split: optional (split_count, split_id, split_index)."""
    frame = bytearray()
    flags = reliability << 5
    if split:
        flags |= SPLIT_FLAG
    frame.append(flags)
    frame += struct.pack(">H", len(payload) * 8)

    if reliability in (RELIABILITY_RELIABLE, RELIABILITY_RELIABLE_ORDERED):
        frame += write_triad(reliable_index)

    if reliability == RELIABILITY_RELIABLE_ORDERED:
        frame += write_triad(order_index)
        frame.append(0)  # order channel

    if split:
        split_count, split_id, split_index = split
        frame += struct.pack(">I", split_count)
        frame += struct.pack(">H", split_id)
        frame += struct.pack(">I", split_index)

    frame += payload
    return bytes(frame)


def build_datagram(seq, frame_bytes_list):
    datagram = bytearray()
    datagram.append(DATAGRAM_FLAG)
    datagram += write_triad(seq)
    for f in frame_bytes_list:
        datagram += f
    return bytes(datagram)


def parse_ack_or_nak(data):
    """Parses an ACK or NAK packet into a flat list of acknowledged/negative
    sequence numbers. Returns None if this isn't an ACK/NAK datagram."""
    flags = data[0]
    if not (flags & (ACK_FLAG | NAK_FLAG)):
        return None

    offset = 1
    range_count = struct.unpack(">H", data[offset:offset + 2])[0]
    offset += 2

    seqs = []
    for _ in range(range_count):
        single = data[offset]
        offset += 1
        start, offset = read_triad(data, offset)
        if single:
            seqs.append(start)
        else:
            end, offset = read_triad(data, offset)
            seqs.extend(range(start, end + 1))
    return seqs


def build_nak(seqs):
    if not seqs:
        return None
    seqs = sorted(seqs)
    packet = bytearray()
    packet.append(NAK_FLAG | DATAGRAM_FLAG)
    packet += struct.pack(">H", 1)
    packet.append(1 if seqs[0] == seqs[-1] else 0)
    packet += write_triad(seqs[0])
    if seqs[0] != seqs[-1]:
        packet += write_triad(seqs[-1])
    return bytes(packet)


def build_ack(seqs):
    if not seqs:
        return None
    seqs = sorted(seqs)
    packet = bytearray()
    packet.append(ACK_FLAG | DATAGRAM_FLAG)
    packet += struct.pack(">H", 1)  # one range (fine for low-loss local/LAN use)
    packet.append(1 if seqs[0] == seqs[-1] else 0)
    packet += write_triad(seqs[0])
    if seqs[0] != seqs[-1]:
        packet += write_triad(seqs[-1])
    return bytes(packet)
