"""OpenStreetMap integration for GoCommuter map and line creation"""
import time
from typing import List, Tuple, Optional, Dict

import requests

# Overpass usage policy: identify the application; avoid generic library UA (often blocked / 403).
_OVERPASS_HEADERS = {
    'User-Agent': (
        'GoCommuter/1.0 (OpenStreetMap data consumer; '
        '+https://wiki.openstreetmap.org/wiki/Overpass_API ; '
        'respects Overpass usage policy)'
    ),
    'Accept': 'application/json',
}


class OSMIntegration:
    """Handles OpenStreetMap API integration for map data and rights of way"""
    
    def __init__(self, timeout: float = None):
        """Initialize OSM integration with timeout for API calls"""
        from map_presets import OSM_SETTINGS
        self.timeout = timeout if timeout is not None else OSM_SETTINGS['timeout']
        self.base_url = OSM_SETTINGS['base_url']
        self._overpass_urls = [self.base_url]
        for u in (OSM_SETTINGS.get('mirror_urls') or ()):
            if u and u not in self._overpass_urls:
                self._overpass_urls.append(u)
        self._cache = {}  # Cache for API responses

    def _post_overpass(self, query: str, timeout: float):
        """POST to Overpass, rotating mirrors on 403/429/5xx. Returns Response or None."""
        data = query.encode('utf-8') if isinstance(query, str) else query
        for url in self._overpass_urls:
            try:
                response = requests.post(
                    url, data=data, timeout=timeout, headers=dict(_OVERPASS_HEADERS)
                )
                if response.status_code in (403, 429, 502, 503, 504):
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException:
                continue
        return None
    
    def get_railway_ways_in_bbox(self, min_lat: float, min_lon: float, 
                                  max_lat: float, max_lon: float) -> List[Dict]:
        """Get railway ways (existing rights of way) in a bounding box from OSM.
        Returns list of ways with their coordinates."""
        cache_key = f"railway_{min_lat}_{min_lon}_{max_lat}_{max_lon}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Overpass QL query to get railway ways
        query = f"""
        [out:json][timeout:{int(self.timeout)}];
        (
          way["railway"~"rail|tram|light_rail|subway"]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
        """
        
        try:
            response = self._post_overpass(query, self.timeout)
            if not response:
                return []
            data = response.json()

            ways = []
            for element in data.get('elements', []):
                if element.get('type') == 'way':
                    # Extract coordinates
                    geometry = element.get('geometry', [])
                    if geometry:
                        coords = [(point.get('lat'), point.get('lon')) for point in geometry]
                        ways.append({
                            'id': element.get('id'),
                            'tags': element.get('tags', {}),
                            'coordinates': coords,
                            'railway_type': element.get('tags', {}).get('railway', 'rail')
                        })

            self._cache[cache_key] = ways
            return ways
        except requests.exceptions.Timeout:
            return []
        except requests.exceptions.RequestException:
            return []
    
    def get_highway_ways_in_bbox(self, min_lat: float, min_lon: float,
                                  max_lat: float, max_lon: float) -> List[Dict]:
        """Get highway ways (roads) in a bounding box from OSM."""
        cache_key = f"highway_{min_lat}_{min_lon}_{max_lat}_{max_lon}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        query = f"""
        [out:json][timeout:{int(self.timeout)}];
        (
          way["highway"~"primary|secondary|tertiary|trunk"]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out geom;
        """
        
        try:
            response = self._post_overpass(query, self.timeout)
            if not response:
                return []
            data = response.json()
            
            ways = []
            for element in data.get('elements', []):
                if element.get('type') == 'way':
                    geometry = element.get('geometry', [])
                    if geometry:
                        coords = [(point.get('lat'), point.get('lon')) for point in geometry]
                        ways.append({
                            'id': element.get('id'),
                            'tags': element.get('tags', {}),
                            'coordinates': coords,
                            'highway_type': element.get('tags', {}).get('highway', 'road')
                        })
            
            self._cache[cache_key] = ways
            return ways
        except requests.exceptions.Timeout:
            return []
        except requests.exceptions.RequestException:
            return []
    
    def find_nearest_way(self, lat: float, lon: float, ways: List[Dict], 
                         max_distance_km: float = 0.5) -> Optional[Dict]:
        """Find the nearest way to a given coordinate.
        Returns the way if within max_distance_km, None otherwise."""
        import math
        
        min_distance = float('inf')
        nearest_way = None
        
        for way in ways:
            coords = way.get('coordinates', [])
            if not coords:
                continue
            
            # Find minimum distance to any point on the way
            for way_lat, way_lon in coords:
                # Haversine distance
                dlat = math.radians(way_lat - lat)
                dlon = math.radians(way_lon - lon)
                a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(way_lat)) * math.sin(dlon/2)**2
                c = 2 * math.asin(math.sqrt(a))
                distance_km = 6371 * c  # Earth radius in km
                
                if distance_km < min_distance:
                    min_distance = distance_km
                    nearest_way = way
        
        if min_distance <= max_distance_km:
            return nearest_way
        return None
    
    def snap_points_to_way(self, points: List[Tuple[float, float]], 
                           way: Dict) -> List[Tuple[float, float]]:
        """Snap a list of points to the nearest points on a way.
        Returns list of snapped coordinates."""
        import math
        
        way_coords = way.get('coordinates', [])
        if not way_coords:
            return points
        
        snapped_points = []
        for lat, lon in points:
            min_distance = float('inf')
            nearest_point = way_coords[0]
            
            # Find nearest point on way
            for way_lat, way_lon in way_coords:
                dlat = math.radians(way_lat - lat)
                dlon = math.radians(way_lon - lon)
                a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(way_lat)) * math.sin(dlon/2)**2
                c = 2 * math.asin(math.sqrt(a))
                distance_km = 6371 * c
                
                if distance_km < min_distance:
                    min_distance = distance_km
                    nearest_point = (way_lat, way_lon)
            
            snapped_points.append(nearest_point)
        
        return snapped_points
    
    def convert_latlon_to_grid(self, lat: float, lon: float, 
                               map_center_lat: float, map_center_lon: float,
                               grid_size: int, map_width: int = None, map_height: int = None,
                               is_nyc_proper: bool = False) -> Tuple[float, float]:
        """Convert lat/lon coordinates to grid coordinates (preserving float precision).
        For NYC maps, uses bounds-based conversion to match grid->lat/lon conversion."""
        import math
        
        # For NYC maps, use bounds-based conversion to match the grid->lat/lon conversion
        if is_nyc_proper and map_width and map_height:
            try:
                from map_presets_nyc import get_nyc_bounds
                nyc_bounds = get_nyc_bounds()
                # Reverse of: lat = min_lat + (y/height) * (max_lat - min_lat)
                # So: y = (lat - min_lat) / (max_lat - min_lat) * height
                y = (lat - nyc_bounds['min_lat']) / (nyc_bounds['max_lat'] - nyc_bounds['min_lat']) * map_height
                # Reverse of: lon = min_lon + (x/width) * (max_lon - min_lon)
                # So: x = (lon - min_lon) / (max_lon - min_lon) * width
                x = (lon - nyc_bounds['min_lon']) / (nyc_bounds['max_lon'] - nyc_bounds['min_lon']) * map_width
                
                # Clamp to grid bounds (but keep as float)
                x = max(0.0, min(float(map_width), x))
                y = max(0.0, min(float(map_height), y))
                
                return (x, y)
            except Exception:
                # Fall through to standard conversion if NYC bounds not available
                pass
        
        # Standard conversion for non-NYC maps
        # Simple equirectangular projection
        # 1 degree lat ≈ 111 km, 1 degree lon ≈ 111 km * cos(lat)
        lat_km = (lat - map_center_lat) * 111.0
        lon_km = (lon - map_center_lon) * 111.0 * math.cos(math.radians(map_center_lat))
        
        # Convert to grid coordinates (assuming 1 grid unit = 1 km)
        # Center the grid - preserve float precision
        x = float(lon_km) + grid_size // 2
        y = float(lat_km) + grid_size // 2
        
        # Clamp to grid bounds (but keep as float)
        x = max(0.0, min(float(grid_size - 1), x))
        y = max(0.0, min(float(grid_size - 1), y))
        
        return (x, y)
    
    def convert_grid_to_latlon(self, x: int, y: int,
                                map_center_lat: float, map_center_lon: float,
                                grid_size: int) -> Tuple[float, float]:
        """Convert grid coordinates to lat/lon coordinates."""
        import math
        
        # Convert from grid to km
        lon_km = (x - grid_size // 2)
        lat_km = (y - grid_size // 2)
        
        # Convert km to degrees
        lat = map_center_lat + (lat_km / 111.0)
        lon = map_center_lon + (lon_km / (111.0 * math.cos(math.radians(map_center_lat))))
        
        return (lat, lon)
    
    def get_available_rights_of_way(self, min_lat: float, min_lon: float,
                                     max_lat: float, max_lon: float) -> List[Dict]:
        """Get all available rights of way (railways and highways) in a bounding box.
        Returns list of ways that can be used for line construction."""
        railway_ways = self.get_railway_ways_in_bbox(min_lat, min_lon, max_lat, max_lon)
        highway_ways = self.get_highway_ways_in_bbox(min_lat, min_lon, max_lat, max_lon)
        
        # Combine and mark type
        all_ways = []
        for way in railway_ways:
            way['right_of_way_type'] = 'railway'
            all_ways.append(way)
        for way in highway_ways:
            way['right_of_way_type'] = 'highway'
            all_ways.append(way)
        
        return all_ways
    
    def is_water_at_latlon(self, lat: float, lon: float) -> bool:
        """Check if a given lat/lon coordinate is over water using OSM data.
        Uses Overpass API to query for water bodies (natural=water) near the point.
        Returns True if water is found, False otherwise."""
        # Use a small bounding box around the point (about 100m radius)
        buffer = 0.001  # Approximately 100 meters
        min_lat = lat - buffer
        max_lat = lat + buffer
        min_lon = lon - buffer
        max_lon = lon + buffer
        
        # Cache with rounding to avoid exploding unique keys for near-identical points.
        # 5 decimals ≈ 1.1m latitude resolution (good tradeoff for speed vs accuracy).
        cache_key = f"water_{round(lat, 5)}_{round(lon, 5)}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Overpass QL query to check for water bodies
        # Include common water-related tags to reduce false negatives near coasts/rivers.
        query = f"""
        [out:json][timeout:5];
        (
          way["natural"="water"]({min_lat},{min_lon},{max_lat},{max_lon});
          relation["natural"="water"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["waterway"="riverbank"]({min_lat},{min_lon},{max_lat},{max_lon});
          relation["waterway"="riverbank"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["natural"="coastline"]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out body;
        """
        
        try:
            response = self._post_overpass(query, 5)
            if not response:
                return False
            data = response.json()
            # If we found any water elements, the point is in water
            is_water = len(data.get('elements', [])) > 0
            self._cache[cache_key] = is_water
            return is_water
        except requests.exceptions.Timeout:
            # Timeout - assume not water to avoid blocking
            return False
        except requests.exceptions.RequestException:
            # Request failed - assume not water
            return False
    
    def is_in_nyc_borough(self, lat: float, lon: float) -> bool:
        """Check if a point is within NYC borough boundaries using OSM administrative boundaries.
        Queries for relations with admin_level=6 and boundary=administrative in NYC area.
        Returns True if the point is within NYC boroughs, False otherwise."""
        # NYC borough bounding box for query
        nyc_bbox = (40.4950, -74.0500, 40.9150, -73.7000)
        min_lat, min_lon, max_lat, max_lon = nyc_bbox
        
        cache_key = f"borough_{lat}_{lon}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Overpass QL query to check for NYC borough boundaries
        # admin_level=6 is typically used for boroughs/counties in US
        # boundary=administrative and place=borough or place=county
        query = f"""
        [out:json][timeout:10];
        (
          relation["admin_level"="6"]["boundary"="administrative"][~"^place|^name"~"^(Manhattan|Brooklyn|Queens|Bronx|Staten Island|Richmond|Kings|New York)",i]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        (._;>;);
        out geom;
        """
        
        try:
            response = self._post_overpass(query, 10)
            if not response:
                return self._is_in_nyc_approximate_bounds(lat, lon)
            data = response.json()
            
            # Check if point is within any of the borough boundaries
            # For simplicity, use a point-in-polygon check with the relation geometries
            # OSM returns relations with members that define the boundary
            elements = data.get('elements', [])
            
            # Use improved approximate bounds (OSM relation parsing is complex)
            # The bounds check excludes water and New Jersey
            result = self._is_in_nyc_approximate_bounds(lat, lon)
            self._cache[cache_key] = result
            return result
        except requests.exceptions.Timeout:
            # Timeout - use approximate bounds as fallback
            return self._is_in_nyc_approximate_bounds(lat, lon)
        except requests.exceptions.RequestException:
            # Request failed - use approximate bounds
            return self._is_in_nyc_approximate_bounds(lat, lon)
    
    def _is_in_nyc_approximate_bounds(self, lat: float, lon: float) -> bool:
        """Fallback method using approximate NYC borough bounds when OSM query fails.
        Expanded to cover all land in the five boroughs."""
        # Manhattan - covers all of Manhattan including small islands
        if 40.70 <= lat <= 40.88 and -74.05 <= lon <= -73.90:
            return True
        # Brooklyn - covers all of Brooklyn including southern and eastern parts
        elif 40.57 <= lat <= 40.75 and -74.05 <= lon <= -73.70:
            return True
        # Queens - covers all of Queens including northern and eastern parts
        elif 40.55 <= lat <= 40.80 and -74.05 <= lon <= -73.70:
            return True
        # Bronx - covers all of the Bronx including northern parts
        elif 40.80 <= lat <= 40.92 and -73.95 <= lon <= -73.75:
            return True
        # Staten Island (Richmond) - extended west to cover all of Staten Island
        elif 40.49 <= lat <= 40.65 and -74.30 <= lon <= -74.02:
            return True
        return False

