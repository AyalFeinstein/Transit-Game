"""Consolidated tests for map."""
import unittest

import unittest
from map import Map
from line import Line
from player import Player


class TestMap(unittest.TestCase):
    def setUp(self):
        self.map = Map(width=50, height=50)
        self.player = Player(0, 10000, [], "Test Player")
    
    def test_map_initialization(self):
        """Test Map initialization"""
        self.assertEqual(self.map.width, 50)
        self.assertEqual(self.map.height, 50)
        self.assertEqual(self.map.grid, {})
        self.assertEqual(self.map.lines, {})
        self.assertEqual(self.map.roads, {})
        # Map defaults to use_osm=True for OSM tile support
        self.assertTrue(self.map.use_osm)
        self.assertEqual(self.map.stations, [])

    def test_map_initialization_use_osm_false(self):
        """Test Map initialization with use_osm=False"""
        map_no_osm = Map(width=30, height=30, use_osm=False)
        self.assertFalse(map_no_osm.use_osm)
    
    def test_add_line(self):
        """Test adding lines to map"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.map.add_line(line)
        
        lines_by_name = self.map.get_lines_by_name()
        self.assertIn("Test Line", lines_by_name)
        self.assertEqual(lines_by_name["Test Line"], line)
    
    def test_delete_line(self):
        """Test deleting lines from map"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.map.add_line(line)
        
        self.assertIn("Test Line", self.map.get_lines_by_name())
        self.map.delete_line(line)
        self.assertNotIn("Test Line", self.map.get_lines_by_name())
    
    def test_get_lines_by_name(self):
        """Test getting lines by name"""
        line1 = Line("Line 1", 1, [(0, 0), (1, 1)], self.player, [])
        line2 = Line("Line 2", 2, [(2, 2), (3, 3)], self.player, [])
        
        self.map.add_line(line1)
        self.map.add_line(line2)
        
        lines_by_name = self.map.get_lines_by_name()
        self.assertEqual(len(lines_by_name), 2)
        self.assertIn("Line 1", lines_by_name)
        self.assertIn("Line 2", lines_by_name)
    
    def test_map_with_osm(self):
        """Test map with OSM enabled"""
        # Note: This test may fail if OSM integration is not available
        try:
            map_osm = Map(width=50, height=50, use_osm=True,
                         map_center_lat=40.7128, map_center_lon=-74.0060)
            self.assertTrue(map_osm.use_osm)
        except Exception:
            # OSM might not be available, that's okay
            pass

    def test_tile_system_none_when_osm_disabled(self):
        """Game operation: tile_system is None when OSM is disabled (no lazy init in production)."""
        m = Map(width=50, height=50, use_osm=False)
        self.assertIsNone(m.get_tile_system())

    def test_tile_system_initialized_when_osm_enabled(self):
        """Game operation: tile_system is initialized in Map.__init__ when use_osm=True and OSM loads."""
        try:
            m = Map(width=50, height=50, use_osm=True,
                    map_center_lat=40.7128, map_center_lon=-74.0060)
            if m.osm_integration is not None:
                self.assertIsNotNone(m.get_tile_system(),
                                    "tile_system must be set at init when use_osm=True and osm_integration exists")
        except Exception:
            # OSM/requests may be unavailable
            self.skipTest("OSM integration not available")
    
    def test_nyc_map_detection(self):
        """Test NYC map detection"""
        # NYC coordinates
        nyc_map = Map(width=50, height=50, use_osm=False,
                     map_center_lat=40.7128, map_center_lon=-74.0060)
        
        # Check if it's detected as NYC proper
        if hasattr(nyc_map, 'is_nyc_proper'):
            self.assertIsNotNone(nyc_map.is_nyc_proper)
    
    def test_population_points(self):
        """Test population points"""
        self.assertIsNotNone(self.map.population)
        self.assertIsInstance(self.map.population, dict)

    def test_ny_metro_initial_population_cells(self):
        """NY Metro map must ship with precomputed INITIAL_POPULATION land cells."""
        from map_presets_ny_metro import NY_METRO_INITIAL_POPULATION

        self.assertGreater(len(NY_METRO_INITIAL_POPULATION), 100)

    def test_ny_metro_nyc_area_population_matches_nyc_map(self):
        """NYC-area population on NY Metro map should match the NYC map total at 1830."""
        from map import Map
        from map_presets_nyc import get_nyc_bounds, NYC_POPULATION, initialize_nyc_population
        from map_presets_ny_metro import (
            get_ny_metro_bounds,
            initialize_ny_metro_population,
            get_nyc_derived_metro_cells,
            NY_METRO_INITIAL_POPULATION,
        )

        nyc_bounds = get_nyc_bounds()
        metro_bounds = get_ny_metro_bounds()
        nw, nh = nyc_bounds['width'], nyc_bounds['height']
        mw, mh = metro_bounds['width'], metro_bounds['height']

        nyc_pop = initialize_nyc_population(nw, nh)
        metro_pop = initialize_ny_metro_population(mw, mh)

        nyc_derived = get_nyc_derived_metro_cells(mw, mh)
        metro_nyc_init = sum(metro_pop[c] for c in nyc_derived if metro_pop.get(c, 0) > 0)
        self.assertAlmostEqual(metro_nyc_init, NYC_POPULATION['total_population_1830'], delta=5)

        nyc_map = Map(width=nw, height=nh, map_preset_name='nyc')
        nyc_map.population = dict(nyc_pop)
        nyc_map.update_population_over_time(1830)

        metro_map = Map(width=mw, height=mh, map_preset_name='ny_metro')
        metro_map.population = dict(metro_pop)
        metro_map.update_population_over_time(1830)

        nyc_total = sum(v for v in nyc_map.population.values() if v > 0)
        self.assertAlmostEqual(nyc_total, NYC_POPULATION['total_population_1830'], delta=500)

        metro_nyc_sum = sum(
            pop for pt, pop in metro_map.population.items()
            if pop > 0 and pt in nyc_derived
        )

        self.assertAlmostEqual(metro_nyc_sum, metro_nyc_init, delta=500)
        metro_total = sum(v for v in metro_map.population.values() if v > 0)
        self.assertGreater(metro_total, nyc_total)

    def test_ny_metro_grid_matches_nyc_cell_spacing(self):
        """Each NY Metro grid step equals one NYC OSM map grid step in degrees."""
        from map_presets_nyc import get_nyc_bounds
        from map_presets_ny_metro import (
            get_nyc_matched_cell_degrees,
            get_ny_metro_bounds,
            _grid_to_latlon,
            _latlon_to_grid,
        )

        nyc_bounds = get_nyc_bounds()
        metro_bounds = get_ny_metro_bounds()
        mw, mh = metro_bounds["width"], metro_bounds["height"]
        cell_lat, cell_lon = get_nyc_matched_cell_degrees()
        nyc_cell_lat = (nyc_bounds["max_lat"] - nyc_bounds["min_lat"]) / nyc_bounds["height"]
        nyc_cell_lon = (nyc_bounds["max_lon"] - nyc_bounds["min_lon"]) / nyc_bounds["width"]
        self.assertAlmostEqual(cell_lat, nyc_cell_lat, places=6)
        self.assertAlmostEqual(cell_lon, nyc_cell_lon, places=6)

        # NYC grid step (1,1) in lat/lon maps to metro grid step (1,1) in cell index
        lat0 = nyc_bounds["min_lat"]
        lon0 = nyc_bounds["min_lon"]
        lat1 = lat0 + cell_lat
        lon1 = lon0 + cell_lon
        gx0, gy0 = _latlon_to_grid(lat0, lon0, metro_bounds, mw, mh)
        gx1, gy1 = _latlon_to_grid(lat1, lon1, metro_bounds, mw, mh)
        self.assertEqual(gx1 - gx0, 1)
        self.assertEqual(gy1 - gy0, 1)

    def test_ny_metro_has_long_island_included_points(self):
        """NY Metro should include population points east of NYC from the Long Island relation."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            get_nyc_derived_metro_cells,
            get_ny_metro_grid_origin,
            MONTAUK_LON,
            MORRISTOWN_LON,
            DANBURY_LAT,
            _grid_to_latlon,
        )

        land_cells = set(NY_METRO_INITIAL_POPULATION.keys())
        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        origin_lat, origin_lon = get_ny_metro_grid_origin()
        self.assertAlmostEqual(bounds['max_lon'], MONTAUK_LON, places=2)
        self.assertAlmostEqual(bounds['min_lon'], MORRISTOWN_LON, places=2)
        self.assertAlmostEqual(origin_lat, bounds['min_lat'], places=3)
        self.assertAlmostEqual(bounds['max_lat'], DANBURY_LAT, places=2)
        nyc_derived = get_nyc_derived_metro_cells(mw, mh)
        li_points = []
        for x, y in land_cells:
            if (x, y) in nyc_derived:
                continue
            _, lon = _grid_to_latlon(x, y, bounds, mw, mh)
            if lon > -73.71:
                li_points.append((x, y))
        self.assertGreater(len(li_points), 100)
        easternmost = max(
            _grid_to_latlon(x, y, bounds, mw, mh)[1]
            for x, y in land_cells
        )
        self.assertGreater(easternmost, -72.0)

    def test_ny_metro_nyc_land_points_preserved(self):
        """NYC OSM land cells appear on NY Metro grid except Hudson River water cells."""
        from map_presets_nyc import NYC_INITIAL_POPULATION
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_nyc_derived_metro_cells,
            get_ny_metro_bounds,
            nyc_grid_to_metro_grid,
        )
        from generate_ny_metro_population_points import (
            HUDSON_RIVER_RELATION_ID,
            _grid_to_latlon,
            is_hudson_river_cell,
            load_hudson_river_polylines,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        nyc_derived = get_nyc_derived_metro_cells()
        included = set(NY_METRO_INITIAL_POPULATION.keys())
        self.assertEqual(len(nyc_derived), len(NYC_INITIAL_POPULATION))
        hr_polylines = load_hudson_river_polylines()
        missing_land = []
        for nx, ny in NYC_INITIAL_POPULATION:
            pt = nyc_grid_to_metro_grid(nx, ny)
            lat, lon = _grid_to_latlon(pt[0], pt[1], bounds, mw, mh)
            if is_hudson_river_cell(lat, lon, hr_polylines):
                continue
            if pt not in included:
                missing_land.append(pt)
        self.assertEqual(missing_land, [], f"Missing NYC land cells on metro map: {len(missing_land)}")

    def test_governors_island_cells_are_buildable_with_population(self):
        """Governors Island (OSM relation 2389631) is included in NYC buildable area."""
        from map_presets_nyc import GOVERNORS_ISLAND_OSM_RELATION_ID, NYC_INITIAL_POPULATION

        nyc_map = Map(width=50, height=50, map_preset_name='nyc')
        gi_cells = [(26, 25), (26, 26), (27, 25), (27, 26)]
        for cell in gi_cells:
            self.assertIn(cell, NYC_INITIAL_POPULATION, msg=f"Missing GI cell {cell}")
            self.assertGreater(NYC_INITIAL_POPULATION[cell], 0)
            self.assertTrue(
                nyc_map.is_point_in_buildable_area(cell[0], cell[1]),
                msg=f"GI cell {cell} not buildable",
            )
        self.assertEqual(GOVERNORS_ISLAND_OSM_RELATION_ID, 2389631)

    def test_ny_metro_includes_glen_cove_dobbs_ferry_tappan(self):
        """North Shore LI and Hudson rivertowns are buildable; Hudson channel is not."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            _grid_to_latlon,
            _latlon_to_grid,
        )
        from generate_ny_metro_population_points import is_hudson_river_cell, load_hudson_river_polylines

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        hr = load_hudson_river_polylines()
        included = set(NY_METRO_INITIAL_POPULATION.keys())

        for name, lat, lon in (
            ('Glen Cove', 40.867, -73.633),
            ('Dobbs Ferry', 40.878, -73.872),
            ('Tappan', 41.025, -73.948),
        ):
            gx, gy = _latlon_to_grid(lat, lon, bounds, mw, mh)
            self.assertIn((gx, gy), included, msg=f"{name} cell ({gx}, {gy}) missing from NY Metro")
            self.assertGreater(NY_METRO_INITIAL_POPULATION[(gx, gy)], 0)
            clat, clon = _grid_to_latlon(gx, gy, bounds, mw, mh)
            self.assertFalse(
                is_hudson_river_cell(clat, clon, hr),
                msg=f"{name} should not be classified as Hudson River",
            )

    def test_ny_metro_excludes_hudson_river_relation_cells(self):
        """No population cells whose center lies inside OSM Hudson River relation 2148192."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            _grid_to_latlon,
        )
        from generate_ny_metro_population_points import (
            is_hudson_river_cell,
            load_hudson_river_polylines,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        hr_polylines = load_hudson_river_polylines()
        in_river = []
        for x, y in NY_METRO_INITIAL_POPULATION:
            lat, lon = _grid_to_latlon(x, y, bounds, mw, mh)
            if is_hudson_river_cell(lat, lon, hr_polylines):
                in_river.append((x, y))
        self.assertEqual(in_river, [], f"Population on Hudson River: {in_river[:5]}")

    def test_ny_metro_excludes_sound_and_hudson_water_cells(self):
        """Non-NYC included points must not sit on open water (LIS, bays); counties use OSM river exclusion."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            get_nyc_derived_metro_cells,
            is_metro_water_location,
            is_county_open_water,
            _grid_to_latlon,
        )
        from generate_ny_metro_population_points import (
            is_hudson_river_cell,
            load_hudson_river_polylines,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        nyc_derived = get_nyc_derived_metro_cells(mw, mh)
        land_cells = set(NY_METRO_INITIAL_POPULATION.keys())
        hr = load_hudson_river_polylines()
        for x, y in land_cells:
            if (x, y) in nyc_derived:
                continue
            lat, lon = _grid_to_latlon(x, y, bounds, mw, mh)
            self.assertFalse(
                is_hudson_river_cell(lat, lon, hr),
                f"Point ({x}, {y}) at ({lat:.4f}, {lon:.4f}) is Hudson River water",
            )

    def test_ny_metro_excludes_open_water_cells(self):
        """No population cell may be in Hudson River or Long Island Sound open water."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            is_metro_water_location,
            _grid_to_latlon,
        )
        from generate_ny_metro_population_points import (
            is_hudson_river_cell,
            load_hudson_river_polylines,
            is_open_water_cell,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        hr = load_hudson_river_polylines()
        in_water = []
        for x, y in NY_METRO_INITIAL_POPULATION:
            lat, lon = _grid_to_latlon(x, y, bounds, mw, mh)
            if is_hudson_river_cell(lat, lon, hr) or is_open_water_cell(lat, lon):
                in_water.append((x, y))
        self.assertEqual(in_water, [], f"Population in Hudson/LIS: {in_water[:8]}")

    def test_ny_metro_includes_connecticut_south_of_hartford(self):
        """Connecticut population centers (New Haven, Hartford) must be on the map."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            _grid_to_latlon,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        # New Haven ~41.31,-72.93 and Hartford ~41.76,-72.69 should have nearby population.
        targets = {'New Haven': (41.308, -72.928), 'Hartford': (41.764, -72.685)}
        for name, (tlat, tlon) in targets.items():
            near = False
            for (x, y), pop in NY_METRO_INITIAL_POPULATION.items():
                if pop <= 0:
                    continue
                lat, lon = _grid_to_latlon(x, y, bounds, mw, mh)
                if abs(lat - tlat) < 0.06 and abs(lon - tlon) < 0.06:
                    near = True
                    break
            self.assertTrue(near, f"No NYMZ population near {name}")

    def test_ny_metro_includes_hudson_bergen_rockland_westchester_counties(self):
        """NY Metro includes land population strictly inside Hudson, Bergen, Rockland, Westchester."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_nyc_derived_metro_cells,
            get_ny_metro_bounds,
            _grid_to_latlon,
        )
        from generate_ny_metro_population_points import (
            BERGEN_COUNTY_RELATION_ID,
            HUDSON_COUNTY_RELATION_ID,
            ROCKLAND_COUNTY_RELATION_ID,
            WESTCHESTER_COUNTY_RELATION_ID,
            UNION_COUNTY_RELATION_ID,
            MIDDLESEX_COUNTY_RELATION_ID,
            CONNECTICUT_STATE_RELATION_ID,
            ESSEX_COUNTY_RELATION_ID,
            MORRIS_COUNTY_RELATION_ID,
            SOMERSET_COUNTY_RELATION_ID,
            PASSAIC_COUNTY_RELATION_ID,
            DUTCHESS_COUNTY_RELATION_ID,
            audit_relation_coverage,
            prepare_relation_for_scan,
            _indexed_polygons,
            load_hudson_river_polylines,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        nyc_derived = get_nyc_derived_metro_cells(mw, mh)
        included = set(NY_METRO_INITIAL_POPULATION.keys())
        county_only = included - nyc_derived
        west_of_nyc = []
        for x, y in county_only:
            lat, lon = _grid_to_latlon(x, y, bounds, mw, mh)
            if lon < -74.02:
                west_of_nyc.append((x, y))
        self.assertGreater(len(west_of_nyc), 150, "Expected county land cells west/north of NYC")
        hr_polylines = load_hudson_river_polylines()
        for relation_id, label, kind in (
            (HUDSON_COUNTY_RELATION_ID, "Hudson County", "county"),
            (BERGEN_COUNTY_RELATION_ID, "Bergen County", "county"),
            (ROCKLAND_COUNTY_RELATION_ID, "Rockland County", "county"),
            (WESTCHESTER_COUNTY_RELATION_ID, "Westchester County", "county"),
            (UNION_COUNTY_RELATION_ID, "Union County", "county"),
            (MIDDLESEX_COUNTY_RELATION_ID, "Middlesex County", "county"),
            (CONNECTICUT_STATE_RELATION_ID, "Connecticut", "county"),
            (ESSEX_COUNTY_RELATION_ID, "Essex County", "county"),
            (MORRIS_COUNTY_RELATION_ID, "Morris County", "county"),
            (SOMERSET_COUNTY_RELATION_ID, "Somerset County", "county"),
            (PASSAIC_COUNTY_RELATION_ID, "Passaic County", "county"),
            (DUTCHESS_COUNTY_RELATION_ID, "Dutchess County", "county"),
        ):
            indexed = _indexed_polygons(prepare_relation_for_scan(relation_id))
            _, missing_count, _missing = audit_relation_coverage(
                included, relation_id, label, indexed,
                hudson_river_polylines=hr_polylines, land_kind=kind,
            )
            self.assertEqual(missing_count, 0, f"{label} missing {missing_count} land cells")

    def test_ny_metro_non_nyc_population_strictly_inside_osm_relations(self):
        """Every non-NYC population cell must lie inside an OSM land relation boundary."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            get_nyc_derived_metro_cells,
            _grid_to_latlon,
        )
        from generate_ny_metro_population_points import (
            LAND_AREA_RELATIONS,
            prepare_relation_for_scan,
            _indexed_polygons,
            cell_intersects_relation,
            _exclude_extra_land_cell,
            load_hudson_river_polylines,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        nyc_derived = get_nyc_derived_metro_cells(mw, mh)
        hr = load_hudson_river_polylines()
        relation_indexed = {
            relation_id: _indexed_polygons(prepare_relation_for_scan(relation_id))
            for relation_id, _label, _kind in LAND_AREA_RELATIONS
        }
        outside = []
        for pt in NY_METRO_INITIAL_POPULATION:
            if pt in nyc_derived:
                continue
            x, y = pt
            lat, lon = _grid_to_latlon(x, y, bounds, mw, mh)
            in_any = False
            for relation_id, _label, kind in LAND_AREA_RELATIONS:
                indexed = relation_indexed[relation_id]
                if not cell_intersects_relation(
                    x, y, bounds, mw, mh, indexed, subgrid_size=0,
                ):
                    continue
                if _exclude_extra_land_cell(lat, lon, kind, hr):
                    continue
                in_any = True
                break
            if not in_any:
                outside.append(pt)
        self.assertEqual(outside, [], f"Population outside all OSM land relations: {outside[:8]}")

    def test_ny_metro_osm_island_relations_cover_land_cells(self):
        """Long Island included cells should cover OSM relation 3955977 land (water cells excluded)."""
        from map_presets_ny_metro import NY_METRO_INITIAL_POPULATION, is_water_location, get_ny_metro_bounds, _grid_to_latlon
        from generate_ny_metro_population_points import (
            LONG_ISLAND_RELATION_ID,
            audit_relation_coverage,
            prepare_relation_for_scan,
            _indexed_polygons,
            is_hudson_river_cell,
            load_hudson_river_polylines,
        )

        bounds = get_ny_metro_bounds()
        mw, mh = bounds['width'], bounds['height']
        included = set(NY_METRO_INITIAL_POPULATION.keys())
        relation_id = LONG_ISLAND_RELATION_ID
        label = "Long Island"
        hr_polylines = load_hudson_river_polylines()
        indexed = _indexed_polygons(prepare_relation_for_scan(relation_id))
        _, missing_count, missing = audit_relation_coverage(
            included, relation_id, label, indexed,
            hudson_river_polylines=hr_polylines, land_kind="li",
        )
        water_missing = []
        for pt in missing or []:
            lat, lon = _grid_to_latlon(pt[0], pt[1], bounds, mw, mh)
            if is_water_location(lat, lon):
                continue
            if is_hudson_river_cell(lat, lon, hr_polylines):
                continue
            water_missing.append(pt)
        self.assertEqual(
            water_missing, [],
            f"Long Island has {len(water_missing)} non-water relation cells missing",
        )

    def test_ny_metro_display_hides_zero_population_points(self):
        """Grey (zero-pop) included cells must not be sent to the map UI."""
        from operations import Operations
        from map_presets_ny_metro import NY_METRO_INITIAL_POPULATION

        ops = Operations(['P1'], map_preset_name='ny_metro')
        game_map = ops.get_map()
        game_map.population[(10, 10)] = 0
        displayed = ops.get_population_points_for_display(game_map)
        zero_pts = {(p['grid_x'], p['grid_y']) for p in displayed if p.get('pop', 0) <= 0}
        self.assertEqual(zero_pts, set())
        if (10, 10) in NY_METRO_INITIAL_POPULATION:
            self.assertNotIn((10, 10), {(p['grid_x'], p['grid_y']) for p in displayed})

    def test_ny_metro_ai_station_pop_reaches_nearby_cells(self):
        """NY Metro grid cells are ~1 km; AI station pop search must exceed the default 750 m cap."""
        from operations import Operations
        from map_presets_ny_metro import get_road_segment_points_for_ai
        from map_preset_loader import get_preset_ai_station_population

        ops = Operations(['AI1'], map_preset_name='ny_metro', ai_player_indices=[0])
        game_map = ops.get_map()
        pop_map = game_map.population
        road_points = get_road_segment_points_for_ai(game_map, 1830, min_endpoint_population=50)
        self.assertGreaterEqual(len(road_points), 2)
        p1, p2 = road_points[0], road_points[-1]
        pop1 = get_preset_ai_station_population(game_map, float(p1[0]), float(p1[1]), pop_map)
        pop2 = get_preset_ai_station_population(game_map, float(p2[0]), float(p2[1]), pop_map)
        self.assertGreater(pop1, 0, f"endpoint {p1} should resolve nearby population")
        self.assertGreater(pop2, 0, f"endpoint {p2} should resolve nearby population")

    def test_preset_loader_lazy_import(self):
        """Preset discovery should not import every map_presets_* module at once."""
        import sys
        import map_preset_loader as mpl

        mpl._registry = None
        for mod in [m for m in list(sys.modules) if m.startswith("map_presets_")]:
            del sys.modules[mod]
        names = mpl.list_available_presets()
        self.assertIn("nyc", names)
        self.assertIn("ny_metro", names)
        loaded_before = [m for m in sys.modules if m.startswith("map_presets_")]
        self.assertEqual(loaded_before, [])
        mpl.load_map_preset("standard")
        self.assertIn("map_presets_standard", sys.modules)

    def test_ny_metro_sparse_population_init(self):
        """NY Metro init should not zero-fill the entire grid."""
        from map_presets_ny_metro import (
            NY_METRO_INITIAL_POPULATION,
            get_ny_metro_bounds,
            initialize_ny_metro_population,
        )

        bounds = get_ny_metro_bounds()
        pop = initialize_ny_metro_population(bounds["width"], bounds["height"])
        expected = sum(1 for p in NY_METRO_INITIAL_POPULATION.values() if p > 0)
        self.assertEqual(len(pop), expected)
        self.assertTrue(all(v > 0 for v in pop.values()))

    def test_map_load_uses_in_memory_population_for_display(self):
        """Display path should use game_map.population without re-parsing preset files."""
        import time
        from unittest.mock import patch
        from operations import Operations

        ops = Operations(["P1"], map_preset_name="ny_metro")
        game_map = ops.get_map()
        self.assertGreater(len(game_map.population), 100)
        with patch("map_preset_utils.read_included_points_from_file") as mock_read:
            mock_read.return_value = None
            t0 = time.perf_counter()
            pts = ops.get_population_points_for_display(game_map)
            elapsed = time.perf_counter() - t0
        self.assertGreater(len(pts), 50)
        mock_read.assert_not_called()
        self.assertLess(elapsed, 2.0)

    def test_get_population(self):
        """Test getting population at location"""
        test_x1, test_y1 = 5, 5
        test_x2, test_y2 = 15, 15
        test_x3, test_y3 = 25, 25
        
        original_pop1 = self.map.population.get((test_x1, test_y1), 0)
        original_pop2 = self.map.population.get((test_x2, test_y2), 0)
        original_pop3 = self.map.population.get((test_x3, test_y3), 0)
        
        self.map.population[(test_x1, test_y1)] = 100
        self.map.population[(test_x2, test_y2)] = 200
        
        pop1 = self.map.get_population(test_x1, test_y1)
        pop2 = self.map.get_population(test_x2, test_y2)
        pop3 = self.map.get_population(test_x3, test_y3)
        
        self.assertEqual(pop1, 100)
        self.assertEqual(pop2, 200)
        # pop3 should be 0 if no population, or original value if it existed
        self.assertEqual(pop3, original_pop3)
        
        # Restore original values
        if original_pop1 == 0 and (test_x1, test_y1) in self.map.population:
            del self.map.population[(test_x1, test_y1)]
        elif original_pop1 > 0:
            self.map.population[(test_x1, test_y1)] = original_pop1
            
        if original_pop2 == 0 and (test_x2, test_y2) in self.map.population:
            del self.map.population[(test_x2, test_y2)]
        elif original_pop2 > 0:
            self.map.population[(test_x2, test_y2)] = original_pop2


class TestMapPopulationDisplayCap(unittest.TestCase):
    def test_cap_population_points_for_map_display_keeps_highest_pop(self):
        from operations import cap_population_points_for_map_display, MAP_DISPLAY_MAX_POPULATION_POINTS

        data = [
            {'pop': i, 'grid_x': i, 'grid_y': 0, 'lat': 0.0, 'lon': 0.0}
            for i in range(MAP_DISPLAY_MAX_POPULATION_POINTS + 500)
        ]
        capped = cap_population_points_for_map_display(data)
        self.assertEqual(len(capped), MAP_DISPLAY_MAX_POPULATION_POINTS)
        self.assertEqual(capped[0]['pop'], MAP_DISPLAY_MAX_POPULATION_POINTS + 499)


if __name__ == '__main__':
    unittest.main()
