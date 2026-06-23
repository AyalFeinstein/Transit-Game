"""Unit tests for map selection and line ownership"""
import unittest
import time
from operations import Operations
from PyQt6.QtWidgets import QApplication
import sys


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestMapSelection(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_standard_map_creation(self):
        ui = Operations(["Player1", "Player2"], 50, 50, 20000, 1.0, 1.0, use_osm=False)
        game_map = ui.get_map()
        self.assertFalse(game_map.use_osm)
        self.assertIsNone(game_map.osm_integration)
    
    def test_osm_map_creation(self):
        ui = Operations(["Player1", "Player2"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7128, map_center_lon=-74.0060)
        game_map = ui.get_map()
        self.assertTrue(game_map.use_osm)
        self.assertEqual(game_map.map_center_lat, 40.7128)
        self.assertEqual(game_map.map_center_lon, -74.0060)
    
    def test_osm_map_disabled_on_import_error(self):
        import sys
        original_import = __builtins__.__import__
        
        def mock_import(name, *args, **kwargs):
            if name == 'osm_integration':
                raise ImportError("Module not found")
            return original_import(name, *args, **kwargs)
        
        __builtins__.__import__ = mock_import
        
        try:
            from map import Map
            game_map = Map(50, 50, use_osm=True)
            self.assertFalse(game_map.use_osm)
        finally:
            __builtins__.__import__ = original_import


class TestLineOwnership(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
        self.ui = Operations(["Alice", "Bob"], 50, 50, 20000, 1.0, 1.0)
        self.players = self.ui.get_players()
        self.player1 = self.players[0]
        self.player2 = self.players[1]
    
    def test_line_created_by_player1_belongs_to_player1(self):
        line_points = [(10, 10), (11, 10), (12, 10)]
        result = self.ui.create_line_complete("Test Line 1", line_points, self.player1)
        
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines().get("Test Line 1")
        self.assertIsNotNone(line)
        self.assertEqual(line.get_owner(), self.player1)
        self.assertIn("Test Line 1", self.player1.get_line_names())
        self.assertNotIn("Test Line 1", self.player2.get_line_names())
    
    def test_line_created_by_player2_belongs_to_player2(self):
        line_points = [(20, 20), (21, 20), (22, 20)]
        result = self.ui.create_line_complete("Test Line 2", line_points, self.player2)
        
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines().get("Test Line 2")
        self.assertIsNotNone(line)
        self.assertEqual(line.get_owner(), self.player2)
        self.assertIn("Test Line 2", self.player2.get_line_names())
        self.assertNotIn("Test Line 2", self.player1.get_line_names())
    
    def test_multiple_lines_different_owners(self):
        line1_points = [(5, 5), (6, 5), (7, 5)]
        line2_points = [(15, 15), (16, 15), (17, 15)]
        
        self.ui.create_line_complete("Player1 Line", line1_points, self.player1)
        self.ui.create_line_complete("Player2 Line", line2_points, self.player2)
        
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line1 = self.ui.get_map_lines().get("Player1 Line")
        line2 = self.ui.get_map_lines().get("Player2 Line")
        
        self.assertEqual(line1.get_owner(), self.player1)
        self.assertEqual(line2.get_owner(), self.player2)
        self.assertIn("Player1 Line", self.player1.get_line_names())
        self.assertIn("Player2 Line", self.player2.get_line_names())


class TestPerformanceNoFreezes(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_year_advance_completes_quickly(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        line_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", line_points, player)
        
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        start_time = time.time()
        result = ui.advance_year_complete(year)
        elapsed = time.time() - start_time
        
        self.assertLess(elapsed, 2.0, f"Year advance took {elapsed:.3f}s, should be < 2.0s")
    
    def test_get_line_stats_instant(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        line_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", line_points, player)
        
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = ui.get_map_lines().get("Test Line")
        
        start_time = time.time()
        stats = ui.get_line_stats(line)
        elapsed = time.time() - start_time
        
        self.assertLess(elapsed, 0.1, f"get_line_stats took {elapsed:.3f}s, should be < 0.1s")
        self.assertIn('name', stats)
        self.assertIn('profit', stats)
    
    def test_create_line_completes_quickly(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10), (14, 10)]
        
        start_time = time.time()
        result = ui.create_line_complete("Test Line", line_points, player)
        elapsed = time.time() - start_time
        
        self.assertLess(elapsed, 0.5, f"create_line_complete took {elapsed:.3f}s, should be < 0.5s")
        self.assertIn('cost', result)
        self.assertIn('build_time', result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

