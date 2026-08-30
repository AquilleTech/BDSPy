import time
import traceback

from network.raknet import RakNetTransport
from network import reliability as rel
from server.player import PlayerState
from server.entity import EntityIdAllocator
from server.world import World
from protocol import packets
from protocol.reader import Reader
from world import block
from commands.dispatcher import dispatcher
import log

# Reverse lookup so DEBUG logs can show "login(1)" instead of just "1" --
# makes malformed/unexpected packet traffic much easier to eyeball.
_PACKET_NAMES = {
    v: k[3:].lower() for k, v in vars(packets).items()
    if k.startswith("ID_") and isinstance(v, int)
}


def _pname(pid):
    return f"{_PACKET_NAMES.get(pid, '?')}({pid})"


class BDSPyServer:
    def __init__(self, cfg):
        self.cfg = cfg

        log_level = cfg.get("logging", {}).get("level", "INFO")
        log.set_level(log_level)

        self.transport = RakNetTransport(
            host=cfg["server"]["host"],
            port=cfg["server"]["port"],
            motd=cfg["server"]["motd"],
            protocol_version=1001,
            game_version="1.26.30",
            level_name=cfg["server"]["level_name"],
            max_players=cfg["server"]["max_players"],
        )
        self.transport.on_game_packet = self._on_game_packet
        self.transport.on_disconnected = self._on_disconnected

        self.world = World(
            cfg["world"]["ground_layers"],
            spawn_x=cfg["world"]["spawn_x"],
            spawn_z=cfg["world"]["spawn_z"],
        )
        self.chunk_radius = cfg["chunks"]["radius"]
        self.starting_items = [
            block.SHORT_NAME_TO_FULL[name] for name in cfg["player"]["starting_items"]
        ]
        self.stack_size = cfg["player"]["starting_stack_size"]

        self.entity_ids = EntityIdAllocator(start=1)
        self.players = {}  # addr -> PlayerState

    def start(self):
        self.transport.start()
        log.info(f"'{self.cfg['server']['motd']}' up on "
                 f"{self.cfg['server']['host']}:{self.cfg['server']['port']}", tag="[server]")
        log.info(f"spawn at {self.world.spawn_position}, "
                 f"ground={self.cfg['world']['ground_layers']}", tag="[server]")
        log.debug(f"log level = {logging_level_name()}", tag="[server]")

    def stop(self):
        self.transport.stop()

    def _on_disconnected(self, session):
        player = self.players.pop(session.addr, None)
        if player:
            log.info(f"{session.addr} ({player.display_name}) disconnected", tag="[server]")

    def _on_game_packet(self, session, subpackets):
        player = self.players.get(session.addr)
        if player is None:
            player = PlayerState(session, self.entity_ids.allocate())
            self.players[session.addr] = player
            log.debug(f"new player state created, entity_id={player.entity_id}", tag=f"[game:{session.addr}]")

        for pid, body in subpackets:
            log.debug(f"dispatching {_pname(pid)}, {len(body)}B", tag=f"[game:{session.addr}]")
            try:
                self._dispatch(player, pid, body)
            except Exception as e:
                log.error(f"error handling {_pname(pid)} ({len(body)}B, "
                          f"starts 0x{body[:16].hex()}): {e}", tag=f"[game:{session.addr}]")
                log.debug(traceback.format_exc(), tag=f"[game:{session.addr}]")

    def _dispatch(self, player, pid, body):
        addr = player.session.addr

        if pid == packets.ID_REQUEST_NETWORK_SETTINGS:
            player.send([packets.build_network_settings(compression_threshold=1)])
            player.session.compression_ready = True
            log.info(f"{addr} network settings negotiated", tag="[game]")

        elif pid == packets.ID_LOGIN:
            info = packets.parse_login(body)
            player.display_name = info["display_name"]
            player.xuid = info["xuid"]
            log.info(f"{addr} login: {player.display_name} (protocol {info['protocol_version']})", tag="[game]")
            if info["protocol_version"] != 1001:
                log.warn(f"{addr} client protocol {info['protocol_version']} != server's 1001 "
                         f"(1.26.30) -- likely version mismatch, expect trouble", tag="[game]")
            player.send([
                packets.build_play_status(packets.PLAY_STATUS_LOGIN_SUCCESS),
                packets.build_resource_packs_info(),
            ])

        elif pid == packets.ID_RESOURCE_PACK_CLIENT_RESPONSE:
            log.debug(f"{addr} resource_pack_client_response at pack_stage={player.pack_stage}", tag="[game]")
            if player.pack_stage == 0:
                player.send([packets.build_resource_pack_stack()])
                player.pack_stage = 1
            elif player.pack_stage == 1:
                self._send_start_game_sequence(player)
                player.pack_stage = 2
            else:
                log.debug(f"{addr} extra resource_pack_client_response ignored (already past negotiation)",
                          tag="[game]")

        elif pid == packets.ID_SET_LOCAL_PLAYER_AS_INITIALIZED:
            player.spawned = True
            log.info(f"{addr} ({player.display_name}) fully spawned", tag="[game]")

        elif pid == packets.ID_REQUEST_CHUNK_RADIUS:
            requested = packets.parse_request_chunk_radius(body)
            radius = min(requested, self.chunk_radius)
            log.debug(f"{addr} requested radius={requested}, capped to {radius}", tag="[game]")
            player.send([packets.build_chunk_radius_update(radius)])
            self._send_chunks(player, radius)

        elif pid in (packets.ID_PLAYER_AUTH_INPUT, packets.ID_MOVE_PLAYER):
            pass  # movement accepted client-side; no server authority in this MVP
            # (not even DEBUG-logged: these arrive every tick and would flood the log)

        elif pid == packets.ID_SERVERBOUND_LOADING_SCREEN:
            loading_type = packets.parse_serverbound_loading_screen(body)
            log.debug(f"{addr} serverbound_loading_screen type={loading_type} "
                      f"(0=unknown 1=start 2=end)", tag="[game]")

        else:
            log.debug(f"{addr} unhandled packet {_pname(pid)} ({len(body)}B) -- ignored", tag="[game]")

    def _send_start_game_sequence(self, player):
        addr = player.session.addr
        spawn = self.world.spawn_position
        log.debug(f"{addr} building start_game sequence, spawn={spawn}", tag="[game]")

        sg_id, sg_body = packets.build_start_game(player.entity_id, spawn)
        log.debug(f"{addr} start_game body = {len(sg_body)}B", tag="[game]")

        player.send([
            (sg_id, sg_body),
            packets.build_item_registry(),
        ])
        player.send([
            packets.build_inventory_content_hotbar(self.starting_items, self.stack_size),
            packets.build_biome_definition_list(),
            packets.build_set_spawn_position(spawn),
        ])

    def _debug_log_level_chunk_header(self, addr, body):
        """One-time sanity log for the first level_chunk packet each connection --
        re-decodes our own wire bytes with the project's Reader so the declared
        sub_chunk_count is directly visible in the log, not just assumed from
        the source. Cross-check this against server/chunk.py's
        TOTAL_SUBCHUNKS_IN_DIMENSION (must match) if chunk-related bugs recur."""
        r = Reader(body)
        cx = r.zigzag32()
        cz = r.zigzag32()
        dimension = r.zigzag32()
        sub_chunk_count = r.varint()
        cache_enabled = r.bool()
        payload = r.byte_array()
        log.debug(f"{addr} first level_chunk header: x={cx} z={cz} dimension={dimension} "
                  f"sub_chunk_count={sub_chunk_count} cache_enabled={cache_enabled} "
                  f"payload={len(payload)}B (total body {len(body)}B)", tag="[game]")

    def _send_chunks(self, player, radius):
        addr = player.session.addr
        sent = 0
        for cx in range(-radius, radius + 1):
            for cz in range(-radius, radius + 1):
                pid, body = self.world.level_chunk_packet(cx, cz)
                if sent == 0:
                    self._debug_log_level_chunk_header(addr, body)
                player.send([(pid, body)])
                sent += 1
        log.debug(f"{addr} sent {sent} level_chunk packets (radius={radius})", tag="[game]")
        player.send([
            packets.build_network_chunk_publisher_update(self.world.spawn_position, radius),
            packets.build_play_status(packets.PLAY_STATUS_PLAYER_SPAWN),
        ])
        log.info(f"{addr} sent {sent} chunks, player spawned", tag="[game]")


def logging_level_name():
    for name, val in log.LEVELS.items():
        if val == log._current_level:
            return name
    return "?"


def load_config(path="config.toml"):
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    cfg = load_config()
    server = BDSPyServer(cfg)
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
