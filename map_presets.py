"""Map presets and configuration for Transit Game.
All hard-coded map settings should be defined here."""

# NYC Proper bounds (excludes New Jersey, most of Long Island, etc.)
NYC_PROPER_BOUNDS = {
    'center_lat': 40.7128,  # NYC center (Manhattan)
    'center_lon': -73.9352,  # NYC center
    'min_lat': 40.4950,  # Southern edge (Staten Island, excluding outer areas)
    'max_lat': 40.9150,  # Northern edge (Bronx, excluding outer areas)
    'min_lon': -74.0500,  # Western edge (Staten Island/Hudson River, excluding NJ)
    'max_lon': -73.7000,  # Eastern edge (Queens, excluding most of Long Island)
    'width': 50,
    'height': 50
}

# Standard map defaults (generic grid, not location-specific)
STANDARD_MAP_DEFAULTS = {
    'center_lat': 40.7128,  # Default center (can be customized)
    'center_lon': -73.9352,  # Default center (can be customized)
    'default_width': 100,
    'default_height': 75
}

# Population settings
POPULATION_SETTINGS = {
    'base_year': 1830,
    'nyc_1830_total_population': 202589,  # NYC population in 1830
    'min_population_per_location': 0,  # Minimum population at a location
    'max_population_per_location_1830': 5000,  # Maximum at a location in 1830
    'population_growth_rate': 0.02,  # 2% per year average
    'population_variation': (-0.01, 0.03),  # Growth rate variation range
    'location_growth_variation': (0.9, 1.1),  # Location-specific growth variation
    'max_population_per_location': 2000,  # Maximum population at any location
}

# OSM settings
OSM_SETTINGS = {
    'timeout': 10.0,  # Default timeout for OSM API calls
    'quick_timeout': 2.0,  # Quick timeout for line creation to prevent UI freeze
    'max_timeout_seconds': 3.0,  # Maximum time before returning empty results
    # Overpass usage policy: descriptive User-Agent with app id; generic python-requests UAs are rejected (403).
    # See https://operations.osmfoundation.org/policies/overpass/
    'base_url': 'https://overpass-api.de/api/interpreter',
    # Tried in order after 403/429/5xx on the primary endpoint.
    'mirror_urls': (
        'https://overpass.kumi.systems/api/interpreter',
        'https://overpass.openstreetmap.ru/cgi/interpreter',
    ),
}

# Map display settings
MAP_DISPLAY_SETTINGS = {
    'default_zoom_level': 11,  # Default zoom for OSM maps
    'nyc_zoom_level': 11,  # Zoom level for NYC map
    'standard_zoom_level': 12,  # Zoom level for standard map
    'min_zoom': 1,
    'max_zoom': 19,
}

# Water/land detection (for NYC - approximate boundaries)
# These are approximate - more precise detection would require actual water body polygons
NYC_WATER_AREAS = [
    # Hudson River (west of Manhattan)
    {'min_lat': 40.68, 'max_lat': 40.78, 'min_lon': -74.05, 'max_lon': -74.01},
    # East River (east of Manhattan, between Manhattan and Brooklyn/Queens)
    {'min_lat': 40.68, 'max_lat': 40.78, 'min_lon': -73.99, 'max_lon': -73.92},
    # Upper New York Bay (south of Manhattan)
    {'min_lat': 40.64, 'max_lat': 40.70, 'min_lon': -74.05, 'max_lon': -74.00},
    # Lower New York Bay (south of Staten Island)
    {'min_lat': 40.50, 'max_lat': 40.64, 'min_lon': -74.05, 'max_lon': -74.00},
    # Long Island Sound (northern part, above Bronx)
    {'min_lat': 40.85, 'max_lat': 40.92, 'min_lon': -73.95, 'max_lon': -73.85},
    # Harlem River (between Manhattan and Bronx)
    {'min_lat': 40.80, 'max_lat': 40.85, 'min_lon': -73.95, 'max_lon': -73.92},
]

def is_water_location(lat: float, lon: float) -> bool:
    """Check if a location (lat, lon) is in a water area.
    Returns True if the location is in water, False otherwise."""
    for water_area in NYC_WATER_AREAS:
        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
            water_area['min_lon'] <= lon <= water_area['max_lon']):
            return True
    return False

def get_nyc_1830_population_distribution():
    """Get realistic population distribution for NYC in 1830.
    Returns a function that can be used to generate population at a location."""
    total_pop = POPULATION_SETTINGS['nyc_1830_total_population']
    
    # Historical population centers in 1830 NYC:
    # - Lower Manhattan (densest)
    # - Brooklyn Heights (growing)
    # - Some areas of Queens and Bronx (sparse)
    # - Staten Island (very sparse)
    
    def get_population_at_location(lat: float, lon: float, is_water: bool) -> int:
        """Get population at a specific location for 1830 NYC.
        
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
        
        # Lower Manhattan (densest in 1830)
        if 40.70 <= lat <= 40.72 and -74.02 <= lon <= -73.98:
            # Very dense - most of NYC population was here
            return random.randint(800, 1500)
        
        # Mid Manhattan
        elif 40.72 <= lat <= 40.75 and -74.02 <= lon <= -73.98:
            return random.randint(400, 800)
        
        # Upper Manhattan (sparse in 1830)
        elif 40.75 <= lat <= 40.80 and -74.02 <= lon <= -73.98:
            return random.randint(50, 200)
        
        # Brooklyn Heights (growing area in 1830)
        elif 40.68 <= lat <= 40.72 and -74.02 <= lon <= -73.95:
            return random.randint(200, 500)
        
        # Other Brooklyn (sparse)
        elif 40.65 <= lat <= 40.68 and -74.02 <= lon <= -73.90:
            return random.randint(50, 200)
        
        # Queens (very sparse in 1830)
        elif -73.98 <= lon <= -73.85:
            return random.randint(10, 100)
        
        # Bronx (very sparse in 1830)
        elif 40.80 <= lat <= 40.92:
            return random.randint(10, 100)
        
        # Staten Island (very sparse in 1830)
        elif lat <= 40.65 and lon <= -74.00:
            return random.randint(5, 50)
        
        # Default for other areas
        else:
            return random.randint(10, 150)
    
    return get_population_at_location

