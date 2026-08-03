#!/usr/bin/env python3
"""
Example integration of networking into the PyQt6 UI
This shows how to integrate NetworkClient with the existing MainWindow
"""
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel, QLineEdit, QMessageBox
from PyQt6.QtCore import Qt
from network_client import NetworkClient, NetworkThread
from qt import MainWindow as GameMainWindow


class NetworkSetupWindow(QMainWindow):
    """Window for connecting to server and setting up multiplayer game"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GoCommuter - Network Setup")
        self.setGeometry(100, 100, 500, 400)
        
        # Network client
        self.network_client = NetworkClient("http://localhost:8080")
        self.network_thread = None
        
        # Setup UI
        self.setup_ui()
        
        # Connect signals
        self.network_client.connected.connect(self.on_connected)
        self.network_client.disconnected.connect(self.on_disconnected)
        self.network_client.game_created.connect(self.on_game_created)
        self.network_client.game_state_received.connect(self.on_game_state_received)
        self.network_client.error_received.connect(self.on_error)
        self.network_client.player_joined.connect(self.on_player_joined)
    
    def setup_ui(self):
        """Setup the UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # Server URL input
        layout.addWidget(QLabel("Server URL:"))
        self.server_url_input = QLineEdit("http://localhost:8080")
        layout.addWidget(self.server_url_input)
        
        # Connect button
        self.connect_btn = QPushButton("Connect to Server")
        self.connect_btn.clicked.connect(self.connect_to_server)
        layout.addWidget(self.connect_btn)
        
        # Status label
        self.status_label = QLabel("Not connected")
        layout.addWidget(self.status_label)
        
        # Player name input
        layout.addWidget(QLabel("Your Name:"))
        self.player_name_input = QLineEdit("Player1")
        layout.addWidget(self.player_name_input)
        
        # Create game section
        layout.addWidget(QLabel("Create New Game:"))
        self.create_game_btn = QPushButton("Create Game")
        self.create_game_btn.clicked.connect(self.create_game)
        self.create_game_btn.setEnabled(False)
        layout.addWidget(self.create_game_btn)
        
        # Join game section
        layout.addWidget(QLabel("Join Existing Game:"))
        self.session_id_input = QLineEdit()
        self.session_id_input.setPlaceholderText("Enter session ID")
        layout.addWidget(self.session_id_input)
        
        self.join_game_btn = QPushButton("Join Game")
        self.join_game_btn.clicked.connect(self.join_game)
        self.join_game_btn.setEnabled(False)
        layout.addWidget(self.join_game_btn)
        
        # Start game button (appears after game is ready)
        self.start_game_btn = QPushButton("Start Game")
        self.start_game_btn.clicked.connect(self.start_game)
        self.start_game_btn.setEnabled(False)
        self.start_game_btn.hide()
        layout.addWidget(self.start_game_btn)
    
    def connect_to_server(self):
        """Connect to the game server"""
        server_url = self.server_url_input.text()
        self.network_client.server_url = server_url
        
        # Start network thread
        self.network_thread = NetworkThread(self.network_client)
        self.network_thread.start()
        
        self.status_label.setText("Connecting...")
        self.connect_btn.setEnabled(False)
    
    def on_connected(self, data):
        """Handle connection success"""
        self.status_label.setText("Connected to server")
        self.create_game_btn.setEnabled(True)
        self.join_game_btn.setEnabled(True)
    
    def on_disconnected(self):
        """Handle disconnection"""
        self.status_label.setText("Disconnected from server")
        self.create_game_btn.setEnabled(False)
        self.join_game_btn.setEnabled(False)
        self.start_game_btn.setEnabled(False)
        self.start_game_btn.hide()
    
    def create_game(self):
        """Create a new game"""
        player_name = self.player_name_input.text()
        if not player_name:
            QMessageBox.warning(self, "Error", "Please enter your name")
            return
        
        # For now, create a 2-player game
        # In a full implementation, you'd have a dialog to set up game parameters
        player_names = [player_name, "Player2"]  # Second player will join
        
        self.network_client.create_game(
            player_names=player_names,
            map_width=100,
            map_height=75,
            starting_money=20000,
            worker_replenishment_rate=1.0
        )
        
        self.status_label.setText("Creating game...")
    
    def join_game(self):
        """Join an existing game"""
        session_id = self.session_id_input.text()
        if not session_id:
            QMessageBox.warning(self, "Error", "Please enter a session ID")
            return
        
        self.network_client.join_game(session_id)
        self.status_label.setText(f"Joining game {session_id}...")
    
    def on_game_created(self, data):
        """Handle game creation"""
        session_id = data.get('session_id')
        self.status_label.setText(f"Game created! Session ID: {session_id}")
        self.session_id_input.setText(session_id)
        # Don't start game yet - wait for other players
    
    def on_game_state_received(self, data):
        """Handle game state update"""
        # Game state received - could update UI here
        # For now, just show that we're ready
        game_state = data.get('game_state', {})
        year = game_state.get('year', 1830)
        self.status_label.setText(f"Game state received. Year: {year}")
        
        # If game is ready, show start button
        # (In a real implementation, you'd check if all players are ready)
        # self.start_game_btn.show()
        # self.start_game_btn.setEnabled(True)
    
    def on_player_joined(self, data):
        """Handle player joining"""
        player_name = data.get('player_name')
        total_players = data.get('total_players')
        self.status_label.setText(f"{player_name} joined. Total players: {total_players}")
        
        # If enough players, enable start button
        if total_players >= 2:
            self.start_game_btn.show()
            self.start_game_btn.setEnabled(True)
    
    def on_error(self, message):
        """Handle error"""
        QMessageBox.critical(self, "Error", message)
        self.status_label.setText(f"Error: {message}")
    
    def start_game(self):
        """Start the game (open main game window)"""
        # Deserialize game state and create game window
        # This is a simplified example - you'd need to properly integrate with your UI
        
        # For now, just show a message
        QMessageBox.information(self, "Game Starting", 
                               "Game window would open here. "
                               "Full integration requires syncing NetworkClient with MainWindow.")
        
        # In full implementation:
        # 1. Get game state from network_client
        # 2. Deserialize it
        # 3. Create MainWindow with networked game
        # 4. Connect action handlers to network_client.send_action()
        # 5. Update UI when game_state_received signal fires


def main():
    """Example main function"""
    app = QApplication(sys.argv)
    window = NetworkSetupWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

