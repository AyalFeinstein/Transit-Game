"""Lawsuit system for the game"""
import random
from typing import Dict, Optional
from player import Player

class LawsuitExcuse:
    """Represents a reason/excuse to sue another player"""
    def __init__(self, cause: str, base_damages: int, description: str = ""):
        self.cause = cause
        self.base_damages = base_damages
        self.description = description
    
    def get_damages(self, current_year: int) -> int:
        """Calculate damages with inflation"""
        from inflation_utils import get_inflation_factor
        inflation_factor = get_inflation_factor(current_year, max_factor=5.5)
        return int(self.base_damages * inflation_factor)


# Define lawsuit excuses
LAWSUIT_EXCUSES = [
    LawsuitExcuse("Safety Violation", 5000, "Alleged safety violations in train operations"),
    LawsuitExcuse("Property Damage", 3000, "Damage to property caused by train operations"),
    LawsuitExcuse("Noise Pollution", 2000, "Excessive noise from train operations"),
    LawsuitExcuse("Environmental Damage", 4000, "Environmental harm from train operations"),
    LawsuitExcuse("Breach of Contract", 6000, "Failure to fulfill contractual obligations"),
    LawsuitExcuse("Discrimination", 3500, "Alleged discrimination in hiring or operations"),
    LawsuitExcuse("Accident", 8000, "Train accident causing harm"),
    LawsuitExcuse("Service Interruption", 2500, "Unjustified service interruptions"),
    LawsuitExcuse("Monopolizing", 7000, "Building a line that shares 3+ points with an existing line (only after 1904)"),
    LawsuitExcuse("Wrongful Termination", 4500, "Unlawful dismissal of workers"),
    LawsuitExcuse("Wage Dispute", 3000, "Failure to pay agreed wages or overtime"),
    LawsuitExcuse("Emissions Violation", 5500, "Exceeding permitted emissions or pollution standards"),
    LawsuitExcuse("Accessibility Violation", 4000, "Failure to provide required accessibility accommodations"),
    LawsuitExcuse("False Advertising", 2500, "Misleading claims about service or schedules"),
]

class Lawsuit:
    """Represents a lawsuit between players or agencies"""
    def __init__(self, plaintiff, defendant, excuse: LawsuitExcuse = None, year: int = 1830, made_up_cause: str = ""):
        self.plaintiff = plaintiff  # Can be Player or Agency
        self.defendant = defendant  # Can be Player
        self.excuse = excuse
        self.year = year
        self.made_up_cause = made_up_cause  # For lawsuits without a real excuse
        if excuse:
            self.damages = excuse.get_damages(year)
            self.cause = excuse.cause
        else:
            from inflation_utils import get_inflation_factor
            avg_base = sum(e.base_damages for e in LAWSUIT_EXCUSES) // len(LAWSUIT_EXCUSES)
            self.damages = int(avg_base * get_inflation_factor(year))
            self.cause = made_up_cause
        self.resolved = False
        self.winner = None
    
    def resolve(self, plaintiff_lawyers: int, defendant_lawyers: int) -> 'Player':
        """Resolve the lawsuit based on number of lawyers each side has"""
        # Base chance: 75% if there's a real excuse, 10% if no excuse (made-up cause)
        has_cause = self.excuse is not None
        base_plaintiff_chance = 0.75 if has_cause else 0.10
        
        # Lawyer modifications: each lawyer adds 5% to their side's chance
        # Maximum 95% chance, minimum 5% chance
        plaintiff_chance = base_plaintiff_chance + (plaintiff_lawyers * 0.05)
        defendant_chance = (1.0 - base_plaintiff_chance) + (defendant_lawyers * 0.05)
        
        # Normalize so they sum to 1.0
        total_chance = plaintiff_chance + defendant_chance
        plaintiff_chance = plaintiff_chance / total_chance
        defendant_chance = defendant_chance / total_chance
        
        # Clamp to reasonable bounds
        plaintiff_chance = max(0.05, min(0.95, plaintiff_chance))
        defendant_chance = 1.0 - plaintiff_chance
        
        # Random choice
        random_value = random.random()
        
        # Both sides pay court costs (10% of damages each)
        court_costs = int(self.damages * 0.1)
        self.defendant.deduct_money(court_costs)
        # Only deduct from plaintiff if they're a Player (not an Agency)
        if hasattr(self.plaintiff, 'deduct_money'):
            self.plaintiff.deduct_money(court_costs)
        
        if random_value < plaintiff_chance:
            self.winner = self.plaintiff
            self.defendant.deduct_money(self.damages)
            if hasattr(self.plaintiff, 'add_money'):
                self.plaintiff.add_money(self.damages)
        else:
            self.winner = self.defendant
            reparations = int(self.damages * 0.5)  
            self.defendant.add_money(reparations)
            if hasattr(self.plaintiff, 'deduct_money'):
                self.plaintiff.deduct_money(reparations)
        
        self.resolved = True
        return self.winner
