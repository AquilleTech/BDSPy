from world.generator import FlatGenerator
from server.chunk import ChunkEncoder, TOTAL_SUBCHUNKS_IN_DIMENSION
from protocol import packets

GROUND_BASE_Y = -64  # dimension min Y; ground sits at the bottom of subchunk 0


class World:
    def __init__(self, ground_layer_names, spawn_x=0, spawn_z=0):
        self.generator = FlatGenerator(ground_layer_names)
        self.encoder = ChunkEncoder()
        self.spawn_x = spawn_x
        self.spawn_z = spawn_z
        self.spawn_y = GROUND_BASE_Y + len(ground_layer_names)

    @property
    def spawn_position(self):
        return (self.spawn_x, self.spawn_y, self.spawn_z)

    def level_chunk_packet(self, cx, cz):
        chunk = self.generator.generate(cx, cz)
        payload = self.encoder.encode(chunk)
        # sub_chunk_count must be the full dimension height range (24), not
        # just our 1 occupied subchunk -- see server/chunk.py's module docstring.
        return packets.build_level_chunk(cx, cz, payload, subchunk_count=TOTAL_SUBCHUNKS_IN_DIMENSION)
