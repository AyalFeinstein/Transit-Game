#!/usr/bin/env python3
"""Add Governors Island (OSM relation 2389631) cells to NYC_INITIAL_POPULATION."""

from __future__ import annotations

import hashlib
import os

from generate_ny_metro_population_points import fetch_osm_boundary_polygons_via_api, point_in_polygon
from map_preset_utils import write_initial_population_to_file
from map_presets_nyc import (
    GOVERNORS_ISLAND_OSM_RELATION_ID,
    NYC_INITIAL_POPULATION,
    get_nyc_bounds,
)


def _cell_intersects_polygon(
    x: int,
    y: int,
    bounds: dict,
    polygon: list[tuple[float, float]],
    *,
    width: int = 50,
    height: int = 50,
    subgrid_size: int = 11,
) -> bool:
    lat = bounds['min_lat'] + (y / height) * (bounds['max_lat'] - bounds['min_lat'])
    lon = bounds['min_lon'] + (x / width) * (bounds['max_lon'] - bounds['min_lon'])
    cell_size_lat = (bounds['max_lat'] - bounds['min_lat']) / height
    cell_size_lon = (bounds['max_lon'] - bounds['min_lon']) / width
    half_lat = cell_size_lat / 2
    half_lon = cell_size_lon / 2

    if point_in_polygon((lon, lat), polygon):
        return True

    corners = [
        (lon - half_lon, lat - half_lat),
        (lon + half_lon, lat - half_lat),
        (lon - half_lon, lat + half_lat),
        (lon + half_lon, lat + half_lat),
    ]
    if any(point_in_polygon(corner, polygon) for corner in corners):
        return True

    sub_lat_step = cell_size_lat / subgrid_size
    sub_lon_step = cell_size_lon / subgrid_size
    for i in range(subgrid_size):
        for j in range(subgrid_size):
            sub_lat = lat - half_lat + (i + 0.5) * sub_lat_step
            sub_lon = lon - half_lon + (j + 0.5) * sub_lon_step
            if point_in_polygon((sub_lon, sub_lat), polygon):
                return True
    return False


def _deterministic_gi_population(x: int, y: int) -> int:
    """Stable 1830 population for a Governors Island grid cell."""
    digest = hashlib.md5(f'gi-{GOVERNORS_ISLAND_OSM_RELATION_ID}-{x}-{y}'.encode()).hexdigest()
    value = int(digest[:8], 16)
    return 50 + (value % 451)


def main() -> None:
    bounds = get_nyc_bounds()
    width = int(bounds.get('width', 50))
    height = int(bounds.get('height', 50))
    rings = fetch_osm_boundary_polygons_via_api(GOVERNORS_ISLAND_OSM_RELATION_ID)
    if not rings:
        raise SystemExit(f'Could not fetch OSM relation {GOVERNORS_ISLAND_OSM_RELATION_ID}')

    polygon = max(rings, key=len)
    gi_cells: list[tuple[int, int]] = []
    for x in range(width + 1):
        for y in range(height + 1):
            if _cell_intersects_polygon(x, y, bounds, polygon, width=width, height=height):
                gi_cells.append((x, y))

    population = dict(NYC_INITIAL_POPULATION)
    added = 0
    for x, y in gi_cells:
        if population.get((x, y), 0) > 0:
            continue
        population[(x, y)] = _deterministic_gi_population(x, y)
        added += 1

    print(f'Governors Island cells: {len(gi_cells)} ({added} newly added)')
    for x, y in sorted(gi_cells):
        print(f'  ({x}, {y}): {population[(x, y)]}')

    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, 'map_presets_nyc.py')
    if not write_initial_population_to_file(file_path, 'NYC_INITIAL_POPULATION', population):
        raise SystemExit('Failed to write NYC_INITIAL_POPULATION')
    print('Updated map_presets_nyc.py')


if __name__ == '__main__':
    main()
