"""Thin wrapper around a UDP socket: bind, send, and a background receive
loop that hands (data, addr) pairs to a callback. Knows nothing about RakNet
or Minecraft -- just moves bytes."""

import socket
import threading


class UDPSocket:
    def __init__(self, host="0.0.0.0", port=19132):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running = False
        self.on_packet = None  # callback(data: bytes, addr: (str, int))

    def bind(self):
        self.sock.bind((self.host, self.port))

    def start(self):
        self.bind()
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False
        self.sock.close()

    def send(self, data, addr):
        self.sock.sendto(data, addr)

    def _loop(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                break
            if self.on_packet:
                self.on_packet(data, addr)
