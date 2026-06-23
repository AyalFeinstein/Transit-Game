# Task Summary and Implementation Plan

## 1. Population Points Issue Explanation ✅

**Status:** Explained in POPULATION_POINTS_EXPLANATION.md

### Why Rectangles of Missing Points in Manhattan:
- Grid cells that partially overlap Manhattan boundaries are being excluded
- Detection algorithm (center + corners + edges + 11x11 subgrid) may still miss some edge cases
- Only 88 points were saved (Manhattan only) - script didn't complete

### Why Staten Island and Long Island Points Missing:
- Script timed out during Brooklyn/Queens calculation
- Checkpoints only saved Manhattan points
- Remaining sections never executed

**Fix Required:** Complete the script with better optimization and checkpoint saving

## 2. Line Upgrades with Construction Jobs 🚧

**Current State:**
- `upgrade_line_complete()` immediately applies upgrades (sets `has_third_rail = True`)
- No time cost based on distance
- No construction workers required
- No construction jobs created

**Required Changes:**
1. Modify `upgrade_line_complete()` to create a construction job instead of immediately applying
2. Calculate build time based on line distance (miles/kilometers)
3. Require construction workers (can start without, but won't progress)
4. Add job to build queue with proper type: 'line_upgrade'
5. Modify `process_build_queue()` to handle 'line_upgrade' jobs
6. Update `refresh_construction_tab()` to display upgrade jobs
7. When complete, apply the upgrade

**Files to Modify:**
- `ui.py`: `upgrade_line_complete()`, `process_build_queue()`
- `qt.py`: `refresh_construction_tab()` (already handles line/station jobs, need upgrade type)

## 3. Unit Testing 📝

**Required:**
- Test line upgrade construction jobs
- Test upgrade time calculation
- Test construction worker assignment
- Test upgrade completion
- Test all existing functionality

**Files to Create/Update:**
- `test_line_upgrades.py` (new)
- Update existing test files

## 4. Separation of Concerns ✅

**Current State:**
- Game logic should be in `ui.py`, not `qt.py`
- UI display logic should be in `qt.py`

**Verification Needed:**
- Ensure `qt.py` only calls `ui.py` methods, not direct game access
- Ensure no game logic in `qt.py`

## Implementation Priority:
1. Line upgrades with construction jobs (most requested feature)
2. Fix population points script completion
3. Unit tests
4. Code review for separation of concerns
