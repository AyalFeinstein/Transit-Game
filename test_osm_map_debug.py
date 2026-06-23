"""Debug test to see what's happening with OSM map display"""
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


class TestOSMMapDebug(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_full_osm_map_flow(self):
        window = MainWindow()
        
        print("\n=== Starting OSM Map Debug Test ===")
        
        print(f"1. Combo box items: {[window.map_type_combo.itemText(i) for i in range(window.map_type_combo.count())]}")
        
        window.map_type_combo.setCurrentText("OSM New York City")
        map_type = window.map_type_combo.currentText()
        print(f"2. Selected map type: '{map_type}'")
        
        use_osm = (map_type == "OSM New York" or map_type == "OSM New York City")
        print(f"3. use_osm calculated: {use_osm}")
        
        window.start_game_with_players(["Player1"], 100, 100, 20000, 1.0, 1.0)
        
        game_map = window.operations.get_map()
        print(f"4. Map center lat: {game_map.map_center_lat}")
        print(f"5. Map center lon: {game_map.map_center_lon}")
        print(f"6. Has map_center_lat attr: {hasattr(game_map, 'map_center_lat')}")
        print(f"7. Has map_center_lon attr: {hasattr(game_map, 'map_center_lon')}")
        
        condition_met = (hasattr(game_map, 'map_center_lat') and 
                        hasattr(game_map, 'map_center_lon') and
                        game_map.map_center_lat == 40.7 and 
                        game_map.map_center_lon == -74.0)
        print(f"8. OSM condition met: {condition_met}")
        
        print(f"9. Has player_tabs_dict: {hasattr(window, 'player_tabs_dict')}")
        if hasattr(window, 'player_tabs_dict'):
            print(f"10. player_tabs_dict keys: {list(window.player_tabs_dict.keys())}")
            for player_id, tabs_dict in window.player_tabs_dict.items():
                print(f"11. Player {player_id} tabs: {list(tabs_dict.keys())}")
                map_layout = tabs_dict.get('map_layout')
                if map_layout:
                    print(f"12. Player {player_id} map_layout count: {map_layout.count()}")
                    for i in range(map_layout.count()):
                        item = map_layout.itemAt(i)
                        if item and item.widget():
                            widget_type = type(item.widget()).__name__
                            print(f"13. Player {player_id} map_layout item {i}: {widget_type}")
        
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            print("14. QWebEngineView import: SUCCESS")
        except ImportError as e:
            print(f"14. QWebEngineView import: FAILED - {e}")
        
        self.assertTrue(condition_met, "OSM condition should be met")
        self.assertTrue(hasattr(window, 'player_tabs_dict'), "player_tabs_dict should exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)

