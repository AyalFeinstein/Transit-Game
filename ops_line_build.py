"""Operations mixin: LineBuildMixin."""
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
    FERRY_MAX_DISTANCE_FROM_WATER_M,
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



from route_validation import *


class LineBuildMixin:
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
                            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
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
                    if shared_routes and self._segment_reuses_existing_track(
                        x1, y1, x2, y2, shared_routes, shared_threshold,
                    ):
                        base_cost *= SHARED_TRACK_COST_FACTOR
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
        """Route polylines of all built lines (candidate shared tracks)."""
        game_map = self.game.get_map()
        raw = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else None
        map_lines = raw if isinstance(raw, dict) else {}
        exclude = (exclude_line_name or '').strip().casefold()
        routes = []
        for name, line in map_lines.items():
            if exclude and str(name).strip().casefold() == exclude:
                continue
            route = line.get_route() if hasattr(line, 'get_route') else []
            if route and len(route) >= 2:
                routes.append(route)
        return routes

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
    
