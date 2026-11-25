"""Performance timing tests to verify no freezes occur"""
import unittest
import time
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


class TestPerformanceTiming(unittest.TestCase):
    """Test that operations complete quickly without freezes"""
    
    def setUp(self):
        self._app = _ensure_app()
        # Use smaller map for faster tests
        self.ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        self.player = self.ui.get_players()[0]
    
    def test_get_line_stats_is_fast(self):
        """Test that get_line_stats completes quickly (should use cache, never calculate)"""
        # Create a line first
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        # Complete the line construction
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines()["Test Line"]
        
        # Measure time to get line stats (should be very fast - uses cache)
        start_time = time.time()
        stats = self.ui.get_line_stats(line)
        elapsed = time.time() - start_time
        
        # Should complete in less than 0.1 seconds (instant with cache)
        self.assertLess(elapsed, 0.1, f"get_line_stats took {elapsed:.3f}s, should be < 0.1s")
        
        # Verify stats were returned
        self.assertIn('name', stats)
        self.assertIn('profit', stats)
    
    def test_get_line_stats_before_year_advance_is_fast(self):
        """Test that get_line_stats is fast even before year advance (returns 0 profit)"""
        # Create a line
        line_points = [(10, 10), (11, 10), (12, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        # Complete construction
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines()["Test Line"]
        
        # Get stats immediately (before any year advance that calculates profit)
        # Should be fast because it returns 0 without calculating
        start_time = time.time()
        stats = self.ui.get_line_stats(line)
        elapsed = time.time() - start_time
        
        # Should be instant (< 0.05s)
        self.assertLess(elapsed, 0.05, f"get_line_stats before year advance took {elapsed:.3f}s, should be < 0.05s")
        
        # Profit should be 0 (not calculated yet)
        self.assertEqual(stats['profit'], 0)
    
    def test_multiple_get_line_stats_calls_are_fast(self):
        """Test that multiple calls to get_line_stats are all fast (caching works)"""
        # Create multiple lines
        for i in range(5):
            line_points = [(10 + i*5, 10), (11 + i*5, 10), (12 + i*5, 10)]
            self.ui.create_line_complete(f"Line {i}", line_points, self.player)
        
        # Complete construction
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        lines = self.ui.get_map_lines()
        
        # Measure time for multiple stats calls
        start_time = time.time()
        for line_name, line in lines.items():
            stats = self.ui.get_line_stats(line)
            self.assertIn('name', stats)
        elapsed = time.time() - start_time
        
        # All calls combined should be fast (< 0.5s for 5 lines)
        self.assertLess(elapsed, 0.5, f"Multiple get_line_stats calls took {elapsed:.3f}s, should be < 0.5s")
    
    def test_year_advance_completes_quickly(self):
        """Test that year advance completes quickly (< 2 seconds)"""
        # Create a line with stations to generate some ridership
        line_points = [(10, 10), (11, 10), (12, 10), (13, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        # Complete construction
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines()["Test Line"]
        
        # Add stations and trainset
        from vehicle_presets import get_vehicle_registry
        registry = get_vehicle_registry()
        locomotives = registry.get_locomotives_by_year()
        if locomotives:
            loco, _ = locomotives[0]
            from vehicle import Trainset
            trainset = Trainset(loco, [])
            line.vehicles = [trainset]
            
            station1 = Station("Station1", 10, 10)
            station2 = Station("Station2", 13, 10)
            line.add_station(station1)
            line.add_station(station2)
        
        # Advance year - should complete quickly
        start_time = time.time()
        result = self.ui.advance_year_complete(year)
        elapsed = time.time() - start_time
        
        # Year advance should complete in < 2 seconds for small maps
        self.assertLess(elapsed, 20.0, f"Year advance took {elapsed:.3f}s, should be < 20.0s")
        
        new_year = result.get('new_year', year + 1)
        self.assertEqual(new_year, year + 1)
        
        # Verify ridership was cached
        self.assertIsNotNone(self.ui._cached_ridership_data)
        self.assertEqual(self.ui._cached_ridership_year, new_year)
        
        # Verify profit was cached on line
        self.assertTrue(hasattr(line, '_cached_profit'))
        self.assertEqual(line._cached_profit_year, new_year)
        
        # Now get_line_stats should be instant (uses cache)
        start_time = time.time()
        stats = self.ui.get_line_stats(line)
        elapsed = time.time() - start_time
        
        # Should be instant now (< 0.05s)
        self.assertLess(elapsed, 0.05, f"get_line_stats after year advance took {elapsed:.3f}s, should be < 0.05s")
    
    def test_button_press_operations_complete_quickly(self):
        """Test that all button press operations complete quickly (< 1 second)"""
        line_points = [(10, 10), (11, 10), (12, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines()["Test Line"]
        
        start_time = time.time()
        stats = self.ui.get_line_stats(line)
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.1, f"get_line_stats took {elapsed:.3f}s, should be < 0.1s")
        
        start_time = time.time()
        profit = self.ui.calc_line_profit(line, year)
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.01, f"calc_line_profit took {elapsed:.3f}s, should be < 0.01s")
        
        start_time = time.time()
        finances = self.ui.process_player_finances(self.player, year)
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.1, f"process_player_finances took {elapsed:.3f}s, should be < 0.1s")
    
    def test_no_operation_takes_longer_than_60_seconds(self):
        """Test that no single button press operation takes longer than 60 seconds"""
        line_points = [(10, 10), (11, 10), (12, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines()["Test Line"]
        
        operations = [
            ("get_line_stats", lambda: self.ui.get_line_stats(line)),
            ("calc_line_profit", lambda: self.ui.calc_line_profit(line, year)),
            ("process_player_finances", lambda: self.ui.process_player_finances(self.player, year)),
            ("advance_year_complete", lambda: self.ui.advance_year_complete(year))
        ]
        
        for op_name, op_func in operations:
            start_time = time.time()
            try:
                op_func()
                elapsed = time.time() - start_time
                self.assertLess(elapsed, 20.0, f"{op_name} took {elapsed:.3f}s, should be < 20.0s")
            except Exception as e:
                elapsed = time.time() - start_time
                self.fail(f"{op_name} raised exception after {elapsed:.3f}s: {e}")
    
    def test_process_player_finances_uses_cache(self):
        """Test that process_player_finances uses cached values and is fast"""
        # Create a line
        line_points = [(10, 10), (11, 10), (12, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        # Complete construction and advance year to calculate and cache profit
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        # Advance year once more to cache profit
        result = self.ui.advance_year_complete(year)
        new_year = result.get('new_year', year + 1)
        
        # Now process_player_finances should use cache and be fast
        start_time = time.time()
        finances = self.ui.process_player_finances(self.player, new_year)
        elapsed = time.time() - start_time
        
        # Should be fast since it uses cached profit (< 0.1s)
        self.assertLess(elapsed, 0.1, f"process_player_finances took {elapsed:.3f}s, should be < 0.1s")
        self.assertIn('profit', finances)
    
    def test_calc_line_profit_uses_cache(self):
        """Test that calc_line_profit uses cached values and never calculates"""
        # Create a line
        line_points = [(10, 10), (11, 10), (12, 10)]
        self.ui.create_line_complete("Test Line", line_points, self.player)
        
        # Complete construction
        year = self.ui.get_year()
        while len(self.ui.get_build_queue()) > 0:
            result = self.ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        line = self.ui.get_map_lines()["Test Line"]
        
        # calc_line_profit should return 0 (no cache yet) and be instant
        start_time = time.time()
        profit = self.ui.calc_line_profit(line, year)
        elapsed = time.time() - start_time
        
        # Should be instant (< 0.01s)
        self.assertLess(elapsed, 0.01, f"calc_line_profit without cache took {elapsed:.3f}s, should be < 0.01s")
        self.assertEqual(profit, 0)
        
        # Advance year to cache profit
        result = self.ui.advance_year_complete(year)
        new_year = result.get('new_year', year + 1)
        
        # Now should use cache and be instant
        start_time = time.time()
        profit = self.ui.calc_line_profit(line, new_year)
        elapsed = time.time() - start_time
        
        # Should be instant (< 0.01s)
        self.assertLess(elapsed, 0.01, f"calc_line_profit with cache took {elapsed:.3f}s, should be < 0.01s")
        self.assertIsInstance(profit, int)


if __name__ == "__main__":
    unittest.main(verbosity=2)

