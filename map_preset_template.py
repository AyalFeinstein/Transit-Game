"""Map Preset Template
This is a template for creating custom map presets.
Copy this file and modify it to create your own custom map.

To use a custom map preset:
1. Copy this file to map_presets_<yourname>.py (e.g., map_presets_mycity.py)
2. Fill in all the required variables below
3. The map will be automatically discovered and can be used without modifying main code!

4. Put offline generation scripts in generate_<map_id>_*.py (e.g. generate_nyc_population_points.py).
   Preset files (map_presets_*.py) hold bounds, runtime helpers, and precomputed data only.

The preset will be automatically loaded when you specify the map name in the game.
"""

# Map identification
MAP_NAME = "Custom Map"  # Name of your map (displayed in UI)
MAP_ID = "custom"  # Unique identifier (used in code, should match filename: map_presets_<MAP_ID>.py)

# Map bounds and dimensions
MAP_BOUNDS = {
    'center_lat': 0.0,  # Center latitude
    'center_lon': 0.0,  # Center longitude
    'min_lat': -1.0,  # Southern edge
    'max_lat': 1.0,  # Northern edge
    'min_lon': -1.0,  # Western edge
    'max_lon': 1.0,  # Eastern edge
    'width': 100,  # Grid width
    'height': 75,  # Grid height
}

# Timeline settings
MAP_TIMELINE = {
    'start_year': 1830,  # Starting year
    'end_year': 2050,  # Ending year
}

# Zoom settings
MAP_ZOOM = {
    'default_zoom': 12,  # Default zoom level
    'min_zoom': 8,  # Minimum zoom (can't zoom out further)
    'max_zoom': 19,  # Maximum zoom
}

# Optional: Default line names for companies (players) on this map. First line gets first name, etc.
DEFAULT_LINE_NAMES = [
    "Main Line",
    "Central Link",
    "Harbor Line",
    "Crosstown Line",
    "Uptown Line",
    "Downtown Line",
    "Riverside Line",
    "Metro Link",
]

# Optional: Default schedule scheme for new lines. peak/off_peak/night multipliers (e.g. 1.0 = full service).
DEFAULT_SCHEDULE_SCHEME = {"peak": 1.0, "off_peak": 0.8, "night": 0.3}

# Water areas (list of rectangular areas that are water)
# Format: {'min_lat': float, 'max_lat': float, 'min_lon': float, 'max_lon': float}
MAP_WATER_AREAS = [
    # Example: {'min_lat': 40.0, 'max_lat': 40.5, 'min_lon': -74.0, 'max_lon': -73.5},
]

def is_water_location(lat: float, lon: float) -> bool:
    """Check if a location (lat, lon) is in a water area.
    Returns True if the location is in water, False otherwise."""
    for water_area in MAP_WATER_AREAS:
        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
            water_area['min_lon'] <= lon <= water_area['max_lon']):
            return True
    return False

# Population settings
MAP_POPULATION = {
    'base_year': 1830,  # Base year for population calculations
    'total_population_base_year': 100000,  # Total population in base year
    'min_population_per_location': 0,  # Minimum population at a location
    'max_population_per_location_base_year': 5000,  # Maximum at a location in base year
    'population_growth_rate': 0.02,  # 2% per year average
    'population_variation': (-0.01, 0.03),  # Growth rate variation range
    'location_growth_variation': (0.9, 1.1),  # Location-specific growth variation
    'max_population_per_location': 2000,  # Maximum population at any location
}

def get_population_at_location(lat: float, lon: float, is_water: bool, x: int, y: int, width: int, height: int) -> int:
    """Get population at a specific location.
    
    Args:
        lat: Latitude (for real-world maps)
        lon: Longitude (for real-world maps)
        is_water: Whether the location is in water
        x: Grid X coordinate
        y: Grid Y coordinate
        width: Map width
        height: Map height
        
    Returns:
        Population count (0 if water, otherwise based on your distribution)
    """
    if is_water:
        return 0
    
    import random
    # TODO: Implement your population distribution logic here
    # Example: uniform distribution
    return random.randint(
        MAP_POPULATION['min_population_per_location'],
        MAP_POPULATION['max_population_per_location_base_year'] // 10
    )

# Map display settings
MAP_DISPLAY = {
    'use_real_streets': True,  # True for real OSM streets, False for grid only
    'background_color': '#ffffff',  # Background color (for grid maps)
    'grid_line_color': '#cccccc',  # Grid line color
    'grid_line_width': 2,  # Grid line width in pixels
}

# Optional: Helper functions for compatibility with the map loading system
def get_custom_bounds():
    """Get map bounds. Required for map loading system."""
    return MAP_BOUNDS

def get_custom_timeline():
    """Get timeline settings. Required for map loading system."""
    return MAP_TIMELINE

def get_custom_zoom():
    """Get zoom settings. Required for map loading system."""
    return MAP_ZOOM

def get_custom_population_settings():
    """Get population settings. Required for map loading system."""
    return MAP_POPULATION

