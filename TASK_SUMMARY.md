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

---

## End-year timing findings (for AI speed / profiling)

**Source:** `profile_end_year_first_turn.py` (cProfile), `profile_end_year_sequence.py` (multi-year timings).

### Summary
- **AI turn dominates** end-year time (~90s on first turn for NYC with 1 AI). `advance_year_complete` is fast on the first turn (~0.1s when there are no stations; no ridership calculation).
- In later years, `advance_year_complete` does ridership and per-line profit caching; expect it to grow with number of lines/stations but still secondary to AI turn on NYC.

### Where time goes in `run_ai_turn` (NYC, first turn)
| Phase | Cumulative time | Notes |
|-------|-----------------|--------|
| `run_ai_turn` | ~89s | Total for one AI |
| `_try_build_line` | ~30s × 3 calls | Build attempts/retries |
| `get_preset_road_segment_points` → `map_presets_nyc.get_road_segment_points_for_ai` → `map_presets_nyc_road_utils.get_road_segment_points_for_ai` | ~88s (24 calls) | **Main bottleneck** |
| Inside NYC road utils: `sorted` | ~70.5s | Sorting many segments |
| `_road_priority_by_population_grid` (lambda + grid lookup) | ~68s | Called 463k times |
| `_latlon_to_grid` | ~27.7s | 2.2M calls |
| `_get_bounds` / `get_nyc_bounds` | ~11s / ~8.5s | Bounds lookups |
| `_get_roads_in_map_bounds` | ~5.2s | Road filtering |

### Optimizations already applied
- **Preset loaded once per AI turn:** `run_ai_turn` loads the map preset once and passes it into `_try_build_line`, `_try_build_ferry_line`, and `_try_extend_line`. `get_preset_road_segment_points`, `get_preset_ferry_segment_points`, and `get_preset_road_extension_points` accept an optional `preset=` to avoid repeated `load_map_preset` (was 600+ calls per turn, now 1).
- **Existing routes and population points** are computed once per turn and passed through to avoid redundant work.

### Further speedups (without changing behavior)
- **Core (ai.py / operations.py):** No additional low-hanging fruit; hot path is inside the NYC preset’s `get_road_segment_points_for_ai`.
- **Map preset (nyc_road_utils):** The largest gains would come from:
  - Caching or narrowing the set of segments before the expensive sort (e.g. by bounds or length first).
  - Caching `_road_priority_by_population_grid` or the 4×4 population grid for the turn.
  - Reducing `_latlon_to_grid` calls (e.g. batch or cache by segment).
  - Limiting the number of segments passed to `sorted` (e.g. top-N by a cheap metric before full sort).
- **Profiling:** Run `python profile_end_year_first_turn.py` for cProfile breakdown; run `PROFILE_NUM_GAMES=2 PROFILE_YEARS_PER_GAME=8 python profile_end_year_sequence.py` for multi-year totals (and optionally `PROFILE_CPROFILE=1` for a cProfile dump to `profile_end_year_cprofile.txt`).
