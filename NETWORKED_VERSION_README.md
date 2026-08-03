# Networked Version of GoCommuter

## Overview

The networking functionality has been separated from `qt.py` into a new file `qt_networked.py`. This keeps the original `qt.py` clean and allows you to choose between local-only or networked gameplay.

## Files

- **`qt.py`** - Original UI (local gameplay only, no networking code)
- **`qt_networked.py`** - Networked version that extends `MainWindow` from `qt.py`
- **`main_networked.py`** - Entry point for the networked version
- **`main.py`** - Entry point for the local-only version

## Usage

### Local Game (Original)
```bash
python main.py
# or
python qt.py
```

### Networked Game (Multiplayer)
```bash
python main_networked.py
# or
python qt_networked.py
```

## How It Works

`NetworkedMainWindow` extends `MainWindow` from `qt.py` and:

1. **Adds network UI** to the game setup tab (checkbox, server URL, status)
2. **Overrides key methods** to add network support:
   - `on_start_game_clicked()` - Checks for network mode
   - `on_create_line_clicked()` - Sends build actions to server
   - `advance_year_button_clicked()` - Sends advance year to server
   - `buy_trainset_from_marketplace()` - Sends purchase to server
   - `buy_vehicle_from_marketplace()` - Sends purchase to server

3. **Handles network events**:
   - Connection/disconnection
   - Game creation
   - Game state updates
   - Player joins/leaves
   - Errors

## Features

- **Seamless integration**: Works with all existing UI elements
- **Backward compatible**: Falls back to local mode if networking unavailable
- **Clean separation**: Original `qt.py` remains unchanged
- **Easy to use**: Just check "Enable Multiplayer" checkbox

## Setup

1. Install networking dependencies:
   ```bash
   pip install aiohttp python-socketio
   ```

2. Start the server (in one terminal):
   ```bash
   python server.py
   ```

3. Run networked clients (in separate terminals):
   ```bash
   python main_networked.py
   ```

## Network Mode

When "Enable Multiplayer (Network Mode)" is checked:
- All game actions are sent to the server
- Game state is synchronized across all clients
- Server validates all actions
- UI updates automatically when state changes

When unchecked:
- Game runs locally (same as original `qt.py`)
- No network communication
- All actions processed immediately

## Architecture

```
qt.py (Base)
    ↓
qt_networked.py (Extends qt.py)
    ├── Adds network UI
    ├── Overrides action methods
    └── Handles network events
```

This design allows:
- Easy maintenance (base UI separate from networking)
- Optional networking (can use either version)
- Future extensions (can add more features to networked version)

