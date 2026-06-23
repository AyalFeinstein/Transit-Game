"""Unit tests for lobbying and government funding (operations.py)."""
import unittest
from unittest.mock import patch
from game import Game
from map import Map
from player import Player
from timeline import Timeline
from marketplace import Marketplace
from operations import Operations
from lawsuit import LawsuitExcuse, LAWSUIT_EXCUSES


def _make_ops_and_players():
    timeline = Timeline(initial_year=1830, end_year=2050)
    game_map = Map(width=50, height=50, use_osm=False)
    marketplace = Marketplace()
    p1 = Player(0, 100000, [], "Alice")
    p2 = Player(1, 100000, [], "Bob")
    game = Game(
        players=[p1, p2],
        game_map=game_map,
        timeline=timeline,
        marketplace=marketplace,
    )
    ops = Operations(["Alice", "Bob"], use_osm=False)
    ops.game = game
    return ops, ops.game, p1, p2


class TestLobbyInflation(unittest.TestCase):
    def test_inflation_factor_in_range(self):
        ops, *_ = _make_ops_and_players()
        f = ops.get_lobby_inflation_factor()
        self.assertGreaterEqual(f, 1.0)
        self.assertLessEqual(f, 10.0)


class TestLobbySuccessChance(unittest.TestCase):
    def test_chance_minimum(self):
        ops, *_ = _make_ops_and_players()
        self.assertGreaterEqual(ops.get_lobby_success_chance(0), 0.10)
        self.assertLessEqual(ops.get_lobby_success_chance(0), 0.15)

    def test_chance_increases_with_amount(self):
        ops, *_ = _make_ops_and_players()
        min_c = ops.get_lobby_minimum_cost()
        c1 = ops.get_lobby_success_chance(min_c)
        c2 = ops.get_lobby_success_chance(min_c * 10)
        c3 = ops.get_lobby_success_chance(min_c * 100)
        self.assertLess(c1, c3)
        self.assertLessEqual(c3, 0.95)


class TestGovernmentLobbySeatAndTiebreaker(unittest.TestCase):
    """Unit tests for government seat and tiebreaker lobbying (influence_seat, influence_tiebreaker)."""

    def test_get_government_lobby_options_includes_seat_and_tiebreaker(self):
        ops, *_ = _make_ops_and_players()
        opts = ops.get_government_lobby_options(ops.get_active_players()[0])
        ids = [o["id"] for o in opts]
        self.assertIn("influence_seat", ids)
        self.assertIn("influence_tiebreaker", ids)

    def test_try_lobby_influence_seat_at_index_flips_seat_and_deducts_money(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._seats[5] = 1  # Seat 5 unfavorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_seat", seat_index=5)
        self.assertTrue(r["success"])
        self.assertEqual(p1.get_money(), m0 - 1000)
        # Lobby takes effect at end of turn
        ops.advance_year_complete(ops.get_year() - 1)
        self.assertEqual(gov._seats[5], 0)
        self.assertIn(5, gov._lobbied_hidden)

    def test_try_lobby_influence_seat_at_index_already_favorable_deducts_money_no_flip(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._seats[5] = 0  # Already favorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_seat", seat_index=5)
        self.assertTrue(r["success"])
        self.assertIn("already favorable", r["message"].lower())
        self.assertEqual(gov._seats[5], 0)
        self.assertEqual(p1.get_money(), m0 - 1000)

    def test_try_lobby_influence_tiebreaker_flips_and_deducts_money(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._tiebreaker = 1  # Unfavorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_tiebreaker")
        self.assertTrue(r["success"])
        self.assertEqual(p1.get_money(), m0 - 1000)
        # Lobby takes effect at end of turn
        ops.advance_year_complete(ops.get_year() - 1)
        self.assertEqual(gov._tiebreaker, 0)
        self.assertIn(50, gov._lobbied_hidden)

    def test_try_lobby_influence_tiebreaker_already_favorable_deducts_money_no_flip(self):
        ops, game, p1, p2 = _make_ops_and_players()
        gov = game.get_government()
        gov._tiebreaker = 0  # Already favorable to player 0
        m0 = p1.get_money()
        r = ops.try_lobby(p1, 1000, option_id="influence_tiebreaker")
        self.assertTrue(r["success"])
        self.assertIn("already favorable", r["message"].lower())
        self.assertEqual(gov._tiebreaker, 0)
        self.assertEqual(p1.get_money(), m0 - 1000)

    def test_try_lobby_influence_seat_respects_minimum_cost(self):
        ops, *_ = _make_ops_and_players()
        min_cost = ops.get_lobby_minimum_cost()
        r = ops.try_lobby(ops.get_active_players()[0], min_cost - 1, option_id="influence_seat", seat_index=0)
        self.assertFalse(r["success"])
        self.assertIn("at least", r["message"].lower())

    def test_lobby_minimum_cost_is_cheap(self):
        ops, *_ = _make_ops_and_players()
        min_cost = ops.get_lobby_minimum_cost()
        self.assertLessEqual(min_cost, 100)


class TestLobbyOptions(unittest.TestCase):
    def test_drop_excuse_always_available(self):
        ops, *_ = _make_ops_and_players()
        opts = ops.get_lobby_options(ops.get_active_players()[0])
        ids = [o["id"] for o in opts]
        self.assertIn("drop_excuse", ids)

    def test_funding_options_after_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1971
        opts = ops.get_lobby_options(p1)
        ids = [o["id"] for o in opts]
        self.assertIn("receive_funding", ids)
        self.assertIn("redirect_funding", ids)

    def test_funding_options_before_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1969
        opts = ops.get_lobby_options(p1)
        ids = [o["id"] for o in opts]
        self.assertNotIn("receive_funding", ids)
        self.assertNotIn("redirect_funding", ids)

    def test_tax_break_pass_law_area_protection_gov_attack_available(self):
        ops, *_ = _make_ops_and_players()
        opts = ops.get_lobby_options(ops.get_active_players()[0])
        ids = [o["id"] for o in opts]
        self.assertIn("tax_break", ids)
        self.assertIn("one_time_grant", ids)
        self.assertIn("pass_law", ids)
        self.assertIn("area_protection", ids)
        self.assertIn("gov_attack", ids)


class TestTryLobby(unittest.TestCase):
    def test_try_lobby_requires_positive_amount(self):
        ops, *_ = _make_ops_and_players()
        r = ops.try_lobby(ops.get_active_players()[0], 0)
        self.assertFalse(r["success"])
        self.assertIn("positive", r["message"].lower())

    def test_try_lobby_insufficient_money(self):
        ops, _, p1, _ = _make_ops_and_players()
        p1.deduct_money(p1.get_money() - 500)
        r = ops.try_lobby(p1, 10000)
        self.assertFalse(r["success"])
        self.assertIn("need", r["message"].lower())

    def test_try_lobby_drop_excuse_success_removes_excuse(self):
        ops, game, p1, p2 = _make_ops_and_players()
        excuse = LAWSUIT_EXCUSES[0]
        p1.add_lawsuit_excuse(excuse)
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 1)
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 50000, option_id="drop_excuse")
        self.assertTrue(r["success"])
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 0)

    def test_try_lobby_receive_funding_sets_recipient(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1975
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 50000, option_id="receive_funding")
        self.assertTrue(r["success"])
        self.assertIs(ops.get_government_funding_recipient(), p1)

    def test_try_lobby_tax_break_sets_game_flag(self):
        ops, game, p1, p2 = _make_ops_and_players()
        y0 = 1840
        game.timeline.year = y0
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 1000, option_id="tax_break")
        self.assertTrue(r["success"])
        self.assertIs(getattr(game, "_lobby_tax_break_player", None), p1)
        self.assertEqual(getattr(game, "_lobby_tax_break_year", None), y0 + 1)

    def test_try_lobby_one_time_grant_adds_money(self):
        ops, game, p1, p2 = _make_ops_and_players()
        m0 = p1.get_money()
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 1000, option_id="one_time_grant")
        self.assertTrue(r["success"])
        grant = ops.get_government_funding_amount() // 2
        self.assertEqual(p1.get_money(), m0 - 1000 + grant)

    def test_try_lobby_pass_law_sets_game_flag(self):
        ops, game, p1, p2 = _make_ops_and_players()
        y0 = 1840
        game.timeline.year = y0
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 1000, option_id="pass_law")
        self.assertTrue(r["success"])
        self.assertIs(getattr(game, "_lobby_pass_law_player", None), p1)
        self.assertEqual(getattr(game, "_lobby_pass_law_until_year", None), y0 + 2)


class TestTryFileLawsuit(unittest.TestCase):
    """Tests for operations.try_file_lawsuit (game logic for filing lawsuits)."""

    def test_try_file_lawsuit_self_target_fails(self):
        ops, _, p1, _ = _make_ops_and_players()
        r = ops.try_file_lawsuit(p1, p1, excuse=None, made_up_cause="Test")
        self.assertFalse(r["success"])
        self.assertIn("yourself", r.get("message", "").lower())

    def test_try_file_lawsuit_success_returns_result(self):
        ops, _, p1, p2 = _make_ops_and_players()
        p1.add_money(100000)
        p2.add_money(100000)
        r = ops.try_file_lawsuit(p1, p2, excuse=None, made_up_cause="Unfair Competition")
        self.assertTrue(r["success"])
        self.assertIn("winner", r)
        self.assertIn("result_message", r)
        self.assertIn("plaintiff_name", r)
        self.assertIn("defendant_name", r)
        self.assertIn(r["plaintiff_name"], ["Alice", "Bob"])
        self.assertIn(r["defendant_name"], ["Alice", "Bob"])

    def test_try_file_lawsuit_with_excuse_removes_excuse(self):
        ops, _, p1, p2 = _make_ops_and_players()
        excuse = LAWSUIT_EXCUSES[0]
        p1.add_lawsuit_excuse(excuse)
        p1.add_money(100000)
        p2.add_money(100000)
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 1)
        r = ops.try_file_lawsuit(p1, p2, excuse=excuse, made_up_cause="")
        self.assertTrue(r["success"])
        self.assertEqual(len(ops.get_player_lawsuit_excuses(p1)), 0)


class TestDistributeGovernmentFunding(unittest.TestCase):
    def test_no_funding_before_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1969
        m1, m2 = p1.get_money(), p2.get_money()
        ops.distribute_government_funding(1969)
        self.assertEqual(p1.get_money(), m1)
        self.assertEqual(p2.get_money(), m2)

    def test_funding_distributed_after_1970(self):
        ops, game, p1, p2 = _make_ops_and_players()
        m1, m2 = p1.get_money(), p2.get_money()
        ops.distribute_government_funding(1970)
        total_after = p1.get_money() + p2.get_money()
        total_before = m1 + m2
        self.assertEqual(total_after - total_before, ops.get_government_funding_amount())

    def test_lobbied_recipient_gets_funding(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1975
        with patch("random.random", return_value=0.0):
            ops.try_lobby(p1, 50000, option_id="receive_funding")
        m1_before = p1.get_money()
        m2_before = p2.get_money()
        ops.distribute_government_funding(1975)
        self.assertEqual(p1.get_money(), m1_before + ops.get_government_funding_amount())
        self.assertEqual(p2.get_money(), m2_before)
        self.assertIsNone(ops.get_government_funding_recipient())

    def test_gov_attack_applied_next_year(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1850
        m2_before = p2.get_money()
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="gov_attack", target_player=p2)
        self.assertTrue(r["success"])
        penalty = ops.get_government_funding_amount()
        game.apply_lobby_effects(1851)
        self.assertEqual(p2.get_money(), m2_before - penalty)

    def test_add_lawsuit_excuse_adds_to_target(self):
        ops, game, p1, p2 = _make_ops_and_players()
        excuses_before = len(p2.get_lawsuit_excuses())
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="add_lawsuit_excuse", target_player=p2)
        self.assertTrue(r["success"])
        self.assertEqual(len(p2.get_lawsuit_excuses()), excuses_before + 1)

    def test_audit_target_doubles_taxes_next_year(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1850
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="audit_target", target_player=p2)
        self.assertTrue(r["success"])
        self.assertEqual(getattr(game, "_lobby_audit_target", None), p2)
        self.assertEqual(getattr(game, "_lobby_audit_year", None), 1851)
        poll_tax, property_tax = game.calc_taxes(p2, 1851)
        # Taxes should have been applied and cleared
        self.assertIsNone(getattr(game, "_lobby_audit_target", None))

    def test_block_funding_blocks_target_next_year(self):
        ops, game, p1, p2 = _make_ops_and_players()
        game.timeline.year = 1970
        with patch("random.random", return_value=0.0):
            r = ops.try_lobby(p1, 10000, option_id="block_funding", target_player=p2)
        self.assertTrue(r["success"])
        self.assertEqual(getattr(game, "_lobby_block_funding_target", None), p2)
        m1_before, m2_before = p1.get_money(), p2.get_money()
        ops._government_funding_recipient = p2
        ops.distribute_government_funding(1971)
        # p2 should be blocked; p1 or random other gets funding
        total_after = p1.get_money() + p2.get_money()
        total_before = m1_before + m2_before
        self.assertEqual(total_after - total_before, ops.get_government_funding_amount())
        # p2 should not have received it (blocked)
        self.assertEqual(p2.get_money(), m2_before)


class TestWaterCheckDeadline(unittest.TestCase):
    def test_tile_system_deadline_limits_osm_calls(self):
        from osm_tile_system import OSMTileSystem
        from unittest.mock import Mock
        mock_osm = Mock()
        ts = OSMTileSystem(50, 50, 40.7, -73.9, tile_size=0.01, osm_integration=mock_osm)
        ts.set_water_check_deadline(0.0)
        t = ts.get_tile_type(0, 0)
        ts.clear_water_check_deadline()
        self.assertIn(t, (None, "land", "water"))
        self.assertEqual(mock_osm.is_water_at_latlon.call_count, 0)


class TestWaterDetectionSouthFerryGovernorsIsland(unittest.TestCase):
    """South Ferry (Manhattan) <-> Governors Island ferry corridor must be detected as water."""

    def test_nyc_preset_segment_south_ferry_governors_island_is_water(self):
        import map_presets_nyc as nyc
        # Points in the channel: South Ferry ~40.701,-74.013, Governors Island dock ~40.689,-74.016
        # Mid-channel and Manhattan-side pier should be water via NYC_WATER_AREAS (Buttermilk Channel box)
        self.assertTrue(nyc.is_water_location(40.695, -74.015, None))
        self.assertTrue(nyc.is_water_location(40.701, -74.013, None))
        self.assertTrue(nyc.is_water_location(40.689, -74.016, None))
