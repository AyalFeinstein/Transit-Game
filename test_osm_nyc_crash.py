import unittest
from map import Map
from operations import Operations
from qt import MainWindow

class TestOSMNYCCrash(unittest.TestCase):
    def test_osm_nyc_map_initialization(self):
        map_obj = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        self.assertIsNotNone(map_obj)
        self.assertTrue(map_obj.use_osm)
        self.assertTrue(map_obj.is_nyc_proper)
        self.assertEqual(map_obj.map_center_lat, 40.7128)
        self.assertEqual(map_obj.map_center_lon, -73.9352)
    
    def test_osm_nyc_game_initialization(self):
        ui = Operations(['Player1', 'Player2'], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        self.assertIsNotNone(ui)
        game_map = ui.get_map()
        self.assertIsNotNone(game_map)
        self.assertTrue(game_map.use_osm)
        self.assertTrue(game_map.is_nyc_proper)
    
    def test_generate_osm_map_html_nyc(self):
        ui = Operations(['Player1'], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        game_map = ui.get_map()
        html = MainWindow._generate_osm_map_html(object(), game_map)
        self.assertIsNotNone(html)
        self.assertGreater(len(html), 1000)
        self.assertIn('<!DOCTYPE html>', html)
        self.assertIn('leaflet', html.lower())
        self.assertIn('40.7128', html)
        self.assertIn('-73.9352', html)
    
    def test_generate_osm_map_html_with_lines(self):
        ui = Operations(['Player1'], 50, 50, 20000, 1.0, 1.0, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        game_map = ui.get_map()
        player = ui.get_players()[0]
        
        points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line("Test Line", points, player)
        html = MainWindow._generate_osm_map_html(object(), game_map)
        self.assertIsNotNone(html)
        self.assertIn('Test Line', html)
        self.assertIn('linesData', html)
        self.assertIn('extend-line-btn', html)
        self.assertNotIn('onclick="window.extendLine', html)

if __name__ == '__main__':
    unittest.main()

