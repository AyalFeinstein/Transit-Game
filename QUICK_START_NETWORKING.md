# Quick Start: Networking

## 1. Install Dependencies

```bash
pip install aiohttp python-socketio
```

## 2. Start the Server

In one terminal:
```bash
python server.py
# Or use the script:
./start_server.sh
```

Server will run on `http://localhost:8080`

## 3. Run Clients

In separate terminals (one per player):
```bash
python network_example.py
```

Or integrate into your existing UI (see `NETWORKING_README.md` for details).

## Basic Usage Example

```python
from network_client import NetworkClient, NetworkThread

# Create client
client = NetworkClient("http://localhost:8080")

# Start network thread
thread = NetworkThread(client)
thread.start()

# Wait for connection, then create/join game
client.create_game(player_names=["Player1", "Player2"])

# Send actions
client.build_line(name="Main Line", points=[(10, 10), (20, 20)])
client.advance_year()
```

## Testing Locally

1. Terminal 1: `python server.py`
2. Terminal 2: `python network_example.py` (Player 1 - create game)
3. Terminal 3: `python network_example.py` (Player 2 - join game)

## Network Architecture

```
┌─────────┐         ┌─────────┐
│ Client1 │────────▶│         │
└─────────┘         │ Server  │
                    │         │
┌─────────┐         │         │
│ Client2 │────────▶│         │
└─────────┘         └─────────┘
```

All clients connect to one server. Server manages game state and broadcasts updates.

## Common Issues

- **Import errors**: Install dependencies with `pip install -r requirements_network.txt`
- **Port already in use**: Change port: `python server.py 3000`
- **Connection refused**: Make sure server is running first
- **Game not found**: Check session ID is correct

