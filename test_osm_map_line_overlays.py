"""Unit tests for OSM map with line overlays"""
import unittest
import os
import tempfile
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QDir
import sys
from operations import Operations
from qt import MainWindow
from line import Line


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestOSMMapLineOverlays(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def test_html_generation_includes_lines(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        player = window.operations.get_players()[0]
        game_map = window.operations.get_map()
        
        line_points = [(20, 20), (21, 20), (22, 20), (23, 20)]
        result = window.operations.create_line_complete("Test Line", line_points, player)
        
        year = window.operations.get_year()
        while len(window.operations.get_build_queue()) > 0 and year < 2050:
            result = window.operations.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        if year >= 2050:
            self.fail(f"Build queue did not empty before reaching year 2050")
        
        html = window._generate_osm_map_html(game_map)
        
        self.assertIn('linesData', html)
        self.assertIn('Test Line', html)
        self.assertIn('L.polyline', html)
        self.assertIn('bindPopup', html)
    
    def test_html_generation_includes_population(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        game_map = window.operations.get_map()
        
        html = window._generate_osm_map_html(game_map)
        
        self.assertIn('populationData', html)
        self.assertIn('L.circleMarker', html)
        self.assertIn('Population:', html)
    
    def test_html_generation_includes_osm_tiles(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        game_map = window.operations.get_map()
        
        html = window._generate_osm_map_html(game_map)
        
        self.assertIn('leaflet', html.lower())
        self.assertIn('tile.openstreetmap.org', html)
        self.assertIn('setView', html)
        self.assertIn('setMaxBounds', html)
    
    def test_html_generation_nyc_bounds(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        game_map = window.operations.get_map()
        
        html = window._generate_osm_map_html(game_map)
        
        self.assertIn('40.5', html)
        self.assertIn('40.9', html)
        self.assertIn('-74.05', html)
        self.assertIn('-73.7', html)
    
    def test_multiple_lines_appear_on_map(self):
        window = MainWindow()
        window.operations = Operations(["Player1", "Player2"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        players = window.operations.get_players()
        game_map = window.operations.get_map()
        
        line1_points = [(20, 20), (21, 20), (22, 20)]
        line2_points = [(25, 25), (26, 25), (27, 25)]
        
        window.operations.create_line_complete("Line 1", line1_points, players[0])
        window.operations.create_line_complete("Line 2", line2_points, players[1])
        
        year = window.operations.get_year()
        while len(window.operations.get_build_queue()) > 0 and year < 2050:
            result = window.operations.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        if year >= 2050:
            self.fail(f"Build queue did not empty before reaching year 2050")
        
        html = window._generate_osm_map_html(game_map)
        
        self.assertIn('Line 1', html)
        self.assertIn('Line 2', html)
        line1_count = html.count('Line 1')
        line2_count = html.count('Line 2')
        self.assertGreater(line1_count, 0, "Line 1 should appear in HTML")
        self.assertGreater(line2_count, 0, "Line 2 should appear in HTML")
    
    def test_webengine_view_created_when_available(self):
        try:
            from PyQt6.QtWidgets import QApplication
            import sys
            app = QApplication.instance() or QApplication(sys.argv)
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView()
            webengine_available = True
            view.deleteLater()
        except (ImportError, Exception) as e:
            self.skipTest(f"QWebEngineView test skipped (PyQt6-WebEngine may require display): {e}")
        
        self.assertTrue(webengine_available, "PyQt6-WebEngine should be installed and functional")
    
    def test_html_file_creation(self):
        window = MainWindow()
        window.operations = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7, map_center_lon=-74.0)
        game_map = window.operations.get_map()
        
        html = window._generate_osm_map_html(game_map)
        
        temp_dir = QDir.tempPath()
        temp_file = os.path.join(temp_dir, "test_osm_map.html")
        
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        self.assertTrue(os.path.exists(temp_file))
        self.assertGreater(os.path.getsize(temp_file), 1000)
        
        with open(temp_file, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn('<!DOCTYPE html>', content)
            self.assertIn('<html>', content)
            self.assertIn('</html>', content)
        
        os.remove(temp_file)


if __name__ == "__main__":
    unittest.main(verbosity=2)

