"""Non-widget Qt helpers: paths, file I/O, markdown parsing, vehicle filters, map assets."""
from __future__ import annotations

import logging
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent

# Maximum time (ms) for line creation and map refresh - prevents long freezes
LINE_CREATION_MAX_MS = 15000
LINE_EXTENSION_MAX_MS_PER_SEGMENT = 5000
MAP_REFRESH_MAX_MS = 15000
MAP_DISPLAY_MAX_ROADS = 400

SAVEGAMES_DIR = _PROJECT_ROOT / "savegames"
APP_LOGO_PATH = _PROJECT_ROOT / "assets" / "locologo.png"
APP_SPLASH_PATH = _PROJECT_ROOT / "assets" / "splash.png"

# Leaflet embedded content (loaded once, avoids CDN blocking in QWebEngineView)
_LEAFLET_CSS_CACHE = None
_LEAFLET_JS_CACHE = None

# JavaScript run in the map web view when the pane resizes (not a widget action itself).
_MAP_ENSURE_VISIBLE_JS = (
    "(function(){"
    "if(typeof ensureMapContainerSize==='function'){ensureMapContainerSize();}"
    "if(typeof window.ensureMapVisible==='function'){window.ensureMapVisible();}"
    "else if(window.mapInstance){window.mapInstance.invalidateSize(true);}"
    "else if(typeof initMap==='function'){initMap();}"
    "})();"
)
_MAP_INVALIDATE_JS = _MAP_ENSURE_VISIBLE_JS


def parse_rules_markdown_sections(content: str):
    """Split RULES.md by top-level ## headings (not ###). Returns [(title, body), ...]."""
    lines = content.splitlines()
    sections = []
    preamble = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('## ') and not line.startswith('###'):
            break
        preamble.append(line)
        i += 1
    preamble_text = '\n'.join(preamble).strip()
    if preamble_text:
        sections.append(('Introduction', preamble_text))
    current_title = None
    current_lines = []
    while i < len(lines):
        line = lines[i]
        if line.startswith('## ') and not line.startswith('###'):
            if current_title is not None:
                sections.append((current_title, '\n'.join(current_lines).strip()))
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
        i += 1
    if current_title is not None:
        sections.append((current_title, '\n'.join(current_lines).strip()))
    if not sections and content.strip():
        return [('Rules', content.strip())]
    return sections


# Backward-compatible alias used by qt_networked and tests
_parse_rules_markdown_sections = parse_rules_markdown_sections


def locomotive_is_self_contained(loco):
    """True when a locomotive is a self-contained unit (EMU/DMU/subway/etc.), not a loco+cars consist."""
    return loco is not None and not getattr(loco, 'can_have_railcars', True)


_locomotive_is_self_contained = locomotive_is_self_contained


def trainset_is_dmu_or_emu(trainset):
    """True if the trainset's locomotive is a DMU or EMU (used for marketplace trainset tab filter)."""
    loco = getattr(trainset, 'locomotive', None)
    if not loco:
        return False
    if locomotive_is_self_contained(loco):
        return True
    name_lower = (getattr(loco, 'name', '') or '').lower()
    fuel = getattr(loco, 'fuel', None)
    fuel_name = getattr(fuel, '__class__', None).__name__ if fuel else ''
    if 'emu' in name_lower or 'interurban' in name_lower:
        return True
    if fuel_name == 'Oil' and ('multiple unit' in name_lower or 'railcar' in name_lower or 'dmu' in name_lower):
        return True
    return False


_trainset_is_dmu_or_emu = trainset_is_dmu_or_emu


def get_map_html_file_path(player_id: int) -> Path:
    """Return path for map HTML file. Loading from local file enables OSM tile requests."""
    return Path(tempfile.gettempdir()) / f"gocommuter_map_{player_id}.html"


_get_map_html_file_path = get_map_html_file_path


def write_map_html_for_osm(player_id: int, html_content: str) -> Path | None:
    """Write map HTML to a local file. Returns path if successful, None otherwise."""
    for path in [
        get_map_html_file_path(player_id),
        _PROJECT_ROOT / f"gocommuter_map_{player_id}.html",
    ]:
        try:
            path.write_text(html_content, encoding="utf-8")
            return path
        except Exception:
            continue
    return None


_write_map_html_for_osm = write_map_html_for_osm


def get_embedded_leaflet():
    """Load Leaflet CSS and JS from static files. Embedded to avoid QWebEngineView blocking CDN."""
    global _LEAFLET_CSS_CACHE, _LEAFLET_JS_CACHE
    if _LEAFLET_CSS_CACHE is None or _LEAFLET_JS_CACHE is None:
        static_dir = _PROJECT_ROOT / "static"
        css_path = static_dir / "leaflet.css"
        js_path = static_dir / "leaflet.js"
        try:
            _LEAFLET_CSS_CACHE = css_path.read_text(encoding="utf-8", errors="replace")
            _LEAFLET_JS_CACHE = js_path.read_text(encoding="utf-8", errors="replace")
            _LEAFLET_JS_CACHE = _LEAFLET_JS_CACHE.replace("</script>", "<\\/script>")
            _LEAFLET_CSS_CACHE = _LEAFLET_CSS_CACHE.replace("</style>", "<\\/style>")
        except Exception as e:
            logger.warning("Could not load embedded Leaflet: %s", e)
            _LEAFLET_CSS_CACHE = ""
            _LEAFLET_JS_CACHE = ""
    return _LEAFLET_CSS_CACHE, _LEAFLET_JS_CACHE


_get_embedded_leaflet = get_embedded_leaflet


@dataclass
class MapViewConfig:
    center_lat: float
    center_lon: float
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    zoom_level: int
    min_zoom: int
    max_zoom: int
    map_width_val: float
    map_height_val: float
    grid_size: float
    use_preset_bounds: bool
    preset_min_lat: float
    preset_max_lat: float
    preset_min_lon: float
    preset_max_lon: float
    preset_lat_scale: float
    preset_lon_scale: float
    lon_deg_per_km: float
    view_center_lat: float = None  # where the map view opens (default: center_lat/lon)
    view_center_lon: float = None


def osm_map_html_timeout_placeholder() -> str:
    """Minimal HTML when map refresh exceeds time limit."""
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Map</title></head>
<body style="margin:0;padding:20px;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;box-sizing:border-box;">
<p style="text-align:center;color:#666;">Map refresh timed out (15s limit).<br>Click <b>Refresh Map</b> to try again.</p>
</body></html>"""


_generate_osm_map_html_timeout_placeholder = osm_map_html_timeout_placeholder


def compute_map_view_config(game_map) -> MapViewConfig:
    """Bounds, zoom, and grid-to-lat/lon scales for map display."""
    from map_preset_loader import get_map_bounds, get_map_zoom, get_default_center_lat, get_default_center_lon
    from map_preset_loader import get_default_map_bounds, get_default_map_zoom
    default_bounds, default_zoom = get_default_map_bounds(), get_default_map_zoom()

    preset_name = getattr(game_map, 'map_preset_name', None)
    center_lat_check = getattr(game_map, 'map_center_lat', None)
    center_lon_check = getattr(game_map, 'map_center_lon', None)

    if preset_name:
        center_lat = center_lat_check if center_lat_check is not None else get_default_center_lat(preset_name)
        center_lon = center_lon_check if center_lon_check is not None else get_default_center_lon(preset_name)
    else:
        center_lat = center_lat_check if center_lat_check is not None else default_bounds['center_lat']
        center_lon = center_lon_check if center_lon_check is not None else default_bounds['center_lon']

    if preset_name:
        bounds = get_map_bounds(preset_name)
        zoom_cfg = get_map_zoom(preset_name)
        if bounds and all(k in bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
            min_lat, max_lat = bounds['min_lat'], bounds['max_lat']
            min_lon, max_lon = bounds['min_lon'], bounds['max_lon']
        else:
            # Preset without geographic bounds (a plain grid): the view must cover the
            # whole grid, or lines and stations near the map edge get clipped away.
            _w = game_map.width if getattr(game_map, 'width', None) else default_bounds['default_width']
            _h = game_map.height if getattr(game_map, 'height', None) else default_bounds['default_height']
            _size = max(_w, _h)
            _lat_span = max(0.2, (_size / 2) / 111.0)
            _lon_span = max(0.2, (_size / 2) / (111.0 * math.cos(math.radians(center_lat)) or 111.0))
            min_lat = center_lat - _lat_span
            max_lat = center_lat + _lat_span
            min_lon = center_lon - _lon_span
            max_lon = center_lon + _lon_span
        if zoom_cfg:
            zoom_level = zoom_cfg.get('default_zoom', 11)
            min_zoom = zoom_cfg.get('min_zoom', 10)
            max_zoom = zoom_cfg.get('max_zoom', 18)
        else:
            zoom_level, min_zoom, max_zoom = 11, 10, 18
    else:
        map_width = game_map.width if getattr(game_map, 'width', None) else default_bounds['default_width']
        map_height = game_map.height if getattr(game_map, 'height', None) else default_bounds['default_height']
        lat_span = (map_height / 2) / 111.0
        lon_deg_per_km_tmp = 111.0 * math.cos(math.radians(center_lat)) or 111.0
        lon_span = (map_width / 2) / lon_deg_per_km_tmp
        min_lat = center_lat - lat_span
        max_lat = center_lat + lat_span
        min_lon = center_lon - lon_span
        max_lon = center_lon + lon_span
        zoom_level = default_zoom.get('default_zoom', 12)
        map_diagonal = math.sqrt(map_width ** 2 + map_height ** 2)
        min_zoom = max(1, int(12 - math.log10(map_diagonal / 50))) if map_diagonal > 0 else 8
        max_zoom = default_zoom.get('max_zoom', 19)

    if center_lat is None or center_lon is None:
        if preset_name:
            center_lat = get_default_center_lat(preset_name)
            center_lon = get_default_center_lon(preset_name)
        else:
            center_lat = default_bounds['center_lat']
            center_lon = default_bounds['center_lon']

    if min_lat is None or max_lat is None or min_lon is None or max_lon is None:
        min_lat = center_lat - 0.2
        max_lat = center_lat + 0.2
        min_lon = center_lon - 0.2
        max_lon = center_lon + 0.2

    zoom_level = zoom_level if zoom_level is not None else 11
    min_zoom = min_zoom if min_zoom is not None else 10
    max_zoom = max_zoom if max_zoom is not None else 18

    map_width_val = game_map.width if getattr(game_map, 'width', None) else 50
    map_height_val = game_map.height if getattr(game_map, 'height', None) else 50
    grid_size = max(map_width_val, map_height_val)

    preset_bounds = get_map_bounds(preset_name) if preset_name else None
    use_preset_bounds = bool(
        preset_bounds and all(k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon'))
    )
    if use_preset_bounds:
        preset_min_lat = preset_bounds['min_lat']
        preset_max_lat = preset_bounds['max_lat']
        preset_min_lon = preset_bounds['min_lon']
        preset_max_lon = preset_bounds['max_lon']
        preset_lat_scale = (preset_max_lat - preset_min_lat) / map_height_val if map_height_val > 0 else 0.0
        preset_lon_scale = (preset_max_lon - preset_min_lon) / map_width_val if map_width_val > 0 else 0.0
        lon_deg_per_km = 111.0
    else:
        preset_min_lat = preset_max_lat = preset_min_lon = preset_max_lon = 0.0
        preset_lat_scale = preset_lon_scale = 0.0
        lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat)) or 111.0

    from map_preset_loader import get_map_view_center
    view_center = get_map_view_center(preset_name) if preset_name else None
    view_center_lat, view_center_lon = view_center if view_center else (center_lat, center_lon)

    return MapViewConfig(
        center_lat=float(center_lat),
        center_lon=float(center_lon),
        min_lat=float(min_lat),
        max_lat=float(max_lat),
        min_lon=float(min_lon),
        max_lon=float(max_lon),
        zoom_level=int(zoom_level),
        min_zoom=int(min_zoom),
        max_zoom=int(max_zoom),
        map_width_val=float(map_width_val),
        map_height_val=float(map_height_val),
        grid_size=float(grid_size),
        use_preset_bounds=use_preset_bounds,
        preset_min_lat=float(preset_min_lat),
        preset_max_lat=float(preset_max_lat),
        preset_min_lon=float(preset_min_lon),
        preset_max_lon=float(preset_max_lon),
        preset_lat_scale=float(preset_lat_scale),
        preset_lon_scale=float(preset_lon_scale),
        lon_deg_per_km=float(lon_deg_per_km),
        view_center_lat=float(view_center_lat),
        view_center_lon=float(view_center_lon),
    )


def filter_population_within_bounds(population_data, min_lat, max_lat, min_lon, max_lon) -> list:
    filtered = []
    for point in population_data or []:
        lat = point.get('lat')
        lon = point.get('lon')
        if lat is not None and lon is not None and min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            filtered.append(point)
    return filtered


def grid_point_to_map_latlon(x: float, y: float, game_map) -> list | None:
    """Convert one grid point to [lat, lon] for map overlay."""
    view = compute_map_view_config(game_map)
    if view.use_preset_bounds:
        lat = view.preset_min_lat + y * view.preset_lat_scale
        lon = view.preset_min_lon + x * view.preset_lon_scale
    else:
        lat_km = y - view.grid_size // 2
        lon_km = x - view.grid_size // 2
        lat = view.center_lat + (lat_km / 111.0)
        lon = view.center_lon + (lon_km / view.lon_deg_per_km)
    return [lat, lon]


def grid_route_to_map_line_points(route, game_map, min_lat, max_lat, min_lon, max_lon) -> list:
    """Convert a grid-coordinate route to [lat, lon] points.

    Every point is kept, even off the map's edge: dropping one would join its neighbours
    with a straight chord and lose the segments in between. (The bounds parameters are
    kept for callers; Leaflet draws off-screen parts itself.)
    """
    line_points = []
    for point in route or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = float(point[0]), float(point[1])
        elif hasattr(point, 'x') and hasattr(point, 'y'):
            x, y = float(point.x), float(point.y)
        else:
            continue
        latlon = grid_point_to_map_latlon(x, y, game_map)
        if latlon:
            line_points.append(latlon)
    return line_points


def build_roads_overlay_data(game_map, view: MapViewConfig, current_year: int, max_roads: int = MAP_DISPLAY_MAX_ROADS) -> list:
    """Convert built roads on the game map to lat/lon polylines for Leaflet."""
    roads_data = []
    if not hasattr(game_map, 'get_roads'):
        return roads_data
    roads_dict = game_map.get_roads()
    if not roads_dict or view.grid_size <= 0:
        return roads_data

    for road_name, road in roads_dict.items():
        year_built = getattr(road, 'year_built', None)
        if year_built is not None and year_built > current_year:
            continue
        if not hasattr(road, 'get_route'):
            continue
        route = road.get_route()
        if not route:
            continue
        road_points = []
        for point in route:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                x, y = float(point[0]), float(point[1])
            elif hasattr(point, 'x') and hasattr(point, 'y'):
                x, y = float(point.x), float(point.y)
            else:
                continue
            if view.use_preset_bounds:
                lat = view.preset_min_lat + y * view.preset_lat_scale
                lon = view.preset_min_lon + x * view.preset_lon_scale
            else:
                lat_km = y - view.grid_size // 2
                lon_km = x - view.grid_size // 2
                lat = view.center_lat + (lat_km / 111.0)
                lon = view.center_lon + (lon_km / view.lon_deg_per_km)
            if view.min_lat <= lat <= view.max_lat and view.min_lon <= lon <= view.max_lon:
                road_points.append([lat, lon])
        if len(road_points) >= 2:
            roads_data.append({'name': road_name, 'points': road_points})
    if len(roads_data) > max_roads:
        roads_data = roads_data[:max_roads]
    return roads_data


def ensure_game_map_timeline(game_map) -> None:
    """Fill missing start_year/end_year on game_map from preset or standard defaults."""
    if not hasattr(game_map, 'start_year') or not hasattr(game_map, 'end_year'):
        return
    if game_map.start_year is not None and game_map.end_year is not None:
        return
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import get_map_timeline
        timeline = get_map_timeline(preset_name)
        if timeline:
            game_map.end_year = timeline.get('end_year', game_map.end_year)
            game_map.start_year = timeline.get('start_year', game_map.start_year)
            return
    from map_preset_loader import get_default_map_timeline
    default_timeline = get_default_map_timeline()
    game_map.end_year = default_timeline['end_year']
    game_map.start_year = default_timeline['start_year']


def player_owned_line_combo_entries(
    operations,
    player_id: int,
    *,
    include_build_queue: bool = True,
) -> list[tuple[str, object | None]]:
    """Line names for map sidebar combos: owned lines plus optional in-progress builds."""
    entries: list[tuple[str, object | None]] = []
    seen: set[str] = set()
    all_lines = operations.get_map_lines() if operations else {}
    if all_lines:
        for line_name, line in all_lines.items():
            owner = operations.get_line_owner(line)
            if owner and operations.get_player_id(owner) == player_id:
                if line_name not in seen:
                    entries.append((line_name, line))
                    seen.add(line_name)
    if include_build_queue:
        for build in operations.get_build_queue():
            if build.get('type') != 'line' or not build.get('player'):
                continue
            if operations.get_player_id(build.get('player')) != player_id:
                continue
            line_name = build.get('data', {}).get('name')
            if line_name and line_name not in seen:
                entries.append((line_name, None))
                seen.add(line_name)
    return entries


def marketplace_sale_html_suffix(sale_item, current_year: int, *, max_inflation_factor: float | None = None) -> str:
    """Rich-text HTML fragment for an active marketplace sale discount, or empty string."""
    if not (
        hasattr(sale_item, 'discount_percent')
        and sale_item.discount_percent > 0
        and hasattr(sale_item, 'sale_year')
        and sale_item.sale_year == current_year
    ):
        return ""
    from inflation_utils import get_inflation_factor
    original_base_price = getattr(sale_item, 'original_price', sale_item.price)
    if max_inflation_factor is not None:
        original_price = int(get_inflation_factor(current_year, max_factor=max_inflation_factor) * original_base_price)
    else:
        original_price = int(get_inflation_factor(current_year) * original_base_price)
    return (
        f" <span style='color: red; font-weight: bold;'>"
        f"SALE: {sale_item.discount_percent}% OFF (was ${original_price})</span>"
    )


def locomotive_marketplace_category(item) -> str:
    """Classify a locomotive listing: ferry, horses_trolleys, trainset, or locomotive."""
    item_name = getattr(item, 'name', 'Unknown')
    is_horse = 'horse' in item_name.lower()
    if not is_horse:
        from game_resource import Food
        is_horse = isinstance(getattr(item, 'fuel', None), Food)
    needs_overhead_wire = getattr(item, 'needs_overhead_wire', False)
    can_have_cars = getattr(item, 'can_have_railcars', True)
    needs_third_rail = getattr(item, 'needs_third_rail', False)
    is_trolley = needs_overhead_wire and not can_have_cars and not needs_third_rail
    if not is_trolley:
        item_name_lower = item_name.lower()
        is_trolley = any(term in item_name_lower for term in ('trolley', 'streetcar', 'lrv', 'pcc', 'birney'))
    if is_horse or is_trolley:
        return 'horses_trolleys'
    if not can_have_cars:
        return 'trainset'
    return 'locomotive'


@dataclass
class MarketplaceGroups:
    line_groups: dict
    trainset_groups: dict
    loco_groups: dict
    ferry_groups: dict
    horses_trolleys_groups: dict
    railcar_groups: dict
    other_vehicles: list


def group_marketplace_listings(operations, lines, vehicles, current_year: int) -> MarketplaceGroups:
    """Group marketplace lines and vehicles for UI display (no widgets)."""
    # Vehicle classes, not the Bus/Ferry *line* classes in line.py: with those, ferries and
    # buses for sale matched nothing below and were never listed.
    from vehicle import Bus, Ferry, Locomotive, Railcar, Trainset

    line_groups: dict = {}
    if lines:
        for line_sale_item in lines:
            item = line_sale_item.get_item()
            line_name = item.name
            seller = line_sale_item.get_seller()
            key = (line_name, seller)
            line_groups.setdefault(key, []).append(line_sale_item)

    trainset_groups: dict = {}
    loco_groups: dict = {}
    ferry_groups: dict = {}
    horses_trolleys_groups: dict = {}
    railcar_groups: dict = {}
    other_vehicles: list = []

    if vehicles:
        for sale_item in vehicles:
            item = sale_item.get_item()
            seller = sale_item.get_seller()
            if isinstance(item, Trainset):
                if not trainset_is_dmu_or_emu(item):
                    continue
                loco_name = operations.get_trainset_locomotive_name(item)
                num_cars = len(operations.get_trainset_cars(item))
                capacity = operations.get_trainset_capacity(item)
                year = operations.get_trainset_locomotive_year(item)
                if year and year > current_year:
                    continue
                key = (loco_name, num_cars, capacity, seller)
                trainset_groups.setdefault(key, []).append(sale_item)
            elif isinstance(item, Locomotive):
                year = getattr(item, 'year_introduced', None)
                item_name = getattr(item, 'name', 'Unknown')
                if year and year > current_year:
                    continue
                category = locomotive_marketplace_category(item)
                if category == 'horses_trolleys':
                    key = (item_name, seller)
                    horses_trolleys_groups.setdefault(key, []).append(sale_item)
                elif category == 'trainset':
                    capacity = getattr(item, 'capacity', 0)
                    ts_key = (item_name, 0, capacity, seller)
                    trainset_groups.setdefault(ts_key, []).append(sale_item)
                else:
                    key = (item_name, seller)
                    loco_groups.setdefault(key, []).append(sale_item)
            elif isinstance(item, Ferry):
                year = getattr(item, 'year_introduced', None)
                item_name = getattr(item, 'name', 'Unknown')
                if year and year > current_year:
                    continue
                key = (item_name, seller)
                ferry_groups.setdefault(key, []).append(sale_item)
            elif isinstance(item, Bus):
                year = getattr(item, 'year_introduced', None)
                item_name = getattr(item, 'name', 'Unknown')
                if year and year > current_year:
                    continue
                key = (item_name, seller)
                horses_trolleys_groups.setdefault(key, []).append(sale_item)
            elif isinstance(item, Railcar):
                year = getattr(item, 'year_introduced', None) if hasattr(item, 'year_introduced') else None
                item_name = getattr(item, 'name', 'Unknown')
                capacity = getattr(item, 'capacity', 0)
                if year and year > current_year:
                    continue
                key = (item_name, capacity, seller)
                railcar_groups.setdefault(key, []).append(sale_item)
            else:
                other_vehicles.append(sale_item)

    return MarketplaceGroups(
        line_groups=line_groups,
        trainset_groups=trainset_groups,
        loco_groups=loco_groups,
        ferry_groups=ferry_groups,
        horses_trolleys_groups=horses_trolleys_groups,
        railcar_groups=railcar_groups,
        other_vehicles=other_vehicles,
    )


# Map web-view JavaScript snippets (polling / cleanup; not widget actions).
MAP_JS_CLEAR_PENDING_ADD_STATION = (
    "window.pendingAddStation = null; window.pendingAddStationLat = null; "
    "window.pendingAddStationLon = null;"
)
MAP_JS_READ_AND_CLEAR_PENDING_ADD_STATION = (
    "(function(){ var ln=window.pendingAddStation, lat=window.pendingAddStationLat, "
    "lon=window.pendingAddStationLon; "
    "window.pendingAddStation=null; window.pendingAddStationLat=null; "
    "window.pendingAddStationLon=null; "
    "return (ln&&lat!=null&&lon!=null)?JSON.stringify({lineName:ln,lat:lat,lon:lon}):null; })();"
)
MAP_JS_CLEAR_PENDING_EXTEND_LINE = "window.pendingExtendLine = null;"
MAP_JS_READ_PENDING_EXTEND_LINE = "window.pendingExtendLine || null;"
MAP_JS_FINISH_LINE_CREATION = "JSON.stringify(window.finishLineCreation());"
MAP_JS_READ_AND_CLEAR_PENDING_SEGMENT_SELL = (
    "(function(){ var d=window.pendingSegmentSell; window.pendingSegmentSell=null; return d || null; })();"
)


def parse_map_latlon_points_json(points_json):
    """Parse finishLineCreation JSON; returns list of [lat, lon] or None if invalid."""
    import json
    if not points_json:
        return None
    latlon_points = json.loads(points_json) if isinstance(points_json, str) else points_json
    if not latlon_points or len(latlon_points) < 2:
        return None
    return latlon_points


def build_map_overview_text(operations, game_map) -> str:
    """Sidebar map overview text (no HTML regeneration)."""
    all_lines = operations.get_map_lines() if operations else {}
    map_info_text = "Map Overview\n\n"
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import get_map_display_name
        display_name = get_map_display_name(preset_name)
        map_info_text += f"Map Type: {display_name or preset_name}\n"
    else:
        map_info_text += "Map Type: Standard\n"
    if hasattr(game_map, 'width') and hasattr(game_map, 'height'):
        map_info_text += f"Size: {game_map.width}x{game_map.height}\n"
    if hasattr(game_map, 'start_year') and hasattr(game_map, 'end_year'):
        ensure_game_map_timeline(game_map)
        start_year = game_map.start_year
        end_year = game_map.end_year
        if start_year is not None and end_year is not None:
            map_info_text += f"Timeline: {start_year}-{end_year}\n"
    total_population = 0
    if hasattr(game_map, 'population') and game_map.population:
        total_population = sum(pop for pop in game_map.population.values() if pop > 0)
    map_info_text += f"Total Population: {total_population:,}\n"
    map_info_text += f"\nTotal Lines: {len(all_lines) if all_lines else 0}\n"
    if all_lines and operations:
        for line_name, line in all_lines.items():
            owner = operations.get_line_owner(line)
            owner_name = operations.get_player_name(owner) if owner else "Unclaimed"
            num_stations = len(operations.get_line_stations(line))
            num_trainsets = len(operations.get_line_vehicles(line))
            line_type_str = "Train"
            try:
                from line import Ferry, Bus
                if isinstance(line, Ferry):
                    line_type_str = "Ferry"
                elif isinstance(line, Bus):
                    line_type_str = "Bus"
            except Exception:
                pass
            map_info_text += f"\n{line_name} ({owner_name})\n"
            map_info_text += f"  Type: {line_type_str}\n"
            map_info_text += f"  Stations: {num_stations}\n"
            map_info_text += f"  Trainsets: {num_trainsets}\n"
    return map_info_text


def build_osm_map_html(
    *,
    view: MapViewConfig,
    leaflet_css: str,
    leaflet_js: str,
    population_json: str,
    lines_json: str,
    roads_json: str,
    stations_json: str,
    player_colors_hex_json: str,
) -> str:
    """Assemble Leaflet map HTML/JS from pre-serialized overlay data."""
    min_zoom = view.min_zoom
    max_zoom = view.max_zoom
    center_lat = view.view_center_lat if view.view_center_lat is not None else view.center_lat
    center_lon = view.view_center_lon if view.view_center_lon is not None else view.center_lon
    zoom_level = view.zoom_level
    min_lat = view.min_lat
    max_lat = view.max_lat
    min_lon = view.min_lon
    max_lon = view.max_lon
    map_width_val = view.map_width_val
    map_height_val = view.map_height_val
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>GoCommuter Map</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        {leaflet_css}
        html, body {{ 
            margin: 0; 
            padding: 0; 
            width: 100%;
            height: 100%;
            overflow: hidden;
            position: relative;
        }}
        #map {{ 
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            width: 100%;
            height: 100%;
        }}
        .line-end-label {{ pointer-events: none; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        // Position map tiles without 3D CSS transforms: on some Linux GPU drivers (Wayland,
        // Mesa) QtWebEngine loads the composited tile images but never draws them.
        window.L_DISABLE_3D = true;
    </script>
    <script>
        {leaflet_js}
    </script>
    <script>
        var mapInstance = null;
        function ensureMapContainerSize() {{
            var h = window.innerHeight || document.documentElement.clientHeight || 600;
            var w = window.innerWidth || document.documentElement.clientWidth || 800;
            document.documentElement.style.height = h + 'px';
            document.body.style.height = h + 'px';
            var mapEl = document.getElementById('map');
            if (mapEl) {{
                mapEl.style.height = h + 'px';
                mapEl.style.width = w + 'px';
            }}
        }}
        function initMap() {{
            if (typeof L === 'undefined') {{
                document.getElementById('map').innerHTML = '<div style="padding: 20px; color: red;">Error: Leaflet failed to load. Check internet or static files.</div>';
                return;
            }}
            if (window.mapInstance) return;
            try {{
                ensureMapContainerSize();
                mapInstance = L.map('map', {{
                    // Canvas instead of one SVG node per marker/line: pans and zooms stay smooth
                    // with thousands of population points.
                    preferCanvas: true,
                    minZoom: {min_zoom},
                    maxZoom: {max_zoom},
                    scrollWheelZoom: true,
                    dragging: true,
                    touchZoom: true,
                    doubleClickZoom: true,
                    boxZoom: true,
                    keyboard: true
                }}).setView([{center_lat}, {center_lon}], {zoom_level});
            
            // OpenStreetMap tiles (standard OSM display)
            var osmTiles = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                minZoom: {min_zoom},
                maxZoom: {max_zoom},
                tileSize: 256,
                zoomOffset: 0,
                keepBuffer: 4  // keep nearby tiles when panning instead of fetching them again
            }}).addTo(mapInstance);
            // OSM's tile servers answer 403 to some clients (usage policy / blocked networks):
            // after a few failed tiles switch to a fallback basemap so the map is never blank.
            var osmTileErrors = 0;
            osmTiles.on('tileerror', function() {{
                if (++osmTileErrors !== 4) return;
                mapInstance.removeLayer(osmTiles);
                L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png', {{
                    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions">CARTO</a>',
                    subdomains: 'abcd', minZoom: {min_zoom}, maxZoom: {max_zoom}
                }}).addTo(mapInstance);
            }});
            window.mapInstance = mapInstance;
            window.ensureMapVisible = function() {{
                ensureMapContainerSize();
                if (window.mapInstance) {{
                    // Leaflet skips the redraw when the size is unchanged; no animated pan.
                    window.mapInstance.invalidateSize(false);
                }}
            }};
            // Fix tiles not loading when map container had zero size at init (e.g. tab not yet shown)
            setTimeout(function() {{ if (window.ensureMapVisible) window.ensureMapVisible(); }}, 100);
            setTimeout(function() {{ if (window.ensureMapVisible) window.ensureMapVisible(); }}, 400);
            setTimeout(function() {{ if (window.ensureMapVisible) window.ensureMapVisible(); }}, 500);
            
            mapInstance.setMaxBounds([[{min_lat}, {min_lon}], [{max_lat}, {max_lon}]]);
            
            // Population coloring: Green up to 1000, then yellow, orange, red
            function getPopulationColor(pop) {{
                if (pop === 0) {{
                    return '#cccccc'; // Light gray for 0 population
                }} else if (pop <= 1000) {{
                    return '#88ff00'; // Green for 0-1000
                }} else if (pop <= 2000) {{
                    return '#ffff00'; // Yellow for 1001-2000
                }} else if (pop <= 3000) {{
                    return '#ff8800'; // Orange for 2001-3000
                }} else if (pop <= 5000) {{
                    return '#ff4400'; // Red-orange for 3001-5000
                }} else {{
                    return '#ff0000'; // Red for 5000+
                }}
            }}
        
        var populationData = {population_json};
        // Population points: one dedicated canvas, non-interactive markers.
        //  - their own canvas means refreshing lines/stations never redraws them;
        //  - non-interactive means Leaflet does not hit-test thousands of markers on every
        //    mouse move; clicks are resolved below with a small spatial index instead of one
        //    popup object per marker.
        window.populationMarkers = [];
        var populationIndex = {{}};
        var POP_INDEX_DEG = 0.02;
        if (populationData && populationData.length > 0) {{
            var populationRenderer = L.canvas({{ padding: 0.2 }});
            populationData.forEach(function(item) {{
                var pop = item.pop || 0;
                var marker = L.circleMarker([item.lat, item.lon], {{
                    renderer: populationRenderer,
                    interactive: false,
                    radius: Math.max(2, Math.min(2.5, Math.sqrt(Math.max(1, pop) / 50))),
                    fillColor: getPopulationColor(pop),
                    color: '#000',
                    weight: 1,
                    opacity: 0.8,
                    fillOpacity: 0.6
                }});
                marker.addTo(mapInstance);
                window.populationMarkers.push(marker);
                var key = Math.floor(item.lat / POP_INDEX_DEG) + ':' + Math.floor(item.lon / POP_INDEX_DEG);
                (populationIndex[key] = populationIndex[key] || []).push(item);
            }});
        }}
        mapInstance.on('popupopen', function() {{ window._lastPopupOpenTs = Date.now(); }});
        function showPopulationAtClick(e) {{
            // Not while drawing, and not when the click just opened a line/station popup.
            if ((typeof isCreatingLine !== 'undefined' && isCreatingLine) ||
                (typeof isExtendingLine !== 'undefined' && isExtendingLine) ||
                (typeof isCreatingStation !== 'undefined' && isCreatingStation) ||
                (typeof isSellingLineSegment !== 'undefined' && isSellingLineSegment)) return;
            if (Date.now() - (window._lastPopupOpenTs || 0) < 200) return;
            var cy = Math.floor(e.latlng.lat / POP_INDEX_DEG), cx = Math.floor(e.latlng.lng / POP_INDEX_DEG);
            var clickPt = mapInstance.latLngToContainerPoint(e.latlng);
            var best = null, bestD = 8 * 8;  // within 8 px of a point
            for (var dy = -1; dy <= 1; dy++) {{
                for (var dx = -1; dx <= 1; dx++) {{
                    var bucket = populationIndex[(cy + dy) + ':' + (cx + dx)];
                    if (!bucket) continue;
                    for (var k = 0; k < bucket.length; k++) {{
                        var p = mapInstance.latLngToContainerPoint([bucket[k].lat, bucket[k].lon]);
                        var d = (p.x - clickPt.x) * (p.x - clickPt.x) + (p.y - clickPt.y) * (p.y - clickPt.y);
                        if (d <= bestD) {{ bestD = d; best = bucket[k]; }}
                    }}
                }}
            }}
            if (!best) return;
            var gridX = best.grid_x !== undefined ? best.grid_x : 'N/A';
            var gridY = best.grid_y !== undefined ? best.grid_y : 'N/A';
            L.popup().setLatLng([best.lat, best.lon])
                .setContent('Population: ' + (best.pop || 0) + '<br/>Coordinates: (' + gridX + ', ' + gridY + ')')
                .openOn(mapInstance);
        }}
        mapInstance.on('click', showPopulationAtClick);
        
        var linesData = {lines_json};
            var playerColorsHex = {player_colors_hex_json};
        function formatLastYearStats(lastYear, riders, profit) {{
            if (lastYear === null || lastYear === undefined) {{
                return '<br/><i>Last year: no data yet</i>';
            }}
            var riderText = (riders || 0).toLocaleString();
            var profitText = (profit || 0).toLocaleString();
            return '<br/>Last year (' + lastYear + ') riders: ' + riderText + ' · Fare profit: $' + profitText;
        }}
        function renderMapLinesOntoMap(linesToRender, layerBucket) {{
        linesToRender.forEach(function(line) {{
                var isUnderConstruction = line.under_construction || false;
                var ownerId = line.owner_id !== undefined ? line.owner_id : -1;
                var lineColor;
                if (line.abandoned) {{
                    lineColor = '#888888';  // abandoned: nobody runs it
                }} else if (ownerId >= 0 && ownerId < playerColorsHex.length) {{
                    lineColor = playerColorsHex[ownerId];
                }} else if (isUnderConstruction) {{
                    lineColor = '#ff8800';
                }} else {{
                    lineColor = '#888888';
                }}
                var lineOpacity = isUnderConstruction ? 0.5 : 0.7;
                var lineWeight = isUnderConstruction ? 3 : 4;
                var lineOptions = {{
                color: lineColor,
                weight: lineWeight,
                opacity: lineOpacity
                }};
                if (isUnderConstruction) {{
                    lineOptions.dashArray = '10, 5';
                }} else if (line.damaged) {{
                    lineOptions.dashArray = '2, 6';  // disaster damage, awaiting rebuild
                }}
                var lineLayer = L.polyline(line.points, lineOptions);
                
                var encodedName = encodeURIComponent(line.name || '');
                var popupSym = (line.line_type === 'ferry') ? '⛴ ' : (line.line_type === 'bus') ? '🚌 ' : '🚂 ';
                var lastYearHtml = formatLastYearStats(line.last_year, line.last_year_ridership, line.last_year_fare_profit);
                var scheduleHtml = line.schedule_display ? ('<br/>Schedule: ' + line.schedule_display) : '';
                var popupContent = line.abandoned
                    ? '<div><b>' + popupSym + (line.name || '') + '</b><br/><i>Abandoned</i></div>'
                    : '<div><b>' + popupSym + (line.name || '') + '</b>' +
                    (line.line_damaged ? ' <span style="color:#d9534f;font-weight:bold;">Damaged</span>' : '') +
                    lastYearHtml + scheduleHtml + '<br/>' +
                    '<button class="extend-line-btn" data-line-name="' + encodedName + '">Extend Line</button><br/>' +
                    '<button class="add-station-btn" data-line-name="' + encodedName + '">Add Station</button></div>';
                lineLayer.bindPopup(popupContent);
                
                // Add click handler for station creation mode - intercept clicks before popup opens
                lineLayer.on('click', function(e) {{
                    // Store click location for Add Station (popup will use this)
                    window.lastLineClickLatLng = {{ lat: e.latlng.lat, lon: e.latlng.lng }};
                    window.lastLineClickName = line.name;
                    if (isSellingLineSegment && segmentSellLineName && line.name === segmentSellLineName) {{
                        addSegmentSellClick(e.latlng.lat, e.latlng.lng);
                        if (e.originalEvent) {{
                            e.originalEvent.preventDefault();
                            e.originalEvent.stopPropagation();
                        }}
                        return false;
                    }}
                    if (isCreatingStation && window.pendingStationLine && line.name === window.pendingStationLine) {{
                        // Store lat/lon for exact placement (Python converts to grid with 0.000001 precision)
                        var lat = e.latlng.lat;
                        var lon = e.latlng.lng;
                        var mapWidth = {map_width_val};
                        var mapHeight = {map_height_val};
                        var minLat = {min_lat};
                        var maxLat = {max_lat};
                        var minLon = {min_lon};
                        var maxLon = {max_lon};
                        var gridX = ((lon - minLon) / (maxLon - minLon)) * mapWidth;
                        var gridY = ((lat - minLat) / (maxLat - minLat)) * mapHeight;
                        window.stationClickData = {{ x: gridX, y: gridY, lat: lat, lon: lon, lineName: line.name }};
                        var event = new CustomEvent('stationLineClicked', {{
                            detail: {{ x: gridX, y: gridY, lat: lat, lon: lon, lineName: line.name }}
                        }});
                        window.dispatchEvent(event);
                        // Prevent popup from opening
                        if (e.originalEvent) {{
                            e.originalEvent.preventDefault();
                            e.originalEvent.stopPropagation();
                        }}
                        return false;
                    }}
                }});
                
                lineLayer.addTo(mapInstance);
                layerBucket.push(lineLayer);
                // Add line name at both ends (small labels) with type symbol: train, ferry, or bus
                var lineLabelStyle = 'font-size:9px;font-weight:bold;color:' + lineColor + ';text-shadow:0 0 2px #fff,0 0 2px #fff;white-space:nowrap;pointer-events:none;';
                var pts = line.points;
                if (pts && pts.length >= 2 && (line.name || '')) {{
                    var first = pts[0], last = pts[pts.length - 1];
                    var sym = (line.line_type === 'ferry') ? '⛴ ' : (line.line_type === 'bus') ? '🚌 ' : '🚂 ';
                    var labelHtml = '<span style="' + lineLabelStyle + '">' + sym + (line.name || '').replace(/</g, '&lt;') + '</span>';
                    var mk1 = L.marker([first[0], first[1]], {{ icon: L.divIcon({{ className: 'line-end-label', html: labelHtml, iconSize: null, iconAnchor: [0, 0] }}) }});
                    var mk2 = L.marker([last[0], last[1]], {{ icon: L.divIcon({{ className: 'line-end-label', html: labelHtml, iconSize: null, iconAnchor: [0, 0] }}) }});
                    mk1.addTo(mapInstance);
                    mk2.addTo(mapInstance);
                    layerBucket.push(mk1, mk2);
                }}
            }});
        }}
        // Overlay updates change only what changed: items are keyed by their data, unchanged
        // ones keep their layers (and open popups), new ones are drawn, gone ones removed.
        window._diffMapLayers = function(store, items, render) {{
            var next = {{}};
            (items || []).forEach(function(item) {{
                var key = JSON.stringify(item);
                if (store[key] && !next[key]) {{
                    next[key] = store[key];
                    delete store[key];
                }} else {{
                    var layers = [];
                    render([item], layers);
                    next[key] = (next[key] || []).concat(layers);
                }}
            }});
            Object.keys(store).forEach(function(key) {{
                store[key].forEach(function(layer) {{
                    try {{ mapInstance.removeLayer(layer); }} catch (e) {{}}
                }});
            }});
            return next;
        }};
        window._mapLineStore = window._diffMapLayers({{}}, linesData, renderMapLinesOntoMap);
        window.refreshMapLines = function(newLinesData) {{
            if (!mapInstance) return;
            window._mapLineStore = window._diffMapLayers(
                window._mapLineStore || {{}}, newLinesData, renderMapLinesOntoMap);
        }};
        
        // Render roads (only roads that exist at current year)
        var roadsData = {roads_json};
        var roadLayers = [];
        roadsData.forEach(function(road) {{
            var roadOptions = {{
                color: '#888888',  // Gray color for roads
                weight: 2,
                opacity: 0.6
            }};
            var roadLayer = L.polyline(road.points, roadOptions);
            roadLayer.bindPopup('<div><b>' + (road.name || 'Road') + '</b></div>');
            roadLayer.addTo(mapInstance);
            roadLayers.push(roadLayer);
        }});
            
            function renderMapStationsOntoMap(stationsData, stationLayers) {{
                (stationsData || []).forEach(function(station) {{
                    var color = (station.under_construction === true) ? '#888888' : '#00ff00';
                    var label = (station.under_construction === true) ? ' (under construction)' : '';
                    var lineNames = station.line_names || [];
                    var lineLabel = lineNames.length ? ' (' + lineNames.join(', ') + ')' : '';
                    var labelStyle = 'font-size:9px;font-weight:bold;color:#333;text-shadow:0 0 2px #fff,0 0 2px #fff;white-space:nowrap;pointer-events:none;';
                    var nameAndLine = (station.name || '');
                    var isMerged = station.is_merged === true || (station.num_lines || 0) >= 2;
                    var dotSize = isMerged ? 16 : 12;
                    var borderStyle = isMerged ? '3px solid #0066cc' : '2px solid white';
                    var mergeRing = isMerged ? '<div style="position:absolute;left:-3px;top:-3px;width:' + (dotSize + 6) + 'px;height:' + (dotSize + 6) + 'px;border-radius:50%;border:2px dashed #0066cc;opacity:0.85;"></div>' : '';
                    var mergeBadge = isMerged ? '<span style="font-size:8px;color:#0066cc;margin-left:2px;">[' + (station.num_lines || lineNames.length) + ' lines]</span>' : '';
                    var markerHtml = '<div style="display:inline-block;vertical-align:middle;position:relative;">' + mergeRing + '<div style="background-color: ' + color + '; width: ' + dotSize + 'px; height: ' + dotSize + 'px; border-radius: 50%; border: ' + borderStyle + '; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div><span style="' + labelStyle + '">' + (nameAndLine.replace(/</g, '&lt;')) + mergeBadge + '</span></div>';
                    var stationMarker = L.marker([station.lat, station.lon], {{
                        icon: L.divIcon({{
                            className: 'station-marker' + (isMerged ? ' station-marker-merged' : ''),
                            html: markerHtml,
                            iconSize: null,
                            iconAnchor: [0, Math.floor(dotSize / 2)]
                        }})
                    }});
                    var encodedName = encodeURIComponent(station.name || '');
                    var closeBtnHtml = (station.can_close !== false) ? '<button class="close-station-btn" data-station-name="' + encodedName + '">Close Station</button>' : '';
                    var renameBtnHtml = (station.can_rename !== false && station.under_construction !== true) ? '<button class="rename-station-btn" data-station-name="' + encodedName + '" data-station-lat="' + station.lat + '" data-station-lon="' + station.lon + '">Rename Station</button>' : '';
                    var mergeNote = isMerged ? '<br/><i>Merged hub — ' + (station.num_lines || lineNames.length) + ' lines share this stop</i>' : '';
                    var lastYearHtml = formatLastYearStats(station.last_year, station.last_year_ridership, station.last_year_fare_profit);
                    var expandBtnsHtml = '';
                    (station.expand_lines || []).forEach(function(expandLine) {{
                        var safeLine = String(expandLine).replace(/</g, '&lt;');
                        expandBtnsHtml += '<br/><button class="expand-station-btn" data-station-name="' + encodedName +
                            '" data-line-name="' + encodeURIComponent(expandLine) +
                            '" data-station-lat="' + station.lat + '" data-station-lon="' + station.lon +
                            '" title="This line passes close by. Cost: $' + (station.expand_cost || 0).toLocaleString() +
                            '">Serve ' + safeLine + ' here</button>';
                    }});
                    var stationPopupContent = '<div><b>Station: ' + (station.name || '') + label + '</b>' + (lineLabel ? '<br/>Line(s): ' + (station.line_names || []).join(', ') : '') + mergeNote + lastYearHtml + '<br/>' + renameBtnHtml + closeBtnHtml + expandBtnsHtml + '</div>';
                    stationMarker.bindPopup(stationPopupContent);
                    stationMarker.on('click', function(e) {{
                        // Markers do not pass clicks on to the map: while drawing a route, a click
                        // on a station places a route point there instead of opening its popup.
                        if (window.isMapRouteDrawing && window.isMapRouteDrawing()) {{
                            stationMarker.closePopup();
                            mapInstance.fire('click', {{ latlng: stationMarker.getLatLng() }});
                        }}
                    }});
                    stationMarker.addTo(mapInstance);
                    stationLayers.push(stationMarker);
                }});
            }}
            var stationsData = {stations_json};
            window._mapStationStore = window._diffMapLayers({{}}, stationsData, renderMapStationsOntoMap);
            window.refreshMapStations = function(newStationsData) {{
                if (!mapInstance) return;
                window._mapStationStore = window._diffMapLayers(
                    window._mapStationStore || {{}}, newStationsData, renderMapStationsOntoMap);
            }};
            
            window.extendLine = function(lineName) {{
                var event = new CustomEvent('mapLineExtend', {{
                    detail: {{ lineName: lineName }}
                }});
                window.dispatchEvent(event);
            }};
            
            window.addStationToLine = function(lineName) {{
                var lat = null, lon = null;
                if (window.lastLineClickLatLng) {{
                    lat = window.lastLineClickLatLng.lat;
                    lon = window.lastLineClickLatLng.lon;
                }}
                var event = new CustomEvent('mapAddStation', {{
                    detail: {{ lineName: lineName, lat: lat, lon: lon }}
                }});
                window.dispatchEvent(event);
            }};
            
            mapInstance.on('popupopen', function(e) {{
                try {{
                    var popupEl = e.popup && e.popup.getElement ? e.popup.getElement() : null;
                    if (!popupEl) return;
                    var extendBtn = popupEl.querySelector('.extend-line-btn');
                    if (extendBtn) {{
                        extendBtn.addEventListener('click', function() {{
                            var name = extendBtn.getAttribute('data-line-name') || '';
                            window.extendLine(decodeURIComponent(name));
                        }});
                    }}
                    var addStationBtn = popupEl.querySelector('.add-station-btn');
                    if (addStationBtn) {{
                        addStationBtn.addEventListener('click', function() {{
                            var name = addStationBtn.getAttribute('data-line-name') || '';
                            window.addStationToLine(decodeURIComponent(name));
                        }});
                    }}
                    var closeStationBtn = popupEl.querySelector('.close-station-btn');
                    if (closeStationBtn) {{
                        closeStationBtn.addEventListener('click', function() {{
                            var name = closeStationBtn.getAttribute('data-station-name') || '';
                            window.closeStation(decodeURIComponent(name));
                        }});
                    }}
                    popupEl.querySelectorAll('.expand-station-btn').forEach(function(btn) {{
                        btn.addEventListener('click', function() {{
                            window.dispatchEvent(new CustomEvent('mapExpandStation', {{
                                detail: {{
                                    stationName: decodeURIComponent(btn.getAttribute('data-station-name') || ''),
                                    lineName: decodeURIComponent(btn.getAttribute('data-line-name') || ''),
                                    lat: parseFloat(btn.getAttribute('data-station-lat') || ''),
                                    lon: parseFloat(btn.getAttribute('data-station-lon') || '')
                                }}
                            }}));
                        }});
                    }});
                    var renameStationBtn = popupEl.querySelector('.rename-station-btn');
                    if (renameStationBtn) {{
                        renameStationBtn.addEventListener('click', function() {{
                            var name = renameStationBtn.getAttribute('data-station-name') || '';
                            var lat = renameStationBtn.getAttribute('data-station-lat') || '';
                            var lon = renameStationBtn.getAttribute('data-station-lon') || '';
                            window.renameStation(decodeURIComponent(name), parseFloat(lat), parseFloat(lon));
                        }});
                    }}
                }} catch (err) {{
                }}
            }});
            
            window.closeStation = function(stationName) {{
                var event = new CustomEvent('mapCloseStation', {{
                    detail: {{ stationName: stationName }}
                }});
                window.dispatchEvent(event);
            }};

            window.renameStation = function(stationName, lat, lon) {{
                var event = new CustomEvent('mapRenameStation', {{
                    detail: {{ stationName: stationName, lat: lat, lon: lon }}
                }});
                window.dispatchEvent(event);
            }};
            
            window.addEventListener('mapLineExtend', function(e) {{
                window.pendingExtendLine = e.detail.lineName;
            }});
            window.addEventListener('mapAddStation', function(e) {{
                window.pendingAddStation = e.detail.lineName;
                window.pendingAddStationLat = e.detail.lat;
                window.pendingAddStationLon = e.detail.lon;
            }});
            
            // Map click handler for creating lines/stations
            var clickedPoints = [];
            var isCreatingLine = false;
            var isCreatingStation = false;
            var ROUTE_POINT_HIT_THRESHOLD = 0.0001;

            function findNearestRoutePointIndex(lat, lon, points) {{
                var minDist = Infinity;
                var nearestIndex = -1;
                for (var i = 0; i < points.length; i++) {{
                    var point = points[i];
                    var dist = Math.pow(point[0] - lat, 2) + Math.pow(point[1] - lon, 2);
                    if (dist < minDist) {{
                        minDist = dist;
                        nearestIndex = i;
                    }}
                }}
                if (nearestIndex >= 0 && minDist < ROUTE_POINT_HIT_THRESHOLD) {{
                    return nearestIndex;
                }}
                return -1;
            }}

            function removeLinePointAtIndex(index) {{
                clickedPoints.splice(index, 1);
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer._isLinePoint && layer._linePointIndex === index) {{
                        mapInstance.removeLayer(layer);
                    }} else if (layer instanceof L.Marker && layer._isLinePoint && layer._linePointIndex > index) {{
                        layer._linePointIndex--;
                    }}
                }});
                updateCreatePreviewLine();
                var event = new CustomEvent('mapPointClicked', {{
                    detail: {{ count: clickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}

            window.createPreviewLine = null;

            function updateCreatePreviewLine() {{
                if (window.createPreviewLine) {{
                    mapInstance.removeLayer(window.createPreviewLine);
                    window.createPreviewLine = null;
                }}
                if (!isCreatingLine || clickedPoints.length < 2) {{
                    return;
                }}
                var previewPoints = clickedPoints.map(function(point) {{
                    return [point[0], point[1]];
                }});
                window.createPreviewLine = L.polyline(previewPoints, {{
                    color: '#0066ff',
                    weight: 4,
                    opacity: 0.7,
                    dashArray: '10, 5'
                }}).addTo(mapInstance);
            }}

            function addLinePoint(lat, lng) {{
                clickedPoints.push([lat, lng]);
                var marker = L.marker([lat, lng], {{
                    icon: L.divIcon({{
                        className: 'line-point-marker',
                        html: '<div style="background-color: #0066ff; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                        iconSize: [10, 10]
                    }})
                }}).addTo(mapInstance);
                marker._linePointIndex = clickedPoints.length - 1;
                marker._isLinePoint = true;
                marker.on('click', function(ev) {{
                    if (!isCreatingLine) return;
                    L.DomEvent.stopPropagation(ev);
                    removeLinePointAtIndex(marker._linePointIndex);
                }});
                updateCreatePreviewLine();
                var event = new CustomEvent('mapPointClicked', {{
                    detail: {{ count: clickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}

            function removeExtendPointAtIndex(index) {{
                extendClickedPoints.splice(index, 1);
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer._isExtendPoint && layer._extendPointIndex === index) {{
                        mapInstance.removeLayer(layer);
                    }} else if (layer instanceof L.Marker && layer._isExtendPoint && layer._extendPointIndex > index) {{
                        layer._extendPointIndex--;
                    }}
                }});
                updateExtendPreviewLine();
                var event = new CustomEvent('mapExtendPointClicked', {{
                    detail: {{ count: extendClickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}

            function addExtendPoint(lat, lng) {{
                extendClickedPoints.push([lat, lng]);
                var marker = L.marker([lat, lng], {{
                    icon: L.divIcon({{
                        className: 'extend-point-marker',
                        html: '<div style="background-color: #ff6600; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                        iconSize: [10, 10]
                    }})
                }}).addTo(mapInstance);
                marker._extendPointIndex = extendClickedPoints.length - 1;
                marker._isExtendPoint = true;
                marker.on('click', function(ev) {{
                    if (!isExtendingLine) return;
                    L.DomEvent.stopPropagation(ev);
                    removeExtendPointAtIndex(marker._extendPointIndex);
                }});
                updateExtendPreviewLine();
                var event = new CustomEvent('mapExtendPointClicked', {{
                    detail: {{ count: extendClickedPoints.length }}
                }});
                window.dispatchEvent(event);
            }}
            
            // While points are being placed for a new line or an extension, clicking a line or a
            // station adds a route point; their popups (and buttons) stay closed.
            window.isMapRouteDrawing = function() {{
                return !!(isCreatingLine || isExtendingLine);
            }};
            mapInstance.on('popupopen', function(e) {{
                if (window.isMapRouteDrawing() && e.popup) {{
                    mapInstance.closePopup(e.popup);
                }}
            }});

            mapInstance.on('click', function(e) {{
                if (isCreatingLine) {{
                    addLinePoint(e.latlng.lat, e.latlng.lng);
                }} else if (isExtendingLine) {{
                    addExtendPoint(e.latlng.lat, e.latlng.lng);
                }} else if (isCreatingStation) {{
                    var event = new CustomEvent('mapStationClicked', {{
                        detail: {{ lat: e.latlng.lat, lon: e.latlng.lng }}
                    }});
                    window.dispatchEvent(event);
                }}
            }});
            
            mapInstance.on('mousedown', function(e) {{
                if (e.originalEvent && e.originalEvent.button === 1) {{
                    if (isCreatingLine) {{
                        var nearestIndex = findNearestRoutePointIndex(e.latlng.lat, e.latlng.lng, clickedPoints);
                        if (nearestIndex >= 0) {{
                            removeLinePointAtIndex(nearestIndex);
                        }}
                    }} else if (isExtendingLine) {{
                        var nearestIndex = findNearestRoutePointIndex(e.latlng.lat, e.latlng.lng, extendClickedPoints);
                        if (nearestIndex >= 0) {{
                            removeExtendPointAtIndex(nearestIndex);
                        }}
                    }}
                }}
            }});
            
            // Expose functions to Python via window object
            window.startLineCreation = function() {{
                isCreatingLine = true;
                clickedPoints = [];
                updateCreatePreviewLine();
            }};
            
            // Do not clear isCreatingLine here — Python cancels only after success or Cancel.
            // Returning a copy keeps points if the user backs out of line-type / elevation dialogs.
            window.finishLineCreation = function() {{
                return clickedPoints.slice();
            }};
            
            window.cancelLineCreation = function() {{
                isCreatingLine = false;
                clickedPoints = [];
                updateCreatePreviewLine();
                // Remove markers
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer.options.icon && 
                        layer.options.icon.options && 
                        layer.options.icon.options.className === 'line-point-marker') {{
                        mapInstance.removeLayer(layer);
                    }}
                }});
            }};
            
            window.isExtendingLine = false;
            window.extendingLineName = null;
            window.extendClickedPoints = [];
            window.extendPreviewLine = null;
            window.extensionAttachStart = null;
            window.extensionAttachEnd = null;
            window.extensionAttachFromEnd = null;

            function resolveExtensionAttachEndpoints(lineName) {{
                if (window.extensionAttachStart && window.extensionAttachEnd) {{
                    return {{
                        start: window.extensionAttachStart,
                        end: window.extensionAttachEnd,
                    }};
                }}
                var attachStart = null;
                var attachEnd = null;
                var builtLine = null;
                linesData.forEach(function(line) {{
                    if (line.name !== lineName) {{
                        return;
                    }}
                    if (line.attach_start && line.attach_end) {{
                        attachStart = line.attach_start;
                        attachEnd = line.attach_end;
                    }}
                    if (!line.under_construction) {{
                        builtLine = line;
                    }}
                }});
                if (attachStart && attachEnd) {{
                    return {{ start: attachStart, end: attachEnd }};
                }}
                if (builtLine && builtLine.points && builtLine.points.length > 0) {{
                    var pts = builtLine.points;
                    return {{ start: pts[0], end: pts[pts.length - 1] }};
                }}
                return null;
            }}
            
            function updateExtendPreviewLine() {{
                // Remove existing preview line
                if (window.extendPreviewLine) {{
                    mapInstance.removeLayer(window.extendPreviewLine);
                    window.extendPreviewLine = null;
                }}
                
                // Find the line being extended
                if (!extendingLineName || extendClickedPoints.length === 0) {{
                    return;
                }}
                
                var attach = resolveExtensionAttachEndpoints(extendingLineName);
                if (!attach) {{
                    return;
                }}
                
                var startExisting = attach.start;
                var endExisting = attach.end;
                var attachFromEnd = window.extensionAttachFromEnd;
                if (attachFromEnd === null && extendClickedPoints.length > 0) {{
                    var click = extendClickedPoints[0];
                    var dStart = Math.hypot(click[0] - startExisting[0], click[1] - startExisting[1]);
                    var dEnd = Math.hypot(click[0] - endExisting[0], click[1] - endExisting[1]);
                    attachFromEnd = dEnd <= dStart;
                    window.extensionAttachFromEnd = attachFromEnd;
                }}
                if (attachFromEnd === null) {{
                    attachFromEnd = true;
                }}
                
                // Build preview points: attach end of combined line + all extend points in click order
                var previewPoints = [];
                if (attachFromEnd) {{
                    previewPoints.push([endExisting[0], endExisting[1]]);
                }} else {{
                    previewPoints.push([startExisting[0], startExisting[1]]);
                }}
                extendClickedPoints.forEach(function(point) {{
                    previewPoints.push([point[0], point[1]]);
                }});
                
                // Draw dotted preview line (open polyline only — never connect last point back to start)
                if (previewPoints.length > 1) {{
                    window.extendPreviewLine = L.polyline(previewPoints, {{
                        color: '#ff6600',
                        weight: 4,
                        opacity: 0.7,
                        dashArray: '10, 5'
                    }}).addTo(mapInstance);
                }}
            }};
            
            window.startLineExtension = function(lineName, attachStart, attachEnd) {{
                isExtendingLine = true;
                extendingLineName = lineName;
                extendClickedPoints = [];
                window.extensionAttachFromEnd = null;
                window.extensionAttachStart = attachStart || null;
                window.extensionAttachEnd = attachEnd || null;
                // Initialize preview line
                updateExtendPreviewLine();
            }};
            
            // Keep extension mode until Python confirms success or Cancel.
            window.finishLineExtension = function() {{
                return {{
                    points: extendClickedPoints.slice(),
                    extendFromEnd: window.extensionAttachFromEnd !== null
                        ? window.extensionAttachFromEnd
                        : true,
                }};
            }};
            
            window.cancelLineExtension = function() {{
                isExtendingLine = false;
                extendClickedPoints = [];
                extendingLineName = null;
                window.extensionAttachStart = null;
                window.extensionAttachEnd = null;
                window.extensionAttachFromEnd = null;
                // Remove preview line
                if (window.extendPreviewLine) {{
                    mapInstance.removeLayer(window.extendPreviewLine);
                    window.extendPreviewLine = null;
                }}
                // Remove markers
                mapInstance.eachLayer(function(layer) {{
                    if (layer instanceof L.Marker && layer.options.icon && 
                        layer.options.icon.options && 
                        layer.options.icon.options.className === 'extend-point-marker') {{
                        mapInstance.removeLayer(layer);
                    }}
                }});
            }};

            window.isSellingLineSegment = false;
            window.segmentSellLineName = null;
            window.segmentSellClicks = [];
            window.segmentSellMarkers = [];

            function clearSegmentSellMarkers() {{
                (window.segmentSellMarkers || []).forEach(function(m) {{
                    try {{ mapInstance.removeLayer(m); }} catch (e) {{}}
                }});
                window.segmentSellMarkers = [];
            }}

            function addSegmentSellClick(lat, lng) {{
                if (!isSellingLineSegment || !segmentSellLineName) return;
                if (segmentSellClicks.length >= 2) return;
                segmentSellClicks.push([lat, lng]);
                var marker = L.marker([lat, lng], {{
                    icon: L.divIcon({{
                        className: 'segment-sell-marker',
                        html: '<div style="background-color: #cc0000; width: 10px; height: 10px; border-radius: 50%; border: 2px solid white;"></div>',
                        iconSize: [10, 10]
                    }})
                }}).addTo(mapInstance);
                window.segmentSellMarkers.push(marker);
                var event = new CustomEvent('mapSegmentSellClicked', {{
                    detail: {{ count: segmentSellClicks.length }}
                }});
                window.dispatchEvent(event);
                if (segmentSellClicks.length >= 2) {{
                    window.pendingSegmentSell = JSON.stringify({{
                        lineName: segmentSellLineName,
                        points: segmentSellClicks.slice()
                    }});
                }}
            }}

            window.startLineSegmentSell = function(lineName) {{
                isSellingLineSegment = true;
                segmentSellLineName = lineName;
                segmentSellClicks = [];
                clearSegmentSellMarkers();
                window.pendingSegmentSell = null;
            }};

            window.cancelLineSegmentSell = function() {{
                isSellingLineSegment = false;
                segmentSellLineName = null;
                segmentSellClicks = [];
                clearSegmentSellMarkers();
                window.pendingSegmentSell = null;
            }};
            
            window.pendingStationLine = null;
            window.pendingStationName = null;
            
            window.startStationCreation = function() {{
                isCreatingStation = true;
            }};
            
            window.startStationCreationWithLine = function(lineName) {{
                isCreatingStation = true;
                window.pendingStationLine = lineName;
                // Also set pendingAddStation for compatibility with existing handlers
                window.pendingAddStation = lineName;
            }};
            
            window.cancelStationCreation = function() {{
                isCreatingStation = false;
                window.pendingStationLine = null;
                window.pendingStationName = null;
            }};
            
            // Listen for line clicks during station creation
            window.addEventListener('mapLineClickedForStation', function(e) {{
                var lat = e.detail.lat;
                var lon = e.detail.lon;
                var lineName = e.detail.lineName;
                // Convert lat/lon to grid coordinates
                var mapWidth = {map_width_val};
                var mapHeight = {map_height_val};
                var minLat = {min_lat};
                var maxLat = {max_lat};
                var minLon = {min_lon};
                var maxLon = {max_lon};
                var gridX = Math.round(((lon - minLon) / (maxLon - minLon)) * mapWidth);
                var gridY = Math.round(((lat - minLat) / (maxLat - minLat)) * mapHeight);
                // Call Python function
                if (window.pywebview && window.pywebview.api) {{
                    window.pywebview.api.on_line_clicked_for_station(gridX, gridY, lineName);
                }} else {{
                    // Fallback: use custom event that Python can listen to
                    var event = new CustomEvent('pythonLineClickForStation', {{
                        detail: {{ x: gridX, y: gridY, lineName: lineName }}
                    }});
                    window.dispatchEvent(event);
                }}
            }});
            
            // Store map instance globally for Python access
            window.mapInstance = mapInstance;
            
                // Invalidate size after a short delay to ensure container is visible
                setTimeout(function() {{
                    if (mapInstance) {{
                        mapInstance.invalidateSize();
                    }}
                }}, 100);
            }} catch (error) {{
                console.error('Error initializing map:', error);
                document.getElementById('map').innerHTML = '<div style="padding: 20px; color: red;">Error initializing map: ' + error.message + '</div>';
            }}
            // Invalidate size when window becomes visible (for tab switching)
            document.addEventListener('visibilitychange', function() {{
                var m = window.mapInstance;
                if (m && !document.hidden) {{
                    if (typeof window.ensureMapVisible === 'function') {{
                        window.ensureMapVisible();
                    }} else {{
                        ensureMapContainerSize();
                        setTimeout(function() {{ m.invalidateSize(true); }}, 100);
                    }}
                }}
            }});
            window.addEventListener('resize', function() {{
                if (typeof window.ensureMapVisible === 'function') {{
                    window.ensureMapVisible();
                }} else {{
                    ensureMapContainerSize();
                    var m = window.mapInstance;
                    if (m) {{
                        setTimeout(function() {{ m.invalidateSize(true); }}, 50);
                    }}
                }}
            }});
        }}
        document.addEventListener('DOMContentLoaded', initMap);
        initMap();
    </script>
</body>
</html>"""
