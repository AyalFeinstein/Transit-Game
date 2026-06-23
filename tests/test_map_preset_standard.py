"""Unit tests for map preset discovery and get_game_params_for_preset."""
import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMapPresetDiscovery(unittest.TestCase):
    """Map presets are discovered dynamically - add/remove files without code changes."""

    def test_list_available_presets_non_empty(self):
        """At least standard, nyc, ny_metro should be discoverable."""
        from map_preset_loader import list_available_presets
        presets = list_available_presets()
        self.assertGreater(len(presets), 0)
        self.assertIn("standard", presets)

    def test_get_game_params_for_preset_standard(self):
        """get_game_params_for_preset('standard') returns valid params."""
        from operations import get_game_params_for_preset
        params = get_game_params_for_preset("standard")
        self.assertIn("map_width", params)
        self.assertIn("map_height", params)
        self.assertIn("map_center_lat", params)
        self.assertIn("map_center_lon", params)
        self.assertIn("use_osm", params)
        self.assertEqual(params["map_width"], 100)
        self.assertEqual(params["map_height"], 75)
        self.assertTrue(params["use_osm"])  # All maps use OSM tiles for display

    def test_get_game_params_for_preset_nyc(self):
        """get_game_params_for_preset('nyc') returns NYC params if available."""
        from operations import get_game_params_for_preset
        from map_preset_loader import list_available_presets
        if "nyc" not in list_available_presets():
            self.skipTest("NYC preset not available")
        params = get_game_params_for_preset("nyc")
        self.assertTrue(params["use_osm"])
        self.assertIn("map_width", params)
        self.assertIn("map_height", params)

    def test_operations_with_map_preset_name_standard(self):
        """Operations can be created with map_preset_name='standard'."""
        from operations import Operations
        ops = Operations(["P1", "P2"], map_preset_name="standard")
        self.assertIsNotNone(ops.get_game())
        self.assertEqual(len(ops.get_players()), 2)
        game_map = ops.get_map()
        self.assertEqual(game_map.map_preset_name, "standard")

    def test_operations_standard_allows_custom_size(self):
        """For standard preset, map_width/map_height can override."""
        from operations import Operations
        ops = Operations(["P1"], map_preset_name="standard", map_width=60, map_height=80)
        game_map = ops.get_map()
        self.assertEqual(game_map.width, 60)
        self.assertEqual(game_map.height, 80)
