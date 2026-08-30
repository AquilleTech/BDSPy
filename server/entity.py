"""Minimal entity bookkeeping. Movement is currently unauthoritative (we
never send corrections, the client just uses its own prediction), but
position is tracked here so future features (other players seeing each
other, block interaction range checks, etc.) have somewhere to live."""


class Entity:
    def __init__(self, entity_id, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0)):
        self.entity_id = entity_id
        self.position = position
        self.rotation = rotation  # (yaw, pitch)

    def set_position(self, x, y, z):
        self.position = (x, y, z)

    def set_rotation(self, yaw, pitch):
        self.rotation = (yaw, pitch)


class EntityIdAllocator:
    def __init__(self, start=1):
        self._next = start

    def allocate(self):
        eid = self._next
        self._next += 1
        return eid
