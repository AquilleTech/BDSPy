"""
Block registry. Loads canonical_block_states.nbt -- the client's own
authoritative block palette (source: pmmp/BedrockData, CC0-1.0, matches
protocol version 1001 / MC 1.26.30) -- and exposes lookups so our chunk
data's runtime IDs match exactly what the vanilla client expects.

Also defines the 5 blocks the player can place, with the network item IDs
we hand out via item_registry.
"""

import os
from protocol import types as nbt

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data",
                          "canonical_block_states.nbt")

_entries = None
_by_name = None


def _load():
    global _entries, _by_name
    if _entries is not None:
        return
    with open(DATA_PATH, "rb") as f:
        data = f.read()
    _entries = nbt.load_all_compounds(data)
    _by_name = {}
    for idx, e in enumerate(_entries):
        _by_name.setdefault(e["name"], []).append((idx, e["states"]))


def all_entries():
    _load()
    return _entries


def runtime_id(name, states=None):
    _load()
    candidates = _by_name.get(name)
    if not candidates:
        raise KeyError(f"unknown block: {name}")
    if states is None:
        return candidates[0][0]
    for idx, st in candidates:
        if st == states:
            return idx
    raise KeyError(f"no variant of {name} with states {states}")


def _typed_states(states):
    typed = {}
    for k, v in states.items():
        if isinstance(v, str):
            typed[k] = (nbt.TAG_STRING, v)
        elif isinstance(v, bool):
            typed[k] = (nbt.TAG_BYTE, int(v))
        elif isinstance(v, int):
            typed[k] = (nbt.TAG_INT, v)
        else:
            raise TypeError(f"unhandled state value type for {k}: {type(v)}")
    return typed


def build_block_properties_nbt():
    """Encode the full palette as StartGame.block_properties expects.

    Per the real schema, each entry is a container of two SEPARATE fields --
    not one merged NBT compound like an earlier version of this function
    assumed:
      - "name": a plain protocol string (varint length + utf8, no NBT framing)
      - "state": a full NBT root tag containing just the states dict

    Verified against prismarine-nbt directly (the ground-truth codec
    bedrock-protocol itself uses) -- see BDSPy dev notes for the schema
    dump that caught this.
    """
    from protocol.writer import Writer as ProtocolWriter

    entries = all_entries()
    out = bytearray()
    for e in entries:
        pw = ProtocolWriter()
        pw.string(e["name"])
        out += pw.bytes()

        nw = nbt.Writer()
        out += nw.root_compound(_typed_states(e["states"]))
    return bytes(out), len(entries)


# ---------------- ground layer names (world generation uses these) ----------------

GROUND_BLOCK_NAMES = {
    "cobblestone": "minecraft:cobblestone",
    "dirt": "minecraft:dirt",
    "grass_block": "minecraft:grass_block",
}


# ---------------- the 5 placeable items ----------------
# name -> (network_id, block_name, block_states)
PLACEABLE_ITEMS = {
    "minecraft:grass_block": (1, "minecraft:grass_block", {}),
    "minecraft:dirt": (2, "minecraft:dirt", {}),
    "minecraft:oak_log": (3, "minecraft:oak_log", {"pillar_axis": "y"}),
    "minecraft:cobblestone": (4, "minecraft:cobblestone", {}),
    "minecraft:oak_planks": (5, "minecraft:oak_planks", {}),
}

PLACEABLE_ITEM_ORDER = [
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:oak_log",
    "minecraft:cobblestone",
    "minecraft:oak_planks",
]

# short name (as used in config.toml) -> full item name
SHORT_NAME_TO_FULL = {
    "grass_block": "minecraft:grass_block",
    "dirt": "minecraft:dirt",
    "oak_log": "minecraft:oak_log",
    "cobblestone": "minecraft:cobblestone",
    "oak_planks": "minecraft:oak_planks",
}


def item_registry_entries():
    """List of (name, network_id) for the item_registry packet."""
    return [(name, PLACEABLE_ITEMS[name][0]) for name in PLACEABLE_ITEM_ORDER]


def item_block_runtime_id(item_name):
    _, block_name, states = PLACEABLE_ITEMS[item_name]
    return runtime_id(block_name, states if states else None)


def item_network_id(item_name):
    return PLACEABLE_ITEMS[item_name][0]
