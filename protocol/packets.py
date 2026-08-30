import json
import base64
import struct
import uuid as uuidlib

from protocol.writer import Writer
from protocol.reader import Reader
from protocol import types as nbt
from world import block as block_registry

# --- packet IDs (from minecraft-data protocol.json, version 1.26.30 / proto 1001) ---
ID_LOGIN = 1
ID_PLAY_STATUS = 2
ID_RESOURCE_PACKS_INFO = 6
ID_RESOURCE_PACK_STACK = 7
ID_RESOURCE_PACK_CLIENT_RESPONSE = 8
ID_START_GAME = 11
ID_SET_SPAWN_POSITION = 43
ID_DISCONNECT = 5
ID_INVENTORY_CONTENT = 49
ID_PLAYER_HOTBAR = 48
ID_LEVEL_CHUNK = 58
ID_SET_LOCAL_PLAYER_AS_INITIALIZED = 113
ID_NETWORK_SETTINGS = 143
ID_REQUEST_NETWORK_SETTINGS = 193
ID_BIOME_DEFINITION_LIST = 122
ID_AVAILABLE_ENTITY_IDENTIFIERS = 119
ID_REQUEST_CHUNK_RADIUS = 69
ID_CHUNK_RADIUS_UPDATE = 70
ID_NETWORK_CHUNK_PUBLISHER_UPDATE = 121
ID_ITEM_REGISTRY = 162
ID_SERVERBOUND_LOADING_SCREEN = 312
ID_PLAYER_AUTH_INPUT = 144
ID_MOVE_PLAYER = 19

PLAY_STATUS_LOGIN_SUCCESS = 0
PLAY_STATUS_PLAYER_SPAWN = 3


# ---------------- login parsing (unverified -- offline mode) ----------------

def _b64_json(segment):
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def parse_login(body):
    r = Reader(body)
    protocol_version = struct.unpack(">i", r.raw(4))[0]  # big-endian, unlike everything else
    r.varint()  # encapsulated length prefix
    identity_len = struct.unpack("<i", r.raw(4))[0]
    identity_json = r.raw(identity_len).decode("utf-8")
    client_len = struct.unpack("<i", r.raw(4))[0]
    client_jwt = r.raw(client_len).decode("utf-8")

    display_name = "Player"
    xuid = "0"
    try:
        chain = json.loads(identity_json)["chain"]
        for jwt in chain:
            parts = jwt.split(".")
            if len(parts) < 2:
                continue
            payload = _b64_json(parts[1])
            if "extraData" in payload:
                extra = payload["extraData"]
                display_name = extra.get("displayName", display_name)
                xuid = extra.get("XUID", xuid)
    except Exception:
        pass

    return {
        "protocol_version": protocol_version,
        "display_name": display_name,
        "xuid": xuid,
    }


def parse_request_chunk_radius(body):
    r = Reader(body)
    return r.zigzag32()


def parse_serverbound_loading_screen(body):
    r = Reader(body)
    loading_type = r.zigzag32()  # 0=unknown, 1=start, 2=end
    return loading_type


# ---------------- simple outgoing packets ----------------

def build_play_status(status):
    w = Writer()
    w.raw(struct.pack(">i", status))  # play_status.status is big-endian i32
    return (ID_PLAY_STATUS, w.bytes())


def build_network_settings(compression_threshold=1):
    w = Writer()
    w.lu16(compression_threshold)
    w.lu16(0)  # compression_algorithm = deflate
    w.bool(False)  # client_throttle
    w.u8(0)
    w.lf32(0.0)
    return (ID_NETWORK_SETTINGS, w.bytes())


def build_resource_packs_info():
    w = Writer()
    w.bool(False)  # must_accept
    w.bool(False)  # has_addons
    w.bool(False)  # has_scripts
    w.bool(False)  # disable_vibrant_visuals
    w.uuid(uuidlib.UUID(int=0))
    w.string("")
    w.li16(0)  # texture_packs count
    return (ID_RESOURCE_PACKS_INFO, w.bytes())


def build_resource_pack_stack():
    w = Writer()
    w.bool(False)  # must_accept
    w.varint(0)  # resource_packs count
    w.string("*")  # game_version
    w.li32(0)  # experiments count
    w.bool(False)  # experiments_previously_used
    w.bool(False)  # has_editor_packs
    return (ID_RESOURCE_PACK_STACK, w.bytes())


def build_disconnect(message, hide_reason=False):
    w = Writer()
    w.zigzag32(0)  # reason = unknown
    w.bool(hide_reason)
    if not hide_reason:
        w.string(message)
        w.string(message)
    return (ID_DISCONNECT, w.bytes())


# ---------------- StartGame ----------------

def build_start_game(entity_id, spawn_xyz, world_name="BDSPy Flat World", seed=0):
    x, y, z = spawn_xyz
    w = Writer()
    w.zigzag64(entity_id)
    w.varint64(entity_id)
    w.zigzag32(1)  # player_gamemode = creative
    w.vec3f(float(x), float(y), float(z))
    w.vec2f(0.0, 0.0)
    w.lu64(seed)
    w.li16(0)  # biome_type (legacy, unused)
    w.string("plains")
    w.zigzag32(0)  # dimension = overworld
    w.zigzag32(2)  # generator = flat
    w.zigzag32(1)  # world_gamemode = creative
    w.bool(False)  # hardcore
    w.zigzag32(1)  # difficulty = easy
    w.block_coords(x, y, z)  # spawn_position
    w.bool(False)  # achievements_disabled
    w.zigzag32(0)  # editor_world_type = not_editor
    w.bool(False)  # created_in_editor
    w.bool(False)  # exported_from_editor
    w.zigzag32(-1)  # day_cycle_stop_time
    w.zigzag32(0)  # edu_offer
    w.bool(False)  # edu_features_enabled
    w.string("")  # edu_product_uuid
    w.lf32(0.0)  # rain_level
    w.lf32(0.0)  # lightning_level
    w.bool(False)  # has_confirmed_platform_locked_content
    w.bool(True)  # is_multiplayer
    w.bool(True)  # broadcast_to_lan
    w.zigzag32(0)  # xbox_live_broadcast_mode
    w.zigzag32(0)  # platform_broadcast_mode
    w.bool(True)  # enable_commands
    w.bool(False)  # is_texturepacks_required
    w.varint(0)  # gamerules count
    w.li32(0)  # experiments count
    w.bool(False)  # experiments_previously_used
    w.bool(False)  # bonus_chest
    w.bool(True)  # map_enabled
    w.zigzag32(2)  # player_permissions = operator (Varint32/zigzag, NOT plain u8 -- confirmed against gophertunnel v1.57.0, the exact protocol-1001-matched source)
    w.li32(4)  # server_chunk_tick_range
    w.bool(False)  # has_locked_behavior_pack
    w.bool(False)  # has_locked_resource_pack
    w.bool(False)  # is_from_locked_world_template
    w.bool(False)  # msa_gamertags_only
    w.bool(False)  # is_from_world_template
    w.bool(False)  # is_world_template_option_locked
    w.bool(False)  # only_spawn_v1_villagers
    w.bool(False)  # persona_disabled
    w.bool(False)  # custom_skins_disabled
    w.bool(False)  # emote_chat_muted
    w.string("1.26.30")  # game_version
    w.li32(0)  # limited_world_width
    w.li32(0)  # limited_world_length
    w.bool(False)  # is_new_nether
    w.string("")  # edu_resource_uri.button_name
    w.string("")  # edu_resource_uri.link_uri
    w.bool(False)  # experimental_gameplay_override
    w.u8(0)  # chat_restriction_level = none
    w.bool(False)  # disable_player_interactions
    w.zigzag32(0)  # server_editor_connection_policy
    w.bool(False)  # allow_anonymous_block_drops_in_editor_worlds
    w.string("bdspy_flat")  # level_id
    w.string(world_name)
    w.string("")  # premium_world_template_id
    w.bool(False)  # is_trial
    w.zigzag32(0)  # rewind_history_size
    w.bool(False)  # server_authoritative_block_breaking
    w.li64(0)  # current_tick
    w.zigzag32(0)  # enchantment_seed

    block_props, count = block_registry.build_block_properties_nbt()
    w.varint(count)
    w.raw(block_props)

    w.string("")  # multiplayer_correlation_id
    w.bool(False)  # server_authoritative_inventory
    w.string("BDSPy")  # engine
    w.raw(nbt.Writer().root_compound({}))  # property_data: proper empty NBT root tag (was TAG_END=0x00 instead of TAG_COMPOUND=0x0A -- same 3 bytes, wrong leading type byte)
    w.lu64(0)  # block_pallette_checksum
    w.uuid(uuidlib.UUID(int=0))  # world_template_id
    w.bool(False)  # client_side_generation
    w.bool(False)  # block_network_ids_are_hashes -- we use direct palette indices
    w.bool(False)  # server_controlled_sound
    w.bool(False)  # is_chat_logging
    w.bool(False)  # has_server_join_info
    w.string("")  # server_identifier
    w.string("")  # scenario_identifier
    w.string("")  # world_identifier
    w.string("")  # owner_identifier
    return (ID_START_GAME, w.bytes())


# ---------------- item registry / inventory ----------------

def encode_item_stack(item_name, count=64):
    network_id = block_registry.item_network_id(item_name)
    block_runtime_id = block_registry.item_block_runtime_id(item_name)

    w = Writer()
    w.li16(network_id)
    w.lu16(count)
    w.varint(0)  # metadata
    w.bool(False)  # has_stack_id
    w.varint(block_runtime_id)

    extra = Writer()
    extra.li16(0)  # has_nbt marker (0 = no nbt)
    extra.li32(0)  # can_place_on count
    extra.li32(0)  # can_destroy count
    extra_bytes = extra.bytes()
    w.varint(len(extra_bytes))
    w.raw(extra_bytes)
    return w.bytes()


def encode_empty_stack():
    """An 'air' ItemV4 slot. Unlike a real item, an empty stack (network_id=0)
    has NO extra fields at all -- confirmed against gophertunnel v1.57.0
    (the exact protocol-1001-matched source): the real encoder special-cases
    network_id==0 to write just a single zero-length varuint marker after
    block_runtime_id, with no has_nbt/can_place_on/can_destroy sub-fields.
    (minecraft-data's generic ItemV4 schema doesn't model this special case,
    which is why bedrock-protocol accepted our old, wrong version too -- it
    validates shape, not this real-client-specific shortcut.)"""
    w = Writer()
    w.li16(0)   # network_id
    w.lu16(0)   # count
    w.varint(0)  # metadata
    w.bool(False)  # has_stack_id
    w.varint(0)  # block_runtime_id
    w.varint(0)  # extra length = 0 (no sub-fields at all for an empty stack)
    return w.bytes()


def build_item_registry():
    w = Writer()
    entries = block_registry.item_registry_entries()
    w.varint(len(entries))
    for name, network_id in entries:
        w.string(name)
        w.li16(network_id)
        w.bool(False)  # component_based
        w.zigzag32(0)  # version = legacy
        w.raw(nbt.Writer().root_compound({}))  # nbt: proper empty compound root tag
    return (ID_ITEM_REGISTRY, w.bytes())


def build_inventory_content_hotbar(item_names, stack_size=64):
    w = Writer()
    w.varint(0)  # window_id = inventory
    w.varint(len(item_names))
    for name in item_names:
        w.raw(encode_item_stack(name, count=stack_size))
    w.u8(29)  # container_id = ContainerSlotType.inventory
    w.bool(False)  # dynamic_container_id option = absent
    w.raw(encode_empty_stack())  # storage_item
    return (ID_INVENTORY_CONTENT, w.bytes())


# ---------------- biomes / entities (minimal) ----------------

def build_biome_definition_list():
    w = Writer()
    w.varint(1)  # biome_definitions count
    w.li16(0)  # name_index -> string_list[0]
    w.lu16(1)  # biome_id = plains
    w.lf32(0.8)  # temperature
    w.lf32(0.4)  # downfall
    w.lf32(0.0)  # snow_foliage
    w.lf32(0.1)  # depth
    w.lf32(0.05)  # scale
    w.li32(4159204)  # map_water_colour
    w.bool(False)  # rain
    w.bool(False)  # tags option = absent
    w.bool(False)  # chunk_generation option = absent
    w.varint(1)  # string_list count
    w.string("plains")
    return (ID_BIOME_DEFINITION_LIST, w.bytes())


def build_available_entity_identifiers():
    """Not currently sent -- Pumpkin (a real, working Bedrock server
    implementation) never sends this packet at all, and real clients seem
    fine without it (presumably falling back to their own built-in vanilla
    entity list). Kept here in case custom entities are added later."""
    nw = nbt.Writer()
    typed = {
        "idlist": (nbt.TAG_LIST, {"_list_type": nbt.TAG_COMPOUND, "value": []}),
    }
    data = nw.root_compound(typed)
    w = Writer()
    w.raw(data)
    return (ID_AVAILABLE_ENTITY_IDENTIFIERS, w.bytes())


# ---------------- world / spawn ----------------

def build_set_spawn_position(xyz, dimension=0):
    x, y, z = xyz
    w = Writer()
    w.zigzag32(1)  # spawn_type = world
    w.block_coords(x, y, z)
    w.zigzag32(dimension)
    w.block_coords(x, y, z)
    return (ID_SET_SPAWN_POSITION, w.bytes())


def build_chunk_radius_update(radius):
    w = Writer()
    w.zigzag32(radius)
    return (ID_CHUNK_RADIUS_UPDATE, w.bytes())


def build_network_chunk_publisher_update(xyz, radius):
    x, y, z = xyz
    w = Writer()
    w.block_coords(x, y, z)
    w.varint(radius)
    w.lu32(0)  # saved_chunks count
    return (ID_NETWORK_CHUNK_PUBLISHER_UPDATE, w.bytes())


def build_level_chunk(chunk_x, chunk_z, payload, subchunk_count=1, dimension=0):
    w = Writer()
    w.zigzag32(chunk_x)
    w.zigzag32(chunk_z)
    w.zigzag32(dimension)
    w.varint(subchunk_count)  # positive count -> highest_subchunk_count field is skipped
    w.bool(False)  # cache_enabled
    w.byte_array(payload)
    return (ID_LEVEL_CHUNK, w.bytes())
