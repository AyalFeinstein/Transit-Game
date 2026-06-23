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

# Population settings for standard map
# Calculate population density similar to NYC (per grid square)
# NYC 1830: ~202,589 people in ~50x50 grid = ~81 people per grid square average (excluding water)
# For standard map, use similar density (no water, so all squares have population)
STANDARD_POPULATION = {
    'base_year': 1830,
    'total_population_1830': 750000,  # For 100x75 reference map; scales with area
    'population_per_grid_square_min': 50,  # Similar to NYC sparse areas
    'population_per_grid_square_max': 300,  # Similar to NYC average (adjusted for no water)
    'population_growth_rate': 0.02,  # 2% per year average
    'population_variation': (-0.01, 0.03),
    'location_growth_variation': (0.9, 1.1),
    'max_population_per_location': 500000,
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

