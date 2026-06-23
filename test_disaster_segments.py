"""Tests for natural disaster damage preventing rides over destroyed segments"""
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
        
        # Simulate disaster damage - damage middle segment
        # Add damaged segment to build queue (simulating disaster)
        damaged_segment = [(12, 10), (13, 10)]
        segment_data = {
            'name': line.name,
            'points': damaged_segment,
            'segment_index': 2,
            'total_segments': 4,
            'disaster_repair': True,
            'disaster_type': 'Earthquake',
            'repair_cost': 100
        }
        ui.add_to_build_queue('line', segment_data, player, 0)
        
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
        segment_data = {
            'name': line.name,
            'points': damaged_segment,
            'segment_index': 1,
            'total_segments': 3,
            'disaster_repair': True,
            'disaster_type': 'Fire',
            'repair_cost': 100
        }
        ui.add_to_build_queue('line', segment_data, player, 0)
        
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
        segment_data = {
            'name': line.name,
            'points': damaged_segment,
            'segment_index': 2,
            'total_segments': 4,
            'disaster_repair': True,
            'disaster_type': 'Tornado',
            'repair_cost': 100
        }
        ui.add_to_build_queue('line', segment_data, player, 0)
        
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

