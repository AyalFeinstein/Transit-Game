"""Unit tests for buttons and handlers in qt_networked.py.
Tests that button clicks invoke the correct operations and do not crash."""
import os
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Create QApplication FIRST before any Qt or game imports (required for Qt widgets)
from PyQt6.QtWidgets import QApplication
_qt_app = QApplication.instance()
if _qt_app is None:
    _qt_app = QApplication(sys.argv)


def _ensure_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestQtNetworkedMainWindow(unittest.TestCase):
    """Test MainWindow from qt_networked without starting full game."""

    def setUp(self):
        _ensure_app()

    def test_main_window_creates_without_error(self):
        """MainWindow can be instantiated."""
        from qt_networked import MainWindow
        w = MainWindow()
        self.assertIsNotNone(w)
        self.assertIsNotNone(w.tabs)
        w.close()

    def test_new_game_button_no_reset_when_declined(self):
        """New Game menu action keeps operations when user declines."""
        from PyQt6.QtWidgets import QMessageBox
        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = Mock()
        with patch('qt_networked.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            w.on_new_game_clicked()
        self.assertIsNotNone(w.operations)
        w.close()

    def test_start_game_button_creates_operations(self):
        """Start Game creates Operations and sets up game."""
        from qt_networked import MainWindow
        w = MainWindow()
        w.map_type_combo.setCurrentIndex(0)
        w.map_width_spin.setValue(20)
        w.map_height_spin.setValue(20)
        w.player_count_spin.setValue(2)
        w.starting_money_spin.setValue(50000)
        w.on_start_game_clicked()
        self.assertIsNotNone(w.operations)
        w.close()

    def test_load_game_no_crash(self):
        """Load Game does not crash (shows 'no saves' when directory empty)."""
        from qt_networked import MainWindow
        w = MainWindow()
        mock_save_dir = Mock()
        mock_save_dir.mkdir = Mock()
        mock_save_dir.glob.return_value = []
        with patch('qt_networked.SAVEGAMES_DIR', mock_save_dir):
            with patch('qt_networked.QMessageBox.information'):
                w.on_load_game_clicked()
        w.close()

    def test_get_player_info_button_no_crash(self):
        """Get Player Info does not crash when no game."""
        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = None
        with patch('qt_networked.QMessageBox.information'):
            w.on_player_info_button_clicked()
        w.close()


class TestQtNetworkedWithGame(unittest.TestCase):
    """Test buttons when a game is running."""

    def setUp(self):
        _ensure_app()
        from operations import Operations
        self.ops = Operations(
            ["Alice", "Bob"],
            map_width=30,
            map_height=30,
            starting_money=100000,
            map_preset_name="standard"
        )
        self.players = self.ops.get_players()

    def test_advance_year_button(self):
        """End Year / Advance Year advances the game year."""
        from qt_networked import MainWindow
        w = MainWindow()
        w.operations = self.ops
        w.player_tabs_dict = {}
        w.status_bar = None
        w.status_year_label = None
        w.status_money_labels = {i: __import__('PyQt6.QtWidgets', fromlist=['QLabel']).QLabel("") for i in range(8)}
        w.status_end_year_btn = None
        w.last_lawyer_replenishment_year = None
        w.tabs.clear()
        w.tabs.addTab(w.game_tab, "Game")
        year_before = self.ops.get_year()
        with patch.object(w, 'DisplayMapInfo', Mock()), \
             patch.object(w, 'DisplayMarketplaceInfo', Mock()), \
             patch.object(w, 'DisplayRollingStockInfo', Mock()), \
             patch.object(w, 'update_status_bar', Mock()), \
             patch('qt_networked.QMessageBox'):
            w.advance_year_button_clicked()
        year_after = self.ops.get_year()
        self.assertEqual(year_after, year_before + 1)
        w.close()

    def test_lobby_options_available(self):
        """Lobby options are returned for a player."""
        opts = self.ops.get_lobby_options(self.players[0])
        self.assertIsInstance(opts, list)
        self.assertGreater(len(opts), 0)
        for o in opts:
            self.assertIn('id', o)
            self.assertIn('label', o)
            self.assertIn('requires_target', o)

    def test_try_lobby_insufficient_amount(self):
        """Lobby with amount below minimum fails."""
        r = self.ops.try_lobby(self.players[0], 1, option_id='drop_excuse')
        self.assertFalse(r['success'])
        self.assertIn('message', r)

    def test_try_lobby_success_drop_excuse(self):
        """Lobby drop_excuse can succeed with enough money."""
        from lawsuit import LAWSUIT_EXCUSES
        self.players[0].add_lawsuit_excuse(LAWSUIT_EXCUSES[0])
        self.players[0].add_money(500000)
        r = self.ops.try_lobby(self.players[0], 500000, option_id='drop_excuse')
        self.assertIn('success', r)
        self.assertIn('message', r)

    def test_try_file_lawsuit_self_target_fails(self):
        """Cannot sue yourself."""
        r = self.ops.try_file_lawsuit(self.players[0], self.players[0], excuse=None, made_up_cause="Test")
        self.assertFalse(r['success'])
        self.assertIn('yourself', r.get('message', '').lower())

    def test_try_file_lawsuit_success(self):
        """File lawsuit between two players succeeds."""
        self.players[0].add_money(100000)
        self.players[1].add_money(100000)
        r = self.ops.try_file_lawsuit(
            self.players[0], self.players[1],
            excuse=None, made_up_cause="Unfair Competition"
        )
        self.assertTrue(r['success'])
        self.assertIn('winner', r)
        self.assertIn('result_message', r)
        self.assertIn('plaintiff_name', r)
        self.assertIn('defendant_name', r)


class TestOperationsLobbyIntegration(unittest.TestCase):
    """Test lobby and lawsuit integration in operations."""

    def setUp(self):
        from operations import Operations
        self.ops = Operations(
            ["P1", "P2"],
            map_width=30,
            map_height=30,
            starting_money=200000,
            map_preset_name="standard"
        )
        self.p1, self.p2 = self.ops.get_players()

    def test_get_lobby_success_chance_increases_with_amount(self):
        """Higher lobby amount gives higher success chance."""
        c1 = self.ops.get_lobby_success_chance(10000)
        c2 = self.ops.get_lobby_success_chance(100000)
        c3 = self.ops.get_lobby_success_chance(500000)
        self.assertLessEqual(c1, c2)
        self.assertLessEqual(c2, c3)
        self.assertLessEqual(c3, 0.95)

    def test_get_lobby_minimum_cost_positive(self):
        """Minimum lobby cost is positive."""
        cost = self.ops.get_lobby_minimum_cost()
        self.assertGreater(cost, 0)


if __name__ == '__main__':
    unittest.main()
