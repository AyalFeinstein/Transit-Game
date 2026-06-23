"""Tests for ferry validation and duplicate line name checking"""
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


if __name__ == '__main__':
    unittest.main()
