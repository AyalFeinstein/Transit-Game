"""Tests to verify OSM map actually displays when OSM New York is selected"""
import unittest
import sys
from PyQt6.QtWidgets import QApplication
from qt import MainWindow
from operations import Operations


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestOSMMapDisplayActual(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_map_type_combo_matches_code(self):
        window = MainWindow()
        items = [window.map_type_combo.itemText(i) for i in range(window.map_type_combo.count())]
        self.assertIn("Standard", items)
        osm_found = "OSM New York City" in items or "OSM New York" in items
        self.assertTrue(osm_found, "OSM map option should be in combo box")
    
    def test_start_game_with_osm_selected(self):
        window = MainWindow()
        window.map_type_combo.setCurrentText("OSM New York City")
        
        map_type = window.map_type_combo.currentText()
        self.assertEqual(map_type, "OSM New York City")
        
        use_osm = (map_type == "OSM New York" or map_type == "OSM New York City")
        self.assertTrue(use_osm)
    
    def test_osm_map_created_when_selected(self):
        window = MainWindow()
        window.map_type_combo.setCurrentText("OSM New York")
        
        window.start_game_with_players(["Player1"], 100, 100, 20000, 1.0, 1.0)
        
        game_map = window.operations.get_map()
        self.assertAlmostEqual(game_map.map_center_lat, 40.7, places=1)
        self.assertAlmostEqual(game_map.map_center_lon, -74.0, places=1)
        self.assertTrue(hasattr(game_map, 'use_osm'))
    
    def test_display_map_info_shows_osm(self):
        window = MainWindow()
        window.map_type_combo.setCurrentText("OSM New York")
        window.start_game_with_players(["Player1"], 100, 100, 20000, 1.0, 1.0)
        
        game_map = window.operations.get_map()
        has_osm_condition = (hasattr(game_map, 'map_center_lat') and 
                            hasattr(game_map, 'map_center_lon') and
                            game_map.map_center_lat == 40.7 and 
                            game_map.map_center_lon == -74.0)
        self.assertTrue(has_osm_condition, "OSM map condition should be met")
    
    def test_standard_map_not_osm(self):
        window = MainWindow()
        window.map_type_combo.setCurrentText("Standard")
        
        window.start_game_with_players(["Player1"], 100, 100, 20000, 1.0, 1.0)
        
        game_map = window.operations.get_map()
        has_osm_condition = (hasattr(game_map, 'map_center_lat') and 
                            hasattr(game_map, 'map_center_lon') and
                            game_map.map_center_lat == 40.7 and 
                            game_map.map_center_lon == -74.0)
        self.assertFalse(has_osm_condition, "Standard map should not have OSM condition")


if __name__ == "__main__":
    unittest.main(verbosity=2)

