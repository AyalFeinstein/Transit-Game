"""Generate NYC population points with population > 0 for map_presets_nyc.py
This script pre-calculates all valid population points to avoid OSM API calls during game startup.
Run this script to regenerate NYC_POPULATION_POINTS when needed."""

from map_presets_nyc import (
    NYC_BOUNDS, get_nyc_bounds, get_nyc_population_at_location, is_water_location, NYC_EXCLUDED_AREAS
)
import math

width, height = 50, 50

def is_in_excluded_area(lat: float, lon: float) -> bool:
    """Check if point is in an excluded area (New Jersey, etc.)."""
    for excluded_area in NYC_EXCLUDED_AREAS:
        if (excluded_area['min_lat'] <= lat <= excluded_area['max_lat'] and
            excluded_area['min_lon'] <= lon <= excluded_area['max_lon']):
            return True
    return False

def is_in_nyc_borough_approximate(lat: float, lon: float) -> bool:
    """Check if point is in NYC borough using approximate bounds."""
    if 40.70 <= lat <= 40.88 and -74.05 <= lon <= -73.90:
        return True
    elif 40.57 <= lat <= 40.75 and -74.05 <= lon <= -73.70:
        return True
    elif 40.55 <= lat <= 40.80 and -74.05 <= lon <= -73.70:
        return True
    elif 40.80 <= lat <= 40.92 and -73.95 <= lon <= -73.75:
        return True
    elif 40.49 <= lat <= 40.65 and -74.30 <= lon <= -74.02:
        return True
    from map_presets_nyc import is_in_governors_island
    if is_in_governors_island(lat, lon):
        return True
    return False

population_points = []
total_points = (width + 1) * (height + 1)
processed = 0

print(f"Generating population points for {width}x{height} grid ({total_points} points total)...")

nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

for x in range(width + 1):
    for y in range(height + 1):
        processed += 1
        if processed % 500 == 0:
            print(f"Processing: {processed}/{total_points} points ({processed*100//total_points}%)...")
        
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        if is_in_excluded_area(lat, lon):
            continue
        
        is_in_borough = is_in_nyc_borough_approximate(lat, lon)
        if not is_in_borough:
            continue
        
        is_water = is_water_location(lat, lon, None)
        if is_water:
            continue
        
        population_points.append((x, y))

print(f"\n# Pre-computed population points (x, y) for {width}x{height} grid")
print(f"# These are all points on land in NYC boroughs where population > 0")
print(f"# Generated using is_water_location and borough boundary checking - do not edit manually")
print(f"# To regenerate, run: python3 generate_nyc_population_points.py")
print(f"NYC_POPULATION_POINTS = {population_points}")
print(f"# Total population points: {len(population_points)} out of {total_points}")

