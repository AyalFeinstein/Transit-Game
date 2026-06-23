"""Unit tests for government entity."""
import unittest
from operations import Operations
from government import Government


class TestGovernment(unittest.TestCase):
    def setUp(self):
        self.ops = Operations(
            ["Alice", "Bob"],
            map_width=30,
            map_height=30,
            starting_money=100000,
            map_preset_name="standard"
        )
        self.gov = self.ops.game.get_government()
        self.p1, self.p2 = self.ops.get_players()

    def test_government_exists(self):
        self.assertIsNotNone(self.gov)

    def test_government_has_50_seats_plus_tiebreaker(self):
        seats = self.gov.get_seats_for_display(0)
        self.assertEqual(len(seats), 51)
        self.assertTrue(any(s[1] for s in seats))

    def test_tax_multiplier_in_range(self):
        m = self.gov.get_tax_multiplier(0)
        self.assertIn(m, (0.75, 1.25))

    def test_next_election_year(self):
        # Before first election (1832), next election is 1832
        y = self.gov.get_next_election_year(1830)
        self.assertEqual(y, 1832)
        y = self.gov.get_next_election_year(1831)
        self.assertEqual(y, 1832)
        # After first election, next is every 6 years
        self.gov._has_held_election = True
        self.gov._election_year = 1832
        y = self.gov.get_next_election_year(1835)
        self.assertEqual(y, 1838)

    def test_hold_election_at_interval(self):
        # First election at 1832
        self.assertFalse(self.gov.hold_election(1831))
        self.assertTrue(self.gov.hold_election(1832))
        self.assertEqual(self.gov._election_year, 1832)
        # Subsequent elections every 6 years
        self.assertFalse(self.gov.hold_election(1835))
        self.assertTrue(self.gov.hold_election(1838))
        self.assertEqual(self.gov._election_year, 1838)

    def test_lobby_flip_seat(self):
        self.gov._seats = [1] * 50  # All unfavorable to player 0
        result = self.gov.lobby_flip_seat(0)
        self.assertTrue(result)
        self.assertEqual(sum(1 for s in self.gov._seats if s == 0), 1)

    def test_lobby_flip_tiebreaker(self):
        self.gov._tiebreaker = 1
        result = self.gov.lobby_flip_tiebreaker(0)
        self.assertTrue(result)
        self.assertEqual(self.gov._tiebreaker, 0)

    def test_lobby_flip_seat_at_index(self):
        self.gov._seats[5] = 1
        result = self.gov.lobby_flip_seat_at_index(5, 0)
        self.assertTrue(result)
        self.assertEqual(self.gov._seats[5], 0)
        result = self.gov.lobby_flip_seat_at_index(5, 0)
        self.assertFalse(result)

    def test_pass_annual_laws(self):
        laws = self.gov.pass_annual_laws(1835)
        self.assertIn('tax_modifiers', laws)
        self.assertIn('lawsuit_protection', laws)
        self.assertIn('lawsuit_target', laws)
        self.assertIn('maintenance_relief', laws)
        self.assertIn('grants', laws)
        self.assertIsInstance(laws['tax_modifiers'], dict)
        self.assertIsInstance(laws['grants'], dict)

    def test_operations_timeline_methods(self):
        """Operations.get_timeline_start_year and get_timeline_end_year work."""
        self.assertEqual(self.ops.get_timeline_start_year(), 1830)
        self.assertEqual(self.ops.get_timeline_end_year(), 2050)


if __name__ == '__main__':
    unittest.main()
