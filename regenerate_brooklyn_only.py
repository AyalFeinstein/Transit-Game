"""Regenerate ONLY Brooklyn points and merge with existing points, removing duplicates."""

from map_presets_nyc import get_nyc_bounds, NYC_INCLUDED_POINTS
from osm_integration import OSMIntegration
import time
import math
import re
import sys

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
    """Get polygon geometry from an OSM relation by ID."""
    print(f"Querying OSM for relation {relation_id}...")
    query = f"""
    [out:json][timeout:60];
    (
      relation(id:{relation_id});
    );
    (._;>;);
    out geom;
    """
    
    max_retries = 7
    wait_times = [10, 15, 20, 30, 40, 60, 80]
    
    for attempt in range(max_retries):
        try:
            import requests
            response = requests.post(osm_integration.base_url, data=query, timeout=90)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"  Error querying relation {relation_id}: {e}")
                print(f"  Attempt {attempt + 1} failed. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"  Failed after {max_retries} attempts")
                raise
    
    try:
        relation_element = None
        for element in data.get('elements', []):
            if element.get('type') == 'relation':
                relation_element = element
                break
        
        if not relation_element:
            print(f"Relation {relation_id} not found")
            return None
        
        ways = []
        way_dict = {}
        for element in data.get('elements', []):
            if element.get('type') == 'way':
                way_id = element.get('id')
                nodes = element.get('geometry', [])
                if nodes:
                    way_dict[way_id] = [(node['lon'], node['lat']) for node in nodes]
                    ways.append(way_id)
        
        if not ways:
            print(f"No ways found for relation {relation_id}")
            return None
        
        # Connect ways into polygon
        polygon = []
        used_ways = set()
        current_way_id = ways[0]
        
        while current_way_id and current_way_id not in used_ways:
            used_ways.add(current_way_id)
            way_points = way_dict[current_way_id]
            if not polygon:
                polygon.extend(way_points)
            else:
                # Connect to existing polygon
                last_point = polygon[-1]
                first_point = way_points[0]
                last_point_way = way_points[-1]
                
                if abs(last_point[0] - first_point[0]) < 0.0001 and abs(last_point[1] - first_point[1]) < 0.0001:
                    polygon.extend(way_points[1:])
                elif abs(last_point[0] - last_point_way[0]) < 0.0001 and abs(last_point[1] - last_point_way[1]) < 0.0001:
                    polygon.extend(reversed(way_points[:-1]))
                else:
                    polygon.extend(way_points)
            
            # Find next way
            current_way_id = None
            last_poly_point = polygon[-1]
            for way_id in ways:
                if way_id not in used_ways:
                    way_pts = way_dict[way_id]
                    if way_pts:
                        first_pt = way_pts[0]
                        last_pt = way_pts[-1]
                        if (abs(last_poly_point[0] - first_pt[0]) < 0.0001 and abs(last_poly_point[1] - first_pt[1]) < 0.0001) or \
                           (abs(last_poly_point[0] - last_pt[0]) < 0.0001 and abs(last_poly_point[1] - last_pt[1]) < 0.0001):
                            current_way_id = way_id
                            break
        
        print(f"Connected polygon with {len(polygon)} points")
        return polygon
    except Exception as e:
        print(f"Error processing relation {relation_id}: {e}")
        return None

def get_polygon_from_relation_by_name(name, relation_id=None):
    """Get polygon geometry from an OSM relation by name or ID."""
    if relation_id:
        print(f"Querying OSM for relation {relation_id} (expected name: '{name}')...")
        query = f"""
        [out:json][timeout:60];
        (
          relation(id:{relation_id});
        );
        (._;>;);
        out geom;
        """
    else:
        print(f"Querying OSM for relation named '{name}'...")
        query = f"""
        [out:json][timeout:60];
        (
          relation["name"="{name}"];
        );
        (._;>;);
        out geom;
        """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            import requests
            response = requests.post(osm_integration.base_url, data=query, timeout=90)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 10
                print(f"  Attempt {attempt + 1} failed: {e}. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                raise
    else:
        return None
    
    try:
        relation_element = None
        for element in data.get('elements', []):
            if element.get('type') == 'relation':
                if relation_id and element.get('id') == relation_id:
                    relation_element = element
                    break
                elif not relation_id and element.get('tags', {}).get('name') == name:
                    relation_element = element
                    break
        
        if not relation_element:
            print(f"Relation '{name}' not found")
            return None
        
        ways = []
        way_dict = {}
        for element in data.get('elements', []):
            if element.get('type') == 'way':
                way_id = element.get('id')
                nodes = element.get('geometry', [])
                if nodes:
                    way_dict[way_id] = [(node['lon'], node['lat']) for node in nodes]
                    ways.append(way_id)
        
        if not ways:
            print(f"No ways found for relation '{name}'")
            return None
        
        # Connect ways into polygon
        polygon = []
        used_ways = set()
        current_way_id = ways[0]
        
        while current_way_id and current_way_id not in used_ways:
            used_ways.add(current_way_id)
            way_points = way_dict[current_way_id]
            if not polygon:
                polygon.extend(way_points)
            else:
                last_point = polygon[-1]
                first_point = way_points[0]
                last_point_way = way_points[-1]
                
                if abs(last_point[0] - first_point[0]) < 0.0001 and abs(last_point[1] - first_point[1]) < 0.0001:
                    polygon.extend(way_points[1:])
                elif abs(last_point[0] - last_point_way[0]) < 0.0001 and abs(last_point[1] - last_point_way[1]) < 0.0001:
                    polygon.extend(reversed(way_points[:-1]))
                else:
                    polygon.extend(way_points)
            
            current_way_id = None
            last_poly_point = polygon[-1]
            for way_id in ways:
                if way_id not in used_ways:
                    way_pts = way_dict[way_id]
                    if way_pts:
                        first_pt = way_pts[0]
                        last_pt = way_pts[-1]
                        if (abs(last_poly_point[0] - first_pt[0]) < 0.0001 and abs(last_poly_point[1] - first_pt[1]) < 0.0001) or \
                           (abs(last_poly_point[0] - last_pt[0]) < 0.0001 and abs(last_poly_point[1] - last_pt[1]) < 0.0001):
                            current_way_id = way_id
                            break
        
        print(f"Connected polygon with {len(polygon)} points")
        return polygon
    except Exception as e:
        print(f"Error processing relation '{name}': {e}")
        return None

def generate_points_for_polygon(polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11):
    """Generate grid points within or intersecting a polygon."""
    population_points = []
    cell_size_lat = (max_lat - min_lat) / height
    cell_size_lon = (max_lon - min_lon) / width
    half_cell_lat = cell_size_lat / 2
    half_cell_lon = cell_size_lon / 2
    
    total_points = width * height
    processed = 0
    
    for y in range(height):
        for x in range(width):
            processed += 1
            lat = min_lat + (y / height) * (max_lat - min_lat)
            lon = min_lon + (x / width) * (max_lon - min_lon)
            
            # Check center
            point = (lon, lat)
            if point_in_polygon(point, polygon):
                population_points.append((x, y))
                continue
            
            # Check corners
            corners = [
                (lon - half_cell_lon, lat - half_cell_lat),
                (lon + half_cell_lon, lat - half_cell_lat),
                (lon - half_cell_lon, lat + half_cell_lat),
                (lon + half_cell_lon, lat + half_cell_lat),
            ]
            if any(point_in_polygon(corner, polygon) for corner in corners):
                population_points.append((x, y))
                continue
            
            # Check edge midpoints
            edge_midpoints = [
                (lon, lat - half_cell_lat),
                (lon, lat + half_cell_lat),
                (lon - half_cell_lon, lat),
                (lon + half_cell_lon, lat),
            ]
            if any(point_in_polygon(midpoint, polygon) for midpoint in edge_midpoints):
                population_points.append((x, y))
                continue
            
            # Check subgrid
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
            
            if processed % 500 == 0:
                time.sleep(0.005)
    
    return population_points

# Get bounds
nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

# Load existing points
print(f"\n=== Loading existing points ===")
existing_points = set(NYC_INCLUDED_POINTS)
print(f"Loaded {len(existing_points)} existing points")

# Query Long Island (needed for Brooklyn filtering)
print("\n=== Querying Long Island polygon (needed for Brooklyn filtering) ===")
long_island_polygon = None
for attempt in range(3):
    try:
        print(f"Attempt {attempt + 1}: Querying Long Island relation 3955977...")
        long_island_polygon = get_polygon_from_relation(3955977)
        if long_island_polygon:
            break
    except Exception as e:
        print(f"  Error: {e}")
    if attempt < 2:
        wait_time = (attempt + 1) * 20
        print(f"  Retrying in {wait_time} seconds...")
        time.sleep(wait_time)

if not long_island_polygon:
    print("ERROR: Long Island polygon required for Brooklyn filtering")
    sys.exit(1)

# Query Kings County (Brooklyn)
print("\n=== Querying Kings County (Brooklyn) ===")
kings_county_polygon = None
kings_relation_ids = [175905]
for kings_id in kings_relation_ids:
    for attempt in range(3):
        try:
            print(f"Trying Kings County relation ID {kings_id} (attempt {attempt + 1})...")
            kings_county_polygon = get_polygon_from_relation(kings_id)
            if kings_county_polygon:
                print(f"Found Kings County using relation ID {kings_id}")
                break
        except Exception as e:
            print(f"  Error: {e}")
        if attempt < 2:
            time.sleep(10)
    if kings_county_polygon:
        break
    time.sleep(5)

if not kings_county_polygon:
    print("Trying by name...")
    kings_county_polygon = get_polygon_from_relation_by_name("Kings County")
if not kings_county_polygon:
    print("Trying alternative name...")
    kings_county_polygon = get_polygon_from_relation_by_name("Brooklyn")

if not kings_county_polygon:
    print("ERROR: Could not retrieve Kings County polygon")
    sys.exit(1)

# Generate Brooklyn points
print("\n=== Generating Brooklyn points ===")
print("Generating all points in Kings County with comprehensive detection (center + corners + edge midpoints + subgrid=11)...")
kings_points = generate_points_for_polygon(kings_county_polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11)
print(f"Found {len(kings_points)} total points in Kings County")

# Filter to only include points also in Long Island
print("Filtering to only include points also in Long Island (with comprehensive checking including subgrid)...")
kings_long_island_points = []
cell_size_lat = (max_lat - min_lat) / height
cell_size_lon = (max_lon - min_lon) / width
half_cell_lat = cell_size_lat / 2
half_cell_lon = cell_size_lon / 2
filtered_count = 0

for point in kings_points:
    x, y = point
    lat = min_lat + (y / height) * (max_lat - min_lat)
    lon = min_lon + (x / width) * (max_lon - min_lon)
    
    # Check center
    point_coord = (lon, lat)
    if point_in_polygon(point_coord, long_island_polygon):
        kings_long_island_points.append(point)
        filtered_count += 1
        continue
    
    # Check corners
    corners = [
        (lon - half_cell_lon, lat - half_cell_lat),
        (lon + half_cell_lon, lat - half_cell_lat),
        (lon - half_cell_lon, lat + half_cell_lat),
        (lon + half_cell_lon, lat + half_cell_lat),
    ]
    if any(point_in_polygon(corner, long_island_polygon) for corner in corners):
        kings_long_island_points.append(point)
        filtered_count += 1
        continue
    
    # Check edge midpoints
    edge_midpoints = [
        (lon, lat - half_cell_lat),
        (lon, lat + half_cell_lat),
        (lon - half_cell_lon, lat),
        (lon + half_cell_lon, lat),
    ]
    if any(point_in_polygon(midpoint, long_island_polygon) for midpoint in edge_midpoints):
        kings_long_island_points.append(point)
        filtered_count += 1
        continue
    
    # Check subgrid (CRITICAL: This was missing and caused the issue!)
    sub_lat_step = cell_size_lat / 11
    sub_lon_step = cell_size_lon / 11
    found = False
    for i in range(11):
        for j in range(11):
            sub_lat = lat - half_cell_lat + (i + 0.5) * sub_lat_step
            sub_lon = lon - half_cell_lon + (j + 0.5) * sub_lon_step
            if point_in_polygon((sub_lon, sub_lat), long_island_polygon):
                kings_long_island_points.append(point)
                filtered_count += 1
                found = True
                break
        if found:
            break
    
    if filtered_count % 100 == 0:
        print(f"  Filtered {filtered_count}/{len(kings_points)} points...")

print(f"✓ Found {len(kings_long_island_points)} new Brooklyn points")

# Merge with existing points and remove duplicates
print("\n=== Merging with existing points ===")
new_brooklyn_points = set(kings_long_island_points)
all_points = existing_points | new_brooklyn_points
print(f"Existing points: {len(existing_points)}")
print(f"New Brooklyn points: {len(new_brooklyn_points)}")
print(f"Total unique points after merge: {len(all_points)}")
print(f"Duplicates removed: {len(existing_points) + len(new_brooklyn_points) - len(all_points)}")

# Update the file
print("\n=== Updating map_presets_nyc.py ===")
try:
    with open('map_presets_nyc.py', 'r') as f:
        content = f.read()
    pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
    new_line = f'NYC_INCLUDED_POINTS = {sorted(list(all_points))}'
    new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
    with open('map_presets_nyc.py', 'w') as f:
        f.write(new_content)
    print(f"✓ Successfully updated map_presets_nyc.py with {len(all_points)} total points")
except Exception as e:
    print(f"ERROR: Could not update file: {e}")
    sys.exit(1)

print("\n=== Complete ===")
print(f"Final point count: {len(all_points)}")

