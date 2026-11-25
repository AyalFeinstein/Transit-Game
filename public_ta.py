from player import Player

class PublicTA(Player):
    def __init__(self, player_id: int, money: int = 0, trainsets: list = None, name: str = "Public Transit Authority", line_names: list[str] = None, maitnance_costs: int = 0):
        super().__init__(player_id, money, trainsets, name, line_names, maitnance_costs)
        self.is_public_ta = True
        self.established = False
    