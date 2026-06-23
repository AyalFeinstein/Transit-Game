"""Unit tests for marketplace line purchase and ownership"""
import unittest
import signal
import time
from operations import Operations
from PyQt6.QtWidgets import QApplication
import sys
from line import Line
from marketplace import Marketplace, SaleItem


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TimeoutException(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutException("Test exceeded 20 second timeout")


class TestMarketplaceLinePurchase(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
        self.ui = Operations(["Alice", "Bob"], 50, 50, 20000, 1.0, 1.0)
        self.players = self.ui.get_players()
        self.player1 = self.players[0]
        self.player2 = self.players[1]
    
    def test_line_purchased_by_player2_belongs_to_player2(self):
        line_points = [(10, 10), (11, 10), (12, 10)]
        marketplace_line = self.ui.game.create_line("Marketplace Line", self.player1, line_points)
        self.ui.game.get_map().add_line(marketplace_line)
        self.player1.acquire_line(marketplace_line)
        
        marketplace = self.ui.game.get_marketplace()
        marketplace.add_line(marketplace_line, price=1000)
        
        sale_items = marketplace.get_available_lines()
        sale_item = None
        for item in sale_items:
            if item.get_item() == marketplace_line:
                sale_item = item
                break
        self.assertIsNotNone(sale_item)
        
        self.assertEqual(marketplace_line.get_owner(), self.player1)
        self.assertIn("Marketplace Line", self.player1.get_line_names())
        self.assertNotIn("Marketplace Line", self.player2.get_line_names())
        
        initial_money = self.player2.get_money()
        price = sale_item.get_price()
        self.player2.add_money(2000)
        expected_final_money = initial_money + 2000 - price
        
        result = self.ui.buy_line_by_purchase(sale_item, self.player2)
        
        self.assertIsNotNone(result)
        self.assertEqual(marketplace_line.get_owner(), self.player2)
        self.assertIn("Marketplace Line", self.player2.get_line_names())
        self.assertNotIn("Marketplace Line", self.player1.get_line_names())
        self.assertEqual(self.player2.get_money(), expected_final_money)
    
    def test_line_purchased_by_player1_stays_with_player1(self):
        line_points = [(20, 20), (21, 20), (22, 20)]
        marketplace_line = self.ui.game.create_line("Player1 Line", self.player1, line_points)
        self.ui.game.get_map().add_line(marketplace_line)
        self.player1.acquire_line(marketplace_line)
        
        marketplace = self.ui.game.get_marketplace()
        marketplace.add_line(marketplace_line, price=1000)
        
        sale_items = marketplace.get_available_lines()
        sale_item = None
        for item in sale_items:
            if item.get_item() == marketplace_line:
                sale_item = item
                break
        self.assertIsNotNone(sale_item)
        
        self.assertEqual(marketplace_line.get_owner(), self.player1)
        self.assertIn("Player1 Line", self.player1.get_line_names())
        
        self.player1.add_money(2000)
        result = self.ui.buy_line_by_purchase(sale_item, self.player1)
        
        self.assertIsNotNone(result)
        self.assertEqual(marketplace_line.get_owner(), self.player1)
        self.assertIn("Player1 Line", self.player1.get_line_names())
    
    def test_old_owner_loses_line_when_sold(self):
        line_points = [(30, 30), (31, 30), (32, 30)]
        marketplace_line = self.ui.game.create_line("Transfer Line", self.player1, line_points)
        self.ui.game.get_map().add_line(marketplace_line)
        self.player1.acquire_line(marketplace_line)
        
        marketplace = self.ui.game.get_marketplace()
        marketplace.add_line(marketplace_line, price=1000)
        
        sale_items = marketplace.get_available_lines()
        sale_item = None
        for item in sale_items:
            if item.get_item() == marketplace_line:
                sale_item = item
                break
        self.assertIsNotNone(sale_item)
        
        self.assertIn("Transfer Line", self.player1.get_line_names())
        
        self.player2.add_money(2000)
        result = self.ui.buy_line_by_purchase(sale_item, self.player2)
        
        self.assertIsNotNone(result)
        self.assertNotIn("Transfer Line", self.player1.get_line_names())
        self.assertIn("Transfer Line", self.player2.get_line_names())


class TestPerformanceTimeout(unittest.TestCase):
    def setUp(self):
        self._app = _ensure_app()
    
    def run(self, result=None):
        if hasattr(signal, 'SIGALRM'):
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(20)
        try:
            super().run(result)
        finally:
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)
    
    def test_year_advance_completes_quickly(self):
        ui = Operations(["Player1"], 50, 50, 20000, 1.0, 1.0)
        player = ui.get_players()[0]
        
        line_points = [(10, 10), (11, 10), (12, 10)]
        ui.create_line_complete("Test Line", line_points, player)
        
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        start_time = time.time()
        result = ui.advance_year_complete(year)
        elapsed = time.time() - start_time
        
        self.assertLess(elapsed, 20.0, f"Year advance took {elapsed:.3f}s, should be < 20.0s")
    
    def test_multiple_operations_complete_quickly(self):
        ui = Operations(["Player1", "Player2"], 50, 50, 20000, 1.0, 1.0)
        players = ui.get_players()
        
        for i, player in enumerate(players):
            line_points = [(10 + i*10, 10), (11 + i*10, 10), (12 + i*10, 10)]
            ui.create_line_complete(f"Line {i}", line_points, player)
        
        year = ui.get_year()
        while len(ui.get_build_queue()) > 0:
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        
        start_time = time.time()
        for _ in range(5):
            result = ui.advance_year_complete(year)
            year = result.get('new_year', year + 1)
        elapsed = time.time() - start_time
        
        self.assertLess(elapsed, 20.0, f"Multiple year advances took {elapsed:.3f}s, should be < 20.0s")


if __name__ == "__main__":
    unittest.main(verbosity=2)

