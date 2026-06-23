"""Pre-calculate water points for NYC map using OSM data.
This creates a cache of water points to avoid slow API calls during generation."""

from map_presets_nyc import NYC_BOUNDS, get_nyc_bounds
from osm_integration import OSMIntegration
import time

width, height = 50, 50
nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

print("Initializing OSM integration...")
osm = OSMIntegration(timeout=10.0)

water_points = set()
total_points = (width + 1) * (height + 1)
processed = 0

print(f"Checking {total_points} points for water using OSM...")
print("This may take several minutes...")

for x in range(width + 1):
    for y in range(height + 1):
        processed += 1
        if processed % 100 == 0:
            print(f"Processed {processed}/{total_points} ({processed*100//total_points}%)...")
        
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        if osm.is_water_at_latlon(lat, lon):
            water_points.add((x, y))
        
        time.sleep(0.1)

print(f"\nNYC_WATER_POINTS = {sorted(water_points)}")
print(f"# Total water points: {len(water_points)} out of {total_points}")

