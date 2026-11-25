"""Land/water and boundary data shared by the New York area maps (NYC and NY Metro).

Not a playable map: the presets import ``WATER_MASK`` from here.

Sources in ``ny_metro_osm_relation_polygons.json``:

* ``metro_coastline``: OSM coastline ways (ocean, bays, Long Island Sound, the tidal
  Hudson and East River).
* ``metro_natural_water``: ``natural=water`` polygons for lakes, reservoirs and rivers.
* ``metro_river_areas``: ``water=river`` areas for the non-tidal Hudson north of ~41.3 N,
  where the OSM coastline ends.
* Named boundaries: ``nj_state``, ``long_island_rings``, ``ct_state_rings``.
"""
import os

from water_mask import WaterMask

_DIR = os.path.dirname(os.path.abspath(__file__))

WATER_MASK = WaterMask(
    data_file=os.path.join(_DIR, 'ny_metro_osm_relation_polygons.json'),
    # (south, west, north, east) of the Overpass query that produced metro_coastline.
    coast_bbox=(40.40, -74.53, 41.85, -71.90),
    coastline_key='metro_coastline',
    water_keys=('metro_natural_water', 'metro_river_areas'),
    index_cache=os.path.join(_DIR, '.water_mask_index.pkl'),
)
