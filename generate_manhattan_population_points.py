"""Generate Manhattan population points using OSM relation polygon data.
Uses the Manhattan Island relation (ways listed) to determine valid land areas.
Population is weighted higher in Lower Manhattan for historical accuracy."""

from map_presets_nyc import get_nyc_bounds, get_nyc_population_at_location
from osm_integration import OSMIntegration
import time
import sys
import math

width, height = 50, 50

print("Initializing OSM integration for Manhattan Island polygon detection...")
osm_integration = OSMIntegration(timeout=15.0)
print("OSM integration ready.")

MANHATTAN_ISLAND_RELATION_ID = None
MANHATTAN_WAYS = [
    179055616, 179055615, 1188974539, 179060559, 179060405, 1187969036,
    890490703, 1132210807, 1132210809, 894440579, 890490701, 702879085,
    179058316, 1234203919, 891134448, 1236746348, 463547105, 891757872,
    463545995, 169362480, 179053998, 135719689, 105562812, 194213820,
    105562810, 135719686, 577241198, 179057063
]

def point_in_polygon(point, polygon):
    """Check if a point is inside a polygon using ray casting algorithm.
    Handles large polygons efficiently."""
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

def get_manhattan_polygon():
    """Get Manhattan Island polygon from OSM relation."""
    print("Querying OSM for Manhattan Island relation with full geometry...")
    
    query = """
    [out:json][timeout:25];
    (
      relation["name"="Manhattan Island"];
    );
    (._;>;);
    out geom;
    """
    
    try:
        import requests
        response = requests.post(osm_integration.base_url, data=query, timeout=25)
        response.raise_for_status()
        data = response.json()
        
        relation_element = None
        for element in data.get('elements', []):
            if element.get('type') == 'relation':
                members = element.get('members', [])
                outer_ways = []
                
                for member in members:
                    if member.get('role') == 'outer' and member.get('type') == 'way':
                        way_id = member.get('ref')
                        if way_id in MANHATTAN_WAYS:
                            outer_ways.append(way_id)
                
                if outer_ways:
                    print(f"Found Manhattan Island relation with {len(outer_ways)} matching ways")
                    relation_element = element
                    break
        
        if relation_element:
            way_coords_dict = {}
            for element in data.get('elements', []):
                if element.get('type') == 'way' and element.get('id') in MANHATTAN_WAYS:
                    geometry = element.get('geometry', [])
                    coords = [(node.get('lon'), node.get('lat')) for node in geometry]
                    if coords:
                        way_coords_dict[element.get('id')] = coords
            
            if way_coords_dict:
                print(f"Extracted {len(way_coords_dict)} ways from relation")
                polygon = connect_ways(way_coords_dict)
                if polygon and len(polygon) > 10:
                    print(f"Connected polygon with {len(polygon)} points")
                    return {'geometry': polygon}
                else:
                    all_coords = []
                    for way_id in MANHATTAN_WAYS:
                        if way_id in way_coords_dict:
                            all_coords.extend(way_coords_dict[way_id])
                    if all_coords:
                        print(f"Using {len(all_coords)} coordinates from all ways (not connected)")
                        return {'geometry': all_coords}
        
        print("Manhattan Island relation not found or incomplete, querying ways directly...")
        return get_manhattan_polygon_from_ways()
    except Exception as e:
        print(f"Error querying relation: {e}")
        print("Falling back to querying ways directly...")
        return get_manhattan_polygon_from_ways()

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

def get_manhattan_polygon_from_ways():
    """Get Manhattan Island polygon by querying the specific ways."""
    print("Querying OSM for Manhattan Island ways...")
    
    way_ids = ','.join(map(str, MANHATTAN_WAYS))
    query = f"""
    [out:json][timeout:25];
    (
      way(id:{way_ids});
    );
    out geom;
    """
    
    try:
        import requests
        response = requests.post(osm_integration.base_url, data=query, timeout=25)
        response.raise_for_status()
        data = response.json()
        
        way_coords = {}
        
        for element in data.get('elements', []):
            if element.get('type') == 'way':
                way_id = element.get('id')
                geometry = element.get('geometry', [])
                coords = [(node.get('lon'), node.get('lat')) for node in geometry]
                if coords:
                    way_coords[way_id] = coords
        
        print(f"Retrieved {len(way_coords)} ways")
        
        if way_coords:
            polygon = connect_ways(way_coords)
            if polygon and len(polygon) > 10:
                print(f"Connected polygon with {len(polygon)} points")
                return {'geometry': polygon}
            else:
                print(f"Warning: Connected polygon has only {len(polygon) if polygon else 0} points, using all way coordinates")
                all_coords = []
                for way_id in MANHATTAN_WAYS:
                    if way_id in way_coords:
                        all_coords.extend(way_coords[way_id])
                if all_coords:
                    print(f"Using {len(all_coords)} coordinates from all ways")
                    return {'geometry': all_coords}
        
        return None
    except Exception as e:
        print(f"Error querying ways: {e}")
        return None

def is_in_lower_manhattan(lat, lon):
    """Check if point is in Lower Manhattan (south of ~40.75)."""
    return lat < 40.75

population_points = []
total_points = (width + 1) * (height + 1)
processed = 0

nyc_bounds = get_nyc_bounds()
min_lat = nyc_bounds['min_lat']
max_lat = nyc_bounds['max_lat']
min_lon = nyc_bounds['min_lon']
max_lon = nyc_bounds['max_lon']

manhattan_data = get_manhattan_polygon()
if not manhattan_data:
    print("ERROR: Could not retrieve Manhattan Island polygon from OSM")
    sys.exit(1)

geometry = manhattan_data.get('geometry', [])
if not geometry:
    print("ERROR: No geometry data in Manhattan Island relation")
    sys.exit(1)

print(f"\nGenerating population points for {width}x{height} grid ({total_points} points total)...")
print(f"Using Manhattan Island polygon with {len(geometry)} boundary points...")
print("")

polygon = geometry

for x in range(width + 1):
    for y in range(height + 1):
        processed += 1
        if processed % 100 == 0:
            print(f"Processing: {processed}/{total_points} points ({processed*100//total_points}%)...")
            sys.stdout.flush()
        
        lat = min_lat + (y / height) * (max_lat - min_lat)
        lon = min_lon + (x / width) * (max_lon - min_lon)
        
        point = (lon, lat)
        
        cell_center_inside = point_in_polygon(point, polygon)
        
        if cell_center_inside:
            population_points.append((x, y))
        else:
            cell_size_lat = (max_lat - min_lat) / height
            cell_size_lon = (max_lon - min_lon) / width
            
            subgrid_size = 3
            sub_lat_step = cell_size_lat / subgrid_size
            sub_lon_step = cell_size_lon / subgrid_size
            
            subgrid_points = []
            for i in range(subgrid_size):
                for j in range(subgrid_size):
                    sub_lat = lat - cell_size_lat/2 + (i + 0.5) * sub_lat_step
                    sub_lon = lon - cell_size_lon/2 + (j + 0.5) * sub_lon_step
                    subgrid_points.append((sub_lon, sub_lat))
            
            any_subpoint_inside = any(point_in_polygon(test_point, polygon) for test_point in subgrid_points)
            
            if any_subpoint_inside:
                population_points.append((x, y))
        
        if processed % 10 == 0:
            time.sleep(0.01)

print(f"\n# Pre-computed population points (x, y) for {width}x{height} grid")
print(f"# Generated using Manhattan Island OSM relation polygon")
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
print("Generation complete!")

