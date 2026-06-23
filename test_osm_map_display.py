"""Unit tests for OSM map display"""
import unittest
from PyQt6.QtWidgets import QApplication
import sys
from operations import Operations
from qt import MainWindow


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestOSMMapDisplay(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_map_uses_osm_when_enabled(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        game_map = ui.get_map()
        self.assertEqual(game_map.map_center_lat, 40.7)
        self.assertEqual(game_map.map_center_lon, -74.0)
    
    def test_map_does_not_use_osm_when_disabled(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=False)
        game_map = ui.get_map()
        self.assertFalse(game_map.use_osm)
    
    def test_osm_map_has_nyc_bounding_box(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        game_map = ui.get_map()
        self.assertEqual(game_map.map_center_lat, 40.7)
        self.assertEqual(game_map.map_center_lon, -74.0)
        
        rights_of_way = game_map.get_osm_rights_of_way()
        self.assertIsInstance(rights_of_way, list)


class TestOSMMapInGUI(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_display_map_info_with_osm(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        
        player = window.operations.get_players()[0]
        window.current_player_index = 0
        
        window.player_tabs_dict = {}
        window._create_player_tabs(player)
        
        game_map = window.operations.get_map()
        self.assertEqual(game_map.map_center_lat, 40.7)
        self.assertEqual(game_map.map_center_lon, -74.0)
        
        window.DisplayMapInfo()
        
        self.assertIsNotNone(window.player_tabs_dict)
        player_id = window.operations.get_player_id(player)
        if player_id in window.player_tabs_dict:
            map_layout = window.player_tabs_dict[player_id].get('map_layout')
            self.assertIsNotNone(map_layout)
    
    def test_display_map_info_without_osm(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=False)
        
        player = window.operations.get_players()[0]
        window.current_player_index = 0
        
        window.player_tabs_dict = {}
        window._create_player_tabs(player)
        
        game_map = window.operations.get_map()
        self.assertFalse(game_map.use_osm)
        
        window.DisplayMapInfo()
        
        self.assertIsNotNone(window.player_tabs_dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)

