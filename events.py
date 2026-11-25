"""Event system for timeline-based game events"""

class GameEvent:
    """Represents a game event that occurs at a specific year"""
    def __init__(self, name: str, year: int, description: str):
        self.name = name
        self.year = year
        self.description = description
    
    def is_active(self, current_year: int) -> bool:
        """Check if this event is active at the given year"""
        return current_year >= self.year
    
    def get_effect(self, current_year: int) -> dict:
        """Get the effect of this event at the given year. Returns a dict of effect modifiers."""
        if not self.is_active(current_year):
            return {}
        return {}


class AreaGrowthBonusEvent(GameEvent):
    """Extra yearly population growth inside some areas between two years (inclusive).

    ``areas`` is a sequence of lat/lon boxes (dicts with min_lat, max_lat, min_lon, max_lon);
    a place inside any of them grows ``bonus`` faster each year (0.01 = 1% more). Maps list
    these in their own files (see a preset's ``get_events``).
    """
    def __init__(self, name: str, start_year: int, end_year: int, areas, bonus: float, description: str = ""):
        super().__init__(name=name, year=int(start_year), description=description or name)
        self.end_year = int(end_year)
        self.areas = tuple(dict(a) for a in areas)
        self.bonus = float(bonus)

    def get_effect(self, current_year: int) -> dict:
        if not (self.year <= current_year <= self.end_year):
            return {}
        return {
            'area_population_change': {'bounds_list': self.areas, 'change_percent': self.bonus},
            'description': f"{self.name}: {self.bonus * 100:.1f}% extra yearly growth",
        }


class AutomobileInventionEvent(GameEvent):
    """The invention of the automobile, which gradually reduces train profits"""
    def __init__(self):
        super().__init__(
            name="Invention of the Automobile",
            year=1910,  # Mass production era begins
            description="The automobile becomes widely available, providing competition to rail transport"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """Calculate population reduction based on years since automobile invention
        People move to suburbs and use cars instead of trains"""
        if not self.is_active(current_year):
            return {}
        
        years_since_invention = current_year - self.year
        
        if current_year <= 2020:
            # From 1910 to 2020: 0% to 60% population reduction (people move to suburbs)
            # Linear interpolation
            period_years = 2020 - self.year  # 110 years
            if period_years == 0:
                population_reduction = 0.0
            else:
                progress = years_since_invention / period_years
                progress = min(1.0, progress)  # Cap at 2020
                population_reduction = progress * 0.6  # 0% to 60%
        else:
            # From 2020 to 2050: 60% to 70% population reduction (continued suburbanization)
            period_years = 2050 - 2020
            years_since_2020 = current_year - 2020
            if period_years == 0:
                population_reduction = 0.6
            else:
                progress = years_since_2020 / period_years
                progress = min(1.0, progress)
                population_reduction = 0.6 + (progress * 0.1)  # 60% to 70%
        
        return {
            'population_reduction': population_reduction,
            'description': f"Automobile competition reduces population by {population_reduction*100:.1f}%"
        }


class Covid19Event(GameEvent):
    """COVID-19 pandemic, which dramatically reduces profits temporarily"""
    def __init__(self):
        super().__init__(
            name="COVID-19 Pandemic",
            year=2020,
            description="The COVID-19 pandemic causes massive disruption to public transit"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """Suppress transit line profits (ridership demand) without applying a city-wide population cut."""
        if not self.is_active(current_year):
            return {}
        years_since_start = current_year - self.year
        if years_since_start < 0:
            return {}
        recovery_period = 4
        if years_since_start > recovery_period:
            return {}
        recovery_progress = years_since_start / recovery_period
        # Year 2020: strong ridership hit; linear recovery to normal by start of year after the 4th recovery year
        profit_multiplier = 0.35 + 0.65 * recovery_progress
        pct = int(round(profit_multiplier * 100))
        return {
            'profit_multiplier': profit_multiplier,
            'description': f"COVID-19 pandemic: transit demand at ~{pct}% of normal, recovering over {recovery_period} years",
        }


class WorkerProtectionActEvent(GameEvent):
    """1925 Worker Protection Act - prevents firing workers on strike"""
    def __init__(self):
        super().__init__(
            name="Worker Protection Act",
            year=1925,
            description="New labor laws protect workers on strike from being fired"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """This event doesn't affect profits, but prevents firing workers on strike"""
        if not self.is_active(current_year):
            return {}
        return {
            'prevents_firing_striking_workers': True,
            'description': "Worker Protection Act: Cannot fire workers who are on strike"
        }


class TrustBustingEvent(GameEvent):
    """1904 Trust Busting - allows monopolizing lawsuits"""
    def __init__(self):
        super().__init__(
            name="Trust Busting",
            year=1904,
            description="Antitrust laws allow lawsuits against monopolistic practices"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """This event enables monopolizing lawsuits"""
        if not self.is_active(current_year):
            return {}
        return {
            'allows_monopolizing_lawsuits': True,
            'description': "Trust Busting: You can now sue for monopolizing practices"
        }


class EnvironmentalProtectionActEvent(GameEvent):
    """1970 Environmental Protection Act - enables EPA"""
    def __init__(self):
        super().__init__(
            name="Environmental Protection Act",
            year=1970,
            description="Environmental Protection Agency is established and can sue for environmental damage"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """This event enables EPA lawsuits"""
        if not self.is_active(current_year):
            return {}
        return {
            'enables_epa_lawsuits': True,
            'description': "Environmental Protection Act: EPA can now sue for environmental damage"
        }


class GovernmentAutomobilePreferenceEvent(GameEvent):
    """Mid-1940s to 1970s: Government policies favor automobiles over public transportation"""
    def __init__(self):
        super().__init__(
            name="Government Automobile Preference",
            year=1945,
            description="Post-WWII government policies heavily favor automobile infrastructure over public transportation, leading to highway construction, suburbanization, and reduced transit funding"
        )
        self.end_year = 1975  # Event ends in mid-1970s when environmental concerns rise
    
    def get_effect(self, current_year: int) -> dict:
        """Calculate effects: reduced ridership and increased construction costs for public transit
        The effect peaks in the 1950s-1960s and gradually decreases by 1975"""
        if not self.is_active(current_year) or current_year > self.end_year:
            return {}
        
        years_since_start = current_year - self.year
        total_period = self.end_year - self.year  # 30 years (1945-1975)
        
        if total_period == 0:
            return {}
        
        # Effect peaks around 1960 (midpoint) and gradually decreases by 1975
        # Peak effect: 25% population reduction, 30% construction cost increase
        peak_year = 1960
        years_to_peak = peak_year - self.year  # 15 years
        
        if current_year <= peak_year:
            # Increasing effect from 1945 to 1960
            progress = years_since_start / years_to_peak if years_to_peak > 0 else 1.0
            progress = min(1.0, progress)
            population_reduction = progress * 0.25  # 0% to 25%
            construction_cost_multiplier = 1.0 + (progress * 0.30)  # 1.0x to 1.3x
        else:
            # Decreasing effect from 1960 to 1975
            years_from_peak = current_year - peak_year
            decline_period = self.end_year - peak_year  # 15 years
            if decline_period == 0:
                progress = 1.0
            else:
                progress = years_from_peak / decline_period
                progress = min(1.0, progress)
            # Decline from peak to zero
            population_reduction = 0.25 * (1.0 - progress)  # 25% to 0%
            construction_cost_multiplier = 1.30 - (0.30 * progress)  # 1.3x to 1.0x
        
        return {
            'population_reduction': population_reduction,
            'construction_cost_multiplier': construction_cost_multiplier,
            'description': f"Government automobile preference reduces ridership by {population_reduction*100:.1f}% and increases construction costs by {(construction_cost_multiplier-1.0)*100:.1f}%"
        }


class PopulationEvent(GameEvent):
    """Random population change event (2-8% increase or decrease)"""
    def __init__(self, year: int, change_percent: float, description: str = ""):
        super().__init__(
            name="Population Change",
            year=year,
            description=description
        )
        self.change_percent = change_percent  # Positive for increase, negative for decrease
    
    def get_effect(self, current_year: int) -> dict:
        """Get the population change effect"""
        if not self.is_active(current_year) or current_year != self.year:
            return {}
        return {
            'population_change_percent': self.change_percent,
            'description': self.description
        }


class EventSystem:
    """Manages game events"""
    def __init__(self, natural_disaster_frequency: float = 1.0):
        self.events = []
        self.last_population_event_year = None
        self.natural_disaster_frequency = natural_disaster_frequency
        self._register_default_events()
    
    def _register_default_events(self):
        """Register default game events"""
        self.events.append(AutomobileInventionEvent())
        self.events.append(Covid19Event())
        self.events.append(WorkerProtectionActEvent())
        self.events.append(TrustBustingEvent())
        self.events.append(EnvironmentalProtectionActEvent())
        self.events.append(GovernmentAutomobilePreferenceEvent())
    
    def add_event(self, event: GameEvent):
        """Add a custom event"""
        self.events.append(event)
    
    def get_active_events(self, current_year: int) -> list[GameEvent]:
        """Get all events active at the given year"""
        return [event for event in self.events if event.is_active(current_year)]
    
    def get_combined_effects(self, current_year: int) -> dict:
        """Get combined effects of all active events"""
        combined = {
            'profit_multiplier': 1.0,  # Start with no modification
            'descriptions': [],
            'population_change_percent': 0.0,
            'population_reduction': 0.0,  # Cumulative population reduction (0.0 to 1.0)
            'area_population_changes': {},  # Area-specific population changes
            'construction_cost_multiplier': 1.0  # Construction cost multiplier
        }
        
        for event in self.get_active_events(current_year):
            effect = event.get_effect(current_year)
            if 'profit_multiplier' in effect:
                # Multiply profit multipliers together
                combined['profit_multiplier'] *= effect['profit_multiplier']
            if 'description' in effect:
                combined['descriptions'].append(effect['description'])
            if 'population_change_percent' in effect:
                # Add population changes (can be positive or negative)
                combined['population_change_percent'] += effect['population_change_percent']
            if 'population_reduction' in effect:
                # Take maximum population reduction (events don't stack, use worst case)
                combined['population_reduction'] = max(combined['population_reduction'], effect['population_reduction'])
            if 'area_population_change' in effect:
                # Store area-specific changes by event name
                combined['area_population_changes'][event.name] = effect['area_population_change']
            if 'construction_cost_multiplier' in effect:
                # Multiply construction cost multipliers together
                combined['construction_cost_multiplier'] *= effect['construction_cost_multiplier']
        
        return combined
    
    def check_and_create_population_event(self, current_year: int) -> PopulationEvent:
        """Check if a random population event should occur (every 3-7 years)"""
        import random
        
        # Initialize if first time
        if self.last_population_event_year is None:
            self.last_population_event_year = 1830 - random.randint(3, 7)  # Set to allow first event soon
        
        # Calculate years since last event
        years_since_last = current_year - self.last_population_event_year
        
        # Check if enough time has passed (3-7 years)
        if years_since_last < 3:
            return None
        
        # If 7+ years have passed, definitely create event
        # Otherwise, random chance based on years passed
        if years_since_last >= 7 or random.random() < (years_since_last - 2) / 5.0:
            # Population changes: 70% chance of increase, 30% chance of decrease
            # Increases: 2-8%, Decreases: 2-6% (smaller decreases)
            if random.random() < 0.7:
                # Increase: 2-8%
                change_percent = random.uniform(0.02, 0.08)
            else:
                # Decrease: 2-6%
                change_percent = random.uniform(-0.06, -0.02)
            
            direction = "increased" if change_percent > 0 else "decreased"
            description = f"Population {direction} by {abs(change_percent)*100:.1f}% due to economic changes."
            
            event = PopulationEvent(current_year, change_percent, description)
            self.events.append(event)
            self.last_population_event_year = current_year
            return event
        
        return None
    
    def check_and_create_natural_disaster(self, current_year: int) -> 'NaturalDisasterEvent':
        """Check if a random natural disaster should occur.
        Frequency is adjusted by natural_disaster_frequency parameter.
        Base frequency: every 40-60 years on average (1.0 frequency).
        Higher frequency (e.g., 2.0) = disasters happen more often (every 20-30 years).
        Lower frequency (e.g., 0.5) = disasters happen less often (every 80-120 years)."""
        import random
        
        # Adjust base interval by frequency (inverse relationship)
        # frequency 1.0 = 40-60 years (avg one per 50), frequency 2.0 = 20-30 years, frequency 0.5 = 80-120 years
        base_min_years = int(40 / self.natural_disaster_frequency)
        base_max_years = int(60 / self.natural_disaster_frequency)
        
        # Initialize if first time
        if not hasattr(self, 'last_natural_disaster_year'):
            self.last_natural_disaster_year = None
        
        # Initialize if first time
        if self.last_natural_disaster_year is None:
            self.last_natural_disaster_year = 1830 - random.randint(base_min_years, base_max_years)
        
        years_since_last = current_year - self.last_natural_disaster_year
        
        if years_since_last < base_min_years:
            return None
        
        max_years = base_max_years
        if years_since_last >= max_years or random.random() < (years_since_last - (base_min_years - 1)) / (max_years - base_min_years + 1):
            # Randomly select one of the four disaster types
            disaster_types = ['Earthquake', 'Fire', 'Tornado', 'Hurricane']
            disaster_type = random.choice(disaster_types)
            
            event = NaturalDisasterEvent(current_year, disaster_type)
            self.events.append(event)
            self.last_natural_disaster_year = current_year
            return event
        
        return None


class NaturalDisasterEvent(GameEvent):
    """Natural disaster events that cause damage to infrastructure"""
    def __init__(self, year: int, disaster_type: str):
        self.disaster_type = disaster_type
        descriptions = {
            'Earthquake': "A major earthquake has struck, causing significant damage to rail infrastructure",
            'Fire': "A massive fire has spread through the region, damaging stations and rail lines",
            'Tornado': "A powerful tornado has torn through the area, destroying rail infrastructure",
            'Hurricane': "A devastating hurricane has hit the region, flooding and damaging transit systems"
        }
        super().__init__(
            name=f"{disaster_type} Disaster",
            year=year,
            description=descriptions.get(disaster_type, "A natural disaster has occurred")
        )
        self.damage_applied = False  # Track if damage has been applied
    
    def get_effect(self, current_year: int) -> dict:
        """Get the effect of the disaster - only active in the year it occurs and recovery period"""
        if not self.is_active(current_year):
            return {}
        
        # Check if still in recovery period
        years_since = current_year - self.year
        recovery_years = self.get_recovery_years()
        
        if years_since < 0 or years_since >= recovery_years:
            return {}
        
        # Effects vary by disaster type
        effects = {
            'Earthquake': {
                'infrastructure_damage': 0.3,  # 30% of lines/stations damaged
                'profit_reduction': 0.5,  # 50% profit reduction during recovery
                'repair_cost_multiplier': 1.5  # 50% more expensive to repair
            },
            'Fire': {
                'infrastructure_damage': 0.25,  # 25% of lines/stations damaged
                'profit_reduction': 0.4,  # 40% profit reduction during recovery
                'repair_cost_multiplier': 1.2  # 20% more expensive to repair
            },
            'Tornado': {
                'infrastructure_damage': 0.35,  # 35% of lines/stations damaged
                'profit_reduction': 0.45,  # 45% profit reduction during recovery
                'repair_cost_multiplier': 1.3  # 30% more expensive to repair
            },
            'Hurricane': {
                'infrastructure_damage': 0.4,  # 40% of lines/stations damaged
                'profit_reduction': 0.6,  # 60% profit reduction during recovery
                'repair_cost_multiplier': 1.6  # 60% more expensive to repair
            }
        }
        
        disaster_effect = effects.get(self.disaster_type, {
            'infrastructure_damage': 0.3,
            'profit_reduction': 0.5,
            'repair_cost_multiplier': 1.5
        })
        
        return {
            'natural_disaster': True,
            'disaster_type': self.disaster_type,
            'infrastructure_damage': disaster_effect['infrastructure_damage'],
            'profit_reduction': disaster_effect['profit_reduction'],
            'repair_cost_multiplier': disaster_effect['repair_cost_multiplier'],
            'description': self.description
        }
    
    def get_recovery_years(self) -> int:
        """Get number of years the disaster effects last"""
        recovery_years = {
            'Earthquake': 2,
            'Fire': 1,
            'Tornado': 2,
            'Hurricane': 3
        }
        return recovery_years.get(self.disaster_type, 2)

