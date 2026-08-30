"""
A Session tracks one client's RakNet connection: reliability counters, ACK/NAK
based retransmission, split packet reassembly, delivery ordering, and the
game-packet batch (de)compression wrapper Bedrock uses on top of raw RakNet
frames.
"""

import struct
import time
import zlib

from network import reliability as rel
from protocol.writer import Writer
from protocol.reader import Reader
import log

BATCH_MARKER = 0xfe

# RakNet-internal packet IDs handled at this layer, before anything reaches
# the game protocol.
ID_CONNECTED_PING = 0x00
ID_CONNECTION_REQUEST = 0x09
ID_CONNECTED_PONG = 0x03
ID_NEW_INCOMING_CONNECTION = 0x13
ID_DISCONNECT_NOTIFICATION = 0x15
ID_CONNECTION_REQUEST_ACCEPTED = 0x10

RESEND_TIMEOUT = 0.6   # seconds before an unacked datagram is resent
MAX_RESENDS = 12        # give up (and log loudly) after this many attempts


def _deflate_raw(data):
    co = zlib.compressobj(6, zlib.DEFLATED, -15)
    return co.compress(data) + co.flush()


def _inflate_raw(data):
    do = zlib.decompressobj(-15)
    return do.decompress(data) + do.flush()


def _encode_address(addr):
    ip, port = addr
    out = bytearray([4])  # IPv4
    out += bytes(int(x) ^ 0xff for x in ip.split("."))
    out += struct.pack(">H", port)
    return bytes(out)


class Session:
    def __init__(self, addr, udp_socket, server_guid):
        self.addr = addr
        self.udp = udp_socket
        self.server_guid = server_guid

        self.mtu = 1400
        self.client_guid = 0
        self.state = "connecting"

        self.datagram_seq_send = 0
        self.reliable_index_send = 0
        self.ordered_index_send = 0
        self.next_split_id = 0

        self.recv_ordered_index = 0
        self.out_of_order_buffer = {}
        self.split_buffers = {}

        # ACK/NAK-based reliability for OUR outgoing datagrams.
        # seq -> {"datagram": bytes, "sent_at": float, "resends": int}
        self.pending_acks = {}
        # highest incoming datagram sequence we've seen, for gap detection
        self.recv_seq_expected = 0

        self.compression_ready = False

        self.last_recv_time = time.time()

        # hooks the transport/game layer attaches
        self.on_connected = None
        self.on_disconnected = None
        self.on_game_packet = None  # callback(session, list_of_(packet_id, body))
        self.dispatch_queue = None  # set by RakNetTransport; decouples game
        # dispatch (which can trigger big blocking sends) from this session's
        # own receive/ack path -- see raknet.py's RakNetTransport docstring.

    # ---------------- raw send helpers ----------------

    def _send_raw(self, data):
        self.udp.send(data, self.addr)

    def _send_ack(self, seqs):
        packet = rel.build_ack(seqs)
        if packet:
            log.debug(f"ACK -> seqs={seqs}", tag=f"[raw:{self.addr}]")
            self._send_raw(packet)

    def _send_nak(self, seqs):
        packet = rel.build_nak(seqs)
        if packet:
            log.debug(f"NAK -> seqs={seqs} (gap detected, requesting resend)", tag=f"[raw:{self.addr}]")
            self._send_raw(packet)

    # ---------------- outgoing: RakNet frames ----------------

    def send_game_packet(self, payload, reliability=rel.RELIABILITY_RELIABLE_ORDERED):
        """Send raw bytes (already batch-encoded) as one or more frames,
        splitting across multiple datagrams if it's bigger than the MTU."""
        max_chunk = max(self.mtu - 60, 400)

        if len(payload) <= max_chunk:
            self._send_single(payload, reliability)
            return

        chunks = [payload[i:i + max_chunk] for i in range(0, len(payload), max_chunk)]
        split_id = self.next_split_id
        self.next_split_id = (self.next_split_id + 1) & 0xffff

        order_index = None
        if reliability == rel.RELIABILITY_RELIABLE_ORDERED:
            order_index = self.ordered_index_send
            self.ordered_index_send += 1

        log.debug(f"sending {len(payload)}B payload as {len(chunks)} fragments (split_id={split_id})",
                  tag=f"[raw:{self.addr}]")

        for idx, chunk in enumerate(chunks):
            self._send_single(
                chunk, reliability,
                split=(len(chunks), split_id, idx),
                order_index_override=order_index,
            )

    def _send_single(self, payload, reliability, split=None, order_index_override=None):
        reliable_index = None
        if reliability in (rel.RELIABILITY_RELIABLE, rel.RELIABILITY_RELIABLE_ORDERED):
            reliable_index = self.reliable_index_send
            self.reliable_index_send += 1

        order_index = None
        if reliability == rel.RELIABILITY_RELIABLE_ORDERED:
            order_index = order_index_override if order_index_override is not None else self.ordered_index_send
            if order_index_override is None:
                self.ordered_index_send += 1

        frame_bytes = rel.build_frame(payload, reliability, reliable_index, order_index, split)
        seq = self.datagram_seq_send
        self.datagram_seq_send += 1
        datagram = rel.build_datagram(seq, [frame_bytes])

        split_tag = f" split={split[2]+1}/{split[0]}" if split else ""
        log.debug(f"-> datagram seq={seq} {len(datagram)}B reliability={reliability}{split_tag}",
                  tag=f"[raw:{self.addr}]")

        if reliability in (rel.RELIABILITY_RELIABLE, rel.RELIABILITY_RELIABLE_ORDERED):
            self.pending_acks[seq] = {"datagram": datagram, "sent_at": time.time(), "resends": 0}

        self._send_raw(datagram)

    def tick(self):
        """Called periodically by the transport's background thread. Resends
        anything that's been waiting too long for an ACK."""
        now = time.time()
        for seq, entry in list(self.pending_acks.items()):
            if now - entry["sent_at"] < RESEND_TIMEOUT:
                continue
            if entry["resends"] >= MAX_RESENDS:
                log.error(f"seq={seq} still unacked after {MAX_RESENDS} resends -- giving up on it "
                          f"(client likely gone, or something is silently eating our packets)",
                          tag=f"[raw:{self.addr}]")
                del self.pending_acks[seq]
                continue
            entry["resends"] += 1
            entry["sent_at"] = now
            log.warn(f"RESEND seq={seq} (attempt {entry['resends']}/{MAX_RESENDS}, "
                     f"no ACK after {RESEND_TIMEOUT}s)", tag=f"[raw:{self.addr}]")
            self._send_raw(entry["datagram"])

    # ---------------- game packet batch (compression) ----------------

    def send_batch(self, subpackets, threshold=1):
        """subpackets: list of (packet_id, body_bytes)."""
        payload = self._pack_subpackets(subpackets)
        if self.compression_ready:
            if len(payload) > threshold:
                blob = bytes([BATCH_MARKER, 0]) + _deflate_raw(payload)
                mode = "deflate"
            else:
                blob = bytes([BATCH_MARKER, 255]) + payload
                mode = "raw"
        else:
            blob = bytes([BATCH_MARKER]) + payload
            mode = "uncompressed(pre-handshake)"
        ids = [pid for pid, _ in subpackets]
        log.debug(f"-> ids={ids} payload={len(payload)}B wire={len(blob)}B mode={mode}", tag=f"[session:{self.addr}]")
        self.send_game_packet(blob, rel.RELIABILITY_RELIABLE_ORDERED)

    @staticmethod
    def _pack_subpackets(subpackets):
        buf = bytearray()
        for pid, body in subpackets:
            w = Writer()
            w.varint(pid)
            w.raw(body)
            packet_bytes = w.bytes()
            length_writer = Writer()
            length_writer.varint(len(packet_bytes))
            buf += length_writer.bytes()
            buf += packet_bytes
        return bytes(buf)

    def _decode_batch(self, data):
        if data[0] != BATCH_MARKER:
            raise ValueError(f"not a game packet batch, first byte 0x{data[0]:02x}")
        rest = data[1:]

        if self.compression_ready:
            algo = rest[0]
            body = rest[1:]
            if algo == 0:
                payload = _inflate_raw(body)
            elif algo == 255:
                payload = body
            else:
                raise ValueError(f"unsupported compression algo {algo}")
        else:
            payload = rest

        out = []
        r = Reader(payload)
        while r.remaining() > 0:
            length = r.varint()
            sub = r.raw(length)
            sr = Reader(sub)
            pid = sr.varint()
            body = sub[sr.offset:]
            out.append((pid, body))

        ids = [pid for pid, _ in out]
        log.debug(f"<- ids={ids} wire={len(data)}B payload={len(payload)}B", tag=f"[session:{self.addr}]")
        return out

    # ---------------- incoming: RakNet datagram handling ----------------

    def handle_datagram(self, data):
        self.last_recv_time = time.time()
        flags = data[0]

        if flags & rel.ACK_FLAG:
            seqs = rel.parse_ack_or_nak(data)
            log.debug(f"<- ACK seqs={seqs}", tag=f"[raw:{self.addr}]")
            for s in seqs:
                self.pending_acks.pop(s, None)
            return

        if flags & rel.NAK_FLAG:
            seqs = rel.parse_ack_or_nak(data)
            log.warn(f"<- NAK seqs={seqs} -- client says it never got these, resending now", tag=f"[raw:{self.addr}]")
            for s in seqs:
                entry = self.pending_acks.get(s)
                if entry:
                    entry["sent_at"] = time.time()
                    entry["resends"] += 1
                    self._send_raw(entry["datagram"])
            return

        if not (flags & rel.DATAGRAM_FLAG):
            return

        seq, frames = rel.parse_datagram(data)
        if seq is None:
            return

        log.debug(f"<- datagram seq={seq} {len(data)}B, {len(frames)} frame(s)", tag=f"[raw:{self.addr}]")

        # Gap detection: if we jumped ahead of what we expected, some
        # datagram(s) in between are likely lost -- NAK them immediately
        # instead of waiting for the sender's own resend timeout.
        if seq > self.recv_seq_expected:
            missing = list(range(self.recv_seq_expected, seq))
            self._send_nak(missing)
        if seq >= self.recv_seq_expected:
            self.recv_seq_expected = seq + 1

        for f in frames:
            if f["is_split"]:
                self._handle_split_fragment(f)
            else:
                self._handle_ordered_payload(f["payload"], f["reliability"], f["order_index"])

        self._send_ack([seq])

    def _handle_split_fragment(self, f):
        split_id = f["split_id"]
        entry = self.split_buffers.setdefault(split_id, {"count": f["split_count"], "parts": {}})
        entry["parts"][f["split_index"]] = f["payload"]
        log.debug(
            f"split fragment {f['split_index']+1}/{f['split_count']} for split_id={split_id} "
            f"({len(entry['parts'])}/{entry['count']} collected)",
            tag=f"[session:{self.addr}]",
        )
        if len(entry["parts"]) >= entry["count"]:
            missing = [i for i in range(entry["count"]) if i not in entry["parts"]]
            if missing:
                log.error(f"split_id={split_id} claims complete but missing indices {missing}!",
                          tag=f"[session:{self.addr}]")
                return
            full_payload = b"".join(entry["parts"][i] for i in range(entry["count"]))
            del self.split_buffers[split_id]
            log.debug(f"reassembled split_id={split_id} -> {len(full_payload)}B", tag=f"[session:{self.addr}]")
            # Route through the same ordering path as normal packets -- all
            # fragments share one order_index, so this correctly advances
            # recv_ordered_index instead of stalling whatever comes next.
            self._handle_ordered_payload(full_payload, f["reliability"], f["order_index"])

    def _handle_ordered_payload(self, payload, reliability, order_index):
        if not payload:
            return
        if reliability != rel.RELIABILITY_RELIABLE_ORDERED:
            self._dispatch_raknet(payload)
            return

        if order_index == self.recv_ordered_index:
            self._dispatch_raknet(payload)
            self.recv_ordered_index += 1
            while self.recv_ordered_index in self.out_of_order_buffer:
                nxt = self.out_of_order_buffer.pop(self.recv_ordered_index)
                log.debug(f"flushing buffered order_index={self.recv_ordered_index}", tag=f"[session:{self.addr}]")
                self._dispatch_raknet(nxt)
                self.recv_ordered_index += 1
        elif order_index > self.recv_ordered_index:
            log.warn(
                f"packet arrived out of order (got order_index={order_index}, "
                f"expected={self.recv_ordered_index}) -- buffering until the gap fills.",
                tag=f"[session:{self.addr}]",
            )
            self.out_of_order_buffer[order_index] = payload
        else:
            log.debug(f"dropping duplicate/old order_index={order_index} (already at {self.recv_ordered_index})",
                       tag=f"[session:{self.addr}]")

    def _dispatch_raknet(self, payload):
        """Handles RakNet-internal packets; anything else is a game batch."""
        pid = payload[0]

        if pid == ID_CONNECTION_REQUEST:
            self.client_guid = struct.unpack(">q", payload[1:9])[0]
            self._send_connection_request_accepted()
        elif pid == ID_NEW_INCOMING_CONNECTION:
            self.state = "connected"
            if self.on_connected:
                self.on_connected(self)
        elif pid == ID_CONNECTED_PING:
            send_time = struct.unpack(">q", payload[1:9])[0]
            pong = bytearray([ID_CONNECTED_PONG])
            pong += struct.pack(">q", send_time)
            pong += struct.pack(">q", int(time.time() * 1000))
            self.send_game_packet(bytes(pong), rel.RELIABILITY_UNRELIABLE)
        elif pid == ID_DISCONNECT_NOTIFICATION:
            self.state = "disconnected"
            if self.on_disconnected:
                self.on_disconnected(self)
        else:
            try:
                subpackets = self._decode_batch(payload)
            except Exception as e:
                log.error(f"failed to decode batch ({len(payload)}B, starts 0x{payload[:8].hex()}): {e}",
                          tag=f"[session:{self.addr}]")
                return
            # Enqueue for the dispatch worker thread instead of calling
            # on_game_packet directly -- keeps this (receive/ack) thread free
            # to keep servicing new datagrams immediately, even if the game
            # layer's response involves a big, slow send.
            if self.dispatch_queue is not None:
                self.dispatch_queue.put((self, subpackets))
            elif self.on_game_packet:
                self.on_game_packet(self, subpackets)

    def _send_connection_request_accepted(self):
        packet = bytearray([ID_CONNECTION_REQUEST_ACCEPTED])
        packet += _encode_address(self.addr)
        packet += struct.pack(">H", 0)
        for _ in range(10):
            packet += _encode_address(("0.0.0.0", 0))
        packet += struct.pack(">q", int(time.time() * 1000))
        packet += struct.pack(">q", int(time.time() * 1000))
        self.send_game_packet(bytes(packet), rel.RELIABILITY_RELIABLE)
