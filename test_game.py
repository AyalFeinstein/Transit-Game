"""Consolidated tests for game."""
import unittest

import unittest
from game import Game
from player import Player
from map import Map
from timeline import Timeline
from marketplace import Marketplace
from line import Line


class TestGame(unittest.TestCase):
    def setUp(self):
        self.players = [
            Player(0, 10000, [], "Player 1"),
            Player(1, 10000, [], "Player 2")
        ]
        self.game_map = Map(width=50, height=50)
        self.timeline = Timeline(initial_year=1830, end_year=2050)
        self.marketplace = Marketplace()
        self.game = Game(
            players=self.players,
            game_map=self.game_map,
            timeline=self.timeline,
            marketplace=self.marketplace
        )
    
    def test_game_initialization(self):
        """Test Game initialization"""
        self.assertEqual(self.game.get_players(), self.players)
        self.assertEqual(self.game.get_map(), self.game_map)
        self.assertEqual(self.game.get_marketplace(), self.marketplace)
        self.assertFalse(self.game.is_playing)
        self.assertEqual(self.game.worker_replenishment_rate, 1.0)
    
    def test_get_players_by_name(self):
        """Test getting players by name"""
        players_by_name = self.game.get_players_by_name()
        self.assertEqual(players_by_name["Player 1"], self.players[0])
        self.assertEqual(players_by_name["Player 2"], self.players[1])
    
    def test_get_active_players(self):
        """Test getting active players"""
        active = self.game.get_active_players()
        self.assertEqual(len(active), 2)
        self.assertIn(self.players[0], active)
        self.assertIn(self.players[1], active)
        
        # Eliminate a player
        self.players[0].eliminate()
        active = self.game.get_active_players()
        self.assertEqual(len(active), 1)
        self.assertIn(self.players[1], active)
        self.assertNotIn(self.players[0], active)
    
    def test_eliminate_player(self):
        """Test eliminating a player"""
        line = Line("Test Line", 1, [(0, 0), (1, 1)], self.players[0], [])
        self.game_map.add_line(line)
        self.players[0].acquire_line(line)
        
        self.assertFalse(self.players[0].is_eliminated)
        self.assertIn("Test Line", self.players[0].get_line_names())
        self.assertIn("Test Line", self.game_map.get_lines_by_name())
        
        self.game.eliminate_player(self.players[0])
        
        self.assertTrue(self.players[0].is_eliminated)
        self.assertEqual(len(self.players[0].get_line_names()), 0)
        self.assertNotIn("Test Line", self.game_map.get_lines_by_name())
        self.assertEqual(len(self.players[0].get_trainsets()), 0)

if __name__ == '__main__':
    unittest.main()
