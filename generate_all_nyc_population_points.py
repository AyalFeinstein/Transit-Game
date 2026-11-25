"""Generate population points for all NYC areas using OSM relation polygons.
Includes:
- Manhattan Island (improved detection)
- Brooklyn/Queens area: Long Island boundaries (west, south, north) minus Nassau County (east boundary)
- Roosevelt Island (2389564)"""

from map_presets_nyc import get_nyc_bounds
from osm_integration import OSMIntegration
import time
import sys
import math

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
        
        members = relation_element.get('members', [])
        outer_ways = []
        for member in members:
            if member.get('role') == 'outer' and member.get('type') == 'way':
                outer_ways.append(member.get('ref'))
        
        if not outer_ways:
            print(f"No outer ways found for relation '{name}'")
            return None
        
        way_coords_dict = {}
        for element in data.get('elements', []):
            if element.get('type') == 'way' and element.get('id') in outer_ways:
                geometry = element.get('geometry', [])
                coords = [(node.get('lon'), node.get('lat')) for node in geometry]
                if coords:
                    way_coords_dict[element.get('id')] = coords
        
        if way_coords_dict:
            print(f"Extracted {len(way_coords_dict)} ways from relation '{name}'")
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
        print(f"Error querying relation '{name}': {e}")
        return None
    except:
        return None

def get_polygon_from_relation(relation_id):
    """Get polygon geometry from an OSM relation ID."""
    print(f"Querying OSM for relation {relation_id}...")
    
    query = f"""
    [out:json][timeout:30];
    (
      relation(id:{relation_id});
    );
    (._;>;);
    out geom;
    """
    
    try:
        import requests
        response = requests.post(osm_integration.base_url, data=query, timeout=30)
        response.raise_for_status()
        data = response.json()
        
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
    except:
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

def get_manhattan_polygon():
    """Get Manhattan Island polygon from OSM relation."""
    query = """
    [out:json][timeout:30];
    (
      relation["name"="Manhattan Island"];
    );
    (._;>;);
    out geom;
    """
    
    try:
        import requests
        response = requests.post(osm_integration.base_url, data=query, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        relation_element = None
        for element in data.get('elements', []):
            if element.get('type') == 'relation':
                relation_element = element
                break
        
        if relation_element:
            members = relation_element.get('members', [])
            outer_ways = []
            for member in members:
                if member.get('role') == 'outer' and member.get('type') == 'way':
                    outer_ways.append(member.get('ref'))
            
            if outer_ways:
                way_coords_dict = {}
                for element in data.get('elements', []):
                    if element.get('type') == 'way' and element.get('id') in outer_ways:
                        geometry = element.get('geometry', [])
                        coords = [(node.get('lon'), node.get('lat')) for node in geometry]
                        if coords:
                            way_coords_dict[element.get('id')] = coords
                
                if way_coords_dict:
                    polygon = connect_ways(way_coords_dict)
                    if polygon and len(polygon) > 10:
                        return polygon
                    else:
                        all_coords = []
                        for way_id in outer_ways:
                            if way_id in way_coords_dict:
                                all_coords.extend(way_coords_dict[way_id])
                        if all_coords:
                            return all_coords
        
        return None
    except Exception as e:
        print(f"Error querying Manhattan: {e}")
        return None

def generate_points_for_polygon(polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11):
    """Generate all grid points that are inside or intersect with the polygon.
    Uses multiple detection methods to ensure complete coverage:
    1. Cell center check
    2. Cell corner checks (4 corners)
    3. Cell edge midpoint checks (4 midpoints)
    4. Dense subgrid sampling
    subgrid_size: Higher values (11-13) provide better coverage for complex boundaries."""
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
            
            # Progress tracking with time-based updates
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
            
            # Method 3: Check edge midpoints for better boundary detection
            edge_midpoints = [
                (lon, lat - half_cell_lat),  # Top edge
                (lon, lat + half_cell_lat),  # Bottom edge
                (lon - half_cell_lon, lat),  # Left edge
                (lon + half_cell_lon, lat),  # Right edge
            ]
            
            if any(point_in_polygon(midpoint, polygon) for midpoint in edge_midpoints):
                population_points.append((x, y))
                continue
            
            # Method 4: Dense subgrid sampling for edge cases
            sub_lat_step = cell_size_lat / subgrid_size
            sub_lon_step = cell_size_lon / subgrid_size
            
            # Check subgrid points efficiently (early exit on first match)
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
            
            # Small delay to prevent overwhelming the system
            # Reduced sleep frequency to speed up processing
            if processed % 500 == 0:
                time.sleep(0.005)
    
    print(f"  [Progress] Complete: {processed}/{total_points} points processed (100%)")
    return population_points

nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

all_population_points = set()
manhattan_points = []
intersection_points = []
roosevelt_points = []
staten_points = []

print("\n=== Generating Manhattan Island points ===")
print("Querying specifically for 'Manhattan Island' relation (not just 'Manhattan')...")
manhattan_polygon = None
# First try by name "Manhattan Island" specifically
print("Attempt 1: Querying by name 'Manhattan Island'...")
manhattan_polygon = get_polygon_from_relation_by_name("Manhattan Island")
if not manhattan_polygon:
    print("Attempt 2: Trying Manhattan Island relation by ID (3954665)...")
    for attempt in range(3):
        manhattan_polygon = get_polygon_from_relation(3954665)
        if manhattan_polygon:
            print(f"Successfully retrieved Manhattan Island polygon via relation ID")
            break
        if attempt < 2:
            wait_time = (attempt + 1) * 15
            print(f"  Attempt {attempt + 1} failed. Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

if manhattan_polygon:
    print(f"Manhattan Island polygon has {len(manhattan_polygon)} points")
    print("Generating Manhattan points with comprehensive detection (center + corners + subgrid=11 for maximum coverage)...")
    print("Progress will be displayed every 100 points processed...")
    manhattan_points = generate_points_for_polygon(manhattan_polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11)
    all_population_points.update(manhattan_points)
    print(f"✓ Found {len(manhattan_points)} Manhattan Island points (all land areas included, even with 0 population)")
    
    # Save intermediate progress
    print("\n[Saving checkpoint: Manhattan complete]")
    import re
    try:
        with open('map_presets_nyc.py', 'r') as f:
            content = f.read()
        pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
        new_line = f'NYC_INCLUDED_POINTS = {sorted(list(all_population_points))}'
        new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
        with open('map_presets_nyc.py', 'w') as f:
            f.write(new_content)
        print(f"✓ Saved {len(all_population_points)} points to map_presets_nyc.py")
    except Exception as e:
        print(f"WARNING: Could not save checkpoint: {e}")
else:
    print("WARNING: Could not retrieve Manhattan Island polygon after retries")
    manhattan_points = []

print("\n=== Generating Long Island polygon ===")
long_island_polygon = None
max_retries = 5
for attempt in range(max_retries):
    try:
        long_island_polygon = get_polygon_from_relation(3955977)
        if long_island_polygon:
            break
    except Exception as e:
        print(f"  Attempt {attempt + 1} error: {e}")
    
    if attempt < max_retries - 1:
        wait_time = (attempt + 1) * 20
        print(f"  Attempt {attempt + 1} failed. Retrying in {wait_time} seconds...")
        time.sleep(wait_time)

if not long_island_polygon:
    print("ERROR: Could not retrieve Long Island polygon after {max_retries} retries")
    print("This is required for Brooklyn/Queens calculation. Please try again later.")
    sys.exit(1)

print("\n=== Generating Nassau County polygon (to exclude) ===")
print("Trying to find Nassau County relation...")
nassau_polygon = None
nassau_relation_ids = [1210720, 2552442]  # Correct Nassau County relation IDs
for nassau_id in nassau_relation_ids:
    for attempt in range(3):
        try:
            print(f"Trying Nassau County relation ID {nassau_id} (attempt {attempt + 1})...")
            nassau_polygon = get_polygon_from_relation(nassau_id)
            if nassau_polygon:
                print(f"Found Nassau County using relation ID {nassau_id}")
                break
        except Exception as e:
            print(f"  Error: {e}")
        if attempt < 2:
            time.sleep(10)
    if nassau_polygon:
        break
    time.sleep(5)

if not nassau_polygon:
    print("Trying by name...")
    nassau_polygon = get_polygon_from_relation_by_name("Nassau County")
if not nassau_polygon:
    print("ERROR: Could not retrieve Nassau County polygon - required to exclude Nassau points")
    sys.exit(1)

print("\n=== Generating Suffolk County polygon (to exclude) ===")
print("Trying to find Suffolk County relation...")
suffolk_polygon = None
suffolk_relation_ids = [1210721, 2552443]  # Common Suffolk County relation IDs
for suffolk_id in suffolk_relation_ids:
    for attempt in range(3):
        try:
            print(f"Trying Suffolk County relation ID {suffolk_id} (attempt {attempt + 1})...")
            suffolk_polygon = get_polygon_from_relation(suffolk_id)
            if suffolk_polygon:
                print(f"Found Suffolk County using relation ID {suffolk_id}")
                break
        except Exception as e:
            print(f"  Error: {e}")
        if attempt < 2:
            time.sleep(10)
    if suffolk_polygon:
        break
    time.sleep(5)

if not suffolk_polygon:
    print("Trying by name...")
    suffolk_polygon = get_polygon_from_relation_by_name("Suffolk County")
if not suffolk_polygon:
    print("WARNING: Could not retrieve Suffolk County polygon")

if long_island_polygon:
    print("\n=== Calculating Brooklyn/Queens area (Long Island minus Nassau and Suffolk Counties) ===")
    if nassau_polygon and suffolk_polygon:
        print("Finding points in Long Island but NOT in Nassau County and NOT in Suffolk County...")
        print("This gives us Brooklyn and Queens only (NYC boroughs on Long Island)")
    elif nassau_polygon:
        print("Finding points in Long Island but NOT in Nassau County...")
        print("WARNING: Suffolk County polygon not available, may include some Suffolk points")
    elif suffolk_polygon:
        print("Finding points in Long Island but NOT in Suffolk County...")
        print("WARNING: Nassau County polygon not available, may include some Nassau points")
    else:
        print("WARNING: Neither Nassau nor Suffolk County polygons available")
        print("Using all Long Island points (may include Nassau and Suffolk)")
    
    print("Using comprehensive detection (center + corners + edge midpoints + subgrid=11) for complete coverage...")
    total_points = (width + 1) * (height + 1)
    processed = 0
    last_progress_time = time.time()
    
    cell_size_lat = (max_lat - min_lat) / height
    cell_size_lon = (max_lon - min_lon) / width
    half_cell_lat = cell_size_lat / 2
    half_cell_lon = cell_size_lon / 2
    subgrid_size = 11
    
    for x in range(width + 1):
        for y in range(height + 1):
            processed += 1
            
            # Progress tracking with time-based updates
            current_time = time.time()
            if processed % 100 == 0 or (current_time - last_progress_time) >= 5.0:
                percentage = processed * 100 // total_points
                print(f"  [Progress] Processing: {processed}/{total_points} points ({percentage}%)...")
                sys.stdout.flush()
                last_progress_time = current_time
            
            lat = min_lat + (y / height) * (max_lat - min_lat)
            lon = min_lon + (x / width) * (max_lon - min_lon)
            
            # Check center
            point = (lon, lat)
            in_long_island = point_in_polygon(point, long_island_polygon)
            
            # If center not in Long Island, check corners, edge midpoints, and subgrid
            if not in_long_island:
                corners = [
                    (lon - half_cell_lon, lat - half_cell_lat),
                    (lon + half_cell_lon, lat - half_cell_lat),
                    (lon - half_cell_lon, lat + half_cell_lat),
                    (lon + half_cell_lon, lat + half_cell_lat),
                ]
                if any(point_in_polygon(corner, long_island_polygon) for corner in corners):
                    in_long_island = True
                else:
                    # Check edge midpoints
                    edge_midpoints = [
                        (lon, lat - half_cell_lat),
                        (lon, lat + half_cell_lat),
                        (lon - half_cell_lon, lat),
                        (lon + half_cell_lon, lat),
                    ]
                    if any(point_in_polygon(midpoint, long_island_polygon) for midpoint in edge_midpoints):
                        in_long_island = True
                    else:
                        # Check subgrid
                        sub_lat_step = cell_size_lat / subgrid_size
                        sub_lon_step = cell_size_lon / subgrid_size
                        found = False
                        for i in range(subgrid_size):
                            for j in range(subgrid_size):
                                sub_lat = lat - half_cell_lat + (i + 0.5) * sub_lat_step
                                sub_lon = lon - half_cell_lon + (j + 0.5) * sub_lon_step
                                if point_in_polygon((sub_lon, sub_lat), long_island_polygon):
                                    in_long_island = True
                                    found = True
                                    break
                            if found:
                                break
            
            if in_long_island:
                include_point = True
                
                # Check if point is in Nassau County (exclude) - use comprehensive checking
                if nassau_polygon:
                    in_nassau = point_in_polygon(point, nassau_polygon)
                    if not in_nassau:
                        # Check corners
                        corners = [
                            (lon - half_cell_lon, lat - half_cell_lat),
                            (lon + half_cell_lon, lat - half_cell_lat),
                            (lon - half_cell_lon, lat + half_cell_lat),
                            (lon + half_cell_lon, lat + half_cell_lat),
                        ]
                        in_nassau = any(point_in_polygon(corner, nassau_polygon) for corner in corners)
                        # Also check edge midpoints
                        if not in_nassau:
                            edge_midpoints = [
                                (lon, lat - half_cell_lat),
                                (lon, lat + half_cell_lat),
                                (lon - half_cell_lon, lat),
                                (lon + half_cell_lon, lat),
                            ]
                            in_nassau = any(point_in_polygon(midpoint, nassau_polygon) for midpoint in edge_midpoints)
                    if in_nassau:
                        include_point = False
                
                # Check if point is in Suffolk County (exclude) - use comprehensive checking
                if include_point and suffolk_polygon:
                    in_suffolk = point_in_polygon(point, suffolk_polygon)
                    if not in_suffolk:
                        # Check corners
                        corners = [
                            (lon - half_cell_lon, lat - half_cell_lat),
                            (lon + half_cell_lon, lat - half_cell_lat),
                            (lon - half_cell_lon, lat + half_cell_lat),
                            (lon + half_cell_lon, lat + half_cell_lat),
                        ]
                        in_suffolk = any(point_in_polygon(corner, suffolk_polygon) for corner in corners)
                        # Also check edge midpoints
                        if not in_suffolk:
                            edge_midpoints = [
                                (lon, lat - half_cell_lat),
                                (lon, lat + half_cell_lat),
                                (lon - half_cell_lon, lat),
                                (lon + half_cell_lon, lat),
                            ]
                            in_suffolk = any(point_in_polygon(midpoint, suffolk_polygon) for midpoint in edge_midpoints)
                    if in_suffolk:
                        include_point = False
                
                if include_point:
                    intersection_points.append((x, y))
            
            # Reduced sleep frequency to speed up processing
            if processed % 500 == 0:
                time.sleep(0.005)
    
    print(f"  [Progress] Complete: {processed}/{total_points} points processed (100%)")
    all_population_points.update(intersection_points)
    print(f"✓ Found {len(intersection_points)} points in Brooklyn/Queens area (Long Island minus Nassau and Suffolk)")
    
    # Save intermediate progress
    print("\n[Saving checkpoint: Brooklyn/Queens complete]")
    import re
    try:
        with open('map_presets_nyc.py', 'r') as f:
            content = f.read()
        pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
        new_line = f'NYC_INCLUDED_POINTS = {sorted(list(all_population_points))}'
        new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
        with open('map_presets_nyc.py', 'w') as f:
            f.write(new_content)
        print(f"✓ Saved {len(all_population_points)} points to map_presets_nyc.py")
    except Exception as e:
        print(f"WARNING: Could not save checkpoint: {e}")
else:
    print("ERROR: Long Island polygon required for Brooklyn/Queens area calculation")

print("\n=== Querying Kings County (Brooklyn) ===")
kings_county_polygon = None
kings_relation_ids = [175905]  # Common Kings County relation IDs
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

if kings_county_polygon and long_island_polygon:
    print("\n=== Adding points in both Kings County and Long Island ===")
    print("Using comprehensive detection algorithm to ensure complete Brooklyn coverage...")
    print("Generating all points in Kings County with comprehensive detection (center + corners + edge midpoints + subgrid=11)...")
    kings_points = generate_points_for_polygon(kings_county_polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11)
    print(f"Found {len(kings_points)} total points in Kings County")
    
    print("Filtering to only include points also in Long Island (with comprehensive checking)...")
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
    
    all_population_points.update(kings_long_island_points)
    print(f"✓ Found {len(kings_long_island_points)} points in both Kings County and Long Island (all Brooklyn land areas included)")
    
    # Save intermediate progress
    print("\n[Saving checkpoint: Kings County complete]")
    import re
    try:
        with open('map_presets_nyc.py', 'r') as f:
            content = f.read()
        pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
        new_line = f'NYC_INCLUDED_POINTS = {sorted(list(all_population_points))}'
        new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)
        with open('map_presets_nyc.py', 'w') as f:
            f.write(new_content)
        print(f"✓ Saved {len(all_population_points)} points to map_presets_nyc.py")
    except Exception as e:
        print(f"WARNING: Could not save checkpoint: {e}")
elif not kings_county_polygon:
    print("WARNING: Could not retrieve Kings County polygon")

print("\n=== Generating Staten Island points ===")
staten_island_polygon = None
for attempt in range(3):
    try:
        print(f"Attempt {attempt + 1}: Querying Staten Island relation 9692329...")
        staten_island_polygon = get_polygon_from_relation(9692329)
        if staten_island_polygon:
            break
    except Exception as e:
        print(f"  Error: {e}")
    if attempt < 2:
        wait_time = (attempt + 1) * 15
        print(f"  Retrying in {wait_time} seconds...")
        time.sleep(wait_time)

if not staten_island_polygon:
    print("Trying by name...")
    staten_island_polygon = get_polygon_from_relation_by_name("Staten Island")

if staten_island_polygon:
    print(f"Staten Island polygon has {len(staten_island_polygon)} points")
    print("Generating Staten Island points with comprehensive detection (center + corners + edge midpoints + subgrid=11)...")
    staten_points = generate_points_for_polygon(staten_island_polygon, min_lat, max_lat, min_lon, max_lon, width, height, subgrid_size=11)
    all_population_points.update(staten_points)
    print(f"✓ Found {len(staten_points)} Staten Island points (all land areas included)")
else:
    print("WARNING: Could not retrieve Staten Island polygon")
    staten_points = []

print("\n=== Generating Roosevelt Island points ===")
roosevelt_polygon = None
try:
    print("Trying Roosevelt Island by name first...")
    roosevelt_polygon = get_polygon_from_relation_by_name("Roosevelt Island")
except:
    pass

if not roosevelt_polygon:
    try:
        print("Trying Roosevelt Island relation 2389564...")
        roosevelt_polygon = get_polygon_from_relation(2389564)
    except:
        pass

if not roosevelt_polygon:
    print("Roosevelt Island not found via OSM. Adding manually based on approximate bounds...")
    print("Roosevelt Island is approximately: 40.75-40.77 lat, -73.95 to -73.94 lon")
    roosevelt_points = []
    for x in range(width + 1):
        for y in range(height + 1):
            lat = min_lat + (y / height) * (max_lat - min_lat)
            lon = min_lon + (x / width) * (max_lon - min_lon)
            if 40.75 <= lat <= 40.77 and -73.95 <= lon <= -73.94:
                roosevelt_points.append((x, y))
    if roosevelt_points:
        all_population_points.update(roosevelt_points)
        print(f"Found {len(roosevelt_points)} Roosevelt Island points (manual)")
    else:
        print("WARNING: Could not add Roosevelt Island points")
        roosevelt_points = []
else:
    print(f"Roosevelt Island polygon has {len(roosevelt_polygon)} points")
    roosevelt_points = generate_points_for_polygon(roosevelt_polygon, min_lat, max_lat, min_lon, max_lon, width, height)
    all_population_points.update(roosevelt_points)
    print(f"Found {len(roosevelt_points)} Roosevelt Island points")

print("\n=== Removing any points in Nassau County ===")
if nassau_polygon:
    points_to_remove = []
    for point in all_population_points:
        x, y = point
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        point_coord = (lon, lat)
        if point_in_polygon(point_coord, nassau_polygon):
            points_to_remove.append(point)
    
    for point in points_to_remove:
        all_population_points.discard(point)
    
    print(f"Removed {len(points_to_remove)} points that were in Nassau County")
else:
    print("WARNING: Nassau County polygon not available, cannot remove Nassau points")

print(f"\n=== Final Results ===")
print(f"Total unique population points: {len(all_population_points)}")
print(f"Manhattan points: {len(manhattan_points)}")
print(f"Intersection area points: {len(intersection_points)}")
print(f"Roosevelt Island points: {len(roosevelt_points)}")
if 'kings_long_island_points' in locals():
    print(f"Kings County + Long Island points: {len(kings_long_island_points)}")
print(f"Staten Island points: {len(staten_points)}")

population_points_list = sorted(list(all_population_points))

print(f"\n# Pre-computed population points (x, y) for {width}x{height} grid")
print(f"# Generated using OSM relation polygons")
print(f"# DO NOT REGENERATE - This list is saved and should be reused")
print(f"# Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"NYC_POPULATION_POINTS = {population_points_list}")
print(f"# Total population points: {len(population_points_list)}")

import re
print("\nUpdating map_presets_nyc.py with generated points...")
with open('map_presets_nyc.py', 'r') as f:
    content = f.read()

pattern = r'NYC_INCLUDED_POINTS = \[.*?\]'
new_line = f'NYC_INCLUDED_POINTS = {population_points_list}'
new_content = re.sub(pattern, new_line, content, flags=re.DOTALL)

with open('map_presets_nyc.py', 'w') as f:
    f.write(new_content)

print(f"Successfully updated map_presets_nyc.py with {len(population_points_list)} population points")
print("Generation complete!")

