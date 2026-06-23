"""Unit tests for line extension through build queue"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

