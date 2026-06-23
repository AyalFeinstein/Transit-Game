"""Generate NYC excluded points for map_presets_nyc.py
This script should only be run when updating map_presets_nyc.py.
It generates the NYC_EXCLUDED_POINTS list based on is_water_location function and borough boundaries.
Uses approximate bounds (same as OSMIntegration fallback) to avoid slow OSM API calls."""

from map_presets_nyc import NYC_BOUNDS, is_water_location
import math

width, height = 50, 50
grid_size = max(width, height)
center_lat, center_lon = NYC_BOUNDS['center_lat'], NYC_BOUNDS['center_lon']

def is_in_nyc_borough_approximate(lat: float, lon: float) -> bool:
    """Check if point is in NYC borough using approximate bounds.
    Expanded to cover all land in the five boroughs."""
    # Manhattan - covers all of Manhattan including small islands
    if 40.70 <= lat <= 40.88 and -74.05 <= lon <= -73.90:
        return True
    # Brooklyn - covers all of Brooklyn including southern and eastern parts
    elif 40.57 <= lat <= 40.75 and -74.05 <= lon <= -73.70:
        return True
    # Queens - covers all of Queens including northern and eastern parts
    elif 40.55 <= lat <= 40.80 and -74.05 <= lon <= -73.70:
        return True
    # Bronx - covers all of the Bronx including northern parts
    elif 40.80 <= lat <= 40.92 and -73.95 <= lon <= -73.75:
        return True
    # Staten Island (Richmond) - extended west to cover all of Staten Island
    elif 40.49 <= lat <= 40.65 and -74.30 <= lon <= -74.02:
        return True
    return False

excluded_points = []
included_points = []
total_points = (width + 1) * (height + 1)
processed = 0

print(f"Generating excluded and included points for {width}x{height} grid ({total_points} points total)...")

for x in range(width + 1):
    for y in range(height + 1):
        processed += 1
        if processed % 500 == 0:
            print(f"Processing: {processed}/{total_points} points ({processed*100//total_points}%)...")
        
        # Convert grid coordinates to lat/lon using actual NYC bounds
        # Grid (0, 0) maps to (min_lat, min_lon), Grid (width, height) maps to (max_lat, max_lon)
        from map_presets_nyc import get_nyc_bounds
        nyc_bounds = get_nyc_bounds()
        min_lat = nyc_bounds['min_lat']
        max_lat = nyc_bounds['max_lat']
        min_lon = nyc_bounds['min_lon']
        max_lon = nyc_bounds['max_lon']
        
        # Linear interpolation: x/width maps to lon, y/height maps to lat
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        # Check if water location (pass None to use preset water areas, no OSM calls)
        is_water = is_water_location(lat, lon, None)
        
        # Check if within NYC borough boundaries using approximate bounds
        # Don't use OSM API calls - they're too slow for 2601 points
        is_in_borough = is_in_nyc_borough_approximate(lat, lon)
        
        if is_water or not is_in_borough:
            excluded_points.append((x, y))
        else:
            included_points.append((x, y))

print(f"\n# Pre-computed excluded points (water/non-NYC boroughs) for {width}x{height} grid")
print(f"# Generated using is_water_location function and borough boundary checking - do not edit manually")
print(f"# To regenerate, run: python3 generate_nyc_excluded_points.py")
print(f"NYC_EXCLUDED_POINTS = {excluded_points}")
print(f"# Total excluded points: {len(excluded_points)} out of {(width+1)*(height+1)}")

print(f"\n# Pre-computed included points (valid NYC borough points with population) for {width}x{height} grid")
print(f"# This is the reverse of NYC_EXCLUDED_POINTS - all points that should have population")
print(f"# Generated using is_water_location function and borough boundary checking - do not edit manually")
print(f"# To regenerate, run: python3 generate_nyc_excluded_points.py")
print(f"NYC_INCLUDED_POINTS = {included_points}")
print(f"# Total included points: {len(included_points)} out of {(width+1)*(height+1)}")

