# Map Preset Format

Map presets allow adding new maps to GoCommuter without modifying core files. Add a `map_presets_<name>.py` file to the project directory and it will be discovered automatically.

## File Naming

- **Pattern**: `map_presets_<preset_id>.py`
- **Examples**: `map_presets_nyc.py`, `map_presets_ny_metro.py`, `map_presets_standard.py`
- **Preset ID**: The part after `map_presets_` (e.g. `nyc`, `ny_metro`, `standard`)

Files with `template` or `_backup` in the name are skipped.

## Required Interface

Each preset must provide one of these:

### Option A: Template-style (MAP_BOUNDS + MAP_TIMELINE)

```python
MAP_BOUNDS = {
    'center_lat': float,
    'center_lon': float,
    'min_lat': float,   # optional for bounds check
    'max_lat': float,
    'min_lon': float,
    'max_lon': float,
    'width': int,
    'height': int,
}

MAP_TIMELINE = {
    'start_year': int,
    'end_year': int,
}
```

### Option B: Function-style

```python
def is_<preset_id>_map(map_center_lat, map_center_lon, width, height) -> bool:
    """Return True if the given params match this map."""
    ...

def get_<preset_id>_bounds() -> dict:
    """Return MAP_BOUNDS-style dict."""
    ...

def get_<preset_id>_timeline() -> dict:
    """Return MAP_TIMELINE-style dict."""
    ...
```

## Optional: Display Name

```python
def get_<preset_id>_map_display_name() -> str:
    """Return user-facing name (e.g. "New York City")."""
    return "My Map"
# or
MAP_NAME = "My Map"
```

## Optional: Center Coordinates

```python
def get_<preset_id>_default_center_lat() -> float:
    ...
def get_<preset_id>_default_center_lon() -> float:
    ...
```

## Usage in Game

- **operations.py**: Uses `get_game_params_for_preset(preset_name)` for all map logic.
- **qt.py**: Populates map type combo from `list_available_presets()` and `get_map_display_name()`.
- **No code changes** needed when adding or removing map files.

## Example: Minimal Preset

See `map_preset_template.py` for a full template.
