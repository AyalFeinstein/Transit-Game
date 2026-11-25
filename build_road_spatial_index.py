#!/usr/bin/env python3
"""Build and save the NYC road spatial index to a pickle file.

Run once after updating OSM roads. The pre-built index loads in ~4s vs ~25s
build at runtime, dramatically speeding up AI turns on NYC/NY Metro maps.

Usage: python build_road_spatial_index.py

Output: nyc_road_spatial_index.pkl (~48MB) - auto-loaded by map_presets_nyc_road_utils
"""
import os
import pickle
import sys

_CELL_SIZE = 0.02


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base, 'nyc_historical_roads_osm_generated.json')
    out_path = os.path.join(base, 'nyc_road_spatial_index.pkl')

    if not os.path.exists(json_path):
        print(f"JSON not found: {json_path}", file=sys.stderr)
        return 1

    from map_presets_nyc import get_nyc_bounds
    from collections import defaultdict

    print("Loading roads...")
    try:
        import orjson
        with open(json_path, 'rb') as f:
            roads = orjson.loads(f.read())
    except ImportError:
        import json
        with open(json_path, 'r', encoding='utf-8') as f:
            roads = json.load(f)

    if not roads or not any(r.get('route') and len(r.get('route', [])) >= 2 for r in roads.values()):
        print("No valid roads with routes", file=sys.stderr)
        return 1

    b = get_nyc_bounds()
    map_cell_min = (int(b['min_lat'] / _CELL_SIZE), int(b['min_lon'] / _CELL_SIZE))
    map_cell_max = (int(b['max_lat'] / _CELL_SIZE), int(b['max_lon'] / _CELL_SIZE))

    print("Building index...")
    road_idx = defaultdict(list)
    bridge_idx = defaultdict(list)
    n_roads = 0
    for road_name, road in roads.items():
        route = road.get('route', [])
        if len(route) < 2:
            continue
        rc = road.get('bounds')
        if rc:
            rc_min = (int(rc['min_lat'] / _CELL_SIZE), int(rc['min_lon'] / _CELL_SIZE))
            rc_max = (int(rc['max_lat'] / _CELL_SIZE), int(rc['max_lon'] / _CELL_SIZE))
        else:
            r_lat = [r[0] for r in route]
            r_lon = [r[1] for r in route]
            rc_min = (int(min(r_lat) / _CELL_SIZE), int(min(r_lon) / _CELL_SIZE))
            rc_max = (int(max(r_lat) / _CELL_SIZE), int(max(r_lon) / _CELL_SIZE))
        if rc_max[0] < map_cell_min[0] or rc_min[0] > map_cell_max[0] or rc_max[1] < map_cell_min[1] or rc_min[1] > map_cell_max[1]:
            continue
        n_roads += 1
        is_bridge = 'bridge' in road_name.lower()
        for i in range(len(route) - 1):
            a_lat, a_lon = route[i][0], route[i][1]
            b_lat, b_lon = route[i + 1][0], route[i + 1][1]
            cells = {(int(a_lat / _CELL_SIZE), int(a_lon / _CELL_SIZE)), (int(b_lat / _CELL_SIZE), int(b_lon / _CELL_SIZE))}
            for key in cells:
                road_idx[key].append((road_name, road, i))
                if is_bridge:
                    bridge_idx[key].append((road_name, road, i))

    # Convert to dict for pickle (defaultdict not needed when loading)
    road_idx = dict(road_idx)
    bridge_idx = dict(bridge_idx)
    n_cells = len(road_idx)

    print(f"Indexed {n_roads} roads, {n_cells} cells")
    with open(out_path, 'wb') as f:
        pickle.dump({'road': road_idx, 'bridge': bridge_idx, '_CELL_SIZE': _CELL_SIZE}, f, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"Saved to {out_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
