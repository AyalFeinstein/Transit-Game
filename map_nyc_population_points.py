"""Map all NYC_INCLUDED_POINTS to their geographic locations and estimate 1830 population.
Creates a data structure that can be used for population events."""

from map_presets_nyc import get_nyc_bounds, NYC_INCLUDED_POINTS, get_nyc_population_at_location
import json

def map_population_points():
    """Map all population points to their geographic locations and 1830 population estimates."""
    nyc_bounds = get_nyc_bounds()
    min_lat = nyc_bounds['min_lat']
    max_lat = nyc_bounds['max_lat']
    min_lon = nyc_bounds['min_lon']
    max_lon = nyc_bounds['max_lon']
    width = nyc_bounds['width']
    height = nyc_bounds['height']
    
    population_map = {}
    
    for x, y in NYC_INCLUDED_POINTS:
        # Convert grid coordinates to lat/lon
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        # Estimate 1830 population (using the existing function but with fixed seed for consistency)
        # We'll call it multiple times and average for more stable estimates
        pop_estimates = []
        import random
        # Use a deterministic seed based on coordinates for reproducibility
        random.seed(hash((x, y)) % (2**32))
        for _ in range(5):
            pop = get_nyc_population_at_location(lat, lon, False)
            pop_estimates.append(pop)
        
        # Use median for more stable estimate
        pop_estimates.sort()
        estimated_pop_1830 = pop_estimates[2]  # Median
        
        population_map[(x, y)] = {
            'lat': lat,
            'lon': lon,
            'population_1830': estimated_pop_1830,
            'borough': _identify_borough(lat, lon)
        }
    
    return population_map

def _identify_borough(lat: float, lon: float) -> str:
    """Identify which NYC borough a point is in."""
    # Manhattan
    if 40.70 <= lat <= 40.88 and -74.02 <= lon <= -73.93:
        return 'Manhattan'
    # Bronx
    elif 40.80 <= lat <= 40.92 and -73.95 <= lon <= -73.75:
        return 'Bronx'
    # Brooklyn
    elif 40.50 <= lat <= 40.75 and -74.15 <= lon <= -73.70:
        return 'Brooklyn'
    # Queens
    elif 40.57 <= lat <= 40.80 and -74.05 <= lon <= -73.70:
        return 'Queens'
    # Staten Island
    elif 40.49 <= lat <= 40.65 and -74.30 <= lon <= -74.02:
        return 'Staten Island'
    else:
        return 'Unknown'

if __name__ == "__main__":
    print("Mapping NYC population points...")
    population_map = map_population_points()
    
    print(f"\nMapped {len(population_map)} population points")
    print("\nSample points:")
    for i, ((x, y), data) in enumerate(list(population_map.items())[:5]):
        print(f"  Point ({x}, {y}): lat={data['lat']:.4f}, lon={data['lon']:.4f}, "
              f"pop_1830={data['population_1830']}, borough={data['borough']}")
    
    # Save to a Python file that can be imported
    output_file = 'nyc_population_map.py'
    with open(output_file, 'w') as f:
        f.write('"""NYC Population Point Map\n')
        f.write('Maps all NYC_INCLUDED_POINTS to their geographic locations and 1830 population estimates.\n')
        f.write('Generated automatically - do not edit manually.\n')
        f.write('"""\n\n')
        f.write('NYC_POPULATION_MAP = {\n')
        for (x, y), data in sorted(population_map.items()):
            f.write(f"    ({x}, {y}): {{\n")
            f.write(f"        'lat': {data['lat']:.6f},\n")
            f.write(f"        'lon': {data['lon']:.6f},\n")
            f.write(f"        'population_1830': {data['population_1830']},\n")
            f.write(f"        'borough': '{data['borough']}',\n")
            f.write(f"    }},\n")
        f.write('}\n')
    
    print(f"\nSaved population map to {output_file}")
    print("Generation complete!")

