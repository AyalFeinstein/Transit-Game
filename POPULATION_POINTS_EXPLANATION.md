# Population Points Issue Explanation

## Why There Are Rectangles of Missing Population Points in Manhattan

1. **Grid Cell Alignment Issue**: The population point generation uses a 50x50 grid. Each grid point represents a cell center, but Manhattan's shoreline is complex and doesn't align with this grid. Some grid cells that partially overlap Manhattan's boundary may be excluded if:
   - The cell center is outside the polygon
   - None of the corners are inside
   - None of the edge midpoints are inside
   - None of the 11x11 subgrid points are inside

2. **Checkpoint Saving**: The script only saved Manhattan points (88 points) because:
   - The script was saving checkpoints after each major section
   - It likely only completed Manhattan before timing out or encountering errors
   - The Brooklyn/Queens calculation (which includes Long Island area) was still in progress
   - Staten Island and Roosevelt Island sections may not have been reached

3. **Nassau County Removal**: At the end of the script, there's a section that removes points in Nassau County. This might be incorrectly identifying some valid points as being in Nassau County due to:
   - Polygon overlap issues
   - Coordinate precision problems
   - Grid cell boundary checks being too strict

## Why Points Were Removed from Staten Island and Long Island

The points were never actually removed - they were never added in the first place because:
- The script times out during the Brooklyn/Queens calculation (which processes 2601 points with complex polygon intersections)
- The script likely didn't complete the Staten Island or Kings County sections
- Only Manhattan points were saved to the checkpoint

## Solution

To fix this, we need to:
1. Improve the polygon detection to catch all edge cases
2. Add better checkpoint saving throughout the process
3. Optimize the Brooklyn/Queens calculation to prevent timeouts
4. Verify that Nassau County removal logic isn't too aggressive
