"""NY Metropolitan Zone Map Presets
All configuration for the NY Metropolitan Zone map should be defined here.
This is a SEPARATE map from NYC proper - it includes the surrounding metropolitan area.
Follows standardized preset format for easy customization."""

# Map identification
MAP_NAME = "New York Metropolitan Zone"
MAP_ID = "ny_metro"

# NY Metropolitan Zone bounds - includes NYC plus surrounding areas (Long Island, Newark, White Plains, etc.)
# Extended west to Newark, NJ (~40.7357° N, -74.1724° W) and north to White Plains, NY (~41.0330° N, -73.7629° W)
NY_METRO_BOUNDS = {
    'center_lat': 40.75,  
    'center_lon': -73.95,  
    'min_lat': 40.4000,  
    'max_lat': 41.10,  
    'min_lon': -74.25,  
    'max_lon': -73.2000,  
    'width': 60,
    'height': 60
}

NY_METRO_TIMELINE = {
    'start_year': 1830,
    'end_year': 2050,
}

# Zoom settings
NY_METRO_ZOOM = {
    'default_zoom': 10,
    'min_zoom': 8,
    'max_zoom': 15,
}

# Population settings
NY_METRO_POPULATION = {
    'base_year': 1830,
    'total_population_1830': 350000,  
    'min_population_per_location': 0,
    'max_population_per_location_1830': 8000,
    'population_growth_rate': 0.02,  
    'population_variation': (-0.01, 0.03),
    'location_growth_variation': (0.9, 1.1),
    'max_population_per_location': 500000,  
}

def get_ny_metro_bounds() -> dict:
    """Get NY Metropolitan Zone bounds dictionary."""
    return NY_METRO_BOUNDS.copy()

def get_ny_metro_timeline() -> dict:
    """Get NY Metropolitan Zone timeline dictionary."""
    return NY_METRO_TIMELINE.copy()

def get_ny_metro_zoom() -> dict:
    """Get NY Metropolitan Zone zoom settings dictionary."""
    return NY_METRO_ZOOM.copy()

def get_ny_metro_population_settings() -> dict:
    """Get NY Metropolitan Zone population settings dictionary."""
    return NY_METRO_POPULATION.copy()

def get_ny_metro_default_center_lat() -> float:
    """Get default center latitude for NY Metropolitan Zone map."""
    return NY_METRO_BOUNDS['center_lat']

def get_ny_metro_default_center_lon() -> float:
    """Get default center longitude for NY Metropolitan Zone map."""
    return NY_METRO_BOUNDS['center_lon']

def get_ny_metro_map_display_name() -> str:
    """Get display name for NY Metropolitan Zone map."""
    return MAP_NAME

def is_ny_metro_map(map_center_lat: float = None, map_center_lon: float = None, width: int = None, height: int = None) -> bool:
    """Check if map parameters match NY Metropolitan Zone map.
    
    Args:
        map_center_lat: Map center latitude
        map_center_lon: Map center longitude
        width: Map width
        height: Map height
        
    Returns:
        True if parameters match NY Metropolitan Zone map
    """
    if map_center_lat is None or map_center_lon is None:
        return False
    
    # Check if center is within NY Metro bounds
    bounds = get_ny_metro_bounds()
    if not (bounds['min_lat'] <= map_center_lat <= bounds['max_lat']):
        return False
    if not (bounds['min_lon'] <= map_center_lon <= bounds['max_lon']):
        return False
    
    # Check if dimensions match (approximately)
    if width is not None and height is not None:
        if abs(width - bounds['width']) > 10 or abs(height - bounds['height']) > 10:
            return False
    
    return True

def get_ny_metro_population_at_location(lat: float, lon: float, is_water: bool) -> int:
    """Get population at a specific location for 1830 NY Metropolitan Zone.
    
    Args:
        lat: Latitude
        lon: Longitude
        is_water: Whether the location is in water
        
    Returns:
        Population count (0 if water, otherwise based on historical density)
    """
    if is_water:
        return 0
    
    import random
    
    # Use NYC population function for NYC area
    if 40.70 <= lat <= 40.88 and -74.02 <= lon <= -73.93:
        # Manhattan - use NYC function
        from map_presets_nyc import get_nyc_population_at_location
        return get_nyc_population_at_location(lat, lon, False)
    
    if 40.60 <= lat <= 41.30 and -74.50 <= lon <= -72.00:
        if not (40.49 <= lat <= 40.92 and -74.30 <= lon <= -73.70):
            if 40.70 <= lat <= 40.85 and -73.80 <= lon <= -73.20:
                return random.randint(50, 200)
            else:
                return random.randint(10, 100)
    if 40.92 <= lat <= 41.10 and -74.20 <= lon <= -73.50:
        if 41.00 <= lat <= 41.10 and -73.80 <= lon <= -73.70:
            return random.randint(50, 200)
        else:
            return random.randint(20, 150)
    
    if 40.50 <= lat <= 41.00 and -74.30 <= lon <= -74.00:
        if 40.70 <= lat <= 40.80 and -74.20 <= lon <= -74.00:
            return random.randint(100, 300)
        elif 40.70 <= lat <= 40.75 and -74.20 <= lon <= -74.15:
            return random.randint(80, 250)
        else:
            return random.randint(20, 100)
    
    return random.randint(5, 50)

def initialize_ny_metro_population(width: int, height: int, map_center_lat: float = None, map_center_lon: float = None) -> dict:
    """Initialize population dictionary for NY Metropolitan Zone map.
    
    Args:
        width: Map width in grid squares
        height: Map height in grid squares
        map_center_lat: Map center latitude (uses default if None)
        map_center_lon: Map center longitude (uses default if None)
        
    Returns:
        Dictionary mapping (x, y) tuples to population counts
    """
    import math
    
    population = {}
    
    # Read NY_METRO_INCLUDED_POINTS directly from file to ensure we get the latest version
    # (avoids cached module imports that might be stale)
    from map_preset_utils import get_preset_file_info, read_included_points_from_file
    import os
    
    # Create a temporary map-like object for detection
    class TempMap:
        def __init__(self):
            self.is_ny_metro = True
            self.is_nyc_proper = False
    
    temp_map = TempMap()
    preset_info = get_preset_file_info(temp_map)
    use_included_points = False
    included_set = set()
    
    if preset_info:
        file_path, variable_name = preset_info
        included_set = read_included_points_from_file(file_path, variable_name)
        use_included_points = len(included_set) > 0
    else:
        # Fallback to module import if detection fails
        try:
            included_set = set(NY_METRO_INCLUDED_POINTS)
            use_included_points = True
        except NameError:
            # Fallback: initialize all points
            use_included_points = False
    
    if map_center_lat is None:
        map_center_lat = get_ny_metro_default_center_lat()
    if map_center_lon is None:
        map_center_lon = get_ny_metro_default_center_lon()
    
    bounds = get_ny_metro_bounds()
    min_lat = bounds['min_lat']
    max_lat = bounds['max_lat']
    min_lon = bounds['min_lon']
    max_lon = bounds['max_lon']
    
    for x in range(width + 1):
        for y in range(height + 1):
            if use_included_points:
                # Only assign population to included points
                if (x, y) not in included_set:
                    population[(x, y)] = 0
                    continue
            
            # Linear interpolation: x/width maps to lon, y/height maps to lat
            lat = min_lat + (y / height) * (max_lat - min_lat)
            lon = min_lon + (x / width) * (max_lon - min_lon)
            
            # Point is included - assign population
            pop = get_ny_metro_population_at_location(lat, lon, False)
            population[(x, y)] = pop
    
    return population

# Pre-computed included points - will be populated with Long Island relation points
# This is SEPARATE from NYC_INCLUDED_POINTS
NY_METRO_INCLUDED_POINTS = []

NY_METRO_POPULATION_POINTS = NY_METRO_INCLUDED_POINTS

