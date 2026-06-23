"""Unit tests for AI street-based line building.

Verifies that:
- On NYC/NY Metro maps, AI uses street polylines (never population-based straight chords for year-1 OSM openers).
- On standard map, AI uses population-based building when no street data exists.
- When preset has road capability but road points are empty, AI returns None (no fallback).
- AI uses above_ground for segments in water (prevents "Cannot build at street level on water" error).
"""

import unittest
from unittest.mock import MagicMock, patch

from ai import _try_build_line, _get_safe_elevation_levels, _generate_ai_line_name, MIN_LINE_LENGTH_GRID
from map_preset_loader import list_available_presets


class TestAITryBuildLineStreetVsPopulation(unittest.TestCase):
    """Test _try_build_line uses streets on road-capable presets and never falls back."""

    def _make_mock_ops(self):
        """Create mock ops that captures create_line_complete calls."""
        ops = MagicMock()
        ops.create_line_complete.return_value = {'success': True}
        ops.get_map.return_value = None  # AI uses ops.get_map() first; tests set this to their map when needed
        game = MagicMock()
        game.ai_min_population = 100  # Allow tests with lower population (500, 400)
        ops.get_game.return_value = game
        return ops

    def _make_mock_player(self, name="AI_Player"):
        """Create mock player."""
        p = MagicMock()
        p.get_name.return_value = name
        return p

    def _make_mock_map(self, preset_name=None, width=50, height=50):
        """Create mock game map."""
        m = MagicMock()
        m.map_preset_name = preset_name
        m.width = width
        m.height = height
        m.population = {}
        return m

    @patch('map_preset_loader.get_preset_ai_station_population', return_value=2000)
    @patch('ai._get_safe_elevation_levels')
    @patch('map_preset_loader.load_map_preset')
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_nyc_preset_uses_street_polylines_not_population(
        self, mock_get_road, mock_load_preset, mock_elev, _mock_preset_pop
    ):
        """On NYC preset, AI builds on street polylines; never uses population points."""
        mock_elev.return_value = ['street', 'street', 'street']  # 3 segments for 4 points
        # Preset has road capability
        mock_preset = MagicMock()
        mock_preset.get_road_segment_points_for_ai = MagicMock()
        mock_load_preset.return_value = mock_preset

        # Street polyline: 4 points along a road (not straight between 2 population points)
        street_points = [(10.0, 20.0), (12.0, 21.0), (14.0, 22.0), (16.0, 23.0)]
        mock_get_road.return_value = street_points

        ops = self._make_mock_ops()
        ops.get_build_queue.return_value = []
        ops.get_player_money.return_value = 50000  # For _add_ai_endpoint_stations
        ops.create_station_complete.return_value = {'remaining_money': 1000}
        player = self._make_mock_player()
        player.get_line_names.return_value = []  # First line for this player
        game_map = self._make_mock_map(preset_name='nyc')
        # Population at endpoints >= 1000 (min_pop_road) so build succeeds
        game_map.population = {(10, 20): 1500, (16, 23): 1200}
        ops.get_map.return_value = game_map

        result = _try_build_line(ops, player, game_map, spendable=50000, current_year=1835)

        self.assertIsNotNone(result)
        mock_get_road.assert_called()
        call = mock_get_road.call_args
        self.assertEqual(call[0][0], 'nyc')
        self.assertEqual(call[1].get('max_points'), 8)
        self.assertEqual(call[1].get('existing_line_routes'), [])
        # min_length_grid: must be > 0.5 (500m) - endpoint-to-endpoint > 500m
        self.assertGreaterEqual(call[1].get('min_length_grid'), MIN_LINE_LENGTH_GRID)
        self.assertGreater(MIN_LINE_LENGTH_GRID, 0.5)
        # Must have been called with street points (4 points), not 2 population points
        call_kw = ops.create_line_complete.call_args
        self.assertIsNotNone(call_kw)
        points = call_kw[0][1]  # second positional arg is points
        self.assertEqual(len(points), 4)
        self.assertEqual(points, street_points)

    @patch('map_preset_loader.load_map_preset')
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_nyc_preset_empty_road_points_raises_error_no_fallback(
        self, mock_get_road, mock_load_preset
    ):
        """When preset has road capability but road points empty, AI returns None (no population fallback)."""
        mock_preset = MagicMock()
        mock_preset.get_road_segment_points_for_ai = MagicMock()
        mock_load_preset.return_value = mock_preset
        mock_get_road.return_value = []  # empty

        ops = self._make_mock_ops()
        player = self._make_mock_player()
        game_map = self._make_mock_map(preset_name='nyc')

        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1835,
            points_with_pop=[((5, 10), 100), ((20, 30), 200)]  # would allow population build
        )
        self.assertIsNone(result)
        # Fallback chain tries 1000, 800, 100, 1, 0 - all return empty
        self.assertGreaterEqual(mock_get_road.call_count, 1)
        ops.create_line_complete.assert_not_called()

    @patch('map_preset_loader.load_map_preset')
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_nyc_preset_single_road_point_raises_error_no_fallback(
        self, mock_get_road, mock_load_preset
    ):
        """When road points has only 1 point, AI returns None (no population fallback)."""
        mock_preset = MagicMock()
        mock_preset.get_road_segment_points_for_ai = MagicMock()
        mock_load_preset.return_value = mock_preset
        mock_get_road.return_value = [(10.0, 20.0)]

        ops = self._make_mock_ops()
        player = self._make_mock_player()
        game_map = self._make_mock_map(preset_name='nyc')

        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1835,
            points_with_pop=[((5, 10), 100), ((20, 30), 200)]
        )
        self.assertIsNone(result)
        ops.create_line_complete.assert_not_called()

    @patch('map_preset_loader.load_map_preset')
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_nyc_preset_exception_raises_error_no_fallback(
        self, mock_get_road, mock_load_preset
    ):
        """When get_preset_road_segment_points raises, AI returns None (no population fallback)."""
        mock_preset = MagicMock()
        mock_preset.get_road_segment_points_for_ai = MagicMock()
        mock_load_preset.return_value = mock_preset
        mock_get_road.side_effect = RuntimeError("OSM error")

        ops = self._make_mock_ops()
        player = self._make_mock_player()
        game_map = self._make_mock_map(preset_name='nyc')

        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1835,
            points_with_pop=[((5, 10), 100), ((20, 30), 200)]
        )
        self.assertIsNone(result)
        ops.create_line_complete.assert_not_called()

    @patch('ai._get_safe_elevation_levels')
    @patch('map_preset_loader.preset_has_water_areas')
    @patch('map_preset_loader.load_map_preset')
    def test_standard_preset_uses_population_points(self, mock_load, mock_water, mock_elev):
        """On standard preset (no road capability), AI uses population-based building."""
        mock_water.return_value = False  # No water filtering
        mock_elev.return_value = ['street']  # 1 segment for 2 points
        # Preset without get_road_segment_points_for_ai -> AI uses population branch
        mock_preset = object()  # no road capability
        mock_load.return_value = mock_preset

        ops = self._make_mock_ops()
        ops.get_player_money.return_value = 50000  # For _add_ai_endpoint_stations
        ops.create_station_complete.return_value = {'remaining_money': 1000}
        player = self._make_mock_player()
        game_map = self._make_mock_map(preset_name='standard')
        game_map.population = {(5, 10): 600, (15, 12): 500}
        ops.get_map.return_value = game_map

        points_with_pop = [((5, 10), 500), ((15, 12), 400)]

        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1835,
            points_with_pop=points_with_pop
        )

        # Should attempt population-based build (2 points)
        self.assertIsNotNone(result)
        call_kw = ops.create_line_complete.call_args
        self.assertIsNotNone(call_kw)
        points = call_kw[0][1]
        self.assertEqual(len(points), 2)
        self.assertIn(points[0], [(5, 10), (15, 12)])
        self.assertIn(points[1], [(5, 10), (15, 12)])

    @patch('map_preset_loader.get_preset_ai_station_population', return_value=2000)
    @patch('ai._get_safe_elevation_levels')
    @patch('map_preset_loader.load_map_preset')
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_nyc_preset_passes_existing_routes_to_avoid_overlap(
        self, mock_get_road, mock_load_preset, mock_elev, _mock_preset_pop
    ):
        """AI passes existing line routes so preset can exclude overlapping roads."""
        mock_elev.return_value = ['street']
        mock_preset = MagicMock()
        mock_preset.get_road_segment_points_for_ai = MagicMock()
        mock_load_preset.return_value = mock_preset
        # Return a different road (non-overlapping)
        mock_get_road.return_value = [(30.0, 35.0), (32.0, 36.0)]

        ops = self._make_mock_ops()
        ops.get_player_money.return_value = 50000
        ops.create_station_complete.return_value = {'remaining_money': 1000}
        player = self._make_mock_player()
        game_map = self._make_mock_map(preset_name='nyc')
        game_map.population = {(30, 35): 1500, (32, 36): 1200}  # >= 1000 for min_pop_road
        # Simulate existing line on the map
        mock_line = MagicMock()
        mock_line.get_route.return_value = [(10, 20), (12, 21), (14, 22)]
        game_map.get_lines_by_name = MagicMock(return_value={'Existing_Line': mock_line})
        ops.get_map.return_value = game_map

        result = _try_build_line(ops, player, game_map, spendable=50000, current_year=1835)

        self.assertIsNotNone(result)
        # Should have been called with existing_line_routes containing the existing line's route
        call_kw = mock_get_road.call_args
        existing = call_kw[1].get('existing_line_routes', [])
        self.assertEqual(len(existing), 1)
        self.assertEqual(len(existing[0]), 3)

    def test_first_line_requires_min_1km_length(self):
        """First line must be at least 1km (1.0 grid units)."""
        ops = self._make_mock_ops()
        player = self._make_mock_player()
        player.get_line_names.return_value = []
        game_map = self._make_mock_map(preset_name=None)
        game_map.population = {(5, 10): 500}
        # Points very close together - would be < 0.5 grid units
        points_with_pop = [((5, 10), 500), ((5, 10.3), 400)]  # dist ~0.3
        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1830,
            points_with_pop=points_with_pop
        )
        # Should not build - too short for first line
        self.assertIsNone(result)
        ops.create_line_complete.assert_not_called()

    def test_first_line_builds_when_long_enough(self):
        """First line builds when distance >= 1.0 grid units (1km)."""
        ops = self._make_mock_ops()
        ops.get_player_money.return_value = 50000
        ops.create_station_complete.return_value = {'remaining_money': 1000}
        player = self._make_mock_player()
        player.get_line_names.return_value = []
        game_map = self._make_mock_map(preset_name=None)
        game_map.population = {(5, 10): 500, (15, 20): 400}
        ops.get_map.return_value = game_map
        # Points far enough apart
        points_with_pop = [((5, 10), 500), ((15, 20), 400)]  # dist ~14
        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1830,
            points_with_pop=points_with_pop
        )
        self.assertIsNotNone(result)
        ops.create_line_complete.assert_called_once()

    def test_no_preset_name_uses_population_points(self):
        """When map has no preset_name, AI uses population-based building."""
        ops = self._make_mock_ops()
        player = self._make_mock_player()
        game_map = self._make_mock_map(preset_name=None)
        game_map.population = {(5, 10): 500, (15, 12): 400}

        points_with_pop = [((5, 10), 500), ((15, 12), 400)]

        result = _try_build_line(
            ops, player, game_map, spendable=50000, current_year=1835,
            points_with_pop=points_with_pop
        )

        if result is not None:
            call_kw = ops.create_line_complete.call_args
            self.assertIsNotNone(call_kw)
            points = call_kw[0][1]
            self.assertEqual(len(points), 2)

    def test_ai_route_min_length_more_than_500m(self):
        """AI route endpoint-to-endpoint must be more than 500m (MIN_LINE_LENGTH_GRID > 0.5)."""
        self.assertGreater(MIN_LINE_LENGTH_GRID, 0.5)
        self.assertLessEqual(MIN_LINE_LENGTH_GRID, 1.0)

    @patch('ai._get_safe_elevation_levels')
    @patch('map_preset_loader.load_map_preset')
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_road_preset_rejects_routes_shorter_than_500m(
        self, mock_get_road, mock_load_preset, mock_elev
    ):
        """Road preset: routes with total_length < MIN_LINE_LENGTH_GRID are rejected."""
        mock_elev.return_value = ['street']
        mock_preset = MagicMock()
        mock_preset.get_road_segment_points_for_ai = MagicMock()
        mock_load_preset.return_value = mock_preset
        # Very short road: 2 points 0.4 units apart (< 0.55)
        short_points = [(10.0, 20.0), (10.4, 20.0)]
        mock_get_road.return_value = short_points

        ops = self._make_mock_ops()
        ops.get_build_queue.return_value = []
        player = self._make_mock_player()
        player.get_line_names.return_value = []
        game_map = self._make_mock_map(preset_name='nyc')
        game_map.population = {(10, 20): 2000}
        ops.get_map.return_value = game_map

        result = _try_build_line(ops, player, game_map, spendable=50000, current_year=1835)

        # total_length 0.4 < MIN_LINE_LENGTH_GRID (0.55) -> should return None
        self.assertIsNone(result)


class TestAIManhattanBuildExplanation(unittest.TestCase):
    """Documentation for Manhattan vs AI road selection on OSM NYC.

    Previously, AIs rarely built in lower/midtown Manhattan because population lookup used a tight
    grid radius (2) everywhere. OSM street vertices often fall *between* sparse population keys, so
    endpoints and sampled polyline points read as zero population even though the 6x6 density cell
    is high — filters rejected those roads.

    Fix (map_presets_nyc_road_utils): inside a Manhattan lat/lon bbox, AI road scoring uses a wider
    search radius (_MANHATTAN_POP_SEARCH_RADIUS) via _ai_pop_at_road_point so endpoints and
    _road_has_population_along_route align with actual ridership corridors without letting distant
    waterfront roads borrow population across the harbor."""

    def test_min_endpoint_population_filters_low_pop_roads(self):
        """Roads with endpoint population < min_endpoint_population are skipped (explains Manhattan gaps)."""
        from map_presets_nyc_road_utils import _population_at_endpoint_strict
        pop_map = {(10, 20): 500, (15, 25): 300}  # Both below 1000
        self.assertLess(_population_at_endpoint_strict(10.0, 20.0, pop_map), 1000)
        self.assertLess(_population_at_endpoint_strict(15.0, 25.0, pop_map), 1000)

    def test_road_requires_population_along_route(self):
        """_road_has_population_along_route requires min_frac of points to have population."""
        from map_presets_nyc_road_utils import _road_has_population_along_route
        game_map = MagicMock()
        game_map.population = {(10, 20): 100, (12, 21): 0, (14, 22): 0, (16, 23): 100}
        # 4 points: 2 have pop (10,20) and (16,23) -> 50%. min_frac=0.4 needs 2 points. Passes.
        points = [(10.0, 20.0), (12.0, 21.0), (14.0, 22.0), (16.0, 23.0)]
        result = _road_has_population_along_route(points, game_map, min_frac=0.4)
        self.assertTrue(result)
        # Route through empty area: pop only at (10,20); points (30,40), (31,41)... are far, no pop
        points_sparse = [(30.0, 40.0), (31.0, 41.0), (32.0, 42.0), (33.0, 43.0), (34.0, 44.0)]
        game_map.population = {(10, 20): 100}  # Pop far from route
        result_sparse = _road_has_population_along_route(points_sparse, game_map, min_frac=0.4)
        self.assertFalse(result_sparse)  # 0/5 = 0% < 40%


class TestGetPresetRoadSegmentPoints(unittest.TestCase):
    """Test map_preset_loader.get_preset_road_segment_points for street-capable presets."""

    def test_nyc_preset_has_road_capability(self):
        """NYC preset exposes get_road_segment_points_for_ai."""
        from map_preset_loader import load_map_preset

        preset = load_map_preset('nyc')
        self.assertIsNotNone(preset)
        self.assertTrue(hasattr(preset, 'get_road_segment_points_for_ai'))

    def test_ny_metro_preset_has_road_capability(self):
        """NY Metro preset exposes get_road_segment_points_for_ai."""
        from map_preset_loader import load_map_preset

        preset = load_map_preset('ny_metro')
        self.assertIsNotNone(preset)
        self.assertTrue(hasattr(preset, 'get_road_segment_points_for_ai'))

    def test_standard_preset_no_road_capability(self):
        """Standard preset does not have get_road_segment_points_for_ai."""
        from map_preset_loader import load_map_preset

        preset = load_map_preset('standard')
        self.assertIsNotNone(preset)
        self.assertFalse(hasattr(preset, 'get_road_segment_points_for_ai'))

    def test_get_preset_road_segment_points_nyc_returns_list(self):
        """get_preset_road_segment_points for NYC returns a list (may be empty if no OSM data)."""
        from map_preset_loader import get_preset_road_segment_points
        from map import Map

        game_map = Map(width=50, height=50, map_preset_name='nyc')
        points = get_preset_road_segment_points('nyc', game_map, 1835, max_points=12)

        self.assertIsInstance(points, list)
        # If OSM data exists, we may get points; otherwise []
        for pt in points:
            self.assertIsInstance(pt, (tuple, list))
            self.assertEqual(len(pt), 2)
            self.assertIsInstance(pt[0], (int, float))
            self.assertIsInstance(pt[1], (int, float))

    def test_get_preset_road_segment_points_standard_returns_empty(self):
        """get_preset_road_segment_points for standard returns []."""
        from map_preset_loader import get_preset_road_segment_points
        from map import Map

        game_map = Map(width=100, height=75, map_preset_name='standard')
        points = get_preset_road_segment_points('standard', game_map, 1835, max_points=12)

        self.assertEqual(points, [])


class TestAIWaterSegmentPrevention(unittest.TestCase):
    """Test that AI uses above_ground for segments in water to prevent year-advance error."""

    def test_get_safe_elevation_levels_uses_above_ground_for_water_segment(self):
        """When segment midpoint is in water, _get_safe_elevation_levels returns above_ground."""
        game_map = MagicMock()
        game_map.map_preset_name = 'nyc'
        game_map.tile_system = None
        with patch('map_preset_loader.preset_has_water_areas', return_value=True):
            with patch('map_preset_loader.preset_is_point_in_water', return_value=True):
                points = [(30.0, 29.0), (32.0, 28.0)]
                levels = _get_safe_elevation_levels(points, game_map, 'nyc', base='street')
                self.assertEqual(levels, ['above_ground'])
                self.assertIn('above_ground', levels)

    def test_get_safe_elevation_levels_uses_street_for_land_segment(self):
        """When segment is on land, _get_safe_elevation_levels returns street."""
        game_map = MagicMock()
        game_map.map_preset_name = 'nyc'
        game_map.tile_system = None
        with patch('map_preset_loader.preset_has_water_areas', return_value=True):
            with patch('map_preset_loader.preset_is_point_in_water', return_value=False):
                points = [(10.0, 20.0), (12.0, 21.0)]
                levels = _get_safe_elevation_levels(points, game_map, 'nyc', base='street')
                self.assertEqual(levels, ['street'])

    def test_generate_ai_line_name_no_ai_prefix(self):
        """AI line names never include 'AI' or line id - only display name."""
        player = MagicMock()
        player.get_line_names.return_value = []
        rng = __import__('random').Random(42)
        name = _generate_ai_line_name(player, rng)
        self.assertNotIn('AI', name)
        self.assertNotRegex(name, r'AI_.*_\d{3}')  # No AI_Player_123 pattern
        self.assertGreater(len(name), 0)

    def test_generate_ai_line_name_second_line_different(self):
        """Second line gets different name."""
        player = MagicMock()
        player.get_line_names.return_value = ['A']
        rng = __import__('random').Random(42)
        name = _generate_ai_line_name(player, rng)
        self.assertNotEqual(name, 'A')


class TestAINoPopulationChordYearOneOsMOpener(unittest.TestCase):
    """Year-1 OSM fast openers must stay on preset road geometry, not straight population-point chords."""

    def test_ai_has_no_emergency_population_pair_opener(self):
        import ai as ai_mod

        self.assertFalse(
            hasattr(ai_mod, '_osm_emergency_opener_from_population_pairs'),
            'Remove straight population-pair OSM openers; use preset road polylines (chain short edges if needed).',
        )
        self.assertFalse(
            hasattr(ai_mod, '_try_population_point_opener_line'),
            'Population-point opener fallback must not exist on OSM maps.',
        )

    @patch('map_preset_loader.get_preset_ai_station_population', return_value=2000)
    @patch('map_preset_loader.get_preset_road_segment_points')
    def test_nyc_three_ais_queue_street_based_first_lines(self, mock_get_road, _mock_preset_pop):
        """Three NYC AIs on 1830 must each queue a line from street sampling (not population chords)."""
        if 'nyc' not in list_available_presets():
            self.skipTest('NYC preset not available')
        import ai as ai_mod
        from game_presets import LINE_BUILD_SPEED_MULTIPLIER, STARTING_MONEY
        from operations import Operations, get_game_params_for_preset
        from ai import run_ai_turn

        def _street_for_player(*args, **kwargs):
            _street_for_player.calls += 1
            base = 10.0 + (_street_for_player.calls % 3) * 8.0
            return [
                (base, 20.0),
                (base + 2.0, 21.0),
                (base + 4.0, 22.0),
                (base + 6.0, 23.0),
            ]

        _street_for_player.calls = 0
        mock_get_road.side_effect = _street_for_player

        params = get_game_params_for_preset('nyc')
        for game_seed in (777, 888, 999):
            ai_mod._AI_FIRST_TURN_OSM_SEGMENTS_CACHE.clear()
            ai_mod._AI_FIRST_TURN_OSM_CANDIDATES_CACHE.clear()
            ops = Operations(
                ['AI1', 'AI2', 'AI3'],
                map_width=params['map_width'],
                map_height=params['map_height'],
                starting_money=STARTING_MONEY,
                worker_replenishment_rate=1.0,
                natural_disaster_frequency=0.1,
                map_preset_name='nyc',
                ai_player_indices=[0, 1, 2],
                ai_min_population=100,
                line_build_speed_multiplier=LINE_BUILD_SPEED_MULTIPLIER,
                random_ai_spawn_enabled=False,
                game_seed=game_seed,
                war_start_chance_per_year=0.0,
                war_end_chance_per_year=0.0,
                war_worker_draft_chance_per_year=0.0,
            )
            for p in ops.get_players():
                actions = run_ai_turn(ops, p)
                self.assertTrue(actions, f'seed {game_seed} {p.get_name()} took no actions')
                q = ops.get_build_queue()
                my_line = [b for b in q if b.get('player') == p and b.get('type') == 'line']
                self.assertTrue(
                    my_line or any(a == 'create_line' for a, _ in actions),
                    f'seed {game_seed} {p.get_name()} has no year-1 line build',
                )
                self.assertFalse(
                    getattr(ops, '_ai_street_build_failures', []),
                    'Street failures must not be recorded when mocked street polylines are available',
                )


if __name__ == '__main__':
    unittest.main()
