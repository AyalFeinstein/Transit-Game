#!/usr/bin/env python3
"""Generate NY Metro land cells and precomputed 1830 population for map_presets_ny_metro.py.

Land cells = OSM NYC map (NYC_INITIAL_POPULATION reprojected onto the metro grid)
             + every non-water grid cell inside OSM Long Island relation 3955977
             + Hudson County (957239), Bergen County (958930), Rockland County (1835363),
             + Westchester County (1838027), Union County (957236), Middlesex County (961153).
Cells inside Hudson River relation 2148192 (centerline buffer) are excluded, and any cell
whose center and all four corners lie inside OSM natural=water (Long Island Sound, harbor,
bays, ocean, and the wide Hudson at Haverstraw Bay / Tappan Zee) is excluded.

Grid cells use the same physical size (degrees) as the OSM NYC map.
All generation logic lives in this file; map_presets_ny_metro.py holds config + runtime API.

Run after bounds/water edits:
  poetry run python generate_ny_metro_population_points.py
  poetry run python generate_ny_metro_population_points.py --audit  # relation check only
  poetry run python generate_ny_metro_population_points.py --offline  # polygon fallbacks

Writes to map_presets_ny_metro.py:
  - NY_METRO_INITIAL_POPULATION {(x, y): population} — land cells only (0 omitted)
"""

from __future__ import annotations

import json
import os
import re
import sys

from map_presets_ny_metro import (
    _grid_to_latlon,
    get_ny_metro_bounds,
    is_metro_water_location,
    is_water_location,
)
from map_presets_nyc import (
    NYC_INITIAL_POPULATION,
    NYC_POPULATION,
)

LONG_ISLAND_RELATION_ID = 3955977
HUDSON_RIVER_RELATION_ID = 2148192
HUDSON_COUNTY_RELATION_ID = 957239
BERGEN_COUNTY_RELATION_ID = 958930
ROCKLAND_COUNTY_RELATION_ID = 1835363
WESTCHESTER_COUNTY_RELATION_ID = 1838027
UNION_COUNTY_RELATION_ID = 957236
MIDDLESEX_COUNTY_RELATION_ID = 961153
# Connecticut (whole state); grid north edge clips it to ~south of Hartford,
# covering Fairfield/New Haven and the shoreline up to Hartford.
CONNECTICUT_STATE_RELATION_ID = 165794

# (relation_id, label, population_kind) — population_kind: "li" | "county"
LAND_AREA_RELATIONS: list[tuple[int, str, str]] = [
    (LONG_ISLAND_RELATION_ID, "Long Island", "li"),
    (HUDSON_COUNTY_RELATION_ID, "Hudson County", "county"),
    (BERGEN_COUNTY_RELATION_ID, "Bergen County", "county"),
    (ROCKLAND_COUNTY_RELATION_ID, "Rockland County", "county"),
    (WESTCHESTER_COUNTY_RELATION_ID, "Westchester County", "county"),
    (UNION_COUNTY_RELATION_ID, "Union County", "county"),
    (MIDDLESEX_COUNTY_RELATION_ID, "Middlesex County", "county"),
    (CONNECTICUT_STATE_RELATION_ID, "Connecticut", "county"),
]

LONG_ISLAND_FALLBACK_POLYGON = [
    (-73.72, 40.55), (-73.45, 40.54), (-73.10, 40.56), (-72.55, 40.62),
    (-72.00, 40.68), (-71.86, 40.78), (-71.86, 40.92), (-72.10, 41.02),
    (-72.55, 41.08), (-73.05, 41.10), (-73.45, 41.05), (-73.62, 40.98),
    (-73.72, 40.88), (-73.72, 40.55),
]

HUDSON_COUNTY_FALLBACK = [
    (-74.12, 40.64), (-74.02, 40.64), (-74.02, 40.78), (-74.12, 40.78), (-74.12, 40.64),
]

BERGEN_COUNTY_FALLBACK = [
    (-74.20, 40.78), (-73.88, 40.78), (-73.88, 41.12), (-74.20, 41.12), (-74.20, 40.78),
]

ROCKLAND_COUNTY_FALLBACK = [
    (-74.25, 40.98), (-73.82, 40.98), (-73.82, 41.38), (-74.25, 41.38), (-74.25, 40.98),
]

WESTCHESTER_COUNTY_FALLBACK = [
    (-73.98, 40.88), (-73.48, 40.88), (-73.48, 41.38), (-73.98, 41.38), (-73.98, 40.88),
]

UNION_COUNTY_FALLBACK = [
    (-74.46, 40.59), (-74.14, 40.59), (-74.14, 40.74), (-74.46, 40.74), (-74.46, 40.59),
]

MIDDLESEX_COUNTY_FALLBACK = [
    (-74.63, 40.31), (-74.20, 40.31), (-74.20, 40.61), (-74.63, 40.61), (-74.63, 40.31),
]

CONNECTICUT_STATE_FALLBACK = [
    (-73.73, 41.00), (-72.10, 41.00), (-71.79, 41.35), (-71.79, 41.80),
    (-73.73, 41.80), (-73.73, 41.00),
]

HUDSON_RIVER_OFFLINE_FALLBACK = [
    (-74.10, 40.68), (-74.02, 40.68), (-74.02, 40.88), (-74.10, 40.88), (-74.10, 40.68),
    (-73.96, 40.95), (-73.84, 40.95), (-73.84, 41.15), (-73.96, 41.15), (-73.96, 40.95),
]

RELATION_OFFLINE_FALLBACKS = {
    LONG_ISLAND_RELATION_ID: [LONG_ISLAND_FALLBACK_POLYGON],
    HUDSON_COUNTY_RELATION_ID: [HUDSON_COUNTY_FALLBACK],
    BERGEN_COUNTY_RELATION_ID: [BERGEN_COUNTY_FALLBACK],
    ROCKLAND_COUNTY_RELATION_ID: [ROCKLAND_COUNTY_FALLBACK],
    WESTCHESTER_COUNTY_RELATION_ID: [WESTCHESTER_COUNTY_FALLBACK],
    UNION_COUNTY_RELATION_ID: [UNION_COUNTY_FALLBACK],
    MIDDLESEX_COUNTY_RELATION_ID: [MIDDLESEX_COUNTY_FALLBACK],
    CONNECTICUT_STATE_RELATION_ID: [CONNECTICUT_STATE_FALLBACK],
    HUDSON_RIVER_RELATION_ID: [HUDSON_RIVER_OFFLINE_FALLBACK],
}

OSM_RELATION_POLYGON_CACHE = os.path.join(
    os.path.dirname(__file__), "ny_metro_osm_relation_polygons.json",
)
HUDSON_RIVER_WAYS_CACHE_KEY = "2148192_river_ways"
HUDSON_RIVER_BUFFER_KM = 0.45

# Open-water exclusion: natural=water polygons (Hudson, Long Island Sound, harbor, bays, ocean).
# A land cell is dropped only when its center AND all four corners lie inside water, so
# coastal cells (partly on land) are kept while fully-submerged cells (wide Hudson at
# Haverstraw Bay/Tappan Zee, the Sound, open bays) are removed. Cached to avoid re-querying.
METRO_WATER_POLYGONS_CACHE_KEY = "metro_natural_water"
# Only large water bodies matter at ~1 km cell resolution; drop ponds/lakes smaller than this.
METRO_WATER_MIN_SPAN_DEG = 0.008
_metro_water_indexed_cache: list | None = None


def _polygon_bounds(polygon: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lons = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    return min(lons), max(lons), min(lats), max(lats)


def _indexed_polygons(polygons: list[list[tuple[float, float]]]) -> list[tuple[float, float, float, float, list]]:
    return [(*_polygon_bounds(poly), poly) for poly in polygons]


def convert_nyc_points_to_metro() -> set[tuple[int, int]]:
    """Metro cells for every NYC_INITIAL_POPULATION cell (1:1 grid offset, no lat/lon rounding)."""
    from map_presets_ny_metro import nyc_grid_to_metro_grid

    return {nyc_grid_to_metro_grid(x, y) for x, y in NYC_INITIAL_POPULATION}


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
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
        if (yi > y) != (yj > y):
            intersect_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < intersect_x:
                inside = not inside
        j = i
    return inside


def _points_near(a: tuple[float, float], b: tuple[float, float], eps: float = 1e-4) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def assemble_closed_rings(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join OSM outer way segments into closed rings for point-in-polygon tests."""
    unused = [list(way) for way in ways if len(way) >= 2]
    rings: list[list[tuple[float, float]]] = []
    while unused:
        ring = unused.pop(0)
        changed = True
        while changed:
            changed = False
            for idx, way in enumerate(unused):
                if _points_near(ring[-1], way[0]):
                    ring.extend(way[1:])
                    unused.pop(idx)
                    changed = True
                    break
                if _points_near(ring[-1], way[-1]):
                    ring.extend(reversed(way[:-1]))
                    unused.pop(idx)
                    changed = True
                    break
                if _points_near(ring[0], way[-1]):
                    ring = way[:-1] + ring
                    unused.pop(idx)
                    changed = True
                    break
                if _points_near(ring[0], way[0]):
                    ring = list(reversed(way[1:])) + ring
                    unused.pop(idx)
                    changed = True
                    break
        if len(ring) < 4:
            continue
        if not _points_near(ring[0], ring[-1]):
            ring.append(ring[0])
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def prepare_relation_polygons(raw_ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Assemble relation outer ways into closed rings; drop tiny noise rings."""
    rings = assemble_closed_rings(raw_ways)
    if not rings:
        return raw_ways
    return [ring for ring in rings if len(ring) >= 12]


def _load_osm_api_relation_payload(relation_id: int) -> dict:
    import urllib.request

    url = f"https://www.openstreetmap.org/api/0.6/relation/{relation_id}/full.json"
    with urllib.request.urlopen(url, timeout=180) as response:
        return json.load(response)


def fetch_osm_boundary_polygons_via_api(relation_id: int) -> list[list[tuple[float, float]]]:
    """Fetch admin-boundary outer rings via the OSM API (works when Overpass is slow)."""
    payload = _load_osm_api_relation_payload(relation_id)
    nodes = {
        element["id"]: (float(element["lon"]), float(element["lat"]))
        for element in payload.get("elements", [])
        if element.get("type") == "node" and "lat" in element and "lon" in element
    }
    rel = next(
        element for element in payload.get("elements", [])
        if element.get("type") == "relation" and element.get("id") == relation_id
    )
    ways_by_id = {
        element["id"]: element
        for element in payload.get("elements", [])
        if element.get("type") == "way"
    }
    polygons: list[list[tuple[float, float]]] = []
    for member in rel.get("members", []):
        if member.get("type") != "way" or member.get("role") != "outer":
            continue
        way = ways_by_id.get(member.get("ref"))
        if not way:
            continue
        ring = [nodes[node_id] for node_id in way.get("nodes", []) if node_id in nodes]
        if len(ring) >= 2:
            polygons.append(ring)
    assembled = prepare_relation_polygons(polygons)
    print(
        f"  OSM API relation {relation_id}: {len(assembled)} assembled ring(s), "
        f"{sum(len(p) for p in assembled)} vertices (from {len(polygons)} outer ways)"
    )
    _update_relation_polygon_cache(relation_id, polygons)
    return assembled


def fetch_osm_relation_polygons(relation_id: int, allow_offline: bool = False) -> list[list[tuple[float, float]]]:
    """Return closed outer rings for an OSM admin boundary or landmass relation."""
    if relation_id == HUDSON_RIVER_RELATION_ID:
        return []

    try:
        return fetch_osm_boundary_polygons_via_api(relation_id)
    except Exception as exc:
        print(f"  OSM API relation {relation_id} failed: {exc}")

    if allow_offline:
        fallback = RELATION_OFFLINE_FALLBACKS.get(relation_id)
        if fallback:
            print(f"  WARNING: using offline fallback polygon for relation {relation_id}")
            return [list(poly) for poly in fallback]
    print(f"ERROR: OSM relation {relation_id} required. Retry or pass --offline.")
    sys.exit(1)


def fetch_osm_river_way_polylines(relation_id: int, allow_offline: bool = False) -> list[list[tuple[float, float]]]:
    """Fetch centerline ways for a waterway relation (e.g. Hudson River 2148192)."""
    use_cache = "--refresh-osm" not in sys.argv
    if use_cache and os.path.exists(OSM_RELATION_POLYGON_CACHE):
        try:
            with open(OSM_RELATION_POLYGON_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            polylines = cached.get(HUDSON_RIVER_WAYS_CACHE_KEY)
            if polylines:
                print(f"  Cached Hudson River ways: {len(polylines)} segments")
                return [list(map(tuple, poly)) for poly in polylines]
        except Exception as exc:
            print(f"  River-way cache read failed: {exc}")

    try:
        payload = _load_osm_api_relation_payload(relation_id)
    except Exception as exc:
        print(f"  OSM API river relation {relation_id} failed: {exc}")
        if allow_offline:
            return []
        sys.exit(1)

    nodes = {
        element["id"]: (float(element["lon"]), float(element["lat"]))
        for element in payload.get("elements", [])
        if element.get("type") == "node" and "lat" in element and "lon" in element
    }
    ways_by_id = {
        element["id"]: element
        for element in payload.get("elements", [])
        if element.get("type") == "way"
    }
    rel = next(
        element for element in payload.get("elements", [])
        if element.get("type") == "relation" and element.get("id") == relation_id
    )
    polylines: list[list[tuple[float, float]]] = []
    for member in rel.get("members", []):
        if member.get("type") != "way":
            continue
        way = ways_by_id.get(member.get("ref"))
        if not way:
            continue
        line = [nodes[node_id] for node_id in way.get("nodes", []) if node_id in nodes]
        if len(line) >= 2:
            polylines.append(line)

    bounds = get_ny_metro_bounds()
    metro_min_lon, metro_max_lon = bounds["min_lon"], bounds["max_lon"]
    metro_min_lat, metro_max_lat = bounds["min_lat"], bounds["max_lat"]
    pad = 0.08
    clipped = []
    for line in polylines:
        in_bbox = [
            pt for pt in line
            if (metro_min_lon - pad <= pt[0] <= metro_max_lon + pad
                and metro_min_lat - pad <= pt[1] <= metro_max_lat + pad)
        ]
        if len(in_bbox) >= 2:
            clipped.append(in_bbox)
    polylines = clipped or polylines
    print(f"  OSM API river relation {relation_id}: {len(polylines)} way segment(s) in metro bounds")

    cache: dict = {}
    if os.path.exists(OSM_RELATION_POLYGON_CACHE):
        try:
            with open(OSM_RELATION_POLYGON_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[HUDSON_RIVER_WAYS_CACHE_KEY] = polylines
    try:
        with open(OSM_RELATION_POLYGON_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as exc:
        print(f"  WARNING: could not write river-way cache: {exc}")
    return polylines


def _point_to_segment_km(
    lat: float, lon: float,
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    import math

    cos_lat = math.cos(math.radians(lat))
    x, y = lon * 111.0 * cos_lat, lat * 111.0
    x1, y1 = lon1 * 111.0 * cos_lat, lat1 * 111.0
    x2, y2 = lon2 * 111.0 * cos_lat, lat2 * 111.0
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    px, py = x1 + t * dx, y1 + t * dy
    return math.hypot(x - px, y - py)


def min_distance_km_to_polylines(
    lat: float,
    lon: float,
    polylines: list[list[tuple[float, float]]],
) -> float:
    best = float("inf")
    for line in polylines:
        for i in range(len(line) - 1):
            lon1, lat1 = line[i]
            lon2, lat2 = line[i + 1]
            best = min(best, _point_to_segment_km(lat, lon, lat1, lon1, lat2, lon2))
    return best


def _update_relation_polygon_cache(relation_id: int, polygons: list[list[tuple[float, float]]]) -> None:
    cache: dict = {}
    if os.path.exists(OSM_RELATION_POLYGON_CACHE):
        try:
            with open(OSM_RELATION_POLYGON_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[str(relation_id)] = polygons
    try:
        with open(OSM_RELATION_POLYGON_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as exc:
        print(f"  WARNING: could not write relation cache: {exc}")


def simplify_polygon(polygon: list[tuple[float, float]], max_points: int = 400) -> list[tuple[float, float]]:
    if len(polygon) <= max_points:
        return polygon
    step = max(1, len(polygon) // max_points)
    simplified = polygon[::step]
    if simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return simplified


def simplify_polygons(
    polygons: list[list[tuple[float, float]]], max_points: int = 400,
) -> list[list[tuple[float, float]]]:
    return [simplify_polygon(poly, max_points=max_points) for poly in polygons]


def prepare_relation_for_scan(
    relation_id: int, allow_offline: bool = False,
) -> list[list[tuple[float, float]]]:
    """Load assembled OSM rings for point-in-polygon scans."""
    if relation_id == HUDSON_RIVER_RELATION_ID:
        return []
    assembled = fetch_osm_relation_polygons(relation_id, allow_offline)
    if relation_id == LONG_ISLAND_RELATION_ID:
        rings = sorted(assembled, key=len, reverse=True)
        result: list[list[tuple[float, float]]] = []
        for i, ring in enumerate(rings):
            if i == 0:
                result.append(simplify_polygon(ring, max_points=3500))
            elif len(ring) >= 8:
                result.append(simplify_polygon(ring, max_points=250))
        return result or assembled
    return simplify_polygons(assembled, max_points=1800)


def load_hudson_river_polylines(allow_offline: bool = False) -> list[list[tuple[float, float]]]:
    return fetch_osm_river_way_polylines(HUDSON_RIVER_RELATION_ID, allow_offline)


def _overpass_natural_water_elements() -> list[dict]:
    """Query Overpass for natural=water ways/relations across the metro bounding box."""
    import urllib.request
    import urllib.parse

    bounds = get_ny_metro_bounds()
    pad = 0.05
    s = bounds["min_lat"] - pad
    w = bounds["min_lon"] - pad
    n = bounds["max_lat"] + pad
    e = bounds["max_lon"] + pad
    query = (
        "[out:json][timeout:120];("
        f'way["natural"="water"]({s},{w},{n},{e});'
        f'relation["natural"="water"]({s},{w},{n},{e});'
        ");out geom;"
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    last_exc: Exception | None = None
    for url in endpoints:
        try:
            req = urllib.request.Request(
                url, data=data, headers={"User-Agent": "TransitGame/1.0"},
            )
            with urllib.request.urlopen(req, timeout=150) as resp:
                payload = json.load(resp)
            return payload.get("elements", [])
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"  Overpass natural=water via {url} failed: {exc}")
    if last_exc:
        raise last_exc
    return []


def _assemble_water_rings(elements: list[dict]) -> list[list[tuple[float, float]]]:
    """Build closed (lon, lat) rings for large water bodies from Overpass 'out geom' elements."""
    rings: list[list[tuple[float, float]]] = []

    def _ring_from_geom(geom: list[dict]) -> list[tuple[float, float]]:
        return [(float(p["lon"]), float(p["lat"])) for p in geom if "lon" in p and "lat" in p]

    for el in elements:
        if el.get("type") == "way" and "geometry" in el:
            ring = _ring_from_geom(el["geometry"])
            if len(ring) >= 4:
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                rings.append(ring)
        elif el.get("type") == "relation":
            segments = [
                _ring_from_geom(m["geometry"])
                for m in el.get("members", [])
                if m.get("role") in ("outer", "") and "geometry" in m
            ]
            rings.extend(assemble_closed_rings([s for s in segments if len(s) >= 2]))

    large: list[list[tuple[float, float]]] = []
    for ring in rings:
        if len(ring) < 4:
            continue
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        if (max(lons) - min(lons)) < METRO_WATER_MIN_SPAN_DEG and (
            max(lats) - min(lats)
        ) < METRO_WATER_MIN_SPAN_DEG:
            continue
        large.append(ring)
    return large


def fetch_metro_water_polygons(allow_offline: bool = False) -> list[list[tuple[float, float]]]:
    """Return large natural=water rings for open-water exclusion (cached)."""
    use_cache = "--refresh-osm" not in sys.argv
    if use_cache and os.path.exists(OSM_RELATION_POLYGON_CACHE):
        try:
            with open(OSM_RELATION_POLYGON_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            rings = cached.get(METRO_WATER_POLYGONS_CACHE_KEY)
            if rings:
                print(f"  Cached open-water polygons: {len(rings)}")
                return [list(map(tuple, ring)) for ring in rings]
        except Exception as exc:  # noqa: BLE001
            print(f"  Open-water cache read failed: {exc}")

    try:
        elements = _overpass_natural_water_elements()
    except Exception as exc:  # noqa: BLE001
        print(f"  Open-water fetch failed: {exc}")
        if allow_offline:
            return []
        raise

    rings = _assemble_water_rings(elements)
    print(f"  Open-water polygons assembled: {len(rings)} (from {len(elements)} elements)")

    cache: dict = {}
    if os.path.exists(OSM_RELATION_POLYGON_CACHE):
        try:
            with open(OSM_RELATION_POLYGON_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[METRO_WATER_POLYGONS_CACHE_KEY] = rings
    try:
        with open(OSM_RELATION_POLYGON_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: could not write open-water cache: {exc}")
    return rings


def _get_metro_water_indexed() -> list:
    """Lazily load and index the cached open-water polygons (empty list if unavailable)."""
    global _metro_water_indexed_cache
    if _metro_water_indexed_cache is None:
        try:
            rings = fetch_metro_water_polygons(allow_offline=True)
        except Exception:  # noqa: BLE001
            rings = []
        _metro_water_indexed_cache = _indexed_polygons(rings)
    return _metro_water_indexed_cache


def is_open_water_cell(lat: float, lon: float) -> bool:
    """True only when a cell's center and all four corners lie inside open water.

    This keeps coastal cells (partly on land) while removing cells fully inside the
    Hudson River (including the wide Haverstraw Bay / Tappan Zee), Long Island Sound,
    the harbor, bays and open ocean.
    """
    indexed = _get_metro_water_indexed()
    if not indexed:
        return False
    bounds = get_ny_metro_bounds()
    half_lat = (bounds["max_lat"] - bounds["min_lat"]) / bounds["height"] / 2.0
    half_lon = (bounds["max_lon"] - bounds["min_lon"]) / bounds["width"] / 2.0
    samples = (
        (lat, lon),
        (lat - half_lat, lon - half_lon),
        (lat - half_lat, lon + half_lon),
        (lat + half_lat, lon - half_lon),
        (lat + half_lat, lon + half_lon),
    )
    return all(cell_center_in_relation(s_lat, s_lon, indexed) for s_lat, s_lon in samples)


def cell_center_in_relation(
    lat: float,
    lon: float,
    indexed_polygons: list[tuple[float, float, float, float, list]],
) -> bool:
    """True when the cell center lies inside any relation outer ring."""
    for min_lon, max_lon, min_lat, max_lat, polygon in indexed_polygons:
        if lon < min_lon or lon > max_lon or lat < min_lat or lat > max_lat:
            continue
        if point_in_polygon((lon, lat), polygon):
            return True
    return False


def cell_intersects_relation(
    x: int,
    y: int,
    bounds: dict,
    width: int,
    height: int,
    indexed_polygons: list[tuple[float, float, float, float, list]],
    *,
    subgrid_size: int = 3,
) -> bool:
    """True when center or any corner/subgrid sample in a cell lies inside a relation."""
    lat, lon = _grid_to_latlon(x, y, bounds, width, height)
    if cell_center_in_relation(lat, lon, indexed_polygons):
        return True
    cell_lat = (bounds["max_lat"] - bounds["min_lat"]) / height
    cell_lon = (bounds["max_lon"] - bounds["min_lon"]) / width
    half_lat, half_lon = cell_lat / 2.0, cell_lon / 2.0
    for corner_lon, corner_lat in (
        (lon - half_lon, lat - half_lat),
        (lon + half_lon, lat - half_lat),
        (lon - half_lon, lat + half_lat),
        (lon + half_lon, lat + half_lat),
    ):
        if cell_center_in_relation(corner_lat, corner_lon, indexed_polygons):
            return True
    if subgrid_size <= 0:
        return False
    sub_lat_step = cell_lat / subgrid_size
    sub_lon_step = cell_lon / subgrid_size
    for i in range(subgrid_size):
        for j in range(subgrid_size):
            sample_lat = lat - half_lat + (i + 0.5) * sub_lat_step
            sample_lon = lon - half_lon + (j + 0.5) * sub_lon_step
            if cell_center_in_relation(sample_lat, sample_lon, indexed_polygons):
                return True
    return False


def is_hudson_river_cell(
    lat: float,
    lon: float,
    hudson_river_polylines: list | None,
) -> bool:
    """True when cell center is on Hudson River (OSM relation 2148192 centerline + buffer)."""
    if hudson_river_polylines:
        return min_distance_km_to_polylines(lat, lon, hudson_river_polylines) <= HUDSON_RIVER_BUFFER_KM
    return is_metro_water_location(lat, lon) and -74.12 <= lon <= -73.82 and 40.65 <= lat <= 41.18


def _exclude_extra_land_cell(
    lat: float,
    lon: float,
    kind: str,
    hudson_river_polylines: list | None,
) -> bool:
    """Return True when a candidate land cell should be skipped."""
    if hudson_river_polylines and is_hudson_river_cell(lat, lon, hudson_river_polylines):
        return True
    if is_open_water_cell(lat, lon):
        return True
    if kind == "li":
        return is_water_location(lat, lon)
    if kind == "county":
        from map_presets_ny_metro import is_county_open_water
        if is_county_open_water(lat, lon):
            return True
        return is_metro_water_location(lat, lon)
    return False


def _cell_inside_allowed_relation(
    x: int,
    y: int,
    lat: float,
    lon: float,
    kind: str,
    relation_indexed: dict[int, list],
    bounds: dict,
    width: int,
    height: int,
) -> bool:
    """True when (lat, lon) lies inside the OSM relation for this land kind."""
    if kind == "li":
        indexed = relation_indexed.get(LONG_ISLAND_RELATION_ID)
        if not indexed:
            return False
        if cell_center_in_relation(lat, lon, indexed):
            return True
        return cell_intersects_relation(x, y, bounds, width, height, indexed, subgrid_size=0)
    for relation_id, _label, rel_kind in LAND_AREA_RELATIONS:
        if rel_kind != "county":
            continue
        indexed = relation_indexed.get(relation_id)
        if not indexed:
            continue
        if cell_center_in_relation(lat, lon, indexed):
            return True
        if cell_intersects_relation(x, y, bounds, width, height, indexed, subgrid_size=0):
            return True
    return False


def strict_enforce_relation_bounds(
    population: dict[tuple[int, int], int],
    nyc_metro_cells: set[tuple[int, int]],
    extra_cells_by_kind: dict[tuple[int, int], str],
    relation_indexed: dict[int, list],
    hudson_river_polylines: list | None,
) -> dict[tuple[int, int], int]:
    """Keep only cells inside their OSM relation (or NYC map); drop all strays."""
    bounds = get_ny_metro_bounds()
    width, height = bounds["width"], bounds["height"]
    kept: dict[tuple[int, int], int] = {}
    removed = 0
    for pt, pop in population.items():
        x, y = pt
        lat, lon = _grid_to_latlon(x, y, bounds, width, height)
        if hudson_river_polylines and is_hudson_river_cell(lat, lon, hudson_river_polylines):
            removed += 1
            continue
        if is_open_water_cell(lat, lon):
            removed += 1
            continue
        if pt in nyc_metro_cells:
            kept[pt] = pop
            continue
        kind = extra_cells_by_kind.get(pt, "li")
        if (
            _cell_inside_allowed_relation(
                x, y, lat, lon, kind, relation_indexed, bounds, width, height,
            )
            and not _exclude_extra_land_cell(lat, lon, kind, hudson_river_polylines)
        ):
            kept[pt] = pop
        else:
            removed += 1
    if removed:
        print(f"  Strict OSM bounds: removed {removed} cells outside their relation")
    return kept


def audit_relation_coverage(
    included: set[tuple[int, int]],
    relation_id: int,
    label: str,
    indexed_polygons: list,
    hudson_river_polylines: list | None = None,
    land_kind: str = "li",
) -> tuple[int, int, list[tuple[int, int]]]:
    """Point-by-point scan: non-water cells whose center lies inside the OSM relation."""
    bounds = get_ny_metro_bounds()
    width, height = bounds["width"], bounds["height"]
    in_relation_included = 0
    missing: list[tuple[int, int]] = []
    hr_lines = hudson_river_polylines if hudson_river_polylines is not None else None
    for x in range(width + 1):
        for y in range(height + 1):
            lat, lon = _grid_to_latlon(x, y, bounds, width, height)
            if _exclude_extra_land_cell(lat, lon, land_kind, hr_lines):
                continue
            if not cell_center_in_relation(lat, lon, indexed_polygons):
                if not cell_intersects_relation(
                    x, y, bounds, width, height, indexed_polygons, subgrid_size=0,
                ):
                    continue
            if (x, y) in included:
                in_relation_included += 1
            else:
                missing.append((x, y))
    print(
        f"  {label} relation {relation_id}: "
        f"{in_relation_included} included, {len(missing)} land cells in relation but missing"
    )
    return in_relation_included, len(missing), missing


def add_relation_land_cells(
    existing: set[tuple[int, int]],
    indexed_polygons: list,
    hudson_river_polylines: list | None = None,
    land_kind: str = "li",
) -> set[tuple[int, int]]:
    """Add metro grid cells whose center lies inside a land relation (interior land, not water)."""
    p_min_lon = min(item[0] for item in indexed_polygons)
    p_max_lon = max(item[1] for item in indexed_polygons)
    p_min_lat = min(item[2] for item in indexed_polygons)
    p_max_lat = max(item[3] for item in indexed_polygons)

    bounds = get_ny_metro_bounds()
    width, height = bounds["width"], bounds["height"]
    cell_lat = (bounds["max_lat"] - bounds["min_lat"]) / height
    cell_lon = (bounds["max_lon"] - bounds["min_lon"]) / width
    half_lat, half_lon = cell_lat / 2.0, cell_lon / 2.0
    added: set[tuple[int, int]] = set()
    hr_lines = hudson_river_polylines if hudson_river_polylines is not None else None

    for x in range(width + 1):
        for y in range(height + 1):
            pt = (x, y)
            if pt in existing:
                continue
            lat, lon = _grid_to_latlon(x, y, bounds, width, height)
            cell_min_lon = lon - half_lon
            cell_max_lon = lon + half_lon
            cell_min_lat = lat - half_lat
            cell_max_lat = lat + half_lat
            if (
                cell_max_lon < p_min_lon
                or cell_min_lon > p_max_lon
                or cell_max_lat < p_min_lat
                or cell_min_lat > p_max_lat
            ):
                continue
            if not cell_center_in_relation(lat, lon, indexed_polygons):
                if not cell_intersects_relation(
                    x, y, bounds, width, height, indexed_polygons, subgrid_size=0,
                ):
                    continue
            if _exclude_extra_land_cell(lat, lon, land_kind, hr_lines):
                continue
            added.add(pt)
    return added


def remove_hudson_river_cells(
    cells: set[tuple[int, int]],
    hudson_river_polylines: list,
) -> set[tuple[int, int]]:
    """Drop cells whose center lies inside Hudson River relation 2148192."""
    bounds = get_ny_metro_bounds()
    width, height = bounds["width"], bounds["height"]
    kept: set[tuple[int, int]] = set()
    removed = 0
    for pt in cells:
        lat, lon = _grid_to_latlon(pt[0], pt[1], bounds, width, height)
        if is_hudson_river_cell(lat, lon, hudson_river_polylines):
            removed += 1
            continue
        kept.add(pt)
    if removed:
        print(f"  Hudson River exclusion: removed {removed} cells inside relation {HUDSON_RIVER_RELATION_ID}")
    return kept


def _population_for_extra_cell(
    lat: float,
    lon: float,
    kind: str,
) -> int:
    from map_presets_ny_metro import (
        get_long_island_population_at_location,
        get_suburb_county_population_at_location,
    )

    if kind == "li":
        return get_long_island_population_at_location(lat, lon, False)
    return get_suburb_county_population_at_location(lat, lon, False)


def build_initial_population(
    included: set[tuple[int, int]],
    nyc_metro_cells: set[tuple[int, int]],
    extra_cells_by_kind: dict[tuple[int, int], str],
    hudson_river_polylines: list | None = None,
    relation_indexed: dict[int, list] | None = None,
) -> dict[tuple[int, int], int]:
    """Precompute {(x,y): population} — NYC INITIAL_POPULATION + relation land cells."""
    bounds = get_ny_metro_bounds()
    width, height = bounds["width"], bounds["height"]
    nyc_1830_target = NYC_POPULATION["total_population_1830"]

    population: dict[tuple[int, int], int] = {}
    nyc_area_cells: list[tuple[int, int]] = []

    from map_presets_ny_metro import nyc_grid_to_metro_grid

    for (nx, ny), raw_pop in NYC_INITIAL_POPULATION.items():
        if raw_pop <= 0:
            continue
        gx, gy = nyc_grid_to_metro_grid(nx, ny)
        pt = (gx, gy)
        lat, lon = _grid_to_latlon(gx, gy, bounds, width, height)
        if hudson_river_polylines and is_hudson_river_cell(lat, lon, hudson_river_polylines):
            continue
        population[pt] = population.get(pt, 0) + int(raw_pop)
        if pt not in nyc_area_cells:
            nyc_area_cells.append(pt)

    nyc_raw_total = sum(population[c] for c in nyc_area_cells)
    if nyc_raw_total > 0:
        nyc_scale = nyc_1830_target / nyc_raw_total
        for c in nyc_area_cells:
            population[c] = max(1, int(round(population[c] * nyc_scale)))
        nyc_actual = sum(population[c] for c in nyc_area_cells)
        if nyc_actual != nyc_1830_target and nyc_area_cells:
            population[nyc_area_cells[0]] = max(
                1, population[nyc_area_cells[0]] + (nyc_1830_target - nyc_actual),
            )

    for pt, kind in sorted(extra_cells_by_kind.items()):
        if pt in nyc_area_cells or pt not in included:
            continue
        lat, lon = _grid_to_latlon(pt[0], pt[1], bounds, width, height)
        if _exclude_extra_land_cell(lat, lon, kind, hudson_river_polylines):
            continue
        pop = _population_for_extra_cell(lat, lon, kind)
        if pop > 0:
            population[pt] = pop

    if relation_indexed:
        population = strict_enforce_relation_bounds(
            population, set(nyc_metro_cells), extra_cells_by_kind,
            relation_indexed, hudson_river_polylines,
        )

    return {pt: pop for pt, pop in population.items() if pop > 0}


def _format_population_dict(population: dict[tuple[int, int], int]) -> str:
    """One grid cell per line for readable map_presets_ny_metro.py."""
    lines = ["NY_METRO_INITIAL_POPULATION: dict[tuple[int, int], int] = {"]
    items = sorted(population.items())
    for i, ((x, y), count) in enumerate(items):
        suffix = "," if i < len(items) - 1 else ""
        lines.append(f"    ({x}, {y}): {count}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def write_preset_data(population: dict[tuple[int, int], int]) -> None:
    sorted_pop = dict(sorted(population.items()))
    pop_total = sum(sorted_pop.values())
    with open("map_presets_ny_metro.py", "r", encoding="utf-8") as f:
        content = f.read()
    if not re.search(r"NY_METRO_INITIAL_POPULATION: dict\[tuple\[int, int\], int\] = \{", content):
        print("ERROR: NY_METRO_INITIAL_POPULATION not found")
        sys.exit(1)
    content = re.sub(
        r"NY_METRO_INITIAL_POPULATION: dict\[tuple\[int, int\], int\] = \{.*?\}\n",
        _format_population_dict(sorted_pop) + "\n",
        content,
        flags=re.DOTALL,
        count=1,
    )
    content = re.sub(
        r"('total_population_1830': )\d+",
        rf"\g<1>{pop_total}",
        content,
        count=1,
    )
    content = re.sub(
        r"(population_targets_by_year': \{[^}]*1830: )\d+",
        rf"\g<1>{pop_total}",
        content,
        count=1,
    )
    with open("map_presets_ny_metro.py", "w", encoding="utf-8") as f:
        f.write(content)


def main():
    allow_offline = "--offline" in sys.argv
    audit_only = "--audit" in sys.argv
    bounds = get_ny_metro_bounds()
    width, height = bounds["width"], bounds["height"]
    from map_presets_ny_metro import get_nyc_matched_cell_degrees
    cell_lat, cell_lon = get_nyc_matched_cell_degrees()
    print("Generating NY Metro included points and population...")
    print("  Step 1: OSM NYC map (NYC_INITIAL_POPULATION → metro grid)")
    print("  Step 2: Land cells strictly inside OSM relations "
          "(LI, Hudson, Bergen, Rockland, Westchester, Union, Middlesex, Connecticut)")
    print(f"  Step 3: Exclude Hudson River relation {HUDSON_RIVER_RELATION_ID} "
          "and cells fully inside open water (Sound, bays, wide Hudson)")
    print(f"  Grid: {width}x{height} (NYC-matched cell {cell_lat:.5f}° lat × {cell_lon:.5f}° lon)")
    print(f"  Bounds: {bounds['min_lat']:.3f}–{bounds['max_lat']:.3f} lat, "
          f"{bounds['min_lon']:.3f}–{bounds['max_lon']:.3f} lon")

    print(f"  Loading Hudson River exclusion relation {HUDSON_RIVER_RELATION_ID}...")
    hr_polylines = load_hudson_river_polylines(allow_offline)

    print("  Loading open-water polygons (Hudson wide reaches, Long Island Sound, bays)...")
    global _metro_water_indexed_cache
    _metro_water_indexed_cache = _indexed_polygons(fetch_metro_water_polygons(allow_offline))

    nyc_metro_cells = convert_nyc_points_to_metro()
    points = remove_hudson_river_cells(set(nyc_metro_cells), hr_polylines)
    print(f"  NYC OSM cells (INITIAL_POPULATION): {len(points)} after Hudson River filter")

    extra_cells_by_kind: dict[tuple[int, int], str] = {}
    relation_indexed: dict[int, list] = {}

    for relation_id, label, kind in LAND_AREA_RELATIONS:
        print(f"  Scanning OSM relation {relation_id} ({label})...")
        polygons = prepare_relation_for_scan(relation_id, allow_offline)
        indexed = _indexed_polygons(polygons)
        relation_indexed[relation_id] = indexed
        audit_relation_coverage(
            points, relation_id, label, indexed,
            hudson_river_polylines=hr_polylines, land_kind=kind,
        )
        if audit_only:
            continue
        added = add_relation_land_cells(
            points, indexed, hudson_river_polylines=hr_polylines, land_kind=kind,
        )
        print(f"  {label}: added {len(added)} land cells from relation {relation_id}")
        points |= added
        for pt in added:
            extra_cells_by_kind[pt] = kind

    if audit_only:
        print("Audit complete (--audit, no file write)")
        return

    if not allow_offline:
        for relation_id, label, kind in LAND_AREA_RELATIONS:
            indexed = relation_indexed[relation_id]
            _, missing_count, missing = audit_relation_coverage(
                points, relation_id, label, indexed,
                hudson_river_polylines=hr_polylines, land_kind=kind,
            )
            if missing:
                print(f"  WARNING: {missing_count} land cells still missing inside relation {relation_id}")

    points = remove_hudson_river_cells(points, hr_polylines)
    li_only = {pt for pt in points if pt not in nyc_metro_cells and extra_cells_by_kind.get(pt) == "li"}
    county_only = {pt for pt in points if pt not in nyc_metro_cells and extra_cells_by_kind.get(pt) == "county"}
    print(
        f"  Total land cells: {len(points)} "
        f"(NYC {len(points & nyc_metro_cells)}, LI {len(li_only)}, counties {len(county_only)})"
    )
    if len(points) < 200:
        print("ERROR: too few points")
        sys.exit(1)
    missing_nyc = nyc_metro_cells - points
    if missing_nyc:
        print(f"  NOTE: {len(missing_nyc)} NYC cells removed (Hudson River / water)")

    population = build_initial_population(
        points, nyc_metro_cells, extra_cells_by_kind, hr_polylines, relation_indexed,
    )
    pop_total = sum(population.values())
    print(f"  Precomputed population cells: {len(population)}, total={pop_total}")
    write_preset_data(population)
    print("Updated map_presets_ny_metro.py")
    if not allow_offline:
        for relation_id, label, kind in LAND_AREA_RELATIONS:
            audit_relation_coverage(
                set(population.keys()), relation_id, label, relation_indexed[relation_id],
                hudson_river_polylines=hr_polylines, land_kind=kind,
            )


if __name__ == "__main__":
    main()
