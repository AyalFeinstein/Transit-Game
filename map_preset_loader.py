"""Dynamic Map Preset Loader: discover and load map presets by name.
Presets expose a generic interface (get_bounds, get_timeline, get_display_name, etc.)
so the game never needs to know the map name."""

import importlib
import importlib.util
import math
import sys
from typing import Optional, Dict, Any
from pathlib import Path

class MapPresetRegistry:
    """Registry for discovering and loading map presets dynamically."""
    
    def __init__(self):
        self._presets: Dict[str, Any] = {}
        self._discover_presets()
    
    def _discover_presets(self):
        """Discover preset files without importing heavy modules (lazy load on first use)."""
        current_dir = Path(__file__).parent
        for preset_file in current_dir.glob("map_presets_*.py"):
            if "template" in preset_file.stem.lower() or preset_file.stem.endswith("_backup"):
                continue
            preset_name = preset_file.stem.replace("map_presets_", "")
            self._presets[preset_name] = {
                'module': None,
                'file': preset_file,
                'name': preset_name,
                'module_name': preset_file.stem,
            }

    def _ensure_preset_loaded(self, preset_name: str) -> Optional[Any]:
        """Import a preset module on first access."""
        info = self._presets.get(preset_name)
        if not info:
            return None
        if info.get('module') is not None:
            return info['module']
        module_name = info['module_name']
        preset_file = info['file']
        try:
            if module_name in sys.modules:
                module = importlib.reload(sys.modules[module_name])
            else:
                spec = importlib.util.spec_from_file_location(module_name, preset_file)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            if self._is_valid_preset(module):
                info['module'] = module
                print(f"Discovered map preset: {preset_name}")
                return module
        except Exception as e:
            print(f"Warning: Failed to load map preset {preset_name}: {e}")
        return None
    
    def _is_valid_preset(self, module: Any) -> bool:
        """Check if a module is a valid map preset.
        
        A valid preset must have either:
        1. Functions: is_*_map, get_*_bounds, get_*_timeline (for location-based maps)
        2. Variables: MAP_BOUNDS, MAP_TIMELINE (for template-based maps)
        """
        module_name = module.__name__
        preset_name = module_name.replace("map_presets_", "")
        
        # Check for location-based preset
        has_is_map = hasattr(module, f"is_{preset_name}_map") or hasattr(module, "is_map")
        has_bounds = hasattr(module, f"get_{preset_name}_bounds") or hasattr(module, "get_bounds")
        has_timeline = hasattr(module, f"get_{preset_name}_timeline") or hasattr(module, "get_timeline")
        
        # Check for template-based preset
        has_map_bounds = hasattr(module, "MAP_BOUNDS")
        has_map_timeline = hasattr(module, "MAP_TIMELINE")
        
        return (has_is_map and has_bounds and has_timeline) or (has_map_bounds and has_map_timeline)
    
    def get_preset(self, preset_name: str) -> Optional[Any]:
        """Get a preset module by name.
        
        Args:
            preset_name: Name of the preset (e.g., "nyc", "ny_metro", "custom")
            
        Returns:
            The preset module, or None if not found
        """
        return self._ensure_preset_loaded(preset_name)
    
    def list_presets(self) -> list[str]:
        """List all available preset names."""
        return list(self._presets.keys())
    
    def get_preset_info(self, preset_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a preset."""
        return self._presets.get(preset_name)


# Global registry instance
_registry: Optional[MapPresetRegistry] = None


def get_registry() -> MapPresetRegistry:
    """Get the global map preset registry."""
    global _registry
    if _registry is None:
        _registry = MapPresetRegistry()
    return _registry


def load_map_preset(preset_name: str) -> Optional[Any]:
    """Load a map preset by name.
    
    Args:
        preset_name: Name of the preset (e.g., "nyc", "ny_metro", "custom")
        
    Returns:
        The preset module, or None if not found
    """
    return get_registry().get_preset(preset_name)


def reload_map_preset_from_disk(preset_name: str) -> Optional[Any]:
    """Force reload a map preset from disk. Use after edit-mode changes to INITIAL_POPULATION.
    Ensures the registry and all imports see the updated file contents."""
    registry = get_registry()
    preset_info = registry._presets.get(preset_name)
    if not preset_info:
        return None
    module_name = preset_info.get('module_name') or f"map_presets_{preset_name}"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
        preset_info['module'] = module
        return module
    return registry._ensure_preset_loaded(preset_name)


def list_available_presets() -> list[str]:
    """List all available map preset names."""
    return get_registry().list_presets()


def preset_has_water_areas(preset_name: str) -> bool:
    """Check if a preset provides custom water detection (is_water_location)."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    return preset is not None and hasattr(preset, 'is_water_location')


def preset_ai_relax_polyline_self_overlap_for_osm_candidate(
    preset_name: str,
    current_year: int,
    min_pop_road: int,
    existing_line_routes,
) -> bool:
    """Delegate to preset module if it defines ai_relax_polyline_self_overlap_for_osm_candidate (e.g. NYC OSM)."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    fn = getattr(preset, "ai_relax_polyline_self_overlap_for_osm_candidate", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(current_year, min_pop_road, existing_line_routes))
    except Exception:
        return False


def get_preset_excluded_water_points(preset_name: str) -> set:
    """Get grid points that are always water (e.g. excluded from buildable area). Returns empty set if not available."""
    if not preset_name:
        return set()
    preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'get_excluded_water_points'):
        return set()
    try:
        pts = preset.get_excluded_water_points()
        return set(pts) if pts else set()
    except Exception:
        return set()


def preset_is_point_in_water(preset_name: str, grid_x: float, grid_y: float, game_map, skip_osm: bool = False) -> bool:
    """Check if grid point is in water using preset's water detection. Returns False if preset has no water support.
    skip_osm: If True, do not use OSM/Overpass (avoids HTTP calls). Use preset-only water data (e.g. NYC_WATER_AREAS)."""
    import math
    preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'is_water_location'):
        return False
    excluded = get_preset_excluded_water_points(preset_name)
    gx, gy = int(round(grid_x)), int(round(grid_y))
    if excluded and (gx, gy) in excluded:
        return True
    bounds = get_map_bounds(preset_name)
    if not bounds or 'min_lat' not in bounds:
        return False
    w = max(1, getattr(game_map, 'width', 50) or 50)
    h = max(1, getattr(game_map, 'height', 50) or 50)
    min_lat, max_lat = bounds['min_lat'], bounds['max_lat']
    min_lon, max_lon = bounds['min_lon'], bounds['max_lon']
    lat = min_lat + (gy / h) * (max_lat - min_lat)
    lon = min_lon + (gx / w) * (max_lon - min_lon)
    osm = None if skip_osm else getattr(game_map, 'osm_integration', None)
    try:
        argcount = preset.is_water_location.__code__.co_argcount
        if argcount >= 3:
            return preset.is_water_location(lat, lon, osm)
        return preset.is_water_location(lat, lon)
    except Exception:
        return False


def preset_has_road_support(preset_name: str) -> bool:
    """True if preset provides road/bridge checks (is_point_on_road_at_year or get_road_segment_points_for_ai)."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    return preset is not None and (
        hasattr(preset, 'is_point_on_road_at_year') or hasattr(preset, 'get_road_segment_points_for_ai')
    )


def preload_road_data_for_preset(preset_name: str, game_map) -> None:
    """Preload road data (e.g. spatial index) for presets with road support.
    Call at game start to avoid 2-5s load on first AI turn. No-op if preset has no preload."""
    if not preset_name or not game_map:
        return
    preset = load_map_preset(preset_name)
    if preset is None or not hasattr(preset, 'preload_road_data'):
        return
    try:
        preset.preload_road_data(game_map)
    except Exception:
        pass


def warm_game_start_year_end_caches(preset_name: str, game_map) -> None:
    """Run expensive one-time loads during game init instead of on first year advance / first AI turn.

    Covers: road spatial preload, borough/point-area map for population events, AI road feature
    caches (common max_points), and top-N population sort caches used heavily on turn 1."""
    if not preset_name or not game_map:
        return
    if getattr(game_map, "_transit_game_start_caches_warmed", False):
        return
    preload_road_data_for_preset(preset_name, game_map)
    try:
        game_map._preset_point_area_map_cache = get_preset_point_area_map(preset_name)
    except Exception:
        game_map._preset_point_area_map_cache = {}
    if preset_has_road_support(preset_name):
        try:
            from map_presets_nyc_road_utils import _get_ai_road_features

            _get_ai_road_features(game_map, max_points=12)
        except Exception:
            pass
    try:
        from ai import _get_population_points_sorted_by_population

        for _mp in (8, 20):
            _get_population_points_sorted_by_population(game_map, max_points=_mp)
    except Exception:
        pass
    setattr(game_map, "_transit_game_start_caches_warmed", True)


def get_preset_is_point_on_road(preset_name: str, x: float, y: float, year: int, game_map) -> bool:
    """Check if point is on a road at given year. Returns False if preset has no road support."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'is_point_on_road_at_year'):
        return False
    try:
        return preset.is_point_on_road_at_year(x, y, year, game_map)
    except Exception:
        return False


def get_preset_is_point_on_bridge(preset_name: str, x: float, y: float, year: int, game_map) -> bool:
    """Check if point is on a bridge at given year. Returns False if preset has no road support."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'is_point_on_bridge_at_year'):
        return False
    try:
        return preset.is_point_on_bridge_at_year(x, y, year, game_map)
    except Exception:
        return False


def get_preset_land_segment_fraction_on_street(
    preset_name: str,
    game_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
) -> float:
    """Share of a land segment lying on the preset street network (0..1)."""
    if not preset_name or not preset_has_road_support(preset_name):
        return 0.0
    from map_presets_nyc_road_utils import land_segment_fraction_on_street_network
    try:
        return float(
            land_segment_fraction_on_street_network(
                game_map, float(x1), float(y1), float(x2), float(y2), int(current_year),
            )
        )
    except Exception:
        return 0.0


def get_preset_bus_segment_follows_street(
    preset_name: str,
    game_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
) -> bool:
    """True when a bus segment lies on the preset street network."""
    from game_presets import BUS_STREET_MIN_SEGMENT_COVERAGE

    frac = get_preset_land_segment_fraction_on_street(
        preset_name, game_map, x1, y1, x2, y2, current_year,
    )
    return frac >= float(BUS_STREET_MIN_SEGMENT_COVERAGE)


def get_preset_point_near_street_centerline(
    preset_name: str,
    game_map,
    x: float,
    y: float,
    current_year: int,
) -> bool:
    """True when a point lies near a preset street/bridge centerline (train midpoint check)."""
    if not preset_name or not preset_has_road_support(preset_name):
        return False
    from map_presets_nyc_road_utils import point_near_street_network
    try:
        return bool(point_near_street_network(game_map, float(x), float(y), int(current_year)))
    except Exception:
        return False


def get_preset_polyline_follows_streets(
    preset_name: str, game_map, points: list, current_year: int,
) -> bool:
    """Fast check that an AI street polyline can be queued on a road-capable preset."""
    if not preset_name or not points or len(points) < 2:
        return False
    preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'polyline_follows_streets_for_ai'):
        return True
    try:
        return bool(preset.polyline_follows_streets_for_ai(game_map, points, current_year))
    except Exception:
        return False


def get_preset_street_route_between_points(
    preset_name: str,
    game_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    current_year: int,
    max_distance_grid: float = 15.0,
) -> list:
    """Grid polyline from A to B along streets, or a straight chord when roads are unavailable."""
    if not preset_name or not preset_has_road_support(preset_name):
        return [(float(x1), float(y1)), (float(x2), float(y2))]
    from map_presets_nyc_road_utils import find_street_route_between_points
    try:
        return find_street_route_between_points(
            game_map, float(x1), float(y1), float(x2), float(y2), int(current_year),
            max_distance_grid=max_distance_grid,
        )
    except Exception:
        return [(float(x1), float(y1)), (float(x2), float(y2))]


def get_preset_ai_station_population(game_map, px: float, py: float, pop_map: dict) -> int:
    """Population count for AI station placement. OSM presets use geodesic-capped lookup (see preset modules)."""
    if game_map is None:
        return 0
    if not isinstance(pop_map, dict) or not pop_map:
        return 0
    preset_name = getattr(game_map, 'map_preset_name', None) or ''
    preset = load_map_preset(preset_name) if preset_name else None
    fn = getattr(preset, 'ai_station_population_at_grid', None) if preset else None
    if callable(fn):
        try:
            return max(0, int(fn(float(px), float(py), game_map, pop_map) or 0))
        except Exception:
            pass
    from map_presets_nyc_road_utils import _population_at_or_near
    return _population_at_or_near(float(px), float(py), pop_map, radius=4)


def get_preset_station_name_for_point(
    preset_name: str,
    x: float,
    y: float,
    game_map,
    current_year: int = None,
    line_name: str = None,
    existing_names: set = None,
) -> Optional[str]:
    """Ask the map preset for a human-friendly station name for (x,y).
    Presets may use local geography (streets, neighborhoods, landmarks). Returns None if not supported."""
    if not preset_name:
        return None
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    fn = getattr(preset, "get_station_name_for_point", None)
    if not callable(fn):
        return None
    try:
        return fn(x, y, game_map, current_year=current_year, line_name=line_name, existing_names=existing_names)
    except TypeError:
        # Backwards/alternate signature support: allow presets to omit kwargs.
        try:
            return fn(x, y, game_map)
        except Exception:
            return None
    except Exception:
        return None


def get_preset_geo_feature_name_for_line_naming(
    preset_name: str,
    game_map,
    x: float,
    y: float,
    current_year: int = None,
    max_distance_m: float = None,
) -> Optional[str]:
    """Street/geography label for AI line naming if a feature exists within max_distance_m; else None."""
    if max_distance_m is None:
        from game_presets import AI_STATION_STREET_NAME_MAX_DISTANCE_M
        max_distance_m = AI_STATION_STREET_NAME_MAX_DISTANCE_M
    if not preset_name:
        return None
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    fn = getattr(preset, "get_geo_feature_name_for_line_naming", None)
    if not callable(fn):
        return None
    try:
        s = fn(game_map, float(x), float(y), current_year=current_year, max_distance_m=max_distance_m)
    except TypeError:
        try:
            s = fn(game_map, float(x), float(y), current_year, max_distance_m)
        except Exception:
            return None
    except Exception:
        return None
    if not s or not str(s).strip():
        return None
    return str(s).strip()


def _is_generic_station_label(name: str) -> bool:
    return (name or "").strip().lower() in ("", "station")


def _format_geography_street_label(raw: str, preset_name: str = None) -> str:
    if not raw or not str(raw).strip():
        return ""
    if preset_name:
        try:
            if preset_has_road_support(preset_name):
                from map_presets_nyc_road_utils import format_osm_road_name_for_display
                return format_osm_road_name_for_display(str(raw).strip())
        except Exception:
            pass
    s = str(raw).replace("_", " ").strip()
    return " ".join(part.capitalize() for part in s.split()) if s else ""


def _nearest_area_label_for_point(preset_name: str, x: float, y: float) -> Optional[str]:
    from game_presets import AI_STATION_STREET_NAME_MAX_DISTANCE_GRID

    area_map = get_preset_point_area_map(preset_name)
    if not area_map:
        return None
    ix, iy = int(round(float(x))), int(round(float(y)))
    best_d = float("inf")
    best = None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            key = (ix + dx, iy + dy)
            label = area_map.get(key)
            if not isinstance(label, str) or not label.strip():
                continue
            d = math.hypot(float(x) - key[0], float(y) - key[1])
            if d <= AI_STATION_STREET_NAME_MAX_DISTANCE_GRID and d < best_d:
                best_d = d
                best = label.strip()
    return best


def _unique_station_name(base: str, existing_names: set, station_ordinal: int = None) -> str:
    if base not in existing_names:
        return base
    if station_ordinal is not None:
        ord_cand = f"{base} — Stop {station_ordinal}"
        if ord_cand not in existing_names:
            return ord_cand
    k = 2
    while f"{base} ({k})" in existing_names and k < 999:
        k += 1
    return f"{base} ({k})"


def resolve_geography_station_name(
    preset_name: str,
    x: float,
    y: float,
    game_map,
    current_year: int = None,
    line_name: str = None,
    existing_names: set = None,
    station_ordinal: int = None,
) -> Optional[str]:
    """Street- or neighborhood-based station name from map geography (never fictional placeholders)."""
    existing_names = existing_names or set()
    if preset_name:
        suggestion = get_preset_station_name_for_point(
            preset_name,
            x,
            y,
            game_map,
            current_year=current_year,
            line_name=line_name,
            existing_names=existing_names,
        )
        if suggestion:
            s = str(suggestion).strip()
            if line_name:
                ln = str(line_name).strip()
                tagged = f" ({ln})"
                if s.endswith(tagged):
                    s = s[:-len(tagged)].strip()
            if not _is_generic_station_label(s):
                return _unique_station_name(s, existing_names, station_ordinal=station_ordinal)

    street = ""
    area = _nearest_area_label_for_point(preset_name, x, y) if preset_name else None
    if preset_name and game_map:
        try:
            from game_presets import AI_STATION_STREET_NAME_MAX_DISTANCE_M
            raw = get_preset_geo_feature_name_for_line_naming(
                preset_name,
                game_map,
                float(x),
                float(y),
                current_year=current_year,
                max_distance_m=AI_STATION_STREET_NAME_MAX_DISTANCE_M,
            )
            street = _format_geography_street_label(raw or "", preset_name)
        except Exception:
            street = ""

    name = ""
    if area and street:
        al, sl = area.lower(), street.lower()
        if al not in sl and sl not in al:
            name = f"{area} — {street}"
        else:
            name = street
    elif street:
        name = street
    elif area:
        name = area

    if _is_generic_station_label(name):
        return None
    return _unique_station_name(name, existing_names, station_ordinal=station_ordinal)


def get_preset_streets_route_runs_along(
    preset_name: str,
    game_map,
    route_points: list,
    current_year: int = None,
    max_distance_m: float = None,
    min_route_fraction: float = None,
) -> list:
    """Street names the route actually runs along (within max_distance_m), longest first."""
    if not preset_name or not route_points or len(route_points) < 2:
        return []
    from game_presets import AI_LINE_NAME_MAX_DISTANCE_M, AI_LINE_NAME_MIN_ROUTE_FRACTION
    if max_distance_m is None:
        max_distance_m = AI_LINE_NAME_MAX_DISTANCE_M
    if min_route_fraction is None:
        min_route_fraction = AI_LINE_NAME_MIN_ROUTE_FRACTION
    preset = load_map_preset(preset_name)
    if preset is None:
        return []
    fn = getattr(preset, "get_streets_route_runs_along", None)
    if not callable(fn):
        return []
    year = int(current_year) if current_year is not None else getattr(game_map, "start_year", 1830)
    try:
        return fn(
            game_map, list(route_points), current_year=year,
            max_distance_m=max_distance_m, min_route_fraction=min_route_fraction,
        ) or []
    except TypeError:
        try:
            return fn(game_map, list(route_points), year, max_distance_m, min_route_fraction) or []
        except Exception:
            return []
    except Exception:
        return []


def get_preset_public_ta_config(preset_name: str) -> Optional[Dict[str, Any]]:
    """Get PUBLIC_TA_CONFIG for preset. Returns None if not available (caller uses standard)."""
    if not preset_name:
        return None
    preset = load_map_preset(preset_name)
    if not preset:
        return None
    if hasattr(preset, 'get_public_ta_config'):
        try:
            return preset.get_public_ta_config()
        except Exception:
            pass
    if hasattr(preset, 'PUBLIC_TA_CONFIG'):
        return preset.PUBLIC_TA_CONFIG
    return None


def initialize_preset_population(preset_name: str, width: int, height: int,
                                map_center_lat: float = None, map_center_lon: float = None) -> Optional[Dict]:
    """Initialize population for preset. Returns None if preset has no custom init (caller uses standard)."""
    if not preset_name:
        return None
    preset = load_map_preset(preset_name)
    if not preset:
        return None
    for attr in dir(preset):
        if isinstance(attr, str) and attr.startswith('initialize_') and attr.endswith('_population'):
            try:
                fn = getattr(preset, attr)
                if callable(fn):
                    return fn(width, height, map_center_lat, map_center_lon)
            except Exception:
                pass
    return None


def get_preset_point_area_map(preset_name: str) -> dict:
    """Get point-to-area mapping for area-specific population changes (e.g. borough). Returns {} if not available."""
    if not preset_name:
        return {}
    preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'get_point_area_map'):
        return {}
    try:
        return preset.get_point_area_map()
    except Exception:
        return {}


def preset_has_population_init(preset_name: str) -> bool:
    """Check if a preset provides custom population initialization."""
    if not preset_name:
        return False
    preset = load_map_preset(preset_name)
    if not preset:
        return False
    if hasattr(preset, 'get_population_at_location'):
        return True
    return any(
        attr.startswith('initialize_') and attr.endswith('_population')
        for attr in dir(preset) if isinstance(attr, str)
    )


def preset_ai_uses_street_network(preset_name: str, game_map=None) -> bool:
    """True when AI line building must follow preset street/road polylines (not display-only OSM tiles)."""
    if not preset_name:
        return False
    return preset_has_road_support(preset_name)


def is_map_preset(preset_name: str, map_center_lat: float, map_center_lon: float, width: int, height: int) -> bool:
    """Check if a location matches a map preset.
    
    Args:
        preset_name: Name of the preset to check
        map_center_lat: Center latitude
        map_center_lon: Center longitude
        width: Map width
        height: Map height
        
    Returns:
        True if the location matches the preset, False otherwise
    """
    preset = load_map_preset(preset_name)
    if preset is None:
        return False
    if hasattr(preset, "is_map"):
        try:
            return preset.is_map(map_center_lat, map_center_lon, width, height)
        except Exception:
            pass
    for func_name in [f"is_{preset_name}_map", "is_map"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)(map_center_lat, map_center_lon, width, height)
            except Exception:
                pass
    if hasattr(preset, "MAP_BOUNDS"):
        try:
            b = preset.MAP_BOUNDS
            return (b['min_lat'] <= map_center_lat <= b['max_lat'] and
                    b['min_lon'] <= map_center_lon <= b['max_lon'])
        except Exception:
            pass
    return False


def convert_preset_grid_to_latlon(
    preset_name: str,
    grid_x: int,
    grid_y: int,
    bounds: dict,
    width: int,
    height: int,
) -> tuple[float, float]:
    """Convert preset grid indices to lat/lon using preset _grid_to_latlon when available."""
    preset = load_map_preset(preset_name)
    if preset is not None and hasattr(preset, '_grid_to_latlon'):
        try:
            return preset._grid_to_latlon(int(grid_x), int(grid_y), bounds, width, height)
        except Exception:
            pass
    min_lat = bounds.get('min_lat', 0.0)
    min_lon = bounds.get('min_lon', 0.0)
    max_lat = bounds.get('max_lat', min_lat)
    max_lon = bounds.get('max_lon', min_lon)
    lat_scale = (max_lat - min_lat) / height if height > 0 else 0.0
    lon_scale = (max_lon - min_lon) / width if width > 0 else 0.0
    return min_lat + grid_y * lat_scale, min_lon + grid_x * lon_scale


def get_map_bounds(preset_name: str) -> Optional[Dict[str, Any]]:
    """Get map bounds for a preset.
    
    Args:
        preset_name: Name of the preset
        
    Returns:
        Dictionary with bounds, or None if not found
    """
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    
    # Try standard function names
    for func_name in [f"get_{preset_name}_bounds", "get_bounds"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    
    # Try MAP_BOUNDS variable
    if hasattr(preset, "MAP_BOUNDS"):
        return preset.MAP_BOUNDS
    
    return None


def get_map_timeline(preset_name: str) -> Optional[Dict[str, Any]]:
    """Get timeline settings for a preset.
    
    Args:
        preset_name: Name of the preset
        
    Returns:
        Dictionary with timeline settings, or None if not found
    """
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_timeline"):
        try:
            return preset.get_timeline()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_timeline", "get_timeline"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    if hasattr(preset, "MAP_TIMELINE"):
        return preset.MAP_TIMELINE
    return None


def get_map_display_name(preset_name: str) -> Optional[str]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_display_name"):
        try:
            return preset.get_display_name()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_map_display_name", "get_display_name"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    if hasattr(preset, "MAP_NAME"):
        return preset.MAP_NAME
    return None


def get_map_zoom(preset_name: str) -> Optional[Dict[str, Any]]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_zoom"):
        try:
            return preset.get_zoom()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_zoom", "get_zoom"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    if hasattr(preset, "MAP_ZOOM"):
        return preset.MAP_ZOOM
    return None


def get_default_center_lat(preset_name: str) -> Optional[float]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_default_center_lat"):
        try:
            return preset.get_default_center_lat()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_default_center_lat", "get_default_center_lat"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    b = get_map_bounds(preset_name)
    return b.get("center_lat") if b else None


def get_default_center_lon(preset_name: str) -> Optional[float]:
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, "get_default_center_lon"):
        try:
            return preset.get_default_center_lon()
        except Exception:
            pass
    for func_name in [f"get_{preset_name}_default_center_lon", "get_default_center_lon"]:
        if hasattr(preset, func_name):
            try:
                return getattr(preset, func_name)()
            except Exception:
                pass
    b = get_map_bounds(preset_name)
    return b.get("center_lon") if b else None


def get_preset_default_line_names(preset_name: str) -> list:
    """Default line names for companies on this map (e.g. ['A', 'B', 'C']). Empty if not set."""
    names = []
    try:
        from ruleset_loader import load_organizations_for_ruleset
        org = load_organizations_for_ruleset()
        ruleset_lines = org.get("default_line_names") or []
        if ruleset_lines:
            names.extend(ruleset_lines)
    except Exception:
        pass
    if not preset_name:
        return names
    preset = load_map_preset(preset_name)
    if preset is None:
        return names
    if hasattr(preset, 'DEFAULT_LINE_NAMES') and preset.DEFAULT_LINE_NAMES:
        preset_names = list(preset.DEFAULT_LINE_NAMES)
        if names:
            merged = list(names)
            for n in preset_names:
                if n not in merged:
                    merged.append(n)
            return merged
        return preset_names


def get_preset_available_line_corridor_counts(
    preset_name: str, game_map, current_year: int,
) -> dict:
    """Counts of buildable street corridors for AI lines (OSM presets only)."""
    if not preset_name or game_map is None:
        return {'total_roads_in_bounds': 0, 'buildable_corridors': 0, 'unique_named_streets': 0}
    preset = load_map_preset(preset_name)
    if preset is None:
        return {'total_roads_in_bounds': 0, 'buildable_corridors': 0, 'unique_named_streets': 0}
    fn = getattr(preset, 'count_available_line_corridors', None)
    if not callable(fn):
        return {'total_roads_in_bounds': 0, 'buildable_corridors': 0, 'unique_named_streets': 0}
    try:
        return dict(fn(game_map, int(current_year)))
    except Exception:
        return {'total_roads_in_bounds': 0, 'buildable_corridors': 0, 'unique_named_streets': 0}
    return names


def get_preset_default_schedule_scheme(preset_name: str) -> Optional[Dict[str, float]]:
    """Default schedule scheme for new lines (e.g. peak/off_peak/night multipliers). None if not set."""
    if not preset_name:
        return None
    preset = load_map_preset(preset_name)
    if preset is None:
        return None
    if hasattr(preset, 'DEFAULT_SCHEDULE_SCHEME') and preset.DEFAULT_SCHEDULE_SCHEME:
        return dict(preset.DEFAULT_SCHEDULE_SCHEME)
    return None


def get_preset_road_segment_points(preset_name: str, game_map, current_year: int, max_points: int = 12,
                                   existing_line_routes=None, min_length_grid=None, max_length_grid=None,
                                   min_endpoint_population=None, rng=None, preset=None) -> list:
    """Get road segment points for AI line building. Preset provides street polylines if available.
    existing_line_routes: optional list of point lists from build queue (avoids overlapping lines).
    rng: optional Random - varies per game so different games build in different spots.
    preset: optional pre-loaded preset module (avoids repeated load_map_preset when caller already has it)."""
    if not preset_name:
        return []
    if preset is None:
        preset = load_map_preset(preset_name)
    if preset is None or not hasattr(preset, 'get_road_segment_points_for_ai'):
        return []
    try:
        return preset.get_road_segment_points_for_ai(
            game_map, current_year, max_points,
            existing_line_routes=existing_line_routes,
            min_length_grid=min_length_grid,
            max_length_grid=max_length_grid,
            min_endpoint_population=min_endpoint_population,
            rng=rng
        )
    except Exception:
        return []


def get_preset_ferry_segment_points(preset_name: str, game_map, current_year: int, max_points: int = 12,
                                    existing_line_routes=None, rng=None, preset=None) -> list:
    """Get ferry (water-crossing) segment candidates for AI. Each item is a list of grid points [(x,y), ...]
    for a straight or polyline segment over water. Used so the AI never uses the population-point build system for ferries.
    Returns [] if preset does not provide get_ferry_segment_points_for_ai.
    preset: optional pre-loaded preset module (avoids repeated load_map_preset)."""
    if not preset_name:
        return []
    if preset is None:
        preset = load_map_preset(preset_name)
    if preset is None or not hasattr(preset, 'get_ferry_segment_points_for_ai'):
        return []
    try:
        return preset.get_ferry_segment_points_for_ai(
            game_map, current_year, max_points,
            existing_line_routes=existing_line_routes,
            rng=rng
        ) or []
    except Exception:
        return []


def get_preset_bridge_polyline_between_shores(
    preset_name: str,
    game_map,
    shore_a: tuple,
    shore_b: tuple,
    current_year: int,
    max_points: int = 12,
    preset=None,
) -> list:
    """Historic bridge/tunnel polyline between two shore points, or [] if unavailable."""
    if not preset_name:
        return []
    if preset is None:
        preset = load_map_preset(preset_name)
    if preset is None:
        return []
    fn = getattr(preset, "get_bridge_polyline_between_shores", None)
    if not callable(fn):
        return []
    try:
        return fn(game_map, shore_a, shore_b, current_year, max_points=max_points) or []
    except TypeError:
        try:
            return fn(game_map, shore_a, shore_b, current_year) or []
        except Exception:
            return []
    except Exception:
        return []


def get_preset_road_extension_points(preset_name: str, game_map, base_pt: tuple, other_end_pt: tuple,
                                     current_year: int, extend_from_end: bool, max_points: int = 6,
                                     prefer_bridges: bool = False, preset=None, exclude_road_names=None,
                                     _chain_depth: int = 0, max_neighbor_street_gap_grid: float = None) -> list:
    """Get road extension points for AI line extension. Preset provides street polylines if available.
    prefer_bridges: when True, prefer bridge/tunnel segments (for crossing water).
    preset: optional pre-loaded preset module (avoids repeated load_map_preset).
    Returns list of (x,y) grid points, or [] if preset does not provide this capability."""
    if not preset_name:
        return []
    if preset is None:
        preset = load_map_preset(preset_name)
    if preset is None or not hasattr(preset, 'get_road_extension_points_from_end'):
        return []
    try:
        return preset.get_road_extension_points_from_end(
            game_map, base_pt, other_end_pt, current_year, extend_from_end, max_points,
            prefer_bridges=prefer_bridges, exclude_road_names=exclude_road_names, _chain_depth=_chain_depth,
            max_neighbor_street_gap_grid=max_neighbor_street_gap_grid,
        )
    except TypeError:
        try:
            return preset.get_road_extension_points_from_end(
                game_map, base_pt, other_end_pt, current_year, extend_from_end, max_points,
                prefer_bridges=prefer_bridges, exclude_road_names=exclude_road_names, _chain_depth=_chain_depth,
            )
        except TypeError:
            try:
                return preset.get_road_extension_points_from_end(
                    game_map, base_pt, other_end_pt, current_year, extend_from_end, max_points,
                    prefer_bridges=prefer_bridges,
                )
            except TypeError:
                return preset.get_road_extension_points_from_end(
                    game_map, base_pt, other_end_pt, current_year, extend_from_end, max_points
                )
    except Exception:
        return []
