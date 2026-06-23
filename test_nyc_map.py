"""Unit tests for NYC map functionality"""

import unittest
import time
from map import Map
from map_presets_nyc import NYC_BOUNDS, is_water_location
from line import Line
from player import Player
from ui import setup_game


class TestNYCMap(unittest.TestCase):
    """Test NYC map initialization and functionality"""
    
    def test_nyc_map_initialization(self):
        """Test that NYC map initializes without freezing"""
        # This should complete quickly without OSM calls during init
        start_time = time.time()
        map_obj = Map(
            width=NYC_BOUNDS['width'],
            height=NYC_BOUNDS['height'],
            use_osm=True,
            map_center_lat=NYC_BOUNDS['center_lat'],
            map_center_lon=NYC_BOUNDS['center_lon']
        )
        elapsed = time.time() - start_time
        
        self.assertTrue(map_obj.is_nyc_proper)
        self.assertEqual(len(map_obj.population), (NYC_BOUNDS['width'] + 1) * (NYC_BOUNDS['height'] + 1))
        # Should complete in under 2 seconds (no OSM calls during init)
        self.assertLess(elapsed, 2.0, f"Map initialization took {elapsed:.2f}s, should be < 2s")
    
    def test_nyc_map_initialization_no_crash(self):
        """Test that NYC map initialization doesn't crash"""
        try:
            map_obj = Map(
                width=NYC_BOUNDS['width'],
                height=NYC_BOUNDS['height'],
                use_osm=True,
                map_center_lat=NYC_BOUNDS['center_lat'],
                map_center_lon=NYC_BOUNDS['center_lon']
            )
            # If we get here, no exception was raised
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Map initialization raised exception: {e}")
    
    def test_game_setup_with_nyc_map(self):
        """Test that game setup with NYC map doesn't crash"""
        try:
            game = setup_game(
                player_names=["Player1", "Player2"],
                map_width=NYC_BOUNDS['width'],
                map_height=NYC_BOUNDS['height'],
                use_osm=True,
                map_center_lat=NYC_BOUNDS['center_lat'],
                map_center_lon=NYC_BOUNDS['center_lon']
            )
            # If we get here, no exception was raised
            self.assertIsNotNone(game)
            self.assertIsNotNone(game.map)
            self.assertTrue(game.map.is_nyc_proper)
        except Exception as e:
            self.fail(f"Game setup with NYC map raised exception: {e}")
    
    def test_water_detection_no_osm(self):
        """Test water detection without OSM (should use presets)"""
        # Test that water detection works without OSM calls
        # New Jersey should be water
        self.assertTrue(is_water_location(40.75, -74.08, None))
        # Manhattan should not be water
        self.assertFalse(is_water_location(40.71, -73.95, None))
        # Long Island Sound should be water
        self.assertTrue(is_water_location(40.90, -73.87, None))
    
    def test_population_growth_near_railroads(self):
        """Test that population increases when lines are built"""
        map_obj = Map(
            width=NYC_BOUNDS['width'],
            height=NYC_BOUNDS['height'],
            use_osm=True,
            map_center_lat=NYC_BOUNDS['center_lat'],
            map_center_lon=NYC_BOUNDS['center_lon']
        )
        
        # Get initial population at a location
        test_x, test_y = 25, 25
        initial_pop = map_obj.population.get((test_x, test_y), 0)
        
        # Create a mock player and line
        player = Player(player_id=0, money=10000, trainsets=[], name="Test Player", line_names=[])
        line = Line(
            name="Test Line",
            object_id=1,
            route=[(test_x, test_y), (test_x + 1, test_y)],
            owner=player,
            vehicles=[]
        )
        
        # Add line to map (should trigger population growth)
        map_obj.add_line(line)
        
        # Check that population increased
        new_pop = map_obj.population.get((test_x, test_y), 0)
        if initial_pop > 0:  # Only check if there was initial population
            self.assertGreater(new_pop, initial_pop, "Population should increase near railroads")
    
    def test_schedule_system(self):
        """Test line schedule system"""
        player = Player(player_id=0, money=10000, trainsets=[], name="Test Player", line_names=[])
        line = Line(
            name="Test Line",
            object_id=1,
            route=[(0, 0), (10, 0)],
            owner=player,
            vehicles=[]
        )
        
        # Test default schedule
        self.assertEqual(line.schedule['peak'], 0.0)
        self.assertEqual(line.schedule['off_peak'], 0.0)
        self.assertEqual(line.schedule['night'], 0.0)
        
        # Set schedule in test
        line.set_schedule_frequency('peak', 4.0)
        line.set_schedule_frequency('off_peak', 2.0)
        line.set_schedule_frequency('night', 1.0)
        
        # Test max frequency calculation
        max_freq = line.get_max_frequency_per_hour('peak')
        self.assertGreaterEqual(max_freq, 0.0)
        
        # Test setting schedule
        line.set_schedule_frequency('peak', 6.0)
        self.assertEqual(line.schedule['peak'], 6.0)
        
        # Test track upgrade
        initial_tracks = line.num_tracks
        line.upgrade_tracks(1)
        self.assertEqual(line.num_tracks, initial_tracks + 1)
        
        # More tracks should increase max frequency
        new_max_freq = line.get_max_frequency_per_hour('peak')
        self.assertGreaterEqual(new_max_freq, max_freq)


class TestNYCEvents(unittest.TestCase):
    """Test NYC events system"""
    
    def test_no_public_transportation_events(self):
        """Test that public transportation events are removed"""
        from nyc_events import get_nyc_events
        
        events = get_nyc_events()
        event_names = [e.name for e in events]
        
        # Should not have Brooklyn Bridge or Subway events
        self.assertNotIn("Brooklyn Bridge Opens", event_names)
        self.assertNotIn("NYC Subway Opens", event_names)
        
        # Should still have other events
        self.assertIn("Great Depression", event_names)
        self.assertIn("Post-War Boom", event_names)


if __name__ == '__main__':
    unittest.main()

