"""Route geometry, street, and water validation for line construction."""
from typing import Optional
from game import Game
from line import Line, Ferry, Bus as BusLine
from player import Player
from public_ta import PublicTA
from map import Map
from vehicle import Trainset, Locomotive, Railcar, Vehicle, Boat, Ferry, Bus
from timeline import Timeline
import math
import random
from marketplace import Marketplace, SaleItem
from game_presets import (
    AI_DIFFICULTIES,
    AI_FOCUSES,
    AI_MAX_STRAIGHT_LAND_SEGMENT_MILES,
    LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER,
    LINE_NIMBY_LAWSUIT_BASE_CHANCE,
    STARTING_MONEY,
    STATION_MAX_WATER_DISTANCE_TO_LAND_M,
)

# NIMBY line-build excuses (noise / property / environment).
_LINE_NIMBY_EXCUSE_CAUSES = frozenset({
    'Noise Pollution',
    'Property Damage',
    'Environmental Damage',
})

# AI difficulty bonuses: hard=strongest (cost 0.75x, marketplace 0.75x, lobby +0.15), easy=weakest
AI_DIFFICULTY_COST_MULTIPLIER = {'easy': 1.15, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_MARKETPLACE_MULTIPLIER = {'easy': 1.1, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_LOBBY_BONUS = {'easy': -0.05, 'medium': 0.0, 'hard': 0.15}
# AI-built stops must be at least this straight-line distance from any existing or queued stop (meters).
AI_MIN_STOP_DIRECT_SEPARATION_METERS = 80.0



def _norm_display_name(name: str) -> str:
    """Trim and casefold for uniqueness checks (lines and stations)."""
    return (name or "").strip().casefold()


def _normalize_line_route_geometry_key(points: list) -> tuple:
    """Ordered polyline key for duplicate-route checks (6 decimal places)."""
    key = []
    for pt in points:
        x, y = pt[0], pt[1]
        if isinstance(x, (list, tuple)):
            x, y = float(x[0]), float(x[1])
        elif hasattr(x, 'x'):
            x, y = float(x.x), float(x.y)
        else:
            x, y = float(x), float(y)
        key.append((round(x, 6), round(y, 6)))
    return tuple(key)


def _ai_line_geometry_exists_elsewhere(points: list, game_map: Map, build_queue: list) -> bool:
    """True if route matches any completed or queued line (forward or reverse)."""
    if not points or len(points) < 2:
        return False
    key_fwd = _normalize_line_route_geometry_key(points)
    key_rev = tuple(reversed(key_fwd))
    raw = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else None
    map_lines = raw if isinstance(raw, dict) else {}
    for line in map_lines.values():
        r = line.get_route() if hasattr(line, 'get_route') else None
        if not r or len(r) < 2:
            continue
        k = _normalize_line_route_geometry_key(r)
        if k == key_fwd or k == key_rev:
            return True
    bq = build_queue if isinstance(build_queue, list) else []
    for build in bq:
        if build.get('type') != 'line':
            continue
        pts = build.get('data', {}) or {}
        pts = pts.get('points', [])
        if len(pts) < 2:
            continue
        k = _normalize_line_route_geometry_key(pts)
        if k == key_fwd or k == key_rev:
            return True
    return False


def _ai_route_has_backtracking(points: list, game_map: Map) -> bool:
    """True when a route doubles back (loops, hairpins, parallel reverse legs, excessive detours)."""
    if not points or len(points) < 2:
        return False
    seen_edges: set = set()
    for i in range(len(points) - 1):
        seg_key = _normalize_line_route_geometry_key([points[i], points[i + 1]])
        if len(seg_key) != 2:
            continue
        rev_key = (seg_key[1], seg_key[0])
        if seg_key in seen_edges or rev_key in seen_edges:
            return True
        seen_edges.add(seg_key)
    if len(points) < 3:
        return False
    try:
        from game_presets import AI_MAX_ROUTE_DETOUR_RATIO, AI_ROUTE_BACKTRACK_TURN_DEGREES
        from map_preset_utils import (
            _polyline_xy_points,
            ai_grid_polyline_overlap_tolerance,
            polyline_has_reverse_corridor,
            polyline_has_self_overlap,
            route_turn_angle_degrees,
        )

        pts = _polyline_xy_points(points)
        if len(pts) >= 3:
            start, end = pts[0], pts[-1]
            direct = math.hypot(end[0] - start[0], end[1] - start[1])
            if direct < 0.04:
                return True
            total = 0.0
            for i in range(len(pts) - 1):
                total += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            if direct > 1e-9 and total / direct > float(AI_MAX_ROUTE_DETOUR_RATIO):
                return True
            for i in range(1, len(pts) - 1):
                if route_turn_angle_degrees(pts[i - 1], pts[i], pts[i + 1]) >= float(AI_ROUTE_BACKTRACK_TURN_DEGREES):
                    return True
        tol = ai_grid_polyline_overlap_tolerance(game_map)
        wide_tol = max(tol * 6.0, 0.15)
        if polyline_has_reverse_corridor(pts, wide_tol):
            return True
        return bool(polyline_has_self_overlap(points, tolerance=tol))
    except Exception:
        return False


def _route_corner_borders_water_crossing(
    game_map: Map, pts: list, corner_index: int, *, skip_osm: bool = False,
) -> bool:
    """Skip turn checks at corners where an adjacent segment is an over-water span."""
    if corner_index <= 0 or corner_index >= len(pts) - 1:
        return False
    x0, y0 = float(pts[corner_index - 1][0]), float(pts[corner_index - 1][1])
    x1, y1 = float(pts[corner_index][0]), float(pts[corner_index][1])
    x2, y2 = float(pts[corner_index + 1][0]), float(pts[corner_index + 1][1])
    return (
        _route_segment_is_water_crossing(game_map, x0, y0, x1, y1, skip_osm=skip_osm)
        or _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm)
    )


def _route_exceeds_max_turn_except_water_corners(
    game_map: Map,
    points: list,
    max_turn_degrees: float,
    *,
    min_segment_grid: float = 0.025,
    skip_osm: bool = False,
) -> bool:
    from map_preset_utils import _polyline_xy_points, route_turn_angle_degrees

    pts = _polyline_xy_points(points)
    if len(pts) < 3:
        return False
    for i in range(1, len(pts) - 1):
        if _route_corner_borders_water_crossing(game_map, pts, i, skip_osm=skip_osm):
            continue
        l1 = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        l2 = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if l1 < min_segment_grid or l2 < min_segment_grid:
            continue
        if route_turn_angle_degrees(pts[i - 1], pts[i], pts[i + 1]) > float(max_turn_degrees):
            return True
    return False


def _route_violates_min_turn_radius_except_water_corners(
    game_map: Map,
    points: list,
    min_radius_grid: float,
    *,
    min_segment_grid: float = 0.025,
    skip_osm: bool = False,
) -> bool:
    from map_preset_utils import _polyline_xy_points, route_turn_angle_degrees, route_approx_turn_radius_grid

    pts = _polyline_xy_points(points)
    if len(pts) < 3:
        return False
    for i in range(1, len(pts) - 1):
        if _route_corner_borders_water_crossing(game_map, pts, i, skip_osm=skip_osm):
            continue
        l1 = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        l2 = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if l1 < min_segment_grid or l2 < min_segment_grid:
            continue
        if route_turn_angle_degrees(pts[i - 1], pts[i], pts[i + 1]) < 8.0:
            continue
        if route_approx_turn_radius_grid(pts[i - 1], pts[i], pts[i + 1]) < float(min_radius_grid):
            return True
    return False


def _validate_route_geometry(
    points: list,
    game_map: Map,
    *,
    is_bus_line: bool = False,
    is_ferry_line: bool = False,
    skip_osm: bool = False,
) -> None:
    """Reject routes that exceed turn limits (rail and bus; not ferries). Backtracking is AI-only."""
    if is_ferry_line or not points or len(points) < 3:
        return
    from game_presets import (
        MAX_BUS_ROUTE_TURN_DEGREES,
        MAX_TRAIN_ROUTE_TURN_DEGREES,
        MIN_TRAIN_TURN_RADIUS_GRID,
        ROUTE_TURN_CHECK_MIN_SEGMENT_GRID,
    )

    max_turn = MAX_BUS_ROUTE_TURN_DEGREES if is_bus_line else MAX_TRAIN_ROUTE_TURN_DEGREES
    min_seg = ROUTE_TURN_CHECK_MIN_SEGMENT_GRID
    if _route_exceeds_max_turn_except_water_corners(
        game_map, points, max_turn, min_segment_grid=min_seg, skip_osm=skip_osm,
    ):
        kind = 'Bus' if is_bus_line else 'Train'
        raise ValueError(
            f"{kind} routes cannot turn more than {int(max_turn)}° at a corner. "
            "Use a gentler curve or a longer alignment."
        )
    if not is_bus_line and _route_violates_min_turn_radius_except_water_corners(
        game_map, points, MIN_TRAIN_TURN_RADIUS_GRID, min_segment_grid=min_seg, skip_osm=skip_osm,
    ):
        raise ValueError(
            "Turn radius is too tight for trains at one or more corners."
        )


def _route_segment_is_water_crossing(
    game_map: Map, x1: float, y1: float, x2: float, y2: float, *, skip_osm: bool = False,
) -> bool:
    """True when a segment may be a straight line (open water or bridge), not a land street."""
    ts = getattr(game_map, 'tile_system', None)

    def _in_water(x, y) -> bool:
        if ts:
            try:
                return bool(ts.is_point_in_water(x, y, skip_osm=skip_osm))
            except TypeError:
                return bool(ts.is_point_in_water(x, y))
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name:
            from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
            if preset_has_water_areas(preset_name):
                return bool(preset_is_point_in_water(
                    preset_name, x, y, game_map, skip_osm=skip_osm,
                ))
        return False

    p1_w = _in_water(x1, y1)
    p2_w = _in_water(x2, y2)
    if p1_w and p2_w:
        return True
    if ts and hasattr(ts, 'is_segment_entirely_water') and not skip_osm:
        try:
            if ts.is_segment_entirely_water(x1, y1, x2, y2, skip_osm=False):
                return True
        except TypeError:
            if ts.is_segment_entirely_water(x1, y1, x2, y2):
                return True
    mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
    if not p1_w and not p2_w and _in_water(mid_x, mid_y):
        return True
    return False


def _ferry_point_near_water(game_map: Map, x: float, y: float, max_distance_m: float = 60.0) -> bool:
    """True when a ferry terminal may be placed: on water or within max_distance_m of water."""
    import math

    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import preset_has_water_areas, preset_is_point_in_water, get_map_bounds
        if preset_has_water_areas(preset_name):
            if preset_is_point_in_water(preset_name, x, y, game_map, skip_osm=True):
                return True
            bounds = get_map_bounds(preset_name)
            h = max(1, int(getattr(game_map, 'height', 50) or 50))
            w = max(1, int(getattr(game_map, 'width', 50) or 50))
            if bounds and 'min_lat' in bounds:
                lat_span = bounds['max_lat'] - bounds['min_lat']
                lon_span = bounds['max_lon'] - bounds['min_lon']
                mid_lat = bounds['min_lat'] + (float(y) / h) * lat_span
                m_per_lat = 111_000.0
                m_per_lon = 111_000.0 * max(0.01, math.cos(math.radians(mid_lat)))
                dlat = (max_distance_m / m_per_lat) / lat_span * h
                dlon = (max_distance_m / m_per_lon) / lon_span * w
                for k in range(10):
                    ang = 2.0 * math.pi * k / 10.0
                    sx = float(x) + dlon * math.cos(ang)
                    sy = float(y) + dlat * math.sin(ang)
                    if preset_is_point_in_water(preset_name, sx, sy, game_map, skip_osm=True):
                        return True
            return False
    ts = getattr(game_map, 'tile_system', None)
    if ts:
        try:
            if ts.is_point_in_water(x, y, skip_osm=False):
                return True
        except TypeError:
            if ts.is_point_in_water(x, y):
                return True
        if hasattr(ts, 'is_point_near_water'):
            return bool(ts.is_point_near_water(x, y, max_distance_m, skip_osm=False))
    return False


def _ferry_segment_crosses_open_water(
    game_map: Map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    min_fraction: float = 0.72,
) -> bool:
    """True when the interior of a segment is mostly open water (single ferry geometry check)."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
        if preset_has_water_areas(preset_name):
            hits = 0
            total = 0
            end_trim = 0.12
            for k in range(1, 11):
                t = end_trim + (1.0 - 2.0 * end_trim) * (k / 10.0)
                sx = x1 + t * (x2 - x1)
                sy = y1 + t * (y2 - y1)
                total += 1
                if preset_is_point_in_water(preset_name, sx, sy, game_map, skip_osm=True):
                    hits += 1
            if total > 0 and (hits / total) >= min_fraction:
                return True
    ts = getattr(game_map, 'tile_system', None)
    if ts and hasattr(ts, 'ferry_corridor_interior_mostly_water'):
        return bool(ts.ferry_corridor_interior_mostly_water(
            x1, y1, x2, y2, skip_osm=False, min_fraction=min_fraction,
        ))
    return False


def _ai_land_straight_limit_applies(game_map: Map) -> bool:
    """~1 km straight land limit applies only on presets with street routing (not grid chord maps)."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return False
    from map_preset_loader import preset_has_road_support
    return bool(preset_has_road_support(preset_name))


def _ai_land_straight_segment_exceeds_limit(
    game_map: Map, x1: float, y1: float, x2: float, y2: float,
    *, skip_osm: bool = False, planning_only: bool = False, is_ferry_line: bool = False,
    current_year: int = None,
) -> bool:
    """True when a straight land chord violates street rules (ferries and water exempt).

    Buses use _route_has_off_street_bus_segment instead. Other modes allow short off-street
    connectors up to 60m when not longer than adjacent on-street segments (route-level check).
    """
    if is_ferry_line:
        return False
    if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
        return False
    if current_year is not None and _route_land_segment_follows_street(
        game_map, x1, y1, x2, y2, int(current_year), skip_osm=skip_osm,
    ):
        return False
    from game_presets import AI_MAX_STRAIGHT_LAND_SEGMENT_GRID

    dist = _grid_segment_length(x1, y1, x2, y2)
    if dist <= float(AI_MAX_STRAIGHT_LAND_SEGMENT_GRID):
        return False
    if planning_only:
        return True
    return True


def _bus_land_segment_follows_street(
    game_map: Map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when a bus segment stays on the street network (water crossings exempt)."""
    if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
        return True
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return False
    from map_preset_loader import get_preset_bus_segment_follows_street, preset_has_road_support

    if not preset_has_road_support(preset_name):
        return False
    return get_preset_bus_segment_follows_street(
        preset_name, game_map, x1, y1, x2, y2, int(current_year),
    )


def _route_has_off_street_bus_segment(
    game_map: Map, points: list, *, skip_osm: bool = False, current_year: int = None,
) -> bool:
    """True when a bus route leaves the street network on land (beyond short connectors)."""
    if not points or len(points) < 2:
        return False
    cy = int(current_year) if current_year is not None else 1830
    for i in range(len(points) - 1):
        x1, y1 = float(points[i][0]), float(points[i][1])
        x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
            continue
        if _bus_land_segment_follows_street(
            game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
        ):
            continue
        if _bus_connector_valid_at_route_index(
            game_map, points, i, cy, skip_osm=skip_osm,
        ):
            continue
    return True
    return False


def _ai_route_has_overlong_land_straight_segment(
    game_map: Map, points: list, *, skip_osm: bool = False, planning_only: bool = False,
    is_ferry_line: bool = False, is_bus_line: bool = False, current_year: int = None,
) -> bool:
    if is_ferry_line or not points or len(points) < 2:
        return False
    if is_bus_line:
        return _route_has_off_street_bus_segment(
            game_map, points, skip_osm=skip_osm, current_year=current_year,
        )
    cy = int(current_year) if current_year is not None else 1830
    for i in range(len(points) - 1):
        x1, y1 = float(points[i][0]), float(points[i][1])
        x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
        if _ai_land_straight_segment_exceeds_limit(
            game_map, x1, y1, x2, y2, skip_osm=skip_osm, planning_only=planning_only,
            is_ferry_line=is_ferry_line, current_year=current_year,
        ):
            return True
        if (
            not _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm)
            and not _route_land_segment_follows_street(
                game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
            )
            and not _straight_connector_valid_at_route_index(
                game_map, points, i, cy, skip_osm=skip_osm,
            )
        ):
            return True
    if _ai_route_has_excessive_inter_street_connectors(
        game_map, points, current_year=cy, skip_osm=skip_osm,
        ):
            return True
    return False


def _land_segment_is_short_straight_connector(x1: float, y1: float, x2: float, y2: float) -> bool:
    """True for straight land chords up to the max off-street connector length."""
    from game_presets import AI_MAX_STRAIGHT_LAND_SEGMENT_GRID

    return _grid_segment_length(x1, y1, x2, y2) <= float(AI_MAX_STRAIGHT_LAND_SEGMENT_GRID) + 1e-9


def _straight_connector_valid_at_route_index(
    game_map: Map,
    points: list,
    seg_idx: int,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when a land straight connector is not longer than adjacent on-street segments (water exempt)."""
    if not points or seg_idx < 0 or seg_idx >= len(points) - 1:
        return False
    x1, y1 = float(points[seg_idx][0]), float(points[seg_idx][1])
    x2, y2 = float(points[seg_idx + 1][0]), float(points[seg_idx + 1][1])
    if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
        return True
    cy = int(current_year)
    if _route_land_segment_follows_street(game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm):
        return True
    conn_len = _grid_segment_length(x1, y1, x2, y2)
    street_limits = []
    if seg_idx > 0:
        px1, py1 = float(points[seg_idx - 1][0]), float(points[seg_idx - 1][1])
        if _route_land_segment_follows_street(game_map, px1, py1, x1, y1, cy, skip_osm=skip_osm):
            street_limits.append(_grid_segment_length(px1, py1, x1, y1))
    if seg_idx < len(points) - 2:
        nx2, ny2 = float(points[seg_idx + 2][0]), float(points[seg_idx + 2][1])
        if _route_land_segment_follows_street(game_map, x2, y2, nx2, ny2, cy, skip_osm=skip_osm):
            street_limits.append(_grid_segment_length(x2, y2, nx2, ny2))
    if not street_limits:
        return False
    return all(conn_len <= lim + 1e-9 for lim in street_limits)


def _segment_on_bridge_or_tunnel(
    game_map: Map, x1: float, y1: float, x2: float, y2: float, current_year: int,
) -> bool:
    """True when a segment lies on a historic bridge or tunnel (street elevation allowed over water)."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return False
    from map_preset_loader import get_preset_is_point_on_bridge
    cy = int(current_year)
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        if get_preset_is_point_on_bridge(preset_name, px, py, cy, game_map):
            return True
    return False


def _bus_connector_valid_at_route_index(
    game_map: Map,
    points: list,
    seg_idx: int,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when a bus off-street segment is a short connector between on-street segments."""
    from game_presets import AI_BUS_MAX_CONNECTOR_GRID

    if not points or seg_idx < 0 or seg_idx >= len(points) - 1:
        return False
    x1, y1 = float(points[seg_idx][0]), float(points[seg_idx][1])
    x2, y2 = float(points[seg_idx + 1][0]), float(points[seg_idx + 1][1])
    if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
        return False
    cy = int(current_year)
    if _route_land_segment_follows_street(game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm):
        return False
    conn_len = _grid_segment_length(x1, y1, x2, y2)
    if conn_len > float(AI_BUS_MAX_CONNECTOR_GRID) + 1e-9:
        return False
    has_prev_street = False
    has_next_street = False
    if seg_idx > 0:
        px1, py1 = float(points[seg_idx - 1][0]), float(points[seg_idx - 1][1])
        has_prev_street = _route_land_segment_follows_street(
            game_map, px1, py1, x1, y1, cy, skip_osm=skip_osm,
        )
    if seg_idx < len(points) - 2:
        nx2, ny2 = float(points[seg_idx + 2][0]), float(points[seg_idx + 2][1])
        has_next_street = _route_land_segment_follows_street(
            game_map, x2, y2, nx2, ny2, cy, skip_osm=skip_osm,
        )
    return has_prev_street and has_next_street


_STREET_ALIGNED_ELEVATIONS = frozenset({'street', 'above_ground'})
_WATER_SEGMENT_ELEVATIONS = ('above_ground', 'underground')
_LAND_SEGMENT_ELEVATIONS = ('street', 'above_ground', 'underground')


def _segment_elevation_requires_street_alignment(elevation: str) -> bool:
    """True when a segment must follow streets (not free-form chords on land)."""
    return (elevation or 'street').strip().lower() in _STREET_ALIGNED_ELEVATIONS


def _normalize_route_elevation_levels(
    points: list,
    elevation_levels: list = None,
    *,
    is_bus_line: bool = False,
    is_ferry_line: bool = False,
    cost_info: dict = None,
) -> list:
    """Return one elevation per segment, padding with mode-appropriate defaults."""
    n = max(0, len(points) - 1)
    if n <= 0:
        return []
    elev = list(elevation_levels or [])
    if not elev and cost_info:
        elev = list(cost_info.get('elevation_levels') or [])
    default = 'water' if is_ferry_line else ('street' if is_bus_line else 'above_ground')
    if len(elev) < n:
        elev = elev + [default] * (n - len(elev))
    return elev[:n]


def _merged_route_elevation_levels(
    line,
    merged_points: list,
    additional_points: list,
    extend_from_end: bool,
    extension_elevation_levels: list = None,
    *,
    is_bus_line: bool = False,
) -> list:
    """Elevation per segment for a line plus extension (for street-alignment validation)."""
    n = max(0, len(merged_points) - 1)
    if n <= 0:
        return []
    default = 'street' if is_bus_line else 'above_ground'
    old_route = list(line.get_route() or []) if line and hasattr(line, 'get_route') else []
    n_old = max(0, len(old_route) - 1)
    old_elevs = list(getattr(line, 'elevation_levels', None) or [])
    if len(old_elevs) < n_old:
        old_elevs = old_elevs + [default] * (n_old - len(old_elevs))
    old_elevs = old_elevs[:n_old]
    n_new = max(0, n - n_old)
    ext_elevs = list(extension_elevation_levels or [])
    if len(ext_elevs) < n_new:
        ext_elevs = ext_elevs + [default] * (n_new - len(ext_elevs))
    ext_elevs = ext_elevs[:n_new]
    if extend_from_end:
        combined = old_elevs + ext_elevs
    else:
        combined = ext_elevs + old_elevs
    if len(combined) < n:
        combined = combined + [default] * (n - len(combined))
    return combined[:n]


def _route_land_segment_follows_street(
    game_map: Map, x1: float, y1: float, x2: float, y2: float, current_year: int,
    *, skip_osm: bool = False,
) -> bool:
    """True when a land segment lies on streets (endpoints and midpoint on road or bridge)."""
    if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
        return True
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return True
    from map_preset_loader import (
        get_preset_is_point_on_bridge,
        get_preset_is_point_on_road,
        get_preset_point_near_street_centerline,
        preset_has_road_support,
    )
    if not preset_has_road_support(preset_name):
        return True

    cy = int(current_year)

    def _on_street(x: float, y: float) -> bool:
        if get_preset_is_point_on_road(preset_name, x, y, cy, game_map):
            return True
        return bool(get_preset_is_point_on_bridge(preset_name, x, y, cy, game_map))

    def _midpoint_on_street() -> bool:
        mid_x = x1 + 0.5 * (x2 - x1)
        mid_y = y1 + 0.5 * (y2 - y1)
        if _on_street(mid_x, mid_y):
            return True
        return get_preset_point_near_street_centerline(preset_name, game_map, mid_x, mid_y, cy)

    for t in (0.0, 1.0):
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        if not _on_street(px, py):
            return False
    if not _midpoint_on_street():
        return False

    seg_len = _grid_segment_length(x1, y1, x2, y2)
    from game_presets import AI_MAX_STRAIGHT_LAND_SEGMENT_GRID
    if seg_len > float(AI_MAX_STRAIGHT_LAND_SEGMENT_GRID):
        for i in range(1, 8):
            if i == 4:
                continue
            t = i / 8.0
            px = x1 + t * (x2 - x1)
            py = y1 + t * (y2 - y1)
            if not _on_street(px, py):
                return False
    return True


def _grid_segment_length(x1: float, y1: float, x2: float, y2: float) -> float:
    import math
    return math.hypot(float(x2) - float(x1), float(y2) - float(y1))


def _off_street_segment_cost_multiplier(
    game_map: Map, x1: float, y1: float, x2: float, y2: float, current_year: int,
    *, is_bus_line: bool = False,
) -> float:
    from game_presets import (
        OFF_STREET_SEGMENT_COST_MULTIPLIER,
        AI_OFF_STREET_FREE_SEGMENT_GRID,
        AI_BUS_CONNECTOR_COST_MULTIPLIER,
        AI_BUS_MAX_CONNECTOR_GRID,
    )

    if _route_land_segment_follows_street(game_map, x1, y1, x2, y2, current_year):
        return 1.0
    seg_len = _grid_segment_length(x1, y1, x2, y2)
    if is_bus_line and seg_len <= float(AI_BUS_MAX_CONNECTOR_GRID) + 1e-9:
        return float(AI_BUS_CONNECTOR_COST_MULTIPLIER)
    if seg_len <= float(AI_OFF_STREET_FREE_SEGMENT_GRID):
        return 1.0
    return float(OFF_STREET_SEGMENT_COST_MULTIPLIER)


def _ai_route_off_street_cost_multiplier(
    game_map: Map, route_points: list, current_year: int, *, is_bus_line: bool = False,
) -> float:
    """>1 when the route leaves streets (matches build-cost penalty per segment)."""
    if not route_points or len(route_points) < 2:
        return 1.0
    mult = 1.0
    cy = int(current_year)
    for i in range(len(route_points) - 1):
        x1, y1 = float(route_points[i][0]), float(route_points[i][1])
        x2, y2 = float(route_points[i + 1][0]), float(route_points[i + 1][1])
        mult *= _off_street_segment_cost_multiplier(
            game_map, x1, y1, x2, y2, cy, is_bus_line=is_bus_line,
        )
    return float(mult)


def _route_off_street_length_fraction(
    game_map: Map, route_points: list, current_year: int,
) -> float:
    """Share of route length on off-street land segments (water is never off-street).

    Off-street = any non-water segment that does not follow a street/road polyline.
    Short connectors are exempt from the maintenance surcharge only.
    """
    from game_presets import AI_OFF_STREET_FREE_SEGMENT_GRID

    if not route_points or len(route_points) < 2:
        return 0.0
    total_len = 0.0
    penalized_len = 0.0
    free_len = float(AI_OFF_STREET_FREE_SEGMENT_GRID)
    cy = int(current_year)
    for i in range(len(route_points) - 1):
        x1, y1 = float(route_points[i][0]), float(route_points[i][1])
        x2, y2 = float(route_points[i + 1][0]), float(route_points[i + 1][1])
        seg_len = _grid_segment_length(x1, y1, x2, y2)
        total_len += seg_len
        # Water is not off-street (ferries / bridges over water).
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=True):
            continue
        if seg_len <= free_len:
            continue
        if not _route_land_segment_follows_street(game_map, x1, y1, x2, y2, cy, skip_osm=True):
            penalized_len += seg_len
    if total_len <= 0.0:
        return 0.0
    return penalized_len / total_len


def _route_street_water_and_connector_lengths(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> tuple:
    """Return (street_or_water_len, off_street_len, total_len) for an AI route.

    Off-street = any non-water segment not on a street/road polyline (no free-length exemption).
    """
    if not route_points or len(route_points) < 2:
        return 0.0, 0.0, 0.0
    street_water_len = 0.0
    off_street_len = 0.0
    total_len = 0.0
    cy = int(current_year)
    for i in range(len(route_points) - 1):
        x1, y1 = float(route_points[i][0]), float(route_points[i][1])
        x2, y2 = float(route_points[i + 1][0]), float(route_points[i + 1][1])
        seg_len = _grid_segment_length(x1, y1, x2, y2)
        total_len += seg_len
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
            street_water_len += seg_len
        elif _route_land_segment_follows_street(
            game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
        ):
            street_water_len += seg_len
        else:
            # Off-street: land segment not designed to follow a street.
            off_street_len += seg_len
    return street_water_len, off_street_len, total_len


def _route_osm_polyline_water_and_connector_lengths(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> tuple:
    """Return (osm_polyline_len, water_len, connector_len, total_len) for an AI rail route.

    osm_polyline_len counts land segments that hug OSM road centerlines (not chords between
    separate polylines). water_len counts water crossings. connector_len is everything else.
    """
    if not route_points or len(route_points) < 2:
        return 0.0, 0.0, 0.0, 0.0
    osm_polyline_len = 0.0
    water_len = 0.0
    connector_len = 0.0
    total_len = 0.0
    cy = int(current_year)
    preset_name = getattr(game_map, 'map_preset_name', None)
    use_osm_polyline = bool(preset_name)
    for i in range(len(route_points) - 1):
        x1, y1 = float(route_points[i][0]), float(route_points[i][1])
        x2, y2 = float(route_points[i + 1][0]), float(route_points[i + 1][1])
        seg_len = _grid_segment_length(x1, y1, x2, y2)
        total_len += seg_len
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
            water_len += seg_len
        elif use_osm_polyline:
            from map_preset_loader import preset_has_road_support
            from map_presets_nyc_road_utils import route_land_segment_on_osm_polyline

            if preset_has_road_support(preset_name) and route_land_segment_on_osm_polyline(
                game_map, x1, y1, x2, y2, cy,
            ):
                osm_polyline_len += seg_len
            elif _route_land_segment_follows_street(
                game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
            ):
                connector_len += seg_len
            else:
                connector_len += seg_len
        elif _route_land_segment_follows_street(
            game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
        ):
            osm_polyline_len += seg_len
        else:
            connector_len += seg_len
    return osm_polyline_len, water_len, connector_len, total_len


def _ai_route_majority_on_osm_polylines(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when strictly more than 50% of route length is on OSM polylines or over water."""
    if not route_points or len(route_points) < 2:
        return True
    osm_len, water_len, _connector_len, total_len = _route_osm_polyline_water_and_connector_lengths(
        game_map, route_points, current_year, skip_osm=skip_osm,
    )
    if total_len <= 0.0:
        return True
    return (osm_len + water_len) / total_len > 0.5 + 1e-12


def _route_inter_street_connector_length_fraction(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> float:
    """Share of route length that is off-street (non-water, not on a street polyline)."""
    _, off_street_len, total_len = _route_street_water_and_connector_lengths(
        game_map, route_points, current_year, skip_osm=skip_osm,
    )
    if total_len <= 0.0:
        return 0.0
    return off_street_len / total_len


def _ai_route_street_water_length_exceeds_connectors(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when strictly more than half of route length is on streets or water."""
    return _ai_route_majority_on_streets_and_water(
        game_map, route_points, current_year, skip_osm=skip_osm,
    )


def _ai_route_majority_on_streets_and_water(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
    max_connector_fraction: float = None,
) -> bool:
    """True when strictly more than 50% of route length is on streets or over water.

    Off-street for AI = any non-water segment not on a street/road polyline (no free exemption).
    """
    if not route_points or len(route_points) < 2:
        return True
    street_water_len, off_street_len, total_len = _route_street_water_and_connector_lengths(
        game_map, route_points, current_year, skip_osm=skip_osm,
    )
    if total_len <= 0.0:
        return True
    # Strictly more than half on street or water.
    if street_water_len / total_len <= 0.5 + 1e-12:
        return False
    if max_connector_fraction is not None:
        return (off_street_len / total_len) < float(max_connector_fraction) - 1e-9
    return True


def _ai_route_has_excessive_inter_street_connectors(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    return not _ai_route_majority_on_streets_and_water(
        game_map, route_points, current_year, skip_osm=skip_osm,
    )


def _ai_route_street_water_requirement_error(
    game_map: Map,
    route_points: list,
    current_year: int,
    *,
    skip_osm: bool = True,
    for_extension: bool = False,
) -> str | None:
    """Error message when an AI rail route fails street/water or OSM-polyline rules; None if ok."""
    if not _ai_route_majority_on_streets_and_water(
        game_map, route_points, current_year, skip_osm=skip_osm,
    ):
        street_water_len, off_street_len, total_len = _route_street_water_and_connector_lengths(
            game_map, route_points, current_year, skip_osm=skip_osm,
        )
        pct = (100.0 * street_water_len / total_len) if total_len > 0 else 0.0
        kind = "Extensions" if for_extension else "AI lines"
        return (
            f"{kind} must be more than 50% on streets or water "
            f"(off-street = any non-water segment not on a street). "
            f"This route is only {pct:.0f}% street/water "
            f"({street_water_len:.2f} on-street/water vs {off_street_len:.2f} off-street)."
        )
    if not _ai_route_majority_on_osm_polylines(
        game_map, route_points, current_year, skip_osm=skip_osm,
    ):
        osm_len, water_len, connector_len, total_len = _route_osm_polyline_water_and_connector_lengths(
            game_map, route_points, current_year, skip_osm=skip_osm,
        )
        qualifying = osm_len + water_len
        pct = (100.0 * qualifying / total_len) if total_len > 0 else 0.0
        kind = "Extensions" if for_extension else "AI rail lines"
        return (
            f"{kind} must be more than 50% on OSM road polylines or water "
            f"(straight connectors between polylines do not count). "
            f"This route is only {pct:.0f}% on OSM polylines/water "
            f"({osm_len:.2f} on OSM polylines, {water_len:.2f} water, "
            f"{connector_len:.2f} connectors)."
        )
    return None


def calc_line_maintenance_with_off_street_surcharge(
    game_map: Map, line, current_year: int,
) -> int:
    """Line maintenance including off-street segment surcharge (short connectors exempt)."""
    if not hasattr(line, 'calc_maitnance'):
        return 0
    base = line.calc_maitnance(current_year)
    route = line.get_route() if hasattr(line, 'get_route') else []
    if route and len(route) >= 2 and game_map is not None:
        from game_presets import OFF_STREET_MAINTENANCE_COST_BONUS_PER_FRACTION
        frac = _route_off_street_length_fraction(game_map, route, current_year)
        if frac > 0:
            base = int(round(base * (1.0 + float(OFF_STREET_MAINTENANCE_COST_BONUS_PER_FRACTION) * frac)))
    return int(base)


def _ai_route_fully_follows_streets(
    game_map: Map, route_points: list, current_year: int, *, skip_osm: bool = False,
) -> bool:
    if not route_points or len(route_points) < 2:
        return True
    cy = int(current_year)
    for i in range(len(route_points) - 1):
        x1, y1 = float(route_points[i][0]), float(route_points[i][1])
        x2, y2 = float(route_points[i + 1][0]), float(route_points[i + 1][1])
        if not _route_land_segment_follows_street(
            game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
        ):
            return False
    return True


def _enforce_route_street_rules_or_raise(
    game_map: Map,
    points: list,
    *,
    current_year: int,
    is_bus_line: bool = False,
    is_ferry_line: bool = False,
    for_extension: bool = False,
    skip_osm: bool = False,
    for_ai: bool = False,
) -> None:
    """Raise ValueError when a land route leaves the street network."""
    if is_ferry_line or not points or len(points) < 2:
        return
    cy = int(current_year)
    if is_bus_line:
        if _route_has_off_street_bus_segment(game_map, points, skip_osm=skip_osm, current_year=cy):
            msg = (
                "Bus extensions must stay on streets. Turn onto intersecting streets "
                "at corners — no off-street shortcuts."
                if for_extension
                else "Bus routes must stay on streets. Follow road polylines and turn "
                "onto intersecting streets at corners — no off-street shortcuts."
            )
            raise ValueError(msg)
        return
    if not for_ai or not _ai_land_straight_limit_applies(game_map):
        return
    majority_err = _ai_route_street_water_requirement_error(
        game_map, points, cy, skip_osm=skip_osm, for_extension=for_extension,
    )
    if majority_err:
        raise ValueError(majority_err)
    if _ai_route_has_overlong_land_straight_segment(
        game_map, points, skip_osm=skip_osm, current_year=cy, is_bus_line=False,
    ):
        msg = (
            "Extensions: off-street connectors cannot exceed adjacent on-street segment lengths."
            if for_extension
            else "Off-street connectors cannot exceed adjacent on-street segment lengths."
        )
        raise ValueError(msg)


def _normalize_build_point(pt) -> tuple[float, float]:
    x, y = pt[0], pt[1]
    if isinstance(x, (list, tuple)):
        x, y = float(x[0]), float(x[1])
    elif hasattr(x, 'x'):
        x, y = float(x.x), float(x.y)
    else:
        x, y = float(x), float(y)
    return x, y


def _assert_points_in_buildable_area(game_map: Map, points: list) -> None:
    if not points or not hasattr(game_map, 'is_point_in_buildable_area'):
        return
    for i, pt in enumerate(points):
        x, y = _normalize_build_point(pt)
        if not game_map.is_point_in_buildable_area(x, y):
            raise ValueError(
                f"Point {i + 1} at ({x:.1f}, {y:.1f}) is outside the buildable area. "
                "Lines can only be built within the play area."
            )


def _validate_land_line_route_for_build(
    game_map: Map,
    route_points: list,
    *,
    current_year: int,
    is_bus_line: bool,
    is_ferry_line: bool,
    player: Player = None,
    ai_fast: bool = False,
    skip_osm: bool = False,
    for_extension: bool = False,
    elevation_levels: list = None,
    cost_info: dict = None,
    use_fast_path: bool = False,
    human_cost_previewed: bool = False,
) -> dict:
    """Shared geometry, street, tile, and land-route checks for create and extend."""
    if is_ferry_line or not route_points or len(route_points) < 2:
        return {'success': True}

    if not ai_fast or is_bus_line:
        _validate_route_geometry(
            route_points, game_map, is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
            skip_osm=skip_osm,
        )
    if player is not None and getattr(player, 'is_ai', False) and _ai_route_has_backtracking(route_points, game_map):
        raise ValueError("This route doubles back on itself.")
    route_for_ai = player is not None and getattr(player, 'is_ai', False)
    if not ai_fast or is_bus_line:
        _enforce_route_street_rules_or_raise(
            game_map, route_points, current_year=current_year,
            is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
            for_extension=for_extension, skip_osm=skip_osm, for_ai=route_for_ai,
        )
    if player is not None and getattr(player, 'is_ai', False) and not is_bus_line:
        majority_err = _ai_route_street_water_requirement_error(
            game_map, route_points, current_year, skip_osm=True, for_extension=for_extension,
        )
        if majority_err:
            raise ValueError(majority_err)

    if ai_fast and not is_bus_line:
        return {'success': True}
    if use_fast_path:
        return {'success': True}

    if game_map.tile_system and not is_bus_line:
        if human_cost_previewed:
            game_map.tile_system._preset_only_water = True
        elif hasattr(game_map.tile_system, 'set_water_check_deadline'):
            game_map.tile_system.set_water_check_deadline(5.0)
    try:
        if (
            game_map.tile_system
            and not is_bus_line
            and not human_cost_previewed
        ):
            game_map.tile_system.pre_cache_route_tiles(route_points, proximity_buffer_m=0.0)

        route_elevs = _normalize_route_elevation_levels(
            route_points,
            elevation_levels,
            is_bus_line=is_bus_line,
            is_ferry_line=False,
            cost_info=cost_info,
        )
        return _validate_route_streets_on_land(
            route_points,
            game_map,
            current_year,
            is_bus_line=is_bus_line,
            is_ferry_line=False,
            elevation_levels=route_elevs,
            skip_osm=skip_osm,
        )
    finally:
        if game_map.tile_system:
            if hasattr(game_map.tile_system, '_preset_only_water'):
                game_map.tile_system._preset_only_water = False
            if hasattr(game_map.tile_system, 'clear_water_check_deadline'):
                game_map.tile_system.clear_water_check_deadline()


def _validate_route_streets_on_land(
    points: list,
    game_map: Map,
    current_year: int,
    *,
    is_bus_line: bool = False,
    is_ferry_line: bool = False,
    elevation_levels: list = None,
    skip_osm: bool = False,
) -> dict:
    """Land segments at street/above_ground must follow streets; underground may tunnel freely."""
    if is_ferry_line or not points:
        return {'success': True}
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return {'success': True}
    from map_preset_loader import preset_has_road_support, get_preset_is_point_on_road
    if not preset_has_road_support(preset_name):
        return {'success': True}

    ts = getattr(game_map, 'tile_system', None)
    use_osm = bool(getattr(game_map, 'use_osm', False))
    kind = 'Bus' if is_bus_line else 'Train'
    default_elev = 'street' if is_bus_line else 'above_ground'
    seg_elevs = _normalize_route_elevation_levels(
        points, elevation_levels, is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
    )

    def _segment_requires_streets(seg_idx: int) -> bool:
        elev = seg_elevs[seg_idx] if seg_idx < len(seg_elevs) else default_elev
        return _segment_elevation_requires_street_alignment(elev)

    def _point_requires_street_alignment(pt_idx: int) -> bool:
        if pt_idx <= 0:
            return _segment_requires_streets(0)
        if pt_idx >= len(points) - 1:
            return _segment_requires_streets(len(points) - 2)
        return _segment_requires_streets(pt_idx - 1) or _segment_requires_streets(pt_idx)

    if len(points) == 1:
        x, y = points[0]
        if not _point_requires_street_alignment(0):
            return {'success': True}
        if not get_preset_is_point_on_road(preset_name, x, y, current_year, game_map):
            return {
                'success': False,
                'error_message': (
                    f"{kind} routes must be on streets. Point at ({x:.1f}, {y:.1f}) is not on a road."
                ),
            }
        return {'success': True}

    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if not _segment_requires_streets(i):
            continue
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
            continue
        if is_bus_line:
            if _bus_land_segment_follows_street(
                game_map, x1, y1, x2, y2, int(current_year),
            ):
                continue
            if _bus_connector_valid_at_route_index(
                game_map, points, i, int(current_year), skip_osm=skip_osm,
            ):
                continue
            return {
                'success': False,
                'error_message': (
                    "Bus routes must stay on streets. They may use short connectors (≤2 m) "
                    "between street segments but cannot cut across blocks. Segment "
                    f"{i + 1} from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) leaves the street network."
                ),
            }
            continue
        if not _route_land_segment_follows_street(
            game_map, x1, y1, x2, y2, int(current_year),
        ):
            if (
                _straight_connector_valid_at_route_index(
                    game_map, points, i, int(current_year),
                )
            ):
                continue
            return {
                'success': False,
                'error_message': (
                    f"{kind} routes at street or above-ground level must follow streets on land. "
                    f"Segment {i + 1} from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) is not on a road."
                ),
            }

    return {'success': True}


def _segment_overlaps_existing_routes(p1, p2, existing_routes: list, tolerance: float) -> bool:
    """True if segment (p1,p2) is within tolerance of any segment of any route."""
    if not existing_routes:
        return False
    ax, ay = float(p1[0]), float(p1[1])
    bx, by = float(p2[0]), float(p2[1])

    def pt_seg(px, py, sx, sy, tx, ty):
        dx, dy = tx - sx, ty - sy
        d2 = dx * dx + dy * dy
        if d2 < 1e-20:
            return math.hypot(px - sx, py - sy)
        t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / d2))
        return math.hypot(px - (sx + t * dx), py - (sy + t * dy))

    for route in existing_routes:
        if not route or len(route) < 2:
            continue
        for k in range(len(route) - 1):
            c, d = route[k], route[k + 1]
            cx, cy = float(c[0]), float(c[1])
            dx, dy = float(d[0]), float(d[1])
            if (
                pt_seg(ax, ay, cx, cy, dx, dy) <= tolerance
                or pt_seg(bx, by, cx, cy, dx, dy) <= tolerance
                or pt_seg(cx, cy, ax, ay, bx, by) <= tolerance
                or pt_seg(dx, dy, ax, ay, bx, by) <= tolerance
            ):
                return True
    return False


def _collect_routes_for_overlap_validation(game_map, build_queue: list, exclude_line_name: str) -> list:
    """Completed and queued line routes for overlap checks (excluding one in-progress line name)."""
    routes = []
    raw = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else None
    map_lines = raw if isinstance(raw, dict) else {}
    for _name, line in map_lines.items():
        r = line.get_route() if hasattr(line, 'get_route') else []
        if len(r) >= 2:
            routes.append(r)
    exclude = (exclude_line_name or '').strip().casefold()
    chunks_by_key: dict = {}
    for build in build_queue or []:
        if build.get('type') != 'line':
            continue
        data = build.get('data') or {}
        name = (data.get('name') or '').strip()
        if exclude and name.casefold() == exclude:
            continue
        pts = data.get('points') or []
        if len(pts) < 2:
            continue
        player = build.get('player')
        key = (id(player) if player is not None else 0, name)
        seg_idx = int(data.get('segment_index', 0) or 0)
        chunks_by_key.setdefault(key, []).append((seg_idx, list(pts)))
    for _key, chunks in chunks_by_key.items():
        chunks.sort(key=lambda t: t[0])
        merged = []
        for _si, pts in chunks:
            if not merged:
                merged = list(pts)
            else:
                merged.extend(pts[1:])
        if len(merged) >= 2:
            routes.append(merged)
    return routes


def _extension_attach_from_closest_end(route: list, click_point) -> bool:
    """True when an extension should connect at the route end (vs route start)."""
    import math
    if not route:
        return True
    cx, cy = _normalize_route_point(click_point)
    sx, sy = _normalize_route_point(route[0])
    ex, ey = _normalize_route_point(route[-1])
    start_d = math.hypot(cx - sx, cy - sy)
    end_d = math.hypot(cx - ex, cy - ey)
    return end_d <= start_d


def _ai_merged_line_route(existing_route: list, additional_points: list, extend_from_end: bool) -> list:
    """Full polyline after an extension (for backtracking / street validation).

    Segments are: existing_end→first new point, then between each new point in order
    (or last new point→existing_start when extending from the beginning).
    """
    if not existing_route:
        return list(additional_points or [])
    if not additional_points:
        return list(existing_route)
    if extend_from_end:
        return list(existing_route) + list(additional_points)
    return list(additional_points) + list(existing_route)


def _normalize_route_point(p) -> tuple:
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return (float(p[0]), float(p[1]))
    if hasattr(p, 'x') and hasattr(p, 'y'):
        return (float(p.x), float(p.y))
    return (float(p), float(p))


def _polyline_metrics(points: list) -> tuple:
    """Return (total_length, per_segment_lengths) for a route polyline."""
    import math
    if not points or len(points) < 2:
        return 0.0, []
    lengths = []
    total = 0.0
    for i in range(len(points) - 1):
        x1, y1 = _normalize_route_point(points[i])
        x2, y2 = _normalize_route_point(points[i + 1])
        seg_len = math.hypot(x2 - x1, y2 - y1)
        lengths.append(seg_len)
        total += seg_len
    return total, lengths


def _line_build_work_units(total_length: float) -> float:
    from game_presets import LINE_BUILD_SPEED_MULTIPLIER
    return max(0.25, float(total_length) + 0.25) / float(LINE_BUILD_SPEED_MULTIPLIER)


def _batch_polyline_segments_for_build(
    points: list,
    elevation_levels: list = None,
    *,
    batch_size: int = None,
    max_batch_length: float = None,
) -> list:
    """Group consecutive polyline segments into fewer build-queue jobs."""
    from game_presets import LINE_BUILD_SEGMENT_BATCH_SIZE, LINE_BUILD_SEGMENT_BATCH_MAX_LENGTH_GRID

    if not points or len(points) < 2:
        return []
    if batch_size is None:
        batch_size = int(LINE_BUILD_SEGMENT_BATCH_SIZE)
    if max_batch_length is None:
        max_batch_length = float(LINE_BUILD_SEGMENT_BATCH_MAX_LENGTH_GRID)
    batch_size = max(1, batch_size)
    max_batch_length = max(0.05, max_batch_length)

    n_segments = len(points) - 1
    _, seg_lengths = _polyline_metrics(points)
    batches = []
    seg_start = 0
    while seg_start < n_segments:
        seg_end = seg_start
        batch_len = 0.0
        count = 0
        while seg_end < n_segments and count < batch_size:
            next_len = seg_lengths[seg_end] if seg_end < len(seg_lengths) else 0.0
            if count > 0 and batch_len + next_len > max_batch_length:
                break
            batch_len += next_len
            seg_end += 1
            count += 1
        if seg_end == seg_start:
            seg_end = seg_start + 1
        batch_points = [_normalize_route_point(p) for p in points[seg_start:seg_end + 1]]
        seg_elev = None
        if elevation_levels:
            seg_elev = list(elevation_levels[seg_start:seg_end])
        batches.append({
            'points': batch_points,
            'segment_index': seg_start,
            'total_segments': n_segments,
            'elevation_levels': seg_elev,
        })
        seg_start = seg_end
    return batches


def _merge_route_with_completed_build_points(existing_route: list, build_points: list) -> list:
    """Append a completed line-build batch onto an existing partial route."""
    if not build_points:
        return list(existing_route or [])
    incoming = [_normalize_route_point(p) for p in build_points]
    route = [_normalize_route_point(p) for p in (existing_route or [])]
    if not route:
        return incoming
    last = route[-1]
    first = incoming[0]
    if abs(last[0] - first[0]) < 1e-9 and abs(last[1] - first[1]) < 1e-9:
        route.extend(incoming[1:])
    else:
        for p in incoming:
            if not route or route[-1] != p:
                route.append(p)
    return route

