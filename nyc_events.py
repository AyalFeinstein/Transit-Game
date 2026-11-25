"""NYC-specific historical events for GoCommuter"""

from events import GameEvent
try:
    from nyc_population_map import NYC_POPULATION_MAP
except ImportError:
    NYC_POPULATION_MAP = {}


# Removed BrooklynBridgeOpeningEvent and SubwayOpeningEvent
# These should trigger when players build lines, not as historical events
# Population growth near railroads is handled in map.py when lines are built


class GreatDepressionNYCEvent(GameEvent):
    """1929 Great Depression - reduces population in NYC"""
    def __init__(self):
        super().__init__(
            name="Great Depression",
            year=1929,
            description="The Great Depression hits NYC, causing population decline as people move away"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """Great Depression reduces population"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0:
            return {}
        
        if current_year <= 1940:
            # 11 years of decline: 0% to 20% reduction
            progress = min(1.0, years_since / 11.0)
            population_reduction = progress * 0.20
        elif current_year <= 1950:
            # Recovery period: 20% to 10% reduction
            recovery_progress = (current_year - 1940) / 10.0
            population_reduction = 0.20 - (recovery_progress * 0.10)
        else:
            # After 1950, no effect
            return {}
        
        return {
            'population_reduction': population_reduction,
            'description': f"Great Depression: Population reduced by {population_reduction*100:.1f}%"
        }


class PostWarBoomNYCEvent(GameEvent):
    """1945 Post-War Boom - increases population in all boroughs"""
    def __init__(self):
        super().__init__(
            name="Post-War Boom",
            year=1945,
            description="Post-World War II economic boom leads to population growth across NYC"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """Post-war boom increases population"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0:
            return {}
        
        if current_year <= 1960:
            # 15 years of growth: 0% to 40% increase
            progress = min(1.0, years_since / 15.0)
            population_increase = progress * 0.40
        else:
            # After 1960, maintain 40% increase
            population_increase = 0.40
        
        return {
            'population_change_percent': population_increase,
            'description': f"Post-War Boom: Population increased by {population_increase*100:.1f}%"
        }


class NYC1970sCrisisEvent(GameEvent):
    """1970s NYC Fiscal Crisis - reduces population"""
    def __init__(self):
        super().__init__(
            name="NYC Fiscal Crisis",
            year=1975,
            description="NYC faces a fiscal crisis, leading to population decline and reduced transit funding"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """Fiscal crisis reduces population"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0:
            return {}
        
        if current_year <= 1985:
            # 10 years of decline: 0% to 15% reduction
            progress = min(1.0, years_since / 10.0)
            population_reduction = progress * 0.15
        elif current_year <= 1995:
            # Recovery period: 15% to 5% reduction
            recovery_progress = (current_year - 1985) / 10.0
            population_reduction = 0.15 - (recovery_progress * 0.10)
        else:
            # After 1995, minimal effect
            population_reduction = 0.05
        
        return {
            'population_reduction': population_reduction,
            'description': f"NYC Fiscal Crisis: Population reduced by {population_reduction*100:.1f}%"
        }


class BrooklynGrowthEvent(GameEvent):
    """1850s-1870s Brooklyn Growth - rapid population growth in Brooklyn"""
    def __init__(self):
        super().__init__(
            name="Brooklyn Expansion",
            year=1850,
            description="Brooklyn experiences rapid population growth as people move from Manhattan"
        )
        self.affected_boroughs = ['Brooklyn']
    
    def get_effect(self, current_year: int) -> dict:
        """Brooklyn growth increases population in Brooklyn borough"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0 or current_year > 1880:
            return {}
        
        # 30 years of growth: 0% to 25% increase in Brooklyn (reduced to avoid Manhattan scaling collapse)
        progress = min(1.0, years_since / 30.0)
        population_increase = progress * 0.25
        
        return {
            'area_population_change': {
                'boroughs': self.affected_boroughs,
                'change_percent': population_increase
            },
            'description': f"Brooklyn Expansion: Population increased by {population_increase*100:.1f}% in Brooklyn"
        }


class QueensSuburbanizationEvent(GameEvent):
    """1900s-1920s Queens Suburbanization - growth in Queens as suburbs develop"""
    def __init__(self):
        super().__init__(
            name="Queens Suburbanization",
            year=1900,
            description="Queens experiences suburban growth as transportation improves"
        )
        self.affected_boroughs = ['Queens']
    
    def get_effect(self, current_year: int) -> dict:
        """Queens growth increases population in Queens borough"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0 or current_year > 1930:
            return {}
        
        # 30 years of growth: 0% to 80% increase in Queens
        progress = min(1.0, years_since / 30.0)
        population_increase = progress * 0.80
        
        return {
            'area_population_change': {
                'boroughs': self.affected_boroughs,
                'change_percent': population_increase
            },
            'description': f"Queens Suburbanization: Population increased by {population_increase*100:.1f}% in Queens"
        }


class BronxExpansionEvent(GameEvent):
    """1880s-1920s Bronx Expansion - growth in the Bronx"""
    def __init__(self):
        super().__init__(
            name="Bronx Expansion",
            year=1880,
            description="The Bronx experiences rapid population growth"
        )
        self.affected_boroughs = ['Bronx']
    
    def get_effect(self, current_year: int) -> dict:
        """Bronx growth increases population in Bronx borough"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0 or current_year > 1930:
            return {}
        
        # 50 years of growth: 0% to 100% increase in Bronx
        progress = min(1.0, years_since / 50.0)
        population_increase = progress * 1.00
        
        return {
            'area_population_change': {
                'boroughs': self.affected_boroughs,
                'change_percent': population_increase
            },
            'description': f"Bronx Expansion: Population increased by {population_increase*100:.1f}% in the Bronx"
        }


class ManhattanLowerEastSideEvent(GameEvent):
    """1880s-1920s Lower East Side Immigration - massive population growth in Lower Manhattan"""
    def __init__(self):
        super().__init__(
            name="Lower East Side Immigration",
            year=1880,
            description="Massive immigration to Lower East Side creates population boom"
        )
        self.affected_area = {
            'min_lat': 40.70,
            'max_lat': 40.75,
            'min_lon': -74.00,
            'max_lon': -73.98
        }
    
    def get_effect(self, current_year: int) -> dict:
        """Lower East Side growth increases population in specific area"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0 or current_year > 1920:
            return {}
        
        # 40 years of growth: 0% to 150% increase in Lower East Side
        progress = min(1.0, years_since / 40.0)
        population_increase = progress * 1.50
        
        return {
            'area_population_change': {
                'bounds': self.affected_area,
                'change_percent': population_increase
            },
            'description': f"Lower East Side Immigration: Population increased by {population_increase*100:.1f}% in Lower Manhattan"
        }


class CholeraEpidemicEvent(GameEvent):
    """1832, 1849, 1866 Cholera Epidemics - reduce population"""
    def __init__(self, year: int):
        super().__init__(
            name=f"Cholera Epidemic ({year})",
            year=year,
            description=f"Cholera epidemic in {year} causes population decline and reduced transit usage"
        )
        self.epidemic_year = year
    
    def get_effect(self, current_year: int) -> dict:
        """Cholera epidemic reduces population"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.epidemic_year
        if years_since < 0:
            return {}
        
        # Epidemic lasts 2-3 years with peak impact in first year (reduced to avoid Manhattan collapse)
        if years_since == 0:
            # Peak impact: ~4% population reduction (was 12%, too severe)
            population_reduction = 0.04
        elif years_since == 1:
            # Recovery: ~2% reduction
            population_reduction = 0.02
        elif years_since == 2:
            # Further recovery: ~1% reduction
            population_reduction = 0.01
        else:
            # After 3 years, no effect
            return {}
        
        return {
            'population_reduction': population_reduction,
            'description': f"Cholera Epidemic ({self.epidemic_year}): Population reduced by {population_reduction*100:.1f}%"
        }


class SpanishFluEpidemicEvent(GameEvent):
    """1918 Spanish Flu Pandemic - reduces population"""
    def __init__(self):
        super().__init__(
            name="Spanish Flu Pandemic",
            year=1918,
            description="The Spanish Flu pandemic causes massive population decline and reduced transit usage"
        )
    
    def get_effect(self, current_year: int) -> dict:
        """Spanish Flu reduces population"""
        if not self.is_active(current_year):
            return {}
        
        years_since = current_year - self.year
        if years_since < 0:
            return {}
        
        # Pandemic lasts 2-3 years with peak impact in first year
        if years_since == 0:
            # Peak impact: 15-20% population reduction
            population_reduction = 0.18
        elif years_since == 1:
            # Recovery: 8-10% reduction
            population_reduction = 0.09
        elif years_since == 2:
            # Further recovery: 3-5% reduction
            population_reduction = 0.04
        else:
            # After 3 years, no effect
            return {}
        
        return {
            'population_reduction': population_reduction,
            'description': f"Spanish Flu Pandemic: Population reduced by {population_reduction*100:.1f}%"
        }


def get_nyc_events():
    """Get list of NYC-specific historical events (excluding public transportation events)"""
    return [
        # Global events
        GreatDepressionNYCEvent(),
        PostWarBoomNYCEvent(),
        NYC1970sCrisisEvent(),
        # Epidemic events
        CholeraEpidemicEvent(1832),
        CholeraEpidemicEvent(1849),
        CholeraEpidemicEvent(1866),
        SpanishFluEpidemicEvent(),
        # The borough growth events above (Brooklyn, Queens, Bronx, Lower East Side) are not
        # listed: their yearly boosts (25-150%) overwhelm the population targets. The maps add
        # smaller Brooklyn and Queens boosts of their own (AREA_GROWTH_BONUSES in each map file).
    ]

