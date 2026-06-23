"""Unit tests to diagnose and prevent NYC map crash on game start"""

import unittest
import time
import sys
from map import Map
from map_presets_nyc import NYC_BOUNDS
from ui import setup_game, UI


class TestNYCCrash(unittest.TestCase):
    """Test NYC map crash scenarios"""
    
    def test_map_init_no_blocking_osm_calls(self):
        """Test that map initialization doesn't make blocking OSM calls"""
        start_time = time.time()
        try:
            map_obj = Map(
                width=NYC_BOUNDS['width'],
                height=NYC_BOUNDS['height'],
                use_osm=True,
                map_center_lat=NYC_BOUNDS['center_lat'],
                map_center_lon=NYC_BOUNDS['center_lon']
            )
            elapsed = time.time() - start_time
            # Should complete quickly (no OSM API calls during init)
            self.assertLess(elapsed, 2.0, f"Initialization took {elapsed:.2f}s, should be < 2s")
            self.assertIsNotNone(map_obj)
        except Exception as e:
            self.fail(f"Map initialization failed: {e}")
    
    def test_game_setup_completes(self):
        """Test that game setup completes without hanging"""
        start_time = time.time()
        try:
            game = setup_game(
                player_names=["Test1", "Test2"],
                map_width=NYC_BOUNDS['width'],
                map_height=NYC_BOUNDS['height'],
                use_osm=True,
                map_center_lat=NYC_BOUNDS['center_lat'],
                map_center_lon=NYC_BOUNDS['center_lon']
            )
            elapsed = time.time() - start_time
            # Should complete in reasonable time
            self.assertLess(elapsed, 5.0, f"Game setup took {elapsed:.2f}s, should be < 5s")
            self.assertIsNotNone(game)
            self.assertIsNotNone(game.map)
        except Exception as e:
            self.fail(f"Game setup failed: {e}")
    
    def test_ui_init_no_crash(self):
        """Test that UI initialization with NYC map doesn't crash"""
        start_time = time.time()
        try:
            ui = UI(
                player_names=["Test1", "Test2"],
                map_width=NYC_BOUNDS['width'],
                map_height=NYC_BOUNDS['height'],
                use_osm=True,
                map_center_lat=NYC_BOUNDS['center_lat'],
                map_center_lon=NYC_BOUNDS['center_lon']
            )
            elapsed = time.time() - start_time
            # Should complete in reasonable time
            self.assertLess(elapsed, 5.0, f"UI initialization took {elapsed:.2f}s, should be < 5s")
            self.assertIsNotNone(ui)
            self.assertIsNotNone(ui.game)
        except Exception as e:
            self.fail(f"UI initialization failed: {e}")
    
    def test_osm_integration_lazy_loading(self):
        """Test that OSM integration doesn't block during map init"""
        map_obj = Map(
            width=NYC_BOUNDS['width'],
            height=NYC_BOUNDS['height'],
            use_osm=True,
            map_center_lat=NYC_BOUNDS['center_lat'],
            map_center_lon=NYC_BOUNDS['center_lon']
        )
        
        # OSM integration should exist but not make calls during init
        self.assertIsNotNone(map_obj.osm_integration)
        # But get_osm_rights_of_way should be called lazily, not during init
        # This test verifies that init doesn't call it
        self.assertIsNone(map_obj._osm_rights_of_way)


if __name__ == '__main__':
    unittest.main()

