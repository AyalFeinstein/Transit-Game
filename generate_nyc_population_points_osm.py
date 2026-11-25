"""Generate NYC population points using OSM polygon data for accurate coastline detection.
This script pre-calculates all valid population points using OSM API calls.
The results are saved to map_presets_nyc.py and should not need to be regenerated."""

from map_presets_nyc import (
    NYC_BOUNDS, get_nyc_bounds, get_nyc_population_at_location, NYC_EXCLUDED_AREAS
)
from osm_integration import OSMIntegration
import time
import sys

width, height = 50, 50

print("Initializing OSM integration for accurate water detection using polygon data...")
osm_integration = OSMIntegration(timeout=15.0)
print("OSM integration ready. This will take several minutes...")
print("")

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
    return False

population_points = []
total_points = (width + 1) * (height + 1)
processed = 0

nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

print(f"Generating population points for {width}x{height} grid ({total_points} points total)...")
print("Using OSM polygon data for accurate water detection...")
print("")

for x in range(width + 1):
    for y in range(height + 1):
        processed += 1
        if processed % 100 == 0:
            print(f"Processing: {processed}/{total_points} points ({processed*100//total_points}%)...")
            sys.stdout.flush()
        
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        if is_in_excluded_area(lat, lon):
            continue
        
        is_in_borough = is_in_nyc_borough_approximate(lat, lon)
        if not is_in_borough:
            continue
        
        is_water = osm_integration.is_water_at_latlon(lat, lon)
        if is_water:
            continue
        
        population_points.append((x, y))
        
        if processed % 5 == 0:
            time.sleep(0.05)

print(f"\n# Pre-computed population points (x, y) for {width}x{height} grid")
print(f"# Generated using OSM polygon data for accurate coastline detection")
print(f"# DO NOT REGENERATE - This list is saved and should be reused")
print(f"# Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"NYC_POPULATION_POINTS = {population_points}")
print(f"# Total population points: {len(population_points)} out of {total_points}")

import re
print("\nUpdating map_presets_nyc.py with generated points...")
with open('map_presets_nyc.py', 'r') as f:
    content = f.read()

pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
new_line = f'NYC_INCLUDED_POINTS = {population_points}'
new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)

with open('map_presets_nyc.py', 'w') as f:
    f.write(new_content)

print(f"Successfully updated map_presets_nyc.py with {len(population_points)} population points")
print("Generation complete! The list is now saved and will be reused.")

