"""Generate Long Island population points for NY Metropolitan Zone map.
Queries OSM relation for Long Island (3955977) and generates grid points.
This is SEPARATE from NYC map - these points go into NY_METRO_INCLUDED_POINTS."""

from map_presets_ny_metro import get_ny_metro_bounds, NY_METRO_INCLUDED_POINTS
from osm_integration import OSMIntegration
import time
import sys
import math
import re

width, height = 60, 60  # NY Metro uses 60x60 grid

print("Initializing OSM integration...")
osm_integration = OSMIntegration(timeout=90.0)
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
    [out:json][timeout:90];
    (
      relation({relation_id});
    );
    out geom;
    """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = osm_integration.overpass_query(query)
            if response and 'elements' in response:
                elements = response['elements']
                if elements:
                    relation = elements[0]
                    if 'members' in relation:
                        # Collect all outer ways
                        ways = {}
                        for member in relation['members']:
                            if member.get('type') == 'way' and member.get('role') == 'outer':
                                way_id = member.get('ref')
                                if 'geometry' in member:
                                    ways[way_id] = member['geometry']
                        
                        if ways:
                            # Connect ways into a polygon
                            polygon = []
                            for way_id, geometry in ways.items():
                                for node in geometry:
                                    if 'lat' in node and 'lon' in node:
                                        polygon.append((node['lon'], node['lat']))
                            
                            if len(polygon) >= 3:
                                print(f"Successfully retrieved polygon with {len(polygon)} points")
                                return polygon
            print(f"Attempt {attempt + 1} failed: No valid polygon found")
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = 15 * (2 ** attempt)  # Exponential backoff
                print(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
    
    return None

def generate_grid_points_in_polygon(polygon, bounds, width, height):
    """Generate grid points within a polygon using comprehensive sampling."""
    min_lat = bounds['min_lat']
    max_lat = bounds['max_lat']
    min_lon = bounds['min_lon']
    max_lon = bounds['max_lon']
    
    points = []
    
    # Sample grid points
    for x in range(width + 1):
        for y in range(height + 1):
            # Convert grid coordinates to lat/lon
            lat = min_lat + (y / height) * (max_lat - min_lat)
            lon = min_lon + (x / width) * (max_lon - min_lon)
            
            # Check center point
            if point_in_polygon((lon, lat), polygon):
                points.append((x, y))
                continue
            
            # Check corners of grid cell
            cell_size_lat = (max_lat - min_lat) / height
            cell_size_lon = (max_lon - min_lon) / width
            
            corners = [
                (lon - cell_size_lon/2, lat - cell_size_lat/2),
                (lon + cell_size_lon/2, lat - cell_size_lat/2),
                (lon + cell_size_lon/2, lat + cell_size_lat/2),
                (lon - cell_size_lon/2, lat + cell_size_lat/2),
            ]
            
            # If any corner is inside, include the point
            if any(point_in_polygon(corner, polygon) for corner in corners):
                points.append((x, y))
                continue
            
            # Check edge midpoints
            edge_midpoints = [
                (lon, lat - cell_size_lat/2),
                (lon + cell_size_lon/2, lat),
                (lon, lat + cell_size_lat/2),
                (lon - cell_size_lon/2, lat),
            ]
            
            if any(point_in_polygon(mp, polygon) for mp in edge_midpoints):
                points.append((x, y))
                continue
            
            # Check subgrid (4x4) for better coverage
            subgrid_size = 4
            subcell_lat = cell_size_lat / subgrid_size
            subcell_lon = cell_size_lon / subgrid_size
            
            for sx in range(subgrid_size):
                for sy in range(subgrid_size):
                    sub_lat = lat - cell_size_lat/2 + (sy + 0.5) * subcell_lat
                    sub_lon = lon - cell_size_lon/2 + (sx + 0.5) * subcell_lon
                    if point_in_polygon((sub_lon, sub_lat), polygon):
                        points.append((x, y))
                        break
                else:
                    continue
                break
    
    return points

def main():
    print("=" * 60)
    print("NY Metropolitan Zone - Long Island Population Points Generator")
    print("=" * 60)
    print(f"Grid size: {width}x{height}")
    print()
    
    bounds = get_ny_metro_bounds()
    print(f"Map bounds: {bounds}")
    print()
    
    # Query Long Island relation
    long_island_relation_id = 3955977
    print(f"Querying Long Island relation (ID: {long_island_relation_id})...")
    polygon = get_polygon_from_relation(long_island_relation_id)
    
    if not polygon:
        print("ERROR: Failed to retrieve Long Island polygon from OSM")
        sys.exit(1)
    
    print(f"Long Island polygon has {len(polygon)} points")
    print()
    
    # Generate grid points
    print("Generating grid points within Long Island...")
    points = generate_grid_points_in_polygon(polygon, bounds, width, height)
    print(f"Generated {len(points)} points within Long Island")
    print()
    
    # Read current NY_METRO_INCLUDED_POINTS
    try:
        from map_presets_ny_metro import NY_METRO_INCLUDED_POINTS
        current_points = list(NY_METRO_INCLUDED_POINTS)
        print(f"Current NY_METRO_INCLUDED_POINTS has {len(current_points)} points")
    except:
        current_points = []
        print("NY_METRO_INCLUDED_POINTS not found, starting fresh")
    
    # Add new points (avoid duplicates)
    new_points = []
    current_set = set(current_points)
    for point in points:
        if point not in current_set:
            new_points.append(point)
            current_points.append(point)
    
    print(f"Adding {len(new_points)} new points to NY_METRO_INCLUDED_POINTS")
    print(f"Total points after update: {len(current_points)}")
    print()
    
    # Update the file
    print("Updating map_presets_ny_metro.py...")
    with open('map_presets_ny_metro.py', 'r') as f:
        content = f.read()
    
    # Find and replace NY_METRO_INCLUDED_POINTS
    pattern = r'NY_METRO_INCLUDED_POINTS = \[.*?\]'
    replacement = f'NY_METRO_INCLUDED_POINTS = {current_points}'
    
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    else:
        # If pattern not found, add it before NY_METRO_POPULATION_POINTS
        pattern2 = r'NY_METRO_POPULATION_POINTS = NY_METRO_INCLUDED_POINTS'
        replacement2 = f'NY_METRO_INCLUDED_POINTS = {current_points}\n\nNY_METRO_POPULATION_POINTS = NY_METRO_INCLUDED_POINTS'
        content = re.sub(pattern2, replacement2, content)
    
    with open('map_presets_ny_metro.py', 'w') as f:
        f.write(content)
    
    print("Update complete!")
    print()
    print("=" * 60)
    print("Summary:")
    print(f"  - Long Island points generated: {len(points)}")
    print(f"  - New points added: {len(new_points)}")
    print(f"  - Total NY_METRO_INCLUDED_POINTS: {len(current_points)}")
    print("=" * 60)

if __name__ == "__main__":
    main()

