import unittest
from map import Map
from qt import MainWindow


class TestMapHTMLGeneration(unittest.TestCase):
    def test_generate_osm_map_html_without_ui(self):
        mock_self = object()
        mock_self.ui = None
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        result = MainWindow._generate_osm_map_html(mock_self, game_map)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 1000)
        self.assertIn('<!DOCTYPE html>', result)

    def test_generate_osm_map_html_without_ui_attribute(self):
        mock_self = object()
        if hasattr(mock_self, 'ui'):
            delattr(mock_self, 'ui')
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        result = MainWindow._generate_osm_map_html(mock_self, game_map)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 1000)


if __name__ == '__main__':
    unittest.main()

