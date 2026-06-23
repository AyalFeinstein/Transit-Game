"""Agency system for non-player entities that can sue players"""
import random
from typing import Optional
from lawsuit import LawsuitExcuse, LAWSUIT_EXCUSES

class Agency:
    """Represents a non-player agency that can file lawsuits"""
    def __init__(self, name: str, activation_year: int, description: str = ""):
        self.name = name
        self.activation_year = activation_year
        self.description = description
        self.lawyers = 0  # Will be set randomly when suing
    
    def is_active(self, current_year: int) -> bool:
        """Check if this agency is active at the given year"""
        return current_year >= self.activation_year
    
    def get_lawyers(self) -> int:
        """Get the number of lawyers (1-5 random)"""
        if self.lawyers == 0:
            self.lawyers = random.randint(1, 5)
        return self.lawyers
    
    def reset_lawyers(self):
        """Reset lawyers for next lawsuit (will be randomized again)"""
        self.lawyers = 0
    
    def can_sue_for(self, excuse: LawsuitExcuse, current_year: int) -> bool:
        """Check if this agency can sue for a specific excuse"""
        if not self.is_active(current_year):
            return False
        
        # NIMBYs can sue for anything
        if self.name == "NIMBYs":
            return True
        
        # EPA can only sue for environmental damage
        if self.name == "EPA":
            return excuse.cause == "Environmental Damage"
        
        return False
    
    def get_available_excuses(self, current_year: int) -> list[LawsuitExcuse]:
        """Get list of excuses this agency can use"""
        if not self.is_active(current_year):
            return []
        
        available = []
        for excuse in LAWSUIT_EXCUSES:
            if self.can_sue_for(excuse, current_year):
                available.append(excuse)
        
        return available

# Define agencies
EPA = Agency("EPA", 1970, "Environmental Protection Agency - can sue for environmental damage")
NIMBYs = Agency("NIMBYs", 1890, "Not In My Backyard activists - can sue for any reason")

AGENCIES = [EPA, NIMBYs]

