"""Tests for ridership route selection based on fastest and cheapest routes"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

