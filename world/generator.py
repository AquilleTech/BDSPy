from world import block
from world import biome
from world.chunk import Chunk


class FlatGenerator:
    """Every column is identical: same ground layers, same biome. Simplest
    possible generator, but kept as its own class so a real generator (noise,
    structures, etc.) could replace it without touching anything else."""

    def __init__(self, ground_layer_names, biome_id=biome.PLAINS):
        self.ground_runtime_ids = [
            block.runtime_id(block.GROUND_BLOCK_NAMES[name]) for name in ground_layer_names
        ]
        self.biome_id = biome_id

    def generate(self, cx, cz):
        return Chunk(cx, cz, self.ground_runtime_ids, self.biome_id)
