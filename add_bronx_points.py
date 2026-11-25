"""Add Bronx County population points to NYC_INCLUDED_POINTS.
Queries OSM relation for Bronx County and filters out water points."""

from map_presets_nyc import get_nyc_bounds, NYC_INCLUDED_POINTS, is_water_location
from osm_integration import OSMIntegration
import time
import sys
import math
import re

width, height = 50, 50

print("Initializing OSM integration...")
osm_integration = OSMIntegration(timeout=60.0)
print("OSM integration ready.")

def point_in_polygon(point, polygon):
    """Check if a point is inside a polygon using ray casting algorithm."""
    x, y = point
    n = len(polygon)
    if n < 3:
        return False
    
    inside = False
    j = n - 1
    
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        
        if yi == yj:
            j = i
            continue
            
        if ((yi > y) != (yj > y)):
            intersect_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < intersect_x:
                inside = not inside
        j = i
    
    return inside

def get_polygon_from_relation(relation_id):
    """Get polygon geometry from an OSM relation ID."""
    print(f"Querying OSM for relation {relation_id}...")
    
    query = f"""
    [out:json][timeout:60];
    (
      relation(id:{relation_id});
    );
    (._;>;);
    out geom;
    """
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            import requests
            response = requests.post(osm_integration.base_url, data=query, timeout=120)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 20
                print(f"  Attempt {attempt + 1} failed: {e}. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                raise
    
    try:
        relation_element = None
        for element in data.get('elements', []):
            if element.get('type') == 'relation' and element.get('id') == relation_id:
                relation_element = element
                break
        
        if not relation_element:
            print(f"Relation {relation_id} not found")
            return None
        
        members = relation_element.get('members', [])
        outer_ways = []
        for member in members:
            role = member.get('role', '')
            if (role == 'outer' or role == '') and member.get('type') == 'way':
                outer_ways.append(member.get('ref'))
        
        if not outer_ways:
            # Try to get all ways if no outer ways found
            print(f"No outer ways found for relation {relation_id}, trying all ways...")
            for member in members:
                if member.get('type') == 'way':
                    outer_ways.append(member.get('ref'))
        
        if not outer_ways:
            print(f"No ways found for relation {relation_id}")
            print(f"Relation has {len(members)} members")
            return None
        
        way_coords_dict = {}
        for element in data.get('elements', []):
            if element.get('type') == 'way' and element.get('id') in outer_ways:
                geometry = element.get('geometry', [])
                coords = [(node.get('lon'), node.get('lat')) for node in geometry]
                if coords:
                    way_coords_dict[element.get('id')] = coords
        
        if not way_coords_dict:
            print(f"No way coordinates found for relation {relation_id}")
            return None
        
        # Connect ways into a polygon
        polygon = []
        used_ways = set()
        
        def find_next_way(current_end, way_coords):
            best_match = None
            best_distance = float('inf')
            reverse = False
            
            for way_id, coords in way_coords.items():
                start = coords[0]
                end = coords[-1]
                
                dist_to_start = math.sqrt((start[0] - current_end[0])**2 + (start[1] - current_end[1])**2)
                dist_to_end = math.sqrt((end[0] - current_end[0])**2 + (end[1] - current_end[1])**2)
                
                if dist_to_start < best_distance:
                    best_distance = dist_to_start
                    best_match = way_id
                    reverse = False
                
                if dist_to_end < best_distance:
                    best_distance = dist_to_end
                    best_match = way_id
                    reverse = True
            
            return best_match, reverse
        
        first_way_id = list(way_coords_dict.keys())[0]
        first_coords = way_coords_dict[first_way_id]
        polygon.extend(first_coords)
        used_ways.add(first_way_id)
        current_end = first_coords[-1]
        
        remaining_ways = {k: v for k, v in way_coords_dict.items() if k not in used_ways}
        
        while remaining_ways:
            next_way_id, reverse = find_next_way(current_end, remaining_ways)
            if next_way_id is None:
                break
            
            next_coords = way_coords_dict[next_way_id]
            if reverse:
                next_coords = list(reversed(next_coords))
            
            if next_coords[0] == current_end:
                polygon.extend(next_coords[1:])
            else:
                polygon.extend(next_coords)
            
            used_ways.add(next_way_id)
            current_end = polygon[-1]
            remaining_ways = {k: v for k, v in way_coords_dict.items() if k not in used_ways}
        
        return polygon
    
    except Exception as e:
        print(f"Error processing relation {relation_id}: {e}")
        import traceback
        traceback.print_exc()
        return None

def generate_points_for_polygon(polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11):
    """Generate all grid points that are inside the polygon."""
    population_points = []
    total_points = (width + 1) * (height + 1)
    processed = 0
    last_progress_time = time.time()
    
    cell_size_lat = (max_lat - min_lat) / height
    cell_size_lon = (max_lon - min_lon) / width
    half_cell_lat = cell_size_lat / 2
    half_cell_lon = cell_size_lon / 2
    
    for x in range(width + 1):
        for y in range(height + 1):
            processed += 1
            
            # Progress tracking
            current_time = time.time()
            if processed % 100 == 0 or (current_time - last_progress_time) >= 5.0:
                percentage = processed * 100 // total_points
                print(f"  [Progress] Processing: {processed}/{total_points} points ({percentage}%)...")
                sys.stdout.flush()
                last_progress_time = current_time
            
            lat = min_lat + (y / height) * (max_lat - min_lat)
            lon = min_lon + (x / width) * (max_lon - min_lon)
            
            # Check if point is in polygon
            cell_center = (lon, lat)
            in_polygon = point_in_polygon(cell_center, polygon)
            
            # If center not in polygon, check corners, edge midpoints, and subgrid
            if not in_polygon:
                corners = [
                    (lon - half_cell_lon, lat - half_cell_lat),
                    (lon + half_cell_lon, lat - half_cell_lat),
                    (lon - half_cell_lon, lat + half_cell_lat),
                    (lon + half_cell_lon, lat + half_cell_lat),
                ]
                if any(point_in_polygon(corner, polygon) for corner in corners):
                    in_polygon = True
                else:
                    edge_midpoints = [
                        (lon, lat - half_cell_lat),
                        (lon, lat + half_cell_lat),
                        (lon - half_cell_lon, lat),
                        (lon + half_cell_lon, lat),
                    ]
                    if any(point_in_polygon(midpoint, polygon) for midpoint in edge_midpoints):
                        in_polygon = True
                    else:
                        # Check subgrid
                        sub_lat_step = cell_size_lat / subgrid_size
                        sub_lon_step = cell_size_lon / subgrid_size
                        found = False
                        for i in range(subgrid_size):
                            for j in range(subgrid_size):
                                sub_lat = lat - half_cell_lat + (i + 0.5) * sub_lat_step
                                sub_lon = lon - half_cell_lon + (j + 0.5) * sub_lon_step
                                if point_in_polygon((sub_lon, sub_lat), polygon):
                                    in_polygon = True
                                    found = True
                                    break
                            if found:
                                break
            
            if in_polygon:
                # Check if point is in water - filter it out
                is_water = is_water_location(lat, lon, osm_integration)
                if not is_water:
                    population_points.append((x, y))
    
    return population_points

if __name__ == "__main__":
    # Bronx County relation ID (Bronx County, New York)
    BRONX_RELATION_ID = 2552450
    
    nyc_bounds = get_nyc_bounds()
    min_lat = nyc_bounds['min_lat']
    max_lat = nyc_bounds['max_lat']
    min_lon = nyc_bounds['min_lon']
    max_lon = nyc_bounds['max_lon']
    
    # Read existing points from map_presets_nyc.py
    import re
    with open('map_presets_nyc.py', 'r') as f:
        content = f.read()
    match = re.search(r'NYC_INCLUDED_POINTS = \[(.*?)\]', content, re.DOTALL)
    if match:
        existing_points_str = match.group(1)
        # Safely evaluate the string to a list of tuples
        existing_points = eval(f"[{existing_points_str}]")
        all_population_points = set(existing_points)
        print(f"Current points: {len(existing_points)}")
    else:
        all_population_points = set()
        existing_points = []
        print("No existing NYC_INCLUDED_POINTS found, starting fresh.")
    
    # Remove existing Bronx points before recalculating
    print("\n=== Removing existing Bronx County points ===")
    bronx_bounds_approx = {
        'min_lat': 40.7857,
        'max_lat': 40.9176,
        'min_lon': -73.9339,
        'max_lon': -73.7484
    }
    
    points_to_remove = []
    for (x, y) in all_population_points:
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        if (bronx_bounds_approx['min_lat'] <= lat <= bronx_bounds_approx['max_lat'] and
            bronx_bounds_approx['min_lon'] <= lon <= bronx_bounds_approx['max_lon']):
            points_to_remove.append((x, y))
    
    for point in points_to_remove:
        all_population_points.discard(point)
    
    print(f"Removed {len(points_to_remove)} existing Bronx points")
    
    print("\n=== Generating Bronx County points ===")
    bronx_polygon = None
    try:
        bronx_polygon = get_polygon_from_relation(BRONX_RELATION_ID)
    except Exception as e:
        print(f"ERROR: Failed to get Bronx polygon: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    if bronx_polygon:
        print(f"Bronx polygon has {len(bronx_polygon)} points")
        print(f"Polygon bounds: lat {min(p[1] for p in bronx_polygon):.4f}-{max(p[1] for p in bronx_polygon):.4f}, lon {min(p[0] for p in bronx_polygon):.4f}-{max(p[0] for p in bronx_polygon):.4f}")
        print("Generating Bronx points with comprehensive detection and water filtering...")
        bronx_points = generate_points_for_polygon(bronx_polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11)
        all_population_points.update(bronx_points)
        print(f"✓ Found {len(bronx_points)} Bronx County points (water points filtered out)")
    else:
        print("WARNING: Could not retrieve Bronx polygon")
        bronx_points = []
    
    # Write updated points back to map_presets_nyc.py
    all_points_list = sorted(list(all_population_points))
    print(f"\n=== Final Results ===")
    print(f"Previous points: {len(existing_points)}")
    print(f"Removed old Bronx points: {len(points_to_remove)}")
    print(f"New Bronx points: {len(bronx_points)}")
    print(f"Total combined points: {len(all_points_list)}")
    
    print("\nUpdating map_presets_nyc.py with all points...")
    new_line = f'NYC_INCLUDED_POINTS = {all_points_list}'
    
    # Replace the NYC_INCLUDED_POINTS line
    pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
    new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
    
    with open('map_presets_nyc.py', 'w') as f:
        f.write(new_content)
    
    print(f"Successfully updated map_presets_nyc.py with {len(all_points_list)} total points")
    print("Generation complete!")

