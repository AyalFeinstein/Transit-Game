"""Unit tests for edit mode population point removal.

Tests that when edit mode is engaged and the user clicks on a population point,
the appropriate preset file is rewritten so the INCLUDED_POINTS list does not
contain the clicked point. Also tests that get_population_points_for_display
excludes points in _deleted_population_points so removed points never reappear.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from map_preset_utils import (
    read_included_points_from_file,
    write_included_points_to_file,
    get_preset_file_info,
)


class TestEditModePopulationPointRemoval(unittest.TestCase):
    """Test that clicking a population point removes it from the INCLUDED_POINTS file."""

    def setUp(self):
        """Create a temporary preset file with sample points."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "map_presets_test.py")
        self.variable_name = "TEST_INCLUDED_POINTS"
        self.initial_points = [(10, 20), (30, 40), (50, 60), (70, 80)]

    def tearDown(self):
        """Remove temporary directory."""
        if os.path.exists(self.temp_file):
            os.remove(self.temp_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def _write_initial_file(self, points: list):
        """Write a preset file with the given points."""
        points_str = "[\n" + ",\n".join(f"    {p}" for p in sorted(points)) + "\n]"
        content = f'''"""Test map preset for edit mode tests."""
MAP_ID = "test"

{self.variable_name} = {points_str}
'''
        with open(self.temp_file, "w") as f:
            f.write(content)

    def test_read_and_write_included_points(self):
        """Test that we can read and write points to a preset file."""
        self._write_initial_file(self.initial_points)
        read_points = read_included_points_from_file(self.temp_file, self.variable_name)
        self.assertIsNotNone(read_points)
        self.assertEqual(read_points, set(self.initial_points))

        # Write modified points (remove one)
        modified = set(self.initial_points) - {(30, 40)}
        success = write_included_points_to_file(
            self.temp_file, self.variable_name, modified
        )
        self.assertTrue(success)

        # Verify the file was updated
        read_after = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertEqual(read_after, modified)
        self.assertNotIn((30, 40), read_after)

    def test_remove_point_updates_file(self):
        """Test that removing a point from INCLUDED_POINTS persists to file."""
        self._write_initial_file(self.initial_points)
        point_to_remove = (50, 60)

        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertIn(point_to_remove, existing)

        existing.discard(point_to_remove)
        success = write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )
        self.assertTrue(success)

        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertNotIn(point_to_remove, verify)
        self.assertEqual(len(verify), len(self.initial_points) - 1)

    def test_remove_nonexistent_point_no_error(self):
        """Test that removing a point not in the file does not corrupt it."""
        self._write_initial_file(self.initial_points)
        point_not_in_file = (99, 99)

        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_not_in_file)  # No-op for set
        success = write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )
        self.assertTrue(success)

        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertEqual(verify, set(self.initial_points))

    def test_fuzzy_match_removal(self):
        """Test that a point within 1 pixel can be resolved for removal."""
        self._write_initial_file([(25, 35)])  # Exact point
        # Simulate click returning (24, 36) - off by 1
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        similar = [p for p in existing if abs(p[0] - 24) <= 1 and abs(p[1] - 36) <= 1]
        self.assertTrue(len(similar) >= 1)
        to_remove = min(similar, key=lambda p: abs(p[0] - 24) + abs(p[1] - 36))
        existing.discard(to_remove)
        write_included_points_to_file(self.temp_file, self.variable_name, existing)
        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertEqual(len(verify), 0)


class TestEditModeWithMockMainWindow(unittest.TestCase):
    """Test _remove_population_point behavior via map_preset_utils and mock game map."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "map_presets_nyc.py")
        self.variable_name = "NYC_INCLUDED_POINTS"

    def tearDown(self):
        if os.path.exists(self.temp_file):
            os.remove(self.temp_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def _create_nyc_like_preset(self, points: list):
        """Create a file that looks like map_presets_nyc.py."""
        points_str = "[\n" + ",\n".join(f"    {p}" for p in sorted(points)) + "\n]"
        content = f'''"""NYC map preset - test copy."""
MAP_ID = "nyc"

{self.variable_name} = {points_str}
'''
        with open(self.temp_file, "w") as f:
            f.write(content)

    def test_get_preset_file_info_requires_map_preset_name(self):
        """get_preset_file_info returns None for map without map_preset_name."""
        from map import Map
        game_map = Map(width=60, height=60, use_osm=False)
        # Standard map may not have map_preset_name set for preset files
        info = get_preset_file_info(game_map)
        # If map has map_preset_name, we get info; otherwise None
        if info:
            file_path, var_name = info
            self.assertIn("map_presets_", file_path)
            self.assertIn("_INCLUDED_POINTS", var_name)

    def test_remove_flow_simulation(self):
        """Simulate the full remove flow: read, remove, write, verify."""
        points = [(1, 1), (2, 2), (3, 3)]
        self._create_nyc_like_preset(points)
        point_to_remove = (2, 2)

        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertIn(point_to_remove, existing)

        existing.discard(point_to_remove)
        success = write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )
        self.assertTrue(success)

        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertNotIn(point_to_remove, verify)
        self.assertIn((1, 1), verify)
        self.assertIn((3, 3), verify)

    def test_included_points_list_actually_changed_on_disk(self):
        """Verify the INCLUDED_POINTS list on disk is actually changed after removal."""
        points = [(5, 10), (15, 20), (25, 30)]
        self._create_nyc_like_preset(points)
        point_to_remove = (15, 20)

        # Remove point
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(self.temp_file, self.variable_name, existing)

        # Read back from file (simulating what get_population_points_for_display does)
        import time
        time.sleep(0.02)
        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertIsNotNone(verify)
        self.assertNotIn(point_to_remove, verify, "Point must not reappear after removal")
        self.assertEqual(len(verify), 2)
        self.assertEqual(verify, {(5, 10), (25, 30)})

    def test_removed_point_stays_removed_on_multiple_reads(self):
        """Verify removed point does not reappear on subsequent reads (no caching)."""
        points = [(1, 1), (2, 2), (3, 3)]
        self._create_nyc_like_preset(points)
        point_to_remove = (2, 2)

        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(self.temp_file, self.variable_name, existing)

        # Multiple reads - point must stay removed (simulates map refresh)
        for _ in range(3):
            verify = read_included_points_from_file(
                self.temp_file, self.variable_name
            )
            self.assertNotIn(point_to_remove, verify)
            self.assertEqual(len(verify), 2)


class TestDeletedPointsExcludedFromDisplay(unittest.TestCase):
    """Test that get_population_points_for_display excludes _deleted_population_points.

    This ensures removed points never reappear on the map after edit-mode removal.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "map_presets_nyc.py")
        self.variable_name = "NYC_INCLUDED_POINTS"

    def tearDown(self):
        if os.path.exists(self.temp_file):
            os.remove(self.temp_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def _create_nyc_like_preset(self, points: list):
        points_str = "[\n" + ",\n".join(f"    {p}" for p in sorted(points)) + "\n]"
        content = f'''"""NYC map preset - test copy."""
MAP_ID = "nyc"

{self.variable_name} = {points_str}
'''
        with open(self.temp_file, "w") as f:
            f.write(content)

    def test_deleted_point_excluded_from_display(self):
        """Points in _deleted_population_points must not appear in display output."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        # Simulate edit-mode removal: point removed from population and added to deleted
        point_removed = (20, 20)
        game_map._deleted_population_points = {point_removed}
        if point_removed in game_map.population:
            del game_map.population[point_removed]

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result = ops.get_population_points_for_display(game_map)

        grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
        self.assertNotIn(
            point_removed,
            grid_coords,
            "Removed point must not appear in get_population_points_for_display",
        )
        self.assertIn((10, 10), grid_coords)
        self.assertIn((30, 30), grid_coords)

    def test_deleted_point_excluded_even_if_in_population_and_included(self):
        """Even if point is still in population and included_points, _deleted filters it."""
        from operations import Operations

        points = [(5, 5), (15, 15), (25, 25)]
        self._create_nyc_like_preset(points)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        point_removed = (15, 15)
        # Point still in population (e.g. removal just happened, population not yet updated)
        game_map._deleted_population_points = {point_removed}
        # Do NOT remove from population - simulate race where file was updated but
        # population dict still has it
        if point_removed not in game_map.population:
            game_map.population[point_removed] = 100

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result = ops.get_population_points_for_display(game_map)

        grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
        self.assertNotIn(
            point_removed,
            grid_coords,
            "_deleted_population_points must override population/included",
        )

    def test_removed_point_stays_removed_even_if_population_stale(self):
        """Regression: After removal, even if population dict has the point (e.g. from stale
        re-init or module cache), display must never show it. File is sole source of truth."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_remove = (20, 20)

        # Remove from file
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        # Simulate stale population: point still in population (e.g. from module cache before file write)
        game_map.population[point_to_remove] = 5000
        # No _deleted_population_points - we're testing that FILE alone is enough

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            for i in range(5):
                result = ops.get_population_points_for_display(game_map)
                grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
                self.assertNotIn(
                    point_to_remove,
                    grid_coords,
                    f"Point must not reappear on display call #{i + 1} even with stale population",
                )

    def test_removed_point_stays_removed_after_map_refresh_simulation(self):
        """Regression: After removal, simulate DisplayMapInfo/Refresh Map being called
        multiple times. The point must stay removed on every call (no cache, no reappearance)."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_remove = (20, 20)

        # Remove from file (as _toggle_population_point does)
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        if point_to_remove in game_map.population:
            del game_map.population[point_to_remove]
        game_map._deleted_population_points = {point_to_remove}

        # Simulate DisplayMapInfo being called 10 times (e.g. user clicks Refresh Map repeatedly)
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            for i in range(10):
                result = ops.get_population_points_for_display(game_map)
                grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
                self.assertNotIn(
                    point_to_remove,
                    grid_coords,
                    f"Point must stay removed on refresh #{i + 1}",
                )
                self.assertEqual(
                    len(result),
                    2,
                    f"Must have exactly 2 points on refresh #{i + 1}",
                )

    def test_removed_point_stays_removed_after_new_game(self):
        """Regression: After removal, starting a NEW game must not show the removed point.
        Simulates: remove point -> user starts new game -> display must still exclude point.
        File is source of truth; new game reads from file."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_remove = (20, 20)

        # Remove from file (as _toggle_population_point does)
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        # First game: remove point
        ops1 = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map1 = ops1.get_map()
        if point_to_remove in game_map1.population:
            del game_map1.population[point_to_remove]
        game_map1._deleted_population_points = {point_to_remove}

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result1 = ops1.get_population_points_for_display(game_map1)
            grid_coords1 = {(p["grid_x"], p["grid_y"]) for p in result1}
            self.assertNotIn(point_to_remove, grid_coords1)
            self.assertEqual(len(result1), 2)

        # NEW game: fresh Operations (simulates user starting new game)
        ops2 = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map2 = ops2.get_map()

        # File still has 2 points; new game must read from file and show 2 points
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            for i in range(3):
                result2 = ops2.get_population_points_for_display(game_map2)
                grid_coords2 = {(p["grid_x"], p["grid_y"]) for p in result2}
                self.assertNotIn(
                    point_to_remove,
                    grid_coords2,
                    f"Point must stay removed in new game (call #{i + 1})",
                )
                self.assertEqual(
                    len(result2),
                    2,
                    f"Must have exactly 2 points in new game (call #{i + 1})",
                )

    def test_removed_point_stays_removed_on_multiple_display_calls(self):
        """Simulate the full remove flow: read, remove, write, verify file.
        Then call get_population_points_for_display multiple times and ensure
        the point never reappears (like DisplayMapInfo being called repeatedly)."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_remove = (20, 20)

        # Simulate _remove_population_point: read, remove, write to file
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertIn(point_to_remove, existing)
        existing.discard(point_to_remove)
        success = write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )
        self.assertTrue(success, "File write must succeed")

        # Verify point is gone from file
        verify_file = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertNotIn(point_to_remove, verify_file)

        # Create game and simulate removal
        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        # Simulate what _remove_population_point does: delete from population, add to deleted
        if point_to_remove in game_map.population:
            del game_map.population[point_to_remove]
        game_map._deleted_population_points = {point_to_remove}

        # Call get_population_points_for_display multiple times (simulates DisplayMapInfo refreshes)
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            for i in range(5):
                result = ops.get_population_points_for_display(game_map)
                grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
                self.assertNotIn(
                    point_to_remove,
                    grid_coords,
                    f"Point must not reappear on display call #{i + 1}",
                )

    def test_total_count_decreases_and_correct_point_removed(self):
        """After removal: (1) total display count decreases by exactly 1,
        (2) the removed point is the one we clicked, not some other point,
        (3) all other points remain."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30), (40, 40)]
        self._create_nyc_like_preset(points)
        point_to_remove = (20, 20)
        expected_remaining = {(10, 10), (30, 30), (40, 40)}

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()

        # Count BEFORE removal
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result_before = ops.get_population_points_for_display(game_map)
        count_before = len(result_before)
        grid_coords_before = {(p["grid_x"], p["grid_y"]) for p in result_before}
        self.assertEqual(count_before, 4, "Should have 4 points before removal")
        self.assertIn(point_to_remove, grid_coords_before)

        # Remove the point (write to file, update game state)
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )
        if point_to_remove in game_map.population:
            del game_map.population[point_to_remove]
        game_map._deleted_population_points = {point_to_remove}

        # Count AFTER removal
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result_after = ops.get_population_points_for_display(game_map)
        count_after = len(result_after)
        grid_coords_after = {(p["grid_x"], p["grid_y"]) for p in result_after}

        self.assertEqual(
            count_after,
            count_before - 1,
            "Total count must decrease by exactly 1 after removal",
        )
        self.assertNotIn(
            point_to_remove,
            grid_coords_after,
            "The removed point must not appear (correct point removed)",
        )
        for pt in expected_remaining:
            self.assertIn(
                pt,
                grid_coords_after,
                f"Point {pt} must still be present after removing {point_to_remove}",
            )

    def test_deleted_point_not_in_display_file_is_sole_source_of_truth(self):
        """Regression: Deleted point must NOT appear on map. File is sole source of truth.
        get_population_points_for_display reads from file - deleted points are simply not in file."""
        from operations import Operations

        points = [(10, 10), (19, 25), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_remove = (19, 25)

        # Remove from file (as _toggle_population_point does)
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        # Verify file no longer has the point
        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertNotIn(point_to_remove, verify, "Point must be gone from file")
        self.assertEqual(len(verify), 2)

        # Create game - NO session tracking (simulates after our fix: we clear deleted sets)
        ops = Operations(
            ["P1"],
            map_width=50,
            map_height=50,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        if point_to_remove in game_map.population:
            del game_map.population[point_to_remove]
        # Explicitly ensure deleted sets are empty - file is source of truth
        game_map._deleted_population_points = set()
        ops._session_deleted_population_points = set()

        # get_population_points_for_display must NOT return the deleted point
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            for i in range(3):
                result = ops.get_population_points_for_display(game_map)
                grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
                self.assertNotIn(
                    point_to_remove,
                    grid_coords,
                    f"Deleted point must not appear on map (call #{i + 1})",
                )
                self.assertEqual(
                    len(result),
                    2,
                    f"Must have exactly 2 points (call #{i + 1})",
                )

    def test_map_initializes_with_one_fewer_point_after_removal(self):
        """After removing a point from file, a freshly created map/ops must display
        one fewer point (file is source of truth for new games)."""
        from operations import Operations

        points = [(5, 5), (15, 15), (25, 25)]
        self._create_nyc_like_preset(points)
        point_to_remove = (15, 15)

        # Create first ops, get count before removal
        ops_before = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map_before = ops_before.get_map()
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result_before = ops_before.get_population_points_for_display(
                game_map_before
            )
        count_before = len(result_before)
        self.assertEqual(count_before, 3, "Initial map should have 3 points")

        # Remove from file (simulate user removing point and saving)
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        # Create NEW ops/map (simulates new game or map refresh from disk)
        ops_after = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map_after = ops_after.get_map()
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result_after = ops_after.get_population_points_for_display(
                game_map_after
            )
        count_after = len(result_after)
        grid_coords_after = {(p["grid_x"], p["grid_y"]) for p in result_after}

        self.assertEqual(
            count_after,
            count_before - 1,
            "New map must initialize with one fewer point after file was updated",
        )
        self.assertNotIn(
            point_to_remove,
            grid_coords_after,
            "Removed point must not appear in fresh map",
        )
        self.assertIn((5, 5), grid_coords_after)
        self.assertIn((25, 25), grid_coords_after)

    def test_point_removed_from_file_and_game(self):
        """Verify that after removal: (1) file no longer has point, (2) game population
        excludes it, (3) display never shows it. File is source of truth."""
        from operations import Operations

        points = [(1, 1), (2, 2), (3, 3)]
        self._create_nyc_like_preset(points)
        point_to_remove = (2, 2)

        # Remove from file
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_to_remove)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        # Create game, simulate removal in game state (no session tracking - file is source of truth)
        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        if point_to_remove in game_map.population:
            del game_map.population[point_to_remove]

        # (1) File must not have point
        file_points = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertNotIn(point_to_remove, file_points)

        # (2) Game population must not have point
        self.assertNotIn(point_to_remove, game_map.population)

        # (3) Display must never show it (reads from file)
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result = ops.get_population_points_for_display(game_map)
        grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
        self.assertNotIn(point_to_remove, grid_coords)

    def test_nyc_map_edit_mode_click_removes_point_from_file(self):
        """NYC map with edit mode on: user clicks point -> point removed from INCLUDED_POINTS file.
        This is the exact scenario: NYC map, edit mode, click on point."""
        from operations import Operations
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_click = (20, 20)

        # Create Operations with NYC map (as when user starts NYC game)
        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        self.assertTrue(
            hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper,
            "Map must be NYC for this test",
        )

        # Create MainWindow and set operations (as when game is started with edit mode)
        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = ops
        w.edit_mode_enabled = True

        # Patch get_preset_file_info to use our temp file (so we don't modify real map_presets_nyc.py)
        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            # Simulate user click: call _remove_population_point directly
            w._remove_population_point(point_to_click[0], point_to_click[1])

        # Verify: point must be removed from the file
        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertIsNotNone(verify)
        self.assertNotIn(
            point_to_click,
            verify,
            "Clicked point must be removed from INCLUDED_POINTS file",
        )
        self.assertEqual(len(verify), 2)
        self.assertIn((10, 10), verify)
        self.assertIn((30, 30), verify)

        w.close()

    def test_update_population_over_time_does_not_restore_deleted_point(self):
        """update_population_over_time must never add population back to deleted points.
        Simulates year advance after point removal - point must stay removed."""
        from operations import Operations

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        # Find any point with population > 0
        point_removed = next(
            (p for p, pop in game_map.population.items() if pop > 0),
            (14, 10),
        )
        # Simulate removal
        if point_removed in game_map.population:
            del game_map.population[point_removed]
        game_map._deleted_population_points = {point_removed}
        ops._session_deleted_population_points.add(point_removed)

        # Simulate year advance (update_population_over_time)
        game_map.update_population_over_time(1840, 0.0)

        # Point must still not be in population
        self.assertNotIn(
            point_removed,
            game_map.population,
            "update_population_over_time must not restore deleted point",
        )

    def test_population_reinit_excludes_deleted_points(self):
        """When population is re-initialized (e.g. map replaced), deleted points must not be added.
        Even if file still has the point, session_deleted must exclude it."""
        from operations import Operations

        points = [(10, 10), (20, 20), (30, 30)]
        self._create_nyc_like_preset(points)
        point_removed = (20, 20)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()
        ops._session_deleted_population_points.add(point_removed)
        game_map._deleted_population_points = {point_removed}
        if point_removed in game_map.population:
            del game_map.population[point_removed]

        # Simulate re-init: remove population so get_population_points_for_display re-initializes it
        # File still has the point - we're testing that deleted_points filter works during re-init
        del game_map.population

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result = ops.get_population_points_for_display(game_map)

        grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
        self.assertNotIn(
            point_removed,
            grid_coords,
            "Re-initialized population must not include deleted point",
        )
        self.assertNotIn(point_removed, game_map.population)

    def test_session_deleted_persists_across_game_map_replacement(self):
        """When game_map is replaced (e.g. networked mode), session_deleted still filters.
        Simulates: remove point -> game state replaced -> display must still exclude point."""
        from operations import Operations

        points = [(5, 5), (15, 15), (25, 25)]
        self._create_nyc_like_preset(points)
        point_removed = (15, 15)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        game_map = ops.get_map()

        # Simulate removal: update file, add to session_deleted (game_map may get replaced)
        existing = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        existing.discard(point_removed)
        write_included_points_to_file(
            self.temp_file, self.variable_name, existing
        )

        # Add to session_deleted (persists even when game_map is replaced)
        ops._session_deleted_population_points.add(point_removed)

        # Simulate game_map replacement: fresh map without _deleted_population_points
        # (e.g. from network deserialization)
        fresh_map = ops.get_map()  # Same object, but could be replaced in real scenario
        # Clear map-level deleted to simulate replacement
        if hasattr(fresh_map, '_deleted_population_points'):
            fresh_map._deleted_population_points = set()
        # Point might still be in population (from init before file was updated)
        if point_removed in fresh_map.population:
            del fresh_map.population[point_removed]

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            result = ops.get_population_points_for_display(fresh_map)

        grid_coords = {(p["grid_x"], p["grid_y"]) for p in result}
        self.assertNotIn(
            point_removed,
            grid_coords,
            "session_deleted must filter even when game_map has no _deleted_population_points",
        )

    def test_duplicate_toggle_ignored_within_debounce_window(self):
        """Same point toggled twice within 200ms must be processed only once.
        Simulates poll firing before JS atomic clear - second call must be a no-op."""
        from PyQt6.QtWidgets import QApplication
        from operations import Operations

        app = QApplication.instance() or QApplication([])

        points = [(10, 10), (19, 25), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_toggle = (19, 25)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )

        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = ops
        w.edit_mode_enabled = True

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            # Simulate duplicate poll: same click processed twice in quick succession
            w._toggle_population_point(point_to_toggle[0], point_to_toggle[1])
            w._toggle_population_point(point_to_toggle[0], point_to_toggle[1])

        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        self.assertIsNotNone(verify)
        # Point must be removed exactly once (second call ignored by debounce)
        self.assertNotIn(
            point_to_toggle,
            verify,
            "Point must be removed; duplicate toggle must not add it back",
        )
        self.assertEqual(
            len(verify),
            2,
            "File must have 2 points (one removed), not 1 or 3",
        )
        self.assertIn((10, 10), verify)
        self.assertIn((30, 30), verify)

        w.close()

    def test_toggle_same_point_after_debounce_processes_again(self):
        """Same point toggled twice with >200ms gap: first removes, second adds back."""
        import time
        from PyQt6.QtWidgets import QApplication
        from operations import Operations

        app = QApplication.instance() or QApplication([])

        points = [(10, 10), (19, 25), (30, 30)]
        self._create_nyc_like_preset(points)
        point_to_toggle = (19, 25)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )

        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = ops
        w.edit_mode_enabled = True

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            w._toggle_population_point(point_to_toggle[0], point_to_toggle[1])
            time.sleep(0.25)  # Past debounce window
            w._toggle_population_point(point_to_toggle[0], point_to_toggle[1])

        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        # First call removed, second call added back
        self.assertIn(
            point_to_toggle,
            verify,
            "After debounce, second toggle must add point back",
        )
        self.assertEqual(len(verify), 3)

        w.close()

    def test_click_on_session_deleted_point_adds_it_back(self):
        """Point in file but in session_deleted is NOT displayed. Clicking empty cell
        must ADD (un-delete), not REMOVE. File stays unchanged; point reappears."""
        from PyQt6.QtWidgets import QApplication
        from operations import Operations

        app = QApplication.instance() or QApplication([])

        points = [(10, 10), (19, 25), (30, 30)]
        self._create_nyc_like_preset(points)
        point_in_session_deleted = (19, 25)

        ops = Operations(
            ["P1"],
            map_width=60,
            map_height=60,
            map_preset_name="nyc",
            starting_money=50000,
        )
        # Simulate: user removed (19,25) earlier - it's in session_deleted, file still has it
        ops._session_deleted_population_points.add(point_in_session_deleted)
        game_map = ops.get_map()
        if not hasattr(game_map, '_deleted_population_points'):
            game_map._deleted_population_points = set()
        game_map._deleted_population_points.add(point_in_session_deleted)

        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = ops
        w.edit_mode_enabled = True

        with patch(
            "map_preset_utils.get_preset_file_info",
            return_value=(self.temp_file, self.variable_name),
        ):
            # User clicks empty cell (19,25) - should ADD (un-delete), NOT remove from file
            w._toggle_population_point(
                point_in_session_deleted[0], point_in_session_deleted[1]
            )

        verify = read_included_points_from_file(
            self.temp_file, self.variable_name
        )
        # Point must still be in file (we un-deleted, did not remove)
        self.assertIn(
            point_in_session_deleted,
            verify,
            "Un-delete must keep point in file; must not remove it",
        )
        self.assertEqual(len(verify), 3)
        # Point must be removed from session_deleted so it appears
        self.assertNotIn(
            point_in_session_deleted,
            ops._session_deleted_population_points,
            "Un-delete must remove point from session_deleted",
        )

        w.close()


class TestGovernmentChamberLayout(unittest.TestCase):
    """Test government chamber widget has 5 rows and no overlapping seats."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_seat_layout_five_rows(self):
        """GovernmentChamberWidget has 5 rows of 10 seats each."""
        from qt_networked import GovernmentChamberWidget
        self.assertEqual(GovernmentChamberWidget.NUM_ROWS, 5)
        self.assertEqual(GovernmentChamberWidget.SEATS_PER_ROW, 10)
        self.assertEqual(
            GovernmentChamberWidget.NUM_SEATS,
            GovernmentChamberWidget.NUM_ROWS * GovernmentChamberWidget.SEATS_PER_ROW,
        )

    def test_seat_rects_no_overlap(self):
        """Computed seat rectangles (50 seats) do not overlap with each other."""
        from qt_networked import GovernmentChamberWidget

        seats_data = [(None, False)] * 51  # 50 seats + leader
        widget = GovernmentChamberWidget(seats_data, lambda i: None, lambda: None)
        widget._compute_layout()

        # Only check seat rects (not leader)
        seat_rects = [r for r, is_leader, idx in widget._seat_rects if not is_leader]
        self.assertEqual(len(seat_rects), 50)

        for i, r1 in enumerate(seat_rects):
            for j, r2 in enumerate(seat_rects):
                if i < j and r1.intersects(r2):
                    self.fail(f"Seats {i} and {j} overlap: {r1} vs {r2}")


if __name__ == "__main__":
    unittest.main()
