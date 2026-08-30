"""A Chunk holds the logical block/biome data for one column. Encoding this
into the wire format lives in server/chunk.py -- this module only represents
the data itself, independent of how it's transmitted."""


class Chunk:
    def __init__(self, x, z, ground_runtime_ids, biome_id):
        self.x = x
        self.z = z
        self.ground_runtime_ids = ground_runtime_ids  # bottom-to-top, global block runtime ids
        self.biome_id = biome_id

    def block_at_local_y(self, y, air_runtime_id):
        """y relative to the bottom of the world's single occupied subchunk."""
        if y < len(self.ground_runtime_ids):
            return self.ground_runtime_ids[y]
        return air_runtime_id
