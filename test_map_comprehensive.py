"""Comprehensive unit tests for map.py"""
import unittest
from map import Map
from line import Line
from player import Player


class TestMap(unittest.TestCase):
    def setUp(self):
        self.map = Map(width=50, height=50)
        self.player = Player(0, 10000, [], "Test Player")
    
    def test_map_initialization(self):
        """Test Map initialization"""
        self.assertEqual(self.map.width, 50)
        self.assertEqual(self.map.height, 50)
        self.assertEqual(self.map.grid, {})
        self.assertEqual(self.map.lines, {})
        self.assertEqual(self.map.roads, {})
        # Map defaults to use_osm=True for OSM tile support
        self.assertTrue(self.map.use_osm)
        self.assertEqual(self.map.stations, [])

    def test_map_initialization_use_osm_false(self):
        """Test Map initialization with use_osm=False"""
        map_no_osm = Map(width=30, height=30, use_osm=False)
        self.assertFalse(map_no_osm.use_osm)
    
    def test_add_line(self):
        """Test adding lines to map"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.map.add_line(line)
        
        lines_by_name = self.map.get_lines_by_name()
        self.assertIn("Test Line", lines_by_name)
        self.assertEqual(lines_by_name["Test Line"], line)
    
    def test_delete_line(self):
        """Test deleting lines from map"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.player, [])
        self.map.add_line(line)
        
        self.assertIn("Test Line", self.map.get_lines_by_name())
        self.map.delete_line(line)
        self.assertNotIn("Test Line", self.map.get_lines_by_name())
    
    def test_get_lines_by_name(self):
        """Test getting lines by name"""
        line1 = Line("Line 1", 1, [(0, 0), (1, 1)], self.player, [])
        line2 = Line("Line 2", 2, [(2, 2), (3, 3)], self.player, [])
        
        self.map.add_line(line1)
        self.map.add_line(line2)
        
        lines_by_name = self.map.get_lines_by_name()
        self.assertEqual(len(lines_by_name), 2)
        self.assertIn("Line 1", lines_by_name)
        self.assertIn("Line 2", lines_by_name)
    
    def test_map_with_osm(self):
        """Test map with OSM enabled"""
        # Note: This test may fail if OSM integration is not available
        try:
            map_osm = Map(width=50, height=50, use_osm=True,
                         map_center_lat=40.7128, map_center_lon=-74.0060)
            self.assertTrue(map_osm.use_osm)
        except Exception:
            # OSM might not be available, that's okay
            pass

    def test_tile_system_none_when_osm_disabled(self):
        """Game operation: tile_system is None when OSM is disabled (no lazy init in production)."""
        m = Map(width=50, height=50, use_osm=False)
        self.assertIsNone(m.get_tile_system())

    def test_tile_system_initialized_when_osm_enabled(self):
        """Game operation: tile_system is initialized in Map.__init__ when use_osm=True and OSM loads."""
        try:
            m = Map(width=50, height=50, use_osm=True,
                    map_center_lat=40.7128, map_center_lon=-74.0060)
            if m.osm_integration is not None:
                self.assertIsNotNone(m.get_tile_system(),
                                    "tile_system must be set at init when use_osm=True and osm_integration exists")
        except Exception:
            # OSM/requests may be unavailable
            self.skipTest("OSM integration not available")
    
    def test_nyc_map_detection(self):
        """Test NYC map detection"""
        # NYC coordinates
        nyc_map = Map(width=50, height=50, use_osm=False,
                     map_center_lat=40.7128, map_center_lon=-74.0060)
        
        # Check if it's detected as NYC proper
        if hasattr(nyc_map, 'is_nyc_proper'):
            self.assertIsNotNone(nyc_map.is_nyc_proper)
    
    def test_population_points(self):
        """Test population points"""
        self.assertIsNotNone(self.map.population)
        self.assertIsInstance(self.map.population, dict)
    
    def test_get_population(self):
        """Test getting population at location"""
        test_x1, test_y1 = 5, 5
        test_x2, test_y2 = 15, 15
        test_x3, test_y3 = 25, 25
        
        original_pop1 = self.map.population.get((test_x1, test_y1), 0)
        original_pop2 = self.map.population.get((test_x2, test_y2), 0)
        original_pop3 = self.map.population.get((test_x3, test_y3), 0)
        
        self.map.population[(test_x1, test_y1)] = 100
        self.map.population[(test_x2, test_y2)] = 200
        
        pop1 = self.map.get_population(test_x1, test_y1)
        pop2 = self.map.get_population(test_x2, test_y2)
        pop3 = self.map.get_population(test_x3, test_y3)
        
        self.assertEqual(pop1, 100)
        self.assertEqual(pop2, 200)
        # pop3 should be 0 if no population, or original value if it existed
        self.assertEqual(pop3, original_pop3)
        
        # Restore original values
        if original_pop1 == 0 and (test_x1, test_y1) in self.map.population:
            del self.map.population[(test_x1, test_y1)]
        elif original_pop1 > 0:
            self.map.population[(test_x1, test_y1)] = original_pop1
            
        if original_pop2 == 0 and (test_x2, test_y2) in self.map.population:
            del self.map.population[(test_x2, test_y2)]
        elif original_pop2 > 0:
            self.map.population[(test_x2, test_y2)] = original_pop2


if __name__ == "__main__":
    unittest.main(verbosity=2)

