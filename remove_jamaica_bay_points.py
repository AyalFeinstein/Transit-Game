"""Remove all points in Jamaica Bay Wildlife Reserve from NYC_INCLUDED_POINTS."""

from map_presets_nyc import get_nyc_bounds, NYC_INCLUDED_POINTS
import re

# Jamaica Bay Wildlife Reserve approximate bounds
# Jamaica Bay is located in Queens, roughly between:
# Latitude: 40.58 to 40.64
# Longitude: -73.85 to -73.75
JAMAICA_BAY_BOUNDS = {
    'min_lat': 40.58,
    'max_lat': 40.64,
    'min_lon': -73.85,
    'max_lon': -73.75
}

if __name__ == "__main__":
    width, height = 50, 50
    nyc_bounds = get_nyc_bounds()
    min_lat = nyc_bounds['min_lat']
    max_lat = nyc_bounds['max_lat']
    min_lon = nyc_bounds['min_lon']
    max_lon = nyc_bounds['max_lon']
    
    # Read existing points from map_presets_nyc.py
    with open('map_presets_nyc.py', 'r') as f:
        content = f.read()
    
    match = re.search(r'NYC_INCLUDED_POINTS = \[(.*?)\]', content, re.DOTALL)
    if match:
        existing_points_str = match.group(1)
        existing_points = eval(f"[{existing_points_str}]")
        print(f"Current points: {len(existing_points)}")
    else:
        existing_points = []
        print("No existing NYC_INCLUDED_POINTS found.")
        exit(1)
    
    # Remove points in Jamaica Bay
    print("\n=== Removing Jamaica Bay Wildlife Reserve points ===")
    points_to_remove = []
    for (x, y) in existing_points:
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        if (JAMAICA_BAY_BOUNDS['min_lat'] <= lat <= JAMAICA_BAY_BOUNDS['max_lat'] and
            JAMAICA_BAY_BOUNDS['min_lon'] <= lon <= JAMAICA_BAY_BOUNDS['max_lon']):
            points_to_remove.append((x, y))
    
    remaining_points = [p for p in existing_points if p not in points_to_remove]
    
    print(f"Removed {len(points_to_remove)} points in Jamaica Bay Wildlife Reserve")
    print(f"Remaining points: {len(remaining_points)}")
    
    # Write updated points back to map_presets_nyc.py
    all_points_list = sorted(remaining_points)
    print("\nUpdating map_presets_nyc.py...")
    new_line = f'NYC_INCLUDED_POINTS = {all_points_list}'
    
    # Replace the NYC_INCLUDED_POINTS line
    pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
    new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
    
    with open('map_presets_nyc.py', 'w') as f:
        f.write(new_content)
    
    print(f"Successfully updated map_presets_nyc.py with {len(all_points_list)} total points")
    print("Removal complete!")

