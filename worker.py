"""Worker system for the game"""
from typing import Dict

class WorkerType:
    """Represents a type of worker"""
    def __init__(self, name: str, base_salary: int, description: str = ""):
        self.name = name
        self.base_salary = base_salary
        self.description = description
        self.salary_multiplier = 1.0  # Player-controlled multiplier for salary
    
    def set_salary_multiplier(self, multiplier: float):
        """Set the salary multiplier (e.g., 1.0 = base, 1.5 = 50% increase)"""
        self.salary_multiplier = max(0.5, min(5.0, multiplier))  # Limit between 0.5x and 5.0x
    
    def get_salary_multiplier(self) -> float:
        """Get the current salary multiplier"""
        return self.salary_multiplier
    
    def get_yearly_cost(self, current_year: int = 1830) -> int:
        """Calculate yearly cost with inflation and salary multiplier"""
        # Inflation factor: 1x in 1830, 10x by 2050
        inflation_factor = 1.0 + (current_year - 1830) * (5.5 - 1.0) / (2050 - 1830)
        inflation_factor = max(1.0, min(10.0, inflation_factor))
        return int(self.base_salary * inflation_factor * self.salary_multiplier)

ENGINEER = WorkerType("Engineer", base_salary=500, description="Required to operate trainsets")
CONSTRUCTION_WORKER = WorkerType("Construction Worker", base_salary=500, description="Required to build lines and stations")
MAINTENANCE_WORKER = WorkerType("Maintenance Worker", base_salary=125, description="Improves quality of lines and stations")
LAWYER = WorkerType("Lawyer", base_salary=1000, description="Helps win lawsuits")

class WorkerRental:
    """Represents a player's rental of workers"""
    def __init__(self, worker_type: WorkerType, quantity: int, current_year: int):
        self.worker_type = worker_type
        self.quantity = quantity
        self.rental_year = current_year
        self.strike_turns_remaining = 0  # Number of turns workers are on strike
        # Initialize demand based on current year
        import random
        base_demand = 1.0 + (current_year - 1830) * (5.0 - 1.0) / (2050 - 1830)
        base_demand = max(1.0, min(5.0, base_demand))
        variance = base_demand * 0.1
        self.demanded_multiplier = random.uniform(base_demand - variance, base_demand + variance)
        self.demanded_multiplier = max(1.0, min(5.0, self.demanded_multiplier))
    
    def get_yearly_cost(self, current_year: int) -> int:
        """Get the yearly cost for this rental"""
        return self.worker_type.get_yearly_cost(current_year) * self.quantity
    
    def is_on_strike(self) -> bool:
        """Check if workers are currently on strike"""
        return self.strike_turns_remaining > 0
    
    def advance_turn(self, current_year: int):
        """Advance one turn - update strike status and check for new demands"""
        import random
        
        # Reduce strike turns
        if self.strike_turns_remaining > 0:
            self.strike_turns_remaining -= 1
            # Don't update demands during strikes - keep them frozen
            return
        
        # Workers gradually demand more money, ending at 5x by 2050
        # Linear progression from 1.0x in 1830 to 5.0x in 2050
        # Base demand increases linearly with time
        base_demand = 1.0 + (current_year - 1830) * (5.0 - 1.0) / (2050 - 1830)
        base_demand = max(1.0, min(5.0, base_demand))  # Clamp to 1.0-5.0
        
        # Add some random variance (±10%)
        variance = base_demand * 0.1
        self.demanded_multiplier = random.uniform(base_demand - variance, base_demand + variance)
        self.demanded_multiplier = max(1.0, min(5.0, self.demanded_multiplier))  # Clamp to 1.0-5.0
        
        # Check if current salary triggers a strike
        # Strikes only happen if workers are being underpaid by at least 0.5x
        # (i.e., current salary must be at least 0.5x below demand)
        current_multiplier = self.worker_type.get_salary_multiplier()
        
        # Calculate how much underpaid workers are
        underpayment = self.demanded_multiplier - current_multiplier
        
        # Strike happens only if underpaid by at least 0.5x
        if (underpayment >= 0.5 and 
            self.strike_turns_remaining == 0):
            # Workers go on strike!
            self.strike_turns_remaining = random.randint(1, 5)
    
    def will_strike_next_turn(self, current_year: int) -> bool:
        """Check if workers will go on strike next turn (before advancing)"""
        # If already on strike, they won't start a new strike next turn (they're already striking)
        if self.strike_turns_remaining > 0:
            return False
        
        current_multiplier = self.worker_type.get_salary_multiplier()
        
        # Check if current salary would trigger a strike with current demand
        current_underpayment = self.demanded_multiplier - current_multiplier
        
        # If already underpaid by 0.5x or more, they will strike next turn
        if current_underpayment >= 0.5:
            return True
        
        # Also check what demand will be next year (simulating advance_turn logic)
        next_year = current_year + 1
        base_demand = 1.0 + (next_year - 1830) * (5.0 - 1.0) / (2050 - 1830)
        base_demand = max(1.0, min(5.0, base_demand))
        
        # The actual demand next year will be base_demand ± 10%, so check worst case
        # (highest possible demand = base_demand + 10%)
        variance = base_demand * 0.1
        max_possible_demand = base_demand + variance
        max_possible_demand = max(1.0, min(5.0, max_possible_demand))
        
        # Check if current salary would trigger a strike with next year's max possible demand
        future_underpayment = max_possible_demand - current_multiplier
        
        # Will strike if underpaid by at least 0.5x next turn
        return future_underpayment >= 0.5

