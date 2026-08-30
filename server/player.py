from server.entity import Entity


class PlayerState:
    def __init__(self, session, entity_id):
        self.session = session
        self.entity = Entity(entity_id)
        self.pack_stage = 0  # 0=info sent, 1=stack sent, 2=done
        self.display_name = None
        self.xuid = None
        self.spawned = False

    @property
    def entity_id(self):
        return self.entity.entity_id

    def send(self, subpackets):
        self.session.send_batch(subpackets, threshold=1)
