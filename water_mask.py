"""Offline land/water test built from OpenStreetMap extracts (no network, pure Python).

A ``WaterMask`` is created by a map's own files, which say where the data is:

* a coastline key: OSM coastline ways.  By OSM convention land is on the LEFT of a coastline
  way, so the side of the nearest coastline segment tells sea from land.  This covers
  oceans, bays, sounds and tidal rivers.
* water keys: closed rings of inland water (lakes, reservoirs, non-tidal river areas).
* any other key may hold a named boundary, tested with ``in_region``.

Outside the box the coastline was fetched for, everything is reported as land.
"""
from __future__ import annotations

import json
import math
import os

_CELL_DEG = 0.01
_MAX_SEARCH_CELLS = 40          # ~40 km: farther from any coastline counts as land
_MAX_REGION_RING_POINTS = 4000


def _point_in_ring(lon: float, lat: float, ring) -> bool:
    inside = False
    xj, yj = ring[-1][0], ring[-1][1]
    for p in ring:
        xi, yi = p[0], p[1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        xj, yj = xi, yi
    return inside


class WaterMask:
    def __init__(self, data_file: str, coast_bbox: tuple, coastline_key: str,
                 water_keys: tuple = (), index_cache: str = None):
        """``coast_bbox`` is (south, west, north, east) of the query that produced the coastline."""
        self.data_file = data_file
        self.coast_bbox = tuple(coast_bbox)
        self.coastline_key = coastline_key
        self.water_keys = tuple(water_keys)
        self.index_cache = index_cache or os.path.join(
            os.path.dirname(os.path.abspath(data_file)), '.water_mask_index.pkl')
        self._state: dict | None = None
        self._regions: dict = {}

    def data_stamp(self) -> str:
        """Changes whenever the data file does (for callers that cache derived results)."""
        try:
            return str(os.path.getmtime(self.data_file))
        except OSError:
            return ''

    def _load(self) -> dict:
        if self._state is not None:
            return self._state
        # Built index is cached next to the data file (rebuilt when the data file changes).
        try:
            import pickle
            if os.path.getmtime(self.index_cache) >= os.path.getmtime(self.data_file):
                with open(self.index_cache, 'rb') as f:
                    segs, lakes = pickle.load(f)
                self._state = {'segs': segs, 'lakes': lakes, 'cache': {}}
                return self._state
        except Exception:
            pass
        coast: list = []
        lakes: list = []
        try:
            with open(self.data_file, encoding='utf-8') as f:
                data = json.load(f)
            coast = data.get(self.coastline_key) or []
            lakes = [ring for key in self.water_keys for ring in (data.get(key) or [])]
        except Exception:
            pass
        # Coastline segments indexed by grid cell. Segments are (lon1, lat1, lon2, lat2).
        seg_index: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        for way in coast:
            for (x1, y1), (x2, y2) in zip(way, way[1:]):
                seg = (x1, y1, x2, y2)
                for cx in range(int(math.floor(min(x1, x2) / _CELL_DEG)), int(math.floor(max(x1, x2) / _CELL_DEG)) + 1):
                    for cy in range(int(math.floor(min(y1, y2) / _CELL_DEG)), int(math.floor(max(y1, y2) / _CELL_DEG)) + 1):
                        seg_index.setdefault((cx, cy), []).append(seg)
        lake_index = []
        for ring in lakes:
            if len(ring) < 3:
                continue
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            lake_index.append((min(lons), max(lons), min(lats), max(lats), ring))
        self._state = {'segs': seg_index, 'lakes': lake_index, 'cache': {}}
        try:
            import pickle
            with open(self.index_cache, 'wb') as f:
                pickle.dump((seg_index, lake_index), f, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            pass
        return self._state

    def has_data(self) -> bool:
        st = self._load()
        return bool(st['segs'] or st['lakes'])

    def _in_lake(self, lon: float, lat: float) -> bool:
        for min_lon, max_lon, min_lat, max_lat, ring in self._load()['lakes']:
            if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat and _point_in_ring(lon, lat, ring):
                return True
        return False

    def _sea_side(self, lon: float, lat: float) -> bool | None:
        """True if the nearest coastline has the point on its water (right) side; None if no coast nearby."""
        segs = self._load()['segs']
        if not segs:
            return None
        k = math.cos(math.radians(lat))  # metric-ish x scale
        cx0, cy0 = int(math.floor(lon / _CELL_DEG)), int(math.floor(lat / _CELL_DEG))
        best = None  # (dist2, side_value)
        for r in range(_MAX_SEARCH_CELLS + 1):
            # Any segment in ring r is at least (r-1) cells away; stop once that exceeds the best.
            if best is not None and ((r - 1) * _CELL_DEG) ** 2 * min(1.0, k * k) > best[0]:
                break
            for cx in range(cx0 - r, cx0 + r + 1):
                for cy in range(cy0 - r, cy0 + r + 1):
                    if max(abs(cx - cx0), abs(cy - cy0)) != r:
                        continue
                    for x1, y1, x2, y2 in segs.get((cx, cy), ()):
                        ax, ay = (x1 - lon) * k, y1 - lat
                        bx, by = (x2 - lon) * k, y2 - lat
                        dx, dy = bx - ax, by - ay
                        den = dx * dx + dy * dy
                        t = 0.0 if den <= 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / den))
                        qx, qy = ax + t * dx, ay + t * dy
                        d2 = qx * qx + qy * qy
                        # cross(segment, point - segment_start): > 0 => point on the left (land).
                        cross = dx * (0 - ay) - dy * (0 - ax)
                        if best is None or d2 < best[0] - 1e-18:
                            best = (d2, cross, den)
                        elif abs(d2 - best[0]) <= 1e-18:
                            # Shared vertex: let the segment the point is more clearly beside decide.
                            if abs(cross) / math.sqrt(den or 1e-30) > abs(best[1]) / math.sqrt(best[2] or 1e-30):
                                best = (d2, cross, den)
        if best is None:
            return None
        return best[1] < 0

    def _coast_free_cell_is_sea(self, cell: tuple[int, int], lon: float, lat: float) -> bool:
        """Sea/land for a cell no coastline passes through.

        Such cells form regions bounded by coastline cells, and a whole region is on one side
        of the coast, so one precise test classifies the region (flood fill, cached).
        """
        st = self._load()
        classes = st.setdefault('cell_class', {})
        hit = classes.get(cell)
        if hit is not None:
            return hit
        segs = st['segs']
        # Clamp to the box the coastline was fetched for: OSM coastline is globally closed, so
        # inside that box it only ends at the box edges and therefore separates land from sea.
        x0 = int(math.floor(self.coast_bbox[1] / _CELL_DEG))
        x1 = int(math.floor(self.coast_bbox[3] / _CELL_DEG))
        y0 = int(math.floor(self.coast_bbox[0] / _CELL_DEG))
        y1 = int(math.floor(self.coast_bbox[2] / _CELL_DEG))
        if not (x0 <= cell[0] <= x1 and y0 <= cell[1] <= y1):
            return False  # outside the coastline data: treat as land
        sea = bool(self._sea_side(lon, lat))
        stack = [cell]
        classes[cell] = sea
        while stack:
            cx, cy = stack.pop()
            for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if nb in classes or nb in segs:
                    continue
                if not (x0 <= nb[0] <= x1 and y0 <= nb[1] <= y1):
                    continue
                classes[nb] = sea
                stack.append(nb)
        return sea

    def is_water_latlon(self, lat: float, lon: float) -> bool:
        st = self._load()
        key = (round(lat, 5), round(lon, 5))
        hit = st['cache'].get(key)
        if hit is None:
            cell = (int(math.floor(lon / _CELL_DEG)), int(math.floor(lat / _CELL_DEG)))
            if cell in st['segs']:
                sea = self._sea_side(lon, lat)  # coastline in this cell: nearest segment is close by
            else:
                sea = self._coast_free_cell_is_sea(cell, lon, lat)
            hit = bool(sea) or self._in_lake(lon, lat)
            if len(st['cache']) > 200_000:
                st['cache'].clear()
            st['cache'][key] = hit
        return hit

    def in_region(self, name: str, lat: float, lon: float) -> bool:
        """True when (lat, lon) is inside a named boundary stored in the data file."""
        rings = self._regions.get(name)
        if rings is None:
            try:
                with open(self.data_file, encoding='utf-8') as f:
                    raw = json.load(f).get(name) or []
            except Exception:
                raw = []
            rings = []
            for ring in raw:
                if len(ring) >= 3:
                    if len(ring) > _MAX_REGION_RING_POINTS:
                        # Long boundaries (tens of thousands of points) are thinned for the
                        # inside/outside test; at map-grid resolution (~1 km) this changes nothing.
                        step = -(-len(ring) // _MAX_REGION_RING_POINTS)
                        ring = ring[::step] + [ring[0]]
                    lons = [p[0] for p in ring]
                    lats = [p[1] for p in ring]
                    rings.append((min(lons), max(lons), min(lats), max(lats), ring))
            self._regions[name] = rings
        for min_lon, max_lon, min_lat, max_lat, ring in rings:
            if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat and _point_in_ring(lon, lat, ring):
                return True
        return False
