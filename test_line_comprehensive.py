"""Comprehensive unit tests for line.py"""
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
    
    def test_extend_route_from_beginning(self):
        """Test extending route from beginning"""
        initial_length = len(self.right_of_way)
        self.right_of_way.extend_route_from_beginning([(-1, -1), (0, 0)])
        # The extend method adds all points, so length increases by 2
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
        self.assertEqual(self.line.schedule['peak'], 0.0)
        self.assertEqual(self.line.schedule['off_peak'], 0.0)
        self.assertEqual(self.line.schedule['night'], 0.0)
        
        self.line.set_schedule_frequency('peak', 5.0)
        self.assertEqual(self.line.schedule['peak'], 5.0)
        
        self.line.set_schedule_frequency('off_peak', 3.0)
        self.assertEqual(self.line.schedule['off_peak'], 3.0)
        
        self.line.set_schedule_frequency('night', 1.0)
        self.assertEqual(self.line.schedule['night'], 1.0)
    
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

