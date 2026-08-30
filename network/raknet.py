"""
Top-level RakNet transport. Handles the "offline" handshake packets (the
ones exchanged before a Session exists: ping/pong for server list visibility,
and the two-step Open Connection Request/Reply that negotiates MTU and
creates the Session), then hands everything else off to the relevant Session.
"""

import struct
import threading
import queue
import time

from network.udp import UDPSocket
from network.session import Session, _encode_address
import log

RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")

ID_UNCONNECTED_PING = 0x01
ID_UNCONNECTED_PONG = 0x1c
ID_OPEN_CONNECTION_REQUEST_1 = 0x05
ID_OPEN_CONNECTION_REPLY_1 = 0x06
ID_OPEN_CONNECTION_REQUEST_2 = 0x07
ID_OPEN_CONNECTION_REPLY_2 = 0x08


class RakNetTransport:
    def __init__(self, host="0.0.0.0", port=19132, motd="Python Bedrock Server",
                 protocol_version=1001, game_version="1.26.30",
                 level_name="BDSPy Server", max_players=10, guid=None):
        self.host = host
        self.port = port
        self.motd = motd
        self.protocol_version = protocol_version
        self.game_version = game_version
        self.level_name = level_name
        self.max_players = max_players
        self.server_guid = guid or int.from_bytes(
            struct.pack(">q", hash((host, port)) & 0x7fffffffffffffff), "big")

        self.udp = UDPSocket(host, port)
        self.udp.on_packet = self._handle_packet

        self.sessions = {}  # addr -> Session

        # Game-packet dispatch runs on a SEPARATE thread from the receive
        # loop. Without this, a slow/large response (e.g. StartGame's ~80KB
        # compressed payload, sent as 50+ fragments) would block the same
        # thread that's supposed to be calling recvfrom() -- delaying our
        # own ACKs to the client and potentially causing it to time out or
        # give up, even though every byte eventually arrives fine. This was
        # observed intermittently on a real device: identical bytes worked
        # on one connection attempt and failed on another.
        self.dispatch_queue = queue.Queue()

        # hooks the game layer attaches
        self.on_connected = None
        self.on_disconnected = None
        self.on_game_packet = None

    def start(self):
        self.udp.start()
        threading.Thread(target=self._tick_loop, daemon=True).start()
        threading.Thread(target=self._dispatch_loop, daemon=True).start()
        log.info(f"listening on {self.host}:{self.port}", tag="[raknet]")

    def _tick_loop(self):
        """Drives ACK-timeout retransmission for every active session."""
        while True:
            time.sleep(0.1)
            for session in list(self.sessions.values()):
                session.tick()

    def _dispatch_loop(self):
        """Runs game-packet handling (and whatever sends it triggers) off
        the receive thread, so a slow response never delays our ACKs."""
        while True:
            session, subpackets = self.dispatch_queue.get()
            if self.on_game_packet:
                try:
                    self.on_game_packet(session, subpackets)
                except Exception as e:
                    log.error(f"unhandled error in game dispatch for {session.addr}: {e}", tag="[raknet]")

    def stop(self):
        self.udp.stop()

    def _handle_packet(self, data, addr):
        if not data:
            return
        pid = data[0]

        if pid == ID_UNCONNECTED_PING:
            self._handle_unconnected_ping(data, addr)
        elif pid == ID_OPEN_CONNECTION_REQUEST_1:
            self._handle_open_connection_request_1(data, addr)
        elif pid == ID_OPEN_CONNECTION_REQUEST_2:
            self._handle_open_connection_request_2(data, addr)
        elif data[0] & 0x80:  # DATAGRAM_FLAG
            session = self.sessions.get(addr)
            if session:
                session.handle_datagram(data)
            else:
                log.warn(f"datagram from {addr} with no active session (id 0x{data[0]:02x}) -- dropped",
                          tag="[raknet]")

    def _handle_unconnected_ping(self, data, addr):
        log.debug(f"unconnected ping from {addr}", tag="[raknet]")
        send_time = struct.unpack(">q", data[1:9])[0]
        motd_str = (f"MCPE;{self.motd};{self.protocol_version};{self.game_version};0;"
                    f"{self.max_players};{self.server_guid};{self.level_name};Survival;1;"
                    f"{self.port};{self.port + 1};")
        packet = bytearray([ID_UNCONNECTED_PONG])
        packet += struct.pack(">q", send_time)
        packet += struct.pack(">q", self.server_guid)
        packet += RAKNET_MAGIC
        packet += struct.pack(">H", len(motd_str))
        packet += motd_str.encode()
        self.udp.send(bytes(packet), addr)

    def _handle_open_connection_request_1(self, data, addr):
        mtu = len(data) + 28  # approx original MTU (udp+ip header overhead)
        log.debug(f"open_connection_request_1 from {addr}, negotiated mtu={mtu}", tag="[raknet]")
        packet = bytearray([ID_OPEN_CONNECTION_REPLY_1])
        packet += RAKNET_MAGIC
        packet += struct.pack(">q", self.server_guid)
        packet.append(0)  # no security
        packet += struct.pack(">H", mtu)
        self.udp.send(bytes(packet), addr)

    def _handle_open_connection_request_2(self, data, addr):
        offset = 1 + 16  # id + magic
        offset += 1 + 4  # address family + ipv4
        offset += 2      # port
        mtu = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        client_guid = struct.unpack(">q", data[offset:offset + 8])[0]

        packet = bytearray([ID_OPEN_CONNECTION_REPLY_2])
        packet += RAKNET_MAGIC
        packet += struct.pack(">q", self.server_guid)
        packet += _encode_address(addr)
        packet += struct.pack(">H", mtu)
        packet.append(0)  # no encryption
        self.udp.send(bytes(packet), addr)

        session = Session(addr, self.udp, self.server_guid)
        session.mtu = mtu
        session.client_guid = client_guid
        session.on_connected = self.on_connected
        session.on_disconnected = self._wrap_disconnected
        session.on_game_packet = self.on_game_packet
        session.dispatch_queue = self.dispatch_queue
        self.sessions[addr] = session
        log.info(f"session created for {addr} (client_guid={client_guid}, mtu={mtu})", tag="[raknet]")

    def _wrap_disconnected(self, session):
        self.sessions.pop(session.addr, None)
        log.info(f"session closed for {session.addr}", tag="[raknet]")
        if self.on_disconnected:
            self.on_disconnected(session)
