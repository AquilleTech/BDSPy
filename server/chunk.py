"""
Converts a world.chunk.Chunk into the raw bytes LevelChunkPacket expects.

Subchunk format: version 9, "network" (runtime-id-referencing) palette --
palette entries are plain varints indexing into the global block_properties
table sent in StartGame (see world/block.py), not embedded NBT. This avoids
implementing the block-hash algorithm entirely.

IMPORTANT: sub_chunk_count is NOT "how many subchunks have real block data".
Confirmed against Dragonfly's real chunk-sending code (server/session/chunk.go
sendNetworkChunk): SubChunkCount is always len(data.SubChunks), which Encode()
sizes to the FULL dimension height range (24 for the standard -64..319
overworld) -- every server always sends one subchunk entry per Y-slot in the
whole column, empty ones included, not just the occupied one. The empty ones
are extremely cheap: an empty Dragonfly SubChunk has zero storages, which
encodes as just 3 bytes (version, storage_count=0, abs_Y_index) with no
palette at all -- confirmed against Dragonfly's NewSubChunk (storages starts
as a nil/empty slice) and decodeSubChunk (storageCount=0 -> 0 iterations, no
error). This must match TOTAL_SUBCHUNKS_IN_DIMENSION exactly, same number as
the biome section count below (both loops size to the same full column).

Ground layers sit at the bottom of the column (world Y -64..-49, absolute
subchunk Y-index -4); every other subchunk in the column is empty air.
"""

from protocol.writer import Writer
from world import block as block_registry

SUBCHUNK_VERSION = 9

# Total subchunks across the *entire* dimension height range (-64..319 for
# the standard overworld we implicitly use, since we never send DimensionData
# to override it) = (319 - -64 + 1) / 16 = 24. This is both the sub_chunk_count
# the outer LevelChunk packet must declare, AND the number of biome sections
# the client's biome-decode loop always reads -- neither is "how many
# subchunks/biomes have real data", both are the full column height.
TOTAL_SUBCHUNKS_IN_DIMENSION = 24
MIN_SUBCHUNK_ABSOLUTE_Y_INDEX = -4  # floor(-64 / 16) -- the bottom-most subchunk's Y-index
GROUND_SUBCHUNK_POSITION = 0  # our one real subchunk is the bottom-most (index 0 of 24)

_BIOME_REUSE_PREVIOUS = bytes([0xFF])  # (0x7f << 1) | 1: "same biome as previous section"


def _pack_paletted_layer(local_indices_4096, palette_global_ids):
    w = Writer()
    palette_size = len(palette_global_ids)

    if palette_size <= 1:
        bits_per_block = 0
    else:
        bits_per_block = 1
        while (1 << bits_per_block) < palette_size:
            bits_per_block += 1
        for allowed in (1, 2, 3, 4, 5, 6, 8, 16):
            if bits_per_block <= allowed:
                bits_per_block = allowed
                break

    header = (bits_per_block << 1) | 1  # bit0 = 1 -> network/runtime palette
    w.u8(header)

    if bits_per_block > 0:
        blocks_per_word = 32 // bits_per_block
        word_count = (4096 + blocks_per_word - 1) // blocks_per_word
        for wi in range(word_count):
            word = 0
            for j in range(blocks_per_word):
                i = wi * blocks_per_word + j
                val = local_indices_4096[i] if i < 4096 else 0
                word |= (val & ((1 << bits_per_block) - 1)) << (j * bits_per_block)
            w.lu32(word)

    w.varint(palette_size)
    for gid in palette_global_ids:
        w.varint(gid)

    return w.bytes()


def _build_ground_subchunk(ground_runtime_ids):
    air_id = block_registry.runtime_id("minecraft:air")
    palette = [air_id] + list(ground_runtime_ids)
    ground_count = len(ground_runtime_ids)

    local_indices = [0] * 4096
    for x in range(16):
        for z in range(16):
            for y in range(16):
                idx = (x << 8) | (z << 4) | y
                local_indices[idx] = (1 + y) if y < ground_count else 0

    layer_bytes = _pack_paletted_layer(local_indices, palette)

    w = Writer()
    w.u8(SUBCHUNK_VERSION)
    w.u8(1)  # one storage layer (no waterlogging layer)
    w.u8(MIN_SUBCHUNK_ABSOLUTE_Y_INDEX)  # version 9: explicit absolute Y index
    w.raw(layer_bytes)
    return w.bytes()


def _build_empty_subchunk(abs_y_index):
    """An untouched (all-air) subchunk -- 0 storage layers, no palette data
    at all. Matches Dragonfly's NewSubChunk() default (storages left empty)."""
    w = Writer()
    w.u8(SUBCHUNK_VERSION)
    w.u8(0)  # storage_count = 0 -> no palette bytes follow
    w.u8(abs_y_index)
    return w.bytes()


def _build_biome_section(biome_id):
    w = Writer()
    w.u8((0 << 1) | 1)  # bits_per_block=0, network palette
    w.varint(1)
    w.varint(biome_id)
    return w.bytes()


class ChunkEncoder:
    """Caches encoded payloads by (ground_runtime_ids tuple, biome_id) since
    a flat world's columns are all identical -- no need to re-encode per chunk."""

    def __init__(self):
        self._cache = {}
        self._empty_subchunks = {
            i: _build_empty_subchunk(MIN_SUBCHUNK_ABSOLUTE_Y_INDEX + i)
            for i in range(TOTAL_SUBCHUNKS_IN_DIMENSION)
            if i != GROUND_SUBCHUNK_POSITION
        }

    def encode(self, chunk):
        key = (tuple(chunk.ground_runtime_ids), chunk.biome_id)
        if key not in self._cache:
            ground_subchunk = _build_ground_subchunk(chunk.ground_runtime_ids)
            biome_section = _build_biome_section(chunk.biome_id)

            w = Writer()
            for i in range(TOTAL_SUBCHUNKS_IN_DIMENSION):
                w.raw(ground_subchunk if i == GROUND_SUBCHUNK_POSITION else self._empty_subchunks[i])
            # One biome section per subchunk in the FULL dimension range, not
            # per sent block subchunk -- first one real, the rest "reuse previous"
            # (uniform biome everywhere, so this is both correct and cheap).
            w.raw(biome_section)
            for _ in range(TOTAL_SUBCHUNKS_IN_DIMENSION - 1):
                w.raw(_BIOME_REUSE_PREVIOUS)
            w.u8(0)  # border block list count
            # no block entities -- reader stops at buffer end
            self._cache[key] = w.bytes()
        return self._cache[key]
