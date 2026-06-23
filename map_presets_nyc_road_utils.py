"""Road and bridge point-checking utilities for NYC map.
Imported by map_presets_nyc to avoid circular imports."""

from map_presets_nyc import NYC_HISTORICAL_ROADS, get_nyc_bounds


def _grid_to_latlon(x: float, y: float, game_map) -> tuple:
    """Convert grid coordinates (x, y) to (lat, lon) for NYC map."""
    bounds = get_nyc_bounds()
    min_lat = bounds['min_lat']
    max_lat = bounds['max_lat']
    min_lon = bounds['min_lon']
    max_lon = bounds['max_lon']
    w = max(1, game_map.width)
    h = max(1, game_map.height)
    lat = min_lat + (y / h) * (max_lat - min_lat)
    lon = min_lon + (x / w) * (max_lon - min_lon)
    return (lat, lon)


def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from point (px, py) to line segment (ax,ay)-(bx,by). Uses lat/lon as x/y."""
    dx = bx - ax
    dy = by - ay
    dist_sq = dx * dx + dy * dy
    if dist_sq < 1e-20:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / dist_sq))
    nx = ax + t * dx
    ny = ay + t * dy
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def is_point_on_road_at_year(x: float, y: float, current_year: int, game_map) -> bool:
    """Check if grid point (x, y) is on a road that exists at current_year in NYC."""
    lat, lon = _grid_to_latlon(x, y, game_map)
    tolerance = 0.00015  # ~15m in degrees
    for road_name, road in NYC_HISTORICAL_ROADS.items():
        year_built = road.get('year', 1800)
        removed_year = road.get('removed_year')
        if year_built > current_year:
            continue
        if removed_year is not None and removed_year <= current_year:
            continue
        bounds = road.get('bounds', {})
        if (bounds.get('min_lat', 0) - tolerance <= lat <= bounds.get('max_lat', 0) + tolerance and
                bounds.get('min_lon', 0) - tolerance <= lon <= bounds.get('max_lon', 0) + tolerance):
            route = road.get('route', [])
            for i in range(len(route) - 1):
                a_lat, a_lon = route[i][0], route[i][1]
                b_lat, b_lon = route[i + 1][0], route[i + 1][1]
                d = _point_to_segment_distance(lon, lat, a_lon, a_lat, b_lon, b_lat)
                if d <= tolerance:
                    return True
    return False


def is_point_on_bridge_at_year(x: float, y: float, current_year: int, game_map) -> bool:
    """Check if grid point (x, y) is on a bridge that exists at current_year in NYC."""
    lat, lon = _grid_to_latlon(x, y, game_map)
    tolerance = 0.00015
    for road_name, road in NYC_HISTORICAL_ROADS.items():
        if 'bridge' not in road_name.lower():
            continue
        year_built = road.get('year', 1800)
        removed_year = road.get('removed_year')
        if year_built > current_year:
            continue
        if removed_year is not None and removed_year <= current_year:
            continue
        bounds = road.get('bounds', {})
        if (bounds.get('min_lat', 0) - tolerance <= lat <= bounds.get('max_lat', 0) + tolerance and
                bounds.get('min_lon', 0) - tolerance <= lon <= bounds.get('max_lon', 0) + tolerance):
            route = road.get('route', [])
            for i in range(len(route) - 1):
                a_lat, a_lon = route[i][0], route[i][1]
                b_lat, b_lon = route[i + 1][0], route[i + 1][1]
                d = _point_to_segment_distance(lon, lat, a_lon, a_lat, b_lon, b_lat)
                if d <= tolerance:
                    return True
    return False
