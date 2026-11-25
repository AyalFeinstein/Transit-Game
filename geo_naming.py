"""Geography-based names for lines and stations, the same rules on every map.

Stations are named for the cross-streets they sit on ("Main St & Mill Rd") or, when only
one street is nearby, for the neighbourhood or town.  Lines are named for the area(s) they
serve ("Northside–Riverside Line").

The rules live here; the geography comes from the map's preset (via map_preset_loader):

* ``get_place_names()``: ``(name, lat, lon)`` or ``(name, lat, lon, max_km)`` centres of
  neighbourhoods and towns.  A point belongs to the nearest centre within its ``max_km``
  (default ``NEIGHBORHOOD_MAX_KM``).
* ``get_area_boxes()``: ``(name, min_lat, max_lat, min_lon, max_lon)`` broad areas used
  when no place is near.
* ``get_road_utils()``: the map's street data, for cross-street names.

A map that supplies none of these simply gets no geographic names.
"""
from __future__ import annotations

import math

NEIGHBORHOOD_MAX_KM = 3.0
CROSS_STREET_MAX_M = 45.0

_ABBREVIATIONS = (
    (" Street", " St"), (" Avenue", " Av"), (" Boulevard", " Blvd"), (" Road", " Rd"),
    (" Parkway", " Pkwy"), (" Place", " Pl"), (" Drive", " Dr"), (" Lane", " Ln"),
    (" Turnpike", " Tpke"), (" Highway", " Hwy"), (" Court", " Ct"),
)
_SKIP_ROAD_WORDS = (
    "footpath", "bikepath", "pathway", "walkpath", "sidewalk", "pedestrian", "trail",
    "ramp", "exit", "service", "driveway", "alley", "path",
)
_CROSS_CACHE: dict = {}


def _road_utils(game_map):
    from map_preset_loader import get_preset_road_utils
    return get_preset_road_utils(game_map)


def _grid_to_latlon(game_map, x: float, y: float) -> tuple[float, float]:
    ru = _road_utils(game_map)
    if ru is not None:
        return ru._grid_to_latlon(float(x), float(y), game_map, None)
    from map_preset_loader import convert_preset_grid_to_latlon, get_map_bounds
    preset_name = getattr(game_map, 'map_preset_name', None)
    return convert_preset_grid_to_latlon(
        preset_name, float(x), float(y), get_map_bounds(preset_name), game_map.width, game_map.height,
    )


_PLACE_DATA: dict = {}  # preset name -> (places, boxes)


def _place_data(preset_name) -> tuple:
    data = _PLACE_DATA.get(preset_name)
    if data is None:
        from map_preset_loader import get_preset_area_boxes, get_preset_place_names
        places = tuple(
            (p[0], float(p[1]), float(p[2]), float(p[3]) if len(p) > 3 else NEIGHBORHOOD_MAX_KM)
            for p in get_preset_place_names(preset_name)
        )
        data = _PLACE_DATA[preset_name] = (places, get_preset_area_boxes(preset_name))
    return data


def neighborhood_at_latlon(lat: float, lon: float, preset_name: str) -> str:
    """Nearest place of the map within that place's range, else the broad area, else ''."""
    places, boxes = _place_data(preset_name)
    best = ""
    best_km = float('inf')
    cos_lat = math.cos(math.radians(lat))
    for name, nlat, nlon, max_km in places:
        dlat = (nlat - lat) * 110.57
        if abs(dlat) >= max_km:
            continue
        km = math.hypot(dlat, (nlon - lon) * 111.32 * cos_lat)
        if km < max_km and km < best_km:
            best_km = km
            best = name
    if best:
        return best
    for name, lat0, lat1, lon0, lon1 in boxes:
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            return name
    return ""


def areas_at(game_map, x: float, y: float) -> set:
    """Every broad area (from the preset's area boxes) containing the grid point."""
    try:
        lat, lon = _grid_to_latlon(game_map, x, y)
    except Exception:
        return set()
    boxes = _place_data(getattr(game_map, 'map_preset_name', None))[1]
    return {name for name, lat0, lat1, lon0, lon1 in boxes if lat0 <= lat <= lat1 and lon0 <= lon <= lon1}


def neighborhood_at(game_map, x: float, y: float) -> str:
    try:
        lat, lon = _grid_to_latlon(game_map, x, y)
    except Exception:
        return ""
    return neighborhood_at_latlon(lat, lon, getattr(game_map, 'map_preset_name', None))


def short_street_name(road_key: str, ru=None) -> str:
    """Display name for a road key, with the usual street-sign abbreviations."""
    if ru is not None:
        label = ru.format_osm_road_name_for_display(road_key)
    else:
        label = " ".join(w.capitalize() for w in (road_key or "").replace("_", " ").split())
    for long_form, short_form in _ABBREVIATIONS:
        if label.endswith(long_form):
            label = label[: -len(long_form)] + short_form
            break
    return label


_BUCKET_M = 60.0          # >= CROSS_STREET_MAX_M so a 3x3 bucket lookup covers the search radius
_CELL_BUCKETS: dict = {}  # (map signature, cell, year) -> {bucket: [(ax, ay, bx, by, label)]}


def _cell_street_buckets(ru, game_map, cell, year: int, mx: float, my: float, bounds) -> dict:
    """Named street segments of one spatial-index cell, bucketed by position (built once)."""
    from ai_street_router import MAX_STREET_SEGMENT_M

    key = (ru._spatial_index_bounds_signature(game_map), cell, int(year))
    buckets = _CELL_BUCKETS.get(key)
    if buckets is not None:
        return buckets
    buckets = {}
    labels: dict = {}
    polys: dict = {}
    max_seg2 = MAX_STREET_SEGMENT_M ** 2
    for road_name, road, i in (ru._ROAD_SPATIAL_INDEX or {}).get(cell, ()):
        if road.get('year', 1800) > year:
            continue
        removed = road.get('removed_year')
        if removed is not None and removed <= year:
            continue
        label = labels.get(road_name)
        if label is None:
            low = (road_name or "").lower().replace("_", " ")
            skip = not low or any(w in low.split() or low.endswith(w) for w in _SKIP_ROAD_WORDS)
            label = labels[road_name] = "" if skip else short_street_name(road_name, ru)
        if not label:
            continue
        poly = polys.get(id(road))
        if poly is None:
            poly = polys[id(road)] = ru._road_grid_polyline(road, game_map, bounds)
        if i + 1 >= len(poly):
            continue
        ax, ay = poly[i][0] * mx, poly[i][1] * my
        bx, by = poly[i + 1][0] * mx, poly[i + 1][1] * my
        den = (bx - ax) ** 2 + (by - ay) ** 2
        if den <= 0 or den > max_seg2:
            continue  # zero-length, or a data jump rather than a street
        seg = (ax, ay, bx, by, label)
        for cx in range(int(min(ax, bx) // _BUCKET_M), int(max(ax, bx) // _BUCKET_M) + 1):
            for cy in range(int(min(ay, by) // _BUCKET_M), int(max(ay, by) // _BUCKET_M) + 1):
                buckets.setdefault((cx, cy), []).append(seg)
    if len(_CELL_BUCKETS) > 64:
        _CELL_BUCKETS.clear()
    _CELL_BUCKETS[key] = buckets
    return buckets


def cross_streets_at(game_map, x: float, y: float, current_year: int, max_m: float = CROSS_STREET_MAX_M) -> list[str]:
    """Up to two distinct street names within max_m of the grid point, nearest first."""
    from ai_street_router import grid_metric_scale

    ru = _road_utils(game_map)
    if ru is None:
        return []
    key = (getattr(game_map, 'map_preset_name', None), round(float(x), 3), round(float(y), 3), int(current_year))
    if key in _CROSS_CACHE:
        return list(_CROSS_CACHE[key])
    ru._build_road_spatial_index(game_map)
    bounds = ru._get_bounds(game_map, None)
    mx, my = grid_metric_scale(game_map, bounds)
    px, py = float(x) * mx, float(y) * my
    lat, lon = ru._grid_to_latlon(float(x), float(y), game_map, bounds)
    cs = ru._CELL_SIZE
    c0 = (int(lat / cs), int(lon / cs))
    edge = 0.001  # degrees (~100 m): only look into a neighbouring cell when close to its edge
    # int() truncates toward zero, so the "low" edge of a cell depends on the sign.
    f_lat, f_lon = abs(lat / cs - c0[0]) * cs, abs(lon / cs - c0[1]) * cs
    s_lat, s_lon = (1 if lat >= 0 else -1), (1 if lon >= 0 else -1)
    dlats = [0] + ([-s_lat] if f_lat < edge else []) + ([s_lat] if cs - f_lat < edge else [])
    dlons = [0] + ([-s_lon] if f_lon < edge else []) + ([s_lon] if cs - f_lon < edge else [])
    best: dict[str, float] = {}
    bx0, by0 = int(px // _BUCKET_M), int(py // _BUCKET_M)
    for dlat in dlats:
        for dlon in dlons:
            buckets = _cell_street_buckets(ru, game_map, (c0[0] + dlat, c0[1] + dlon), int(current_year), mx, my, bounds)
            for cx in (bx0 - 1, bx0, bx0 + 1):
                for cy in (by0 - 1, by0, by0 + 1):
                    for ax, ay, bx, by, label in buckets.get((cx, cy), ()):
                        dx, dy = bx - ax, by - ay
                        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
                        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
                        if d <= max_m and d < best.get(label, float('inf')):
                            best[label] = d
    names = [n for n, _d in sorted(best.items(), key=lambda kv: kv[1])][:2]
    if len(_CROSS_CACHE) > 4096:
        _CROSS_CACHE.clear()
    _CROSS_CACHE[key] = list(names)
    return names


def _unique(name: str, existing: set, extras: list[str]) -> str:
    if name not in existing:
        return name
    for extra in extras:
        if extra and extra.lower() not in name.lower():
            cand = f"{name} ({extra})"
            if cand not in existing:
                return cand
    k = 2
    while f"{name} ({k})" in existing and k < 999:
        k += 1
    return f"{name} ({k})"


def station_name_for_point(game_map, x: float, y: float, current_year: int, existing_names: set = None) -> str:
    """Cross-street name when two streets meet here; else the neighbourhood (plus street)."""
    existing = existing_names or set()
    try:
        streets = cross_streets_at(game_map, x, y, current_year)
    except Exception:
        streets = []
    hood = neighborhood_at(game_map, x, y)
    if len(streets) >= 2:
        return _unique(f"{streets[0]} & {streets[1]}", existing, [hood])
    if hood and hood not in existing:
        return hood
    if hood and streets:
        return _unique(f"{hood} – {streets[0]}", existing, [])
    if streets:
        return _unique(streets[0], existing, [hood])
    if hood:
        return _unique(hood, existing, [])
    return ""


def line_area_name_candidates(game_map, route_points: list) -> list[str]:
    """Area-based line names for a route, best first (e.g. 'Northside–Riverside Line')."""
    if not route_points or len(route_points) < 2:
        return []
    a = neighborhood_at(game_map, route_points[0][0], route_points[0][1])
    b = neighborhood_at(game_map, route_points[-1][0], route_points[-1][1])
    mid_pt = route_points[len(route_points) // 2]
    mid = neighborhood_at(game_map, mid_pt[0], mid_pt[1])
    out = []
    if a and b and a != b:
        out += [f"{a}–{b} Line", f"{b}–{a} Line"]
        if mid and mid not in (a, b):
            out.append(f"{a}–{mid}–{b} Line")
        out += [f"{a}–{b} Local", f"{a}–{b} Crosstown"]
    for area in (a, mid, b):
        if area:
            for suffix in ("Line", "Local", "Crosstown", "Shuttle", "Loop"):
                cand = f"{area} {suffix}"
                if cand not in out:
                    out.append(cand)
    return out
