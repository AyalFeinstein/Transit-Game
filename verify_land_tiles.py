#!/usr/bin/env python3
"""Standalone script to verify that all land tiles in the water detection grid are actually land.

This script:
1. Creates a tile system for a specified map area
2. Checks all tiles marked as 'land'
3. Verifies each land tile is actually land (not water) by querying OSM
4. Reports any discrepancies

Usage:
    python verify_land_tiles.py [--center-lat LAT] [--center-lon LON] [--width WIDTH] [--height HEIGHT] [--sample-rate RATE]
"""

import argparse
import sys
from osm_tile_system import OSMTileSystem

def main():
    parser = argparse.ArgumentParser(description='Verify that all land tiles are correctly identified')
    parser.add_argument('--center-lat', type=float, default=40.7589, 
                       help='Center latitude (default: 40.7589 - Times Square)')
    parser.add_argument('--center-lon', type=float, default=-73.9851,
                       help='Center longitude (default: -73.9851 - Times Square)')
    parser.add_argument('--width', type=int, default=100,
                       help='Map width in grid units (default: 100)')
    parser.add_argument('--height', type=int, default=100,
                       help='Map height in grid units (default: 100)')
    parser.add_argument('--tile-size', type=float, default=0.0005,
                       help='Tile size in degrees (default: 0.0005)')
    parser.add_argument('--sample-rate', type=int, default=1,
                       help='Check every Nth tile (1=all, 2=every other, etc.) (default: 1)')
    parser.add_argument('--pre-populate', action='store_true',
                       help='Pre-populate tiles by querying a sample before verification')
    
    args = parser.parse_args()
    
    try:
        from osm_integration import OSMIntegration
        from map_presets import OSM_SETTINGS
    except ImportError:
        print("ERROR: OSM integration not available. Install required dependencies.")
        sys.exit(1)
    
    print("=" * 70)
    print("Land Tile Verification")
    print("=" * 70)
    print(f"Map area: {args.width}x{args.height} grid units")
    print(f"Center: ({args.center_lat:.6f}, {args.center_lon:.6f})")
    print(f"Tile size: {args.tile_size} degrees (~{args.tile_size * 111000:.0f}m)")
    print(f"Sample rate: {args.sample_rate} (checking every {args.sample_rate} tile)")
    print()
    
    try:
        # Create OSM integration
        print("Initializing OSM integration...")
        real_osm = OSMIntegration(timeout=OSM_SETTINGS.get('timeout', 5.0))
        
        # Create tile system
        print("Creating tile system...")
        tile_system = OSMTileSystem(
            map_width=args.width,
            map_height=args.height,
            map_center_lat=args.center_lat,
            map_center_lon=args.center_lon,
            tile_size=args.tile_size,
            osm_integration=real_osm
        )
        
        print(f"Tile grid: {tile_system.tiles_x}x{tile_system.tiles_y} tiles")
        print(f"Bounds: ({tile_system.min_lat:.6f}, {tile_system.min_lon:.6f}) to ({tile_system.max_lat:.6f}, {tile_system.max_lon:.6f})")
        print()
        
        # Pre-populate tiles if requested
        if args.pre_populate:
            print("Pre-populating tiles (this may take a while)...")
            sample_size = min(50, max(tile_system.tiles_x, tile_system.tiles_y))
            for tile_x in range(0, min(sample_size, tile_system.tiles_x), 2):
                for tile_y in range(0, min(sample_size, tile_system.tiles_y), 2):
                    tile_system.get_tile_type(tile_x, tile_y)
            print(f"Pre-populated {len(tile_system._tile_cache)} tiles")
            print()
        
        # Verify all land tiles
        print("Verifying land tiles...")
        print("(This may take a while depending on map size and sample rate)")
        print()
        
        results = tile_system.verify_all_land_tiles(sample_rate=args.sample_rate)
        
        # Report results
        print("=" * 70)
        print("Verification Results")
        print("=" * 70)
        print(f"Total land tiles checked: {results['total_land_tiles']}")
        print(f"Incorrect tiles (marked land but actually water): {len(results['incorrect_tiles'])}")
        print(f"Verification errors (could not verify): {results['verification_errors']}")
        print()
        
        if results['incorrect_tiles']:
            print("ERROR: Found tiles incorrectly marked as 'land' that are actually water:")
            print()
            for i, tile_info in enumerate(results['incorrect_tiles'], 1):
                print(f"  {i}. Tile {tile_info['tile']} at ({tile_info['lat']:.6f}, {tile_info['lon']:.6f})")
            print()
            print("These tiles should be marked as 'water' instead of 'land'.")
            return 1
        else:
            if results['total_land_tiles'] > 0:
                print("✓ SUCCESS: All land tiles are correctly identified as land!")
                if results['verification_errors'] > 0:
                    print(f"  (Note: {results['verification_errors']} tiles could not be verified due to OSM errors)")
                return 0
            else:
                print("WARNING: No land tiles found in the test area.")
                print("The area may be entirely water, or tiles haven't been queried yet.")
                return 0
                
    except KeyboardInterrupt:
        print("\n\nVerification interrupted by user.")
        return 1
    except Exception as e:
        print(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
