# Creating Custom Map Presets

You can now create your own map files without modifying the main code! The game will automatically discover and load any `map_presets_*.py` files in the project directory.

## Quick Start

1. **Copy the template file:**
   ```bash
   cp map_preset_template.py map_presets_mycity.py
   ```

2. **Edit `map_presets_mycity.py`** and fill in:
   - `MAP_NAME`: Display name for your map
   - `MAP_ID`: Unique identifier (should match filename: `map_presets_<MAP_ID>.py`)
   - `MAP_BOUNDS`: Geographic bounds and grid dimensions
   - `MAP_TIMELINE`: Start and end years
   - `MAP_WATER_AREAS`: List of water areas (rectangular regions)
   - `MAP_POPULATION`: Population settings
   - `get_population_at_location()`: Function to calculate population at each location
   - `is_water_location()`: Function to check if a location is water
   - `is_mycity_map()`: Function to detect if coordinates match your map

3. **The map will be automatically discovered** when you start the game!

## Required Variables and Functions

### Required Variables

- `MAP_BOUNDS`: Dictionary with:
  - `center_lat`, `center_lon`: Center coordinates
  - `min_lat`, `max_lat`, `min_lon`, `max_lon`: Bounds
  - `width`, `height`: Grid dimensions

- `MAP_TIMELINE`: Dictionary with:
  - `start_year`: Starting year (e.g., 1830)
  - `end_year`: Ending year (e.g., 2050)

- `MAP_ZOOM`: Dictionary with:
  - `default_zoom`: Default zoom level
  - `min_zoom`, `max_zoom`: Zoom limits

- `MAP_WATER_AREAS`: List of dictionaries, each with:
  - `min_lat`, `max_lat`, `min_lon`, `max_lon`: Water area bounds

- `MAP_POPULATION`: Dictionary with population settings

### Required Functions

- `is_water_location(lat, lon, osm_integration=None)`: Check if location is water
- `get_population_at_location(lat, lon, is_water, x, y, width, height)`: Get population at a location
- `is_<MAP_ID>_map(map_center_lat, map_center_lon, width, height)`: Detect if coordinates match your map

### Optional Helper Functions (for compatibility)

- `get_<MAP_ID>_bounds()`: Return `MAP_BOUNDS`
- `get_<MAP_ID>_timeline()`: Return `MAP_TIMELINE`
- `get_<MAP_ID>_zoom()`: Return `MAP_ZOOM`
- `get_<MAP_ID>_population_settings()`: Return `MAP_POPULATION`

## Example

See `map_preset_template.py` for a complete example with all required fields filled in.

## How It Works

1. The `map_preset_loader.py` module automatically scans for `map_presets_*.py` files
2. Each preset file is validated to ensure it has the required functions/variables
3. When creating a map, the game checks all available presets to find a match
4. If a match is found, the preset's settings are used automatically

## Notes

- Preset files must follow the naming convention: `map_presets_<name>.py`
- The `MAP_ID` should match the filename (e.g., `map_presets_mycity.py` → `MAP_ID = "mycity"`)
- Template files and backup files (containing "template" or ending with "_backup") are automatically skipped
- The game maintains backward compatibility with hardcoded presets (NYC, NY Metro, Standard)

## Troubleshooting

If your custom map isn't being discovered:

1. Check that the filename follows `map_presets_<name>.py` format
2. Ensure all required variables and functions are defined
3. Check the console output for discovery messages
4. Verify that `is_<name>_map()` correctly detects your map's coordinates
