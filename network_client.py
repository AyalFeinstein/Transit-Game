#!/usr/bin/env python3
"""
Client-side networking module for TransitGame
Handles connection to game server and message passing
"""
import asyncio
import pickle
import base64
from typing import Optional, Callable, Dict, Any
import socketio
try:
    import socketio.exceptions
except ImportError:
    # Fallback for older versions
    socketio.exceptions = socketio
from PyQt6.QtCore import QObject, pyqtSignal, QThread
from game import Game


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
    
    def __init__(self, server_url: str = "http://localhost:8080"):
        super().__init__()
        self.server_url = server_url
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
            self.sio.connect(self.server_url, wait_timeout=5, transports=['websocket'])
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
        if self.sio and self.connected_flag:
            self.sio.disconnect()
            self.connected_flag = False
            self.session_id = None
            self.player_id = None
            self.disconnected.emit()
    
    def _setup_event_handlers(self):
        """Setup Socket.IO event handlers"""
        
        @self.sio.event
        def connect():
            self.connected.emit({'status': 'connected'})
        
        @self.sio.event
        def disconnect():
            self.connected_flag = False
            self.disconnected.emit()
        
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
    
    def create_game(self, player_names: list[str], map_width: int = 100, 
                   map_height: int = 75, starting_money: int = 20000,
                   worker_replenishment_rate: float = 1.0):
        """Create a new game session"""
        if not self.connected_flag:
            self.error_received.emit("Not connected to server")
            return
        
        self.sio.emit('create_game', {
            'player_names': player_names,
            'map_width': map_width,
            'map_height': map_height,
            'starting_money': starting_money,
            'worker_replenishment_rate': worker_replenishment_rate
        })
    
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
    
    def build_line(self, name: str, points: list, construction_workers: int = 0):
        """Build a new line"""
        self.send_action('build_line', {
            'name': name,
            'points': points,
            'construction_workers': construction_workers
        })
    
    def buy_trainset(self, item_id: str):
        """Buy a trainset from marketplace"""
        self.send_action('buy_trainset', {
            'item_id': item_id
        })
    
    def assign_trainset(self, trainset_index: int, line_name: str):
        """Assign a trainset to a line"""
        self.send_action('assign_trainset', {
            'trainset_index': trainset_index,
            'line_name': line_name
        })
    
    def sell_trainset(self, trainset_index: int):
        """Sell a trainset for half price"""
        self.send_action('sell_trainset', {
            'trainset_index': trainset_index
        })
    
    def rent_workers(self, worker_type_name: str, quantity: int):
        """Rent workers from the marketplace"""
        self.send_action('rent_workers', {
            'worker_type_name': worker_type_name,
            'quantity': quantity
        })
    
    def advance_year(self):
        """Advance the game year (legacy - redirects to end_year)"""
        self.end_year()
    
    def end_year(self):
        """Signal ready to end the year - year advances when all players are ready"""
        self.send_action('end_year', {})
    
    def end_turn(self):
        """End current turn (legacy - no longer used in simultaneous mode)"""
        self.send_action('end_turn', {})
    
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
    
    def start_game_on_server(self):
        """Request server to start the game"""
        if not self.connected_flag:
            self.error_received.emit("Not connected to server")
            return
        
        self.sio.emit('start_game', {})
    
    def deserialize_game_state(self, game_state_data: dict) -> Optional[Game]:
        """Deserialize game state from server format"""
        import sys
        try:
            print(f"Deserializing game state. Received keys: {list(game_state_data.keys()) if game_state_data else 'None'}", flush=True)
            sys.stdout.flush()
            
            game_data_b64 = game_state_data.get('game_data')
            if not game_data_b64:
                print(f"Error: No 'game_data' field in game_state_data. Keys: {list(game_state_data.keys()) if game_state_data else 'None'}")
                return None
            
            if not isinstance(game_data_b64, str):
                print(f"Error: game_data_b64 is not a string, got {type(game_data_b64)}")
                return None
            
            print(f"Received game_data_b64 length: {len(game_data_b64)} characters", flush=True)
            sys.stdout.flush()
            
            # Decode base64
            try:
                game_data = base64.b64decode(game_data_b64.encode('utf-8'))
                print(f"Decoded base64 to {len(game_data)} bytes", flush=True)
                sys.stdout.flush()
            except Exception as e:
                print(f"Error decoding base64: {e}")
                print(f"Base64 data length: {len(game_data_b64)}")
                print(f"First 100 chars of base64: {game_data_b64[:100]}")
                import traceback
                traceback.print_exc()
                return None
            
            # Deserialize pickle
            try:
                print("Attempting to unpickle game data...", flush=True)
                sys.stdout.flush()
                game = pickle.loads(game_data)
                print(f"Successfully unpickled game. Type: {type(game)}, Players: {len(game.get_players()) if hasattr(game, 'get_players') else 'N/A'}", flush=True)
                sys.stdout.flush()
                
                # Verify game object is valid
                if game is None:
                    print("ERROR: game object is None after unpickling!", flush=True)
                    return None
                if not hasattr(game, 'get_players'):
                    print(f"ERROR: game object missing get_players method! Attributes: {dir(game)}", flush=True)
                    return None
                try:
                    players = game.get_players()
                    print(f"Verified game object: {len(players)} players found", flush=True)
                except Exception as e:
                    print(f"ERROR: Failed to call get_players() on game object: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    return None
                
                print("Returning game object from deserialize_game_state", flush=True)
                sys.stdout.flush()
                return game
            except pickle.UnpicklingError as e:
                print(f"Unpickling error: {e}")
                print(f"Pickle data length: {len(game_data)} bytes")
                print(f"First 100 bytes (hex): {game_data[:100].hex()}")
                import traceback
                traceback.print_exc()
                return None
            except Exception as e:
                print(f"Error unpickling game data: {e}")
                print(f"Error type: {type(e)}")
                print(f"Pickle data length: {len(game_data)} bytes")
                import traceback
                traceback.print_exc()
                return None
        except Exception as e:
            print(f"Unexpected error deserializing game state: {e}")
            import traceback
            traceback.print_exc()
            return None


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
        while True:
            if self.client.connected_flag:
                time.sleep(0.5)
            else:
                # If not connected, check periodically if we should reconnect
                time.sleep(1)
                if not self.client.sio:
                    break

