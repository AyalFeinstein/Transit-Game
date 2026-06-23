# NYC Historical Roads Database - OSM Generated

## Overview
The NYC historical roads database has been regenerated from actual OpenStreetMap (OSM) Ways data. This provides a comprehensive, accurate representation of all streets within the NYC grid bounds.

## Statistics
- **Total Roads**: 19,853 named streets
- **Source**: OpenStreetMap Overpass API
- **Coverage**: All five NYC boroughs (Manhattan, Brooklyn, Queens, Bronx, Staten Island)
- **Bounds**: 
  - Latitude: 40.49° to 40.92°
  - Longitude: -74.30° to -73.70°

## Road Distribution by Estimated Construction Year
- **1600** (Pre-colonial major routes): 587 roads
- **1700** (Colonial era): 1,832 roads  
- **1800** (Early 19th century): 17,426 roads
- **Modern roads** (1944-2023): 8 roads

## File Information
- **Generated File**: `nyc_historical_roads_osm_generated.py`
- **JSON Backup**: `nyc_historical_roads_osm_generated.json`
- **Integrated Into**: `map_presets_nyc.py` (replaced `NYC_HISTORICAL_ROADS` dictionary)
- **Backup**: `map_presets_nyc.py.backup` (original version preserved)

## Generation Script
The roads were generated using `generate_nyc_historical_roads_from_osm.py`, which:
1. Fetches ALL highway ways from OSM within NYC bounds
2. Groups ways by street name
3. Merges multiple way segments for the same street
4. Estimates construction year from OSM tags (start_date, historic:date) or defaults
5. Calculates bounding boxes for each street
6. Formats as Python dictionary

## Road Data Structure
Each road entry follows this format:
```python
'Street_Name': {
    'year': 1800,  # Construction year (estimated)
    'removed_year': None,  # Removal year (None = never removed)
    'bounds': {
        'min_lat': 40.763539,
        'max_lat': 40.763641,
        'min_lon': -74.080512,
        'max_lon': -74.080430
    }
}
```

## Usage
The roads are automatically used by `is_point_on_road_at_year()` function in `map_presets_nyc.py`:
- Checks if a point (x, y) is on a road that exists at a given year
- Considers construction year and removal year
- Uses bounding box checks for efficiency

## Regenerating Roads
To regenerate the roads database:
```bash
python3 generate_nyc_historical_roads_from_osm.py
```

Configuration options in the script:
- `include_only_major_roads`: Set to `True` for only major roads (116 roads), `False` for all named streets (19,853 roads)
- `include_unnamed`: Set to `True` to include unnamed roads (not recommended - creates too many entries)

## Performance Considerations
- The database contains 19,853 roads, which is comprehensive but large
- Road lookups use bounding box checks for efficiency
- For better performance, consider using the major-roads-only version (116 roads) by setting `include_only_major_roads = True` in the generation script

## Notes
- Most roads are estimated to have existed before 1830 (game start year)
- Construction years are estimated from OSM tags when available, otherwise defaulted based on highway type
- All roads are marked as never removed (`removed_year: None`) unless OSM data indicates otherwise
- The database includes all named streets, from major highways to residential streets

## Verification
The integration has been tested and verified:
- ✅ File compiles without syntax errors
- ✅ Dictionary structure is correct
- ✅ All 19,853 roads imported successfully
- ✅ `is_point_on_road_at_year()` function works correctly

