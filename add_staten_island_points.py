"""Add Staten Island population points to NYC_INCLUDED_POINTS.
Queries OSM relation 9692329 (Staten Island) and adds points to existing list."""

from map_presets_nyc import get_nyc_bounds, NYC_INCLUDED_POINTS
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
    print(f"Querying OSM for Staten Island relation {relation_id}...")
    
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
            if member.get('role') == 'outer' and member.get('type') == 'way':
                outer_ways.append(member.get('ref'))
        
        if not outer_ways:
            print(f"No outer ways found for relation {relation_id}")
            return None
        
        way_coords_dict = {}
        for element in data.get('elements', []):
            if element.get('type') == 'way' and element.get('id') in outer_ways:
                geometry = element.get('geometry', [])
                coords = [(node.get('lon'), node.get('lat')) for node in geometry]
                if coords:
                    way_coords_dict[element.get('id')] = coords
        
        if way_coords_dict:
            print(f"Extracted {len(way_coords_dict)} ways from relation {relation_id}")
            polygon = connect_ways(way_coords_dict)
            if polygon and len(polygon) > 10:
                print(f"Connected polygon with {len(polygon)} points")
                return polygon
            else:
                all_coords = []
                for way_id in outer_ways:
                    if way_id in way_coords_dict:
                        all_coords.extend(way_coords_dict[way_id])
                if all_coords:
                    print(f"Using {len(all_coords)} coordinates from all ways")
                    return all_coords
        
        return None
    except Exception as e:
        print(f"Error querying relation {relation_id}: {e}")
        return None

def connect_ways(way_coords):
    """Connect ways into a continuous polygon by matching endpoints."""
    if not way_coords:
        return []
    
    used_ways = set()
    polygon = []
    
    def find_next_way(current_end, remaining_ways):
        best_match = None
        best_distance = float('inf')
        reverse = False
        
        for way_id, coords in remaining_ways.items():
            if way_id in used_ways:
                continue
            
            start = coords[0]
            end = coords[-1]
            
            dist_to_start = ((current_end[0] - start[0])**2 + (current_end[1] - start[1])**2)**0.5
            dist_to_end = ((current_end[0] - end[0])**2 + (current_end[1] - end[1])**2)**0.5
            
            if dist_to_start < best_distance and dist_to_start < 0.001:
                best_match = way_id
                best_distance = dist_to_start
                reverse = False
            elif dist_to_end < best_distance and dist_to_end < 0.001:
                best_match = way_id
                best_distance = dist_to_end
                reverse = True
        
        return best_match, reverse
    
    first_way_id = list(way_coords.keys())[0]
    first_coords = way_coords[first_way_id]
    polygon.extend(first_coords)
    used_ways.add(first_way_id)
    current_end = first_coords[-1]
    
    remaining_ways = {k: v for k, v in way_coords.items() if k not in used_ways}
    
    while remaining_ways:
        next_way_id, reverse = find_next_way(current_end, remaining_ways)
        if next_way_id is None:
            break
        
        next_coords = way_coords[next_way_id]
        if reverse:
            next_coords = list(reversed(next_coords))
        
        if next_coords[0] == current_end:
            polygon.extend(next_coords[1:])
        else:
            polygon.extend(next_coords)
        
        used_ways.add(next_way_id)
        current_end = polygon[-1]
        remaining_ways = {k: v for k, v in way_coords.items() if k not in used_ways}
    
    return polygon

def generate_points_for_polygon(polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11):
    """Generate all grid points that are inside or intersect with the polygon."""
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
            
            # Method 1: Check cell center
            cell_center = (lon, lat)
            if point_in_polygon(cell_center, polygon):
                population_points.append((x, y))
                continue
            
            # Method 2: Check all 4 corners of the cell
            corners = [
                (lon - half_cell_lon, lat - half_cell_lat),  # Top-left
                (lon + half_cell_lon, lat - half_cell_lat),  # Top-right
                (lon - half_cell_lon, lat + half_cell_lat),  # Bottom-left
                (lon + half_cell_lon, lat + half_cell_lat),  # Bottom-right
            ]
            
            if any(point_in_polygon(corner, polygon) for corner in corners):
                population_points.append((x, y))
                continue
            
            # Method 3: Check edge midpoints
            edge_midpoints = [
                (lon, lat - half_cell_lat),  # Top edge
                (lon, lat + half_cell_lat),  # Bottom edge
                (lon - half_cell_lon, lat),  # Left edge
                (lon + half_cell_lon, lat),  # Right edge
            ]
            
            if any(point_in_polygon(midpoint, polygon) for midpoint in edge_midpoints):
                population_points.append((x, y))
                continue
            
            # Method 4: Dense subgrid sampling
            sub_lat_step = cell_size_lat / subgrid_size
            sub_lon_step = cell_size_lon / subgrid_size
            
            found = False
            for i in range(subgrid_size):
                for j in range(subgrid_size):
                    sub_lat = lat - half_cell_lat + (i + 0.5) * sub_lat_step
                    sub_lon = lon - half_cell_lon + (j + 0.5) * sub_lon_step
                    if point_in_polygon((sub_lon, sub_lat), polygon):
                        population_points.append((x, y))
                        found = True
                        break
                if found:
                    break
    
    print(f"  [Progress] Complete: {processed}/{total_points} points processed (100%)")
    return population_points

nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

print("\n=== Generating Staten Island points ===")
print("Querying Staten Island relation (ID: 9692329)...")
staten_polygon = None

# Get Staten Island polygon
staten_polygon = get_polygon_from_relation(9692329)

if staten_polygon:
    print(f"Staten Island polygon has {len(staten_polygon)} points")
    print("Generating Staten Island points with comprehensive detection...")
    staten_points = generate_points_for_polygon(staten_polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11)
    print(f"✓ Found {len(staten_points)} Staten Island points")
    
    # Get current Manhattan points
    current_manhattan_points = list(NYC_INCLUDED_POINTS)
    print(f"Current Manhattan points: {len(current_manhattan_points)}")
    
    # Combine Manhattan and Staten Island points (remove duplicates)
    all_points = list(set(current_manhattan_points + staten_points))
    all_points.sort()  # Sort for consistent output
    
    print(f"\n=== Final Results ===")
    print(f"Manhattan Island points: {len(current_manhattan_points)}")
    print(f"Staten Island points: {len(staten_points)}")
    print(f"Total combined points: {len(all_points)}")
    
    # Update map_presets_nyc.py
    print("\nUpdating map_presets_nyc.py with Manhattan + Staten Island points...")
    with open('map_presets_nyc.py', 'r') as f:
        content = f.read()
    
    # Update NYC_INCLUDED_POINTS
    pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
    new_line = f'NYC_INCLUDED_POINTS = {all_points}'
    new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
    
    # Update NYC_POPULATION_POINTS to match
    pattern2 = r'NYC_POPULATION_POINTS = .*?NYC_INCLUDED_POINTS'
    new_content = re.sub(pattern2, 'NYC_POPULATION_POINTS = NYC_INCLUDED_POINTS', new_content, flags=re.DOTALL)
    
    with open('map_presets_nyc.py', 'w') as f:
        f.write(new_content)
    
    print(f"Successfully updated map_presets_nyc.py with {len(all_points)} total points (Manhattan + Staten Island)")
    print("Generation complete!")
else:
    print("ERROR: Could not retrieve Staten Island polygon")
    sys.exit(1)

