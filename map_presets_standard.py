"""Standard Map Presets
All configuration for the standard/generic grid map should be defined here.

Conforms to map preset format (see MAP_PRESET_FORMAT.md) for pluggable discovery."""

# Standard map settings (generic grid, not a real location)
STANDARD_BOUNDS = {
    'center_lat': 0.0,  # Generic center (not a real location)
    'center_lon': 0.0,  # Generic center (not a real location)
    'default_width': 100,
    'default_height': 75,
    'width': 100,  # For map_preset_loader compatibility
    'height': 75,
}

# Map preset loader compatibility (see MAP_PRESET_FORMAT.md)
MAP_BOUNDS = STANDARD_BOUNDS
MAP_TIMELINE = {
    'start_year': 1830,
    'end_year': 2050,
}

# Timeline settings
STANDARD_TIMELINE = MAP_TIMELINE

# Public Transit Authority settings
PUBLIC_TA_CONFIG = {
    'establishment_year': 1930,
    'annual_funding': 50000,
    'starting_money': 100000,
}

# Zoom settings
STANDARD_ZOOM = {
    'default_zoom': 12,
    'min_zoom': 8,  # Can't zoom out too far (calculated from map size)
    'max_zoom': 19,
}

# Population settings for standard map - realistic urban growth curve
# Growth rapid in 19th century, moderate in early 20th, slower late 20th/21st
STANDARD_POPULATION = {
    'base_year': 1830,
    'total_population_1830': 750000,  # For 100x75 reference map; scales with area
    'population_per_grid_square_min': 50,
    'population_per_grid_square_max': 300,
    'population_growth_rate': 0.02,
    'population_variation': (-0.01, 0.03),
    'location_growth_variation': (0.9, 1.1),
    'max_population_per_location': 500000,
    'population_targets_by_year': {
        1830: 750000,
        1850: 1100000,
        1900: 2200000,
        1950: 3500000,
        2000: 4200000,
        2050: 4800000,
    },
}

def is_standard_map(map_center_lat: float, map_center_lon: float, width: int, height: int) -> bool:
    """Check if the given parameters match the standard/generic map.
    Standard map uses center (0, 0) - no real-world location."""
    return abs(map_center_lat) < 0.01 and abs(map_center_lon) < 0.01


def get_standard_bounds():
    """Return bounds for map preset loader."""
    return MAP_BOUNDS


def get_standard_timeline():
    """Return timeline for map preset loader."""
    return MAP_TIMELINE


def get_standard_map_display_name():
    """Return display name for map preset loader."""
    return "Standard"


def get_standard_population_at_location(x: int, y: int, width: int, height: int) -> int:
    """Get population at a specific grid location for standard map.
    
    Args:
        x: Grid X coordinate
        y: Grid Y coordinate
        width: Map width
        height: Map height
        
    Returns:
        Population count (uniform distribution across grid)
    """
    import random
    return random.randint(
        STANDARD_POPULATION['population_per_grid_square_min'],
        STANDARD_POPULATION['population_per_grid_square_max']
    )



# Line types AI companies may specialize in here (see ai_specialties.SPECIALTIES).
# The standard grid has no water, so no ferry companies.
AI_LINE_SPECIALTIES = (
    'commuter_rail', 'city_rail', 'light_rail', 'local_bus', 'express_bus', 'all_bus',
)

# Generic hook names used by map_preset_loader
MAP_ZOOM = STANDARD_ZOOM
MAP_POPULATION = STANDARD_POPULATION
get_grid_population_at_location = get_standard_population_at_location
