"""Remove all points in Long Island Sound from NYC_INCLUDED_POINTS.
Long Island Sound bounds: lat 40.85-40.92, lon -73.95 to -73.85"""

from map_presets_nyc import get_nyc_bounds
import re

# Long Island Sound bounds (from map_presets.py)
LONG_ISLAND_SOUND_BOUNDS = {
    'min_lat': 40.85,
    'max_lat': 40.92,
    'min_lon': -73.95,
    'max_lon': -73.85
}

if __name__ == "__main__":
    nyc_bounds = get_nyc_bounds()
    min_lat = nyc_bounds['min_lat']
    max_lat = nyc_bounds['max_lat']
    min_lon = nyc_bounds['min_lon']
    max_lon = nyc_bounds['max_lon']
    
    # Read existing points from map_presets_nyc.py
    with open('map_presets_nyc.py', 'r') as f:
        content = f.read()
    
    match = re.search(r'NYC_INCLUDED_POINTS = \[(.*?)\]', content, re.DOTALL)
    if not match:
        print("ERROR: Could not find NYC_INCLUDED_POINTS in map_presets_nyc.py")
        exit(1)
    
    existing_points_str = match.group(1)
    existing_points = eval(f"[{existing_points_str}]")
    print(f"Current points: {len(existing_points)}")
    
    # Filter out points in Long Island Sound
    filtered_points = []
    removed_count = 0
    
    for x, y in existing_points:
        # Convert grid coordinates to lat/lon
        lat = min_lat + (y / 50) * (max_lat - min_lat)
        lon = min_lon + (x / 50) * (max_lon - min_lon)
        
        # Check if point is in Long Island Sound
        in_long_island_sound = (
            LONG_ISLAND_SOUND_BOUNDS['min_lat'] <= lat <= LONG_ISLAND_SOUND_BOUNDS['max_lat'] and
            LONG_ISLAND_SOUND_BOUNDS['min_lon'] <= lon <= LONG_ISLAND_SOUND_BOUNDS['max_lon']
        )
        
        if not in_long_island_sound:
            filtered_points.append((x, y))
        else:
            removed_count += 1
    
    print(f"Removed {removed_count} points in Long Island Sound")
    print(f"Remaining points: {len(filtered_points)}")
    
    # Write updated points back to map_presets_nyc.py
    all_points_list = sorted(filtered_points)
    new_line = f'NYC_INCLUDED_POINTS = {all_points_list}'
    
    # Replace the NYC_INCLUDED_POINTS line
    pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
    new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
    
    with open('map_presets_nyc.py', 'w') as f:
        f.write(new_content)
    
    print(f"Successfully updated map_presets_nyc.py with {len(all_points_list)} points (removed {removed_count} Long Island Sound points)")
    print("Removal complete!")

