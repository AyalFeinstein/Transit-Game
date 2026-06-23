"""Unit tests for 50m square tiles in OSMTileSystem."""
import unittest
import math
from unittest.mock import MagicMock

# Import after path setup
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from osm_tile_system import OSMTileSystem, TILE_SIZE_METERS


class TestOSMTileSquare50m(unittest.TestCase):
    """Verify water tiles for ferry lines use 50m square tiles."""

    def test_tile_size_meters_is_50(self):
        """TILE_SIZE_METERS must be 50 per user requirement."""
        self.assertEqual(TILE_SIZE_METERS, 50.0)

    def test_tile_size_lat_and_lon_differ_at_nonzero_latitude(self):
        """At non-equator latitude, tile_size_lat != tile_size_lon for square tiles."""
        mock_osm = MagicMock()
        ts = OSMTileSystem(100, 100, 40.7, -73.9, osm_integration=mock_osm)
        # tile_size_lat ≈ 50/111000 ≈ 0.00045
        # tile_size_lon ≈ 50/(111000*cos(40.7°)) ≈ 0.00059
        self.assertAlmostEqual(ts.tile_size_lat * 111000, 50.0, delta=1.0)
        self.assertAlmostEqual(ts.tile_size_lon * 111000 * math.cos(math.radians(40.7)), 50.0, delta=1.0)
        self.assertNotEqual(ts.tile_size_lat, ts.tile_size_lon)

    def test_tiles_are_square_at_center_latitude(self):
        """At map center, each tile should be ~50m x 50m in real-world meters."""
        mock_osm = MagicMock()
        center_lat = 40.7
        ts = OSMTileSystem(100, 100, center_lat, -73.9, osm_integration=mock_osm)
        # 1 deg lat ≈ 111km; 1 deg lon ≈ 111km * cos(lat)
        meters_per_deg_lat = 111000.0
        meters_per_deg_lon = 111000.0 * math.cos(math.radians(center_lat))
        tile_width_m = ts.tile_size_lon * meters_per_deg_lon
        tile_height_m = ts.tile_size_lat * meters_per_deg_lat
        self.assertAlmostEqual(tile_width_m, 50.0, delta=5.0)
        self.assertAlmostEqual(tile_height_m, 50.0, delta=5.0)

    def test_bounds_override_uses_square_tiles(self):
        """set_bounds with explicit bounds still uses 50m square tile dimensions."""
        mock_osm = MagicMock()
        ts = OSMTileSystem(100, 100, 40.7, -73.9, osm_integration=mock_osm)
        ts.set_bounds(40.6, -74.1, 40.8, -73.8)
        # tiles_x and tiles_y should use tile_size_lon and tile_size_lat
        lon_span = 0.3
        lat_span = 0.2
        expected_tiles_x = int(math.ceil(lon_span / ts.tile_size_lon))
        expected_tiles_y = int(math.ceil(lat_span / ts.tile_size_lat))
        self.assertEqual(ts.tiles_x, expected_tiles_x)
        self.assertEqual(ts.tiles_y, expected_tiles_y)
