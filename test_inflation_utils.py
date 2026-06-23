"""Unit tests for inflation_utils."""
import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inflation_utils import get_inflation_factor, DEFAULT_START_YEAR, DEFAULT_END_YEAR


class TestGetInflationFactor(unittest.TestCase):
    """Test get_inflation_factor."""

    def test_start_year_returns_min_factor(self):
        """At start_year, returns min_factor (1.0)."""
        self.assertEqual(get_inflation_factor(1830), 1.0)
        self.assertEqual(get_inflation_factor(DEFAULT_START_YEAR), 1.0)

    def test_end_year_returns_max_factor(self):
        """At end_year, returns max_factor (10.0)."""
        self.assertEqual(get_inflation_factor(2050), 10.0)
        self.assertEqual(get_inflation_factor(DEFAULT_END_YEAR), 10.0)

    def test_before_start_clamped(self):
        """Years before start_year return min_factor."""
        self.assertEqual(get_inflation_factor(1800), 1.0)
        self.assertEqual(get_inflation_factor(0), 1.0)

    def test_after_end_clamped(self):
        """Years after end_year return max_factor."""
        self.assertEqual(get_inflation_factor(2100), 10.0)
        self.assertEqual(get_inflation_factor(3000), 10.0)

    def test_midpoint_linear(self):
        """Midpoint year gives midpoint factor."""
        mid_year = (DEFAULT_START_YEAR + DEFAULT_END_YEAR) // 2
        factor = get_inflation_factor(mid_year)
        self.assertAlmostEqual(factor, 5.5, places=1)

    def test_custom_bounds(self):
        """Custom start/end/min/max work."""
        f = get_inflation_factor(2000, start_year=1900, end_year=2100, min_factor=0.5, max_factor=2.0)
        self.assertGreater(f, 0.5)
        self.assertLess(f, 2.0)

    def test_degenerate_range_returns_min(self):
        """When end_year <= start_year, returns min_factor."""
        self.assertEqual(get_inflation_factor(2000, start_year=2000, end_year=2000), 1.0)
        self.assertEqual(get_inflation_factor(2000, start_year=2010, end_year=2000), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
