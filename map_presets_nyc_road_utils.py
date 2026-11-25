"""Road and bridge point-checking utilities for NYC map.
Used by the NYC and NY Metro maps; city data comes from nyc_city_data.
Uses spatial index to expedite street detection without sacrificing accuracy."""

import heapq
import logging
import math
from weakref import WeakKeyDictionary

from nyc_city_data import get_nyc_bounds
from map_preset_utils import ai_grid_polyline_overlap_tolerance, polyline_has_self_overlap

logger = logging.getLogger(__name__)


def _ai_self_overlap_tolerance(game_map) -> float:
    """Delegate to shared OSM grid tolerance (see map_preset_utils.ai_grid_polyline_overlap_tolerance)."""
    return ai_grid_polyline_overlap_tolerance(game_map)


# Use OSM-generated roads if available (have 'route' polylines); else fall back to map_presets roads
# Prefer JSON over .py - 76MB Python parse is 60+s; JSON is ~5-10s
_OSM_ROADS_CACHE = None

def _get_nyc_roads():
    global _OSM_ROADS_CACHE
    if _OSM_ROADS_CACHE is not None:
        return _OSM_ROADS_CACHE
    import os
    json_path = os.path.join(os.path.dirname(__file__), 'nyc_historical_roads_osm_generated.json')
    if os.path.exists(json_path):
        try:
            try:
                import orjson
                with open(json_path, 'rb') as f:
                    OSM_ROADS = orjson.loads(f.read())
            except ImportError:
                import json
                with open(json_path, 'r', encoding='utf-8') as f:
                    OSM_ROADS = json.load(f)
            if OSM_ROADS and any(r.get('route') and len(r.get('route', [])) >= 2 for r in OSM_ROADS.values()):
                _OSM_ROADS_CACHE = OSM_ROADS
                return OSM_ROADS
        except Exception:
            pass
    try:
        from nyc_historical_roads_osm_generated import NYC_HISTORICAL_ROADS as OSM_ROADS
        if OSM_ROADS and any(r.get('route') and len(r.get('route', [])) >= 2 for r in OSM_ROADS.values()):
            _OSM_ROADS_CACHE = OSM_ROADS
            return OSM_ROADS
    except ImportError:
        pass
    from nyc_city_data import NYC_HISTORICAL_ROADS
    _OSM_ROADS_CACHE = NYC_HISTORICAL_ROADS
    return NYC_HISTORICAL_ROADS

# Generic name used by code that works with any map's road data
get_all_roads = _get_nyc_roads


def road_data_files_status() -> list:
    """[(file name, present)] for the data files the street network is built from."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    names = ('nyc_road_spatial_index.pkl', 'nyc_historical_roads_osm_generated.json')
    return [(name, os.path.isfile(os.path.join(here, name))) for name in names]


# Shown when the street network could not be loaded.
ROAD_DATA_FIX_HINT = (
    "  • Fix: run `python build_road_spatial_index.py` after OSM road JSON is generated",
    "    (or place a pre-built nyc_road_spatial_index.pkl beside the game modules).",
)


# Cache grid polylines per road and per cell-set (AI validation calls this heavily).
_ROAD_GRID_POLYLINE_BY_ID: dict = {}

_ROAD_GRID_POLYLINE_BOUNDS_SIG = None
_POLYLINES_FOR_CELLS_CACHE: dict = {}
# Spatial index: (cell_lat, cell_lon) -> list of (road_name, road, seg_idx). Built lazily.
_ROAD_SPATIAL_INDEX = None
_BRIDGE_SPATIAL_INDEX = None
# Invalidate index when switching maps (e.g. NYC proper vs NY Metro); bounds must match game_map preset.
_SPATIAL_INDEX_BOUNDS_SIG = None
_CELL_SIZE = 0.02  # ~2km - balances speed vs accuracy

# AI road feature cache (per game_map instance).
# Building these features (lat/lon->grid sampling + population stats) is the expensive part of AI street search.
# Cache it so multiple AIs / multiple min_endpoint_population thresholds in the same year do not recompute it.
# Use WeakKeyDictionary — never key by id(game_map): CPython reuses ids after GC, so unittest order could
# attach NYC road features to a new NY Metro Map that happened to get the same id.
_AI_ROAD_FEATURES_CACHE = WeakKeyDictionary()  # game_map -> {max_points: list[dict]}


def _spatial_index_bounds_signature(game_map) -> tuple:
    """Stable signature for the bbox used to filter roads into the spatial index."""
    b = _get_bounds(game_map, None)
    return (
        round(float(b["min_lat"]), 5),
        round(float(b["max_lat"]), 5),
        round(float(b["min_lon"]), 5),
        round(float(b["max_lon"]), 5),
        int(getattr(game_map, "width", 0) or 0),
        int(getattr(game_map, "height", 0) or 0),
        getattr(game_map, "map_preset_name", None) or "",
    )


def _build_road_spatial_index(game_map=None):
    """Build spatial index for fast road lookups. One-time cost.
    Loads pre-built index from nyc_road_spatial_index.pkl if available (~2s); else builds at runtime (~25s)."""
    global _ROAD_SPATIAL_INDEX, _BRIDGE_SPATIAL_INDEX, _SPATIAL_INDEX_BOUNDS_SIG
    global _ROAD_GRID_POLYLINE_BOUNDS_SIG
    sig = _spatial_index_bounds_signature(game_map) if game_map is not None else None
    if game_map is not None:
        if _ROAD_SPATIAL_INDEX is not None and _SPATIAL_INDEX_BOUNDS_SIG == sig:
            return
        # Wrong map / preset was used first (common when tests import map_preset_loader early).
        _ROAD_SPATIAL_INDEX = None
        _BRIDGE_SPATIAL_INDEX = None
        _SPATIAL_INDEX_BOUNDS_SIG = None
        _POLYLINES_FOR_CELLS_CACHE.clear()
        _ROAD_GRID_POLYLINE_BY_ID.clear()
        _ROAD_GRID_POLYLINE_BOUNDS_SIG = None
        _POINT_ON_ROAD_CACHE.clear()
    elif _ROAD_SPATIAL_INDEX is not None:
        return
    import os
    pkl_path = os.path.join(os.path.dirname(__file__), 'nyc_road_spatial_index.pkl')
    if os.path.exists(pkl_path):
        try:
            import pickle
            with open(pkl_path, 'rb') as f:
                data = pickle.load(f)
            idx = data.get('road', {})
            if idx:
                globals()["_ROAD_SPATIAL_INDEX"] = idx
                globals()["_BRIDGE_SPATIAL_INDEX"] = data.get("bridge", {})
                globals()["_SPATIAL_INDEX_BOUNDS_SIG"] = sig if sig is not None else "__pkl__"
                return
            logger.warning("Pre-built road index %s is empty; building at runtime. Run: python build_road_spatial_index.py", pkl_path)
        except Exception as e:
            logger.warning("Pre-built road index load failed (%s): %s. Building at runtime. Run: python build_road_spatial_index.py to regenerate.", type(e).__name__, e)
    from collections import defaultdict
    road_idx = defaultdict(list)
    bridge_idx = defaultdict(list)
    roads = _get_nyc_roads()
    roads_in_index = 0
    _MAX_ROADS_IN_INDEX = 8000  # Index 8k in-bounds roads (~5s); 39k takes 60s
    # Optional bounds filter: skip roads outside map
    map_cell_min = map_cell_max = None
    if game_map:
        b = _get_bounds(game_map, None)
        map_cell_min = (int(b['min_lat'] / _CELL_SIZE), int(b['min_lon'] / _CELL_SIZE))
        map_cell_max = (int(b['max_lat'] / _CELL_SIZE), int(b['max_lon'] / _CELL_SIZE))
    # Prioritize roads in high-population 6x6 sections so they're indexed first
    population_4x4 = _build_population_4x4_grid(game_map) if game_map else None
    road_items = sorted(roads.items(), key=lambda x: _road_priority_by_population_grid(x[1], game_map, None, population_4x4), reverse=True)
    for road_name, road in road_items:
        route = road.get('route', [])
        if len(route) < 2:
            continue
        if map_cell_min is not None:
            b = road.get('bounds')
            if b:
                rc_min = (int(b['min_lat'] / _CELL_SIZE), int(b['min_lon'] / _CELL_SIZE))
                rc_max = (int(b['max_lat'] / _CELL_SIZE), int(b['max_lon'] / _CELL_SIZE))
            else:
                r_lat = [r[0] for r in route]
                r_lon = [r[1] for r in route]
                rc_min = (int(min(r_lat) / _CELL_SIZE), int(min(r_lon) / _CELL_SIZE))
                rc_max = (int(max(r_lat) / _CELL_SIZE), int(max(r_lon) / _CELL_SIZE))
            if rc_max[0] < map_cell_min[0] or rc_min[0] > map_cell_max[0] or rc_max[1] < map_cell_min[1] or rc_min[1] > map_cell_max[1]:
                continue
        roads_in_index += 1
        if roads_in_index > _MAX_ROADS_IN_INDEX:
            break
        name_lower = road_name.lower()
        is_bridge = ('bridge' in name_lower) or ('tunnel' in name_lower)
        for i in range(len(route) - 1):
            a_lat, a_lon = route[i][0], route[i][1]
            b_lat, b_lon = route[i + 1][0], route[i + 1][1]
            # Only add to 2 cells (segment endpoints) - was O(cells spanned), now O(1) per segment
            cells = {(int(a_lat / _CELL_SIZE), int(a_lon / _CELL_SIZE)), (int(b_lat / _CELL_SIZE), int(b_lon / _CELL_SIZE))}
            for key in cells:
                road_idx[key].append((road_name, road, i))
                if is_bridge:
                    bridge_idx[key].append((road_name, road, i))
    _ROAD_SPATIAL_INDEX = road_idx
    _BRIDGE_SPATIAL_INDEX = bridge_idx
    if sig is not None:
        _SPATIAL_INDEX_BOUNDS_SIG = sig
    else:
        _SPATIAL_INDEX_BOUNDS_SIG = "__built_no_map__"


def _get_bounds(game_map, bounds=None):
    """Get bounds for coordinate conversion. Uses provided bounds or fetches from map preset."""
    if bounds is not None:
        return bounds
    if game_map is not None:
        pn = getattr(game_map, "map_preset_name", None)
        if pn:
            try:
                from map_preset_loader import get_map_bounds

                b = get_map_bounds(pn)
                if b and "min_lat" in b:
                    return b
            except Exception:
                pass
    return get_nyc_bounds()


def _get_roads_in_map_bounds(game_map, bounds=None):
    """Return unique (road_name, road) for roads that intersect map bounds. Uses spatial index."""
    _build_road_spatial_index(game_map)
    b = _get_bounds(game_map, bounds)
    min_lat, max_lat = b['min_lat'], b['max_lat']
    min_lon, max_lon = b['min_lon'], b['max_lon']
    cell_lat_min = int(min_lat / _CELL_SIZE)
    cell_lat_max = int(max_lat / _CELL_SIZE)
    cell_lon_min = int(min_lon / _CELL_SIZE)
    cell_lon_max = int(max_lon / _CELL_SIZE)
    seen = {}
    for clat in range(cell_lat_min, cell_lat_max + 1):
        for clon in range(cell_lon_min, cell_lon_max + 1):
            for road_name, road, _ in _ROAD_SPATIAL_INDEX.get((clat, clon), []):
                if road_name not in seen:
                    seen[road_name] = road
    return list(seen.items())


_GRID_COLS = 6
_GRID_ROWS = 6


def _ai_road_passes_static_filters(road_name: str, route: list) -> bool:
    """Cheap name/geography filters shared by feature prefilter and full feature build."""
    if len(route) < 2:
        return False
    if all(p[1] < -74.05 for p in route):
        return False
    cent_lat = sum(p[0] for p in route) / len(route)
    cent_lon = sum(p[1] for p in route) / len(route)
    if 40.686 <= cent_lat <= 40.698 and -74.022 <= cent_lon <= -74.002:
        return False
    name_lower = (road_name or "").lower()
    if any(
        x in name_lower
        for x in (
            "footpath", "bikepath", "pathway", "walkpath", "pedestrian_path",
            "governor", "sidewalk", "pedestrian", "esplanade", "promenade",
        )
    ):
        return False
    if name_lower.endswith("_path") or " path" in name_lower.replace("_", " "):
        return False
    if "cemetery" in name_lower or "greenway" in name_lower:
        return False
    if (" park" in name_lower or name_lower.startswith("park ")) and not any(
        x in name_lower
        for x in ("avenue", "_ave", "place", "_pl", "road", "_rd", "street", "_st", "blvd", "drive", "_dr")
    ):
        return False
    if " trail" in name_lower or name_lower.endswith("_trail"):
        return False
    return True


def _prefilter_roads_for_ai_features(roads_items, game_map, bounds, population_6x6: list, limit: int) -> list:
    """Keep top ``limit`` roads by 6x6 population density using only endpoint/midpoint samples."""
    w = max(1, getattr(game_map, "width", 1))
    h = max(1, getattr(game_map, "height", 1))
    heap = []
    for road_name, road in roads_items:
        route = road.get("route", [])
        if not _ai_road_passes_static_filters(road_name, route):
            continue
        samples = (route[0], route[len(route) // 2], route[-1])
        max_cell = 0
        for lat, lon in samples:
            x, y = _latlon_to_grid(lat, lon, game_map, bounds)
            cx = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * x / w)))
            cy = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * y / h)))
            max_cell = max(max_cell, population_6x6[cx][cy])
        if max_cell <= 0:
            continue
        if len(heap) < limit:
            heapq.heappush(heap, (max_cell, road_name, road))
        elif max_cell > heap[0][0]:
            heapq.heapreplace(heap, (max_cell, road_name, road))
    return [(name, road) for _score, name, road in sorted(heap, reverse=True)]


def _get_ai_road_features(game_map, bounds=None, max_points: int = 8) -> list:
    """Precompute and cache per-road features used by get_road_segment_points_for_ai.

    This avoids repeated lat/lon->grid conversion and population scans across many AI calls.
    Returns a list of feature dicts, pre-sorted by population priority (highest first).
    """
    mp_key = int(max_points or 8)
    sub = _AI_ROAD_FEATURES_CACHE.get(game_map)
    if sub is not None:
        cached = sub.get(mp_key)
        if cached is not None:
            return cached

    _build_road_spatial_index(game_map)
    b = _get_bounds(game_map, bounds)
    w = max(1, getattr(game_map, "width", 1))
    h = max(1, getattr(game_map, "height", 1))
    pop_map = getattr(game_map, "population", None) or {}
    population_6x6 = _build_population_4x4_grid(game_map)

    roads_items = _get_roads_in_map_bounds(game_map, b)
    if len(roads_items) > _MAX_ROADS_FOR_FEATURE_BUILD:
        roads_items = _prefilter_roads_for_ai_features(
            roads_items, game_map, b, population_6x6, _MAX_ROADS_FOR_FEATURE_BUILD,
        )

    feats = []

    for road_name, road in roads_items:
        route = road.get("route", [])
        if not _ai_road_passes_static_filters(road_name, route):
            continue
        name_lower = (road_name or "").lower()

        # Sample grid points (same approach as selection logic).
        # IMPORTANT: keep the full sampled polyline for scoring/length checks; truncation to max_points
        # should happen only at the final return. Truncating too early makes roads look shorter and can
        # eliminate all candidates (AIs then idle).
        mp = max(2, int(max_points or 8))
        step = max(1, (len(route) - 1) // max(1, mp - 1))
        raw_points = []
        for i in range(0, len(route), step):
            lat, lon = route[i][0], route[i][1]
            x, y = _latlon_to_grid(lat, lon, game_map, b)
            raw_points.append((float(x), float(y)))
        if len(raw_points) < 2:
            continue

        in_bounds_count = sum(1 for (px, py) in raw_points if 0 <= px <= w and 0 <= py <= h)
        clamped_points = [(max(0, min(w, px)), max(0, min(h, py))) for (px, py) in raw_points]

        total_length = sum(
            math.hypot(clamped_points[i + 1][0] - clamped_points[i][0], clamped_points[i + 1][1] - clamped_points[i][1])
            for i in range(len(clamped_points) - 1)
        )
        if total_length <= 0:
            continue

        # Precompute population-related stats (Manhattan-aware; see _ai_pop_at_road_point).
        b = _get_bounds(game_map, None)
        p1, p2 = clamped_points[0], clamped_points[-1]
        pop1 = _ai_pop_at_road_point(p1[0], p1[1], game_map, b, pop_map)
        pop2 = _ai_pop_at_road_point(p2[0], p2[1], game_map, b, pop_map)
        pop_sample_pts = clamped_points
        if len(pop_sample_pts) > 8:
            step_p = max(1, len(pop_sample_pts) // 8)
            pop_sample_pts = pop_sample_pts[::step_p][:8]
        with_pop = sum(
            1 for (px, py) in pop_sample_pts if _ai_pop_at_road_point(px, py, game_map, b, pop_map) > 0
        )

        max_pop_cell = 0
        for (px, py) in pop_sample_pts:
            cx = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * px / w)))
            cy = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * py / h)))
            max_pop_cell = max(max_pop_cell, population_6x6[cx][cy])
        for (px, py) in (p1, p2):
            cx = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * px / w)))
            cy = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * py / h)))
            max_pop_cell = max(max_pop_cell, population_6x6[cx][cy])

        # Cache centroid cell for in_used_cell biasing.
        cx_avg = sum(pp[0] for pp in clamped_points) / len(clamped_points)
        cy_avg = sum(pp[1] for pp in clamped_points) / len(clamped_points)
        cell_x = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * cx_avg / w)))
        cell_y = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * cy_avg / h)))

        feats.append({
            "name": road_name,
            "year": int(road.get("year", 1800) or 1800),
            "removed_year": road.get("removed_year"),
            "is_bridge": ("bridge" in name_lower) or ("tunnel" in name_lower),
            "raw_points": raw_points,
            "points_full": clamped_points,
            "in_bounds_count": int(in_bounds_count),
            "length": float(total_length),
            "pop1": int(pop1),
            "pop2": int(pop2),
            "with_pop": int(with_pop),
            "points_n": int(len(clamped_points)),
            "max_pop_cell": int(max_pop_cell),
            "cell_x": int(cell_x),
            "cell_y": int(cell_y),
        })

    # Pre-sort by population priority so selection can early-exit without sorting repeatedly.
    feats.sort(key=lambda f: (f["max_pop_cell"], f["pop1"] + f["pop2"], f["length"]), reverse=True)
    if sub is None:
        sub = {}
        _AI_ROAD_FEATURES_CACHE[game_map] = sub
    sub[mp_key] = feats
    return feats


def _grid_to_latlon(x: float, y: float, game_map, bounds=None) -> tuple:
    """Convert grid coordinates (x, y) to (lat, lon)."""
    b = _get_bounds(game_map, bounds)
    min_lat = b['min_lat']
    max_lat = b['max_lat']
    min_lon = b['min_lon']
    max_lon = b['max_lon']
    w = max(1, game_map.width)
    h = max(1, game_map.height)
    lat = min_lat + (y / h) * (max_lat - min_lat)
    lon = min_lon + (x / w) * (max_lon - min_lon)
    return (lat, lon)


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    r_earth = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(max(0.0, a))))
    return r_earth * c


# AI station sites: max distance to a population key's grid anchor for the count to apply.
# ~750m blocks counting Manhattan cells from a NJ/Hudson-west site while still snapping to
# neighbors on the same landmass (sparse grid vs street vertices).
_AI_STATION_SITE_POP_MAX_METERS = 750.0


def population_at_station_site_geographic(
    game_map, px: float, py: float, pop_map: dict, bounds: dict, max_meters: float = None
) -> int:
    """Population for AI station validation using geodesic distance between grid anchors.

    Grid-neighbor search alone can attribute NYC population to Hudson/NJ sites several km away
    when only a few cells separate them on a coarse map. This only counts population cells whose
    (grid point -> lat/lon) is within max_meters of the query site."""
    if not isinstance(pop_map, dict) or not pop_map or game_map is None or not bounds:
        return 0
    cap = float(_AI_STATION_SITE_POP_MAX_METERS if max_meters is None else max_meters)
    try:
        qlat, qlon = _grid_to_latlon(float(px), float(py), game_map, bounds)
    except Exception:
        return 0
    best = 0
    for (gx, gy), p in pop_map.items():
        try:
            pv = int(p or 0)
        except (TypeError, ValueError):
            continue
        if pv <= 0:
            continue
        try:
            plat, plon = _grid_to_latlon(float(gx), float(gy), game_map, bounds)
        except Exception:
            continue
        if _haversine_distance_m(qlat, qlon, plat, plon) <= cap and pv > best:
            best = pv
    return best


def _latlon_to_grid(lat: float, lon: float, game_map, bounds=None) -> tuple:
    """Convert (lat, lon) to grid (x, y)."""
    b = _get_bounds(game_map, bounds)
    min_lat = b['min_lat']
    max_lat = b['max_lat']
    min_lon = b['min_lon']
    max_lon = b['max_lon']
    w = max(1, game_map.width)
    h = max(1, game_map.height)
    if max_lon - min_lon == 0 or max_lat - min_lat == 0:
        return (w / 2, h / 2)
    x = (lon - min_lon) / (max_lon - min_lon) * w
    y = (lat - min_lat) / (max_lat - min_lat) * h
    return (x, y)


# Max road segments to check for extension (80 was 10s+ per call; 25 is enough for nearby roads)
_MAX_EXTEND_ROAD_CHECKS = 25
# Max roads to check for AI line building (spatial index pre-filters to map bounds)
_MAX_ROADS_FOR_AI_BUILD = 200
# Cap full per-road feature build (population scans); NYC has 10k+ OSM roads in bounds.
_MAX_ROADS_FOR_FEATURE_BUILD = 600
# Stop early when we have enough candidates (only top 5 used)
_MIN_CANDIDATES_BEFORE_EARLY_EXIT = 8
# Cap how many roads we fully score on NYC turn-1 high-pop scans (was unbounded via max_roads).
_MAX_DETERMINISTIC_FIRST_LINE_ROAD_SCAN = 72


def count_available_line_corridors(game_map, current_year: int, bounds=None) -> dict:
    """Count OSM street corridors available for AI line building at ``current_year``."""
    _build_road_spatial_index(game_map)
    roads_items = _get_roads_in_map_bounds(game_map, _get_bounds(game_map, bounds))
    buildable = 0
    named: set = set()
    for road_name, road in roads_items:
        route = road.get('route', [])
        if not _ai_road_passes_static_filters(road_name, route):
            continue
        year_built = road.get('year', 1800)
        removed_year = road.get('removed_year')
        if year_built > current_year or (removed_year is not None and removed_year <= current_year):
            continue
        if len(route) < 2:
            continue
        buildable += 1
        if road_name:
            named.add(str(road_name))
    return {
        'total_roads_in_bounds': len(roads_items),
        'buildable_corridors': buildable,
        'unique_named_streets': len(named),
    }


# Radius for AI road selection: count population within this many grid cells of a point.
# Radius 4 lets Manhattan street endpoints (often between population cells) still qualify;
# strict checks along the route and empty-cell filter still avoid Governors Island / waterfront.
_AI_POPULATION_RADIUS_STRICT = 2
# Manhattan (OSM NYC): population dots are sparse vs the street grid; endpoints often fall between
# included cells. Use a looser search radius only inside this bbox so corridors still qualify without
# letting distant-waterfront roads borrow population across the harbor.
# Manhattan-aware population boost.
#
# IMPORTANT: These values are in NYC grid lat/lon space produced by `_grid_to_latlon`
# (which uses the active NYC bounds). They must be wide enough to cover the grid
# cells used by `NYC_INCLUDED_POINTS` / OSM street vertices in lower and midtown.
_MANHATTAN_POP_BOOST_LAT_MIN = 40.48
_MANHATTAN_POP_BOOST_LAT_MAX = 40.82
_MANHATTAN_POP_BOOST_LON_MIN = -74.25
_MANHATTAN_POP_BOOST_LON_MAX = -73.74
_MANHATTAN_POP_SEARCH_RADIUS = 10
# Hard floor to prevent AI routes in very low-population areas (e.g. NJ waterfront dead zones).
_AI_MIN_ENDPOINT_POP_FLOOR = 100
_AI_MIN_ROUTE_POP_FRACTION_FLOOR = 0.40


def _ai_pop_at_road_point(px: float, py: float, game_map, bounds, pop_map: dict) -> int:
    """Population near (px,py) for AI road scoring.

    NYC/NY Metro road vertices frequently land between sparse population keys (grid misalignment vs
    street polyline sampling). For AI scoring we therefore use a wider lookup radius consistently
    so Manhattan + lower/midtown corridors are not filtered out by endpoint/route pop thresholds.
    """
    if not pop_map or game_map is None:
        return 0
    # Use a wider radius to tolerate sparse NYC population-key alignment.
    return _population_at_or_near(px, py, pop_map, radius=_MANHATTAN_POP_SEARCH_RADIUS)


def _road_centroid_in_empty_cell(points: list, game_map, population_6x6: list) -> bool:
    """True if the road's centroid falls in a 6x6 cell with zero population and the map has population elsewhere.
    Map-agnostic: avoids building in empty regions (islands, water) when populated areas exist."""
    if not points or not population_6x6:
        return False
    total = sum(sum(row) for row in population_6x6)
    if total == 0:
        return False  # No population anywhere: don't filter (e.g. no population layer)
    w = max(1, getattr(game_map, 'width', 1))
    h = max(1, getattr(game_map, 'height', 1))
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    cell_x = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * cx / w)))
    cell_y = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * cy / h)))
    return population_6x6[cell_x][cell_y] == 0


def _build_population_4x4_grid(game_map) -> list:
    """Build a 6x6 grid of total population from the map. Returns grid[col][row] (0..5 each).
    Map-agnostic: divides map width/height into 6x6 sections (36 sections) and sums population in each."""
    pop_map = getattr(game_map, 'population', None) or {}
    w = max(1, getattr(game_map, 'width', 1))
    h = max(1, getattr(game_map, 'height', 1))
    grid = [[0] * _GRID_ROWS for _ in range(_GRID_COLS)]
    for (x, y), pop in pop_map.items():
        if pop <= 0:
            continue
        cx = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * x / w)))
        cy = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * y / h)))
        grid[cx][cy] += pop
    return grid


def _road_priority_by_population_grid(road, game_map, bounds, population_6x6: list) -> float:
    """Return priority for road (higher = process first). Uses the maximum population among 6x6 cells
    the route passes through, so roads through the densest area are tried first (map-agnostic)."""
    if not population_6x6 or not game_map:
        return 1.0
    route = road.get('route', [])
    if len(route) < 2:
        return 0
    w = max(1, game_map.width)
    h = max(1, game_map.height)
    # Sample points along the route (start, end, and up to 3 in between) so we catch the highest-density cell the road touches
    n = len(route)
    indices = [0, n - 1]
    if n > 2:
        step = max(1, (n - 1) // 3)
        for i in range(step, n - 1, step):
            indices.append(i)
    max_pop = 0
    for i in indices:
        lat, lon = route[i][0], route[i][1]
        x, y = _latlon_to_grid(lat, lon, game_map, bounds)
        cx = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * x / w)))
        cy = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * y / h)))
        max_pop = max(max_pop, population_6x6[cx][cy])
    return float(max_pop)


def get_geo_feature_name_for_line_naming(
    game_map, x: float, y: float, current_year: int, bounds=None, max_distance_m: float = 150.0,
) -> str:
    """Return a nearby geographic label (e.g. street name) for AI line naming, or '' if none within max_distance_m.
    Grid distance is treated as kilometers (1 grid unit ≈ 1 km) for the distance cap."""
    max_d_grid = max(1e-6, float(max_distance_m) / 1000.0)
    return get_nearest_road_name_for_point(
        game_map, float(x), float(y), current_year, bounds=bounds,
        tolerance_grid=max_d_grid, max_segments=120, max_distance_grid=max_d_grid,
    ) or ""


def get_road_extension_points_from_end(game_map, base_pt: tuple, other_end_pt: tuple, current_year: int,
                                       extend_from_end: bool, max_points: int = 6, bounds=None,
                                       prefer_bridges: bool = False, exclude_road_names=None, _chain_depth: int = 0,
                                       max_neighbor_street_gap_grid: float = None) -> list:
    """Return grid points along a street polyline to extend a line from base_pt.
    base_pt: line end we're extending from (grid x,y).
    other_end_pt: the line's other end (to pick extension direction away from line).
    extend_from_end: True = extending from route end, False = from route start.
    prefer_bridges: when True, prefer road segments that are bridges (for crossing water).
    exclude_road_names: optional set/frozenset of OSM road names to skip (e.g. when chaining onto another street).
    _chain_depth: internal; one follow-up hop onto a different street when budget remains.
    Returns list of (x,y) starting with base_pt, then points along road; empty if no road found."""
    _build_road_spatial_index(game_map)
    excluded = frozenset(exclude_road_names or ())
    bx, by = float(base_pt[0]), float(base_pt[1])
    ox, oy = float(other_end_pt[0]), float(other_end_pt[1])
    lat, lon = _grid_to_latlon(bx, by, game_map, bounds)
    tolerance_grid = 15  # grid units - line end can be near road (first segment / same-street snap)
    # Tighter cap when hopping onto a different street (chain / excluded roads).
    if _chain_depth >= 1 and max_neighbor_street_gap_grid is not None:
        effective_segment_tol = float(max_neighbor_street_gap_grid)
    else:
        effective_segment_tol = tolerance_grid
    cell = (int(lat / _CELL_SIZE), int(lon / _CELL_SIZE))
    cells_to_check = [cell]
    for dlat, dlon in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
        cells_to_check.append((cell[0] + dlat, cell[1] + dlon))

    bridge_candidates = []  # (dist, is_bridge, ext_pts)
    non_bridge_candidates = []  # (d, n_pts, path_len, ext_pts, road_name)
    segments_checked = 0
    grid_cache = {}  # road_name -> grid_pts (avoid converting same road 80x per call)

    def _finalize_non_bridge(chosen_pts: list, chosen_road: str) -> list:
        if (
            _chain_depth == 0
            and not prefer_bridges
            and chosen_pts
            and len(chosen_pts) >= 2
            and chosen_road
        ):
            remaining = max_points - (len(chosen_pts) - 1)
            if remaining > 0:
                excl = set(excluded)
                excl.add(chosen_road)
                cont = get_road_extension_points_from_end(
                    game_map,
                    chosen_pts[-1],
                    chosen_pts[-2],
                    current_year,
                    True,
                    remaining,
                    bounds,
                    False,
                    frozenset(excl),
                    _chain_depth=1,
                    max_neighbor_street_gap_grid=max_neighbor_street_gap_grid,
                )
                if len(cont) >= 2:
                    chosen_pts = list(chosen_pts) + list(cont[1:])
        return chosen_pts

    for c in cells_to_check:
        for road_name, road, _ in _ROAD_SPATIAL_INDEX.get(c, []):
            if road_name in excluded:
                continue
            if segments_checked >= _MAX_EXTEND_ROAD_CHECKS:
                if prefer_bridges and bridge_candidates:
                    bridge_candidates.sort(key=lambda x: (not x[1], x[0]))
                    return bridge_candidates[0][2]
                if not prefer_bridges and non_bridge_candidates:
                    non_bridge_candidates.sort(key=lambda x: (-x[1], -x[2], x[0]))
                    t = non_bridge_candidates[0]
                    return _finalize_non_bridge(t[3], t[4])
                return []
            year_built = road.get('year', 1800)
            removed_year = road.get('removed_year')
            if year_built > current_year or (removed_year is not None and removed_year <= current_year):
                continue
            route = road.get('route', [])
            if len(route) < 2:
                continue
            is_bridge = 'bridge' in road_name.lower() or 'tunnel' in road_name.lower()
            if road_name not in grid_cache:
                grid_cache[road_name] = []
                for r in route:
                    x, y = _latlon_to_grid(r[0], r[1], game_map, bounds)
                    grid_cache[road_name].append((float(x), float(y)))
            grid_pts = grid_cache[road_name]
            for i in range(len(grid_pts) - 1):
                if segments_checked >= _MAX_EXTEND_ROAD_CHECKS:
                    break
                segments_checked += 1
                ax, ay = grid_pts[i][0], grid_pts[i][1]
                cx, cy = grid_pts[i + 1][0], grid_pts[i + 1][1]
                d = _point_to_segment_distance(bx, by, ax, ay, cx, cy)
                if d > effective_segment_tol:
                    continue
                next_to_other = math.hypot(ox - cx, oy - cy)
                prev_to_other = math.hypot(ox - ax, oy - ay)
                use_forward = next_to_other > prev_to_other
                start_idx = i + 1 if use_forward else i - 1
                step = 1 if use_forward else -1
                ext_pts = [(bx, by)]
                for j in range(start_idx, len(grid_pts) if use_forward else -1, step):
                    if len(ext_pts) >= max_points + 1:
                        break
                    ext_pts.append(grid_pts[j])
                if len(ext_pts) >= 2:
                    trimmed = ext_pts[: max_points + 1]
                    if prefer_bridges:
                        bridge_candidates.append((d, is_bridge, trimmed))
                    else:
                        path_len = sum(
                            math.hypot(trimmed[k + 1][0] - trimmed[k][0], trimmed[k + 1][1] - trimmed[k][1])
                            for k in range(len(trimmed) - 1)
                        )
                        non_bridge_candidates.append((d, len(trimmed), path_len, trimmed, road_name))

    if prefer_bridges and bridge_candidates:
        bridge_candidates.sort(key=lambda x: (not x[1], x[0]))
        return bridge_candidates[0][2]
    if not prefer_bridges and non_bridge_candidates:
        non_bridge_candidates.sort(key=lambda x: (-x[1], -x[2], x[0]))
        t = non_bridge_candidates[0]
        return _finalize_non_bridge(t[3], t[4])
    return []


def _grid_polylines_for_named_street(
    game_map,
    street_name: str,
    current_year: int,
    bounds=None,
) -> list:
    """Grid polylines for all OSM ways with the given name that exist at current_year."""
    target = (street_name or "").strip().casefold()
    if not target:
        return []
    _build_road_spatial_index(game_map)
    polylines = []
    seen_roads = set()
    for cell_roads in _ROAD_SPATIAL_INDEX.values():
        for road_name, road, _ in cell_roads:
            if (road_name or "").strip().casefold() != target:
                continue
            road_id = id(road)
            if road_id in seen_roads:
                continue
            seen_roads.add(road_id)
            year_built = road.get("year", 1800)
            removed_year = road.get("removed_year")
            if year_built > current_year or (removed_year is not None and removed_year <= current_year):
                continue
            route = road.get("route", [])
            if len(route) < 2:
                continue
            pts = []
            for r in route:
                gx, gy = _latlon_to_grid(r[0], r[1], game_map, bounds)
                pts.append((float(gx), float(gy)))
            if len(pts) >= 2:
                polylines.append(pts)
    return polylines


def _min_distance_to_polylines(
    px: float, py: float, polylines: list, max_distance: float = None,
) -> float:
    """Minimum distance from (px, py) to any polyline segment.

    When ``max_distance`` is given, returns as soon as a segment within it is found and
    segments whose bounding box is farther than ``max_distance`` are skipped (the result is
    then only meaningful for ``<= max_distance`` comparisons; it is ``inf`` if none is close).
    """
    best = float("inf")
    md = max_distance
    for poly in polylines or []:
        prev = None
        for pt in poly:
            if prev is None:
                prev = pt
                continue
            ax, ay = prev[0], prev[1]
            bx, by = pt[0], pt[1]
            prev = pt
            if md is not None:
                if ((px < ax - md and px < bx - md) or (px > ax + md and px > bx + md)
                        or (py < ay - md and py < by - md) or (py > ay + md and py > by + md)):
                    continue
            d = _point_to_segment_distance(px, py, ax, ay, bx, by)
            if d < best:
                best = d
                if md is not None and best <= md:
                    return best
    return best


from game_presets import AI_RAIL_OSM_POLYLINE_MAX_DISTANCE_GRID, AI_RAIL_OSM_POLYLINE_MIN_SEGMENT_COVERAGE, BUS_STREET_MAX_DISTANCE_GRID


def _spatial_cells_near_grid_point(
    px: float, py: float, game_map, bounds=None, expand: int = 1,
) -> set:
    """Spatial-index cells near a grid point."""
    bounds = _get_bounds(game_map, bounds)
    lat, lon = _grid_to_latlon(px, py, game_map, bounds)
    base = (int(lat / _CELL_SIZE), int(lon / _CELL_SIZE))
    cells = set()
    for dlat in range(-expand, expand + 1):
        for dlon in range(-expand, expand + 1):
            cells.add((base[0] + dlat, base[1] + dlon))
    return cells


def _spatial_cells_near_grid_segment(
    ax: float, ay: float, bx: float, by: float, game_map, bounds=None, expand: int = 1,
) -> set:
    """Spatial-index cells overlapping a grid segment (with neighbor expansion)."""
    bounds = _get_bounds(game_map, bounds)
    cells = _spatial_cells_near_grid_point(ax, ay, game_map, bounds, expand=expand)
    cells |= _spatial_cells_near_grid_point(bx, by, game_map, bounds, expand=expand)
    mid_x = ax + 0.5 * (bx - ax)
    mid_y = ay + 0.5 * (by - ay)
    cells |= _spatial_cells_near_grid_point(mid_x, mid_y, game_map, bounds, expand=expand)
    return cells


def _road_grid_polyline(road, game_map, bounds) -> list:
    """Grid polyline for one OSM road, cached per bounds signature."""
    global _ROAD_GRID_POLYLINE_BY_ID, _ROAD_GRID_POLYLINE_BOUNDS_SIG
    sig = _spatial_index_bounds_signature(game_map) if game_map is not None else None
    if sig != _ROAD_GRID_POLYLINE_BOUNDS_SIG:
        _ROAD_GRID_POLYLINE_BY_ID = {}
        _ROAD_GRID_POLYLINE_BOUNDS_SIG = sig
    rid = id(road)
    cached = _ROAD_GRID_POLYLINE_BY_ID.get(rid)
    if cached is not None:
        return cached
    route = road.get("route", []) or []
    b = _get_bounds(game_map, bounds)
    d_lon = b['max_lon'] - b['min_lon']
    d_lat = b['max_lat'] - b['min_lat']
    if d_lon == 0 or d_lat == 0:
        pts = [(float(max(1, game_map.width) / 2), float(max(1, game_map.height) / 2)) for _ in route]
    else:
        sx = max(1, game_map.width) / d_lon
        sy = max(1, game_map.height) / d_lat
        min_lon, min_lat = b['min_lon'], b['min_lat']
        pts = [((r[1] - min_lon) * sx, (r[0] - min_lat) * sy) for r in route]
    _ROAD_GRID_POLYLINE_BY_ID[rid] = pts
    return pts


def _collect_road_grid_polylines_for_cells(
    game_map, cells: set, current_year: int, bounds=None, *, include_bridges: bool = True,
) -> list:
    """Road (and optional bridge) centerline polylines in grid units for spatial cells."""
    if not cells:
        return []
    _build_road_spatial_index(game_map)
    bounds = _get_bounds(game_map, bounds)
    sig = _spatial_index_bounds_signature(game_map) if game_map is not None else None
    cache_key = (sig, int(current_year), bool(include_bridges), frozenset(cells))
    cached = _POLYLINES_FOR_CELLS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    polylines = []
    seen = set()

    def _add_road(road):
        rid = id(road)
        if rid in seen:
            return
        year_built = road.get("year", 1800)
        removed_year = road.get("removed_year")
        if year_built > current_year or (removed_year is not None and removed_year <= current_year):
            return
        seen.add(rid)
        pts = _road_grid_polyline(road, game_map, bounds)
        if len(pts) >= 2:
            polylines.append(pts)

    for cell in cells:
        for _road_name, road, _ in _ROAD_SPATIAL_INDEX.get(cell, []):
            _add_road(road)
        if include_bridges:
            for _road_name, road, _ in _BRIDGE_SPATIAL_INDEX.get(cell, []):
                _add_road(road)
    _POLYLINES_FOR_CELLS_CACHE[cache_key] = polylines
    if len(_POLYLINES_FOR_CELLS_CACHE) > 256:
        _POLYLINES_FOR_CELLS_CACHE.clear()
    return polylines


def land_segment_fraction_on_street_network(
    game_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
    bounds=None,
    *,
    max_distance_grid: float = BUS_STREET_MAX_DISTANCE_GRID,
) -> float:
    """Fraction of segment length within max_distance_grid of a road/bridge centerline."""
    ax, ay, bx, by = float(x1), float(y1), float(x2), float(y2)
    seg_len = math.hypot(bx - ax, by - ay)
    if seg_len <= 1e-9:
        return 1.0
    bounds = _get_bounds(game_map, bounds)
    cells = _spatial_cells_near_grid_segment(ax, ay, bx, by, game_map, bounds)
    polylines = _collect_road_grid_polylines_for_cells(game_map, cells, int(current_year), bounds)
    sample_step = max(0.05, float(max_distance_grid) * 0.5)
    n_samples = min(12, max(4, int(math.ceil(seg_len / sample_step))))
    on_len = 0.0
    chunk = seg_len / n_samples
    max_d = float(max_distance_grid)
    for j in range(n_samples):
        t = (j + 0.5) / n_samples
        px = ax + t * (bx - ax)
        py = ay + t * (by - ay)
        if polylines and _min_distance_to_polylines(px, py, polylines, max_distance=max_d) <= max_d:
            on_len += chunk
        elif is_point_on_bridge_at_year(px, py, int(current_year), game_map, bounds):
            on_len += chunk
    return on_len / seg_len


def route_land_segment_on_osm_polyline(
    game_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
    bounds=None,
    *,
    max_distance_grid: float = None,
    min_coverage: float = None,
) -> bool:
    """True when a land segment lies on OSM road geometry (not a chord between polylines)."""
    from game_presets import AI_MIN_STREET_QUALIFYING_SEGMENT_GRID

    ax, ay, bx, by = float(x1), float(y1), float(x2), float(y2)
    seg_len = math.hypot(bx - ax, by - ay)
    max_d = float(
        max_distance_grid
        if max_distance_grid is not None
        else AI_RAIL_OSM_POLYLINE_MAX_DISTANCE_GRID
    )
    min_cov = float(
        min_coverage
        if min_coverage is not None
        else AI_RAIL_OSM_POLYLINE_MIN_SEGMENT_COVERAGE
    )
    if seg_len + 1e-12 >= float(AI_MIN_STREET_QUALIFYING_SEGMENT_GRID):
        if route_segment_is_osm_polyline_edge(
            game_map, ax, ay, bx, by, int(current_year), bounds,
            max_distance_grid=max_d,
        ):
            return True
        # Street-router paths are simplified; accept when the segment hugs the network.
        street_snap = max(max_d, 1.0)
        frac = land_segment_fraction_on_street_network(
            game_map, ax, ay, bx, by, current_year, bounds,
            max_distance_grid=street_snap,
        )
        return frac + 1e-9 >= min_cov
    frac = land_segment_fraction_on_street_network(
        game_map, ax, ay, bx, by, current_year, bounds,
        max_distance_grid=max_d,
    )
    return frac + 1e-9 >= min_cov


def point_near_street_network(
    game_map,
    x: float,
    y: float,
    current_year: int,
    bounds=None,
    *,
    max_distance_grid: float = None,
) -> bool:
    """True when a grid point lies within max_distance_grid of a road/bridge centerline."""
    px, py = float(x), float(y)
    if is_point_on_bridge_at_year(px, py, int(current_year), game_map, bounds):
        return True
    max_d = float(max_distance_grid if max_distance_grid is not None else BUS_STREET_MAX_DISTANCE_GRID)
    bounds = _get_bounds(game_map, bounds)
    lat, lon = _grid_to_latlon(px, py, game_map, bounds)
    base = (int(lat / _CELL_SIZE), int(lon / _CELL_SIZE))
    cells = {(base[0] + dlat, base[1] + dlon) for dlat in (-1, 0, 1) for dlon in (-1, 0, 1)}
    polylines = _collect_road_grid_polylines_for_cells(game_map, cells, int(current_year), bounds)
    return bool(polylines) and _min_distance_to_polylines(px, py, polylines, max_distance=max_d) <= max_d


def route_fraction_on_named_street(
    game_map,
    route_points: list,
    street_name: str,
    current_year: int,
    bounds=None,
    max_distance_m: float = 40.0,
) -> float:
    """Fraction of route length within max_distance_m of the named street's OSM polyline(s)."""
    if not route_points or len(route_points) < 2 or not (street_name or "").strip():
        return 0.0
    polylines = _grid_polylines_for_named_street(game_map, street_name, current_year, bounds=bounds)
    if not polylines:
        return 0.0
    max_d = max(1e-9, float(max_distance_m) / 1000.0)
    sample_step = max_d * 0.5
    total_len = 0.0
    on_street_len = 0.0
    for i in range(len(route_points) - 1):
        ax, ay = float(route_points[i][0]), float(route_points[i][1])
        bx, by = float(route_points[i + 1][0]), float(route_points[i + 1][1])
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
            if _min_distance_to_polylines(px, py, polylines, max_distance=max_d) <= max_d:
                on_street_len += chunk
    if total_len <= 0:
        return 0.0
    return on_street_len / total_len


def get_streets_route_runs_along(
    game_map,
    route_points: list,
    current_year: int,
    bounds=None,
    max_distance_m: float = 40.0,
    min_route_fraction: float = 0.5,
) -> list:
    """Street names the route actually runs along (within max_distance_m), longest coverage first."""
    if not route_points or len(route_points) < 2:
        return []
    max_d_grid = max(1e-9, float(max_distance_m) / 1000.0)
    sample_step = max(0.01, max_d_grid * 0.5)
    candidates = set()
    for i in range(len(route_points) - 1):
        ax, ay = float(route_points[i][0]), float(route_points[i][1])
        bx, by = float(route_points[i + 1][0]), float(route_points[i + 1][1])
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len <= 0:
            continue
        n_samples = max(1, int(math.ceil(seg_len / sample_step)))
        for j in range(n_samples):
            t = (j + 0.5) / n_samples
            px = ax + t * (bx - ax)
            py = ay + t * (by - ay)
            name = get_nearest_road_name_for_point(
                game_map, px, py, current_year, bounds=bounds,
                tolerance_grid=max_d_grid * 2.0, max_segments=80,
                max_distance_grid=max_d_grid,
            )
            if name:
                candidates.add(str(name).strip())
    total_len = sum(
        math.hypot(
            float(route_points[i + 1][0]) - float(route_points[i][0]),
            float(route_points[i + 1][1]) - float(route_points[i][1]),
        )
        for i in range(len(route_points) - 1)
    )
    if total_len <= 0:
        return []
    qualified = []
    for street in candidates:
        frac = route_fraction_on_named_street(
            game_map, route_points, street, current_year, bounds=bounds,
            max_distance_m=max_distance_m,
        )
        if frac >= float(min_route_fraction):
            qualified.append((street, frac * total_len))
    qualified.sort(key=lambda item: (-item[1], item[0].casefold()))
    return [s for s, _ in qualified]


def format_osm_road_name_for_display(road_key: str) -> str:
    """Readable street label from an OSM road key (underscores, borough suffixes, ordinals)."""
    s = (road_key or "").replace("_", " ").strip()
    if not s:
        return ""
    for suf in (
        " Manhattan", " Brooklyn", " Queens", " Bronx", " Staten Island",
        " manhattan", " brooklyn", " queens", " bronx", " staten island",
    ):
        if s.endswith(suf) and len(s) > len(suf) + 3:
            s = s[: -len(suf)].strip()
    parts = s.split()
    out = []
    for w in parts:
        wl = w.lower()
        if wl in ("st", "st."):
            out.append("Street")
        elif wl in ("ave", "av", "av."):
            out.append("Avenue")
        elif wl in ("rd", "rd."):
            out.append("Road")
        elif wl in ("blvd", "blvd."):
            out.append("Boulevard")
        elif wl in ("dr", "dr."):
            out.append("Drive")
        elif wl in ("ln", "ln."):
            out.append("Lane")
        elif wl in ("pl", "pl."):
            out.append("Place")
        elif wl in ("ct", "ct."):
            out.append("Court")
        elif wl in ("pkwy", "pkwy."):
            out.append("Parkway")
        elif any(ch.isdigit() for ch in w):
            out.append(w if w[:1].isupper() else w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower() if w else w)
    return " ".join(out)


def get_nearest_road_name_for_point(game_map, x: float, y: float, current_year: int, bounds=None,
                                   tolerance_grid: float = 10.0, max_segments: int = 80,
                                   max_distance_grid: float = None) -> str:
    """Return a nearby road name for the given grid point (x,y), or '' if none found.
    Uses the NYC road spatial index and picks the road segment with minimum point-to-segment distance."""
    _build_road_spatial_index(game_map)
    bx, by = float(x), float(y)
    lat, lon = _grid_to_latlon(bx, by, game_map, bounds)
    cell = (int(lat / _CELL_SIZE), int(lon / _CELL_SIZE))
    cells_to_check = [cell]
    for dlat, dlon in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
        cells_to_check.append((cell[0] + dlat, cell[1] + dlon))

    best_name = ""
    best_dist = float("inf")
    segments_checked = 0
    grid_cache = {}  # road_name -> grid_pts

    for c in cells_to_check:
        for road_name, road, _ in _ROAD_SPATIAL_INDEX.get(c, []):
            if segments_checked >= max_segments:
                return best_name
            year_built = road.get("year", 1800)
            removed_year = road.get("removed_year")
            if year_built > current_year or (removed_year is not None and removed_year <= current_year):
                continue
            route = road.get("route", [])
            if len(route) < 2:
                continue
            name_lower = (road_name or "").lower()
            # Avoid ultra-generic/low-signal paths for naming
            if any(x in name_lower for x in ("footpath", "bikepath", "pathway", "walkpath", "sidewalk", "pedestrian", "trail")):
                continue
            if name_lower.endswith("_path") or " path" in name_lower.replace("_", " "):
                continue
            if road_name not in grid_cache:
                pts = []
                for r in route:
                    gx, gy = _latlon_to_grid(r[0], r[1], game_map, bounds)
                    pts.append((float(gx), float(gy)))
                grid_cache[road_name] = pts
            grid_pts = grid_cache[road_name]
            for i in range(len(grid_pts) - 1):
                if segments_checked >= max_segments:
                    break
                segments_checked += 1
                ax, ay = grid_pts[i][0], grid_pts[i][1]
                cx, cy = grid_pts[i + 1][0], grid_pts[i + 1][1]
                d = _point_to_segment_distance(bx, by, ax, ay, cx, cy)
                if d > tolerance_grid:
                    continue
                if d < best_dist:
                    best_dist = d
                    best_name = road_name or ""

    if best_dist == float("inf"):
        return ""
    if max_distance_grid is not None and best_dist > float(max_distance_grid):
        return ""
    return best_name


# Overlap tolerance: road points within this distance (grid units) of existing lines are "redundant"
# 6 units catches parallel streets (e.g. adjacent blocks) - avoids building duplicate corridors
_OVERLAP_TOLERANCE = 6.0
# Skip roads where more than this fraction of points are near existing lines (redundant)
_OVERLAP_SKIP_THRESHOLD = 0.25

# When multiple AIs build in the same year, we should be more permissive about what counts
# as "redundant overlap" so later AIs don't deadlock on sparse maps (e.g. only one short
# Brooklyn corridor available).
_OVERLAP_TOLERANCE_MULTI_AI = 0.8
_OVERLAP_SKIP_THRESHOLD_MULTI_AI = 0.7


def _pt(p):
    """Normalize point to (x, y) float tuple."""
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return (float(p[0]), float(p[1]))
    return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))


def _get_existing_line_segments(game_map, build_queue_routes=None) -> list:
    """Get all line segments from existing lines and optionally build queue."""
    segments = []
    lines = getattr(game_map, 'get_lines_by_name', None)
    if callable(lines):
        lines = lines() or {}
    elif not lines:
        lines = getattr(game_map, 'lines', {}) or {}
    for _name, line in (lines or {}).items():
        route = line.get_route() if hasattr(line, 'get_route') else []
        for i in range(len(route) - 1):
            a, b = _pt(route[i]), _pt(route[i + 1])
            segments.append((a, b))
    for route in (build_queue_routes or []):
        if not route or len(route) < 2:
            continue
        for i in range(len(route) - 1):
            a, b = _pt(route[i]), _pt(route[i + 1])
            segments.append((a, b))
    return segments


def _build_segment_spatial_index(segments: list, tolerance: float = _OVERLAP_TOLERANCE) -> dict:
    """Build spatial index for segments: cell (cx, cy) -> list of segments. Reused across many _road_overlap_ratio calls."""
    cell_size = max(tolerance, 1.0)
    seg_grid = {}
    for (ax, ay), (bx, by) in segments:
        x_min = min(ax, bx) - tolerance
        x_max = max(ax, bx) + tolerance
        y_min = min(ay, by) - tolerance
        y_max = max(ay, by) + tolerance
        cx_min = int(x_min / cell_size)
        cx_max = int(x_max / cell_size)
        cy_min = int(y_min / cell_size)
        cy_max = int(y_max / cell_size)
        seg = ((ax, ay), (bx, by))
        for cx in range(cx_min, cx_max + 1):
            for cy in range(cy_min, cy_max + 1):
                key = (cx, cy)
                if key not in seg_grid:
                    seg_grid[key] = []
                seg_grid[key].append(seg)
    return seg_grid


def _road_overlap_ratio(points: list, segments: list, tolerance: float = _OVERLAP_TOLERANCE,
                        seg_grid: dict = None) -> float:
    """Fraction of road points within tolerance of any existing line segment.
    Uses spatial index when segments is large to avoid O(points*segments) slowdown.
    seg_grid: optional pre-built index from _build_segment_spatial_index (avoids rebuilding per call)."""
    if not points or not segments:
        return 0.0
    # For few segments, brute-force is fast
    if len(segments) < 50:
        covered = 0
        for px, py in points:
            for (ax, ay), (bx, by) in segments:
                d = _point_to_segment_distance(px, py, ax, ay, bx, by)
                if d <= tolerance:
                    covered += 1
                    break
        return covered / len(points)
    # Use pre-built or build spatial index
    if seg_grid is None:
        seg_grid = _build_segment_spatial_index(segments, tolerance)
    cell_size = max(tolerance, 1.0)
    covered = 0
    for px, py in points:
        cx, cy = int(px / cell_size), int(py / cell_size)
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for (ax, ay), (bx, by) in seg_grid.get((cx + dx, cy + dy), []):
                    d = _point_to_segment_distance(px, py, ax, ay, bx, by)
                    if d <= tolerance:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            covered += 1
    return covered / len(points)


def _population_at_or_near(px: float, py: float, pop_map: dict, radius: int = 7) -> int:
    """Get population at grid point (px, py) or nearest cell within radius.
    NYC uses sparse population (NYC_INCLUDED_POINTS); road endpoints often land between cells.
    Radius 7 accommodates Manhattan's dense street grid misaligned with coarse population cells.
    Waterfront strips are still rejected by _road_has_population_along_route(min_frac=0.4)."""
    if not pop_map:
        return 0
    ix, iy = int(round(px)), int(round(py))
    pop = pop_map.get((ix, iy), 0)
    if pop > 0:
        return pop
    for r in range(1, radius + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    k = (ix + dx, iy + dy)
                    p = pop_map.get(k, 0)
                    if p > 0:
                        return p
    return 0


def _population_at_endpoint_strict(px: float, py: float, pop_map: dict, radius: int = None) -> int:
    """Population at or near endpoint. Uses strict radius (2) so we only count population
    actually at the road (avoids e.g. Governors Island qualifying via Manhattan across water)."""
    r = radius if radius is not None else _AI_POPULATION_RADIUS_STRICT
    return _population_at_or_near(px, py, pop_map, radius=r)


def _road_has_population_along_route(points: list, game_map, min_frac: float = 0.1, radius: int = None) -> bool:
    """Return True if at least min_frac of points along route have population > 0 within radius.
    When radius is None, uses Manhattan-aware lookup (_ai_pop_at_road_point) so OSM streets in
    lower/midtown Manhattan still qualify; explicit radius keeps legacy/test behavior."""
    pop_map = getattr(game_map, 'population', None) or {}
    if not points or not pop_map:
        return True
    bounds = _get_bounds(game_map, None)

    def _pop_at(px, py):
        if radius is not None:
            return _population_at_or_near(px, py, pop_map, radius=radius)
        return _ai_pop_at_road_point(px, py, game_map, bounds, pop_map)

    with_pop = sum(1 for (px, py) in points if _pop_at(px, py) > 0)
    return with_pop >= max(1, int(len(points) * min_frac))


def _road_profit_score(points: list, game_map, total_length: float, radius: int = None) -> float:
    """Estimate profitability: pop1 * pop2 / length. Higher = more profitable.
    Uses Manhattan-aware population when radius is None (aligned with AI road pick)."""
    if not points or total_length < 0.1:
        return 0.0
    pop_map = getattr(game_map, 'population', None) or {}
    p1, p2 = points[0], points[-1]
    if radius is not None:
        pop1 = _population_at_or_near(p1[0], p1[1], pop_map, radius=radius)
        pop2 = _population_at_or_near(p2[0], p2[1], pop_map, radius=radius)
    else:
        b = _get_bounds(game_map, None)
        pop1 = _ai_pop_at_road_point(p1[0], p1[1], game_map, b, pop_map)
        pop2 = _ai_pop_at_road_point(p2[0], p2[1], game_map, b, pop_map)
    if pop1 == 0 and pop2 == 0:
        return 0.0
    return (max(pop1, 10) * max(pop2, 10)) / max(0.1, total_length)


def _trim_polyline_prefix_to_max_length(points: list, max_length_grid: float) -> list:
    """Return the longest prefix of *points* whose polyline length is at most *max_length_grid*."""
    if not points or len(points) < 2 or max_length_grid is None:
        return list(points) if points else []
    work = [points[0]]
    walked = 0.0
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        seg = math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        if seg <= 0:
            continue
        if walked + seg <= max_length_grid:
            work.append(b)
            walked += seg
            continue
        remain = max(0.0, max_length_grid - walked)
        if remain > 0:
            t = min(1.0, remain / seg)
            work.append((
                float(a[0]) + t * (float(b[0]) - float(a[0])),
                float(a[1]) + t * (float(b[1]) - float(a[1])),
            ))
        break
    return work if len(work) >= 2 else []


def _split_polyline_by_max_edge(points: list, max_edge: float = 1.0) -> list:
    """Split a sampled road polyline where OSM geometry jumps between disjoint segments."""
    if not points or len(points) < 2:
        return [list(points)] if points else []
    chains = []
    cur = [(float(points[0][0]), float(points[0][1]))]
    for p in points[1:]:
        x, y = float(p[0]), float(p[1])
        prev = cur[-1]
        if math.hypot(x - prev[0], y - prev[1]) > max_edge:
            if len(cur) >= 2:
                chains.append(cur)
            cur = [(x, y)]
        else:
            cur.append((x, y))
    if len(cur) >= 2:
        chains.append(cur)
    return chains


def _polyline_land_segments_on_street(game_map, points: list, current_year: int) -> bool:
    """True when each land segment follows streets or crosses water (rail/AI opener check)."""
    if not points or len(points) < 2:
        return False
    from operations import _route_land_segment_follows_street, _route_segment_is_water_crossing

    cy = int(current_year)
    for i in range(len(points) - 1):
        x1, y1 = float(points[i][0]), float(points[i][1])
        x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
        if _route_segment_is_water_crossing(game_map, x1, y1, x2, y2, skip_osm=True):
            continue
        if not _route_land_segment_follows_street(game_map, x1, y1, x2, y2, cy, skip_osm=True):
            return False
    return True


def _best_ai_buildable_subchain(
    points: list, game_map, current_year: int, min_length: float = 1.2,
) -> list:
    """Longest buildable polyline for AI selection (full interrupted routes or best subchain)."""
    if not points or len(points) < 2:
        return []
    total = sum(
        math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        for i in range(len(points) - 1)
    )
    if total >= min_length and _polyline_land_segments_on_street(game_map, points, current_year):
        return list(points)
    best = []
    best_len = 0.0
    for chain in _split_polyline_by_max_edge(points):
        total = sum(
            math.hypot(chain[i + 1][0] - chain[i][0], chain[i + 1][1] - chain[i][1])
            for i in range(len(chain) - 1)
        )
        if total < min_length:
            continue
        if not _polyline_land_segments_on_street(game_map, chain, current_year):
            continue
        if total > best_len:
            best = chain
            best_len = total
    return best


def _chain_connected_roads_at_ends(points: list, game_map, current_year: int, max_points: int,
                                   max_length_grid: float, segments: list, bounds=None,
                                   seg_grid: dict = None,
                                   overlap_tol: float = _OVERLAP_TOLERANCE,
                                   overlap_skip_threshold: float = _OVERLAP_SKIP_THRESHOLD,
                                   self_overlap_tol: float = None) -> list:
    """Extend base road at both ends with connected roads. AI can combine multiple roads into longer lines."""
    if not points or len(points) < 2:
        return points

    def _total_length(pts):
        return sum(
            math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1])
            for i in range(len(pts)-1)
        )

    _sol_tol = self_overlap_tol if self_overlap_tol is not None else _ai_self_overlap_tolerance(game_map)
    result = list(points)
    total_len = _total_length(result)
    max_extend_points = min(4, max(1, max_points - len(result)))

    # Extend from end (find connected road going away from start)
    if len(result) < max_points and (max_length_grid is None or total_len < max_length_grid):
        ext_end = get_road_extension_points_from_end(
            game_map, result[-1], result[0], current_year, extend_from_end=True,
            max_points=max_extend_points, bounds=bounds
        )
        if ext_end and len(ext_end) >= 2:
            for i in range(1, len(ext_end)):
                if len(result) >= max_points:
                    break
                result.append(ext_end[i])
            total_len = _total_length(result)
            if polyline_has_self_overlap(result, tolerance=_sol_tol):
                result = list(points)  # Revert
            elif segments and _road_overlap_ratio(
                result, segments, tolerance=overlap_tol, seg_grid=seg_grid
            ) > overlap_skip_threshold:
                result = list(points)
            elif not _road_has_population_along_route(result, game_map, min_frac=0.4):
                result = list(points)  # Revert: extension goes through empty area

    # Extend from start (recalc total_len in case we reverted end extension)
    total_len = _total_length(result)
    if len(result) < max_points and (max_length_grid is None or total_len < max_length_grid):
        ext_start = get_road_extension_points_from_end(
            game_map, result[0], result[-1], current_year, extend_from_end=False,
            max_points=max_extend_points, bounds=bounds
        )
        if ext_start and len(ext_start) >= 2:
            prepend = []
            for i in range(1, len(ext_start)):
                if len(result) + len(prepend) >= max_points:
                    break
                prepend.append(ext_start[i])
            candidate = list(reversed(prepend)) + result
            if not polyline_has_self_overlap(candidate, tolerance=_sol_tol):
                if not segments or _road_overlap_ratio(
                    candidate, segments, tolerance=overlap_tol, seg_grid=seg_grid
                ) <= overlap_skip_threshold:
                    if _road_has_population_along_route(candidate, game_map, min_frac=0.35):
                        result = candidate
    return result


def get_road_segment_points_for_ai(game_map, current_year: int, max_points: int = 12, bounds=None,
                                   existing_line_routes=None, min_length_grid=None, max_length_grid=None,
                                   min_endpoint_population=None, rng=None) -> list:
    """Return grid points along a street polyline for AI line building.
    Picks the most profitable road that exists at current_year and is not redundant.
    Skips roads that overlap or run parallel to existing lines.
    min_length_grid: if set, skip roads shorter than this (e.g. 0.5 for 500m when 1 unit≈1km).
    max_length_grid: if set, skip roads longer than this (e.g. 3.0 for first line to avoid overly long routes).
    min_endpoint_population: if set, skip roads where either endpoint has population below this (prioritizes high-pop areas)."""
    _build_road_spatial_index(game_map)
    w = max(1, game_map.width)
    h = max(1, game_map.height)
    segments = _get_existing_line_segments(game_map, existing_line_routes)
    seg_grid = _build_segment_spatial_index(segments) if len(segments) >= 50 else None
    # When other AIs have already built this turn, we relax overlap so this AI can get a route too
    # (Operations still rejects if segments under construction overlap; AI retries with different route on that error)
    multi_ai_turn = existing_line_routes is not None and len(existing_line_routes) > 0
    # Looser overlap when other AIs already have geometry: strict multi-AI constants often yield zero candidates.
    if multi_ai_turn:
        overlap_tol = _OVERLAP_TOLERANCE
        overlap_thresh = 0.88
    else:
        overlap_tol = _OVERLAP_TOLERANCE
        overlap_thresh = _OVERLAP_SKIP_THRESHOLD
    self_overlap_tol = _ai_self_overlap_tolerance(game_map)
    if segments and multi_ai_turn and len(segments) >= 50:
        # Ensure the spatial index matches the overlap tolerance we use below.
        seg_grid = _build_segment_spatial_index(segments, tolerance=overlap_tol)
    # When multiple AIs build same turn, avoid picking a road in the same 6x6 cell as an existing route (spread builds)
    used_4x4_cells = set()
    if multi_ai_turn and existing_line_routes:
        for route in existing_line_routes:
            if len(route) < 2:
                continue
            pts = [(float(p[0]), float(p[1])) for p in route]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            cell_x = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * cx / w)))
            cell_y = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * cy / h)))
            used_4x4_cells.add((cell_x, cell_y))

    # Prioritize roads in high-population 6x6 sections so AI builds there first.
    # Use cached per-road features to avoid repeating lat/lon->grid conversion and population scans.
    population_4x4 = _build_population_4x4_grid(game_map)
    from map_preset_loader import preset_has_population_init
    preset_name = getattr(game_map, "map_preset_name", None)
    has_preset_population = preset_has_population_init(preset_name)
    # OSM maps with precomputed population: deterministic first line + densest-cell filter.
    deterministic_first_line = (
        current_year == 1830
        and min_endpoint_population is not None
        and min_endpoint_population >= 1000
        and not existing_line_routes
        and has_preset_population
    )
    road_feats = _get_ai_road_features(game_map, bounds=bounds, max_points=max_points)

    # When min_endpoint_population is 0 (turn 1 fallback) or multi_ai_turn, consider more roads so every AI can qualify
    is_fallback_pop = (
        min_endpoint_population is not None and min_endpoint_population < 1000
    )
    # With cached per-road features, iterating more roads is cheap (the expensive work is cached).
    # We can therefore scan more candidates on fallback/multi-AI turns to avoid the "no candidates"
    # deadlock where the first N high-priority roads fail secondary filters (self-overlap, empty-cell, etc.).
    if is_fallback_pop:
        max_roads = min(len(road_feats), _MAX_ROADS_FOR_AI_BUILD * 3)
    elif multi_ai_turn:
        max_roads = min(_MAX_ROADS_FOR_AI_BUILD * 3, 180)
    else:
        max_roads = _MAX_ROADS_FOR_AI_BUILD
    road_feats = road_feats[:max_roads]
    # Do not shuffle: keep population-based order so AI tries high-pop areas first
    # and builds where ridership is highest instead of picking randomly among qualified roads.

    # Collect more candidates when multi-AI so later AIs have non-overlapping options to choose from
    min_candidates = 12 if multi_ai_turn else _MIN_CANDIDATES_BEFORE_EARLY_EXIT
    if is_fallback_pop and not multi_ai_turn:
        min_candidates = 1
    if max_points is not None and int(max_points) <= 36:
        if not multi_ai_turn:
            max_roads = min(max_roads, 96)
            min_candidates = min(min_candidates, 3)
        else:
            max_roads = min(max_roads, 120)
            min_candidates = min(min_candidates, 6)
        road_feats = road_feats[:max_roads]
    scan_limit = max_roads
    if deterministic_first_line:
        scan_limit = min(max_roads, _MAX_DETERMINISTIC_FIRST_LINE_ROAD_SCAN)
        min_candidates = min(max(min_candidates, 16), scan_limit)
    candidates = []

    relax_self_overlap = (
        current_year == 1830
        and min_endpoint_population is not None
        and has_preset_population
        and (min_endpoint_population >= 1000 or is_fallback_pop)
    )

    for f in road_feats[:scan_limit]:
        if len(candidates) >= min_candidates:
            break
        road_name = f.get("name")
        year_built = f.get("year", 1800)
        removed_year = f.get("removed_year")
        if year_built > current_year:
            continue
        if removed_year is not None and removed_year <= current_year:
            continue
        # Year-1 fallback openers: prefer ordinary streets; bridge/tunnel OSM routes often
        # sample disjoint deck segments that fail street validation when chained.
        if is_fallback_pop and not multi_ai_turn and f.get("is_bridge"):
            continue
        points = list(f.get("points_full") or [])
        if len(points) < 2:
            continue
        # Cheap checks first: bounds, length before expensive overlap/self_overlap.
        # When min_endpoint_population is 0 (turn 1 fallback), allow points near the map edge and clamp so at least one route qualifies.
        is_fallback = is_fallback_pop
        if is_fallback:
            margin = 5
            in_bounds = sum(1 for (px, py) in points if -margin <= px <= w + margin and -margin <= py <= h + margin) >= 2
            if not in_bounds:
                continue
            points = [(max(0, min(w, px)), max(0, min(h, py))) for (px, py) in points]
        else:
            in_bounds = sum(1 for (px, py) in points if 0 <= px <= w and 0 <= py <= h) >= 2
            if not in_bounds:
                continue
        # NYC: never pick extremely short routes (parks, pathways); enforce at least 1.2 grid (~1.2km)
        effective_min = max((min_length_grid or 0), 1.2)
        buildable = _best_ai_buildable_subchain(points, game_map, current_year, effective_min)
        if len(buildable) < 2:
            continue
        points = buildable
        pop_bounds = _get_bounds(game_map, None)
        pop_map = getattr(game_map, "population", None) or {}
        pop1 = int(_ai_pop_at_road_point(points[0][0], points[0][1], game_map, pop_bounds, pop_map))
        pop2 = int(_ai_pop_at_road_point(points[-1][0], points[-1][1], game_map, pop_bounds, pop_map))
        pop_sample_pts = points
        if len(pop_sample_pts) > 8:
            step_p = max(1, len(pop_sample_pts) // 8)
            pop_sample_pts = pop_sample_pts[::step_p][:8]
        with_pop = sum(
            1 for (px, py) in pop_sample_pts
            if _ai_pop_at_road_point(px, py, game_map, pop_bounds, pop_map) > 0
        )
        points_n = len(points)
        max_pop_cell = int(f.get("max_pop_cell", 0))
        for (px, py) in pop_sample_pts + [points[0], points[-1]]:
            cx = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * px / w)))
            cy = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * py / h)))
            max_pop_cell = max(max_pop_cell, population_4x4[cx][cy])
        cx_avg = sum(pp[0] for pp in points) / len(points)
        cy_avg = sum(pp[1] for pp in points) / len(points)
        cell_x = min(_GRID_COLS - 1, max(0, int(_GRID_COLS * cx_avg / w)))
        cell_y = min(_GRID_ROWS - 1, max(0, int(_GRID_ROWS * cy_avg / h)))
        total_length = sum(
            math.hypot(points[i+1][0]-points[i][0], points[i+1][1]-points[i][1])
            for i in range(len(points)-1)
        )
        if total_length < effective_min:
            continue
        if max_length_grid is not None and total_length > max_length_grid:
            trimmed = _trim_polyline_prefix_to_max_length(points, max_length_grid)
            if len(trimmed) < 2:
                continue
            points = trimmed
            total_length = sum(
                math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
                for i in range(len(points) - 1)
            )
            if total_length < effective_min:
                continue
        # Skip roads whose centroid lies in an empty 6x6 cell (map-agnostic: islands, water, etc.)
        if _road_centroid_in_empty_cell(points, game_map, population_4x4):
            continue
        # Skip overlap for fallback (min_endpoint_population=0) and for multi-AI same-turn sampling:
        # merged in-queue geometry can make overlap ratios look like full duplication (no candidates).
        # Operations.create_line_complete still rejects exact duplicate AI geometry.
        skip_overlap = is_fallback or multi_ai_turn
        if (
            not skip_overlap
            and segments
            and _road_overlap_ratio(points, segments, tolerance=overlap_tol, seg_grid=seg_grid)
            > overlap_thresh
        ):
            continue
        if polyline_has_self_overlap(points, tolerance=self_overlap_tol) and not relax_self_overlap:
            continue
        score = _road_profit_score(points, game_map, total_length)
        # When min_endpoint_population is 0 (last-resort fallback) or multi_ai_turn, allow any score so every AI can build
        min_score = 0 if (is_fallback_pop or multi_ai_turn) else 5
        if is_fallback_pop and not multi_ai_turn:
            min_score = 1  # Never pick zero-score roads even in fallback (avoids bad spots like Governors Island)
        if score < min_score:
            continue
        # Require minimum population at both endpoints (prioritize high-population areas over low-pop).
        # Use strict radius=2 so waterfront/empty strips don't qualify via distant urban population.
        if min_endpoint_population is not None:
            if pop1 < min_endpoint_population or pop2 < min_endpoint_population:
                continue
        # Absolute guard: even fallback routes must have meaningful endpoint population.
        if pop1 < _AI_MIN_ENDPOINT_POP_FLOOR or pop2 < _AI_MIN_ENDPOINT_POP_FLOOR:
            continue
        # Require population along route - skip roads through empty/low-pop areas.
        # Strict 50% only when targeting very high endpoint population (dense first-line picks).
        # Year-1 openers pass min_endpoint_population in {1, 50, ...}; those must stay relaxed.
        use_relaxed_route_pop = is_fallback or multi_ai_turn or is_fallback_pop
        if not use_relaxed_route_pop:
            if not _road_has_population_along_route(points, game_map, min_frac=0.5):
                continue
        else:
            # Use stricter route-pop floor so fallback routes still avoid low-pop dead zones.
            min_frac_route = 0.4 if is_fallback_pop and not multi_ai_turn else 0.35
            min_frac_route = max(min_frac_route, _AI_MIN_ROUTE_POP_FRACTION_FLOOR)
            need = max(1, int(points_n * min_frac_route))
            if with_pop < need:
                continue
        # Moderate noise (up to 35%) so different games vary but still pick good roads
        import random
        rand = (rng.uniform(-0.35, 0.35) if rng else random.uniform(-0.35, 0.35)) * max(1, abs(score))
        endpoint_pop_sum = int(pop1) + int(pop2)
        in_used_cell = (cell_x, cell_y) in used_4x4_cells
        candidates.append((in_used_cell, max_pop_cell, endpoint_pop_sum, score + rand, points[:max_points]))

    if not candidates:
        return []
    # When tests / high-threshold first lines require touching the absolute densest 6x6 cell, prefer only
    # corridors whose sampled route actually reaches that population peak. If OSM filters rule out every
    # such road, keep the unfiltered list (same as before).
    if deterministic_first_line:
        try:
            gmax = max(
                population_4x4[i][j]
                for i in range(_GRID_COLS)
                for j in range(_GRID_ROWS)
            )
            filtered = [c for c in candidates if int(c[1]) >= int(gmax)]
            if filtered:
                candidates = filtered
        except Exception:
            pass
    global_max_pop = max(int(c[1]) for c in candidates)
    dense_threshold = max(1, int(0.82 * global_max_pop))

    def _candidate_rank(x):
        in_used, max_pop, ep_sum, score, _pts = x
        spread_penalty = 1 if (in_used and max_pop < dense_threshold) else 0
        return (max_pop, -spread_penalty, ep_sum, score)

    # Prefer highest-density corridors; only avoid a used 6x6 cell when it is not in the top tier.
    candidates.sort(key=_candidate_rank, reverse=True)
    top_n = 1 if deterministic_first_line else (3 if multi_ai_turn else min(5, len(candidates)))
    if top_n <= 1:
        base_points = candidates[0][4]
    else:
        idx = rng.randint(0, top_n - 1) if rng else 0
        base_points = candidates[idx][4]
    # Chain connected roads at both ends to form longer lines (AI can combine multiple roads).
    # For NYC + high endpoint threshold on turn 1, skip chaining: extensions often leave the densest
    # 6x6 cell even though the base corridor was chosen for max_pop_cell (breaks unit test + skews ridership).
    # Year-1 fallback openers also skip chaining so disjoint bridge decks are not merged into one polyline.
    if deterministic_first_line or is_fallback_pop:
        return list(base_points)
    return _chain_connected_roads_at_ends(
        base_points,
        game_map,
        current_year,
        max_points,
        max_length_grid,
        segments,
        bounds,
        seg_grid=seg_grid,
        overlap_tol=overlap_tol,
        overlap_skip_threshold=overlap_thresh,
    )


def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from point (px, py) to line segment (ax,ay)-(bx,by). Uses lat/lon as x/y."""
    d, _t = _point_to_segment_distance_and_param(px, py, ax, ay, bx, by)
    return d


def _point_to_segment_distance_and_param(
    px: float, py: float, ax: float, ay: float, bx: float, by: float,
) -> tuple[float, float]:
    """Return (distance, t) where t in [0,1] is the projection parameter on the segment."""
    dx = bx - ax
    dy = by - ay
    dist_sq = dx * dx + dy * dy
    if dist_sq < 1e-20:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5, 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / dist_sq))
    nx = ax + t * dx
    ny = ay + t * dy
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5, t


def _closest_location_on_polyline(
    px: float, py: float, poly: list,
) -> tuple[float, float, int, float]:
    """Return (distance, cumulative_arclength, segment_index, t) for the closest point on poly."""
    best = (float("inf"), 0.0, 0, 0.0)
    cumulative = 0.0
    for si in range(len(poly) - 1):
        ax, ay = float(poly[si][0]), float(poly[si][1])
        bx, by = float(poly[si + 1][0]), float(poly[si + 1][1])
        seg_len = math.hypot(bx - ax, by - ay)
        d, t = _point_to_segment_distance_and_param(px, py, ax, ay, bx, by)
        if d < best[0]:
            best = (d, cumulative + t * seg_len, si, t)
        cumulative += seg_len
    return best


def route_segment_is_osm_polyline_edge(
    game_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
    bounds=None,
    *,
    max_distance_grid: float = None,
    min_path_ratio: float = 0.85,
) -> bool:
    """True when a land segment follows an OSM road polyline rather than cutting across blocks.

    Long chords that run near a parallel street do not qualify; the segment length must match
    the polyline path length between its endpoints on the same centerline.
    """
    from game_presets import AI_RAIL_OSM_POLYLINE_MAX_DISTANCE_GRID

    ax, ay, bx, by = float(x1), float(y1), float(x2), float(y2)
    seg_len = math.hypot(bx - ax, by - ay)
    if seg_len <= 1e-9:
        return True
    max_d = float(
        max_distance_grid
        if max_distance_grid is not None
        else AI_RAIL_OSM_POLYLINE_MAX_DISTANCE_GRID
    )
    bounds = _get_bounds(game_map, bounds)
    cells = _spatial_cells_near_grid_segment(ax, ay, bx, by, game_map, bounds, expand=2)
    polylines = _collect_road_grid_polylines_for_cells(game_map, cells, int(current_year), bounds)
    if not polylines:
        return False

    best_ratio = 0.0
    for poly in polylines:
        if len(poly) < 2:
            continue
        dist_a, arc_a, _si_a, _t_a = _closest_location_on_polyline(ax, ay, poly)
        dist_b, arc_b, _si_b, _t_b = _closest_location_on_polyline(bx, by, poly)
        if dist_a > max_d or dist_b > max_d:
            continue
        path_len = abs(arc_b - arc_a)
        if path_len <= 1e-9:
            continue
        ratio = seg_len / path_len
        if ratio > best_ratio:
            best_ratio = ratio
    return best_ratio + 1e-9 >= float(min_path_ratio)


_POINT_ON_ROAD_CACHE: dict = {}


def is_point_on_road_at_year(x: float, y: float, current_year: int, game_map, bounds=None) -> bool:
    """Check if grid point (x, y) is on a road that exists at current_year in NYC.
    Uses cached grid polylines near the point (avoids false negatives from dense segment caps)."""
    sig = _spatial_index_bounds_signature(game_map) if game_map is not None else None
    cache_key = (round(float(x), 2), round(float(y), 2), int(current_year), sig)
    cached = _POINT_ON_ROAD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    _build_road_spatial_index(game_map)
    px, py = float(x), float(y)
    bounds = _get_bounds(game_map, bounds)
    cy = int(current_year)
    if is_point_on_bridge_at_year(px, py, cy, game_map, bounds):
        found = True
    else:
        on_road_max_d = max(0.004, float(BUS_STREET_MAX_DISTANCE_GRID) * 0.5)
        cells = _spatial_cells_near_grid_point(px, py, game_map, bounds, expand=1)
        polylines = _collect_road_grid_polylines_for_cells(game_map, cells, cy, bounds)
        found = bool(polylines) and _min_distance_to_polylines(
            px, py, polylines, max_distance=on_road_max_d,
        ) <= on_road_max_d
    _POINT_ON_ROAD_CACHE[cache_key] = found
    if len(_POINT_ON_ROAD_CACHE) > 8192:
        _POINT_ON_ROAD_CACHE.clear()
    return found


def get_bridge_polyline_between_shores(
    game_map,
    shore_a: tuple,
    shore_b: tuple,
    current_year: int,
    bounds=None,
    max_points: int = 12,
    shore_tolerance_grid: float = 0.35,
) -> list:
    """Return grid points along a historic bridge/tunnel between two shore points, or []."""
    _build_road_spatial_index(game_map)
    ax, ay = float(shore_a[0]), float(shore_a[1])
    bx, by = float(shore_b[0]), float(shore_b[1])
    lat_a, lon_a = _grid_to_latlon(ax, ay, game_map, bounds)
    lat_b, lon_b = _grid_to_latlon(bx, by, game_map, bounds)
    cells = set()
    for lat, lon in ((lat_a, lon_a), (lat_b, lon_b)):
        cell = (int(lat / _CELL_SIZE), int(lon / _CELL_SIZE))
        cells.add(cell)
        for dlat, dlon in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            cells.add((cell[0] + dlat, cell[1] + dlon))

    seen_roads = set()
    best_pts = None
    best_score = float("inf")
    grid_cache = {}

    def _grid_pts_for_road(road_name, road):
        if road_name in grid_cache:
            return grid_cache[road_name]
        pts = []
        for r in road.get("route", []) or []:
            gx, gy = _latlon_to_grid(r[0], r[1], game_map, bounds)
            pts.append((float(gx), float(gy)))
        grid_cache[road_name] = pts
        return pts

    for cell in cells:
        for road_name, road, _seg_idx in _BRIDGE_SPATIAL_INDEX.get(cell, []):
            if road_name in seen_roads:
                continue
            seen_roads.add(road_name)
            year_built = road.get("year", 1800)
            removed_year = road.get("removed_year")
            if year_built > current_year:
                continue
            if removed_year is not None and removed_year <= current_year:
                continue
            gpts = _grid_pts_for_road(road_name, road)
            if len(gpts) < 2:
                continue
            ia = min(range(len(gpts)), key=lambda i: math.hypot(gpts[i][0] - ax, gpts[i][1] - ay))
            ib = min(range(len(gpts)), key=lambda i: math.hypot(gpts[i][0] - bx, gpts[i][1] - by))
            da = math.hypot(gpts[ia][0] - ax, gpts[ia][1] - ay)
            db = math.hypot(gpts[ib][0] - bx, gpts[ib][1] - by)
            if da > shore_tolerance_grid or db > shore_tolerance_grid:
                continue
            if ia == ib:
                continue
            i0, i1 = (ia, ib) if ia < ib else (ib, ia)
            sub = list(gpts[i0 : i1 + 1])
            if len(sub) < 2:
                continue
            if len(sub) > max_points:
                step = max(1, (len(sub) - 1) // (max_points - 1))
                trimmed = [sub[0]]
                for j in range(step, len(sub) - 1, step):
                    trimmed.append(sub[j])
                trimmed.append(sub[-1])
                sub = trimmed[:max_points]
            plen = sum(
                math.hypot(sub[k + 1][0] - sub[k][0], sub[k + 1][1] - sub[k][1])
                for k in range(len(sub) - 1)
            )
            score = da + db + plen * 0.05
            if score < best_score:
                best_score = score
                best_pts = sub
    return list(best_pts) if best_pts else []


def is_point_on_bridge_at_year(x: float, y: float, current_year: int, game_map, bounds=None) -> bool:
    """Check if grid point (x, y) is on a bridge that exists at current_year in NYC.
    Uses spatial index for fast lookup."""
    _build_road_spatial_index(game_map)
    lat, lon = _grid_to_latlon(x, y, game_map, bounds)
    tolerance = 0.00015
    cell = (int(lat / _CELL_SIZE), int(lon / _CELL_SIZE))
    candidates = _BRIDGE_SPATIAL_INDEX.get(cell, [])
    for road_name, road, seg_idx in candidates:
        year_built = road.get('year', 1800)
        removed_year = road.get('removed_year')
        if year_built > current_year:
            continue
        if removed_year is not None and removed_year <= current_year:
            continue
        route = road.get('route', [])
        if seg_idx >= len(route) - 1:
            continue
        a_lat, a_lon = route[seg_idx][0], route[seg_idx][1]
        b_lat, b_lon = route[seg_idx + 1][0], route[seg_idx + 1][1]
        d = _point_to_segment_distance(lon, lat, a_lon, a_lat, b_lon, b_lat)
        if d <= tolerance:
            return True
    return False
