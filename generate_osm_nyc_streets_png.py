#!/usr/bin/env python3
"""Generate a PNG map of all OSM polyline streets in the NYC map.
Requires: pip install matplotlib
Output: nyc_osm_streets.png in the project root.
Renders water bodies and streets to resemble an actual map."""

import os
import sys

# Add project root for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
except ImportError:
    print("matplotlib is required. Install with: pip install matplotlib")
    sys.exit(1)


def load_osm_roads():
    """Load OSM roads from JSON. Prefer orjson for speed."""
    json_path = os.path.join(os.path.dirname(__file__), 'nyc_historical_roads_osm_generated.json')
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"OSM roads file not found: {json_path}")
    try:
        import orjson
        with open(json_path, 'rb') as f:
            return orjson.loads(f.read())
    except ImportError:
        import json
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)


def main():
    from map_presets_nyc import get_nyc_bounds, NYC_WATER_AREAS

    bounds = get_nyc_bounds()
    min_lat = bounds['min_lat']
    max_lat = bounds['max_lat']
    min_lon = bounds['min_lon']
    max_lon = bounds['max_lon']

    roads = load_osm_roads()
    fig, ax = plt.subplots(figsize=(14, 12), facecolor='#f5f0e6')
    ax.set_facecolor('#f5f0e6')
    ax.set_aspect('equal')

    # Draw water bodies first (Hudson, East River, bays, etc.) so land shape is recognizable
    for w in NYC_WATER_AREAS:
        rect = Rectangle(
            (w['min_lon'], w['min_lat']),
            w['max_lon'] - w['min_lon'],
            w['max_lat'] - w['min_lat'],
            facecolor='#c8e4f0',
            edgecolor='#a0d0e0',
            linewidth=0.5,
            zorder=0
        )
        ax.add_patch(rect)

    segments = []
    for road_name, road in roads.items():
        route = road.get('route', [])
        if len(route) < 2:
            continue
        b = road.get('bounds')
        if b:
            if b.get('max_lat', 0) < min_lat or b.get('min_lat', 0) > max_lat:
                continue
            if b.get('max_lon', 0) < min_lon or b.get('min_lon', 0) > max_lon:
                continue
        for i in range(len(route) - 1):
            p0, p1 = route[i], route[i + 1]
            segments.append([(p0[1], p0[0]), (p1[1], p1[0])])

    # Thin, semi-transparent lines so streets don't merge into solid black
    lc = LineCollection(
        segments,
        colors='#2a2a2a',
        linewidths=0.06,
        alpha=0.22,
        zorder=2
    )
    ax.add_collection(lc)

    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')
    ax.set_title(f'OSM NYC Streets ({len(segments):,} segments)')
    ax.tick_params(axis='both', which='major', labelsize=9)
    plt.tight_layout()

    out_path = os.path.join(os.path.dirname(__file__), 'nyc_osm_streets.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='#f5f0e6')
    plt.close()
    print(f"Saved {out_path} ({len(segments):,} segments)")


if __name__ == '__main__':
    main()
