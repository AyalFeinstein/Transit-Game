#!/usr/bin/env python3
"""
Client-side networking module for GoCommuter
Handles connection to game server and message passing
"""
import importlib.util
import pickle
import base64
from typing import Optional, Dict, Any
import socketio
try:
    import socketio.exceptions
except ImportError:
    # Fallback for older versions
    socketio.exceptions = socketio
from PyQt6.QtCore import QObject, pyqtSignal, QThread
from game import Game
from message_protocol import parse_server_address


class NetworkClient(QObject):
    """Network client that integrates with PyQt6 event loop"""
    
    # Signals for UI updates
    connected = pyqtSignal(dict)  # Emitted when connected
    disconnected = pyqtSignal()  # Emitted when disconnected
    game_created = pyqtSignal(dict)  # Emitted when game is created
    game_state_received = pyqtSignal(dict)  # Emitted when game state is received
    player_joined = pyqtSignal(dict)  # Emitted when a player joins
    player_left = pyqtSignal(dict)  # Emitted when a player leaves
    action_result = pyqtSignal(dict)  # Emitted when action completes
    error_received = pyqtSignal(str)  # Emitted when error occurs
    settings_updated = pyqtSignal(dict)  # Emitted when settings are updated
    year_advanced = pyqtSignal(dict)  # Emitted when year advances
    end_year_status = pyqtSignal(dict)  # Emitted when end year status updates
    chat_received = pyqtSignal(dict)  # Emitted when chat message received {message, sender}
    
    def __init__(self, server_url: str = "http://localhost:8080", port: Optional[int] = None):
        super().__init__()
        self.server_url = parse_server_address(server_url, port=port)
        self.sio: Optional[socketio.Client] = None
        self.session_id: Optional[str] = None
        self.player_id: Optional[int] = None
        self.player_name: Optional[str] = None
        self.connected_flag = False
        
    def connect_to_server(self):
        """Connect to the game server"""
        try:
            # Use websocket transport only (avoids need for requests package for polling)
            self.sio = socketio.Client(reconnection=True, reconnection_attempts=3, reconnection_delay=1)
            self._setup_event_handlers()
            # Use wait_timeout to avoid hanging and specify websocket transport only
            self.sio.connect(
                self.server_url, wait_timeout=5,
                transports=['websocket', 'polling'] if importlib.util.find_spec('websocket') else ['polling'],
            )
            self.connected_flag = True
            print(f"Connected to server: {self.server_url}")
        except socketio.exceptions.ConnectionError as e:
            error_msg = (f"Could not connect to server at {self.server_url}.\n\n"
                        f"Make sure the server is running.\n\n"
                        f"To start the server, open a terminal and run:\n"
                        f"  python server.py\n\n"
                        f"Then try connecting again.")
            print(f"Connection error: {e}")
            self.error_received.emit(error_msg)
        except Exception as e:
            error_msg = f"Connection error: {str(e)}"
            print(f"Error connecting to server: {e}")
            import traceback
            traceback.print_exc()
            self.error_received.emit(error_msg)
    
    def disconnect_from_server(self):
        """Disconnect from the game server"""
        if self.sio:
            try:
                if self.connected_flag:
                    self.sio.disconnect()
            except Exception:
                pass
            self.sio = None
        self.connected_flag = False
        self.session_id = None
        self.player_id = None
        self.player_name = None
        self.disconnected.emit()
    
    def _setup_event_handlers(self):
        """Setup Socket.IO event handlers"""
        
        @self.sio.event
        def connect():
            self.connected.emit({'status': 'connected'})
        
        @self.sio.event
        def disconnect(*_reason):
            self.connected_flag = False
            try:
                self.disconnected.emit()
            except RuntimeError:
                pass  # the Qt object is already gone (application shutting down)
        
        @self.sio.on('connected')
        def on_connected(data):
            print(f"Server message: {data}")
        
        @self.sio.on('game_created')
        def on_game_created(data):
            print(f"Received game_created event, keys: {list(data.keys()) if data else 'None'}")
            self.session_id = data.get('session_id')
            self.player_id = data.get('player_id')
            self.player_name = data.get('player_name')
            
            # Validate game_state exists
            game_state = data.get('game_state')
            if not game_state:
                print("Error: No game_state in game_created data")
                self.error_received.emit("No game state received from server")
                return
            
            if 'game_data' not in game_state:
                print(f"Error: No 'game_data' in game_state. Keys: {list(game_state.keys())}")
                self.error_received.emit("Invalid game state format from server")
                return
            
            print(f"Game state received: {len(game_state.get('game_data', ''))} chars of game_data")
            self.game_created.emit(data)
        
        @self.sio.on('game_state')
        def on_game_state(data):
            self.game_state_received.emit(data)
        
        @self.sio.on('player_joined')
        def on_player_joined(data):
            self.player_joined.emit(data)
        
        @self.sio.on('player_left')
        def on_player_left(data):
            self.player_left.emit(data)
        
        @self.sio.on('action_failed')
        def on_action_failed(data):
            self.error_received.emit(data.get('message', 'Action failed'))
        
        @self.sio.on('error')
        def on_error(data):
            self.error_received.emit(data.get('message', 'Unknown error'))
        
        @self.sio.on('games_list')
        def on_games_list(data):
            # Handle games list response
            pass
        
        @self.sio.on('settings_updated')
        def on_settings_updated(data):
            self.settings_updated.emit(data)
        
        @self.sio.on('year_advanced')
        def on_year_advanced(data):
            self.year_advanced.emit(data)
        
        @self.sio.on('end_year_status')
        def on_end_year_status(data):
            self.end_year_status.emit(data)

        @self.sio.on('chat_message')
        def on_chat_message(data):
            self.chat_received.emit(data)

    def join_game(self, session_id: str):
        """Join an existing game session"""
        if not self.connected_flag:
            self.error_received.emit("Not connected to server")
            return
        
        self.sio.emit('join_game', {
            'session_id': session_id
        })
    
    def get_game_state(self):
        """Request current game state from server"""
        if not self.connected_flag or not self.session_id:
            self.error_received.emit("Not in a game session")
            return
        
        self.sio.emit('get_game_state', {
            'session_id': self.session_id
        })
    
    def send_action(self, action_type: str, action_data: Dict[str, Any]):
        """Send a player action to the server"""
        if not self.connected_flag or not self.session_id:
            self.error_received.emit("Not in a game session")
            return
        
        self.sio.emit('player_action', {
            'session_id': self.session_id,
            'action_type': action_type,
            'action_data': action_data
        })
    
    def end_year(self):
        """Signal ready to end the year - year advances when all players are ready"""
        self.send_action('end_year', {})

    def send_chat(self, message: str):
        """Send a chat message to the server"""
        if not self.connected_flag or not self.session_id:
            return
        self.sio.emit('chat', {
            'session_id': self.session_id,
            'message': message
        })
    
    def list_games(self):
        """Request list of available games"""
        if not self.connected_flag:
            self.error_received.emit("Not connected to server")
            return
        
        self.sio.emit('list_games', {})
    
    def update_setting(self, setting: str, value):
        """Update a game setting on the server"""
        if not self.connected_flag:
            return
        
        self.sio.emit('update_setting', {
            'setting': setting,
            'value': value
        })
    
    def start_game_on_server(self, player_names: list = None, operations_kwargs: dict = None):
        """Ask the server to start a game for everyone connected.

        ``operations_kwargs`` are the Operations settings (map preset, money, AI players, ...);
        connected clients take the human (non-AI) seats in connection order.
        """
        if not self.connected_flag:
            self.error_received.emit("Not connected to server")
            return
        self.sio.emit('start_game', {
            'player_names': list(player_names or []),
            'operations_kwargs': dict(operations_kwargs or {}),
        })
    
    def deserialize_game_state(self, game_state_data: dict) -> Optional[Game]:
        """The Game from a server game_state message, or None if it is missing or unreadable."""
        game_data_b64 = (game_state_data or {}).get('game_data')
        if not isinstance(game_data_b64, str) or not game_data_b64:
            print(f"Game state without game data (keys: {list((game_state_data or {}).keys())})")
            return None
        try:
            from network_rpc import unpack_game_state
            game = unpack_game_state(base64.b64decode(game_data_b64.encode('utf-8')))
        except Exception as e:
            import traceback
            print(f"Could not read game state from server: {e}")
            traceback.print_exc()
            return None
        if not hasattr(game, 'get_players'):
            print(f"Server sent a {type(game).__name__}, not a game")
            return None
        return game


class NetworkThread(QThread):
    """Thread for running network client (since socketio needs async event loop)"""
    
    def __init__(self, client: NetworkClient):
        super().__init__()
        self.client = client
    
    def run(self):
        """Run the network client in this thread"""
        # Connect to server - socketio handles its own event loop
        self.client.connect_to_server()
        
        # Keep thread alive - socketio will handle its own event loop
        # We just need to keep the thread running
        import time
        while self.client.sio is not None:
            time.sleep(0.5)
            if not self.client.connected_flag and self.client.sio is None:
                break

