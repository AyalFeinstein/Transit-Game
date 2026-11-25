from typing import Optional
from game import Game
from line import Line, Ferry, Bus as BusLine
from player import Player
from public_ta import PublicTA
from map import Map
from vehicle import Trainset, Locomotive, Railcar, Ferry, Bus
from timeline import Timeline
import math
import random
from marketplace import Marketplace, SaleItem
from game_presets import AI_DIFFICULTIES, AI_FOCUSES, FERRY_LENGTH_COST_PER_UNIT, LOAN_BASE_INTEREST_RATE, LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER, LINE_NIMBY_LAWSUIT_BASE_CHANCE, STARTING_MONEY, STATION_MAX_DISTANCE_FROM_LAND_M, FERRY_MAX_DISTANCE_FROM_WATER_M

# NIMBY line-build excuses (noise / property / environment).
_LINE_NIMBY_EXCUSE_CAUSES = frozenset({
    'Noise Pollution',
    'Property Damage',
    'Environmental Damage',
})

# AI difficulty bonuses: hard=strongest (cost 0.75x, marketplace 0.75x, lobby +0.15), easy=weakest
AI_DIFFICULTY_COST_MULTIPLIER = {'easy': 1.15, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_MARKETPLACE_MULTIPLIER = {'easy': 1.1, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_LOBBY_BONUS = {'easy': -0.03, 'medium': 0.05, 'hard': 0.20}
# Law committee / lobby success tuning
LOBBY_SUCCESS_MIN_CHANCE = 0.20
LOBBY_SUCCESS_REF_COST_MULTIPLIER = 0.75
LAW_COMMITTEE_BASE_CHANCE = 0.40
LAW_COMMITTEE_FAVORABLE_PER_SEAT = 0.03
LAW_COMMITTEE_UNFAVORABLE_PER_SEAT = -0.015
LAW_COMMITTEE_TIEBREAKER_BONUS = 0.20
LAW_COMMITTEE_LAWYER_BONUS_PER = 0.07
LAW_COMMITTEE_LAWYER_BONUS_MAX = 0.30
LAW_TARGET_EXTRA_BONUS_PER = 0.05
LAW_TARGET_EXTRA_PENALTY_PER = 0.05
_BAD_LAW_TYPES = frozenset({
    'gov_attack', 'add_lawsuit_excuse', 'audit_target', 'block_funding', 'increase_property_tax_5',
})
_GOOD_MULTI_TARGET_LAW_TYPES = frozenset({'reduce_property_tax_5'})
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


def _ai_line_doubles_back(points: list, game_map: Map) -> bool:
    """True when a whole line (existing route plus any extension) doubles back on itself."""
    if not points or len(points) < 3:
        return False
    seen_edges: set = set()
    for i in range(len(points) - 1):
        seg_key = _normalize_line_route_geometry_key([points[i], points[i + 1]])
        if len(seg_key) != 2:
            continue
        if seg_key in seen_edges or (seg_key[1], seg_key[0]) in seen_edges:
            return True
        seen_edges.add(seg_key)
    if _ai_land_straight_limit_applies(game_map):
        from ai_street_router import grid_metric_scale, route_doubles_back
        mx, my = grid_metric_scale(game_map)
        return route_doubles_back(points, mx, my)
    from map_preset_utils import ai_grid_polyline_overlap_tolerance, polyline_has_self_overlap
    return bool(polyline_has_self_overlap(points, tolerance=ai_grid_polyline_overlap_tolerance(game_map)))


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
            # Street-following routes turn 90 degrees at intersections; the sharp-turn test only
            # makes sense for free-drawn chords on maps without road data.
            if not _ai_land_straight_limit_applies(game_map):
                for i in range(1, len(pts) - 1):
                    if route_turn_angle_degrees(pts[i - 1], pts[i], pts[i + 1]) >= float(AI_ROUTE_BACKTRACK_TURN_DEGREES):
                        return True
        if _ai_land_straight_limit_applies(game_map):
            # Street routes: metric doubling-back test (grid-unit overlap tests flag every
            # curve of a dense street polyline).
            return _ai_line_doubles_back(pts, game_map)
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
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import preset_has_water_areas
        if not preset_has_water_areas(preset_name):
            return False
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
    if p1_w or p2_w:
        return True
    if ts and hasattr(ts, 'is_segment_entirely_water') and not skip_osm:
        try:
            if ts.is_segment_entirely_water(x1, y1, x2, y2, skip_osm=False):
                return True
        except TypeError:
            if ts.is_segment_entirely_water(x1, y1, x2, y2):
                return True
    if ts and hasattr(ts, 'get_water_segment_count') and not skip_osm:
        try:
            if ts.get_water_segment_count(x1, y1, x2, y2) > 0:
                return True
        except Exception:
            pass
    for t in (0.25, 0.5, 0.75):
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        if _in_water(px, py):
            return True
    return False


def _ferry_point_near_water(
    game_map: Map, x: float, y: float, max_distance_m: float = FERRY_MAX_DISTANCE_FROM_WATER_M,
) -> bool:
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


def _ferry_segment_near_water(
    game_map: Map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    max_distance_m: float = FERRY_MAX_DISTANCE_FROM_WATER_M,
) -> bool:
    """True when every sampled point on the segment is within max_distance_m of water."""
    ts = getattr(game_map, 'tile_system', None)
    if ts and hasattr(ts, 'is_segment_near_water'):
        return bool(ts.is_segment_near_water(x1, y1, x2, y2, max_distance_m, skip_osm=False))
    if not _ferry_point_near_water(game_map, x1, y1, max_distance_m):
        return False
    if not _ferry_point_near_water(game_map, x2, y2, max_distance_m):
        return False
    segment_length = math.hypot(x2 - x1, y2 - y1)
    sample_interval_grid = 0.02  # ~20 m in grid units (1 unit ≈ 1 km)
    num_samples = max(2, int(math.ceil(segment_length / sample_interval_grid)) + 1)
    for i in range(num_samples):
        t = i / (num_samples - 1) if num_samples > 1 else 0.0
        sx = x1 + t * (x2 - x1)
        sy = y1 + t * (y2 - y1)
        if not _ferry_point_near_water(game_map, sx, sy, max_distance_m):
            return False
    return True


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
    if current_year is not None and _land_segment_on_osm_polyline_for_length_credit(
        game_map, x1, y1, x2, y2, int(current_year),
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


def _route_bus_combined_street_length_fraction(
    game_map: Map,
    points: list,
    *,
    skip_osm: bool = False,
    current_year: int = None,
) -> float:
    """Share of combined land route length within 5m of street centerlines (water excluded)."""
    if not points or len(points) < 2:
        return 1.0
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return 1.0
    from map_preset_loader import preset_has_road_support
    if not preset_has_road_support(preset_name):
        return 1.0
    from game_presets import BUS_STREET_MAX_DISTANCE_GRID
    from map_preset_loader import get_preset_road_utils
    land_segment_fraction_on_street_network = get_preset_road_utils(preset_name).land_segment_fraction_on_street_network

    cy = int(current_year) if current_year is not None else 1830
    street_len = 0.0
    total_land_len = 0.0
    max_d = float(BUS_STREET_MAX_DISTANCE_GRID)
    for i in range(len(points) - 1):
        x1, y1 = float(points[i][0]), float(points[i][1])
        x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
            continue
        seg_len = _grid_segment_length(x1, y1, x2, y2)
        if seg_len <= 1e-12:
            continue
        total_land_len += seg_len
        frac = land_segment_fraction_on_street_network(
            game_map, x1, y1, x2, y2, cy, max_distance_grid=max_d,
        )
        street_len += seg_len * max(0.0, min(1.0, float(frac)))
    if total_land_len <= 1e-12:
        return 1.0
    return street_len / total_land_len


def _route_has_off_street_bus_segment(
    game_map: Map, points: list, *, skip_osm: bool = False, current_year: int = None,
) -> bool:
    """True when combined bus land length is not entirely on the street network."""
    from game_presets import BUS_STREET_MIN_ROUTE_FRACTION

    frac = _route_bus_combined_street_length_fraction(
        game_map, points, skip_osm=skip_osm, current_year=current_year,
    )
    return frac + 1e-12 < float(BUS_STREET_MIN_ROUTE_FRACTION)


def _enforce_human_bus_route_on_streets_or_raise(
    game_map: Map,
    points: list,
    *,
    current_year: int,
    for_extension: bool = False,
) -> None:
    """Human bus builds: entire combined land length must follow streets (full OSM check)."""
    if _route_has_off_street_bus_segment(
        game_map, points, skip_osm=False, current_year=int(current_year),
    ):
        msg = (
            "Bus extensions must stay entirely on streets (combined route length). "
            "Turn onto intersecting streets at corners — no off-street shortcuts."
            if for_extension
            else "Bus routes must stay entirely on streets (combined route length). "
            "Follow road polylines and turn onto intersecting streets at corners — "
            "no off-street shortcuts."
        )
        raise ValueError(msg)


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
        if _land_segment_on_osm_polyline_for_length_credit(game_map, x1, y1, x2, y2, cy):
            continue
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


def _osm_inter_street_connector_valid_at_route_index(
    game_map: Map,
    points: list,
    seg_idx: int,
    current_year: int,
) -> bool:
    """True when a land segment <=20 m connects two OSM street polylines."""
    from game_presets import AI_OFF_STREET_FREE_SEGMENT_GRID

    if not points or seg_idx < 0 or seg_idx >= len(points) - 1:
        return False
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return False
    from map_preset_loader import preset_has_road_support
    if not preset_has_road_support(preset_name):
        return False
    x1, y1 = float(points[seg_idx][0]), float(points[seg_idx][1])
    x2, y2 = float(points[seg_idx + 1][0]), float(points[seg_idx + 1][1])
    if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=True):
        return False
    cy = int(current_year)
    if _land_segment_on_osm_polyline_for_length_credit(game_map, x1, y1, x2, y2, cy):
        return False
    conn_len = _grid_segment_length(x1, y1, x2, y2)
    if conn_len > float(AI_OFF_STREET_FREE_SEGMENT_GRID) + 1e-9:
        return False
    has_prev = False
    has_next = False
    if seg_idx > 0:
        px1, py1 = float(points[seg_idx - 1][0]), float(points[seg_idx - 1][1])
        has_prev = _land_segment_on_osm_polyline_for_length_credit(
            game_map, px1, py1, x1, y1, cy,
        )
    if seg_idx < len(points) - 2:
        nx2, ny2 = float(points[seg_idx + 2][0]), float(points[seg_idx + 2][1])
        has_next = _land_segment_on_osm_polyline_for_length_credit(
            game_map, x2, y2, nx2, ny2, cy,
        )
    return has_prev and has_next


def _inter_street_connector_valid_at_route_index(
    game_map: Map,
    points: list,
    seg_idx: int,
    current_year: int,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when a land segment <=20 m connects two on-street segments."""
    from game_presets import AI_OFF_STREET_FREE_SEGMENT_GRID

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
    if conn_len > float(AI_OFF_STREET_FREE_SEGMENT_GRID) + 1e-9:
        return False
    has_prev = False
    has_next = False
    if seg_idx > 0:
        px1, py1 = float(points[seg_idx - 1][0]), float(points[seg_idx - 1][1])
        has_prev = _route_land_segment_follows_street(
            game_map, px1, py1, x1, y1, cy, skip_osm=skip_osm,
        )
    if seg_idx < len(points) - 2:
        nx2, ny2 = float(points[seg_idx + 2][0]), float(points[seg_idx + 2][1])
        has_next = _route_land_segment_follows_street(
            game_map, x2, y2, nx2, ny2, cy, skip_osm=skip_osm,
        )
    return has_prev and has_next


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


def _collect_route_elevation_levels_from_build_queue(line_name: str, build_queue: list) -> list:
    """Per-segment elevations from in-progress line build jobs, in route order."""
    name_cf = (line_name or '').strip().casefold()
    segments = []
    for build in build_queue or []:
        if build.get('type') != 'line':
            continue
        data = build.get('data') or {}
        if (data.get('name') or '').strip().casefold() != name_cf:
            continue
        seg_idx = int(data.get('segment_index', 0) or 0)
        elevs = list(data.get('elevation_levels') or [])
        segments.append((seg_idx, elevs))
    segments.sort(key=lambda t: t[0])
    combined = []
    for _idx, elevs in segments:
        combined.extend(elevs)
    return combined


def _merged_route_elevation_levels(
    line,
    merged_points: list,
    additional_points: list,
    extend_from_end: bool,
    extension_elevation_levels: list = None,
    *,
    is_bus_line: bool = False,
    existing_route: list = None,
    existing_elevation_levels: list = None,
) -> list:
    """Elevation per segment for a line plus extension (for street-alignment validation)."""
    n = max(0, len(merged_points) - 1)
    if n <= 0:
        return []
    default = 'street' if is_bus_line else 'above_ground'
    if line and hasattr(line, 'get_route'):
        old_route = list(line.get_route() or [])
        old_elevs = list(getattr(line, 'elevation_levels', None) or [])
    else:
        old_route = list(existing_route or [])
        old_elevs = list(existing_elevation_levels or [])
    # The extension adds one segment per click (the first joins the line); everything else
    # in merged_points is the line so far, pending extensions included.
    n_new = min(len(additional_points or []), n)
    n_old = n - n_new
    old_elevs = (old_elevs + [default] * n_old)[:n_old]
    ext_elevs = list(extension_elevation_levels or [])
    ext_elevs = (ext_elevs + [default] * n_new)[:n_new]  # click order, joining segment first
    if extend_from_end:
        combined = old_elevs + ext_elevs
    else:
        # Beginning extensions run outward from the line start, the reverse of route order.
        combined = list(reversed(ext_elevs)) + old_elevs
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


def _land_segment_qualifies_for_street_length_credit(seg_len: float) -> bool:
    """Segments shorter than 10 m (or isolated on-street points) do not count as on-street."""
    from game_presets import AI_MIN_STREET_QUALIFYING_SEGMENT_GRID
    return seg_len + 1e-12 >= float(AI_MIN_STREET_QUALIFYING_SEGMENT_GRID)


def _land_segment_on_osm_polyline_for_length_credit(
    game_map: Map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
) -> bool:
    """True when a segment's full length counts toward OSM polyline combined-length totals."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return False
    from map_preset_loader import preset_has_road_support
    if not preset_has_road_support(preset_name):
        return False
    from map_preset_loader import get_preset_road_utils
    route_land_segment_on_osm_polyline = get_preset_road_utils(preset_name).route_land_segment_on_osm_polyline
    return route_land_segment_on_osm_polyline(
        game_map, x1, y1, x2, y2, int(current_year),
    )


def _land_segment_on_street_for_length_credit(
    game_map: Map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
    seg_len: float,
    *,
    skip_osm: bool = False,
) -> bool:
    """True when a land segment's full length counts toward on-street / OSM polyline totals."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import preset_has_road_support
        if preset_has_road_support(preset_name):
            from map_preset_loader import get_preset_road_utils
            route_land_segment_on_osm_polyline = get_preset_road_utils(preset_name).route_land_segment_on_osm_polyline
            # Dense OSM street polylines use many short vertices; each polyline-hugging
            # piece counts toward combined length (the 10 m rule applies only to stubs).
            if route_land_segment_on_osm_polyline(
                game_map, x1, y1, x2, y2, int(current_year),
            ):
                return True
    if not _land_segment_qualifies_for_street_length_credit(seg_len):
        return False
    return _route_land_segment_follows_street(
        game_map, x1, y1, x2, y2, int(current_year), skip_osm=skip_osm,
    )


def _ai_station_point_in_water(game_map: Map, grid_x: float, grid_y: float) -> bool:
    """True when a grid point lies in water (strict; no near-shore exception for AI)."""
    if not game_map:
        return False
    gx, gy = float(grid_x), float(grid_y)
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
        if preset_has_water_areas(preset_name):
            try:
                if preset_is_point_in_water(preset_name, gx, gy, game_map, skip_osm=True):
                    return True
            except TypeError:
                if preset_is_point_in_water(preset_name, gx, gy, game_map):
                    return True
    ts = getattr(game_map, 'tile_system', None)
    if ts:
        try:
            if ts.is_point_in_water(gx, gy, skip_osm=True):
                return True
        except TypeError:
            if ts.is_point_in_water(gx, gy):
                return True
    return False


def _require_ai_station_not_in_water(game_map: Map, player, grid_x: float, grid_y: float) -> None:
    """AI stations must never be placed in water."""
    if not getattr(player, 'is_ai', False):
        return
    if _ai_station_point_in_water(game_map, grid_x, grid_y):
        raise ValueError(
            f"AI stations cannot be built in water (point ({float(grid_x):.2f}, {float(grid_y):.2f}))."
        )


def _off_street_segment_cost_multiplier(
    game_map: Map, x1: float, y1: float, x2: float, y2: float, current_year: int,
    *, is_bus_line: bool = False, skip_osm: bool = False,
) -> float:
    from game_presets import (
        OFF_STREET_SEGMENT_COST_MULTIPLIER,
        AI_OFF_STREET_FREE_SEGMENT_GRID,
        AI_BUS_CONNECTOR_COST_MULTIPLIER,
        AI_BUS_MAX_CONNECTOR_GRID,
    )

    if _route_land_segment_follows_street(
        game_map, x1, y1, x2, y2, current_year, skip_osm=skip_osm,
    ):
        return 1.0
    seg_len = _grid_segment_length(x1, y1, x2, y2)
    if is_bus_line and seg_len <= float(AI_BUS_MAX_CONNECTOR_GRID) + 1e-9:
        return float(AI_BUS_CONNECTOR_COST_MULTIPLIER)
    if seg_len <= float(AI_OFF_STREET_FREE_SEGMENT_GRID):
        return 1.0
    return float(OFF_STREET_SEGMENT_COST_MULTIPLIER)


def _ai_route_off_street_cost_multiplier(
    game_map: Map, route_points: list, current_year: int, *, is_bus_line: bool = False,
    skip_osm: bool = False,
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
            game_map, x1, y1, x2, y2, cy, is_bus_line=is_bus_line, skip_osm=skip_osm,
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

    Off-street = any non-water segment not on a street/road polyline, except <=20 m connectors
    between two on-street segments.
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
        elif _land_segment_on_street_for_length_credit(
            game_map, x1, y1, x2, y2, cy, seg_len, skip_osm=skip_osm,
        ):
            street_water_len += seg_len
        elif _inter_street_connector_valid_at_route_index(
            game_map, route_points, i, cy, skip_osm=skip_osm,
        ) or _osm_inter_street_connector_valid_at_route_index(
            game_map, route_points, i, cy,
        ):
            street_water_len += seg_len
        else:
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

    osm_polyline_len counts land segments that hug OSM road centerlines, plus <=20 m connectors
    between separate OSM polylines. water_len counts water crossings. connector_len is everything else.
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

            if preset_has_road_support(preset_name) and _land_segment_on_osm_polyline_for_length_credit(
                game_map, x1, y1, x2, y2, cy,
            ):
                osm_polyline_len += seg_len
            elif preset_has_road_support(preset_name) and _osm_inter_street_connector_valid_at_route_index(
                game_map, route_points, i, cy,
            ):
                osm_polyline_len += seg_len
            else:
                connector_len += seg_len
        elif _land_segment_on_street_for_length_credit(
            game_map, x1, y1, x2, y2, cy, seg_len, skip_osm=skip_osm,
        ):
            osm_polyline_len += seg_len
        elif _inter_street_connector_valid_at_route_index(
            game_map, route_points, i, cy, skip_osm=skip_osm,
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
    """True when strictly more than 50% of route length lies on OSM street polylines."""
    if not route_points or len(route_points) < 2:
        return True
    from game_presets import AI_RAIL_MIN_STREET_POLYLINE_ROUTE_FRACTION

    osm_len, _water_len, _connector_len, total_len = _route_osm_polyline_water_and_connector_lengths(
        game_map, route_points, current_year, skip_osm=skip_osm,
    )
    if total_len <= 0.0:
        return True
    min_frac = float(AI_RAIL_MIN_STREET_POLYLINE_ROUTE_FRACTION)
    return osm_len / total_len > min_frac + 1e-12


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

    Off-street for AI = any non-water segment not on a street/road polyline, except <=20 m
    connectors between two on-street segments.
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
    if _ai_land_straight_limit_applies(game_map):
        # Presets with OSM road data: polylines + connectors up to 10 m + water crossings only.
        from ai_street_router import ai_route_rule_error
        return ai_route_rule_error(game_map, route_points, current_year, for_extension=for_extension)
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
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import preset_has_road_support
        if not preset_has_road_support(preset_name):
            return None
    if not _ai_route_majority_on_osm_polylines(
        game_map, route_points, current_year, skip_osm=skip_osm,
    ):
        from game_presets import AI_RAIL_MIN_STREET_POLYLINE_ROUTE_FRACTION

        osm_len, water_len, connector_len, total_len = _route_osm_polyline_water_and_connector_lengths(
            game_map, route_points, current_year, skip_osm=skip_osm,
        )
        pct = (100.0 * osm_len / total_len) if total_len > 0 else 0.0
        min_pct = 100.0 * float(AI_RAIL_MIN_STREET_POLYLINE_ROUTE_FRACTION)
        kind = "Extensions" if for_extension else "AI rail lines"
        return (
            f"{kind} must be more than {min_pct:.0f}% on OSM street polylines "
            f"(water crossings and straight connectors between polylines do not count). "
            f"This route is only {pct:.0f}% on street polylines "
            f"({osm_len:.2f} on polylines, {water_len:.2f} water, "
            f"{connector_len:.2f} connectors)."
        )
    return None


def _track_population_cost_factor(game_map: Map, x: float, y: float) -> float:
    """Price factor for track over land at (x, y): cheaper where fewer people live.

    TRACK_COST_MIN_POPULATION_FACTOR in empty country, rising linearly to 1.0 at the population
    of the map's busiest cells (top TRACK_COST_FULL_PRICE_TOP_FRACTION of populated cells).
    """
    from game_presets import TRACK_COST_FULL_PRICE_TOP_FRACTION, TRACK_COST_MIN_POPULATION_FACTOR
    if game_map is None:
        return 1.0
    population = getattr(game_map, 'population', None) or {}
    if not population:
        return 1.0
    year = getattr(game_map, '_track_cost_reference_year', None)
    current_year = getattr(getattr(game_map, '_game', None), 'get_year', lambda: None)()
    reference = getattr(game_map, '_track_cost_reference_pop', None)
    if reference is None or year != current_year:
        values = sorted((v for v in population.values() if v > 0), reverse=True)
        if not values:
            return 1.0
        idx = min(len(values) - 1, int(len(values) * float(TRACK_COST_FULL_PRICE_TOP_FRACTION)))
        reference = max(1, values[idx])
        game_map._track_cost_reference_pop = reference
        game_map._track_cost_reference_year = current_year
    pop = float(population.get((int(round(x)), int(round(y))), 0) or 0)
    low = float(TRACK_COST_MIN_POPULATION_FACTOR)
    return low + (1.0 - low) * min(1.0, pop / float(reference))


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
    from game_presets import LINE_MAINTENANCE_COST_MULTIPLIER
    return int(round(base * float(LINE_MAINTENANCE_COST_MULTIPLIER)))


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
    if for_ai and _ai_land_straight_limit_applies(game_map):
        from ai_street_router import ai_route_rule_error
        err = ai_route_rule_error(
            game_map, points, cy, allow_water_crossing=not is_bus_line, for_extension=for_extension,
        )
        if err:
            raise ValueError(err)
        return
    if is_bus_line:
        if not for_ai:
            _enforce_human_bus_route_on_streets_or_raise(
                game_map, points, current_year=cy, for_extension=for_extension,
            )
            return
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


def _assert_points_in_buildable_area(game_map: Map, points: list) -> None:
    """No-op: preset buildable-zone restrictions removed."""
    return


def _validate_land_line_route_for_build_impl(
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
    new_segments: tuple | None = None,
    extension_reversed: bool = False,
) -> dict:
    """Shared geometry, street, tile, and land-route checks for create and extend."""
    if is_ferry_line or not route_points or len(route_points) < 2:
        return {'success': True}

    if not for_extension and not ai_fast:
        _validate_route_geometry(
            route_points, game_map, is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
            skip_osm=skip_osm,
        )
    if (
        player is not None and getattr(player, 'is_ai', False)
        and _ai_land_straight_limit_applies(game_map)
    ):
        # Street rule for every AI land build, fast path included: OSM polylines, connectors
        # of at most 10 m, longer off-street segments only as water crossings.
        from ai_street_router import ai_route_rule_error
        street_err = ai_route_rule_error(
            game_map, route_points, current_year,
            allow_water_crossing=not is_bus_line, for_extension=for_extension,
            segments=new_segments,
        )
        if street_err:
            raise ValueError(street_err)
    if player is not None and getattr(player, 'is_ai', False):
        # AI lines may never double back on themselves (for extensions: the whole merged line).
        if for_extension:
            if _ai_line_doubles_back(route_points, game_map):
                raise ValueError("This extension would double back on the line.")
        elif _ai_route_has_backtracking(route_points, game_map):
            raise ValueError("This route doubles back on itself.")
    route_for_ai = player is not None and getattr(player, 'is_ai', False)
    street_skip_osm = skip_osm
    if is_bus_line and not route_for_ai:
        street_skip_osm = False
    if not ai_fast:
        _enforce_route_street_rules_or_raise(
            game_map, route_points, current_year=current_year,
            is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
            for_extension=for_extension, skip_osm=street_skip_osm, for_ai=route_for_ai,
        )
    if player is not None and getattr(player, 'is_ai', False) and not is_bus_line and not ai_fast:
        majority_err = _ai_route_street_water_requirement_error(
            game_map, route_points, current_year, skip_osm=True, for_extension=for_extension,
        )
        if majority_err:
            raise ValueError(majority_err)

    if ai_fast:
        if is_bus_line:
            _enforce_route_street_rules_or_raise(
                game_map, route_points, current_year=current_year,
                is_bus_line=True, is_ferry_line=False,
                for_extension=for_extension, skip_osm=skip_osm, for_ai=route_for_ai,
            )
        elif route_for_ai and not is_ferry_line and _ai_land_straight_limit_applies(game_map):
            majority_err = _ai_route_street_water_requirement_error(
                game_map, route_points, current_year, skip_osm=True, for_extension=for_extension,
            )
            if majority_err:
                raise ValueError(majority_err)
        return {'success': True}
    if use_fast_path:
        return {'success': True}

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
        new_segments=new_segments if for_extension else None,
        extension_reversed=extension_reversed,
    )


def _validate_land_line_route_for_build(game_map: Map, route_points: list, **kwargs) -> dict:
    """Shared geometry, street, tile, and land-route checks for create and extend.

    Runs entirely on preset water/road data: live Overpass queries made this step take
    minutes on slow connections, and the preset data is what the cost preview already uses.
    """
    ts = getattr(game_map, 'tile_system', None)
    prev = getattr(ts, '_preset_only_water', False) if ts else False
    if ts:
        ts._preset_only_water = True
    try:
        return _validate_land_line_route_for_build_impl(game_map, route_points, **kwargs)
    finally:
        if ts:
            ts._preset_only_water = prev


def _invalid_street_aligned_segment_indices(
    points: list,
    game_map: Map,
    current_year: int,
    *,
    is_bus_line: bool = False,
    is_ferry_line: bool = False,
    elevation_levels: list = None,
    skip_osm: bool = False,
) -> list:
    """Return 0-based segment indices that require streets but do not follow them."""
    if is_ferry_line or not points or len(points) < 2:
        return []
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return []
    from map_preset_loader import preset_has_road_support
    if not preset_has_road_support(preset_name):
        return []

    default_elev = 'street' if is_bus_line else 'above_ground'
    seg_elevs = _normalize_route_elevation_levels(
        points, elevation_levels, is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
    )
    cy = int(current_year)
    invalid = []
    for i in range(len(points) - 1):
        elev = seg_elevs[i] if i < len(seg_elevs) else default_elev
        if not _segment_elevation_requires_street_alignment(elev):
            continue
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=skip_osm):
            continue
        if is_bus_line:
            if _bus_land_segment_follows_street(game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm):
                continue
            invalid.append(i)
            continue
        if _route_land_segment_follows_street(
            game_map, x1, y1, x2, y2, cy, skip_osm=skip_osm,
        ):
            continue
        if _straight_connector_valid_at_route_index(
            game_map, points, i, cy, skip_osm=skip_osm,
        ):
            continue
        invalid.append(i)
    return invalid


def _validate_route_streets_on_land(
    points: list,
    game_map: Map,
    current_year: int,
    *,
    is_bus_line: bool = False,
    is_ferry_line: bool = False,
    elevation_levels: list = None,
    skip_osm: bool = False,
    new_segments: tuple | None = None,
    extension_reversed: bool = False,
) -> dict:
    """Land segments at street/above_ground must follow streets; underground may tunnel freely.

    ``new_segments`` = (first, end) segment indices of an extension within ``points`` (the
    merged route): only those segments are judged, and reported segment numbers and
    ``invalid_segment_indices`` count in click order from the joining segment
    (``extension_reversed``: the extension runs backwards along ``points``, as when a
    line is extended from its start).
    """
    if is_ferry_line or not points:
        return {'success': True}
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return {'success': True}
    from map_preset_loader import preset_has_road_support, get_preset_is_point_on_road
    if not preset_has_road_support(preset_name):
        return {'success': True}

    kind = 'Bus' if is_bus_line else 'Train'
    default_elev = 'street' if is_bus_line else 'above_ground'
    seg_elevs = _normalize_route_elevation_levels(
        points, elevation_levels, is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
    )
    if seg_elevs and not any(
        _segment_elevation_requires_street_alignment(elev) for elev in seg_elevs
    ):
        return {'success': True}

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
                'invalid_segment_indices': [0],
            }
        return {'success': True}

    invalid_indices = _invalid_street_aligned_segment_indices(
        points, game_map, current_year,
        is_bus_line=is_bus_line, is_ferry_line=is_ferry_line,
        elevation_levels=seg_elevs, skip_osm=skip_osm,
    )
    def _reported(i: int) -> int:
        return i
    if new_segments is not None:
        # The existing line is not re-judged against rules (or road data) it may predate.
        first_new, end_new = new_segments
        invalid_indices = [i for i in invalid_indices if first_new <= i < end_new]

        def _reported(i: int) -> int:
            return (end_new - 1 - i) if extension_reversed else (i - first_new)
    if is_bus_line:
        frac = _route_bus_combined_street_length_fraction(
            game_map, points, skip_osm=skip_osm, current_year=current_year,
        )
        from game_presets import BUS_STREET_MIN_ROUTE_FRACTION
        if frac + 1e-12 < float(BUS_STREET_MIN_ROUTE_FRACTION):
            pct = int(round(100.0 * frac))
            return {
                'success': False,
                'error_message': (
                    "Bus routes must stay entirely on streets (combined route length). "
                    f"This route is only about {pct}% on streets within 5 m of road centerlines. "
                    "Follow road polylines and turn at corners — no off-street shortcuts."
                ),
                'invalid_segment_indices': (
                    sorted(_reported(i) for i in invalid_indices) if invalid_indices else [0]
                ),
            }
        return {'success': True}

    if not invalid_indices:
        return {'success': True}

    reported = sorted(_reported(i) for i in invalid_indices)
    i = min(invalid_indices, key=_reported)
    (x1, y1), (x2, y2) = points[i], points[i + 1]
    if new_segments is not None and extension_reversed:
        (x1, y1), (x2, y2) = (x2, y2), (x1, y1)  # in click direction
    what = "Extension segment" if new_segments is not None else "Segment"
    error_message = (
        f"{kind} routes at street or above-ground level must follow streets on land. "
        f"{what} {reported[0] + 1} from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) is not on a road."
    )
    return {
        'success': False,
        'error_message': error_message,
        'invalid_segment_indices': reported,
    }


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


def _extension_points_in_route_order(additional_points: list, extend_from_end: bool) -> list:
    """Map click order to route order (first click adjacent to attach end).

    End extensions append clicks as clicked. Beginning extensions prepend with the
    last click as the new route tip, so click order is reversed along the line.
    """
    pts = list(additional_points or [])
    if extend_from_end:
        return pts
    return list(reversed(pts))


# Laws whose targets are harmed (the rest are good for their targets). Every company that is
# not a target helps pass a harmful law.
HARMFUL_LAW_TYPES = frozenset({
    'gov_attack', 'add_lawsuit_excuse', 'audit_target', 'block_funding', 'increase_property_tax_5',
})


def _route_returns_to_start(points: list) -> bool:
    """True when a route ends back near where it started (a loop or a U-turn).

    Routes shorter than AI_LINE_RETURN_CHECK_MIN_LENGTH_GRID are never counted as returning.
    """
    import math
    from game_presets import AI_LINE_END_SEPARATION_MIN_SHARE, AI_LINE_RETURN_CHECK_MIN_LENGTH_GRID
    pts = [_normalize_route_point(p) for p in (points or [])]
    if len(pts) < 3:
        return False
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    if length < AI_LINE_RETURN_CHECK_MIN_LENGTH_GRID:
        return False
    ends = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
    return ends < AI_LINE_END_SEPARATION_MIN_SHARE * length


def _route_crosses_itself(points: list) -> bool:
    """True when two non-adjacent segments of a route cross or touch (the line meets its own path)."""
    pts = [_normalize_route_point(p) for p in (points or [])]
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1) if pts[i] != pts[i + 1]]
    if len(segs) < 3:
        return False

    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    def on_seg(a, b, c):
        return min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9 and \
            min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9

    boxes = [(min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])) for a, b in segs]
    for i in range(len(segs)):
        a, b = segs[i]
        bi = boxes[i]
        for j in range(i + 2, len(segs)):
            bj = boxes[j]
            if bi[1] < bj[0] or bj[1] < bi[0] or bi[3] < bj[2] or bj[3] < bi[2]:
                continue
            c, d = segs[j]
            o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
            if o1 != o2 and o3 != o4:
                return True
            if (o1 == 0 and on_seg(a, b, c)) or (o2 == 0 and on_seg(a, b, d)) or \
                    (o3 == 0 and on_seg(c, d, a)) or (o4 == 0 and on_seg(c, d, b)):
                return True
    return False


def _require_ai_route_not_returning(player, points: list, game_map=None) -> None:
    """AI lines run efficiently: they never come back to their start, cross their own path or
    double back on themselves."""
    if not getattr(player, 'is_ai', False):
        return
    if _route_returns_to_start(points):
        raise ValueError("AI lines may not return to where they started.")
    if _route_crosses_itself(points):
        raise ValueError("AI lines may not cross their own path.")
    from map_preset_utils import ai_grid_polyline_overlap_tolerance, polyline_has_self_overlap
    if polyline_has_self_overlap(list(points), tolerance=ai_grid_polyline_overlap_tolerance(game_map)):
        raise ValueError("AI lines may not overlap themselves.")
    if game_map is not None and _ai_line_doubles_back(list(points), game_map):
        raise ValueError("AI lines may not double back on themselves.")


def _ai_merged_line_route(existing_route: list, additional_points: list, extend_from_end: bool) -> list:
    """Full polyline after an extension (for backtracking / street validation).

    Segments are: existing_end→first click, then between each click in order
    (or existing_start→first click outward when extending from the beginning).
    """
    if not existing_route:
        return _extension_points_in_route_order(additional_points, extend_from_end)
    if not additional_points:
        return list(existing_route)
    ordered = _extension_points_in_route_order(additional_points, extend_from_end)
    if extend_from_end:
        return list(existing_route) + ordered
    return ordered + list(existing_route)


def _normalize_route_point(p) -> tuple:
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return (float(p[0]), float(p[1]))
    if hasattr(p, 'x') and hasattr(p, 'y'):
        return (float(p.x), float(p.y))
    return (float(p), float(p))


def _points_close(p1, p2, tol: float = 0.05) -> bool:
    import math
    a = _normalize_route_point(p1)
    b = _normalize_route_point(p2)
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def _dedupe_consecutive_route_points(points: list, tol: float = 0.01) -> list:
    if not points:
        return []
    out = [_normalize_route_point(points[0])]
    for p in points[1:]:
        pt = _normalize_route_point(p)
        if not _points_close(out[-1], pt, tol):
            out.append(pt)
    return out


def _open_polyline_display_points(points: list, tol: float = 0.01) -> list:
    """Drop consecutive duplicates and accidental loop-closing last==first points."""
    route = _dedupe_consecutive_route_points(points, tol=tol)
    if len(route) >= 2 and _points_close(route[0], route[-1], tol=tol):
        route = route[:-1]
    return route


def _fit_elevation_levels(elevations: list, num_segments: int, default: str = 'street') -> list:
    if num_segments <= 0:
        return []
    src = list(elevations or [])
    if len(src) >= num_segments:
        return src[:num_segments]
    if not src:
        return [default] * num_segments
    return src + [src[-1]] * (num_segments - len(src))


# Selling a line segment: a click this close (grid units, ~50 m) to a line end counts as the end.
SELL_SEGMENT_END_SNAP_GRID = 0.05


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


def _record_built_long_edges(line, build_points: list) -> None:
    """Remember built edges longer than the gap threshold, so a long segment that was
    ordered and completed is part of the line instead of being read as a gap."""
    import math
    if line is None or not build_points or len(build_points) < 2:
        return
    limit = _max_connected_route_edge_length()
    pts = [_normalize_route_point(p) for p in build_points]
    for a, b in zip(pts, pts[1:]):
        if math.hypot(b[0] - a[0], b[1] - a[1]) > limit:
            edges = getattr(line, '_built_long_edges', None)
            if edges is None:
                edges = line._built_long_edges = set()
            edges.add(_normalize_route_edge(a, b))


def _max_connected_route_edge_length() -> float:
    """Longest grid edge treated as one built segment (gaps exceed this)."""
    from game_presets import LINE_BUILD_SEGMENT_BATCH_MAX_LENGTH_GRID
    return float(LINE_BUILD_SEGMENT_BATCH_MAX_LENGTH_GRID) + 0.01


def _normalize_route_edge(a, b) -> tuple:
    pa = _normalize_route_point(a)
    pb = _normalize_route_point(b)
    return (pa, pb) if pa <= pb else (pb, pa)


def _get_line_destroyed_edges(line) -> set:
    """Undirected edges destroyed by disaster; remain blocked until player rebuilds."""
    edges = getattr(line, '_destroyed_edges', None)
    if not edges:
        return set()
    return set(edges)


def _mark_line_edges_destroyed(line, segment_points: list) -> None:
    """Mark route edges as disaster-destroyed (no auto-repair)."""
    if not line or not segment_points or len(segment_points) < 2:
        return
    if not hasattr(line, '_destroyed_edges') or line._destroyed_edges is None:
        line._destroyed_edges = set()
    pts = [_normalize_route_point(p) for p in segment_points]
    for i in range(len(pts) - 1):
        line._destroyed_edges.add(_normalize_route_edge(pts[i], pts[i + 1]))


def _damaged_route_runs(line) -> list:
    """Stretches of the line's route made of consecutive disaster-destroyed edges."""
    destroyed = _get_line_destroyed_edges(line)
    if not destroyed:
        return []
    route = [_normalize_route_point(p) for p in (line.get_route() or [])]
    runs, current = [], []
    for a, b in zip(route, route[1:]):
        if _normalize_route_edge(a, b) in destroyed:
            if not current:
                current = [a]
            current.append(b)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _clear_rebuilt_destroyed_edges(line, build_points: list) -> None:
    """Remove destroyed-edge markers covered by a completed player build."""
    destroyed = getattr(line, '_destroyed_edges', None)
    if not destroyed:
        return
    pts = [_normalize_route_point(p) for p in (build_points or [])]
    for i in range(len(pts) - 1):
        destroyed.discard(_normalize_route_edge(pts[i], pts[i + 1]))
    if not destroyed:
        line._destroyed_edges = set()


def _split_route_into_connected_portions(
    route: list,
    max_edge_length: float = None,
    blocked_edges: set = None,
    built_edges: set = None,
) -> list:
    """Split a built route into operational polylines at gaps or incomplete edges.

    An over-long edge is a gap (a chord between separately built pieces) unless it is in
    ``built_edges``: edges that were themselves ordered and built as one long segment."""
    import math

    route = _dedupe_consecutive_route_points(route or [])
    if not route:
        return []
    if len(route) == 1:
        return [route]
    if max_edge_length is None:
        max_edge_length = _max_connected_route_edge_length()
    blocked_edges = blocked_edges or set()
    built_edges = built_edges or set()

    portions = []
    current = [_normalize_route_point(route[0])]
    for i in range(len(route) - 1):
        a = _normalize_route_point(route[i])
        b = _normalize_route_point(route[i + 1])
        dist = math.hypot(b[0] - a[0], b[1] - a[1])
        edge = _normalize_route_edge(a, b)
        edge_blocked = edge in blocked_edges
        edge_gap = dist > max_edge_length and edge not in built_edges
        if edge_blocked or edge_gap:
            if len(current) >= 2:
                portions.append(current)
            current = [b]
        elif not _points_close(current[-1], b):
            current.append(b)
    if len(current) >= 2:
        portions.append(current)
    return portions


def _point_on_route_polyline(route: list, x: float, y: float, tolerance: float = 0.5) -> bool:
    """True if (x,y) lies on route polyline within tolerance."""
    import math

    if not route:
        return False
    for pt in route:
        px, py = _normalize_route_point(pt)
        if math.hypot(px - x, py - y) <= tolerance:
            return True
    for i in range(len(route) - 1):
        x1, y1 = _normalize_route_point(route[i])
        x2, y2 = _normalize_route_point(route[i + 1])
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            dist = math.hypot(x - x1, y - y1)
        else:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy
            dist = math.hypot(x - proj_x, y - proj_y)
        if dist <= tolerance:
            return True
    return False


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

def setup_game(player_names: list[str], map_width: int = 100, map_height: int = 75, starting_money: Optional[int] = None, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, use_osm: bool = False, map_center_lat: float = None, map_center_lon: float = None, marketplace_years_visible: int = None, map_preset_name: str = None, line_build_speed_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, marketplace_railcars_per_player_factor: float = None, construction_cost_multiplier: float = None, ai_player_indices: list[int] = None, ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, random_ai_spawn_enabled: bool = True, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, random_ai_spawn_interval_years: int = 30, default_ai_difficulty_for_spawns: str = 'hard', ai_min_population: int = 100, ai_route_candidate_count: int = 8, game_seed: int = None, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None, war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None, ruleset_name: str = None, idle_human_benchmark_enabled: bool = False, ai_debug_notifications_enabled: bool = True) -> Game:
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
    if marketplace_railcars_per_player_factor is not None:
        game_presets.MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR = marketplace_railcars_per_player_factor
    if construction_cost_multiplier is not None:
        game_presets.CONSTRUCTION_COST_MULTIPLIER = construction_cost_multiplier
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
    initialize_marketplace_trainsets(
        marketplace,
        num_players=len(players),
        railcars_per_player_factor=marketplace_railcars_per_player_factor,
    )
    game = Game(players=players, game_map=game_map, timeline=timeline, marketplace=marketplace, worker_replenishment_rate=worker_replenishment_rate, natural_disaster_frequency=natural_disaster_frequency, random_ai_spawn_enabled=random_ai_spawn_enabled, merge_enabled=merge_enabled, merge_ai_ai_enabled=merge_ai_ai_enabled, merge_ai_human_enabled=merge_ai_human_enabled, merge_human_human_enabled=merge_human_human_enabled, inflation_rate=inflation_rate, random_ai_spawn_interval_years=random_ai_spawn_interval_years, default_ai_difficulty_for_spawns=default_ai_difficulty_for_spawns, ai_min_population=ai_min_population, ai_route_candidate_count=ai_route_candidate_count, game_seed=game_seed, merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct, merge_ai_reject_chance_pct=merge_ai_reject_chance_pct, merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept, merge_ai_min_years_before_consider=merge_ai_min_years_before_consider, merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years, war_start_chance_per_year=war_start_chance_per_year, war_end_chance_per_year=war_end_chance_per_year, war_worker_draft_chance_per_year=war_worker_draft_chance_per_year, war_new_worker_cost_multiplier=war_new_worker_cost_multiplier, ai_debug_notifications_enabled=ai_debug_notifications_enabled)
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


def initialize_marketplace_trainsets(marketplace: Marketplace, num_players: int = 2, railcars_per_player_factor: float = None):
    """Initialize marketplace using the new category-based generation system.
    This ensures proper categorization and availability rules from the start."""
    from game_presets import MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR, MARKETPLACE_STARTING_WORKER_SUPPLY_FACTOR
    current_year = 1830
    marketplace.num_players = num_players
    factor = railcars_per_player_factor if railcars_per_player_factor is not None else MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR
    marketplace.railcars_per_player_factor = factor
    marketplace.target_railcar_total = max(1, int(num_players * factor))
    
    # Use the new category-based generation system
    marketplace.generate_trainsets_based_on_discoveries(current_year)
    
    from worker import ENGINEER, LAWYER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER
    worker_factor = MARKETPLACE_STARTING_WORKER_SUPPLY_FACTOR
    marketplace.set_worker_supply(ENGINEER.name, int(num_players * 5 * worker_factor))
    marketplace.set_worker_supply(LAWYER.name, int(num_players * 3 * worker_factor))
    marketplace.set_worker_supply(CONSTRUCTION_WORKER.name, int(num_players * 2 * worker_factor))
    marketplace.set_worker_supply(MAINTENANCE_WORKER.name, int(num_players * 10 * worker_factor))


def get_game_params_for_preset(preset_name: str) -> dict:
    """Get game parameters (map dimensions, center, timeline) for a map preset.
    
    All game logic for map selection lives here - the game is playable without qt.py.
    Uses map_preset_loader for dynamic discovery so new maps work without code changes.
    
    Args:
        preset_name: Map preset identifier (see map_preset_loader.list_available_presets)
        
    Returns:
        dict with: map_width, map_height, map_center_lat, map_center_lon,
                   start_year, end_year, use_osm
    """
    from map_preset_loader import get_map_bounds, get_map_timeline, get_default_center_lat, get_default_center_lon, list_available_presets, get_default_map_bounds, get_default_map_timeline
    
    if preset_name not in list_available_presets():
        # Unknown map: use the default map's generic grid with OSM
        bounds = get_default_map_bounds()
        timeline = get_default_map_timeline()
        use_osm = True
    else:
        bounds = get_map_bounds(preset_name)
        timeline = get_map_timeline(preset_name)
        use_osm = True  # All maps use OSM tiles for display
    
    if bounds is None:
        bounds = get_default_map_bounds()
        timeline = get_default_map_timeline()
    
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
    """Combine two company names into one (e.g. Pennsylvania Railroad + Northern Central -> Penn Central)."""
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
    # Second part: last word of target name (e.g. Central from Northern Central)
    second_part = words_b[-1] if words_b else first_part
    if first_part.lower() == second_part.lower():
        second_part = words_b[0] if words_b else second_part
    return f"{first_part} {second_part}".strip()


# The map draws markers on a canvas, which handles this many comfortably.
MAP_DISPLAY_MAX_POPULATION_POINTS = 20000


# Rides simulated per resident; each sampled ride stands for an equal share of all rides.
RIDERSHIP_SAMPLED_TRIPS_PER_RESIDENT = 48
# Yearly rides the model counts per resident between served places (the sample is scaled to this).
RIDERSHIP_TRIPS_PER_RESIDENT_PER_YEAR = 2.0
# Wall-clock limit for one ridership pass; past it the trips simulated so far are used.
RIDERSHIP_TIME_BUDGET_SEC = 20.0


# Where in a population cell a simulated trip starts or ends: a 3x3 spread over the cell
# (people live across the whole cell, not at its centre point).
_CELL_SPREAD_OFFSETS = tuple((dx / 3.0, dy / 3.0) for dx in (-1, 0, 1) for dy in (-1, 0, 1))


def ridership_sample_size(total_population: int) -> int:
    """Number of rides to simulate for a population (proportional to it)."""
    total = max(0, int(total_population or 0))
    return int(round(total * RIDERSHIP_SAMPLED_TRIPS_PER_RESIDENT))


def _multinomial_counts(rng, total: int, weights: list) -> list:
    """How many of ``total`` independent draws land on each index, for weights summing to 1."""
    counts = [0] * len(weights)
    remaining = int(total)
    weight_left = 1.0
    last = len(weights) - 1
    for idx, weight in enumerate(weights):
        if remaining <= 0:
            break
        if idx == last or weight >= weight_left:
            counts[idx] = remaining
            break
        if weight <= 0:
            continue
        drawn = rng.binomialvariate(remaining, weight / weight_left)
        counts[idx] = drawn
        remaining -= drawn
        weight_left -= weight
    return counts


def cap_population_points_for_map_display(
    population_data: list[dict],
    max_points: int = MAP_DISPLAY_MAX_POPULATION_POINTS,
) -> list[dict]:
    """Cap population markers for the map. Over the cap, thin evenly across the whole map
    (most populous point per block) so every area keeps markers, not just the dense cores."""
    if not population_data or len(population_data) <= max_points:
        return population_data
    import math
    block = 2
    while True:
        best = {}
        for point in population_data:
            gx, gy = int(point.get('grid_x', 0) or 0), int(point.get('grid_y', 0) or 0)
            key = (gx // block, gy // block)
            if key not in best or int(point.get('pop') or 0) > int(best[key].get('pop') or 0):
                best[key] = point
        if len(best) <= max_points or block > 64:
            by_pop = lambda p: -int(p.get('pop') or 0)
            kept = sorted(best.values(), key=by_pop)[:max_points]
            if len(kept) < max_points:
                # Use the rest of the cap for the most populous points left out.
                kept_ids = {id(p) for p in kept}
                rest = sorted((p for p in population_data if id(p) not in kept_ids), key=by_pop)
                kept = sorted(kept + rest[:max_points - len(kept)], key=by_pop)
            return kept
        block = max(block + 1, int(math.ceil(block * math.sqrt(len(best) / max_points))))


class Operations:    
    def __init__(self, player_names: list[str], map_width: int = 100, map_height: int = 100, starting_money: Optional[int] = None, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, use_osm: bool = False, map_center_lat: float = None, map_center_lon: float = None, marketplace_years_visible: int = None, map_preset_name: str = None, line_build_speed_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, marketplace_railcars_per_player_factor: float = None, construction_cost_multiplier: float = None, ai_player_indices: list[int] = None,
        ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, random_ai_spawn_enabled: bool = True, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, random_ai_spawn_interval_years: int = 30, default_ai_difficulty_for_spawns: str = 'hard', ai_min_population: int = 100, ai_route_candidate_count: int = 8, game_seed: int = None, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None,         war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None, ruleset_name: str = None, idle_human_benchmark_enabled: bool = False, ai_debug_notifications_enabled: bool = True, ai_specialty_per_player: dict = None):
        """Create Operations (game logic). Game is playable without qt.py.
        
        map_preset_name: Optional preset id - uses
        get_game_params_for_preset to derive map center and use_osm. Map dimensions
        come from params unless overridden by map_width/map_height (the default map has no fixed size).
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
            # Maps other than the default grid have a fixed size
            from map_preset_loader import DEFAULT_PRESET_NAME
            if map_preset_name != DEFAULT_PRESET_NAME:
                map_width = params["map_width"]
                map_height = params["map_height"]
        # Overrides for maps that supply their own population
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
        self.game = setup_game(player_names, map_width, map_height, starting_money, worker_replenishment_rate, natural_disaster_frequency, use_osm=use_osm, map_center_lat=map_center_lat, map_center_lon=map_center_lon, marketplace_years_visible=marketplace_years_visible, map_preset_name=map_preset_name, line_build_speed_multiplier=line_build_speed_multiplier, marketplace_bulk_discount_percent=marketplace_bulk_discount_percent, marketplace_bulk_discount_threshold=marketplace_bulk_discount_threshold, marketplace_railcars_per_player_factor=marketplace_railcars_per_player_factor, construction_cost_multiplier=construction_cost_multiplier, ai_player_indices=ai_player_indices, ai_focus_per_player=ai_focus_per_player, ai_difficulty_per_player=ai_difficulty_per_player, random_ai_spawn_enabled=random_ai_spawn_enabled, merge_enabled=merge_enabled, merge_ai_ai_enabled=merge_ai_ai_enabled, merge_ai_human_enabled=merge_ai_human_enabled, merge_human_human_enabled=merge_human_human_enabled, inflation_rate=inflation_rate, random_ai_spawn_interval_years=random_ai_spawn_interval_years, default_ai_difficulty_for_spawns=default_ai_difficulty_for_spawns, ai_min_population=ai_min_population, ai_route_candidate_count=ai_route_candidate_count, game_seed=game_seed, merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct, merge_ai_reject_chance_pct=merge_ai_reject_chance_pct, merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept, merge_ai_min_years_before_consider=merge_ai_min_years_before_consider, merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years, war_start_chance_per_year=war_start_chance_per_year, war_end_chance_per_year=war_end_chance_per_year, war_worker_draft_chance_per_year=war_worker_draft_chance_per_year, war_new_worker_cost_multiplier=war_new_worker_cost_multiplier, ruleset_name=ruleset_name, idle_human_benchmark_enabled=idle_human_benchmark_enabled, ai_debug_notifications_enabled=ai_debug_notifications_enabled)
        from ai_specialties import SPECIALTIES, ensure_specialties
        # Line types chosen in the game settings (player index -> specialty); the rest are random.
        for _idx, _spec in (ai_specialty_per_player or {}).items():
            _players = self.game.get_players()
            if _spec in SPECIALTIES and 0 <= int(_idx) < len(_players) and _players[int(_idx)].is_ai:
                _players[int(_idx)].ai_line_specialty = _spec
        ensure_specialties(self.game)
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
            'ai_route_candidate_count': int(getattr(g, 'ai_route_candidate_count', 8)),
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
            'loan_base_interest_rate': float(getattr(g, 'loan_base_interest_rate', LOAN_BASE_INTEREST_RATE)),
            'line_build_speed_multiplier': float(game_presets.LINE_BUILD_SPEED_MULTIPLIER),
            'construction_cost_multiplier': float(game_presets.CONSTRUCTION_COST_MULTIPLIER),
            'marketplace_years_visible': int(getattr(mp, 'marketplace_years_visible', game_presets.MARKETPLACE_YEARS_VISIBLE)),
            'marketplace_bulk_discount_percent': float(game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT) * 100.0,
            'marketplace_bulk_discount_threshold': int(game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD),
            'marketplace_railcars_per_player_factor': float(getattr(mp, 'railcars_per_player_factor', game_presets.MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR) or game_presets.MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR),
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
        if 'ai_route_candidate_count' in settings:
            g.ai_route_candidate_count = max(2, min(24, int(settings['ai_route_candidate_count'])))
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
        if 'loan_base_interest_rate' in settings:
            g.loan_base_interest_rate = max(0.0, min(0.5, float(settings['loan_base_interest_rate'])))
        if 'line_build_speed_multiplier' in settings:
            game_presets.LINE_BUILD_SPEED_MULTIPLIER = max(0.5, min(5.0, float(settings['line_build_speed_multiplier'])))
        if 'construction_cost_multiplier' in settings:
            game_presets.CONSTRUCTION_COST_MULTIPLIER = max(0.5, min(10.0, float(settings['construction_cost_multiplier'])))
        if 'marketplace_years_visible' in settings:
            g.marketplace.marketplace_years_visible = max(1, min(200, int(settings['marketplace_years_visible'])))
        if 'marketplace_bulk_discount_percent' in settings:
            game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT = max(0.0, min(100.0, float(settings['marketplace_bulk_discount_percent']))) / 100.0
        if 'marketplace_bulk_discount_threshold' in settings:
            game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD = max(1, int(settings['marketplace_bulk_discount_threshold']))
        if 'marketplace_railcars_per_player_factor' in settings:
            factor = max(0.5, min(10.0, float(settings['marketplace_railcars_per_player_factor'])))
            game_presets.MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR = factor
            g.marketplace.railcars_per_player_factor = factor
            g.marketplace.target_railcar_total = max(1, int(g.marketplace.num_players * factor))
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
        self.enforce_line_schedule_limits(target_line)

    def unassign_trainset_complete(self, trainset: Trainset) -> dict:
        """Unassign a trainset from its current line and remove it from line vehicles."""
        line_name = trainset.get_line_assignment()
        if not line_name:
            raise ValueError("Trainset is not assigned to a line")
        line = self.game.get_map().get_lines_by_name().get(line_name)
        if line and trainset in line.vehicles:
            line.vehicles.remove(trainset)
        trainset.assign_to_line(None)
        if line:
            self.enforce_line_schedule_limits(line)
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

    def _scale_construction_cost(self, cost: int) -> int:
        """Apply global construction cost multiplier (line, extension, station, upgrade, ferry)."""
        from game_presets import CONSTRUCTION_COST_MULTIPLIER
        return max(1, int(cost * CONSTRUCTION_COST_MULTIPLIER))

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

    def get_player_rail_line_names(self, player: Player) -> list[str]:
        """Return line names that accept trainsets (rail lines only; exclude bus and ferry lines)."""
        lines_by_name = self.game.get_map().get_lines_by_name() or {}
        return [ln for ln in player.get_line_names() if lines_by_name.get(ln) and not isinstance(lines_by_name[ln], (BusLine, Ferry))]

    def get_disassemble_info(self, player: Player) -> dict:
        """Get information needed for disassembling trainsets"""
        trainsets = self.get_player_trainsets(player)
        return {'trainsets': trainsets}
    
    def get_player_color_index(self, player: Player) -> int:
        from player_colors import get_player_color_index
        return get_player_color_index(player)
    
    def _line_profit_from_cache(self, line: Line, current_year: int):
        """Year-end cached profit for this calendar year or the prior year (in-year turns)."""
        if not hasattr(line, '_cached_profit') or not hasattr(line, '_cached_profit_year'):
            return None
        cy = int(current_year)
        cached_year = int(line._cached_profit_year)
        if cached_year == cy or cached_year == cy - 1:
            return int(line._cached_profit or 0)
        return None
    
    def line_has_scheduled_service(self, line: Line) -> bool:
        """True when the line timetable runs at least one train per hour in some period."""
        if not line:
            return False
        sched = getattr(line, 'schedule', None) or {}
        return any(float(sched.get(period, 0) or 0) > 0 for period in ('peak', 'off_peak', 'night'))

    def line_can_collect_fare_revenue(self, line: Line) -> bool:
        """True when assigned trainsets and a non-zero schedule can carry paying passengers."""
        if not line or not getattr(line, 'vehicles', None):
            return False
        stations = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
        if len(stations) < 2:
            return False
        return self.line_has_scheduled_service(line)

    def calc_line_profit(self, line: Line, current_year: int = None) -> int:
        """Line profit from year-end cache (no ridership re-simulation here)."""
        if not self.line_can_collect_fare_revenue(line):
            return 0
        if current_year is None:
            current_year = self.game.get_year()
        cached = self._line_profit_from_cache(line, current_year)
        if cached is not None:
            return cached
        stored = getattr(line, 'profit', None)
        if isinstance(stored, (int, float)) and not isinstance(stored, bool):
            return int(stored)
        try:
            return int(line.calc_profit(current_year))
        except Exception:
            return 0

    def line_has_pending_initial_build(self, line_name: str) -> bool:
        """True while the line's first segment is still queued and not on the map."""
        if not line_name:
            return False
        for build in self.game.build_queue:
            if build.get('type') != 'line':
                continue
            if build.get('data', {}).get('name') != line_name:
                continue
            existing = self.game.get_map().get_lines_by_name().get(line_name)
            return existing is None
        return False

    def line_is_ready_to_earn(self, line: Line, player: Player = None) -> bool:
        """True when a line meets the same readiness rules as year-end profit (before ridership)."""
        if line is None:
            return False
        line_name = getattr(line, 'name', None)
        if line_name and self.line_has_pending_initial_build(line_name):
            return False
        stations = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
        if len(stations) < 2:
            return False
        owner = player or getattr(line, 'owner', None)
        if owner:
            try:
                from worker import ENGINEER
                engineer_rental = owner.get_worker_rental(ENGINEER.name)
                if engineer_rental and engineer_rental.is_on_strike():
                    return False
            except (AttributeError, ImportError):
                pass
        return True

    def estimate_planned_line_annual_profit(
        self,
        pop1: int,
        pop2: int,
        route_length: float,
        current_year: int,
        *,
        interior_max_pop: int = 0,
        route_points: list = None,
        player: Player = None,
    ) -> float:
        """Conservative annual profit potential for build decisions (AI and humans share this)."""
        return self._ai_planned_line_gross_annual_revenue_floor(
            pop1, pop2, route_length, current_year,
            interior_max_pop=interior_max_pop,
            route_points=route_points,
            player=player,
        )

    def get_schedule_operating_cost_per_train_hour(self, year: int = None) -> float:
        """Nominal schedule operating cost for one train-hour in the given calendar year."""
        from game_presets import SCHEDULE_OPERATING_COST_PER_TRAIN_HOUR
        from inflation_utils import get_inflation_factor

        y = int(self.get_year() if year is None else year)
        infl = get_inflation_factor(y, max_factor=5.5)
        return float(SCHEDULE_OPERATING_COST_PER_TRAIN_HOUR) * infl

    def get_schedule_operating_cost_per_train_year(
        self, year: int = None, schedule: dict = None,
    ) -> float:
        """Annual schedule operating cost for one trainset at the given timetable."""
        from game_presets import HOURS_PER_YEAR, schedule_weighted_trips_per_hour

        trips_per_hour = schedule_weighted_trips_per_hour(schedule or {})
        return self.get_schedule_operating_cost_per_train_hour(year) * trips_per_hour * HOURS_PER_YEAR

    def get_ai_schedule_cost_per_train_run(self, year: int = None) -> float:
        """Operating cost of one train run for AI schedule budgeting (8× nominal train-hour rate)."""
        from game_presets import AI_SCHEDULE_COST_PER_RUN_MULTIPLIER

        return (
            self.get_schedule_operating_cost_per_train_hour(year)
            * float(AI_SCHEDULE_COST_PER_RUN_MULTIPLIER)
        )

    def _line_fastest_trainset_speed(self, line: Line) -> float:
        fastest_speed = 0.0
        for vehicle in self.get_line_vehicles(line):
            loco = getattr(vehicle, 'locomotive', None) if vehicle else None
            if loco is not None:
                for attr in ('average_speed', 'top_speed'):
                    s = getattr(loco, attr, None)
                    if isinstance(s, (int, float)) and not isinstance(s, bool):
                        fastest_speed = max(fastest_speed, float(s))
                        break
            else:
                s = getattr(vehicle, 'average_speed', None)
                if isinstance(s, (int, float)) and not isinstance(s, bool):
                    fastest_speed = max(fastest_speed, float(s))
        return fastest_speed if fastest_speed > 0 else 30.0

    def get_line_scheduled_trips_by_period(
        self, line: Line, trainset_avg_speed: float, current_year: int = None,
    ) -> dict:
        """Annual trip counts per peak/off-peak/night (sums split route portions)."""
        from game_presets import SCHEDULE_PERIODS

        year = int(self.get_year() if current_year is None else current_year)
        portions = self.get_line_operational_route_portions(line)
        totals = {period: 0 for period in SCHEDULE_PERIODS}
        if len(portions) <= 1:
            by_period = line.scheduled_trips_per_year_by_period(
                trainset_avg_speed=trainset_avg_speed, current_year=year,
            )
            return {period: int(by_period.get(period, 0) or 0) for period in SCHEDULE_PERIODS}
        for idx, portion in enumerate(portions):
            portion_trips = self._scheduled_trips_for_route_portion_by_period(
                line, portion, idx, trainset_avg_speed, current_year=year,
            )
            for period in SCHEDULE_PERIODS:
                totals[period] += int(portion_trips.get(period, 0) or 0)
        return totals

    def passengers_served_from_ridership_and_schedule(
        self,
        line: Line,
        passengers_per_year: int,
        trainset_avg_speed: float,
        total_capacity_per_trip: int,
        current_year: int = None,
    ) -> int:
        """Cap ridership separately in each timetable period by that period's trip capacity."""
        from game_presets import SCHEDULE_PERIOD_YEAR_FRACTIONS, SCHEDULE_PERIODS

        passengers_per_year = int(passengers_per_year or 0)
        if passengers_per_year <= 0 or total_capacity_per_trip <= 0:
            return 0
        trips_by_period = self.get_line_scheduled_trips_by_period(
            line, trainset_avg_speed, current_year=current_year,
        )
        if not any(trips_by_period.get(period, 0) for period in SCHEDULE_PERIODS):
            return 0
        served = 0
        for period in SCHEDULE_PERIODS:
            period_demand = passengers_per_year * SCHEDULE_PERIOD_YEAR_FRACTIONS[period]
            period_capacity = trips_by_period[period] * total_capacity_per_trip
            served += min(period_demand, period_capacity)
        return int(served)

    def _estimate_line_ridership_for_schedule(self, line: Line, year: int) -> int:
        """Ridership input for AI schedule profit estimates (cached sim, prior year, or floor)."""
        riders = int(self.get_line_ridership_for_year(line, year) or 0)
        if riders > 0:
            return riders
        cached_year = getattr(line, '_cached_ridership_year', None)
        if cached_year == year:
            cached = int(getattr(line, '_cached_ridership', 0) or 0)
            if cached > 0:
                return cached
        prev = int(self.get_line_previous_year_ridership(line) or 0)
        if prev > 0:
            return prev
        total_capacity_per_trip = 0
        for vehicle in self.get_line_vehicles(line):
            if vehicle and hasattr(vehicle, 'get_capacity'):
                total_capacity_per_trip += int(vehicle.get_capacity() or 0)
        if total_capacity_per_trip <= 0:
            return int(self.inflate_economic_value(200, max_factor=5.5))
        return max(
            int(self.inflate_economic_value(200, max_factor=5.5)),
            int(total_capacity_per_trip * 0.25),
        )

    def estimate_line_gross_profit_at_schedule(
        self, line: Line, schedule: dict, year: int = None,
    ) -> float:
        """Gross line profit at a timetable using cached/prior ridership (no re-simulation)."""
        from inflation_utils import get_nominal_fare_cap

        if year is None:
            year = self.get_year()
        year = int(year)
        if not line or not getattr(line, 'vehicles', None):
            return 0.0
        stations = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
        if len(stations) < 2:
            return 0.0
        total_capacity_per_trip = 0
        for vehicle in self.get_line_vehicles(line):
            if vehicle and hasattr(vehicle, 'get_capacity'):
                total_capacity_per_trip += int(vehicle.get_capacity() or 0)
        if total_capacity_per_trip <= 0:
            return 0.0
        passengers_per_year = self._estimate_line_ridership_for_schedule(line, year)
        fastest_speed = self._line_fastest_trainset_speed(line)
        saved_schedule = dict(getattr(line, 'schedule', {}) or {})
        line.schedule = dict(schedule or {})
        try:
            served = self.passengers_served_from_ridership_and_schedule(
                line,
                passengers_per_year,
                fastest_speed,
                total_capacity_per_trip,
                current_year=year,
            )
        finally:
            line.schedule = saved_schedule
        if served <= 0:
            return 0.0
        if hasattr(line, 'update_quality'):
            try:
                line.update_quality()
            except (AttributeError, TypeError, ValueError):
                pass
        quality_multiplier = 1.0 + ((float(getattr(line, 'quality', 1.0) or 1.0) - 1.0) * 0.2)
        sy = self.get_timeline_start_year()
        ey = self.get_timeline_end_year()
        _, max_acceptable_fare = get_nominal_fare_cap(int(year), start_year=sy, end_year=ey)
        effective_fare = min(float(getattr(line, 'fare', 0) or 0), max_acceptable_fare)
        return float(served) * effective_fare * quality_multiplier

    def trainset_annual_cost(self, trainset, year: int = None) -> int:
        """What owning one trainset really costs per year: maintenance, fuel and property tax.

        These are the only recurring vehicle costs in the year-end accounts, and none of them
        depends on how often the train runs.
        """
        from inflation_utils import get_inflation_factor
        year = int(self.get_year() if year is None else year)
        maint = 0
        try:
            maint = int(trainset.get_maitnance_costs(year) or 0)
        except Exception:
            maint = 0
        try:
            fuel = int(self.game.calc_resource_costs(None, year, only_trainset=trainset) or 0)
        except Exception:
            fuel = 0
        property_tax = int(20 * get_inflation_factor(year, max_factor=5.5))
        return maint + fuel + property_tax

    # --- Loans (rules in loans.py) ---
    def get_player_loans(self, player: Player) -> list:
        import loans
        return list(loans.loans_of(player))

    def get_loan_offer(self, player: Player, amount: int, years: int = None) -> dict:
        import loans
        return loans.loan_offer(self, player, amount, loans.clamp_term(years))

    def take_loan(self, player: Player, amount: int, years: int = None) -> dict:
        import loans
        return loans.take_loan(self, player, amount, loans.clamp_term(years))

    def repay_loan(self, player: Player, index: int, amount: int = None) -> dict:
        import loans
        return loans.repay_loan(self, player, index, amount)

    def line_full_service_schedule(self, line: Line) -> dict:
        """Highest feasible trains/hour in every period (limited by trainsets and track)."""
        if not line or not getattr(line, 'vehicles', None) or not hasattr(line, 'get_max_frequency_per_hour'):
            return {'peak': 0.0, 'off_peak': 0.0, 'night': 0.0}
        return {p: float(line.get_max_frequency_per_hour(p) or 0) for p in ('peak', 'off_peak', 'night')}

    def estimate_line_gross_at_full_service(
        self, line: Line, year: int = None, *, vehicles: list = None, demand: int = None,
    ) -> float:
        """Fare revenue at full service for a given fleet and a fixed yearly demand.

        ``vehicles`` replaces the line's fleet for the estimate (to price adding or removing
        a trainset); ``demand`` holds ridership constant so two fleets are compared fairly.
        """
        from inflation_utils import get_nominal_fare_cap

        if line is None:
            return 0.0
        year = int(self.get_year() if year is None else year)
        saved_vehicles = line.vehicles
        saved_schedule = dict(getattr(line, 'schedule', {}) or {})
        try:
            if vehicles is not None:
                line.vehicles = list(vehicles)
            if not line.vehicles:
                return 0.0
            capacity = sum(int(v.get_capacity() or 0) for v in line.vehicles if hasattr(v, 'get_capacity'))
            if capacity <= 0:
                return 0.0
            if demand is None:
                demand = self._estimate_line_ridership_for_schedule(line, year)
            line.schedule = self.line_full_service_schedule(line)
            served = self.passengers_served_from_ridership_and_schedule(
                line, int(demand or 0), self._line_fastest_trainset_speed(line), capacity, current_year=year,
            )
        finally:
            line.vehicles = saved_vehicles
            line.schedule = saved_schedule
        if served <= 0:
            return 0.0
        quality_multiplier = 1.0 + ((float(getattr(line, 'quality', 1.0) or 1.0) - 1.0) * 0.2)
        _, max_fare = get_nominal_fare_cap(
            year, start_year=self.get_timeline_start_year(), end_year=self.get_timeline_end_year(),
        )
        fare = min(float(getattr(line, 'fare', 0) or 0), max_fare)
        return float(served) * fare * quality_multiplier

    def compute_max_affordable_ai_schedule(self, line: Line, year: int = None) -> dict:
        """AI timetable: full feasible service, except commuter-style lines.

        No real cost in the year-end accounts depends on how often trains run (maintenance,
        fuel and tax are per trainset; line upkeep is per line), while fare revenue can only
        rise with capacity. Most AI lines therefore run their full fleet all day; commuter rail
        and express bus lines (``ai_specialties.PEAK_SERVICE_TYPES``) run full service at the
        peak and thinner service off-peak and at night.
        """
        if not line or not getattr(line, 'vehicles', None):
            return {'peak': 0.0, 'off_peak': 0.0, 'night': 0.0}
        if not hasattr(line, 'get_max_frequency_per_hour'):
            current = getattr(line, 'schedule', None) or {}
            return {p: float(current.get(p, 0) or 0) for p in ('peak', 'off_peak', 'night')}
        full = self.line_full_service_schedule(line)
        # Commuter-style lines (commuter rail, express buses) concentrate service in the peak.
        from ai_specialties import line_service_type, schedule_shares
        shares = schedule_shares(line_service_type(line))
        return {p: full[p] * shares.get(p, 1.0) for p in full}

    def enforce_line_schedule_limits(self, line) -> dict:
        """Clamp line schedule to current max trains/hour when capacity rules change."""
        if not line or not hasattr(line, 'schedule') or not hasattr(line, 'set_schedule_frequency'):
            return {'changed': {}}
        changed = {}
        for period in ('peak', 'off_peak', 'night'):
            old = float(line.schedule.get(period, 0) or 0)
            line.set_schedule_frequency(period, old)
            new = float(line.schedule.get(period, 0) or 0)
            if new != old:
                changed[period] = {'old': old, 'new': new}
        return {'changed': changed}

    def set_line_schedule(self, line, schedule: dict) -> dict:
        """Set trains/hour per period ({'peak': ..}); each is capped at the line's maximum."""
        for period in ('peak', 'off_peak', 'night'):
            if period in (schedule or {}):
                line.set_schedule_frequency(period, float(schedule[period] or 0))
        return dict(line.schedule)

    def enforce_all_line_schedule_limits(self) -> dict:
        """Clamp every line's schedule to current max trains/hour (peak/off_peak/night)."""
        results = {}
        for line_name, line in (self.get_map_lines() or {}).items():
            result = self.enforce_line_schedule_limits(line)
            if result.get('changed'):
                results[line_name] = result['changed']
        return results

    def get_line_annual_capacity(self, line: Line, schedule: dict = None) -> int:
        """Maximum passengers per year from schedule and assigned trainsets."""
        vehicles = self.get_line_vehicles(line)
        if not vehicles:
            return 0
        total_capacity_per_trip = 0
        fastest_speed = 0.0
        for vehicle in vehicles:
            if vehicle and hasattr(vehicle, 'get_capacity'):
                total_capacity_per_trip += int(vehicle.get_capacity() or 0)
            loco = getattr(vehicle, 'locomotive', None) if vehicle else None
            if loco and getattr(loco, 'average_speed', 0) > fastest_speed:
                fastest_speed = float(loco.average_speed)
        if total_capacity_per_trip <= 0 or fastest_speed <= 0:
            return 0
        if not hasattr(line, 'service_frequency_per_year'):
            return 0
        saved_schedule = None
        if schedule is not None:
            saved_schedule = dict(getattr(line, 'schedule', {}) or {})
            line.schedule = dict(schedule)
        try:
            service_freq = self.get_line_effective_service_frequency_per_year(line, fastest_speed)
        finally:
            if saved_schedule is not None:
                line.schedule = saved_schedule
        if service_freq <= 0:
            return 0
        return int(service_freq * total_capacity_per_trip)

    def _year_end_history(self) -> dict:
        """{finished_year: {'ridership': {line: riders}, 'profit': {line: fare profit}}}.

        Written once per year by advance_year_complete and kept on the game object (so it is
        saved with the game). "Last year" figures are read from here; they are never
        re-simulated, which both keeps them historically correct and keeps the UI responsive.
        """
        history = self.game.__dict__.get('_year_end_history')
        if history is None:
            history = self.game.__dict__['_year_end_history'] = {}
        return history

    def _record_year_end_history(self, finished_year: int, ridership_data: dict, line_profits: dict) -> None:
        history = self._year_end_history()
        history[int(finished_year)] = {
            'ridership': {k: int(v or 0) for k, v in (ridership_data or {}).items()},
            'profit': {k: int(v or 0) for k, v in (line_profits or {}).items()},
        }
        for old in sorted(history)[:-40]:  # keep the last 40 years
            del history[old]

    def get_line_ridership_for_year(self, line: Line, year: int) -> int:
        """Total riders on this line for a calendar year (year-end record, else cache)."""
        year = int(year)
        line_name = self.get_line_name(line)
        cached_year = getattr(line, '_cached_ridership_year', None)
        if year < self.get_year():
            # Finished years are read from the year-end record and never re-simulated here:
            # this is called from the UI thread for every line and station label.
            record = self._year_end_history().get(year)
            if record is not None:
                return int(record['ridership'].get(line_name, 0) or 0)
            if cached_year == year:
                return int(getattr(line, '_cached_ridership', 0) or 0)
            return 0  # no record (e.g. a save from before records were kept)
        if cached_year == year:
            return int(getattr(line, '_cached_ridership', 0) or 0)
        if self._cached_ridership_data is not None and self._cached_ridership_year == year:
            return int(self._cached_ridership_data.get(line_name, 0) or 0)
        return 0

    def _build_job_line_name(self, build: dict) -> str | None:
        """Line name for a line or line_extension build-queue / completed job."""
        btype = build.get('type')
        data = build.get('data') or {}
        if btype == 'line':
            return data.get('name')
        if btype == 'line_extension':
            return data.get('line_name')
        return None

    def was_line_fully_built_at_end_of_year(self, line_name: str, year: int) -> bool:
        """True if the line had no pending line/line_extension work at end of calendar year."""
        if not line_name:
            return False
        year = int(year)
        current_year = self.get_year()

        for build in self.get_build_queue():
            if build.get('type') not in ('line', 'line_extension'):
                continue
            if self._build_job_line_name(build) == line_name:
                return False

        for job in self.get_completed_jobs() or []:
            if job.get('type') not in ('line', 'line_extension'):
                continue
            jname = self._build_job_line_name(job)
            if jname != line_name:
                continue
            if int(job.get('completed_year', current_year)) > year:
                return False

        if self.get_map_lines().get(line_name) is None:
            return any(
                int(job.get('completed_year', current_year)) <= year
                for job in (self.get_completed_jobs() or [])
                if job.get('type') == 'line'
                and (job.get('data') or {}).get('name') == line_name
            )
        return True

    def get_line_previous_year_ridership(self, line: Line) -> int:
        """Total riders on this line in the previous calendar year."""
        prev_year = self.get_year() - 1
        if prev_year < self.get_timeline_start_year():
            return 0
        line_name = self.get_line_name(line)
        if not self.was_line_fully_built_at_end_of_year(line_name, prev_year):
            return 0
        return self.get_line_ridership_for_year(line, prev_year)

    def get_line_profit_for_year(self, line: Line, year: int) -> int:
        """Fare profit for a line in a calendar year (cached year-end value or ridership replay)."""
        year = int(year)
        if year < self.get_timeline_start_year():
            return 0
        line_name = self.get_line_name(line)
        if not self.was_line_fully_built_at_end_of_year(line_name, year):
            return 0
        if year < self.get_year():
            record = self._year_end_history().get(year)
            if record is not None and line_name in record['profit']:
                return int(record['profit'][line_name] or 0)
        if hasattr(line, '_cached_profit') and hasattr(line, '_cached_profit_year'):
            if int(line._cached_profit_year) == year:
                return int(line._cached_profit or 0)
        if year < self.get_year():
            return 0  # finished year without a record for this line: never re-simulate
        saved_profit = int(getattr(line, 'profit', 0) or 0)
        saved_ridership = getattr(line, '_cached_ridership', None)
        saved_ridership_year = getattr(line, '_cached_ridership_year', None)
        try:
            ridership_data = self.get_ridership_data_for_year(year)
            return int(self.calculate_line_profit_with_ridership(line, year, ridership_data) or 0)
        finally:
            line.profit = saved_profit
            if saved_ridership is None:
                if hasattr(line, '_cached_ridership'):
                    delattr(line, '_cached_ridership')
            else:
                line._cached_ridership = saved_ridership
            if saved_ridership_year is None:
                if hasattr(line, '_cached_ridership_year'):
                    delattr(line, '_cached_ridership_year')
            else:
                line._cached_ridership_year = saved_ridership_year

    def get_line_previous_year_profit(self, line: Line) -> int:
        """Fare profit earned by this line in the previous calendar year."""
        prev_year = self.get_year() - 1
        if prev_year < self.get_timeline_start_year():
            return 0
        riders = self.get_line_previous_year_ridership(line)
        profit = self.get_line_profit_for_year(line, prev_year)
        if riders <= 0 and profit > 0:
            return 0
        return profit

    def get_station_ridership_for_year(self, station, year: int) -> int:
        """Estimated annual passenger throughput at a station for one calendar year."""
        line_names = getattr(station, 'line_names', None) or (
            station.get_line_names() if hasattr(station, 'get_line_names') else []
        )
        if not line_names:
            return 0
        year = int(year)
        map_lines = self.get_map_lines() or {}
        total = 0
        for line_name in line_names:
            line = map_lines.get(line_name)
            if not line:
                continue
            passengers = int(self.get_line_ridership_for_year(line, year) or 0)
            stations = (
                line.get_stations() if hasattr(line, 'get_stations')
                else (getattr(line, 'stations', None) or [])
            )
            share = max(1, len(stations))
            total += passengers // share
        return int(total)

    def get_station_previous_year_ridership(self, station) -> int:
        """Estimated station throughput in the previous calendar year."""
        prev_year = self.get_year() - 1
        if prev_year < self.get_timeline_start_year():
            return 0
        return self.get_station_ridership_for_year(station, prev_year)

    def get_station_fare_profit_for_year(self, station, year: int) -> int:
        """Share of serving lines' fare profit attributed to this station for one calendar year."""
        line_names = getattr(station, 'line_names', None) or (
            station.get_line_names() if hasattr(station, 'get_line_names') else []
        )
        if not line_names:
            return 0
        year = int(year)
        map_lines = self.get_map_lines() or {}
        total = 0
        for line_name in line_names:
            line = map_lines.get(line_name)
            if not line:
                continue
            profit = int(self.get_line_profit_for_year(line, year) or 0)
            stations = (
                line.get_stations() if hasattr(line, 'get_stations')
                else (getattr(line, 'stations', None) or [])
            )
            share = max(1, len(stations))
            total += profit // share
        return int(total)

    def get_station_previous_year_fare_profit(self, station) -> int:
        """This station's share of fare profit from the previous calendar year."""
        prev_year = self.get_year() - 1
        if prev_year < self.get_timeline_start_year():
            return 0
        return self.get_station_fare_profit_for_year(station, prev_year)

    @staticmethod
    def displayed_riders(riders) -> int:
        """Rider count as shown to players (simulated riders x RIDERSHIP_DISPLAY_MULTIPLIER)."""
        from game_presets import RIDERSHIP_DISPLAY_MULTIPLIER
        return int(riders or 0) * int(RIDERSHIP_DISPLAY_MULTIPLIER)

    def get_line_last_year_display_stats(self, line: Line) -> dict:
        """Previous calendar year ridership and fare profit for map/UI display."""
        prev_year = int(self.get_year()) - 1
        if prev_year < self.get_timeline_start_year() or line is None:
            return {
                'last_year': None,
                'last_year_ridership': 0,
                'last_year_fare_profit': 0,
            }
        return {
            'last_year': prev_year,
            'last_year_ridership': self.displayed_riders(self.get_line_previous_year_ridership(line)),
            'last_year_fare_profit': int(self.get_line_previous_year_profit(line) or 0),
        }

    def get_line_schedule_display(self, line: Line) -> str:
        """Public schedule summary (peak / off-peak / night trains per hour)."""
        from game_presets import format_schedule_frequency

        sched = getattr(line, 'schedule', None) or {}
        return (
            f"Peak: {format_schedule_frequency(sched.get('peak', 0))} · "
            f"Off peak: {format_schedule_frequency(sched.get('off_peak', 0))} · "
            f"Night: {format_schedule_frequency(sched.get('night', 0))}"
        )

    def get_station_last_year_display_stats(self, station) -> dict:
        """Previous calendar year throughput and fare profit share for map/UI display."""
        prev_year = int(self.get_year()) - 1
        if prev_year < self.get_timeline_start_year() or station is None:
            return {
                'last_year': None,
                'last_year_ridership': 0,
                'last_year_fare_profit': 0,
            }
        return {
            'last_year': prev_year,
            'last_year_ridership': self.displayed_riders(self.get_station_previous_year_ridership(station)),
            'last_year_fare_profit': int(self.get_station_previous_year_fare_profit(station) or 0),
        }

    def calc_player_trainset_maintenance_for_year(self, player: Player, current_year: int) -> int:
        total = 0
        for trainset in player.get_trainsets():
            maint_cost = trainset.get_maitnance_costs(int(current_year))
            if maint_cost is not None:
                total += maint_cost
        return int(total) if total else 0

    def calc_player_line_maintenance_for_year(self, player: Player, current_year: int) -> int:
        total = 0
        for line_name in player.get_line_names():
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line:
                total += self.calc_line_maintenance(line, int(current_year))
        total = max(0, int(total) - self.calc_player_station_merge_maintenance_savings(player, int(current_year)))
        return int(total) if total else 0

    def calc_player_profit_for_financial_summary(self, player: Player, current_year: int) -> int:
        """Profit for finance projections; includes lines built this turn via live estimate."""
        game_year = self.get_year()
        cy = int(current_year)
        total_profit = 0
        for line_name in player.get_line_names():
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if not line:
                continue
            cached = self._line_profit_from_cache(line, cy)
            if cached is not None:
                total_profit += cached
                continue
            if cy == game_year or cy == game_year + 1:
                total_profit += self.calc_line_profit(line, cy)
            else:
                total_profit += self.calc_line_profit(line, game_year)
        return int(total_profit)
    
    def extend_line_route_from_beginning(self, line: Line, additional_points: list):
        line.extend_route_from_beginning(additional_points)
        self.enforce_line_schedule_limits(line)

    def extend_line_route_from_end(self, line: Line, additional_points: list):
        line.extend_route_from_end(additional_points)
        self.enforce_line_schedule_limits(line)

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
            elif build.get("type") in ("line_extension", "line_upgrade", "line_repair"):
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

    def _iter_player_stations_with_name(
        self,
        player: Player,
        station_name: str,
        exclude_station=None,
    ):
        """Yield built Station objects owned by player with the given name (deduplicated)."""
        nk = _norm_display_name(station_name)
        if not nk:
            return
        seen = set()
        for ln in player.get_line_names() or []:
            line = self.get_map_lines().get(ln)
            if not line:
                continue
            stations = line.get_stations() if hasattr(line, "get_stations") else (getattr(line, "stations", None) or [])
            for st in stations:
                if st is exclude_station or id(st) in seen:
                    continue
                if _norm_display_name(self.get_station_name(st)) != nk:
                    continue
                seen.add(id(st))
                yield st

    def _iter_player_station_sites_with_name(
        self,
        player: Player,
        station_name: str,
        exclude_station=None,
        exclude_pending_station_build=None,
    ):
        """Yield (x, y) for built or queued stops owned by player with the given name."""
        for st in self._iter_player_stations_with_name(
            player, station_name, exclude_station=exclude_station,
        ):
            sx = float(st.get_x() if hasattr(st, "get_x") else getattr(st, "x", 0))
            sy = float(st.get_y() if hasattr(st, "get_y") else getattr(st, "y", 0))
            yield sx, sy
        nk = _norm_display_name(station_name)
        if not nk:
            return
        for build in self.game.build_queue or []:
            if build is exclude_pending_station_build:
                continue
            if build.get("type") != "station" or build.get("player") != player:
                continue
            data = build.get("data") or {}
            if _norm_display_name(data.get("name", "")) != nk:
                continue
            try:
                yield float(data.get("x")), float(data.get("y"))
            except (TypeError, ValueError):
                continue

    def find_player_station_merge_target(
        self,
        player: Player,
        station_name: str,
        x: float,
        y: float,
        exclude_station=None,
        exclude_pending_station_build=None,
        max_distance_m: float = None,
    ):
        """Return nearest built station to merge into (same owner, same name, within merge distance)."""
        from game_presets import STATION_MERGE_MAX_DISTANCE_M

        max_m = float(max_distance_m if max_distance_m is not None else STATION_MERGE_MAX_DISTANCE_M)
        best = None
        best_d = max_m + 1.0
        for st in self._iter_player_stations_with_name(
            player, station_name, exclude_station=exclude_station,
        ):
            sx = float(st.get_x() if hasattr(st, "get_x") else getattr(st, "x", 0))
            sy = float(st.get_y() if hasattr(st, "get_y") else getattr(st, "y", 0))
            d = self._direct_distance_between_stop_positions_m(float(x), float(y), sx, sy)
            if d <= max_m and d < best_d:
                best_d = d
                best = st
        return best

    def station_duplicate_name_allowed_within_distance(
        self,
        player: Player,
        station_name: str,
        x: float,
        y: float,
        exclude_station=None,
        exclude_pending_station_build=None,
        max_distance_m: float = None,
    ) -> bool:
        """True when another stop with the same name is within max_distance_m (default 50 m)."""
        from game_presets import STATION_DUPLICATE_NAME_MAX_DISTANCE_M

        max_m = float(max_distance_m if max_distance_m is not None else STATION_DUPLICATE_NAME_MAX_DISTANCE_M)
        for sx, sy in self._iter_player_station_sites_with_name(
            player,
            station_name,
            exclude_station=exclude_station,
            exclude_pending_station_build=exclude_pending_station_build,
        ):
            if self._direct_distance_between_stop_positions_m(x, y, sx, sy) <= max_m:
                return True
        return False

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
            if x is not None and y is not None:
                if line_names and self.station_transfer_name_reuse_allowed(
                    player, station_name, float(x), float(y), list(line_names),
                ):
                    return
                if self.station_duplicate_name_allowed_within_distance(
                    player,
                    station_name,
                    float(x),
                    float(y),
                    exclude_station=exclude_station,
                    exclude_pending_station_build=exclude_pending_station_build,
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

    def get_line_owner(self, line: Line) -> Player:
        return line.get_owner()

    def is_line_damaged(self, line) -> bool:
        """True while any part of the line is disaster-damaged and not yet repaired."""
        return bool(_get_line_destroyed_edges(line))

    def queue_line_repairs(self, line) -> int:
        """Put a repair job in the owner's construction queue for each damaged stretch of
        ``line`` that is not already queued. Returns how many jobs were added."""
        owner = self.get_line_owner(line)
        if owner is None or self.is_player_eliminated(owner):
            return 0
        line_name = self.get_line_name(line)
        queued = set()
        for build in self.get_build_queue():
            if build.get('type') == 'line_repair' and (build.get('data') or {}).get('line_name') == line_name:
                pts = [_normalize_route_point(p) for p in (build['data'].get('points') or [])]
                queued |= {_normalize_route_edge(a, b) for a, b in zip(pts, pts[1:])}
        added = 0
        for run in _damaged_route_runs(line):
            edges = {_normalize_route_edge(a, b) for a, b in zip(run, run[1:])}
            if edges <= queued:
                continue
            self.add_to_build_queue('line_repair', {
                'line_name': line_name,
                'points': [tuple(p) for p in run],
                'segments': len(run) - 1,
            }, owner, 0)
            queued |= edges
            added += 1
        return added
    
    def get_line_vehicles(self, line: Line) -> list[Trainset]:
        return line.get_vehicles()
    
    def get_line_name(self, line: Line) -> str:
        return line.get_name()
    
    def get_map_lines(self) -> dict:
        return self.game.get_map().get_lines_by_name()
    
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
                or (build.get('type') in ('line_extension', 'line_repair') and data.get('line_name') == line_name)
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
        line_maintenance = max(
            0,
            int(line_maintenance) - self.calc_player_station_merge_maintenance_savings(player, current_year),
        )
        
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
        balance_before = player.get_money()
        
        if not player.is_player_eliminated():
            # Apply year-end net atomically so bankruptcy is not triggered by intermediate negatives.
            if net > 0:
                self.add_player_money(player, net, reason='Year-end finances')
            elif net < 0:
                self.deduct_player_money(player, -net, reason='Year-end finances')
        
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
            'balance_before': balance_before,
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
        cy = int(current_year)
        player_profit = self.calc_player_profit_for_financial_summary(player, cy)

        trainset_maintenance = self.calc_player_trainset_maintenance_for_year(player, cy)
        resource_charge = self.game.calc_resource_costs(player, cy)
        line_maintenance = self.calc_player_line_maintenance_for_year(player, cy)

        laws = getattr(self.game, '_government_laws', {})
        maintenance_relief = laws.get('maintenance_relief', {})
        player_id = self.get_player_id(player)
        if player_id in maintenance_relief:
            factor = maintenance_relief[player_id]
            trainset_maintenance = int(trainset_maintenance * factor)
            line_maintenance = int(line_maintenance * factor)

        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, cy, player_profit)
        # A summary only reads costs: advancing workers here re-rolled their demands and could
        # start strikes mid-year, without the warning a strike always gets first.
        worker_costs, is_on_strike = self.game.calc_worker_costs(player, cy, advance=False)

        total_deductions = trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs
        grants = laws.get('grants', {})
        is_favored = grants.get(player_id, 0) > 0
        government_grant = self._compute_transit_subsidy(player, cy, is_favored)
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
    
    def check_and_eliminate_player(self, player, balance_before: int = None, net_change: int = None):
        """Eliminate only when final balance after net change is below $0."""
        return self.game.check_and_eliminate_player(
            player, balance_before=balance_before, net_change=net_change,
        )
    
    def get_newly_available_vehicles(self, old_year: int, new_year: int):
        """Get newly available vehicles between two years"""
        return self.game.get_newly_available_vehicles(old_year, new_year)
    
    def check_agency_lawsuits(self, current_year: int):
        """Check for agency lawsuits"""
        return self.game.check_agency_lawsuits(current_year)
    
    def remove_player_trainset(self, player: Player, trainset):
        """Remove a trainset from a player"""
        player.remove_trainset(trainset)
    
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
        self.fit_maintenance_assignments_to_rentals(player)
    
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
        drafted = getattr(player, '_war_drafted_workers', None)
        if drafted is None:
            drafted = player._war_drafted_workers = {}
        drafted[wn] = drafted.get(wn, 0) + 1
        self.marketplace_add_worker_supply(wn, 1)
        if wn == ENGINEER.name:
            self.check_and_unassign_trainsets_for_engineers(player, self.get_year())
        return True

    def _rehire_drafted_workers_after_war(self, year: int) -> list:
        """When a war ends, AI players hire back the workers the draft took from them, as many
        as they can afford (the yearly cost of the extra staff must fit in their cash). Every
        player's draft record is cleared: the next war starts a new one."""
        from worker import CONSTRUCTION_WORKER, ENGINEER, LAWYER, MAINTENANCE_WORKER

        types = {w.name: w for w in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER)}
        rehired = []
        for player in self.get_players():
            drafted = getattr(player, '_war_drafted_workers', None) or {}
            player._war_drafted_workers = {}
            if not drafted or not getattr(player, 'is_ai', False) or self.is_player_eliminated(player):
                continue
            for name, count in drafted.items():
                worker_type = types.get(name)
                qty = int(count)
                while worker_type is not None and qty > 0:
                    if self.calculate_worker_rental_cost(worker_type, qty, year) <= self.get_player_money(player):
                        try:
                            self.rent_workers_for_player(player, worker_type, qty, year)
                            rehired.append({'player_name': self.get_player_name(player), 'worker': name, 'count': qty})
                        except ValueError:
                            pass  # e.g. a strike in progress
                        break
                    qty -= 1
        return rehired

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
                out['rehired'] = self._rehire_drafted_workers_after_war(new_year)
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
        self.fit_maintenance_assignments_to_rentals(player)
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
    
    def player_maintenance_workers_rented(self, player: Player) -> int:
        from worker import MAINTENANCE_WORKER
        rental = self.get_player_worker_rental(player, MAINTENANCE_WORKER.name)
        return int(getattr(rental, 'quantity', 0) or 0) if rental else 0

    def player_maintenance_workers_assigned(self, player: Player, exclude_line: str = None) -> int:
        lines = self.get_map_lines() or {}
        return sum(
            int(getattr(lines[n], 'maintenance_workers', 0) or 0)
            for n in (player.get_line_names() or []) if n in lines and n != exclude_line
        )

    def player_maintenance_workers_free(self, player: Player) -> int:
        return max(0, self.player_maintenance_workers_rented(player) - self.player_maintenance_workers_assigned(player))

    def set_line_maintenance_workers(self, player: Player, line_name: str, count: int) -> dict:
        """Assign ``count`` of the player's rented maintenance workers to one of its lines.

        Each worker raises the line's quality by 0.1 (up to 3.0), which draws more riders.
        """
        lines = self.get_map_lines() or {}
        line = lines.get(line_name)
        if line is None or line_name not in (player.get_line_names() or []):
            return {'success': False, 'message': f"You do not own a built line named '{line_name}'."}
        count = max(0, int(count))
        available = self.player_maintenance_workers_rented(player) - self.player_maintenance_workers_assigned(
            player, exclude_line=line_name,
        )
        if count > available:
            return {'success': False, 'message': f"Only {max(0, available)} maintenance worker(s) are free. "
                                                 "Rent more in the Workers tab."}
        line.maintenance_workers = count
        if hasattr(line, 'update_quality'):
            line.update_quality()
            line._quality_updated_year = self.get_year()
        self.invalidate_ridership_cache()
        return {'success': True, 'line_name': line_name, 'maintenance_workers': count,
                'free': self.player_maintenance_workers_free(player)}

    def fit_maintenance_assignments_to_rentals(self, player: Player) -> None:
        """Release line assignments when the player has fewer maintenance workers than assigned."""
        excess = self.player_maintenance_workers_assigned(player) - self.player_maintenance_workers_rented(player)
        if excess <= 0:
            return
        lines = self.get_map_lines() or {}
        for name in sorted(player.get_line_names() or [],
                           key=lambda n: -int(getattr(lines.get(n), 'maintenance_workers', 0) or 0)):
            line = lines.get(name)
            if line is None or excess <= 0:
                continue
            take = min(excess, int(getattr(line, 'maintenance_workers', 0) or 0))
            line.maintenance_workers -= take
            excess -= take

    def auto_assign_maintenance_workers(self, player: Player) -> None:
        """Spread the player's rented maintenance workers evenly over its built lines (AI)."""
        self.fit_maintenance_assignments_to_rentals(player)
        lines = self.get_map_lines() or {}
        own = [lines[n] for n in sorted(player.get_line_names() or []) if n in lines]
        if not own:
            return
        total = self.player_maintenance_workers_rented(player)
        share, extra = divmod(total, len(own))
        for i, line in enumerate(own):
            line.maintenance_workers = share + (1 if i < extra else 0)

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
        return self.get_line_effective_service_frequency_per_year(line, fastest_speed)
    
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
        sx = float(station.get_x()) if hasattr(station, 'get_x') else float(getattr(station, 'x', 0))
        sy = float(station.get_y()) if hasattr(station, 'get_y') else float(getattr(station, 'y', 0))
        line_names = (
            station.get_line_names() if hasattr(station, 'get_line_names')
            else list(getattr(station, 'line_names', []) or [])
        )
        for p in self._station_players(station):
            self.assert_station_name_unique_for_player(
                p, new_name, exclude_station=station, x=sx, y=sy, line_names=line_names,
            )
        station.name = new_name
    
    def get_station_name(self, station) -> str:
        """Get station name"""
        return station.name

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
        marketplace.num_players = len(self.get_players())
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
        from vehicle import Locomotive
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
        for _p in self.get_players():
            self.fit_maintenance_assignments_to_rentals(_p)
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
        self.enforce_all_line_schedule_limits()
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
        # Settled figures for the year that just ended ("last year" in every display).
        self._record_year_end_history(
            old_year, ridership_data,
            {name: int(getattr(line, '_cached_profit', 0) or 0) for name, line in map_lines.items()},
        )
        
        # Process finances for all players (now uses cached profit values only - fast)
        player_finances = []
        strike_messages = []
        loan_reports = []
        for player in all_players:
            finances = self.process_player_finances(player, new_year)
            if not self.is_player_eliminated(player):
                import loans as _loans
                report = _loans.process_year_end(self, player)
                finances['loan_payments'] = report['payments']
                if report['seized']:
                    loan_reports.append({'player': player, 'seized': report['seized']})
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

        lawsuit_result = self.check_agency_lawsuits(new_year)
        
        self.handle_public_ta(new_year)
        government_funding = self.distribute_government_funding(new_year)
        self.game.apply_lobby_effects(new_year)
        self._complete_law_drafts(new_year)

        # Eliminate only after all year-end money changes (finances, funding, lobby penalties).
        for player in all_players:
            if not self.is_player_eliminated(player):
                was_eliminated = self.check_and_eliminate_player(player)
                if was_eliminated:
                    continue
                self.check_and_unassign_trainsets_for_engineers(player, new_year)

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
            'loan_seizures': loan_reports,
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
        Company name becomes a merge of both names (e.g. Pennsylvania Railroad + Northern Central -> Penn Central).
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
        from ai_specialties import assign_specialty
        assign_specialty(self.game, new_player)
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
            return LOBBY_SUCCESS_MIN_CHANCE
        power = self.get_lobby_power_factor(option_id or 'drop_excuse')
        ref = 10000.0 * self.get_lobby_inflation_factor() * power * LOBBY_SUCCESS_REF_COST_MULTIPLIER
        return min(0.95, LOBBY_SUCCESS_MIN_CHANCE + 0.80 * min(1.0, amount / ref))

    def _law_target_count_chance_modifier(self, law_type: str, num_targets: int) -> float:
        """Extra targets help beneficial laws and hurt harmful ones."""
        extra = max(0, int(num_targets or 0) - 1)
        if extra <= 0:
            return 0.0
        if law_type in _BAD_LAW_TYPES:
            return -extra * LAW_TARGET_EXTRA_PENALTY_PER
        if law_type in _GOOD_MULTI_TARGET_LAW_TYPES:
            return extra * LAW_TARGET_EXTRA_BONUS_PER
        return 0.0

    def _compute_law_committee_pass_chance(
        self,
        law_type: str,
        favorable: float,
        unfavorable: float,
        tiebreaker_favorable: bool,
        num_lawyers: int,
        num_targets: int = 0,
    ) -> float:
        chance = (
            LAW_COMMITTEE_BASE_CHANCE
            + favorable * LAW_COMMITTEE_FAVORABLE_PER_SEAT
            + unfavorable * LAW_COMMITTEE_UNFAVORABLE_PER_SEAT
        )
        if favorable == unfavorable and tiebreaker_favorable:
            chance += LAW_COMMITTEE_TIEBREAKER_BONUS
        chance += min(
            LAW_COMMITTEE_LAWYER_BONUS_MAX,
            max(0, num_lawyers - 1) * LAW_COMMITTEE_LAWYER_BONUS_PER,
        )
        chance += self._law_target_count_chance_modifier(law_type, num_targets)
        return max(0.05, min(0.95, chance))

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
        chance += self._law_target_count_chance_modifier(option_id, len(targets))
        chance = max(0.05, min(0.95, chance))
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
        # For laws that harm their targets (gov_attack, audit_target, etc.): the seats of every
        # company that is NOT a target help pass it; the targets' seats oppose it.
        beneficiaries = self._get_beneficiaries_from_targets(law_type, draft_targets, player)
        if law_type in HARMFUL_LAW_TYPES and beneficiaries:
            target_ids = {self.get_player_id(p) for p in beneficiaries if p is not None}
            helper_ids = {
                self.get_player_id(p) for p in self.get_active_players()
                if self.get_player_id(p) not in target_ids
            }
            favorable = gov.get_favorable_influence_for_players(helper_ids)
            unfavorable = gov.get_total_influence() - favorable
            tiebreaker = gov._tiebreaker in helper_ids
        elif law_type in HARMFUL_LAW_TYPES:
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

        num_targets = len(draft_targets or [])
        chance = self._compute_law_committee_pass_chance(
            law_type,
            favorable,
            unfavorable,
            tiebreaker,
            num_lawyers,
            num_targets=num_targets,
        )

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
        Uses tile system if available, otherwise falls back to preset/OSM methods.
        
        Args:
            x: Grid x coordinate
            y: Grid y coordinate
            game_map: Map object
            water_cache: Dictionary cache for water checks (key: (x, y), value: bool)
            
        Returns:
            True if point is in water, False otherwise
        """
        
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
        """True if any part of segment p1→p2 is over water (disaster repair / elevation UI).

        Uses preset/tile cache only (skip_osm=True) so UI elevation prompts never block on Overpass.
        """
        def _pt(p):
            if hasattr(p, 'x') and hasattr(p, 'y'):
                return float(p.x), float(p.y)
            return float(p[0]), float(p[1])
        x1, y1 = _pt(p1)
        x2, y2 = _pt(p2)
        return _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=True)

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

        When any part of a segment is over open water (not a bridge), street level is
        disallowed; above-ground and underground remain available (default above-ground).
        """
        if self.segment_is_over_water_not_bridge(p1, p2):
            return list(_WATER_SEGMENT_ELEVATIONS), 'above_ground'
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
            if new_points and route:
                specs.append({
                    'label': (
                        f"Point 1 ({float(new_points[0][0]):.1f}, {float(new_points[0][1]):.1f})"
                        " — connection from line"
                    ),
                    'p1': route[0],
                    'p2': new_points[0],
                })
            for i in range(1, len(new_points)):
                p1, p2 = new_points[i - 1], new_points[i]
                specs.append({
                    'label': f"Point {i + 1} ({float(p2[0]):.1f}, {float(p2[1]):.1f})",
                    'p1': p1,
                    'p2': p2,
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
            ferry_cost = max(15, int(total_length * FERRY_LENGTH_COST_PER_UNIT * LINE_COST_MULTIPLIER))
            return {
                'cost': self._scale_construction_cost(ferry_cost),
                'length': total_length,
                'points_count': len(points),
                'elevation_levels': list(elevation_levels)
            }
        if is_bus_line:
            # Bus lines are assumed to be on roads (route validation enforces this on supported maps).
            # Use the road base cost and apply the bus discount.
            bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * LINE_COST_MULTIPLIER))
            return {
                'cost': self._scale_construction_cost(bus_cost),
                'length': total_length,
                'points_count': len(points),
                'elevation_levels': list(elevation_levels)
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

            # Helper to check if a point is on a road (on maps with road data, check if the road exists at the current year)
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
                    elif _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=False):
                        segment_in_water = True
                        if game_map.tile_system:
                            if game_map.tile_system._preset_only_water:
                                water_tile_count = max(1, int(segment_length * 2))
                            else:
                                water_tile_count = max(
                                    1,
                                    game_map.tile_system.get_water_segment_count(x1, y1, x2, y2),
                                )
                        else:
                            water_tile_count = 1
                    
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
                    if not is_ferry_line and not (segment_in_water and not on_bridge_or_tunnel):
                        base_cost *= _track_population_cost_factor(game_map, mid_x, mid_y)
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
                'cost': self._scale_construction_cost(total_cost),
                'length': total_length,
                'points_count': len(points),
                'elevation_levels': list(elevation_levels)
            }
        finally:
            if game_map.tile_system:
                game_map.tile_system._preset_only_water = False
    
    def _validate_ferry_route(self, points: list) -> dict:
        """Validate ferry route: all points and segments within FERRY_MAX_DISTANCE_FROM_WATER_M of water."""
        game_map = self.game.get_map()
        max_m = int(FERRY_MAX_DISTANCE_FROM_WATER_M)

        if not points:
            raise ValueError("Ferry route must have at least one point")
        if len(points) < 2:
            raise ValueError("Ferry route must have at least two points.")

        for i, (x, y) in enumerate(points):
            if not _ferry_point_near_water(game_map, float(x), float(y)):
                raise ValueError(
                    f"Ferry routes must be within {max_m}m of water. Point {i + 1} at ({float(x):.1f}, {float(y):.1f}) "
                    f"is too far from water (on land)."
                    )
            
        for i in range(len(points) - 1):
            x1, y1 = float(points[i][0]), float(points[i][1])
            x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
            if not _ferry_segment_near_water(game_map, x1, y1, x2, y2):
                raise ValueError(
                    f"Ferry routes must stay within {max_m}m of water. Segment {i + 1} from ({x1:.1f}, {y1:.1f}) to "
                    f"({x2:.1f}, {y2:.1f}) is too far from water."
                )

        return {'success': True}

    def _require_ferry_terminal_near_coast(self, game_map, grid_x: float, grid_y: float) -> None:
        """Ferry stations must be within FERRY_MAX_DISTANCE_FROM_WATER_M of water."""
        ts = getattr(game_map, "tile_system", None)
        if not ts or not hasattr(ts, "is_point_near_water"):
            if not _ferry_point_near_water(game_map, float(grid_x), float(grid_y)):
                max_m = int(FERRY_MAX_DISTANCE_FROM_WATER_M)
                raise ValueError(
                    f"Ferry terminals must be within {max_m} m of the coast or navigable water."
                )
            return
        gx, gy = float(grid_x), float(grid_y)
        if not ts.is_point_near_water(gx, gy, FERRY_MAX_DISTANCE_FROM_WATER_M, skip_osm=False):
            max_m = int(FERRY_MAX_DISTANCE_FROM_WATER_M)
            raise ValueError(
                f"Ferry terminals must be within {max_m} m of the coast or navigable water."
            )

    def _require_station_not_in_deep_water(self, game_map, grid_x: float, grid_y: float) -> None:
        """Stations in water must be within STATION_MAX_DISTANCE_FROM_LAND_M of land."""
        preset_name = getattr(game_map, "map_preset_name", None)
        from map_preset_loader import preset_has_water_areas
        if not (preset_name and preset_has_water_areas(preset_name)):
            return
        gx, gy = float(grid_x), float(grid_y)
        max_m = float(STATION_MAX_DISTANCE_FROM_LAND_M)
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
        bonus = 0.0
        for _station, line, walk_dist in nearby:
            if not line or not line.get_stations() or not line.get_vehicles():
                continue
            walk_factor = max(0.15, 1.0 - float(walk_dist) / 5.0)
            other_fare = min(float(getattr(line, "fare", fare_cap) or fare_cap), float(fare_cap))
            station_count = max(2, len(line.get_stations()))
            demand_proxy = float(station_count) * 180.0 * walk_factor
            # Transfer trips pay each line's fare; model extra ridership on the planned line.
            leg_value = demand_proxy * other_fare * 0.12
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

    def first_bus_year(self):
        """Year the first bus is invented (None if the game has no buses)."""
        from vehicle_presets import get_vehicle_registry
        years = [int(y) for _v, y in get_vehicle_registry().get_buses_by_year() if y]
        return min(years) if years else None

    def bus_lines_available(self, year: int = None) -> bool:
        """True once a bus exists to run a bus line (the first bus's invention year)."""
        first = self.first_bus_year()
        return first is not None and int(self.get_year() if year is None else year) >= first

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
        
        # OPTIMIZATION: Early validation - check inputs first before expensive operations
        if not points or len(points) < 2:
            raise ValueError("Line must have at least 2 points")
        _require_ai_route_not_returning(player, points, self.game.get_map())
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
                    'invalid_segment_indices': street_check.get('invalid_segment_indices') or [],
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
            elevation_levels = list(elev)
        
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
            'cost': self._scale_construction_cost(total_cost),
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
    
    def create_station_complete(self, station_name: str, x: float, y: float, player: Player, line_names: list[str]) -> dict:
        """Complete station creation operation. Returns dict with result info"""

        station_name = (station_name or "").strip()
        x, y = float(x), float(y)
        merge_target = self.find_player_station_merge_target(player, station_name, x, y)
        if merge_target is not None:
            x, y = float(merge_target.get_x()), float(merge_target.get_y())
            existing_lines = set(merge_target.get_line_names() if hasattr(merge_target, 'get_line_names') else getattr(merge_target, 'line_names', []))
            line_names = [ln for ln in line_names if ln not in existing_lines]
            if not line_names:
                raise ValueError(
                    f"Station '{station_name}' already serves all selected lines."
                )

        self.assert_station_name_unique_for_player(
            player, station_name, x=x, y=y, line_names=line_names,
        )
        
        # Get construction workers (can be 0 - jobs can start without workers, they just won't progress)
        construction_workers = self.get_player_construction_worker_count(player)
        
        # Calculate cost (half-price extension when merging into nearby same-name station)
        base_cost = self.calculate_station_cost(
            len(line_names), x, y, player=player, station_name=station_name,
        )
        
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

        _require_ai_station_not_in_water(game_map, player, x, y)
        self._require_station_not_in_deep_water(game_map, x, y)

        if merge_target is None:  # merging joins an existing stop: spacing rules do not apply
            self.assert_ai_stop_minimum_direct_separation(player, x, y)

        # Deduct money
        self.deduct_player_money(player, base_cost, reason='Station build')
        self.invalidate_ridership_cache()
        
        # Calculate build time based on construction workers
        build_data = {
            'name': station_name, 'x': x, 'y': y, 'line_names': line_names,
            'merge': merge_target is not None,
        }
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
            'remaining_money': self.get_player_money(player),
            'merged': merge_target is not None,
        }
    
    def create_station_immediate(self, station_name: str, x: float, y: float, player: Player, line_names: list[str]) -> dict:
        """Create a station immediately (no build queue). Used for OSM map Add Station flow.
        Uses calculate_station_cost for pricing (including half-price merge extensions within 20m)."""
        from station import Station
        
        station_name = (station_name or "").strip()
        x, y = float(x), float(y)
        merge_target = self.find_player_station_merge_target(player, station_name, x, y)
        if merge_target is not None:
            x, y = float(merge_target.get_x()), float(merge_target.get_y())
            existing_lines = set(
                merge_target.get_line_names() if hasattr(merge_target, 'get_line_names')
                else getattr(merge_target, 'line_names', [])
            )
            line_names = [ln for ln in line_names if ln not in existing_lines]
            if not line_names:
                raise ValueError(
                    f"Station '{station_name}' already serves all selected lines."
                )

        self.assert_station_name_unique_for_player(
            player, station_name, x=x, y=y, line_names=line_names,
        )
        
        cost = self.calculate_station_cost(
            len(line_names), x, y, player=player, station_name=station_name,
        )
        
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

        _require_ai_station_not_in_water(game_map, player, x, y)
        self._require_station_not_in_deep_water(game_map, x, y)

        self.assert_ai_stop_minimum_direct_separation(player, x, y)

        self.deduct_player_money(player, cost, reason='Station build')
        self.invalidate_ridership_cache()

        if merge_target is not None:
            for line_name in line_names:
                self.add_station_to_line(player, line_name, merge_target)
            return {
                'station': merge_target,
                'cost': cost,
                'line_names': line_names,
                'coordinates': (x, y),
                'remaining_money': self.get_player_money(player),
                'merged': True,
            }
        
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
            'remaining_money': self.get_player_money(player),
            'merged': False,
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
        
        existing_route = self.get_line_route_including_extensions(line_name)
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
            first_new_x, first_new_y = additional_points[0]
            total_length += math.hypot(first_x - first_new_x, first_y - first_new_y)
        
        if len(additional_points) > 1:
            for i in range(len(additional_points) - 1):
                x1, y1 = additional_points[i]
                x2, y2 = additional_points[i+1]
                total_length += math.hypot(x2 - x1, y2 - y1)

        LINE_COST_MULTIPLIER = 1.5
        is_ferry_line, is_bus_line = self.get_line_build_type_flags(line_name)
        if is_ferry_line:
            # Fast cost preview — full ferry validation runs in extend_line_complete.
            ferry_cost = max(15, int(total_length * FERRY_LENGTH_COST_PER_UNIT * LINE_COST_MULTIPLIER))
            return {
                'cost': self._scale_construction_cost(ferry_cost),
                'length': total_length,
                'points_count': len(additional_points),
                'elevation_levels': list(elevation_levels),
            }
        if is_bus_line:
            bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * LINE_COST_MULTIPLIER))
            return {
                'cost': self._scale_construction_cost(bus_cost),
                'length': total_length,
                'points_count': len(additional_points),
                'elevation_levels': list(elevation_levels),
            }

        # Fast cost preview — preset-only water, no OSM tile queries (matches calculate_line_cost).
        # Full OSM validation runs in extend_line_complete when the user confirms.
        merged_preview_pts = list(existing_route)
        if extend_from_end:
            merged_preview_pts.extend(additional_points)
        else:
            merged_preview_pts = (
                _extension_points_in_route_order(additional_points, False) + merged_preview_pts
            )

        if game_map.tile_system:
            game_map.tile_system._preset_only_water = True
        try:
            if game_map.tile_system:
                game_map.tile_system.pre_cache_route_tiles(
                    merged_preview_pts, proximity_buffer_m=0.0, max_osm_queries=0,
                )

            road_cache = {}
            bridge_cache = {}
            water_cache = {}

            def is_point_in_water(x, y):
                return self._is_point_in_water_cached(x, y, game_map, water_cache)

            def is_point_on_road(x, y):
                cache_key = (int(round(x)), int(round(y)))
                if cache_key in road_cache:
                    return road_cache[cache_key]
                result = False
                preset_name = getattr(game_map, 'map_preset_name', None)
                if preset_name:
                    from map_preset_loader import preset_has_road_support, get_preset_is_point_on_road
                    if preset_has_road_support(preset_name):
                        result = get_preset_is_point_on_road(preset_name, x, y, current_year, game_map)
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

            def has_buildings(x, y):
                grid_x, grid_y = int(round(x)), int(round(y))
                pop = game_map.get_population(grid_x, grid_y)
                return pop > 0

            def _segment_cost(x1, y1, x2, y2, elevation: str) -> int:
                seg_length = math.hypot(x2 - x1, y2 - y1)
                mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
                seg_in_water = _route_segment_is_water_crossing(
                    game_map, x1, y1, x2, y2, skip_osm=False,
                )
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
                if seg_in_water and not seg_on_bridge_or_tunnel and elevation == 'street':
                    raise ValueError(
                        f"Cannot build at street level on water. Segment from "
                        f"({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f}) is in water. "
                        "Use 'above_ground' or 'underground' instead."
                    )
                seg_cost = base_cost_per_unit * seg_length
                seg_cost *= elevation_multipliers.get(elevation, 1.0)
                seg_cost *= event_cost_multiplier
                if not (seg_in_water and not seg_on_bridge_or_tunnel):
                    seg_cost *= _track_population_cost_factor(game_map, mid_x, mid_y)
                seg_cost *= _off_street_segment_cost_multiplier(
                    game_map, x1, y1, x2, y2, current_year, is_bus_line=False,
                )
                return int(seg_cost)

            total_cost = 0
            if extend_from_end and existing_route and additional_points:
                last_x, last_y = existing_route[-1]
                first_x, first_y = additional_points[0]
                elevation = elevation_levels[0] if elevation_levels else 'street'
                total_cost += _segment_cost(last_x, last_y, first_x, first_y, elevation)
            elif not extend_from_end and existing_route and additional_points:
                first_x, first_y = existing_route[0]
                first_new_x, first_new_y = additional_points[0]
                elevation = elevation_levels[0] if elevation_levels else 'street'
                total_cost += _segment_cost(first_x, first_y, first_new_x, first_new_y, elevation)

            if len(additional_points) > 1:
                for i in range(len(additional_points) - 1):
                    x1, y1 = additional_points[i]
                    x2, y2 = additional_points[i + 1]
                    if extend_from_end:
                        elevation = elevation_levels[i + 1] if (i + 1) < len(elevation_levels) else 'street'
                    else:
                        elevation = elevation_levels[i] if i < len(elevation_levels) else 'street'
                    total_cost += _segment_cost(x1, y1, x2, y2, elevation)

            total_cost = max(15, total_cost)
            return {
                'cost': self._scale_construction_cost(total_cost),
                'length': total_length,
                'points_count': len(additional_points),
                'elevation_levels': list(elevation_levels),
            }
        finally:
            if game_map.tile_system:
                game_map.tile_system._preset_only_water = False
    
    def extend_line_complete(self, line_name: str, player: Player, additional_points: list, extend_from_end: bool, elevation_levels: list = None, cost_info: dict = None, use_fast_path: bool = False) -> dict:
        """Complete line extension operation. Adds extension to build queue instead of extending immediately.
        Returns dict with result info. use_fast_path: when True and cost_info provided, skip expensive calculate_extend_cost (AI only)."""
        
        line = self.get_map_lines().get(line_name)
        if getattr(player, 'is_ai', False):
            _require_ai_route_not_returning(player, _ai_merged_line_route(
                list(self.get_line_route_including_extensions(line_name) or []),
                additional_points, extend_from_end,
            ), self.get_map())
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
        elevation_levels = list(cost_info['elevation_levels'])  # copy; never mutate caller preview
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
            existing_route=existing_route,
            existing_elevation_levels=(
                _collect_route_elevation_levels_from_build_queue(
                    line_name, self.get_build_queue(),
                )
                if line is None else None
            ),
        )
        if not is_ferry_line:
            # Segments of `merged` that are new (the joint to the old line end included):
            # existing segments are not re-judged against rules they may predate.
            n_new = len(additional_points)
            if extend_from_end:
                new_segments = (max(0, len(merged) - 1 - n_new), len(merged) - 1)
            else:
                new_segments = (0, min(n_new, len(merged) - 1))
            street_check = _validate_land_line_route_for_build(
                game_map, merged, current_year=self.get_year(),
                is_bus_line=is_bus_line, is_ferry_line=False, player=player,
                ai_fast=ai_fast, skip_osm=skip_osm_recheck, for_extension=True,
                elevation_levels=merged_elevs, cost_info=cost_info,
                use_fast_path=use_fast_path, human_cost_previewed=human_cost_previewed,
                new_segments=new_segments, extension_reversed=not extend_from_end,
            )
            if not street_check.get('success'):
                return {
                    'success': False,
                    'error_message': (
                        street_check.get('error_message') or 'Route must follow streets on land.'
                    ),
                    'invalid_segment_indices': street_check.get('invalid_segment_indices') or [],
                    'line_name': line_name,
                    'cost': 0,
                    'build_time': 0,
                    'points_count': len(additional_points),
                }
        
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
        extension_order = self._next_pending_line_extension_order(line_name)
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
                'extension_order': extension_order,
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
        route = list(self.get_line_route_including_extensions(line_name) or [])
        ext_route = list(additional_points)
        if extend_from_end and route:
            ext_route = [route[-1]] + ext_route if route else ext_route
        elif route:
            ext_route = [route[0]] + ext_route
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
    
    def _line_route_with_pending_original_batches(self, line_name: str) -> list:
        """The line's originally planned route: built part + its queued 'line' batches, in order."""
        line = self.get_map_lines().get(line_name)
        route = [_normalize_route_point(p) for p in (self.get_line_route(line) if line else [])]
        batches = []
        for build in self.get_build_queue():
            if build.get('type') != 'line':
                continue
            data = build.get('data') or {}
            if data.get('name') != line_name:
                continue
            pts = data.get('points') or []
            if len(pts) >= 2:
                batches.append((int(data.get('segment_index', 0) or 0), pts))
        for _idx, pts in sorted(batches, key=lambda b: b[0]):
            route = _merge_route_with_completed_build_points(route, pts)
        return route

    def get_line_route_including_extensions(self, line_name: str) -> list:
        """Full route including pending extension segments in the build queue."""
        def _pt(p):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                return (float(p[0]), float(p[1]))
            return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

        # Built part plus the line's own batches still under construction: an extension must
        # attach to the end of the whole planned line, not to wherever construction has reached.
        route = [_pt(p) for p in self._line_route_with_pending_original_batches(line_name)]
        build_queue = self.get_build_queue()

        ext_builds = []
        for qi, build in enumerate(build_queue):
            if build.get('type') != 'line_extension':
                continue
            data = build.get('data', {})
            if data.get('line_name') != line_name:
                continue
            ext_order = int(data.get('extension_order', qi))
            seg_idx = int(data.get('segment_index', 0) or 0)
            ext_builds.append((
                ext_order,
                seg_idx,
                data.get('points', []),
                data.get('extend_from_end', True),
            ))
        ext_builds.sort(key=lambda x: (x[0], x[1]))

        for _ext_order, _seg_idx, ext_pts, extend_from_end in ext_builds:
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

    def get_line_operational_route_portions(self, line) -> list:
        """Built-route connected components; each portion runs independently until gaps fill.

        Memoised: ridership estimates ask for this once per station pair (hundreds of
        thousands of calls per AI turn). The key covers everything the result depends on:
        the route contents, the build queue entries and the line's destroyed edges.
        """
        route = self.get_line_route(line) if line else []
        line_name = self.get_line_name(line) if line else ''
        bq = self.get_build_queue()
        try:
            route_key = tuple(
                (float(p[0]), float(p[1])) if isinstance(p, (list, tuple)) else (float(p.x), float(p.y))
                for p in (route or [])
            )
        except Exception:
            route_key = None
        cache = getattr(self, '_route_portions_cache', None)
        if cache is None:
            cache = self._route_portions_cache = {}
        key = None
        if route_key is not None:
            key = (
                id(line), line_name, route_key,
                tuple(id(b) for b in bq),
                frozenset(getattr(line, '_destroyed_edges', None) or ()),
                frozenset(getattr(line, '_built_long_edges', None) or ()),
            )
            hit = cache.get(key)
            if hit is not None:
                return [list(portion) for portion in hit]
        blocked_edges = self._blocked_line_edges_for_line(line_name, line)
        portions = _split_route_into_connected_portions(
            route, blocked_edges=blocked_edges,
            built_edges=getattr(line, '_built_long_edges', None),
        )
        if key is not None:
            if len(cache) > 512:
                cache.clear()
            cache[key] = [list(portion) for portion in portions]
        return portions

    def line_is_operationally_split(self, line) -> bool:
        """True when incomplete construction leaves multiple operational route portions."""
        return len(self.get_line_operational_route_portions(line)) > 1

    def _incomplete_line_edges_for_line(self, line_name: str) -> set:
        """Undirected edges still under construction for this line."""
        blocked = set()
        if not line_name:
            return blocked
        for build in self.get_build_queue():
            if build.get('type') != 'line':
                continue
            data = build.get('data') or {}
            if data.get('name') != line_name or 'segment_index' not in data:
                continue
            pts = [_normalize_route_point(p) for p in data.get('points', []) or []]
            for i in range(len(pts) - 1):
                blocked.add(_normalize_route_edge(pts[i], pts[i + 1]))
        return blocked

    def _blocked_line_edges_for_line(self, line_name: str, line=None) -> set:
        """Edges blocked by incomplete construction or disaster damage."""
        blocked = self._incomplete_line_edges_for_line(line_name)
        if line is None and line_name:
            line = self.get_map_lines().get(line_name)
        if line:
            blocked |= _get_line_destroyed_edges(line)
        return blocked

    def _close_stations_disconnected_from_operational_route(self, player: Player, line) -> list:
        """Close stations on ``line`` that are not on any built operational route portion."""
        if line is None:
            return []
        line_name = self.get_line_name(line)
        closed_names = []
        stations_to_check = {}
        for station in (
            line.get_stations() if hasattr(line, 'get_stations')
            else (getattr(line, 'stations', None) or [])
        ):
            stations_to_check[id(station)] = station
        game_map = self.get_map()
        for station in getattr(game_map, 'stations', None) or []:
            if line_name in (getattr(station, 'line_names', None) or []):
                stations_to_check[id(station)] = station
        for station in stations_to_check.values():
            if self._operational_portion_index_for_station(line, station) is not None:
                continue
            if hasattr(line, 'remove_station'):
                line.remove_station(station)
            line_names = getattr(station, 'line_names', None)
            if line_names is not None and line_name in line_names:
                line_names.remove(line_name)
            if not getattr(station, 'line_names', []):
                result = self.close_station(player, station)
                if result.get('success'):
                    closed_names.append(self.get_station_name(station))
        return closed_names

    def _operational_portion_index_for_station(self, line, station) -> int | None:
        if station is None or line is None:
            return None
        memo = getattr(self, '_sim_portion_index_cache', None)
        if memo is not None:
            key = (id(line), id(station))
            if key not in memo:
                memo[key] = self._operational_portion_index_uncached(line, station)
            return memo[key]
        return self._operational_portion_index_uncached(line, station)

    def _operational_portion_index_uncached(self, line, station) -> int | None:
        sx = float(station.get_x()) if hasattr(station, 'get_x') else float(getattr(station, 'x', 0))
        sy = float(station.get_y()) if hasattr(station, 'get_y') else float(getattr(station, 'y', 0))
        for idx, portion in enumerate(self.get_line_operational_route_portions(line)):
            if _point_on_route_polyline(portion, sx, sy):
                return idx
        return None

    def stations_on_same_operational_portion(self, line, start_station, end_station) -> bool:
        """True when both stations lie on the same connected built portion."""
        start_idx = self._operational_portion_index_for_station(line, start_station)
        end_idx = self._operational_portion_index_for_station(line, end_station)
        return start_idx is not None and start_idx == end_idx

    def _calc_route_portion_traversal_time(self, line, portion: list, trainset, portion_index: int) -> float:
        """Traversal time for one operational route portion including dwell at its stations."""
        import math
        from game_presets import STATION_DWELL_MINUTES_PER_STOP

        if not portion or len(portion) < 2 or not trainset:
            return 0.0
        loco = getattr(trainset, 'locomotive', None)
        if not loco:
            return 0.0
        speed = float(getattr(loco, 'average_speed', 0) or 0)
        if speed <= 0:
            return 0.0

        total_distance = 0.0
        for i in range(len(portion) - 1):
            x1, y1 = _normalize_route_point(portion[i])
            x2, y2 = _normalize_route_point(portion[i + 1])
            total_distance += math.hypot(x2 - x1, y2 - y1)
        total_distance *= float(getattr(line, 'miles_per_grid_unit', 1.0) or 1.0)
        travel_hours = total_distance / speed

        stations_on_portion = 0
        for station in line.get_stations() or []:
            if self._operational_portion_index_for_station(line, station) == portion_index:
                stations_on_portion += 1
        travel_hours += stations_on_portion * (STATION_DWELL_MINUTES_PER_STOP / 60.0)
        return travel_hours

    def _scheduled_trips_for_route_portion_by_period(
        self, line, portion: list, portion_index: int, trainset_avg_speed: float, *, current_year: int,
    ) -> dict:
        """Annual trips per timetable period for one operational route portion."""
        from game_presets import SCHEDULE_PERIOD_YEAR_FRACTIONS, SCHEDULE_PERIODS, HOURS_PER_YEAR
        import math

        zero = {period: 0 for period in SCHEDULE_PERIODS}
        if not portion or len(portion) < 2 or trainset_avg_speed <= 0:
            return zero
        total_distance = 0.0
        for i in range(len(portion) - 1):
            x1, y1 = _normalize_route_point(portion[i])
            x2, y2 = _normalize_route_point(portion[i + 1])
            total_distance += math.hypot(x2 - x1, y2 - y1)
        total_distance *= float(getattr(line, 'miles_per_grid_unit', 1.0) or 1.0)
        trav_time = total_distance / float(trainset_avg_speed)
        if trav_time <= 0:
            return zero

        from game_presets import STATION_DWELL_MINUTES_PER_STOP, TERMINAL_TURNAROUND_MINUTES
        stations_on_portion = sum(
            1 for station in (line.get_stations() or [])
            if self._operational_portion_index_for_station(line, station) == portion_index
        )
        trav_time += stations_on_portion * (STATION_DWELL_MINUTES_PER_STOP / 60.0)
        round_trip_time = (2.0 * trav_time) + (TERMINAL_TURNAROUND_MINUTES / 60.0)
        if round_trip_time <= 0:
            return zero
        max_by_roundtrip = 60.0 / round_trip_time

        trips = {}
        for period in SCHEDULE_PERIODS:
            sched_freq = float(line.schedule.get(period, 0) or 0)
            if sched_freq <= 0:
                trips[period] = 0
                continue
            max_freq = line.get_max_frequency_per_hour(period) if hasattr(line, 'get_max_frequency_per_hour') else max_by_roundtrip
            actual_freq = min(sched_freq, max_freq)
            trips[period] = int(actual_freq * SCHEDULE_PERIOD_YEAR_FRACTIONS[period] * HOURS_PER_YEAR)
        return trips

    def _service_frequency_for_route_portion(
        self, line, portion: list, portion_index: int, trainset_avg_speed: float,
    ) -> int:
        """Annual trips for one operational portion using the line schedule."""
        year = int(self.get_year())
        by_period = self._scheduled_trips_for_route_portion_by_period(
            line, portion, portion_index, trainset_avg_speed, current_year=year,
        )
        return int(sum(by_period.values()))

    def get_line_effective_service_frequency_per_year(self, line, trainset_avg_speed: float = 30) -> int:
        """Annual service trips; sums independent frequencies when line is split by gaps."""
        portions = self.get_line_operational_route_portions(line)
        if len(portions) <= 1:
            return int(line.service_frequency_per_year(trainset_avg_speed=trainset_avg_speed) or 0)
        total = 0
        for idx, portion in enumerate(portions):
            total += self._service_frequency_for_route_portion(line, portion, idx, trainset_avg_speed)
        return total

    def _next_pending_line_extension_order(self, line_name: str) -> int:
        """Monotonic order index for queued extensions on one line (stable merge ordering)."""
        max_order = -1
        for qi, build in enumerate(self.get_build_queue()):
            if build.get('type') != 'line_extension':
                continue
            data = build.get('data') or {}
            if data.get('line_name') != line_name:
                continue
            max_order = max(max_order, int(data.get('extension_order', qi)))
        return max_order + 1

    def get_extension_attach_endpoints(self, line_name: str) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
        """Grid endpoints of the full route (built line + pending extensions)."""
        route = self.get_line_route_including_extensions(line_name)
        if not route:
            return (None, None)
        return (_normalize_route_point(route[0]), _normalize_route_point(route[-1]))

    def extension_attach_from_closest_end(self, line_name: str, click_point: tuple) -> bool:
        """Pick route end vs start by proximity to the first extension click.

        Uses the combined route (built line + pending extensions) so train-line
        extensions attach from the nearest end, including the tip of the most
        recent pending extension when one exists.
        """
        route = self.get_line_route_including_extensions(line_name)
        return _extension_attach_from_closest_end(route, click_point)

    def _route_before_pending_extension(self, line_name: str, extension_order: int) -> list:
        """Planned route (built + own queued batches) plus pending extensions queued before extension_order."""
        route = self._line_route_with_pending_original_batches(line_name)

        pending = []
        for qi, build in enumerate(self.get_build_queue()):
            if build.get('type') != 'line_extension':
                continue
            data = build.get('data') or {}
            if data.get('line_name') != line_name:
                continue
            ext_order = int(data.get('extension_order', qi))
            if ext_order >= extension_order:
                continue
            pending.append((
                ext_order,
                int(data.get('segment_index', 0) or 0),
                data.get('points', []) or [],
                bool(data.get('extend_from_end', True)),
            ))
        pending.sort(key=lambda x: (x[0], x[1]))

        for _ext_order, _seg_idx, ext_pts, extend_from_end in pending:
            ext_pts = [_normalize_route_point(p) for p in ext_pts if p]
            if not ext_pts:
                continue
            if extend_from_end and route:
                if _points_close(route[-1], ext_pts[0]):
                    route = route + ext_pts[1:]
                else:
                    route = route + ext_pts
            elif not extend_from_end and route:
                if _points_close(route[0], ext_pts[-1]):
                    route = ext_pts[:-1] + route
                else:
                    route = ext_pts + route
            else:
                route = route + ext_pts
        return route

    def get_pending_line_extension_routes(self) -> list:
        """Grid polylines for line extensions in the build queue (for map display)."""
        def _dedupe_append(route: list, pt) -> None:
            if not route or not _points_close(route[-1], pt):
                route.append(pt)

        extensions_by_key = {}
        for qi, build in enumerate(self.get_build_queue()):
            if build.get('type') != 'line_extension':
                continue
            data = build.get('data') or {}
            line_name = data.get('line_name')
            if not line_name:
                continue
            ext_order = int(data.get('extension_order', qi))
            extend_from_end = bool(data.get('extend_from_end', True))
            key = (line_name, ext_order, extend_from_end)
            if key not in extensions_by_key:
                extensions_by_key[key] = {
                    'segments': [],
                    'player': build.get('player'),
                    'extend_from_end': extend_from_end,
                    'extension_order': ext_order,
                }
            extensions_by_key[key]['segments'].append(
                (data.get('segment_index', 0), data.get('points', []))
            )

        results = []
        for (line_name, ext_order, extend_from_end), info in sorted(
            extensions_by_key.items(),
            key=lambda item: (item[0][0], item[0][1], not item[0][2]),
        ):
            segments = sorted(info['segments'], key=lambda x: x[0])
            ext_route = []
            for _seg_idx, seg_pts in segments:
                for p in seg_pts or []:
                    _dedupe_append(ext_route, _normalize_route_point(p))
            if len(ext_route) < 1:
                continue

            route_before = self._route_before_pending_extension(line_name, ext_order)
            display_route = []
            if extend_from_end:
                if route_before:
                    display_route.append(route_before[-1])
                for p in ext_route:
                    _dedupe_append(display_route, p)
            elif route_before:
                _dedupe_append(display_route, route_before[0])
                for p in ext_route:
                    _dedupe_append(display_route, p)
            else:
                display_route = list(ext_route)

            display_route = _open_polyline_display_points(display_route)
            if len(display_route) < 2:
                continue

            line = self.get_map_lines().get(line_name)
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

    def _line_type_for_map_display(self, line=None, build_data: dict = None) -> str:
        """Return 'train', 'ferry', or 'bus' for map overlay styling."""
        if build_data:
            if build_data.get('is_ferry_line'):
                return 'ferry'
            if build_data.get('is_bus_line'):
                return 'bus'
            return 'train'
        if line is not None:
            try:
                from line import Ferry, Bus
                if isinstance(line, Ferry):
                    return 'ferry'
                if isinstance(line, Bus):
                    return 'bus'
            except Exception:
                pass
        return 'train'

    def get_lines_for_map_display(self) -> list:
        """Polylines for the map overlay: every segment of every line, plus construction.

        Built lines are drawn whole. They are only broken where a stretch is still under
        construction (drawn from the build queue instead) or at a long jump between separately
        finished pieces that was never built itself, so an unbuilt gap is never drawn as track
        (the same gaps get_line_operational_route_portions splits trains at). Disaster-damaged
        segments are drawn too, with ``damaged`` set.
        Abandoned lines (no owner, an eliminated owner, or listed on the marketplace after
        being given up) are drawn with ``abandoned`` set and no player.
        """
        results = []

        def add_built_line(line_name, line, owner, abandoned):
            line_type = self._line_type_for_map_display(line=line)
            route = _open_polyline_display_points(self.get_line_route(line) or [])
            if len(route) < 2:
                return
            damaged = _get_line_destroyed_edges(line)
            pending = self._incomplete_line_edges_for_line(line_name) if not abandoned else set()
            built_long = getattr(line, '_built_long_edges', None) or set()
            limit = _max_connected_route_edge_length()

            def split(break_at_gaps):
                pieces = []  # (points, damaged)
                current, current_damaged = [route[0]], None
                for a, b in zip(route, route[1:]):
                    edge = _normalize_route_edge(a, b)
                    if edge in pending or (
                        break_at_gaps and edge not in built_long
                        and math.hypot(b[0] - a[0], b[1] - a[1]) > limit
                    ):
                        if len(current) >= 2:
                            pieces.append((current, bool(current_damaged)))
                        current, current_damaged = [b], None
                        continue
                    is_damaged = edge in damaged
                    if current_damaged is not None and is_damaged != current_damaged:
                        pieces.append((current, current_damaged))
                        current = [a]
                    current_damaged = is_damaged
                    current.append(b)
                if len(current) >= 2:
                    pieces.append((current, bool(current_damaged)))
                return pieces

            # A line that is nothing but long jumps was built that way (nothing else made it):
            # it is drawn whole rather than not at all.
            pieces = split(True) or split(False)
            for points, is_damaged in pieces:
                entry = {
                    'name': line_name,
                    'points': points,
                    'under_construction': False,
                    'player': None if abandoned else owner,
                    'line_type': line_type,
                }
                if is_damaged:
                    entry['damaged'] = True
                if damaged:
                    entry['line_damaged'] = True  # some part of this line awaits repair
                if abandoned:
                    entry['abandoned'] = True
                results.append(entry)

        map_lines = self.get_map_lines()
        for line_name, line in map_lines.items():
            if not hasattr(line, 'get_route'):
                continue
            owner = self.get_line_owner(line)
            abandoned = owner is None or self.is_player_eliminated(owner)
            add_built_line(line_name, line, owner, abandoned)
        # Lines given up to the marketplace leave the map until someone buys them; they stay
        # visible, as abandoned track.
        try:
            listed = list(self.game.get_marketplace().get_available_lines())
        except Exception:
            listed = []
        shown = set(map_lines)
        for sale_item in listed:
            line = getattr(sale_item, 'item', None)
            line_name = getattr(line, 'name', None)
            if not line_name or line_name in shown or not hasattr(line, 'get_route'):
                continue
            shown.add(line_name)
            add_built_line(line_name, line, None, True)

        for build in self.get_build_queue():
            if build.get('type') != 'line':
                continue
            data = build.get('data') or {}
            line_name = data.get('name')
            points = data.get('points') or []
            if not line_name or len(points) < 2:
                continue
            display_route = _open_polyline_display_points(points)
            if len(display_route) < 2:
                continue
            results.append({
                'name': line_name,
                'points': display_route,
                'under_construction': True,
                'player': build.get('player'),
                'line_type': self._line_type_for_map_display(build_data=data),
            })

        for ext_info in self.get_pending_line_extension_routes():
            ext_route = ext_info.get('points') or []
            if len(ext_route) < 2:
                continue
            results.append({
                'name': ext_info.get('line_name', ''),
                'points': list(ext_route),
                'under_construction': True,
                'player': ext_info.get('player'),
                'line_type': ext_info.get('line_type', 'train'),
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
        prev_year = int(current_year) - 1
        previous_year_ridership = (
            self.get_line_previous_year_ridership(line)
            if prev_year >= self.get_timeline_start_year() else 0
        )
        previous_year_fare_profit = (
            self.get_line_previous_year_profit(line)
            if prev_year >= self.get_timeline_start_year() else 0
        )
        
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
            'route': route,
            'previous_year': prev_year,
            'previous_year_ridership': previous_year_ridership,
            'previous_year_fare_profit': previous_year_fare_profit,
        }
    
    def calculate_station_cost(
        self,
        num_lines: int,
        x: float = None,
        y: float = None,
        player: Player = None,
        station_name: str = None,
    ) -> int:
        """Calculate station creation cost.

        New stop: base + per additional line selected at once.
        Merge extension (same owner, same name, within STATION_MERGE_MAX_DISTANCE_M):
        discounted per-line extension cost only.
        """
        from game_presets import STATION_ADDITIONAL_LINE_COST, STATION_BASE_COST
        base_cost = self.inflate_economic_value(STATION_BASE_COST, max_factor=10.0)
        additional_cost = self.inflate_economic_value(STATION_ADDITIONAL_LINE_COST, max_factor=10.0)
        merge_target = None
        if player is not None and station_name and x is not None and y is not None:
            merge_target = self.find_player_station_merge_target(
                player, station_name, float(x), float(y),
            )
        if merge_target is not None:
            from game_presets import STATION_MERGE_CONSTRUCTION_COST_FRACTION
            cost = additional_cost * max(1, int(num_lines))
            return self._scale_construction_cost(
                max(1, int(cost * float(STATION_MERGE_CONSTRUCTION_COST_FRACTION)))
            )
        cost = base_cost + ((num_lines - 1) * additional_cost)
        return self._scale_construction_cost(cost)

    def calc_player_station_merge_maintenance_savings(self, player: Player, current_year: int) -> int:
        """Annual maintenance reduction for multi-line merged station hubs."""
        from game_presets import STATION_MERGE_MAINTENANCE_SAVINGS
        from inflation_utils import get_inflation_factor

        seen = set()
        merged_count = 0
        for ln in player.get_line_names() or []:
            line = self.get_map_lines().get(ln)
            if not line:
                continue
            for st in line.get_stations() or []:
                sid = id(st)
                if sid in seen:
                    continue
                seen.add(sid)
                lns = (
                    st.get_line_names() if hasattr(st, 'get_line_names')
                    else getattr(st, 'line_names', [])
                )
                if len(lns or []) >= 2:
                    merged_count += 1
        if merged_count <= 0:
            return 0
        infl = get_inflation_factor(int(current_year), max_factor=10.0)
        return int(merged_count * STATION_MERGE_MAINTENANCE_SAVINGS * infl)
    
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
                from map_preset_utils import haversine_distance_m
                return float(haversine_distance_m(lat1, lon1, lat2, lon2))
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

    def _ai_endpoint_station_legal(self, player: Player, game_map, x: float, y: float) -> bool:
        """True when a route endpoint may host a station (legal rules only: water + AI separation)."""
        if not game_map:
            return False
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name:
            from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
            if preset_has_water_areas(preset_name):
                try:
                    if preset_is_point_in_water(preset_name, float(x), float(y), game_map, skip_osm=True):
                        return False
                except TypeError:
                    if preset_is_point_in_water(preset_name, float(x), float(y), game_map):
                        return False
        ts = getattr(game_map, 'tile_system', None)
        if ts:
            try:
                if ts.is_point_in_water(float(x), float(y), skip_osm=True):
                    return False
            except TypeError:
                if ts.is_point_in_water(float(x), float(y)):
                    return False
        if getattr(player, 'is_ai', False):
            shared = self.player_station_at(player, float(x), float(y))
            if shared is not None and not isinstance(shared, dict):
                return True  # the line shares this existing (built) station
            try:
                self.assert_ai_stop_minimum_direct_separation(player, float(x), float(y))
            except ValueError:
                return False
        return True

    def player_station_at(self, player: Player, x: float, y: float, max_distance_m: float = None):
        """The player's own built or queued station within merge distance of (x, y), or None.
        Returns the Station, or the queued build's data dict."""
        from game_presets import STATION_MERGE_MAX_DISTANCE_M

        max_m = float(max_distance_m if max_distance_m is not None else STATION_MERGE_MAX_DISTANCE_M)
        own_lines = set(player.get_line_names() or [])
        for build in self.game.build_queue or []:
            if build.get('player') is player and build.get('type') == 'line':
                own_lines.add((build.get('data') or {}).get('name'))
        best, best_d = None, max_m + 1e-9
        for st in getattr(self.game.get_map(), 'stations', None) or []:
            served = st.get_line_names() if hasattr(st, 'get_line_names') else getattr(st, 'line_names', [])
            if not own_lines.intersection(served or []):
                continue
            d = self._direct_distance_between_stop_positions_m(x, y, float(st.get_x()), float(st.get_y()))
            if d <= best_d:
                best, best_d = st, d
        if best is not None:
            return best
        for build in self.game.build_queue or []:
            if build.get('player') is not player or build.get('type') != 'station':
                continue
            data = build.get('data') or {}
            try:
                d = self._direct_distance_between_stop_positions_m(x, y, float(data['x']), float(data['y']))
            except (KeyError, TypeError, ValueError):
                continue
            if d <= best_d:
                best, best_d = data, d
        return best

    def _ai_resolve_line_endpoint_station_site(
        self,
        player: Player,
        game_map,
        route_points: list,
        *,
        from_start: bool,
    ) -> Optional[tuple]:
        """First legal station site on the route terminal, walking inward from the line end."""
        if not route_points:
            return None
        seq = route_points if from_start else list(reversed(route_points))
        for pt in seq:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                x, y = float(pt[0]), float(pt[1])
            else:
                x, y = float(getattr(pt, 'x', 0)), float(getattr(pt, 'y', 0))
            if self._ai_endpoint_station_legal(player, game_map, x, y):
                return (x, y)
        return None

    def _ai_route_has_two_viable_station_sites(
        self, player: Player, game_map, route_points: list, *, is_bus_line: bool = False,
    ) -> bool:
        """Route must support legal endpoint station sites at both line ends."""
        if not route_points or len(route_points) < 2:
            return False
        s1 = self._ai_resolve_line_endpoint_station_site(
            player, game_map, route_points, from_start=True,
        )
        s2 = self._ai_resolve_line_endpoint_station_site(
            player, game_map, route_points, from_start=False,
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
        if hasattr(game_map, 'apply_distance_scale_to_lines'):
            game_map.apply_distance_scale_to_lines()
        from ai_specialties import ensure_specialties
        ensure_specialties(game)
        if not hasattr(game_map, 'end_year') or game_map.end_year is None:
            from map_preset_loader import get_map_timeline
            preset_name = getattr(game_map, 'map_preset_name', None)
            timeline = get_map_timeline(preset_name) if preset_name else None
            if timeline:
                game_map.end_year = timeline.get('end_year') or game_map.end_year
                if not hasattr(game_map, 'start_year') or game_map.start_year is None:
                    game_map.start_year = timeline.get('start_year', 1830)
            else:
                from map_preset_loader import get_default_map_timeline
                _default_timeline = get_default_map_timeline()
                game_map.end_year = _default_timeline['end_year']
                if not hasattr(game_map, 'start_year') or game_map.start_year is None:
                    game_map.start_year = _default_timeline['start_year']
        # Saves made before long built segments were recorded: a long edge already in a
        # line's route was built, so it must be shown and served (not read as a gap).
        try:
            for _line in (game_map.get_lines_by_name() or {}).values():
                if hasattr(_line, 'get_route') and getattr(_line, '_built_long_edges', None) is None:
                    _record_built_long_edges(_line, list(_line.get_route() or []))
        except Exception:
            pass
        # Older saves: make the tile system use this map's own water test.
        try:
            from map_preset_loader import load_map_preset
            _mod = load_map_preset(getattr(game_map, 'map_preset_name', None) or '')
            _ts = getattr(game_map, 'tile_system', None)
            if _ts is not None and _mod is not None and hasattr(_mod, 'is_water_location'):
                _ts.preset_water_fn = _mod.is_water_location
                from map_preset_loader import get_preset_water_mask
                _mask = get_preset_water_mask(getattr(game_map, 'map_preset_name', None))
                _ts.preset_water_authoritative = bool(_mask is not None and _mask.has_data())
        except Exception:
            pass
        # Saves made before the water / excluded-area cleanup still carry those cells; apply the
        # same cleanup a new map gets (water cells merge into land, excluded areas are dropped).
        if hasattr(game_map, '_remove_population_on_water'):
            try:
                game_map._remove_population_on_water()
            except Exception:
                pass
    
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
        self.enforce_line_schedule_limits(line)
    
    def convert_click_to_grid(self, lat: float, lon: float) -> tuple[float, float]:
        """Convert lat/lon click to grid coordinates with 0.000001 precision.
        Returns (x, y) rounded to 6 decimal places."""
        game_map = self.game.get_map()
        center_lat = game_map.map_center_lat if game_map.map_center_lat is not None else 0.0
        center_lon = game_map.map_center_lon if game_map.map_center_lon is not None else 0.0
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
        grid_size = max(map_width, map_height)
        from map_preset_loader import get_map_bounds
        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        if game_map.use_osm and game_map.osm_integration:
            x, y = game_map.osm_integration.convert_latlon_to_grid(
                lat, lon, center_lat, center_lon, grid_size,
                map_width=game_map.width, map_height=game_map.height,
                bounds=preset_bounds
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
        
        from map_preset_loader import get_map_bounds
        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        clicked_x, clicked_y = game_map.osm_integration.convert_latlon_to_grid(
            clicked_lat, clicked_lon, center_lat, center_lon, grid_size,
            map_width=game_map.width, map_height=game_map.height,
            bounds=preset_bounds
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

    def _locate_position_on_route(self, route: list, x: float, y: float, max_distance: float = 0.5) -> tuple:
        """Return (position_along_route, snapped_point) where position is 0..len(route)-1."""
        if not route or len(route) < 2:
            raise ValueError("Line route must have at least 2 points")
        best_dist_sq = float('inf')
        best_pos = 0.0
        best_pt = _normalize_route_point(route[0])
        for i in range(len(route) - 1):
            p1 = _normalize_route_point(route[i])
            p2 = _normalize_route_point(route[i + 1])
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((x - p1[0]) * dx + (y - p1[1]) * dy) / seg_len_sq))
            proj = (p1[0] + t * dx, p1[1] + t * dy)
            d_sq = (x - proj[0]) ** 2 + (y - proj[1]) ** 2
            if d_sq < best_dist_sq:
                best_dist_sq = d_sq
                best_pos = i + t
                best_pt = (round(proj[0], 6), round(proj[1], 6))
        if best_dist_sq > max_distance * max_distance:
            raise ValueError(
                f"Point ({x:.1f}, {y:.1f}) is not on the line route "
                f"(must be within {max_distance} units)"
            )
        return best_pos, best_pt

    def _route_part_from_start(self, route: list, pos: float, end_pt) -> list:
        import math
        result = []
        end_idx = int(math.floor(pos + 1e-9))
        for i in range(min(end_idx + 1, len(route))):
            result.append(_normalize_route_point(route[i]))
        on_vertex = abs(pos - round(pos)) < 1e-6 and int(round(pos)) < len(route)
        if on_vertex:
            vtx = _normalize_route_point(route[int(round(pos))])
            if not result or not _points_close(result[-1], vtx):
                result.append(vtx)
        else:
            pt = _normalize_route_point(end_pt)
            if not result or not _points_close(result[-1], pt):
                result.append(pt)
        return _dedupe_consecutive_route_points(result)

    def _route_part_from_position(self, route: list, pos: float, start_pt) -> list:
        import math
        result = []
        on_vertex = abs(pos - round(pos)) < 1e-6
        if on_vertex:
            start_idx = int(round(pos))
            if start_idx < len(route):
                result.append(_normalize_route_point(route[start_idx]))
            next_idx = start_idx + 1
        else:
            result.append(_normalize_route_point(start_pt))
            next_idx = int(math.ceil(pos - 1e-9))
        for i in range(next_idx, len(route)):
            pt = _normalize_route_point(route[i])
            if not result or not _points_close(result[-1], pt):
                result.append(pt)
        return _dedupe_consecutive_route_points(result)

    def _route_part_between(self, route: list, pos_lo: float, pt_lo, pos_hi: float, pt_hi) -> list:
        import math
        combined = [_normalize_route_point(pt_lo)]
        lo_idx = int(math.ceil(pos_lo - 1e-9))
        if abs(pos_lo - round(pos_lo)) < 1e-6:
            lo_idx = int(round(pos_lo)) + 1
        hi_idx = int(math.floor(pos_hi + 1e-9))
        for i in range(lo_idx, min(hi_idx + 1, len(route))):
            pt = _normalize_route_point(route[i])
            if not _points_close(combined[-1], pt):
                combined.append(pt)
        end_pt = _normalize_route_point(pt_hi)
        if not _points_close(combined[-1], end_pt):
            combined.append(end_pt)
        return _dedupe_consecutive_route_points(combined)

    def _split_route_at_clicks(self, route: list, pos_a: float, pt_a, pos_b: float, pt_b):
        pos_lo = min(pos_a, pos_b)
        pos_hi = max(pos_a, pos_b)
        pt_lo = pt_a if pos_a <= pos_b else pt_b
        pt_hi = pt_b if pos_a <= pos_b else pt_a
        if pos_hi - pos_lo < 1e-6:
            raise ValueError("Both clicks are at the same location on the line.")
        before = self._route_part_from_start(route, pos_lo, pt_lo)
        after = self._route_part_from_position(route, pos_hi, pt_hi)
        removed = self._route_part_between(route, pos_lo, pt_lo, pos_hi, pt_hi)
        return before, after, removed, pos_lo, pos_hi

    def _elevation_slices(self, elevations: list, pos_lo: float, pos_hi: float):
        import math
        n = len(elevations or [])
        s0 = max(0, min(n, int(math.floor(pos_lo + 1e-9))))
        s1 = max(0, min(n, int(math.ceil(pos_hi - 1e-9))))
        return list((elevations or [])[:s0]), list((elevations or [])[s0:s1]), list((elevations or [])[s1:])

    def _line_end_has_pending_construction(self, line_name: str, route: list, *, at_start: bool) -> bool:
        """True when queued batches or extensions of the line continue past this built end."""
        if not route:
            return False
        end = _normalize_route_point(route[0] if at_start else route[-1])
        try:
            planned = [_normalize_route_point(p) for p in self.get_line_route_including_extensions(line_name)]
        except Exception:
            planned = []
        if len(planned) >= 2:
            planned_end = planned[0] if at_start else planned[-1]
            if math.hypot(planned_end[0] - end[0], planned_end[1] - end[1]) > SELL_SEGMENT_END_SNAP_GRID:
                return True
        for build in self.get_build_queue():
            data = build.get('data') or {}
            if build.get('type') == 'line_extension' and data.get('line_name') == line_name:
                if bool(data.get('extend_from_end', True)) != at_start:
                    return True
            elif build.get('type') == 'line' and data.get('name') == line_name:
                for p in data.get('points') or []:
                    q = _normalize_route_point(p)
                    if math.hypot(q[0] - end[0], q[1] - end[1]) <= SELL_SEGMENT_END_SNAP_GRID:
                        return True
        return False

    def sell_line_segment_complete(
        self,
        player: Player,
        line_name: str,
        point_a: tuple,
        point_b: tuple,
    ) -> dict:
        """Remove/sell the route portion between two clicked points on a line.

        The sold portion must include one end of the line (a click within
        SELL_SEGMENT_END_SNAP_GRID of an end counts as the end), so the line stays in one piece.
        Returns summary dict with refund and the resulting line name.
        """
        from line import Ferry, Bus

        if line_name not in player.get_line_names():
            raise ValueError(f"Player does not own line '{line_name}'")

        game_map = self.get_map()
        line = game_map.get_lines_by_name().get(line_name)
        if line is None:
            raise ValueError(f"Line '{line_name}' not found on map")

        owner = self.get_line_owner(line)
        if owner is not None and owner != player:
            raise ValueError(f"Player does not own line '{line_name}'")

        route = list(line.get_route() or [])
        if len(route) < 2:
            raise ValueError(f"Line '{line_name}' has no route to modify")

        ax, ay = float(point_a[0]), float(point_a[1])
        bx, by = float(point_b[0]), float(point_b[1])
        pos_a, pt_a = self._locate_position_on_route(route, ax, ay)
        pos_b, pt_b = self._locate_position_on_route(route, bx, by)

        before, after, removed, pos_lo, pos_hi = self._split_route_at_clicks(
            route, pos_a, pt_a, pos_b, pt_b,
        )
        # A click this close to a line end means the end itself (hard to hit exactly).
        if len(before) >= 2 and _polyline_metrics(before)[0] < SELL_SEGMENT_END_SNAP_GRID:
            removed = list(before[:-1]) + list(removed)
            before = []
            pos_lo = 0.0
        if len(after) >= 2 and _polyline_metrics(after)[0] < SELL_SEGMENT_END_SNAP_GRID:
            removed = list(removed) + list(after[1:])
            after = []
            pos_hi = float(len(route) - 1)

        if len(removed) < 2:
            raise ValueError("Selected segment is too short to sell.")
        if len(before) < 2 and len(after) < 2:
            raise ValueError("Cannot remove the entire line.")
        if len(before) >= 2 and len(after) >= 2:
            raise ValueError(
                "Only a segment that includes an end of the line can be sold. "
                "Selling a middle section would leave the line in two disconnected pieces."
            )
        # A built end with construction queued beyond it is not the end of the line: selling
        # there would cut the middle out of the planned line.
        sells_start = len(before) < 2
        if self._line_end_has_pending_construction(line_name, route, at_start=sells_start):
            raise ValueError(
                "That end of the line still has construction queued beyond it, so this would be "
                "a middle section. Only a segment that includes an end of the line can be sold."
            )

        is_ferry = isinstance(line, Ferry)
        is_bus = isinstance(line, Bus)
        default_elev = 'water' if is_ferry else 'street'
        src_elevs = list(getattr(line, 'elevation_levels', None) or [])
        elevs_before, elevs_removed, elevs_after = self._elevation_slices(src_elevs, pos_lo, pos_hi)
        elevs_before = _fit_elevation_levels(elevs_before, max(0, len(before) - 1), default_elev)
        elevs_after = _fit_elevation_levels(elevs_after, max(0, len(after) - 1), default_elev)
        elevs_removed = _fit_elevation_levels(elevs_removed, max(0, len(removed) - 1), default_elev)

        try:
            removed_cost = self.calculate_line_cost(
                removed, elevs_removed, is_bus_line=is_bus, is_ferry_line=is_ferry,
            ).get('cost', 0)
        except Exception:
            removed_cost = 0
        refund = int(removed_cost * 0.5)

        closed_stations = []
        stations_on_line = list(
            line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
        )
        for station in stations_on_line:
            try:
                st_pos, _ = self._locate_position_on_route(route, station.get_x(), station.get_y(), max_distance=0.75)
            except ValueError:
                continue
            if len(before) >= 2:
                should_remove = st_pos > pos_lo + 1e-6
            else:
                should_remove = st_pos < pos_hi - 1e-6
            if should_remove:
                line.remove_station(station)
                if line_name in getattr(station, 'line_names', []):
                    station.line_names.remove(line_name)
                if not getattr(station, 'line_names', []):
                    result = self.close_station(player, station)
                    if result.get('success'):
                        closed_stations.append(self.get_station_name(station))

        resulting_lines = []
        if len(before) >= 2:
            line.route = before
            line.elevation_levels = elevs_before
            resulting_lines = [line_name]
        else:
            line.route = after
            line.elevation_levels = elevs_after
            resulting_lines = [line_name]

        if refund > 0:
            self.add_player_money(player, refund, reason='Line segment scrap refund')

        closed_disconnected = []
        for res_name in resulting_lines:
            res_line = game_map.get_lines_by_name().get(res_name)
            if res_line:
                closed_disconnected.extend(
                    self._close_stations_disconnected_from_operational_route(player, res_line)
                )
        all_closed = list(closed_stations)
        for name in closed_disconnected:
            if name not in all_closed:
                all_closed.append(name)

        self.invalidate_ridership_cache()
        return {
            'success': True,
            'line_name': line_name,
            'resulting_lines': resulting_lines,
            'refund': refund,
            'removed_points': len(removed),
            'closed_stations': all_closed,
        }

    def estimate_line_scrap_refund(self, line_name: str) -> int:
        """Estimate 50% scrap refund for an entire built line."""
        from line import Ferry, Bus

        line = self.get_map_lines().get(line_name)
        if line is None:
            return 0
        route = list(line.get_route() or [])
        if len(route) < 2:
            return 0
        is_ferry = isinstance(line, Ferry)
        is_bus = isinstance(line, Bus)
        default_elev = 'water' if is_ferry else 'street'
        elevs = list(getattr(line, 'elevation_levels', None) or [])
        elevs = _fit_elevation_levels(elevs, max(0, len(route) - 1), default_elev)
        try:
            line_cost = self.calculate_line_cost(
                route, elevs, is_bus_line=is_bus, is_ferry_line=is_ferry,
            ).get('cost', 0)
        except Exception:
            line_cost = 0
        return int(line_cost * 0.5)

    def sell_line_complete(self, player: Player, line_name: str) -> dict:
        """Scrap an entire owned line for 50% of construction cost (not marketplace sale)."""
        from line import Ferry, Bus

        if not line_name:
            raise ValueError("Line name required")
        if line_name not in player.get_line_names():
            raise ValueError(f"Player does not own line '{line_name}'")

        game_map = self.get_map()
        line = game_map.get_lines_by_name().get(line_name)
        if line is None:
            raise ValueError(f"Line '{line_name}' not found on map")

        owner = self.get_line_owner(line)
        if owner is not None and owner != player:
            raise ValueError(f"Player does not own line '{line_name}'")

        route = list(line.get_route() or [])
        if len(route) < 2:
            raise ValueError(f"Line '{line_name}' has no route to sell")

        for ts in list(player.get_trainsets()):
            if ts.get_line_assignment() == line_name:
                try:
                    self.unassign_trainset_complete(ts)
                except ValueError:
                    pass
        if hasattr(line, 'vehicles'):
            line.vehicles.clear()

        cancelled = []
        queue = self.get_build_queue() if hasattr(self, 'get_build_queue') else []
        for idx in range(len(queue) - 1, -1, -1):
            build = queue[idx]
            if build.get('player') != player:
                continue
            data = build.get('data') or {}
            matches = (
                (build.get('type') == 'line' and data.get('name') == line_name)
                or (build.get('type') in ('line_extension', 'line_repair') and data.get('line_name') == line_name)
                or (build.get('type') == 'station' and line_name in (data.get('line_names') or []))
            )
            if matches:
                cancelled.append(self.cancel_build(idx))

        is_ferry = isinstance(line, Ferry)
        is_bus = isinstance(line, Bus)
        default_elev = 'water' if is_ferry else 'street'
        elevs = list(getattr(line, 'elevation_levels', None) or [])
        elevs = _fit_elevation_levels(elevs, max(0, len(route) - 1), default_elev)
        try:
            line_cost = self.calculate_line_cost(
                route, elevs, is_bus_line=is_bus, is_ferry_line=is_ferry,
            ).get('cost', 0)
        except Exception:
            line_cost = 0
        refund = int(line_cost * 0.5)

        closed_stations = []
        for station in list(
            line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
        ):
            line_names = list(
                station.get_line_names() if hasattr(station, 'get_line_names')
                else getattr(station, 'line_names', []) or []
            )
            if line_name in line_names and len(line_names) <= 1:
                closed_stations.append(self.get_station_name(station))

        if line_name in player.get_line_names():
            player.remove_line(line_name)
        self.delete_line_from_map(line)

        if refund > 0:
            self.add_player_money(player, refund, reason='Line scrap refund')

        self.invalidate_ridership_cache()
        return {
            'success': True,
            'line_name': line_name,
            'refund': refund,
            'closed_stations': closed_stations,
            'cancelled_builds': len(cancelled),
        }
    
    def _snap_point_to_route(self, route: list, x: float, y: float, max_distance: float = 0.5) -> tuple:
        """Snap (x,y) to the nearest point on the route. Returns (snapped_x, snapped_y) if within max_distance, else None."""
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

    def station_line_expansion_cost(self) -> int:
        """Cost of making an existing station also serve one more of the owner's lines."""
        from game_presets import STATION_ADDITIONAL_LINE_COST, STATION_MERGE_CONSTRUCTION_COST_FRACTION
        cost = self.inflate_economic_value(STATION_ADDITIONAL_LINE_COST, max_factor=10.0)
        return self._scale_construction_cost(
            max(1, int(cost * float(STATION_MERGE_CONSTRUCTION_COST_FRACTION)))
        )

    def station_expansion_lines(self, player: Player, station) -> list:
        """Names of the player's lines that pass within reach of ``station`` but do not stop there.

        Only for stations the player may edit (none of its lines belong to someone else).
        """
        from game_presets import STATION_LINE_EXPANSION_RANGE_GRID
        if player is None or station is None:
            return []
        served = set(
            station.get_line_names() if hasattr(station, 'get_line_names')
            else (getattr(station, 'line_names', None) or [])
        )
        lines = self.get_map_lines() or {}
        owned = set(player.get_line_names() or [])
        if any(ln in lines and ln not in owned for ln in served):
            return []
        sx, sy = float(station.get_x()), float(station.get_y())
        out = []
        for name in player.get_line_names() or []:
            line = lines.get(name)
            if line is None or name in served:
                continue
            route = line.get_route() or []
            if _point_on_route_polyline(route, sx, sy, tolerance=float(STATION_LINE_EXPANSION_RANGE_GRID)):
                out.append(name)
        return out

    def expand_station_to_line(self, player: Player, station, line_name: str) -> dict:
        """Make an existing station also serve one of the player's lines that passes within reach."""
        if line_name not in self.station_expansion_lines(player, station):
            return {'success': False, 'message': f"Line '{line_name}' does not pass close enough to this station."}
        cost = self.station_line_expansion_cost()
        if self.get_player_money(player) < cost:
            return {'success': False, 'message': f"Expanding this station costs ${cost:,}."}
        self.add_station_to_line(player, line_name, station)
        self.deduct_player_money(player, cost)
        line = (self.get_map_lines() or {}).get(line_name)
        for attr in ('_cached_profit', '_cached_profit_year', '_cached_ridership', '_cached_ridership_year'):
            if line is not None and hasattr(line, attr):
                delattr(line, attr)
        self.invalidate_ridership_cache()
        return {'success': True, 'cost': cost, 'line_name': line_name, 'station_name': station.name}

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

        _require_ai_station_not_in_water(game_map, player, grid_x, grid_y)

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
        merge_target = self.find_player_station_merge_target(
            player, station_name, float(grid_x), float(grid_y),
        )
        if merge_target is not None:
            grid_x, grid_y = float(merge_target.get_x()), float(merge_target.get_y())
            if line_name in (
                merge_target.get_line_names() if hasattr(merge_target, 'get_line_names')
                else getattr(merge_target, 'line_names', [])
            ):
                raise ValueError(
                    f"Station '{station_name}' already serves line '{line_name}'."
                )

        self.assert_station_name_unique_for_player(
            player, station_name, x=float(grid_x), y=float(grid_y), line_names=[line_name],
        )
        if merge_target is not None:
            # Joining an existing station (a transfer): the stop already exists, so the
            # minimum-separation rule for new AI stops does not apply.
            self.add_station_to_line(player, line_name, merge_target)
            return {'success': True, 'station': merge_target, 'merged': True}
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
        bq = getattr(self.game, 'build_queue', None)
        if not isinstance(bq, list):
            bq = []
            self.game.build_queue = bq
        return bq
    
    def process_build_queue(self):
        """Process the build queue, completing builds that are ready
        Construction workers actively work on builds each turn, reducing remaining time.
        This method contains all game logic for construction completion.
        Speed: N workers = 1/N time (1 worker = 6 turns, 2 workers = 3 turns, 3 workers = 2 turns)"""
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
        
        # Route pieces of one line must be attached in order: a later batch or an extension
        # finishing first would be joined to the wrong point of the route. Hold such a job
        # (finished, zero work left) until the pieces before it are done.
        completed_builds = self._hold_out_of_order_route_builds(completed_builds)

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
                        _clear_rebuilt_destroyed_edges(existing_line, segment_points)
                        _record_built_long_edges(existing_line, segment_points)
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
                        _record_built_long_edges(line, segment_points)
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
                    _record_built_long_edges(line, build['data']['points'])
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
                _route_before = list(existing_line.get_route() or [])
                if extend_from_end:
                    self.extend_line_route_from_end(existing_line, extension_points)
                else:
                    self.extend_line_route_from_beginning(existing_line, extension_points)
                _clear_rebuilt_destroyed_edges(existing_line, extension_points)
                # The extension's first edge starts at the line's old end.
                _old_end = (_route_before[-1] if extend_from_end else _route_before[0]) if _route_before else None
                _record_built_long_edges(
                    existing_line, ([_old_end] if _old_end is not None else []) + list(extension_points),
                )
            elif build['type'] == 'station':
                from station import Station
                st_player = build['player']
                st_nm = (build['data'].get('name') or '').strip()
                st_x = float(build['data']['x'])
                st_y = float(build['data']['y'])
                st_line_names = list(build['data'].get('line_names') or [])
                self.assert_station_name_unique_for_player(
                    st_player,
                    st_nm,
                    exclude_pending_station_build=build,
                    x=st_x,
                    y=st_y,
                    line_names=st_line_names,
                )
                merge_target = self.find_player_station_merge_target(
                    st_player, st_nm, st_x, st_y, exclude_pending_station_build=build,
                )
                if merge_target is None:
                    self.assert_ai_stop_minimum_direct_separation(
                        st_player,
                        st_x,
                        st_y,
                        exclude_pending_station_build=build,
                    )
                if merge_target is not None:
                    existing_lines = set(
                        merge_target.get_line_names() if hasattr(merge_target, 'get_line_names')
                        else getattr(merge_target, 'line_names', [])
                    )
                    st_line_names = [ln for ln in st_line_names if ln not in existing_lines]
                    owned = set(st_player.get_line_names() or [])
                    for line_name in st_line_names:
                        # The line may have been sold or abandoned while the stop was queued.
                        if line_name in owned and line_name in self.game.get_map().get_lines_by_name():
                            self.add_station_to_line(st_player, line_name, merge_target)
                else:
                    station = Station(
                        st_nm,
                        st_x,
                        st_y,
                        line_names=st_line_names,
                    )
                    for line_name in st_line_names:
                        all_lines = self.game.get_map().get_lines_by_name()
                        line = all_lines.get(line_name)

                        if line and line_name not in (build['player'].get_line_names() or []):
                            continue  # sold or abandoned while the stop was queued
                        if line:
                            self.add_station_to_line(build['player'], line_name, station)
                            if hasattr(station, 'add_line'):
                                station.add_line(line_name)
                        else:
                            partial_points = []
                            line_kind = {}
                            for build_item in self.game.build_queue:
                                if build_item['type'] == 'line' and build_item['data'].get('name') == line_name:
                                        partial_points.extend(build_item['data'].get('points', []))
                                        line_kind = build_item['data']
                            
                            if partial_points:
                                # Same kind of line as the queued build (bus and ferry lines too).
                                if line_kind.get('is_ferry_line'):
                                    from line import Ferry
                                    partial_line = Ferry(name=line_name, object_id=None, route=partial_points, owner=build['player'], vehicles=[])
                                    self.apply_timeline_fare_to_line(partial_line)
                                elif line_kind.get('is_bus_line'):
                                    from line import Bus
                                    partial_line = Bus(name=line_name, object_id=None, route=partial_points, owner=build['player'], vehicles=[])
                                    self.apply_timeline_fare_to_line(partial_line)
                                else:
                                    partial_line = self.game.create_line(line_name, build['player'], partial_points)
                                _record_built_long_edges(partial_line, partial_points)
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
            elif build['type'] == 'line_repair':
                repaired_line = self.game.get_map().get_lines_by_name().get(build['data'].get('line_name'))
                if repaired_line is not None:
                    _clear_rebuilt_destroyed_edges(repaired_line, build['data'].get('points') or [])
                    if not _get_line_destroyed_edges(repaired_line):
                        for attr in ('_disaster_damaged', '_disaster_damage_year', '_disaster_type'):
                            if hasattr(repaired_line, attr):
                                delattr(repaired_line, attr)
                    self.invalidate_ridership_cache()
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

        # AI lines go into service as soon as their second station opens.
        ai_players = []
        for build in completed_builds:
            p = build.get('player')
            if p is not None and getattr(p, 'is_ai', False) and p not in ai_players:
                ai_players.append(p)
        if ai_players:
            from ai import ai_enact_schedules_on_ready_lines
            for p in ai_players:
                try:
                    ai_enact_schedules_on_ready_lines(self, p)
                except Exception:
                    pass
    
    @staticmethod
    def _route_build_sequence_key(build: dict):
        """(line name, order) for jobs that add route to a line; None for other jobs.

        Original batches come first (by segment_index), then extensions (by extension_order,
        then segment_index).
        """
        data = build.get('data') or {}
        if build.get('type') == 'line' and 'segment_index' in data:
            return data.get('name'), (0, 0, int(data.get('segment_index') or 0))
        if build.get('type') == 'line_extension':
            return data.get('line_name'), (
                1, int(data.get('extension_order', 0) or 0), int(data.get('segment_index', 0) or 0),
            )
        return None

    def _hold_out_of_order_route_builds(self, completed_builds: list) -> list:
        done = {id(b) for b in completed_builds}
        ordered = sorted(
            completed_builds,
            key=lambda b: (self._route_build_sequence_key(b) or ('', ()))[1],
        )
        allowed = []
        for build in ordered:
            key = self._route_build_sequence_key(build)
            blocked = False
            if key is not None and key[0]:
                for other in self.game.build_queue:
                    if other is build or id(other) in done:
                        continue
                    other_key = self._route_build_sequence_key(other)
                    if other_key and other_key[0] == key[0] and other_key[1] < key[1]:
                        blocked = True
                        break
            if blocked:
                done.discard(id(build))
                build['work_remaining'] = 0.0
                build['turns_remaining'] = 1
            else:
                allowed.append(build)
        # Keep the queue order, except that route pieces take their slots in route order.
        allowed_ids = {id(b) for b in allowed}
        kept = [b for b in completed_builds if id(b) in allowed_ids]
        route_jobs = iter(sorted(
            (b for b in kept if self._route_build_sequence_key(b)),
            key=lambda b: self._route_build_sequence_key(b)[1],
        ))
        return [next(route_jobs) if self._route_build_sequence_key(b) else b for b in kept]

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
        elif build['type'] == 'line_repair':
            total_length, _ = _polyline_metrics(build['data'].get('points') or [])
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
        elif build_type == 'line_repair':
            total_length, _ = _polyline_metrics(build_data.get('points') or [])
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
        memo = getattr(self, '_sim_route_time_cache', None)
        if memo is None:
            return self._calculate_train_route_time(start_station, end_station, line, trainset)
        key = (id(start_station), id(end_station), id(line), id(trainset))
        if key not in memo:
            memo[key] = self._calculate_train_route_time(start_station, end_station, line, trainset)
        return memo[key]

    def _calculate_train_route_time(self, start_station, end_station, line, trainset) -> float:
        if not trainset or not hasattr(trainset, 'locomotive') or not trainset.locomotive:
            return None

        line_stations = line.get_stations()
        if len(line_stations) < 2:
            return None

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

        portion_index = self._operational_portion_index_for_station(line, start_station)
        if portion_index is None:
            return None
        if not self.stations_on_same_operational_portion(line, start_station, end_station):
            return None

        portions = self.get_line_operational_route_portions(line)
        portion = portions[portion_index]

        full_portion_time = self._calc_route_portion_traversal_time(
            line, portion, trainset, portion_index,
        )
        if full_portion_time <= 0:
            return None

        stations_on_portion = [
            station for station in line_stations
            if self._operational_portion_index_for_station(line, station) == portion_index
        ]
        start_local = end_local = None
        for i, station in enumerate(stations_on_portion):
            if station is start_station or (
                station.get_x() == start_station.get_x() and station.get_y() == start_station.get_y()
            ):
                start_local = i
            if station is end_station or (
                station.get_x() == end_station.get_x() and station.get_y() == end_station.get_y()
            ):
                end_local = i
        if start_local is None or end_local is None:
            return None

        num_stations_between = abs(end_local - start_local)
        total_on_portion = len(stations_on_portion)
        if total_on_portion > 1:
            return (num_stations_between / (total_on_portion - 1)) * full_portion_time
        return full_portion_time
    
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
                    if not self._transfer_station_pairs(origin_line, dest_line):
                        continue  # the two lines share no station: no transfer possible
                    transfer_route = self.find_route_with_transfers(origin_station, origin_line, origin_walk_dist,
                                                                    dest_station, dest_line, dest_walk_dist)
                    if transfer_route and transfer_route.get('score', float('inf')) < best_route['score']:
                        best_route = transfer_route
        
        return best_route
    
    def calculate_driving_time(self, distance: float, driving_speed_mph: float = 25.0) -> float:
        """Calculate driving time in hours. Default speed is 25 mph (early cars)."""
        return distance / driving_speed_mph
    
    def _transfer_station_pairs(self, origin_line, dest_line) -> list:
        """[(station on origin_line, station at the same position on dest_line)].

        Cached for the duration of a ridership pass: most line pairs share no station, and
        this used to be rediscovered by a nested scan for every sampled trip.
        """
        memo = getattr(self, '_sim_transfer_pairs_cache', None)
        key = (id(origin_line), id(dest_line))
        if memo is not None and key in memo:
            return memo[key]
        by_pos = {}
        for ds in dest_line.get_stations():
            by_pos.setdefault((ds.get_x(), ds.get_y()), ds)
        pairs = []
        for st in origin_line.get_stations():
            match = by_pos.get((st.get_x(), st.get_y()))
            if match is not None:
                pairs.append((st, match))
        if memo is not None:
            memo[key] = pairs
        return pairs

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
        
        for transfer_station, dest_line_station_at_transfer in self._transfer_station_pairs(
            origin_line, dest_line,
        ):
            
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
    
    def _ridership_served_points(self, population: dict, max_walk: float = 5.0) -> list:
        """Populated points within walking distance of a line's station (find_stations_near_point).

        Only trips between two such points can use a line; every other trip walks or drives.
        """
        served = set()
        reach = int(math.ceil(max_walk))
        for line in (self.game.get_map().get_lines_by_name() or {}).values():
            for station in line.get_stations():
                sx, sy = float(station.get_x()), float(station.get_y())
                for gx in range(int(math.floor(sx)) - reach, int(math.ceil(sx)) + reach + 1):
                    for gy in range(int(math.floor(sy)) - reach, int(math.ceil(sy)) + reach + 1):
                        if (population.get((gx, gy)) or 0) > 0 and math.hypot(gx - sx, gy - sy) <= max_walk:
                            served.add((gx, gy))
        return sorted(served)

    def calculate_ridership_for_year(self, current_year: int) -> dict:
        """Riders per line for the year: {line_name: passengers}.

        Each resident makes one trip a year, between two points drawn by population. A trip can only ride a line when both ends are within walking
        distance of a station, so the simulated sample is drawn among those "served" points
        and each simulated trip stands for its share of all such trips.
        """
        import random
        import time

        line_passengers = {}  # line_name -> number of passengers
        game_map = self.game.get_map()
        if not getattr(game_map, 'stations', None):
            return line_passengers
        population = {pt: pop for pt, pop in game_map.population.items() if pop > 0}
        total_population = sum(population.values())
        if len(population) < 2 or total_population <= 0:
            return line_passengers
        served = self._ridership_served_points(population)
        if len(served) < 2:
            return line_passengers
        cumulative = []
        cum = 0
        for pt in served:
            cum += population[pt]
            cumulative.append(cum)
        served_share = cum / total_population

        # The sample grows with the population (RIDERSHIP_SAMPLED_TRIPS_PER_RESIDENT); the time
        # budget keeps End Year responsive.
        sample_size = ridership_sample_size(total_population)

        start_time = time.time()
        self._route_stations_cache = {}
        # Nothing about the network changes during one pass, so station-to-station times and
        # each station's route portion are computed once instead of once per sampled trip.
        self._sim_route_time_cache = {}
        self._sim_portion_index_cache = {}
        self._sim_transfer_pairs_cache = {}
        for line in (game_map.get_lines_by_name() or {}).values():
            if getattr(line, '_quality_updated_year', None) != current_year:
                line.update_quality()
                line._quality_updated_year = current_year
        # The same draws every year (seeded per game): a line's riders change when the network
        # or the population changes, not with the luck of a fresh random sample.
        rng = random.Random(int(getattr(self.game, '_ai_rng_seed', 0) or 0) ^ 0x5EED)
        route_memo: dict = {}  # (origin, destination) -> lines ridden (the network is fixed this pass)
        from collections import Counter
        n = len(served)
        weights = [population[pt] / cum for pt in served]
        # Points drawn by population from a table where each fills slots in proportion to its
        # people (an index lookup per draw instead of a search over cumulative weights).
        slots = max(1 << 16, 64 * n)
        draw_table = []
        prev = 0
        for idx, cum_pop in enumerate(cumulative):
            end = round(cum_pop / cum * slots)
            draw_table.extend([idx] * (end - prev))
            prev = end
        # Every trip's origin and destination are drawn by population. Drawing how many trips
        # start at each point, then how many of those go to each destination, gives the same
        # counts as drawing the trips one by one, at a cost that does not grow with the number
        # of trips. Origins go in random order so a pass cut short by the time budget is still
        # a fair sample.
        origin_counts = _multinomial_counts(rng, sample_size, weights)
        origins = [i for i in range(n) if origin_counts[i] > 0]
        rng.shuffle(origins)
        simulated = 0
        riders = {}  # line name -> expected riders among the simulated trips
        for origin_idx in origins:
            if time.time() - start_time > RIDERSHIP_TIME_BUDGET_SEC:
                break
            count = origin_counts[origin_idx]
            if count < n:
                destinations = Counter(rng.choices(draw_table, k=count)).items()
            else:
                destinations = enumerate(_multinomial_counts(rng, count, weights))
            origin_cell = served[origin_idx]
            for dest_idx, trips in destinations:
                if not trips or dest_idx == origin_idx:
                    continue  # a trip that goes nowhere rides nothing
                # People live and work all over a cell, not at its centre: each pair of cells
                # travels between spots spread across the two cells (walks to stations differ).
                ox, oy = _CELL_SPREAD_OFFSETS[rng.randrange(len(_CELL_SPREAD_OFFSETS))]
                dx, dy = _CELL_SPREAD_OFFSETS[rng.randrange(len(_CELL_SPREAD_OFFSETS))]
                origin = (origin_cell[0] + ox, origin_cell[1] + oy)
                destination = (served[dest_idx][0] + dx, served[dest_idx][1] + dy)
                lines_used = route_memo.get((origin, destination))
                if lines_used is None:
                    route = self.find_fastest_route(origin, destination)
                    lines_used = (
                        tuple(route.get('lines') or ())
                        if route['method'] in ('train', 'train_transfer') else ()
                    )
                    route_memo[(origin, destination)] = lines_used
                for line in lines_used:
                    share = min(1.0, max(0.0, 0.5 + (line.quality - 1.0) * 0.15))  # take the train
                    riders[line.name] = riders.get(line.name, 0.0) + trips * share
            simulated += count
            time.sleep(0)  # let the UI thread run (the switch interval does the rest)

        self.last_ridership_sample_size = simulated  # trips simulated (the time budget may cut it)
        # Each simulated trip stands for an equal share of the year's trips between served points.
        trips_between_served_points = total_population * served_share ** 2 * RIDERSHIP_TRIPS_PER_RESIDENT_PER_YEAR
        scale_factor = trips_between_served_points / max(1, simulated)
        line_passengers = {k: int(v * scale_factor) for k, v in riders.items() if v > 0}

        self._route_stations_cache = None
        self._sim_route_time_cache = None
        self._sim_portion_index_cache = None
        self._sim_transfer_pairs_cache = None
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
        line._cached_ridership = int(passengers_per_year or 0)
        line._cached_ridership_year = int(current_year)
        
        if not line.vehicles or len(line.vehicles) == 0:
            line.profit = 0
            line._cached_ridership = int(passengers_per_year or 0)
            line._cached_ridership_year = int(current_year)
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
        
        actual_passengers = self.passengers_served_from_ridership_and_schedule(
            line,
            passengers_per_year,
            fastest_speed,
            total_capacity_per_trip,
            current_year=current_year,
        )
        if actual_passengers <= 0:
            line.profit = 0
            return 0
        
        line.update_quality()
        quality_multiplier = 1.0 + ((line.quality - 1.0) * 0.2)
        # Fare revenue: riders times fare (line quality nudges it up or down).
        total_profit = actual_passengers * effective_fare * quality_multiplier
        
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
                
                # Mark destroyed segments; repair jobs go straight into the owner's construction queue.
                for segment_idx in segment_indices:
                    segment_points = [route[segment_idx], route[segment_idx + 1]]
                    _mark_line_edges_destroyed(line, segment_points)
                self.queue_line_repairs(line)
                
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
        if damaged_lines:
            self.invalidate_ridership_cache()
    
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
        from map_preset_loader import DEFAULT_PRESET_NAME, get_preset_public_ta_config as _default_ta
        return _default_ta(DEFAULT_PRESET_NAME)
    
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

        seen_keys = set()
        stations_data = []

        def can_edit_station(line_names: list) -> bool:
            if not line_names or current_player is None:
                return True
            for ln in line_names:
                line = self.get_map_lines().get(ln)
                if line and self.get_line_owner(line) != current_player:
                    return False
            return True

        def append_station(x, y, name, under_construction, line_names, station_obj=None):
            key = (round(float(x), 3), round(float(y), 3))
            if key in seen_keys:
                return
            lat, lon = grid_to_lat_lon(float(x), float(y))
            if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                return
            seen_keys.add(key)
            line_names = list(line_names or [])
            num_lines = len(line_names)
            editable = can_edit_station(line_names)
            entry = {
                'lat': lat,
                'lon': lon,
                'name': name or 'Station',
                'under_construction': bool(under_construction),
                'can_close': editable,
                'can_rename': editable,
                'line_names': line_names,
                'is_merged': num_lines >= 2,
                'num_lines': num_lines,
            }
            if station_obj is not None and not under_construction:
                entry.update(self.get_station_last_year_display_stats(station_obj))
                if current_player is not None and editable:
                    expand = self.station_expansion_lines(current_player, station_obj)
                    if expand:
                        entry['expand_lines'] = expand
                        entry['expand_cost'] = self.station_line_expansion_cost()
            stations_data.append(entry)

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
            append_station(x, y, name, False, line_names, station_obj=station)

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
                append_station(x, y, name, False, line_names, station_obj=station)

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
                from map_preset_loader import default_grid_population_at
                game_map.population = {}
                for x in range(game_map.width + 1):
                    for y in range(game_map.height + 1):
                        pop = default_grid_population_at(x, y, game_map.width, game_map.height)
                        game_map.population[(x, y)] = pop
        
        if not game_map.population:
            return population_data
        
        # Get map dimensions
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width and game_map.width > 0 else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height and game_map.height > 0 else 50
        
        # Preset land cells come from INITIAL_POPULATION in the preset file (source of truth).
        # Uses preset_info (from map_preset_name) so every map works without core changes
        preset_info = None
        if hasattr(game_map, 'map_preset_name') and game_map.map_preset_name:
            from map_preset_utils import get_preset_file_info
            preset_info = get_preset_file_info(game_map)
        
        included_points = None
        deleted_points = (
            (getattr(game_map, '_deleted_population_points', set()) or set())
            | (getattr(self, '_session_deleted_population_points', set()) or set())
        )
        if game_map.population:
            included_points = {
                pt for pt, pop in game_map.population.items()
                if pop and int(pop) > 0
            } - deleted_points
        elif preset_info:
            from map_preset_utils import read_included_points_from_file
            file_path, variable_name = preset_info
            file_points = read_included_points_from_file(file_path, variable_name)
            if file_points:
                included_points = file_points - deleted_points
        if included_points is None and preset_info:
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