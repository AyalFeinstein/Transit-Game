from line import Line
# Dynamic map preset loading - allows custom maps without modifying main code
from map_preset_loader import load_map_preset, is_map_preset, get_map_bounds, get_map_timeline, list_available_presets, preset_has_water_areas, preset_has_population_init, get_default_map_bounds, get_default_map_timeline, get_default_map_zoom, get_default_population_settings, default_grid_population_at, get_preset_water_mask, DEFAULT_PRESET_NAME
from map_presets import OSM_SETTINGS  # settings shared by every map


class Map:
    def __init__(self,
                 grid: dict = dict(),
                 lines_list: list = [],
                 roads: dict = dict(),
                 width: int = 1,
                 height: int = 1,
                 use_osm: bool = True,
                 map_center_lat: float = None,
                 map_center_lon: float = None,
                 map_preset_name: str = None):
        """The map contains many things:
        Grid squares are represented by a dictionary: the point is the key, the data is in grid_point.
        The lines are a dictionary (line_name:Line)
        
        If use_osm is True, the map uses OpenStreetMap data for rights of way.
        map_center_lat and map_center_lon define the center point for OSM queries.
        map_preset_name: Optional explicit preset name - skips auto-detection."""
        self.grid = grid
        self.lines = dict()
        for l in lines_list:
            self.lines[l.name] = l
        self.roads = roads
        self.width = width
        self.height = height
        self.use_osm = use_osm
        if not hasattr(self, 'stations'):
            self.stations = []
        
        # Map type: use explicit preset if provided, else auto-detect
        self.map_preset_name = None
        self.has_preset_water = False
        self.has_preset_population = False
        
        if map_preset_name and map_preset_name in list_available_presets():
            self.map_preset_name = map_preset_name
            self.has_preset_water = preset_has_water_areas(map_preset_name)
            self.has_preset_population = preset_has_population_init(map_preset_name)
        else:
            try:
                for preset_name in list_available_presets():
                    if is_map_preset(preset_name, map_center_lat, map_center_lon, width, height):
                        self.map_preset_name = preset_name
                        self.has_preset_water = preset_has_water_areas(preset_name)
                        self.has_preset_population = preset_has_population_init(preset_name)
                        break
            except Exception:
                pass
        
        # Set center coordinates based on map type (using dynamic preset loader)
        if self.map_preset_name:
            # Try to get bounds and timeline from preset
            preset_bounds = get_map_bounds(self.map_preset_name)
            preset_timeline = get_map_timeline(self.map_preset_name)
            
            if preset_bounds:
                self.map_center_lat = preset_bounds.get('center_lat', map_center_lat if map_center_lat is not None else 0.0)
                self.map_center_lon = preset_bounds.get('center_lon', map_center_lon if map_center_lon is not None else 0.0)
            else:
                self.map_center_lat = map_center_lat if map_center_lat is not None else 0.0
                self.map_center_lon = map_center_lon if map_center_lon is not None else 0.0
            
            if preset_timeline:
                self.start_year = preset_timeline.get('start_year', 1830)
                self.end_year = preset_timeline.get('end_year', 2050)
            else:
                self.start_year = 1830
                self.end_year = 2050
            
            # Try to get zoom settings
            preset = load_map_preset(self.map_preset_name)
            if preset and hasattr(preset, f"get_{self.map_preset_name}_zoom"):
                try:
                    self.zoom_settings = getattr(preset, f"get_{self.map_preset_name}_zoom")()
                except:
                    self.zoom_settings = get_default_map_zoom()
            elif preset and hasattr(preset, "MAP_ZOOM"):
                self.zoom_settings = preset.MAP_ZOOM
            else:
                self.zoom_settings = get_default_map_zoom()
        else:
            # Standard/default map
            self.map_center_lat = map_center_lat if map_center_lat is not None else get_default_map_bounds()['center_lat']
            self.map_center_lon = map_center_lon if map_center_lon is not None else get_default_map_bounds()['center_lon']
            self.start_year = get_default_map_timeline()['start_year']
            self.end_year = get_default_map_timeline()['end_year']
            self.zoom_settings = get_default_map_zoom()
        
        self.osm_integration = None
        if use_osm:
            try:
                from osm_integration import OSMIntegration
                init_timeout = min(OSM_SETTINGS['timeout'], 2.0)
                self.osm_integration = OSMIntegration(timeout=init_timeout)
            except ImportError:
                self.use_osm = False
                import warnings
                warnings.warn("OSM integration disabled: requests module is required for OSM integration. Install with: pip install requests")
            except Exception as e:
                self.use_osm = False
                import warnings
                warnings.warn(f"OSM integration disabled due to error: {e}")
        
        # Initialize OSM tile system for land/water detection
        # This is stored per map instance and used for all water/land calculations
        self.tile_system = None
        if use_osm and self.osm_integration:
            try:
                from osm_tile_system import OSMTileSystem
                self.tile_system = OSMTileSystem(
                    map_width=width,
                    map_height=height,
                    map_center_lat=self.map_center_lat,
                    map_center_lon=self.map_center_lon,
                    tile_size=0.0005,  # ~55m per tile (dramatically increased resolution)
                    osm_integration=self.osm_integration
                )
                # Tell the tile system whether this map has its own water test
                if hasattr(self, 'has_preset_water'):
                    self.tile_system.has_preset_water = self.has_preset_water
                # Each preset's own water test (the tile system must not assume another map's).
                _preset_mod = load_map_preset(self.map_preset_name) if self.map_preset_name else None
                if _preset_mod is not None and hasattr(_preset_mod, 'is_water_location'):
                    self.tile_system.preset_water_fn = _preset_mod.is_water_location
                _mask = get_preset_water_mask(self.map_preset_name)
                self.tile_system.preset_water_authoritative = bool(_mask is not None and _mask.has_data())

                # CRITICAL FIX: Ensure tile system bounds match preset map bounds.
                # Without this, grid->latlon conversion can be inconsistent with UI rendering,
                # causing false "too far from water" rejections for ferry lines.
                try:
                    bounds = get_map_bounds(self.map_preset_name) if self.map_preset_name else None
                    if bounds and all(k in bounds for k in ("min_lat", "min_lon", "max_lat", "max_lon")):
                        self.tile_system.set_bounds(bounds["min_lat"], bounds["min_lon"], bounds["max_lat"], bounds["max_lon"])
                except Exception:
                    # If bounds override fails, continue with center-based bounds.
                    pass
            except Exception as e:
                import warnings
                warnings.warn(f"OSM tile system initialization failed: {e}")
        
        # Store reference to game (set after Map creation)
        self._game = None
        
        # Population system: each location (x, y) has a limited number of people
        # Population is stored as {(x, y): population_count}
        # Delegate to preset-specific initialization functions
        # Try custom preset first, then fall back to hardcoded presets
        if self.map_preset_name and self.map_preset_name != DEFAULT_PRESET_NAME and preset_has_population_init(self.map_preset_name):
            # Custom preset - try to initialize population
            preset = load_map_preset(self.map_preset_name)
            if preset:
                try:
                    # Try to find population initialization function
                    if hasattr(preset, f"initialize_{self.map_preset_name}_population"):
                        init_func = getattr(preset, f"initialize_{self.map_preset_name}_population")
                        self.population = init_func(self.width, self.height, self.map_center_lat, self.map_center_lon)
                    elif hasattr(preset, "get_population_at_location"):
                        # Initialize population grid using get_population_at_location
                        self.population = {}
                        bounds = get_map_bounds(self.map_preset_name) or {}
                        min_lat = bounds.get('min_lat', self.map_center_lat - 0.5)
                        max_lat = bounds.get('max_lat', self.map_center_lat + 0.5)
                        min_lon = bounds.get('min_lon', self.map_center_lon - 0.5)
                        max_lon = bounds.get('max_lon', self.map_center_lon + 0.5)
                        
                        for x in range(self.width + 1):
                            for y in range(self.height + 1):
                                lat = min_lat + (y / self.height) * (max_lat - min_lat) if self.height > 0 else self.map_center_lat
                                lon = min_lon + (x / self.width) * (max_lon - min_lon) if self.width > 0 else self.map_center_lon
                                is_water = False
                                if hasattr(preset, "is_water_location"):
                                    try:
                                        is_water = preset.is_water_location(lat, lon, self.osm_integration)
                                    except:
                                        is_water = preset.is_water_location(lat, lon) if hasattr(preset.is_water_location, '__code__') and preset.is_water_location.__code__.co_argcount == 2 else False
                                pop = preset.get_population_at_location(lat, lon, is_water, x, y, self.width, self.height)
                                self.population[(x, y)] = pop
                    else:
                        # Fallback: use standard population
                        self.population = {}
                        for x in range(self.width + 1):
                            for y in range(self.height + 1):
                                self.population[(x, y)] = default_grid_population_at(x, y, self.width, self.height)
                except Exception as e:
                    import warnings
                    warnings.warn(f"Failed to initialize population for custom preset {self.map_preset_name}: {e}")
                    # Fallback to standard
                    self.population = {}
                    for x in range(self.width + 1):
                        for y in range(self.height + 1):
                            self.population[(x, y)] = default_grid_population_at(x, y, self.width, self.height)
            else:
                # Preset module failed to load (load_map_preset returned None) - fall back to standard
                import warnings
                warnings.warn(f"Preset {self.map_preset_name} could not be loaded; using standard population")
                self.population = {}
                for x in range(self.width + 1):
                    for y in range(self.height + 1):
                        self.population[(x, y)] = default_grid_population_at(x, y, self.width, self.height)
        else:
            # Standard map: Use similar population density (per grid square)
            self.population = {}
            for x in range(self.width + 1):
                for y in range(self.height + 1):
                    pop = default_grid_population_at(x, y, self.width, self.height)
                    self.population[(x, y)] = pop
        
        self._remove_population_on_water()
        if self.map_preset_name:
            from map_preset_loader import apply_preset_workplace_density
            try:
                apply_preset_workplace_density(self.map_preset_name, self.population, self.width, self.height)
            except Exception as e:
                import warnings
                warnings.warn(f"Workplace density adjustment failed for {self.map_preset_name}: {e}")

        # Points removed in edit mode - never show or use these (persists for session)
        self._deleted_population_points = set()

        # Cache for OSM rights of way
        self._osm_rights_of_way = None
        self._osm_bbox = None
        # Lazy cache for preset INITIAL_POPULATION land cells (avoids re-parsing preset file per point check)
        self._buildable_included_points: frozenset[tuple[int, int]] | None = None
        self._buildable_included_points_loaded = False

    def _remove_population_on_water(self) -> None:
        """Fill required land, drop excluded areas, and keep population off water.

        Water cells are dropped (people move to the nearest land cell).

        Runs on the final grid, after presets spread anchors onto neighbouring cells, which
        is what otherwise puts points in rivers and bays. Maps without real geography
        (the standard grid) have no water data and are left as they are.
        """
        preset_name = getattr(self, 'map_preset_name', None)
        if not preset_name or not self.population or not preset_has_water_areas(preset_name):
            return
        try:
            from map_preset_loader import convert_preset_grid_to_latlon
            from map_preset_utils import remove_water_population_cells
            bounds = get_map_bounds(preset_name) or {}
            if not all(k in bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
                return
            w, h = self.width, self.height
            to_latlon = lambda gx, gy: convert_preset_grid_to_latlon(preset_name, gx, gy, bounds, w, h)
            preset = load_map_preset(preset_name)
            mask = get_preset_water_mask(preset_name)
            # Land a preset must cover even where its stored data has gaps.
            fill_fn = getattr(preset, 'population_fill_regions', None) if preset else None
            fill_regions = fill_fn() if callable(fill_fn) else None
            if fill_regions:
                from map_preset_utils import fill_population_in_regions
                self.population, _filled = fill_population_in_regions(
                    self.population, w, h, to_latlon, fill_regions, cache_key=f"{preset_name}_{w}x{h}",
                    mask=mask,
                )
            # Areas a preset leaves out entirely.
            regions = getattr(preset, 'EXCLUDED_POPULATION_REGIONS', ()) if preset else ()
            if regions:
                from map_preset_utils import remove_population_in_regions
                self.population, _dropped = remove_population_in_regions(
                    self.population, to_latlon, regions, cache_key=f"{preset_name}_{w}x{h}", mask=mask,
                )
            self.population, _removed = remove_water_population_cells(
                self.population, to_latlon, cache_key=f"{preset_name}_{w}x{h}", mask=mask,
            )
        except Exception as e:
            import warnings
            warnings.warn(f"Could not remove population on water: {e}")

    def is_point_in_buildable_area(self, x: float, y: float) -> bool:
        """Check if a grid point is within map bounds.

        Buildable-area zone restrictions (preset population cells) are not enforced here;
        water/street validation applies at line-build time instead.
        """
        gx, gy = int(round(x)), int(round(y))
        return 0 <= gx <= self.width and 0 <= gy <= self.height
    
    def get_tile_system(self):
        """Get the tile system for this map. Returns None if not initialized."""
        return self.tile_system
    
    def get_population(self, x, y) -> int:
        """Get the population at a specific location. Accepts int or float coordinates."""
        grid_x, grid_y = int(round(x)), int(round(y))
        direct = self.population.get((grid_x, grid_y))
        if direct is not None and int(direct) > 0:
            return int(direct)
        anchors = getattr(self, '_population_anchors', None)
        if anchors:
            from map_preset_utils import population_at_point_from_anchors
            return population_at_point_from_anchors(float(x), float(y), anchors)
        return int(direct or 0)
    
    def get_available_passengers(self, x: int, y: int, time_period: str = "off_peak") -> int:
        """Get available passengers at a location for a time period.
        
        Only a fraction of the population takes trips:
        - peak: highest
        - off_peak: medium
        - night: lowest
        """
        pop = self.get_population(x, y)
        participation_rates = {
            "peak": 0.25,
            "off_peak": 0.15,
            "night": 0.05,
        }
        rate = participation_rates.get(time_period, participation_rates["off_peak"])
        # Clamp and convert to int passengers available
        rate = max(0.0, min(1.0, float(rate)))
        return int(pop * rate)
    
    def update_population_over_time(self, current_year: int, event_population_change: float = 0.0,
                                    area_population_changes: dict = None):
        """Update population levels based on the current year.
        Population grows over time, with some locations growing faster than others.
        event_population_change: additional percentage change from events (e.g., 0.05 for 5% increase)
        area_population_changes: {event name: area change} from the game's events (extra growth
        in some boroughs or boxes); looked up from the game when not given."""
        import random
        
        # Get population settings from preset or use standard
        pop_settings = get_default_population_settings()
        if self.map_preset_name:
            preset = load_map_preset(self.map_preset_name)
            if preset:
                for attr in dir(preset):
                    if attr.endswith('_population_settings') and callable(getattr(preset, attr, None)):
                        try:
                            pop_settings = getattr(preset, attr)()
                            break
                        except Exception:
                            pass
        
        # Base year for population growth
        base_year = pop_settings['base_year']
        years_passed = current_year - base_year
        
        # Growth factor: use milestone targets for fluid rate when available (realistic growth)
        if years_passed <= 0:
            growth_factor = 1.0
        elif pop_settings.get('population_targets_by_year'):
            # Derive growth from targets so population hits milestones at a fluid rate
            targets = pop_settings['population_targets_by_year']
            years = sorted(targets.keys())
            if current_year <= years[0]:
                growth_factor = 1.0
            else:
                # target_prev = interpolated target for (current_year - 1)
                # target_curr = interpolated target for current_year
                # growth_factor = target_curr / target_prev
                def target_at_year(y):
                    if y <= years[0]:
                        return targets[years[0]]
                    if y >= years[-1]:
                        return targets[years[-1]]
                    y_lo = years[0]
                    for y_hi in years[1:]:
                        if y <= y_hi:
                            t = (y - y_lo) / (y_hi - y_lo)
                            return targets[y_lo] * (1 - t) + targets[y_hi] * t
                        y_lo = y_hi
                    return targets[years[-1]]
                target_prev = target_at_year(current_year - 1)
                target_curr = target_at_year(current_year)
                growth_factor = target_curr / target_prev if target_prev > 0 else 1.0
        else:
            # Fallback: exponential growth for maps without targets
            base_growth_rate = pop_settings['population_growth_rate']
            min_var, max_var = pop_settings['population_variation']
            growth_rate = base_growth_rate + random.uniform(min_var, max_var)
            growth_factor = (1.0 + growth_rate) ** years_passed
        
        station_locations = {}
        for station in getattr(self, 'stations', []):
            if hasattr(station, 'get_x') and hasattr(station, 'get_y'):
                sx, sy = int(round(station.get_x())), int(round(station.get_y()))
                station_locations[(sx, sy)] = station_locations.get((sx, sy), 0) + 1
        
        for line in self.lines.values():
            if hasattr(line, 'stations'):
                for station in line.stations:
                    if hasattr(station, 'get_x') and hasattr(station, 'get_y'):
                        sx, sy = int(round(station.get_x())), int(round(station.get_y()))
                        station_locations[(sx, sy)] = station_locations.get((sx, sy), 0) + 1
        
        total_new_population = 0
        
        import math
        max_pop = pop_settings.get('max_population_per_location', None)
        max_pop_int = int(max_pop) if max_pop is not None else None
        min_pop = pop_settings.get('min_population_per_location', 0)
        min_var, max_var = pop_settings['location_growth_variation']
        event_multiplier = (1.0 + event_population_change) if event_population_change != 0.0 else 1.0

        # Get area-specific population changes from events (if preset map)
        if area_population_changes is None:
            area_population_changes = {}
        if not area_population_changes and self.map_preset_name and self.map_preset_name != DEFAULT_PRESET_NAME:
            try:
                if hasattr(self, '_game') and hasattr(self._game, 'event_system'):
                    event_effects = self._game.event_system.get_combined_effects(current_year)
                    area_population_changes = event_effects.get('area_population_changes', {})
            except Exception:
                pass

        # Convert station_locations to set for faster lookups
        station_set = set(station_locations.keys())

        # Get bounds for coordinate conversion (if preset map)
        bounds = get_map_bounds(self.map_preset_name) if self.map_preset_name else None
        if bounds and all(k in bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
            min_lat = bounds['min_lat']
            max_lat = bounds['max_lat']
            min_lon = bounds['min_lon']
            max_lon = bounds['max_lon']
            point_area_map = getattr(self, '_preset_point_area_map_cache', None)
            if point_area_map is None and self.map_preset_name:
                from map_preset_loader import get_preset_point_area_map
                point_area_map = get_preset_point_area_map(self.map_preset_name)
                try:
                    self._preset_point_area_map_cache = point_area_map
                except Exception:
                    pass
            elif point_area_map is None:
                point_area_map = {}
        else:
            min_lat = max_lat = min_lon = max_lon = None
            point_area_map = {}

        # Points removed in edit mode - never update these (they must stay removed)
        deleted = getattr(self, '_deleted_population_points', set()) or set()

        # Iterate over items() for better performance (single dict lookup per iteration)
        pop_items = list(self.population.items())
        for (x, y), current_pop in pop_items:
            if (x, y) in deleted:
                continue  # Never add population back to edit-mode-removed points
            if current_pop == 0:
                continue

            # Calculate area-specific multiplier for this point
            area_multiplier = 1.0
            if self.map_preset_name and area_population_changes and min_lat is not None:
                # Convert grid coordinates to lat/lon
                lat = min_lat + (y / self.height) * (max_lat - min_lat)
                lon = min_lon + (x / self.width) * (max_lon - min_lon)
                
                # Check each area-specific event
                for event_name, area_change in area_population_changes.items():
                    if 'boroughs' in area_change:
                        # Check if point is in affected borough
                        point_borough = point_area_map.get((x, y), {}).get('borough', 'Unknown')
                        if point_borough in area_change['boroughs']:
                            area_multiplier *= (1.0 + area_change['change_percent'])
                    elif 'bounds_list' in area_change:
                        # Several boxes (an area that is not a rectangle): inside any one counts once
                        if any(b['min_lat'] <= lat <= b['max_lat'] and b['min_lon'] <= lon <= b['max_lon']
                               for b in area_change['bounds_list']):
                            area_multiplier *= (1.0 + area_change['change_percent'])
                    elif 'bounds' in area_change:
                        # Check if point is in affected bounds
                        bounds = area_change['bounds']
                        if (bounds['min_lat'] <= lat <= bounds['max_lat'] and
                            bounds['min_lon'] <= lon <= bounds['max_lon']):
                            area_multiplier *= (1.0 + area_change['change_percent'])
            
            if years_passed <= 0:
                location_growth = growth_factor * event_multiplier * area_multiplier
            else:
                location_growth = growth_factor * random.uniform(min_var, max_var) * event_multiplier * area_multiplier

            # Prevent pathological growth from blowing up simulation (and runtime)
            if not math.isfinite(location_growth) or location_growth < 0:
                location_growth = 1.0
            
            has_station = (x, y) in station_set
            if has_station:
                station_bonus = 1.0 + (station_locations[(x, y)] * 0.05)
                location_growth *= station_bonus
                total_new_population += int(current_pop * 0.10)
            
            new_pop = int(current_pop * location_growth)
            new_pop = max(min_pop, new_pop)
            if max_pop_int is not None:
                new_pop = min(max_pop_int, new_pop)
            
            self.population[(x, y)] = new_pop
        
        if total_new_population > 0:
            station_points = [p for p in station_locations.keys() if p not in deleted and self.population.get(p, 0) > 0]
            if station_points:
                n = min(total_new_population, len(station_points) * 50)
                from collections import Counter
                for pt, cnt in Counter(random.choices(station_points, k=n)).items():
                    self.population[pt] = self.population.get(pt, 0) + cnt
        
        # Scale total population to match the map's historical targets (fluid interpolation)
        current_total = sum(self.population.values())
        if current_total > 0:
            scale_factor = 1.0
            if pop_settings.get('population_targets_by_year'):
                targets = pop_settings['population_targets_by_year']
                years = sorted(targets.keys())
                if current_year <= years[0]:
                    target = targets[years[0]]
                elif current_year >= years[-1]:
                    target = targets[years[-1]]
                else:
                    # Interpolate linearly between surrounding years
                    y_lo = years[0]
                    for y_hi in years[1:]:
                        if current_year <= y_hi:
                            t = (current_year - y_lo) / (y_hi - y_lo)
                            target = int(targets[y_lo] * (1 - t) + targets[y_hi] * t)
                            break
                        y_lo = y_hi
                    else:
                        target = targets[years[-1]]
                scale_factor = target / current_total
            elif (self.map_preset_name == DEFAULT_PRESET_NAME or not self.map_preset_name):
                # Scale by map size: reference 100x75; total proportional to area
                ref_area = 100 * 75
                map_area = self.width * self.height
                # Base total at ref size grows over time (2% from 1830)
                years_passed = max(0, current_year - base_year)
                ref_total = int(pop_settings.get('total_population_1830', 100000) * ((1.02) ** years_passed))
                target = int(ref_total * map_area / ref_area)
                scale_factor = target / current_total
            if scale_factor != 1.0 and scale_factor > 0:
                for (x, y) in self.population:
                    if (x, y) in deleted:
                        continue  # Never scale deleted points
                    if self.population[(x, y)] > 0:
                        new_val = max(min_pop, int(self.population[(x, y)] * scale_factor))
                        if max_pop_int is not None:
                            new_val = min(max_pop_int, new_val)
                        self.population[(x, y)] = new_val
    
    def get_lines_by_name(self):
        return self.lines

    def get_roads(self):
        return self.roads

    def miles_per_grid_unit(self) -> float:
        """Real-world miles per grid cell (1.0 on maps without geography)."""
        cached = getattr(self, '_miles_per_grid_unit', None)
        if cached is None:
            from map_preset_loader import get_preset_miles_per_grid_unit
            try:
                cached = float(get_preset_miles_per_grid_unit(getattr(self, 'map_preset_name', None)))
            except Exception:
                cached = 1.0
            self._miles_per_grid_unit = cached
        return cached

    def apply_distance_scale_to_lines(self) -> None:
        """Give every line this map's miles-per-cell (used for travel times)."""
        scale = self.miles_per_grid_unit()
        for line in self.lines.values():
            line.miles_per_grid_unit = scale

    def add_line(self,
                 the_line: Line):
        self.lines[the_line.name] = the_line
        the_line.miles_per_grid_unit = self.miles_per_grid_unit()
        
        # When a line is built, increase population near the line
        # People settle near railroads
        if hasattr(the_line, 'get_route'):
            route = the_line.get_route()
            if route:
                import random
                
                # Increase population at stations and nearby locations
                for point in route:
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        x, y = point[0], point[1]
                        
                        # Increase population at the line location
                        if (x, y) in self.population:
                            current_pop = self.population[(x, y)]
                            if current_pop > 0:  # Don't increase water locations
                                # 10-30% increase when line is built
                                increase_factor = random.uniform(1.10, 1.30)
                                self.population[(x, y)] = int(current_pop * increase_factor)
                        
                        # Also increase population in nearby locations (within 2 grid units)
                        for dx in range(-2, 3):
                            for dy in range(-2, 3):
                                if dx == 0 and dy == 0:
                                    continue
                                nx, ny = x + dx, y + dy
                                if (nx, ny) in self.population:
                                    current_pop = self.population[(nx, ny)]
                                    if current_pop > 0:
                                        # Smaller increase for nearby locations (5-15%)
                                        nearby_increase = random.uniform(1.05, 1.15)
                                        self.population[(nx, ny)] = int(current_pop * nearby_increase)

    def delete_line(self,
                    the_line: Line):
        # Remove stations that only served this line from the map (e.g. when player eliminated)
        line_name = the_line.name
        stations = list(the_line.get_stations() if hasattr(the_line, 'get_stations') else getattr(the_line, 'stations', []))
        if hasattr(self, 'stations') and self.stations:
            for station in stations:
                line_names = list(station.get_line_names() if hasattr(station, 'get_line_names') else getattr(station, 'line_names', []))
                if line_name in line_names:
                    line_names_after = [ln for ln in line_names if ln != line_name]
                    if hasattr(station, 'line_names'):
                        station.line_names = line_names_after
                    if not line_names_after and station in self.stations:
                        self.stations.remove(station)
                if hasattr(the_line, 'remove_station'):
                    the_line.remove_station(station)
        del self.lines[the_line.name]
    
    def get_osm_rights_of_way(self) -> list:
        if not self.use_osm or not self.osm_integration:
            return []
        
        import math
        bounds = get_map_bounds(self.map_preset_name) if self.map_preset_name else None
        if bounds and all(k in bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
            min_lat = bounds['min_lat']
            max_lat = bounds['max_lat']
            min_lon = bounds['min_lon']
            max_lon = bounds['max_lon']
        else:
            center_lat = self.map_center_lat if self.map_center_lat is not None else 0.0
            center_lon = self.map_center_lon if self.map_center_lon is not None else 0.0
            lat_span = (self.height / 2) / 111.0
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
            if lon_deg_per_km == 0:
                lon_deg_per_km = 111.0
            lon_span = (self.width / 2) / lon_deg_per_km
            min_lat = center_lat - lat_span
            max_lat = center_lat + lat_span
            min_lon = center_lon - lon_span
            max_lon = center_lon + lon_span
        
        # Check cache
        bbox_key = (min_lat, min_lon, max_lat, max_lon)
        if self._osm_rights_of_way is not None and self._osm_bbox == bbox_key:
            return self._osm_rights_of_way
        
        try:
            import time
            start_time = time.time()
            # Use shorter timeout for line creation to prevent UI freeze
            original_timeout = self.osm_integration.timeout
            self.osm_integration.timeout = OSM_SETTINGS['quick_timeout']
            rights_of_way = self.osm_integration.get_available_rights_of_way(
                min_lat, min_lon, max_lat, max_lon
            )
            self.osm_integration.timeout = original_timeout  # Restore original timeout
            elapsed = time.time() - start_time
            
            # If query took too long, return empty to prevent future freezes
            if elapsed > OSM_SETTINGS['max_timeout_seconds']:
                return []
            
            self._osm_rights_of_way = rights_of_way
            self._osm_bbox = bbox_key
            return rights_of_way
        except Exception:
            # On any error, return empty list but don't disable OSM completely
            # This allows the map to still work without OSM snapping
            return []
    
    def find_nearest_right_of_way(self, x: int, y: int) -> dict:
        if not self.use_osm or not self.osm_integration:
            return None
        
        center_lat = self.map_center_lat if self.map_center_lat is not None else 0.0
        center_lon = self.map_center_lon if self.map_center_lon is not None else 0.0
        lat, lon = self.osm_integration.convert_grid_to_latlon(
            x, y, center_lat, center_lon, max(self.width, self.height)
        )
        
        # Get rights of way
        ways = self.get_osm_rights_of_way()
        if not ways:
            return None
        
        # Find nearest
        nearest = self.osm_integration.find_nearest_way(lat, lon, ways, max_distance_km=1.0)
        return nearest

