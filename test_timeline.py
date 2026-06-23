"""Unit tests for Timeline."""
import unittest
from timeline import Timeline


class TestTimeline(unittest.TestCase):
    def test_initialization(self):
        tl = Timeline(initial_year=1830, end_year=2050)
        self.assertEqual(tl.get_year(), 1830)
        self.assertEqual(tl.get_start_year(), 1830)
        self.assertEqual(tl.get_end_year(), 2050)

    def test_get_start_year(self):
        tl = Timeline(initial_year=1850, end_year=2100)
        self.assertEqual(tl.get_start_year(), 1850)

    def test_get_end_year(self):
        tl = Timeline(initial_year=1830, end_year=2050)
        self.assertEqual(tl.get_end_year(), 2050)

    def test_advance_preserves_start_end(self):
        tl = Timeline(initial_year=1830, end_year=2050)
        tl.advance()
        tl.advance()
        self.assertEqual(tl.get_year(), 1832)
        self.assertEqual(tl.get_start_year(), 1830)
        self.assertEqual(tl.get_end_year(), 2050)

    def test_reset_restores_initial_year(self):
        tl = Timeline(initial_year=1830, end_year=2050)
        tl.advance()
        tl.advance()
        tl.reset()
        self.assertEqual(tl.get_year(), 1830)
