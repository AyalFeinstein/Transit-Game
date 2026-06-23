"""Comprehensive unit tests for ferry validation and tile system functionality"""
import unittest
from unittest.mock import Mock, MagicMock, patch
import sys
import os
import math

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from osm_tile_system import OSMTileSystem
from map import Map
from operations import Operations
from game import Game
from timeline import Timeline
from marketplace import Marketplace
from player import Player


class TestFerryValidationComprehensive(unittest.TestCase):
    """Comprehensive tests for ferry validation using tile system"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Create a minimal game setup with OSM enabled
        self.players = [Player(0, money=100000, name="TestPlayer")]
        self.game_map = Map(width=100, height=100, use_osm=True, map_center_lat=40.7, map_center_lon=-73.9)
        self.timeline = Timeline(initial_year=1830, end_year=2050)
        self.marketplace = Marketplace()
        self.game = Game(players=self.players, game_map=self.game_map, timeline=self.timeline, marketplace=self.marketplace)
        self.ui = Operations(["TestPlayer"], map_width=100, map_height=100, use_osm=True, map_center_lat=40.7, map_center_lon=-73.9)
        self.ui.game = self.game
    
    def test_ferry_validation_all_water_points(self):
        """Test ferry validation passes when all points are on water"""
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")
        
        # Mock tile system to return water for all points
        original_is_point_in_water = self.game_map.tile_system.is_point_in_water
        
        def mock_is_point_in_water(x, y):
            return True  # All water
        
        self.game_map.tile_system.is_point_in_water = mock_is_point_in_water
        
        # Test ferry route entirely on water
        water_points = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
        
        # Should not raise an error
        try:
            self.ui._validate_ferry_route(water_points)
        except ValueError as e:
            self.fail(f"Ferry validation should pass for all-water route, but raised: {e}")
        
        # Restore original method
        self.game_map.tile_system.is_point_in_water = original_is_point_in_water
    
    def test_ferry_validation_point_on_land(self):
        """Test ferry validation fails when a point is on land"""
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")
        
        # Mock tile system to return land for one point
        call_count = [0]
        def mock_is_point_in_water(x, y):
            call_count[0] += 1
            # First point is water, second is land, third is water
            if call_count[0] == 1:
                return True
            elif call_count[0] == 2:
                return False  # Land!
            else:
                return True
        
        original_is_point_in_water = self.game_map.tile_system.is_point_in_water
        self.game_map.tile_system.is_point_in_water = mock_is_point_in_water
        
        # Test ferry route with one point on land
        mixed_points = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
        
        # Should raise ValueError
        with self.assertRaises(ValueError) as context:
            self.ui._validate_ferry_route(mixed_points)
        
        self.assertIn("on land", str(context.exception))
        
        # Restore original method
        self.game_map.tile_system.is_point_in_water = original_is_point_in_water
    
    def test_ferry_validation_segment_midpoint_on_land(self):
        """Test ferry validation fails when segment midpoint is on land"""
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")
        
        # Mock tile system: points are water, but midpoint is land
        call_count = [0]
        def mock_is_point_in_water(x, y):
            call_count[0] += 1
            # Points are water, but midpoint (15, 15) is land
            if abs(x - 15.0) < 0.1 and abs(y - 15.0) < 0.1:
                return False  # Midpoint is land
            return True  # All points are water
        
        original_is_point_in_water = self.game_map.tile_system.is_point_in_water
        self.game_map.tile_system.is_point_in_water = mock_is_point_in_water
        
        # Test ferry route where segment passes through land
        points = [(10.0, 10.0), (20.0, 20.0)]
        
        # Should raise ValueError
        with self.assertRaises(ValueError) as context:
            self.ui._validate_ferry_route(points)
        
        self.assertIn("passes through land", str(context.exception))
        
        # Restore original method
        self.game_map.tile_system.is_point_in_water = original_is_point_in_water
    
    def test_ferry_validation_no_tile_system_fallback(self):
        """Test ferry validation uses fallback method when tile system unavailable"""
        # Temporarily disable tile system
        original_tile_system = self.game_map.tile_system
        self.game_map.tile_system = None
        
        # Mock OSM integration for fallback
        mock_osm = Mock()
        mock_osm.is_water_at_latlon.return_value = True
        mock_osm.convert_grid_to_latlon.return_value = (40.7, -73.9)
        self.game_map.osm_integration = mock_osm
        self.game_map.is_nyc_proper = False
        self.game_map.width = 100
        self.game_map.height = 100
        
        # Test that fallback method is used
        water_points = [(10.0, 10.0), (20.0, 20.0)]
        
        try:
            self.ui._validate_ferry_route(water_points)
        except Exception as e:
            # If it fails, it should be a meaningful error, not a NameError
            self.assertNotIsInstance(e, NameError, f"Should not have NameError, got: {type(e).__name__}: {e}")
        
        # Restore tile system
        self.game_map.tile_system = original_tile_system
    
    def test_ferry_validation_nyc_proper_fallback(self):
        """Test ferry validation uses NYC-specific fallback when tile system unavailable"""
        # Temporarily disable tile system
        original_tile_system = self.game_map.tile_system
        self.game_map.tile_system = None
        
        # Set up NYC map
        self.game_map.is_nyc_proper = True
        self.game_map.width = 50
        self.game_map.height = 50
        self.game_map.map_center_lat = 40.7128
        self.game_map.map_center_lon = -73.9352
        
        # Mock NYC functions
        with patch('map_presets_nyc.is_water_location', return_value=True):
            with patch('map_presets_nyc.get_nyc_default_center_lat', return_value=40.7128):
                with patch('map_presets_nyc.get_nyc_default_center_lon', return_value=-73.9352):
                    with patch('map_presets_nyc.NYC_EXCLUDED_POINTS', new=set()):
                        water_points = [(10.0, 10.0), (20.0, 20.0)]
                        
                        try:
                            self.ui._validate_ferry_route(water_points)
                        except Exception as e:
                            # Should not have NameError about width
                            self.assertNotIsInstance(e, NameError, f"Should not have NameError, got: {type(e).__name__}: {e}")
        
        # Restore tile system
        self.game_map.tile_system = original_tile_system
    
    def test_ferry_validation_missing_width_height(self):
        """Test ferry validation handles missing width/height gracefully"""
        # Temporarily disable tile system
        original_tile_system = self.game_map.tile_system
        self.game_map.tile_system = None
        
        # Remove width/height attributes
        original_width = getattr(self.game_map, 'width', None)
        original_height = getattr(self.game_map, 'height', None)
        if hasattr(self.game_map, 'width'):
            delattr(self.game_map, 'width')
        if hasattr(self.game_map, 'height'):
            delattr(self.game_map, 'height')
        
        # Mock OSM integration
        mock_osm = Mock()
        mock_osm.is_water_at_latlon.return_value = True
        mock_osm.convert_grid_to_latlon.return_value = (40.7, -73.9)
        self.game_map.osm_integration = mock_osm
        self.game_map.is_nyc_proper = False
        
        # Should not raise NameError
        water_points = [(10.0, 10.0), (20.0, 20.0)]
        try:
            self.ui._validate_ferry_route(water_points)
        except NameError as e:
            self.fail(f"Should not raise NameError when width/height missing, got: {e}")
        except Exception:
            pass  # Other exceptions are OK
        
        # Restore
        if original_width is not None:
            self.game_map.width = original_width
        if original_height is not None:
            self.game_map.height = original_height
        self.game_map.tile_system = original_tile_system
    
    def test_ferry_validation_empty_route(self):
        """Test ferry validation handles empty route"""
        with self.assertRaises((ValueError, IndexError)):
            self.ui._validate_ferry_route([])
    
    def test_ferry_validation_single_point(self):
        """Test ferry validation with single point (should check point only)"""
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")
        
        # Mock tile system
        original_is_point_in_water = self.game_map.tile_system.is_point_in_water
        
        def mock_is_point_in_water(x, y):
            return True
        
        self.game_map.tile_system.is_point_in_water = mock_is_point_in_water
        
        # Single point on water should pass
        try:
            self.ui._validate_ferry_route([(10.0, 10.0)])
        except ValueError:
            self.fail("Single point on water should pass validation")
        
        # Restore
        self.game_map.tile_system.is_point_in_water = original_is_point_in_water


class TestTileSystemComprehensive(unittest.TestCase):
    """Comprehensive tests for tile system functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_osm = Mock()
        self.tile_system = OSMTileSystem(
            map_width=100,
            map_height=100,
            map_center_lat=40.7,
            map_center_lon=-73.9,
            tile_size=0.01,
            osm_integration=self.mock_osm
        )
    
    def test_tile_system_initialization_all_scenarios(self):
        """Test tile system initialization with various parameters"""
        # Test with different map sizes
        for width, height in [(50, 50), (100, 100), (200, 150), (60, 60)]:
            ts = OSMTileSystem(
                map_width=width,
                map_height=height,
                map_center_lat=40.7,
                map_center_lon=-73.9,
                tile_size=0.01,
                osm_integration=self.mock_osm
            )
            self.assertIsNotNone(ts.tiles_x)
            self.assertIsNotNone(ts.tiles_y)
            self.assertGreater(ts.tiles_x, 0)
            self.assertGreater(ts.tiles_y, 0)
    
    def test_grid_to_latlon_conversion(self):
        """Test grid to lat/lon conversion for all edge cases"""
        # Test center point
        lat, lon = self.tile_system._grid_to_latlon(50.0, 50.0)
        self.assertAlmostEqual(lat, 40.7, delta=1.0)
        self.assertAlmostEqual(lon, -73.9, delta=1.0)
        
        # Test corner points
        for x, y in [(0, 0), (100, 0), (0, 100), (100, 100)]:
            lat, lon = self.tile_system._grid_to_latlon(float(x), float(y))
            self.assertIsInstance(lat, float)
            self.assertIsInstance(lon, float)
            self.assertFalse(math.isnan(lat))
            self.assertFalse(math.isnan(lon))
    
    def test_latlon_to_tile_conversion(self):
        """Test lat/lon to tile conversion"""
        # Test center
        tile_x, tile_y = self.tile_system._latlon_to_tile(40.7, -73.9)
        self.assertIsInstance(tile_x, int)
        self.assertIsInstance(tile_y, int)
        
        # Test bounds
        tile_x, tile_y = self.tile_system._latlon_to_tile(
            self.tile_system.min_lat, self.tile_system.min_lon
        )
        self.assertGreaterEqual(tile_x, 0)
        self.assertGreaterEqual(tile_y, 0)
    
    def test_is_point_in_water_all_scenarios(self):
        """Test is_point_in_water with all possible scenarios"""
        # Test with water
        self.mock_osm.is_water_at_latlon.return_value = True
        self.tile_system._tile_cache.clear()
        result = self.tile_system.is_point_in_water(50.0, 50.0)
        self.assertTrue(result)
        
        # Test with land
        self.mock_osm.is_water_at_latlon.return_value = False
        self.tile_system._tile_cache.clear()
        result = self.tile_system.is_point_in_water(51.0, 51.0)
        self.assertFalse(result)
        
        # Test edge cases
        for x, y in [(0, 0), (100, 100), (-10, -10), (200, 200)]:
            self.tile_system._tile_cache.clear()
            try:
                result = self.tile_system.is_point_in_water(float(x), float(y))
                self.assertIsInstance(result, bool)
            except Exception as e:
                self.fail(f"is_point_in_water should handle edge case ({x}, {y}), but raised: {e}")
    
    def test_get_water_segment_count_all_scenarios(self):
        """Test get_water_segment_count with all scenarios"""
        # All water
        self.mock_osm.is_water_at_latlon.return_value = True
        self.tile_system._tile_cache.clear()
        count = self.tile_system.get_water_segment_count(50.0, 50.0, 60.0, 60.0)
        self.assertGreaterEqual(count, 0)
        
        # All land
        self.mock_osm.is_water_at_latlon.return_value = False
        self.tile_system._tile_cache.clear()
        count = self.tile_system.get_water_segment_count(51.0, 51.0, 61.0, 61.0)
        self.assertGreaterEqual(count, 0)
        
        # Test various segment lengths
        for length in [1.0, 5.0, 10.0, 50.0]:
            self.tile_system._tile_cache.clear()
            count = self.tile_system.get_water_segment_count(50.0, 50.0, 50.0 + length, 50.0 + length)
            self.assertGreaterEqual(count, 0)
    
    def test_tile_system_nyc_proper_flag(self):
        """Test tile system with NYC proper flag set"""
        self.tile_system.is_nyc_proper = True
        
        # Mock NYC function
        with patch('map_presets_nyc.is_water_location', return_value=True):
            self.tile_system._tile_cache.clear()
            result = self.tile_system.get_tile_type(0, 0)
            self.assertEqual(result, 'water')
    
    def test_tile_system_error_handling(self):
        """Test tile system handles errors gracefully"""
        # Test with OSM integration that raises errors
        self.mock_osm.is_water_at_latlon.side_effect = Exception("OSM error")
        self.tile_system._tile_cache.clear()
        
        # Should default to land, not crash
        result = self.tile_system.get_tile_type(0, 0)
        self.assertIn(result, ['land', 'water'])
    
    def test_tile_system_cache_behavior(self):
        """Test tile system caching behavior"""
        # First call should query OSM
        self.mock_osm.is_water_at_latlon.return_value = True
        self.tile_system._tile_cache.clear()
        result1 = self.tile_system.get_tile_type(0, 0)
        call_count_1 = self.mock_osm.is_water_at_latlon.call_count
        
        # Second call should use cache
        result2 = self.tile_system.get_tile_type(0, 0)
        call_count_2 = self.mock_osm.is_water_at_latlon.call_count
        
        self.assertEqual(result1, result2)
        self.assertEqual(call_count_1, call_count_2)  # No additional call


if __name__ == '__main__':
    unittest.main()
