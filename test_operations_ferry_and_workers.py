"""Unit tests for ferry water validation fix, worker protection (max half fireable), and operations fire_workers."""
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

        def mock_not_near(x, y, max_m=60, skip_osm=False):
            return False
        tile_system.is_point_near_water = mock_not_near
        try:
            with self.assertRaises(ValueError) as ctx:
                self.ops._validate_ferry_route([(10.0, 10.0), (20.0, 20.0)])
            self.assertIn("60m of water", str(ctx.exception))
        finally:
            tile_system.is_point_near_water = original_near


if __name__ == '__main__':
    unittest.main()
