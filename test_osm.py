"""Consolidated tests for osm."""
import unittest

import unittest
from unittest.mock import Mock, patch, MagicMock
from map import Map
from osm_integration import OSMIntegration


class TestOSMIntegration(unittest.TestCase):
    """Test OSM integration functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.osm = OSMIntegration(timeout=5.0)
    
    def test_osm_integration_initialization(self):
        """Test OSM integration initializes correctly"""
        self.assertIsNotNone(self.osm)
        self.assertEqual(self.osm.timeout, 5.0)
        self.assertEqual(self.osm.base_url, "https://overpass-api.de/api/interpreter")
    
    @patch('osm_integration.requests.post')
    def test_get_railway_ways_success(self, mock_post):
        """Test successful retrieval of railway ways"""
        mock_response = Mock()
        mock_response.json.return_value = {
            'elements': [
                {
                    'type': 'way',
                    'id': 123,
                    'tags': {'railway': 'rail'},
                    'geometry': [
                        {'lat': 40.7, 'lon': -74.0},
                        {'lat': 40.8, 'lon': -74.1}
                    ]
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        ways = self.osm.get_railway_ways_in_bbox(40.0, -75.0, 41.0, -73.0)
        
        self.assertEqual(len(ways), 1)
        self.assertEqual(ways[0]['id'], 123)
        self.assertEqual(ways[0]['railway_type'], 'rail')
        self.assertEqual(len(ways[0]['coordinates']), 2)
    
    @patch('osm_integration.requests.post')
    def test_get_railway_ways_timeout(self, mock_post):
        """Test timeout handling in railway way retrieval"""
        import requests
        mock_post.side_effect = requests.exceptions.Timeout()
        
        ways = self.osm.get_railway_ways_in_bbox(40.0, -75.0, 41.0, -73.0)
        
        self.assertEqual(ways, [])
    
    def test_convert_latlon_to_grid(self):
        """Test conversion from lat/lon to grid coordinates"""
        # NYC center
        center_lat, center_lon = 40.7128, -74.0060
        grid_size = 100
        
        # Point near center
        lat, lon = 40.7128, -74.0060
        x, y = self.osm.convert_latlon_to_grid(lat, lon, center_lat, center_lon, grid_size)
        
        # Should be near center of grid
        self.assertGreater(x, grid_size // 2 - 10)
        self.assertLess(x, grid_size // 2 + 10)
        self.assertGreater(y, grid_size // 2 - 10)
        self.assertLess(y, grid_size // 2 + 10)
    
    def test_convert_grid_to_latlon(self):
        """Test conversion from grid coordinates to lat/lon"""
        center_lat, center_lon = 40.7128, -74.0060
        grid_size = 100
        
        # Center of grid
        x, y = grid_size // 2, grid_size // 2
        lat, lon = self.osm.convert_grid_to_latlon(x, y, center_lat, center_lon, grid_size)
        
        # Should be near center coordinates
        self.assertAlmostEqual(lat, center_lat, delta=0.1)
        self.assertAlmostEqual(lon, center_lon, delta=0.1)
    
    def test_find_nearest_way(self):
        """Test finding nearest way to a coordinate"""
        ways = [
            {
                'id': 1,
                'coordinates': [(40.7, -74.0), (40.8, -74.1)]
            },
            {
                'id': 2,
                'coordinates': [(41.0, -75.0), (41.1, -75.1)]
            }
        ]
        
        # Point near first way
        nearest = self.osm.find_nearest_way(40.72, -74.01, ways, max_distance_km=10.0)
        
        self.assertIsNotNone(nearest)
        self.assertEqual(nearest['id'], 1)
    
    def test_find_nearest_way_too_far(self):
        """Test that way too far away is not returned"""
        ways = [
            {
                'id': 1,
                'coordinates': [(50.0, -80.0), (50.1, -80.1)]  # Very far
            }
        ]
        
        # Point in NYC
        nearest = self.osm.find_nearest_way(40.7, -74.0, ways, max_distance_km=0.5)
        
        self.assertIsNone(nearest)
    
    def test_snap_points_to_way(self):
        """Test snapping points to a way"""
        way = {
            'coordinates': [(40.7, -74.0), (40.8, -74.1), (40.9, -74.2)]
        }
        
        points = [(40.72, -74.01), (40.82, -74.11)]
        snapped = self.osm.snap_points_to_way(points, way)
        
        self.assertEqual(len(snapped), 2)
        # Should be close to way coordinates
        self.assertAlmostEqual(snapped[0][0], 40.7, delta=0.1)
        self.assertAlmostEqual(snapped[1][0], 40.8, delta=0.1)


class TestMapWithOSM(unittest.TestCase):
    """Test Map class with OSM integration"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Create map without OSM first
        self.map = Map(width=100, height=100, use_osm=False)
    
    def test_map_without_osm(self):
        """Test map works without OSM"""
        self.assertFalse(self.map.use_osm)
        self.assertIsNone(self.map.osm_integration)
        
        rights_of_way = self.map.get_osm_rights_of_way()
        self.assertEqual(rights_of_way, [])
    
    @patch('map.OSMIntegration')
    def test_map_with_osm(self, mock_osm_class):
        """Test map with OSM enabled"""
        mock_osm = Mock()
        mock_osm_class.return_value = mock_osm
        mock_osm.get_available_rights_of_way.return_value = [
            {'id': 1, 'coordinates': [(40.7, -74.0)], 'right_of_way_type': 'railway'}
        ]
        
        map_with_osm = Map(
            width=100, height=100,
            use_osm=True,
            map_center_lat=40.7128,
            map_center_lon=-74.0060
        )
        
        self.assertTrue(map_with_osm.use_osm)
        self.assertIsNotNone(map_with_osm.osm_integration)
        
        rights_of_way = map_with_osm.get_osm_rights_of_way()
        self.assertEqual(len(rights_of_way), 1)
    
    def test_find_nearest_right_of_way_without_osm(self):
        """Test finding nearest right of way when OSM is disabled"""
        nearest = self.map.find_nearest_right_of_way(50, 50)
        self.assertIsNone(nearest)


class TestLineCreationWithOSM(unittest.TestCase):
    """Test line creation with OSM snapping"""
    
    def setUp(self):
        """Set up test fixtures"""
        from operations import Operations
        from PyQt6.QtWidgets import QApplication
        import sys
        
        # Ensure QApplication exists
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        # Create UI with OSM disabled for basic tests
        self.ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        self.player = self.ui.get_players()[0]
    
    @patch('map.OSMIntegration')
    def test_line_creation_snaps_to_osm(self, mock_osm_class):
        """Test that line creation snaps points to OSM rights of way when enabled"""
        # Mock OSM integration
        mock_osm = Mock()
        mock_osm_class.return_value = mock_osm
        
        # Mock way near the points
        mock_way = {
            'id': 1,
            'coordinates': [(40.7, -74.0), (40.8, -74.1)],
            'right_of_way_type': 'railway'
        }
        mock_osm.find_nearest_way.return_value = mock_way
        mock_osm.convert_grid_to_latlon.return_value = (40.7, -74.0)
        mock_osm.convert_latlon_to_grid.return_value = (25, 25)
        mock_osm.snap_points_to_way.return_value = [(40.7, -74.0)]
        
        # Enable OSM on map
        self.ui.game.get_map().use_osm = True
        self.ui.game.get_map().osm_integration = mock_osm
        self.ui.game.get_map().map_center_lat = 40.7128
        self.ui.game.get_map().map_center_lon = -74.0060
        
        # Create line
        points = [(25, 25), (26, 26)]
        result = self.ui.create_line_complete("Test Line", points, self.player)
        
        # Verify OSM was used
        self.assertTrue(result.get('osm_snapped', False))
        mock_osm.find_nearest_way.assert_called()
    
    def test_line_creation_without_osm(self):
        """Test that line creation works normally without OSM"""
        # Ensure OSM is disabled
        self.ui.game.get_map().use_osm = False
        
        # Create line
        points = [(10, 10), (11, 11), (12, 12)]
        
        # Complete construction first
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        result = self.ui.create_line_complete("Test Line", points, self.player)
        
        # Should work normally
        self.assertIn('cost', result)
        self.assertIn('build_time', result)
        self.assertFalse(result.get('osm_snapped', False))

if __name__ == '__main__':
    unittest.main()
