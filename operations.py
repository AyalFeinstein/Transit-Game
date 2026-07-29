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

# Track sharing: rail segments that run within this distance of an existing line's track
# reuse the same physical tracks, so they are not charged full construction cost.
SHARED_TRACK_MAX_DISTANCE_M = 10.0
# Fraction of normal segment cost charged when a segment reuses an existing track
# (junctions/connections only — no new track laid).
SHARED_TRACK_COST_FACTOR = 0.1


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


def generate_players(player_names: list[str], starting_money: int = 10000, ai_player_indices: list[int] = None, ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, map_preset_name: str = None) -> list[Player]:
    """Generate players. ai_player_indices: list of player indices that are AI-controlled.
    ai_focus_per_player: optional dict mapping player_index -> focus for AI players. Unset indices get random focus.
    ai_difficulty_per_player: optional dict mapping player_index -> difficulty ('easy','medium','hard') for AI players."""
    import random
    from vehicle_presets import get_vehicle_registry
    from vehicle import Trainset, Railcar
    registry = get_vehicle_registry()
    
    all_locomotives = registry.get_locomotives_by_year()
    earliest_loco = None
    for loco, year in all_locomotives:
        if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
            earliest_loco = loco
            break
    
    earliest_railcar = registry.get_earliest_railcar()
    
    if not earliest_loco or not earliest_railcar:
        raise ValueError("No suitable vehicles found in registry (need a locomotive that can have railcars and at least one railcar)")
    
    from transit_company_names import get_transit_company_name
    ai_indices = set(ai_player_indices or [])
    players = []
    used_names = set()
    for i, name in enumerate(player_names):
        starting_trainsets = []
        for _ in range(3):
            cars = [Railcar("Standard Passenger", 0, earliest_railcar.capacity, earliest_railcar.maitnance_costs)]
            starting_trainset = Trainset(locomotive=earliest_loco, cars=cars)
            starting_trainsets.append(starting_trainset)
        is_ai = i in ai_indices
        default_name = f"Player{i + 1}"
        stripped_name = (name or "").strip()
        if is_ai:
            if stripped_name and stripped_name != default_name:
                name = stripped_name
            else:
                name = get_transit_company_name(used_names, map_preset_name=map_preset_name)
            focus = (ai_focus_per_player or {}).get(i)
            ai_focus = focus if focus in AI_FOCUSES else random.choice(AI_FOCUSES)
            from game_presets import AI_DEFAULT_DIFFICULTY
            diff = (ai_difficulty_per_player or {}).get(i)
            ai_difficulty = diff if diff in AI_DIFFICULTIES else AI_DEFAULT_DIFFICULTY
        else:
            ai_focus = None
            ai_difficulty = None
        used_names.add(name)
        player = Player(player_id=i, money=starting_money, trainsets=starting_trainsets, name=name, line_names=[], is_ai=is_ai, ai_focus=ai_focus, ai_difficulty=ai_difficulty)
        players.append(player)
    from player_colors import assign_color_indices_for_new_players
    assign_color_indices_for_new_players(players)
    return players

def setup_game(player_names: list[str], map_width: int = 100, map_height: int = 75, starting_money: Optional[int] = None, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, use_osm: bool = False, map_center_lat: float = None, map_center_lon: float = None, marketplace_years_visible: int = None, map_preset_name: str = None, line_build_speed_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, ai_player_indices: list[int] = None, ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, random_ai_spawn_enabled: bool = True, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, profit_margin: float = 1.0, random_ai_spawn_interval_years: int = 30, default_ai_difficulty_for_spawns: str = 'hard', ai_min_population: int = 100, game_seed: int = None, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None, war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None, ruleset_name: str = None, idle_human_benchmark_enabled: bool = False, ai_debug_notifications_enabled: bool = True) -> Game:
    """Create game. Optional overrides for game_presets (line build speed, bulk discount).
    ai_player_indices: list of player indices (0-based) that are AI-controlled.
    ai_focus_per_player: optional dict mapping player_index -> focus for AI players.
    ai_difficulty_per_player: optional dict mapping player_index -> difficulty for AI players.
    random_ai_spawn_enabled: if True, new AI players spawn randomly every 50-100 years.
    merge_enabled: if True, mergers are allowed (master switch).
    merge_ai_ai_enabled: if True, AIs can merge with other AIs (doomed-merge, etc.).
    merge_ai_human_enabled: if True, humans can propose merger with desperate AIs.
    merge_human_human_enabled: if True, humans can propose merger with other humans (both control merged company)."""
    import game_presets
    from game_presets import AI_OUTLAST_IDLE_STARTING_MONEY
    if starting_money is None:
        starting_money = STARTING_MONEY
    if not idle_human_benchmark_enabled and int(starting_money) == int(AI_OUTLAST_IDLE_STARTING_MONEY):
        idle_human_benchmark_enabled = True
    if line_build_speed_multiplier is not None:
        game_presets.LINE_BUILD_SPEED_MULTIPLIER = line_build_speed_multiplier
    if marketplace_bulk_discount_percent is not None:
        game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT = marketplace_bulk_discount_percent
    if marketplace_bulk_discount_threshold is not None:
        game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD = marketplace_bulk_discount_threshold
    from ruleset_loader import apply_ruleset_to_new_game, apply_ruleset_events_to_game
    used_ruleset = apply_ruleset_to_new_game(ruleset_name)
    players = generate_players(player_names, starting_money, ai_player_indices=ai_player_indices, ai_focus_per_player=ai_focus_per_player, ai_difficulty_per_player=ai_difficulty_per_player, map_preset_name=map_preset_name)
    game_map = Map(width=map_width, height=map_height, use_osm=use_osm, map_center_lat=map_center_lat, map_center_lon=map_center_lon, map_preset_name=map_preset_name)

    start_year = game_map.start_year if hasattr(game_map, 'start_year') else 1830
    end_year = game_map.end_year if hasattr(game_map, 'end_year') else 2050
    timeline = Timeline(initial_year=start_year, end_year=end_year)

    if marketplace_years_visible is None:
        from game_presets import MARKETPLACE_YEARS_VISIBLE
        marketplace_years_visible = MARKETPLACE_YEARS_VISIBLE
    marketplace = Marketplace(marketplace_years_visible=marketplace_years_visible)
    initialize_marketplace_trainsets(marketplace, num_players=len(players))
    game = Game(players=players, game_map=game_map, timeline=timeline, marketplace=marketplace, worker_replenishment_rate=worker_replenishment_rate, natural_disaster_frequency=natural_disaster_frequency, random_ai_spawn_enabled=random_ai_spawn_enabled, merge_enabled=merge_enabled, merge_ai_ai_enabled=merge_ai_ai_enabled, merge_ai_human_enabled=merge_ai_human_enabled, merge_human_human_enabled=merge_human_human_enabled, inflation_rate=inflation_rate, profit_margin=profit_margin, random_ai_spawn_interval_years=random_ai_spawn_interval_years, default_ai_difficulty_for_spawns=default_ai_difficulty_for_spawns, ai_min_population=ai_min_population, game_seed=game_seed, merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct, merge_ai_reject_chance_pct=merge_ai_reject_chance_pct, merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept, merge_ai_min_years_before_consider=merge_ai_min_years_before_consider, merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years, war_start_chance_per_year=war_start_chance_per_year, war_end_chance_per_year=war_end_chance_per_year, war_worker_draft_chance_per_year=war_worker_draft_chance_per_year, war_new_worker_cost_multiplier=war_new_worker_cost_multiplier, ai_debug_notifications_enabled=ai_debug_notifications_enabled)
    game.ruleset_name = used_ruleset
    game.starting_money = int(starting_money)
    game.idle_human_benchmark_enabled = bool(idle_human_benchmark_enabled)
    apply_ruleset_events_to_game(game, used_ruleset)
    return game

def create_single_ai_player(player_id: int, name: str, starting_money: int, ai_focus: str = None, ai_difficulty: str = None) -> Player:
    """Create a single AI player (e.g. for random spawn)."""
    from vehicle_presets import get_vehicle_registry
    from vehicle import Trainset, Railcar
    registry = get_vehicle_registry()
    all_locomotives = registry.get_locomotives_by_year()
    earliest_loco = None
    for loco, year in all_locomotives:
        if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
            earliest_loco = loco
            break
    earliest_railcar = registry.get_earliest_railcar()
    if not earliest_loco or not earliest_railcar:
        raise ValueError("No suitable vehicles found in registry")
    starting_trainsets = []
    for _ in range(3):
        cars = [Railcar("Standard Passenger", 0, earliest_railcar.capacity, earliest_railcar.maitnance_costs)]
        starting_trainsets.append(Trainset(locomotive=earliest_loco, cars=cars))
    focus = ai_focus if ai_focus in AI_FOCUSES else random.choice(AI_FOCUSES)
    from game_presets import AI_DEFAULT_DIFFICULTY
    diff = ai_difficulty if ai_difficulty in AI_DIFFICULTIES else AI_DEFAULT_DIFFICULTY
    return Player(player_id=player_id, money=starting_money, trainsets=starting_trainsets, name=name, line_names=[], is_ai=True, ai_focus=focus, ai_difficulty=diff)


def initialize_marketplace_trainsets(marketplace: Marketplace, num_players: int = 2):
    """Initialize marketplace using the new category-based generation system.
    This ensures proper categorization and availability rules from the start."""
    current_year = 1830
    
    # Use the new category-based generation system
    marketplace.generate_trainsets_based_on_discoveries(current_year)
    
    from worker import ENGINEER, LAWYER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER
    marketplace.set_worker_supply(ENGINEER.name, num_players * 5)
    marketplace.set_worker_supply(LAWYER.name, num_players * 3)
    marketplace.set_worker_supply(CONSTRUCTION_WORKER.name, num_players * 2)
    marketplace.set_worker_supply(MAINTENANCE_WORKER.name, num_players * 10)


def get_game_params_for_preset(preset_name: str) -> dict:
    """Get game parameters (map dimensions, center, timeline) for a map preset.
    
    All game logic for map selection lives here - the game is playable without qt.py.
    Uses map_preset_loader for dynamic discovery so new maps work without code changes.
    
    Args:
        preset_name: Map preset identifier (e.g. 'standard', 'nyc', 'ny_metro')
        
    Returns:
        dict with: map_width, map_height, map_center_lat, map_center_lon,
                   start_year, end_year, use_osm
    """
    from map_preset_loader import (
        load_map_preset, get_map_bounds, get_map_timeline,
        get_default_center_lat, get_default_center_lon, list_available_presets
    )
    
    if preset_name not in list_available_presets():
        # Fallback for "Standard" or unknown - use generic grid with OSM
        from map_presets_standard import STANDARD_BOUNDS, STANDARD_TIMELINE
        bounds = STANDARD_BOUNDS
        timeline = STANDARD_TIMELINE
        use_osm = True
    else:
        bounds = get_map_bounds(preset_name)
        timeline = get_map_timeline(preset_name)
        use_osm = True  # All maps use OSM tiles for display
    
    if bounds is None:
        from map_presets_standard import STANDARD_BOUNDS, STANDARD_TIMELINE
        bounds = STANDARD_BOUNDS
        timeline = STANDARD_TIMELINE
    
    # Bounds may have width/height or default_width/default_height
    map_width = bounds.get("width", bounds.get("default_width", 100))
    map_height = bounds.get("height", bounds.get("default_height", 75))
    map_center_lat = get_default_center_lat(preset_name) if preset_name in list_available_presets() else bounds.get("center_lat", 0.0)
    map_center_lon = get_default_center_lon(preset_name) if preset_name in list_available_presets() else bounds.get("center_lon", 0.0)
    start_year = timeline.get("start_year", 1830) if timeline else 1830
    end_year = timeline.get("end_year", 2050) if timeline else 2050
    
    return {
        "map_width": map_width,
        "map_height": map_height,
        "map_center_lat": map_center_lat,
        "map_center_lon": map_center_lon,
        "start_year": start_year,
        "end_year": end_year,
        "use_osm": use_osm,
        "map_preset_name": preset_name,
    }


def _merge_company_names(name_a: str, name_b: str) -> str:
    """Combine two company names into one (e.g. Pennsylvania Railroad + New York Central -> Penn Central)."""
    a = (name_a or '').strip()
    b = (name_b or '').strip()
    if not a:
        return b or 'Merged Company'
    if not b:
        return a
    words_a = [w for w in a.split() if w]
    words_b = [w for w in b.split() if w]
    if not words_a:
        return b
    if not words_b:
        return a
    # First part: abbreviate long first word (e.g. Pennsylvania -> Penn), else use first word
    first_word = words_a[0]
    if len(first_word) > 5:
        first_part = first_word[:4].rstrip('s')  # Penn, New , etc.
    else:
        first_part = first_word
    # Second part: last word of target name (e.g. Central from New York Central)
    second_part = words_b[-1] if words_b else first_part
    if first_part.lower() == second_part.lower():
        second_part = words_b[0] if words_b else second_part
    return f"{first_part} {second_part}".strip()


MAP_DISPLAY_MAX_POPULATION_POINTS = 3000


def cap_population_points_for_map_display(
    population_data: list[dict],
    max_points: int = MAP_DISPLAY_MAX_POPULATION_POINTS,
) -> list[dict]:
    """Cap population markers for map HTML so large presets stay fast and fit in memory."""
    if not population_data or len(population_data) <= max_points:
        return population_data
    ranked = sorted(population_data, key=lambda p: int(p.get('pop') or 0), reverse=True)
    return ranked[:max_points]


class Operations:    
    def __init__(self, player_names: list[str], map_width: int = 100, map_height: int = 100, starting_money: Optional[int] = None, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, use_osm: bool = False, map_center_lat: float = None, map_center_lon: float = None, marketplace_years_visible: int = None, map_preset_name: str = None, line_build_speed_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, ai_player_indices: list[int] = None,
        ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, random_ai_spawn_enabled: bool = True, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, profit_margin: float = 1.0, random_ai_spawn_interval_years: int = 30, default_ai_difficulty_for_spawns: str = 'hard', ai_min_population: int = 100, game_seed: int = None, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None,         war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None, ruleset_name: str = None, idle_human_benchmark_enabled: bool = False, ai_debug_notifications_enabled: bool = True):
        """Create Operations (game logic). Game is playable without qt.py.
        
        map_preset_name: Optional preset id ('standard', 'nyc', 'ny_metro') - uses
        get_game_params_for_preset to derive map center and use_osm. Map dimensions
        come from params unless overridden by map_width/map_height (e.g. standard with custom size).
        line_build_speed_multiplier, marketplace_bulk_discount_percent, marketplace_bulk_discount_threshold:
        Optional overrides for game_presets (applied in setup_game).
        ai_player_indices: list of player indices (0-based) that are AI-controlled.
        ai_focus_per_player: optional dict mapping player_index -> focus for AI players.
        ai_difficulty_per_player: optional dict mapping player_index -> difficulty for AI players.
        game_seed: optional 32-bit seed for Game._ai_rng_seed (deterministic AI/unit tests). If None, seed is random each game."""
        if starting_money is None:
            starting_money = STARTING_MONEY
        if map_preset_name:
            params = get_game_params_for_preset(map_preset_name)
            map_center_lat = params["map_center_lat"]
            map_center_lon = params["map_center_lon"]
            use_osm = params["use_osm"]
            # Use preset dimensions only for non-standard (nyc, ny_metro have fixed size)
            if map_preset_name != "standard":
                map_width = params["map_width"]
                map_height = params["map_height"]
        # Preset-specific overrides for population-init maps (e.g. NYC, NY Metro)
        from map_preset_loader import preset_has_population_init
        if map_preset_name and preset_has_population_init(map_preset_name):
            if line_build_speed_multiplier is None:
                line_build_speed_multiplier = 4.0
            if not ai_difficulty_per_player and ai_player_indices:
                ai_difficulty_per_player = {i: 'hard' for i in ai_player_indices}
        from ruleset_loader import get_active_ruleset
        if ruleset_name is None:
            ruleset_name = get_active_ruleset()
        self.ruleset_name = ruleset_name
        self.game = setup_game(player_names, map_width, map_height, starting_money, worker_replenishment_rate, natural_disaster_frequency, use_osm=use_osm, map_center_lat=map_center_lat, map_center_lon=map_center_lon, marketplace_years_visible=marketplace_years_visible, map_preset_name=map_preset_name, line_build_speed_multiplier=line_build_speed_multiplier, marketplace_bulk_discount_percent=marketplace_bulk_discount_percent, marketplace_bulk_discount_threshold=marketplace_bulk_discount_threshold, ai_player_indices=ai_player_indices, ai_focus_per_player=ai_focus_per_player, ai_difficulty_per_player=ai_difficulty_per_player, random_ai_spawn_enabled=random_ai_spawn_enabled, merge_enabled=merge_enabled, merge_ai_ai_enabled=merge_ai_ai_enabled, merge_ai_human_enabled=merge_ai_human_enabled, merge_human_human_enabled=merge_human_human_enabled, inflation_rate=inflation_rate, profit_margin=profit_margin, random_ai_spawn_interval_years=random_ai_spawn_interval_years, default_ai_difficulty_for_spawns=default_ai_difficulty_for_spawns, ai_min_population=ai_min_population, game_seed=game_seed, merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct, merge_ai_reject_chance_pct=merge_ai_reject_chance_pct, merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept, merge_ai_min_years_before_consider=merge_ai_min_years_before_consider, merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years, war_start_chance_per_year=war_start_chance_per_year, war_end_chance_per_year=war_end_chance_per_year, war_worker_draft_chance_per_year=war_worker_draft_chance_per_year, war_new_worker_cost_multiplier=war_new_worker_cost_multiplier, ruleset_name=ruleset_name, idle_human_benchmark_enabled=idle_human_benchmark_enabled, ai_debug_notifications_enabled=ai_debug_notifications_enabled)
        # Warm road index, point-area map, AI road features, and population sort caches at init
        # (avoids multi-second cold loads on first year advance / first AI turn).
        _warm_preset = map_preset_name or getattr(self.game.get_map(), "map_preset_name", None)
        if _warm_preset:
            from map_preset_loader import warm_game_start_year_end_caches
            warm_game_start_year_end_caches(_warm_preset, self.game.get_map())
        self.current_player_index = 0
        self.completed_jobs = []
        self._cached_ridership_data = None
        self._cached_ridership_year = None
        self.public_ta = None
        # Session-level deleted points: survives game state replacement (e.g. networked mode)
        self._session_deleted_population_points = set()
        # Government funding (from 1970): one player receives funding each year. Set by lobbying or random.
        self._government_funding_recipient = None
        self._government_funding_amount = 50000
        # AI money log for AI Watch tab (all years)
        self._ai_money_debug_log = []
        # Generated yearly news articles for the News Log tab
        self._news_log = []
    
    def get_game(self) -> Game:
        return self.game

    def get_runtime_game_settings(self) -> dict:
        """Snapshot of game settings that can be changed during play."""
        import game_presets
        g = self.game
        mp = g.marketplace
        return {
            'ai_debug_notifications_enabled': bool(getattr(g, 'ai_debug_notifications_enabled', True)),
            'random_ai_spawn_enabled': bool(getattr(g, 'random_ai_spawn_enabled', False)),
            'random_ai_spawn_interval_years': int(getattr(g, 'random_ai_spawn_interval_years', 30)),
            'ai_min_population': int(getattr(g, 'ai_min_population', 100)),
            'merge_enabled': bool(getattr(g, 'merge_enabled', False)),
            'merge_ai_ai_enabled': bool(getattr(g, 'merge_ai_ai_enabled', True)),
            'merge_ai_human_enabled': bool(getattr(g, 'merge_ai_human_enabled', True)),
            'merge_human_human_enabled': bool(getattr(g, 'merge_human_human_enabled', True)),
            'merge_ai_healthy_profit_threshold_pct': float(getattr(g, 'merge_ai_healthy_profit_threshold_pct', 25.0)),
            'merge_ai_reject_chance_pct': float(getattr(g, 'merge_ai_reject_chance_pct', 90.0)),
            'merge_ai_min_turns_to_live_for_accept': int(getattr(g, 'merge_ai_min_turns_to_live_for_accept', 10)),
            'merge_ai_min_years_before_consider': int(getattr(g, 'merge_ai_min_years_before_consider', 0)),
            'merge_ai_auto_fail_first_n_years': int(getattr(g, 'merge_ai_auto_fail_first_n_years', 0)),
            'worker_replenishment_rate': float(getattr(g, 'worker_replenishment_rate', 1.0)),
            'natural_disaster_frequency': float(getattr(g.event_system, 'natural_disaster_frequency', 1.0)),
            'inflation_rate': float(getattr(g, 'inflation_rate', 1.0)),
            'profit_margin': float(getattr(g, 'profit_margin', 1.0)),
            'line_build_speed_multiplier': float(game_presets.LINE_BUILD_SPEED_MULTIPLIER),
            'marketplace_years_visible': int(getattr(mp, 'marketplace_years_visible', game_presets.MARKETPLACE_YEARS_VISIBLE)),
            'marketplace_bulk_discount_percent': float(game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT) * 100.0,
            'marketplace_bulk_discount_threshold': int(game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD),
            'war_start_chance_per_year': float(getattr(g, 'war_start_chance_per_year', 0.0)),
            'war_end_chance_per_year': float(getattr(g, 'war_end_chance_per_year', 0.0)),
            'war_worker_draft_chance_per_year': float(getattr(g, 'war_worker_draft_chance_per_year', 0.0)),
            'war_new_worker_cost_multiplier': float(getattr(g, 'war_new_worker_cost_multiplier', 1.0)),
        }

    def apply_runtime_game_settings(self, settings: dict) -> None:
        """Apply in-game settings changes to the live game."""
        import random
        import game_presets
        from inflation_utils import set_inflation_rate_multiplier

        g = self.game
        if 'ai_debug_notifications_enabled' in settings:
            g.ai_debug_notifications_enabled = bool(settings['ai_debug_notifications_enabled'])
        if 'ai_min_population' in settings:
            g.ai_min_population = max(0, int(settings['ai_min_population']))
        if 'random_ai_spawn_interval_years' in settings:
            g.random_ai_spawn_interval_years = max(5, min(200, int(settings['random_ai_spawn_interval_years'])))
        if 'random_ai_spawn_enabled' in settings:
            g.random_ai_spawn_enabled = bool(settings['random_ai_spawn_enabled'])
            if not g.random_ai_spawn_enabled:
                g._next_random_ai_spawn_year = None
            elif getattr(g, '_next_random_ai_spawn_year', None) is None:
                interval = int(getattr(g, 'random_ai_spawn_interval_years', 30))
                half = max(5, interval // 2)
                g._next_random_ai_spawn_year = int(self.get_year()) + random.randint(interval - half, interval + half)
        if 'merge_enabled' in settings:
            g.merge_enabled = bool(settings['merge_enabled'])
        if 'merge_ai_ai_enabled' in settings:
            g.merge_ai_ai_enabled = bool(settings['merge_ai_ai_enabled'])
        if 'merge_ai_human_enabled' in settings:
            g.merge_ai_human_enabled = bool(settings['merge_ai_human_enabled'])
        if 'merge_human_human_enabled' in settings:
            g.merge_human_human_enabled = bool(settings['merge_human_human_enabled'])
        if 'merge_ai_healthy_profit_threshold_pct' in settings:
            g.merge_ai_healthy_profit_threshold_pct = max(0.0, min(100.0, float(settings['merge_ai_healthy_profit_threshold_pct'])))
        if 'merge_ai_reject_chance_pct' in settings:
            g.merge_ai_reject_chance_pct = max(0.0, min(100.0, float(settings['merge_ai_reject_chance_pct'])))
        if 'merge_ai_min_turns_to_live_for_accept' in settings:
            g.merge_ai_min_turns_to_live_for_accept = max(0, min(200, int(settings['merge_ai_min_turns_to_live_for_accept'])))
        if 'merge_ai_min_years_before_consider' in settings:
            g.merge_ai_min_years_before_consider = max(0, min(50, int(settings['merge_ai_min_years_before_consider'])))
        if 'merge_ai_auto_fail_first_n_years' in settings:
            g.merge_ai_auto_fail_first_n_years = max(0, min(50, int(settings['merge_ai_auto_fail_first_n_years'])))
        if 'worker_replenishment_rate' in settings:
            g.worker_replenishment_rate = max(0.1, min(5.0, float(settings['worker_replenishment_rate'])))
        if 'natural_disaster_frequency' in settings:
            freq = max(0.1, min(5.0, float(settings['natural_disaster_frequency'])))
            g.event_system.natural_disaster_frequency = freq
        if 'inflation_rate' in settings:
            g.inflation_rate = max(0.1, min(5.0, float(settings['inflation_rate'])))
            set_inflation_rate_multiplier(g.inflation_rate)
        if 'profit_margin' in settings:
            g.profit_margin = max(0.25, min(5.0, float(settings['profit_margin'])))
        if 'line_build_speed_multiplier' in settings:
            game_presets.LINE_BUILD_SPEED_MULTIPLIER = max(0.5, min(5.0, float(settings['line_build_speed_multiplier'])))
        if 'marketplace_years_visible' in settings:
            g.marketplace.marketplace_years_visible = max(1, min(200, int(settings['marketplace_years_visible'])))
        if 'marketplace_bulk_discount_percent' in settings:
            game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT = max(0.0, min(100.0, float(settings['marketplace_bulk_discount_percent']))) / 100.0
        if 'marketplace_bulk_discount_threshold' in settings:
            game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD = max(1, int(settings['marketplace_bulk_discount_threshold']))
        if 'war_start_chance_per_year' in settings:
            g.war_start_chance_per_year = max(0.0, min(1.0, float(settings['war_start_chance_per_year'])))
        if 'war_end_chance_per_year' in settings:
            g.war_end_chance_per_year = max(0.0, min(1.0, float(settings['war_end_chance_per_year'])))
        if 'war_worker_draft_chance_per_year' in settings:
            g.war_worker_draft_chance_per_year = max(0.0, min(1.0, float(settings['war_worker_draft_chance_per_year'])))
        if 'war_new_worker_cost_multiplier' in settings:
            g.war_new_worker_cost_multiplier = max(1.0, min(3.0, float(settings['war_new_worker_cost_multiplier'])))
    
    def get_players(self) -> list[Player]:
        players = self.game.get_players()
        return [p for p in players if not (hasattr(p, 'is_public_ta') and p.is_public_ta)]
    
    def get_current_player(self, current_player_index: int) -> Player:
        """Get the current active player based on index, or first active player if index is invalid"""
        active_players = self.get_active_players()
        if not active_players:
            return None
        if current_player_index < len(self.get_players()):
            player = self.get_players()[current_player_index]
            if not self.is_player_eliminated(player):
                return player
        return active_players[0]

    def get_effective_player(self, player: Player) -> Player:
        """Return the player who actually controls this company. If player merged into another human, return that acquirer."""
        if player is None:
            return None
        mid = getattr(player, 'merged_into_player_id', None)
        if mid is None:
            return player
        for p in self.get_players():
            if getattr(p, 'player_id', None) == mid:
                return p
        return player
    
    def set_current_player_index(self, index: int):
        """Set the current player index"""
        self.current_player_index = index
    
    def get_current_player_index(self) -> int:
        """Get the current player index"""
        return self.current_player_index
    
    def get_active_players(self) -> list[Player]:
        active = self.game.get_active_players()
        return [p for p in active if not (hasattr(p, 'is_public_ta') and p.is_public_ta)]
    
    def eliminate_player(self, player: Player):
        """Eliminate a player from the game"""
        self.game.eliminate_player(player)
    
    def get_map(self) -> Map:
        return self.game.get_map()
    
    def get_event_system(self):
        """Get the event system"""
        return self.game.get_event_system()
    
    def calc_player_profit(self, player: Player, current_year: int = None) -> int:
        """Calculate total profit for a player using cached profit values.
        Uses cached profit per line calculated during year advance to avoid expensive calculations.
        current_year: if provided, use this year to match cache; otherwise use game.get_year()."""
        if current_year is None:
            current_year = self.game.get_year()
        
        total_profit = 0
        for line_name in player.get_line_names():
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line:
                total_profit += self.calc_line_profit(line, current_year)
        
        return total_profit
    
    def calc_player_maintenance(self, player: Player) -> int:
        """Calculate total maintenance costs for a player"""
        return self.game.calc_maitnance_by_player(player)
    
    def add_railcar_to_trainset(self, trainset: Trainset, railcar: Railcar) -> bool:
        """Add a railcar to a trainset. Returns True if successful, False if at max capacity or locomotive can't have railcars"""
        loco = getattr(trainset, 'locomotive', None)
        if loco and getattr(loco, 'associated_railcar_name', None):
            if railcar.name != loco.associated_railcar_name:
                raise ValueError(f"{loco.name} can only use '{loco.associated_railcar_name}' railcars, but '{railcar.name}' was provided")
        return trainset.add_railcar(railcar)

    def get_add_railcar_info(self, player: Player) -> dict:
        """Trainsets that can accept another railcar, and spare railcars in inventory."""
        eligible_trainsets = []
        for ts in self.get_player_trainsets(player):
            loco = self.get_trainset_locomotive(ts)
            if not loco or not getattr(loco, 'can_have_railcars', True):
                continue
            max_cars = loco.max_cars if loco.max_cars is not None else Trainset.DEFAULT_MAX_RAILCARS
            if len(self.get_trainset_cars(ts)) < max_cars:
                eligible_trainsets.append(ts)
        railcars = list(getattr(player, 'railcars', []) or [])
        return {'trainsets': eligible_trainsets, 'railcars': railcars}

    def _compatible_railcars_for_trainset_locomotive(self, locomotive, railcars: list) -> list:
        if not locomotive or not getattr(locomotive, 'can_have_railcars', True):
            return []
        assoc = getattr(locomotive, 'associated_railcar_name', None)
        if assoc:
            return [c for c in railcars if getattr(c, 'name', None) == assoc]
        return list(railcars)

    def _compatible_locomotives_for_trainset(self, trainset: Trainset, locomotives: list) -> list:
        cars = self.get_trainset_cars(trainset)
        compatible = []
        for loco in locomotives:
            if not getattr(loco, 'can_have_railcars', True):
                if cars:
                    continue
            else:
                max_cars = loco.max_cars if loco.max_cars is not None else Trainset.DEFAULT_MAX_RAILCARS
                if len(cars) > max_cars:
                    continue
                assoc = getattr(loco, 'associated_railcar_name', None)
                if assoc and any(getattr(c, 'name', None) != assoc for c in cars):
                    continue
            compatible.append(loco)
        return compatible

    def get_modify_trainset_info(self, player: Player, trainset: Trainset) -> dict:
        """Inventory and options for modifying a trainset in place."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        loco = self.get_trainset_locomotive(trainset)
        cars = list(self.get_trainset_cars(trainset))
        spare_locomotives = list(getattr(player, 'locomotives', []) or [])
        spare_railcars = list(getattr(player, 'railcars', []) or [])
        max_cars = Trainset.DEFAULT_MAX_RAILCARS
        can_add_cars = False
        if loco and getattr(loco, 'can_have_railcars', True):
            max_cars = loco.max_cars if loco.max_cars is not None else Trainset.DEFAULT_MAX_RAILCARS
            can_add_cars = len(cars) < max_cars
        return {
            'trainset': trainset,
            'locomotive': loco,
            'cars': cars,
            'max_cars': max_cars,
            'can_add_cars': can_add_cars,
            'can_remove_cars': bool(cars),
            'compatible_locomotives': self._compatible_locomotives_for_trainset(trainset, spare_locomotives),
            'compatible_railcars': self._compatible_railcars_for_trainset_locomotive(loco, spare_railcars) if can_add_cars else [],
        }

    def add_railcar_to_trainset_for_player(self, player: Player, trainset: Trainset, railcar: Railcar) -> dict:
        """Move a spare railcar from player inventory onto a trainset."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        railcars = getattr(player, 'railcars', None) or []
        if railcar not in railcars:
            raise ValueError("Railcar not in player inventory")
        if not self.add_railcar_to_trainset(trainset, railcar):
            raise ValueError("Cannot add railcar (trainset at capacity or locomotive cannot pull cars)")
        railcars.remove(railcar)
        return {
            'capacity': trainset.get_capacity(),
            'cars_count': len(self.get_trainset_cars(trainset)),
        }

    def remove_railcar_from_trainset_for_player(
        self, player: Player, trainset: Trainset, railcar: Railcar = None, index: int = -1,
    ) -> dict:
        """Remove a railcar from a trainset back into player inventory."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        removed = trainset.remove_railcar(railcar=railcar, index=index)
        if removed is None:
            raise ValueError("No railcar to remove")
        self.acquire_railcar_for_player(player, removed)
        return {
            'railcar_name': getattr(removed, 'name', 'Railcar'),
            'capacity': trainset.get_capacity(),
            'cars_count': len(self.get_trainset_cars(trainset)),
        }

    def replace_trainset_locomotive_for_player(
        self, player: Player, trainset: Trainset, new_locomotive: Locomotive,
    ) -> dict:
        """Swap the trainset locomotive with one from player inventory."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        locomotives = getattr(player, 'locomotives', None) or []
        if new_locomotive not in locomotives:
            raise ValueError("Locomotive not in player inventory")
        if new_locomotive is trainset.locomotive:
            raise ValueError("Trainset already uses this locomotive")
        compatible = self._compatible_locomotives_for_trainset(trainset, [new_locomotive])
        if not compatible:
            raise ValueError("Locomotive is not compatible with this trainset's railcars")
        line_name = trainset.get_line_assignment()
        if line_name:
            target_line = self.get_map_lines().get(line_name)
            if target_line:
                if getattr(new_locomotive, 'needs_overhead_wire', False) and not getattr(target_line, 'has_overhead_wire', False):
                    raise ValueError("This locomotive requires overhead wire which the assigned line does not have.")
                if getattr(new_locomotive, 'needs_third_rail', False) and not getattr(target_line, 'has_third_rail', False):
                    raise ValueError("This locomotive requires a third rail which the assigned line does not have.")
        old_locomotive = trainset.locomotive
        locomotives.remove(new_locomotive)
        trainset.locomotive = new_locomotive
        trainset.capacity = trainset.calc_capacity()
        trainset.maitnance_costs = trainset.calc_maitnance_costs()
        self.acquire_locomotive_for_player(player, old_locomotive)
        return {
            'old_locomotive_name': getattr(old_locomotive, 'name', 'Locomotive'),
            'new_locomotive_name': getattr(new_locomotive, 'name', 'Locomotive'),
            'capacity': trainset.get_capacity(),
        }
    
    def assemble_trainset_complete(self, player: Player, locomotive: Locomotive, railcars: list[Railcar]) -> dict:
        """Complete trainset assembly operation. Returns dict with result info.
        Raises ValueError if locomotive cannot have railcars attached or if wrong railcars are used."""
        if railcars and not locomotive.can_have_railcars:
            raise ValueError(f"{locomotive.name} cannot have railcars attached (it is a self-contained unit like an EMU, DMU, streetcar, or subway car)")
        
        if locomotive.associated_railcar_name:
            for railcar in railcars:
                if railcar.name != locomotive.associated_railcar_name:
                    raise ValueError(f"{locomotive.name} can only use '{locomotive.associated_railcar_name}' railcars, but '{railcar.name}' was provided")
        
        trainset = self.create_trainset(locomotive, railcars)
        self.acquire_trainset_for_player(player, trainset)
        
        if locomotive in getattr(player, 'locomotives', []):
            player.locomotives.remove(locomotive)
        
        for railcar in railcars:
            if railcar in getattr(player, 'railcars', []):
                player.railcars.remove(railcar)
        
        return {
            'locomotive_name': locomotive.name,
            'railcars_count': len(railcars),
            'capacity': trainset.get_capacity()
        }
    
    def get_timeline(self) -> Timeline:
        return self.game.get_timeline()
    
    def get_year(self) -> int:
        return self.game.get_year()

    def ai_in_year_turn_completed(self, player) -> bool:
        """True if this AI already ran its build/actions for the current calendar year."""
        return getattr(player, '_ai_in_year_turn_year', None) == self.get_year()

    def mark_ai_in_year_turn_completed(self, player) -> None:
        player._ai_in_year_turn_year = self.get_year()

    def get_ai_players_needing_in_year_turn(self) -> list:
        """Living AIs that have not yet acted during the current calendar year."""
        year = self.get_year()
        return [
            p for p in self.get_players()
            if getattr(p, 'is_ai', False)
            and not self.is_player_eliminated(p)
            and getattr(p, '_ai_in_year_turn_year', None) != year
        ]

    def human_ready_for_year_end(self, player) -> bool:
        """True if this human player clicked End Year for the current calendar year."""
        return getattr(player, '_ready_for_end_year_year', None) == self.get_year()

    def mark_human_ready_for_year_end(self, player) -> None:
        player._ready_for_end_year_year = self.get_year()

    def player_year_actions_completed(self, player) -> bool:
        """True when this player has finished their portion of the current year (build/actions or End Year)."""
        if self.is_player_eliminated(player):
            return True
        if getattr(player, 'is_ai', False):
            return self.ai_in_year_turn_completed(player)
        return self.human_ready_for_year_end(player)

    def all_players_completed_year_actions(self) -> bool:
        active = self.get_active_players()
        if not active:
            return True
        return all(self.player_year_actions_completed(p) for p in active)

    def get_players_needing_year_actions(self) -> list:
        return [p for p in self.get_active_players() if not self.player_year_actions_completed(p)]
    
    def increase_year(self):
        self.game.increase_year()

    def decrease_year(self):
        self.game.decrease_year()

    def reset_year(self):
        self.game.reset_year()

    def start_game(self):
        self.game.start_game()

    def end_game(self):
        self.game.end_game()

    def create_line(self, name: str, points: list,  player: Player = None) -> Line:
        base = (name or "").strip()
        if not base:
            raise ValueError("Line name cannot be empty.")
        name = base
        suffix = 0
        while self.is_line_name_in_use(name):
            suffix += 1
            name = f"{base}_{suffix}"
        if not player:
            line = self.game.create_line(name, player, points)
            self.game.get_marketplace().add_line(line)
        else:
            total_length = 0.0
            if len(points) > 1:
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i+1]
                    total_length += math.hypot(x2 - x1, y2 - y1)

            COST_PER_UNIT_LENGTH = 150
            total_cost = int(COST_PER_UNIT_LENGTH * total_length)
            if total_cost < 15:
                total_cost = 15

            if player.get_money() < total_cost:
                raise ValueError(f"Player {player.get_name()} cannot afford line creation (needs ${total_cost}).")

            self.deduct_player_money(player, total_cost, reason='Line creation')
            line = self.game.create_line(name, player, points)
            player.acquire_line(line)
            self.game.get_map().add_line(line)           
        return line
    
    def buy_line_by_purchase(self, sale_item, player: Player) -> Line:
        current_year = self.get_year()
        base_price = sale_item.get_price(current_year=current_year)
        effective_price = self.get_effective_marketplace_price_for_player(player, base_price)
        if player.get_money() < effective_price:
            return None
        line = sale_item.get_item()
        old_line_name = line.name
        base = old_line_name
        new_name = base
        k = 0
        while self.is_line_name_in_use(new_name, exclude_line=line):
            k += 1
            new_name = f"{base}_{k}"
        if new_name != base:
            line.rename(new_name)
        old_owner = line.get_owner()
        if old_owner and old_owner != player:
            if old_line_name in old_owner.get_line_names():
                old_owner.get_line_names().remove(old_line_name)
        self.game.get_marketplace().remove_line(line)
        self.deduct_player_money(player, effective_price, reason='Marketplace line purchase')
        line.owner = player
        if line.name not in player.get_line_names():
            player.acquire_line(line)
        # Lines listed on the marketplace are removed from the map; always restore after purchase.
        self.add_line_to_map(line)
        self.invalidate_ridership_cache()
        return line
    
    def create_trainset(self, locomotive: Locomotive, cars: list[Railcar], line_assignment: str=None) -> Trainset:
        """Create a trainset from a locomotive and railcars.
        Raises ValueError if locomotive cannot have railcars attached."""
        if cars and not locomotive.can_have_railcars:
            raise ValueError(f"{locomotive.name} cannot have railcars attached (it is a self-contained unit like an EMU, DMU, streetcar, or subway car)")
        trainset = Trainset(locomotive=locomotive, cars=cars, line_assignment=line_assignment)
        return trainset
    
    def assign_trainset_to_line(self, trainset: Trainset, line_name: str):
        target_line = self.game.get_map().get_lines_by_name().get(line_name)
        if not target_line:
            raise ValueError(f"Line '{line_name}' not found")
        # Only rail lines accept trainsets; bus and ferry lines require bus/ferry vehicles
        if isinstance(target_line, (BusLine, Ferry)):
            raise ValueError("Cannot assign a trainset to a bus or ferry line. Assign buses to bus lines and ferries to ferry lines.")

        loco = trainset.locomotive
        if hasattr(loco, 'needs_overhead_wire') and loco.needs_overhead_wire:
            if not getattr(target_line, 'has_overhead_wire', False):
                raise ValueError("This trainset requires overhead wire which the line does not have.")
        if hasattr(loco, 'needs_third_rail') and loco.needs_third_rail:
            if not getattr(target_line, 'has_third_rail', False):
                raise ValueError("This trainset requires a third rail which the line does not have.")

        prev = trainset.get_line_assignment()
        if prev:
            prev_line = self.game.get_map().get_lines_by_name().get(prev)
            if prev_line and trainset in prev_line.vehicles:
                prev_line.vehicles.remove(trainset)

        trainset.assign_to_line(line_name)
        if trainset not in target_line.vehicles:
            target_line.vehicles.append(trainset)

    def unassign_trainset_complete(self, trainset: Trainset) -> dict:
        """Unassign a trainset from its current line and remove it from line vehicles."""
        line_name = trainset.get_line_assignment()
        if not line_name:
            raise ValueError("Trainset is not assigned to a line")
        line = self.game.get_map().get_lines_by_name().get(line_name)
        if line and trainset in line.vehicles:
            line.vehicles.remove(trainset)
        trainset.assign_to_line(None)
        return {'line_name': line_name}

    def get_player_by_id(self, player_id: int) -> Player:
        for p in self.game.get_players():
            if p.get_player_id() == player_id:
                return p
        raise KeyError(f"No player with id {player_id}")
    
    def get_players_by_name(self) -> dict:
        return self.game.get_players_by_name()
    
    def get_unassigned_trainsets(self, player: Player) -> list[Trainset]:
        return player.get_unassigned_trainsets()
    
    def get_trainsets_by_line(self, player: Player, line_name: str) -> list[Trainset]:
        return player.get_trainsets_by_line(line_name)
    
    def get_trainset_locomotive_name(self, trainset: Trainset) -> str:
        if trainset and hasattr(trainset, 'locomotive') and trainset.locomotive:
            return getattr(trainset.locomotive, 'name', 'Unknown')
        return 'Unknown'
    
    def get_trainset_cars(self, trainset: Trainset) -> list:
        if trainset and hasattr(trainset, 'cars'):
            return trainset.cars
        return []
    
    def get_trainset_capacity(self, trainset: Trainset) -> int:
        if trainset and hasattr(trainset, 'capacity'):
            return trainset.capacity
        return 0
    
    def get_trainset_line_assignment(self, trainset: Trainset) -> str:
        if trainset and hasattr(trainset, 'line_assignment'):
            return trainset.line_assignment or ''
        return ''
    
    def deduct_player_money(self, player: Player, amount: int, reason: str = None):
        """Deduct money from player. reason is used for AI money log (all years)."""
        if amount <= 0:
            return
        player.deduct_money(amount)
        if getattr(player, 'is_ai', False):
            self._log_ai_money(player, 'down', amount, reason or 'Spending')

    def add_player_money(self, player: Player, amount: int, reason: str = None):
        """Add money to player. reason is used for AI money log (all years)."""
        if amount <= 0:
            return
        player.add_money(amount)
        if getattr(player, 'is_ai', False):
            self._log_ai_money(player, 'up', amount, reason or 'Income')

    def _log_ai_money(self, player: Player, direction: str, amount: int, reason: str):
        """Append one AI money change to the log (all years, for AI Watch tab)."""
        if not hasattr(self, '_ai_money_debug_log'):
            self._ai_money_debug_log = []
        year = self.get_year()
        name = self.get_player_name(player)
        balance = self.get_player_money(player)  # balance after this change (we log after the change)
        self._ai_money_debug_log.append({
            'year': year,
            'player_name': name,
            'direction': direction,
            'amount': amount,
            'reason': reason,
            'balance_after': balance,
        })

    def get_ai_money_debug_summary(self):
        """Return summary of AI money changes (all years) for AI Watch tab.
        Returns dict with 'ups' and 'downs' lists of {year, player_name, amount, reason, balance_after}."""
        log = getattr(self, '_ai_money_debug_log', []) or []
        ups = [e for e in log if e['direction'] == 'up']
        downs = [e for e in log if e['direction'] == 'down']
        return {'ups': ups, 'downs': downs}

    def get_news_log(self, limit: int = None) -> list:
        """Return generated news articles (newest first)."""
        log = getattr(self, '_news_log', []) or []
        if limit is None or limit <= 0:
            return list(reversed(log))
        return list(reversed(log[-limit:]))

    def _news_article_pools(self) -> dict:
        """Economy/politics/oddities pools from the active game ruleset (or built-in defaults)."""
        from ruleset_loader import load_news_pools_for_ruleset

        ruleset = getattr(self, 'ruleset_name', None) or getattr(self.game, 'ruleset_name', None)
        return load_news_pools_for_ruleset(ruleset)

    def _generate_news_articles_for_year(self, new_year: int, payload: dict) -> list:
        """Generate mostly event-related yearly news copy and append to persistent log."""
        articles = []

        def _add(headline: str, body: str, category: str = "General"):
            if not headline or not body:
                return
            article = {
                'year': int(new_year),
                'category': category,
                'headline': str(headline),
                'body': str(body),
            }
            articles.append(article)
            self._news_log.append(article)

        natural = payload.get('natural_disaster_info')
        if natural:
            dname = natural.get('disaster_type', 'Disaster')
            sev = natural.get('severity', 0)
            _add(
                f"{dname} Disrupts Regional Network (Severity {sev})",
                "Authorities reported major disruption across affected corridors and temporary timetable cuts.",
                category="Disaster",
            )
        for ev in (payload.get('newly_triggered_events') or []):
            _add(
                f"Historical Shift: {getattr(ev, 'name', 'Market Event')}",
                getattr(ev, 'description', "Travel patterns and operating priorities are expected to adjust this year."),
                category="History",
            )
        for name, year in (payload.get('newly_available_vehicles') or []):
            _add(
                f"Technology Desk: {name} Debuts",
                f"New rolling stock class entered the market in {year}, with operators citing efficiency gains.",
                category="Technology",
            )
        spawned = payload.get('new_ai_spawned')
        if spawned:
            cname = spawned.get('name', 'New Transit Co')
            _add(
                f"{cname} Launches Service",
                f"The entrant opened with ${int(spawned.get('money', 0)):,} in capital and immediate expansion signals.",
                category="Market",
            )
        for acq, tgt in (payload.get('ai_ai_merges') or []):
            _add(
                f"Corporate Move: {self.get_player_name(acq)} Acquires {self.get_player_name(tgt)}",
                "Industry analysts expect route consolidation and lower duplicate service along overlapping corridors.",
                category="Corporate",
            )

        war = payload.get('war_events') or {}
        if war.get('war_started'):
            _add(
                "War Declared: Transit Labor and Steel Face Mobilization Pressure",
                "Operators braced for project delays as agencies competed for crews amid national call-ups.",
                category="World",
            )
        if war.get('war_ended'):
            _add(
                "Armistice: Agencies Expect Workers Back—At a Higher Price",
                "Analysts predicted a tight labor market and rising salaries for new hires after the conflict.",
                category="World",
            )
        for d in war.get('drafts', []) or []:
            pname = d.get('player_name', 'Operator')
            n = int(d.get('count', 0) or 0)
            if n > 0:
                _add(
                    f"Draft Orders: {pname} Loses {n} Transit Worker(s)",
                    "Unfavored operators reportedly bore heavier manpower losses as seats turned on the capital.",
                    category="World",
                )

        for w in payload.get('strike_warnings') or []:
            msg = str(w.get('message') or '').strip()
            if msg:
                first_line = msg.split('\n', 1)[0].strip()
                _add(
                    f"Labor Desk: {first_line}" if first_line else "Labor Desk: Strike Vote Looms",
                    msg.replace('\n', ' '),
                    category="Labor",
                )
        for sm in payload.get('strike_messages') or []:
            pname = sm.get('player_name', 'Operator')
            wname = sm.get('worker_name', 'Worker')
            qty = int(sm.get('quantity', 0) or 0)
            turns = int(sm.get('turns_remaining', 0) or 0)
            demand = float(sm.get('demanded_multiplier', 0) or 0)
            _add(
                f"Strike Halts Service at {pname}",
                (
                    f"{qty} {wname}(s) walked off the job for {turns} more turn(s), "
                    f"demanding {demand:.2f}x base pay."
                ),
                category="Labor",
                )

        self._add_company_ranking_and_venture_news(new_year, payload, _add)
        self._add_government_and_politics_news(new_year, payload, _add)

        pools = self._news_article_pools()
        economy_pool = pools.get("economy") or ()
        politics_pool = pools.get("politics") or ()
        oddities_pool = pools.get("oddities") or ()
        year = int(new_year)
        oddity_eligible = [
            (h, b) for (h, b, min_year) in oddities_pool if year >= int(min_year)
        ]

        # Filler columns from ruleset-owned pools so quiet years still get varied copy.
        filler_targets = max(0, 3 - len(articles))
        for _ in range(filler_targets):
            roll = random.random()
            if roll < 0.4 and politics_pool:
                h, b = random.choice(politics_pool)
                _add(h, b, category="Politics")
            elif roll < 0.7 and oddity_eligible:
                h, b = random.choice(oddity_eligible)
                _add(h, b, category="Oddities")
            elif economy_pool:
                h, b = random.choice(economy_pool)
                _add(h, b, category="Economy")

        if random.random() < 0.5 and politics_pool:
            h, b = random.choice(politics_pool)
            _add(h, b, category="Politics")

        if random.random() < 0.4 and oddity_eligible:
            h, b = random.choice(oddity_eligible)
            _add(h, b, category="Oddities")

        from game_presets import NEWS_LOG_MAX_ENTRIES
        max_entries = int(NEWS_LOG_MAX_ENTRIES)
        if max_entries > 0 and len(self._news_log) > max_entries:
            self._news_log = self._news_log[-max_entries:]

        return articles

    def _add_government_and_politics_news(self, new_year: int, payload: dict, _add) -> None:
        """Elections, lobbying, legislature, agency suits, and federal subsidies."""
        if payload.get('election_held'):
            leader_name = None
            leader_inf = 0.0
            gov = self.game.get_government() if self.game else None
            if gov:
                ranking = gov.get_favor_ranking()
                if ranking:
                    top_pid, leader_inf = ranking[0]
                    for player in self.get_players():
                        if self.get_player_id(player) == top_pid:
                            leader_name = self.get_player_name(player)
                            break
            body = (
                "Fifty transit-board seats and the tiebreaker post were filled after the six-year cycle. "
                "Hidden lobbying results from the prior term were revealed before ballots were counted."
            )
            if leader_name:
                body += (
                    f" {leader_name} heads the largest favorable bloc "
                    f"({leader_inf:.1f} weighted influence)."
                )
            _add(
                f"Election {new_year}: Transit Board Seated",
                body,
                category="Politics",
            )

        for notif in payload.get('lobby_seat_notifications', []) or []:
            lobbyist = notif.get('lobbyist_name', 'An operator')
            prev_id = notif.get('previous_owner_id')
            prev_name = 'an opponent'
            if prev_id is not None:
                for player in self.get_players():
                    if self.get_player_id(player) == prev_id:
                        prev_name = self.get_player_name(player)
                        break
            if notif.get('type') == 'tiebreaker':
                _add(
                    f"Lobbying: {lobbyist} Secures Tiebreaker Seat",
                    f"The tiebreaker post flipped from {prev_name}'s column after successful lobbying.",
                    category="Politics",
                )
            else:
                seat_idx = int(notif.get('seat_index', 0)) + 1
                _add(
                    f"Lobbying: {lobbyist} Flips Board Seat {seat_idx}",
                    f"Seat {seat_idx} moved out of {prev_name}'s column following a paid influence campaign.",
                    category="Politics",
                )

        players_by_id = {self.get_player_id(p): p for p in self.get_players()}

        gov_laws = payload.get('government_laws') or {}
        if gov_laws:
            favored_names = []
            unfavored_names = []
            grants = gov_laws.get('grants') or {}
            tax_mods = gov_laws.get('tax_modifiers') or {}
            for pid, grant in grants.items():
                if grant and pid in players_by_id:
                    favored_names.append(self.get_player_name(players_by_id[pid]))
            for pid, mod in tax_mods.items():
                if mod and mod > 1.0 and pid in players_by_id:
                    name = self.get_player_name(players_by_id[pid])
                    if name not in unfavored_names:
                        unfavored_names.append(name)
            if favored_names:
                _add(
                    "Legislature: Annual Transit Relief Package",
                    "The government enacted favorable property-tax relief, transit subsidies, "
                    f"maintenance credits, and lawsuit protections for {', '.join(favored_names)}.",
                    category="Politics",
                )
            if unfavored_names:
                _add(
                    "Legislature: Regulatory Burden Increased",
                    "Higher property taxes, heavier maintenance assessments, and increased "
                    f"lawsuit scrutiny target {', '.join(unfavored_names)} under the annual slate.",
                    category="Politics",
                )

        law_labels = {
            'reduce_property_tax_15': '15% property tax reduction',
            'reduce_property_tax_5': '5% property tax reduction',
            'increase_property_tax_15': '15% property tax increase',
            'increase_property_tax_5': '5% property tax increase',
            'tax_break': 'one-year tax holiday',
            'audit_target': 'tax audit order',
        }
        for eff in payload.get('new_government_laws', []) or []:
            law_type = eff.get('type', 'law')
            label = law_labels.get(law_type, law_type.replace('_', ' '))
            names = [
                self.get_player_name(players_by_id[pid])
                for pid in (eff.get('player_ids') or [])
                if pid in players_by_id
            ]
            if not names and eff.get('player_id') in players_by_id:
                names = [self.get_player_name(players_by_id[eff['player_id']])]
            target_text = ", ".join(names) if names else "selected operators"
            _add(
                f"Legislature: {label.title()}",
                f"The government enacted {label} affecting {target_text}.",
                category="Politics",
            )

        lawsuit = payload.get('lawsuit_result')
        if lawsuit:
            agency = lawsuit.get('agency', 'Agency')
            player = lawsuit.get('player', 'Operator')
            excuse = lawsuit.get('excuse', 'complaint')
            if lawsuit.get('agency_won'):
                penalty = int(lawsuit.get('penalty', 0) or 0)
                _add(
                    f"{agency} Wins Suit Against {player}",
                    f"{agency} prevailed on {excuse}; {player} paid ${penalty:,} in damages.",
                    category="Politics",
                )
            else:
                _add(
                    f"{player} Beats {agency} in Court",
                    f"{player} defeated a {agency} action over {excuse}.",
                    category="Politics",
                )

        funding = payload.get('government_funding')
        if funding and int(funding.get('amount', 0) or 0) > 0:
            recipient = funding.get('recipient_name', 'An operator')
            amount = int(funding['amount'])
            _add(
                f"Federal Subsidy: {recipient} Awarded ${amount:,}",
                "Congressional transit funding flowed to the designated carrier based on "
                "route-kilometers and open stations.",
                category="Politics",
            )

    def _add_company_ranking_and_venture_news(self, new_year: int, payload: dict, _add) -> None:
        """Publish company rankings and venture briefs (not every year — random cadence)."""
        import random
        if random.random() > 0.4:
            return
        player_finances = payload.get('player_finances') or []
        if not player_finances:
            return

        standings = []
        for entry in player_finances:
            player = entry.get('player')
            finances = entry.get('finances') or {}
            if player is None or self.is_player_eliminated(player):
                continue
            net = int(finances.get('net', 0) or 0)
            profit = int(finances.get('profit', 0) or 0)
            money = int(self.get_player_money(player))
            standings.append({
                'name': self.get_player_name(player),
                'player': player,
                'net': net,
                'profit': profit,
                'money': money,
            })
        if not standings:
            return

        standings.sort(key=lambda row: (row['net'], row['money']), reverse=True)
        ranking_lines = []
        for rank, row in enumerate(standings[:5], start=1):
            ranking_lines.append(
                f"{rank}. {row['name']} — net ${row['net']:,}, cash ${row['money']:,}, line profit ${row['profit']:,}"
            )
        _add(
            f"Transit Rankings {new_year}: {standings[0]['name']} Leads the Market",
            "Annual standings by net income:\n" + "\n".join(ranking_lines),
            category="Market",
        )

        leader = standings[0]
        laggard = standings[-1]
        if leader['net'] >= 1500 and random.random() < 0.55:
            _add(
                f"Investor Watch: {leader['name']} Posts a Strong Year",
                f"Analysts credited disciplined operations after net income of ${leader['net']:,} "
                f"and line profit of ${leader['profit']:,}.",
                category="Corporate",
            )
        if len(standings) > 1 and laggard['net'] <= -800 and random.random() < 0.55:
            _add(
                f"Warning: {laggard['name']} Faces a Difficult Year",
                f"The company reported net income of ${laggard['net']:,} with ${laggard['money']:,} "
                "in cash reserves; observers urged route consolidation or service cuts.",
                category="Corporate",
            )

        if random.random() > 0.35:
            return
        current_year = int(new_year)
        map_lines = self.get_map_lines() or {}
        for row in standings:
            player = row['player']
            for line_name in player.get_line_names():
                line = map_lines.get(line_name)
                if not line:
                    continue
                line_profit = 0
                if hasattr(line, '_cached_profit') and getattr(line, '_cached_profit_year', None) == current_year:
                    line_profit = int(line._cached_profit or 0)
                else:
                    line_profit = int(self.calc_line_profit(line, current_year))
                if line_profit >= 1200 and random.random() < 0.25:
                    _add(
                        f"Success: {row['name']}'s {line_name} Delivers",
                        f"The route contributed an estimated ${line_profit:,} in line profit this year, "
                        "outperforming peer corridors.",
                        category="Venture",
                    )
                elif line_profit <= -600 and random.random() < 0.25:
                    _add(
                        f"Setback: {row['name']}'s {line_name} Struggles",
                        f"Service on {line_name} lost an estimated ${abs(line_profit):,} after costs, "
                        "prompting timetable and fare reviews.",
                        category="Venture",
                    )

    def get_effective_cost_for_player(self, player: Player, base_cost: int) -> int:
        """Return effective construction cost for player. AI difficulty applies: hard=0.85x, easy=1.15x."""
        if not getattr(player, 'is_ai', False):
            return base_cost
        diff = getattr(player, 'ai_difficulty', None) or 'hard'
        mult = AI_DIFFICULTY_COST_MULTIPLIER.get(diff, 1.0)
        return max(1, int(base_cost * mult))

    def get_effective_marketplace_price_for_player(self, player: Player, base_price: int) -> int:
        """Return effective marketplace price for player. AI difficulty applies: hard=0.85x, easy=1.1x."""
        if not getattr(player, 'is_ai', False):
            return base_price
        diff = getattr(player, 'ai_difficulty', None) or 'hard'
        mult = AI_DIFFICULTY_MARKETPLACE_MULTIPLIER.get(diff, 1.0)
        return max(1, int(base_price * mult))

    def get_lobby_success_chance_for_player(self, player: Player, amount: int, option_id: str = None) -> float:
        """Return lobby success chance for player. Hard AI gets +10% bonus, easy gets -5%."""
        base = self.get_lobby_success_chance(amount, option_id)
        if not getattr(player, 'is_ai', False):
            return base
        diff = getattr(player, 'ai_difficulty', None) or 'hard'
        bonus = AI_DIFFICULTY_LOBBY_BONUS.get(diff, 0.0)
        return min(0.95, max(0.05, base + bonus))

    def acquire_line_for_player(self, player: Player, line: Line):
        player.acquire_line(line)

    @staticmethod
    def _stamp_vehicle_purchase_year(vehicle, purchase_year: int) -> None:
        """Record when rolling stock entered service (maintenance aging uses this, not invention year)."""
        try:
            py = int(purchase_year)
        except (TypeError, ValueError):
            return
        from vehicle import Trainset
        if isinstance(vehicle, Trainset):
            vehicle.purchase_year = py
            loco = getattr(vehicle, 'locomotive', None)
            if loco is not None:
                loco.purchase_year = py
            for rc in getattr(vehicle, 'railcars', None) or []:
                rc.purchase_year = py
            return
        if hasattr(vehicle, 'purchase_year'):
            vehicle.purchase_year = py

    def acquire_trainset_for_player(self, player: Player, trainset: Trainset):
        self._stamp_vehicle_purchase_year(trainset, self.get_year())
        player.aquire_trainset(trainset)
    
    def acquire_locomotive_for_player(self, player: Player, locomotive: Locomotive):
        """Add a locomotive to player's inventory (stored separately, can be assembled later)"""
        self._stamp_vehicle_purchase_year(locomotive, self.get_year())
        if not hasattr(player, 'locomotives'):
            player.locomotives = []
        player.locomotives.append(locomotive)
    
    def acquire_railcar_for_player(self, player: Player, railcar: Railcar):
        """Add a railcar to player's inventory (stored separately, can be assembled later)"""
        self._stamp_vehicle_purchase_year(railcar, self.get_year())
        if not hasattr(player, 'railcars'):
            player.railcars = []
        player.railcars.append(railcar)

    def acquire_bus_for_player(self, player: Player, bus):
        """Add a bus to player inventory (used on bus lines)."""
        self._stamp_vehicle_purchase_year(bus, self.get_year())
        if not hasattr(player, 'buses'):
            player.buses = []
        player.buses.append(bus)

    def acquire_ferry_for_player(self, player: Player, ferry):
        """Add a ferry vessel to player inventory (used on ferry lines)."""
        self._stamp_vehicle_purchase_year(ferry, self.get_year())
        if not hasattr(player, 'ferries'):
            player.ferries = []
        player.ferries.append(ferry)

    def get_player_money(self, player: Player) -> int:
        return player.get_money()
    
    def get_player_line_names(self, player: Player) -> list[str]:
        return player.get_line_names()

    def get_player_rail_line_names(self, player: Player) -> list[str]:
        """Return line names that accept trainsets (rail lines only; exclude bus and ferry lines)."""
        lines_by_name = self.game.get_map().get_lines_by_name() or {}
        return [ln for ln in player.get_line_names() if lines_by_name.get(ln) and not isinstance(lines_by_name[ln], (BusLine, Ferry))]

    def get_player_trainsets(self, player: Player) -> list[Trainset]:
        return player.get_trainsets()
    
    def get_disassemble_info(self, player: Player) -> dict:
        """Get information needed for disassembling trainsets"""
        trainsets = self.get_player_trainsets(player)
        return {'trainsets': trainsets}
    
    def get_player_id(self, player: Player) -> int:
        return player.get_player_id()

    def get_player_color_index(self, player: Player) -> int:
        from player_colors import get_player_color_index
        return get_player_color_index(player)
    
    def get_trainset_capacity(self, trainset: Trainset) -> int:
        return trainset.get_capacity()
    
    def get_trainset_line_assignment(self, trainset: Trainset) -> str:
        return trainset.get_line_assignment()
    
    def _line_profit_from_cache(self, line: Line, current_year: int):
        """Year-end cached profit for this calendar year or the prior year (in-year turns)."""
        if not hasattr(line, '_cached_profit') or not hasattr(line, '_cached_profit_year'):
            return None
        cy = int(current_year)
        cached_year = int(line._cached_profit_year)
        if cached_year == cy or cached_year == cy - 1:
            return int(line._cached_profit or 0)
        return None
    
    def calc_line_profit(self, line: Line, current_year: int = None) -> int:
        """Line profit from year-end cache (no ridership re-simulation here)."""
        if current_year is None:
            current_year = self.game.get_year()
        cached = self._line_profit_from_cache(line, current_year)
        if cached is not None:
            return cached
        stored = getattr(line, 'profit', None)
        if isinstance(stored, (int, float)) and not isinstance(stored, bool):
            return int(stored)
            return 0
    
    def extend_line_route_from_beginning(self, line: Line, additional_points: list):
        line.extend_route_from_beginning(additional_points)

    def extend_line_route_from_end(self, line: Line, additional_points: list):
        line.extend_route_from_end(additional_points)

    def is_line_name_in_use(self, name: str, exclude_line: Line = None) -> bool:
        """True if any completed line or queued line build uses this name (case-insensitive)."""
        key = _norm_display_name(name)
        if not key:
            return False
        for _nm, ln in self.game.get_map().get_lines_by_name().items():
            if _norm_display_name(_nm) == key:
                if exclude_line is not None and ln is exclude_line:
                    continue
                return True
        for build in self.game.build_queue or []:
            if build.get("type") != "line":
                continue
            bd = build.get("data") or {}
            if _norm_display_name(bd.get("name", "")) == key:
                return True
        return False

    def assert_line_name_globally_available(self, name: str, exclude_line: Line = None) -> None:
        if not (name or "").strip():
            raise ValueError("Line name cannot be empty.")
        if self.is_line_name_in_use(name, exclude_line=exclude_line):
            raise ValueError(
                f"Line name '{name.strip()}' is already in use or reserved in the build queue. "
                "Line names must be unique for all players (compared case-insensitively)."
            )

    def _update_line_name_references(self, old_name: str, new_name: str) -> None:
        """After renaming a line: trainset assignments, station line lists, and queued jobs."""
        for p in self.game.get_players():
            for ts in p.get_trainsets():
                if ts.get_line_assignment() == old_name:
                    ts.assign_to_line(new_name)
        for line in self.get_map_lines().values():
            for st in line.get_stations() if hasattr(line, "get_stations") else (getattr(line, "stations", None) or []):
                lns = getattr(st, "line_names", None)
                if not lns and hasattr(st, "get_line_names"):
                    lns = st.get_line_names()
                if not lns:
                    continue
                for i, ln in enumerate(list(lns)):
                    if ln == old_name:
                        lns[i] = new_name
        for build in self.game.build_queue or []:
            if build.get("type") == "line":
                bd = build.setdefault("data", {})
                if bd.get("name") == old_name:
                    bd["name"] = new_name
            elif build.get("type") == "line_extension":
                bd = build.setdefault("data", {})
                if bd.get("line_name") == old_name:
                    bd["line_name"] = new_name
            elif build.get("type") == "station":
                bd = build.setdefault("data", {})
                lns = bd.get("line_names") or []
                if old_name in lns:
                    bd["line_names"] = [new_name if x == old_name else x for x in lns]

    def rename_line(self, line: Line, new_name: str) -> None:
        """Rename a line and keep map keys, owners, trainsets, stations, and build queue consistent."""
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Line name cannot be empty.")
        old_name = line.get_name()
        if new_name == old_name:
            return
        if _norm_display_name(new_name) == _norm_display_name(old_name):
            lines_dict = self.game.get_map().get_lines_by_name()
            if old_name in lines_dict and lines_dict[old_name] is line:
                del lines_dict[old_name]
                line.rename(new_name)
                lines_dict[new_name] = line
                owner = line.get_owner()
                if owner and old_name in owner.get_line_names():
                    owner.line_names[owner.line_names.index(old_name)] = new_name
                self._update_line_name_references(old_name, new_name)
            return
        self.assert_line_name_globally_available(new_name, exclude_line=line)
        lines_dict = self.game.get_map().get_lines_by_name()
        if old_name in lines_dict and lines_dict.get(old_name) is line:
            del lines_dict[old_name]
        line.rename(new_name)
        lines_dict[new_name] = line
        owner = line.get_owner()
        if owner and old_name in owner.get_line_names():
            owner.line_names[owner.line_names.index(old_name)] = new_name
        self._update_line_name_references(old_name, new_name)

    def _collect_normalized_station_names_for_player(
        self, player: Player, exclude_station=None, exclude_pending_station_build=None
    ) -> set:
        keys = set()
        for ln in player.get_line_names() or []:
            line = self.get_map_lines().get(ln)
            if not line:
                continue
            stations = line.get_stations() if hasattr(line, "get_stations") else (getattr(line, "stations", None) or [])
            for st in stations:
                if st is exclude_station:
                    continue
                keys.add(_norm_display_name(self.get_station_name(st)))
        for build in self.game.build_queue or []:
            if build is exclude_pending_station_build:
                continue
            if build.get("type") != "station" or build.get("player") != player:
                continue
            keys.add(_norm_display_name((build.get("data") or {}).get("name", "")))
        return keys

    def _station_serves_line_outside(self, served_line_names: list, new_line_names: set) -> bool:
        return any(ln not in new_line_names for ln in (served_line_names or []))

    def find_nearby_transfer_station_name(
        self,
        x: float,
        y: float,
        line_names: list,
        max_distance_m: float = None,
    ) -> Optional[str]:
        """Return the name of the nearest station on another line within max_distance_m, or None."""
        from game_presets import AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M

        max_m = float(max_distance_m if max_distance_m is not None else AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M)
        new_lines = set(line_names or [])
        if not new_lines:
            return None
        best_name = None
        best_d = max_m + 1.0
        game_map = self.game.get_map()

        def _consider(st_name: str, sx: float, sy: float, served: list) -> None:
            nonlocal best_name, best_d
            if not self._station_serves_line_outside(served, new_lines):
                return
            name = (st_name or "").strip()
            if not name:
                return
            d = self._direct_distance_between_stop_positions_m(x, y, sx, sy)
            if d <= max_m and d < best_d:
                best_d = d
                best_name = name

        for line_name, line in (game_map.get_lines_by_name() or {}).items():
            for station in (line.get_stations() or []):
                served = getattr(station, "line_names", None) or (
                    station.get_line_names() if hasattr(station, "get_line_names") else [line_name]
                )
                sx = float(station.get_x() if hasattr(station, "get_x") else getattr(station, "x", 0))
                sy = float(station.get_y() if hasattr(station, "get_y") else getattr(station, "y", 0))
                _consider(getattr(station, "name", ""), sx, sy, served)

        for build in self.game.build_queue or []:
            if build.get("type") != "station":
                continue
            data = build.get("data") or {}
            served = data.get("line_names") or []
            try:
                qx = float(data.get("x"))
                qy = float(data.get("y"))
            except (TypeError, ValueError):
                continue
            _consider(data.get("name", ""), qx, qy, served)

        return best_name

    def station_transfer_name_reuse_allowed(
        self,
        player: Player,
        station_name: str,
        x: float,
        y: float,
        line_names: list,
        max_distance_m: float = None,
    ) -> bool:
        """True when station_name matches a nearby stop on another line (transfer-hub naming)."""
        from game_presets import AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M

        target = _norm_display_name(station_name)
        if not target:
            return False
        nearby = self.find_nearby_transfer_station_name(
            float(x), float(y), list(line_names or []), max_distance_m=max_distance_m,
        )
        return bool(nearby) and _norm_display_name(nearby) == target

    def assert_station_name_unique_for_player(
        self,
        player: Player,
        station_name: str,
        exclude_station=None,
        exclude_pending_station_build=None,
        x: float = None,
        y: float = None,
        line_names: list = None,
    ) -> None:
        if not (station_name or "").strip():
            raise ValueError("Station name cannot be empty.")
        nk = _norm_display_name(station_name)
        if nk in self._collect_normalized_station_names_for_player(
            player,
            exclude_station=exclude_station,
            exclude_pending_station_build=exclude_pending_station_build,
        ):
            if (
                x is not None
                and y is not None
                and line_names
                and self.station_transfer_name_reuse_allowed(
                    player, station_name, float(x), float(y), list(line_names),
                )
            ):
                return
            raise ValueError(
                "You already have a station or a station in your build queue with this name "
                "(names are compared case-insensitively)."
            )

    def _station_players(self, station) -> list:
        owners = []
        seen = set()
        lns = getattr(station, "line_names", None) or (
            station.get_line_names() if hasattr(station, "get_line_names") else []
        )
        for lname in lns or []:
            line = self.get_map_lines().get(lname)
            if not line:
                continue
            o = self.get_line_owner(line)
            if o and id(o) not in seen:
                seen.add(id(o))
                owners.append(o)
        return owners

    def get_line_route(self, line: Line) -> list:
        return line.get_route()
    
    def get_line_owner(self, line: Line) -> Player:
        return line.get_owner()
    
    def get_line_vehicles(self, line: Line) -> list[Trainset]:
        return line.get_vehicles()
    
    def get_line_name(self, line: Line) -> str:
        return line.get_name()
    
    def get_line_object_id(self, line: Line) -> int:
        return line.get_object_id()
    
    def get_line_length(self, line: Line) -> int:
        return len(line)
    
    def get_vehicle_name(self, vehicle: Vehicle) -> str:
        return vehicle.name
    
    def get_vehicle_object_id(self, vehicle: Vehicle) -> int:
        return vehicle.object_id
    
    def get_locomotive_specs(self, locomotive: Locomotive) -> dict:
        return {
            "fuel": locomotive.fuel,
            "miles_per_unit": locomotive.miles_per_unit,
            "top_speed": locomotive.top_speed,
            "average_speed": locomotive.average_speed,
            "capacity": locomotive.capacity,
            "needs_third_rail": locomotive.needs_third_rail,
            "needs_overhead_wire": locomotive.needs_overhead_wire
        }
    
    def get_railcar_specs(self, railcar: Railcar) -> dict:
        return {
            "capacity": railcar.capacity
        }
    
    def get_boat_specs(self, boat: Boat) -> dict:
        return {
            "capacity": boat.capacity,
            "top_speed": boat.top_speed,
            "average_speed": boat.average_speed
        }
    
    def get_trainset_specs(self, trainset: Trainset) -> dict:
        return {
            "locomotive": trainset.locomotive,
            "cars": trainset.cars,
            "capacity": trainset.get_capacity(),
            "line_assignment": trainset.get_line_assignment()
        }
    
    def assign_vehicle_to_line(self, vehicle: Vehicle, line_name: str):
        if isinstance(vehicle, Trainset):
            vehicle.assign_to_line(line_name)

    def get_map_width(self) -> int:
        return self.game.get_map().get_width()
    
    def get_map_height(self) -> int:
        return self.game.get_map().get_height()
    
    def get_map_grid(self) -> list[list]:
        return self.game.get_map().get_grid()
    
    def get_map_lines(self) -> dict:
        return self.game.get_map().get_lines_by_name()
    
    def get_map_roads(self) -> dict:
        return self.game.get_map().get_roads()
    
    def add_line_to_map(self, line: Line):
        self.game.get_map().add_line(line)

    def delete_line_from_map(self, line: Line):
        self.game.get_map().delete_line(line)

    def get_marketplace(self) -> Marketplace:
        return self.game.get_marketplace()

    def add_line_to_marketplace(self, line: Line, seller: str = None):
        owner = self.get_line_owner(line) if hasattr(self, 'get_line_owner') else getattr(line, 'owner', None)
        if seller is None and owner is not None:
            seller = self.get_player_name(owner)
        if seller is None:
            seller = "Manufacturer"
        current_year = self.get_year()
        self.game.get_marketplace().add_line(line, current_year=current_year, seller=seller)

    def sell_player_line_to_marketplace(self, player: Player, line_name: str) -> dict:
        """List a player-owned line on the marketplace and remove it from the map."""
        if not line_name:
            raise ValueError("Line name required")
        game_map = self.get_map()
        line = game_map.get_lines_by_name().get(line_name)
        if line is not None:
            owner = self.get_line_owner(line) if hasattr(self, 'get_line_owner') else getattr(line, 'owner', None)
            if owner is not None and owner != player:
                raise ValueError(f"Player does not own line '{line_name}'")
            for ts in list(player.get_trainsets()):
                if ts.get_line_assignment() == line_name:
                    try:
                        self.unassign_trainset_complete(ts)
                    except ValueError:
                        pass
        cancelled = []
        queue = self.get_build_queue() if hasattr(self, 'get_build_queue') else []
        for idx in range(len(queue) - 1, -1, -1):
            build = queue[idx]
            if build.get('player') != player:
                continue
            data = build.get('data') or {}
            matches = (
                (build.get('type') == 'line' and data.get('name') == line_name)
                or (build.get('type') == 'line_extension' and data.get('line_name') == line_name)
                or (build.get('type') == 'station' and line_name in (data.get('line_names') or []))
            )
            if matches:
                cancelled.append(self.cancel_build(idx))
        if line is None:
            return {
                'success': True,
                'line_name': line_name,
                'listed': False,
                'cancelled_builds': len(cancelled),
            }
        current_year = self.get_year()
        seller = self.get_player_name(player)
        self.get_marketplace().add_line(line, current_year=current_year, seller=seller)
        if line_name in player.get_line_names():
            player.remove_line(line_name)
        self.delete_line_from_map(line)
        return {
            'success': True,
            'line_name': line_name,
            'listed': True,
            'cancelled_builds': len(cancelled),
        }

    def get_timeline_start_year(self) -> int:
        return self.game.get_timeline().get_start_year()
    
    def get_timeline_end_year(self) -> int:
        return self.game.get_timeline().get_end_year()

    def get_inflation_factor_for_economy(self, max_factor: float = 10.0) -> float:
        from inflation_utils import get_inflation_factor
        return get_inflation_factor(
            self.get_year(),
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
            max_factor=max_factor,
        )

    def inflate_economic_value(self, base_amount: float, max_factor: float = 10.0) -> int:
        from inflation_utils import inflate_economic_value
        return inflate_economic_value(
            base_amount,
            self.get_year(),
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
            max_factor=max_factor,
        )

    def get_nominal_fare_cap(self, year: int = None) -> tuple[float, float]:
        from inflation_utils import get_nominal_fare_cap
        y = int(self.get_year() if year is None else year)
        return get_nominal_fare_cap(
            y,
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
        )

    def get_timeline_fare_target(self, year: int = None) -> int:
        from inflation_utils import get_timeline_fare_target
        y = int(self.get_year() if year is None else year)
        return get_timeline_fare_target(
            y,
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
        )

    def apply_timeline_fare_to_line(self, line, year: int = None) -> None:
        """Set a new line's fare to the low early-game target for the calendar year."""
        if line is not None and hasattr(line, 'fare'):
            line.fare = self.get_timeline_fare_target(year)

    def _calendar_years_since_timeline_start(self) -> int:
        return self.get_year() - self.get_timeline_start_year()

    def merge_ai_consideration_allowed_this_year(self) -> bool:
        min_y = getattr(self.game, 'merge_ai_min_years_before_consider', None)
        if min_y is None:
            from game_presets import AI_MERGE_MIN_YEARS_BEFORE_CONSIDER
            min_y = AI_MERGE_MIN_YEARS_BEFORE_CONSIDER
        return self._calendar_years_since_timeline_start() >= int(min_y)

    def merge_ai_blocked_early_calendar_years(self, acquirer: Player, target: Player) -> bool:
        n = getattr(self.game, 'merge_ai_auto_fail_first_n_years', None)
        if n is None:
            from game_presets import AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
            n = AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
        if self._calendar_years_since_timeline_start() >= int(n):
            return False
        return bool(getattr(acquirer, 'is_ai', False) or getattr(target, 'is_ai', False))

    def get_timeline_current_year(self) -> int:
        return self.game.get_timeline().get_year()
    
    def advance_timeline(self):
        self.game.get_timeline().advance()

    def go_back_timeline(self):
        self.game.get_timeline().go_back()

    def advance_turn(self, event_population_change: float = 0.0):
        self.game.increase_year(event_population_change)
        # to ensure it happens after all year-end operations
    
    def get_strike_warnings(self, current_year: int):
        """Get strike warnings for all players"""
        return self.game.get_strike_warnings(current_year)
    
    def process_player_finances(self, player, current_year: int):
        """Process all financial calculations for a player using cached profit values.
        Profit must be pre-calculated during year advance to avoid expensive calculations here."""
        player_profit = self.calc_player_profit(player, current_year)
        
        trainset_maintenance = self.game.calc_trainset_maintenance(player)
        resource_charge = self.game.calc_resource_costs(player, current_year)
        line_maintenance = self.game.calc_maitnance_by_player(player) or 0
        
        laws = getattr(self.game, '_government_laws', {})
        maintenance_relief = laws.get('maintenance_relief', {})
        player_id = player.get_player_id()
        if player_id in maintenance_relief:
            factor = maintenance_relief[player_id]
            trainset_maintenance = int(trainset_maintenance * factor)
            line_maintenance = int(line_maintenance * factor)
        
        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, current_year, player_profit)
        worker_costs, is_on_strike = self.game.calc_worker_costs(player, current_year)
        
        grants = laws.get('grants', {})
        is_favored = grants.get(player_id, 0) > 0
        government_grant = self._compute_transit_subsidy(player, current_year, is_favored)
        
        total_deductions = trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs
        net = player_profit - total_deductions + government_grant
        
        if not player.is_player_eliminated():
            # Profit must ADD to money (never subtract). Costs are deducted separately.
            if player_profit > 0:
                self.add_player_money(player, player_profit, reason='Line profit')
            elif player_profit < 0:
                self.deduct_player_money(player, -player_profit, reason='Line loss')
            if government_grant:
                self.add_player_money(player, government_grant, reason='Government grant')
            if total_deductions:
                self.deduct_player_money(player, total_deductions, reason='Taxes, maintenance, workers')
        
        return {
            'profit': player_profit,
            'trainset_maintenance': trainset_maintenance,
            'resource_charge': resource_charge,
            'line_maintenance': line_maintenance,
            'poll_tax': poll_tax,
            'property_tax': property_tax,
            'income_tax': income_tax,
            'worker_costs': worker_costs,
            'government_grant': government_grant,
            'net': net,
            'is_on_strike': is_on_strike
        }
    
    def _get_player_transit_km_and_stations(self, player) -> tuple:
        """Return (total_route_km, total_open_stations) for this player. Used for transit subsidy.
        total_route_km is sum of segment lengths (grid units, ~1 unit ≈ 1 km)."""
        import math
        total_km = 0.0
        total_stations = 0
        map_lines = self.get_map_lines() or {}
        for line_name in player.get_line_names():
            line = map_lines.get(line_name)
            if not line:
                continue
            route = self.get_line_route(line)
            if len(route) > 1:
                for i in range(len(route) - 1):
                    p1, p2 = route[i], route[i + 1]
                    x1, y1 = float(p1[0]) if isinstance(p1, (list, tuple)) else float(getattr(p1, 'x', 0)), float(p1[1]) if isinstance(p1, (list, tuple)) else float(getattr(p1, 'y', 0))
                    x2, y2 = float(p2[0]) if isinstance(p2, (list, tuple)) else float(getattr(p2, 'x', 0)), float(p2[1]) if isinstance(p2, (list, tuple)) else float(getattr(p2, 'y', 0))
                    total_km += math.hypot(x2 - x1, y2 - y1)
            stations = line.get_stations() if hasattr(line, 'get_stations') else []
            total_stations += len(stations or [])
        return (total_km, total_stations)
    
    def _compute_transit_subsidy(self, player, current_year: int, is_favored: bool) -> int:
        """Government subsidizes transit by km and open stations. One turn only; no subsidy if no transit (player cannot make money doing nothing)."""
        if not is_favored:
            return 0
        total_km, num_stations = self._get_player_transit_km_and_stations(player)
        if total_km <= 0 or num_stations <= 0:
            return 0
        per_km = 80 + (current_year - 1830) // 10 * 10
        per_station = 40 + (current_year - 1830) // 10 * 5
        return int(total_km * per_km + num_stations * per_station)
    
    def estimate_annual_costs_for_player(self, player, next_year: int, extra_workers_estimate: int = 2) -> int:
        """Estimate next year's total costs (taxes, maintenance, workers) without side effects.
        Used by AI for survival reserve. extra_workers_estimate: buffer for workers AI might rent this turn."""
        game = self.game
        current_year = int(next_year) - 1
        player_profit = self.calc_player_profit(player, current_year) if hasattr(self, 'calc_player_profit') else 0
        poll_tax, property_tax, income_tax = game.calc_taxes(player, next_year, player_profit)
        taxes = poll_tax + property_tax + income_tax
        line_maint = game.calc_maitnance_by_player(player) or 0
        trainset_maint = game.calc_trainset_maintenance(player) or 0
        resource = game.calc_resource_costs(player, next_year) or 0
        from worker import ENGINEER, CONSTRUCTION_WORKER
        worker_costs = 0
        for w in (ENGINEER, CONSTRUCTION_WORKER):
            rental = player.get_worker_rental(w.name)
            if rental:
                worker_costs += rental.get_yearly_cost(next_year)
        worker_costs += extra_workers_estimate * 600
        return taxes + line_maint + trainset_maint + resource + worker_costs

    def get_player_financial_summary(self, player, current_year: int):
        """Get financial summary for a player without modifying player state.
        Returns dict with all financial calculations (for Finance tab and display)."""
        player_profit = self.calc_player_profit(player)
        
        trainset_maintenance = self.game.calc_trainset_maintenance(player)
        resource_charge = self.game.calc_resource_costs(player)
        line_maintenance = self.game.calc_maitnance_by_player(player) or 0
        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, current_year, player_profit)
        worker_costs, is_on_strike = self.game.calc_worker_costs(player, current_year)
        
        total_deductions = trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs
        laws = getattr(self.game, '_government_laws', {})
        grants = laws.get('grants', {})
        is_favored = grants.get(self.get_player_id(player), 0) > 0
        government_grant = self._compute_transit_subsidy(player, current_year, is_favored)
        net = player_profit - total_deductions + government_grant
        
        return {
            'profit': player_profit,
            'trainset_maintenance': trainset_maintenance,
            'resource_charge': resource_charge,
            'line_maintenance': line_maintenance,
            'poll_tax': poll_tax,
            'property_tax': property_tax,
            'income_tax': income_tax,
            'worker_costs': worker_costs,
            'government_grant': government_grant,
            'total_deductions': total_deductions,
            'net': net,
            'is_on_strike': is_on_strike
        }
    
    def process_end_of_year(self, current_year: int):
        """Process end of year operations"""
        self.game.process_end_of_year(current_year)
    
    def check_and_unassign_trainsets_for_engineers(self, player, current_year: int):
        """Check engineer availability and unassign trainsets if needed"""
        self.game.check_and_unassign_trainsets_for_engineers(player, current_year)
    
    def check_and_eliminate_player(self, player):
        """Check if player should be eliminated and eliminate them"""
        return self.game.check_and_eliminate_player(player)
    
    def get_newly_available_vehicles(self, old_year: int, new_year: int):
        """Get newly available vehicles between two years"""
        return self.game.get_newly_available_vehicles(old_year, new_year)
    
    def check_agency_lawsuits(self, current_year: int):
        """Check for agency lawsuits"""
        return self.game.check_agency_lawsuits(current_year)
    
    def remove_player_trainset(self, player: Player, trainset):
        """Remove a trainset from a player"""
        player.remove_trainset(trainset)
    
    def remove_trainset_from_player_list(self, player: Player, trainset):
        """Remove a trainset from player's trainsets list"""
        if trainset in player.trainsets:
            player.trainsets.remove(trainset)
    
    def player_has_active_worker_strike(self, player: Player) -> bool:
        """True when any rented worker type is currently on strike."""
        rentals = getattr(player, 'worker_rentals', None) or {}
        for rental in rentals.values():
            if rental and hasattr(rental, 'is_on_strike') and rental.is_on_strike():
                return True
        return False
    
    def rent_workers_for_player(self, player: Player, worker_type, quantity: int, current_year: int):
        """Rent workers for a player. Blocked while any worker strike is in progress."""
        if self.player_has_active_worker_strike(player):
            raise ValueError(
                "Cannot hire workers while a strike is in progress. "
                "Raise salaries in the Workers tab to end the strike first."
            )
        player.rent_workers(worker_type, quantity, current_year)
        if getattr(self.game, 'war_active', False) and worker_type.name in player.worker_rentals:
            mult = float(getattr(self.game, 'war_new_worker_cost_multiplier', 1.25))
            rental = player.worker_rentals[worker_type.name]
            rental.demanded_multiplier = min(5.0, float(rental.demanded_multiplier) * mult)
    
    def calculate_worker_rental_cost(self, worker_type, quantity: int, current_year: int) -> int:
        """Calculate worker rental cost with supply and demand pricing"""
        base_yearly_cost = worker_type.get_yearly_cost(current_year)
        base_total = base_yearly_cost * quantity
        
        # Get available supply for supply/demand pricing
        available_supply = self.marketplace_get_worker_supply(worker_type.name)
        
        supply = max(1, available_supply)
        supply_demand_multiplier = 1.0 + (1.0 / supply) * 2.0
        supply_demand_multiplier = min(5.0, supply_demand_multiplier)
        
        adjusted_total = int(base_total * supply_demand_multiplier)
        if getattr(self.game, 'war_active', False):
            adjusted_total = int(adjusted_total * float(getattr(self.game, 'war_new_worker_cost_multiplier', 1.25)))
        return adjusted_total
    
    def fire_workers_for_player(self, player: Player, worker_type_name: str):
        """Fire workers for a player (remove from worker_rentals)"""
        if worker_type_name in player.worker_rentals:
            del player.worker_rentals[worker_type_name]
    
    def get_max_fireable_workers(self, player: Player, worker_type_name: str) -> int:
        """Max number of workers of this type the player can fire this turn.
        After Worker Protection Act (1925): at most half of that type can be fired;
        striking workers cannot be fired at all."""
        rental = self.get_player_worker_rental(player, worker_type_name)
        if not rental or rental.quantity == 0:
            return 0
        current_year = self.get_year()
        if current_year >= 1925:
            if rental.is_on_strike():
                return 0
            return max(0, rental.quantity // 2)
        return rental.quantity
    
    def get_war_start_roll_probability(self, calendar_year: int) -> float:
        """Per-year probability of a new war starting (after Pacifism law, if active for that year)."""
        g = self.game
        start_p = max(0.0, min(1.0, float(getattr(g, 'war_start_chance_per_year', 0.02))))
        until = getattr(g, '_pacifism_until_year', None)
        if until is not None and calendar_year < until:
            from game_presets import PACIFISM_WAR_START_CHANCE_MULTIPLIER
            start_p = max(0.0, min(1.0, start_p * PACIFISM_WAR_START_CHANCE_MULTIPLIER))
        return start_p

    def _war_remove_one_random_worker(self, player: Player) -> bool:
        """Remove one hired worker of a random type; return True if a worker was removed."""
        from worker import ENGINEER
        rentals = getattr(player, 'worker_rentals', None) or {}
        names = [wn for wn, rental in list(rentals.items()) if rental and rental.quantity > 0]
        if not names:
            return False
        wn = random.choice(names)
        rental = player.worker_rentals[wn]
        rental.quantity -= 1
        if rental.quantity <= 0:
            del player.worker_rentals[wn]
        self.marketplace_add_worker_supply(wn, 1)
        if wn == ENGINEER.name:
            self.check_and_unassign_trainsets_for_engineers(player, self.get_year())
        return True

    def _process_war_events(self, new_year: int) -> dict:
        """Start/end wars and run weighted drafts. Uses government influence: unfavored players lose more workers."""
        g = self.game
        out = {'war_started': False, 'war_ended': False, 'drafts': [], 'war_active': bool(getattr(g, 'war_active', False))}
        war_active = bool(getattr(g, 'war_active', False))
        start_p = self.get_war_start_roll_probability(new_year)
        end_p = max(0.0, min(1.0, float(getattr(g, 'war_end_chance_per_year', 0.3))))
        draft_p = max(0.0, min(1.0, float(getattr(g, 'war_worker_draft_chance_per_year', 0.5))))
        if not war_active:
            if random.random() < start_p:
                g.war_active = True
                out['war_started'] = True
        else:
            if random.random() < end_p:
                g.war_active = False
                out['war_ended'] = True
            elif random.random() < draft_p:
                players = [p for p in self.get_players() if not self.is_player_eliminated(p)]
                gov = g.get_government()
                if players:
                    weights = []
                    for p in players:
                        if gov:
                            pid = p.get_player_id()
                            u = float(gov.get_unfavorable_influence(pid)) + 0.25
                            f = float(gov.get_favorable_influence(pid)) + 0.25
                            weights.append(max(0.05, u / max(1e-6, f)))
                        else:
                            weights.append(1.0)
                    removals = random.randint(max(1, len(players)), max(2, 2 * len(players)))
                    lost = {p: 0 for p in players}
                    for _ in range(removals):
                        idx = random.choices(range(len(players)), weights=weights, k=1)[0]
                        p = players[idx]
                        if self._war_remove_one_random_worker(p):
                            lost[p] = lost.get(p, 0) + 1
                    for p, c in lost.items():
                        if c > 0:
                            out['drafts'].append({'player_name': self.get_player_name(p), 'count': c})
        out['war_active'] = bool(getattr(g, 'war_active', False))
        return out

    def fire_workers(self, player: Player, worker_type_name: str, quantity: int) -> dict:
        """Fire a number of workers of the given type. All game logic here (not in UI).
        Returns dict with success (bool), message (str), and optionally unassign_count (int).
        Validates max fireable (Worker Protection Act) and applies firing + marketplace + unassign."""
        rental = self.get_player_worker_rental(player, worker_type_name)
        if not rental or rental.quantity == 0:
            return {'success': False, 'message': f'No {worker_type_name}s to fire.'}
        max_fireable = self.get_max_fireable_workers(player, worker_type_name)
        if max_fireable == 0:
            return {'success': False, 'message': 'Cannot fire workers (Worker Protection Act: cannot fire striking workers).'}
        if quantity <= 0 or quantity > max_fireable:
            return {'success': False, 'message': f'You may fire at most {max_fireable} {worker_type_name}(s) per turn.'}
        from worker import ENGINEER
        assigned_count = self.get_player_assigned_trainsets_count(player) if worker_type_name == ENGINEER.name else 0
        new_count = rental.quantity - quantity
        unassign_count = 0
        if worker_type_name == ENGINEER.name and new_count < assigned_count:
            unassign_count = assigned_count - new_count
        rental.quantity = new_count
        if rental.quantity == 0:
            self.fire_workers_for_player(player, worker_type_name)
        self.marketplace_add_worker_supply(worker_type_name, quantity)
        if worker_type_name == ENGINEER.name and unassign_count > 0:
            self.check_and_unassign_trainsets_for_engineers(player, self.get_year())
        return {'success': True, 'message': f'Fired {quantity} {worker_type_name}(s).', 'quantity_fired': quantity, 'unassign_count': unassign_count}

    def ai_minimum_turn_action(self, player: Player, year: int) -> dict:
        """Guaranteed successful bookkeeping action when no other AI action is possible (e.g. broke, build queued).
        Keeps the invariant that every living AI registers at least one turn action. No gameplay state change."""
        return {
            'success': True,
            'year': year,
            'player_id': self.get_player_id(player),
        }

    def set_line_fare(self, line, fare: float):
        """Set the fare for a line (rounded to nearest $0.25)."""
        from inflation_utils import round_fare_to_quarter
        line.fare = round_fare_to_quarter(fare)

    def get_line_fare(self, line) -> float:
        from inflation_utils import round_fare_to_quarter
        return round_fare_to_quarter(getattr(line, 'fare', 0) or 0)

    def set_worker_salary_multiplier(self, worker_type_name: str, multiplier: float) -> None:
        """Set salary multiplier for a worker type (shared across players, same as Workers tab)."""
        from worker import CONSTRUCTION_WORKER, ENGINEER, LAWYER, MAINTENANCE_WORKER
        for wt in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
            if wt.name == worker_type_name:
                wt.set_salary_multiplier(float(multiplier))
                return
        raise ValueError(f"Unknown worker type: {worker_type_name!r}")
    
    def set_line_overhead_wire(self, line, has_wire: bool):
        """Set whether a line has overhead wire"""
        line.has_overhead_wire = has_wire
    
    def set_line_third_rail(self, line, has_rail: bool):
        """Set whether a line has third rail"""
        line.has_third_rail = has_rail
    
    def unassign_trainset_from_line(self, trainset, line_name: str = None):
        """Unassign a trainset from a line (or its current line when line_name is None)."""
        if line_name is None:
            line_name = trainset.get_line_assignment()
        if line_name:
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line and trainset in line.vehicles:
                line.vehicles.remove(trainset)
        trainset.assign_to_line(None)
    
    def remove_lawsuit_excuse_from_player(self, player: Player, excuse):
        """Remove a lawsuit excuse from a player"""
        player.remove_lawsuit_excuse(excuse)

    def try_file_lawsuit(self, plaintiff: Player, defendant: Player, excuse=None, made_up_cause: str = "") -> dict:
        """File and resolve a lawsuit between players. All game logic lives here.
        Returns dict with: winner, plaintiff_name, defendant_name, cause, damages, court_costs,
        plaintiff_lawyers, defendant_lawyers, result_message."""
        from lawsuit import Lawsuit
        if plaintiff == defendant:
            return {'success': False, 'message': 'Cannot sue yourself.'}
        if self.is_player_eliminated(plaintiff) or self.is_player_eliminated(defendant):
            return {'success': False, 'message': 'Cannot sue eliminated player.'}
        current_year = self.get_year()
        plaintiff_lawyers = self.get_player_available_lawyer_count(plaintiff)
        defendant_lawyers = self.get_player_available_lawyer_count(defendant)
        lawsuit = Lawsuit(plaintiff, defendant, excuse, current_year, made_up_cause)
        winner = lawsuit.resolve(plaintiff_lawyers, defendant_lawyers)
        if excuse:
            self.remove_lawsuit_excuse_from_player(plaintiff, excuse)
        court_costs = int(lawsuit.damages * 0.1)
        plaintiff_name = self.get_player_name(plaintiff)
        defendant_name = self.get_player_name(defendant)
        if winner == plaintiff:
            result_msg = (
                f"Lawsuit won by {plaintiff_name}!\n"
                f"Cause: {lawsuit.cause}\n"
                f"{defendant_name} must pay ${lawsuit.damages} in damages.\n"
                f"Both sides paid ${court_costs} in court costs.\n"
                f"Lawyers: {plaintiff_name} ({plaintiff_lawyers}) vs {defendant_name} ({defendant_lawyers})"
            )
        else:
            reparations = int(lawsuit.damages * 0.5)
            result_msg = (
                f"Lawsuit won by {defendant_name}!\n"
                f"Cause: {lawsuit.cause}\n"
                f"{plaintiff_name} must pay ${reparations} in reparations to {defendant_name}.\n"
                f"Both sides paid ${court_costs} in court costs.\n"
                f"Lawyers: {plaintiff_name} ({plaintiff_lawyers}) vs {defendant_name} ({defendant_lawyers})"
            )
        return {
            'success': True,
            'winner': winner,
            'plaintiff_name': plaintiff_name,
            'defendant_name': defendant_name,
            'cause': lawsuit.cause,
            'damages': lawsuit.damages,
            'court_costs': court_costs,
            'plaintiff_lawyers': plaintiff_lawyers,
            'defendant_lawyers': defendant_lawyers,
            'result_message': result_msg,
            'lawsuit': lawsuit,
        }

    def get_player_name(self, player: Player) -> str:
        return player.get_name()
    
    def get_player_money(self, player: Player) -> int:
        return player.get_money()
    
    def get_player_line_names(self, player: Player) -> list:
        return player.get_line_names()
    
    def is_player_eliminated(self, player: Player) -> bool:
        return player.is_player_eliminated()
    
    def get_player_worker_rental(self, player: Player, worker_type_name: str):
        return player.get_worker_rental(worker_type_name)
    
    def get_player_trainsets(self, player: Player) -> list:
        return player.get_trainsets()
    
    def get_line_route(self, line) -> list:
        return line.get_route()
    
    def get_line_stations(self, line) -> list:
        return line.get_stations()
    
    def get_line_frequency(self, line) -> int:
        """Get service frequency per year for a line"""
        if not line.vehicles:
            return 0
        fastest_speed = 0
        for vehicle in line.vehicles:
            if vehicle and hasattr(vehicle, 'locomotive') and vehicle.locomotive:
                if vehicle.locomotive.average_speed > fastest_speed:
                    fastest_speed = vehicle.locomotive.average_speed
        if fastest_speed == 0:
            return 0
        return line.service_frequency_per_year(trainset_avg_speed=fastest_speed)
    
    def get_stations_at_point(self, x: float, y: float, tolerance: float = 0.5) -> list:
        """Get all stations near a coordinate point (within tolerance distance)"""
        import math
        stations_at_point = []
        game_map = self.game.get_map()
        all_lines = game_map.get_lines_by_name()
        for line_name, line in all_lines.items():
            stations = line.get_stations()
            if not stations:
                continue
            for station in stations:
                # Use distance-based checking instead of exact match
                dist = math.hypot(station.get_x() - x, station.get_y() - y)
                if dist <= tolerance:
                    stations_at_point.append((station, line_name, line))
        return stations_at_point
    
    def rename_station(self, station, new_name: str):
        """Rename a station; name must be unique for each owning company (case-insensitive)."""
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Station name cannot be empty.")
        for p in self._station_players(station):
            self.assert_station_name_unique_for_player(p, new_name, exclude_station=station)
        station.name = new_name
    
    def get_station_name(self, station) -> str:
        """Get station name"""
        return station.name

    def get_station_throughput(self, station, current_year: int = None) -> int:
        """Estimated annual passenger throughput at a station (share of serving lines' ridership)."""
        line_names = getattr(station, 'line_names', None) or (
            station.get_line_names() if hasattr(station, 'get_line_names') else []
        )
        if not line_names:
            return 0
        if current_year is None:
            current_year = self.get_year()
        ridership_data = self.get_ridership_data_for_year(current_year)
        map_lines = self.get_map_lines() or {}
        total = 0
        for line_name in line_names:
            line = map_lines.get(line_name)
            if not line:
                continue
            passengers = int(ridership_data.get(line_name, 0) or 0)
            stations = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
            share = max(1, len(stations))
            total += passengers // share
        return total
    
    def close_station(self, player, station) -> dict:
        """Close a station. Fails if station serves a line owned by another player.
        Returns {'success': True} or {'success': False, 'message': str}."""
        line_names = getattr(station, 'line_names', None) or (getattr(station, 'get_line_names', lambda: [])() if callable(getattr(station, 'get_line_names', None)) else [])
        all_lines = self.get_map_lines()
        for line_name in (line_names or []):
            line = all_lines.get(line_name)
            if line:
                owner = self.get_line_owner(line)
                if owner is not None and owner != player:
                    return {'success': False, 'message': f"Cannot close station: it serves line '{line_name}' owned by another player."}
        for line_name, line in all_lines.items():
            if hasattr(line, 'stations') and station in line.stations:
                line.remove_station(station)
        game_map = self.game.get_map()
        if hasattr(game_map, 'stations') and station in game_map.stations:
            game_map.stations.remove(station)
        return {'success': True}

    def modify_station_lines(self, station, line_names_to_add: list = None, line_names_to_remove: list = None):
        """Add or remove lines from a station"""
        if line_names_to_add:
            for line_name in line_names_to_add:
                station.add_line(line_name)
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line and station not in line.get_stations():
                    line.add_station(station)
        if line_names_to_remove:
            for line_name in line_names_to_remove:
                if line_name in station.line_names:
                    station.line_names.remove(line_name)
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line:
                    line.remove_station(station)
    
    
    def calc_line_maintenance(self, line, current_year: int = None) -> int:
        """Full line maintenance including schedule operating cost when ``current_year`` is set (matches year-end finances)."""
        if current_year is None and self.game is not None:
            current_year = self.game.get_year()
        return calc_line_maintenance_with_off_street_surcharge(
            self.get_map(), line, int(current_year or 0),
        )
    
    def marketplace_add_trainsets_for_new_technology(self, new_year: int, newly_available: list):
        marketplace = self.game.get_marketplace()
        marketplace.add_trainsets_for_new_technology(new_year, newly_available)
    
    def marketplace_add_worker_supply(self, worker_type_name: str, quantity: int):
        marketplace = self.game.get_marketplace()
        marketplace.add_worker_supply(worker_type_name, quantity)
    
    def marketplace_deduct_worker_supply(self, worker_type_name: str, quantity: int):
        marketplace = self.game.get_marketplace()
        marketplace.deduct_worker_supply(worker_type_name, quantity)
    
    def event_system_check_and_create_population_event(self, year: int):
        event_system = self.game.get_event_system()
        return event_system.check_and_create_population_event(year)
    
    def event_system_get_active_events(self, year: int):
        event_system = self.game.get_event_system()
        return event_system.get_active_events(year)
    
    def event_system_check_and_create_natural_disaster(self, year: int):
        """Check and create natural disaster event if conditions are met"""
        return self.game.get_event_system().check_and_create_natural_disaster(year)
    
    def get_trainset_line_assignment(self, trainset) -> str:
        return trainset.get_line_assignment()
    
    def get_trainset_cars(self, trainset) -> list:
        return trainset.cars if hasattr(trainset, 'cars') else []
    
    def get_trainset_locomotive(self, trainset):
        return trainset.locomotive
    
    def marketplace_get_available_lines(self):
        marketplace = self.game.get_marketplace()
        from game_presets import UNOWNED_LINE_BASE_FEE, MARKETPLACE_REPLENISHMENT_START_YEAR
        result = list(marketplace.get_available_lines())
        game_map = self.game.get_map()
        for line in game_map.get_lines_by_name().values():
            if line.get_owner() is None:
                fee = UNOWNED_LINE_BASE_FEE
                sale_item = SaleItem(
                    item=line,
                    price=fee,
                    seller="Unowned",
                    year_added=MARKETPLACE_REPLENISHMENT_START_YEAR,
                )
                result.append(sale_item)
        return result
    
    def marketplace_get_available_vehicles(self):
        marketplace = self.game.get_marketplace()
        return marketplace.get_available_vehicles()
    
    def marketplace_get_worker_supply(self, worker_type_name: str) -> int:
        marketplace = self.game.get_marketplace()
        return marketplace.get_worker_supply(worker_type_name)
    
    def get_resource_costs(self, current_year: int = None):
        """Get current resource costs based on historical pricing. Returns dict of resource_name -> cost per unit."""
        if current_year is None:
            current_year = self.game.get_year()
        
        base_costs = {
            'Coal': 50,
            'Wood': 30,
            'Oil': 80,
            'Electricity': 20,  # Base cost - never free, will be adjusted by year
        }
        
        year_adjustments = {}
        
        if current_year < 1850:
            year_adjustments['Coal'] = 0.8  # 80% of base (cheaper early on)
        elif current_year < 1900:
            year_adjustments['Coal'] = 1.0  # Base price
        elif current_year < 1950:
            year_adjustments['Coal'] = 1.2  # 20% increase
        elif current_year < 1970:
            year_adjustments['Coal'] = 1.5  # 50% increase
        else:
            year_adjustments['Coal'] = 2.0  # 100% increase (environmental costs)
        
        if current_year < 1850:
            year_adjustments['Wood'] = 0.6  # Very cheap early
        elif current_year < 1900:
            year_adjustments['Wood'] = 1.0  # Base price
        elif current_year < 1950:
            year_adjustments['Wood'] = 1.8  # 80% increase
        else:
            year_adjustments['Wood'] = 3.0  # 200% increase
        
        if current_year < 1870:
            year_adjustments['Oil'] = 2.0  # Very expensive early (rare)
        elif current_year < 1900:
            year_adjustments['Oil'] = 1.5  # Still expensive
        elif current_year < 1940:
            year_adjustments['Oil'] = 1.0  # Base price (standardized production)
        elif current_year < 1970:
            year_adjustments['Oil'] = 0.9  # Slightly cheaper (post-war boom)
        else:
            year_adjustments['Oil'] = 1.5  # 50% increase (oil crisis era)
        
        # Electricity always costs money (never free)
        if current_year < 1880:
            year_adjustments['Electricity'] = 0.3  # Early experimental (cheap but not free)
        elif current_year < 1920:
            year_adjustments['Electricity'] = 0.7  # Early commercialization
        elif current_year < 1950:
            year_adjustments['Electricity'] = 1.0  # Base price
        else:
            year_adjustments['Electricity'] = 1.2  # 20% increase
        
        resource_costs = {}
        for resource, base_cost in base_costs.items():
            adjustment = year_adjustments.get(resource, 1.0)
            resource_costs[resource] = int(base_cost * adjustment)
        
        return resource_costs
    
    def marketplace_update_notify(self):
        """Notify that marketplace has changed. This should be called whenever marketplace state changes.
        Returns True if marketplace was updated, False otherwise."""
        return True
    
    def marketplace_remove_vehicle(self, vehicle):
        marketplace = self.game.get_marketplace()
        marketplace.remove_vehicle(vehicle)
    
    def buy_vehicle_complete(self, sale_item, player: Player, current_year: int) -> dict:
        """Complete vehicle purchase operation. All game logic in ui.py.
        Returns dict with purchase info. AI difficulty applies to marketplace price."""
        from vehicle import Trainset, Locomotive, Railcar
        
        vehicle = sale_item.get_item()
        base_price = sale_item.get_price()
        price = self.get_effective_marketplace_price_for_player(player, base_price)
        
        if self.get_player_money(player) < price:
            raise ValueError(f"Player {self.get_player_name(player)} cannot afford this purchase (needs ${price}, has ${self.get_player_money(player)})")
        
        if sale_item.get_quantity_available() <= 0:
            raise ValueError("This vehicle is no longer available.")
        
        if isinstance(vehicle, Trainset):
            year = self.get_trainset_locomotive_year(vehicle)
            if year and year > current_year:
                raise ValueError(f"This trainset is not available until year {year}.")
        
        self.deduct_player_money(player, price, reason='Marketplace purchase')
        
        marketplace = self.game.get_marketplace()
        
        from line import Line
        is_line = isinstance(vehicle, Line)
        target_list = marketplace.available_lines if is_line else marketplace.available_vehicles
        
        removed = False
        if sale_item in target_list:
            # Exact match - this works when sale_item is the actual object from marketplace
            sale_item.deduct_quantity()
            if sale_item.get_quantity_available() <= 0:
                target_list.remove(sale_item)
            removed = True
        else:
            # After deserialization, sale_item is a new object, so we need to find it in the correct list
            # by matching the vehicle AND finding the first SaleItem that matches (since quantities are synced)
            for existing_sale_item in list(target_list):  # Use list() to avoid modification during iteration
                existing_vehicle = existing_sale_item.get_item()
                
                # Match vehicles by properties
                vehicles_match = False
                if existing_vehicle == vehicle:
                    # Exact object match
                    vehicles_match = True
                elif hasattr(existing_vehicle, 'object_id') and hasattr(vehicle, 'object_id'):
                    # Match by object_id if available
                    if (existing_vehicle.object_id is not None and vehicle.object_id is not None and
                        existing_vehicle.object_id == vehicle.object_id and 
                        hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and
                        existing_vehicle.name == vehicle.name):
                        vehicles_match = True
                elif hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name'):
                    # Match by name and type as fallback
                    if existing_vehicle.name == vehicle.name and type(existing_vehicle) == type(vehicle):
                        vehicles_match = True
                
                if vehicles_match:
                    # Found matching SaleItem - deduct quantity
                    existing_sale_item.deduct_quantity()
                    if existing_sale_item.get_quantity_available() <= 0:
                        target_list.remove(existing_sale_item)  # Use target_list, not hardcoded available_vehicles
                    removed = True
                    break
            
            if not removed:
                # Last resort: try marketplace_remove_vehicle (which does more complex matching)
                self.marketplace_remove_vehicle(vehicle)
                removed = True  # Assume it worked
        
        # Add to player inventory
        if isinstance(vehicle, Trainset):
            self.acquire_trainset_for_player(player, vehicle)
            item_name = self.get_trainset_locomotive_name(vehicle)
            capacity = self.get_trainset_capacity(vehicle)
        elif isinstance(vehicle, Locomotive):
            self.acquire_locomotive_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        elif isinstance(vehicle, Ferry):
            self.acquire_ferry_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        elif isinstance(vehicle, Bus):
            self.acquire_bus_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        elif isinstance(vehicle, Railcar):
            self.acquire_railcar_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        else:
            item_name = getattr(vehicle, 'name', 'Unknown')
            capacity = getattr(vehicle, 'capacity', 0)
        
        return {
            'item': vehicle,
            'item_name': item_name,
            'price': price,
            'remaining_money': self.get_player_money(player),
            'capacity': capacity
        }
    
    def marketplace_add_vehicle(self, vehicle, price: int, seller: str):
        marketplace = self.game.get_marketplace()
        marketplace.add_vehicle(vehicle, price=price, seller=seller, current_year=self.game.get_year())
    
    def sell_trainset_for_half_price(self, trainset, player: Player) -> dict:
        """Sell a trainset for half its original purchase price.
        All game logic in ui.py. Returns dict with sale info."""
        from vehicle import Trainset
        if not isinstance(trainset, Trainset):
            raise ValueError("Can only sell trainsets")
        player_trainsets = player.get_trainsets()
        if trainset not in player_trainsets:
            raise ValueError(f"Player {self.get_player_name(player)} does not own this trainset")
        current_year = self.get_year()
        original_price = trainset.get_price(current_year)
        sale_price = original_price // 2
        player_trainsets.remove(trainset)
        if trainset.get_line_assignment():
            line_name = trainset.get_line_assignment()
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line and trainset in line.vehicles:
                line.vehicles.remove(trainset)
            trainset.assign_to_line(None)
        self.add_player_money(player, sale_price, reason='Trainset sale')
        return {
            'trainset': trainset,
            'original_price': original_price,
            'sale_price': sale_price,
            'new_money': player.get_money()
        }
    
    def sell_locomotive_for_half_price(self, locomotive, player: Player) -> dict:
        """Sell a locomotive for half its original purchase price.
        All game logic in ui.py. Returns dict with sale info."""
        from vehicle import Locomotive
        if not isinstance(locomotive, Locomotive):
            raise ValueError("Can only sell locomotives")
        player_locomotives = getattr(player, 'locomotives', [])
        if locomotive not in player_locomotives:
            raise ValueError(f"Player {self.get_player_name(player)} does not own this locomotive")
        current_year = self.get_year()
        original_price = locomotive.get_price(current_year)
        sale_price = original_price // 2
        player_locomotives.remove(locomotive)
        self.add_player_money(player, sale_price, reason='Locomotive sale')
        return {
            'locomotive': locomotive,
            'original_price': original_price,
            'sale_price': sale_price,
            'new_money': player.get_money()
        }
    
    def sell_railcar_for_half_price(self, railcar, player: Player) -> dict:
        """Sell a railcar for half its original purchase price.
        All game logic in ui.py. Returns dict with sale info."""
        from vehicle import Railcar
        if not isinstance(railcar, Railcar):
            raise ValueError("Can only sell railcars")
        player_railcars = getattr(player, 'railcars', [])
        if railcar not in player_railcars:
            raise ValueError(f"Player {self.get_player_name(player)} does not own this railcar")
        current_year = self.get_year()
        original_price = railcar.get_price(current_year)
        sale_price = original_price // 2
        player_railcars.remove(railcar)
        self.add_player_money(player, sale_price, reason='Railcar sale')
        return {
            'railcar': railcar,
            'original_price': original_price,
            'sale_price': sale_price,
            'new_money': player.get_money()
        }

    def get_historical_society_items(self, player: Player, current_year: int) -> dict:
        """Eligible trainsets and spare locomotives (100+ years old). RULES.md: 5x catalog base price."""
        from vehicle import Trainset, Locomotive
        out_trainsets = []
        for ts in player.get_trainsets():
            loco = getattr(ts, 'locomotive', None)
            if loco is None or loco.year_introduced is None:
                continue
            years_old = current_year - loco.year_introduced
            if years_old < 100:
                continue
            price = ts.get_historical_society_price(current_year)
            if price <= 0:
                continue
            out_trainsets.append({
                'type': 'trainset',
                'item': ts,
                'name': getattr(ts.locomotive, 'name', 'Trainset'),
                'years_old': years_old,
                'price': price,
            })
        out_locos = []
        for loco in getattr(player, 'locomotives', []) or []:
            if not isinstance(loco, Locomotive) or loco.year_introduced is None:
                continue
            years_old = current_year - loco.year_introduced
            if years_old < 100:
                continue
            price = loco.get_historical_society_price(current_year)
            if price <= 0:
                continue
            out_locos.append({
                'type': 'locomotive',
                'item': loco,
                'name': loco.name,
                'years_old': years_old,
                'price': price,
            })
        return {'trainsets': out_trainsets, 'locomotives': out_locos}

    def sell_to_historical_society_complete(self, item, item_type: str, player: Player, current_year: int) -> dict:
        """Sell a trainset or spare locomotive to the Historical Society (5x catalog base, 100+ years)."""
        from vehicle import Trainset, Locomotive
        if current_year is None:
            current_year = self.get_year()
        if item_type == 'trainset':
            if not isinstance(item, Trainset):
                raise ValueError("Item must be a trainset")
            if item not in player.get_trainsets():
                raise ValueError(f"Player {self.get_player_name(player)} does not own this trainset")
            price = item.get_historical_society_price(current_year)
            if price <= 0:
                raise ValueError("Trainset is not eligible (must be 100+ years since locomotive introduction).")
            player.remove_trainset(item)
            if item.get_line_assignment():
                line_name = item.get_line_assignment()
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line and item in line.vehicles:
                    line.vehicles.remove(item)
                item.assign_to_line(None)
            self.add_player_money(player, price, reason='Historical Society')
            return {'price': price, 'new_money': player.get_money(), 'item_type': 'trainset'}
        if item_type == 'locomotive':
            if not isinstance(item, Locomotive):
                raise ValueError("Item must be a locomotive")
            locos = getattr(player, 'locomotives', []) or []
            if item not in locos:
                raise ValueError(f"Player {self.get_player_name(player)} does not own this locomotive")
            price = item.get_historical_society_price(current_year)
            if price <= 0:
                raise ValueError("Locomotive is not eligible (must be 100+ years since introduction).")
            locos.remove(item)
            self.add_player_money(player, price, reason='Historical Society')
            return {'price': price, 'new_money': player.get_money(), 'item_type': 'locomotive'}
        raise ValueError(f"Unknown item_type: {item_type!r}")

    def get_player_government_retention_bonus(self, player, prior_year: int) -> float:
        """Return 0.0-1.0: higher = government less likely to change in election year.
        Based on: no worker strikes, paying workers well, not losing lawsuits."""
        bonus = 0.5
        from worker import ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER
        for worker_type in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
            rental = player.get_worker_rental(worker_type.name) if hasattr(player, 'get_worker_rental') else None
            if not rental:
                continue
            if rental.is_on_strike():
                bonus -= 0.25
            elif rental.demanded_multiplier > 0:
                current = worker_type.get_salary_multiplier()
                if current < rental.demanded_multiplier - 0.3:
                    bonus -= 0.15
        last_loser = getattr(self.game, '_last_lawsuit_loser_player_id', None)
        if last_loser is not None and last_loser == self.get_player_id(player):
            bonus -= 0.2
        return max(0.0, min(1.0, bonus))

    def advance_year_complete(self, old_year: int):
        """Complete year advancement - handles all year-end operations and returns summary data"""
        new_year = old_year + 1
        gov = self.game.get_government()
        lobby_seat_notifications = []
        election_held = False
        if gov:
            # Apply pending seat lobbies (queued during the turn; take effect at end of turn)
            pending = getattr(self.game, '_pending_seat_lobbies', [])
            for action, seat_idx, player_id in pending:
                if action == 'tiebreaker':
                    prev_tb = gov._tiebreaker
                    gov.lobby_flip_tiebreaker(player_id)
                    if prev_tb is not None and prev_tb != player_id:
                        lobby_seat_notifications.append({
                            'type': 'tiebreaker',
                            'lobbyist_name': self.get_player_name(next((p for p in self.get_players() if self.get_player_id(p) == player_id), None)),
                            'previous_owner_id': prev_tb,
                            'success': True,
                        })
                elif action == 'seat':
                    if seat_idx >= 0:
                        prev_owner = gov._seats[seat_idx] if seat_idx < len(gov._seats) else None
                        gov.lobby_flip_seat_at_index(seat_idx, player_id)
                        if prev_owner is not None and prev_owner != player_id:
                            lobby_seat_notifications.append({
                                'type': 'seat',
                                'seat_index': seat_idx,
                                'lobbyist_name': self.get_player_name(next((p for p in self.get_players() if self.get_player_id(p) == player_id), None)),
                                'previous_owner_id': prev_owner,
                                'success': True,
                            })
                    else:
                        ok, idx, prev_owner = gov.lobby_flip_seat(player_id)
                        if ok and prev_owner is not None and prev_owner != player_id:
                            lobby_seat_notifications.append({
                                'type': 'seat',
                                'seat_index': idx,
                                'lobbyist_name': self.get_player_name(next((p for p in self.get_players() if self.get_player_id(p) == player_id), None)),
                                'previous_owner_id': prev_owner,
                                'success': True,
                            })
            self.game._pending_seat_lobbies = []
            retention = {}
            eliminated_ids = {self.get_player_id(p) for p in self.get_players() if self.is_player_eliminated(p)}
            for p in self.get_players():
                if not self.is_player_eliminated(p):
                    retention[self.get_player_id(p)] = self.get_player_government_retention_bonus(p, old_year)
            election_held = bool(
                gov.hold_election(
                    new_year,
                    player_retention_bonuses=retention,
                    eliminated_player_ids=eliminated_ids,
                )
            )
            self.game._government_laws = gov.pass_annual_laws(new_year, eliminated_player_ids=eliminated_ids)
            new_government_laws = []
            random_effects = gov.pass_random_government_laws(new_year, eliminated_player_ids=eliminated_ids)
            if random_effects:
                new_government_laws = random_effects
                self._apply_random_government_law_effects(new_year, random_effects)
            self.game._new_government_laws_this_year = new_government_laws
        else:
            self.game._government_laws = {}
            self.game._new_government_laws_this_year = []
            election_held = False
        war_events = self._process_war_events(new_year)
        strike_warnings_data = self.get_strike_warnings(old_year)
        population_event = self.event_system_check_and_create_population_event(old_year)
        event_population_change = 0.0
        if population_event:
            event_population_change = population_event.change_percent
        self.advance_turn(event_population_change)
        new_year = self.get_year()
        self.invalidate_ridership_cache()
        natural_disaster = self.event_system_check_and_create_natural_disaster(new_year)
        natural_disaster_info = None
        if natural_disaster:
            self.apply_natural_disaster_damage(natural_disaster, new_year)
            natural_disaster_info = self.format_natural_disaster_info(natural_disaster, new_year)
        
        # Check for new events
        active_events = self.event_system_get_active_events(new_year)
        newly_triggered_events = [e for e in active_events if e.year == new_year]
        newly_available = self.get_newly_available_vehicles(old_year, new_year)
        if newly_available:
            self.marketplace_add_trainsets_for_new_technology(new_year, newly_available)
        self.process_end_of_year(new_year)
        
        # Auto-assign unassigned construction workers to top jobs
        all_players = list(self.get_players())
        if self.public_ta is not None and self.public_ta.established:
            all_players.append(self.public_ta)
        for player in all_players:
            if not self.is_player_eliminated(player):
                self.auto_assign_unassigned_workers(player)
        # Complete construction from the year that just ended so profit/finances see the new lines and stations
        self.process_build_queue()
        game_map = self.game.get_map()
        has_stations = hasattr(game_map, 'stations') and game_map.stations and len(game_map.stations) > 0
        map_lines = self.get_map_lines()
        if has_stations:
            ridership_data = self.get_ridership_data_for_year(new_year)
            # Pre-calculate and cache profit for all lines now that ridership is available
            # This ensures get_line_stats and process_player_finances use cache only (no calculations)
            for _line_name, line in map_lines.items():
                profit = self.calculate_line_profit_with_ridership(line, new_year, ridership_data)
                line._cached_profit = profit
                line._cached_profit_year = new_year
        else:
            ridership_data = {}
            for _line_name, line in map_lines.items():
                line._cached_profit = 0
                line._cached_profit_year = new_year
        
        # Process finances for all players (now uses cached profit values only - fast)
        player_finances = []
        strike_messages = []
        for player in all_players:
            finances = self.process_player_finances(player, new_year)
            try:
                from ai import record_ai_settled_annual_net, record_player_money_at_year_end
                record_player_money_at_year_end(player, new_year, self.get_player_money(player))
                if getattr(player, 'is_ai', False):
                    record_ai_settled_annual_net(self, player, new_year, finances.get('net', 0))
            except Exception:
                pass
            player_finances.append({
                'player': player,
                'finances': finances
            })
            # Track strike messages (only for active players - no strikes for dead players)
            if not self.is_player_eliminated(player) and finances['is_on_strike']:
                from worker import ENGINEER
                engineer_rental = self.get_player_worker_rental(player, ENGINEER.name)
                if engineer_rental:
                    strike_messages.append({
                        'player_name': self.get_player_name(player),
                        'quantity': engineer_rental.quantity,
                        'worker_name': ENGINEER.name,
                        'turns_remaining': engineer_rental.strike_turns_remaining,
                        'demanded_multiplier': engineer_rental.demanded_multiplier
                    })

        # Before eliminating bankrupt players, allow doomed AIs a last-chance merger so two failing AIs
        # can combine instead of both being eliminated.
        if getattr(self.game, 'merge_enabled', False):
            self._process_ai_doomed_merges()

        # Now perform elimination and post-finance maintenance for all players (after potential AI-AI mergers)
        for player in all_players:
            if not self.is_player_eliminated(player):
                was_eliminated = self.check_and_eliminate_player(player)
                if was_eliminated:
                    # Player was eliminated this year
                    continue
                # Check engineer availability for surviving players
                self.check_and_unassign_trainsets_for_engineers(player, new_year)
        
        lawsuit_result = self.check_agency_lawsuits(new_year)
        
        self.handle_public_ta(new_year)
        government_funding = self.distribute_government_funding(new_year)
        self.game.apply_lobby_effects(new_year)
        self._complete_law_drafts(new_year)

        # Random AI spawn: add new AI every 50-100 years if enabled
        new_ai_spawned = None
        if getattr(self.game, 'random_ai_spawn_enabled', False):
            next_spawn = getattr(self.game, '_next_random_ai_spawn_year', None)
            if next_spawn is not None and new_year >= next_spawn:
                new_ai_spawned = self._try_spawn_random_ai(new_year)
                if new_ai_spawned:
                    interval = getattr(self.game, 'random_ai_spawn_interval_years', 30)
                    half = max(5, interval // 2)
                    self.game._next_random_ai_spawn_year = new_year + random.randint(interval - half, interval + half)

        # Merge: AIs merge with other AIs first (more than with humans); then remaining desperate AIs propose to humans
        merge_proposals = []
        ai_ai_merges = []
        if getattr(self.game, 'merge_enabled', False):
            # AI-AI merges first (repeat until no more pairs so AIs merge with AIs more than with humans)
            while True:
                performed = self._process_ai_ai_merges()
                if not performed:
                    break
                ai_ai_merges.extend(performed)
            # Remaining desperate AIs propose to human players
            merge_proposals = self.get_ai_merge_proposals()

        result = {
            'new_year': new_year,
            'new_ai_spawned': new_ai_spawned,
            'merge_proposals': merge_proposals,
            'ai_ai_merges': ai_ai_merges,
            'strike_warnings': strike_warnings_data,
            'population_event': population_event,
            'natural_disaster': natural_disaster,
            'natural_disaster_info': natural_disaster_info,
            'lawsuit_result': lawsuit_result,
            'newly_triggered_events': newly_triggered_events,
            'newly_available_vehicles': newly_available,
            'player_finances': player_finances,
            'strike_messages': strike_messages,
            'active_players': self.get_active_players(),
            'government_laws': self.game._government_laws,
            'new_government_laws': getattr(self.game, '_new_government_laws_this_year', []),
            'lobby_seat_notifications': lobby_seat_notifications,
            'election_held': election_held,
            'government_funding': government_funding,
            'war_events': war_events,
        }
        result['news_articles'] = self._generate_news_articles_for_year(new_year, result)
        return result

    def is_player_desperate(self, player: Player) -> bool:
        """True if player is in extremely desperate situation (eligible for merger).
        Desperate = money < 500 AND (no lines OR no profitable lines)."""
        if self.is_player_eliminated(player):
            return False
        money = self.get_player_money(player)
        if money >= 500:
            return False
        lines = player.get_line_names()
        if not lines:
            return True
        current_year = self.get_year()
        map_lines = self.get_map_lines()
        for line_name in lines:
            line = map_lines.get(line_name)
            if line:
                profit = self.calc_line_profit(line, current_year) if hasattr(self, 'calc_line_profit') else 0
                if profit > 0:
                    return False
        return True

    def try_ai_desperate_merge_no_lines(self, player: Player) -> Optional[dict]:
        """During an AI turn: if the AI is desperate and has no lines, attempt to merge into another AI.
        Uses the same stochastic reject chance as year-end AI–AI merges. Returns merge_players result on success,
        or {'success': False, 'rejected': True} when the merge is declined, or {'success': False, 'message': ...}."""
        if not getattr(player, 'is_ai', False) or self.is_player_eliminated(player):
            return {'success': False, 'message': 'Not an eligible AI'}
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_ai_enabled', True):
            return {'success': False, 'message': 'AI–AI merges disabled'}
        if not self.merge_ai_consideration_allowed_this_year():
            return {'success': False, 'message': 'AI mergers are not considered yet this early in the timeline.'}
        if player.get_line_names():
            return {'success': False, 'message': 'AI has lines'}
        if not self.is_player_desperate(player):
            return {'success': False, 'message': 'AI is not desperate'}
        others = [
            p for p in self.get_active_players()
            if getattr(p, 'is_ai', False) and p is not player and not self.is_player_eliminated(p)
        ]
        if not others:
            return {'success': False, 'message': 'No other AI to merge with'}
        import random as _r
        reject_chance = getattr(self.game, 'merge_ai_reject_chance_pct', None)
        if reject_chance is None:
            reject_chance = 90.0
        reject_chance = float(reject_chance)
        rng = _r.Random(
            (self.get_year() * 10007 + self.get_player_id(player) * 131 + len(others)) & 0xFFFFFFFF
        )
        if rng.random() < (reject_chance / 100.0):
            return {'success': False, 'rejected': True}
        acquirer = max(others, key=lambda p: (len(p.get_line_names()), self.get_player_money(p)))
        return self.merge_players(acquirer, player)

    def merge_players(self, acquirer: Player, target: Player) -> dict:
        """Merge target into acquirer. Acquirer gets all assets. Target is eliminated (or, if human, becomes co-controller via merged_into_player_id).
        Company name becomes a merge of both names (e.g. Pennsylvania Railroad + New York Central -> Penn Central).
        Valid when merge_enabled and the specific merge type is allowed (AI-AI, AI-human, human-human). Returns result dict."""
        if not getattr(self.game, 'merge_enabled', False):
            return {'success': False, 'message': 'Mergers are not enabled'}
        if self.is_player_eliminated(acquirer) or self.is_player_eliminated(target):
            return {'success': False, 'message': 'Cannot merge eliminated player'}
        acquirer_is_ai = getattr(acquirer, 'is_ai', False)
        target_is_ai = getattr(target, 'is_ai', False)
        if acquirer_is_ai and not target_is_ai:
            return {'success': False, 'message': 'Only human players can propose a merger (acquire another company)'}
        if acquirer is target:
            return {'success': False, 'message': 'Cannot merge with yourself'}
        if self.merge_ai_blocked_early_calendar_years(acquirer, target):
            return {'success': False, 'message': 'Mergers involving an AI are blocked during the first years of the timeline.'}
        # Enforce per-type merge settings
        if acquirer_is_ai and target_is_ai:
            if not getattr(self.game, 'merge_ai_ai_enabled', True):
                return {'success': False, 'message': 'Mergers between AIs are not allowed in this game'}
        elif not acquirer_is_ai and target_is_ai:
            if not getattr(self.game, 'merge_ai_human_enabled', True):
                return {'success': False, 'message': 'Mergers between humans and AIs are not allowed in this game'}
        elif not acquirer_is_ai and not target_is_ai:
            if not getattr(self.game, 'merge_human_human_enabled', True):
                return {'success': False, 'message': 'Mergers between humans are not allowed in this game'}
        current_year = self.get_year()
        map_lines = self.get_map_lines()
        for line_name in list(target.get_line_names()):
            line = map_lines.get(line_name)
            if line and line.get_owner() == target:
                line.owner = acquirer
                if line.name not in acquirer.get_line_names():
                    acquirer.acquire_line(line)
                target.remove_line(line_name)
        for ts in list(target.get_trainsets()):
            target.remove_trainset(ts)
            acquirer.aquire_trainset(ts)
        from worker import ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER
        for worker_type in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
            target_rental = target.get_worker_rental(worker_type.name)
            if target_rental and target_rental.quantity > 0:
                acquirer_rental = acquirer.get_worker_rental(worker_type.name)
                if acquirer_rental:
                    acquirer_rental.quantity += target_rental.quantity
                else:
                    acquirer.rent_workers(worker_type, target_rental.quantity, current_year)
        cash = int(target.get_money() or 0)
        if cash > 0:
            # Use Operations wrappers so AI money log records this transfer.
            self.add_player_money(acquirer, cash, reason=f"Merger: acquired cash from {target.get_name()}")
            self.deduct_player_money(target, cash, reason=f"Merger: cash transferred to {acquirer.get_name()}")
        merged_name = _merge_company_names(acquirer.get_name(), target.get_name())
        acquirer.rename(merged_name)
        target_eliminated_name = target.get_name()
        target_is_human = not target_is_ai
        if target_is_human:
            # Human merged into human: target keeps their seat and controls the same company (shared tab).
            target.merged_into_player_id = acquirer.get_player_id()
            # Do not eliminate; target still has a tab and both can control the merged company.
        else:
            target.eliminate()
            target.line_names.clear()
            target.trainsets.clear()
        return {'success': True, 'acquirer': acquirer, 'target': target, 'target_name': target_eliminated_name}

    def _process_ai_doomed_merges(self) -> list:
        """Before elimination, merge pairs of AIs that are effectively bankrupt.
        Any two AI players with money < 0 this year have a high (90%) chance to merge instead of both dying.
        Returns list of (acquirer, target) pairs that were merged."""
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_ai_enabled', True):
            return []
        if not self.merge_ai_consideration_allowed_this_year():
            return []
        import random as _r
        ais = [p for p in self.get_active_players() if getattr(p, 'is_ai', False)]
        # "Doomed" = negative cash after finances (same threshold as bankruptcy)
        doomed = [a for a in ais if self.get_player_money(a) < 0 and not self.is_player_eliminated(a)]
        if len(doomed) < 2:
            return []
        merged = []
        used = set()
        # Deterministic-ish per-year seed so behavior is stable across reruns of the same game state
        rng = _r.Random(self.get_year() * 10007 + len(doomed) * 17)
        for target in doomed:
            if target in used or self.is_player_eliminated(target):
                continue
            others = [a for a in doomed if a is not target and a not in used and not self.is_player_eliminated(a)]
            if not others:
                continue
            # merge_ai_reject_chance_pct chance to reject (e.g. 90% reject = 10% accept)
            reject_chance = getattr(self.game, 'merge_ai_reject_chance_pct', None)
            if reject_chance is None:
                reject_chance = 90.0
            reject_chance = float(reject_chance)
            if rng.random() < (reject_chance / 100.0):
                continue
            # Choose acquirer: the stronger of the pair (more lines, then more money)
            acquirer = max(others, key=lambda p: (len(p.get_line_names()), self.get_player_money(p)))
            result = self.merge_players(acquirer, target)
            if result.get('success'):
                merged.append((acquirer, target))
                used.add(target)
        return merged

    def _process_ai_ai_merges(self) -> list:
        """Process AI-AI merges: desperate AIs merge with each other or with struggling AIs (negative money).
        At least one of the pair must be desperate so two AIs in bad shape are inclined to merge.
        Returns list of (acquirer, target) for each merge performed."""
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_ai_enabled', True):
            return []
        if not self.merge_ai_consideration_allowed_this_year():
            return []
        ais = [p for p in self.get_active_players() if getattr(p, 'is_ai', False)]
        desperate = [a for a in ais if self.is_player_desperate(a)]
        # Struggling = negative money (or very low) - can merge with a desperate AI
        struggling = [a for a in ais if self.get_player_money(a) < 0 or self.get_player_money(a) < 500]
        # Merge targets: desperate or struggling; at least one in each pair must be desperate
        eligible = [a for a in ais if a in desperate or a in struggling]
        if len(eligible) < 2:
            return []
        performed = []
        merged_this_round = set()
        for target in eligible:
            if target in merged_this_round:
                continue
            others = [a for a in eligible if a != target and a not in merged_this_round]
            if not others:
                continue
            # At least one of (target, acquirer) must be desperate
            if not self.is_player_desperate(target) and not any(self.is_player_desperate(a) for a in others):
                continue
            acquirer = max(others, key=lambda p: (
                len(p.get_line_names()),
                self.get_player_money(p)
            ))
            result = self.merge_players(acquirer, target)
            if result.get('success'):
                performed.append((acquirer, target))
                merged_this_round.add(target)
        return performed

    def get_ai_merge_proposals(self) -> list:
        """Get list of (ai_player, human_player) where desperate AI wants to merge with human.
        Only includes AIs that did not already merge with another AI this round.
        Returns empty if merge disabled or AI-human mergers not allowed or no eligible pairs."""
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_human_enabled', True):
            return []
        if not self.merge_ai_consideration_allowed_this_year():
            return []
        humans = [p for p in self.get_active_players() if not getattr(p, 'is_ai', False)]
        ais = [p for p in self.get_active_players() if getattr(p, 'is_ai', False)]
        if not humans or not ais:
            return []
        proposals = []
        for ai in ais:
            if self.is_player_desperate(ai):
                for human in humans:
                    proposals.append((ai, human))
        return proposals

    def get_desperate_merge_targets(self, acquirer: Player) -> list:
        """Players that acquirer can propose a merge to (other desperate players).
        Used by human players: returns all other active desperate players (AI or human) per game merge settings.
        Excludes players who have merged into someone else (they are not a separate company)."""
        if not getattr(self.game, 'merge_enabled', False) or not acquirer:
            return []
        if self.is_player_eliminated(acquirer):
            return []
        merge_ai_human = getattr(self.game, 'merge_ai_human_enabled', True)
        merge_human_human = getattr(self.game, 'merge_human_human_enabled', True)
        others = []
        for p in self.get_players():
            if p is acquirer or self.is_player_eliminated(p):
                continue
            if getattr(p, 'merged_into_player_id', None) is not None:
                continue
            if not self.is_player_desperate(p):
                continue
            if getattr(p, 'is_ai', False):
                if merge_ai_human:
                    others.append(p)
            else:
                if merge_human_human:
                    others.append(p)
        return others

    def get_merge_targets(self, acquirer: Player) -> list:
        """Players that acquirer can propose a merge to (any other mergeable player).
        Used for 'propose merger at any time' in the Law tab. Same filters as get_desperate_merge_targets but does not require targets to be desperate."""
        if not getattr(self.game, 'merge_enabled', False) or not acquirer:
            return []
        if self.is_player_eliminated(acquirer):
            return []
        merge_ai_human = getattr(self.game, 'merge_ai_human_enabled', True)
        merge_human_human = getattr(self.game, 'merge_human_human_enabled', True)
        others = []
        for p in self.get_players():
            if p is acquirer or self.is_player_eliminated(p):
                continue
            if getattr(p, 'merged_into_player_id', None) is not None:
                continue
            if getattr(p, 'is_ai', False):
                if merge_ai_human:
                    others.append(p)
            else:
                if merge_human_human:
                    others.append(p)
        return others

    def ai_would_accept_merge(self, ai_player: Player, acquirer: Player = None) -> bool:
        """True if AI would accept a merge proposal.
        If AI is in good financial situation (not losing more than merge_ai_healthy_profit_threshold_pct of its profit), auto-reject.
        Otherwise reject with merge_ai_reject_chance_pct probability (e.g. 90% reject)."""
        n_early = getattr(self.game, 'merge_ai_auto_fail_first_n_years', None)
        if n_early is None:
            from game_presets import AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
            n_early = AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
        if self._calendar_years_since_timeline_start() < int(n_early):
            return False
        try:
            summary = self.get_player_financial_summary(ai_player, self.get_year())
        except Exception:
            summary = {}
        profit = summary.get('profit', 0) or 0
        net = summary.get('net', 0) or 0
        threshold_pct = getattr(self.game, 'merge_ai_healthy_profit_threshold_pct', 25.0) or 25.0
        # Good situation: positive profit and not losing more than threshold% of it (net >= profit * (1 - threshold/100))
        if profit > 0:
            min_net = profit * (1.0 - threshold_pct / 100.0)
            if net >= min_net:
                return False  # Auto-reject when healthy
        # Survival gate: if AI can survive longer than configured turns at current net loss, reject merger.
        # Only evaluate when net is negative (losing money now); non-losing cases already reject above.
        if net < 0:
            turns_cfg = getattr(self.game, 'merge_ai_min_turns_to_live_for_accept', 10) or 10
            turns_cfg = max(0, int(turns_cfg))
            if turns_cfg > 0:
                try:
                    turns_to_live = float(self.get_player_money(ai_player)) / float(-net)
                except Exception:
                    turns_to_live = 0.0
                if turns_to_live > turns_cfg:
                    return False
        if not self.is_player_desperate(ai_player):
            return False
        if acquirer is None:
            return True
        combined = self.get_player_money(acquirer) + self.get_player_money(ai_player)
        if combined <= 0:
            return False
        reject_chance = getattr(self.game, 'merge_ai_reject_chance_pct', None)
        if reject_chance is None:
            reject_chance = 90.0
        reject_chance = float(reject_chance)
        import random as _r
        rng = _r.Random(self.get_year() * 10007 + self.get_player_id(ai_player) * 31)
        return rng.random() >= (reject_chance / 100.0)

    def _try_spawn_random_ai(self, current_year: int):
        """Spawn a new AI player if random AI spawn is enabled and it's time. Returns new player or None."""
        from game_presets import STARTING_MONEY
        from transit_company_names import get_transit_company_name
        existing_names = {p.get_name() for p in self.get_players()}
        map_preset = getattr(self.get_map(), 'map_preset_name', None)
        name = get_transit_company_name(existing_names, map_preset_name=map_preset)
        player_id = max((p.get_player_id() for p in self.get_players()), default=-1) + 1
        # Late-joining AIs get 50% of starting money
        starting_money = max(5000, STARTING_MONEY // 2)
        default_diff = getattr(self.game, 'default_ai_difficulty_for_spawns', 'hard')
        new_player = create_single_ai_player(player_id, name, starting_money, ai_difficulty=default_diff)
        from player_colors import next_available_color_index, assign_player_color_index
        assign_player_color_index(new_player, next_available_color_index(self.get_players()))
        # ai.run_ai_turn treats join year like a first-line opener (must queue/build a line on first turn).
        try:
            new_player._ai_join_game_year = int(current_year)
        except Exception:
            new_player._ai_join_game_year = current_year
        self.game.add_player(new_player)
        return {'player': new_player, 'name': name, 'money': starting_money}

    def _apply_random_government_law_effects(self, year: int, effects: list[dict]) -> None:
        """Apply random government-passed laws (benefits favored, harms unfavored)."""
        players_by_id = {self.get_player_id(p): p for p in self.get_players()}
        for eff in effects:
            t = eff.get('type')
            if t == 'reduce_property_tax_5' or t == 'reduce_property_tax_15':
                reduction = getattr(self.game, '_permanent_property_tax_reduction', set()) or set()
                if not isinstance(reduction, set):
                    reduction = set(reduction) if reduction else set()
                for pid in eff.get('player_ids', []):
                    reduction.add(pid)
                self.game._permanent_property_tax_reduction = reduction
            elif t == 'increase_property_tax_5' or t == 'increase_property_tax_15':
                increase = getattr(self.game, '_permanent_property_tax_increase', set()) or set()
                if not isinstance(increase, set):
                    increase = set(increase) if increase else set()
                for pid in eff.get('player_ids', []):
                    increase.add(pid)
                self.game._permanent_property_tax_increase = increase
            elif t == 'audit_target':
                targets = [players_by_id[pid] for pid in eff.get('player_ids', []) if pid in players_by_id]
                if targets:
                    self.game._lobby_audit_targets = targets
                    self.game._lobby_audit_year = eff.get('year', year + 1)
            elif t == 'tax_break':
                pid = eff.get('player_id')
                if pid in players_by_id:
                    self.game._lobby_tax_break_player = players_by_id[pid]
                    self.game._lobby_tax_break_year = eff.get('year', year + 1)

    # Player additional getters
    def get_player_engineer_count(self, player: Player) -> int:
        return player.get_engineer_count()
    
    def get_player_construction_worker_count(self, player: Player) -> int:
        """Get the number of construction workers a player has.
        Returns 0 if construction workers are on strike."""
        from worker import CONSTRUCTION_WORKER
        worker_rental = player.get_worker_rental(CONSTRUCTION_WORKER.name) if hasattr(player, 'get_worker_rental') else None
        if worker_rental and hasattr(worker_rental, 'is_on_strike') and worker_rental.is_on_strike():
            return 0
        return player.get_construction_worker_count()
    
    def get_player_assigned_trainsets_count(self, player: Player) -> int:
        """Get the number of trainsets assigned to lines for a player"""
        return player.get_assigned_trainsets_count()
    
    def get_player_lawyer_count(self, player: Player) -> int:
        """Get the number of lawyers a player has"""
        return player.get_lawyer_count()

    def get_player_available_lawyer_count(self, player: Player) -> int:
        """Lawyers available for lawsuits (total minus those drafting laws)."""
        return self.game._get_player_available_lawyer_count(player)
    
    def get_player_lawsuit_excuses(self, player: Player) -> list:
        """Get list of lawsuit excuses for a player"""
        return player.get_lawsuit_excuses()

    def get_lobby_inflation_factor(self) -> float:
        """Inflation factor for lobby success chance (1.0 in 1830, up to 10.0 by 2050)."""
        from inflation_utils import get_inflation_factor
        return get_inflation_factor(self.get_year(), max_factor=10.0)

    def get_lobby_minimum_cost(self) -> int:
        """Return minimum lobby cost (scales with inflation)."""
        return max(50, int(50 * self.get_lobby_inflation_factor()))

    def get_lobby_power_factor(self, option_id: str) -> float:
        """Power factor for lobby option. <1 = less powerful (cheaper), >1 = more powerful (costlier)."""
        factors = {
            'influence_seat': 0.6,       # One seat - weaker
            'influence_tiebreaker': 1.4,  # Leader ~3 seats - stronger
            'drop_excuse': 0.6,
            'tax_break': 0.8,
            'one_time_grant': 0.7,
            'pass_law': 0.9,
            'area_protection': 0.9,
            'gov_attack': 1.2,
            'add_lawsuit_excuse': 1.1,
            'audit_target': 1.2,
            'block_funding': 1.0,
            'receive_funding': 1.0,
            'redirect_funding': 1.0,
            'reduce_property_tax_5': 1.5,  # Permanent - most powerful
            'increase_property_tax_5': 1.5,  # Permanent - most powerful
        }
        return factors.get(option_id, 1.0)

    def get_lobby_success_chance(self, amount: int, option_id: str = None) -> float:
        """Return the probability (0.0–0.95) that lobbying succeeds for a given payment.
        Less powerful options (lower power factor) require less money for the same success chance."""
        if amount <= 0:
            return 0.10
        power = self.get_lobby_power_factor(option_id or 'drop_excuse')
        ref = 10000.0 * self.get_lobby_inflation_factor() * power
        return min(0.95, 0.10 + 0.85 * min(1.0, amount / ref))

    def get_government_lobby_options(self, player: Player) -> list:
        """Lobby options for government tab only: influence seats and tiebreaker."""
        return [
            {'id': 'influence_seat', 'label': 'Influence a seat: flip one unfavorable seat to favorable', 'requires_target': False},
            {'id': 'influence_tiebreaker', 'label': 'Influence tiebreaker: make leader favorable to you', 'requires_target': False},
        ]

    def get_law_draft_options(self, player: Player) -> list:
        """Law types that can be drafted (non-seat lobbies). Requires target for some."""
        year = self.get_year()
        options = [
            {'id': 'drop_excuse', 'label': 'Drop lawsuit excuse', 'requires_target': False},
            {'id': 'tax_break', 'label': 'Tax break (half taxes, 1 year)', 'requires_target': False},
            {'id': 'reduce_property_tax_5', 'label': 'Reduce property tax by 15% (permanent)', 'requires_target': False, 'allows_multi_target': True},
            {'id': 'increase_property_tax_5', 'label': 'Increase property tax by 15% on target(s) (permanent)', 'requires_target': True, 'allows_multi_target': True},
            {'id': 'one_time_grant', 'label': 'One-time government grant', 'requires_target': False},
            {'id': 'pass_law', 'label': 'Delay agency lawsuit checks (2 years)', 'requires_target': False},
            {'id': 'area_protection', 'label': 'Area protection: delay lawsuit checks (2 years)', 'requires_target': False},
            {'id': 'pacifism', 'label': 'Pacifism: reduce chance of war starting (temporary)', 'requires_target': False},
        ]
        others = [p for p in self.get_active_players() if p != player]
        if others:
            options.append({'id': 'gov_attack', 'label': 'Government penalty on target(s)', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'add_lawsuit_excuse', 'label': 'Add lawsuit excuse to target(s)', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'audit_target', 'label': 'Force audit: target(s) pay double taxes', 'requires_target': True, 'allows_multi_target': True})
        if year >= 1970:
            options.append({'id': 'receive_funding', 'label': 'Be chosen for government funding', 'requires_target': False})
            if others:
                options.append({'id': 'redirect_funding', 'label': 'Redirect funding from another player', 'requires_target': True})
                options.append({'id': 'block_funding', 'label': 'Block target(s) from government funding', 'requires_target': True, 'allows_multi_target': True})
        return options

    def get_lobby_options(self, player: Player) -> list:
        """Return list of lobby options: dict with id, label, requires_target (bool)."""
        year = self.get_year()
        options = [
            {'id': 'influence_seat', 'label': 'Influence a seat: flip one unfavorable seat to favorable', 'requires_target': False},
            {'id': 'influence_tiebreaker', 'label': 'Influence tiebreaker: make leader favorable to you', 'requires_target': False},
            {'id': 'drop_excuse', 'label': 'Drop a lawsuit excuse (government drops one case against you)', 'requires_target': False},
            {'id': 'tax_break', 'label': 'Tax break next year (half taxes for one year)', 'requires_target': False},
            {'id': 'reduce_property_tax_5', 'label': 'Reduce property tax by 15% (permanent)', 'requires_target': False},
            {'id': 'increase_property_tax_5', 'label': 'Increase property tax by 15% on target(s) (permanent)', 'requires_target': True},
            {'id': 'one_time_grant', 'label': 'One-time government grant', 'requires_target': False},
            {'id': 'pass_law', 'label': 'Delay agency lawsuit checks against you (2 years)', 'requires_target': False},
            {'id': 'area_protection', 'label': 'Area protection: delay lawsuit checks (2 years)', 'requires_target': False},
        ]
        others = [p for p in self.get_active_players() if p != player]
        # Attack options (require another player to target)
        if others:
            options.append({'id': 'gov_attack', 'label': 'Government penalty: target(s) pay a fine next year', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'add_lawsuit_excuse', 'label': 'Trigger audit: add lawsuit excuse to target(s)', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'audit_target', 'label': 'Force audit: target(s) pay double taxes next year', 'requires_target': True, 'allows_multi_target': True})
        if year >= 1970:
            options.append({'id': 'receive_funding', 'label': 'Be chosen for government funding this year', 'requires_target': False})
            if others:
                options.append({'id': 'redirect_funding', 'label': 'Redirect government funding from another player to you', 'requires_target': True})
                options.append({'id': 'block_funding', 'label': 'Block target(s) from government funding next year', 'requires_target': True, 'allows_multi_target': True})
        return options

    def try_lobby(self, player: Player, amount: int, option_id: str = 'drop_excuse', target_player: Player = None, target_players: list = None, seat_index: int = None) -> dict:
        """Pay money to lobby for an option. Returns dict with success, message, and option-specific keys.
        target_players: for options with allows_multi_target, list of targets."""
        min_cost = self.get_lobby_minimum_cost()
        if amount < min_cost:
            return {'success': False, 'message': f'Lobby cost must be positive and at least ${min_cost} (scales with inflation).'}
        money = self.get_player_money(player)
        if money < amount:
            return {'success': False, 'message': f'You need ${amount} but only have ${money}.'}
        opts = {o['id']: o for o in self.get_lobby_options(player)}
        if option_id not in opts:
            return {'success': False, 'message': f'Unknown lobby option: {option_id}.'}
        targets = list(target_players) if target_players else ([target_player] if target_player else [])
        if opts[option_id]['requires_target'] and not targets:
            return {'success': False, 'message': 'Please select at least one player to target.'}
        if opts[option_id]['requires_target'] and player in targets:
            return {'success': False, 'message': 'Cannot target yourself.'}

        gov = self.game.get_government()
        # Players cannot lobby for influence (seats/tiebreaker) until they have at least one seat allegiant to them
        if option_id in ('influence_seat', 'influence_tiebreaker') and gov:
            player_id = self.get_player_id(player)
            if gov.get_favorable_influence(player_id) <= 0:
                return {'success': False, 'message': 'You need at least one seat allegiant to you before you can lobby for more influence. Win an election first.'}

        chance = self.get_lobby_success_chance_for_player(player, amount, option_id)
        self.deduct_player_money(player, amount, reason='Lobby')
        if option_id == 'influence_seat' and gov:
            player_id = self.get_player_id(player)
            if seat_index is not None:
                if gov._seats[seat_index] == player_id:
                    return {'success': True, 'message': 'That seat was already favorable to you.'}
                pending = getattr(self.game, '_pending_seat_lobbies', [])
                pending.append(('seat', seat_index, player_id))
                self.game._pending_seat_lobbies = pending
                return {'success': True, 'message': 'Lobby submitted. Takes effect at end of turn; results revealed at next election.'}
            # Random seat - check if any unfavorable
            unfavorable = [i for i, s in enumerate(gov._seats) if s != player_id]
            if not unfavorable:
                return {'success': True, 'message': 'All seats already favorable to you.'}
            pending = getattr(self.game, '_pending_seat_lobbies', [])
            pending.append(('seat', -1, player_id))
            self.game._pending_seat_lobbies = pending
            return {'success': True, 'message': 'Lobby submitted. Takes effect at end of turn; results revealed at next election.'}

        if option_id == 'influence_tiebreaker' and gov:
            player_id = self.get_player_id(player)
            if gov._tiebreaker == player_id:
                return {'success': True, 'message': 'The tiebreaker was already favorable to you.'}
            pending = getattr(self.game, '_pending_seat_lobbies', [])
            pending.append(('tiebreaker', None, player_id))
            self.game._pending_seat_lobbies = pending
            return {'success': True, 'message': 'Lobby submitted. Takes effect at end of turn; results revealed at next election.'}

        if random.random() >= chance:
            return {'success': False, 'message': f'Lobbying failed. Your ${amount} did not sway the government.'}

        if option_id == 'drop_excuse':
            excuses = self.get_player_lawsuit_excuses(player)
            if excuses:
                excuse_removed = random.choice(excuses)
                self.remove_lawsuit_excuse_from_player(player, excuse_removed)
                return {'success': True, 'message': f'Lobbying succeeded! The government dropped one case: "{excuse_removed.cause}".', 'excuse_removed': excuse_removed}
            return {'success': True, 'message': 'Lobbying succeeded! The government is favorable (no pending cases to drop).'}

        if option_id == 'tax_break':
            self.game._lobby_tax_break_player = player
            self.game._lobby_tax_break_year = self.get_year() + 1
            return {'success': True, 'message': 'Lobbying succeeded! You will receive a tax break next year (half taxes).'}

        if option_id == 'reduce_property_tax_5':
            reduction = getattr(self.game, '_permanent_property_tax_reduction', set())
            if not isinstance(reduction, set):
                reduction = set(reduction) if reduction else set()
            reduction.add(self.get_player_id(player))
            self.game._permanent_property_tax_reduction = reduction
            return {'success': True, 'message': 'Lobbying succeeded! Your property tax is permanently reduced by 5%.'}

        if option_id == 'one_time_grant':
            grant = self.get_government_funding_amount() // 2
            self.add_player_money(player, grant, reason='Lobby grant')
            return {'success': True, 'message': f'Lobbying succeeded! You received a one-time grant of ${grant}.'}

        if option_id == 'pass_law':
            duration = 2
            self.game._lobby_pass_law_player = player
            self.game._lobby_pass_law_until_year = self.get_year() + duration
            return {'success': True, 'message': f'Lobbying succeeded! Agency lawsuit checks against you are delayed for {duration} years.'}

        if option_id == 'area_protection':
            duration = 2
            if getattr(self.game, '_lobby_pass_law_until_year', None) is not None and getattr(self.game, '_lobby_pass_law_player', None) == player:
                self.game._lobby_pass_law_until_year = max(self.game._lobby_pass_law_until_year, self.get_year() + duration)
            else:
                self.game._lobby_pass_law_player = player
                self.game._lobby_pass_law_until_year = self.get_year() + duration
            return {'success': True, 'message': f'Lobbying succeeded! Area protections delay lawsuit checks against you for {duration} years.'}

        if option_id == 'gov_attack':
            penalty = self.get_government_funding_amount()
            self.game._lobby_gov_attack_targets = targets
            self.game._lobby_gov_attack_penalty = penalty
            self.game._lobby_gov_attack_year = self.get_year() + 1
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! The government will impose a ${penalty} penalty on {names} next year.'}

        if option_id == 'add_lawsuit_excuse':
            from lawsuit import LAWSUIT_EXCUSES
            excuse = random.choice(LAWSUIT_EXCUSES)
            for t in targets:
                t.add_lawsuit_excuse(excuse)
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! {names} now have an additional lawsuit excuse: "{excuse.cause}".'}

        if option_id == 'audit_target':
            self.game._lobby_audit_targets = targets
            self.game._lobby_audit_year = self.get_year() + 1
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! {names} will pay double taxes next year.'}

        if option_id == 'block_funding':
            self.game._lobby_block_funding_targets = targets
            self.game._lobby_block_funding_year = self.get_year() + 1
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! {names} will be blocked from government funding next year.'}

        if option_id == 'increase_property_tax_5':
            increase = getattr(self.game, '_permanent_property_tax_increase', set())
            if not isinstance(increase, set):
                increase = set(increase) if increase else set()
            for t in targets:
                increase.add(self.get_player_id(t))
            self.game._permanent_property_tax_increase = increase
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! Property tax permanently increased by 5% for {names}.'}

        if option_id == 'receive_funding':
            self._government_funding_recipient = player
            amt = self.get_government_funding_amount()
            return {'success': True, 'message': f'Lobbying succeeded! You will receive this year\'s government funding (${amt}).'}

        if option_id == 'redirect_funding':
            if not targets:
                return {'success': False, 'message': 'Please select which player to redirect from.'}
            self._government_funding_recipient = player
            return {'success': True, 'message': f'Lobbying succeeded! Government funding will go to you instead of {self.get_player_name(targets[0])}.'}

        return {'success': False, 'message': 'Unknown option.'}

    def _complete_law_drafts(self, new_year: int) -> None:
        """Move drafts that finished this year to ready queue."""
        drafts = getattr(self.game, '_law_drafts', [])
        ready = getattr(self.game, '_law_drafts_ready', [])
        remaining = []
        for item in drafts:
            pid, law_type, year_started = item[0], item[1], item[2]
            num_lawyers = item[3] if len(item) > 3 else 1
            target_players = item[4] if len(item) > 4 else None
            if year_started + 1 == new_year:
                ready.append((pid, law_type, None, num_lawyers, target_players))
            else:
                remaining.append((pid, law_type, year_started, num_lawyers, target_players))
        self.game._law_drafts = remaining
        self.game._law_drafts_ready = ready

    def _get_beneficiaries_from_targets(self, law_type: str, target_players: list, drafter: Player) -> list:
        """Given law type and targets selected at draft time, return who the law applies to.
        For good laws (e.g. reduce_property_tax_5): they benefit. For bad laws (gov_attack, etc.): they are harmed.
        Seats allegiant to beneficiaries help pass good laws."""
        if not target_players:
            return [drafter] if law_type in ('reduce_property_tax_5', 'tax_break', 'pacifism') else []
        opts = {o['id']: o for o in self.get_law_draft_options(drafter)}
        if opts.get(law_type, {}).get('allows_multi_target', False):
            return [p for p in target_players if p is not None]
        return [target_players[0]] if target_players else []

    def start_law_draft(self, player: Player, law_type: str, num_lawyers: int = 1, target_players: list = None) -> dict:
        """Assign lawyers to draft a law. Takes 1 year. Targets are selected at draft time; game calculates who benefits; seats allegiant to beneficiaries help pass."""
        pid = self.get_player_id(player)
        total = player.get_lawyer_count()
        drafts = getattr(self.game, '_law_drafts', [])
        drafting = sum((d[3] if len(d) > 3 else 1) for d in drafts if d[0] == pid)
        if drafting + num_lawyers > total:
            return {'success': False, 'message': f'Not enough lawyers. You have {total}, {drafting} are drafting. Need {num_lawyers} more.'}
        opts = {o['id']: o for o in self.get_law_draft_options(player)}
        if law_type not in opts:
            return {'success': False, 'message': f'Unknown law type: {law_type}.'}
        self.game._law_drafts.append((pid, law_type, self.get_year(), num_lawyers, target_players))
        return {'success': True, 'message': f'{num_lawyers} lawyer(s) started drafting {opts[law_type]["label"]}. Will be ready next year.'}

    def get_player_drafts(self, player: Player) -> tuple:
        """Return (in_progress, ready) drafts. Each is (law_type, year_started or None, num_lawyers, target_players)."""
        pid = self.get_player_id(player)
        drafts = getattr(self.game, '_law_drafts', [])
        ready = getattr(self.game, '_law_drafts_ready', [])
        in_progress = [(d[1], d[2], d[3] if len(d) > 3 else 1, d[4] if len(d) > 4 else None) for d in drafts if d[0] == pid]
        ready_list = [(d[1], None, d[3] if len(d) > 3 else 1, d[4] if len(d) > 4 else None) for d in ready if d[0] == pid]
        return (in_progress, ready_list)

    def submit_law_draft(self, player: Player, law_type: str, target_player: Player = None, target_players: list = None) -> dict:
        """Submit a ready draft to government committee. Targets were selected at draft time; game calculates beneficiaries; seats allegiant to beneficiaries help pass."""
        pid = self.get_player_id(player)
        ready = getattr(self.game, '_law_drafts_ready', [])
        key = (pid, law_type)
        idx = next((i for i, d in enumerate(ready) if d[0] == pid and d[1] == law_type), None)
        if idx is None:
            return {'success': False, 'message': 'No ready draft of that type. Draft laws in the Law tab.'}
        item = ready.pop(idx)
        num_lawyers = item[3] if len(item) > 3 else 1
        draft_targets = item[4] if len(item) > 4 else None
        self.game._law_drafts_ready = ready

        attempts = getattr(self.game, '_law_attempts', {})
        last_year = attempts.get(key, -999)
        year = self.get_year()
        if year - last_year <= 3:
            return {'success': False, 'message': f'Similar law attempted recently (year {last_year}). Committee rejected it.'}
        attempts[key] = year
        self.game._law_attempts = attempts

        gov = self.game.get_government()
        if not gov:
            return {'success': False, 'message': 'No government.'}

        # Use targets from draft. For good laws (reduce_property_tax_5): seats allegiant to beneficiaries help.
        # For bad laws (gov_attack, audit_target, etc.): drafter's seats help (they benefit from harming targets).
        beneficiaries = self._get_beneficiaries_from_targets(law_type, draft_targets, player)
        bad_laws = {'gov_attack', 'add_lawsuit_excuse', 'audit_target', 'block_funding', 'increase_property_tax_5'}
        if law_type in bad_laws:
            favorable = gov.get_favorable_influence(pid)
            unfavorable = gov.get_unfavorable_influence(pid)
            tiebreaker = gov.get_tiebreaker_favorable(pid)
        elif beneficiaries:
            beneficiary_ids = {self.get_player_id(p) for p in beneficiaries if p is not None}
            favorable = gov.get_favorable_influence_for_players(beneficiary_ids)
            total_inf = gov.get_total_influence()
            unfavorable = total_inf - favorable
            tiebreaker = gov._tiebreaker in beneficiary_ids
        else:
            favorable = gov.get_favorable_influence(pid)
            unfavorable = gov.get_unfavorable_influence(pid)
            tiebreaker = gov.get_tiebreaker_favorable(pid)

        base = 0.30
        per_fav = 0.02
        per_unfav = -0.02
        chance = base + favorable * per_fav + unfavorable * per_unfav
        if favorable == unfavorable and tiebreaker:
            chance += 0.15
        # More lawyers on the draft = higher success chance (+5% per lawyer, max +25%)
        chance += min(0.25, (num_lawyers - 1) * 0.05)
        chance = max(0.05, min(0.95, chance))

        if random.random() >= chance:
            return {'success': False, 'message': f'Committee rejected the law. (Pass chance was {chance*100:.0f}%.)'}

        return self._apply_law_effect(player, law_type, target_player, beneficiaries)

    def _apply_law_effect(self, player: Player, law_type: str, target_player: Player = None, beneficiaries: list = None) -> dict:
        """Apply a passed law's effect. Same logic as try_lobby for non-seat options.
        beneficiaries: players who benefit (from targets selected at draft time)."""
        year = self.get_year()
        if law_type == 'drop_excuse':
            excuses = self.get_player_lawsuit_excuses(player)
            if excuses:
                excuse_removed = random.choice(excuses)
                self.remove_lawsuit_excuse_from_player(player, excuse_removed)
                return {'success': True, 'message': f'Law passed! Government dropped case: "{excuse_removed.cause}".'}
            return {'success': True, 'message': 'Law passed! No pending cases to drop.'}
        if law_type == 'tax_break':
            self.game._lobby_tax_break_player = player
            self.game._lobby_tax_break_year = year + 1
            return {'success': True, 'message': 'Law passed! Tax break next year (half taxes).'}
        if law_type == 'reduce_property_tax_5':
            reduction = getattr(self.game, '_permanent_property_tax_reduction', set())
            if not isinstance(reduction, set):
                reduction = set(reduction) if reduction else set()
            for p in (beneficiaries or [player]):
                if p is not None:
                    reduction.add(self.get_player_id(p))
            self.game._permanent_property_tax_reduction = reduction
            names = ', '.join(self.get_player_name(p) for p in beneficiaries if p)
            return {'success': True, 'message': f'Law passed! Property tax permanently reduced by 5% for: {names}.'}
        if law_type == 'one_time_grant':
            grant = self.get_government_funding_amount() // 2
            self.add_player_money(player, grant, reason='Law grant')
            return {'success': True, 'message': f'Law passed! One-time grant of ${grant}.'}
        if law_type == 'pass_law':
            self.game._lobby_pass_law_player = player
            self.game._lobby_pass_law_until_year = year + 2
            return {'success': True, 'message': 'Law passed! Agency lawsuit checks delayed 2 years.'}
        if law_type == 'area_protection':
            if getattr(self.game, '_lobby_pass_law_until_year', None) is not None and getattr(self.game, '_lobby_pass_law_player', None) == player:
                self.game._lobby_pass_law_until_year = max(self.game._lobby_pass_law_until_year, year + 2)
            else:
                self.game._lobby_pass_law_player = player
                self.game._lobby_pass_law_until_year = year + 2
            return {'success': True, 'message': 'Law passed! Area protection 2 years.'}
        if law_type == 'gov_attack' and beneficiaries:
            penalty = self.get_government_funding_amount()
            self.game._lobby_gov_attack_targets = beneficiaries
            self.game._lobby_gov_attack_penalty = penalty
            self.game._lobby_gov_attack_year = year + 1
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} will pay ${penalty} penalty next year.'}
        if law_type == 'add_lawsuit_excuse' and beneficiaries:
            from lawsuit import LAWSUIT_EXCUSES
            excuse = random.choice(LAWSUIT_EXCUSES)
            for t in beneficiaries:
                t.add_lawsuit_excuse(excuse)
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} have new excuse: "{excuse.cause}".'}
        if law_type == 'audit_target' and beneficiaries:
            self.game._lobby_audit_targets = beneficiaries
            self.game._lobby_audit_year = year + 1
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} pay double taxes next year.'}
        if law_type == 'block_funding' and beneficiaries:
            self.game._lobby_block_funding_targets = beneficiaries
            self.game._lobby_block_funding_year = year + 1
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} blocked from funding.'}
        if law_type == 'increase_property_tax_5' and beneficiaries:
            increase = getattr(self.game, '_permanent_property_tax_increase', set())
            if not isinstance(increase, set):
                increase = set(increase) if increase else set()
            for t in beneficiaries:
                increase.add(self.get_player_id(t))
            self.game._permanent_property_tax_increase = increase
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! Property tax permanently increased by 5% for {names}.'}
        if law_type == 'receive_funding':
            self._government_funding_recipient = player
            return {'success': True, 'message': "Law passed! You'll receive a government transit subsidy next year (based on your route km and open stations)."}
        if law_type == 'redirect_funding' and target_player:
            self._government_funding_recipient = player
            return {'success': True, 'message': f'Law passed! Funding redirected from {self.get_player_name(target_player)} to you.'}
        if law_type == 'pacifism':
            from game_presets import PACIFISM_DURATION_CALENDAR_YEARS, PACIFISM_WAR_START_CHANCE_MULTIPLIER
            # Exclusive calendar upper bound: war rolls use new_year when advancing; first affected
            # roll is entering year+1; we want D rolls → new_year runs year+1 .. year+D while new_year < year+D+1.
            new_until = year + PACIFISM_DURATION_CALENDAR_YEARS + 1
            prev = getattr(self.game, '_pacifism_until_year', None)
            if prev is not None and prev > year:
                new_until = max(new_until, prev)
            self.game._pacifism_until_year = new_until
            pct = int(round(PACIFISM_WAR_START_CHANCE_MULTIPLIER * 100))
            first_enter = year + 1
            last_enter = year + PACIFISM_DURATION_CALENDAR_YEARS
            return {
                'success': True,
                'message': (
                    f'Law passed! Pacifism halves the yearly chance of a new war starting for '
                    f'{PACIFISM_DURATION_CALENDAR_YEARS} war rolls (when advancing into {first_enter}–{last_enter}, '
                    f'~{pct}% of normal chance each roll).'
                ),
            }
        return {'success': False, 'message': 'Unknown law type.'}

    def get_government_funding_amount(self) -> int:
        base = getattr(self, '_government_funding_amount', 50000)
        return self.inflate_economic_value(base, max_factor=10.0)

    def get_government_seats_for_display(self, player: Player) -> list:
        """Return list of (is_favorable, is_tiebreaker) for government tab."""
        gov = self.game.get_government()
        if not gov:
            return []
        return gov.get_seats_for_display(self.get_player_id(player), self.get_year())

    def get_government_seats_with_allegiance(self) -> list:
        """Return list of (allegiance_player_id, influence, is_tiebreaker) for government tab."""
        gov = self.game.get_government()
        if not gov:
            return []
        return gov.get_seats_with_allegiance(self.get_year())

    def get_government_next_election_year(self) -> int:
        """Year of next government election."""
        gov = self.game.get_government()
        if not gov:
            return 0
        return gov.get_next_election_year(self.get_year())

    def get_government_tax_multiplier(self, player: Player) -> float:
        """Current tax multiplier for player based on government."""
        gov = self.game.get_government()
        if not gov:
            return 1.0
        return gov.get_tax_multiplier(self.get_player_id(player))

    def get_government_favor_ranking(self) -> list[tuple[int, float]]:
        """Return (player_id, influence) sorted by who government favors most (by influence)."""
        gov = self.game.get_government()
        if not gov:
            return []
        return gov.get_favor_ranking()

    def get_player_total_manpower(self, player: Player) -> int:
        """Total workers (engineers, construction workers, lawyers, maintenance workers) for a player."""
        total = 0
        for rental in getattr(player, 'worker_rentals', {}).values():
            total += getattr(rental, 'quantity', 0)
        return total

    def get_player_seats_controlled(self, player_id: int) -> int:
        """Number of government seats favorable to this player (people on their side)."""
        gov = self.game.get_government()
        if not gov:
            return 0
        return gov.get_favorable_count(player_id)

    def get_government_funding_recipient(self) -> Player:
        return getattr(self, '_government_funding_recipient', None)

    def distribute_government_funding(self, year: int) -> Optional[dict]:
        """If year >= 1970, give government funding to the chosen recipient (or random player).
        Funding is one turn only and equals transit subsidy (by km and open stations); no subsidy if no transit.
        Returns dict with recipient_name and amount when a grant is paid."""
        if year < 1970:
            return None
        active = [p for p in self.get_active_players() if not getattr(p, 'is_public_ta', False)]
        if not active:
            return None
        blocked_set = set()
        if getattr(self.game, '_lobby_block_funding_year', None) == year:
            targets = getattr(self.game, '_lobby_block_funding_targets', None) or (
                [getattr(self.game, '_lobby_block_funding_target', None)] if getattr(self.game, '_lobby_block_funding_target', None) is not None else [])
            blocked_set = {p for p in targets if p is not None}
            setattr(self.game, '_lobby_block_funding_targets', None)
            setattr(self.game, '_lobby_block_funding_target', None)
            setattr(self.game, '_lobby_block_funding_year', None)
        active = [p for p in active if p not in blocked_set]
        if not active:
            return None
        recipient = self._government_funding_recipient
        if recipient is None or recipient not in active or self.is_player_eliminated(recipient) or recipient in blocked_set:
            recipient = random.choice(active)
        amount = self._compute_transit_subsidy(recipient, year, is_favored=True)
        result = None
        if amount > 0:
            self.add_player_money(recipient, amount, reason='Government funding')
            result = {
                'recipient_name': self.get_player_name(recipient),
                'amount': int(amount),
            }
        self._government_funding_recipient = None
        return result

    # Trainset additional getters
    def get_trainset_locomotive_name(self, trainset) -> str:
        if trainset.locomotive:
            return trainset.locomotive.name
        return "Unknown"
    
    def get_trainset_locomotive_year(self, trainset):
        if trainset.locomotive:
            return getattr(trainset.locomotive, 'year_introduced', None)
        return None
    
    def get_trainset_capacity(self, trainset) -> int:
        return trainset.get_capacity()
    
    def get_trainset_maintenance_costs(self, trainset) -> int:
        return trainset.get_maitnance_costs(self.get_year())
    
    def get_vehicle_name(self, vehicle) -> str:
        return vehicle.name
    
    def get_vehicle_capacity(self, vehicle) -> int:
        return getattr(vehicle, 'capacity', 0)
    
    def get_player_id(self, player: Player) -> int:
        return player.get_player_id()
    
    def get_player_info(self, player: Player) -> str:
        """Get formatted player information string with all details"""
        current_year = self.get_year()
        out = []
        
        # Header
        out.append(f"{self.get_player_name(player)}: Money: ${self.get_player_money(player)}")
        
        # Lines owned
        line_names = self.get_player_line_names(player)
        if line_names:
            out.append("  Lines:")
            for lname in line_names:
                line = self.get_map_lines().get(lname)
                if line:
                    length = len(self.get_line_route(line))
                    profit = self.calc_line_profit(line, current_year)
                    mait = self.calc_line_maintenance(line, current_year)
                    out.append(f"    - {lname}: points={length}, profit=${int(profit)}, maintenance=${int(mait)}")
                else:
                    out.append(f"    - {lname}")
        else:
            out.append("  Lines: none")
        
        # Trainsets owned
        trainsets = self.get_player_trainsets(player)
        if trainsets:
            out.append("  Trainsets:")
            for i, ts in enumerate(trainsets):
                try:
                    if ts is None:
                        out.append(f"    - Trainset {i}: [None]")
                        continue
                    loco_name = self.get_trainset_locomotive_name(ts)
                    cars = len(self.get_trainset_cars(ts))
                    cap = self.get_trainset_capacity(ts)
                    mait = self.get_trainset_maintenance_costs(ts)
                    assign = self.get_trainset_line_assignment(ts)
                    out.append(f"    - Trainset {i}: loco={loco_name}, cars={cars}, cap={cap}, maintenance=${mait}, assigned_to={assign}")
                except Exception as e:
                    out.append(f"    - Trainset {i}: Error: {str(e)}")
        else:
            out.append("  Trainsets: none")
        
        player_profit = self.calc_player_profit(player)
        
        trainset_maintenance = 0
        for ts in self.get_player_trainsets(player):
            maint_cost = ts.get_maitnance_costs(current_year)
            if maint_cost is not None:
                trainset_maintenance += maint_cost
        
        # Resource charges
        # Use historical resource costs
        resource_costs = self.get_resource_costs(current_year)
        resource_charge = 0
        for trainset in player.get_trainsets():
            try:
                loco = getattr(trainset, 'locomotive', None)
                if loco and hasattr(loco, 'fuel') and loco.fuel:
                    fuel_name = getattr(loco.fuel, 'name', None)
                    if fuel_name and fuel_name in resource_costs:
                        resource_charge += resource_costs[fuel_name]
            except (AttributeError, TypeError):
                pass
        
        line_maintenance = self.game.calc_maitnance_by_player(player)
        
        # Taxes
        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, current_year, player_profit or 0)
        
        # Worker costs
        worker_costs, _ = self.game.calc_worker_costs(player, current_year)
        
        total_costs = int(trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs)
        net = int(player_profit) - total_costs
        
        out.append("  Financial Summary:")
        out.append(f"    - Profit (lines): ${int(player_profit)}")
        out.append(f"    - Trainset maintenance: ${int(trainset_maintenance)}")
        out.append(f"    - Resource charges: ${int(resource_charge)}")
        out.append(f"    - Line maintenance: ${int(line_maintenance)}")
        out.append(f"    - Taxes: poll=${poll_tax}, property=${property_tax}, income=${income_tax}")
        if worker_costs > 0:
            out.append(f"    - Worker costs: ${int(worker_costs)}")
        out.append(f"    - Net this year: ${net}")
        
        return "\n".join(out)
    
    def get_all_players_info(self) -> list[str]:
        """Get formatted info for all active players"""
        active_players = self.get_active_players()
        return [self.get_player_info(p) for p in active_players]
    
    def convert_latlon_points_to_grid(self, latlon_points: list) -> list:
        """Convert list of (lat, lon) tuples to grid (x, y) coordinates"""
        game_map = self.game.get_map()
        if not game_map.use_osm or not game_map.osm_integration:
            raise ValueError("OSM integration required for lat/lon to grid conversion")
        
        center_lat = game_map.map_center_lat if game_map.map_center_lat is not None else 0.0
        center_lon = game_map.map_center_lon if game_map.map_center_lon is not None else 0.0
        # Ensure width and height are available
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
        grid_size = max(map_width, map_height)
        from map_preset_loader import get_map_bounds
        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        use_preset_bounds = bool(
            preset_bounds
            and all(k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon'))
            and map_width and map_height
        )
        
        grid_points = []
        for lat, lon in latlon_points:
            if use_preset_bounds:
                y = (
                    (lat - preset_bounds['min_lat'])
                    / (preset_bounds['max_lat'] - preset_bounds['min_lat'])
                    * map_height
                )
                x = (
                    (lon - preset_bounds['min_lon'])
                    / (preset_bounds['max_lon'] - preset_bounds['min_lon'])
                    * map_width
                )
                x = max(0.0, min(float(map_width), float(x)))
                y = max(0.0, min(float(map_height), float(y)))
                grid_points.append((x, y))
            else:
                x, y = game_map.osm_integration.convert_latlon_to_grid(
                    lat, lon, center_lat, center_lon, grid_size,
                    map_width=game_map.width, map_height=game_map.height,
                )
                grid_points.append((x, y))
        
        return grid_points
    
    def _is_point_in_water_cached(self, x: float, y: float, game_map, water_cache: dict) -> bool:
        """Helper method to check if a point is in water, with caching to avoid redundant checks.
        Uses tile system if available, otherwise falls back to NYC/OSM methods.
        
        Args:
            x: Grid x coordinate
            y: Grid y coordinate
            game_map: Map object
            water_cache: Dictionary cache for water checks (key: (x, y), value: bool)
            
        Returns:
            True if point is in water, False otherwise
        """
        import math
        
        # OPTIMIZATION: Check cache first to avoid redundant checks
        cache_key = (round(x, 2), round(y, 2))  # Round to avoid floating point precision issues
        if cache_key in water_cache:
            return water_cache[cache_key]
        
        result = False
        # Use tile system if available (preferred method)
        # Tiles are already pre-cached, so this is fast
        if game_map.tile_system:
            result = game_map.tile_system.is_point_in_water(x, y)
        else:
            # Fallback: use preset's water detection if available
            preset_name = getattr(game_map, 'map_preset_name', None)
            if preset_name:
                from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
                if preset_has_water_areas(preset_name):
                    result = preset_is_point_in_water(preset_name, x, y, game_map)
            elif game_map.osm_integration:
                # Ensure width and height are available
                map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
                map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
                grid_size = max(map_width, map_height)
                center_lat = game_map.map_center_lat or 0.0
                center_lon = game_map.map_center_lon or 0.0
                lat, lon = game_map.osm_integration.convert_grid_to_latlon(x, y, center_lat, center_lon, grid_size)
                result = game_map.osm_integration.is_water_at_latlon(lat, lon)
            else:
                result = False
        
        # Cache the result
        water_cache[cache_key] = result
        return result

    def _segment_in_water_for_disaster(self, p1, p2, game_map, preset_name: str = None) -> bool:
        """True if segment from p1 to p2 is in water. Used for disaster repair to avoid street-on-water error.

        Uses preset/tile cache only (skip_osm=True) so UI elevation prompts never block on Overpass.
        """
        def _pt(p):
            if hasattr(p, 'x') and hasattr(p, 'y'):
                return float(p.x), float(p.y)
            return float(p[0]), float(p[1])
        x1, y1 = _pt(p1)
        x2, y2 = _pt(p2)
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        if game_map.tile_system:
            try:
                return (
                    game_map.tile_system.is_point_in_water(x1, y1, skip_osm=True)
                    or game_map.tile_system.is_point_in_water(x2, y2, skip_osm=True)
                    or game_map.tile_system.is_point_in_water(mid_x, mid_y, skip_osm=True)
                )
            except TypeError:
                return (
                    game_map.tile_system.is_point_in_water(x1, y1)
                    or game_map.tile_system.is_point_in_water(x2, y2)
                    or game_map.tile_system.is_point_in_water(mid_x, mid_y)
                )
        if preset_name:
            from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
            if preset_has_water_areas(preset_name):
                return (
                    preset_is_point_in_water(preset_name, x1, y1, game_map, skip_osm=True)
                    or preset_is_point_in_water(preset_name, x2, y2, game_map, skip_osm=True)
                    or preset_is_point_in_water(preset_name, mid_x, mid_y, game_map, skip_osm=True)
                )
        return False

    def segment_is_over_water_not_bridge(self, p1, p2) -> bool:
        """True when a segment crosses open water (not a bridge). Preset-only (no OSM)."""
        game_map = self.game.get_map()
        preset_name = getattr(game_map, 'map_preset_name', None)
        if not self._segment_in_water_for_disaster(p1, p2, game_map, preset_name):
            return False
        def _pt(p):
            if hasattr(p, 'x') and hasattr(p, 'y'):
                return float(p.x), float(p.y)
            return float(p[0]), float(p[1])
        x1, y1 = _pt(p1)
        x2, y2 = _pt(p2)
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        if preset_name:
            from map_preset_loader import get_preset_is_point_on_bridge
            if get_preset_is_point_on_bridge(preset_name, mid_x, mid_y, self.get_year(), game_map):
                return False
            if _segment_on_bridge_or_tunnel(game_map, x1, y1, x2, y2, self.get_year()):
                return False
        return True

    def get_segment_elevation_options(self, p1, p2, *, is_bus_line: bool = False) -> tuple:
        """Return (allowed_elevations, default_elevation) for one route segment.

        When a segment cannot be at street level (e.g. over water) the constructor
        defaults it to 'underground' rather than an elevated structure.
        """
        if self.segment_is_over_water_not_bridge(p1, p2):
            return list(_WATER_SEGMENT_ELEVATIONS), 'underground'
        return list(_LAND_SEGMENT_ELEVATIONS), 'street'

    def get_line_creation_elevation_specs(self, points: list) -> list:
        """One elevation spec per segment for a new line."""
        specs = []
        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i + 1]
            specs.append({
                'label': (
                    f"Segment {i + 1} ({float(p1[0]):.1f}, {float(p1[1]):.1f})"
                    f" → ({float(p2[0]):.1f}, {float(p2[1]):.1f})"
                ),
                'p1': p1,
                'p2': p2,
            })
        return specs

    def get_extension_elevation_specs(
        self, line_name: str, new_points: list, extend_from_end: bool,
    ) -> list:
        """One elevation spec per extension point (segment starting at that point)."""
        route = self.get_line_route_including_extensions(line_name)
        if not route:
            line = self.get_map_lines().get(line_name)
            route = list(self.get_line_route(line) or []) if line else []
        specs = []
        if extend_from_end:
            if new_points:
                specs.append({
                    'label': (
                        f"Point 1 ({float(new_points[0][0]):.1f}, {float(new_points[0][1]):.1f})"
                        " — connection from line"
                    ),
                    'p1': route[-1] if route else new_points[0],
                    'p2': new_points[0],
                })
            for i in range(1, len(new_points)):
                p1, p2 = new_points[i - 1], new_points[i]
                specs.append({
                    'label': f"Point {i + 1} ({float(p2[0]):.1f}, {float(p2[1]):.1f})",
                    'p1': p1,
                    'p2': p2,
                })
        else:
            for i in range(len(new_points) - 1):
                p1, p2 = new_points[i], new_points[i + 1]
                specs.append({
                    'label': f"Point {i + 1} ({float(p1[0]):.1f}, {float(p1[1]):.1f})",
                    'p1': p1,
                    'p2': p2,
                })
            if new_points and route:
                p1 = new_points[-1]
                specs.append({
                    'label': (
                        f"Point {len(new_points)} ({float(p1[0]):.1f}, {float(p1[1]):.1f})"
                        " — connection to line"
                    ),
                    'p1': p1,
                    'p2': route[0],
                })
        return specs

    def calculate_line_cost(self, points: list, elevation_levels: list = None, is_bus_line: bool = False, is_ferry_line: bool = False) -> dict:
        """Calculate line creation cost based on points, terrain, and elevation.
        Returns dict with cost and length info.
        
        Cost factors:
        - On roads: cheapest (base cost)
        - On land with buildings: 2x cost (only if population > 0)
        - On water: 2x cost (unless on a bridge)
        - Elevation: street (1x), above_ground (1.5x), underground (2x)
        - Historical events: multipliers applied based on current year
        - Bus lines: 50% of normal cost (buses are cheaper to build)
        """
        import math
        current_year = self.game.get_year()
        game_map = self.game.get_map()
        
        # Get historical event multipliers
        event_cost_multiplier = 1.0
        try:
            event_effects = self.game.event_system.get_combined_effects(current_year)
            event_cost_multiplier = event_effects.get('construction_cost_multiplier', 1.0)
        except:
            pass
        
        # Base costs per unit length (increased for balance; profit margin raised to compensate)
        road_cost_per_unit = 225  # Cheapest - on existing roads
        land_with_buildings_cost_per_unit = 450  # 2x - on land with buildings (population > 0)
        land_without_buildings_cost_per_unit = 225  # Same as road - empty land
        water_cost_per_unit = 450  # 2x - on water (unless bridge)
        
        # Elevation multipliers
        elevation_multipliers = {
            'street': 1.0,
            'above_ground': 1.5,
            'underground': 2.0
        }
        
        # Elevation levels:
        # - Train lines: must be explicitly provided (no default).
        # - Ferry/bus lines: can be safely defaulted (keeps game logic in UI layer and
        #   avoids forcing Qt to invent elevation lists).
        if elevation_levels is None:
            if is_ferry_line:
                elevation_levels = ['water'] * max(0, (len(points) - 1))
            elif is_bus_line:
                elevation_levels = ['street'] * max(0, (len(points) - 1))
            else:
                raise ValueError("Elevation levels must be specified for line construction. Cannot default to 'street'.")
        if len(elevation_levels) < len(points) - 1:
            raise ValueError(
                f"Not enough elevation levels provided. Need {len(points) - 1} levels for {len(points)} points, got {len(elevation_levels)}"
            )
        
        total_length = 0.0
        if len(points) > 1:
            for i in range(len(points) - 1):
                x1, y1 = points[i]
                x2, y2 = points[i+1]
                total_length += math.hypot(x2 - x1, y2 - y1)
        
        # Line creation cost: more expensive (1.5x base) for balance; profit margin increased to compensate.
        LINE_COST_MULTIPLIER = 1.5
        if is_ferry_line:
            # Fast cost preview only — full 60 m water validation runs in create_line_complete.
            # Calling _validate_ferry_route here blocks the UI for minutes on OSM maps.
            ferry_cost = max(15, int(total_length * 40 * 0.8 * LINE_COST_MULTIPLIER))
            return {
                'cost': ferry_cost,
                'length': total_length,
                'points_count': len(points),
                'elevation_levels': elevation_levels
            }
        if is_bus_line:
            # Bus lines are assumed to be on roads (route validation enforces this on supported maps).
            # Use the road base cost and apply the bus discount.
            bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * LINE_COST_MULTIPLIER))
            return {
                'cost': bus_cost,
                'length': total_length,
                'points_count': len(points),
                'elevation_levels': elevation_levels
            }

        # OPTIMIZATION: Use preset-only water detection for cost preview - no OSM calls, completes in <1s.
        # Full OSM validation runs in create_line_complete when the user confirms.
        if game_map.tile_system:
            game_map.tile_system._preset_only_water = True
        try:
            if game_map.tile_system:
                game_map.tile_system.pre_cache_route_tiles(
                    points, proximity_buffer_m=0.0, max_osm_queries=0,
                )

            road_cache = {}
            bridge_cache = {}
            population_cache = {}
            water_cache = {}  
            def is_point_in_water(x, y):
                return self._is_point_in_water_cached(x, y, game_map, water_cache)

            # Helper to check if a point is on a road (for NYC maps, check if road exists at current year)
            # Uses cache to avoid repeated lookups
            def is_point_on_road(x, y):
                # Round coordinates for cache key
                cache_key = (int(round(x)), int(round(y)))
                if cache_key in road_cache:
                    return road_cache[cache_key]

                result = False
                preset_name = getattr(game_map, 'map_preset_name', None)
                if preset_name:
                    from map_preset_loader import preset_has_road_support, get_preset_is_point_on_road
                    if preset_has_road_support(preset_name):
                        result = get_preset_is_point_on_road(preset_name, x, y, current_year, game_map)
                if not result and game_map.use_osm and game_map.osm_integration:
                    if hasattr(game_map, '_osm_rights_of_way') and game_map._osm_rights_of_way:
                        nearest = game_map.find_nearest_right_of_way(x, y)
                        result = nearest is not None

                road_cache[cache_key] = result
                return result

            def is_point_on_bridge(x, y):
                preset_name = getattr(game_map, 'map_preset_name', None)
                if not preset_name:
                    return False
                cache_key = (int(round(x)), int(round(y)))
                if cache_key in bridge_cache:
                    return bridge_cache[cache_key]
                from map_preset_loader import get_preset_is_point_on_bridge
                result = get_preset_is_point_on_bridge(preset_name, x, y, current_year, game_map)
                bridge_cache[cache_key] = result
                return result
        
            # Helper to check if a point has buildings (population > 0)
            def has_buildings(x, y):
                grid_x, grid_y = int(round(x)), int(round(y))
                pop = game_map.get_population(grid_x, grid_y)
                return pop > 0
        
            total_cost = 0
            water_segments_count = 0  # Track consecutive water segments for exponential pricing
            # Segments that run on top of an existing line's track reuse it (shared tracks).
            shared_threshold = self._shared_track_threshold_grid()
            shared_routes = self._existing_track_segments_for_sharing()
            if len(points) > 1:
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i+1]
                    segment_length = math.hypot(x2 - x1, y2 - y1)

                    # Shared track: if this segment reuses an existing line's track, only a
                    # small connection cost applies. Once track reuse is confirmed, the
                    # terrain/water/road/bridge checks below are unnecessary, so skip them.
                    if (
                        not is_ferry_line
                        and shared_routes
                        and self._segment_reuses_existing_track(
                            x1, y1, x2, y2, shared_routes, shared_threshold,
                        )
                    ):
                        total_cost += int(
                            road_cost_per_unit * segment_length
                            * SHARED_TRACK_COST_FACTOR * event_cost_multiplier
                        )
                        water_segments_count = 0
                        continue

                    # Check terrain for midpoint of segment
                    mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
                    mid_on_road = is_point_on_road(mid_x, mid_y)
                    mid_on_bridge = is_point_on_bridge(mid_x, mid_y)
                    on_bridge_or_tunnel = mid_on_bridge or _segment_on_bridge_or_tunnel(
                        game_map, x1, y1, x2, y2, current_year,
                    )
                    mid_has_buildings = has_buildings(mid_x, mid_y)
                    
                    # Get elevation for this segment
                    elevation = elevation_levels[i] if i < len(elevation_levels) else 'street'
                
                    # OPTIMIZATION: For ferry lines, skip water detection entirely - they're always on water
                    segment_in_water = False
                    water_tile_count = 0
                    if is_ferry_line:
                        segment_in_water = True
                        water_tile_count = max(1, int(segment_length * 10))
                    elif game_map.tile_system:
                        if game_map.tile_system.is_point_in_water(x1, y1) or game_map.tile_system.is_point_in_water(x2, y2):
                            segment_in_water = True
                            # Preset-only: use segment length as approximation (avoids slow tile walk)
                            water_tile_count = max(1, int(segment_length * 2)) if game_map.tile_system._preset_only_water else game_map.tile_system.get_water_segment_count(x1, y1, x2, y2)
                        else:
                            if game_map.tile_system.is_point_in_water(mid_x, mid_y):
                                segment_in_water = True
                                water_tile_count = max(1, int(segment_length * 2)) if game_map.tile_system._preset_only_water else game_map.tile_system.get_water_segment_count(x1, y1, x2, y2)
                    else:
                        point1_in_water = is_point_in_water(x1, y1)
                        point2_in_water = is_point_in_water(x2, y2)
                        segment_in_water = point1_in_water or point2_in_water
                    
                    # Street level on open water is invalid; bridges and tunnels may use street elevation.
                    if segment_in_water and not on_bridge_or_tunnel and elevation == 'street':
                        raise ValueError(f"Cannot build at street level on water. Segment from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) is in water. Use 'above_ground' or 'underground' instead.")
                    
                    # Track water segments for exponential pricing
                    if segment_in_water and not on_bridge_or_tunnel:
                        if game_map.tile_system:
                            water_segments_count += water_tile_count
                        else:
                            water_segments_count += 1
                    else:
                        water_segments_count = 0
                    
                    # Determine base cost per unit based on terrain
                    if is_ferry_line:
                        base_cost_per_unit = water_cost_per_unit
                    elif mid_on_road:
                        base_cost_per_unit = road_cost_per_unit
                    elif segment_in_water and not on_bridge_or_tunnel:
                        # Cap exponent to avoid overflow (water_segments_count can be large for long segments)
                        capped_count = min(water_segments_count, 20)
                        exponential_multiplier = 1.5 ** capped_count
                        base_cost_per_unit = water_cost_per_unit * exponential_multiplier
                    elif mid_has_buildings:
                        base_cost_per_unit = land_with_buildings_cost_per_unit
                    else:
                        base_cost_per_unit = land_without_buildings_cost_per_unit
                    
                    base_cost = base_cost_per_unit * segment_length
                    elevation_mult = elevation_multipliers.get(elevation, 1.0)
                    base_cost *= elevation_mult
                    base_cost *= event_cost_multiplier
                    if not is_ferry_line:
                        base_cost *= _off_street_segment_cost_multiplier(
                            game_map, x1, y1, x2, y2, current_year,
                            is_bus_line=is_bus_line,
                        )
                    total_cost += int(base_cost)
            
            # Bus lines are 50% cheaper
            if is_bus_line:
                total_cost = int(total_cost * 0.5)
            
            total_cost = max(15, int(total_cost * 0.75))
            
            return {
                'cost': total_cost,
                'length': total_length,
                'points_count': len(points),
                'elevation_levels': elevation_levels
            }
        finally:
            if game_map.tile_system:
                game_map.tile_system._preset_only_water = False
    
    def _validate_line_not_on_construction_segments(self, points: list, new_line_name: str) -> None:
        """Raise ValueError if any segment of the new line overlaps with segments of other lines under construction."""
        import math
        tol = 0.5

        def _pt(p):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                return (float(p[0]), float(p[1]))
            if hasattr(p, 'x') and hasattr(p, 'y'):
                return (float(p.x), float(p.y))
            return None

        def _point_on_segment(px, py, x1, y1, x2, y2):
            dx, dy = x2 - x1, y2 - y1
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq == 0:
                return math.hypot(px - x1, py - y1) <= tol
            t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / seg_len_sq))
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy
            return math.hypot(px - proj_x, py - proj_y) <= tol

        def _segments_overlap(a1, a2, b1, b2):
            ax1, ay1 = a1
            ax2, ay2 = a2
            bx1, by1 = b1
            bx2, by2 = b2
            if _point_on_segment(ax1, ay1, bx1, by1, bx2, by2):
                return True
            if _point_on_segment(ax2, ay2, bx1, by1, bx2, by2):
                return True
            if _point_on_segment(bx1, by1, ax1, ay1, ax2, ay2):
                return True
            if _point_on_segment(bx2, by2, ax1, ay1, ax2, ay2):
                return True
            return False

        # OPTIMIZATION: spatial-hash under-construction segments so validation is not O(n*m) with many queued lines.
        bq = self.game.build_queue or []
        try:
            sig = (len(bq), sum(len((b.get("data", {}) or {}).get("points", []) or []) for b in bq))
        except Exception:
            sig = (len(bq), 0)
        idx = getattr(self.game, "_construction_segment_index_cache", None)
        if not (isinstance(idx, dict) and idx.get("sig") == sig):
            bucket = 1.0  # grid units (~km) bucket size; tol is 0.5 so this is safe
            grid = {}  # (cx,cy) -> list[(bp1,bp2,other_name)]
            for build in bq:
                if build.get('type') != 'line':
                    continue
                build_data = build.get('data', {}) or {}
                other_name = build_data.get('name', 'Unknown')
                if other_name == new_line_name:
                    continue
                seg_pts = build_data.get('points', []) or []
                if len(seg_pts) < 2:
                    continue
                for j in range(len(seg_pts) - 1):
                    bp1 = _pt(seg_pts[j])
                    bp2 = _pt(seg_pts[j + 1])
                    if bp1 is None or bp2 is None:
                        continue
                    minx = min(bp1[0], bp2[0]) - tol
                    maxx = max(bp1[0], bp2[0]) + tol
                    miny = min(bp1[1], bp2[1]) - tol
                    maxy = max(bp1[1], bp2[1]) + tol
                    x0 = int(math.floor(minx / bucket))
                    x1 = int(math.floor(maxx / bucket))
                    y0 = int(math.floor(miny / bucket))
                    y1 = int(math.floor(maxy / bucket))
                    for cx in range(x0, x1 + 1):
                        for cy in range(y0, y1 + 1):
                            grid.setdefault((cx, cy), []).append((bp1, bp2, other_name))
            idx = {"sig": sig, "bucket": bucket, "grid": grid}
            try:
                setattr(self.game, "_construction_segment_index_cache", idx)
            except Exception:
                pass

        bucket = float(idx.get("bucket", 1.0) or 1.0)
        grid = idx.get("grid", {}) if isinstance(idx, dict) else {}
        # Validate each segment of the new line against only nearby under-construction segments.
        for i in range(len(points) - 1):
            ap1 = _pt(points[i])
            ap2 = _pt(points[i + 1])
            if ap1 is None or ap2 is None:
                continue
            minx = min(ap1[0], ap2[0]) - tol
            maxx = max(ap1[0], ap2[0]) + tol
            miny = min(ap1[1], ap2[1]) - tol
            maxy = max(ap1[1], ap2[1]) + tol
            x0 = int(math.floor(minx / bucket))
            x1 = int(math.floor(maxx / bucket))
            y0 = int(math.floor(miny / bucket))
            y1 = int(math.floor(maxy / bucket))
            seen = set()
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
                    for bp1, bp2, other_name in (grid.get((cx, cy), []) or []):
                        key = (bp1, bp2, other_name)
                        if key in seen:
                            continue
                        seen.add(key)
                        if _segments_overlap(ap1, ap2, bp1, bp2):
                            raise ValueError(
                                f"Cannot build on segments under construction. "
                                f"Your route overlaps with line '{other_name}' which is being built."
                            )

    def _ferry_segment_interior_in_water(self, tile_system, x1: float, y1: float, x2: float, y2: float) -> bool:
        """Deprecated wrapper — use _ferry_segment_crosses_open_water."""
        game_map = self.game.get_map()
        return _ferry_segment_crosses_open_water(game_map, x1, y1, x2, y2)

    def _validate_ferry_route(self, points: list) -> dict:
        """Validate ferry route: endpoints near water; segment interior mostly open water."""
        game_map = self.game.get_map()

        if not points:
            raise ValueError("Ferry route must have at least one point")
        if len(points) < 2:
            raise ValueError("Ferry route must have at least two points.")

        for i, (x, y) in enumerate(points):
            if not _ferry_point_near_water(game_map, float(x), float(y)):
                raise ValueError(
                    f"Ferry routes must be within 60m of water. Point {i + 1} at ({float(x):.1f}, {float(y):.1f}) "
                    f"is too far from water (on land)."
                    )
            
        for i in range(len(points) - 1):
            x1, y1 = float(points[i][0]), float(points[i][1])
            x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
            if not _ferry_segment_crosses_open_water(game_map, x1, y1, x2, y2):
                raise ValueError(
                    f"Ferry routes must cross open water. Segment {i + 1} from ({x1:.1f}, {y1:.1f}) to "
                    f"({x2:.1f}, {y2:.1f}) is not a valid water crossing."
                )

        return {'success': True}

    def _require_ferry_terminal_near_coast(self, game_map, grid_x: float, grid_y: float) -> None:
        """Ferry stations must be within 60 m of water (same rule as ferry line geometry)."""
        ts = getattr(game_map, "tile_system", None)
        if not ts or not hasattr(ts, "is_point_near_water"):
            return
        gx, gy = float(grid_x), float(grid_y)
        if not ts.is_point_near_water(gx, gy, 60.0, skip_osm=False):
            raise ValueError(
                "Ferry terminals must be within 60 m of the coast or navigable water."
            )

    def _require_station_not_in_deep_water(self, game_map, grid_x: float, grid_y: float) -> None:
        """Stations in water must be within STATION_MAX_WATER_DISTANCE_TO_LAND_M of land."""
        preset_name = getattr(game_map, "map_preset_name", None)
        from map_preset_loader import preset_has_water_areas
        if not (preset_name and preset_has_water_areas(preset_name)):
            return
        gx, gy = float(grid_x), float(grid_y)
        max_m = float(STATION_MAX_WATER_DISTANCE_TO_LAND_M)
        ts = getattr(game_map, "tile_system", None)
        if ts and hasattr(ts, "is_point_in_water"):
            try:
                in_water = bool(ts.is_point_in_water(gx, gy, skip_osm=True))
            except TypeError:
                in_water = bool(ts.is_point_in_water(gx, gy))
            if not in_water:
                return
            if hasattr(ts, "is_point_near_land"):
                if not ts.is_point_near_land(gx, gy, max_m, skip_osm=True):
                    raise ValueError(
                        f"Stations cannot be built in water more than {int(max_m)} m from land."
                    )
                return
        if preset_name:
            from map_preset_loader import preset_is_point_in_water
            if preset_is_point_in_water(preset_name, gx, gy, game_map, skip_osm=True):
                raise ValueError(
                    f"Stations cannot be built in water more than {int(max_m)} m from land."
                )
    
    def _validate_ferry_route_fallback(self, points: list, game_map, MAX_DISTANCE_M: float) -> dict:
        """Fallback ferry validation when tile system is not available.
        Less accurate - only checks if points are directly on water, not 60m proximity.
        
        Args:
            points: List of (x, y) coordinate tuples
            game_map: Map object
            MAX_DISTANCE_M: Maximum distance in meters (not used in fallback, but kept for API consistency)
            
        Returns:
            dict with 'success' (bool) and 'error_message' (str, if failed)
        """
        import math
        
        def is_point_in_water(x, y):
            preset_name = getattr(game_map, 'map_preset_name', None)
            if preset_name:
                from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
                if preset_has_water_areas(preset_name):
                    return preset_is_point_in_water(preset_name, x, y, game_map)
            if game_map.osm_integration:
                # Ensure width and height are available
                map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
                map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
                grid_size = max(map_width, map_height)
                center_lat = game_map.map_center_lat or 0.0
                center_lon = game_map.map_center_lon or 0.0
                lat, lon = game_map.osm_integration.convert_grid_to_latlon(x, y, center_lat, center_lon, grid_size)
                return game_map.osm_integration.is_water_at_latlon(lat, lon)
            return False
        
        if len(points) < 2:
            raise ValueError("Ferry route must have at least two points.")

        for idx, (x, y) in enumerate(points):
            if not is_point_in_water(x, y):
                raise ValueError(
                    f"Ferry routes must be within 60m of water. Point {idx+1} at ({x:.1f}, {y:.1f}) is not on water (tile system required for proximity checking)."
                )

        # Check all segments
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i+1]
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            if not is_point_in_water(mid_x, mid_y):
                raise ValueError(
                    f"Ferry routes must be within 60m of water. Segment {i+1} from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) is not on water (tile system required for proximity checking)."
                )
        
        return {'success': True}  # All checks passed
    
    def _validate_bus_route(self, points: list) -> dict:
        """Validate bus routes: land segments on streets; water crossings may be straight."""
        game_map = self.game.get_map()
        return _validate_route_streets_on_land(
            points,
            game_map,
            self.game.get_year(),
            is_bus_line=True,
            is_ferry_line=False,
        )

    def _ai_guard_normalize_xy(self, x, y) -> tuple:
        if isinstance(x, (list, tuple)):
            return float(x[0]), float(x[1])
        if hasattr(x, "x"):
            return float(x.x), float(x.y)
        return float(x), float(y)

    def _ai_guard_pop_at_endpoint(self, game_map, x, y) -> int:
        """Population / demand proxy at a route endpoint for AI operating-margin checks."""
        fx, fy = self._ai_guard_normalize_xy(x, y)
        preset_name = getattr(game_map, "map_preset_name", None) or ""
        pop_map = getattr(game_map, "population", None) or {}
        try:
            from map_preset_loader import get_preset_ai_station_population

            v = int(get_preset_ai_station_population(game_map, fx, fy, pop_map))
            if v > 0:
                return v
        except Exception:
            pass
        if hasattr(game_map, "get_available_passengers"):
            try:
                ix, iy = int(round(fx)), int(round(fy))
                peak = int(game_map.get_available_passengers(ix, iy, "peak") or 0)
                off = int(game_map.get_available_passengers(ix, iy, "off_peak") or 0)
                night = int(game_map.get_available_passengers(ix, iy, "night") or 0)
                w = peak * 0.4 + off * 0.4 + night * 0.2
                if w > 0:
                    return int(w)
            except Exception:
                pass
        try:
            return int(pop_map.get((int(round(fx)), int(round(fy))), 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _ai_guard_interior_max_pop(self, game_map, route_points: list, max_samples: int = 7) -> int:
        """Maximum population/demand proxy along the route interior (OSM polylines through dense cores)."""
        if not game_map or not route_points or len(route_points) < 2:
            return 0
        pts = []
        for p in route_points:
            if not p:
                continue
            pts.append(self._ai_guard_normalize_xy(p[0], p[1]))
        if len(pts) < 2:
            return 0
        seg_lens = []
        total = 0.0
        for i in range(len(pts) - 1):
            sl = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            seg_lens.append(sl)
            total += sl
        if total <= 1e-9:
            return 0
        best = 0
        n = max(1, int(max_samples))
        for k in range(1, n + 1):
            dist_along = total * (k / (n + 1))
            acc = 0.0
            for j, sl in enumerate(seg_lens):
                if acc + sl >= dist_along - 1e-9:
                    t = (dist_along - acc) / sl if sl > 1e-9 else 0.0
                    ax, ay = pts[j]
                    bx, by = pts[j + 1]
                    mx, my = ax + t * (bx - ax), ay + t * (by - ay)
                    best = max(best, self._ai_guard_pop_at_endpoint(game_map, mx, my))
                    break
                acc += sl
        return int(best)

    def _ai_planned_transfer_revenue_at_endpoint(
        self,
        x: float,
        y: float,
        current_year: int,
        building_player: Player = None,
    ) -> float:
        """Conservative annual revenue uplift when a planned endpoint connects to existing lines (transfer fares)."""
        nearby = self.find_stations_near_point((float(x), float(y)), max_distance=5.0)
        if not nearby:
            return 0.0
        from inflation_utils import get_nominal_fare_cap

        sy = self.get_timeline_start_year()
        ey = self.get_timeline_end_year()
        _, fare_cap = get_nominal_fare_cap(int(current_year), start_year=sy, end_year=ey)
        margin = max(0.25, min(10.0, getattr(self.game, "profit_margin", 1.0)))
        bonus = 0.0
        for _station, line, walk_dist in nearby:
            if not line or not line.get_stations() or not line.get_vehicles():
                continue
            walk_factor = max(0.15, 1.0 - float(walk_dist) / 5.0)
            other_fare = min(float(getattr(line, "fare", fare_cap) or fare_cap), float(fare_cap))
            station_count = max(2, len(line.get_stations()))
            demand_proxy = float(station_count) * 180.0 * walk_factor
            # Transfer trips pay each line's fare; model extra ridership on the planned line.
            leg_value = demand_proxy * other_fare * margin * 0.12
            other_owner = getattr(line, "owner", None)
            if building_player and other_owner and other_owner is not building_player:
                leg_value *= 1.35
            bonus = max(bonus, leg_value)
        return float(bonus)

    def _ai_collect_line_routes_for_competition(self) -> list:
        """All completed and queued line polylines (any owner) for parallel-corridor scoring."""
        routes = []
        game_map = self.get_map()
        if not game_map:
            return routes
        map_lines = game_map.get_lines_by_name() if hasattr(game_map, 'get_lines_by_name') else {}
        if not isinstance(map_lines, dict):
            map_lines = {}
        for name, line in map_lines.items():
            r = []
            if hasattr(self, 'get_line_route_including_extensions'):
                try:
                    r = self.get_line_route_including_extensions(name) or []
                except Exception:
                    r = []
            if len(r) < 2 and line and hasattr(line, 'get_route'):
                r = line.get_route() or []
            if len(r) >= 2:
                routes.append(r)
        bq = self.get_build_queue() if hasattr(self, 'get_build_queue') else []
        if not isinstance(bq, list):
            bq = []
        chunks_by_key: dict = {}
        for build in bq:
            if build.get('type') not in ('line', 'line_extension'):
                continue
            data = build.get('data') or {}
            pts = data.get('points') or []
            if len(pts) < 2:
                continue
            player = build.get('player')
            name = (data.get('name') or '').strip()
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

    def _ai_parallel_corridor_competition_factor(
        self,
        route_points: list,
        existing_routes: list = None,
        parallel_distance_grid: float = None,
    ) -> float:
        """1.0 = no nearby parallel line; lower when the route hugs existing corridors."""
        import math
        from game_presets import AI_PARALLEL_COMPETITION_DISTANCE_GRID

        if not route_points or len(route_points) < 2:
            return 1.0
        max_d = float(
            parallel_distance_grid
            if parallel_distance_grid is not None
            else AI_PARALLEL_COMPETITION_DISTANCE_GRID
        )
        if max_d <= 0:
            return 1.0
        if existing_routes is None:
            existing_routes = self._ai_collect_line_routes_for_competition()
        if not existing_routes:
            return 1.0

        def _pt_seg_dist(px, py, ax, ay, bx, by):
            dx = bx - ax
            dy = by - ay
            dist_sq = dx * dx + dy * dy
            if dist_sq < 1e-20:
                return math.hypot(px - ax, py - ay)
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / dist_sq))
            nx = ax + t * dx
            ny = ay + t * dy
            return math.hypot(px - nx, py - ny)

        def _min_dist_to_routes(px, py):
            best = float('inf')
            for route in existing_routes:
                for i in range(len(route) - 1):
                    a, b = route[i], route[i + 1]
                    ax = float(a[0]) if isinstance(a, (list, tuple)) else float(getattr(a, 'x', 0))
                    ay = float(a[1]) if isinstance(a, (list, tuple)) else float(getattr(a, 'y', 0))
                    bx = float(b[0]) if isinstance(b, (list, tuple)) else float(getattr(b, 'x', 0))
                    by = float(b[1]) if isinstance(b, (list, tuple)) else float(getattr(b, 'y', 0))
                    best = min(best, _pt_seg_dist(px, py, ax, ay, bx, by))
            return best

        sample_step = max(0.01, max_d * 0.5)
        total_len = 0.0
        parallel_len = 0.0
        for i in range(len(route_points) - 1):
            a, b = route_points[i], route_points[i + 1]
            ax = float(a[0]) if isinstance(a, (list, tuple)) else float(getattr(a, 'x', 0))
            ay = float(a[1]) if isinstance(a, (list, tuple)) else float(getattr(a, 'y', 0))
            bx = float(b[0]) if isinstance(b, (list, tuple)) else float(getattr(b, 'x', 0))
            by = float(b[1]) if isinstance(b, (list, tuple)) else float(getattr(b, 'y', 0))
            seg_len = math.hypot(bx - ax, by - ay)
            if seg_len <= 0:
                continue
            total_len += seg_len
            n_samples = max(1, int(math.ceil(seg_len / sample_step)))
            chunk = seg_len / n_samples
            for j in range(n_samples):
                t = (j + 0.5) / n_samples
                px = ax + t * (bx - ax)
                py = ay + t * (by - ay)
                if _min_dist_to_routes(px, py) <= max_d:
                    parallel_len += chunk
        if total_len <= 0:
            return 1.0
        parallel_fraction = parallel_len / total_len
        return max(0.12, 1.0 - 0.82 * parallel_fraction)

    def _ai_planned_line_gross_annual_revenue_floor(
        self,
        pop1: int,
        pop2: int,
        route_length: float,
        current_year: int,
        interior_max_pop: int = 0,
        route_points: list = None,
        player: Player = None,
    ) -> float:
        """Conservative annual revenue potential (no construction cost) for endpoint demand vs length."""
        if route_length <= 0:
            return 0.0
        p1 = float(max(0, pop1))
        p2 = float(max(0, pop2))
        interior = float(max(0, int(interior_max_pop)))
        # Corridor through a dense cell: boost product beyond endpoints-only when interior demand dominates.
        prod = p1 * p2
        if interior > 0:
            prod = max(prod, interior * max(p1, p2, max(1.0, interior * 0.25)))
        connection_value = prod / max(0.1, float(route_length))
        sy = self.get_timeline_start_year()
        ey = self.get_timeline_end_year()
        span = max(1, ey - sy)
        from inflation_utils import get_nominal_fare_cap

        sy = self.get_timeline_start_year()
        ey = self.get_timeline_end_year()
        _, fare_cap = get_nominal_fare_cap(
            int(current_year), start_year=sy, end_year=ey,
        )
        base = connection_value * fare_cap * 0.35
        margin = max(0.25, min(10.0, getattr(self.game, "profit_margin", 1.0)))
        base *= margin
        try:
            em = float(self.game.event_system.get_combined_effects(int(current_year)).get("profit_multiplier", 1.0))
        except Exception:
            em = 1.0
        base *= min(1.0, em) * 0.9
        if route_points and len(route_points) >= 2:
            p0, p1 = route_points[0], route_points[-1]
            base += self._ai_planned_transfer_revenue_at_endpoint(
                float(p0[0]), float(p0[1]), int(current_year), player,
            )
            base += self._ai_planned_transfer_revenue_at_endpoint(
                float(p1[0]), float(p1[1]), int(current_year), player,
            )
            comp = self._ai_parallel_corridor_competition_factor(route_points)
            base *= comp
        return float(base)

    @staticmethod
    def _nimby_line_elevation_chance_multiplier(
        elevation_levels: list,
        segment_lengths: list = None,
    ) -> float:
        """Length-weighted NIMBY lawsuit chance multiplier from segment elevations."""
        if not elevation_levels:
            return 1.0
        weights = segment_lengths if segment_lengths and len(segment_lengths) == len(elevation_levels) else None
        if weights:
            total = sum(max(0.0, float(w)) for w in weights)
            if total <= 0:
                weights = None
        if not weights:
            mults = [
                LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER.get(elev, 1.0)
                for elev in elevation_levels
            ]
            return sum(mults) / len(mults) if mults else 1.0
        weighted = 0.0
        for elev, weight in zip(elevation_levels, weights):
            weighted += max(0.0, float(weight)) * LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER.get(elev, 1.0)
        return weighted / total

    def _maybe_nimby_lawsuit_for_line_build(
        self,
        player: Player,
        elevation_levels: list,
        segment_lengths: list = None,
        route_points: list = None,
    ) -> Optional[dict]:
        """Roll for NIMBY lawsuit when queuing line construction. Returns prosecution result or None."""
        from agency import get_agency_by_name
        from lawsuit import LAWSUIT_EXCUSES

        nimby = get_agency_by_name("NIMBYs")
        current_year = self.get_year()
        if not nimby or current_year < nimby.activation_year or player.is_player_eliminated():
            return None

        skip_id = self.game._lawsuit_protection_skip_player_id(current_year)
        if skip_id is not None and player.get_player_id() == skip_id:
            return None

        laws = getattr(self.game, '_government_laws', {})
        if player.get_player_id() in laws.get('lawsuit_protection', []):
            return None

        elev_mult = self._nimby_line_elevation_chance_multiplier(elevation_levels, segment_lengths)
        off_street_mult = 1.0
        if route_points and len(route_points) >= 2:
            from game_presets import OFF_STREET_NIMBY_CHANCE_BONUS_PER_FRACTION
            frac = _route_off_street_length_fraction(self.get_map(), route_points, current_year)
            if frac > 0:
                off_street_mult = 1.0 + float(OFF_STREET_NIMBY_CHANCE_BONUS_PER_FRACTION) * frac
        chance = LINE_NIMBY_LAWSUIT_BASE_CHANCE * elev_mult * off_street_mult
        if random.random() >= chance:
            return None

        nimby_excuses = [e for e in LAWSUIT_EXCUSES if e.cause in _LINE_NIMBY_EXCUSE_CAUSES]
        if not nimby_excuses:
            return None
        excuse = random.choice(nimby_excuses)
        player.add_lawsuit_excuse(excuse)
        return self.game.prosecute_excuse_with_agency(nimby, player, excuse, current_year)

    def create_line_complete(self, name: str, points: list, player: Player, elevation_levels: list = None, is_ferry_line: bool = False, is_bus_line: bool = False, cost_info: dict = None, use_fast_path: bool = False) -> dict:
        """Complete line creation operation. Returns dict with result info.
        
        Args:
            name: Line name
            points: List of (x, y) coordinate tuples
            player: Player creating the line
            elevation_levels: List of elevation levels ('street', 'above_ground', 'underground', 'water') for each segment.
                             Must have len(points) - 1 elements. Required - no default.
            is_ferry_line: If True, creates a FerryLine instead of a regular Line.
            is_bus_line: If True, creates a BusLine instead of a regular Line.
            cost_info: Pre-computed cost (skips calculate_line_cost). Required when use_fast_path=True.
            use_fast_path: Skip water/terrain/road checks and tile pre-cache. For AI only. Requires cost_info.
        """
        import math
        
        # OPTIMIZATION: Early validation - check inputs first before expensive operations
        if not points or len(points) < 2:
            raise ValueError("Line must have at least 2 points")
        name = (name or "").strip()
        if not name:
            raise ValueError("Line name cannot be empty.")
        self.assert_line_name_globally_available(name)
        
        # OPTIMIZATION: Calculate segment lengths once, then derive total_length
        segment_lengths = []
        total_length = 0.0
        if len(points) > 1:
            total_length, segment_lengths = _polyline_metrics(points)
        
        # OPTIMIZATION: Fast duplicate name checking with early exit
        game_map = self.game.get_map()
        
        human_cost_previewed = (
            cost_info is not None
            and not getattr(player, 'is_ai', False)
            and not use_fast_path
        )
        
        if not use_fast_path:
            _assert_points_in_buildable_area(game_map, points)
        
        build_queue = self.game.build_queue
        if getattr(player, 'is_ai', False) and _ai_line_geometry_exists_elsewhere(points, game_map, build_queue):
            raise ValueError(
                "AI players cannot create a line with the same route as an existing or in-progress line."
            )
        ai_fast = bool(use_fast_path and getattr(player, 'is_ai', False))
        skip_osm_recheck = human_cost_previewed or ai_fast
        if not is_ferry_line:
            street_check = _validate_land_line_route_for_build(
                game_map, points, current_year=self.get_year(),
                is_bus_line=is_bus_line, is_ferry_line=False, player=player,
                ai_fast=ai_fast, skip_osm=skip_osm_recheck, for_extension=False,
                elevation_levels=elevation_levels, cost_info=cost_info,
                use_fast_path=use_fast_path, human_cost_previewed=human_cost_previewed,
            )
            if not street_check.get('success'):
                return {
                    'success': False,
                    'error_message': street_check.get('error_message'),
                    'line': None,
                    'cost': 0,
                    'build_time': 0,
                    'points_count': len(points),
                }
        
        # Lines may cross or share corridors with other routes.

        if getattr(player, 'is_ai', False) and not is_ferry_line:
            ep0, ep1 = points[0], points[-1]
            line_cost_hint = None
            if cost_info and isinstance(cost_info.get('cost'), (int, float)):
                line_cost_hint = int(cost_info['cost'])
            two_station_err = self._ai_line_supports_two_stations_error(
                player, points, is_bus_line=is_bus_line, line_cost=line_cost_hint,
            )
            if two_station_err:
                return {
                    'success': False,
                    'error_message': two_station_err,
                    'line': None,
                    'cost': 0,
                    'build_time': 0,
                    'points_count': len(points),
                }
            pop1 = self._ai_guard_pop_at_endpoint(game_map, ep0[0], ep0[1])
            pop2 = self._ai_guard_pop_at_endpoint(game_map, ep1[0], ep1[1])
            if pop1 <= 0 or pop2 <= 0:
                if not self._ai_route_has_two_viable_station_sites(
                    player, game_map, points, is_bus_line=is_bus_line,
                ):
                    return {
                        'success': False,
                        'error_message': 'AI lines must have two viable station sites along the route.',
                    'line': None,
                    'cost': 0,
                    'build_time': 0,
                    'points_count': len(points),
                }
        
        # Get construction workers (can be 0 - jobs can start without workers, they just won't progress)
        construction_workers = self.get_player_construction_worker_count(player)

        if use_fast_path and is_ferry_line:
            raise ValueError("Ferry lines cannot use the fast build path; water proximity must be validated.")

        # Ferry routes: preset-first water validation (no redundant tile/OSM proximity loops).
        if is_ferry_line:
            try:
                self._validate_ferry_route(points)
            except ValueError as e:
                return {
                    'success': False,
                    'error_message': str(e),
                    'line': None,
                    'cost': 0,
                    'build_time': 0,
                    'points_count': len(points),
                }
        
        if use_fast_path and cost_info is None:
                raise ValueError("use_fast_path requires cost_info")
        
        # Cost calculation: reuse cost_info if provided (avoids redundant calculation from UI preview).
        if cost_info is None:
            cost_info = self.calculate_line_cost(
                points,
                elevation_levels,
                is_bus_line=is_bus_line,
                is_ferry_line=is_ferry_line
            )
        base_cost = cost_info['cost']
        effective_cost = self.get_effective_cost_for_player(player, base_cost)
        required = len(points) - 1
        elev = (elevation_levels if elevation_levels and len(elevation_levels) >= required else
                cost_info.get('elevation_levels') if cost_info and cost_info.get('elevation_levels') and len(cost_info.get('elevation_levels', [])) >= required else
                None)
        if elev is None or len(elev) < required:
            default = ['water'] if is_ferry_line else (['street'] if is_bus_line else ['above_ground'])
            elevation_levels = (elev or []) + default * max(0, required - len(elev or []))
        else:
            elevation_levels = elev
        
        # Check if player can afford
        if self.get_player_money(player) < effective_cost:
            raise ValueError(f"Player {self.get_player_name(player)} cannot afford line creation (needs ${effective_cost}).")
        
        # Deduct money (AI difficulty applies: hard=0.85x, easy=1.15x)
        self.deduct_player_money(player, effective_cost, reason='Line build')
        self.invalidate_ridership_cache()
        
        # OPTIMIZATION: Reuse total_length already calculated above
        base_turns = max(1, int(total_length) + 1)
        
        # Estimate build time based on current workers (workers work actively each turn)
        # With 0 workers: base_turns
        # With 4+ workers: base_turns * 0.2 (80% faster)
        work_rate = max(0.2, 1.0 - min(construction_workers * 0.2, 0.8))
        estimated_turns = max(1, int(base_turns * work_rate + 0.999))  # Round up to integer
        
        # Batched build-queue jobs: many point-to-point segments per job (same cost, far fewer turns).
        batches = _batch_polyline_segments_for_build(points, elevation_levels)
        for batch in batches:
            segment_data = {
                'name': name,
                'points': batch['points'],
                'segment_index': batch['segment_index'],
                'total_segments': batch['total_segments'],
                'is_ferry_line': is_ferry_line,
                'is_bus_line': is_bus_line,
                'elevation_levels': batch.get('elevation_levels'),
            }
            self.add_to_build_queue('line', segment_data, player, construction_workers)

        nimby_lawsuit = self._maybe_nimby_lawsuit_for_line_build(
            player, elevation_levels, segment_lengths, route_points=points,
        )

        return {
            'line': None,  # Line not created yet, will be created when build completes
            'cost': effective_cost,
            'construction_workers': construction_workers,
            'build_time': estimated_turns,
            'base_turns': base_turns,
            'length': cost_info['length'],
            'points_count': cost_info['points_count'],
            'remaining_money': self.get_player_money(player),
            'osm_snapped': game_map.use_osm if game_map.use_osm else False,
            'nimby_lawsuit': nimby_lawsuit,
            'segments_queued': len(batches),
        }
    
    def calculate_upgrade_cost(self, line, upgrades: list[str]) -> dict:
        """Calculate upgrade cost for a line. Returns dict with cost info"""
        import math
        route = self.get_line_route(line)
        total_length = 0.0
        if len(route) > 1:
            for i in range(len(route) - 1):
                x1, y1 = route[i]
                x2, y2 = route[i+1]
                total_length += math.hypot(x2 - x1, y2 - y1)
        length_units = int(max(1, round(total_length)))
        
        overhead_cost = 300 + (50 * length_units)
        thirdrail_cost = 400 + (60 * length_units)
        total_cost = 0
        
        current_year = self.get_year()
        valid_upgrades = []
        
        for upgrade in upgrades:
            if upgrade == 'overhead' and not getattr(line, 'has_overhead_wire', False):
                if current_year < 1890:
                    raise ValueError("Overhead wire technology is not invented until 1890.")
                total_cost += overhead_cost
                valid_upgrades.append('overhead')
            elif upgrade == 'thirdrail' and not getattr(line, 'has_third_rail', False):
                if current_year < 1890:
                    raise ValueError("Third rail technology is not invented until 1890.")
                total_cost += thirdrail_cost
                valid_upgrades.append('thirdrail')
        
        return {
            'cost': total_cost,
            'upgrades': valid_upgrades
        }
    
    def upgrade_line_complete(self, line_name: str, player: Player, upgrades: list[str]) -> dict:
        """Complete line upgrade operation. Creates a construction job instead of immediately applying.
        Returns dict with result info including construction job details."""
        line = self.get_map_lines().get(line_name)
        if not line:
            raise ValueError("Selected line not found.")
        
        cost_info = self.calculate_upgrade_cost(line, upgrades)
        
        if not cost_info['upgrades']:
            raise ValueError("No new upgrades selected.")
        
        # Check if player can afford
        if self.get_player_money(player) < cost_info['cost']:
            raise ValueError(f"This upgrade costs ${cost_info['cost']} but you have ${self.get_player_money(player)}")
        
        # Deduct money
        self.deduct_player_money(player, cost_info['cost'], reason='Line upgrade')
        
        # Get construction workers (can be 0 - job can start without workers, they just won't progress)
        construction_workers = self.get_player_construction_worker_count(player)
        
        # Calculate build time based on line distance
        route = self.get_line_route(line)
        import math
        total_length = 0.0
        if len(route) > 1:
            for i in range(len(route) - 1):
                x1, y1 = route[i]
                x2, y2 = route[i+1]
                total_length += math.hypot(x2 - x1, y2 - y1)
        base_work = max(1.0, float(int(total_length) + 1))
        
        # Estimate build time based on current workers
        if construction_workers > 0:
            estimated_turns = max(1, int(math.ceil(base_work / construction_workers)))
        else:
            estimated_turns = int(base_work)  # No workers = takes base_work turns
        
        # Create construction job data
        upgrade_data = {
            'line_name': line_name,
            'upgrades': cost_info['upgrades']
        }
        
        # Add to build queue as a construction job
        self.add_to_build_queue('line_upgrade', upgrade_data, player, construction_workers)
        
        return {
            'line_name': line_name,
            'cost': cost_info['cost'],
            'upgrades': cost_info['upgrades'],
            'remaining_money': self.get_player_money(player),
            'construction_workers': construction_workers,
            'build_time': estimated_turns,
            'base_work': base_work
        }
    
    def get_station_creation_info(self, player: Player) -> dict:
        """Get information needed for station creation dialog.
        Includes both complete lines and incomplete lines from build queue."""
        line_names = self.get_player_line_names(player)
        if not line_names:
            return {'has_lines': False}
        
        # Get all lines with their routes and existing stations
        lines_info = {}
        all_lines = self.get_map_lines()
        occupied_coordinates = set()
        
        # Get incomplete lines from build queue
        incomplete_line_routes = {}  # line_name -> set of all points in incomplete segments
        for build in self.game.build_queue:
            if build['type'] == 'line' and build['data'].get('name') in line_names:
                line_name = build['data'].get('name')
                if 'segment_index' in build['data']:
                    # Segment
                    segment_points = build['data'].get('points', [])
                    if line_name not in incomplete_line_routes:
                        incomplete_line_routes[line_name] = set()
                    incomplete_line_routes[line_name].update(segment_points)
                else:
                    # Full line
                    line_points = build['data'].get('points', [])
                    if line_name not in incomplete_line_routes:
                        incomplete_line_routes[line_name] = set()
                    incomplete_line_routes[line_name].update(line_points)
        
        for line_name in line_names:
            route = []
            # Get route from complete line if it exists
            line = all_lines.get(line_name)
            if line:
                route = list(self.get_line_route(line))
            
            # Add incomplete segments to route
            if line_name in incomplete_line_routes:
                incomplete_points = incomplete_line_routes[line_name]
                # Add points that aren't already in route
                for point in incomplete_points:
                    if point not in route:
                        route.append(point)
            
            if not route:
                continue
            
            # Get stations
            stations = []
            if line:
                stations = self.get_line_stations(line)
            
            station_coords = []
            for station in stations:
                if station:
                    try:
                        x, y = station.get_x(), station.get_y()
                        station_coords.append((x, y))
                        occupied_coordinates.add((x, y))
                    except:
                        pass
            
            lines_info[line_name] = {
                'route': route,
                'stations': station_coords,
                'available_points': [(x, y) for (x, y) in route if (x, y) not in occupied_coordinates]
            }
        
        return {
            'has_lines': True,
            'line_names': line_names,
            'lines_info': lines_info,
            'occupied_coordinates': occupied_coordinates
        }
    
    def _is_point_on_route(self, route: list, x: float, y: float, tolerance: float = 0.5) -> bool:
        """Check if point (x,y) is on or near the route (within tolerance)."""
        import math
        for i, pt in enumerate(route):
            px = float(pt[0]) if hasattr(pt, '__getitem__') else float(pt.x if hasattr(pt, 'x') else pt)
            py = float(pt[1]) if hasattr(pt, '__getitem__') else float(pt.y if hasattr(pt, 'y') else pt)
            if abs(px - x) < tolerance and abs(py - y) < tolerance:
                return True
        for i in range(len(route) - 1):
            p1, p2 = route[i], route[i + 1]
            x1 = float(p1[0]) if hasattr(p1, '__getitem__') else float(getattr(p1, 'x', p1))
            y1 = float(p1[1]) if hasattr(p1, '__getitem__') else float(getattr(p1, 'y', p1))
            x2 = float(p2[0]) if hasattr(p2, '__getitem__') else float(getattr(p2, 'x', p2))
            y2 = float(p2[1]) if hasattr(p2, '__getitem__') else float(getattr(p2, 'y', p2))
            dx, dy = x2 - x1, y2 - y1
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq > 0:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
                proj_x, proj_y = x1 + t * dx, y1 + t * dy
                if math.hypot(x - proj_x, y - proj_y) < tolerance:
                    return True
        return False

    def create_station_complete(self, station_name: str, x: float, y: float, player: Player, line_names: list[str]) -> dict:
        """Complete station creation operation. Returns dict with result info"""

        station_name = (station_name or "").strip()
        self.assert_station_name_unique_for_player(
            player, station_name, x=float(x), y=float(y), line_names=line_names,
        )
        
        # Get construction workers (can be 0 - jobs can start without workers, they just won't progress)
        construction_workers = self.get_player_construction_worker_count(player)
        
        # Calculate cost (includes half-price for extensions within 50m of existing station)
        base_cost = self.calculate_station_cost(len(line_names), float(x), float(y))
        
        # Check if player can afford
        if self.get_player_money(player) < base_cost:
            raise ValueError(f"Station costs ${base_cost} but you have ${self.get_player_money(player)}")
        
        # Verify point is on selected lines and snap to route
        x, y = float(x), float(y)
        all_lines = self.get_map_lines()
        for line_name in line_names:
            route = None
            line = all_lines.get(line_name)
            if line:
                route = self.get_line_route(line)
            if not route:
                all_segments = []
                for build in self.game.build_queue:
                    if build['type'] == 'line' and build['data'].get('name') == line_name:
                        seg_pts = build['data'].get('points', [])
                        if seg_pts and len(seg_pts) >= 2:
                            seg_idx = build['data'].get('segment_index', 0)
                            all_segments.append((seg_idx, seg_pts))
                if all_segments:
                    all_segments.sort(key=lambda x: x[0])
                    route = []
                    for _idx, seg_pts in all_segments:
                        def _pt_eq(a, b):
                            if a is b:
                                return True
                            ax, ay = (float(a[0]), float(a[1])) if len(a) >= 2 else (0, 0)
                            bx, by = (float(b[0]), float(b[1])) if len(b) >= 2 else (0, 0)
                            return abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6
                        if not route:
                            route.extend(seg_pts)
                        elif seg_pts and _pt_eq(seg_pts[0], route[-1]):
                            route.extend(seg_pts[1:])
                        else:
                            route.extend(seg_pts)
            if not route:
                raise ValueError(f"Line '{line_name}' not found")
            snapped = self._snap_point_to_route(route, x, y, 0.5)
            if snapped is None:
                raise ValueError(f"Point ({x:.6f}, {y:.6f}) is not on line '{line_name}'")
            x, y = snapped

        game_map = self.game.get_map()

        def _station_targets_ferry(lnames: list) -> bool:
            for ln in lnames:
                lob = all_lines.get(ln)
                if isinstance(lob, Ferry):
                    return True
                for build in (self.game.build_queue or []):
                    if build.get("type") != "line":
                        continue
                    bd = build.get("data") or {}
                    if bd.get("name") == ln and bool(bd.get("is_ferry_line")):
                        return True
            return False

        if _station_targets_ferry(line_names):
            self._require_ferry_terminal_near_coast(game_map, x, y)

        self._require_station_not_in_deep_water(game_map, x, y)

        self.assert_ai_stop_minimum_direct_separation(player, x, y)

        # Deduct money
        self.deduct_player_money(player, base_cost, reason='Station build')
        self.invalidate_ridership_cache()
        
        # Calculate build time based on construction workers
        build_data = {'name': station_name, 'x': x, 'y': y, 'line_names': line_names}
        build_time = self.game.calculate_build_time('station', build_data, construction_workers)
        base_turns = max(1, len(line_names))  # Base turns for station
        
        # Add to build queue instead of creating immediately
        self.add_to_build_queue('station', build_data, player, construction_workers)
        
        return {
            'station': None,  # Station not created yet, will be created when build completes
            'cost': base_cost,
            'construction_workers': construction_workers,
            'build_time': build_time,
            'base_turns': base_turns,
            'line_names': line_names,
            'coordinates': (x, y),
            'remaining_money': self.get_player_money(player)
        }
    
    def create_station_immediate(self, station_name: str, x: float, y: float, player: Player, line_names: list[str]) -> dict:
        """Create a station immediately (no build queue). Used for OSM map Add Station flow.
        Uses calculate_station_cost for pricing (including half-price for extensions within 50m)."""
        from station import Station
        
        station_name = (station_name or "").strip()
        self.assert_station_name_unique_for_player(
            player, station_name, x=float(x), y=float(y), line_names=line_names,
        )
        
        x, y = float(x), float(y)
        cost = self.calculate_station_cost(len(line_names), x, y)
        
        if self.get_player_money(player) < cost:
            raise ValueError(f"Station costs ${cost} but you have ${self.get_player_money(player)}")
        
        all_lines = self.get_map_lines()
        for line_name in line_names:
            line = all_lines.get(line_name)
            if not line:
                raise ValueError(f"Line '{line_name}' not found")
            route = self.get_line_route(line)
            if route:
                snapped = self._snap_point_to_route(route, x, y, max_distance=0.5)
                if snapped is None:
                    raise ValueError(f"Point ({x:.1f}, {y:.1f}) is not on line '{line_name}'")
                x, y = snapped

        game_map = self.game.get_map()
        if any(isinstance(all_lines.get(ln), Ferry) for ln in line_names):
            self._require_ferry_terminal_near_coast(game_map, x, y)

        self._require_station_not_in_deep_water(game_map, x, y)

        self.assert_ai_stop_minimum_direct_separation(player, x, y)

        self.deduct_player_money(player, cost, reason='Station build')
        self.invalidate_ridership_cache()
        
        station = Station(station_name, x, y, line_names=line_names)
        for line_name in line_names:
            self.add_station_to_line(player, line_name, station)
            if hasattr(station, 'add_line'):
                station.add_line(line_name)
        
        if not hasattr(game_map, 'stations'):
            game_map.stations = []
        if station not in game_map.stations:
            game_map.stations.append(station)
        
        return {
            'station': station,
            'cost': cost,
            'line_names': line_names,
            'coordinates': (x, y),
            'remaining_money': self.get_player_money(player)
        }

    def get_line_build_type_flags(self, line_name: str) -> tuple:
        """Return (is_ferry_line, is_bus_line) for a built or queued line."""
        line = self.get_map_lines().get(line_name)
        if line is not None:
            return isinstance(line, Ferry), isinstance(line, BusLine)
        for build in self.get_build_queue():
            if build.get('type') != 'line':
                continue
            data = build.get('data') or {}
            if data.get('name') == line_name:
                return bool(data.get('is_ferry_line', False)), bool(data.get('is_bus_line', False))
        return False, False
    
    def calculate_extend_cost(self, line_name: str, additional_points: list, extend_from_end: bool, elevation_levels: list = None) -> dict:
        """Calculate cost to extend a line. Uses same variable cost system as calculate_line_cost.
        Returns dict with cost info"""
        import math
        current_year = self.game.get_year()
        game_map = self.game.get_map()
        
        # Get historical event multipliers
        event_cost_multiplier = 1.0
        try:
            event_effects = self.game.event_system.get_combined_effects(current_year)
            event_cost_multiplier = event_effects.get('construction_cost_multiplier', 1.0)
        except:
            pass
        
        # Base costs per unit length (same as calculate_line_cost)
        road_cost_per_unit = 150
        land_with_buildings_cost_per_unit = 300
        land_without_buildings_cost_per_unit = 150
        water_cost_per_unit = 300
        
        # Elevation multipliers
        elevation_multipliers = {
            'street': 1.0,
            'above_ground': 1.5,
            'underground': 2.0
        }
        
        # Elevation levels must be provided - no default
        # For extensions, we need elevation for:
        # - Connection segment (if extending from end: first elevation, if extending from start: last elevation)
        # - Each segment between additional points (len(additional_points) - 1 segments)
        # Total needed: len(additional_points) elevations (one per point, used for segment after that point)
        if elevation_levels is None:
            raise ValueError("Elevation levels must be specified for line extension. Cannot default to 'street'.")
        # For extensions: need one elevation per additional point (used for segment starting at that point)
        # Plus one for the connection segment if needed
        required_elevations = len(additional_points)  # One per point
        if len(elevation_levels) < required_elevations:
            raise ValueError(f"Not enough elevation levels provided. Need {required_elevations} levels for {len(additional_points)} points, got {len(elevation_levels)}")
        
        line = self.get_map_lines().get(line_name)
        existing_route = None
        
        if line:
            existing_route = self.get_line_route(line)
        else:
            build_queue = self.get_build_queue()
            all_segments = []
            for build in build_queue:
                if build.get('type') == 'line' and build.get('data', {}).get('name') == line_name:
                    segment_points = build.get('data', {}).get('points', [])
                    if segment_points and len(segment_points) >= 2:
                        segment_idx = build.get('data', {}).get('segment_index', 0)
                        all_segments.append((segment_idx, segment_points))
            
            if all_segments:
                all_segments.sort(key=lambda x: x[0])
                route_points = []
                for segment_idx, segment_points in all_segments:
                    if not route_points:
                        route_points.extend(segment_points)
                    else:
                        if segment_points[0] == route_points[-1]:
                            route_points.extend(segment_points[1:])
                        else:
                            route_points.extend(segment_points)
                if route_points:
                    existing_route = route_points
        
        if not existing_route:
            raise ValueError(f"Line '{line_name}' not found or has no route")
        
        # Calculate total length
        total_length = 0.0
        if extend_from_end and len(existing_route) > 0 and len(additional_points) > 0:
            last_x, last_y = existing_route[-1]
            first_x, first_y = additional_points[0]
            total_length += math.hypot(first_x - last_x, first_y - last_y)
        elif not extend_from_end and len(existing_route) > 0 and len(additional_points) > 0:
            first_x, first_y = existing_route[0]
            last_new_x, last_new_y = additional_points[-1]
            total_length += math.hypot(first_x - last_new_x, first_y - last_new_y)
        
        if len(additional_points) > 1:
            for i in range(len(additional_points) - 1):
                x1, y1 = additional_points[i]
                x2, y2 = additional_points[i+1]
                total_length += math.hypot(x2 - x1, y2 - y1)

        LINE_COST_MULTIPLIER = 1.5
        is_ferry_line, is_bus_line = self.get_line_build_type_flags(line_name)
        if is_ferry_line:
            # Fast cost preview — full ferry validation runs in extend_line_complete.
            ferry_cost = max(15, int(total_length * 40 * 0.8 * LINE_COST_MULTIPLIER))
            return {
                'cost': ferry_cost,
                'length': total_length,
                'points_count': len(additional_points),
                'elevation_levels': elevation_levels,
            }
        if is_bus_line:
            bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * LINE_COST_MULTIPLIER))
            return {
                'cost': bus_cost,
                'length': total_length,
                'points_count': len(additional_points),
                'elevation_levels': elevation_levels,
            }
        
        # OPTIMIZATION: Reuse shared water checking method instead of duplicating logic
        water_cache = {}
        def is_point_in_water(x, y):
            return self._is_point_in_water_cached(x, y, game_map, water_cache)
        
        def is_point_on_road(x, y):
            preset_name = getattr(game_map, 'map_preset_name', None)
            if preset_name:
                from map_preset_loader import preset_has_road_support, get_preset_is_point_on_road
                if preset_has_road_support(preset_name):
                    return get_preset_is_point_on_road(preset_name, x, y, current_year, game_map)
            if game_map.use_osm and game_map.osm_integration:
                if hasattr(game_map, '_osm_rights_of_way') and game_map._osm_rights_of_way:
                    nearest = game_map.find_nearest_right_of_way(x, y)
                    return nearest is not None
            return False
        
        def is_point_on_bridge(x, y):
            preset_name = getattr(game_map, 'map_preset_name', None)
            if not preset_name:
                return False
            from map_preset_loader import get_preset_is_point_on_bridge
            return get_preset_is_point_on_bridge(preset_name, x, y, current_year, game_map)
        
        def has_buildings(x, y):
            grid_x, grid_y = int(round(x)), int(round(y))
            pop = game_map.get_population(grid_x, grid_y)
            return pop > 0
        
        # Calculate cost
        total_cost = 0
        
        # Cost for connection segment (if any)
        if extend_from_end and len(existing_route) > 0 and len(additional_points) > 0:
            last_x, last_y = existing_route[-1]
            first_x, first_y = additional_points[0]
            conn_length = math.hypot(first_x - last_x, first_y - last_y)
            mid_x, mid_y = (last_x + first_x) / 2, (last_y + first_y) / 2
            conn_in_water = is_point_in_water(last_x, last_y) or is_point_in_water(first_x, first_y)
            conn_on_road = is_point_on_road(mid_x, mid_y)
            conn_on_bridge = is_point_on_bridge(mid_x, mid_y)
            conn_on_bridge_or_tunnel = conn_on_bridge or _segment_on_bridge_or_tunnel(
                game_map, last_x, last_y, first_x, first_y, current_year,
            )
            conn_has_buildings = has_buildings(mid_x, mid_y)
            
            if conn_on_road:
                base_cost_per_unit = road_cost_per_unit
            elif conn_in_water and not conn_on_bridge_or_tunnel:
                base_cost_per_unit = water_cost_per_unit
            elif conn_has_buildings:
                base_cost_per_unit = land_with_buildings_cost_per_unit
            else:
                base_cost_per_unit = land_without_buildings_cost_per_unit
            
            conn_cost = base_cost_per_unit * conn_length
            elevation = elevation_levels[0] if elevation_levels else 'street'
            if conn_in_water and not conn_on_bridge_or_tunnel and elevation == 'street':
                raise ValueError(f"Cannot build at street level on water. Connection segment from ({last_x:.1f}, {last_y:.1f}) to ({first_x:.1f}, {first_y:.1f}) is in water. Use 'above_ground' or 'underground' instead.")
            conn_cost *= elevation_multipliers.get(elevation, 1.0)
            conn_cost *= event_cost_multiplier
            _, is_bus_ext = self.get_line_build_type_flags(line_name)
            conn_cost *= _off_street_segment_cost_multiplier(
                game_map, last_x, last_y, first_x, first_y, current_year,
                is_bus_line=is_bus_ext,
            )
            total_cost += int(conn_cost)
        elif not extend_from_end and len(existing_route) > 0 and len(additional_points) > 0:
            first_x, first_y = existing_route[0]
            last_new_x, last_new_y = additional_points[-1]
            conn_length = math.hypot(first_x - last_new_x, first_y - last_new_y)
            mid_x, mid_y = (first_x + last_new_x) / 2, (first_y + last_new_y) / 2
            conn_in_water = is_point_in_water(first_x, first_y) or is_point_in_water(last_new_x, last_new_y)
            conn_on_road = is_point_on_road(mid_x, mid_y)
            conn_on_bridge = is_point_on_bridge(mid_x, mid_y)
            conn_on_bridge_or_tunnel = conn_on_bridge or _segment_on_bridge_or_tunnel(
                game_map, last_new_x, last_new_y, first_x, first_y, current_year,
            )
            conn_has_buildings = has_buildings(mid_x, mid_y)
            
            if conn_on_road:
                base_cost_per_unit = road_cost_per_unit
            elif conn_in_water and not conn_on_bridge_or_tunnel:
                base_cost_per_unit = water_cost_per_unit
            elif conn_has_buildings:
                base_cost_per_unit = land_with_buildings_cost_per_unit
            else:
                base_cost_per_unit = land_without_buildings_cost_per_unit
            
            conn_cost = base_cost_per_unit * conn_length
            elevation = elevation_levels[-1] if elevation_levels else 'street'
            if conn_in_water and not conn_on_bridge_or_tunnel and elevation == 'street':
                raise ValueError(f"Cannot build at street level on water. Connection segment from ({last_new_x:.1f}, {last_new_y:.1f}) to ({first_x:.1f}, {first_y:.1f}) is in water. Use 'above_ground' or 'underground' instead.")
            conn_cost *= elevation_multipliers.get(elevation, 1.0)
            conn_cost *= event_cost_multiplier
            _, is_bus_ext = self.get_line_build_type_flags(line_name)
            conn_cost *= _off_street_segment_cost_multiplier(
                game_map, last_new_x, last_new_y, first_x, first_y, current_year,
                is_bus_line=is_bus_ext,
            )
            total_cost += int(conn_cost)
        
        # Cost for extension segments
        if len(additional_points) > 1:
            for i in range(len(additional_points) - 1):
                x1, y1 = additional_points[i]
                x2, y2 = additional_points[i+1]
                seg_length = math.hypot(x2 - x1, y2 - y1)
                mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
                seg_in_water = is_point_in_water(x1, y1) or is_point_in_water(x2, y2)
                seg_on_road = is_point_on_road(mid_x, mid_y)
                seg_on_bridge = is_point_on_bridge(mid_x, mid_y)
                seg_on_bridge_or_tunnel = seg_on_bridge or _segment_on_bridge_or_tunnel(
                    game_map, x1, y1, x2, y2, current_year,
                )
                seg_has_buildings = has_buildings(mid_x, mid_y)
                
                if seg_on_road:
                    base_cost_per_unit = road_cost_per_unit
                elif seg_in_water and not seg_on_bridge_or_tunnel:
                    base_cost_per_unit = water_cost_per_unit
                elif seg_has_buildings:
                    base_cost_per_unit = land_with_buildings_cost_per_unit
                else:
                    base_cost_per_unit = land_without_buildings_cost_per_unit
                
                seg_cost = base_cost_per_unit * seg_length
                if extend_from_end:
                    elevation = elevation_levels[i + 1] if (i + 1) < len(elevation_levels) else 'street'
                else:
                    elevation = elevation_levels[i] if i < len(elevation_levels) else 'street'
                if seg_in_water and not seg_on_bridge_or_tunnel and elevation == 'street':
                    raise ValueError(f"Cannot build at street level on water. Extension segment from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) is in water. Use 'above_ground' or 'underground' instead.")
                seg_cost *= elevation_multipliers.get(elevation, 1.0)
                seg_cost *= event_cost_multiplier
                _, is_bus_ext = self.get_line_build_type_flags(line_name)
                seg_cost *= _off_street_segment_cost_multiplier(
                    game_map, x1, y1, x2, y2, current_year,
                    is_bus_line=is_bus_ext,
                )
                total_cost += int(seg_cost)
        
        if total_cost < 15:
            total_cost = 15
        
        return {
            'cost': total_cost,
            'length': total_length,
            'points_count': len(additional_points),
            'elevation_levels': elevation_levels
        }
    
    def extend_line_complete(self, line_name: str, player: Player, additional_points: list, extend_from_end: bool, elevation_levels: list = None, cost_info: dict = None, use_fast_path: bool = False) -> dict:
        """Complete line extension operation. Adds extension to build queue instead of extending immediately.
        Returns dict with result info. use_fast_path: when True and cost_info provided, skip expensive calculate_extend_cost (AI only)."""
        from worker import CONSTRUCTION_WORKER
        import math
        
        line = self.get_map_lines().get(line_name)
        from line import Ferry as FerryLineClass
        if line and isinstance(line, FerryLineClass):
            existing_route = list(line.get_route() or [])
            merged_preview = _ai_merged_line_route(
                existing_route, additional_points, extend_from_end
            )
            if len(merged_preview) >= 2:
                self._validate_ferry_route(merged_preview)
        if not line:
            build_queue = self.get_build_queue()
            found_in_queue = False
            for build in build_queue:
                if build.get('type') == 'line' and build.get('data', {}).get('name') == line_name:
                    if build.get('player') == player:
                        found_in_queue = True
                        break
            if not found_in_queue:
                raise ValueError(f"Line '{line_name}' not found or not owned by player")
        
        # Fast path: use pre-computed cost from AI (avoids expensive water/road checks, max 10s per extension)
        human_cost_previewed = (
            cost_info is not None
            and not getattr(player, 'is_ai', False)
            and not use_fast_path
        )
        if use_fast_path and cost_info:
            pass  # use cost_info as-is
        elif human_cost_previewed:
            pass  # human already ran calculate_extend_cost in the UI
        else:
            cost_info = self.calculate_extend_cost(line_name, additional_points, extend_from_end, elevation_levels)
        elevation_levels = cost_info['elevation_levels']  # Use validated elevation levels
        effective_cost = self.get_effective_cost_for_player(player, cost_info['cost'])
        
        if self.get_player_money(player) < effective_cost:
            raise ValueError(f"Extending costs ${effective_cost} but you have ${self.get_player_money(player)}")

        game_map = self.game.get_map()
        is_bus_line = bool(line and line.__class__.__name__ == 'Bus')
        is_ferry_line = bool(line and line.__class__.__name__ == 'Ferry')
        if line is None:
            for build in self.get_build_queue():
                if build.get('type') != 'line':
                    continue
                data = build.get('data', {})
                if data.get('name') != line_name:
                    continue
                is_bus_line = bool(data.get('is_bus_line', False))
                is_ferry_line = bool(data.get('is_ferry_line', False))
                break
        existing_route = list(self.get_line_route_including_extensions(line_name) or [])
        merged = _ai_merged_line_route(existing_route, additional_points, extend_from_end)
        ai_fast = bool(use_fast_path and getattr(player, 'is_ai', False))
        skip_osm_recheck = human_cost_previewed or ai_fast

        if not use_fast_path:
            _assert_points_in_buildable_area(game_map, additional_points)

        merged_elevs = _merged_route_elevation_levels(
            line,
                merged,
            additional_points,
            extend_from_end,
            elevation_levels,
                is_bus_line=is_bus_line,
        )
        if not is_ferry_line:
            street_check = _validate_land_line_route_for_build(
                game_map, merged, current_year=self.get_year(),
                is_bus_line=is_bus_line, is_ferry_line=False, player=player,
                ai_fast=ai_fast, skip_osm=skip_osm_recheck, for_extension=True,
                elevation_levels=merged_elevs, cost_info=cost_info,
                use_fast_path=use_fast_path, human_cost_previewed=human_cost_previewed,
            )
            if not street_check.get('success'):
                raise ValueError(
                    street_check.get('error_message') or 'Route must follow streets on land.'
                )
        
        construction_workers = self.get_player_construction_worker_count(player)
        
        # Deduct money immediately (AI difficulty applies)
        self.deduct_player_money(player, effective_cost, reason='Line extension')
        self.invalidate_ridership_cache()
        
        # Calculate base build time for extension
        total_length = cost_info['length']
        base_turns = max(1, int(total_length) + 1)
        
        # Estimate build time based on current workers
        work_rate = max(0.2, 1.0 - min(construction_workers * 0.2, 0.8))
        estimated_turns = max(1, int(base_turns * work_rate + 0.999))  # Round up to integer
        
        # Split extension into batched segments for progressive building
        batches = _batch_polyline_segments_for_build(additional_points, elevation_levels)
        for batch in batches:
            batch_elev = batch.get('elevation_levels')
            segment_elevation = (
                batch_elev[0] if batch_elev else (
                    elevation_levels[batch['segment_index']]
                    if elevation_levels and batch['segment_index'] < len(elevation_levels)
                    else 'street'
                )
            )
            extension_data = {
                'line_name': line_name,
                'points': batch['points'],
                'extend_from_end': extend_from_end,
                'segment_index': batch['segment_index'],
                'total_segments': batch['total_segments'],
                'elevation_levels': batch_elev if batch_elev else [segment_elevation],
            }
            self.add_to_build_queue('line_extension', extension_data, player, construction_workers)

        _, ext_segment_lengths = _polyline_metrics(additional_points)
        ext_elevations = []
        if elevation_levels and len(additional_points) > 1:
            if extend_from_end:
                ext_elevations = list(elevation_levels[1:len(additional_points)])
            else:
                ext_elevations = list(elevation_levels[: len(additional_points) - 1])
        route = list(self.get_line_route(line)) if line else []
        ext_route = list(additional_points)
        if extend_from_end and route:
            ext_route = [route[-1]] + ext_route if route else ext_route
        elif route:
            ext_route = ext_route + [route[0]]
        nimby_lawsuit = self._maybe_nimby_lawsuit_for_line_build(
            player, ext_elevations or elevation_levels, ext_segment_lengths or None,
            route_points=ext_route if len(ext_route) >= 2 else None,
        )

        return {
            'line_name': line_name,
            'cost': cost_info['cost'],
            'length': cost_info['length'],
            'points_count': cost_info['points_count'],
            'remaining_money': self.get_player_money(player),
            'construction_workers': construction_workers,
            'build_time': estimated_turns,
            'base_turns': base_turns,
            'nimby_lawsuit': nimby_lawsuit,
            'segments_queued': len(batches),
        }
    
    def get_line_route_including_extensions(self, line_name: str) -> list:
        """Full route including pending extension segments in the build queue."""
        def _pt(p):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                return (float(p[0]), float(p[1]))
            return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

        line = self.get_map_lines().get(line_name)
        route = list(self.get_line_route(line)) if line else []
        route = [_pt(p) for p in route] if route else []

        build_queue = self.get_build_queue()
        if not route:
            line_segments = []
            for build in build_queue:
                if build.get('type') != 'line':
                    continue
                data = build.get('data', {})
                if data.get('name') != line_name:
                    continue
                segment_points = data.get('points', [])
                if segment_points and len(segment_points) >= 2:
                    line_segments.append((data.get('segment_index', 0), segment_points))
            if line_segments:
                line_segments.sort(key=lambda x: x[0])
                route_points = []
                for _segment_idx, segment_points in line_segments:
                    segment_points = [_pt(p) for p in segment_points]
                    if not route_points:
                        route_points.extend(segment_points)
                    elif segment_points[0] == route_points[-1]:
                        route_points.extend(segment_points[1:])
                    else:
                        route_points.extend(segment_points)
                route = route_points

        ext_builds = []
        for build in build_queue:
            if build.get('type') != 'line_extension':
                continue
            data = build.get('data', {})
            if data.get('line_name') != line_name:
                continue
            ext_builds.append((data.get('segment_index', 0), data.get('points', []), data.get('extend_from_end', True)))
        ext_builds.sort(key=lambda x: x[0])

        for _seg_idx, ext_pts, extend_from_end in ext_builds:
            if not ext_pts:
                continue
            ext_pts = [_pt(p) for p in ext_pts]
            if extend_from_end and route:
                last = route[-1]
                first_ext = ext_pts[0]
                if abs(last[0] - first_ext[0]) < 0.01 and abs(last[1] - first_ext[1]) < 0.01:
                    route = route + ext_pts[1:]
                else:
                    route = route + ext_pts
            elif not extend_from_end and route:
                first = route[0]
                last_ext = ext_pts[-1]
                if abs(first[0] - last_ext[0]) < 0.01 and abs(first[1] - last_ext[1]) < 0.01:
                    route = ext_pts[:-1] + route
                else:
                    route = ext_pts + route
            else:
                route = route + ext_pts
        return route

    def extension_attach_from_closest_end(self, line_name: str, click_point: tuple) -> bool:
        """Pick route end vs start by proximity to the first extension click."""
        route = self.get_line_route_including_extensions(line_name)
        return _extension_attach_from_closest_end(route, click_point)

    def get_pending_line_extension_routes(self) -> list:
        """Grid polylines for line extensions in the build queue (for map display)."""
        def _pt(p):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                return (float(p[0]), float(p[1]))
            return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

        def _dedupe_append(route: list, pt) -> None:
            if not route or route[-1] != pt:
                route.append(pt)

        extensions_by_line = {}
        for build in self.get_build_queue():
            if build.get('type') != 'line_extension':
                continue
            data = build.get('data') or {}
            line_name = data.get('line_name')
            if not line_name:
                continue
            if line_name not in extensions_by_line:
                extensions_by_line[line_name] = {
                    'segments': [],
                    'player': build.get('player'),
                    'extend_from_end': data.get('extend_from_end', True),
                }
            extensions_by_line[line_name]['segments'].append(
                (data.get('segment_index', 0), data.get('points', []))
            )

        results = []
        for line_name, info in extensions_by_line.items():
            segments = sorted(info['segments'], key=lambda x: x[0])
            ext_route = []
            for _seg_idx, seg_pts in segments:
                for p in seg_pts or []:
                    _dedupe_append(ext_route, _pt(p))
            if len(ext_route) < 1:
                continue

            line = self.get_map_lines().get(line_name)
            built_route = [_pt(p) for p in (self.get_line_route(line) if line else [])]
            extend_from_end = info.get('extend_from_end', True)
            display_route = []
            if built_route:
                if extend_from_end:
                    display_route.append(built_route[-1])
                    for p in ext_route:
                        _dedupe_append(display_route, p)
                else:
                    for p in ext_route:
                        _dedupe_append(display_route, p)
                    _dedupe_append(display_route, built_route[0])
            else:
                display_route = ext_route

            if len(display_route) < 2:
                continue

            line_type = 'train'
            if line:
                try:
                    from line import Ferry, Bus
                    if isinstance(line, Ferry):
                        line_type = 'ferry'
                    elif isinstance(line, Bus):
                        line_type = 'bus'
                except Exception:
                    pass

            results.append({
                'line_name': line_name,
                'points': display_route,
                'player': info.get('player'),
                'line_type': line_type,
                'extend_from_end': extend_from_end,
            })
        return results

    def get_station_creation_available_points(self, line_name: str) -> list[tuple]:
        """Get all points on a line where stations can be built (anywhere on the line)"""
        line = self.get_map_lines().get(line_name)
        if not line:
            return []
        
        route = self.get_line_route(line)
        if not route:
            return []
        
        return list(route)
    
    def get_lines_through_point(self, x: int, y: int, player: Player) -> list[str]:
        """Get all lines owned by player that pass through a point"""
        line_names = self.get_player_line_names(player)
        all_lines = self.get_map_lines()
        intersecting_lines = []
        
        for line_name, line in all_lines.items():
            if line_name not in line_names:
                continue
            route = self.get_line_route(line)
            if route and (x, y) in route:
                intersecting_lines.append(line_name)
        
        return intersecting_lines
    
    def get_lines_at_point(self, x: float, y: float, tolerance: float = 0.5) -> list:
        """Get all lines that pass through or near a point (within tolerance distance), with owner info.
        Includes both completed lines and lines under construction."""
        import math
        all_lines = self.get_map_lines()
        lines_at_point = []
        
        for line_name, line in all_lines.items():
            route = self.get_line_route(line)
            if not route:
                continue
            # Check if point is within tolerance distance of any segment in the route
            for i in range(len(route) - 1):
                # Get coordinates from route points (handle both tuples and GridPoint objects)
                pt1 = route[i]
                pt2 = route[i + 1]
                if isinstance(pt1, (list, tuple)) and len(pt1) >= 2:
                    x1, y1 = float(pt1[0]), float(pt1[1])
                elif hasattr(pt1, 'x') and hasattr(pt1, 'y'):
                    x1, y1 = float(pt1.x), float(pt1.y)
                else:
                    continue
                if isinstance(pt2, (list, tuple)) and len(pt2) >= 2:
                    x2, y2 = float(pt2[0]), float(pt2[1])
                elif hasattr(pt2, 'x') and hasattr(pt2, 'y'):
                    x2, y2 = float(pt2.x), float(pt2.y)
                else:
                    continue
                
                # Calculate distance from point to line segment
                # Vector from pt1 to pt2
                dx = x2 - x1
                dy = y2 - y1
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq == 0:
                    # Segment is a point - check distance to that point
                    dist = math.hypot(x - x1, y - y1)
                else:
                    # Project point onto line segment
                    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
                    proj_x = x1 + t * dx
                    proj_y = y1 + t * dy
                    dist = math.hypot(x - proj_x, y - proj_y)
                
                if dist <= tolerance:
                    owner = self.get_line_owner(line)
                    entry = (line, line_name, owner)
                    if entry not in lines_at_point:
                        lines_at_point.append(entry)
                    break  # Found this line, move to next
        
        # Check lines under construction
        build_queue = self.get_build_queue()
        for build in build_queue:
            if build['type'] == 'line':
                build_data = build['data']
                line_name = build_data.get('name', 'Unknown')
                player = build.get('player')
                
                segment_points = build_data.get('points', [])
                if 'segment_index' in build_data:
                    # Only check the specific segment being built
                    segment_points = build_data.get('points', [])
                else:
                    line_points = build_data.get('points', [])
                
                # Check if point is within tolerance of any segment in build points
                for i in range(len(segment_points) - 1):
                    pt1 = segment_points[i]
                    pt2 = segment_points[i + 1]
                    if isinstance(pt1, (list, tuple)) and len(pt1) >= 2:
                        x1, y1 = float(pt1[0]), float(pt1[1])
                    elif hasattr(pt1, 'x') and hasattr(pt1, 'y'):
                        x1, y1 = float(pt1.x), float(pt1.y)
                    else:
                        continue
                    if isinstance(pt2, (list, tuple)) and len(pt2) >= 2:
                        x2, y2 = float(pt2[0]), float(pt2[1])
                    elif hasattr(pt2, 'x') and hasattr(pt2, 'y'):
                        x2, y2 = float(pt2.x), float(pt2.y)
                    else:
                        continue
                    
                    # Calculate distance from point to line segment
                    dx = x2 - x1
                    dy = y2 - y1
                    seg_len_sq = dx * dx + dy * dy
                    if seg_len_sq == 0:
                        dist = math.hypot(x - x1, y - y1)
                    else:
                        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
                        proj_x = x1 + t * dx
                        proj_y = y1 + t * dy
                        dist = math.hypot(x - proj_x, y - proj_y)
                    
                    if dist <= tolerance:
                        line = all_lines.get(line_name)
                        entry = (line, line_name, player)
                        if entry not in lines_at_point:
                            lines_at_point.append(entry)
                        break  # Found this line in construction, move to next

        for ext_info in self.get_pending_line_extension_routes():
            line_name = ext_info.get('line_name', 'Unknown')
            player = ext_info.get('player')
            segment_points = ext_info.get('points', [])
            for i in range(len(segment_points) - 1):
                pt1 = segment_points[i]
                pt2 = segment_points[i + 1]
                x1, y1 = float(pt1[0]), float(pt1[1])
                x2, y2 = float(pt2[0]), float(pt2[1])
                dx = x2 - x1
                dy = y2 - y1
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq == 0:
                    dist = math.hypot(x - x1, y - y1)
                else:
                    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
                    proj_x = x1 + t * dx
                    proj_y = y1 + t * dy
                    dist = math.hypot(x - proj_x, y - proj_y)
                if dist <= tolerance:
                    line = all_lines.get(line_name)
                    entry = (line, line_name, player)
                    if entry not in lines_at_point:
                        lines_at_point.append(entry)
                    break
        
        return lines_at_point
    
    def get_line_stats(self, line: Line) -> dict:
        """Get statistics for a line. Uses only cached values to avoid expensive calculations."""
        current_year = self.get_year()
        route = self.get_line_route(line)
        owner = self.get_line_owner(line)
        vehicles = self.get_line_vehicles(line)
        stations = self.get_line_stations(line)
        
        profit = self.calc_line_profit(line, current_year)
        
        maintenance = self.calc_line_maintenance(line, current_year)
        fare = self.get_line_fare(line)
        
        return {
            'name': self.get_line_name(line),
            'owner': owner,
            'owner_name': owner.get_name() if owner else 'None',
            'route_length': len(route) if route else 0,
            'num_stations': len(stations) if stations else 0,
            'num_vehicles': len(vehicles) if vehicles else 0,
            'profit': profit,
            'maintenance': maintenance,
            'fare': fare,
            'route': route
        }
    
    def calculate_station_cost(self, num_lines: int, x: float = None, y: float = None) -> int:
        """Calculate station creation cost. If (x,y) provided and an existing station
        is within 50m (station extension to another line), returns half price."""
        import math
        base_cost = self.inflate_economic_value(500, max_factor=10.0)
        additional_cost = self.inflate_economic_value(250, max_factor=10.0)
        cost = base_cost + ((num_lines - 1) * additional_cost)
        if x is not None and y is not None:
            game_map = self.game.get_map()
            if hasattr(game_map, 'stations') and game_map.stations:
                # 50m in grid units: assume 1 unit ≈ 1km for standard maps
                distance_threshold = 0.05
                for station in game_map.stations:
                    dx = station.x - x
                    dy = station.y - y
                    if math.hypot(dx, dy) <= distance_threshold:
                        return max(1, cost // 2)
        return cost
    
    def _direct_distance_between_stop_positions_m(self, x1: float, y1: float, x2: float, y2: float) -> float:
        """Geodesic distance on OSM maps (via grid->lat/lon); else Euclidean grid distance × 1000 (1 unit ≈ 1 km)."""
        game_map = self.game.get_map()
        if getattr(game_map, 'use_osm', False) and getattr(game_map, 'osm_integration', None) is not None:
            try:
                lat0 = game_map.map_center_lat if game_map.map_center_lat is not None else 0.0
                lon0 = game_map.map_center_lon if game_map.map_center_lon is not None else 0.0
                gs = max(int(game_map.width or 1), int(game_map.height or 1))
                oi = game_map.osm_integration
                lat1, lon1 = oi.convert_grid_to_latlon(float(x1), float(y1), lat0, lon0, gs)
                lat2, lon2 = oi.convert_grid_to_latlon(float(x2), float(y2), lat0, lon0, gs)
                from map_presets_nyc_road_utils import _haversine_distance_m
                return float(_haversine_distance_m(lat1, lon1, lat2, lon2))
            except Exception:
                pass
        return float(math.hypot(x2 - x1, y2 - y1) * 1000.0)

    def _shared_track_threshold_grid(self) -> float:
        """Convert SHARED_TRACK_MAX_DISTANCE_M to grid units for the current map."""
        meters_per_grid = self._direct_distance_between_stop_positions_m(0.0, 0.0, 1.0, 0.0)
        if meters_per_grid <= 0:
            return SHARED_TRACK_MAX_DISTANCE_M / 1000.0
        return SHARED_TRACK_MAX_DISTANCE_M / meters_per_grid

    def _existing_track_segments_for_sharing(self, exclude_line_name: str = None) -> list:
        """Route polylines of built and under-construction lines (candidate shared tracks)."""
        game_map = self.game.get_map()
        build_queue = getattr(self.game, 'build_queue', None) or []
        return _collect_routes_for_overlap_validation(game_map, build_queue, exclude_line_name or '')

    def _segment_reuses_existing_track(
        self, x1: float, y1: float, x2: float, y2: float, existing_routes: list, threshold_grid: float,
    ) -> bool:
        """True when segment (x1,y1)-(x2,y2) runs on top of an existing track (within threshold)."""
        if not existing_routes:
            return False

        def pt_seg(px, py, sx, sy, tx, ty):
            dx, dy = tx - sx, ty - sy
            d2 = dx * dx + dy * dy
            if d2 < 1e-20:
                return math.hypot(px - sx, py - sy)
            t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / d2))
            return math.hypot(px - (sx + t * dx), py - (sy + t * dy))

        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        for route in existing_routes:
            for k in range(len(route) - 1):
                cx, cy = float(route[k][0]), float(route[k][1])
                dx, dy = float(route[k + 1][0]), float(route[k + 1][1])
                # Shared track requires the whole new segment to lie on the existing one.
                if (
                    pt_seg(x1, y1, cx, cy, dx, dy) <= threshold_grid
                    and pt_seg(x2, y2, cx, cy, dx, dy) <= threshold_grid
                    and pt_seg(mx, my, cx, cy, dx, dy) <= threshold_grid
                ):
                    return True
        return False

    def _ai_min_two_station_route_length_grid(self, is_bus_line: bool) -> float:
        from game_presets import (
            AI_BUS_MIN_TWO_STATION_ROUTE_LENGTH_GRID,
            AI_RAIL_MIN_TWO_STATION_ROUTE_LENGTH_GRID,
        )
        return (
            float(AI_BUS_MIN_TWO_STATION_ROUTE_LENGTH_GRID)
            if is_bus_line
            else float(AI_RAIL_MIN_TWO_STATION_ROUTE_LENGTH_GRID)
        )

    def _ai_station_site_viable_on_route(
        self, player: Player, game_map, x: float, y: float, *, is_bus_line: bool = False,
    ) -> bool:
        """True when (x, y) can host a queued/built station (not a bare route vertex)."""
        if not game_map:
            return False
        try:
            from map_preset_loader import get_preset_ai_station_population
            pop_map = getattr(game_map, 'population', None) or {}
            pop = get_preset_ai_station_population(game_map, float(x), float(y), pop_map)
        except Exception:
            pop = 0
        if pop <= 0:
            gx, gy = int(round(x)), int(round(y))
            pop = int((getattr(game_map, 'population', None) or {}).get((gx, gy), 0) or 0)
        if pop <= 0:
            return False
        if getattr(player, 'is_ai', False):
            try:
                self.assert_ai_stop_minimum_direct_separation(player, float(x), float(y))
            except ValueError:
                return False
        return True

    def _ai_find_route_station_site_from_end(
        self, player: Player, game_map, route_points: list, *, from_start: bool, is_bus_line: bool = False,
    ) -> Optional[tuple]:
        if not route_points:
            return None
        seq = route_points if from_start else list(reversed(route_points))
        for pt in seq:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                x, y = float(pt[0]), float(pt[1])
            else:
                x, y = float(getattr(pt, 'x', 0)), float(getattr(pt, 'y', 0))
            if self._ai_station_site_viable_on_route(
                player, game_map, x, y, is_bus_line=is_bus_line,
            ):
                return (x, y)
        return None

    def _ai_route_has_two_viable_station_sites(
        self, player: Player, game_map, route_points: list, *, is_bus_line: bool = False,
    ) -> bool:
        """Route must support two station sites along it (endpoints may be in water)."""
        if not route_points or len(route_points) < 2:
            return False
        s1 = self._ai_find_route_station_site_from_end(
            player, game_map, route_points, from_start=True, is_bus_line=is_bus_line,
        )
        s2 = self._ai_find_route_station_site_from_end(
            player, game_map, route_points, from_start=False, is_bus_line=is_bus_line,
        )
        if not s1 or not s2:
            return False
        min_sp = self._ai_min_two_station_route_length_grid(is_bus_line)
        return math.hypot(s2[0] - s1[0], s2[1] - s1[1]) >= min_sp - 1e-9

    def _ai_line_supports_two_stations_error(
        self,
        player: Player,
        points: list,
        *,
        is_bus_line: bool = False,
        line_cost: int = None,
    ) -> Optional[str]:
        """Return an error message when an AI line cannot support two endpoint stations."""
        if not points or len(points) < 2:
            return "AI lines must have at least two route points for two stations."
        min_sp = self._ai_min_two_station_route_length_grid(is_bus_line)
        total_len, _ = _polyline_metrics(points)
        if total_len < min_sp - 1e-9:
            return (
                f"AI lines must be long enough for two stations "
                f"(at least {min_sp:.2f} grid units, got {total_len:.2f})."
            )
        x0, y0 = _normalize_route_point(points[0])
        x1, y1 = _normalize_route_point(points[-1])
        if math.hypot(x1 - x0, y1 - y0) < min_sp - 1e-9:
            return (
                f"AI line endpoints are too close for two stations "
                f"(need at least {min_sp:.2f} grid units apart)."
            )
        if line_cost is not None:
            try:
                money = int(self.get_player_money(player))
                station_cost = int(self.calculate_station_cost(1))
                need = int(line_cost) + station_cost * 2
                if money < need:
                    return (
                        f"AI cannot afford this line and two endpoint stations "
                        f"(need ${need:,}, have ${money:,})."
                    )
            except (TypeError, ValueError):
                pass
        return None

    def assert_ai_stop_minimum_direct_separation(
        self, player: Player, x: float, y: float, exclude_pending_station_build: dict = None
    ) -> None:
        """Raise ValueError if an AI-owned build would place a stop within AI_MIN_STOP_DIRECT_SEPARATION_METERS
        of any completed station or any station job still in the build queue."""
        if not getattr(player, 'is_ai', False):
            return
        min_m = float(AI_MIN_STOP_DIRECT_SEPARATION_METERS)
        game_map = self.game.get_map()
        if hasattr(game_map, 'stations') and game_map.stations:
            for station in game_map.stations:
                sx = float(station.get_x()) if hasattr(station, 'get_x') else float(getattr(station, 'x', 0))
                sy = float(station.get_y()) if hasattr(station, 'get_y') else float(getattr(station, 'y', 0))
                d = self._direct_distance_between_stop_positions_m(x, y, sx, sy)
                if d < min_m:
                    raise ValueError(
                        f"AI stops must be at least {int(min_m)} m apart (straight-line). "
                        f"This site is only {d:.0f} m from existing station '{getattr(station, 'name', '?')}'."
                    )
        for build in self.game.build_queue or []:
            if build.get('type') != 'station':
                continue
            if exclude_pending_station_build is not None and build is exclude_pending_station_build:
                continue
            data = build.get('data') or {}
            try:
                qx = float(data.get('x'))
                qy = float(data.get('y'))
            except (TypeError, ValueError):
                continue
            d = self._direct_distance_between_stop_positions_m(x, y, qx, qy)
            if d < min_m:
                raise ValueError(
                    f"AI stops must be at least {int(min_m)} m apart (straight-line). "
                    f"This site is only {d:.0f} m from a station already in the construction queue."
                )

    def reset_timeline(self):
        self.game.get_timeline().reset()

    def is_game_playing(self) -> bool:
        return bool(self.game)
    
    def get_game_for_save(self) -> Game:
        """Get the game object for saving (serialization)"""
        return self.game
    
    def set_game_from_load(self, game: Game):
        """Set the game object from loading (deserialization)"""
        self.game = game
        if getattr(game, 'government', None) is None:
            from government import Government
            start_year = getattr(game.get_timeline(), 'initial_year', 1830)
            game.government = Government(start_year, len(game.get_players()))
        # Ensure map has end_year and start_year set (in case they weren't preserved during serialization)
        game_map = game.get_map()
        if not hasattr(game_map, 'end_year') or game_map.end_year is None:
            from map_preset_loader import get_map_timeline
            preset_name = getattr(game_map, 'map_preset_name', None)
            timeline = get_map_timeline(preset_name) if preset_name else None
            if timeline:
                game_map.end_year = timeline.get('end_year') or game_map.end_year
                if not hasattr(game_map, 'start_year') or game_map.start_year is None:
                    game_map.start_year = timeline.get('start_year', 1830)
            else:
                from map_presets_standard import STANDARD_TIMELINE
                game_map.end_year = STANDARD_TIMELINE['end_year']
                if not hasattr(game_map, 'start_year') or game_map.start_year is None:
                    game_map.start_year = STANDARD_TIMELINE['start_year']
    
    def add_station_to_line(self, player: Player, line_name: str, station):
        """Add a station to a player's line"""
        if line_name not in player.get_line_names():
            raise ValueError(f"Player does not own line '{line_name}'")
        
        line = self.game.get_map().get_lines_by_name().get(line_name)
        if not line:
            raise ValueError(f"Line '{line_name}' not found on map")
        
        # Check if a station already exists at these coordinates on this specific line
        station_x, station_y = station.get_position()
        # Check if this station already serves this line
        if hasattr(station, 'serves_line') and station.serves_line(line_name):
            # Station already serves this line, just add it to the line's station list if not already there
            if station not in line.get_stations():
                line.add_station(station)
            return
        
        # Check if a different station exists at these coordinates on this line
        for existing_station in line.get_stations():
            if existing_station.get_x() == station_x and existing_station.get_y() == station_y:
                # Same location, check if it's the same station object or a different one
                if existing_station is not station:
                    # Different station at same location on same line - not allowed
                    raise ValueError(f"A different station already exists at coordinates ({station_x}, {station_y}) on line '{line_name}'")
                else:
                    # Same station, already added
                    return
        
        # Add station to line and update station's line list
        line.add_station(station)
        if hasattr(station, 'add_line'):
            station.add_line(line_name)
        elif hasattr(station, 'line_names'):
            if line_name not in station.line_names:
                station.line_names.append(line_name)
    
    def convert_click_to_grid(self, lat: float, lon: float) -> tuple[float, float]:
        """Convert lat/lon click to grid coordinates with 0.000001 precision.
        Returns (x, y) rounded to 6 decimal places."""
        game_map = self.game.get_map()
        center_lat = game_map.map_center_lat if game_map.map_center_lat is not None else 0.0
        center_lon = game_map.map_center_lon if game_map.map_center_lon is not None else 0.0
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
        grid_size = max(map_width, map_height)
        from map_preset_loader import preset_has_water_areas
        preset_name = getattr(game_map, 'map_preset_name', None)
        is_water_preset = preset_has_water_areas(preset_name) if preset_name else False
        if game_map.use_osm and game_map.osm_integration:
            x, y = game_map.osm_integration.convert_latlon_to_grid(
                lat, lon, center_lat, center_lon, grid_size,
                map_width=game_map.width, map_height=game_map.height,
                is_nyc_proper=is_water_preset
            )
        else:
            import math
            lat_km = (lat - center_lat) * 111.0
            lon_km = (lon - center_lon) * 111.0 * math.cos(math.radians(center_lat))
            x = float(lon_km) + grid_size // 2
            y = float(lat_km) + grid_size // 2
        return (round(x, 6), round(y, 6))

    def find_nearest_point_on_line(self, line_name: str, clicked_lat: float, clicked_lon: float) -> tuple:
        """Find the nearest point on a line's route to a clicked coordinate.
        Returns (x, y) grid coordinates of the nearest point."""
        import math
        game_map = self.game.get_map()
        
        line = game_map.get_lines_by_name().get(line_name)
        if not line:
            build_queue = self.game.build_queue
            for build in build_queue:
                if build.get('type') == 'line':
                    build_data = build.get('data', {})
                    if build_data.get('name') == line_name:
                        points = build_data.get('points', [])
                        if points:
                            line_route = points
                            break
            else:
                raise ValueError(f"Line '{line_name}' not found")
        else:
            line_route = line.get_route()
        
        if not line_route or len(line_route) < 1:
            raise ValueError(f"Line '{line_name}' has no route points")
        
        center_lat = game_map.map_center_lat if game_map.map_center_lat is not None else 0.0
        center_lon = game_map.map_center_lon if game_map.map_center_lon is not None else 0.0
        # Ensure width and height are available
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
        grid_size = max(map_width, map_height)
        
        from map_preset_loader import preset_has_water_areas
        preset_name = getattr(game_map, 'map_preset_name', None)
        is_water_preset = preset_has_water_areas(preset_name) if preset_name else False
        clicked_x, clicked_y = game_map.osm_integration.convert_latlon_to_grid(
            clicked_lat, clicked_lon, center_lat, center_lon, grid_size,
            map_width=game_map.width, map_height=game_map.height,
            is_nyc_proper=is_water_preset
        ) if game_map.use_osm and game_map.osm_integration else (0, 0)
        
        min_distance = float('inf')
        nearest_point = line_route[0]
        
        for i in range(len(line_route) - 1):
            p1_x, p1_y = line_route[i]
            p2_x, p2_y = line_route[i + 1]
            
            dx = p2_x - p1_x
            dy = p2_y - p1_y
            seg_len_sq = dx * dx + dy * dy
            
            if seg_len_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((clicked_x - p1_x) * dx + (clicked_y - p1_y) * dy) / seg_len_sq))
            
            proj_x = p1_x + t * dx
            proj_y = p1_y + t * dy
            
            dist_sq = (clicked_x - proj_x) ** 2 + (clicked_y - proj_y) ** 2
            if dist_sq < min_distance:
                min_distance = dist_sq
                nearest_point = (proj_x, proj_y)
        
        return nearest_point
    
    def _snap_point_to_route(self, route: list, x: float, y: float, max_distance: float = 0.5) -> tuple:
        """Snap (x,y) to the nearest point on the route. Returns (snapped_x, snapped_y) if within max_distance, else None."""
        import math
        if not route or len(route) < 1:
            return None

        def _pt(p):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                return (float(p[0]), float(p[1]))
            return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

        min_dist_sq = float('inf')
        best = None
        for i in range(len(route) - 1):
            p1_x, p1_y = _pt(route[i])
            p2_x, p2_y = _pt(route[i + 1])
            dx, dy = p2_x - p1_x, p2_y - p1_y
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((x - p1_x) * dx + (y - p1_y) * dy) / seg_len_sq))
            proj_x = p1_x + t * dx
            proj_y = p1_y + t * dy
            d_sq = (x - proj_x) ** 2 + (y - proj_y) ** 2
            if d_sq < min_dist_sq:
                min_dist_sq = d_sq
                best = (proj_x, proj_y)
        return best if min_dist_sq <= max_distance * max_distance else None

    def add_station_to_line_at_point(self, player: Player, line_name: str, station_name: str, grid_x: float, grid_y: float) -> dict:
        """Add a station to a line at a specific grid point. Snaps point to line if near; rejects if off line.
        Uses full route including pending extensions so stations can be placed on extended segments."""
        from station import Station

        if line_name not in player.get_line_names():
            raise ValueError(f"Player does not own line '{line_name}'")

        game_map = self.game.get_map()
        line = game_map.get_lines_by_name().get(line_name)
        route = self.get_line_route_including_extensions(line_name)
        if not route:
            for build in (self.game.build_queue or []):
                if build.get('type') == 'line':
                    build_data = build.get('data', {})
                    if build_data.get('name') == line_name and build.get('player') == player:
                        route = build_data.get('points', [])
                        break
            if not route:
                raise ValueError(f"Line '{line_name}' not found")

        snapped = self._snap_point_to_route(route, float(grid_x), float(grid_y), max_distance=0.5)
        if snapped is None:
            raise ValueError(f"Point ({grid_x:.1f}, {grid_y:.1f}) is not on line '{line_name}' (must be within 0.5 units)")
        grid_x, grid_y = snapped

        is_ferry_line = isinstance(line, Ferry)
        if line is None:
            for build in (self.game.build_queue or []):
                if build.get("type") != "line":
                    continue
                bd = build.get("data") or {}
                if bd.get("name") == line_name and build.get("player") == player:
                    is_ferry_line = bool(bd.get("is_ferry_line"))
                    break
        if is_ferry_line:
            self._require_ferry_terminal_near_coast(game_map, grid_x, grid_y)

        station_name = (station_name or "").strip()
        self.assert_station_name_unique_for_player(
            player, station_name, x=float(grid_x), y=float(grid_y), line_names=[line_name],
        )
        self.assert_ai_stop_minimum_direct_separation(player, grid_x, grid_y)
        station = Station(station_name, grid_x, grid_y)
        self.add_station_to_line(player, line_name, station)
        
        if not hasattr(game_map, 'stations'):
            game_map.stations = []
        if station not in game_map.stations:
            game_map.stations.append(station)
        
        return {'success': True, 'station': station}
    
    def get_build_queue(self) -> list:
        """Get the current build queue"""
        return self.game.build_queue
    
    def process_build_queue(self):
        """Process the build queue, completing builds that are ready
        Construction workers actively work on builds each turn, reducing remaining time.
        This method contains all game logic for construction completion.
        Speed: N workers = 1/N time (1 worker = 6 turns, 2 workers = 3 turns, 3 workers = 2 turns)"""
        from worker import CONSTRUCTION_WORKER
        import math
        completed_builds = []

        # Performance: avoid O(n^2) scanning of build_queue to compute assigned workers.
        # Precompute total assigned workers per player once.
        assigned_by_player = {}
        for b in self.game.build_queue:
            p = b.get('player')
            if not p:
                continue
            aw = b.get('assigned_workers', 0)
            if aw is None:
                aw = 0
            assigned_by_player[p] = assigned_by_player.get(p, 0) + aw
        
        # Cache construction worker counts per player to avoid repeated lookups
        rented_by_player = {}
        for p in assigned_by_player.keys():
            rented_by_player[p] = self.get_player_construction_worker_count(p)
        
        for build in self.game.build_queue:
            player = build['player']
            # Get assigned workers for this specific build
            # assigned_workers should always be set (0 or more), never None after initialization
            assigned_workers = build.get('assigned_workers', 0)
            if assigned_workers is None:
                # Fallback: if somehow None, treat as 0
                assigned_workers = 0
                build['assigned_workers'] = 0
            
            # Calculate how many workers are actually available (not assigned elsewhere)
            total_rented = rented_by_player.get(player, 0)
            total_assigned = assigned_by_player.get(player, 0)
            total_assigned_elsewhere = max(0, total_assigned - assigned_workers)
            available_workers = total_rented - total_assigned_elsewhere

            # Construction only progresses when players assign construction workers.
            # No baseline workforce - jobs stall until workers are assigned.
            
            # Use only the available workers (cap at assigned_workers)
            # This allows unassigned workers to be assigned but only available ones work
            construction_workers = min(assigned_workers, available_workers) if available_workers > 0 else 0
            
            # Ensure turns_remaining exists and is an integer
            if 'turns_remaining' not in build:
                # Calculate initial turns based on workers
                if 'work_remaining' not in build:
                    build['work_remaining'] = self._calculate_base_work(build)
                work_remaining = float(build['work_remaining'])
                if construction_workers > 0:
                    build['turns_remaining'] = int(math.ceil(work_remaining / construction_workers))
                else:
                    # No workers - set to None to indicate "N/A"
                    build['turns_remaining'] = None
            
            if construction_workers == 0:
                build['turns_remaining'] = None
                continue
            
            if 'work_remaining' not in build:
                build['work_remaining'] = self._calculate_base_work(build)
            
            work_remaining = float(build['work_remaining'])
            
            work_remaining -= construction_workers
            
            if work_remaining <= 0:
                completed_builds.append(build)
            else:
                build['work_remaining'] = work_remaining
                if construction_workers > 0:
                    build['turns_remaining'] = max(1, int(math.ceil(work_remaining / construction_workers)))
                else:
                    build['turns_remaining'] = None
        
        # Complete finished builds
        if completed_builds:
            self.invalidate_ridership_cache()
        for build in completed_builds:
            if build['type'] == 'line':
                # For segment-based building, check if this is a segment or full line
                if 'segment_index' in build['data']:
                    # This is a segment - add it to the partial line
                    line_name = build['data']['name']
                    existing_line = self.game.get_map().get_lines_by_name().get(line_name)
                    segment_points = build['data']['points']
                    
                    if existing_line:
                        existing_line.route = _merge_route_with_completed_build_points(
                            existing_line.get_route(), segment_points,
                        )
                    else:
                        # Create new partial line with this segment
                        is_ferry = build['data'].get('is_ferry_line', False)
                        is_bus = build['data'].get('is_bus_line', False)
                        if is_ferry:
                            from line import Ferry
                            line = Ferry(name=build['data']['name'], object_id=None, route=segment_points, owner=build['player'], vehicles=[])
                            self.apply_timeline_fare_to_line(line)
                        elif is_bus:
                            from line import Bus
                            line = Bus(name=build['data']['name'], object_id=None, route=segment_points, owner=build['player'], vehicles=[])
                            self.apply_timeline_fare_to_line(line)
                        else:
                            line = self.game.create_line(build['data']['name'], build['player'], segment_points)
                        build['player'].acquire_line(line)
                        self.game.get_map().add_line(line)
                else:
                    # Full line completion
                    is_ferry = build['data'].get('is_ferry_line', False)
                    is_bus = build['data'].get('is_bus_line', False)
                    if is_ferry:
                        from line import Ferry
                        line = Ferry(name=build['data']['name'], object_id=None, route=build['data']['points'], owner=build['player'], vehicles=[])
                        self.apply_timeline_fare_to_line(line)
                    elif is_bus:
                        from line import Bus
                        line = Bus(name=build['data']['name'], object_id=None, route=build['data']['points'], owner=build['player'], vehicles=[])
                        self.apply_timeline_fare_to_line(line)
                    else:
                        line = self.game.create_line(build['data']['name'], build['player'], build['data']['points'])
                    build['player'].acquire_line(line)
                    self.game.get_map().add_line(line)
            elif build['type'] == 'line_extension':
                # Handle line extension completion
                line_name = build['data']['line_name']
                extension_points = build['data']['points']
                extend_from_end = build['data'].get('extend_from_end', True)
                
                # Get the existing line
                existing_line = self.game.get_map().get_lines_by_name().get(line_name)
                if not existing_line:
                    # Line doesn't exist - this shouldn't happen, but handle gracefully
                    continue
                
                from line import Ferry as FerryLineClass
                if isinstance(existing_line, FerryLineClass):
                    existing_route = list(existing_line.get_route() or [])
                    merged_preview = _ai_merged_line_route(
                        existing_route, extension_points, extend_from_end
                    )
                    try:
                        self._validate_ferry_route(merged_preview)
                    except ValueError:
                        continue
                # Extend the line based on direction
                if extend_from_end:
                    self.extend_line_route_from_end(existing_line, extension_points)
                else:
                    self.extend_line_route_from_beginning(existing_line, extension_points)
            elif build['type'] == 'station':
                from station import Station
                st_player = build['player']
                st_nm = (build['data'].get('name') or '').strip()
                self.assert_station_name_unique_for_player(
                    st_player,
                    st_nm,
                    exclude_pending_station_build=build,
                    x=float(build['data']['x']),
                    y=float(build['data']['y']),
                    line_names=build['data'].get('line_names') or [],
                )
                self.assert_ai_stop_minimum_direct_separation(
                    st_player,
                    float(build['data']['x']),
                    float(build['data']['y']),
                    exclude_pending_station_build=build,
                )
                station = Station(
                    st_nm,
                    build['data']['x'], 
                    build['data']['y'],
                    line_names=build['data'].get('line_names', [])
                )
                for line_name in build['data'].get('line_names', []):
                    all_lines = self.game.get_map().get_lines_by_name()
                    line = all_lines.get(line_name)
                    
                    if line:
                        self.add_station_to_line(build['player'], line_name, station)
                        if hasattr(station, 'add_line'):
                            station.add_line(line_name)
                    else:
                        partial_points = []
                        for build_item in self.game.build_queue:
                            if build_item['type'] == 'line' and build_item['data'].get('name') == line_name:
                                    partial_points.extend(build_item['data'].get('points', []))
                        
                        if partial_points:
                            partial_line = self.game.create_line(line_name, build['player'], partial_points)
                            build['player'].acquire_line(partial_line)
                            self.game.get_map().add_line(partial_line)
                            partial_line.add_station(station)
                            if hasattr(station, 'add_line'):
                                station.add_line(line_name)
                
                game_map = self.game.get_map()
                if not hasattr(game_map, 'stations'):
                    game_map.stations = []
                if station not in game_map.stations:
                    game_map.stations.append(station)
            elif build['type'] == 'line_upgrade':
                # Handle line upgrade completion
                line_name = build['data']['line_name']
                upgrades = build['data']['upgrades']
                
                # Get the existing line
                existing_line = self.game.get_map().get_lines_by_name().get(line_name)
                if not existing_line:
                    # Line doesn't exist - this shouldn't happen, but handle gracefully
                    continue
                
                # Apply upgrades
                if 'overhead' in upgrades:
                    self.set_line_overhead_wire(existing_line, True)
                if 'thirdrail' in upgrades:
                    self.set_line_third_rail(existing_line, True)
        
        # Store completed builds in completed_jobs list before removing from queue
        for build in completed_builds:
            # Add completion info
            completed_job = {
                'type': build['type'],
                'data': build['data'],
                'player': build['player'],
                'completed_year': self.get_year()
            }
            self.completed_jobs.append(completed_job)
        
        # Remove completed builds from queue
        self.game.build_queue = [b for b in self.game.build_queue if b not in completed_builds]
    
    def cancel_build(self, build_index: int) -> dict:
        """Cancel a build from the queue. Returns refund amount."""
        if build_index < 0 or build_index >= len(self.game.build_queue):
            raise ValueError("Invalid build index")
        
        build = self.game.build_queue[build_index]
        player = build['player']
        
        # Calculate refund (50% of cost)
        if build['type'] == 'line':
            data = build['data']
            pts = data['points']
            elev = data.get('elevation_levels')
            is_bus = data.get('is_bus_line', False)
            is_ferry = data.get('is_ferry_line', False)
            if elev is None and not is_bus and not is_ferry:
                elev = ['above_ground'] * max(0, len(pts) - 1)
            cost_info = self.calculate_line_cost(
                pts,
                elevation_levels=elev,
                is_bus_line=is_bus,
                is_ferry_line=is_ferry
            )
            refund = int(cost_info['cost'] * 0.5)
        elif build['type'] == 'line_extension':
            # Calculate extension cost
            line_name = build['data'].get('line_name')
            points = build['data'].get('points', [])
            extend_from_end = build['data'].get('extend_from_end', True)
            if line_name:
                cost_info = self.calculate_extend_cost(line_name, points, extend_from_end)
                refund = int(cost_info['cost'] * 0.5)
            else:
                refund = 0
        elif build['type'] == 'station':
            # Calculate station cost
            line_names = build['data'].get('line_names', [])
            base_cost = 100 * len(line_names)
            refund = int(base_cost * 0.5)
        elif build['type'] == 'line_upgrade':
            # Calculate upgrade cost for refund
            line_name = build['data'].get('line_name')
            upgrades = build['data'].get('upgrades', [])
            if line_name:
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line:
                    cost_info = self.calculate_upgrade_cost(line, upgrades)
                    refund = int(cost_info['cost'] * 0.5)
                else:
                    refund = 0
            else:
                refund = 0
        else:
            refund = 0
        
        # Remove from queue
        self.game.build_queue.pop(build_index)
        
        # Refund player
        self.add_player_money(player, refund, reason='Build cancel refund')
        
        return {'refund': refund, 'build_type': build['type']}
    
    def reorder_build_queue(self, old_index: int, new_index: int):
        """Reorder build queue by moving item from old_index to new_index"""
        if old_index < 0 or old_index >= len(self.game.build_queue):
            raise ValueError("Invalid old index")
        if new_index < 0 or new_index >= len(self.game.build_queue):
            raise ValueError("Invalid new index")
        
        build = self.game.build_queue.pop(old_index)
        self.game.build_queue.insert(new_index, build)
    
    def assign_workers_to_build(self, build_index: int, num_workers: int):
        """Assign specific number of construction workers to a build task.
        Ensures workers are not double-assigned and respects total rented workers.
        Updates turns_remaining when workers change."""
        import math
        if build_index < 0 or build_index >= len(self.game.build_queue):
            raise ValueError("Invalid build index")
        
        build = self.game.build_queue[build_index]
        player = build['player']
        
        # Get total rented construction workers for this player
        total_rented = self.get_player_construction_worker_count(player)
        
        # Calculate how many workers are already assigned to other builds
        total_assigned = 0
        for other_build in self.game.build_queue:
            if other_build['player'] == player and other_build != build:
                # Count assigned workers (treat None as 0)
                assigned = other_build.get('assigned_workers', 0) if other_build.get('assigned_workers') is not None else 0
                total_assigned += assigned
        available_workers = total_rented - total_assigned
        
        # Enforce limit: can only assign up to available workers
        build['assigned_workers'] = max(0, min(num_workers, available_workers))
        
        # Initialize work_remaining if not present
        if 'work_remaining' not in build:
            build['work_remaining'] = self._calculate_base_work(build)
        
        # Recalculate turns_remaining based on new worker count
        assigned_workers = build['assigned_workers']
        if 'work_remaining' not in build:
            build['work_remaining'] = self._calculate_base_work(build)
        
        if assigned_workers > 0:
            # Workers assigned - calculate turns remaining
            build['turns_remaining'] = int(math.ceil(build['work_remaining'] / assigned_workers))
        else:
            # No workers - set to None (N/A)
            build['turns_remaining'] = None
    
    def _calculate_base_work(self, build: dict) -> float:
        """Calculate the base work needed for a build (work with 1 worker = turns with 1 worker)"""
        import math
        if build['type'] == 'line':
            points = build['data']['points']
            total_length, _ = _polyline_metrics(points)
            base_work = _line_build_work_units(total_length)
        elif build['type'] == 'line_extension':
            # Line extension work is based on the length of the extension points
            points = build['data']['points']
            total_length, _ = _polyline_metrics(points)
            # Also add distance from existing line to first extension point
            if len(points) > 0:
                line_name = build['data'].get('line_name')
                if line_name:
                    line = self.game.get_map().get_lines_by_name().get(line_name)
                    if line:
                        existing_route = self.get_line_route(line)
                        if existing_route:
                            import math
                            extend_from_end = build['data'].get('extend_from_end', True)
                            if extend_from_end:
                                last_x, last_y = _normalize_route_point(existing_route[-1])
                                first_x, first_y = _normalize_route_point(points[0])
                                total_length += math.hypot(first_x - last_x, first_y - last_y)
                            else:
                                first_x, first_y = _normalize_route_point(existing_route[0])
                                last_new_x, last_new_y = _normalize_route_point(points[-1])
                                total_length += math.hypot(first_x - last_new_x, first_y - last_new_y)
            base_work = _line_build_work_units(total_length)
        elif build['type'] == 'station':
            num_lines = len(build['data'].get('line_names', []))
            base_work = max(1.0, float(num_lines))
        else:
            base_work = 2.0
        return base_work
    
    def add_to_build_queue(self, build_type: str, build_data: dict, player: Player, construction_workers: int):
        """Add a build to the queue. All game logic in ui.py.
        Speed: N workers = 1/N time (1 worker = base_work turns, 2 workers = base_work/2 turns)"""
        import math
        # Calculate base work needed (work with 1 worker). Must match _calculate_base_work.
        if build_type == 'line':
            points = build_data['points']
            total_length, _ = _polyline_metrics(points)
            base_work = _line_build_work_units(total_length)
        elif build_type == 'line_extension':
            points = build_data['points']
            total_length, _ = _polyline_metrics(points)
            if len(points) > 0:
                line_name = build_data.get('line_name')
                if line_name:
                    line = self.game.get_map().get_lines_by_name().get(line_name)
                    if line:
                        existing_route = self.get_line_route(line)
                        if existing_route:
                            extend_from_end = build_data.get('extend_from_end', True)
                            if extend_from_end:
                                last_x, last_y = _normalize_route_point(existing_route[-1])
                                first_x, first_y = _normalize_route_point(points[0])
                                total_length += math.hypot(first_x - last_x, first_y - last_y)
                            else:
                                first_x, first_y = _normalize_route_point(existing_route[0])
                                last_new_x, last_new_y = _normalize_route_point(points[-1])
                                total_length += math.hypot(first_x - last_new_x, first_y - last_new_y)
            base_work = _line_build_work_units(total_length)
        elif build_type == 'station':
            # Stations always take 1 turn with 1 worker
            base_work = 1.0
        elif build_type == 'line_upgrade':
            # Line upgrades take time based on line distance
            line_name = build_data.get('line_name')
            if line_name:
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line:
                    route = self.get_line_route(line)
                    total_length = 0.0
                    if len(route) > 1:
                        for i in range(len(route) - 1):
                            x1, y1 = route[i]
                            x2, y2 = route[i+1]
                            total_length += math.hypot(x2 - x1, y2 - y1)
                    base_work = max(1.0, float(int(total_length) + 1))
                else:
                    base_work = 5.0  # Default if line not found
            else:
                base_work = 5.0  # Default if no line name
        else:
            base_work = 2.0
        
        # Initialize work_remaining (starts at base_work)
        work_remaining = base_work
        
        # Start with 0 assigned workers - will be auto-assigned after adding to queue
        # Calculate initial turns_remaining assuming 0 workers (will be recalculated after auto-assign)
        initial_turns = int(math.ceil(work_remaining))
        
        # Create build entry - initially no workers assigned (will be auto-assigned)
        build_entry = {
            'type': build_type,
            'data': build_data,
            'player': player,
            'work_remaining': work_remaining,
            'turns_remaining': initial_turns,
            'assigned_workers': 0  # Start with 0, will be auto-assigned if workers available
        }
        self.game.build_queue.append(build_entry)
        
        # Auto-assign unassigned workers to the top job for this player
        # This will assign workers to the first job in the queue for this player
        self.auto_assign_unassigned_workers(player)
    
    def auto_assign_unassigned_workers(self, player: Player):
        """Automatically assign unassigned construction workers across this player's build queue.
        Spreads workers across different segments (at least one per job when possible)."""
        import math
        total_rented = self.get_player_construction_worker_count(player)
        
        if total_rented == 0:
            return
        
        total_assigned = 0
        player_builds = []
        for build in self.game.build_queue:
            if build['player'] == player:
                assigned = build.get('assigned_workers', 0) if build.get('assigned_workers') is not None else 0
                total_assigned += assigned
                if 'work_remaining' not in build:
                    build['work_remaining'] = self._calculate_base_work(build)
                player_builds.append((build, assigned, build.get('work_remaining', 0)))
        
        unassigned = total_rented - total_assigned
        if unassigned <= 0 or not player_builds:
            return
        
        # Distribute unassigned workers across this player's builds (different segments get different workers)
        # Strategy: give each build at least 1 worker if we have enough, then distribute rest by work_remaining
        n_builds = len(player_builds)
        # First pass: ensure each build has at least 1 worker if we have enough
        for i, (build, current_assigned, work_remaining) in enumerate(player_builds):
            if current_assigned >= 1 or unassigned <= 0:
                continue
            build['assigned_workers'] = current_assigned + 1
            unassigned -= 1
        
        # Second pass: distribute remaining unassigned by work_remaining proportion
        if unassigned > 0:
            total_work = sum(w for (_, _, w) in player_builds)
            if total_work > 0:
                for build, current_assigned, work_remaining in player_builds:
                    if unassigned <= 0:
                        break
                    # Proportional share (at least 0, round so we don't over-assign)
                    share = (work_remaining / total_work) * unassigned
                    add = min(unassigned, max(0, int(round(share))))
                    if add > 0:
                        build['assigned_workers'] = build.get('assigned_workers', current_assigned) + add
                        unassigned -= add
            # Any leftover: assign one each to builds with most work_remaining until we run out
            if unassigned > 0:
                by_work = sorted(player_builds, key=lambda x: -x[2])
                for (build, _, _) in by_work:
                    if unassigned <= 0:
                        break
                    build['assigned_workers'] = build.get('assigned_workers', 0) + 1
                    unassigned -= 1
        
        # Recompute turns_remaining for each of this player's builds
        for build in self.game.build_queue:
            if build['player'] != player:
                continue
            nw = build.get('assigned_workers', 0) if build.get('assigned_workers') is not None else 0
            wr = build.get('work_remaining', self._calculate_base_work(build))
            build['turns_remaining'] = int(math.ceil(wr / nw)) if nw > 0 else int(math.ceil(wr))
    
    def get_build_assigned_workers(self, build_index: int) -> int:
        """Get number of workers assigned to a build"""
        if build_index < 0 or build_index >= len(self.game.build_queue):
            return 0
        build = self.game.build_queue[build_index]
        return build.get('assigned_workers', 0)
    
    def get_completed_jobs(self) -> list:
        """Get the list of completed jobs"""
        return self.completed_jobs
    
    def calculate_walking_distance(self, start: tuple, end: tuple) -> float:
        """Calculate walking distance between two points (Euclidean distance)"""
        x1, y1 = start
        x2, y2 = end
        return math.hypot(x2 - x1, y2 - y1)
    
    def calculate_walking_time(self, distance: float, walking_speed_mph: float = 3.0) -> float:
        """Calculate walking time in hours. Default walking speed is 3 mph."""
        return distance / walking_speed_mph
    
    def find_stations_near_point(self, point: tuple, max_distance: float = 5.0, cache: dict = None) -> list:
        """Find all stations within max_distance of a point. Returns list of (station, line, distance) tuples.
        If cache dict is provided, results are cached by point for performance (used during ridership calc)."""
        if cache is not None and point in cache:
            return cache[point]
        stations = []
        game_map = self.game.get_map()
        
        # Get all lines
        all_lines = game_map.get_lines_by_name()
        for line_name, line in all_lines.items():
            for station in line.get_stations():
                station_pos = (station.get_x(), station.get_y())
                distance = self.calculate_walking_distance(point, station_pos)
                if distance <= max_distance:
                    stations.append((station, line, distance))
        
        if cache is not None:
            cache[point] = stations
        return stations
    
    def calculate_train_route_time(self, start_station, end_station, line, trainset) -> float:
        """Calculate time to travel from start_station to end_station on a line using a trainset.
        Returns time in hours, or None if route is not possible."""
        if not trainset or not hasattr(trainset, 'locomotive') or not trainset.locomotive:
            return None
        
        # Get stations on the line in order
        line_stations = line.get_stations()
        if len(line_stations) < 2:
            return None
        
        # Find positions of start and end stations in the line's route
        start_idx = None
        end_idx = None
        
        for i, station in enumerate(line_stations):
            if station is start_station or (station.get_x() == start_station.get_x() and station.get_y() == start_station.get_y()):
                start_idx = i
            if station is end_station or (station.get_x() == end_station.get_x() and station.get_y() == end_station.get_y()):
                end_idx = i
        
        if start_idx is None or end_idx is None:
            return None
        
        if start_idx == end_idx:
            return 0.0
        
        # Check if route goes through incomplete segments
        # Get incomplete segment points from build queue for this line
        incomplete_segment_points = set()
        line_name = line.name
        for build in self.game.build_queue:
            if build['type'] == 'line' and build['data'].get('name') == line_name:
                if 'segment_index' in build['data']:
                    # This is an incomplete segment
                    segment_points = build['data'].get('points', [])
                    for point in segment_points:
                        incomplete_segment_points.add(point)
        
        # Get the route between stations
        route = line.get_route()
        start_station_pos = (start_station.get_x(), start_station.get_y())
        end_station_pos = (end_station.get_x(), end_station.get_y())
        
        # Find the route segment between start and end stations
        start_route_idx = None
        end_route_idx = None
        for i, point in enumerate(route):
            if point == start_station_pos:
                start_route_idx = i
            if point == end_station_pos:
                end_route_idx = i
        
        # Check if any point in the route segment between stations is in incomplete segments
        if start_route_idx is not None and end_route_idx is not None:
            min_idx = min(start_route_idx, end_route_idx)
            max_idx = max(start_route_idx, end_route_idx)
            for i in range(min_idx, max_idx + 1):
                if i < len(route) and route[i] in incomplete_segment_points:
                    # Route goes through incomplete segment - trip not possible
                    return None
        
        # Calculate distance along route between the two stations
        # For simplicity, we'll use the line's traversal time and scale by station positions
        current_year = self.game.get_year()
        if isinstance(line, Bus):
            # Buses need time_period for traffic calculation
            # Use off_peak as default for route time calculation
            line.calc_traversal_time(avg_speed_mph=trainset.locomotive.average_speed, current_year=current_year, time_period='off_peak')
        else:
            line.calc_traversal_time(avg_speed_mph=trainset.locomotive.average_speed)
        full_line_time = line.traversal_time_hours
        
        # Estimate time based on number of stations between start and end
        num_stations_between = abs(end_idx - start_idx)
        total_stations = len(line_stations)
        if total_stations > 1:
            estimated_time = (num_stations_between / (total_stations - 1)) * full_line_time
        else:
            estimated_time = full_line_time
        
        return estimated_time
    
    def find_fastest_route(self, origin: tuple, destination: tuple) -> dict:
        """Find the best route from origin to destination based on time and cost.
        Returns dict with 'method' ('walk', 'train', 'car', 'train_transfer'), 'time' (hours), 
        'cost' (fare), 'line' (if train), 'transfers' (list of transfer stations if multi-line), etc."""
        current_year = self.game.get_year()
        
        walking_distance = self.calculate_walking_distance(origin, destination)
        walking_time = self.calculate_walking_time(walking_distance)
        
        TIME_WEIGHT = 0.7
        COST_WEIGHT = 0.3
        COST_PER_HOUR_VALUE = 5.0
        
        def calculate_route_score(time, cost):
            return (time * TIME_WEIGHT) + (cost / COST_PER_HOUR_VALUE * COST_WEIGHT)
        
        best_route = {
            'method': 'walk',
            'time': walking_time,
            'cost': 0,
            'score': calculate_route_score(walking_time, 0),
            'line': None,
            'lines': [],
            'start_station': None,
            'end_station': None,
            'trainset': None,
            'transfers': []
        }
        
        if current_year >= 1900:
            driving_time = self.calculate_driving_time(walking_distance)
            driving_cost = 10
            driving_score = calculate_route_score(driving_time, driving_cost)
            if driving_score < best_route['score']:
                best_route = {
                    'method': 'car',
                    'time': driving_time,
                    'cost': driving_cost,
                    'score': driving_score,
                    'line': None,
                    'lines': [],
                    'start_station': None,
                    'end_station': None,
                    'trainset': None,
                    'transfers': []
                }
        
        stations_cache = getattr(self, '_route_stations_cache', None)
        origin_stations = self.find_stations_near_point(origin, cache=stations_cache)
        dest_stations = self.find_stations_near_point(destination, cache=stations_cache)
        
        if not origin_stations or not dest_stations:
            return best_route
        
        game_map = self.game.get_map()
        all_lines = game_map.get_lines_by_name()
        
        for origin_station, origin_line, origin_walk_dist in origin_stations:
            for dest_station, dest_line, dest_walk_dist in dest_stations:
                if origin_line is dest_line:
                    line = origin_line
                    fastest_trainset = None
                    fastest_time = None
                    
                    for trainset in line.get_vehicles():
                        if not trainset or not hasattr(trainset, 'locomotive'):
                            continue
                        route_time = self.calculate_train_route_time(origin_station, dest_station, line, trainset)
                        if route_time is not None:
                            if fastest_time is None or route_time < fastest_time:
                                fastest_time = route_time
                                fastest_trainset = trainset
                    
                    if fastest_time is not None:
                        total_time = (self.calculate_walking_time(origin_walk_dist) + 
                                     fastest_time + 
                                     self.calculate_walking_time(dest_walk_dist))
                        total_cost = line.fare if hasattr(line, 'fare') else 10
                        route_score = calculate_route_score(total_time, total_cost)
                        
                        if route_score < best_route['score']:
                            best_route = {
                                'method': 'train',
                                'time': total_time,
                                'cost': total_cost,
                                'score': route_score,
                                'line': line,
                                'lines': [line],
                                'start_station': origin_station,
                                'end_station': dest_station,
                                'trainset': fastest_trainset,
                                'transfers': []
                            }
                else:
                    transfer_route = self.find_route_with_transfers(origin_station, origin_line, origin_walk_dist,
                                                                    dest_station, dest_line, dest_walk_dist)
                    if transfer_route and transfer_route.get('score', float('inf')) < best_route['score']:
                        best_route = transfer_route
        
        return best_route
    
    def calculate_driving_time(self, distance: float, driving_speed_mph: float = 25.0) -> float:
        """Calculate driving time in hours. Default speed is 25 mph (early cars)."""
        return distance / driving_speed_mph
    
    def find_route_with_transfers(self, origin_station, origin_line, origin_walk_dist,
                                  dest_station, dest_line, dest_walk_dist) -> dict:
        """Find route between stations on different lines, finding transfer points."""
        TRANSFER_PENALTY_HOURS = 0.25
        
        TIME_WEIGHT = 0.7
        COST_WEIGHT = 0.3
        COST_PER_HOUR_VALUE = 5.0
        
        def calculate_route_score(time, cost):
            return (time * TIME_WEIGHT) + (cost / COST_PER_HOUR_VALUE * COST_WEIGHT)
        
        all_lines = self.game.get_map().get_lines_by_name()
        
        best_route = None
        best_score = None
        
        origin_line_stations = origin_line.get_stations()
        dest_line_stations = dest_line.get_stations()
        
        for transfer_station in origin_line_stations:
            transfer_pos = (transfer_station.get_x(), transfer_station.get_y())
            
            dest_line_station_at_transfer = None
            for ds in dest_line_stations:
                if ds.get_x() == transfer_pos[0] and ds.get_y() == transfer_pos[1]:
                    dest_line_station_at_transfer = ds
                    break
            
            if not dest_line_station_at_transfer:
                continue
            
            fastest_trainset1 = None
            fastest_time1 = None
            for trainset in origin_line.get_vehicles():
                if not trainset or not hasattr(trainset, 'locomotive'):
                    continue
                route_time = self.calculate_train_route_time(origin_station, transfer_station, origin_line, trainset)
                if route_time is not None:
                    if fastest_time1 is None or route_time < fastest_time1:
                        fastest_time1 = route_time
                        fastest_trainset1 = trainset
            
            fastest_trainset2 = None
            fastest_time2 = None
            for trainset in dest_line.get_vehicles():
                if not trainset or not hasattr(trainset, 'locomotive'):
                    continue
                route_time = self.calculate_train_route_time(dest_line_station_at_transfer, dest_station, dest_line, trainset)
                if route_time is not None:
                    if fastest_time2 is None or route_time < fastest_time2:
                        fastest_time2 = route_time
                        fastest_trainset2 = trainset
            
            if fastest_time1 is not None and fastest_time2 is not None:
                total_time = (self.calculate_walking_time(origin_walk_dist) +
                             fastest_time1 +
                             TRANSFER_PENALTY_HOURS +
                             fastest_time2 +
                             self.calculate_walking_time(dest_walk_dist))
                
                total_cost = (origin_line.fare if hasattr(origin_line, 'fare') else 10) + \
                            (dest_line.fare if hasattr(dest_line, 'fare') else 10)
                route_score = calculate_route_score(total_time, total_cost)
                
                if best_score is None or route_score < best_score:
                    best_score = route_score
                    best_route = {
                        'method': 'train_transfer',
                        'time': total_time,
                        'cost': total_cost,
                        'score': route_score,
                        'line': origin_line,
                        'lines': [origin_line, dest_line],
                        'start_station': origin_station,
                        'end_station': dest_station,
                        'trainset': fastest_trainset1,
                        'transfers': [transfer_station]
                    }
        
        return best_route
    
    def get_ridership_data_for_year(self, current_year: int) -> dict:
        """Get ridership data for a year, using cache if available"""
        if self._cached_ridership_data is not None and self._cached_ridership_year == current_year:
            return self._cached_ridership_data
        ridership_data = self.calculate_ridership_for_year(current_year)
        self._cached_ridership_data = ridership_data
        self._cached_ridership_year = current_year
        return ridership_data
    
    def invalidate_ridership_cache(self):
        """Invalidate cached ridership data (call when year advances or lines change)"""
        self._cached_ridership_data = None
        self._cached_ridership_year = None
    
    def calculate_ridership_for_year(self, current_year: int) -> dict:
        """Calculate ridership for the year based on new system:
        Each person takes a trip to another point with probability based on population.
        Returns dict mapping line_name -> passenger_count.
        Optimized to complete quickly (< 1 second for typical maps)."""
        import random
        import time
        
        line_passengers = {}  # line_name -> number of passengers
        game_map = self.game.get_map()
        
        # Skip if no stations on map - no routes/profits to calculate
        if not hasattr(game_map, 'stations') or not game_map.stations or len(game_map.stations) == 0:
            return line_passengers
        
        # Get all population points, excluding points with 0 population (water/excluded areas)
        # This ensures excluded points don't contribute to profit calculations
        population_points = [(x, y) for (x, y), pop in game_map.population.items() if pop > 0]
        if len(population_points) < 2:
            return line_passengers
        # Skip ridership sim on huge maps to prevent freeze; profit floor will be used per line
        if len(population_points) > 15000:
            return line_passengers
        
        # Cap population points for ridership sampling to avoid freeze on very large maps (e.g. NYC 50k+)
        MAX_POPULATION_POINTS = 3000
        if len(population_points) > MAX_POPULATION_POINTS:
            import random as _r
            population_points = _r.sample(population_points, MAX_POPULATION_POINTS)
        
        # Calculate total population
        total_population = sum(game_map.population.values())
        if total_population == 0:
            return line_passengers
        
        # Build destination weights lazily (on demand) to avoid O(n^2) freeze on large maps.
        # Precomputing for all origins caused freezes when population_points had 10k+ entries.
        destination_weights_cache = {}
        
        def get_destination_weights(origin):
            if origin not in destination_weights_cache:
                weights = [game_map.population[dest] for dest in population_points if dest != origin]
                destinations = [dest for dest in population_points if dest != origin]
                total_weight = sum(weights)
                destination_weights_cache[origin] = (destinations, weights, total_weight)
            return destination_weights_cache[origin]
        
        # Optimize sample size and enforce time budget to keep End Year responsive
        RIDERSHIP_TIME_BUDGET_SEC = 18.0
        map_size = game_map.width * game_map.height
        if map_size < 2500:  # < 50x50
            sample_size = min(total_population, 1800)
        elif map_size < 10000:  # < 100x100
            sample_size = min(total_population, 960)
        else:  # Large maps (e.g. NYC)
            sample_size = min(total_population, 480)
        
        start_time = time.time()
        self._route_stations_cache = {}
        # Pre-update line quality once per year (avoids per-trip checks in loop)
        for line in (game_map.get_lines_by_name() or {}).values():
            if not hasattr(line, '_quality_updated_year') or line._quality_updated_year != current_year:
                line.update_quality()
                line._quality_updated_year = current_year
        # Precompute cumulative origin weights once (O(n)) to avoid O(n) per trip on large maps
        origin_weights = [game_map.population[p] for p in population_points]
        total_origin_weight = sum(origin_weights)
        if total_origin_weight <= 0:
            return line_passengers
        origin_cumulative = []
        cum = 0
        for w in origin_weights:
            cum += w
            origin_cumulative.append(cum)
        
        for trip_idx in range(sample_size):
            if time.time() - start_time > RIDERSHIP_TIME_BUDGET_SEC:
                sample_size = trip_idx or 1
                break
            if trip_idx > 0 and trip_idx % 360 == 0:
                time.sleep(0.01)  # Yield GIL occasionally so main thread can update timer
            # Randomly select an origin weighted by population (binary search for large n)
            rand_origin = random.random() * total_origin_weight
            lo, hi = 0, len(origin_cumulative) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if rand_origin <= origin_cumulative[mid]:
                    hi = mid
                else:
                    lo = mid + 1
            origin = population_points[lo]
            
            # Get destination weights for this origin (lazy - only for sampled origins)
            destinations, weights, total_weight = get_destination_weights(origin)
            if not destinations or total_weight == 0:
                continue
            
            # Choose destination based on weighted probability
            rand_val = random.random() * total_weight
            cumulative = 0
            destination = destinations[0]
            for i, weight in enumerate(weights):
                cumulative += weight
                if rand_val <= cumulative:
                    destination = destinations[i]
                    break
            
            route = self.find_fastest_route(origin, destination)
            
            if route['method'] in ['train', 'train_transfer'] and route.get('lines'):
                for line in route['lines']:
                    line_name = line.name
                    quality_multiplier = line.quality
                    if random.random() < (0.5 + (quality_multiplier - 1.0) * 0.15):
                        line_passengers[line_name] = line_passengers.get(line_name, 0) + 1
        
        # Scale up results if we sampled
        if sample_size < total_population:
            scale_factor = total_population / sample_size
            line_passengers = {k: int(v * scale_factor) for k, v in line_passengers.items()}
        
        self._route_stations_cache = None
        return line_passengers
    
    def calculate_line_profit_with_ridership(self, line: Line, current_year: int, ridership_data: dict) -> int:
        """Calculate profit for a line based on ridership data.
        This replaces the old calc_profit method with the new trip-based system.
        Lines with incomplete segments do not generate profit."""
        total_profit = 0
        
        # Check if line has incomplete segments in build queue
        # Only block profit when the line does not exist on the map yet (initial build still in progress).
        # Once the line exists on the map (including after first segment), allow profit if it has 2+ stations.
        line_name = line.name
        has_incomplete_segments = False
        for build in self.game.build_queue:
            if build['type'] == 'line' and build['data'].get('name') == line_name:
                existing_line = self.game.get_map().get_lines_by_name().get(line_name)
                if existing_line is None:
                    has_incomplete_segments = True
                break
        
        if has_incomplete_segments:
            line.profit = 0
            return 0
        
        if len(line.stations) == 0:
            line.profit = 0
            return 0
        
        # Check if engineers are on strike
        try:
            if hasattr(line, 'owner') and line.owner:
                from worker import ENGINEER
                engineer_rental = line.owner.get_worker_rental(ENGINEER.name)
                if engineer_rental and engineer_rental.is_on_strike():
                    line.profit = 0
                    return 0
        except (AttributeError, ImportError):
            pass
        
        # Get number of passengers for this line
        passengers_per_year = ridership_data.get(line.name, 0)
        
        if not line.vehicles or len(line.vehicles) == 0:
            # No trainset assigned yet: give a small profit floor so players/AIs get income the first year after building
            stations_list = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
            if len(stations_list) >= 2 and not has_incomplete_segments:
                floor = self.inflate_economic_value(400, max_factor=5.5)
                line.profit = max(0, floor)
                return line.profit
            line.profit = 0
            return 0
        
        from inflation_utils import get_nominal_fare_cap

        sy = self.get_timeline_start_year()
        ey = self.get_timeline_end_year()
        _, max_acceptable_fare = get_nominal_fare_cap(
            int(current_year), start_year=sy, end_year=ey,
        )
        effective_fare = min(line.fare, max_acceptable_fare)
        
        # Calculate total capacity across all trainsets
        total_capacity_per_trip = 0
        for vehicle in line.vehicles:
            if vehicle and hasattr(vehicle, 'get_capacity'):
                total_capacity_per_trip += vehicle.get_capacity()
        
        if total_capacity_per_trip == 0:
            line.profit = 0
            return 0
        # When ridership sim returns 0 (sparse population, sampling), use minimum floor for completed lines
        if passengers_per_year == 0:
            stations_list = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
            if len(stations_list) >= 2:
                passengers_per_year = max(
                    self.inflate_economic_value(200, max_factor=5.5),
                    int(total_capacity_per_trip * 0.25),
                )
        
        # Calculate how many trips can be made per year
        # Use the fastest trainset to determine service frequency
        fastest_trainset = None
        fastest_speed = 0
        for vehicle in line.vehicles:
            if vehicle and hasattr(vehicle, 'locomotive') and vehicle.locomotive:
                if vehicle.locomotive.average_speed > fastest_speed:
                    fastest_speed = vehicle.locomotive.average_speed
                    fastest_trainset = vehicle
        
        if not fastest_trainset:
            line.profit = 0
            return 0
        
        service_freq = line.service_frequency_per_year(trainset_avg_speed=fastest_speed)
        if service_freq == 0:
            line.profit = 0
            return 0
        
        max_passengers_served = service_freq * total_capacity_per_trip
        
        actual_passengers = min(passengers_per_year, max_passengers_served)
        
        line.update_quality()
        quality_multiplier = 1.0 + ((line.quality - 1.0) * 0.2)
        profit_multiplier = max(0.25, min(10.0, getattr(self.game, 'profit_margin', 1.0)))
        total_profit = actual_passengers * effective_fare * profit_multiplier * quality_multiplier
        
        # Apply event effects
        event_profit_multiplier = 1.0
        natural_disaster_reduction = 0.0
        try:
            if hasattr(line, 'owner') and line.owner:
                game = line.owner.__dict__.get('_game', None)
                if game and hasattr(game, 'event_system'):
                    event_effects = game.event_system.get_combined_effects(current_year)
                    event_profit_multiplier = event_effects.get('profit_multiplier', 1.0)
                    natural_disaster_reduction = 0.0
                    for event in game.event_system.get_active_events(current_year):
                        if hasattr(event, 'disaster_type'):
                            years_since = current_year - event.year
                            recovery_years = event.get_recovery_years() if hasattr(event, 'get_recovery_years') else 2
                            if 0 <= years_since < recovery_years:
                                effect = event.get_effect(current_year)
                                if effect and 'profit_reduction' in effect:
                                    natural_disaster_reduction = max(natural_disaster_reduction, effect['profit_reduction'])
        except:
            pass
        
        # Apply natural disaster profit reduction
        if natural_disaster_reduction > 0.0:
            total_profit = total_profit * (1.0 - natural_disaster_reduction)
        
        total_profit = total_profit * event_profit_multiplier
        
        line.profit = int(total_profit)
        return line.profit
    
    def apply_natural_disaster_damage(self, disaster_event, current_year: int):
        """Apply natural disaster damage to infrastructure (lines and stations)
        This method contains all game logic for disaster damage."""
        import random
        
        # Get disaster effect
        effect = disaster_event.get_effect(current_year)
        if not effect or not effect.get('natural_disaster'):
            return
        
        damage_percent = effect.get('infrastructure_damage', 0.3)
        disaster_type = effect.get('disaster_type', 'Unknown')
        
        # Track damaged infrastructure
        damaged_lines = []
        damaged_stations = []
        
        # Damage lines for all players
        all_lines = self.get_map_lines()
        all_line_list = list(all_lines.values())
        
        if all_line_list:
            # Calculate number of lines to damage
            num_lines_to_damage = max(1, int(len(all_line_list) * damage_percent))
            # Randomly select lines to damage
            lines_to_damage = random.sample(all_line_list, min(num_lines_to_damage, len(all_line_list)))
            
            for line in lines_to_damage:
                # Damage line by adding damaged segments back to build queue
                route = self.get_line_route(line)
                if not route or len(route) < 2:
                    continue
                
                owner = self.get_line_owner(line)
                if not owner:
                    continue
                
                # Determine how many segments to damage (20-60% of route)
                num_segments = len(route) - 1
                segments_to_damage = max(1, int(num_segments * random.uniform(0.2, 0.6)))
                
                # Randomly select which segments to damage
                segment_indices = random.sample(range(num_segments), min(segments_to_damage, num_segments))
                
                # Add damaged segments to build queue
                from line import Bus, Ferry
                is_bus = isinstance(line, Bus)
                is_ferry = isinstance(line, Ferry)
                line_elev = getattr(line, 'elevation_levels', None)
                game_map = self.game.get_map()
                preset_name = getattr(game_map, 'map_preset_name', None)
                for segment_idx in segment_indices:
                    segment_points = [route[segment_idx], route[segment_idx + 1]]
                    seg_elev = [line_elev[segment_idx]] if line_elev and segment_idx < len(line_elev) else (['street'] if is_bus else (['water'] if is_ferry else ['above_ground']))
                    # Use above_ground for segments in water when street would fail (prevents ValueError)
                    if seg_elev == ['street'] and self._segment_in_water_for_disaster(segment_points[0], segment_points[1], game_map, preset_name):
                        seg_elev = ['above_ground']
                    # Calculate repair cost (50% of original construction cost)
                    segment_cost_info = self.calculate_line_cost(
                        segment_points,
                        elevation_levels=seg_elev,
                        is_bus_line=is_bus,
                        is_ferry_line=is_ferry
                    )
                    repair_cost = int(segment_cost_info['cost'] * 0.5)
                    
                    # Check if player can afford repair, if not they'll need to pay later
                    if self.get_player_money(owner) >= repair_cost:
                        self.deduct_player_money(owner, repair_cost, reason='Disaster repair')
                    else:
                        repair_cost = 0
                    
                    # Add segment to build queue as repair job
                    segment_data = {
                        'name': line.name,
                        'points': segment_points,
                        'segment_index': segment_idx,
                        'total_segments': num_segments,
                        'disaster_repair': True,
                        'disaster_type': disaster_type,
                        'repair_cost': repair_cost,
                        'elevation_levels': seg_elev,
                        'is_bus_line': is_bus,
                        'is_ferry_line': is_ferry,
                    }
                    
                    # Get construction workers for repair
                    construction_workers = self.get_player_construction_worker_count(owner)
                    if construction_workers < 1:
                        construction_workers = 0
                    
                    self.add_to_build_queue('line', segment_data, owner, construction_workers)
                
                if not hasattr(line, '_disaster_damaged'):
                    line._disaster_damaged = True
                    line._disaster_damage_year = current_year
                    line._disaster_type = disaster_type
                    damaged_lines.append(line.name)
        
        # Damage stations
        game_map = self.game.get_map()
        if hasattr(game_map, 'stations') and game_map.stations:
            all_stations = game_map.stations
            num_stations_to_damage = max(1, int(len(all_stations) * damage_percent))
            stations_to_damage = random.sample(all_stations, min(num_stations_to_damage, len(all_stations)))
            
            for station in stations_to_damage:
                if not hasattr(station, '_disaster_damaged'):
                    station._disaster_damaged = True
                    station._disaster_damage_year = current_year
                    station._disaster_type = disaster_type
                    damaged_stations.append(station.name if hasattr(station, 'name') else 'Unnamed Station')
        
        # Store damage info for display
        if not hasattr(self, '_disaster_damage_history'):
            self._disaster_damage_history = []
        
        self._disaster_damage_history.append({
            'year': current_year,
            'disaster_type': disaster_type,
            'damaged_lines': damaged_lines,
            'damaged_stations': damaged_stations,
            'recovery_years': disaster_event.get_recovery_years()
        })
    
    def get_disaster_damage_history(self) -> list:
        """Get history of natural disaster damage"""
        if hasattr(self, '_disaster_damage_history'):
            return self._disaster_damage_history
        return []
    
    def format_natural_disaster_info(self, disaster_event, current_year: int) -> dict:
        """Format natural disaster information for UI display.
        This method contains all game logic for formatting disaster information."""
        disaster_effect = disaster_event.get_effect(current_year)
        if not disaster_effect or not disaster_effect.get('natural_disaster'):
            return None
        
        damage_percent = disaster_effect.get('infrastructure_damage', 0.3) * 100
        profit_reduction = disaster_effect.get('profit_reduction', 0.5) * 100
        recovery_years = disaster_event.get_recovery_years()
        
        # Get damage details from history
        damaged_lines = []
        damaged_stations = []
        damage_count = 0
        
        if hasattr(self, '_disaster_damage_history') and self._disaster_damage_history:
            latest_damage = self._disaster_damage_history[-1]
            if latest_damage['year'] == current_year:
                damaged_lines = latest_damage.get('damaged_lines', [])
                damaged_stations = latest_damage.get('damaged_stations', [])
                damage_count = len(damaged_lines) + len(damaged_stations)
        
        return {
            'name': disaster_event.name,
            'description': disaster_event.description,
            'disaster_type': disaster_effect.get('disaster_type', 'Unknown'),
            'damage_percent': damage_percent,
            'profit_reduction': profit_reduction,
            'recovery_years': recovery_years,
            'damaged_lines': damaged_lines,
            'damaged_stations': damaged_stations,
            'damage_count': damage_count
        }
    
    def get_public_ta_config(self) -> dict:
        game_map = self.get_map()
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name:
            from map_preset_loader import get_preset_public_ta_config
            config = get_preset_public_ta_config(preset_name)
            if config:
                return config
        from map_presets_standard import PUBLIC_TA_CONFIG
        return PUBLIC_TA_CONFIG
    
    def get_public_ta(self) -> PublicTA:
        return self.public_ta
    
    def establish_public_ta(self, current_year: int):
        if self.public_ta is not None:
            return
        
        config = self.get_public_ta_config()
        establishment_year = config.get('establishment_year', 1930)
        
        if current_year >= establishment_year:
            max_player_id = max([p.player_id for p in self.get_players()]) if self.get_players() else -1
            starting_money = config.get('starting_money', 100000)
            
            from vehicle_presets import get_vehicle_registry
            from vehicle import Trainset, Railcar
            registry = get_vehicle_registry()
            
            # Get earliest locomotive that CAN have railcars (exclude self-contained units like ferries, streetcars, etc.)
            all_locomotives = registry.get_locomotives_by_year()
            earliest_loco = None
            for loco, year in all_locomotives:
                if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
                    earliest_loco = loco
                    break
            
            earliest_railcar = registry.get_earliest_railcar()
            
            starting_trainsets = []
            if earliest_loco and earliest_railcar:
                for _ in range(5):
                    cars = [Railcar("Standard Passenger", 0, earliest_railcar.capacity, earliest_railcar.maitnance_costs)]
                    starting_trainset = Trainset(locomotive=earliest_loco, cars=cars)
                    starting_trainsets.append(starting_trainset)
            
            self.public_ta = PublicTA(player_id=max_player_id + 1, money=starting_money, trainsets=starting_trainsets, name="Public Transit Authority")
            self.public_ta.established = True
            self.game.players.append(self.public_ta)
            self.game.players_by_name[self.public_ta.name] = self.public_ta
    
    def handle_public_ta(self, current_year: int):
        self.establish_public_ta(current_year)
        
        if self.public_ta is None or not self.public_ta.established:
            return
        
        config = self.get_public_ta_config()
        annual_funding = config.get('annual_funding', 50000)
        self.public_ta.add_money(annual_funding)
        
        self.public_ta_build_lines(current_year)
        self.public_ta_buy_lines_from_marketplace()
    
    def public_ta_build_lines(self, current_year: int):
        if self.public_ta is None or not self.public_ta.established:
            return
        
        if self.public_ta.get_money() < 10000:
            return
        
        game_map = self.get_map()
        map_width = game_map.width
        map_height = game_map.height
        
        import random
        if random.random() < 0.3:
            num_points = random.randint(3, 6)
            start_x = random.randint(5, max(5, map_width - 10))
            start_y = random.randint(5, max(5, map_height - 10))
            dx, dy = random.choice([(1, 0), (0, 1), (1, 1), (-1, 0), (0, -1), (-1, 1)])
            step = random.randint(2, 5)
            points = [(start_x, start_y)]
            x, y = float(start_x), float(start_y)
            for _ in range(num_points - 1):
                x = max(0, min(map_width, x + dx * step))
                y = max(0, min(map_height, y + dy * step))
                points.append((int(x), int(y)))

            if len(points) >= 2:
                base = f"Public Line {current_year}"
                line_name = base
                counter = 1
                while self.is_line_name_in_use(line_name):
                    line_name = f"{base}-{counter}"
                    counter += 1

                elevation_levels = ['street'] * max(0, len(points) - 1)
                cost_info = self.calculate_line_cost(points, elevation_levels=elevation_levels)
                if cost_info and cost_info.get('cost', 0) <= self.public_ta.get_money():
                    try:
                        self.create_line_complete(
                            line_name, points, self.public_ta, elevation_levels=elevation_levels
                        )
                    except ValueError:
                        pass
    
    def public_ta_buy_lines_from_marketplace(self):
        if self.public_ta is None or not self.public_ta.established:
            return
        
        marketplace = self.game.get_marketplace()
        available_lines = self.marketplace_get_available_lines()
        current_year = self.get_year()

        def _seller_priority(sale_item) -> int:
            seller = sale_item.get_seller() if hasattr(sale_item, 'get_seller') else ''
            if seller and seller not in ('Unowned', 'Manufacturer'):
                return 0
            return 1

        sorted_lines = sorted(
            (si for si in available_lines if isinstance(si, SaleItem)),
            key=lambda si: (_seller_priority(si), si.get_price(current_year=current_year)),
        )

        for sale_item in sorted_lines:
            line = sale_item.get_item()
            if not isinstance(line, Line):
                continue
            
            price = sale_item.get_price(current_year=current_year)
            if price > self.public_ta.get_money():
                continue
                self.public_ta.deduct_money(price)
                line.owner = self.public_ta
                self.public_ta.acquire_line(line)
            self.add_line_to_map(line)
            self.invalidate_ridership_cache()
                
            is_unowned = sale_item.get_seller() == "Unowned"
            if not is_unowned:
                seller = sale_item.get_salesman()
                if seller:
                    self.add_player_money(seller, price, reason='Public TA line sale')
                marketplace.remove_line(line)
    
    def get_stations_for_map_display(self, game_map, current_player=None) -> list[dict]:
        """Stations for OSM map overlay: completed map stations, line-attached stops, and queue jobs."""
        import math
        from map_preset_loader import get_map_bounds, get_default_center_lat, get_default_center_lon

        map_width_val = game_map.width if getattr(game_map, 'width', None) else 50
        map_height_val = game_map.height if getattr(game_map, 'height', None) else 50
        grid_size = max(map_width_val, map_height_val)
        if grid_size <= 0:
            return []

        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        center_lat = (
            game_map.map_center_lat
            if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None
            else get_default_center_lat(preset_name)
        )
        center_lon = (
            game_map.map_center_lon
            if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None
            else get_default_center_lon(preset_name)
        )
        use_preset_bounds = preset_bounds and all(
            k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')
        )
        if use_preset_bounds:
            min_lat, max_lat = preset_bounds['min_lat'], preset_bounds['max_lat']
            min_lon, max_lon = preset_bounds['min_lon'], preset_bounds['max_lon']
            preset_lat_scale = (max_lat - min_lat) / map_height_val if map_height_val > 0 else 0
            preset_lon_scale = (max_lon - min_lon) / map_width_val if map_width_val > 0 else 0
        else:
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat)) or 111.0
            min_lat, max_lat = center_lat - 0.5, center_lat + 0.5
            min_lon, max_lon = center_lon - 0.5, center_lon + 0.5

        def grid_to_lat_lon(x: float, y: float):
            if use_preset_bounds:
                lat = preset_bounds['min_lat'] + y * preset_lat_scale
                lon = preset_bounds['min_lon'] + x * preset_lon_scale
            else:
                lat_km = y - grid_size // 2
                lon_km = x - grid_size // 2
                lat = center_lat + (lat_km / 111.0)
                lon = center_lon + (lon_km / lon_deg_per_km)
            return lat, lon

        def can_close_station(line_names: list) -> bool:
            if not line_names or current_player is None:
                return True
            for ln in line_names:
                line = self.get_map_lines().get(ln)
                if line and self.get_line_owner(line) != current_player:
                    return False
            return True

        seen_keys = set()
        stations_data = []

        def append_station(x, y, name, under_construction, line_names):
            key = (round(float(x), 3), round(float(y), 3))
            if key in seen_keys:
                return
            lat, lon = grid_to_lat_lon(float(x), float(y))
            if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                return
            seen_keys.add(key)
            line_names = list(line_names or [])
            stations_data.append({
                'lat': lat,
                'lon': lon,
                'name': name or 'Station',
                'under_construction': bool(under_construction),
                'can_close': can_close_station(line_names),
                'line_names': line_names,
            })

        for build in self.game.build_queue or []:
            if build.get('type') != 'station':
                continue
            build_data = build.get('data') or {}
            x, y = build_data.get('x'), build_data.get('y')
            if x is None or y is None:
                continue
            append_station(
                x, y,
                build_data.get('name', 'Station'),
                True,
                build_data.get('line_names', []),
            )

        for station in getattr(game_map, 'stations', []) or []:
            x = station.get_x() if hasattr(station, 'get_x') else getattr(station, 'x', None)
            y = station.get_y() if hasattr(station, 'get_y') else getattr(station, 'y', None)
            if x is None or y is None:
                continue
            line_names = (
                station.get_line_names()
                if hasattr(station, 'get_line_names')
                else getattr(station, 'line_names', [])
            )
            name = station.get_name() if hasattr(station, 'get_name') else getattr(station, 'name', 'Station')
            append_station(x, y, name, False, line_names)

        lines = game_map.lines if hasattr(game_map, 'lines') and game_map.lines else {}
        for line in lines.values():
            line_stations = line.get_stations() if hasattr(line, 'get_stations') else getattr(line, 'stations', [])
            for station in line_stations or []:
                x = station.get_x() if hasattr(station, 'get_x') else getattr(station, 'x', None)
                y = station.get_y() if hasattr(station, 'get_y') else getattr(station, 'y', None)
                if x is None or y is None:
                    continue
                line_names = (
                    station.get_line_names()
                    if hasattr(station, 'get_line_names')
                    else getattr(station, 'line_names', [])
                )
                name = station.get_name() if hasattr(station, 'get_name') else getattr(station, 'name', 'Station')
                append_station(x, y, name, False, line_names)

        return stations_data

    def get_population_points_for_display(self, game_map) -> list[dict]:
        """Get all population points that should be displayed on the map.
        Returns a list of dicts with keys: 'grid_x', 'grid_y', 'pop', 'lat', 'lon', and optionally 'included'
        All game logic for determining which points to display is here.
        """
        population_data = []
        
        # Ensure population attribute exists - initialize if missing
        if not hasattr(game_map, 'population'):
            from map_preset_loader import initialize_preset_population
            preset_name = getattr(game_map, 'map_preset_name', None)
            pop_dict = initialize_preset_population(
                preset_name, game_map.width, game_map.height,
                game_map.map_center_lat, game_map.map_center_lon
            ) if preset_name else None
            if pop_dict is not None:
                game_map.population = pop_dict
                deleted = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
                for pt in deleted:
                    game_map.population.pop(pt, None)
            else:
                from map_presets_standard import get_standard_population_at_location
                game_map.population = {}
                for x in range(game_map.width + 1):
                    for y in range(game_map.height + 1):
                        pop = get_standard_population_at_location(x, y, game_map.width, game_map.height)
                        game_map.population[(x, y)] = pop
        
        if not game_map.population:
            return population_data
        
        # Get map dimensions
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width and game_map.width > 0 else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height and game_map.height > 0 else 50
        
        # Preset land cells come from INITIAL_POPULATION in the preset file (source of truth).
        # Uses preset_info (from map_preset_name) so Bay Area, NYC, NY Metro all work without core changes
        preset_info = None
        if hasattr(game_map, 'map_preset_name') and game_map.map_preset_name:
            from map_preset_utils import get_preset_file_info
            preset_info = get_preset_file_info(game_map)
        
        included_points = None
        deleted_points = (
            (getattr(game_map, '_deleted_population_points', set()) or set())
            | (getattr(self, '_session_deleted_population_points', set()) or set())
        )
        # Fast path: use in-memory population from Map init (avoids re-parsing huge preset files).
        if game_map.population:
            included_points = {
                pt for pt, pop in game_map.population.items()
                if pop and int(pop) > 0
            } - deleted_points
        elif preset_info:
            from map_preset_utils import read_included_points_from_file
            file_path, variable_name = preset_info
            for attempt in range(5):
                points_set = read_included_points_from_file(file_path, variable_name)
                if points_set is not None and len(points_set) > 0:
                    included_points = points_set - deleted_points
                    break
                elif points_set is not None and len(points_set) == 0:
                    included_points = set()
                    break
                import time
                time.sleep(0.02 * (attempt + 1))
        
        # Get bounds for coordinate conversion (preset-agnostic via map_preset_loader)
        bounds = None
        if preset_info and game_map.map_preset_name:
            from map_preset_loader import get_map_bounds
            bounds = get_map_bounds(game_map.map_preset_name)
        bounds_has_minmax = bounds and 'min_lat' in bounds and 'max_lat' in bounds
        center_lat = game_map.map_center_lat if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None else (bounds.get('center_lat', 0.0) if bounds else 0.0)
        center_lon = game_map.map_center_lon if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None else (bounds.get('center_lon', 0.0) if bounds else 0.0)
        grid_size = max(map_width, map_height)
        if bounds_has_minmax:
            min_lat = bounds['min_lat']
            max_lat = bounds['max_lat']
            min_lon = bounds['min_lon']
            max_lon = bounds['max_lon']
        else:
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
            if lon_deg_per_km == 0:
                lon_deg_per_km = 111.0
            min_lat = center_lat - (grid_size / 2) / 111.0
            max_lat = center_lat + (grid_size / 2) / 111.0
            min_lon = center_lon - (grid_size / 2) / lon_deg_per_km
            max_lon = center_lon + (grid_size / 2) / lon_deg_per_km
        lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
        if lon_deg_per_km == 0:
            lon_deg_per_km = 111.0
        lat_scale = (max_lat - min_lat) / map_height if map_height > 0 else 0
        lon_scale = (max_lon - min_lon) / map_width if map_width > 0 else 0
        
        # Process population points
        # Points explicitly removed in edit mode - never show these (map + session-level for persistence)
        deleted_points = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
        
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name and bounds_has_minmax:
            from map_preset_loader import convert_preset_grid_to_latlon
            _grid_latlon = lambda gx, gy: convert_preset_grid_to_latlon(
                preset_name, gx, gy,
                {'min_lat': min_lat, 'max_lat': max_lat, 'min_lon': min_lon, 'max_lon': max_lon},
                map_width, map_height,
            )
        else:
            _grid_latlon = lambda gx, gy: (min_lat + gy * lat_scale, min_lon + gx * lon_scale)
        
        # Preset maps: land cells = INITIAL_POPULATION keys (population > 0).
        if preset_info and included_points is not None:
            for (grid_x, grid_y) in included_points:
                if (grid_x, grid_y) in deleted_points:
                    continue
                pop = game_map.population.get((grid_x, grid_y), 0)
                if pop <= 0:
                    continue
                lat, lon = _grid_latlon(grid_x, grid_y)
                population_data.append({
                    'grid_x': grid_x,
                    'grid_y': grid_y,
                    'pop': pop,
                    'lat': lat,
                    'lon': lon
                })
        else:
            # Standard maps or fallback: iterate over population
            points_processed = 0
            points_filtered_out = 0
            for (x, y), pop in game_map.population.items():
                points_processed += 1
                grid_x = int(round(x))
                grid_y = int(round(y))
                if (grid_x, grid_y) in deleted_points:
                    points_filtered_out += 1
                    continue
                if pop <= 0:
                    points_filtered_out += 1
                    continue
                lat = min_lat + grid_y * lat_scale
                lon = min_lon + grid_x * lon_scale
                population_data.append({
                    'grid_x': grid_x,
                    'grid_y': grid_y,
                    'pop': pop,
                    'lat': lat,
                    'lon': lon
                })
        
        # Final safety filter: never return deleted points (catches any edge case)
        deleted_points = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
        population_data = [p for p in population_data if (p['grid_x'], p['grid_y']) not in deleted_points]
        
        # Mark included points for JS (default True when not specified)
        for p in population_data:
            if 'included' not in p:
                p['included'] = True

        return cap_population_points_for_map_display(population_data)