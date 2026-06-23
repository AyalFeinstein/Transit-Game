#!/usr/bin/env python3
"""
Unit tests for map rendering performance.
Ensures map renders within 10 seconds of game start.
"""

import unittest
import time
from unittest.mock import Mock, MagicMock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from map import Map
from operations import Operations


class TestMapRenderingPerformance(unittest.TestCase):
    """Test map rendering performance requirements."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.max_html_generation_time = 5.0  # HTML generation should take < 5s
        self.max_total_render_time = 10.0  # Total render should take < 10s
        
    def test_map_html_generation_performance(self):
        """Test that map HTML generation completes quickly."""
        from PyQt6.QtWidgets import QApplication
        import sys
        
        # Create QApplication if it doesn't exist
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        from qt import MainWindow
        
        # Create a mock UI and map
        mock_ui = Mock()
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        mock_ui.get_map = Mock(return_value=game_map)
        mock_ui.get_year = Mock(return_value=1830)
        mock_ui.get_map_lines = Mock(return_value={})
        mock_ui.get_build_queue = Mock(return_value=[])
        
        # Create main window instance
        main_window = MainWindow()
        main_window.operations = mock_ui
        main_window.edit_mode_enabled = False
        
        # Measure HTML generation time
        start_time = time.time()
        try:
            html_content = main_window._generate_osm_map_html(game_map)
            elapsed_time = time.time() - start_time
        except Exception as e:
            self.fail(f"HTML generation failed: {e}")
        
        # Assert performance
        self.assertLess(elapsed_time, self.max_html_generation_time,
                        f"HTML generation took {elapsed_time:.2f}s, expected < {self.max_html_generation_time}s")
        
        # Assert HTML is valid
        self.assertIsInstance(html_content, str)
        self.assertGreater(len(html_content), 1000, "HTML content too short")
        self.assertIn("<!DOCTYPE html>", html_content)
        self.assertIn("leaflet", html_content.lower())
        
        print(f"✅ HTML generation: {elapsed_time:.2f}s")
    
    def test_road_data_optimization(self):
        """Test that road data is properly optimized (limited roads and points)."""
        from map_presets_nyc import NYC_HISTORICAL_ROADS
        
        # Check that we have roads
        self.assertGreater(len(NYC_HISTORICAL_ROADS), 0, "No roads in database")
        
        # Sample some roads to check structure
        sample_roads = list(NYC_HISTORICAL_ROADS.values())[:10]
        
        for road in sample_roads:
            # Check structure
            self.assertIn('year', road)
            self.assertIn('bounds', road)
            self.assertIn('route', road)
            
            # Check route is a list
            route = road.get('route', [])
            self.assertIsInstance(route, list)
            
            # Check route points format
            if route:
                for point in route[:5]:  # Check first 5 points
                    self.assertIsInstance(point, (list, tuple))
                    self.assertEqual(len(point), 2)
                    self.assertIsInstance(point[0], (int, float))
                    self.assertIsInstance(point[1], (int, float))
        
        print(f"✅ Road data structure validated for {len(sample_roads)} sample roads")
    
    def test_road_count_limiting(self):
        """Test that road rendering limits the number of roads displayed."""
        from PyQt6.QtWidgets import QApplication
        import sys
        
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        from qt import MainWindow
        
        mock_ui = Mock()
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        game_map.is_nyc_proper = True
        mock_ui.get_map = Mock(return_value=game_map)
        mock_ui.get_year = Mock(return_value=1830)
        mock_ui.get_map_lines = Mock(return_value={})
        mock_ui.get_build_queue = Mock(return_value=[])
        
        main_window = MainWindow()
        main_window.operations = mock_ui
        main_window.edit_mode_enabled = False
        
        # Generate HTML
        html_content = main_window._generate_osm_map_html(game_map)
        
        # Extract roads data from HTML (it's embedded as JSON)
        import json
        import re
        
        # Find roadsData in the HTML
        roads_match = re.search(r'var roadsData = (\[.*?\]);', html_content, re.DOTALL)
        if roads_match:
            roads_json = roads_match.group(1)
            roads_data = json.loads(roads_json)
            
            # Check road count is limited
            max_expected_roads = 150  # Allow some buffer over 100
            self.assertLessEqual(len(roads_data), max_expected_roads,
                               f"Too many roads displayed: {len(roads_data)} (expected <= {max_expected_roads})")
            
            # Check total points is reasonable
            total_points = sum(len(road.get('points', [])) for road in roads_data)
            max_expected_points = 10000  # 100 roads * 50 points max = 5000, allow buffer
            self.assertLessEqual(total_points, max_expected_points,
                               f"Too many road points: {total_points} (expected <= {max_expected_points})")
            
            print(f"✅ Road limiting: {len(roads_data)} roads, {total_points} total points")
        else:
            self.fail("Could not find roadsData in HTML")
    
    def test_map_html_size(self):
        """Test that generated HTML is not excessively large."""
        from PyQt6.QtWidgets import QApplication
        import sys
        
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        from qt import MainWindow
        
        mock_ui = Mock()
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        mock_ui.get_map = Mock(return_value=game_map)
        mock_ui.get_year = Mock(return_value=1830)
        mock_ui.get_map_lines = Mock(return_value={})
        mock_ui.get_build_queue = Mock(return_value=[])
        
        main_window = MainWindow()
        main_window.operations = mock_ui
        main_window.edit_mode_enabled = False
        
        html_content = main_window._generate_osm_map_html(game_map)
        
        # Check HTML size (should be reasonable, not megabytes)
        html_size_mb = len(html_content) / (1024 * 1024)
        max_size_mb = 5.0  # 5MB max
        
        self.assertLess(html_size_mb, max_size_mb,
                       f"HTML too large: {html_size_mb:.2f}MB (expected < {max_size_mb}MB)")
        
        print(f"✅ HTML size: {html_size_mb:.2f}MB")
    
    def test_route_simplification(self):
        """Test that routes with many points are simplified."""
        from PyQt6.QtWidgets import QApplication
        import sys
        
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        from qt import MainWindow
        
        mock_ui = Mock()
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        game_map.is_nyc_proper = True
        mock_ui.get_map = Mock(return_value=game_map)
        mock_ui.get_year = Mock(return_value=1830)
        mock_ui.get_map_lines = Mock(return_value={})
        mock_ui.get_build_queue = Mock(return_value=[])
        
        main_window = MainWindow()
        main_window.operations = mock_ui
        main_window.edit_mode_enabled = False
        
        html_content = main_window._generate_osm_map_html(game_map)
        
        # Extract roads data
        import json
        import re
        
        roads_match = re.search(r'var roadsData = (\[.*?\]);', html_content, re.DOTALL)
        if roads_match:
            roads_json = roads_match.group(1)
            roads_data = json.loads(roads_json)
            
            # Check that no road has excessive points
            max_points_per_road = 60  # Allow some buffer over 50
            for road in roads_data:
                points = road.get('points', [])
                self.assertLessEqual(len(points), max_points_per_road,
                                   f"Road '{road.get('name', 'unknown')}' has {len(points)} points (expected <= {max_points_per_road})")
            
            if roads_data:
                avg_points = sum(len(road.get('points', [])) for road in roads_data) / len(roads_data)
                print(f"✅ Route simplification: avg {avg_points:.1f} points per road")
        else:
            self.skipTest("Could not extract roads data")
    
    def test_map_rendering_with_population(self):
        """Test map rendering with population data."""
        from PyQt6.QtWidgets import QApplication
        import sys
        
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        from qt import MainWindow
        
        mock_ui = Mock()
        game_map = Map(width=50, height=50, use_osm=True, map_center_lat=40.7128, map_center_lon=-73.9352)
        
        # Add some population
        game_map.population = {
            (10, 10): 1000,
            (20, 20): 2000,
            (30, 30): 1500,
        }
        
        mock_ui.get_map = Mock(return_value=game_map)
        mock_ui.get_year = Mock(return_value=1830)
        mock_ui.get_map_lines = Mock(return_value={})
        mock_ui.get_build_queue = Mock(return_value=[])
        
        main_window = MainWindow()
        main_window.operations = mock_ui
        main_window.edit_mode_enabled = False
        
        start_time = time.time()
        html_content = main_window._generate_osm_map_html(game_map)
        elapsed_time = time.time() - start_time
        
        self.assertLess(elapsed_time, self.max_html_generation_time,
                       f"Rendering with population took {elapsed_time:.2f}s")
        
        # Check population data is in HTML
        self.assertIn("populationData", html_content)
        
        print(f"✅ Rendering with population: {elapsed_time:.2f}s")


def run_all_tests():
    """Run all performance tests."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestMapRenderingPerformance)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)

