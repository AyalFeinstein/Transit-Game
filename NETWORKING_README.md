# GoCommuter Networking Guide

This guide explains how to use the networking features to play GoCommuter multiplayer over a network.

## Overview

The networking implementation uses **python-socketio** with **aiohttp** to provide WebSocket-based real-time communication between a game server and multiple clients.

## Architecture

- **Server** (`server.py`): Manages game sessions, validates actions, and broadcasts game state updates
- **Client** (`network_client.py`): Connects to server, sends actions, receives game state updates
- **Message Protocol** (`message_protocol.py`): Defines message structures for communication

## Installation

1. Install networking dependencies:
```bash
pip install -r requirements_network.txt
```

Or install manually:
```bash
pip install aiohttp python-socketio
```

## Running the Server

Start the game server on a machine that all players can connect to:

```bash
python server.py [port]
```

Default port is 8080. Example:
```bash
python server.py 8080
```

The server will:
- Accept client connections
- Manage game sessions
- Validate and process player actions
- Broadcast game state updates to all players

## Running the Client

### Option 1: Use the Example Network Setup Window

```bash
python network_example.py
```

This opens a window where you can:
- Connect to a server
- Create a new game
- Join an existing game

### Option 2: Integrate into Your Existing UI

To integrate networking into your existing `MainWindow`:

1. **Import the network client:**
```python
from network_client import NetworkClient, NetworkThread
```

2. **Create and connect the client:**
```python
self.network_client = NetworkClient("http://localhost:8080")
self.network_thread = NetworkThread(self.network_client)
self.network_thread.start()
```

3. **Connect signals:**
```python
self.network_client.game_state_received.connect(self.on_game_state_received)
self.network_client.error_received.connect(self.on_error)
```

4. **Send actions when player performs them:**
```python
# When player builds a line
self.network_client.build_line(name="Line1", points=[(10, 10), (20, 20)])

# When player advances year
self.network_client.advance_year()
```

5. **Update UI when game state is received:**
```python
def on_game_state_received(self, data):
    game_state = data.get('game_state', {})
    game = self.network_client.deserialize_game_state(game_state)
    # Update your UI with the new game state
```

## Game Flow

1. **Server Setup**: One player starts the server
2. **Create Game**: First player creates a game session with player names
3. **Join Game**: Other players join using the session ID
4. **Play**: Players take turns performing actions
5. **Synchronization**: Server broadcasts game state after each action

## Available Actions

The following actions can be sent to the server:

- `build_line(name, points, construction_workers)`: Build a new transit line
- `buy_trainset(item_id)`: Buy a trainset from marketplace
- `assign_trainset(trainset_index, line_name)`: Assign trainset to line
- `advance_year()`: Advance the game year (ends turn)
- `end_turn()`: End current turn without advancing year

## Message Protocol

Messages follow this structure:

**Client → Server:**
```python
{
    'session_id': 'uuid-string',
    'action_type': 'build_line',
    'action_data': {
        'name': 'Line1',
        'points': [(10, 10), (20, 20)],
        'construction_workers': 2
    }
}
```

**Server → Client:**
```python
{
    'game_state': {
        'game_data': 'base64-encoded-pickled-game',
        'year': 1830,
        'is_playing': True,
        'num_players': 2,
        'active_players': 2
    },
    'action_result': {
        'success': True,
        'message': 'Line built successfully'
    }
}
```

## Customization

### Changing Server Port

Edit `server.py`:
```python
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
```

Or pass port as argument:
```bash
python server.py 3000
```

### Changing Server URL

In client code:
```python
client = NetworkClient("http://your-server-ip:8080")
```

### Adding New Actions

1. Add action type to `ActionType` enum in `message_protocol.py`
2. Add handler function in `server.py`:
```python
async def handle_new_action(ui, player, action_data):
    # Your logic here
    return {'success': True, 'message': 'Action completed'}
```
3. Add case in `player_action` event handler
4. Add method to `NetworkClient` class

## Troubleshooting

### Connection Issues

- **"Connection refused"**: Server is not running or wrong URL/port
- **"Game session not found"**: Invalid session ID or session expired
- **"Not your turn"**: Trying to perform turn-based action out of turn

### Game State Issues

- **Deserialization errors**: Ensure all players have same game code versions
- **State out of sync**: Server should be authoritative - trust server state

### Performance

- **Large game states**: Current implementation uses pickle which can be slow for large games
  - Consider using more efficient serialization (msgpack, protobuf) for production
- **Network latency**: Actions are validated server-side, so there's a round-trip delay

## Security Considerations

⚠️ **Current implementation is for development/testing only!**

For production use, add:
- Authentication/authorization
- Input validation and sanitization
- Rate limiting
- Encryption (WSS instead of WS)
- Server-side game logic validation
- Anti-cheat measures

## Example Integration

See `network_example.py` for a complete example of:
- Connecting to server
- Creating/joining games
- Handling game state updates
- Sending player actions

## Next Steps

To fully integrate networking:

1. **Replace local game state with networked state**: Instead of creating game locally, receive from server
2. **Sync UI updates**: Update UI when `game_state_received` signal fires
3. **Handle disconnections**: Implement reconnection logic
4. **Add turn indicators**: Show whose turn it is
5. **Add chat**: Use socketio events for player chat
6. **Add spectator mode**: Allow observers to watch games

## Support

For issues or questions, check:
- Server logs for errors
- Client console output
- Network tab in browser (if using web client)

