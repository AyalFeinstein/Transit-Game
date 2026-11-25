class Player:
    def __init__(self,
                 player_id: int,
                 money: int = 0,
                 trainsets: list = None,
                 name: str = "",
                 line_names: list[str] = None,
                 maitnance_costs: int = 0,
                 is_ai: bool = False,
                 ai_focus: str = None,
                 ai_difficulty: str = None):
        """The Player stores all information pertaining to the player's stats:
        Money, equipment, lines, ect.
        Money = int
        lines is a list of dictionary keys to be used to read lines from the map.
        is_ai: If True, this player is computer-controlled.
        ai_focus: For AI players, one of 'lobbying', 'marketplace', 'line_expansion', 'balanced'.
        ai_difficulty: For AI players, one of 'easy', 'medium', 'hard'. Harder AIs get in-game bonuses."""
        self.money = money
        self.line_names = line_names
        self.player_id = player_id
        self.trainsets = trainsets
        self.name = name
        self.maitnance_costs = maitnance_costs
        self.is_eliminated = False
        self.is_ai = is_ai
        self.ai_focus = (ai_focus or 'balanced') if is_ai else None
        self.ai_difficulty = (ai_difficulty or 'hard') if is_ai else None
        if self.trainsets is None:
            self.trainsets = []
        if self.line_names is None:
            self.line_names = []
        self.buses = []
        self.ferries = []
        self.worker_rentals = {}
        self.lawsuit_excuses = []
        # When a human is merged into another human, they control the acquirer's company (same tab content).
        self.merged_into_player_id = None
        self.color_index = None

    def __setstate__(self, d):
        self.__dict__.update(d)
        if not hasattr(self, 'is_ai'):
            self.is_ai = False
        if not hasattr(self, 'ai_focus'):
            self.ai_focus = 'balanced' if getattr(self, 'is_ai', False) else None
        if not hasattr(self, 'ai_difficulty'):
            self.ai_difficulty = 'hard' if getattr(self, 'is_ai', False) else None
        if not hasattr(self, 'worker_rentals'):
            self.worker_rentals = {}
        if not hasattr(self, 'lawsuit_excuses'):
            self.lawsuit_excuses = []
        if not hasattr(self, 'merged_into_player_id'):
            self.merged_into_player_id = None
        if not hasattr(self, 'color_index'):
            self.color_index = None
        if not hasattr(self, 'buses'):
            self.buses = []
        if not hasattr(self, 'ferries'):
            self.ferries = []
        if getattr(self, 'line_names', None) is None:
            self.line_names = []
        if getattr(self, 'trainsets', None) is None:
            self.trainsets = []

    def get_money(self):
        return self.money

    def get_line_names(self):
        if self.line_names is None:
            self.line_names = []
        return self.line_names

    def get_player_id(self):
        return self.player_id
    
    def add_money(self, addition):
        self.money += addition

    def deduct_money(self, deduction):
        self.money -= deduction
        return self.money

    def acquire_line(self, line):
        if line.name not in self.line_names:
            self.line_names.append(line.name)

    def aquire_trainset(self, trainset):
        self.trainsets.append(trainset)

    def get_trainsets(self):
        if self.trainsets is None:
            self.trainsets = []
        return self.trainsets
    
    def get_trainsets_by_line(self, line_name: str):
        assigned_trainsets = []
        for trainset in self.trainsets:
            if trainset.get_line_assignment() == line_name:
                assigned_trainsets.append(trainset)
        return assigned_trainsets
    
    def get_unassigned_trainsets(self):
        unassigned_trainsets = []
        for trainset in self.trainsets:
            if trainset.get_line_assignment() is None:
                unassigned_trainsets.append(trainset)
        return unassigned_trainsets
    
    def get_name(self):
        return self.name
    
    def rename(self, new_name: str):
        self.name = new_name

    def remove_line(self, line_name: str):
        if line_name in self.line_names:
            self.line_names.remove(line_name)
        else:
            raise ValueError(f"Line name {line_name} not found in player's line names.")
    
    def __repr__(self):
        return f"Player(id={self.player_id}, name={self.name}, money={self.money}, lines={len(self.line_names)}, trainsets={len(self.trainsets)})"
    
    def remove_trainset(self, trainset):
        if trainset in self.trainsets:
            self.trainsets.remove(trainset)
        else:
            raise ValueError(f"Trainset {trainset} not found in player's trainsets.")
    
    def eliminate(self):
        """Mark the player as eliminated"""
        self.is_eliminated = True
    
    def is_player_eliminated(self):
        """Check if the player is eliminated"""
        return self.is_eliminated
    
    def get_engineer_count(self) -> int:
        """Get the number of engineers this player has rented"""
        from worker import ENGINEER
        rental = self.worker_rentals.get(ENGINEER.name)
        return rental.quantity if rental else 0
    
    def get_assigned_trainsets_count(self) -> int:
        """Get the number of trainsets assigned to lines"""
        count = 0
        for trainset in self.trainsets:
            if trainset.get_line_assignment() is not None:
                count += 1
        return count
    
    def get_assigned_trainsets(self):
        """Get list of trainsets assigned to lines"""
        assigned = []
        for trainset in self.trainsets:
            if trainset.get_line_assignment() is not None:
                assigned.append(trainset)
        return assigned
    
    def rent_workers(self, worker_type, quantity: int, current_year: int):
        """Rent workers of a given type"""
        from worker import WorkerRental
        if worker_type.name in self.worker_rentals:
            self.worker_rentals[worker_type.name].quantity += quantity
        else:
            self.worker_rentals[worker_type.name] = WorkerRental(worker_type, quantity, current_year)
    
    def get_worker_rental(self, worker_type_name: str):
        """Get worker rental for a type, or None if not rented"""
        return self.worker_rentals.get(worker_type_name)
    
    def get_lawyer_count(self) -> int:
        """Get the number of lawyers this player has rented"""
        from worker import LAWYER
        rental = self.worker_rentals.get(LAWYER.name)
        return rental.quantity if rental else 0
    
    def get_construction_worker_count(self) -> int:
        """Get the number of construction workers this player has rented"""
        from worker import CONSTRUCTION_WORKER
        rental = self.worker_rentals.get(CONSTRUCTION_WORKER.name)
        return rental.quantity if rental else 0
    
    def add_lawsuit_excuse(self, excuse):
        """Add a lawsuit excuse to this player"""
        self.lawsuit_excuses.append(excuse)
    
    def remove_lawsuit_excuse(self, excuse):
        """Remove a lawsuit excuse from this player"""
        if excuse in self.lawsuit_excuses:
            self.lawsuit_excuses.remove(excuse)
    
    def get_lawsuit_excuses(self):
        """Get list of lawsuit excuses"""
        return self.lawsuit_excuses.copy()

    