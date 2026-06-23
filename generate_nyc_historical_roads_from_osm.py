#!/usr/bin/env python3
"""
Generate NYC historical roads database from actual OSM Ways.
Fetches all highway ways within NYC bounds and creates the historical roads dictionary.
"""

import requests
import time
import json
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# NYC bounds
NYC_BOUNDS = {
    'min_lat': 40.4900,  # Southern edge (Staten Island)
    'max_lat': 40.9200,  # Northern edge (Bronx)
    'min_lon': -74.3000,  # Western edge (Staten Island)
    'max_lon': -73.7000,  # Eastern edge (Queens)
}

# OSM Overpass API endpoint
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Timeout for API calls (in seconds)
TIMEOUT = 300  # 5 minutes for large queries

def get_all_highway_ways_in_bbox(min_lat: float, min_lon: float, 
                                  max_lat: float, max_lon: float) -> List[Dict]:
    """Get ALL highway ways (roads) in a bounding box from OSM.
    Includes all highway types: motorway, trunk, primary, secondary, tertiary,
    unclassified, residential, service, etc."""
    
    print(f"Fetching all highway ways from OSM...")
    print(f"Bounds: ({min_lat}, {min_lon}) to ({max_lat}, {max_lon})")
    
    # Query for ALL highway types
    query = f"""
    [out:json][timeout:{TIMEOUT}];
    (
      way["highway"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out geom;
    """
    
    try:
        print("Sending request to Overpass API (this may take several minutes)...")
        start_time = time.time()
        response = requests.post(OVERPASS_URL, data=query, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        elapsed = time.time() - start_time
        print(f"Received response in {elapsed:.1f} seconds")
        
        ways = []
        for element in data.get('elements', []):
            if element.get('type') == 'way':
                geometry = element.get('geometry', [])
                if geometry:
                    coords = [(point.get('lat'), point.get('lon')) for point in geometry]
                    if len(coords) >= 2: 
                        ways.append({
                            'id': element.get('id'),
                            'tags': element.get('tags', {}),
                            'coordinates': coords,
                            'highway_type': element.get('tags', {}).get('highway', 'road')
                        })
        
        print(f"Found {len(ways)} highway ways")
        return ways
        
    except requests.exceptions.Timeout:
        print("ERROR: Request timed out. Try increasing TIMEOUT or splitting the query.")
        return []
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return []


def calculate_bounds(coordinates: List[Tuple[float, float]]) -> Dict[str, float]:
    """Calculate bounding box from a list of coordinates."""
    if not coordinates:
        return {'min_lat': 0, 'max_lat': 0, 'min_lon': 0, 'max_lon': 0}
    
    lats = [coord[0] for coord in coordinates]
    lons = [coord[1] for coord in coordinates]
    
    return {
        'min_lat': min(lats),
        'max_lat': max(lats),
        'min_lon': min(lons),
        'max_lon': max(lons)
    }


def estimate_construction_year(tags: Dict[str, str]) -> int:
    """Estimate construction year from OSM tags.
    Uses start_date if available, otherwise defaults to pre-1830 (1600) for most roads."""
    
    # Check for start_date tag
    if 'start_date' in tags:
        try:
            year_str = tags['start_date']
            # Handle various formats: "1830", "1830s", "c. 1830", etc.
            year_str = year_str.replace('s', '').replace('c.', '').replace('c', '').strip()
            year_str = year_str.split('-')[0].split('/')[0]  # Take first year if range
            year = int(year_str)
            return year
        except (ValueError, AttributeError):
            pass
    
    # Check for historic:date tag
    if 'historic:date' in tags:
        try:
            year_str = tags['historic:date']
            year_str = year_str.replace('s', '').replace('c.', '').replace('c', '').strip()
            year_str = year_str.split('-')[0].split('/')[0]
            year = int(year_str)
            return year
        except (ValueError, AttributeError):
            pass
    
    # Default: most NYC streets existed before 1830
    # Major streets: 1600 (pre-colonial)
    # Minor streets: 1800 (early 19th century)
    highway_type = tags.get('highway', '')
    
    # Major roads likely existed earlier
    if highway_type in ['motorway', 'trunk', 'primary']:
        return 1600  # Pre-colonial major routes
    elif highway_type in ['secondary', 'tertiary']:
        return 1700  # Colonial era
    else:
        return 1800  # Early 19th century for residential/unclassified


def get_road_name(tags: Dict[str, str], way_id: int) -> str:
    """Get road name from OSM tags, or generate one from way ID."""
    
    # Try various name tags
    name_tags = ['name', 'name:en', 'ref', 'alt_name']
    
    for tag in name_tags:
        if tag in tags and tags[tag]:
            name = tags[tag]
            # Clean up name for use as dictionary key
            name = name.replace(' ', '_').replace('-', '_').replace('/', '_')
            name = name.replace("'", '').replace('"', '')
            # Remove special characters
            name = ''.join(c for c in name if c.isalnum() or c == '_')
            if name:
                return name
    
    # If no name, use way ID
    return f"OSM_Way_{way_id}"


def group_ways_by_name(ways: List[Dict]) -> Dict[str, List[Dict]]:
    """Group ways by their name (many ways may share the same street name)."""
    grouped = defaultdict(list)
    
    for way in ways:
        name = get_road_name(way['tags'], way['id'])
        grouped[name].append(way)
    
    return dict(grouped)


def merge_ways_for_road(ways: List[Dict]) -> Dict:
    """Merge multiple way segments for the same road into one entry.
    Takes the earliest construction year, merges bounds, and preserves route coordinates."""
    
    if not ways:
        return None
    
    # Get name from first way (all should have same name)
    name = get_road_name(ways[0]['tags'], ways[0]['id'])
    
    # Find earliest construction year
    years = [estimate_construction_year(way['tags']) for way in ways]
    earliest_year = min(years)
    
    # Collect all coordinates from all ways to create the full route
    # Preserve the order of coordinates to maintain the actual street path
    all_coords = []
    for way in ways:
        # Add coordinates from this way
        coords = way.get('coordinates', [])
        if coords:
            # If this isn't the first way, check if we need to connect it
            if all_coords:
                # Check if the last point of previous way matches first point of this way
                last_point = all_coords[-1]
                first_point = coords[0]
                # If they're close (within ~10 meters), they're connected
                import math
                lat_diff = abs(last_point[0] - first_point[0])
                lon_diff = abs(last_point[1] - first_point[1])
                # Rough check: ~0.0001 degrees ≈ 10 meters
                if lat_diff > 0.0001 or lon_diff > 0.0001:
                    # Not connected, add the first point again to create a connection
                    all_coords.append(first_point)
            all_coords.extend(coords)
        elif not all_coords:
            # First way has no coordinates, skip it
            continue
    
    if not all_coords:
        return None
    
    bounds = calculate_bounds(all_coords)
    
    return {
        'name': name,
        'year': earliest_year,
        'removed_year': None,  # Most roads are never removed
        'bounds': bounds,
        'route': all_coords,  # Save actual route coordinates
        'way_count': len(ways)  # Track how many OSM ways were merged
    }


def generate_historical_roads_dict(ways: List[Dict], 
                                     include_unnamed: bool = False,
                                     min_highway_types: List[str] = None) -> Dict:
    """Generate the historical roads dictionary from OSM ways.
    
    Args:
        ways: List of OSM way dictionaries
        include_unnamed: If True, include unnamed roads (default: False)
        min_highway_types: Minimum highway types to include (e.g., ['primary', 'secondary'])
                          If None, includes all types
    """
    
    print(f"\nProcessing {len(ways)} ways...")
    
    # Filter by highway type if specified
    if min_highway_types:
        highway_priority = {
            'motorway': 1,
            'trunk': 2,
            'primary': 3,
            'secondary': 4,
            'tertiary': 5,
            'unclassified': 6,
            'residential': 7,
            'service': 8
        }
        min_priority = min(highway_priority.get(ht, 99) for ht in min_highway_types)
        
        filtered_ways = []
        for way in ways:
            hw_type = way.get('highway_type', '')
            priority = highway_priority.get(hw_type, 99)
            if priority <= min_priority:
                filtered_ways.append(way)
        
        ways = filtered_ways
        print(f"Filtered to {len(ways)} ways (highway types: {', '.join(min_highway_types)})")
    
    # Group ways by name
    grouped = group_ways_by_name(ways)
    print(f"Found {len(grouped)} unique road names")
    
    # Merge ways for each road
    historical_roads = {}
    unnamed_count = 0
    included_unnamed = 0
    
    for name, way_list in grouped.items():
        if name.startswith('OSM_Way_'):
            unnamed_count += 1
            if not include_unnamed:
                continue
            included_unnamed += 1
        
        merged = merge_ways_for_road(way_list)
        if merged:
            # Handle duplicate names by appending number
            final_name = merged['name']
            counter = 1
            while final_name in historical_roads:
                final_name = f"{merged['name']}_{counter}"
                counter += 1
            
            historical_roads[final_name] = {
                'year': merged['year'],
                'removed_year': merged.get('removed_year'),
                'bounds': merged['bounds'],
                'route': merged.get('route', [])  # Include route coordinates
            }
    
    print(f"Generated {len(historical_roads)} historical road entries")
    print(f"Skipped {unnamed_count - included_unnamed} unnamed roads")
    if included_unnamed > 0:
        print(f"Included {included_unnamed} unnamed roads")
    
    return historical_roads


def format_as_python_dict(historical_roads: Dict) -> str:
    """Format the historical roads dictionary as Python code."""
    
    lines = ["NYC_HISTORICAL_ROADS = {"]
    
    # Sort by name for readability
    sorted_roads = sorted(historical_roads.items(), key=lambda x: x[0])
    
    for i, (name, info) in enumerate(sorted_roads):
        comma = "," if i < len(sorted_roads) - 1 else ""
        
        lines.append(f"    # {name.replace('_', ' ')}")
        lines.append(f"    '{name}': {{")
        lines.append(f"        'year': {info['year']},")
        
        if info.get('removed_year'):
            lines.append(f"        'removed_year': {info['removed_year']},")
        else:
            lines.append(f"        'removed_year': None,  # Never removed")
        
        bounds = info['bounds']
        lines.append(f"        'bounds': {{")
        lines.append(f"            'min_lat': {bounds['min_lat']:.6f},")
        lines.append(f"            'max_lat': {bounds['max_lat']:.6f},")
        lines.append(f"            'min_lon': {bounds['min_lon']:.6f},")
        lines.append(f"            'max_lon': {bounds['max_lon']:.6f}")
        lines.append(f"        }},")
        
        # Add route coordinates
        route = info.get('route', [])
        if route:
            lines.append(f"        'route': [")
            # Format route as list of [lat, lon] tuples
            for j, coord in enumerate(route):
                route_comma = "," if j < len(route) - 1 else ""  # Use different variable name
                lat, lon = coord[0], coord[1]
                lines.append(f"            [{lat:.6f}, {lon:.6f}]{route_comma}")
            lines.append(f"        ]")
        else:
            lines.append(f"        'route': []  # No route data available")
        
        # Add comma after the road entry (before closing brace)
        # Note: comma variable is set at the start of the loop for this road entry
        lines.append(f"    }}{comma}")
        lines.append("")
    
    lines.append("}")
    
    return "\n".join(lines)


def main():
    """Main function to generate historical roads from OSM."""
    
    import sys
    
    print("=" * 60)
    print("NYC Historical Roads Generator from OSM")
    print("=" * 60)
    print()
    
    # Configuration options
    # Set to True to include only major roads (primary, secondary, tertiary, trunk, motorway)
    # Set to False to include all named roads (including residential, unclassified)
    include_only_major_roads = False  # Changed to False to include ALL named streets
    
    # Set to True to include unnamed roads (not recommended - creates too many entries)
    include_unnamed = False
    
    if include_only_major_roads:
        min_highway_types = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary']
        print("Mode: Including only major roads (motorway, trunk, primary, secondary, tertiary)")
    else:
        min_highway_types = None
        print("Mode: Including all named roads")
    
    if include_unnamed:
        print("Warning: Including unnamed roads (will create many entries)")
    else:
        print("Excluding unnamed roads")
    
    print()
    
    # Fetch all highway ways
    ways = get_all_highway_ways_in_bbox(
        NYC_BOUNDS['min_lat'],
        NYC_BOUNDS['min_lon'],
        NYC_BOUNDS['max_lat'],
        NYC_BOUNDS['max_lon']
    )
    
    if not ways:
        print("ERROR: No ways found. Exiting.")
        return
    
    # Generate historical roads dictionary
    historical_roads = generate_historical_roads_dict(
        ways, 
        include_unnamed=include_unnamed,
        min_highway_types=min_highway_types
    )
    
    if not historical_roads:
        print("ERROR: No historical roads generated. Exiting.")
        return
    
    # Format as Python code
    python_code = format_as_python_dict(historical_roads)
    
    # Save to file
    output_file = "nyc_historical_roads_osm_generated.py"
    print(f"\nSaving to {output_file}...")
    
    with open(output_file, 'w') as f:
        f.write("# Generated NYC Historical Roads from OSM Ways\n")
        f.write("# This file was auto-generated by generate_nyc_historical_roads_from_osm.py\n")
        f.write("# Generated on: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
        f.write("# Total roads: " + str(len(historical_roads)) + "\n")
        f.write("#\n")
        f.write("# To use this in map_presets_nyc.py, replace the NYC_HISTORICAL_ROADS dictionary\n")
        f.write("# with the contents of this file.\n")
        f.write("#\n\n")
        f.write(python_code)
    
    print(f"✅ Successfully generated {len(historical_roads)} historical roads")
    print(f"✅ Saved to {output_file}")
    print()
    print("Next steps:")
    print("1. Review the generated file")
    print("2. Copy the NYC_HISTORICAL_ROADS dictionary to map_presets_nyc.py")
    print("3. Test the game to ensure roads work correctly")
    
    # Also save as JSON for reference
    json_file = "nyc_historical_roads_osm_generated.json"
    with open(json_file, 'w') as f:
        json.dump(historical_roads, f, indent=2)
    print(f"✅ Also saved as JSON to {json_file}")


if __name__ == '__main__':
    main()

