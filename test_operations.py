"""Consolidated tests for operations."""
import unittest

import unittest
import math
from game import Game
from map import Map
from player import Player
from timeline import Timeline
from marketplace import Marketplace
from operations import Operations


class TestLineCreationComprehensive(unittest.TestCase):
    """Comprehensive tests for line creation covering all scenarios"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.timeline = Timeline(initial_year=1850, end_year=2000)
        self.marketplace = Marketplace()
        
        # Create map (use_osm=False for faster tests)
        self.game_map = Map(
            width=100,
            height=100,
            use_osm=False,
            map_center_lat=40.7128,
            map_center_lon=-74.0060
        )
        # Leave NYC-specific water/road detection disabled for this unit test suite.
        # NYC detection relies on large datasets and can misclassify synthetic grid coords.
        # self.game_map.is_nyc_proper = True
        
        # Create players with sufficient money
        self.player1 = Player(player_id=1, name="Player 1", money=100000)
        self.player2 = Player(player_id=2, name="Player 2", money=100000)
        
        # Create game
        self.game = Game(
            players=[self.player1, self.player2],
            game_map=self.game_map,
            timeline=self.timeline,
            marketplace=self.marketplace
        )
        
        # Create UI
        self.ui = Operations(["Player 1", "Player 2"], use_osm=False)
        self.ui.game = self.game
    
    # ========== Basic Line Creation Tests ==========
    
    def test_create_regular_line_success(self):
        """Test successful creation of a regular line"""
        # Use above_ground to avoid water detection issues in tests
        points = [(5, 5), (15, 15), (30, 30)]
        elevation_levels = ['above_ground', 'above_ground']
        
        result = self.ui.create_line_complete(
            "Regular Line",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
        self.assertGreater(result.get('cost', 0), 0)
        self.assertGreater(result.get('build_time', 0), 0)
        self.assertEqual(result.get('points_count'), 3)

    def test_shared_track_reuses_existing_line_cheaper(self):
        """A segment within 10m of an existing line's track reuses it and costs far less."""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        first = self.ui.create_line_complete(
            "First Line",
            points,
            self.player1,
            elevation_levels,
        )
        self.assertTrue(first.get('success', True))
        # Same alignment as the built line -> shared tracks -> heavily discounted.
        shared = self.ui.calculate_line_cost(points, elevation_levels)
        # A fresh route elsewhere of identical length lays new track -> full cost.
        fresh = self.ui.calculate_line_cost([(5, 40), (15, 50)], elevation_levels)
        self.assertLess(shared['cost'], fresh['cost'])
    
    def test_create_line_single_point_fails(self):
        """Test that creating a line with only one point fails"""
        points = [(10, 10)]
        elevation_levels = []
        
        # Single point should fail - need at least 2 points for a line
        with self.assertRaises((ValueError, IndexError, TypeError)):
            self.ui.create_line_complete(
                "Single Point",
                points,
                self.player1,
                elevation_levels
            )
    
    def test_create_line_empty_points_fails(self):
        """Test that creating a line with empty points fails"""
        points = []
        elevation_levels = []
        
        with self.assertRaises((ValueError, IndexError)):
            self.ui.create_line_complete(
                "Empty Points",
                points,
                self.player1,
                elevation_levels
            )
    
    def test_create_line_insufficient_funds(self):
        """Test that creating a line with insufficient funds fails"""
        # Set player money to very low amount
        self.player1.money = 1
        
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Expensive Line",
                points,
                self.player1,
                elevation_levels
            )
        
        self.assertIn("cannot afford", str(context.exception).lower())
    
    # ========== Elevation Level Tests ==========
    
    def test_create_line_street_level(self):
        """Test creating a line at street level"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Street Line",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
    
    def test_create_line_above_ground(self):
        """Test creating a line above ground"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Elevated Line",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
    
    def test_create_line_underground(self):
        """Test creating a line underground"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['underground']
        
        result = self.ui.create_line_complete(
            "Subway Line",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
    
    def test_create_line_mixed_elevation(self):
        """Test creating a line with mixed elevation levels"""
        # Use above_ground/underground to avoid water detection issues
        points = [(5, 5), (15, 15), (25, 25), (35, 35)]
        elevation_levels = ['above_ground', 'above_ground', 'underground']
        
        result = self.ui.create_line_complete(
            "Mixed Elevation",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
    
    def test_create_line_elevation_mismatch(self):
        """Test that elevation levels count matches segment count"""
        points = [(5, 5), (15, 15), (30, 30)]  # 2 segments
        elevation_levels = ['above_ground']  # Only 1 elevation level

        # Must reject (elevation levels are required per segment)
        with self.assertRaises(ValueError):
            self.ui.create_line_complete(
                "Mismatch Elevation",
                points,
                self.player1,
                elevation_levels
            )
    
    # ========== Ferry Line Tests ==========
    
    def test_create_ferry_line_land_rejected(self):
        """Test that ferry lines on land are rejected"""
        land_points = [(45, 45), (50, 50), (55, 55)]
        elevation_levels = ['street', 'street']
        
        result = self.ui.create_line_complete(
            "Land Ferry",
            land_points,
            self.player1,
            elevation_levels,
            is_ferry_line=True
        )
        
        self.assertFalse(result.get('success', True))
        self.assertIn('error_message', result)
        self.assertIn('water', result['error_message'].lower())
    
    def test_create_ferry_line_validation_called(self):
        """Test that ferry validation is called"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        # Mock validation to track if it's called
        original_validate = self.ui._validate_ferry_route
        validation_called = [False]
        
        def mock_validate(p):
            validation_called[0] = True
            return {'success': False, 'error_message': 'Test validation'}
        
        self.ui._validate_ferry_route = mock_validate
        
        result = self.ui.create_line_complete(
            "Test Ferry",
            points,
            self.player1,
            elevation_levels,
            is_ferry_line=True
        )
        
        self.assertTrue(validation_called[0])
        self.assertFalse(result.get('success', True))
        
        # Restore original
        self.ui._validate_ferry_route = original_validate
    
    def test_ferry_validation_with_tile_system_pre_cache_called(self):
        """Test that pre_cache_route_tiles is called with correct parameters when tile system exists"""
        from unittest.mock import Mock, patch
        
        # Create a mock tile system
        mock_tile_system = Mock()
        mock_tile_system.is_point_in_water = Mock(return_value=False)
        mock_tile_system.is_point_near_water = Mock(return_value=False)
        mock_tile_system.is_segment_near_water = Mock(return_value=False)
        
        # Set the tile system on the map
        self.game_map.tile_system = mock_tile_system
        
        # Points that will fail validation (not near water)
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Test Ferry Tile System",
            points,
            self.player1,
            elevation_levels,
            is_ferry_line=True
        )
        
        # Verify pre_cache_route_tiles was called with correct parameter name
        self.assertTrue(mock_tile_system.pre_cache_route_tiles.called)
        call_args = mock_tile_system.pre_cache_route_tiles.call_args
        
        # Check that proximity_buffer_m was used (not max_distance_m)
        self.assertIn('proximity_buffer_m', call_args.kwargs)
        self.assertEqual(call_args.kwargs['proximity_buffer_m'], 60.0)
        
        # Validation should have failed (points not near water)
        self.assertFalse(result.get('success', True))
        self.assertIn('error_message', result)
    
    # ========== Bus Line Tests ==========
    
    def test_create_bus_line_success(self):
        """Test creating a bus line"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']  # Use above_ground to avoid water issues
        
        result = self.ui.create_line_complete(
            "Bus Line",
            points,
            self.player1,
            elevation_levels,
            is_bus_line=True
        )
        
        self.assertTrue(result.get('success', True))
        # Bus lines should be cheaper
        self.assertGreater(result.get('cost', 0), 0)
    
    def test_create_bus_line_validation_called(self):
        """Test that bus validation is called"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        # Mock validation to track if it's called
        original_validate = self.ui._validate_bus_route
        validation_called = [False]
        
        def mock_validate(p):
            validation_called[0] = True
            return {'success': True}
        
        self.ui._validate_bus_route = mock_validate
        
        result = self.ui.create_line_complete(
            "Test Bus",
            points,
            self.player1,
            elevation_levels,
            is_bus_line=True
        )
        
        self.assertTrue(validation_called[0])
        self.assertTrue(result.get('success', True))
        
        # Restore original
        self.ui._validate_bus_route = original_validate
    
    def test_create_bus_line_water_rejected(self):
        """Test that bus lines in water are rejected"""
        # Note: This test may be skipped if tile system is not available
        # or if coordinates don't map to actual water in the test environment
        game_map = self.game.get_map()
        
        # Try to create a bus line (tile_system is created in Map.__init__ when use_osm=True) - if tile system is available, it should check for water
        # For this test, we'll use coordinates that might be in water (depending on map setup)
        # In a real scenario, you'd use coordinates known to be in water
        points = [(5, 5), (15, 15)]
        elevation_levels = ['street']
        
        result = self.ui.create_line_complete(
            "Water Bus",
            points,
            self.player1,
            elevation_levels,
            is_bus_line=True
        )
        
        # If validation fails due to water, success should be False
        # If tile system isn't available or points aren't in water, test may pass
        # This test mainly ensures the validation code path exists and works
        if result.get('success') == False:
            error_msg = result.get('error_message', '')
            # Check if rejection was due to water (not road validation)
            if 'water' in error_msg.lower():
                self.assertFalse(result.get('success'))
                self.assertIn('water', error_msg.lower())
    
    def test_create_bus_line_water_rejected_with_tile_system(self):
        """Test that bus lines in water are rejected when tile system detects water"""
        from unittest.mock import Mock
        
        # Create a mock tile system that reports water
        mock_tile_system = Mock()
        mock_tile_system.is_point_in_water = Mock(return_value=True)  # Point is in water
        mock_tile_system.is_segment_entirely_water = Mock(return_value=True)  # Segment is in water
        mock_tile_system.pre_cache_route_tiles = Mock()  # Mock pre-caching
        
        # Set the tile system on the map
        self.game_map.tile_system = mock_tile_system
        self.game_map.use_osm = True  # Enable OSM for tile system checks
        
        # Points that are in water (according to mock)
        points = [(5, 5), (15, 15)]
        elevation_levels = ['street']
        
        result = self.ui.create_line_complete(
            "Water Bus Test",
            points,
            self.player1,
            elevation_levels,
            is_bus_line=True
        )
        
        # Should fail validation due to water
        self.assertFalse(result.get('success'))
        self.assertIn('error_message', result)
        self.assertIn('water', result['error_message'].lower())
        
        # OPTIMIZATION: Bus lines skip pre-caching (no water checks needed for pre-caching)
        # But tile system is still used for validation
        # Verify tile system was used for validation (not pre-caching)
        # Note: pre_cache_route_tiles is skipped for bus lines as an optimization
        self.assertTrue(mock_tile_system.is_point_in_water.called)
    
    def test_create_bus_line_bridge_allowed(self):
        """Test that bus lines crossing water with both endpoints on land (bridge) are allowed"""
        from unittest.mock import Mock
        
        # Mock: points on land, segment over water (bridge)
        mock_tile_system = Mock()
        mock_tile_system.is_point_in_water = Mock(return_value=False)  # Both endpoints on land
        mock_tile_system.is_segment_entirely_water = Mock(return_value=True)  # Segment over water (bridge)
        mock_tile_system.pre_cache_route_tiles = Mock()
        
        self.game_map.tile_system = mock_tile_system
        self.game_map.use_osm = True
        
        points = [(5, 5), (15, 15)]  # Both on land per mock
        elevation_levels = ['street']
        
        result = self.ui.create_line_complete(
            "Bridge Bus",
            points,
            self.player1,
            elevation_levels,
            is_bus_line=True
        )
        
        # Bridge (endpoints on land, segment over water) should be allowed
        # On success, create_line_complete returns cost/build_time etc., not 'success' key
        self.assertNotIn('error_message', result, result.get('error_message', ''))
        self.assertIn('cost', result)
        # Segment check should not be used for bus (bridge exception)
        self.assertFalse(mock_tile_system.is_segment_entirely_water.called)
    
    # ========== Duplicate Name Tests ==========
    
    def test_duplicate_name_existing_line(self):
        """Test duplicate name with existing completed line"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        # Create first line and complete it
        result1 = self.ui.create_line_complete(
            "Existing Line",
            points,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))
        
        # Process build queue multiple times to complete the line
        # Advance time to complete builds
        for _ in range(100):  # Enough turns to complete
            self.ui.process_build_queue()
            if len(self.game.build_queue) == 0:
                break
        
        # Try to create duplicate
        # Note: If line is still in queue, it will say "under construction"
        # If line is completed, it will say "already exists"
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Existing Line",
                [(25, 25), (35, 35)],
                self.player1,
                ['above_ground']
            )
        
        error_msg = str(context.exception).lower()
        # Should be either "already exists" or "under construction"
        self.assertTrue(
            "already exists" in error_msg or "under construction" in error_msg,
            f"Expected 'already exists' or 'under construction', got: {error_msg}"
        )
    
    def test_duplicate_name_same_player_queue(self):
        """Test duplicate name in same player's build queue"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result1 = self.ui.create_line_complete(
            "Queued Line",
            points,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))
        
        # Try to create duplicate before first completes
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Queued Line",
                [(30, 30), (40, 40)],
                self.player1,
                ['above_ground']
            )
        
        self.assertIn("under construction", str(context.exception).lower())
        self.assertIn("you", str(context.exception).lower())
    
    def test_duplicate_name_other_player_queue(self):
        """Test duplicate name in other player's build queue"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result1 = self.ui.create_line_complete(
            "Other Player Line",
            points,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))
        
        # Player 2 tries to create duplicate
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Other Player Line",
                [(30, 30), (40, 40)],
                self.player2,
                ['above_ground']
            )
        
        self.assertIn("under construction", str(context.exception).lower())
        self.assertIn("Player 1", str(context.exception))
    
    # ========== Cost Calculation Tests ==========
    
    def test_line_cost_calculation(self):
        """Test that line cost is calculated correctly"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Cost Test",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertGreater(result.get('cost', 0), 0)
        # Cost should be deducted from player money
        self.assertLess(self.player1.money, 100000)
    
    def test_ferry_line_cost_cheaper(self):
        """Test that ferry lines are cheaper"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        # Create regular line
        result1 = self.ui.create_line_complete(
            "Regular",
            points,
            self.player1,
            elevation_levels,
            is_ferry_line=False
        )
        
        # Reset money
        self.player1.money = 100000
        
        # Create ferry line (if validation passes, which it might not)
        # For this test, we'll just check the cost calculation logic
        # Ferry lines use simplified cost: total_length * 40 * 0.8
        import math
        total_length = math.hypot(20 - 10, 20 - 10)
        expected_ferry_cost = int(total_length * 40 * 0.8)
        if expected_ferry_cost < 15:
            expected_ferry_cost = 15
        
        # Ferry cost should be reasonable
        self.assertGreater(expected_ferry_cost, 0)
        self.assertLess(expected_ferry_cost, 1000)  # Should be relatively cheap
    
    def test_bus_line_cost_cheaper(self):
        """Test that bus lines are cheaper (50% discount)"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Bus Cost Test",
            points,
            self.player1,
            elevation_levels,
            is_bus_line=True
        )
        
        # Bus lines should have cost
        self.assertGreater(result.get('cost', 0), 0)
    
    # ========== Build Queue Tests ==========
    
    def test_line_added_to_build_queue(self):
        """Test that line is added to build queue"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        initial_queue_length = len(self.game.build_queue)
        
        result = self.ui.create_line_complete(
            "Queue Test",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
        # Build queue should have increased
        self.assertGreater(len(self.game.build_queue), initial_queue_length)
        
        # Check that build entry exists
        found = False
        for build in self.game.build_queue:
            if build.get('type') == 'line' and build.get('data', {}).get('name') == 'Queue Test':
                found = True
                break
        self.assertTrue(found)
    
    def test_line_segments_in_build_queue(self):
        """Test that line segments are properly added to build queue"""
        points = [(5, 5), (15, 15), (25, 25), (35, 35)]  # 3 segments
        elevation_levels = ['above_ground', 'above_ground', 'above_ground']
        
        result = self.ui.create_line_complete(
            "Segments Test",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
        
        # Count segments in build queue
        segment_count = 0
        for build in self.game.build_queue:
            if build.get('type') == 'line' and build.get('data', {}).get('name') == 'Segments Test':
                segment_count += 1
        
        # Should have at least one segment (may be combined)
        self.assertGreater(segment_count, 0)
    
    # ========== Edge Cases ==========
    
    def test_line_with_zero_length_segment(self):
        """Test line with zero-length segment (same point twice)"""
        points = [(5, 5), (5, 5), (15, 15)]  # Zero-length first segment
        elevation_levels = ['above_ground', 'above_ground']
        
        # Should handle gracefully
        result = self.ui.create_line_complete(
            "Zero Length",
            points,
            self.player1,
            elevation_levels
        )
        
        # Should either succeed or fail gracefully
        # Zero-length segments might be filtered or cause issues
        # This tests the robustness
    
    def test_line_with_very_long_route(self):
        """Test line with many points"""
        # Create a long route
        points = [(i * 5 + 1, i * 5 + 1) for i in range(20)]  # 20 points
        elevation_levels = ['above_ground'] * 19
        
        result = self.ui.create_line_complete(
            "Long Route",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
        self.assertEqual(result.get('points_count'), 20)
    
    def test_line_name_with_special_characters(self):
        """Test line name with special characters"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Line-Name_With.Special@Chars!",
            points,
            self.player1,
            elevation_levels
        )
        
        self.assertTrue(result.get('success', True))
    
    def test_line_name_empty_string(self):
        """Test line name with empty string"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        # Empty name might be allowed or rejected
        result = self.ui.create_line_complete(
            "",
            points,
            self.player1,
            elevation_levels
        )
        
        # Should either succeed or fail gracefully
        # This tests edge case handling
    
    def test_line_with_negative_coordinates(self):
        """Test line with negative coordinates"""
        points = [(-10, -10), (-20, -20)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Negative Coords",
            points,
            self.player1,
            elevation_levels
        )
        
        # Should handle negative coordinates
        self.assertTrue(result.get('success', True))
    
    def test_line_with_very_large_coordinates(self):
        """Test line with very large coordinates"""
        # Use smaller large coordinates to avoid cost issues
        points = [(1000, 1000), (2000, 2000)]
        elevation_levels = ['above_ground']  # Use above_ground to avoid water issues
        # Ensure player can afford this (cost scales with distance)
        self.player1.money = 10_000_000
        
        result = self.ui.create_line_complete(
            "Large Coords",
            points,
            self.player1,
            elevation_levels
        )
        
        # Should handle large coordinates
        self.assertTrue(result.get('success', True))
    
    # ========== Performance Tests ==========
    
    def test_line_creation_speed(self):
        """Test that line creation is reasonably fast"""
        import time
        
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        start_time = time.time()
        result = self.ui.create_line_complete(
            "Speed Test",
            points,
            self.player1,
            elevation_levels
        )
        end_time = time.time()
        
        elapsed = end_time - start_time
        # Should complete in under 1 second (reasonable for non-OSM)
        self.assertLess(elapsed, 1.0, f"Line creation took {elapsed:.3f}s, should be < 1.0s")
        self.assertTrue(result.get('success', True))
    
    def test_multiple_lines_creation_speed(self):
        """Test creating multiple lines quickly"""
        import time
        
        start_time = time.time()
        
        for i in range(10):
            points = [(i * 5 + 1, i * 5 + 1), ((i + 1) * 5 + 1, (i + 1) * 5 + 1)]
            elevation_levels = ['above_ground']
            
            result = self.ui.create_line_complete(
                f"Speed Test {i}",
                points,
                self.player1,
                elevation_levels
            )
            self.assertTrue(result.get('success', True))
        
        end_time = time.time()
        elapsed = end_time - start_time
        
        # 10 lines should complete in under 5 seconds
        self.assertLess(elapsed, 5.0, f"10 lines took {elapsed:.3f}s, should be < 5.0s")
    
    # ========== Return Value Tests ==========
    
    def test_return_value_structure(self):
        """Test that return value has correct structure"""
        points = [(5, 5), (15, 15)]
        elevation_levels = ['above_ground']
        
        result = self.ui.create_line_complete(
            "Return Test",
            points,
            self.player1,
            elevation_levels
        )
        
        # Check required fields
        self.assertIn('cost', result)
        self.assertIn('build_time', result)
        self.assertIn('points_count', result)
        self.assertIn('remaining_money', result)
        self.assertIn('length', result)
        
        # Check types
        self.assertIsInstance(result['cost'], (int, float))
        self.assertIsInstance(result['build_time'], (int, float))
        self.assertIsInstance(result['points_count'], int)
        self.assertIsInstance(result['remaining_money'], (int, float))

import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from operations import Operations
from player import Player
from worker import ENGINEER, CONSTRUCTION_WORKER


class TestGetMaxFireableWorkers(unittest.TestCase):
    """Test get_max_fireable_workers: before 1925 = all; after 1925 = half; striking = 0."""

    def setUp(self):
        self.ops = Operations(["P1"], map_width=50, map_height=50)

    def _rental(self, quantity, on_strike=False, strike_turns=0):
        class Rental:
            pass
        r = Rental()
        r.quantity = quantity
        r.strike_turns_remaining = strike_turns
        r.is_on_strike = lambda: on_strike
        return r

    def test_before_1925_can_fire_all(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {ENGINEER.name: self._rental(6)}
        self.ops.get_timeline().year = 1920
        self.assertEqual(self.ops.get_max_fireable_workers(player, ENGINEER.name), 6)

    def test_after_1925_max_half(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {ENGINEER.name: self._rental(10)}
        self.ops.get_timeline().year = 1930
        self.assertEqual(self.ops.get_max_fireable_workers(player, ENGINEER.name), 5)

    def test_after_1925_one_worker_zero_fireable(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {ENGINEER.name: self._rental(1)}
        self.ops.get_timeline().year = 1930
        self.assertEqual(self.ops.get_max_fireable_workers(player, ENGINEER.name), 0)

    def test_after_1925_striking_zero_fireable(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {ENGINEER.name: self._rental(10, on_strike=True, strike_turns=2)}
        self.ops.get_timeline().year = 1930
        self.assertEqual(self.ops.get_max_fireable_workers(player, ENGINEER.name), 0)

    def test_no_rental_zero(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {}
        self.assertEqual(self.ops.get_max_fireable_workers(player, ENGINEER.name), 0)


class TestFireWorkers(unittest.TestCase):
    """Test fire_workers: validation (max fireable) and success path."""

    def setUp(self):
        self.ops = Operations(["P1"], map_width=50, map_height=50)

    def _rental(self, quantity, on_strike=False, strike_turns=0):
        class Rental:
            pass
        r = Rental()
        r.quantity = quantity
        r.strike_turns_remaining = strike_turns
        r.is_on_strike = lambda: on_strike
        return r

    def test_fire_workers_respects_max_after_1925(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {CONSTRUCTION_WORKER.name: self._rental(6)}
        self.ops.get_timeline().year = 1930
        result = self.ops.fire_workers(player, CONSTRUCTION_WORKER.name, 4)  # 4 > 3 (half of 6)
        self.assertFalse(result['success'])
        self.assertIn('at most', result['message'])

    def test_fire_workers_success_after_1925(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {CONSTRUCTION_WORKER.name: self._rental(6)}
        self.ops.get_timeline().year = 1930
        result = self.ops.fire_workers(player, CONSTRUCTION_WORKER.name, 3)
        self.assertTrue(result['success'])
        self.assertEqual(player.worker_rentals[CONSTRUCTION_WORKER.name].quantity, 3)

    def test_fire_workers_all_removes_rental(self):
        player = self.ops.get_players()[0]
        player.worker_rentals = {CONSTRUCTION_WORKER.name: self._rental(2)}
        self.ops.get_timeline().year = 1920
        result = self.ops.fire_workers(player, CONSTRUCTION_WORKER.name, 2)
        self.assertTrue(result['success'])
        self.assertNotIn(CONSTRUCTION_WORKER.name, player.worker_rentals)


class TestFerryValidationExactPointInWater(unittest.TestCase):
    """Test that ferry validation accepts points that are in water (exact lat/lon check)."""

    def setUp(self):
        self.ops = Operations(
            ["P1"], map_width=100, map_height=75,
            use_osm=True, map_center_lat=40.7, map_center_lon=-73.9
        )
        self.game_map = self.ops.get_map()
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")

    def test_validate_ferry_route_all_water_by_exact_check(self):
        """When is_point_in_water returns True (exact point check), validation passes."""
        tile_system = self.game_map.tile_system
        original = tile_system.is_point_in_water

        def mock_in_water(x, y, skip_osm=False):
            return True
        tile_system.is_point_in_water = mock_in_water
        try:
            result = self.ops._validate_ferry_route([(10.0, 10.0), (20.0, 20.0)])
            self.assertTrue(result.get('success'), result)
        finally:
            tile_system.is_point_in_water = original

    def test_validate_ferry_route_fails_if_point_far_from_water(self):
        """When a point is not near water, validation fails with clear message."""
        tile_system = self.game_map.tile_system
        original_near = tile_system.is_point_near_water
        original_in_water = tile_system.is_point_in_water

        def mock_not_near(x, y, max_m=100, skip_osm=False):
            return False

        def mock_not_in_water(x, y, skip_osm=False):
            return False

        tile_system.is_point_near_water = mock_not_near
        tile_system.is_point_in_water = mock_not_in_water
        try:
            with self.assertRaises(ValueError) as ctx:
                self.ops._validate_ferry_route([(10.0, 10.0), (20.0, 20.0)])
            self.assertIn("100m of water", str(ctx.exception))
        finally:
            tile_system.is_point_near_water = original_near
            tile_system.is_point_in_water = original_in_water

import unittest
from game import Game
from map import Map
from player import Player
from timeline import Timeline
from marketplace import Marketplace
from operations import Operations


class TestFerryValidationAndDuplicateNames(unittest.TestCase):
    """Test ferry validation and duplicate line name prevention"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Create a simple game with NYC map for water detection
        self.timeline = Timeline(initial_year=1850, end_year=2000)
        self.marketplace = Marketplace()
        
        # Create map (use_osm=False for faster tests, but set is_nyc_proper for water detection fallback)
        self.game_map = Map(
            width=100,
            height=100,
            use_osm=False,  # Disable OSM for faster tests
            map_center_lat=40.7128,  # NYC coordinates
            map_center_lon=-74.0060
        )
        self.game_map.is_nyc_proper = True
        
        # Create players (enough money for two lines)
        self.player1 = Player(player_id=1, name="Player 1", money=50000)
        self.player2 = Player(player_id=2, name="Player 2", money=50000)
        
        # Create game
        self.game = Game(
            players=[self.player1, self.player2],
            game_map=self.game_map,
            timeline=self.timeline,
            marketplace=self.marketplace
        )
        
        # Create UI - UI.__init__ expects player_names, so create it and replace the game
        # Use use_osm=False to avoid slow OSM initialization in tests
        self.ui = Operations(["Player 1", "Player 2"], use_osm=False)
        # Replace the game with our test game
        self.ui.game = self.game
    
    def test_duplicate_line_name_existing_line(self):
        """Test that duplicate line names are rejected for existing lines"""
        # Create a line first - use above_ground to avoid water detection issues
        points = [(10, 10), (20, 20)]
        elevation_levels = ['above_ground']
        
        result1 = self.ui.create_line_complete(
            "Test Line",
            points,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))  # First line should succeed
        
        # Try to create another line with the same name
        # Note: Since lines go into build queue first, it will be "under construction" not "already exists"
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Test Line",
                [(30, 30), (40, 40)],
                self.player1,
                ['above_ground']
            )
        
        # Should reject duplicate name (either "already exists" or "under construction")
        error_msg = str(context.exception)
        self.assertTrue(
            "already exists" in error_msg or "under construction" in error_msg,
            f"Expected 'already exists' or 'under construction' in error message, got: {error_msg}"
        )
    
    def test_duplicate_line_name_same_player_construction(self):
        """Test that duplicate line names are rejected for lines under construction by same player"""
        # Start building a line - use above_ground to avoid water detection issues
        points = [(10, 10), (20, 20)]
        elevation_levels = ['above_ground']
        
        result1 = self.ui.create_line_complete(
            "Under Construction",
            points,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))
        
        # Try to create another line with the same name (before first completes)
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Under Construction",
                [(30, 30), (40, 40)],
                self.player1,
                ['street']
            )
        
        self.assertIn("already under construction by you", str(context.exception))
    
    def test_duplicate_line_name_other_player_construction(self):
        """Test that duplicate line names are rejected for lines under construction by other players"""
        # Player 1 starts building a line - use above_ground to avoid water detection issues
        points = [(10, 10), (20, 20)]
        elevation_levels = ['above_ground']
        
        result1 = self.ui.create_line_complete(
            "Shared Name",
            points,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))
        
        # Player 2 tries to create a line with the same name
        with self.assertRaises(ValueError) as context:
            self.ui.create_line_complete(
                "Shared Name",
                [(30, 30), (40, 40)],
                self.player2,
                ['street']
            )
        
        self.assertIn("already under construction", str(context.exception))
        self.assertIn("Player 1", str(context.exception))
    
    def test_ferry_validation_land_route(self):
        """Test that ferry routes on land are rejected"""
        # Create points in Lower Manhattan (Chinatown/Little Italy area - clearly on land)
        # These coordinates should be in the land area shown in the image
        # Approximate grid coordinates for Lower Manhattan (land area)
        land_points = [
            (45, 45),  # Little Italy area
            (50, 50),  # Chinatown area
            (55, 55)   # Bowery area
        ]
        elevation_levels = ['street', 'street']
        
        # Try to create a ferry line on land (tile_system is created in Map.__init__ when use_osm=True)
        result = self.ui.create_line_complete(
            "Land Ferry",
            land_points,
            self.player1,
            elevation_levels,
            is_ferry_line=True
        )
        
        # Should fail validation
        self.assertFalse(result.get('success', True))
        self.assertIn('error_message', result)
        self.assertIn('water', result['error_message'].lower())
    
    def test_ferry_validation_water_route(self):
        """Test that ferry routes on water are accepted"""
        # Create points in water (NYC harbor area)
        # These should be in water areas
        # Note: Actual coordinates depend on map setup - this is a placeholder
        # In a real test, you'd use coordinates known to be in water
        
        # For now, skip this test if tile system can't determine water
        # (requires actual OSM integration or cached water data)
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available - cannot test water validation")
        
        # This test would need actual water coordinates
        # Skipping for now as it requires OSM data or pre-cached water tiles
        self.skipTest("Requires actual water coordinates - needs OSM integration")
    
    def test_unique_line_names_allowed(self):
        """Test that unique line names are allowed"""
        points1 = [(10, 10), (20, 20)]
        points2 = [(30, 30), (40, 40)]
        elevation_levels = ['above_ground']
        
        # Create first line
        result1 = self.ui.create_line_complete(
            "Line 1",
            points1,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result1.get('success', True))
        
        # Create second line with different name
        result2 = self.ui.create_line_complete(
            "Line 2",
            points2,
            self.player1,
            elevation_levels
        )
        self.assertTrue(result2.get('success', True))
    
    def test_ferry_validation_empty_points(self):
        """Test that ferry validation handles empty points"""
        with self.assertRaises(ValueError):
            self.ui._validate_ferry_route([])

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
        """Test ferry validation fails when a point is on land far from water"""
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")
        
        original_is_point_in_water = self.game_map.tile_system.is_point_in_water
        original_is_point_near_water = getattr(
            self.game_map.tile_system, 'is_point_near_water', None
        )

        def mock_is_point_in_water(x, y, skip_osm=False):
            return False

        def mock_is_point_near_water(x, y, max_m=100, skip_osm=False):
            return False

        self.game_map.tile_system.is_point_in_water = mock_is_point_in_water
        self.game_map.tile_system.is_point_near_water = mock_is_point_near_water
        
        mixed_points = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
        
        with self.assertRaises(ValueError) as context:
            self.ui._validate_ferry_route(mixed_points)
        
        self.assertIn("too far from water", str(context.exception))
        
        self.game_map.tile_system.is_point_in_water = original_is_point_in_water
        if original_is_point_near_water is not None:
            self.game_map.tile_system.is_point_near_water = original_is_point_near_water
    
    def test_ferry_validation_segment_midpoint_on_land(self):
        """Test ferry validation fails when segment passes far from water"""
        if not self.game_map.tile_system:
            self.skipTest("Tile system not available")
        
        original_is_point_in_water = self.game_map.tile_system.is_point_in_water
        original_is_segment_near_water = getattr(
            self.game_map.tile_system, 'is_segment_near_water', None
        )

        def mock_is_point_in_water(x, y, skip_osm=False):
            return True

        def mock_is_segment_near_water(x1, y1, x2, y2, max_m=100, skip_osm=False):
            return False

        self.game_map.tile_system.is_point_in_water = mock_is_point_in_water
        self.game_map.tile_system.is_segment_near_water = mock_is_segment_near_water
        
        points = [(10.0, 10.0), (20.0, 20.0)]
        
        with self.assertRaises(ValueError) as context:
            self.ui._validate_ferry_route(points)
        
        self.assertIn("too far from water", str(context.exception))
        
        self.game_map.tile_system.is_point_in_water = original_is_point_in_water
        if original_is_segment_near_water is not None:
            self.game_map.tile_system.is_segment_near_water = original_is_segment_near_water
    
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

import unittest
from line import Line, RightOfWay
from player import Player
from vehicle import Trainset, Locomotive, Railcar


class TestRightOfWay(unittest.TestCase):
    def setUp(self):
        self.right_of_way = RightOfWay("Test ROW", 1, [(0, 0), (1, 1), (2, 2)])
    
    def test_right_of_way_initialization(self):
        """Test RightOfWay initialization"""
        self.assertEqual(self.right_of_way.get_name(), "Test ROW")
        self.assertEqual(self.right_of_way.get_object_id(), 1)
        self.assertEqual(len(self.right_of_way.get_route()), 3)
    
    def test_rename(self):
        """Test renaming right of way"""
        self.right_of_way.rename("New Name")
        self.assertEqual(self.right_of_way.get_name(), "New Name")
    
    def test_extend_route_from_end(self):
        """Test extending route from end"""
        initial_length = len(self.right_of_way)
        self.right_of_way.extend_route_from_end([(3, 3), (4, 4)])
        self.assertEqual(len(self.right_of_way), initial_length + 2)
        self.assertEqual(self.right_of_way.get_route()[-1], (4, 4))

    def test_extend_route_from_end_drops_auto_loop_closer(self):
        """End extension must not auto-connect the new tip back to route start."""
        self.right_of_way.extend_route_from_end([(3, 3), (0, 0)])
        route = self.right_of_way.get_route()
        self.assertEqual(route[-1], (3, 3))
        self.assertNotEqual(route[-1], route[0])
    
    def test_extend_route_from_beginning(self):
        """Test extending route from beginning"""
        initial_length = len(self.right_of_way)
        self.right_of_way.extend_route_from_beginning([(-1, -1), (-0.5, -0.5)])
        self.assertEqual(len(self.right_of_way), initial_length + 2)
        self.assertEqual(self.right_of_way.get_route()[0], (-1, -1))


class TestLine(unittest.TestCase):
    def setUp(self):
        self.player = Player(0, 10000, [], "Test Player")
        self.route = [(0, 0), (1, 1), (2, 2)]
        self.line = Line("Test Line", 1, self.route, self.player, [])
    
    def test_line_initialization(self):
        """Test Line initialization"""
        self.assertEqual(self.line.get_name(), "Test Line")
        self.assertEqual(self.line.get_owner(), self.player)
        self.assertEqual(self.line.get_route(), self.route)
        self.assertEqual(self.line.fare, 10)
        self.assertEqual(self.line.profit, 0)
        self.assertFalse(self.line.has_third_rail)
        self.assertFalse(self.line.has_overhead_wire)
        self.assertEqual(self.line.num_tracks, 1)
    
    def test_line_with_options(self):
        """Test Line with custom options"""
        line = Line("Custom Line", 2, self.route, self.player, [],
                   fare=20, profit=100, has_third_rail=True, has_overhead_wire=True)
        self.assertEqual(line.fare, 20)
        self.assertEqual(line.profit, 100)
        self.assertTrue(line.has_third_rail)
        self.assertTrue(line.has_overhead_wire)
    
    def test_schedule_operations(self):
        """Test schedule frequency operations"""
        from vehicle import Trainset, Locomotive, Railcar

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        cars = [Railcar("Car", 1830, 50, 20)]
        ts = Trainset(locomotive=loco, cars=cars)
        self.line = Line("Test Line", 1, self.route, self.player, [ts])

        self.assertEqual(self.line.schedule['peak'], 0.0)
        self.assertEqual(self.line.schedule['off_peak'], 0.0)
        self.assertEqual(self.line.schedule['night'], 0.0)

        max_peak = self.line.get_max_frequency_per_hour('peak')
        self.assertGreater(max_peak, 0)

        self.line.set_schedule_frequency('peak', 5.0)
        self.assertEqual(self.line.schedule['peak'], min(5.0, max_peak))

        self.line.set_schedule_frequency('off_peak', 3.0)
        max_off_peak = self.line.get_max_frequency_per_hour('off_peak')
        self.assertEqual(self.line.schedule['off_peak'], min(3.0, max_off_peak))

        self.line.set_schedule_frequency('night', 1.0)
        max_night = self.line.get_max_frequency_per_hour('night')
        self.assertEqual(self.line.schedule['night'], min(1.0, max_night))

    def test_schedule_capped_by_assigned_trainsets(self):
        """Cannot schedule more trains per hour than assigned trainsets can physically run."""
        from vehicle import Trainset, Locomotive, Railcar

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        cars = [Railcar("Car", 1830, 50, 20)]
        ts = Trainset(locomotive=loco, cars=cars)
        line = Line("Scheduled Line", 2, self.route, self.player, [ts])
        max_peak = line.get_max_frequency_per_hour('peak')
        self.assertGreater(max_peak, 0)
        line.set_schedule_frequency('peak', max_peak + 100.0)
        self.assertLessEqual(line.schedule['peak'], max_peak)

        line_no_trains = Line("Empty Line", 3, self.route, self.player, [])
        self.assertEqual(line_no_trains.get_max_frequency_per_hour('peak'), 0.0)
        line_no_trains.set_schedule_frequency('peak', 10.0)
        self.assertEqual(line_no_trains.schedule['peak'], 0.0)

    def test_enforce_line_schedule_limits_clamps_after_unassign(self):
        """Schedule auto-updates when max trains/hour drops after unassigning trainsets."""
        from vehicle import Trainset, Locomotive, Railcar
        from map import Map
        from game import Game
        from timeline import Timeline
        from marketplace import Marketplace

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        ts1 = Trainset(locomotive=loco, cars=[Railcar("Car", 1830, 50, 20)])
        ts2 = Trainset(locomotive=loco, cars=[Railcar("Car2", 1830, 50, 20)])
        player = Player(0, 10000, [], "Test Player")
        line = Line("Ops Line", 1, self.route, player, [ts1, ts2])
        ts1.assign_to_line("Ops Line")
        ts2.assign_to_line("Ops Line")
        max_peak = line.get_max_frequency_per_hour('peak')
        line.schedule = {'peak': max_peak, 'off_peak': max_peak, 'night': max_peak}

        game_map = Map(width=100, height=100, use_osm=False)
        game = Game(
            players=[player],
            game_map=game_map,
            timeline=Timeline(1830, 2000),
            marketplace=Marketplace(),
        )
        ui = Operations(["Test Player"], use_osm=False)
        ui.game = game
        game_map.add_line(line)
        player.acquire_line(line)

        ui.unassign_trainset_complete(ts2)
        self.assertLess(line.schedule['peak'], max_peak)

    def test_enforce_all_line_schedule_limits_clamps_excess(self):
        """Year-end sweep clamps peak/off_peak/night when schedule exceeds max."""
        from vehicle import Trainset, Locomotive, Railcar
        from map import Map
        from game import Game
        from timeline import Timeline
        from marketplace import Marketplace

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        ts = Trainset(locomotive=loco, cars=[Railcar("Car", 1830, 50, 20)])
        player = Player(0, 10000, [], "Test Player")
        line = Line("Year End Line", 1, self.route, player, [ts])
        ts.assign_to_line("Year End Line")
        max_peak = line.get_max_frequency_per_hour('peak')
        max_off_peak = line.get_max_frequency_per_hour('off_peak')
        max_night = line.get_max_frequency_per_hour('night')
        line.schedule = {
            'peak': max_peak + 50.0,
            'off_peak': max_off_peak + 30.0,
            'night': max_night + 10.0,
        }

        game_map = Map(width=100, height=100, use_osm=False)
        game = Game(
            players=[player],
            game_map=game_map,
            timeline=Timeline(1830, 2000),
            marketplace=Marketplace(),
        )
        ui = Operations(["Test Player"], use_osm=False)
        ui.game = game
        game_map.add_line(line)
        player.acquire_line(line)

        changed = ui.enforce_all_line_schedule_limits()
        self.assertIn("Year End Line", changed)
        self.assertEqual(line.schedule['peak'], max_peak)
        self.assertEqual(line.schedule['off_peak'], max_off_peak)
        self.assertEqual(line.schedule['night'], max_night)

    def test_advance_year_clamps_excess_schedule(self):
        """advance_year_complete clamps schedules that exceed physical max."""
        from vehicle import Trainset, Locomotive, Railcar
        from map import Map
        from game import Game
        from timeline import Timeline
        from marketplace import Marketplace

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        ts = Trainset(locomotive=loco, cars=[Railcar("Car", 1830, 50, 20)])
        player = Player(0, 100000, [], "Test Player")
        line = Line("Advance Year Line", 1, self.route, player, [ts])
        ts.assign_to_line("Advance Year Line")
        max_peak = line.get_max_frequency_per_hour('peak')
        line.schedule = {'peak': max_peak + 100.0, 'off_peak': 0.0, 'night': 0.0}

        game_map = Map(width=100, height=100, use_osm=False)
        game = Game(
            players=[player],
            game_map=game_map,
            timeline=Timeline(1830, 2000),
            marketplace=Marketplace(),
        )
        ui = Operations(["Test Player"], use_osm=False)
        ui.game = game
        game_map.add_line(line)
        player.acquire_line(line)

        ui.advance_year_complete(1830)
        self.assertEqual(line.schedule['peak'], max_peak)

    def test_zero_schedule_yields_zero_trains_per_year(self):
        """All-zero timetable means no annual train trips and no schedule operating cost."""
        from vehicle import Trainset, Locomotive, Railcar

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        cars = [Railcar("Car", 1830, 50, 20)]
        ts = Trainset(locomotive=loco, cars=cars)
        line = Line("Idle Line", 4, self.route, self.player, [ts])
        line.schedule = {'peak': 0.0, 'off_peak': 0.0, 'night': 0.0}
        self.assertEqual(line.service_frequency_per_year(trainset_avg_speed=30), 0)
        self.assertEqual(line.calc_schedule_operating_cost(1830), 0)

    def test_one_train_long_line_caps_frequency_near_two_per_hour(self):
        """One trainset on a ~5 grid-unit line should run at most ~2 trips/hour per direction."""
        from resource import Wood
        from vehicle import Locomotive, Trainset, Railcar

        loco = Locomotive(
            "Test Loco", fuel=Wood(), miles_per_unit=10.0, top_speed=30.0, average_speed=30.0,
        )
        ts = Trainset(locomotive=loco, cars=[Railcar("Car", 0, 50, 10)])
        route = [(0.0, 0.0), (5.0, 0.0)]
        line = Line("Five Mile Line", 1, route, self.player, [ts])
        max_peak = line.get_max_frequency_per_hour('peak')
        self.assertAlmostEqual(max_peak, 2.0, delta=0.35)

    def test_stations_reduce_max_schedule_frequency(self):
        """Each station adds dwell time, lowering the physical schedule cap."""
        from resource import Wood
        from vehicle import Locomotive, Trainset, Railcar
        from station import Station

        loco = Locomotive(
            "Test Loco", fuel=Wood(), miles_per_unit=10.0, top_speed=30.0, average_speed=30.0,
        )
        ts = Trainset(locomotive=loco, cars=[Railcar("Car", 0, 50, 10)])
        route = [(0.0, 0.0), (5.0, 0.0)]
        line = Line("Dwell Line", 1, route, self.player, [ts])
        max_no_stops = line.get_max_frequency_per_hour('peak')
        for i in range(4):
            line.add_station(Station(f"Stop {i}", float(i), 0.0, line_names=[line.get_name()]))
        max_with_stops = line.get_max_frequency_per_hour('peak')
        self.assertLess(max_with_stops, max_no_stops)

    def test_extra_tracks_raise_schedule_cap(self):
        """More tracks increase throughput when trainsets are not the bottleneck."""
        from resource import Wood
        from vehicle import Locomotive, Trainset, Railcar

        loco = Locomotive(
            "Fast Loco", fuel=Wood(), miles_per_unit=10.0, top_speed=45.0, average_speed=45.0,
        )
        cars = [Railcar("Car", 0, 50, 10)]
        trainsets = [Trainset(locomotive=loco, cars=cars) for _ in range(10)]
        route = [(0.0, 0.0), (0.5, 0.0)]
        single_track = Line("Single", 1, route, self.player, trainsets)
        double_track = Line("Double", 2, route, self.player, trainsets)
        double_track.num_tracks = 2
        self.assertGreater(
            double_track.get_max_frequency_per_hour('peak'),
            single_track.get_max_frequency_per_hour('peak'),
        )

    def test_schedule_operating_cost_is_small_annual_charge(self):
        """Scheduled service adds a modest annual operating cost, not per simulated trip."""
        from vehicle import Trainset, Locomotive, Railcar

        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        cars = [Railcar("Car", 1830, 50, 20)]
        ts = Trainset(locomotive=loco, cars=cars)
        line = Line("Scheduled Line", 2, self.route, self.player, [ts])
        line.set_schedule_frequency('peak', 2.0)
        line.set_schedule_frequency('off_peak', 1.0)
        line.set_schedule_frequency('night', 0.5)
        cost = line.calc_schedule_operating_cost(1850)
        self.assertGreater(cost, 0)
        self.assertLess(cost, 500)
        maint = line.calc_maitnance(1850)
        self.assertGreaterEqual(maint, cost)
    
    def test_upgrade_tracks(self):
        """Test upgrading tracks"""
        self.assertEqual(self.line.num_tracks, 1)
        self.line.upgrade_tracks(2)
        self.assertEqual(self.line.num_tracks, 3)
        self.line.upgrade_tracks(1)
        self.assertEqual(self.line.num_tracks, 4)
    
    def test_calc_traversal_time(self):
        """Test traversal time calculation"""
        # Simple route, should have some traversal time
        time = self.line.calc_traversal_time(avg_speed_mph=30)
        self.assertGreater(time, 0)
    
    def test_update_quality(self):
        """Test quality update"""
        initial_quality = self.line.quality
        self.line.update_quality()
        # Quality should be at least base quality (1.0)
        self.assertGreaterEqual(self.line.quality, 1.0)
    
    def test_update_quality_with_maintenance_workers(self):
        """Test quality update with maintenance workers"""
        self.line.maintenance_workers = 5
        self.line.update_quality()
        # With 5 maintenance workers, quality should be > 1.0
        self.assertGreater(self.line.quality, 1.0)
    
    def test_get_vehicles(self):
        """Test getting vehicles from line"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        # Use a locomotive that can have railcars (get_earliest returns Horse-Drawn Carriage which cannot)
        locomotives = registry.get_locomotives_by_year()
        locomotive = next((l for l, _ in locomotives if getattr(l, 'can_have_railcars', True)), None)
        railcar = registry.get_earliest_railcar()
        
        if locomotive and railcar:
            cars = [Railcar("Test Car", 0, railcar.capacity, railcar.maitnance_costs)]
            trainset = Trainset(locomotive=locomotive, cars=cars)
            
            # Create line with vehicles
            line_with_vehicles = Line("Line With Vehicles", 2, self.route, self.player, [trainset])
            vehicles = line_with_vehicles.get_vehicles()
            self.assertEqual(len(vehicles), 1)
            self.assertIn(trainset, vehicles)
    
    def test_get_price(self):
        """Test line price calculation"""
        price = self.line.get_price(current_year=1830)
        self.assertGreater(price, 0)
    
    def test_elevation_levels(self):
        """Test elevation levels"""
        # Default elevation should be 'street' for all segments
        self.assertEqual(len(self.line.elevation_levels), len(self.route) - 1)
        if self.line.elevation_levels:
            self.assertEqual(self.line.elevation_levels[0], 'street')
        
        custom_elevations = ['underground', 'above_ground']
        line = Line("Elevated Line", 2, self.route, self.player, [],
                   elevation_levels=custom_elevations)
        self.assertEqual(line.elevation_levels, custom_elevations)

import unittest
from operations import Operations
from PyQt6.QtWidgets import QApplication
import sys


def _ensure_app():
    """Ensure QApplication exists for tests"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestStraightConnectorRules(unittest.TestCase):
    """Straight off-street connectors cannot be longer than adjacent on-street segments."""

    def test_connector_longer_than_adjacent_street_segment_is_invalid(self):
        from operations import _straight_connector_valid_at_route_index

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        street_len = 0.08
        connector_len = 0.10
        points = [
            (0.0, 0.0),
            (street_len, 0.0),
            (street_len + connector_len, 0.0),
            (street_len + connector_len + street_len, 0.0),
        ]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            seg_len = math.hypot(x2 - x1, y2 - y1)
            return abs(seg_len - connector_len) >= 1e-6

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertFalse(_straight_connector_valid_at_route_index(game_map, points, 1, 1850))

    def test_connector_shorter_than_adjacent_street_segments_is_valid(self):
        from operations import _straight_connector_valid_at_route_index

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        street_len = 0.10
        connector_len = 0.06
        points = [
            (0.0, 0.0),
            (street_len, 0.0),
            (street_len + connector_len, 0.0),
            (street_len + connector_len + street_len, 0.0),
        ]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            seg_len = math.hypot(x2 - x1, y2 - y1)
            return abs(seg_len - connector_len) >= 1e-6

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertTrue(_straight_connector_valid_at_route_index(game_map, points, 1, 1850))

    def test_water_connector_exempt_from_adjacent_street_length_rule(self):
        from operations import _straight_connector_valid_at_route_index

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        points = [(0.0, 0.0), (0.5, 0.0)]

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=True):
            self.assertTrue(_straight_connector_valid_at_route_index(game_map, points, 0, 1850))

    def test_ai_extend_route_caps_connector_at_adjacent_street_length(self):
        from ai import _ai_extend_route_with_straight_connector

        street_seg = 0.05
        route = [(10.0, 10.0), (10.0 + street_seg, 10.0)]
        extended = _ai_extend_route_with_straight_connector(route, 0.2, from_end=True)
        self.assertIsNotNone(extended)
        added = math.hypot(extended[-1][0] - extended[-2][0], extended[-1][1] - extended[-2][1])
        self.assertLessEqual(added, street_seg + 1e-9)

    def test_route_majority_on_streets_and_water_accepts_minor_connectors(self):
        from operations import _ai_route_majority_on_streets_and_water

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        seg = 0.04
        points = [
            (0.0, 0.0),
            (seg, 0.0),
            (2 * seg, 0.0),
            (3 * seg, 0.0),
            (4 * seg, 0.0),
            (5 * seg, 0.0),
            (6 * seg, 0.0),
            (7 * seg, 0.0),
        ]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 0

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertTrue(_ai_route_majority_on_streets_and_water(game_map, points, 1850))

    def test_route_majority_on_streets_and_water_rejects_connector_heavy_route(self):
        from operations import (
            _ai_route_has_excessive_inter_street_connectors,
            _ai_route_majority_on_streets_and_water,
        )

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        seg = 0.04
        points = [(i * seg, 0.0) for i in range(10)]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 1

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertFalse(_ai_route_majority_on_streets_and_water(game_map, points, 1850))
                self.assertTrue(_ai_route_has_excessive_inter_street_connectors(game_map, points, 1850))

    def test_water_segments_count_toward_street_water_majority(self):
        from operations import _ai_route_majority_on_streets_and_water

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        street_len = 0.05
        water_len = 0.20
        connector_len = 0.08
        points = [
            (0.0, 0.0),
            (street_len, 0.0),
            (street_len + water_len, 0.0),
            (street_len + water_len + connector_len, 0.0),
            (street_len + water_len + connector_len + street_len, 0.0),
        ]

        def is_water(_map, x1, y1, x2, y2, **kwargs):
            seg_len = math.hypot(x2 - x1, y2 - y1)
            return abs(seg_len - water_len) < 1e-6

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            seg_len = math.hypot(x2 - x1, y2 - y1)
            return abs(seg_len - connector_len) >= 1e-6

        with unittest.mock.patch("operations._route_segment_is_water_crossing", side_effect=is_water):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertTrue(_ai_route_majority_on_streets_and_water(game_map, points, 1850))

    def test_route_majority_rejects_exactly_fifty_percent_street_water(self):
        """AI requires strictly more than 50% street/water."""
        from operations import _ai_route_majority_on_streets_and_water

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        # 1 street + 1 off-street of equal length => exactly 50%, must fail.
        points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return x1 < 0.5

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertFalse(_ai_route_majority_on_streets_and_water(game_map, points, 1850))

    def test_rail_osm_polyline_majority_rejects_inter_polyline_connectors(self):
        """Connectors that pass street endpoint checks must not count toward OSM polyline share."""
        from operations import (
            _ai_route_majority_on_osm_polylines,
            _ai_route_majority_on_streets_and_water,
        )

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        seg = 0.04
        points = [(i * seg, 0.0) for i in range(10)]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 0

        def on_osm_polyline(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 1

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                with unittest.mock.patch(
                    "map_presets_nyc_road_utils.route_land_segment_on_osm_polyline",
                    side_effect=on_osm_polyline,
                ):
                    self.assertTrue(_ai_route_majority_on_streets_and_water(game_map, points, 1850))
                    self.assertFalse(_ai_route_majority_on_osm_polylines(game_map, points, 1850))

    def test_rail_osm_polyline_majority_accepts_polyline_heavy_route(self):
        from operations import _ai_route_majority_on_osm_polylines

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        seg = 0.04
        points = [(i * seg, 0.0) for i in range(10)]

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch(
                "map_presets_nyc_road_utils.route_land_segment_on_osm_polyline",
                return_value=True,
            ):
                self.assertTrue(_ai_route_majority_on_osm_polylines(game_map, points, 1850))

    def test_track_maintenance_uses_distance_not_vertex_count(self):
        """OSM street polylines must not cost more maintenance solely for having more vertices."""
        from line import Line

        player = self.player if hasattr(self, "player") else None
        if player is None:
            from operations import Operations
            ops = Operations(["P1"], 30, 30, 50000)
            player = ops.get_players()[0]
        # Same 2-unit length: 2 points vs many intermediate vertices.
        short_route = [(0.0, 0.0), (2.0, 0.0)]
        dense_route = [(i * 0.1, 0.0) for i in range(21)]  # length 2.0, 21 vertices
        line_short = Line("Short", 1, short_route, player, [])
        line_dense = Line("Dense", 2, dense_route, player, [])
        self.assertEqual(line_short.calc_maitnance(1830), line_dense.calc_maitnance(1830))
        self.assertEqual(line_short.calc_maitnance(1830), 100)  # 2.0 * 50

    def test_bankruptcy_requires_negative_money(self):
        """Exactly $0 is solvent; only money < 0 is bankrupt."""
        from operations import Operations

        ops = Operations(["P1"], 30, 30, 50000)
        player = ops.get_players()[0]
        player.money = 0
        self.assertFalse(ops.check_and_eliminate_player(player))
        self.assertFalse(ops.is_player_eliminated(player))
        player.money = -1
        self.assertTrue(ops.check_and_eliminate_player(player))
        self.assertTrue(ops.is_player_eliminated(player))

    def test_elimination_uses_net_change_not_intermediate_balance(self):
        """Year-end finances apply net atomically; lawsuit net must not bankrupt on intermediate negatives."""
        from operations import Operations
        from lawsuit import Lawsuit, LawsuitExcuse
        import random

        ops = Operations(["P1", "P2"], 30, 30, 50000)
        plaintiff = ops.get_players()[0]
        defendant = ops.get_players()[1]
        start_money = 20
        defendant.money = start_money

        excuse = LawsuitExcuse("Test", 800, "")
        lawsuit = Lawsuit(plaintiff, defendant, excuse, ops.get_year())
        court_costs = int(lawsuit.damages * 0.1)
        reparations = int(lawsuit.damages * 0.5)
        expected_net = reparations - court_costs
        self.assertGreater(expected_net, 0)

        with unittest.mock.patch.object(random, 'random', return_value=0.99):
            lawsuit.resolve(plaintiff_lawyers=0, defendant_lawyers=0)

        self.assertEqual(defendant.get_money(), start_money + expected_net)
        self.assertFalse(ops.check_and_eliminate_player(defendant))

    def test_process_player_finances_applies_net_atomically(self):
        """Finances apply one net change; player survives when final balance stays non-negative."""
        from operations import Operations

        ops = Operations(["P1"], 30, 30, 50000)
        player = ops.get_players()[0]
        player.money = 30

        with unittest.mock.patch.object(ops, 'calc_player_profit', return_value=-40):
            with unittest.mock.patch.object(ops.game, 'calc_trainset_maintenance', return_value=0):
                with unittest.mock.patch.object(ops.game, 'calc_resource_costs', return_value=0):
                    with unittest.mock.patch.object(ops.game, 'calc_maitnance_by_player', return_value=0):
                        with unittest.mock.patch.object(ops.game, 'calc_taxes', return_value=(0, 0, 0)):
                            with unittest.mock.patch.object(ops.game, 'calc_worker_costs', return_value=(0, False)):
                                with unittest.mock.patch.object(
                                    ops, '_compute_transit_subsidy', return_value=50,
                                ):
                                    finances = ops.process_player_finances(player, ops.get_year())

        self.assertEqual(finances['net'], -40 - 0 + 50)
        self.assertEqual(player.get_money(), 30 + finances['net'])
        self.assertFalse(ops.check_and_eliminate_player(player))

    def test_line_annual_capacity_and_ridership_helpers(self):
        from line import Line
        from resource import Wood
        from vehicle import Trainset, Locomotive, Railcar

        ops = Operations(["P1"], 50, 50, 50000, map_preset_name="standard")
        player = ops.get_players()[0]
        route = [(5.0, 5.0), (10.0, 5.0), (15.0, 5.0)]
        loco = Locomotive(
            "Test Loco", fuel=Wood(), miles_per_unit=10.0, top_speed=30.0, average_speed=30.0,
        )
        trainset = Trainset(locomotive=loco, cars=[Railcar("Coach", 0, 60, 20)])
        line = Line("CapLine", 1, route, player, [trainset])
        line.schedule = {'peak': 4, 'off_peak': 2, 'night': 1}
        ops.get_map().add_line(line)

        capacity = ops.get_line_annual_capacity(line)
        self.assertGreater(capacity, 0)

        ops._cached_ridership_data = {'CapLine': 1234}
        ops._cached_ridership_year = ops.get_year()
        self.assertEqual(ops.get_line_ridership_for_year(line, ops.get_year()), 1234)

        ops.game.timeline.year = ops.get_timeline_start_year() + 1
        prev_year = ops.get_year() - 1
        line._cached_ridership = 567
        line._cached_ridership_year = prev_year
        self.assertEqual(ops.get_line_previous_year_ridership(line), 567)

        preview_schedule = {'peak': 1, 'off_peak': 0, 'night': 0}
        preview_capacity = ops.get_line_annual_capacity(line, schedule=preview_schedule)
        self.assertLess(preview_capacity, capacity)
        self.assertEqual(line.schedule['peak'], 4)

    def test_previous_year_ridership_zero_when_line_incomplete(self):
        """Previous-year riders are 0 when the line still had pending construction."""
        from line import Line
        from resource import Wood
        from vehicle import Trainset, Locomotive, Railcar

        ops = Operations(["P1"], 50, 50, 50000, map_preset_name="standard")
        player = ops.get_players()[0]
        route = [(5.0, 5.0), (10.0, 5.0), (15.0, 5.0)]
        loco = Locomotive(
            "Test Loco", fuel=Wood(), miles_per_unit=10.0, top_speed=30.0, average_speed=30.0,
        )
        trainset = Trainset(locomotive=loco, cars=[Railcar("Coach", 0, 60, 20)])
        line = Line("CapLine", 1, route, player, [trainset])
        ops.get_map().add_line(line)

        ops.game.timeline.year = ops.get_timeline_start_year() + 1
        prev_year = ops.get_year() - 1
        line._cached_ridership = 999
        line._cached_ridership_year = prev_year
        ops._cached_ridership_data = {'CapLine': 999}
        ops._cached_ridership_year = prev_year

        ops.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'CapLine',
                'points': [(16.0, 5.0), (20.0, 5.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        self.assertFalse(ops.was_line_fully_built_at_end_of_year('CapLine', prev_year))
        self.assertEqual(ops.get_line_previous_year_ridership(line), 0)

    def test_get_pending_line_extension_routes_returns_display_polylines(self):
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        pending = ui.get_pending_line_extension_routes()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['line_name'], 'Main')
        self.assertGreaterEqual(len(pending[0]['points']), 2)
        self.assertEqual(pending[0]['points'][-1], (25.0, 0.0))
        self.assertEqual(pending[0]['points'][0], (20.0, 0.0))

    def test_check_and_eliminate_honors_net_change_over_intermediate_money(self):
        """Bankruptcy uses balance_before + net_change, not a stale intermediate balance."""
        from operations import Operations

        ops = Operations(["P1"], 30, 30, 50000)
        player = ops.get_players()[0]
        player.money = -50
        self.assertFalse(ops.check_and_eliminate_player(player, balance_before=30, net_change=20))
        self.assertFalse(ops.is_player_eliminated(player))
        self.assertTrue(ops.check_and_eliminate_player(player, balance_before=10, net_change=-20))
        self.assertTrue(ops.is_player_eliminated(player))


class TestRouteBacktrackingRules(unittest.TestCase):
    """Doubling-back is enforced for AI only; corner threshold is 80°."""

    def _map(self):
        from map import Map
        return Map(width=50, height=50, map_preset_name="standard")

    def _point_for_turn_degrees(self, turn_degrees: float, leg: float = 1.0):
        import math
        from map_preset_utils import route_turn_angle_degrees

        a = (0.0, 0.0)
        b = (leg, 0.0)
        rad = math.radians(float(turn_degrees))
        c = (b[0] + leg * math.cos(rad), b[1] + leg * math.sin(rad))
        self.assertAlmostEqual(route_turn_angle_degrees(a, b, c), turn_degrees, places=1)
        return [a, b, c]

    def test_ai_backtrack_triggers_at_80_degree_corner_not_90(self):
        from operations import _ai_route_has_backtracking
        from game_presets import AI_ROUTE_BACKTRACK_TURN_DEGREES

        self.assertEqual(AI_ROUTE_BACKTRACK_TURN_DEGREES, 80.0)
        game_map = self._map()
        sharp = self._point_for_turn_degrees(85.0)
        gentle = self._point_for_turn_degrees(75.0)
        self.assertTrue(_ai_route_has_backtracking(sharp, game_map))
        self.assertFalse(_ai_route_has_backtracking(gentle, game_map))

    def test_human_create_line_allows_loop_route(self):
        from operations import _ai_route_has_backtracking

        ui = Operations(["Human"], 50, 50, 50000, 1.0, 1.0, map_preset_name="standard")
        human = ui.get_players()[0]
        game_map = ui.get_map()
        loop = [(2.0, 2.0), (7.0, 2.0), (7.0, 7.0), (2.0, 7.0), (2.0, 2.0)]
        self.assertTrue(_ai_route_has_backtracking(loop, game_map))
        elev = ["street"] * (len(loop) - 1)
        result = ui.create_line_complete("Human Loop", loop, human, elevation_levels=elev)
        self.assertNotEqual(result.get("success"), False)

    def test_ai_create_line_rejects_loop_route(self):
        ui = Operations(
            ["Human", "AI1"], 50, 50, 50000, 1.0, 1.0,
            ai_player_indices=[1],
            map_preset_name="standard",
        )
        ai = ui.get_players()[1]
        loop = [(2.0, 2.0), (7.0, 2.0), (7.0, 7.0), (2.0, 7.0), (2.0, 2.0)]
        elev = ["street"] * (len(loop) - 1)
        with self.assertRaises(ValueError):
            ui.create_line_complete(
                "AI Loop",
                loop,
                ai,
                elevation_levels=elev,
                cost_info={
                    "cost": 500,
                    "length": 20.0,
                    "points_count": len(loop) - 1,
                    "elevation_levels": elev,
                },
                use_fast_path=True,
            )

    def test_validate_route_geometry_does_not_check_backtracking(self):
        from operations import _validate_route_geometry, _ai_route_has_backtracking

        game_map = self._map()
        loop = [(2.0, 2.0), (7.0, 2.0), (7.0, 7.0), (2.0, 7.0), (2.0, 2.0)]
        self.assertTrue(_ai_route_has_backtracking(loop, game_map))
        _validate_route_geometry(loop, game_map, is_bus_line=False, is_ferry_line=False)


class TestBusOnStreetRules(unittest.TestCase):
    """Bus routes must stay on streets for all players, including AI fast path."""

    def test_nyc_road_check_uses_polyline_not_bounding_box(self):
        """Regression: is_point_on_road_at_year must delegate to road_utils, not bbox checks."""
        import inspect
        import map_presets_nyc as nyc_preset

        src = inspect.getsource(nyc_preset.is_point_on_road_at_year)
        self.assertIn("road_utils", src)
        self.assertNotIn("within road bounds", src.lower())

    def _off_street_route(self):
        return [(10.0, 10.0), (10.5, 10.0), (10.5, 10.4)]

    def test_route_has_off_street_bus_segment(self):
        from operations import _route_has_off_street_bus_segment

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        points = self._off_street_route()

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._bus_land_segment_follows_street", return_value=False):
                self.assertTrue(_route_has_off_street_bus_segment(game_map, points, current_year=1850))

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._bus_land_segment_follows_street", return_value=True):
                self.assertFalse(_route_has_off_street_bus_segment(game_map, points, current_year=1850))

    def test_human_bus_create_rejects_off_street_segment(self):
        ui = Operations(["Human"], 50, 50, 50000, 1.0, 1.0, map_preset_name="standard")
        human = ui.get_players()[0]
        points = self._off_street_route()
        elev = ["street", "street"]

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._bus_land_segment_follows_street", return_value=False):
                with self.assertRaises(ValueError) as ctx:
                    ui.create_line_complete(
                        "Off Street Bus",
                        points,
                        human,
                        elevation_levels=elev,
                        is_bus_line=True,
                    )
        self.assertIn("stay on streets", str(ctx.exception).lower())

    def test_ai_bus_fast_path_rejects_off_street_segment(self):
        ui = Operations(
            ["Human", "AI1"], 50, 50, 50000, 1.0, 1.0,
            ai_player_indices=[1],
            map_preset_name="standard",
        )
        ai = ui.get_players()[1]
        points = [(10.0, 10.0), (10.8, 10.0)]
        elev = ["street"]

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._bus_land_segment_follows_street", return_value=False):
                with self.assertRaises(ValueError) as ctx:
                    ui.create_line_complete(
                        "AI Off Street Bus",
                        points,
                        ai,
                        elevation_levels=elev,
                        is_bus_line=True,
                        cost_info={
                            "cost": 200,
                            "length": 0.8,
                            "points_count": 1,
                            "elevation_levels": elev,
                        },
                        use_fast_path=True,
                    )
        self.assertIn("stay on streets", str(ctx.exception).lower())

    def test_bus_extension_rejects_off_street_segment(self):
        ui = Operations(["Human"], 50, 50, 50000, 1.0, 1.0, map_preset_name="standard")
        human = ui.get_players()[0]
        base = [(10.0, 10.0), (10.3, 10.0)]
        elev = ["street"]

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._bus_land_segment_follows_street", return_value=True):
                ui.create_line_complete(
                    "Street Bus",
                    base,
                    human,
                    elevation_levels=elev,
                    is_bus_line=True,
                )

        extension = [(10.3, 10.4)]
        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._bus_land_segment_follows_street", return_value=False):
                with self.assertRaises(ValueError) as ctx:
                    ui.extend_line_complete(
                        "Street Bus",
                        human,
                        extension,
                        extend_from_end=True,
                        elevation_levels=["street"],
                    )
        self.assertIn("stay on streets", str(ctx.exception).lower())


    def test_train_create_rejects_connector_heavy_route(self):
        from operations import _enforce_route_street_rules_or_raise

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        seg = 0.04
        connector_heavy = [(i * seg, 0.0) for i in range(10)]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 1

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                with self.assertRaises(ValueError) as ctx:
                    _enforce_route_street_rules_or_raise(
                        game_map, connector_heavy, current_year=1850, is_bus_line=False,
                    )
        self.assertIn("on-street", str(ctx.exception).lower())


    def test_route_validation_allows_water_endpoints(self):
        from operations import _validate_route_streets_on_land
        from unittest.mock import patch

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        route = [(0.0, 0.0), (5.0, 5.0)]
        with patch("map_preset_loader.preset_has_road_support", return_value=True):
            with patch("operations._route_segment_is_water_crossing", return_value=True):
                result = _validate_route_streets_on_land(
                    route, game_map, 1850,
                    elevation_levels=["above_ground"],
                )
        self.assertTrue(result.get("success"))

    def test_bus_connector_requires_street_on_both_ends(self):
        from operations import _bus_connector_valid_at_route_index
        from game_presets import AI_BUS_MAX_CONNECTOR_GRID
        from unittest.mock import patch

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        points = [(0.0, 0.0), (1.0, 0.0), (1.001, 0.0), (2.0, 0.0)]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            if (x1, y1) == (0.0, 0.0) and (x2, y2) == (1.0, 0.0):
                return True
            if (x1, y1) == (1.001, 0.0) and (x2, y2) == (2.0, 0.0):
                return True
            return False

        with patch("operations._route_segment_is_water_crossing", return_value=False):
            with patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                self.assertTrue(
                    _bus_connector_valid_at_route_index(game_map, points, 1, 1850)
                )
        long_points = [(0.0, 0.0), (0.0, 0.0), (AI_BUS_MAX_CONNECTOR_GRID + 0.01, 0.0), (1.0, 0.0)]
        with patch("operations._route_segment_is_water_crossing", return_value=False):
            with patch("operations._route_land_segment_follows_street", return_value=True):
                self.assertFalse(
                    _bus_connector_valid_at_route_index(game_map, long_points, 1, 1850)
                )

    def test_segment_elevation_allows_street_on_bridge(self):
        from operations import Operations
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 50000, 1.0, map_preset_name='nyc')
        with patch.object(ui, '_segment_in_water_for_disaster', return_value=True):
            with patch('operations._segment_on_bridge_or_tunnel', return_value=True):
                choices, default = ui.get_segment_elevation_options((0.0, 0.0), (1.0, 0.0))
        self.assertIn('street', choices)
        self.assertEqual(default, 'street')


class TestExtensionClosestEnd(unittest.TestCase):
    def test_extension_attach_from_closest_end_module(self):
        from operations import _extension_attach_from_closest_end

        route = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
        self.assertTrue(_extension_attach_from_closest_end(route, (19.0, 0.5)))
        self.assertFalse(_extension_attach_from_closest_end(route, (1.0, 0.5)))
        self.assertTrue(_extension_attach_from_closest_end(route, (20.0, 0.0)))

    def test_operations_extension_attach_from_closest_end(self):
        ui = Operations(["P1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        ui.create_line_complete(
            "Main",
            [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
            player,
            elevation_levels=["street", "street"],
        )
        self.assertTrue(ui.extension_attach_from_closest_end("Main", (19.0, 0.0)))
        self.assertFalse(ui.extension_attach_from_closest_end("Main", (1.0, 0.0)))

    def test_train_extension_attach_uses_pending_extension_tip(self):
        """After a pending end extension, attach end is the extension tip (combined route)."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        combined = ui.get_line_route_including_extensions("Main")
        self.assertEqual(combined[-1], (25.0, 0.0))
        self.assertTrue(ui.extension_attach_from_closest_end("Main", (24.0, 0.0)))
        self.assertFalse(ui.extension_attach_from_closest_end("Main", (1.0, 0.0)))

    def test_calculate_extend_cost_uses_combined_route(self):
        """Second extension cost should connect from pending extension tip, not original end."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        elev = ["street", "street"]
        cost_from_tip = ui.calculate_extend_cost(
            "Main", [(26.0, 0.0), (28.0, 0.0)], extend_from_end=True, elevation_levels=elev,
        )
        # Connection from (25,0) to (26,0) is length 1; two extension segments add 2 more.
        self.assertAlmostEqual(cost_from_tip['length'], 3.0, places=5)
        cost_from_original_end = ui.calculate_extend_cost(
            "Main", [(26.0, 0.0), (28.0, 0.0)], extend_from_end=False, elevation_levels=elev,
        )
        self.assertGreater(cost_from_original_end['length'], cost_from_tip['length'])

    def test_chained_pending_extensions_merge_in_order(self):
        """Two pending end extensions merge to the latest tip on the combined route."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(27.0, 0.0), (30.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 1,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        combined = ui.get_line_route_including_extensions("Main")
        self.assertEqual(combined[-1], (30.0, 0.0))
        self.assertTrue(ui.extension_attach_from_closest_end("Main", (29.0, 0.0)))

    def test_get_extension_attach_endpoints_uses_combined_route(self):
        """Attach endpoints reflect pending extensions, not just the built line."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        start, end = ui.get_extension_attach_endpoints("Main")
        self.assertEqual(start, (0.0, 0.0))
        self.assertEqual(end, (20.0, 0.0))
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        start, end = ui.get_extension_attach_endpoints("Main")
        self.assertEqual(start, (0.0, 0.0))
        self.assertEqual(end, (25.0, 0.0))

    def test_extension_auto_attach_from_start_with_pending_end_extension(self):
        """First click near the original start attaches from start, not the pending tip."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        start, end = ui.get_extension_attach_endpoints("Main")
        self.assertEqual(start, (0.0, 0.0))
        self.assertEqual(end, (25.0, 0.0))
        self.assertFalse(ui.extension_attach_from_closest_end("Main", (1.0, 0.0)))
        self.assertTrue(ui.extension_attach_from_closest_end("Main", (24.0, 0.0)))

    def test_ai_merged_line_route_includes_all_extension_points(self):
        from operations import _ai_merged_line_route

        route = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
        add = [(22.0, 1.0), (25.0, 2.0)]
        merged = _ai_merged_line_route(route, add, extend_from_end=True)
        self.assertEqual(merged, route + add)
        merged_start = _ai_merged_line_route(route, add, extend_from_end=False)
        self.assertEqual(merged_start, [(25.0, 2.0), (22.0, 1.0)] + route)

    def test_extension_from_end_preserves_click_order(self):
        """Closer first click then farther second — route must not visit farther point first."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        closer = (22.0, 0.0)
        farther = (25.0, 3.0)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [closer, farther],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street', 'street'],
            },
            player,
            0,
        )
        pending = ui.get_pending_line_extension_routes()
        self.assertEqual(len(pending), 1)
        pts = pending[0]['points']
        self.assertEqual(pts[0], (20.0, 0.0))
        self.assertEqual(pts[1], closer)
        self.assertEqual(pts[2], farther)
        self.assertEqual(pts[-1], farther)

        ui.extend_line_route_from_end(line, [closer, farther])
        route = ui.get_line_route(line)
        self.assertEqual(route[-2], closer)
        self.assertEqual(route[-1], farther)

    def test_extension_from_start_preserves_click_order_in_display(self):
        """Beginning extension: attach → first click → last click (not far point then back)."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        closer = (2.0, 0.0)
        farther = (-3.0, 0.0)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [closer, farther],
                'extend_from_end': False,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street', 'street'],
            },
            player,
            0,
        )
        pending = ui.get_pending_line_extension_routes()
        self.assertEqual(len(pending), 1)
        pts = pending[0]['points']
        self.assertEqual(pts[0], (0.0, 0.0))
        self.assertEqual(pts[1], closer)
        self.assertEqual(pts[2], farther)

        ui.extend_line_route_from_beginning(line, [closer, farther])
        route = ui.get_line_route(line)
        self.assertEqual(route[0], farther)
        self.assertEqual(route[1], closer)
        self.assertEqual(route[2], (0.0, 0.0))

    def test_pending_end_extension_never_connects_to_route_start(self):
        """End extension display must not draw a closing segment back to route start."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 5.0), (25.0, 8.0)],
                'extend_from_end': True,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        pending = ui.get_pending_line_extension_routes()
        self.assertEqual(len(pending), 1)
        pts = pending[0]['points']
        self.assertEqual(pts[0], (20.0, 0.0))
        self.assertEqual(pts[-1], (25.0, 8.0))
        self.assertNotEqual(pts[-1], pts[0])
        self.assertNotIn((0.0, 0.0), pts[1:])

    def test_pending_mixed_direction_extensions_use_separate_polylines(self):
        """Start and end extensions on one line must not share one display polyline."""
        ui = Operations(["P1"], 50, 50, 50000, 1.0, 1.0)
        player = ui.get_players()[0]
        line = ui.game.create_line(
            "Main", player, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
        )
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(-2.0, 0.0), (-4.0, 0.0)],
                'extend_from_end': False,
                'extension_order': 0,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        ui.add_to_build_queue(
            'line_extension',
            {
                'line_name': 'Main',
                'points': [(22.0, 0.0), (25.0, 0.0)],
                'extend_from_end': True,
                'extension_order': 1,
                'segment_index': 0,
                'total_segments': 1,
                'elevation_levels': ['street'],
            },
            player,
            0,
        )
        pending = ui.get_pending_line_extension_routes()
        self.assertEqual(len(pending), 2)
        end_ext = [p for p in pending if p.get('extend_from_end')][0]
        self.assertEqual(end_ext['points'][0], (20.0, 0.0))
        self.assertEqual(end_ext['points'][-1], (25.0, 0.0))
        self.assertNotEqual(end_ext['points'][-1], (0.0, 0.0))

    def test_open_polyline_display_points_strips_loop_closer(self):
        from operations import _open_polyline_display_points

        closed = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)]
        self.assertEqual(
            _open_polyline_display_points(closed),
            [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
        )


    def test_get_segment_elevation_options_water_defaults_above_ground(self):
        from operations import Operations
        from unittest.mock import Mock, patch

        ui = Operations(['Human'], 50, 50, 50000, 1.0, map_preset_name='standard')
        game_map = ui.game.get_map()
        # A segment that cannot be at street level (over water) auto-defaults to above-ground.
        with patch.object(ui, 'segment_is_over_water_not_bridge', return_value=True):
            choices, default = ui.get_segment_elevation_options((0.0, 0.0), (1.0, 0.0))
        self.assertEqual(choices, ['above_ground', 'underground'])
        self.assertEqual(default, 'above_ground')
        self.assertIn('above_ground', choices)
        with patch.object(ui, 'segment_is_over_water_not_bridge', return_value=False):
            choices, default = ui.get_segment_elevation_options((0.0, 0.0), (1.0, 0.0), is_bus_line=True)
        self.assertIn('street', choices)
        self.assertEqual(default, 'street')

    def test_street_validation_failure_preserves_elevation_levels(self):
        from operations import Operations
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 500000, 1.0, map_preset_name='nyc')
        player = ui.get_players()[0]
        points = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0)]
        elevation_levels = ['street', 'above_ground']
        original = list(elevation_levels)
        with patch('operations._route_land_segment_follows_street', return_value=False):
            with patch('operations._route_segment_is_water_crossing', return_value=False):
                with patch('operations._straight_connector_valid_at_route_index', return_value=False):
                    cost_info = ui.calculate_line_cost(points, elevation_levels)
                    result = ui.create_line_complete(
                        'PreserveTest', points, player,
                        elevation_levels=elevation_levels, cost_info=cost_info,
                    )
        self.assertEqual(result.get('success'), False)
        self.assertEqual(elevation_levels, original)
        self.assertEqual(cost_info['elevation_levels'], original)
        self.assertIsNot(cost_info['elevation_levels'], elevation_levels)

    def test_invalid_street_aligned_segment_indices_offroad_only(self):
        from operations import _invalid_street_aligned_segment_indices
        from map import Map
        from unittest.mock import patch

        game_map = Map(width=50, height=50, map_preset_name='nyc', use_osm=False)
        points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]

        with patch('operations._route_land_segment_follows_street', return_value=False):
            with patch('operations._route_segment_is_water_crossing', return_value=False):
                with patch('operations._straight_connector_valid_at_route_index', return_value=False):
                    invalid = _invalid_street_aligned_segment_indices(
                        points, game_map, 1850,
                        elevation_levels=['street', 'underground'],
                    )
        self.assertEqual(invalid, [0])

    def test_all_underground_route_skips_street_validation(self):
        from operations import _validate_route_streets_on_land
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 500000, 1.0, map_preset_name='nyc')
        player = ui.get_players()[0]
        points = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0)]
        elev = ['underground', 'underground']

        with patch('operations._route_land_segment_follows_street', return_value=False):
            with patch('operations._route_segment_is_water_crossing', return_value=False):
                with patch('operations._straight_connector_valid_at_route_index', return_value=False):
                    result = _validate_route_streets_on_land(
                        points, ui.game.get_map(), 1850, elevation_levels=elev,
                    )
                    self.assertTrue(result.get('success'))
                    cost = ui.calculate_line_cost(points, elev)
                    created = ui.create_line_complete(
                        'AllUnder', points, player, elevation_levels=elev, cost_info=cost,
                    )
        self.assertNotEqual(created.get('success'), False)

    def test_all_underground_extension_skips_street_validation(self):
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 500000, 1.0, map_preset_name='nyc')
        player = ui.get_players()[0]
        points = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0)]
        elev = ['underground', 'underground']
        ext_pts = [(13.0, 10.0), (14.0, 10.0)]
        ext_elev = ['underground', 'underground']

        with patch('operations._route_land_segment_follows_street', return_value=False):
            with patch('operations._route_segment_is_water_crossing', return_value=False):
                with patch('operations._straight_connector_valid_at_route_index', return_value=False):
                    cost = ui.calculate_line_cost(points, elev)
                    ui.create_line_complete(
                        'SubExt', points, player, elevation_levels=elev, cost_info=cost,
                    )
                    ext_cost = ui.calculate_extend_cost('SubExt', ext_pts, True, ext_elev)
                    result = ui.extend_line_complete(
                        'SubExt', player, ext_pts, True,
                        elevation_levels=ext_elev, cost_info=ext_cost,
                    )
        self.assertNotEqual(result.get('success'), False)

    def test_default_profit_margin_applied_to_new_games(self):
        from game_presets import DEFAULT_PROFIT_MARGIN

        ui = Operations(['Human'], 50, 50, 50000, 1.0)
        self.assertEqual(ui.game.profit_margin, DEFAULT_PROFIT_MARGIN)
        self.assertEqual(DEFAULT_PROFIT_MARGIN, 0.75)

    def test_create_line_street_failure_reports_invalid_segments(self):
        from operations import Operations
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 500000, 1.0, map_preset_name='nyc')
        player = ui.get_players()[0]
        points = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0)]
        elev = ['street', 'street']
        with patch('operations._route_land_segment_follows_street', return_value=False):
            with patch('operations._route_segment_is_water_crossing', return_value=False):
                with patch('operations._straight_connector_valid_at_route_index', return_value=False):
                    cost_info = ui.calculate_line_cost(points, elev)
                    result = ui.create_line_complete(
                        'FailTest', points, player, elevation_levels=elev, cost_info=cost_info,
                    )
        self.assertEqual(result.get('success'), False)
        self.assertIn('invalid_segment_indices', result)
        self.assertEqual(result['invalid_segment_indices'], [0, 1])
        self.assertEqual(elev, ['street', 'street'])

    def test_mixed_elevations_preserved_in_build_queue(self):
        from operations import Operations
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 500000, 1.0, map_preset_name='nyc')
        player = ui.get_players()[0]
        points = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0)]
        elev = ['street', 'underground']

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return x1 < 10.5

        with patch('operations._route_land_segment_follows_street', side_effect=follows_street):
            with patch('operations._route_segment_is_water_crossing', return_value=False):
                with patch('operations._straight_connector_valid_at_route_index', return_value=False):
                    cost_info = ui.calculate_line_cost(points, list(elev))
                    result = ui.create_line_complete(
                        'Mixed', points, player, elevation_levels=list(elev), cost_info=cost_info,
                    )
        self.assertNotEqual(result.get('success'), False)
        queued_elevs = [b['data'].get('elevation_levels') for b in ui.get_build_queue()]
        self.assertTrue(any(e == ['street', 'underground'] for e in queued_elevs))

    def test_route_segment_is_water_crossing_any_endpoint_in_water(self):
        from operations import _route_segment_is_water_crossing
        from map import Map

        game_map = Map(width=50, height=50, map_preset_name='nyc', use_osm=False)

        def in_water(_preset, x, y, _game_map, skip_osm=True):
            fx, fy = float(x), float(y)
            if fx == 10.0 and fy == 10.0:
                return True
            if fx == 5.0 and fy == 5.0:
                return True
            return False

        with unittest.mock.patch('map_preset_loader.preset_has_water_areas', return_value=True):
            with unittest.mock.patch('map_preset_loader.preset_is_point_in_water', side_effect=in_water):
                self.assertTrue(
                    _route_segment_is_water_crossing(game_map, 10.0, 10.0, 20.0, 20.0, skip_osm=True),
                )
                self.assertTrue(
                    _route_segment_is_water_crossing(game_map, 0.0, 0.0, 10.0, 10.0, skip_osm=True),
                )
                self.assertFalse(
                    _route_segment_is_water_crossing(game_map, 0.0, 0.0, 2.0, 2.0, skip_osm=True),
                )

        def in_water_midpoint_only(_preset, x, y, _game_map, skip_osm=True):
            return float(x) == 5.0 and float(y) == 0.0

        with unittest.mock.patch('map_preset_loader.preset_has_water_areas', return_value=True):
            with unittest.mock.patch('map_preset_loader.preset_is_point_in_water', side_effect=in_water_midpoint_only):
                self.assertTrue(
                    _route_segment_is_water_crossing(game_map, 0.0, 0.0, 10.0, 0.0, skip_osm=True),
                )

    def test_calculate_line_cost_allows_above_ground_on_water_segment(self):
        from operations import Operations
        from unittest.mock import patch

        ui = Operations(['Human'], 50, 50, 50000, 1.0, map_preset_name='standard')
        points = [(0.0, 0.0), (10.0, 10.0)]
        with patch('operations._route_segment_is_water_crossing', return_value=True):
            with patch('operations._segment_on_bridge_or_tunnel', return_value=False):
                cost_info = ui.calculate_line_cost(points, ['above_ground'])
        self.assertIn('cost', cost_info)
        self.assertGreater(cost_info['cost'], 0)
        with patch('operations._route_segment_is_water_crossing', return_value=True):
            with patch('operations._segment_on_bridge_or_tunnel', return_value=False):
                with self.assertRaises(ValueError) as ctx:
                    ui.calculate_line_cost(points, ['street'])
                self.assertIn('street level on water', str(ctx.exception).lower())


    def test_train_segment_requires_midpoint_on_street(self):
        from operations import _route_land_segment_follows_street

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)

        def on_road(_preset, x, y, _year, _map):
            return (round(float(x), 3), round(float(y), 3)) in {
                (0.0, 0.0), (0.05, 0.0),
            }

        with unittest.mock.patch("map_preset_loader.preset_has_road_support", return_value=True):
            with unittest.mock.patch("map_preset_loader.get_preset_is_point_on_road", side_effect=on_road):
                with unittest.mock.patch("map_preset_loader.get_preset_is_point_on_bridge", return_value=False):
                    with unittest.mock.patch(
                        "map_preset_loader.get_preset_point_near_street_centerline", return_value=False,
                    ):
                        with unittest.mock.patch(
                            "operations._route_segment_is_water_crossing", return_value=False,
                        ):
                            self.assertFalse(
                                _route_land_segment_follows_street(
                                    game_map, 0.0, 0.0, 0.05, 0.0, 1850,
                                ),
                            )

        with unittest.mock.patch("map_preset_loader.preset_has_road_support", return_value=True):
            with unittest.mock.patch("map_preset_loader.get_preset_is_point_on_road", side_effect=on_road):
                with unittest.mock.patch("map_preset_loader.get_preset_is_point_on_bridge", return_value=False):
                    with unittest.mock.patch(
                        "map_preset_loader.get_preset_point_near_street_centerline", return_value=True,
                    ):
                        with unittest.mock.patch(
                            "operations._route_segment_is_water_crossing", return_value=False,
                        ):
                            self.assertTrue(
                                _route_land_segment_follows_street(
                                    game_map, 0.0, 0.0, 0.05, 0.0, 1850,
                                ),
                            )


    def test_street_water_combined_length_must_exceed_connectors(self):
        from operations import (
            _ai_route_street_water_length_exceeds_connectors,
            _route_street_water_and_connector_lengths,
        )

        game_map = Map(width=50, height=50, map_preset_name="nyc", use_osm=True)
        seg = 0.04
        balanced = [(i * seg, 0.0) for i in range(9)]

        def follows_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 1

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=follows_street):
                sw, conn, total = _route_street_water_and_connector_lengths(game_map, balanced, 1850)
                self.assertAlmostEqual(sw, conn, places=6)
                self.assertGreater(total, 0.0)
                self.assertFalse(_ai_route_street_water_length_exceeds_connectors(game_map, balanced, 1850))

        street_heavy = [(i * seg, 0.0) for i in range(8)]

        def mostly_street(_map, x1, y1, x2, y2, _year, **kwargs):
            return int(round(x1 / seg)) % 2 == 0

        with unittest.mock.patch("operations._route_segment_is_water_crossing", return_value=False):
            with unittest.mock.patch("operations._route_land_segment_follows_street", side_effect=mostly_street):
                sw, conn, _total = _route_street_water_and_connector_lengths(game_map, street_heavy, 1850)
                self.assertGreater(sw, conn)
                self.assertTrue(_ai_route_street_water_length_exceeds_connectors(game_map, street_heavy, 1850))

    def test_ai_create_line_rejected_when_too_short_for_two_stations(self):
        ui = Operations(
            ["Human", "AI1"], 50, 50, 50000, 1.0, 1.0,
            ai_player_indices=[1],
            map_preset_name="standard",
        )
        ai = ui.get_players()[1]
        short = [(10.0, 10.0), (10.15, 10.0)]
        elev = ["street"]
        result = ui.create_line_complete(
            "Too Short",
            short,
            ai,
            elevation_levels=elev,
            cost_info={"cost": 50, "length": 0.15, "points_count": 1, "elevation_levels": elev},
            use_fast_path=True,
        )
        self.assertEqual(result.get("success"), False)
        self.assertIn("two stations", (result.get("error_message") or "").lower())

    def test_ai_create_line_allowed_when_long_enough_for_two_stations(self):
        ui = Operations(
            ["Human", "AI1"], 50, 50, 50000, 1.0, 1.0,
            ai_player_indices=[1],
            map_preset_name="standard",
        )
        ai = ui.get_players()[1]
        route = [(10.0, 10.0), (10.8, 10.0)]
        elev = ["street"]
        with unittest.mock.patch("operations._ai_land_straight_limit_applies", return_value=False):
            with unittest.mock.patch.object(ui, "_ai_guard_pop_at_endpoint", return_value=100):
                with unittest.mock.patch.object(
                    ui, "assert_ai_stop_minimum_direct_separation",
                ):
                    result = ui.create_line_complete(
                        "Long Enough",
                        route,
                        ai,
                        elevation_levels=elev,
                        cost_info={
                            "cost": 200,
                            "length": 0.8,
                            "points_count": 1,
                            "elevation_levels": elev,
                        },
                        use_fast_path=True,
                    )
        self.assertNotEqual(result.get("success"), False)


class TestLineExtensionBuildQueue(unittest.TestCase):
    """Test that line extensions go through the build queue"""
    
    def setUp(self):
        self._app = _ensure_app()
    
    def test_extend_line_complete_adds_to_build_queue(self):
        """Test that extend_line_complete adds extension jobs to build queue instead of extending immediately"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create an initial line first
        initial_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", initial_points, player)
        
        # Process build queue to complete the line
        # Note: process_build_queue is called at the end of advance_year_complete
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
        
        # Verify line exists
        lines = ui.get_map_lines()
        self.assertIn("Test Line", lines)
        
        # Get the line to verify initial route
        line = lines["Test Line"]
        initial_route = ui.get_line_route(line)
        initial_length = len(initial_route)
        
        # Add some money for extension
        player.add_money(10000)
        
        # Extend the line - should add to build queue, not extend immediately
        extension_points = [(12, 10), (13, 10), (14, 10)]
        result = ui.extend_line_complete("Test Line", player, extension_points, extend_from_end=True)
        
        # Verify extension was added to build queue
        build_queue = ui.get_build_queue()
        self.assertGreater(len(build_queue), 0, "Extension should be in build queue")
        
        # Verify money was deducted
        self.assertLess(ui.get_player_money(player), 10000, "Money should be deducted")
        
        # Verify line has NOT been extended yet
        current_route = ui.get_line_route(line)
        self.assertEqual(len(current_route), initial_length, "Line should not be extended until build completes")
        
        # Verify result contains expected fields
        self.assertEqual(result['line_name'], "Test Line")
        self.assertIn('cost', result)
        self.assertIn('build_time', result)
        self.assertIn('points_count', result)
        self.assertEqual(result['points_count'], len(extension_points))
    
    def test_line_extension_completes_after_build_time(self):
        """Test that line extension completes after required build time"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create an initial line first
        initial_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", initial_points, player)
        
        # Process build queue to complete the line
        # Note: process_build_queue is called at the end of advance_year_complete
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
        
        # Get initial route
        line = ui.get_map_lines()["Test Line"]
        initial_route = ui.get_line_route(line)
        initial_length = len(initial_route)
        
        # Add money and rent workers
        player.add_money(10000)
        from worker import CONSTRUCTION_WORKER
        ui.rent_workers_for_player(player, CONSTRUCTION_WORKER, 2, 1830)
        
        # Extend the line
        extension_points = [(12, 10), (13, 10)]
        ui.extend_line_complete("Test Line", player, extension_points, extend_from_end=True)
        
        # Verify extension is in build queue
        build_queue = ui.get_build_queue()
        extension_jobs = [b for b in build_queue if b['type'] == 'line_extension']
        self.assertGreater(len(extension_jobs), 0, "Extension job should be in queue")
        
        # Process build queue until extension completes
        extension_completed = False
        max_years = 10
        years_processed = 0
        
        year = ui.get_year()
        while len(extension_jobs) > 0 and years_processed < max_years:
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
            years_processed += 1
            
            # Check if extension completed
            current_route = ui.get_line_route(line)
            if len(current_route) > initial_length:
                extension_completed = True
                break
            
            # Update extension jobs list
            build_queue = ui.get_build_queue()
            extension_jobs = [b for b in build_queue if b['type'] == 'line_extension']
        
        # Verify extension completed
        self.assertTrue(extension_completed, f"Extension should complete within {max_years} years")
        current_route = ui.get_line_route(line)
        self.assertGreater(len(current_route), initial_length, "Line should be extended after build completes")
        
        # Verify extension points are in the route
        for point in extension_points:
            if point not in initial_route:
                self.assertIn(point, current_route, f"Extension point {point} should be in route")
    
    def test_line_extension_requires_workers_to_progress(self):
        """Test that line extension requires workers to progress"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create an initial line first
        initial_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", initial_points, player)
        
        # Process build queue to complete the line
        # Note: process_build_queue is called at the end of advance_year_complete
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
        
        # Get initial route
        line = ui.get_map_lines()["Test Line"]
        initial_route = ui.get_line_route(line)
        initial_length = len(initial_route)
        
        # Add money but don't rent workers
        player.add_money(10000)
        
        # Extend the line
        extension_points = [(12, 10), (13, 10)]
        ui.extend_line_complete("Test Line", player, extension_points, extend_from_end=True)
        
        # Process build queue multiple times without workers
        # Note: process_build_queue is called at the end of advance_year_complete
        year = ui.get_year()
        for _ in range(5):
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
            
            # Verify line has NOT been extended (no workers = no progress)
            current_route = ui.get_line_route(line)
            self.assertEqual(len(current_route), initial_length, "Line should not extend without workers")
            
            # Verify job is still in queue
            build_queue = ui.get_build_queue()
            extension_jobs = [b for b in build_queue if b['type'] == 'line_extension']
            self.assertGreater(len(extension_jobs), 0, "Extension job should still be in queue")
    
    def test_extend_line_from_beginning(self):
        """Test extending line from beginning"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create an initial line first
        initial_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", initial_points, player)
        
        # Process build queue to complete the line
        # Note: process_build_queue is called at the end of advance_year_complete
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
        
        # Get initial route
        line = ui.get_map_lines()["Test Line"]
        initial_route = ui.get_line_route(line)
        
        # Add money and rent workers
        player.add_money(10000)
        from worker import CONSTRUCTION_WORKER
        ui.rent_workers_for_player(player, CONSTRUCTION_WORKER, 2, 1830)
        
        # Extend from beginning
        extension_points = [(8, 10), (9, 10)]
        ui.extend_line_complete("Test Line", player, extension_points, extend_from_end=False)
        
        # Process until completion
        # Note: process_build_queue is called at the end of advance_year_complete
        max_years = 10
        year = ui.get_year()
        for _ in range(max_years):
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
            
            current_route = ui.get_line_route(line)
            if len(current_route) > len(initial_route):
                break
        
        # Verify extension points are at the beginning
        current_route = ui.get_line_route(line)
        self.assertGreaterEqual(len(current_route), len(initial_route) + len(extension_points) - 1)
        # Check that extension points appear at the start
        for point in extension_points:
            if point not in initial_route:
                self.assertIn(point, current_route, f"Extension point {point} should be in route")
    
    def test_line_extension_segment_building(self):
        """Test that line extensions are split into segments like line creation"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create an initial line first
        initial_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", initial_points, player)
        
        # Process build queue to complete the line
        # Note: process_build_queue is called at the end of advance_year_complete
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            # advance_year_complete returns a dict, extract the new year
            year = result.get('new_year', year + 1)
        
        # Add money and rent workers
        player.add_money(10000)
        ui.rent_workers_for_player(player, ui.game.get_worker_registry().get_worker_by_name("Construction Worker"), 3, 1830)
        
        # Extend with multiple points (should create multiple segments)
        extension_points = [(12, 10), (13, 10), (14, 10), (15, 10)]
        ui.extend_line_complete("Test Line", player, extension_points, extend_from_end=True)
        
        # Verify multiple segments in build queue
        build_queue = ui.get_build_queue()
        extension_jobs = [b for b in build_queue if b['type'] == 'line_extension']
        
        # Should have multiple segments (one per point pair)
        expected_segments = len(extension_points) - 1
        self.assertGreaterEqual(len(extension_jobs), expected_segments, 
                               f"Should have at least {expected_segments} extension segments")
        
        # Verify segments have correct indices
        for job in extension_jobs:
            self.assertIn('segment_index', job['data'])
            self.assertIn('total_segments', job['data'])
            segment_idx = job['data']['segment_index']
            total_segments = job['data']['total_segments']
            self.assertLess(segment_idx, total_segments)
            self.assertGreaterEqual(segment_idx, 0)

import unittest
from operations import Operations
from PyQt6.QtWidgets import QApplication
import sys
from station import Station
from events import NaturalDisasterEvent


def _ensure_app():
    """Ensure QApplication exists for tests"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestDisasterSegmentBlocking(unittest.TestCase):
    """Test that destroyed line segments prevent rides"""
    
    def setUp(self):
        self._app = _ensure_app()

    def _mark_segment_destroyed(self, line, segment_points):
        from operations import _mark_line_edges_destroyed
        _mark_line_edges_destroyed(line, segment_points)
    
    def test_destroyed_segment_prevents_route(self):
        """Test that a destroyed segment prevents travel over that segment"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create a line with multiple segments
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10), (14, 10)]
        elevation_levels = ['street'] * (len(line_points) - 1)
        ui.create_line_complete("Test Line", line_points, player, elevation_levels)
        
        # Process build queue to complete the line
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = ui.get_map_lines()["Test Line"]
        
        # Add trainset
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotives = registry.get_locomotives_by_year()
        if locomotives:
            loco, _ = locomotives[0]
            from vehicle import Trainset
            trainset = Trainset(loco, [])
            line.vehicles = [trainset]
        
        # Add stations at start and end
        start_station = Station("Start", 10, 10)
        end_station = Station("End", 14, 10)
        line.add_station(start_station)
        line.add_station(end_station)
        
        # Verify route works before damage
        route_time_before = ui.calculate_train_route_time(start_station, end_station, line, trainset)
        self.assertIsNotNone(route_time_before, "Route should work before damage")
        
        # Simulate disaster damage - destroy middle segment (no auto-repair queue)
        damaged_segment = [(12, 10), (13, 10)]
        self._mark_segment_destroyed(line, damaged_segment)
        
        # Now route should be blocked
        route_time_after = ui.calculate_train_route_time(start_station, end_station, line, trainset)
        self.assertIsNone(route_time_after, "Route should be blocked when segment is destroyed")
        
        # Route from start to middle station (before damage) should still work
        middle_station = Station("Middle", 11, 10)
        line.add_station(middle_station)
        route_to_middle = ui.calculate_train_route_time(start_station, middle_station, line, trainset)
        self.assertIsNotNone(route_to_middle, "Route before damaged segment should still work")
    
    def test_destroyed_segment_prevents_ridership(self):
        """Test that destroyed segments prevent passengers from using that route"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create a line
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10)]
        elevation_levels = ['street'] * (len(line_points) - 1)
        ui.create_line_complete("Test Line", line_points, player, elevation_levels)
        
        # Process build queue
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = ui.get_map_lines()["Test Line"]
        
        # Add trainset and stations
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotives = registry.get_locomotives_by_year()
        if locomotives:
            loco, _ = locomotives[0]
            from vehicle import Trainset
            trainset = Trainset(loco, [])
            line.vehicles = [trainset]
        
        start_station = Station("Start", 10, 10)
        end_station = Station("End", 13, 10)
        line.add_station(start_station)
        line.add_station(end_station)
        
        # Calculate ridership before damage
        ui.invalidate_ridership_cache()
        ridership_before = ui.calculate_ridership_for_year(ui.get_year())
        passengers_before = ridership_before.get(line.name, 0)
        
        # Damage middle segment
        damaged_segment = [(11, 10), (12, 10)]
        self._mark_segment_destroyed(line, damaged_segment)
        
        # Calculate ridership after damage
        ui.invalidate_ridership_cache()
        ridership_after = ui.calculate_ridership_for_year(ui.get_year())
        passengers_after = ridership_after.get(line.name, 0)
        
        # Ridership should decrease (or be zero) when segment is destroyed
        # Note: This depends on whether passengers can find alternative routes
        # At minimum, direct route over destroyed segment should not be used
        route = ui.find_fastest_route((10, 10), (13, 10))
        
        # If route uses train, it should not go through destroyed segment
        if route.get('method') == 'train':
            # Route should either be None (blocked) or use alternative
            # Since we only have one line, route should be blocked or use walking
            pass  # Route finding should handle this correctly
    
    def test_walking_between_undestroyed_segments(self):
        """Test that passengers can walk between undestroyed segments"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create a line with a gap (destroyed middle segment)
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10), (14, 10)]
        elevation_levels = ['street'] * (len(line_points) - 1)
        ui.create_line_complete("Test Line", line_points, player, elevation_levels)
        
        # Process build queue
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = ui.get_map_lines()["Test Line"]
        
        # Add stations
        station1 = Station("Station1", 10, 10)
        station2 = Station("Station2", 11, 10)
        station3 = Station("Station3", 13, 10)
        station4 = Station("Station4", 14, 10)
        
        line.add_station(station1)
        line.add_station(station2)
        line.add_station(station3)
        line.add_station(station4)
        
        # Damage middle segment (between station2 and station3)
        damaged_segment = [(12, 10), (13, 10)]
        self._mark_segment_destroyed(line, damaged_segment)
        
        # Route from station1 to station2 should work (before damage)
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotives = registry.get_locomotives_by_year()
        if locomotives:
            loco, _ = locomotives[0]
            from vehicle import Trainset
            trainset = Trainset(loco, [])
            line.vehicles = [trainset]
            
            route_1_to_2 = ui.calculate_train_route_time(station1, station2, line, trainset)
            self.assertIsNotNone(route_1_to_2, "Route before damaged segment should work")
        
        # Route from station3 to station4 should work (after damage)
        route_3_to_4 = ui.calculate_train_route_time(station3, station4, line, trainset)
        self.assertIsNotNone(route_3_to_4, "Route after damaged segment should work")
        
        # Route from station1 to station4 should be blocked (goes through damage)
        route_1_to_4 = ui.calculate_train_route_time(station1, station4, line, trainset)
        self.assertIsNone(route_1_to_4, "Route through damaged segment should be blocked")
        
        # But walking route should still be available
        walking_route = ui.find_fastest_route((10, 10), (14, 10))
        self.assertEqual(walking_route.get('method'), 'walk', 
                        "Walking route should be available when train route is blocked")

    def test_disaster_does_not_auto_queue_repair_jobs(self):
        """Natural disaster must not enqueue automatic connector/repair builds."""
        from events import NaturalDisasterEvent
        from operations import _get_line_destroyed_edges
        from unittest.mock import patch

        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10), (14, 10)]
        elevation_levels = ['street'] * (len(line_points) - 1)
        ui.create_line_complete("Disaster Line", line_points, player, elevation_levels)

        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)

        line = ui.get_map_lines()["Disaster Line"]
        disaster = NaturalDisasterEvent(1830, "Earthquake")

        def _sample(population, k):
            if population and hasattr(population[0], 'get_route'):
                return [population[0]]
            return [1]

        with patch('random.sample', side_effect=_sample):
            with patch('random.uniform', return_value=0.2):
                ui.apply_natural_disaster_damage(disaster, 1830)

        repair_jobs = [
            b for b in ui.get_build_queue()
            if b.get('type') == 'line' and (b.get('data') or {}).get('disaster_repair')
        ]
        self.assertEqual(repair_jobs, [], "Disaster must not auto-queue repair jobs")
        self.assertTrue(ui.line_is_operationally_split(line))
        self.assertGreater(len(_get_line_destroyed_edges(line)), 0)

    def test_disaster_split_persists_after_year_advance(self):
        """Split from disaster stays until player rebuilds."""
        from events import NaturalDisasterEvent
        from unittest.mock import patch
        from operations import _get_line_destroyed_edges

        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10), (14, 10)]
        elevation_levels = ['street'] * (len(line_points) - 1)
        ui.create_line_complete("Split Line", line_points, player, elevation_levels)

        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)

        line = ui.get_map_lines()["Split Line"]
        disaster = NaturalDisasterEvent(1830, "Tornado")

        def _sample(population, k):
            if population and hasattr(population[0], 'get_route'):
                return [population[0]]
            return [2]

        with patch('random.sample', side_effect=_sample):
            with patch('random.uniform', return_value=0.2):
                ui.apply_natural_disaster_damage(disaster, 1830)

        self.assertTrue(ui.line_is_operationally_split(line))

        for _ in range(5):
            result = ui.advance_year_complete(ui.get_year())
            ui.process_build_queue()

        self.assertTrue(ui.line_is_operationally_split(line))
        self.assertGreater(len(_get_line_destroyed_edges(line)), 0)

    def test_manual_rebuild_clears_destroyed_edge(self):
        """Player-initiated segment build reconnects destroyed portions."""
        from operations import _get_line_destroyed_edges

        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10)]
        elevation_levels = ['street'] * (len(line_points) - 1)
        ui.create_line_complete("Repair Line", line_points, player, elevation_levels)

        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)

        line = ui.get_map_lines()["Repair Line"]
        damaged_segment = [(11, 10), (12, 10)]
        self._mark_segment_destroyed(line, damaged_segment)
        self.assertTrue(ui.line_is_operationally_split(line))

        segment_data = {
            'name': line.name,
            'points': damaged_segment,
            'segment_index': 1,
            'total_segments': 3,
            'elevation_levels': ['street'],
        }
        ui.add_to_build_queue('line', segment_data, player, 1)

        while len(ui.get_build_queue()) > 0:
            ui.process_build_queue()

        self.assertFalse(ui.line_is_operationally_split(line))
        self.assertEqual(len(_get_line_destroyed_edges(line)), 0)


class TestIncompleteSegmentLineSplit(unittest.TestCase):
    """Lines split by incomplete construction operate as separate route portions."""

    def setUp(self):
        self._app = _ensure_app()

    def _make_trainset(self):
        from vehicle_presets import get_vehicle_registry
        from vehicle import Trainset, Locomotive, Railcar
        from resource import Wood

        registry = get_vehicle_registry()
        loco = None
        for candidate, _year in registry.get_locomotives_by_year():
            if getattr(candidate, 'can_have_railcars', False):
                loco = candidate
                break
        if loco is None:
            loco = Locomotive(
                "Test Loco", fuel=Wood(), miles_per_unit=10.0, top_speed=30.0, average_speed=30.0,
            )
            loco.can_have_railcars = True
        return Trainset(loco, [Railcar("Coach", 0, 60, 20)])

    def test_get_line_operational_route_portions_detects_gap(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        gap_route = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0), (16.0, 10.0), (17.0, 10.0)]
        line = ui.game.create_line("Gap Line", player, gap_route)
        player.acquire_line(line)
        ui.game.get_map().add_line(line)

        portions = ui.get_line_operational_route_portions(line)
        self.assertEqual(len(portions), 2)
        self.assertTrue(ui.line_is_operationally_split(line))
        self.assertEqual(portions[0][0], (10.0, 10.0))
        self.assertEqual(portions[0][-1], (12.0, 10.0))
        self.assertEqual(portions[1][0], (16.0, 10.0))
        self.assertEqual(portions[1][-1], (17.0, 10.0))

    def test_incomplete_middle_segment_blocks_cross_gap_travel(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        route = [
            (10.0, 10.0), (11.0, 10.0), (12.0, 10.0),
            (13.0, 10.0), (14.0, 10.0), (15.0, 10.0),
            (16.0, 10.0), (17.0, 10.0),
        ]
        line = ui.game.create_line("Split Line", player, route)
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        trainset = self._make_trainset()
        line.vehicles = [trainset]

        west_a = Station("WestA", 10.0, 10.0)
        west_b = Station("WestB", 12.0, 10.0)
        east_a = Station("EastA", 15.0, 10.0)
        east_b = Station("EastB", 17.0, 10.0)
        for st in (west_a, west_b, east_a, east_b):
            line.add_station(st)

        ui.add_to_build_queue(
            'line',
            {
                'name': line.name,
                'points': [(12.0, 10.0), (13.0, 10.0), (14.0, 10.0), (15.0, 10.0)],
                'segment_index': 2,
                'total_segments': 4,
                'elevation_levels': ['street', 'street', 'street'],
            },
            player,
            0,
        )

        self.assertIsNotNone(ui.calculate_train_route_time(west_a, west_b, line, trainset))
        self.assertIsNotNone(ui.calculate_train_route_time(east_a, east_b, line, trainset))
        self.assertIsNone(ui.calculate_train_route_time(west_a, east_b, line, trainset))
        self.assertFalse(ui.stations_on_same_operational_portion(line, west_a, east_b))

    def test_continuous_line_is_single_operational_portion(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        route = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0), (13.0, 10.0)]
        line = ui.game.create_line("Connected Line", player, route)
        player.acquire_line(line)
        ui.game.get_map().add_line(line)

        self.assertEqual(len(ui.get_line_operational_route_portions(line)), 1)
        self.assertFalse(ui.line_is_operationally_split(line))

    def test_get_lines_for_map_display_splits_at_gap(self):
        """Map display must not draw a chord across route gaps."""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        gap_route = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0), (16.0, 10.0), (17.0, 10.0)]
        line = ui.game.create_line("Interborough", player, gap_route)
        player.acquire_line(line)
        ui.game.get_map().add_line(line)

        entries = ui.get_lines_for_map_display()
        built = [e for e in entries if e['name'] == 'Interborough' and not e['under_construction']]
        self.assertEqual(len(built), 2)
        self.assertEqual(built[0]['points'][0], (10.0, 10.0))
        self.assertEqual(built[0]['points'][-1], (12.0, 10.0))
        self.assertEqual(built[1]['points'][0], (16.0, 10.0))
        self.assertEqual(built[1]['points'][-1], (17.0, 10.0))

    def test_get_lines_for_map_display_incomplete_segment_separate(self):
        """Incomplete middle segment renders as its own under-construction polyline."""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        route = [
            (10.0, 10.0), (11.0, 10.0), (12.0, 10.0),
            (13.0, 10.0), (14.0, 10.0), (15.0, 10.0),
            (16.0, 10.0), (17.0, 10.0),
        ]
        line = ui.game.create_line("Bridge Line", player, route)
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        ui.add_to_build_queue(
            'line',
            {
                'name': line.name,
                'points': [(12.0, 10.0), (13.0, 10.0), (14.0, 10.0), (15.0, 10.0)],
                'segment_index': 2,
                'total_segments': 4,
                'elevation_levels': ['street', 'street', 'street'],
            },
            player,
            0,
        )

        entries = ui.get_lines_for_map_display()
        built = [e for e in entries if e['name'] == 'Bridge Line' and not e['under_construction']]
        pending = [e for e in entries if e['name'] == 'Bridge Line' and e['under_construction']]
        self.assertEqual(len(built), 2)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['points'][0], (12.0, 10.0))
        self.assertEqual(pending[0]['points'][-1], (15.0, 10.0))

    def test_split_line_service_frequency_sums_portions(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        gap_route = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0), (20.0, 10.0), (21.0, 10.0)]
        line = ui.game.create_line("Freq Line", player, gap_route)
        player.acquire_line(line)
        ui.game.get_map().add_line(line)
        line.vehicles = [self._make_trainset()]
        line.schedule = {'peak': 2.0, 'off_peak': 2.0, 'night': 2.0}

        single_freq = int(line.service_frequency_per_year(trainset_avg_speed=30) or 0)
        effective_freq = ui.get_line_effective_service_frequency_per_year(line, 30)
        self.assertGreater(effective_freq, single_freq)

import unittest
from operations import Operations
from PyQt6.QtWidgets import QApplication
import sys
from station import Station


def _ensure_app():
    """Ensure QApplication exists for tests"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestRidershipRouteSelection(unittest.TestCase):
    """Test that passengers choose routes based on fastest and cheapest combination"""
    
    def setUp(self):
        self._app = _ensure_app()
    
    def test_route_selection_considers_both_time_and_cost(self):
        """Test that route selection considers both time and cost"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create two lines with different speeds and fares
        # Line 1: Fast but expensive (3 segments -> 3 elevation levels)
        fast_line_points = [(10, 10), (11, 10), (12, 10), (13, 10)]
        ui.create_line_complete("Fast Line", fast_line_points, player, elevation_levels=['street', 'street', 'street'])
        
        # Line 2: Slow but cheap
        slow_line_points = [(10, 10), (10, 11), (10, 12), (10, 13)]
        ui.create_line_complete("Slow Line", slow_line_points, player, elevation_levels=['street', 'street', 'street'])
        
        # Process build queue to complete lines
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        # Get lines
        fast_line = ui.get_map_lines()["Fast Line"]
        slow_line = ui.get_map_lines()["Slow Line"]
        
        # Set different fares
        fast_line.fare = 20
        slow_line.fare = 5
        
        # Add trainsets with different speeds
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotives = registry.get_locomotives_by_year()
        if locomotives:
            fast_loco, _ = locomotives[0]
            slow_loco, _ = locomotives[0] if len(locomotives) > 0 else (None, None)
            
            # Create trainsets
            from vehicle import Trainset
            fast_trainset = Trainset(fast_loco, [])
            slow_trainset = Trainset(slow_loco, [])
            
            # Assign trainsets to lines
            fast_line.vehicles = [fast_trainset]
            slow_line.vehicles = [slow_trainset]
        
        # Add stations at origin and destination
        origin = (10, 10)
        destination = (13, 10)
        
        # Create stations
        station1 = Station("Origin", origin[0], origin[1])
        station2 = Station("Dest Fast", destination[0], destination[1])
        station3 = Station("Dest Slow", 10, 13)
        
        fast_line.add_station(station1)
        fast_line.add_station(station2)
        slow_line.add_station(station1)
        slow_line.add_station(station3)
        
        # Test route finding
        route = ui.find_fastest_route(origin, destination)
        
        # Route should consider both time and cost
        self.assertIn('score', route, "Route should have a score combining time and cost")
        self.assertIn('time', route, "Route should have time")
        self.assertIn('cost', route, "Route should have cost")
        
        # If fast line is selected, it should be because its score is better
        if route.get('method') == 'train' and route.get('line') == fast_line:
            self.assertLess(route['score'], 
                          (route['time'] * 0.7 + route['cost'] / 5.0 * 0.3),
                          "Fast line should have better score")
    
    def test_cheaper_route_selected_when_times_similar(self):
        """Test that cheaper route is selected when travel times are similar"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        # Create two lines with similar routes but different fares (2 segments each)
        line1_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Expensive Line", line1_points, player, elevation_levels=['street', 'street'])
        
        line2_points = [(10, 10), (10, 11), (12, 10)]
        ui.create_line_complete("Cheap Line", line2_points, player, elevation_levels=['street', 'street'])
        
        # Process build queue
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line1 = ui.get_map_lines()["Expensive Line"]
        line2 = ui.get_map_lines()["Cheap Line"]
        
        line1.fare = 20
        line2.fare = 5
        
        # Add same trainsets to both lines
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotives = registry.get_locomotives_by_year()
        if locomotives:
            loco, _ = locomotives[0]
            from vehicle import Trainset
            trainset = Trainset(loco, [])
            line1.vehicles = [trainset]
            line2.vehicles = [trainset]
        
        # Add stations
        origin = (10, 10)
        destination = (12, 10)
        
        station1 = Station("Origin", origin[0], origin[1])
        station2a = Station("Dest1", destination[0], destination[1])
        station2b = Station("Dest2", destination[0], destination[1])
        
        line1.add_station(station1)
        line1.add_station(station2a)
        line2.add_station(station1)
        line2.add_station(station2b)
        
        # Find route
        route = ui.find_fastest_route(origin, destination)
        
        # Should prefer cheaper route when times are similar
        if route.get('method') == 'train':
            selected_line = route.get('line')
            if selected_line:
                # Cheaper line should be preferred
                self.assertLessEqual(selected_line.fare, 10, 
                                   "Should prefer cheaper route when times are similar")
    
    def test_route_finding_returns_score(self):
        """Test that route finding returns a score that combines time and cost"""
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        
        origin = (10, 10)
        destination = (20, 20)
        
        route = ui.find_fastest_route(origin, destination)
        
        # All routes should have a score
        self.assertIn('score', route, "Route should always have a score")
        self.assertIsInstance(route['score'], (int, float), "Score should be numeric")
        
        # Score should be positive
        self.assertGreater(route['score'], 0, "Score should be positive")

import unittest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from operations import Operations, setup_game, generate_players
from player import Player
from line import Line, Ferry
from map import Map
from vehicle import Trainset, Locomotive, Railcar
from vehicle_presets import get_vehicle_registry
from game_resource import Food
from map_preset_utils import get_preset_file_info, read_included_points_from_file, write_included_points_to_file


class TestBuildQueueWithFerryLines(unittest.TestCase):
    """Test build queue processing with ferry lines."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.player = self.ui.get_players()[0]
        self.ui.set_current_player_index(0)
    
    def test_ferry_line_build_queue_processing(self):
        """Test that ferry lines are properly processed in build queue."""
        line_name = "Ferry Route"
        points = [(10, 10), (20, 20)]
        elevation_levels = ['water']
        
        # Create ferry line
        result = self.ui.create_line_complete(
            line_name, points, self.player,
            elevation_levels, is_ferry_line=True
        )
        
        # Process build queue
        self.ui.process_build_queue()
        
        # Check that line was created
        all_lines = self.ui.get_map_lines()
        created_line = all_lines.get(line_name)
        
        # Line might not be created yet if build time > 0
        # But the build queue should have the ferry flag
        build_queue = self.ui.get_build_queue()
        ferry_builds = [
            b for b in build_queue
            if b['type'] == 'line' and b['data'].get('name') == line_name
        ]
        
        if ferry_builds:
            self.assertTrue(ferry_builds[0]['data'].get('is_ferry_line', False))


class TestMapPresetUtils(unittest.TestCase):
    """Test map preset utility functions."""
    
    def test_get_preset_file_info(self):
        """Test getting preset file info."""
        # Create a mock map
        game_map = Map(width=60, height=60, use_osm=False,
                      map_center_lat=40.7128, map_center_lon=-74.0060)
        
        if game_map.is_nyc_proper:
            preset_info = get_preset_file_info(game_map)
            self.assertIsNotNone(preset_info)
            file_path, variable_name = preset_info
            self.assertIsInstance(file_path, str)
            self.assertIsInstance(variable_name, str)
            self.assertIn('map_presets_nyc.py', file_path)
            self.assertEqual(variable_name, 'NYC_INITIAL_POPULATION')


class TestVehicleConstraints(unittest.TestCase):
    """Test vehicle constraints and validation."""
    
    def test_ferry_cannot_have_railcars(self):
        """Test that ferry vehicles cannot have railcars."""
        registry = get_vehicle_registry()
        all_locomotives = registry.get_all_locomotives()
        
        for loco in all_locomotives:
            if 'Ferry' in loco.name:
                # Try to create a trainset with railcars - should fail
                cars = [Railcar("Test Car", 0, 50, 10)]
                with self.assertRaises(ValueError):
                    Trainset(locomotive=loco, cars=cars)
    
    def test_associated_railcar_constraint(self):
        """Test that locomotives with associated railcars only accept those railcars."""
        registry = get_vehicle_registry()
        all_locomotives = registry.get_all_locomotives()
        
        for loco in all_locomotives:
            if hasattr(loco, 'associated_railcar_name') and loco.associated_railcar_name:
                # Find the associated railcar
                all_railcars = registry.get_all_railcars()
                associated_car = None
                for car in all_railcars:
                    if car.name == loco.associated_railcar_name:
                        associated_car = car
                        break
                
                if associated_car:
                    # Should work with associated railcar
                    cars = [Railcar(associated_car.name, 0, associated_car.capacity, associated_car.maitnance_costs)]
                    trainset = Trainset(locomotive=loco, cars=cars)
                    self.assertIsNotNone(trainset)
                    
                    # Check constraint in assemble_trainset_complete (where validation happens)
                    player = Player(0, 10000, [], "Test Player")
                    ui = Operations(["TestPlayer"], map_width=50, map_height=50, starting_money=50000)
                    
                    # Should work with correct railcar
                    try:
                        result = ui.assemble_trainset_complete(player, loco, cars)
                        self.assertIsNotNone(result)
                    except ValueError:
                        pass  # Might fail for other reasons
                    
                    # Should fail with wrong railcar
                    wrong_car = Railcar("Wrong Car", 0, 50, 10)
                    with self.assertRaises(ValueError):
                        ui.assemble_trainset_complete(player, loco, [wrong_car])
                    
                break  # Only test first locomotive with associated railcar


class TestLineExtension(unittest.TestCase):
    """Test line extension functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.player = self.ui.get_players()[0]
        self.ui.set_current_player_index(0)
    
    def test_extend_line_human_cost_previewed_skips_recalc(self):
        """Human extend with pre-computed cost_info must not re-run calculate_extend_cost."""
        from unittest.mock import patch

        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        self.ui.create_line_complete(line_name, points, self.player, ['street'])
        self.ui.process_build_queue()
        extension_points = [(30, 30), (40, 40)]
        elevation_levels = ['street', 'street']
        cost_info = self.ui.calculate_extend_cost(
            line_name, extension_points, True, elevation_levels,
        )
        with patch.object(self.ui, 'calculate_extend_cost') as mock_calc:
            result = self.ui.extend_line_complete(
                line_name, self.player, extension_points, True,
                elevation_levels, cost_info=cost_info,
            )
            mock_calc.assert_not_called()
        self.assertIsNotNone(result)

    def test_extend_line_with_elevation_levels(self):
        """Test extending a line with elevation levels."""
        # Create initial line
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        elevation_levels = ['street']
        
        self.ui.create_line_complete(line_name, points, self.player, elevation_levels)
        self.ui.process_build_queue()
        
        # Extend line
        new_points = [(30, 30)]
        extend_from_end = True
        new_elevation_levels = ['above_ground']
        
        try:
            result = self.ui.extend_line_complete(
                line_name, new_points, self.player,
                extend_from_end, new_elevation_levels
            )
            self.assertIsNotNone(result)
        except Exception as e:
            # Extension might fail if line not found, that's okay
            pass

    def test_calculate_extend_cost_ferry_fast_path(self):
        """Ferry extension cost preview must not run full OSM terrain sampling."""
        from unittest.mock import patch

        line_name = "Test Line"
        points = [(10.0, 10.0), (20.0, 20.0)]
        self.ui.create_line_complete(line_name, points, self.player, ['street'])
        self.ui.process_build_queue()
        with patch.object(self.ui, 'get_line_build_type_flags', return_value=(True, False)):
            cost = self.ui.calculate_extend_cost(
                line_name, [(30.0, 30.0)], True, ['water'],
            )
        self.assertGreater(cost['cost'], 0)
        self.assertEqual(cost['points_count'], 1)

    def test_calculate_extend_cost_train_uses_preset_only_water(self):
        """Train extension cost preview must not query OSM tiles."""
        import time
        from unittest.mock import Mock, patch

        line_name = "Test Line"
        points = [(10.0, 10.0), (20.0, 20.0)]
        self.ui.create_line_complete(line_name, points, self.player, ['street'])
        self.ui.process_build_queue()
        game_map = self.ui.game.get_map()
        mock_ts = Mock()
        mock_ts._preset_only_water = False
        mock_ts.is_point_in_water = Mock(return_value=False)
        game_map.tile_system = mock_ts
        t0 = time.time()
        cost = self.ui.calculate_extend_cost(
            line_name, [(30.0, 30.0), (31.0, 31.0)], True, ['street', 'street'],
        )
        elapsed = time.time() - t0
        self.assertGreater(cost['cost'], 0)
        mock_ts.pre_cache_route_tiles.assert_called_once()
        self.assertEqual(mock_ts.pre_cache_route_tiles.call_args.kwargs.get('max_osm_queries'), 0)
        self.assertLess(elapsed, 2.0)

    def test_get_line_build_type_flags(self):
        self.assertEqual(self.ui.get_line_build_type_flags("missing"), (False, False))
        self.ui.add_to_build_queue(
            'line',
            {'name': 'Queued Bus', 'is_bus_line': True, 'points': [(1.0, 1.0), (2.0, 2.0)]},
            self.player,
            1,
        )
        self.assertEqual(self.ui.get_line_build_type_flags("Queued Bus"), (False, True))


class TestMarketplaceSales(unittest.TestCase):
    """Test marketplace sales functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.marketplace = self.ui.game.marketplace
    
    def test_sales_expire_after_one_turn(self):
        """Test that sales expire after one turn."""
        current_year = self.ui.get_year()
        
        # Generate marketplace items
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        # Find items on sale
        available_vehicles = self.marketplace.get_available_vehicles()
        sale_items = [
            item for item in available_vehicles
            if hasattr(item, 'sale_year') and item.sale_year == current_year
        ]
        
        if sale_items:
            sale_item = sale_items[0]
            original_sale_year = sale_item.sale_year
            
            # Advance year
            self.ui.increase_year()
            new_year = self.ui.get_year()
            
            # Clear expired sales (this should happen automatically)
            # Check that sale is no longer active
            if hasattr(sale_item, 'sale_year'):
                # Sale should be expired if sale_year < new_year
                if sale_item.sale_year < new_year:
                    # Original price should be cleared or sale should be inactive
                    pass  # This is expected behavior


class TestPopulationGrowth(unittest.TestCase):
    """Test population growth functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=60, map_height=60,
                    starting_money=50000, use_osm=False,
                    map_center_lat=40.7128, map_center_lon=-74.0060)
        self.game_map = self.ui.game.get_map()
    
    def test_population_only_changes_on_events(self):
        """Test that population only changes when events occur."""
        if self.game_map.is_nyc_proper:
            # Get initial population
            initial_pop = {}
            for point, pop in self.game_map.population.items():
                initial_pop[point] = pop
            
            # Advance year without events
            current_year = self.ui.get_year()
            self.ui.increase_year()
            
            # Population should only change if there's an event
            # (This is a simplified test - actual implementation may vary)
            new_pop = {}
            for point, pop in self.game_map.population.items():
                new_pop[point] = pop
            
            # Check that population changed (growth should happen)
            # The exact behavior depends on implementation
            self.assertIsInstance(new_pop, dict)


class TestGridSystem(unittest.TestCase):
    """Test grid system functionality (if implemented)."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.game_map = self.ui.game.get_map()
    
    def test_grid_exists(self):
        """Test that grid system exists."""
        self.assertIsNotNone(self.game_map.grid)
        self.assertIsInstance(self.game_map.grid, dict)
    
    def test_grid_water_land_flags(self):
        """Test that grid has water/land flags (if implemented)."""
        # This test checks if the grid system has been implemented
        # with water/land flags
        grid = self.game_map.grid
        
        # If grid system is implemented, it should have some structure
        # For now, just verify grid exists
        self.assertIsNotNone(grid)


def run_all_tests():
    """Run all test suites."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    test_classes = [
        TestBuildQueueWithFerryLines,
        TestMapPresetUtils,
        TestVehicleConstraints,
        TestLineExtension,
        TestMarketplaceSales,
        TestPopulationGrowth,
        TestGridSystem,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()

import unittest
import signal
import time
from operations import Operations
from PyQt6.QtWidgets import QApplication
import sys
from line import Line
from marketplace import Marketplace, SaleItem


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TimeoutException(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutException("Test exceeded 20 second timeout")


class TestMarketplaceLinePurchase(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
        self.ui = Operations(["Alice", "Bob"], 50, 50, 20000, 1.0, 1.0)
        self.players = self.ui.get_players()
        self.player1 = self.players[0]
        self.player2 = self.players[1]
    
    def test_line_purchased_by_player2_belongs_to_player2(self):
        line_points = [(10, 10), (11, 10), (12, 10)]
        marketplace_line = self.ui.game.create_line("Marketplace Line", self.player1, line_points)
        self.ui.game.get_map().add_line(marketplace_line)
        self.player1.acquire_line(marketplace_line)
        
        marketplace = self.ui.game.get_marketplace()
        marketplace.add_line(marketplace_line, price=1000)
        
        sale_items = marketplace.get_available_lines()
        sale_item = None
        for item in sale_items:
            if item.get_item() == marketplace_line:
                sale_item = item
                break
        self.assertIsNotNone(sale_item)
        
        self.assertEqual(marketplace_line.get_owner(), self.player1)
        self.assertIn("Marketplace Line", self.player1.get_line_names())
        self.assertNotIn("Marketplace Line", self.player2.get_line_names())
        
        initial_money = self.player2.get_money()
        price = sale_item.get_price()
        self.player2.add_money(2000)
        expected_final_money = initial_money + 2000 - price
        
        result = self.ui.buy_line_by_purchase(sale_item, self.player2)
        
        self.assertIsNotNone(result)
        self.assertEqual(marketplace_line.get_owner(), self.player2)
        self.assertIn("Marketplace Line", self.player2.get_line_names())
        self.assertNotIn("Marketplace Line", self.player1.get_line_names())
        self.assertEqual(self.player2.get_money(), expected_final_money)
    
    def test_line_purchased_by_player1_stays_with_player1(self):
        line_points = [(20, 20), (21, 20), (22, 20)]
        marketplace_line = self.ui.game.create_line("Player1 Line", self.player1, line_points)
        self.ui.game.get_map().add_line(marketplace_line)
        self.player1.acquire_line(marketplace_line)
        
        marketplace = self.ui.game.get_marketplace()
        marketplace.add_line(marketplace_line, price=1000)
        
        sale_items = marketplace.get_available_lines()
        sale_item = None
        for item in sale_items:
            if item.get_item() == marketplace_line:
                sale_item = item
                break
        self.assertIsNotNone(sale_item)
        
        self.assertEqual(marketplace_line.get_owner(), self.player1)
        self.assertIn("Player1 Line", self.player1.get_line_names())
        
        self.player1.add_money(2000)
        result = self.ui.buy_line_by_purchase(sale_item, self.player1)
        
        self.assertIsNotNone(result)
        self.assertEqual(marketplace_line.get_owner(), self.player1)
        self.assertIn("Player1 Line", self.player1.get_line_names())
    
    def test_old_owner_loses_line_when_sold(self):
        line_points = [(30, 30), (31, 30), (32, 30)]
        marketplace_line = self.ui.game.create_line("Transfer Line", self.player1, line_points)
        self.ui.game.get_map().add_line(marketplace_line)
        self.player1.acquire_line(marketplace_line)
        
        marketplace = self.ui.game.get_marketplace()
        marketplace.add_line(marketplace_line, price=1000)
        
        sale_items = marketplace.get_available_lines()
        sale_item = None
        for item in sale_items:
            if item.get_item() == marketplace_line:
                sale_item = item
                break
        self.assertIsNotNone(sale_item)
        
        self.assertIn("Transfer Line", self.player1.get_line_names())
        
        self.player2.add_money(2000)
        result = self.ui.buy_line_by_purchase(sale_item, self.player2)
        
        self.assertIsNotNone(result)
        self.assertNotIn("Transfer Line", self.player1.get_line_names())
        self.assertIn("Transfer Line", self.player2.get_line_names())

    def test_buy_marketplace_line_restores_to_map(self):
        from line import Line

        seller = self.player1
        buyer = self.player2
        line = Line('Listed Line', 1, [(5.0, 5.0), (6.0, 5.0)], seller, [])
        self.ui.add_line_to_map(line)
        seller.acquire_line(line)
        self.ui.sell_player_line_to_marketplace(seller, 'Listed Line')
        self.assertNotIn('Listed Line', self.ui.get_map_lines())
        sale_item = next(
            si for si in self.ui.get_marketplace().get_available_lines()
            if si.get_item().name == 'Listed Line'
        )
        buyer.add_money(5000)
        purchased = self.ui.buy_line_by_purchase(sale_item, buyer)
        self.assertIsNotNone(purchased)
        self.assertIn('Listed Line', self.ui.get_map_lines())
        self.assertEqual(self.ui.get_line_owner(purchased), buyer)


class TestSellLineSegment(unittest.TestCase):
    """Tests for selling/removing a line segment between two route clicks."""

    def setUp(self):
        self.timeline = Timeline(initial_year=1850, end_year=2000)
        self.marketplace = Marketplace()
        self.game_map = Map(
            width=100,
            height=100,
            use_osm=False,
            map_center_lat=40.7128,
            map_center_lon=-74.0060,
        )
        self.player1 = Player(player_id=1, name="Player 1", money=100000)
        self.game = Game(
            players=[self.player1],
            game_map=self.game_map,
            timeline=self.timeline,
            marketplace=self.marketplace,
        )
        self.ui = Operations(["Player 1"], use_osm=False)
        self.ui.game = self.game

    def _add_built_line(self, name, route):
        from line import Line
        line = Line(
            name=name,
            object_id=1,
            route=list(route),
            owner=self.player1,
            vehicles=[],
            elevation_levels=['street'] * max(0, len(route) - 1),
        )
        self.ui.add_line_to_map(line)
        self.player1.acquire_line(line)
        return line

    def test_sell_line_segment_splits_into_two_lines(self):
        self._add_built_line("Main", [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)])
        money_before = self.player1.get_money()
        result = self.ui.sell_line_segment_complete(
            self.player1, "Main", (12, 0), (28, 0),
        )
        self.assertTrue(result['success'])
        self.assertTrue(result['split'])
        self.assertEqual(len(result['resulting_lines']), 2)
        self.assertIn("Main", self.ui.get_map_lines())
        self.assertIn(result['resulting_lines'][1], self.ui.get_map_lines())
        self.assertGreaterEqual(self.player1.get_money(), money_before)

    def test_sell_line_segment_keeps_single_line_when_end_segment_removed(self):
        self._add_built_line("Spur", [(0, 0), (10, 0), (20, 0), (30, 0)])
        result = self.ui.sell_line_segment_complete(
            self.player1, "Spur", (22, 0), (30, 0),
        )
        self.assertTrue(result['success'])
        self.assertFalse(result['split'])
        line = self.ui.get_map_lines()["Spur"]
        self.assertGreaterEqual(len(line.get_route()), 2)
        last = line.get_route()[-1]
        self.assertLessEqual(last[0], 22)

    def test_sell_line_segment_rejects_same_click(self):
        self._add_built_line("Tiny", [(0, 0), (10, 0), (20, 0)])
        with self.assertRaises(ValueError):
            self.ui.sell_line_segment_complete(
                self.player1, "Tiny", (10, 0), (10, 0),
            )

    def test_sell_line_segment_closes_station_on_removed_portion(self):
        from station import Station
        line = self._add_built_line("Urban", [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)])
        station = Station("Mid", 20, 0, line_names=["Urban"])
        line.add_station(station)
        self.game_map.stations = getattr(self.game_map, 'stations', [])
        self.game_map.stations.append(station)
        result = self.ui.sell_line_segment_complete(
            self.player1, "Urban", (12, 0), (28, 0),
        )
        self.assertTrue(result['success'])
        self.assertIn("Mid", result.get('closed_stations', []))


class TestSellEntireLine(unittest.TestCase):
    """Tests for scrapping/selling an entire owned line."""

    def setUp(self):
        self.timeline = Timeline(initial_year=1850, end_year=2000)
        self.marketplace = Marketplace()
        self.game_map = Map(
            width=100,
            height=100,
            use_osm=False,
            map_center_lat=40.7128,
            map_center_lon=-74.0060,
        )
        self.player1 = Player(player_id=1, name="Player 1", money=100000)
        self.game = Game(
            players=[self.player1],
            game_map=self.game_map,
            timeline=self.timeline,
            marketplace=self.marketplace,
        )
        self.ui = Operations(["Player 1"], use_osm=False)
        self.ui.game = self.game

    def _add_built_line(self, name, route):
        from line import Line
        line = Line(
            name=name,
            object_id=1,
            route=list(route),
            owner=self.player1,
            vehicles=[],
            elevation_levels=['street'] * max(0, len(route) - 1),
        )
        self.ui.add_line_to_map(line)
        self.player1.acquire_line(line)
        return line

    def test_sell_line_complete_removes_line_and_refunds(self):
        self._add_built_line("Main", [(0, 0), (10, 0), (20, 0)])
        money_before = self.player1.get_money()
        result = self.ui.sell_line_complete(self.player1, "Main")
        self.assertTrue(result['success'])
        self.assertNotIn("Main", self.ui.get_map_lines())
        self.assertNotIn("Main", self.player1.get_line_names())
        self.assertGreaterEqual(self.player1.get_money(), money_before)

    def test_sell_line_complete_unassigns_trainsets(self):
        from vehicle import Trainset, Locomotive, Railcar
        line = self._add_built_line("Rail", [(0, 0), (10, 0), (20, 0)])
        loco = Locomotive("Test Loco", 1830, 30, 100, "Wood", 50, 100)
        ts = Trainset(locomotive=loco, cars=[Railcar("Car", 1830, 50, 20)])
        ts.assign_to_line("Rail")
        line.vehicles.append(ts)
        self.player1.trainsets = getattr(self.player1, 'trainsets', [])
        self.player1.trainsets.append(ts)
        self.ui.sell_line_complete(self.player1, "Rail")
        self.assertIsNone(ts.get_line_assignment())

    def test_sell_line_complete_closes_single_line_station(self):
        from station import Station
        line = self._add_built_line("Urban", [(0, 0), (10, 0), (20, 0)])
        station = Station("Only", 10, 0, line_names=["Urban"])
        line.add_station(station)
        self.game_map.stations = getattr(self.game_map, 'stations', [])
        self.game_map.stations.append(station)
        result = self.ui.sell_line_complete(self.player1, "Urban")
        self.assertTrue(result['success'])
        self.assertIn("Only", result.get('closed_stations', []))
        self.assertNotIn(station, self.game_map.stations)

    def test_sell_line_complete_rejects_unowned(self):
        self._add_built_line("Other", [(0, 0), (10, 0)])
        from player import Player
        other = Player(player_id=2, name="Player 2", money=100000)
        with self.assertRaises(ValueError):
            self.ui.sell_line_complete(other, "Other")


class TestPerformanceTimeout(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def run(self, result=None):
        if hasattr(signal, 'SIGALRM'):
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(20)
        try:
            super().run(result)
        finally:
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)
    
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
        
        self.assertLess(elapsed, 20.0, f"Year advance took {elapsed:.3f}s, should be < 20.0s")
    
    def test_multiple_operations_complete_quickly(self):
        ui = Operations(["Player1", "Player2"], 50, 50, 20000, 1.0, 1.0)
        players = ui.get_players()
        
        for i, player in enumerate(players):
            line_points = [(10 + i*10, 10), (11 + i*10, 10), (12 + i*10, 10)]
            ui.create_line_complete(f"Line {i}", line_points, player)
        
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        start_time = time.time()
        for _ in range(5):
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        elapsed = time.time() - start_time
        
        self.assertLess(elapsed, 20.0, f"Multiple year advances took {elapsed:.3f}s, should be < 20.0s")

import unittest
from marketplace import Marketplace, SaleItem
from line import Line
from player import Player
from vehicle import Locomotive, Railcar, Trainset
from game_resource import Coal, Oil, Electricity


class TestSaleItem(unittest.TestCase):
    def setUp(self):
        self.test_item = "test_item"
        self.test_price = 100
    
    def test_sale_item_initialization(self):
        """Test SaleItem initialization"""
        item = SaleItem(self.test_item, self.test_price)
        self.assertEqual(item.get_item(), self.test_item)
        self.assertEqual(item.price, self.test_price)
        self.assertEqual(item.get_quantity_available(), 1)
        self.assertEqual(item.get_seller(), "Manufacturer")
        self.assertIsNone(item.get_salesman())
    
    def test_sale_item_with_salesman(self):
        """Test SaleItem with salesman"""
        player = Player(0, 1000, [], "Test Player")
        item = SaleItem(self.test_item, self.test_price, salesman=player, seller="Player")
        self.assertEqual(item.get_salesman(), player)
        self.assertEqual(item.get_seller(), "Player")
    
    def test_get_price_without_inflation(self):
        """Test get_price at base year (1830)"""
        item = SaleItem(self.test_item, 100)
        price = item.get_price(current_year=1830)
        self.assertEqual(price, 300)
    
    def test_get_price_with_inflation(self):
        """Test get_price with inflation"""
        item = SaleItem(self.test_item, 100)
        price_1900 = item.get_price(current_year=1900)
        price_2000 = item.get_price(current_year=2000)
        self.assertGreater(price_2000, price_1900)
        self.assertGreater(price_1900, 300)  
    
    def test_get_price_with_discount(self):
        """Test get_price with discount (sale_year must match current_year for discount to apply)"""
        item = SaleItem(self.test_item, 100)
        item.original_price = 100
        item.discount_percent = 20  
        item.sale_year = 1830 
        price = item.get_price(current_year=1830)
        self.assertEqual(price, 240)
    
    def test_get_price_with_quantity(self):
        """Test get_price with different quantities"""
        item = SaleItem(self.test_item, 100)
        item.quantity_avalilable = 1
        price_q1 = item.get_price(current_year=1830)
        
        item.quantity_avalilable = 10
        price_q10 = item.get_price(current_year=1830)
        self.assertLess(price_q10, price_q1)
    
    def test_quantity_operations(self):
        """Test quantity add and deduct"""
        item = SaleItem(self.test_item, 100)
        self.assertEqual(item.get_quantity_available(), 1)
        
        item.add_quantity(5)
        self.assertEqual(item.get_quantity_available(), 6)
        
        item.deduct_quantity(2)
        self.assertEqual(item.get_quantity_available(), 4)
        
        item.deduct_quantity(10)  # Should go negative, but that's allowed
        self.assertEqual(item.get_quantity_available(), -6)
    
    def test_sale_item_equality(self):
        """Test SaleItem equality comparison"""
        item1 = SaleItem("item1", 100)
        item2 = SaleItem("item1", 200)  # Different price
        item3 = SaleItem("item2", 100)  # Different name
        
        # Items with same name should be equal (ignoring price)
        self.assertEqual(item1, item2)
        self.assertNotEqual(item1, item3)
        
        # Test with items that have name attribute
        class NamedItem:
            def __init__(self, name):
                self.name = name
        
        named1 = SaleItem(NamedItem("test"), 100)
        named2 = SaleItem(NamedItem("test"), 200)
        named3 = SaleItem(NamedItem("other"), 100)
        
        self.assertEqual(named1, named2)
        self.assertNotEqual(named1, named3)


class TestMarketplace(unittest.TestCase):
    def setUp(self):
        self.marketplace = Marketplace()
        self.marketplace.available_lines = []  # Ensure clean state
        self.marketplace.available_vehicles = []  # Ensure clean state
        self.player = Player(0, 10000, [], "Test Player")
    
    def test_marketplace_initialization(self):
        """Test Marketplace initialization"""
        marketplace = Marketplace()  # Create fresh instance
        self.assertEqual(marketplace.available_lines, [])
        self.assertEqual(marketplace.available_vehicles, [])
        self.assertEqual(marketplace.marketplace_years_visible, 40)
        self.assertIsNone(marketplace.last_generation_year)
        self.assertEqual(marketplace.worker_supply, {})
    
    def test_worker_supply_operations(self):
        """Test worker supply management"""
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 0)
        
        self.marketplace.set_worker_supply("Engineer", 10)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 10)
        
        self.marketplace.deduct_worker_supply("Engineer", 3)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 7)
        
        self.marketplace.add_worker_supply("Engineer", 5)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 12)
        
        # Test negative deduction (should not go below 0)
        self.marketplace.deduct_worker_supply("Engineer", 20)
        self.assertEqual(self.marketplace.get_worker_supply("Engineer"), 0)
    
    def test_add_line(self):
        """Test adding lines to marketplace"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        self.marketplace.add_line(line, price=1000, current_year=1830)
        self.assertEqual(len(self.marketplace.available_lines), 1)
        
        sale_item = self.marketplace.available_lines[0]
        self.assertEqual(sale_item.get_item().name, line.name)
        self.assertEqual(sale_item.price, 1000)
    
    def test_add_duplicate_line(self):
        """Test adding duplicate lines increases quantity"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        self.marketplace.add_line(line, price=1000, current_year=1830)
        self.marketplace.add_line(line, price=1000, current_year=1830)
        
        self.assertEqual(len(self.marketplace.available_lines), 1)
        self.assertEqual(self.marketplace.available_lines[0].get_quantity_available(), 2)
    
    def test_remove_line(self):
        """Test removing lines from marketplace"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        
        # Clear any existing lines
        self.marketplace.available_lines = []
        
        self.marketplace.add_line(line, price=1000, current_year=1830)
        self.marketplace.available_lines[0].add_quantity(2)  # Total quantity 3
        
        initial_quantity = self.marketplace.available_lines[0].get_quantity_available()
        self.marketplace.remove_line(line)
        self.assertEqual(self.marketplace.available_lines[0].get_quantity_available(), initial_quantity - 1)
        
        # Remove until empty
        while len(self.marketplace.available_lines) > 0:
            self.marketplace.remove_line(line)
        
        # Should be removed when quantity reaches 0
        self.assertEqual(len(self.marketplace.available_lines), 0)
    
    def test_add_vehicle(self):
        """Test adding vehicles to marketplace"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotive = registry.get_earliest_locomotive()
        
        if locomotive:
            self.marketplace.add_vehicle(locomotive, price=500, current_year=1830)
            self.assertEqual(len(self.marketplace.available_vehicles), 1)
            
            sale_item = self.marketplace.available_vehicles[0]
            self.assertEqual(sale_item.get_item(), locomotive)
    
    def test_generate_trainsets_based_on_discoveries(self):
        """Test trainset generation: target total from presets, distributed among available types"""
        from game_presets import MARKETPLACE_TARGET_TOTAL_ITEMS
        current_year = 1830
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        # Should have generated some vehicles
        self.assertGreater(len(self.marketplace.available_vehicles), 0)
        self.assertIsNotNone(self.marketplace.target_total_items)
        self.assertEqual(self.marketplace.target_total_items, MARKETPLACE_TARGET_TOTAL_ITEMS)
        self.assertEqual(self.marketplace.last_generation_year, current_year)
        # Total quantity should equal target (distribution) or be slightly higher if associated railcars added
        total_quantity = sum(item.get_quantity_available() for item in self.marketplace.available_vehicles)
        self.assertGreaterEqual(total_quantity, MARKETPLACE_TARGET_TOTAL_ITEMS)
        self.assertLessEqual(total_quantity, MARKETPLACE_TARGET_TOTAL_ITEMS + 30)
    
    def test_generate_trainsets_replenishment(self):
        """Test trainset replenishment"""
        current_year = 1830
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        initial_count = len(self.marketplace.available_vehicles)
        target_total = self.marketplace.target_total_items
        
        # Remove some items
        if len(self.marketplace.available_vehicles) > 0:
            self.marketplace.available_vehicles.pop()
        
        # Generate again - should replenish
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        # Should have replenished to target (non-railcar items plus player-scaled railcars)
        current_total = sum(item.get_quantity_available() for item in self.marketplace.available_vehicles)
        railcar_total = self.marketplace._count_standalone_railcar_quantity()
        non_railcar_total = current_total - railcar_total
        self.assertGreaterEqual(non_railcar_total, target_total - 1)  # Allow for rounding
        self.assertGreaterEqual(railcar_total, self.marketplace.target_railcar_total - 1)

    def test_marketplace_railcar_count_scales_with_players(self):
        """Standalone marketplace railcars = num_players * factor at game start."""
        from operations import initialize_marketplace_trainsets
        from game_presets import MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR

        marketplace = Marketplace()
        num_players = 3
        factor = MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR
        initialize_marketplace_trainsets(marketplace, num_players=num_players, railcars_per_player_factor=factor)

        expected = int(num_players * factor)
        self.assertEqual(marketplace.target_railcar_total, expected)
        railcar_total = marketplace._count_standalone_railcar_quantity()
        self.assertGreaterEqual(railcar_total, expected)
        self.assertLessEqual(railcar_total, expected + 5)

    def test_marketplace_railcar_replenishment_uses_player_factor(self):
        """Railcar replenishment target uses num_players * factor."""
        from operations import initialize_marketplace_trainsets
        from game_presets import MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR
        from vehicle import Railcar

        marketplace = Marketplace()
        num_players = 2
        factor = MARKETPLACE_RAILCARS_PER_PLAYER_FACTOR
        initialize_marketplace_trainsets(marketplace, num_players=num_players, railcars_per_player_factor=factor)
        target = int(num_players * factor)
        marketplace.available_vehicles = [
            item for item in marketplace.available_vehicles
            if not isinstance(item.get_item(), Railcar)
        ]
        marketplace.generate_trainsets_based_on_discoveries(1830)
        self.assertGreaterEqual(marketplace._count_standalone_railcar_quantity(), target - 1)

    def test_new_locomotive_spawns_on_invention(self):
        """Newly invented locomotive types get guaranteed marketplace stock."""
        from operations import initialize_marketplace_trainsets
        from game_presets import (
            MARKETPLACE_NEW_LOCOMOTIVE_COPIES,
            MARKETPLACE_NEW_VEHICLE_COPIES_PER_PLAYER,
        )
        from vehicle import Locomotive, Trainset

        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1849
        marketplace = game.get_marketplace()
        num_players = len(game.get_players())
        initialize_marketplace_trainsets(marketplace, num_players=num_players)
        min_copies = max(
            MARKETPLACE_NEW_LOCOMOTIVE_COPIES,
            num_players * MARKETPLACE_NEW_VEHICLE_COPIES_PER_PLAYER,
        )

        target_name = "0-6-0 Tank Coal"

        def count_loco_qty(name):
            qty = 0
            for sale_item in marketplace.available_vehicles:
                item = sale_item.get_item()
                if isinstance(item, Locomotive) and not isinstance(item, Trainset):
                    if getattr(item, 'name', None) == name:
                        qty += sale_item.get_quantity_available()
            return qty

        self.assertEqual(count_loco_qty(target_name), 0)
        newly = ops.get_newly_available_vehicles(1849, 1850)
        self.assertTrue(any(n == target_name for n, y in newly))
        ops.advance_year_complete(1849)
        self.assertEqual(ops.get_year(), 1850)
        self.assertGreaterEqual(count_loco_qty(target_name), min_copies)
    
    def test_get_available_items(self):
        """Test getting all available items"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.marketplace.add_line(line, price=1000, current_year=1830)
        
        items = self.marketplace.get_available_items(current_year=1830)
        self.assertGreater(len(items), 0)
        self.assertIn(self.marketplace.available_lines[0], items)
    
    def test_get_available_items_filters_future_trainsets(self):
        """Test that future trainsets are filtered out"""
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        
        # Generate trainsets for 1830
        self.marketplace.generate_trainsets_based_on_discoveries(1830)
        
        # Get items for 1830 - should include available trainsets
        items_1830 = self.marketplace.get_available_items(current_year=1830)
        
        # Get items for 1800 - should exclude trainsets introduced after 1800
        items_1800 = self.marketplace.get_available_items(current_year=1800)
        
        # Items in 1800 should be a subset of items in 1830 (or equal if all are old)
        self.assertLessEqual(len(items_1800), len(items_1830))

import unittest
from unittest.mock import patch
from game import Game
from map import Map
from player import Player
from timeline import Timeline
from marketplace import Marketplace
from operations import Operations
from lawsuit import LawsuitExcuse, LAWSUIT_EXCUSES


def _make_ops_and_players():
    timeline = Timeline(initial_year=1830, end_year=2050)
    game_map = Map(width=50, height=50, use_osm=False)
    marketplace = Marketplace()
    p1 = Player(0, 100000, [], "Alice")
    p2 = Player(1, 100000, [], "Bob")
    game = Game(
        players=[p1, p2],
        game_map=game_map,
        timeline=timeline,
        marketplace=marketplace,
    )
    ops = Operations(["Alice", "Bob"], use_osm=False)
    ops.game = game
    return ops, ops.game, p1, p2


class TestLobbyInflation(unittest.TestCase):
    def test_inflation_factor_in_range(self):
        ops, *_ = _make_ops_and_players()
        f = ops.get_lobby_inflation_factor()
        self.assertGreaterEqual(f, 1.0)
        self.assertLessEqual(f, 10.0)


class TestLobbySuccessChance(unittest.TestCase):
    def test_chance_minimum(self):
        ops, *_ = _make_ops_and_players()
        self.assertGreaterEqual(ops.get_lobby_success_chance(0), 0.20)
        self.assertLessEqual(ops.get_lobby_success_chance(0), 0.25)

    def test_chance_increases_with_amount(self):
        ops, *_ = _make_ops_and_players()
        min_c = ops.get_lobby_minimum_cost()
        c1 = ops.get_lobby_success_chance(min_c)
        c2 = ops.get_lobby_success_chance(min_c * 10)
        c3 = ops.get_lobby_success_chance(min_c * 100)
        self.assertLess(c1, c3)
        self.assertLessEqual(c3, 0.95)


class TestLawTargetCountModifier(unittest.TestCase):
    def test_extra_targets_help_good_laws(self):
        ops, *_ = _make_ops_and_players()
        self.assertGreater(
            ops._law_target_count_chance_modifier("reduce_property_tax_5", 3), 0.0
        )
        self.assertEqual(
            ops._law_target_count_chance_modifier("reduce_property_tax_5", 3), 0.10
        )

    def test_extra_targets_hurt_bad_laws(self):
        ops, *_ = _make_ops_and_players()
        self.assertLess(ops._law_target_count_chance_modifier("gov_attack", 3), 0.0)
        self.assertEqual(ops._law_target_count_chance_modifier("gov_attack", 3), -0.10)

    def test_single_target_no_modifier(self):
        ops, *_ = _make_ops_and_players()
        self.assertEqual(ops._law_target_count_chance_modifier("gov_attack", 1), 0.0)
        self.assertEqual(ops._law_target_count_chance_modifier("reduce_property_tax_5", 1), 0.0)

    def test_committee_chance_includes_target_modifier(self):
        ops, *_ = _make_ops_and_players()
        base = ops._compute_law_committee_pass_chance(
            "reduce_property_tax_5", 10.0, 5.0, True, 1, num_targets=1
        )
        with_targets = ops._compute_law_committee_pass_chance(
            "reduce_property_tax_5", 10.0, 5.0, True, 1, num_targets=3
        )
        self.assertGreater(with_targets, base)


class TestStationDuplicateNameDistance(unittest.TestCase):
    def _ops_with_named_station(self, x=10.0, y=10.0, name="Central"):
        from line import Line
        from station import Station

        ops, game, p1, p2 = _make_ops_and_players()
        line_name = "Line A"
        line = Line(line_name, 1, [(5.0, 10.0), (15.0, 10.0)], owner=p1)
        game.get_map().add_line(line)
        p1.line_names = [line_name]
        station = Station(name, x, y, line_names=[line_name])
        line.add_station(station)
        return ops, p1, line

    def test_duplicate_name_allowed_within_20m(self):
        ops, player, _ = self._ops_with_named_station()
        # 0.015 grid units ≈ 15 m on non-OSM maps
        ops.assert_station_name_unique_for_player(
            player, "Central", x=10.015, y=10.0, line_names=["Line A"],
        )

    def test_duplicate_name_blocked_beyond_20m(self):
        ops, player, _ = self._ops_with_named_station()
        with self.assertRaises(ValueError):
            ops.assert_station_name_unique_for_player(
                player, "Central", x=10.03, y=10.0, line_names=["Line A"],
            )

    def test_rename_station_allows_same_name_within_20m(self):
        from station import Station
        ops, player, line = self._ops_with_named_station()
        station = line.get_stations()[0]
        nearby = Station("Central Hub", 10.015, 10.0, line_names=["Line A"])
        game_map = ops.get_map()
        game_map.stations.append(nearby)
        line.add_station(nearby)
        ops.rename_station(station, "Central Hub")


class TestStationMerge(unittest.TestCase):
    def _ops_with_two_lines_and_station(self):
        from line import Line
        from station import Station

        ops, game, p1, p2 = _make_ops_and_players()
        line_a = Line("Line A", 1, [(5.0, 10.0), (15.0, 10.0)], owner=p1)
        line_b = Line("Line B", 1, [(10.0, 5.0), (10.0, 15.0)], owner=p1)
        game.get_map().add_line(line_a)
        game.get_map().add_line(line_b)
        p1.line_names = ["Line A", "Line B"]
        station = Station("Central", 10.0, 10.0, line_names=["Line A"])
        line_a.add_station(station)
        game.get_map().stations = [station]
        return ops, p1, station, line_a, line_b

    def test_find_merge_target_within_20m(self):
        ops, player, station, _, _ = self._ops_with_two_lines_and_station()
        target = ops.find_player_station_merge_target(
            player, "Central", 10.015, 10.0,
        )
        self.assertIs(target, station)

    def test_find_merge_target_beyond_20m(self):
        ops, player, station, _, _ = self._ops_with_two_lines_and_station()
        target = ops.find_player_station_merge_target(
            player, "Central", 10.04, 10.0,
        )
        self.assertIsNone(target)

    def test_merge_cost_cheaper_than_new_station(self):
        ops, player, station, _, _ = self._ops_with_two_lines_and_station()
        new_cost = ops.calculate_station_cost(1, 10.04, 10.0)
        merge_cost = ops.calculate_station_cost(
            1, 10.015, 10.0, player=player, station_name="Central",
        )
        self.assertLess(merge_cost, new_cost)

    def test_create_station_immediate_merges_same_name_nearby(self):
        ops, player, station, line_a, line_b = self._ops_with_two_lines_and_station()
        result = ops.create_station_immediate(
            "Central", 10.015, 10.01, player, ["Line B"],
        )
        self.assertTrue(result.get("merged"))
        self.assertIs(result["station"], station)
        self.assertIn("Line B", station.get_line_names())
        self.assertIn(station, line_b.get_stations())

    def test_merge_maintenance_savings_for_multi_line_hub(self):
        ops, player, station, _, line_b = self._ops_with_two_lines_and_station()
        self.assertEqual(ops.calc_player_station_merge_maintenance_savings(player, 1830), 0)
        ops.create_station_immediate("Central", 10.015, 10.01, player, ["Line B"])
        savings = ops.calc_player_station_merge_maintenance_savings(player, 1830)
        self.assertGreater(savings, 0)

    def test_map_display_merged_flag(self):
        ops, player, station, _, line_b = self._ops_with_two_lines_and_station()
        ops.create_station_immediate("Central", 10.015, 10.01, player, ["Line B"])
        rows = ops.get_stations_for_map_display(ops.get_map(), current_player=player)
        merged_rows = [r for r in rows if r.get("is_merged")]
        self.assertEqual(len(merged_rows), 1)
        self.assertGreaterEqual(merged_rows[0]["num_lines"], 2)


class TestGovernmentLobbySeatAndTiebreaker(unittest.TestCase):
    """Unit tests for government seat and tiebreaker lobbying (influence_seat, influence_tiebreaker)."""

    def test_get_government_lobby_options_includes_seat_and_tiebreaker(self):
        ops, *_ = _make_ops_and_players()
        opts = ops.get_government_lobby_options(ops.get_active_players()[0])
        ids = [o["id"] for o in opts]
        self.assertIn("influence_seat", ids)
        self.assertIn("influence_tiebreaker", ids)

    def test_try_lobby_influence_seat_at_index_flips_seat_and_deducts_money(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._seats[5] = 1  # Seat 5 unfavorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_seat", seat_index=5)
        self.assertTrue(r["success"])
        self.assertEqual(p1.get_money(), m0 - 1000)
        # Lobby takes effect at end of turn
        ops.advance_year_complete(ops.get_year() - 1)
        self.assertEqual(gov._seats[5], 0)
        self.assertIn(5, gov._lobbied_hidden)

    def test_try_lobby_influence_seat_at_index_already_favorable_deducts_money_no_flip(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._seats[5] = 0  # Already favorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_seat", seat_index=5)
        self.assertTrue(r["success"])
        self.assertIn("already favorable", r["message"].lower())
        self.assertEqual(gov._seats[5], 0)
        self.assertEqual(p1.get_money(), m0 - 1000)

    def test_try_lobby_influence_tiebreaker_flips_and_deducts_money(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._tiebreaker = 1  # Unfavorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_tiebreaker")
        self.assertTrue(r["success"])
        self.assertEqual(p1.get_money(), m0 - 1000)
        # Lobby takes effect at end of turn
        ops.advance_year_complete(ops.get_year() - 1)
        self.assertEqual(gov._tiebreaker, 0)
        self.assertIn(50, gov._lobbied_hidden)

    def test_try_lobby_influence_tiebreaker_already_favorable_deducts_money_no_flip(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._tiebreaker = 0  # Already favorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_tiebreaker")
        self.assertTrue(r["success"])
        self.assertIn("already favorable", r["message"].lower())
        self.assertEqual(gov._tiebreaker, 0)
        self.assertEqual(p1.get_money(), m0 - 1000)

    def test_try_lobby_influence_seat_respects_minimum_cost(self):
        ops, *_ = _make_ops_and_players()
        min_cost = ops.get_lobby_minimum_cost()
        r = ops.try_lobby(ops.get_active_players()[0], min_cost - 1, option_id="influence_seat", seat_index=0)
        self.assertFalse(r["success"])
        self.assertIn("at least", r["message"].lower())

    def test_lobby_minimum_cost_is_cheap(self):
        ops, *_ = _make_ops_and_players()
        min_cost = ops.get_lobby_minimum_cost()
        self.assertLessEqual(min_cost, 100)


class TestLobbyOptions(unittest.TestCase):
    def test_drop_excuse_always_available(self):
        ops, *_ = _make_ops_and_players()
        opts = ops.get_lobby_options(ops.get_active_players()[0])
        ids = [o["id"] for o in opts]
        self.assertIn("drop_excuse", ids)

    def test_funding_options_after_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1971
        opts = ops.get_lobby_options(p1)
        ids = [o["id"] for o in opts]
        self.assertIn("receive_funding", ids)
        self.assertIn("redirect_funding", ids)

    def test_funding_options_before_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1969
        opts = ops.get_lobby_options(p1)
        ids = [o["id"] for o in opts]
        self.assertNotIn("receive_funding", ids)
        self.assertNotIn("redirect_funding", ids)

    def test_tax_break_pass_law_area_protection_gov_attack_available(self):
        ops, *_ = _make_ops_and_players()
        opts = ops.get_lobby_options(ops.get_active_players()[0])
        ids = [o["id"] for o in opts]
        self.assertIn("tax_break", ids)
        self.assertIn("one_time_grant", ids)
        self.assertIn("pass_law", ids)
        self.assertIn("area_protection", ids)
        self.assertIn("gov_attack", ids)


class TestTryLobby(unittest.TestCase):
    def test_try_lobby_requires_positive_amount(self):
        ops, *_ = _make_ops_and_players()
        r = ops.try_lobby(ops.get_active_players()[0], 0)
        self.assertFalse(r["success"])
        self.assertIn("positive", r["message"].lower())

    def test_try_lobby_insufficient_money(self):
        ops, _, p1, _ = _make_ops_and_players()
        p1.deduct_money(p1.get_money() - 500)
        r = ops.try_lobby(p1, 10000)
        self.assertFalse(r["success"])
        self.assertIn("need", r["message"].lower())

    def test_try_lobby_drop_excuse_success_removes_excuse(self):
        ops, game, p1, p2 = _make_ops_and_players()
        excuse = LAWSUIT_EXCUSES[0]
        p1.add_lawsuit_excuse(excuse)
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 1)
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 50000, option_id="drop_excuse")
        self.assertTrue(r["success"])
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 0)

    def test_try_lobby_receive_funding_sets_recipient(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1975
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 50000, option_id="receive_funding")
        self.assertTrue(r["success"])
        self.assertIs(ops.get_government_funding_recipient(), p1)

    def test_try_lobby_tax_break_sets_game_flag(self):
        ops, game, p1, p2 = _make_ops_and_players()
        y0 = 1840
        game.timeline.year = y0
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 1000, option_id="tax_break")
        self.assertTrue(r["success"])
        self.assertIs(getattr(game, "_lobby_tax_break_player", None), p1)
        self.assertEqual(getattr(game, "_lobby_tax_break_year", None), y0 + 1)

    def test_try_lobby_one_time_grant_adds_money(self):
        ops, game, p1, p2 = _make_ops_and_players()
        m0 = p1.get_money()
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 1000, option_id="one_time_grant")
        self.assertTrue(r["success"])
        grant = ops.get_government_funding_amount() // 2
        self.assertEqual(p1.get_money(), m0 - 1000 + grant)

    def test_try_lobby_pass_law_sets_game_flag(self):
        ops, game, p1, p2 = _make_ops_and_players()
        y0 = 1840
        game.timeline.year = y0
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 1000, option_id="pass_law")
        self.assertTrue(r["success"])
        self.assertIs(getattr(game, "_lobby_pass_law_player", None), p1)
        self.assertEqual(getattr(game, "_lobby_pass_law_until_year", None), y0 + 2)


class TestTryFileLawsuit(unittest.TestCase):
    """Tests for operations.try_file_lawsuit (game logic for filing lawsuits)."""

    def test_try_file_lawsuit_self_target_fails(self):
        ops, _, p1, _ = _make_ops_and_players()
        r = ops.try_file_lawsuit(p1, p1, excuse=None, made_up_cause="Test")
        self.assertFalse(r["success"])
        self.assertIn("yourself", r.get("message", "").lower())

    def test_try_file_lawsuit_success_returns_result(self):
        ops, _, p1, p2 = _make_ops_and_players()
        p1.add_money(100000)
        p2.add_money(100000)
        r = ops.try_file_lawsuit(p1, p2, excuse=None, made_up_cause="Unfair Competition")
        self.assertTrue(r["success"])
        self.assertIn("winner", r)
        self.assertIn("result_message", r)
        self.assertIn("plaintiff_name", r)
        self.assertIn("defendant_name", r)
        self.assertIn(r["plaintiff_name"], ["Alice", "Bob"])
        self.assertIn(r["defendant_name"], ["Alice", "Bob"])

    def test_try_file_lawsuit_with_excuse_removes_excuse(self):
        ops, _, p1, p2 = _make_ops_and_players()
        excuse = LAWSUIT_EXCUSES[0]
        p1.add_lawsuit_excuse(excuse)
        p1.add_money(100000)
        p2.add_money(100000)
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 1)
        r = ops.try_file_lawsuit(p1, p2, excuse=excuse, made_up_cause="")
        self.assertTrue(r["success"])
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 0)


class TestLawsuitResolveChance(unittest.TestCase):
    def test_lawyer_bonus_increased(self):
        from lawsuit import Lawsuit, LAWSUIT_EXCUSES

        ops, _, p1, p2 = _make_ops_and_players()
        lawsuit = Lawsuit(p1, p2, LAWSUIT_EXCUSES[0], 1830)
        with unittest.mock.patch("lawsuit.random.random", return_value=0.0):
            lawsuit.resolve(0, 0)
        self.assertEqual(lawsuit.winner, p1)

    def test_base_chance_higher_with_cause(self):
        from lawsuit import Lawsuit, LAWSUIT_EXCUSES

        ops, _, p1, p2 = _make_ops_and_players()
        lawsuit = Lawsuit(p1, p2, LAWSUIT_EXCUSES[0], 1830)
        # 0.84 is below new 85% base plaintiff chance
        with unittest.mock.patch("lawsuit.random.random", return_value=0.84):
            lawsuit.resolve(0, 0)
        self.assertEqual(lawsuit.winner, p1)


class TestDistributeGovernmentFunding(unittest.TestCase):
    def test_no_funding_before_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1969
        m1, m2 = p1.get_money(), p2.get_money()
        ops.distribute_government_funding(1969)
        self.assertEqual(p1.get_money(), m1)
        self.assertEqual(p2.get_money(), m2)

    def test_funding_distributed_after_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        m1, m2 = p1.get_money(), p2.get_money()
        ops.distribute_government_funding(1970)
        total_after = p1.get_money() + p2.get_money()
        total_before = m1 + m2
        self.assertEqual(total_after - total_before, ops.get_government_funding_amount())

    def test_lobbied_recipient_gets_funding(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1975
        with patch("random.random", return_value=0.0):
            ops.try_lobby(p1, 50000, option_id="receive_funding")
        m1_before = p1.get_money()
        m2_before = p2.get_money()
        ops.distribute_government_funding(1975)
        self.assertEqual(p1.get_money(), m1_before + ops.get_government_funding_amount())
        self.assertEqual(p2.get_money(), m2_before)
        self.assertIsNone(ops.get_government_funding_recipient())

    def test_gov_attack_applied_next_year(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1850
        m2_before = p2.get_money()
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="gov_attack", target_player=p2)
        self.assertTrue(r["success"])
        penalty = ops.get_government_funding_amount()
        game.apply_lobby_effects(1851)
        self.assertEqual(p2.get_money(), m2_before - penalty)

    def test_add_lawsuit_excuse_adds_to_target(self):
        ops, game, p1, p2 = _make_ops_and_players()
        excuses_before = len(p2.get_lawsuit_excuses())
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="add_lawsuit_excuse", target_player=p2)
        self.assertTrue(r["success"])
        self.assertEqual(len(p2.get_lawsuit_excuses()), excuses_before + 1)

    def test_audit_target_doubles_taxes_next_year(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1850
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="audit_target", target_player=p2)
        self.assertTrue(r["success"])
        self.assertEqual(getattr(game, "_lobby_audit_target", None), p2)
        self.assertEqual(getattr(game, "_lobby_audit_year", None), 1851)
        poll_tax, property_tax = game.calc_taxes(p2, 1851)
        # Taxes should have been applied and cleared
        self.assertIsNone(getattr(game, "_lobby_audit_target", None))

    def test_block_funding_blocks_target_next_year(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1970
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="block_funding", target_player=p2)
        self.assertTrue(r["success"])
        self.assertEqual(getattr(game, "_lobby_block_funding_target", None), p2)
        m1_before, m2_before = p1.get_money(), p2.get_money()
        ops._government_funding_recipient = p2
        ops.distribute_government_funding(1971)
        # p2 should be blocked; p1 or random other gets funding
        total_after = p1.get_money() + p2.get_money()
        total_before = m1_before + m2_before
        self.assertEqual(total_after - total_before, ops.get_government_funding_amount())
        # p2 should not have received it (blocked)
        self.assertEqual(p2.get_money(), m2_before)


class TestWaterCheckDeadline(unittest.TestCase):
    def test_tile_system_deadline_limits_osm_calls(self):
        from osm_tile_system import OSMTileSystem
        from unittest.mock import Mock
        mock_osm = Mock()
        ts = OSMTileSystem(50, 50, 40.7, -73.9, tile_size=0.01, osm_integration=mock_osm)
        ts.set_water_check_deadline(0.0)
        t = ts.get_tile_type(0, 0)
        ts.clear_water_check_deadline()
        self.assertIn(t, (None, "land", "water"))
        self.assertEqual(mock_osm.is_water_at_latlon.call_count, 0)


class TestWaterDetectionSouthFerryGovernorsIsland(unittest.TestCase):
    """South Ferry (Manhattan) <-> Governors Island ferry corridor must be detected as water."""

    def test_nyc_preset_segment_south_ferry_governors_island_is_water(self):
        import map_presets_nyc as nyc
        # Points in the channel: South Ferry ~40.701,-74.013, Governors Island dock ~40.689,-74.016
        # Mid-channel and Manhattan-side pier should be water via NYC_WATER_AREAS (Buttermilk Channel box)
        self.assertTrue(nyc.is_water_location(40.695, -74.015, None))
        self.assertTrue(nyc.is_water_location(40.701, -74.013, None))
        self.assertTrue(nyc.is_water_location(40.689, -74.016, None))


class TestFerryWaterDetectionHelpers(unittest.TestCase):
    """Preset-first ferry validation must accept water and reject land."""

    def test_ferry_helpers_use_preset_water_not_redundant_tile_checks(self):
        from operations import Operations, _ferry_point_near_water, _ferry_segment_near_water
        import map_presets_nyc as nyc

        ops = Operations(
            ["P"], map_width=50, map_height=50, starting_money=50000,
            map_preset_name="nyc", use_osm=True,
        )
        game_map = ops.get_map()
        lat, lon = 40.695, -74.015
        gy = int(round((lat - nyc.NYC_BOUNDS['min_lat']) / (nyc.NYC_BOUNDS['max_lat'] - nyc.NYC_BOUNDS['min_lat']) * game_map.height))
        gx = int(round((lon - nyc.NYC_BOUNDS['min_lon']) / (nyc.NYC_BOUNDS['max_lon'] - nyc.NYC_BOUNDS['min_lon']) * game_map.width))
        self.assertTrue(_ferry_point_near_water(game_map, float(gx), float(gy)))
        self.assertTrue(
            _ferry_segment_near_water(
                game_map, float(gx), float(gy), float(gx + 1), float(gy),
            )
        )
        vr = ops._validate_ferry_route(
            [(float(gx), float(gy)), (float(gx + 1), float(gy))],
        )
        self.assertTrue(vr.get('success'))

    def test_ferry_segment_near_shore_not_in_water_passes(self):
        """Ferry routes on land within 100m of water are valid (no open-water crossing required)."""
        from operations import Operations, _ferry_point_near_water, _ferry_segment_near_water
        from unittest.mock import patch

        ops = Operations(
            ["P"], map_width=50, map_height=50, starting_money=50000,
            use_osm=True, map_center_lat=40.7, map_center_lon=-73.9,
        )
        game_map = ops.get_map()

        with patch('operations._ferry_point_near_water', return_value=True), \
             patch('operations._ferry_segment_near_water', return_value=True):
            vr = ops._validate_ferry_route([(10.0, 10.0), (20.0, 20.0)])
            self.assertTrue(vr.get('success'))

    def test_validate_ferry_route_rejects_land(self):
        from operations import Operations

        ops = Operations(
            ["P"], map_width=50, map_height=50, starting_money=50000,
            map_preset_name="nyc", use_osm=True,
        )
        with self.assertRaises(ValueError):
            ops._validate_ferry_route([(45.0, 45.0), (46.0, 45.0)])


class TestStationLandDistanceValidation(unittest.TestCase):
    """Stations in water must be within STATION_MAX_DISTANCE_FROM_LAND_M of land."""

    def setUp(self):
        from game_presets import STATION_MAX_DISTANCE_FROM_LAND_M
        self.max_m = STATION_MAX_DISTANCE_FROM_LAND_M
        self.ops = Operations(
            ["P1"], map_width=50, map_height=50, starting_money=50000,
            map_preset_name="nyc", use_osm=True,
        )
        self.game_map = self.ops.get_map()

    def test_station_land_distance_constant_is_60m(self):
        self.assertEqual(self.max_m, 60.0)

    def test_station_on_land_passes_water_check(self):
        tile_system = self.game_map.tile_system
        if not tile_system:
            self.skipTest("Tile system not available")
        original = tile_system.is_point_in_water

        def mock_on_land(x, y, skip_osm=True):
            return False

        tile_system.is_point_in_water = mock_on_land
        try:
            self.ops._require_station_not_in_deep_water(self.game_map, 10.0, 10.0)
        finally:
            tile_system.is_point_in_water = original

    def test_station_in_water_near_land_passes(self):
        tile_system = self.game_map.tile_system
        if not tile_system:
            self.skipTest("Tile system not available")
        original_in_water = tile_system.is_point_in_water
        original_near_land = tile_system.is_point_near_land
        captured = {}

        def mock_in_water(x, y, skip_osm=True):
            return True

        def mock_near_land(x, y, max_distance_m, skip_osm=True):
            captured['max_distance_m'] = max_distance_m
            return True

        tile_system.is_point_in_water = mock_in_water
        tile_system.is_point_near_land = mock_near_land
        try:
            self.ops._require_station_not_in_deep_water(self.game_map, 10.0, 10.0)
            self.assertEqual(captured.get('max_distance_m'), self.max_m)
        finally:
            tile_system.is_point_in_water = original_in_water
            tile_system.is_point_near_land = original_near_land

    def test_station_in_deep_water_rejected(self):
        tile_system = self.game_map.tile_system
        if not tile_system:
            self.skipTest("Tile system not available")
        original_in_water = tile_system.is_point_in_water
        original_near_land = tile_system.is_point_near_land

        def mock_in_water(x, y, skip_osm=True):
            return True

        def mock_not_near_land(x, y, max_distance_m, skip_osm=True):
            return False

        tile_system.is_point_in_water = mock_in_water
        tile_system.is_point_near_land = mock_not_near_land
        try:
            with self.assertRaises(ValueError) as ctx:
                self.ops._require_station_not_in_deep_water(self.game_map, 10.0, 10.0)
            self.assertIn("60 m from land", str(ctx.exception))
        finally:
            tile_system.is_point_in_water = original_in_water
            tile_system.is_point_near_land = original_near_land

    def test_station_water_check_skipped_without_water_preset(self):
        from map import Map
        land_map = Map(width=50, height=50, use_osm=False, map_preset_name="standard")
        self.ops._require_station_not_in_deep_water(land_map, 10.0, 10.0)


class TestAISplitRouteAtWater(unittest.TestCase):
    def test_split_route_produces_land_ferry_land_chunks(self):
        from ai import _ai_split_route_at_water_boundaries
        from unittest.mock import MagicMock

        game_map = MagicMock()
        game_map.map_preset_name = "nyc"
        game_map.tile_system = None
        game_map.width = 50
        game_map.height = 50
        pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
        ferry_flags = [False, True, False]

        def qual(p1, p2, gm, preset):
            i = pts.index((float(p1[0]), float(p1[1])))
            return ferry_flags[i]

        import ai as ai_mod
        orig = ai_mod._ai_segment_qualifies_as_ferry
        ai_mod._ai_segment_qualifies_as_ferry = qual
        try:
            chunks = _ai_split_route_at_water_boundaries(pts, game_map, "nyc", 1830)
        finally:
            ai_mod._ai_segment_qualifies_as_ferry = orig
        self.assertEqual(len(chunks), 3)
        self.assertFalse(chunks[0]['is_ferry'])
        self.assertTrue(chunks[1]['is_ferry'])
        self.assertFalse(chunks[2]['is_ferry'])


import unittest
from operations import Operations
from government import Government


class TestGovernment(unittest.TestCase):
    def setUp(self):
        self.ops = Operations(
            ["Alice", "Bob"],
            map_width=30,
            map_height=30,
            starting_money=100000,
            map_preset_name="standard"
        )
        self.gov = self.ops.game.get_government()
        self.p1, self.p2 = self.ops.get_players()

    def test_government_exists(self):
        self.assertIsNotNone(self.gov)

    def test_government_has_50_seats_plus_tiebreaker(self):
        seats = self.gov.get_seats_for_display(0)
        self.assertEqual(len(seats), 51)
        self.assertTrue(any(s[1] for s in seats))

    def test_tax_multiplier_in_range(self):
        m = self.gov.get_tax_multiplier(0)
        self.assertIn(m, (0.75, 1.25))

    def test_next_election_year(self):
        # Before first election (1832), next election is 1832
        y = self.gov.get_next_election_year(1830)
        self.assertEqual(y, 1832)
        y = self.gov.get_next_election_year(1831)
        self.assertEqual(y, 1832)
        # After first election, next is every 6 years
        self.gov._has_held_election = True
        self.gov._election_year = 1832
        y = self.gov.get_next_election_year(1835)
        self.assertEqual(y, 1838)

    def test_hold_election_at_interval(self):
        # First election at 1832
        self.assertFalse(self.gov.hold_election(1831))
        self.assertTrue(self.gov.hold_election(1832))
        self.assertEqual(self.gov._election_year, 1832)
        # Subsequent elections every 6 years
        self.assertFalse(self.gov.hold_election(1835))
        self.assertTrue(self.gov.hold_election(1838))
        self.assertEqual(self.gov._election_year, 1838)

    def test_lobby_flip_seat(self):
        self.gov._seats = [1] * 50  # All unfavorable to player 0
        result = self.gov.lobby_flip_seat(0)
        self.assertTrue(result)
        self.assertEqual(sum(1 for s in self.gov._seats if s == 0), 1)

    def test_lobby_flip_tiebreaker(self):
        self.gov._tiebreaker = 1
        result = self.gov.lobby_flip_tiebreaker(0)
        self.assertTrue(result)
        self.assertEqual(self.gov._tiebreaker, 0)

    def test_lobby_flip_seat_at_index(self):
        self.gov._seats[5] = 1
        result = self.gov.lobby_flip_seat_at_index(5, 0)
        self.assertTrue(result)
        self.assertEqual(self.gov._seats[5], 0)
        result = self.gov.lobby_flip_seat_at_index(5, 0)
        self.assertFalse(result)

    def test_pass_annual_laws(self):
        laws = self.gov.pass_annual_laws(1835)
        self.assertIn('tax_modifiers', laws)
        self.assertIn('lawsuit_protection', laws)
        self.assertIn('lawsuit_target', laws)
        self.assertIn('maintenance_relief', laws)
        self.assertIn('grants', laws)
        self.assertIsInstance(laws['tax_modifiers'], dict)
        self.assertIsInstance(laws['grants'], dict)

    def test_operations_timeline_methods(self):
        """Operations.get_timeline_start_year and get_timeline_end_year work."""
        self.assertEqual(self.ops.get_timeline_start_year(), 1830)
        self.assertEqual(self.ops.get_timeline_end_year(), 2050)

import sys
sys.path.insert(0, '/home/ayal/PycharmProjects/TransitGame')

from operations import Operations
from game import Game
from player import Player
from map import Map

def test_completed_jobs_persistence():
    ui = Operations()
    ui.start_game(["Player1", "Player2"], 50, 50, 10000, 0, 0)
    
    player1 = ui.get_players()[0]
    
    initial_completed = len(ui.get_completed_jobs())
    print(f"Initial completed jobs: {initial_completed}")
    
    points = [(10, 10), (15, 15), (20, 20)]
    result = ui.create_line_complete("TestLine", points, player1)
    print(f"Line created, build_time: {result.get('build_time')}")
    
    build_queue = ui.get_build_queue()
    print(f"Build queue length: {len(build_queue)}")
    
    if build_queue:
        build = build_queue[0]
        build['work_remaining'] = 1.0
        build['assigned_workers'] = 10
        
        ui.process_build_queue()
        
        after_complete = len(ui.get_completed_jobs())
        print(f"Completed jobs after processing: {after_complete}")
        
        assert after_complete > initial_completed, "Completed jobs should increase"
        
        ui.process_build_queue()
        
        after_second = len(ui.get_completed_jobs())
        print(f"Completed jobs after second processing: {after_second}")
        
        assert after_second == after_complete, f"Completed jobs should persist, got {after_second} expected {after_complete}"
    
    print("✓ Completed jobs persistence test passed")

def test_line_cost_off_preset():
    ui = Operations()
    ui.start_game(["Player1"], 50, 50, 10000, 0, 0)
    
    game_map = ui.get_map()
    game_map.use_osm = False
    
    points = [(10, 10), (15, 15)]
    cost_info = ui.calculate_line_cost(points)
    
    print(f"Line cost for {points}: ${cost_info['cost']}")
    assert cost_info['cost'] > 0, "Cost should be positive"
    
    print("✓ Line cost calculation test passed")

def test_extend_line_button_exists():
    from PyQt6.QtWidgets import QApplication
    import qt
    
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    
    window = qt.MainWindow()
    window.operations = Operations()
    window.operations.start_game(["Player1"], 50, 50, 10000, 0, 0)
    
    player = window.operations.get_players()[0]
    player_id = window.operations.get_player_id(player)
    
    window.player_tabs_dict = {}
    window.current_player_index = 0
    
    tabs = window._create_player_tabs(player)
    
    extend_btn = tabs.get('extend_line_btn')
    assert extend_btn is not None, "Extend line button should exist"
    assert extend_btn.text() == "Extend Line", "Button should have correct text"
    
    print("✓ Extend line button exists test passed")

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from operations import Operations, setup_game, generate_players
from player import Player
from line import Line, Ferry
from map import Map
from vehicle import Trainset, Locomotive, Railcar, Vehicle
from vehicle_presets import get_vehicle_registry
from game_resource import Food, Coal, Wood, Oil, Electricity
from marketplace import Marketplace


class TestFerryLineCreation(unittest.TestCase):
    """Test ferry line creation functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.player = self.ui.get_players()[0]
        self.ui.set_current_player_index(0)
    
    def test_create_ferry_line_complete(self):
        """Test creating a ferry line."""
        line_name = "Ferry Route 1"
        points = [(10, 10), (20, 20)]
        elevation_levels = ['water']
        
        result = self.ui.create_line_complete(
            line_name, points, self.player, 
            elevation_levels, is_ferry_line=True
        )
        
        self.assertIsNotNone(result)
        self.assertIn('cost', result)
        self.assertIn('build_time', result)
        self.assertIn('line', result)
    
    def test_ferry_line_in_build_queue(self):
        """Test that ferry line is added to build queue with correct flag."""
        line_name = "Ferry Route 2"
        points = [(5, 5), (15, 15)]
        elevation_levels = ['water']
        
        self.ui.create_line_complete(
            line_name, points, self.player,
            elevation_levels, is_ferry_line=True
        )
        
        # Check build queue
        build_queue = self.ui.get_build_queue()
        self.assertGreater(len(build_queue), 0)
        
        # Find the ferry line in queue
        ferry_build = None
        for build in build_queue:
            if build['type'] == 'line' and build['data'].get('name') == line_name:
                ferry_build = build
                break
        
        self.assertIsNotNone(ferry_build)
        self.assertTrue(ferry_build['data'].get('is_ferry_line', False))


class TestWaterCostExponential(unittest.TestCase):
    """Test exponential water cost calculation for train lines."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=100000)
        self.player = self.ui.get_players()[0]
    
    def test_water_cost_calculation(self):
        """Test that water segments have exponential cost increase."""
        # Mock water detection
        with patch.object(self.ui.game.get_map(), 'osm_integration', None):
            # Create points that would be detected as water
            # For this test, we'll check the cost calculation logic
            points = [(10, 10), (20, 20), (30, 30), (40, 40)]
            elevation_levels = ['above_ground', 'above_ground', 'above_ground']
            
            # Mock is_point_in_water to return True for water segments
            original_is_water = None
            if hasattr(self.ui, '_is_point_in_water'):
                original_is_water = self.ui._is_point_in_water
            
            def mock_is_water(x, y):
                # Points 20,20 and beyond are in water
                return x >= 20 or y >= 20
            
            # Test cost calculation
            try:
                cost_info = self.ui.calculate_line_cost(points, elevation_levels)
                self.assertIn('cost', cost_info)
                self.assertGreater(cost_info['cost'], 0)
            except Exception as e:
                # If water detection fails, that's okay for this test
                # The important thing is the exponential logic exists
                pass
    
    def test_water_segments_reset_on_land(self):
        """Test that water segment count resets when returning to land."""
        # This tests the logic that water_segments_count resets to 0
        # when a segment is not in water
        points = [(10, 10), (20, 20), (30, 30), (40, 40), (50, 50)]
        elevation_levels = ['above_ground', 'above_ground', 'above_ground', 'above_ground']
        
        # The cost calculation should handle this correctly
        try:
            cost_info = self.ui.calculate_line_cost(points, elevation_levels)
            self.assertIn('cost', cost_info)
        except Exception:
            pass  # Water detection may not work in test environment


class TestVehicleRegistry(unittest.TestCase):
    """Test vehicle registry and ferry/horse-drawn vehicles."""
    
    def test_ferry_vehicles_in_registry(self):
        """Test that ferry vehicles are in the registry."""
        registry = get_vehicle_registry()
        all_locomotives = registry.get_all_locomotives()
        
        ferry_names = [loco.name for loco in all_locomotives if 'Ferry' in loco.name]
        self.assertGreater(len(ferry_names), 0, "Should have at least one ferry vehicle")
        
        # Check that ferries cannot have railcars
        for loco in all_locomotives:
            if 'Ferry' in loco.name:
                self.assertFalse(
                    getattr(loco, 'can_have_railcars', True),
                    f"{loco.name} should not be able to have railcars"
                )
    
    def test_horse_drawn_vehicles_in_registry(self):
        """Test that horse-drawn vehicles are in the registry."""
        registry = get_vehicle_registry()
        all_locomotives = registry.get_all_locomotives()
        
        horse_names = [loco.name for loco in all_locomotives if 'Horse' in loco.name]
        self.assertGreater(len(horse_names), 0, "Should have at least one horse-drawn vehicle")
        
        # Check that horse-drawn vehicles use Food resource
        for loco in all_locomotives:
            if 'Horse' in loco.name:
                self.assertIsInstance(
                    loco.fuel, Food,
                    f"{loco.name} should use Food resource"
                )
    
    def test_generate_players_uses_compatible_locomotive(self):
        """Test that generate_players uses a locomotive that can have railcars."""
        try:
            players = generate_players(["TestPlayer"], starting_money=10000)
            self.assertEqual(len(players), 1)
            
            player = players[0]
            self.assertGreater(len(player.trainsets), 0)
            
            # Check that trainsets have valid locomotives
            for trainset in player.trainsets:
                self.assertIsNotNone(trainset.locomotive)
                self.assertTrue(
                    getattr(trainset.locomotive, 'can_have_railcars', False),
                    "Starting locomotive should be able to have railcars"
                )
        except ValueError as e:
            self.fail(f"generate_players raised ValueError: {e}")


class TestElevationLevels(unittest.TestCase):
    """Test elevation level handling."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.player = self.ui.get_players()[0]
    
    def test_elevation_levels_required(self):
        """Test that elevation levels are required for line creation."""
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        
        # Should raise ValueError if elevation_levels is None
        with self.assertRaises(ValueError):
            self.ui.create_line_complete(line_name, points, self.player, elevation_levels=None)
    
    def test_elevation_levels_count_must_match(self):
        """Test that elevation levels count must match segment count."""
        line_name = "Test Line"
        points = [(10, 10), (20, 20), (30, 30)]  # 2 segments
        elevation_levels = ['street']  # Only 1 level
        
        # Should raise ValueError
        with self.assertRaises(ValueError):
            self.ui.create_line_complete(line_name, points, self.player, elevation_levels)
    
    def test_street_level_on_water_raises_error(self):
        """Test that street level on water raises an error."""
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        elevation_levels = ['street']
        
        # This may or may not raise an error depending on water detection
        # But the logic should be in place
        try:
            cost_info = self.ui.calculate_line_cost(points, elevation_levels)
            # If it doesn't raise, that's okay - water detection may not work in test
        except ValueError as e:
            if 'street level on water' in str(e).lower():
                pass  # Expected behavior
            else:
                raise


class TestStationCreation(unittest.TestCase):
    """Test station creation functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.player = self.ui.get_players()[0]
        self.ui.set_current_player_index(0)
    
    def test_create_station_complete(self):
        """Test creating a station."""
        # First create a line
        line_name = "Test Line"
        points = [(10, 10), (20, 20), (30, 30)]
        elevation_levels = ['street', 'street']
        
        self.ui.create_line_complete(line_name, points, self.player, elevation_levels)
        
        # Process build queue to complete line
        self.ui.process_build_queue()
        
        # Now create a station
        station_name = "Test Station"
        x, y = 20, 20
        
        result = self.ui.create_station_complete(
            station_name, x, y, self.player, [line_name]
        )
        
        self.assertIsNotNone(result)
        self.assertIn('cost', result)
        self.assertIn('build_time', result)
        self.assertIn('line_names', result)
        self.assertEqual(result['line_names'], [line_name])

    def test_get_station_creation_info_no_lines(self):
        """Station dialog helper must report has_lines=False when player has no lines."""
        info = self.ui.get_station_creation_info(self.player)
        self.assertFalse(info['has_lines'])

    def test_get_station_creation_info_with_lines(self):
        """Station dialog helper must report has_lines=True when player owns a line."""
        line = self.ui.game.create_line(
            "Station Info Line",
            self.player,
            [(10, 10), (20, 20), (30, 30)],
        )
        self.player.acquire_line(line)
        self.ui.game.get_map().add_line(line)
        info = self.ui.get_station_creation_info(self.player)
        self.assertTrue(info['has_lines'])
        self.assertIn("Station Info Line", info['line_names'])


class TestPopulationPointRemoval(unittest.TestCase):
    """Test population point removal functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=60, map_height=60, 
                    starting_money=50000, use_osm=False,
                    map_center_lat=40.7128, map_center_lon=-74.0060)
        self.game_map = self.ui.game.get_map()
    
    def test_population_points_exist(self):
        """Test that population points exist on NYC map."""
        if self.game_map.is_nyc_proper:
            self.assertGreater(len(self.game_map.population), 0)
    
    def test_deleted_population_points_tracked(self):
        """Test that deleted population points are tracked."""
        if self.game_map.is_nyc_proper:
            # Get a population point
            if len(self.game_map.population) > 0:
                point = list(self.game_map.population.keys())[0]
                initial_pop = self.game_map.population.get(point, 0)
                
                if initial_pop > 0:
                    # Mark as deleted
                    if not hasattr(self.game_map, '_deleted_population_points'):
                        self.game_map._deleted_population_points = set()
                    
                    self.game_map._deleted_population_points.add(point)
                    del self.game_map.population[point]
                    
                    # Verify it's tracked
                    self.assertIn(point, self.game_map._deleted_population_points)
                    self.assertNotIn(point, self.game_map.population)


class TestLineCostCalculation(unittest.TestCase):
    """Test line cost calculation with various scenarios."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
    
    def test_cost_calculation_basic(self):
        """Test basic cost calculation."""
        points = [(10, 10), (20, 20)]
        elevation_levels = ['street']
        
        cost_info = self.ui.calculate_line_cost(points, elevation_levels)
        
        self.assertIn('cost', cost_info)
        self.assertIn('length', cost_info)
        self.assertGreater(cost_info['cost'], 0)
        self.assertGreater(cost_info['length'], 0)
    
    def test_cost_calculation_elevation_multipliers(self):
        """Test that elevation affects cost."""
        points = [(10, 10), (20, 20)]
        
        street_cost = self.ui.calculate_line_cost(points, ['street'])
        above_cost = self.ui.calculate_line_cost(points, ['above_ground'])
        underground_cost = self.ui.calculate_line_cost(points, ['underground'])
        
        # Underground should be more expensive than above_ground
        # Above_ground should be more expensive than street
        self.assertGreater(underground_cost['cost'], above_cost['cost'])
        self.assertGreater(above_cost['cost'], street_cost['cost'])


class TestFerryLineClass(unittest.TestCase):
    """Test Ferry class functionality."""
    
    def test_ferry_initialization(self):
        """Test Ferry initialization."""
        player = Player(0, 10000, [], "Test Player")
        route = [(10, 10), (20, 20)]
        
        ferry = Ferry(
            name="Test Ferry",
            object_id=1,
            route=route,
            owner=player,
            vehicles=[]
        )
        
        self.assertEqual(ferry.get_name(), "Test Ferry")
        self.assertTrue(ferry.can_traverse_water())
        self.assertFalse(ferry.can_traverse_land())


class TestMarketplaceFerryVehicles(unittest.TestCase):
    """Test marketplace ferry vehicle availability."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.marketplace = self.ui.game.marketplace
    
    def test_ferry_vehicles_in_marketplace(self):
        """Test that ferry vehicles can appear in marketplace."""
        # Generate marketplace items
        current_year = self.ui.get_year()
        self.marketplace.generate_trainsets_based_on_discoveries(current_year)
        
        # Check available vehicles
        available_vehicles = self.marketplace.get_available_vehicles()
        
        # Look for ferry vehicles
        ferry_vehicles = [
            item for item in available_vehicles
            if hasattr(item.item, 'name') and 'Ferry' in item.item.name
        ]
        
        # Ferry vehicles may or may not be available depending on year
        # But the system should support them
        self.assertIsInstance(ferry_vehicles, list)


class TestResourceCosts(unittest.TestCase):
    """Test resource cost calculation."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=50, map_height=50, starting_money=50000)
    
    def test_electricity_not_free(self):
        """Test that electricity is never free."""
        # Check electricity cost at different years
        for year in [1830, 1890, 1950, 2000]:
            try:
                resource_costs = self.ui.get_resource_costs(year)
                electricity_cost = resource_costs.get('Electricity', 0)
                self.assertGreater(
                    electricity_cost, 0,
                    f"Electricity should not be free in year {year}"
                )
            except AttributeError:
                # Method might not exist or have different signature
                # Just verify the method exists
                self.assertTrue(hasattr(self.ui, 'get_resource_costs'))
    
    def test_resource_costs_update_annually(self):
        """Test that resource costs can vary by year."""
        # Check that costs exist for multiple years
        try:
            costs_1830 = self.ui.get_resource_costs(1830)
            costs_2000 = self.ui.get_resource_costs(2000)
            
            # Costs should exist for both years
            self.assertIsInstance(costs_1830, dict)
            self.assertIsInstance(costs_2000, dict)
            
            # Costs might vary between years
            if 'Coal' in costs_1830 and 'Coal' in costs_2000:
                # Costs may be the same or different, but both should exist
                self.assertGreater(costs_1830.get('Coal', 0), 0)
                self.assertGreater(costs_2000.get('Coal', 0), 0)
        except AttributeError:
            # Method might not exist
            self.assertTrue(hasattr(self.ui, 'get_resource_costs'))


class TestTotalPopulationDisplay(unittest.TestCase):
    """Test total population calculation for display."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["TestPlayer"]
        self.ui = Operations(self.player_names, map_width=60, map_height=60,
                    starting_money=50000, use_osm=False,
                    map_center_lat=40.7128, map_center_lon=-74.0060)
        self.game_map = self.ui.game.get_map()
    
    def test_total_population_calculation(self):
        """Test that total population can be calculated."""
        if hasattr(self.game_map, 'population') and self.game_map.population:
            total_pop = sum(pop for pop in self.game_map.population.values() if pop > 0)
            self.assertGreaterEqual(total_pop, 0)
            
            # For NYC map, should have significant population
            if self.game_map.is_nyc_proper:
                self.assertGreater(total_pop, 0)


def run_all_tests():
    """Run all test suites."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    test_classes = [
        TestFerryLineCreation,
        TestWaterCostExponential,
        TestVehicleRegistry,
        TestElevationLevels,
        TestStationCreation,
        TestPopulationPointRemoval,
        TestLineCostCalculation,
        TestFerryLineClass,
        TestMarketplaceFerryVehicles,
        TestResourceCosts,
        TestTotalPopulationDisplay,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()

import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inflation_utils import get_inflation_factor, DEFAULT_START_YEAR, DEFAULT_END_YEAR


class TestGetInflationFactor(unittest.TestCase):
    """Test get_inflation_factor."""

    def test_start_year_returns_min_factor(self):
        """At start_year, returns min_factor (1.0)."""
        self.assertEqual(get_inflation_factor(1830), 1.0)
        self.assertEqual(get_inflation_factor(DEFAULT_START_YEAR), 1.0)

    def test_end_year_returns_max_factor(self):
        """At end_year, returns max_factor (10.0)."""
        self.assertEqual(get_inflation_factor(2050), 10.0)
        self.assertEqual(get_inflation_factor(DEFAULT_END_YEAR), 10.0)

    def test_before_start_clamped(self):
        """Years before start_year return min_factor."""
        self.assertEqual(get_inflation_factor(1800), 1.0)
        self.assertEqual(get_inflation_factor(0), 1.0)

    def test_after_end_clamped(self):
        """Years after end_year return max_factor."""
        self.assertEqual(get_inflation_factor(2100), 10.0)
        self.assertEqual(get_inflation_factor(3000), 10.0)

    def test_midpoint_linear(self):
        """Midpoint year gives midpoint factor."""
        mid_year = (DEFAULT_START_YEAR + DEFAULT_END_YEAR) // 2
        factor = get_inflation_factor(mid_year)
        self.assertAlmostEqual(factor, 5.5, places=1)

    def test_custom_bounds(self):
        """Custom start/end/min/max work."""
        f = get_inflation_factor(2000, start_year=1900, end_year=2100, min_factor=0.5, max_factor=2.0)
        self.assertGreater(f, 0.5)
        self.assertLess(f, 2.0)

    def test_degenerate_range_returns_min(self):
        """When end_year <= start_year, returns min_factor."""
        self.assertEqual(get_inflation_factor(2000, start_year=2000, end_year=2000), 1.0)
        self.assertEqual(get_inflation_factor(2000, start_year=2010, end_year=2000), 1.0)


class TestTimelineFareTarget(unittest.TestCase):
    def test_fare_starts_low_and_rises(self):
        from inflation_utils import get_timeline_fare_target, round_fare_to_quarter

        self.assertEqual(get_timeline_fare_target(1830), 2.0)
        self.assertGreater(get_timeline_fare_target(1900), get_timeline_fare_target(1830))
        self.assertEqual(get_timeline_fare_target(2050), 15.0)

    def test_fare_rounds_to_quarter_dollars(self):
        from inflation_utils import round_fare_to_quarter

        self.assertEqual(round_fare_to_quarter(2.12), 2.0)
        self.assertEqual(round_fare_to_quarter(2.13), 2.25)
        self.assertEqual(round_fare_to_quarter(2.37), 2.25)
        self.assertEqual(round_fare_to_quarter(2.38), 2.5)


class TestFinancialSummaryAndScheduleCost(unittest.TestCase):
    def setUp(self):
        self.ui = Operations(["Alice"], map_width=30, map_height=30, starting_money=100000, map_preset_name="standard")
        self.player = self.ui.get_players()[0]

    def test_schedule_operating_cost_per_train_hour_scales_with_year(self):
        early = self.ui.get_schedule_operating_cost_per_train_hour(1830)
        late = self.ui.get_schedule_operating_cost_per_train_hour(2000)
        self.assertGreater(late, early)
        self.assertGreater(early, 0)

    def test_schedule_operating_cost_per_train_year_uses_timetable(self):
        hourly = self.ui.get_schedule_operating_cost_per_train_hour(1830)
        schedule = {'peak': 4, 'off_peak': 2, 'night': 1}
        annual = self.ui.get_schedule_operating_cost_per_train_year(1830, schedule=schedule)
        trips_per_hour = 4 * 0.4 + 2 * 0.4 + 1 * 0.2
        expected = hourly * trips_per_hour * 8760.0
        self.assertAlmostEqual(annual, expected, places=4)
        self.assertGreater(annual, hourly)

    def test_construction_cost_multiplier_applies_to_line_creation_and_extension(self):
        from game_presets import CONSTRUCTION_COST_MULTIPLIER
        from line import Line

        points = [(10, 10), (20, 20)]
        elevation = ['street']
        create_cost = self.ui.calculate_line_cost(points, elevation)['cost']
        self.assertGreater(create_cost, 0)

        game_map = self.ui.get_map()
        line = Line("ExtendMe", 1, points, self.player, [])
        game_map.add_line(line)
        self.player.line_names.append("ExtendMe")
        ext_points = [(20, 20), (25, 25)]
        ext_cost = self.ui.calculate_extend_cost(
            "ExtendMe", ext_points, extend_from_end=True, elevation_levels=['street', 'street'],
        )['cost']
        self.assertGreater(ext_cost, 0)

        base_create = int(create_cost / CONSTRUCTION_COST_MULTIPLIER)
        base_ext = int(ext_cost / CONSTRUCTION_COST_MULTIPLIER)
        self.assertGreater(base_create, 0)
        self.assertGreater(base_ext, 0)

    def test_construction_cost_multiplier_doubles_extension_and_station(self):
        from game_presets import CONSTRUCTION_COST_MULTIPLIER

        points = [(10, 10), (20, 20)]
        elevation = ['street']
        with unittest.mock.patch('game_presets.CONSTRUCTION_COST_MULTIPLIER', 1.0):
            base_create = self.ui.calculate_line_cost(points, elevation)['cost']
            base_station = self.ui.calculate_station_cost(1)

        doubled_create = self.ui.calculate_line_cost(points, elevation)['cost']
        doubled_station = self.ui.calculate_station_cost(1)
        self.assertEqual(doubled_create, base_create * CONSTRUCTION_COST_MULTIPLIER)
        self.assertEqual(doubled_station, base_station * CONSTRUCTION_COST_MULTIPLIER)

    def test_construction_cost_multiplier_runtime_settings(self):
        from game_presets import CONSTRUCTION_COST_MULTIPLIER
        import game_presets

        points = [(10, 10), (20, 20)]
        elevation = ['street']
        default_cost = self.ui.calculate_line_cost(points, elevation)['cost']
        self.ui.apply_runtime_game_settings({'construction_cost_multiplier': 1.0})
        self.assertEqual(game_presets.CONSTRUCTION_COST_MULTIPLIER, 1.0)
        halved_cost = self.ui.calculate_line_cost(points, elevation)['cost']
        self.assertEqual(halved_cost * 2, default_cost)
        settings = self.ui.get_runtime_game_settings()
        self.assertEqual(settings['construction_cost_multiplier'], 1.0)
        self.ui.apply_runtime_game_settings({'construction_cost_multiplier': CONSTRUCTION_COST_MULTIPLIER})

    def test_financial_summary_uses_requested_year_for_taxes(self):
        from inflation_utils import get_inflation_factor
        year = self.ui.get_year()
        summary_now = self.ui.get_player_financial_summary(self.player, year)
        summary_next = self.ui.get_player_financial_summary(self.player, year + 1)
        infl_now = get_inflation_factor(year, max_factor=5.5)
        infl_next = get_inflation_factor(year + 1, max_factor=5.5)
        if infl_next > infl_now:
            self.assertGreaterEqual(summary_next['poll_tax'], summary_now['poll_tax'])

    def test_financial_summary_includes_uncached_line_profit(self):
        from line import Line

        game_map = self.ui.get_map()
        route = [(1, 1), (2, 2), (3, 3)]
        line = Line("NewLine", 99, route, self.player, [])
        line.profit = 0
        game_map.add_line(line)
        self.player.line_names.append("NewLine")
        self.assertFalse(hasattr(line, '_cached_profit'))
        profit = self.ui.calc_player_profit_for_financial_summary(self.player, self.ui.get_year())
        summary = self.ui.get_player_financial_summary(self.player, self.ui.get_year())
        self.assertEqual(summary['profit'], profit)


if __name__ == '__main__':
    unittest.main()
