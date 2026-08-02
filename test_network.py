"""Unit and integration tests for TransitGame networking."""
import base64
import importlib.util
import os
import pickle
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_qt_app = QApplication.instance()
if _qt_app is None:
    _qt_app = QApplication(sys.argv)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestParseServerAddress(unittest.TestCase):
    def test_localhost_default_port(self):
        from message_protocol import parse_server_address

        self.assertEqual(parse_server_address("localhost"), "http://localhost:8080")

    def test_ip_only(self):
        from message_protocol import parse_server_address

        self.assertEqual(parse_server_address("192.168.1.42"), "http://192.168.1.42:8080")

    def test_ip_with_port_in_host(self):
        from message_protocol import parse_server_address

        self.assertEqual(parse_server_address("192.168.1.42:9090"), "http://192.168.1.42:9090")

    def test_ip_with_explicit_port_argument(self):
        from message_protocol import parse_server_address

        self.assertEqual(parse_server_address("10.0.0.5", port=3000), "http://10.0.0.5:3000")

    def test_full_url_preserved(self):
        from message_protocol import parse_server_address

        self.assertEqual(
            parse_server_address("http://game.example.com:7777"),
            "http://game.example.com:7777",
        )

    def test_empty_address_raises(self):
        from message_protocol import parse_server_address

        with self.assertRaises(ValueError):
            parse_server_address("   ")

    def test_invalid_address_raises(self):
        from message_protocol import parse_server_address

        with self.assertRaises(ValueError):
            parse_server_address("not a valid host!!!")


class TestMessageProtocol(unittest.TestCase):
    def test_build_line_action_to_dict(self):
        from message_protocol import ActionType, BuildLineAction

        action = BuildLineAction(name="A Line", points=[(0, 0), (1, 1)], construction_workers=2)
        payload = action.to_dict()
        self.assertEqual(payload["action_type"], ActionType.BUILD_LINE.value)
        self.assertEqual(payload["action_data"]["name"], "A Line")
        self.assertEqual(payload["action_data"]["points"], [(0, 0), (1, 1)])

    def test_game_state_message_from_dict(self):
        from message_protocol import GameStateMessage

        msg = GameStateMessage.from_dict({
            "game_data": "abc",
            "year": 1840,
            "is_playing": True,
            "num_players": 2,
            "active_players": 2,
        })
        self.assertEqual(msg.year, 1840)
        self.assertTrue(msg.is_playing)
        self.assertEqual(msg.num_players, 2)


class TestNetworkClient(unittest.TestCase):
    def test_client_normalizes_ip_address(self):
        from network_client import NetworkClient

        client = NetworkClient("192.168.0.10", port=8080)
        self.assertEqual(client.server_url, "http://192.168.0.10:8080")

    def test_client_normalizes_full_url(self):
        from network_client import NetworkClient

        client = NetworkClient("http://remote.host:9999")
        self.assertEqual(client.server_url, "http://remote.host:9999")

    def test_disconnect_clears_state(self):
        from network_client import NetworkClient

        client = NetworkClient("localhost")
        client.connected_flag = True
        client.session_id = "abc"
        client.player_id = 1
        client.sio = object()
        emitted = []
        client.disconnected.connect(lambda: emitted.append(True))
        client.disconnect_from_server()
        self.assertFalse(client.connected_flag)
        self.assertIsNone(client.session_id)
        self.assertIsNone(client.player_id)
        self.assertIsNone(client.sio)
        self.assertEqual(emitted, [True])


class TestServerSerialization(unittest.TestCase):
    def test_serialize_and_deserialize_round_trip(self):
        from operations import Operations
        from server import serialize_game_state

        ui = Operations(
            ["Alice", "Bob"],
            map_width=30,
            map_height=30,
            starting_money=50000,
            map_preset_name="standard",
            random_ai_spawn_enabled=False,
        )
        game = ui.get_game()
        state = serialize_game_state(game)
        self.assertIn("game_data", state)
        self.assertEqual(state["num_players"], 2)
        raw = base64.b64decode(state["game_data"].encode("utf-8"))
        restored = pickle.loads(raw)
        self.assertEqual(len(restored.get_players()), 2)


@unittest.skipUnless(
    all(importlib.util.find_spec(m) for m in ("socketio", "aiohttp")),
    "socketio/aiohttp not installed",
)
class TestServerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server_proc = subprocess.Popen(
            [sys.executable, "server.py", str(cls.port)],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                import urllib.request

                with urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            cls.server_proc.kill()
            raise RuntimeError("Server did not become ready in time")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server_proc", None):
            cls.server_proc.terminate()
            try:
                cls.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.server_proc.kill()

    def test_socketio_client_connects_by_ip(self):
        import socketio

        client = socketio.Client()
        connected = {}

        @client.event
        def connect():
            connected["ok"] = True

        client.connect(f"http://127.0.0.1:{self.port}", transports=["websocket"], wait_timeout=5)
        try:
            self.assertTrue(connected.get("ok"))
        finally:
            client.disconnect()

    def test_network_client_connects_by_ip(self):
        from PyQt6.QtCore import QCoreApplication
        from network_client import NetworkClient

        client = NetworkClient(f"127.0.0.1:{self.port}")
        events = []
        client.connected.connect(lambda _data: events.append("connected"))
        client.connect_to_server()
        try:
            QCoreApplication.processEvents()
            self.assertTrue(client.connected_flag)
        finally:
            client.disconnect_from_server()


class TestNetworkedUI(unittest.TestCase):
    def setUp(self):
        if QApplication.instance() is None:
            QApplication(sys.argv)

    def test_networked_main_window_has_ip_and_port_inputs(self):
        from qt_networked import NetworkedMainWindow

        w = NetworkedMainWindow(network_mode=True)
        try:
            self.assertTrue(hasattr(w, "server_host_input"))
            self.assertTrue(hasattr(w, "server_port_spin"))
            self.assertEqual(w.server_port_spin.value(), 8080)
        finally:
            w.close()

    def test_get_server_url_from_inputs(self):
        from qt_networked import NetworkedMainWindow

        w = NetworkedMainWindow(network_mode=True)
        try:
            w.server_host_input.setText("192.168.5.10")
            w.server_port_spin.setValue(3000)
            self.assertEqual(w._get_server_url_from_inputs(), "http://192.168.5.10:3000")
        finally:
            w.close()

    def test_cleanup_network_disconnects_client(self):
        from qt_networked import MainWindow
        from network_client import NetworkClient

        w = MainWindow()
        client = NetworkClient("localhost")
        client.connected_flag = True
        w.network_client = client
        w._cleanup_network()
        self.assertIsNone(w.network_client)
        self.assertFalse(client.connected_flag)
        w.close()

    def test_exit_to_main_menu_opens_game_mode_dialog(self):
        from PyQt6.QtWidgets import QDialog
        from qt_networked import MainWindow, GameModeDialog

        w = MainWindow()
        with patch.object(GameModeDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            with patch.object(QApplication.instance(), "quit") as mock_quit:
                w._exit_to_main_menu(skip_confirm=True)
                mock_quit.assert_called_once()
        w.close()


if __name__ == "__main__":
    unittest.main()
