"""NYC Map Presets
All configuration for the NYC proper map should be defined here.
Follows standardized preset format for easy customization.

Precomputed included points / population: run generate_nyc_*.py scripts (not at game startup).
"""

# Map identification
MAP_NAME = "New York City"
MAP_ID = "nyc"

from nyc_city_data import (  # shared with the NY Metro map
    AREA_BOXES, GOVERNORS_ISLAND_BOUNDS, GOVERNORS_ISLAND_OSM_RELATION_ID, NYC_BOUNDS,
    NYC_BOUNDS_EXTENDED_BROOKLYN_SW, NYC_HISTORICAL_BRIDGES, NYC_HISTORICAL_ROADS, PLACE_NAMES,
    TEMP_EXTEND_BROOKLYN_SW_BOUNDS, TRANSIT_COMPANY_NAMES, WORKPLACE_DENSITY_ZONES,
    _get_active_nyc_bounds, get_nyc_bounds, get_nyc_population_at_location, is_in_governors_island,
)


# Timeline settings
NYC_TIMELINE = {
    'start_year': 1830,
    'end_year': 2050,
}

# Public Transit Authority settings
PUBLIC_TA_CONFIG = {
    'establishment_year': 1930,
    'annual_funding': 50000,
    'starting_money': 100000,
}

# Zoom settings
NYC_ZOOM = {
    'default_zoom': 11,
    'min_zoom': 10,  # Can't zoom out too far
    'max_zoom': 19,
}

# Default line names for companies on this map (first line A, second B, etc.)
# Boundaries (keys in ny_metro_osm_relation_polygons.json) whose population is not part of
# this map: the New York City map shows only New York City.
EXCLUDED_POPULATION_REGIONS = ('nj_state',)


DEFAULT_LINE_NAMES = [
    "Hudson Line",
    "Harlem Service",
    "Lexington Line",
    "Queens Boulevard Line",
    "Brooklyn Crosstown",
    "Seventh Avenue Line",
    "Fulton Street Line",
    "East Side Line",
    "West Side Line",
    "Rockaway Branch",
    "Crosstown Link",
    "Waterfront Line",
    "Midtown Connector",
    "Downtown Link",
    "River Line",
    "Park Avenue Line",
    "Bridge Line",
    "East River Line",
    "Harbor Shuttle",
    "Metro Local",
]
# Default schedule scheme for new lines
DEFAULT_SCHEDULE_SCHEME = {"peak": 1.0, "off_peak": 0.8, "night": 0.3}

# Water/land detection (for NYC - approximate boundaries)
# These are approximate rectangular areas for water bodies
# Includes all water areas: Hudson River, Harlem River, East River, Lower Bay, Long Island Sound
# Also includes areas outside NYC proper that should have no population
NYC_WATER_AREAS = [
    {'min_lat': 40.70, 'max_lat': 40.88, 'min_lon': -74.08, 'max_lon': -74.02},
    {'min_lat': 40.68, 'max_lat': 40.78, 'min_lon': -73.97, 'max_lon': -73.955},
    {'min_lat': 40.64, 'max_lat': 40.70, 'min_lon': -74.05, 'max_lon': -74.02},
    {'min_lat': 40.50, 'max_lat': 40.64, 'min_lon': -74.05, 'max_lon': -73.95},
    {'min_lat': 40.49, 'max_lat': 40.65, 'min_lon': -74.30, 'max_lon': -74.25},
    {'min_lat': 40.64, 'max_lat': 40.70, 'min_lon': -74.25, 'max_lon': -74.02},
    {'min_lat': 40.89, 'max_lat': 40.92, 'min_lon': -73.90, 'max_lon': -73.85},
    {'min_lat': 40.80, 'max_lat': 40.85, 'min_lon': -73.95, 'max_lon': -73.92},
    {'min_lat': 40.57, 'max_lat': 40.65, 'min_lon': -73.92, 'max_lon': -73.80},
    {'min_lat': 40.50, 'max_lat': 40.55, 'min_lon': -74.05, 'max_lon': -73.70},
]

# Areas outside NYC proper that should have no population
# NOTE: These are rectangular approximations. Actual borough boundaries and water bodies are not rectangles,
# so these bounds may include some water/outside areas or exclude some valid land. For more accurate
# boundaries, OSM polygon queries would be needed, but those are too slow for point generation.
    # New Jersey (Hoboken, Union City, Hackensack, etc.) - all of New Jersey including water around it
NYC_EXCLUDED_AREAS = [
    # New Jersey (west of Hudson River) - all of New Jersey should be excluded including water
    # Northern NJ (Hoboken, Jersey City, Newark, etc.) - west of Hudson River
    {'min_lat': 40.68, 'max_lat': 40.90, 'min_lon': -74.50, 'max_lon': -74.05},  # Northern NJ (west of Hudson)
    # Southern NJ area west of Staten Island (Staten Island is -74.30 to -74.02)
    {'min_lat': 40.49, 'max_lat': 40.65, 'min_lon': -74.50, 'max_lon': -74.30},  # Southern NJ west of Staten Island
    # New Jersey cities north of Staten Island (Bayonne, Elizabeth, etc.) - these are at 40.66-40.68, west of -74.05
    {'min_lat': 40.65, 'max_lat': 40.68, 'min_lon': -74.50, 'max_lon': -74.05},  # NJ cities north of Staten Island
    # Pelham Manor and north (north of Bronx) - extended to catch all areas north
    {'min_lat': 40.92, 'max_lat': 41.00, 'min_lon': -73.95, 'max_lon': -73.75},
    # East of Queens/Long Island (Great Neck, Nassau County, etc.) - extend further east
    {'min_lat': 40.50, 'max_lat': 40.90, 'min_lon': -73.70, 'max_lon': -73.50},
]

def is_water_location(lat: float, lon: float, osm_integration=None) -> bool:
    """True for water, and for places that are not part of this map (off the map, or in New
    Jersey). Uses the offline OSM coastline / lake data and the real New Jersey boundary; the
    rough rectangles below are only a fallback when that data file is missing."""
    active_bounds = _get_active_nyc_bounds()
    if (lat < active_bounds['min_lat'] or lat > active_bounds['max_lat'] or
            lon < active_bounds['min_lon'] or lon > active_bounds['max_lon']):
        return True
    try:
        mask = get_water_mask()
        if mask.has_data():
            if is_in_governors_island(lat, lon):
                return False
            if any(mask.in_region(name, lat, lon) for name in EXCLUDED_POPULATION_REGIONS):
                return True
            return mask.is_water_latlon(lat, lon)
    except Exception:
        pass
    return _is_water_location_rectangles(lat, lon, osm_integration)


def _is_water_location_rectangles(lat: float, lon: float, osm_integration=None) -> bool:
    """Fallback water test from rectangles (used only without the offline water data).
    Check if a location (lat, lon) is in a water area or excluded area.
    Uses OSM data if available for accurate water detection (rivers aren't straight).
    Returns True if the location is in water or outside NYC proper, False otherwise.
    Upper Manhattan and Bronx are included (even if population is 0)."""
    # Manhattan island bounds (includes Upper Manhattan)
    MANHATTAN_BOUNDS = {
        'min_lat': 40.70, 'max_lat': 40.88,  # Extended to include Upper Manhattan
        'min_lon': -74.02, 'max_lon': -73.93
    }
    
    # Bronx bounds (all of Bronx should be included)
    BRONX_BOUNDS = {
        'min_lat': 40.80, 'max_lat': 40.92,
        'min_lon': -73.95, 'max_lon': -73.75
    }
    
    # Brooklyn bounds (protect Brooklyn from water detection)
    # Extended south/west a bit to match TEMP_EXTEND_BROOKLYN_SW_BOUNDS usage.
    BROOKLYN_BOUNDS = {
        'min_lat': 40.50, 'max_lat': 40.75,
        'min_lon': -74.15, 'max_lon': -73.70
    }
    
    # Queens bounds (protect Queens from water detection) - stop at Long Island boundary
    QUEENS_BOUNDS = {
        'min_lat': 40.57, 'max_lat': 40.80,
        'min_lon': -74.05, 'max_lon': -73.70
    }
    
    # Staten Island bounds (protect Staten Island from water detection)
    STATEN_ISLAND_BOUNDS = {
        'min_lat': 40.49, 'max_lat': 40.65,
        'min_lon': -74.30, 'max_lon': -74.02
    }

    # Governors Island (included in buildable area; harbor water bbox must not swallow it)
    GOVERNORS_ISLAND = GOVERNORS_ISLAND_BOUNDS
    
    active_bounds = _get_active_nyc_bounds()

    # First check if outside NYC proper bounds (should have no population)
    if (lat < active_bounds['min_lat'] or lat > active_bounds['max_lat'] or
        lon < active_bounds['min_lon'] or lon > active_bounds['max_lon']):
        return True  # Outside bounds = no population
    
    for excluded_area in NYC_EXCLUDED_AREAS:
        if (excluded_area['min_lat'] <= lat <= excluded_area['max_lat'] and
            excluded_area['min_lon'] <= lon <= excluded_area['max_lon']):
            return True

    if is_in_governors_island(lat, lon):
        return False

    if osm_integration and hasattr(osm_integration, 'is_water_at_latlon'):
        try:
            if osm_integration.is_water_at_latlon(lat, lon):
                if (MANHATTAN_BOUNDS['min_lat'] <= lat <= MANHATTAN_BOUNDS['max_lat'] and
                    MANHATTAN_BOUNDS['min_lon'] <= lon <= MANHATTAN_BOUNDS['max_lon']):
                    # In Manhattan - check if it's actually in a defined water area (rivers)
                    for water_area in NYC_WATER_AREAS:
                        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                            water_area['min_lon'] <= lon <= water_area['max_lon']):
                            return True  # In water (Hudson/East River)
                    return False  # In Manhattan, not water
                
                if (BRONX_BOUNDS['min_lat'] <= lat <= BRONX_BOUNDS['max_lat'] and
                    BRONX_BOUNDS['min_lon'] <= lon <= BRONX_BOUNDS['max_lon']):
                    for water_area in NYC_WATER_AREAS:
                        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                            water_area['min_lon'] <= lon <= water_area['max_lon']):
                            return True  # In water
                    return False  # In Bronx, not water
                
                if (BROOKLYN_BOUNDS['min_lat'] <= lat <= BROOKLYN_BOUNDS['max_lat'] and
                    BROOKLYN_BOUNDS['min_lon'] <= lon <= BROOKLYN_BOUNDS['max_lon']):
                    # In Brooklyn - check if it's actually in a defined water area
                    for water_area in NYC_WATER_AREAS:
                        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                            water_area['min_lon'] <= lon <= water_area['max_lon']):
                            return True  # In water
                    return False  # In Brooklyn, not water
                
                if (QUEENS_BOUNDS['min_lat'] <= lat <= QUEENS_BOUNDS['max_lat'] and
                    QUEENS_BOUNDS['min_lon'] <= lon <= QUEENS_BOUNDS['max_lon']):
                    # In Queens - check if it's actually in a defined water area
                    for water_area in NYC_WATER_AREAS:
                        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                            water_area['min_lon'] <= lon <= water_area['max_lon']):
                            return True  # In water
                    return False  # In Queens, not water
                
                if (STATEN_ISLAND_BOUNDS['min_lat'] <= lat <= STATEN_ISLAND_BOUNDS['max_lat'] and
                    STATEN_ISLAND_BOUNDS['min_lon'] <= lon <= STATEN_ISLAND_BOUNDS['max_lon']):
                    # In Staten Island - check if it's actually in a defined water area
                    for water_area in NYC_WATER_AREAS:
                        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                            water_area['min_lon'] <= lon <= water_area['max_lon']):
                            return True  # In water
                    return False  # In Staten Island, not water

                if (GOVERNORS_ISLAND['min_lat'] <= lat <= GOVERNORS_ISLAND['max_lat'] and
                    GOVERNORS_ISLAND['min_lon'] <= lon <= GOVERNORS_ISLAND['max_lon']):
                    return False  # Governors Island is land (buildable)
                
                # Outside Manhattan/Bronx/Brooklyn/Queens/Staten Island, trust OSM
                return True
        except:
            pass  # Fall back to preset water areas if OSM fails
    
    # Fallback: Check preset water areas (for areas not covered by OSM or if OSM unavailable)
    # Check if in Manhattan (including Upper Manhattan) - not water unless in river
    if (MANHATTAN_BOUNDS['min_lat'] <= lat <= MANHATTAN_BOUNDS['max_lat'] and
        MANHATTAN_BOUNDS['min_lon'] <= lon <= MANHATTAN_BOUNDS['max_lon']):
        # Check if it's actually in a water area (rivers)
        for water_area in NYC_WATER_AREAS:
            if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                water_area['min_lon'] <= lon <= water_area['max_lon']):
                return True  # In water (Hudson/East River)
        return False  # In Manhattan, not water (may have 0 population but is included)
    
    # Check if in Bronx - all Bronx is included (may have 0 population)
    if (BRONX_BOUNDS['min_lat'] <= lat <= BRONX_BOUNDS['max_lat'] and
        BRONX_BOUNDS['min_lon'] <= lon <= BRONX_BOUNDS['max_lon']):
        # Check if it's actually in a water area (Harlem River, Long Island Sound)
        for water_area in NYC_WATER_AREAS:
            if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                water_area['min_lon'] <= lon <= water_area['max_lon']):
                return True  # In water
        return False  # In Bronx, not water (may have 0 population but is included)
    
    # Check if in Brooklyn - protect from water detection
    if (BROOKLYN_BOUNDS['min_lat'] <= lat <= BROOKLYN_BOUNDS['max_lat'] and
        BROOKLYN_BOUNDS['min_lon'] <= lon <= BROOKLYN_BOUNDS['max_lon']):
        # Check if it's actually in a defined water area
        for water_area in NYC_WATER_AREAS:
            if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                water_area['min_lon'] <= lon <= water_area['max_lon']):
                return True  # In water
        return False  # In Brooklyn, not water
    
    # Check if in Queens - protect from water detection
    if (QUEENS_BOUNDS['min_lat'] <= lat <= QUEENS_BOUNDS['max_lat'] and
        QUEENS_BOUNDS['min_lon'] <= lon <= QUEENS_BOUNDS['max_lon']):
        # Check if it's actually in a defined water area
        for water_area in NYC_WATER_AREAS:
            if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                water_area['min_lon'] <= lon <= water_area['max_lon']):
                return True  # In water
        return False  # In Queens, not water
    
    # Check if in Staten Island - protect from water detection
    if (STATEN_ISLAND_BOUNDS['min_lat'] <= lat <= STATEN_ISLAND_BOUNDS['max_lat'] and
        STATEN_ISLAND_BOUNDS['min_lon'] <= lon <= STATEN_ISLAND_BOUNDS['max_lon']):
        # Check if it's actually in a defined water area
        for water_area in NYC_WATER_AREAS:
            if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
                water_area['min_lon'] <= lon <= water_area['max_lon']):
                return True  # In water
        return False  # In Staten Island, not water

    # Governors Island — land in the harbor (not harbor water)
    if (GOVERNORS_ISLAND['min_lat'] <= lat <= GOVERNORS_ISLAND['max_lat'] and
        GOVERNORS_ISLAND['min_lon'] <= lon <= GOVERNORS_ISLAND['max_lon']):
        return False
    
    # Check water areas for other locations
    for water_area in NYC_WATER_AREAS:
        if (water_area['min_lat'] <= lat <= water_area['max_lat'] and
            water_area['min_lon'] <= lon <= water_area['max_lon']):
            return True
    
    return False


# Line types AI companies may specialize in here (see ai_specialties.SPECIALTIES).
# The city map is too small for commuter rail.
AI_LINE_SPECIALTIES = (
    'city_rail', 'light_rail', 'local_bus', 'express_bus', 'all_bus', 'ferry',
)

# Population settings for NYC - historical milestones for realistic growth
# Sources: NYC Planning, US Census. Growth was rapid 1830-1900, moderate 1900-1950, flat 1950-2000.
NYC_POPULATION = {
    'base_year': 1830,
    'total_population_1830': 100226,  # NYC population 1830 (historical)
    'min_population_per_location': 0,
    'max_population_per_location_1830': 5000,
    'population_growth_rate': 0.02,  # Fallback when targets not used
    'population_variation': (-0.01, 0.03),
    'location_growth_variation': (0.9, 1.1),
    'max_population_per_location': 50000,
    # Realistic historical targets (interpolated between years)
    'population_targets_by_year': {
        1830: 100226,
        1850: 515547,
        1870: 942292,
        1890: 1515301,
        1900: 3437202,
        1920: 5620048,
        1940: 7454995,
        1950: 7891957,
        1970: 7894863,
        1990: 7322564,
        2000: 8008278,
        2020: 8336817,
        2050: 9000000,  # Modest projected growth
    },
}


def is_nyc_map(map_center_lat: float = None, map_center_lon: float = None, width: int = None, height: int = None) -> bool:
    """Determine if a map configuration matches NYC proper map.
    
    Args:
        map_center_lat: Center latitude of the map
        map_center_lon: Center longitude of the map
        width: Map width in grid squares
        height: Map height in grid squares
        
    Returns:
        True if the map matches NYC configuration, False otherwise
    """
    if map_center_lat is None or map_center_lon is None:
        return False
    
    active_bounds = _get_active_nyc_bounds()

    lat_match = abs(map_center_lat - active_bounds['center_lat']) < 0.1
    lon_match = abs(map_center_lon - active_bounds['center_lon']) < 0.1
    
    dim_match = True
    if width is not None and height is not None:
        dim_match = ((width == active_bounds['width'] or abs(width - active_bounds['width']) <= 5) and
                     (height == active_bounds['height'] or abs(height - active_bounds['height']) <= 5))
    
    return lat_match and lon_match and dim_match


def get_nyc_map_display_name() -> str:
    """Get the display name for NYC map in UI dropdowns."""
    return "OSM New York City"


def get_nyc_default_center_lat() -> float:
    """Get default center latitude for NYC map."""
    return _get_active_nyc_bounds()['center_lat']


def get_nyc_default_center_lon() -> float:
    """Get default center longitude for NYC map."""
    return _get_active_nyc_bounds()['center_lon']


def get_nyc_timeline() -> dict:
    """Get NYC timeline dictionary. All NYC-specific timeline logic should use this function."""
    return NYC_TIMELINE.copy()


def get_nyc_zoom() -> dict:
    """Get NYC zoom settings dictionary. All NYC-specific zoom logic should use this function."""
    return NYC_ZOOM.copy()


def get_nyc_population_settings() -> dict:
    """Get NYC population settings dictionary. All NYC-specific population settings should use this function."""
    return NYC_POPULATION.copy()


def get_place_names() -> tuple:
    """Neighbourhood centres used to name stations and lines on this map."""
    return PLACE_NAMES


def get_area_boxes() -> tuple:
    """Broad areas used for names when no neighbourhood is near."""
    return AREA_BOXES


def get_water_mask():
    """Offline coastline / lake / boundary data for this map."""
    from map_presets_ny_water_utils import WATER_MASK
    return WATER_MASK


# Small boosts to yearly population growth in Brooklyn and Queens while they grew fastest.
# Areas are lat/lon boxes that together cover each borough's land (rough at the borders).
_BROOKLYN_AREAS = (
    {'min_lat': 40.57, 'max_lat': 40.68, 'min_lon': -74.04, 'max_lon': -73.86},   # south and east
    {'min_lat': 40.68, 'max_lat': 40.70, 'min_lon': -74.00, 'max_lon': -73.90},   # Brooklyn Heights to Bushwick
    {'min_lat': 40.70, 'max_lat': 40.735, 'min_lon': -73.965, 'max_lon': -73.90},  # Williamsburg, Greenpoint
)
_QUEENS_AREAS = (
    {'min_lat': 40.735, 'max_lat': 40.765, 'min_lon': -73.958, 'max_lon': -73.70},  # Long Island City east
    {'min_lat': 40.765, 'max_lat': 40.80, 'min_lon': -73.935, 'max_lon': -73.70},   # Astoria to Bayside
    {'min_lat': 40.68, 'max_lat': 40.735, 'min_lon': -73.90, 'max_lon': -73.70},    # Ridgewood to Queens Village
    {'min_lat': 40.54, 'max_lat': 40.68, 'min_lon': -73.86, 'max_lon': -73.70},     # Jamaica, Rockaways
)
# (name, first year, last year, areas, extra yearly growth, description)
AREA_GROWTH_BONUSES = (
    ("Brooklyn Ferry Suburbs", 1835, 1860, _BROOKLYN_AREAS, 0.015,
     "Steam ferries make Brooklyn a commuter town for Manhattan."),
    ("Brooklyn Bridge Growth", 1883, 1898, _BROOKLYN_AREAS, 0.01,
     "The Brooklyn Bridge draws new residents across the East River."),
    ("Brooklyn Subway Era", 1905, 1930, _BROOKLYN_AREAS, 0.01,
     "New bridges and subway lines fill in Brooklyn's outer neighbourhoods."),
    ("Queens Bridge and Subway Boom", 1909, 1932, _QUEENS_AREAS, 0.015,
     "The Queensboro Bridge and new subway lines open Queens to development."),
    ("Queens Postwar Suburbs", 1946, 1965, _QUEENS_AREAS, 0.01,
     "Postwar housing spreads across eastern Queens."),
)


def get_events() -> list:
    """Historical events for this map: the city's, plus Brooklyn and Queens growth periods."""
    from events import AreaGrowthBonusEvent
    from nyc_events import get_nyc_events
    return get_nyc_events() + [AreaGrowthBonusEvent(*bonus) for bonus in AREA_GROWTH_BONUSES]


def get_road_utils():
    """Module holding this map's street network data and queries."""
    import map_presets_nyc_road_utils
    return map_presets_nyc_road_utils


def get_point_area_map() -> dict:
    """Get point-to-area mapping for area-specific population changes (borough, etc). Used by map_preset_loader."""
    try:
        from nyc_population_map import NYC_POPULATION_MAP
        return NYC_POPULATION_MAP
    except ImportError:
        return {}


def get_excluded_water_points() -> set:
    """Get grid points that are always water. Used by map_preset_loader."""
    try:
        return set(NYC_EXCLUDED_POINTS)
    except NameError:
        return set()


def is_point_on_road_at_year(x: float, y: float, current_year: int, game_map) -> bool:
    """Check if grid point is on a road at current_year. Used by map_preset_loader."""
    from map_presets_nyc_road_utils import is_point_on_road_at_year as _impl
    return _impl(x, y, current_year, game_map, bounds=get_nyc_bounds())


def is_point_on_bridge_at_year(x: float, y: float, current_year: int, game_map) -> bool:
    """Check if grid point is on a bridge at current_year. Used by map_preset_loader."""
    from map_presets_nyc_road_utils import is_point_on_bridge_at_year as _impl
    return _impl(x, y, current_year, game_map, bounds=get_nyc_bounds())


def polyline_follows_streets_for_ai(game_map, points: list, current_year: int) -> bool:
    """True when every land segment of an AI opener polyline lies on buildable streets."""
    from map_presets_nyc_road_utils import _polyline_land_segments_on_street
    return _polyline_land_segments_on_street(game_map, list(points or []), current_year)


def preload_road_data(game_map) -> None:
    """Preload road spatial index at game start. Avoids 2-5s load on first AI turn."""
    from map_presets_nyc_road_utils import _build_road_spatial_index
    _build_road_spatial_index(game_map)


def ai_station_population_at_grid(px: float, py: float, game_map, pop_map: dict) -> int:
    """AI station population: geodesic-capped so Hudson/NJ grid sites do not borrow Manhattan pop."""
    from map_presets_nyc_road_utils import population_at_station_site_geographic
    return population_at_station_site_geographic(game_map, px, py, pop_map, get_nyc_bounds())


def get_road_segment_points_for_ai(game_map, current_year: int, max_points: int = 12,
                                   existing_line_routes=None, min_length_grid=None, max_length_grid=None,
                                   min_endpoint_population=None, rng=None) -> list:
    """Return grid points along street polylines for AI line building. Used by map_preset_loader.
    Uses min_endpoint_population=50 to prioritize high-population areas (Manhattan, Brooklyn Heights)."""
    from map_presets_nyc_road_utils import get_road_segment_points_for_ai as _impl
    min_pop = 1000 if min_endpoint_population is None else min_endpoint_population  # Require 1000+ to avoid low-pop areas (e.g. NJ waterfront)
    return _impl(game_map, current_year, max_points, existing_line_routes=existing_line_routes,
                 min_length_grid=min_length_grid, max_length_grid=max_length_grid,
                 min_endpoint_population=min_pop, rng=rng)


def count_available_line_corridors(game_map, current_year: int, bounds=None) -> dict:
    from map_presets_nyc_road_utils import count_available_line_corridors as _impl
    return _impl(game_map, current_year, bounds=bounds)


def get_geo_feature_name_for_line_naming(
    game_map, x: float, y: float, current_year: int = None, max_distance_m: float = 150.0,
) -> str:
    """Geographic label within max_distance_m of (x,y) for AI line names (OSM roads)."""
    from map_presets_nyc_road_utils import get_geo_feature_name_for_line_naming as _impl
    year = int(current_year) if current_year is not None else (getattr(game_map, "start_year", 1830) or 1830)
    return _impl(game_map, float(x), float(y), year, bounds=get_nyc_bounds(), max_distance_m=max_distance_m)


def get_streets_route_runs_along(
    game_map,
    route_points: list,
    current_year: int = None,
    max_distance_m: float = 40.0,
    min_route_fraction: float = 0.5,
) -> list:
    """Street names whose OSM polylines the route follows within max_distance_m."""
    from map_presets_nyc_road_utils import get_streets_route_runs_along as _impl
    year = int(current_year) if current_year is not None else (getattr(game_map, "start_year", 1830) or 1830)
    return _impl(
        game_map, list(route_points or []), year, bounds=get_nyc_bounds(),
        max_distance_m=max_distance_m, min_route_fraction=min_route_fraction,
    )


def get_bridge_polyline_between_shores(
    game_map, shore_a: tuple, shore_b: tuple, current_year: int, max_points: int = 12,
) -> list:
    from map_presets_nyc_road_utils import get_bridge_polyline_between_shores as _impl
    return _impl(game_map, shore_a, shore_b, current_year, bounds=None, max_points=max_points)


def get_road_extension_points_from_end(game_map, base_pt: tuple, other_end_pt: tuple, current_year: int,
                                       extend_from_end: bool, max_points: int = 6, prefer_bridges: bool = False,
                                       exclude_road_names=None, _chain_depth: int = 0,
                                       max_neighbor_street_gap_grid: float = None) -> list:
    """Return grid points along street polylines for AI line extension. Used by map_preset_loader."""
    from map_presets_nyc_road_utils import get_road_extension_points_from_end as _impl
    return _impl(
        game_map, base_pt, other_end_pt, current_year, extend_from_end, max_points,
        bounds=None, prefer_bridges=prefer_bridges, exclude_road_names=exclude_road_names, _chain_depth=_chain_depth,
        max_neighbor_street_gap_grid=max_neighbor_street_gap_grid,
    )


def get_ferry_segment_points_for_ai(game_map, current_year: int, max_points: int = 12, existing_line_routes=None, rng=None) -> list:
    """NYC ferry route candidates: water crossings near population, ending on the shore."""
    from map_preset_utils import ai_ferry_segment_candidates
    return ai_ferry_segment_candidates('nyc', get_nyc_bounds(), game_map, max_points=max_points, rng=rng)


def get_station_name_for_point(x: float, y: float, game_map, current_year: int = None, line_name: str = None, existing_names: set = None) -> str:
    """Return a human-friendly station name for (x,y) on the NYC map.
    Prefers nearby road names; falls back to borough/area label. Intended for AI station naming."""
    try:
        year = int(current_year) if current_year is not None else getattr(game_map, "start_year", 1830) or 1830
    except Exception:
        year = 1830
    existing_names = existing_names or set()

    # Cross-streets ("Canal St & Broadway") or the neighbourhood; older logic below is the fallback.
    try:
        from geo_naming import station_name_for_point
        geo = station_name_for_point(game_map, x, y, year, existing_names)
        if geo:
            return geo
    except Exception:
        pass

    try:
        from game_presets import pick_major_terminal_station_name
        year_i = int(year)
        mix = (
            int(getattr(game_map, "start_year", 1830) or 1830)
            ^ year_i * 997
            ^ int(round(x * 1000))
            ^ (int(round(y * 1000)) << 7)
            ^ len(existing_names) * 31
        )
        major = pick_major_terminal_station_name(mix, existing_names)
        if major:
            return major
    except Exception:
        pass

    area_label = ""
    try:
        import math
        from game_presets import AI_STATION_STREET_NAME_MAX_DISTANCE_GRID
        area_map = get_point_area_map() or {}
        ix, iy = int(round(x)), int(round(y))
        best_d = float("inf")
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                key = (ix + dx, iy + dy)
                a = area_map.get(key)
                if not isinstance(a, str) or not a.strip():
                    continue
                d = math.hypot(x - key[0], y - key[1])
                if d <= AI_STATION_STREET_NAME_MAX_DISTANCE_GRID and d < best_d:
                    best_d = d
                    area_label = a.strip()
    except Exception:
        area_label = ""

    # 1) Nearest road name (best signal) — wider search catches dense street grid
    road_name = ""
    try:
        from map_presets_nyc_road_utils import get_nearest_road_name_for_point, format_osm_road_name_for_display
        from game_presets import AI_STATION_STREET_NAME_MAX_DISTANCE_GRID
        road_name = get_nearest_road_name_for_point(
            game_map, x, y, year, bounds=get_nyc_bounds(), tolerance_grid=2.0, max_segments=220,
            max_distance_grid=AI_STATION_STREET_NAME_MAX_DISTANCE_GRID,
        )
    except Exception:
        road_name = ""

    def _clean(s: str) -> str:
        s = (s or "").replace("_", " ").strip()
        # Drop common internal suffixes while keeping meaningful phrases
        for suf in (" Manhattan", " Brooklyn", " Queens", " Bronx", " Staten Island"):
            if s.endswith(suf) and len(s) > len(suf) + 3:
                s = s[: -len(suf)].strip()
        return s

    def _title_road_phrase(s: str) -> str:
        """Make OSM key fragments readable (preserve ordinals like 42nd)."""
        return format_osm_road_name_for_display(s)

    street_display = _title_road_phrase(road_name)
    name = street_display

    # Combine neighborhood + street when both are informative (e.g. "Harlem / Lexington Av")
    if area_label and street_display:
        al, sl = area_label.lower(), street_display.lower()
        if al not in sl and sl not in al:
            name = f"{area_label} — {street_display}"
    elif area_label and not street_display:
        name = area_label

    # 3) Last resort
    if not name:
        name = "Station"

    # Ensure uniqueness — prefer neighborhood or grid position over "Jackson Street (2)" for every stop.
    if name not in existing_names:
        return name
    if area_label and area_label.lower() not in name.lower():
        area_cand = f"{area_label} — {name}"
        if area_cand not in existing_names:
            return area_cand
    k = 2
    while f"{name} ({k})" in existing_names and k < 999:
        k += 1
    return f"{name} ({k})"


def initialize_nyc_population(width: int, height: int, map_center_lat: float = None, map_center_lon: float = None) -> dict:
    """Initialize population dictionary for NYC map from NYC_INITIAL_POPULATION."""
    from map_preset_utils import get_preset_file_info, read_initial_population_from_file

    class _TempMap:
        map_preset_name = "nyc"

    source = dict(NYC_INITIAL_POPULATION)
    preset_info = get_preset_file_info(_TempMap())
    if preset_info:
        file_pop = read_initial_population_from_file(*preset_info)
        if file_pop:
            source = file_pop

    from map_preset_utils import spread_population_from_anchors

    anchors: dict[tuple[int, int], int] = {}
    for (x, y), pop in source.items():
        if 0 <= x <= width and 0 <= y <= height:
            anchors[(x, y)] = int(pop or 0)

    return spread_population_from_anchors(anchors, width, height)


# Pre-computed excluded points - cleared, will be regenerated from OSM
NYC_EXCLUDED_POINTS = []


NYC_INITIAL_POPULATION: dict[tuple[int, int], int] = {
    (10, 5): 26,
    (10, 6): 24,
    (10, 7): 6,
    (10, 10): 32,
    (11, 6): 29,
    (11, 7): 39,
    (11, 8): 50,
    (11, 9): 30,
    (11, 10): 20,
    (11, 11): 8,
    (12, 6): 48,
    (12, 7): 50,
    (12, 8): 26,
    (12, 9): 38,
    (12, 10): 26,
    (12, 11): 11,
    (13, 7): 47,
    (13, 8): 50,
    (13, 9): 48,
    (13, 10): 22,
    (13, 11): 9,
    (13, 12): 5,
    (13, 13): 15,
    (13, 14): 32,
    (14, 7): 46,
    (14, 8): 44,
    (14, 9): 50,
    (14, 10): 39,
    (14, 11): 29,
    (14, 12): 22,
    (14, 13): 19,
    (14, 14): 50,
    (14, 16): 29,
    (14, 17): 45,
    (14, 18): 25,
    (14, 19): 49,
    (14, 20): 30,
    (15, 8): 49,
    (15, 9): 35,
    (15, 10): 13,
    (15, 11): 45,
    (15, 12): 21,
    (15, 13): 28,
    (15, 14): 26,
    (15, 15): 35,
    (15, 16): 47,
    (15, 17): 11,
    (15, 18): 15,
    (15, 19): 26,
    (15, 20): 25,
    (16, 9): 10,
    (16, 10): 6,
    (16, 11): 5,
    (16, 12): 12,
    (16, 13): 34,
    (16, 14): 15,
    (16, 15): 24,
    (16, 16): 43,
    (16, 17): 35,
    (16, 18): 37,
    (16, 19): 7,
    (16, 20): 31,
    (17, 9): 42,
    (17, 10): 5,
    (17, 11): 45,
    (17, 12): 35,
    (17, 14): 47,
    (17, 16): 31,
    (17, 17): 45,
    (17, 18): 11,
    (17, 20): 44,
    (18, 11): 11,
    (18, 12): 5,
    (18, 13): 38,
    (18, 14): 9,
    (18, 15): 45,
    (18, 16): 30,
    (18, 17): 25,
    (18, 18): 43,
    (18, 19): 35,
    (18, 20): 40,
    (19, 10): 6,
    (19, 11): 28,
    (19, 12): 46,
    (19, 13): 34,
    (19, 14): 23,
    (19, 15): 15,
    (19, 16): 44,
    (19, 17): 30,
    (19, 18): 7,
    (19, 19): 39,
    (19, 20): 23,
    (20, 11): 9,
    (20, 12): 22,
    (20, 13): 32,
    (20, 14): 16,
    (20, 15): 17,
    (20, 16): 48,
    (20, 17): 31,
    (20, 18): 14,
    (20, 19): 11,
    (20, 20): 14,
    (21, 12): 38,
    (21, 13): 15,
    (21, 15): 28,
    (21, 19): 10,
    (21, 20): 24,
    (22, 14): 50,
    (22, 15): 25,
    (22, 16): 37,
    (22, 17): 17,
    (22, 18): 48,
    (22, 19): 40,
    (22, 20): 28,
    (22, 21): 12,
    (23, 15): 31,
    (23, 16): 38,
    (23, 17): 50,
    (23, 18): 7,
    (24, 15): 17,
    (24, 16): 29,
    (25, 18): 37,
    (25, 19): 8,
    (25, 20): 25,
    (26, 17): 9,
    (26, 18): 17,
    (26, 19): 49,
    (26, 20): 16,
    (26, 21): 16,
    (26, 25): 465,
    (26, 26): 262,
    (26, 29): 50,
    (27, 13): 88,
    (27, 14): 71,
    (27, 17): 56,
    (27, 18): 48,
    (27, 19): 70,
    (27, 20): 24,
    (27, 21): 97,
    (27, 22): 95,
    (27, 23): 66,
    (27, 24): 125,
    (27, 25): 255,
    (27, 26): 302,
    (27, 27): 1327,
    (27, 28): 1200,
    (27, 29): 1119,
    (27, 30): 1089,
    (27, 31): 1358,
    (27, 32): 621,
    (27, 33): 594,
    (28, 13): 58,
    (28, 14): 71,
    (28, 15): 46,
    (28, 16): 94,
    (28, 17): 75,
    (28, 18): 96,
    (28, 19): 99,
    (28, 20): 63,
    (28, 21): 86,
    (28, 22): 161,
    (28, 23): 50,
    (28, 24): 152,
    (28, 25): 296,
    (28, 26): 293,
    (28, 27): 1043,
    (28, 28): 982,
    (28, 29): 1420,
    (28, 30): 1419,
    (28, 31): 895,
    (28, 32): 402,
    (28, 33): 637,
    (28, 34): 546,
    (28, 35): 626,
    (29, 13): 47,
    (29, 14): 81,
    (29, 15): 63,
    (29, 16): 42,
    (29, 17): 37,
    (29, 18): 78,
    (29, 19): 65,
    (29, 20): 20,
    (29, 21): 66,
    (29, 22): 113,
    (29, 23): 78,
    (29, 24): 133,
    (29, 25): 259,
    (29, 26): 311,
    (29, 27): 1396,
    (29, 28): 1011,
    (29, 29): 955,
    (29, 30): 982,
    (29, 31): 1278,
    (29, 32): 740,
    (29, 33): 741,
    (29, 34): 727,
    (29, 35): 417,
    (29, 36): 648,
    (29, 37): 592,
    (30, 13): 38,
    (30, 14): 40,
    (30, 15): 79,
    (30, 16): 92,
    (30, 17): 44,
    (30, 18): 31,
    (30, 19): 45,
    (30, 20): 90,
    (30, 21): 31,
    (30, 22): 156,
    (30, 23): 78,
    (30, 24): 145,
    (30, 25): 439,
    (30, 26): 495,
    (30, 27): 934,
    (30, 28): 822,
    (30, 29): 1384,
    (30, 30): 1338,
    (30, 31): 1482,
    (30, 32): 493,
    (30, 33): 657,
    (30, 34): 683,
    (30, 35): 405,
    (30, 36): 463,
    (30, 37): 450,
    (30, 38): 120,
    (30, 39): 109,
    (31, 13): 45,
    (31, 14): 81,
    (31, 15): 57,
    (31, 16): 70,
    (31, 17): 44,
    (31, 18): 45,
    (31, 19): 42,
    (31, 20): 99,
    (31, 21): 93,
    (31, 22): 78,
    (31, 23): 150,
    (31, 24): 177,
    (31, 25): 240,
    (31, 26): 397,
    (31, 27): 1061,
    (31, 28): 939,
    (31, 29): 1490,
    (31, 30): 1194,
    (31, 31): 990,
    (31, 32): 464,
    (31, 33): 616,
    (31, 34): 788,
    (31, 35): 717,
    (31, 36): 475,
    (31, 37): 780,
    (31, 38): 140,
    (31, 39): 117,
    (31, 40): 75,
    (31, 41): 49,
    (32, 10): 26,
    (32, 11): 95,
    (32, 13): 95,
    (32, 14): 21,
    (32, 15): 81,
    (32, 16): 97,
    (32, 17): 98,
    (32, 18): 23,
    (32, 19): 81,
    (32, 20): 60,
    (32, 21): 38,
    (32, 22): 87,
    (32, 23): 76,
    (32, 24): 42,
    (32, 25): 59,
    (32, 26): 78,
    (32, 27): 833,
    (32, 28): 845,
    (32, 29): 998,
    (32, 30): 1467,
    (32, 31): 1387,
    (32, 32): 664,
    (32, 33): 644,
    (32, 34): 483,
    (32, 35): 474,
    (32, 36): 800,
    (32, 37): 677,
    (32, 38): 70,
    (32, 39): 76,
    (32, 40): 31,
    (32, 41): 27,
    (32, 42): 35,
    (33, 10): 24,
    (33, 11): 32,
    (33, 12): 98,
    (33, 14): 57,
    (33, 15): 20,
    (33, 16): 36,
    (33, 17): 96,
    (33, 18): 55,
    (33, 19): 81,
    (33, 20): 53,
    (33, 21): 48,
    (33, 22): 136,
    (33, 23): 117,
    (33, 24): 38,
    (33, 25): 54,
    (33, 26): 97,
    (33, 27): 821,
    (33, 28): 875,
    (33, 29): 894,
    (33, 30): 1472,
    (33, 31): 1136,
    (33, 32): 771,
    (33, 33): 652,
    (33, 34): 472,
    (33, 35): 707,
    (33, 37): 643,
    (33, 43): 28,
    (33, 46): 46,
    (33, 47): 45,
    (33, 48): 37,
    (34, 10): 61,
    (34, 11): 67,
    (34, 12): 91,
    (34, 14): 91,
    (34, 15): 71,
    (34, 16): 30,
    (34, 17): 60,
    (34, 18): 47,
    (34, 19): 84,
    (34, 20): 76,
    (34, 21): 81,
    (34, 22): 72,
    (34, 23): 60,
    (34, 24): 134,
    (34, 25): 11,
    (34, 26): 96,
    (34, 27): 16,
    (34, 28): 55,
    (34, 29): 62,
    (34, 30): 51,
    (34, 31): 96,
    (34, 32): 37,
    (34, 33): 17,
    (34, 34): 79,
    (34, 35): 91,
    (34, 36): 84,
    (34, 37): 22,
    (34, 38): 20,
    (34, 39): 49,
    (34, 40): 25,
    (34, 41): 68,
    (34, 42): 42,
    (34, 43): 80,
    (34, 44): 51,
    (34, 45): 34,
    (34, 46): 12,
    (34, 47): 78,
    (34, 48): 24,
    (34, 49): 80,
    (35, 11): 100,
    (35, 12): 96,
    (35, 14): 92,
    (35, 15): 78,
    (35, 16): 35,
    (35, 17): 47,
    (35, 18): 76,
    (35, 19): 100,
    (35, 20): 97,
    (35, 21): 27,
    (35, 22): 143,
    (35, 23): 132,
    (35, 24): 106,
    (35, 25): 57,
    (35, 26): 47,
    (35, 27): 30,
    (35, 28): 36,
    (35, 29): 40,
    (35, 30): 15,
    (35, 31): 97,
    (35, 32): 35,
    (35, 33): 42,
    (35, 34): 53,
    (35, 35): 79,
    (35, 36): 89,
    (35, 37): 50,
    (35, 38): 37,
    (35, 39): 29,
    (35, 40): 89,
    (35, 41): 70,
    (35, 42): 52,
    (35, 43): 50,
    (35, 44): 86,
    (35, 45): 77,
    (35, 46): 11,
    (35, 47): 86,
    (35, 48): 91,
    (35, 49): 16,
    (36, 11): 97,
    (36, 12): 27,
    (36, 14): 47,
    (36, 15): 37,
    (36, 16): 43,
    (36, 17): 25,
    (36, 18): 87,
    (36, 19): 83,
    (36, 20): 23,
    (36, 21): 55,
    (36, 22): 68,
    (36, 23): 13,
    (36, 24): 66,
    (36, 25): 36,
    (36, 26): 13,
    (36, 27): 92,
    (36, 28): 81,
    (36, 29): 25,
    (36, 30): 76,
    (36, 31): 31,
    (36, 32): 90,
    (36, 33): 36,
    (36, 34): 99,
    (36, 35): 95,
    (36, 37): 77,
    (36, 38): 13,
    (36, 39): 16,
    (36, 40): 72,
    (36, 41): 28,
    (36, 42): 48,
    (36, 43): 97,
    (36, 44): 41,
    (36, 45): 80,
    (36, 46): 81,
    (37, 12): 67,
    (37, 13): 60,
    (37, 14): 91,
    (37, 15): 79,
    (37, 16): 79,
    (37, 17): 17,
    (37, 19): 71,
    (37, 20): 95,
    (37, 21): 48,
    (37, 22): 51,
    (37, 23): 14,
    (37, 24): 15,
    (37, 25): 61,
    (37, 26): 10,
    (37, 27): 90,
    (37, 28): 37,
    (37, 29): 21,
    (37, 30): 81,
    (37, 31): 73,
    (37, 32): 52,
    (37, 33): 12,
    (37, 34): 97,
    (37, 35): 25,
    (37, 37): 81,
    (37, 38): 88,
    (37, 39): 47,
    (37, 40): 39,
    (37, 41): 90,
    (37, 42): 86,
    (37, 43): 31,
    (37, 44): 88,
    (37, 45): 90,
    (37, 46): 93,
    (38, 12): 92,
    (38, 13): 12,
    (38, 14): 61,
    (38, 20): 51,
    (38, 21): 40,
    (38, 22): 61,
    (38, 23): 31,
    (38, 24): 33,
    (38, 25): 75,
    (38, 26): 24,
    (38, 27): 50,
    (38, 28): 95,
    (38, 29): 26,
    (38, 30): 13,
    (38, 31): 73,
    (38, 32): 22,
    (38, 33): 55,
    (38, 34): 88,
    (38, 35): 18,
    (38, 38): 96,
    (38, 39): 19,
    (38, 40): 83,
    (38, 41): 64,
    (38, 42): 53,
    (38, 43): 54,
    (38, 44): 16,
    (38, 45): 74,
    (38, 46): 70,
    (39, 13): 120,
    (39, 21): 45,
    (39, 22): 38,
    (39, 23): 147,
    (39, 24): 125,
    (39, 25): 20,
    (39, 26): 77,
    (39, 27): 24,
    (39, 28): 112,
    (39, 29): 48,
    (39, 30): 140,
    (39, 31): 131,
    (39, 32): 101,
    (39, 33): 131,
    (39, 34): 146,
    (39, 35): 116,
    (39, 37): 103,
    (39, 38): 94,
    (39, 39): 98,
    (39, 40): 99,
    (39, 41): 35,
    (39, 42): 51,
    (39, 43): 41,
    (39, 44): 45,
    (39, 45): 60,
    (39, 46): 31,
    (39, 47): 28,
    (39, 48): 30,
    (39, 49): 24,
    (40, 13): 15,
    (40, 21): 93,
    (40, 22): 108,
    (40, 23): 30,
    (40, 24): 124,
    (40, 25): 105,
    (40, 26): 69,
    (40, 27): 42,
    (40, 28): 33,
    (40, 29): 36,
    (40, 30): 19,
    (40, 31): 30,
    (40, 32): 65,
    (40, 33): 92,
    (40, 34): 89,
    (40, 35): 116,
    (40, 37): 11,
    (40, 38): 72,
    (40, 39): 64,
    (40, 40): 45,
    (40, 41): 88,
    (40, 42): 100,
    (40, 43): 51,
    (40, 44): 57,
    (40, 45): 26,
    (40, 46): 38,
    (40, 47): 32,
    (40, 48): 34,
    (41, 21): 118,
    (41, 22): 83,
    (41, 23): 83,
    (41, 24): 93,
    (41, 25): 111,
    (41, 26): 108,
    (41, 27): 20,
    (41, 28): 67,
    (41, 29): 115,
    (41, 30): 114,
    (41, 31): 135,
    (41, 32): 23,
    (41, 33): 140,
    (41, 34): 17,
    (41, 35): 125,
    (41, 38): 31,
    (41, 39): 56,
    (41, 40): 62,
    (41, 41): 91,
    (41, 42): 68,
    (41, 43): 44,
    (41, 44): 10,
    (41, 45): 35,
    (41, 46): 34,
    (41, 47): 10,
    (42, 21): 23,
    (42, 22): 67,
    (42, 23): 101,
    (42, 24): 107,
    (42, 25): 22,
    (42, 26): 28,
    (42, 27): 38,
    (42, 28): 51,
    (42, 29): 124,
    (42, 30): 60,
    (42, 31): 73,
    (42, 32): 45,
    (42, 33): 75,
    (42, 34): 98,
    (42, 35): 57,
    (42, 37): 133,
    (42, 38): 34,
    (42, 39): 86,
    (42, 40): 41,
    (42, 41): 72,
    (42, 42): 63,
    (42, 43): 19,
    (42, 44): 17,
    (42, 45): 20,
    (42, 46): 17,
    (42, 47): 14,
    (43, 21): 43,
    (43, 22): 132,
    (43, 23): 128,
    (43, 24): 76,
    (43, 25): 91,
    (43, 26): 31,
    (43, 27): 101,
    (43, 28): 113,
    (43, 29): 94,
    (43, 30): 47,
    (43, 31): 103,
    (43, 32): 93,
    (43, 33): 63,
    (43, 34): 130,
    (43, 35): 65,
    (43, 37): 109,
    (43, 38): 88,
    (43, 39): 66,
    (43, 40): 53,
    (43, 41): 41,
    (43, 42): 52,
    (43, 43): 46,
    (43, 44): 55,
    (43, 45): 17,
    (43, 46): 33,
    (44, 21): 97,
    (44, 22): 94,
    (44, 23): 70,
    (44, 24): 20,
    (44, 25): 46,
    (44, 26): 125,
    (44, 27): 26,
    (44, 28): 96,
    (44, 29): 81,
    (44, 30): 70,
    (44, 31): 42,
    (44, 32): 54,
    (44, 33): 84,
    (44, 34): 90,
    (44, 35): 134,
    (44, 37): 81,
    (44, 38): 57,
    (44, 39): 88,
    (44, 40): 82,
    (44, 41): 73,
    (44, 42): 58,
    (44, 43): 44,
    (44, 44): 19,
    (44, 45): 53,
    (44, 46): 10,
    (45, 21): 126,
    (45, 22): 43,
    (45, 23): 67,
    (45, 24): 135,
    (45, 25): 35,
    (45, 26): 130,
    (45, 27): 49,
    (45, 28): 150,
    (45, 29): 147,
    (45, 30): 119,
    (45, 31): 62,
    (45, 32): 32,
    (45, 33): 118,
    (45, 34): 28,
    (45, 35): 15,
    (45, 41): 53,
    (45, 42): 73,
    (45, 43): 20,
    (45, 44): 16,
    (45, 45): 19,
    (45, 46): 5,
    (46, 21): 85,
    (46, 22): 96,
    (46, 23): 129,
    (46, 24): 42,
    (46, 25): 39,
    (46, 26): 85,
    (46, 27): 49,
    (46, 28): 21,
    (46, 29): 92,
    (46, 30): 95,
    (46, 31): 123,
    (46, 32): 67,
    (46, 33): 102,
    (46, 34): 91,
    (46, 42): 54,
    (46, 43): 53,
    (46, 44): 40,
    (46, 45): 36,
    (47, 22): 139,
    (47, 23): 141,
    (47, 24): 18,
    (47, 25): 112,
    (47, 26): 74,
    (47, 27): 72,
    (47, 28): 79,
    (47, 29): 42,
    (47, 30): 12,
    (47, 31): 145,
    (47, 32): 32,
    (47, 33): 15,
    (48, 30): 107,
    (48, 31): 17,
    (48, 32): 29,
    (49, 31): 108,
    (49, 32): 106
}


def ai_relax_polyline_self_overlap_for_osm_candidate(
    current_year: int, min_pop_road: int, existing_line_routes,
) -> bool:
    """If True, ai.py may build despite polyline_has_self_overlap on preset OSM routes.

    Dense Manhattan corridors often false-positive after grid sampling; the same relaxation exists in
    map_presets_nyc_road_utils when collecting candidates (relax_self_overlap).
    """
    if existing_line_routes:
        return False
    return current_year == 1830 and int(min_pop_road or 0) >= 1000
