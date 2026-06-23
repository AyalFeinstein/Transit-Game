"""Unit tests for ui.py - tests line upgrade system and other UI game logic."""
import unittest
from unittest.mock import Mock, patch
from operations import Operations, setup_game
from player import Player
from line import Line
from map import Map


class TestLineUpgradeSystem(unittest.TestCase):
    """Test the line upgrade construction job system."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.player_names = ["Player1", "Player2"]
        self.ui = setup_game(self.player_names, map_width=50, map_height=50, starting_money=50000)
        self.player1 = self.ui.get_players()[0]
        self.player2 = self.ui.get_players()[1]
        self.ui.set_current_player_index(0)
    
    def test_upgrade_line_creates_construction_job(self):
        """Test that upgrading a line creates a construction job instead of immediately applying."""
        # Create a line first
        line_name = "Test Line"
        points = [(10, 10), (20, 20), (30, 30)]
        self.ui.create_line_complete(line_name, points, self.player1)
        
        # Process build queue to complete line construction
        self.ui.process_build_queue()
        
        # Get the line
        line = self.ui.get_map_lines().get(line_name)
        self.assertIsNotNone(line)
        self.assertFalse(line.has_third_rail)
        
        # Advance year to 1890 (when third rail is available)
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Upgrade line with third rail
        result = self.ui.upgrade_line_complete(line_name, self.player1, ['thirdrail'])
        
        # Verify construction job was created
        self.assertIn('line_name', result)
        self.assertIn('upgrades', result)
        self.assertIn('construction_workers', result)
        self.assertIn('build_time', result)
        self.assertIn('base_work', result)
        
        # Verify line doesn't have third rail yet (construction job not complete)
        line = self.ui.get_map_lines().get(line_name)
        self.assertFalse(line.has_third_rail)
        
        # Verify build queue has the upgrade job
        build_queue = self.ui.get_build_queue()
        upgrade_jobs = [b for b in build_queue if b['type'] == 'line_upgrade']
        self.assertEqual(len(upgrade_jobs), 1)
        self.assertEqual(upgrade_jobs[0]['data']['line_name'], line_name)
        self.assertIn('thirdrail', upgrade_jobs[0]['data']['upgrades'])
    
    def test_upgrade_line_cost_calculation(self):
        """Test that upgrade cost is calculated based on line distance."""
        # Create a short line
        short_line_name = "Short Line"
        short_points = [(10, 10), (15, 15)]
        self.ui.create_line_complete(short_line_name, short_points, self.player1)
        self.ui.process_build_queue()
        
        # Create a long line
        long_line_name = "Long Line"
        long_points = [(10, 10), (20, 20), (30, 30), (40, 40), (50, 50)]
        self.ui.create_line_complete(long_line_name, long_points, self.player1)
        self.ui.process_build_queue()
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Calculate costs
        short_line = self.ui.get_map_lines().get(short_line_name)
        long_line = self.ui.get_map_lines().get(long_line_name)
        
        short_cost = self.ui.calculate_upgrade_cost(short_line, ['thirdrail'])
        long_cost = self.ui.calculate_upgrade_cost(long_line, ['thirdrail'])
        
        # Long line should cost more
        self.assertGreater(long_cost['cost'], short_cost['cost'])
    
    def test_upgrade_line_requires_money(self):
        """Test that upgrading a line requires money."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        # Set player money to 0
        self.ui.deduct_player_money(self.player1, self.ui.get_player_money(self.player1))
        self.assertEqual(self.ui.get_player_money(self.player1), 0)
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Try to upgrade - should fail
        with self.assertRaises(ValueError) as context:
            self.ui.upgrade_line_complete(line_name, self.player1, ['thirdrail'])
        
        self.assertIn("cannot afford", str(context.exception).lower())
    
    def test_upgrade_line_with_construction_workers(self):
        """Test that upgrade progress depends on construction workers."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (20, 20), (30, 30)]
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Upgrade with no construction workers
        self.ui.upgrade_line_complete(line_name, self.player1, ['thirdrail'])
        
        build_queue = self.ui.get_build_queue()
        upgrade_job = next(b for b in build_queue if b['type'] == 'line_upgrade')
        
        # With 0 workers, work shouldn't progress
        initial_work = upgrade_job.get('work_remaining', 0)
        self.ui.process_build_queue()
        
        # Work should remain the same (no workers)
        self.assertEqual(upgrade_job.get('work_remaining', 0), initial_work)
        
        # Now assign workers
        from worker import CONSTRUCTION_WORKER
        self.ui.rent_workers_for_player(self.player1, CONSTRUCTION_WORKER, 2, 1890)
        
        # Get job index
        job_index = build_queue.index(upgrade_job)
        self.ui.assign_workers_to_build(job_index, 2)
        
        # Process again - work should decrease
        self.ui.process_build_queue()
        
        # Work should be less than initial (2 workers working)
        self.assertLess(upgrade_job.get('work_remaining', float('inf')), initial_work)
    
    def test_upgrade_line_completes_after_construction(self):
        """Test that upgrade is applied after construction job completes."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (15, 15)]  # Short line for faster completion
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        line = self.ui.get_map_lines().get(line_name)
        self.assertFalse(line.has_third_rail)
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Hire construction workers
        from worker import CONSTRUCTION_WORKER
        self.ui.rent_workers_for_player(self.player1, CONSTRUCTION_WORKER, 10, 1890)
        
        # Upgrade line
        self.ui.upgrade_line_complete(line_name, self.player1, ['thirdrail'])
        
        # Get job and assign workers
        build_queue = self.ui.get_build_queue()
        upgrade_job = next(b for b in build_queue if b['type'] == 'line_upgrade')
        job_index = build_queue.index(upgrade_job)
        self.ui.assign_workers_to_build(job_index, 10)
        
        # Process until complete (should complete quickly with 10 workers)
        for _ in range(10):
            self.ui.process_build_queue()
            if upgrade_job not in build_queue:
                break
        
        # Verify upgrade was applied
        line = self.ui.get_map_lines().get(line_name)
        self.assertTrue(line.has_third_rail)
    
    def test_upgrade_line_technology_year_restriction(self):
        """Test that upgrades can only be applied after technology is invented."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        line = self.ui.get_map_lines().get(line_name)
        
        # Try to upgrade before 1890 (when third rail is invented)
        with self.assertRaises(ValueError) as context:
            self.ui.calculate_upgrade_cost(line, ['thirdrail'])
        
        self.assertIn("not invented until 1890", str(context.exception))
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Now should work
        cost_info = self.ui.calculate_upgrade_cost(line, ['thirdrail'])
        self.assertIn('thirdrail', cost_info['upgrades'])
    
    def test_upgrade_line_overhead_wire(self):
        """Test that overhead wire upgrade works correctly."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        line = self.ui.get_map_lines().get(line_name)
        self.assertFalse(line.has_overhead_wire)
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Upgrade with overhead wire
        self.ui.upgrade_line_complete(line_name, self.player1, ['overhead'])
        
        # Process until complete
        from worker import CONSTRUCTION_WORKER
        self.ui.rent_workers_for_player(self.player1, CONSTRUCTION_WORKER, 10, 1890)
        build_queue = self.ui.get_build_queue()
        upgrade_job = next(b for b in build_queue if b['type'] == 'line_upgrade')
        job_index = build_queue.index(upgrade_job)
        self.ui.assign_workers_to_build(job_index, 10)
        
        for _ in range(10):
            self.ui.process_build_queue()
            if upgrade_job not in build_queue:
                break
        
        # Verify upgrade
        line = self.ui.get_map_lines().get(line_name)
        self.assertTrue(line.has_overhead_wire)
    
    def test_upgrade_line_multiple_upgrades(self):
        """Test that multiple upgrades can be applied in one job."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (20, 20)]
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Upgrade with both third rail and overhead wire
        cost_info = self.ui.calculate_upgrade_cost(
            self.ui.get_map_lines().get(line_name),
            ['thirdrail', 'overhead']
        )
        
        self.assertIn('thirdrail', cost_info['upgrades'])
        self.assertIn('overhead', cost_info['upgrades'])
        self.assertGreater(cost_info['cost'], 0)
    
    def test_upgrade_already_upgraded_line(self):
        """Test that already upgraded lines can't be upgraded again with same upgrade."""
        # Create a line
        line_name = "Test Line"
        points = [(10, 10), (15, 15)]
        self.ui.create_line_complete(line_name, points, self.player1)
        self.ui.process_build_queue()
        
        # Advance to 1890
        for _ in range(1890 - 1830):
            self.ui.increase_year()
        
        # Upgrade once
        from worker import CONSTRUCTION_WORKER
        self.ui.rent_workers_for_player(self.player1, CONSTRUCTION_WORKER, 10, 1890)
        self.ui.upgrade_line_complete(line_name, self.player1, ['thirdrail'])
        
        # Complete the upgrade
        build_queue = self.ui.get_build_queue()
        upgrade_job = next(b for b in build_queue if b['type'] == 'line_upgrade')
        job_index = build_queue.index(upgrade_job)
        self.ui.assign_workers_to_build(job_index, 10)
        
        for _ in range(10):
            self.ui.process_build_queue()
            if upgrade_job not in build_queue:
                break
        
        # Try to upgrade again with same upgrade - should be filtered out
        cost_info = self.ui.calculate_upgrade_cost(
            self.ui.get_map_lines().get(line_name),
            ['thirdrail']
        )
        
        # Should have no valid upgrades (already has third rail)
        self.assertEqual(len(cost_info['upgrades']), 0)


if __name__ == '__main__':
    unittest.main()

