"""Generic utilities for working with map preset files.
Preset land cells are defined only by {MAP_ID}_INITIAL_POPULATION in each preset file."""
import math
import os
import re
import ast
import time

# (absolute path, variable name) -> (mtime, population dict)
_preset_population_cache: dict[tuple[str, str], tuple[float, dict[tuple[int, int], int]]] = {}

DEFAULT_NEW_POPULATION_CELL = 50


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    r_earth = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r_earth * 2 * math.asin(min(1.0, math.sqrt(max(0.0, a))))


def population_at_or_near(px: float, py: float, pop_map: dict, radius: int = 7) -> int:
    """Population at grid point (px, py), or of the nearest populated cell within radius.
    Population grids can be sparse and route endpoints often land between cells."""
    if not pop_map:
        return 0
    ix, iy = int(round(px)), int(round(py))
    pop = pop_map.get((ix, iy), 0)
    if pop > 0:
        return pop
    for r in range(1, radius + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    p = pop_map.get((ix + dx, iy + dy), 0)
                    if p > 0:
                        return p
    return 0


def preset_population_variable_name(map_id: str) -> str:
    return f"{map_id.upper().replace('-', '_')}_INITIAL_POPULATION"


def invalidate_preset_population_cache(
    file_path: str | None = None, variable_name: str | None = None
) -> None:
    if file_path is None:
        _preset_population_cache.clear()
        return
    file_path = os.path.abspath(file_path)
    if variable_name is None:
        for key in list(_preset_population_cache):
            if key[0] == file_path:
                del _preset_population_cache[key]
    else:
        _preset_population_cache.pop((file_path, variable_name), None)


def get_preset_file_info(game_map) -> tuple[str, str] | None:
    """Return (preset_file_path, INITIAL_POPULATION variable name) for a map."""
    if not game_map:
        return None

    map_id = None
    preset_file = None

    if hasattr(game_map, 'map_preset_name') and game_map.map_preset_name:
        map_id = game_map.map_preset_name
        preset_file = f"map_presets_{map_id}.py"

    if not map_id or not preset_file:
        return None

    variable_name = preset_population_variable_name(map_id)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, preset_file)

    if not os.path.exists(file_path):
        return None

    return (file_path, variable_name)


def _assignment_open_bracket(content: str, variable_name: str) -> tuple[int, int, str] | None:
    """Return (match_start, open_bracket_index, opener) for list or dict assignment."""
    pattern = (
        rf'{re.escape(variable_name)}'
        r'(?:\s*:\s*dict\[tuple\[int,\s*int\],\s*int\])?\s*=\s*([\[\{{])'
    )
    match = re.search(pattern, content)
    if not match:
        return None
    opener = match.group(1)
    open_idx = match.end() - 1
    return match.start(), open_idx, opener


def _extract_braced_literal(content: str, open_idx: int, open_char: str) -> str | None:
    close_char = ']' if open_char == '[' else '}'
    bracket_count = 0
    i = open_idx
    in_string = False
    string_char = None

    while i < len(content):
        char = content[i]
        if char in ('"', "'") and (i == 0 or content[i - 1] != '\\'):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None

        if not in_string:
            if char == open_char:
                bracket_count += 1
            elif char == close_char:
                bracket_count -= 1
                if bracket_count == 0:
                    return content[open_idx:i + 1]
        i += 1
    return None


# Grid radius for spreading each population anchor over a 1×1 unit area (~1 km²).
POPULATION_ANCHOR_SPREAD_RADIUS_GRID = 1.0


def _cells_in_population_spread_zone(
    anchor_x: float,
    anchor_y: float,
    spread_radius: float = POPULATION_ANCHOR_SPREAD_RADIUS_GRID,
) -> list[tuple[int, int]]:
    """Integer grid cells whose centers fall within spread_radius of an anchor."""
    import math

    radius = max(0.0, float(spread_radius))
    if radius <= 0:
        ax, ay = int(round(anchor_x)), int(round(anchor_y))
        return [(ax, ay)]
    cell_r = int(math.ceil(radius))
    ax, ay = float(anchor_x), float(anchor_y)
    cells: list[tuple[int, int]] = []
    base_x, base_y = int(math.floor(ax)), int(math.floor(ay))
    for dx in range(-cell_r, cell_r + 1):
        for dy in range(-cell_r, cell_r + 1):
            cx, cy = base_x + dx, base_y + dy
            if math.hypot((cx + 0.5) - ax, (cy + 0.5) - ay) <= radius + 1e-9:
                cells.append((cx, cy))
    if not cells:
        cells = [(int(round(ax)), int(round(ay)))]
    return cells


def _nearest_population_anchor(
    px: float,
    py: float,
    anchors: dict[tuple[int, int], int],
) -> tuple[tuple[int, int], int, float] | None:
    """Return ((ax, ay), pop, distance) for the closest anchor, or None."""
    import math

    if not anchors:
        return None
    best = None
    best_dist = float('inf')
    for (ax, ay), pop in anchors.items():
        dist = math.hypot(float(px) - float(ax), float(py) - float(ay))
        if dist < best_dist:
            best_dist = dist
            best = ((int(ax), int(ay)), int(pop or 0), dist)
    return best


def population_at_point_from_anchors(
    px: float,
    py: float,
    anchors: dict[tuple[int, int], int],
    spread_radius: float = POPULATION_ANCHOR_SPREAD_RADIUS_GRID,
) -> int:
    """Population at a grid location from sparse anchor points."""
    nearest = _nearest_population_anchor(px, py, anchors)
    if nearest is None:
        return 0
    (ax, ay), anchor_pop, dist = nearest
    if anchor_pop <= 0 or dist > float(spread_radius) + 1e-9:
        return 0
    zone_cells = _cells_in_population_spread_zone(ax, ay, spread_radius)
    ix, iy = int(round(px)), int(round(py))
    if (ix, iy) not in zone_cells:
        return 0
    assigned = []
    for cx, cy in zone_cells:
        cell_nearest = _nearest_population_anchor(cx + 0.5, cy + 0.5, anchors)
        if cell_nearest and cell_nearest[0] == (ax, ay):
            assigned.append((cx, cy))
    if not assigned:
        return 0
    share, remainder = divmod(int(anchor_pop), len(assigned))
    assigned = sorted(assigned)
    idx = assigned.index((ix, iy))
    return share + (1 if idx < remainder else 0)


def spread_population_from_anchors(
    anchors: dict[tuple[int, int], int],
    width: int,
    height: int,
    spread_radius: float = POPULATION_ANCHOR_SPREAD_RADIUS_GRID,
) -> dict[tuple[int, int], int]:
    """Build a full population grid by evenly spreading each anchor over one square unit."""
    if not anchors:
        return {}

    radius = float(spread_radius)
    normalized: dict[tuple[int, int], int] = {}
    for key, value in anchors.items():
        if not isinstance(key, (tuple, list)) or len(key) < 2:
            continue
        ax, ay = int(round(key[0])), int(round(key[1]))
        normalized[(ax, ay)] = int(value or 0)

    zone_by_anchor: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for (ax, ay), pop in normalized.items():
        if pop <= 0:
            continue
        zone_by_anchor[(ax, ay)] = sorted(
            _cells_in_population_spread_zone(ax, ay, radius)
        )

    assigned: dict[tuple[int, int], list[tuple[int, int]]] = {
        anchor: [] for anchor in zone_by_anchor
    }
    max_x = int(width)
    max_y = int(height)
    for cx in range(max_x + 1):
        for cy in range(max_y + 1):
            px, py = cx + 0.5, cy + 0.5
            nearest = _nearest_population_anchor(px, py, normalized)
            if nearest is None:
                continue
            (ax, ay), anchor_pop, dist = nearest
            if anchor_pop <= 0 or dist > radius + 1e-9:
                continue
            zone_cells = zone_by_anchor.get((ax, ay))
            if not zone_cells or (cx, cy) not in zone_cells:
                continue
            assigned[(ax, ay)].append((cx, cy))

    population: dict[tuple[int, int], int] = {}
    for (ax, ay), pop in normalized.items():
        if pop <= 0:
            continue
        cells = assigned.get((ax, ay), [])
        if not cells:
            continue
        share, remainder = divmod(pop, len(cells))
        for idx, cell in enumerate(sorted(cells)):
            pop_val = share + (1 if idx < remainder else 0)
            if pop_val > 0:
                population[cell] = pop_val
    return population


def _normalize_population_dict(raw) -> dict[tuple[int, int], int]:
    population: dict[tuple[int, int], int] = {}
    if not isinstance(raw, dict):
        return population
    for key, value in raw.items():
        if isinstance(key, (tuple, list)) and len(key) >= 2:
            x, y = int(round(key[0])), int(round(key[1]))
            pop = int(value) if value is not None else 0
            if pop > 0:
                population[(x, y)] = pop
    return population


def _normalize_points_list(raw) -> set[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    if not isinstance(raw, list):
        return points
    for p in raw:
        if isinstance(p, (tuple, list)) and len(p) >= 2:
            points.add((int(round(p[0])), int(round(p[1]))))
    return points


def _read_initial_population_from_file_uncached(file_path: str, variable_name: str) -> dict[tuple[int, int], int]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        located = _assignment_open_bracket(content, variable_name)
        if located:
            _, open_idx, opener = located
            literal = _extract_braced_literal(content, open_idx, opener)
            if literal:
                try:
                    parsed = ast.literal_eval(literal)
                except Exception:
                    parsed = None
                if opener == '{':
                    return _normalize_population_dict(parsed)
                return {(x, y): DEFAULT_NEW_POPULATION_CELL for x, y in _normalize_points_list(parsed)}

        # Legacy INCLUDED_POINTS list (read-only fallback for old preset files).
        legacy_name = variable_name.replace('_INITIAL_POPULATION', '_INCLUDED_POINTS')
        if legacy_name != variable_name:
            located = _assignment_open_bracket(content, legacy_name)
            if located:
                _, open_idx, opener = located
                if opener == '[':
                    literal = _extract_braced_literal(content, open_idx, opener)
                    if literal:
                        try:
                            parsed = ast.literal_eval(literal)
                        except Exception:
                            parsed = None
                        return {
                            (x, y): DEFAULT_NEW_POPULATION_CELL
                            for x, y in _normalize_points_list(parsed)
                        }
        return {}
    except Exception as e:
        print(f"Error reading {variable_name} from {file_path}: {e}")
        return {}


def read_initial_population_from_file(file_path: str, variable_name: str) -> dict[tuple[int, int], int]:
    file_path = os.path.abspath(file_path)
    key = (file_path, variable_name)
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = 0.0
    cached = _preset_population_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return dict(cached[1])
    population = _read_initial_population_from_file_uncached(file_path, variable_name)
    _preset_population_cache[key] = (mtime, dict(population))
    return population


def read_included_points_from_file(file_path: str, variable_name: str) -> set[tuple[int, int]]:
    """Land cells for a preset = keys of INITIAL_POPULATION with population > 0."""
    population = read_initial_population_from_file(file_path, variable_name)
    return set(population.keys())


def format_initial_population_dict(variable_name: str, population: dict[tuple[int, int], int]) -> str:
    lines = [f"{variable_name}: dict[tuple[int, int], int] = {{"]
    items = sorted(population.items())
    for i, ((x, y), count) in enumerate(items):
        suffix = "," if i < len(items) - 1 else ""
        lines.append(f"    ({x}, {y}): {int(count)}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def _population_write_succeeded(file_path: str, variable_name: str) -> bool:
    invalidate_preset_population_cache(file_path, variable_name)
    return True


def write_initial_population_to_file(
    file_path: str,
    variable_name: str,
    population: dict[tuple[int, int], int],
    *,
    max_points_to_remove: int | None = None,
) -> bool:
    try:
        file_path = os.path.abspath(file_path)
        cleaned: dict[tuple[int, int], int] = {}
        for key, value in (population or {}).items():
            if isinstance(key, (tuple, list)) and len(key) >= 2:
                pop = int(value) if value is not None else 0
                if pop > 0:
                    cleaned[(int(round(key[0])), int(round(key[1])))] = pop

        if max_points_to_remove is not None:
            current = read_initial_population_from_file(file_path, variable_name)
            if current:
                points_removed = len(current) - len(cleaned)
                if points_removed > max_points_to_remove:
                    print(
                        f"ERROR: Aborting write - would remove {points_removed} population cells "
                        f"(max allowed {max_points_to_remove}). Likely read failure or bug."
                    )
                    return False

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        population_block = format_initial_population_dict(variable_name, cleaned) + "\n"
        located = _assignment_open_bracket(content, variable_name)
        if located:
            match_start, open_idx, opener = located
            literal = _extract_braced_literal(content, open_idx, opener)
            if literal:
                end_pos = open_idx + len(literal)
                new_content = content[:match_start] + population_block + content[end_pos:]
            else:
                return False
        else:
            # Legacy list assignment -> replace with population dict.
            legacy_name = variable_name.replace('_INITIAL_POPULATION', '_INCLUDED_POINTS')
            legacy_located = _assignment_open_bracket(content, legacy_name)
            if not legacy_located:
                print(f"ERROR: Could not find {variable_name} assignment in {file_path}")
                return False
            match_start, open_idx, opener = legacy_located
            literal = _extract_braced_literal(content, open_idx, opener)
            if not literal:
                return False
            end_pos = open_idx + len(literal)
            new_content = content[:match_start] + population_block + content[end_pos:]

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())

        time.sleep(0.01)
        verify = read_initial_population_from_file(file_path, variable_name)
        if len(verify) == len(cleaned):
            print(f"SUCCESS: Wrote {len(verify)} population cells to {variable_name} in {file_path}")
            return _population_write_succeeded(file_path, variable_name)
        print(f"WARNING: Write verification - expected {len(cleaned)} cells, got {len(verify)}")
        return len(verify) > 0
    except Exception as e:
        print(f"Error writing {variable_name} to {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return False


def write_included_points_to_file(
    file_path: str,
    variable_name: str,
    points: set[tuple[int, int]],
    max_points_to_remove: int | None = None,
) -> bool:
    """Update INITIAL_POPULATION keys to match the given land-cell set (edit mode)."""
    current = read_initial_population_from_file(file_path, variable_name)
    cleaned_points: set[tuple[int, int]] = set()
    for p in points:
        if isinstance(p, (tuple, list)) and len(p) >= 2:
            cleaned_points.add((int(round(p[0])), int(round(p[1]))))

    new_population: dict[tuple[int, int], int] = {}
    for pt in cleaned_points:
        new_population[pt] = current.get(pt, DEFAULT_NEW_POPULATION_CELL)

    return write_initial_population_to_file(
        file_path,
        variable_name,
        new_population,
        max_points_to_remove=max_points_to_remove,
    )


def remove_duplicates_from_file(file_path: str, variable_name: str) -> bool:
    """Remove duplicate population cells in a preset file."""
    try:
        population = read_initial_population_from_file(file_path, variable_name)
        if not population:
            return False
        return write_initial_population_to_file(file_path, variable_name, population)
    except Exception as e:
        print(f"Error removing duplicates from {variable_name} in {file_path}: {e}")
        return False


def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from point (px, py) to line segment (ax,ay)-(bx,by)."""
    dx = bx - ax
    dy = by - ay
    dist_sq = dx * dx + dy * dy
    if dist_sq < 1e-20:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / dist_sq))
    nx = ax + t * dx
    ny = ay + t * dy
    return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5


def ai_grid_polyline_overlap_tolerance(game_map, base: float = 0.02) -> float:
    """Tolerance for polyline_has_self_overlap on grid-snapped OSM routes.

    On maps with larger grids, vertices sit closer in grid units than on a 50×50 map; the
    default base tolerance then false-flags real street polylines. 50×50 maps are unchanged."""
    if game_map is None:
        return base
    m = max(
        50,
        int(getattr(game_map, "width", 50) or 50),
        int(getattr(game_map, "height", 50) or 50),
    )
    if m <= 55:
        return base
    return max(0.008, min(base, base * (50.0 / float(m)) * 0.62))


def _polyline_xy_points(points: list) -> list:
    out = []
    for p in points:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append((float(p[0]), float(p[1])))
        else:
            out.append((float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0))))
    return out


def route_turn_angle_degrees(p0, p1, p2) -> float:
    """Heading change at ``p1`` in degrees (0 = straight, 180 = reverse)."""
    v1x = float(p1[0]) - float(p0[0])
    v1y = float(p1[1]) - float(p0[1])
    v2x = float(p2[0]) - float(p1[0])
    v2y = float(p2[1]) - float(p1[1])
    l1 = math.hypot(v1x, v1y)
    l2 = math.hypot(v2x, v2y)
    if l1 < 1e-12 or l2 < 1e-12:
        return 0.0
    cos_a = (v1x * v2x + v1y * v2y) / (l1 * l2)
    cos_a = max(-1.0, min(1.0, cos_a))
    return math.degrees(math.acos(cos_a))


def route_approx_turn_radius_grid(p0, p1, p2) -> float:
    """Approximate turn radius at ``p1`` using the shorter adjacent leg."""
    angle = math.radians(route_turn_angle_degrees(p0, p1, p2))
    if angle < math.radians(2.0):
        return float('inf')
    l1 = math.hypot(float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1]))
    l2 = math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))
    leg = min(l1, l2)
    half = math.sin(angle * 0.5)
    if half < 1e-9:
        return float('inf')
    return leg / (2.0 * half)


def route_exceeds_max_turn(
    points: list,
    max_turn_degrees: float,
    *,
    min_segment_grid: float = 0.025,
) -> bool:
    """True if any interior vertex turns sharper than ``max_turn_degrees``."""
    pts = _polyline_xy_points(points)
    if len(pts) < 3:
        return False
    for i in range(1, len(pts) - 1):
        l1 = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        l2 = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if l1 < min_segment_grid or l2 < min_segment_grid:
            continue
        if route_turn_angle_degrees(pts[i - 1], pts[i], pts[i + 1]) > float(max_turn_degrees):
            return True
    return False


def polyline_has_reverse_corridor(points: list, tolerance: float) -> bool:
    """True when two non-adjacent segments run opposite directions on the same corridor."""
    pts = _polyline_xy_points(points)
    n = len(pts)
    if n < 3:
        return False
    tol = max(float(tolerance), 1e-6)
    seg_data = []
    for i in range(n - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < tol * 0.25:
            continue
        seg_data.append(((dx / length, dy / length), length, (ax, ay), (bx, by)))
    for i in range(len(seg_data)):
        dir_i, len_i, a_i, b_i = seg_data[i]
        mid_i = ((a_i[0] + b_i[0]) * 0.5, (a_i[1] + b_i[1]) * 0.5)
        for j in range(i + 1, len(seg_data)):
            dir_j, len_j, a_j, b_j = seg_data[j]
            dot = dir_i[0] * dir_j[0] + dir_i[1] * dir_j[1]
            if dot > -0.65:
                continue
            mid_j = ((a_j[0] + b_j[0]) * 0.5, (a_j[1] + b_j[1]) * 0.5)
            sep = math.hypot(mid_j[0] - mid_i[0], mid_j[1] - mid_i[1])
            corridor_tol = max(tol * 4.0, 0.06)
            if sep > corridor_tol:
                continue
            d_ai = _point_to_segment_distance(a_i[0], a_i[1], a_j[0], a_j[1], b_j[0], b_j[1])
            d_bi = _point_to_segment_distance(b_i[0], b_i[1], a_j[0], a_j[1], b_j[0], b_j[1])
            if min(d_ai, d_bi) <= corridor_tol:
                return True
    return False


def polyline_has_self_overlap(points: list, tolerance: float = 0.02) -> bool:
    """Return True if the polyline crosses itself, has duplicate/near-duplicate points, or parallel segments too close.
    tolerance: min distance to consider two points/segments distinct (~20m when 1 unit≈1km)."""
    if not points or len(points) < 3:
        return False

    pts = _polyline_xy_points(points)

    def _pt_idx(i):
        return pts[i]

    if polyline_has_reverse_corridor(pts, tolerance):
        return True

    # Check for duplicate/near-duplicate points (non-adjacent only; adjacent = junction)
    for i in range(len(pts)):
        for j in range(i + 2, len(pts)):
            a, b = _pt_idx(i), _pt_idx(j)
            if math.hypot(b[0] - a[0], b[1] - a[1]) < tolerance:
                return True

    # Consecutive segments can retrace the previous edge (OSM extensions often stack on the same street).
    # The non-adjacent loop below skips j == i+1, so backtracking along AB with B->D was missed.
    for i in range(len(pts) - 2):
        a, b = _pt_idx(i), _pt_idx(i + 1)
        d = _pt_idx(i + 2)
        abx, aby = b[0] - a[0], b[1] - a[1]
        ab_len_sq = abx * abx + aby * aby
        if ab_len_sq < 1e-20:
            continue
        dist_d = _point_to_segment_distance(d[0], d[1], a[0], a[1], b[0], b[1])
        if dist_d >= tolerance:
            continue
        t = ((d[0] - a[0]) * abx + (d[1] - a[1]) * aby) / ab_len_sq
        if 1e-6 < t < 1.0 - 1e-6:
            return True
        bdx, bdy = d[0] - b[0], d[1] - b[1]
        bd_len = math.hypot(bdx, bdy)
        if bd_len < tolerance * 0.2:
            continue
        cross = abx * bdy - aby * bdx
        max_cross = tolerance * max(math.sqrt(ab_len_sq), 1.0) * max(bd_len, 1.0)
        if abs(cross) > max_cross:
            continue
        dot_back = (-abx) * bdx + (-aby) * bdy
        if dot_back > tolerance * 0.1 * math.sqrt(ab_len_sq) * math.sqrt(bd_len):
            return True

    # Check for parallel segments that run too close (hairpin/loop overlap)
    for i in range(len(pts) - 1):
        a, b = _pt_idx(i), _pt_idx(i + 1)
        for j in range(i + 2, len(pts) - 1):
            c, d = _pt_idx(j), _pt_idx(j + 1)
            d_ac = _point_to_segment_distance(a[0], a[1], c[0], c[1], d[0], d[1])
            d_bc = _point_to_segment_distance(b[0], b[1], c[0], c[1], d[0], d[1])
            d_ca = _point_to_segment_distance(c[0], c[1], a[0], a[1], b[0], b[1])
            d_da = _point_to_segment_distance(d[0], d[1], a[0], a[1], b[0], b[1])
            min_dist = min(d_ac, d_bc, d_ca, d_da)
            if min_dist < tolerance and min_dist > 1e-12:
                return True

    # Check for non-adjacent segment intersection
    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def _on_segment(p, a, b):
        return (min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9 and
                min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)

    def _segments_intersect(a, b, c, d):
        o1 = _cross(a, b, c)
        o2 = _cross(a, b, d)
        o3 = _cross(c, d, a)
        o4 = _cross(c, d, b)
        if o1 * o2 < 0 and o3 * o4 < 0:
            return True
        if o1 == 0 and _on_segment(c, a, b):
            return True
        if o2 == 0 and _on_segment(d, a, b):
            return True
        if o3 == 0 and _on_segment(a, c, d):
            return True
        if o4 == 0 and _on_segment(b, c, d):
            return True
        return False

    for i in range(len(pts) - 1):
        for j in range(i + 2, len(pts) - 1):
            a, b = _pt_idx(i), _pt_idx(i + 1)
            c, d = _pt_idx(j), _pt_idx(j + 1)
            if _segments_intersect(a, b, c, d):
                return True
    return False


_WATER_CELLS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.water_cells_cache')


def _water_cells_cache_path(cache_key: str, cells: list, data_stamp: str = '') -> tuple[str, str]:
    import hashlib
    digest = hashlib.sha1((data_stamp + repr(cells)).encode()).hexdigest()
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in cache_key)
    return os.path.join(_WATER_CELLS_CACHE_DIR, f'{safe}.json'), digest


def _cached_water_cells(cache_key: str, cells: list, data_stamp: str = ''):
    import json
    path, digest = _water_cells_cache_path(cache_key, cells, data_stamp)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('digest') == digest:
            return [tuple(c) for c in data.get('wet', [])]
    except Exception:
        pass
    return None


def _store_water_cells(cache_key: str, cells: list, wet: list, data_stamp: str = '') -> None:
    import json
    path, digest = _water_cells_cache_path(cache_key, cells, data_stamp)
    try:
        os.makedirs(_WATER_CELLS_CACHE_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'digest': digest, 'wet': [list(c) for c in wet]}, f)
    except Exception:
        pass


def remove_water_population_cells(
    population: dict, cell_to_latlon, max_shift: int = 6, cache_key: str = None, mask=None,
) -> tuple[dict, int]:
    """Drop populated cells whose map position is on water.

    ``mask`` is the map's ``water_mask.WaterMask``; without one nothing is removed.

    Each removed cell's population moves to the nearest land cell within ``max_shift``
    cells, so totals are preserved where possible. Returns (new_population, removed_count).
    """
    if not population or mask is None or not mask.has_data():
        return population, 0
    stamp = mask.data_stamp()
    cells = sorted(pt for pt, pop in population.items() if pop and int(pop) > 0)
    wet = _cached_water_cells(cache_key, cells, stamp) if cache_key else None
    if wet is None:
        wet = []
        for x, y in cells:
            try:
                lat, lon = cell_to_latlon(x, y)
            except Exception:
                continue
            if mask.is_water_latlon(lat, lon):
                wet.append((x, y))
        if cache_key:
            _store_water_cells(cache_key, cells, wet, stamp)
    if not wet:
        return population, 0
    wet_set = set(wet)
    result = {pt: p for pt, p in population.items() if pt not in wet_set}
    for x, y in sorted(wet):
        pop = int(population[(x, y)] or 0)
        for r in range(1, max_shift + 1):
            ring = [
                (x + dx, y + dy)
                for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                if max(abs(dx), abs(dy)) == r and int(result.get((x + dx, y + dy)) or 0) > 0
            ]
            if ring:
                best = min(ring, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)
                result[best] = int(result[best]) + pop
                break
    return result, len(wet)


def remove_population_in_regions(
    population: dict, cell_to_latlon, regions, cache_key: str = None, mask=None,
) -> tuple[dict, int]:
    """Drop populated cells inside any of the named boundaries (people there are not counted).

    Unlike water cells, their population is not moved: it belongs to a place the map
    leaves out. ``regions`` are boundary names known to ``mask`` (a ``WaterMask``).
    """
    regions = tuple(regions or ())
    if not population or not regions or mask is None:
        return population, 0
    stamp = mask.data_stamp()
    cells = sorted(pt for pt, pop in population.items() if pop and int(pop) > 0)
    key = f"{cache_key}_regions_{'_'.join(regions)}" if cache_key else None
    inside = _cached_water_cells(key, cells, stamp) if key else None
    if inside is None:
        inside = []
        for x, y in cells:
            try:
                lat, lon = cell_to_latlon(x, y)
            except Exception:
                continue
            if any(mask.in_region(name, lat, lon) for name in regions):
                inside.append((x, y))
        if key:
            _store_water_cells(key, cells, inside, stamp)
    drop = set(inside)
    return {pt: p for pt, p in population.items() if pt not in drop}, len(drop)


def fill_population_in_regions(
    population: dict, width: int, height: int, cell_to_latlon, regions: dict, cache_key: str = None,
    mask=None,
) -> tuple[dict, int]:
    """Give every empty land cell inside the named boundaries a population.

    ``regions`` maps a boundary name (``mask.in_region``) to ``fn(lat, lon) -> int``; the
    first matching boundary decides. Water cells are never filled. Cached per map like the
    water cleanup, since the boundary tests are slow.
    """
    if not regions or mask is None or not mask.has_data():
        return population, 0
    stamp = mask.data_stamp()
    names = tuple(regions)
    key = f"{cache_key}_fill_{'_'.join(names)}" if cache_key else None
    empty = sorted(
        (x, y) for x in range(int(width) + 1) for y in range(int(height) + 1)
        if not population.get((x, y))
    )
    filled = None
    if key:
        cached = _cached_water_cells(key, empty, stamp)
        if cached is not None:
            filled = {(int(c[0]), int(c[1])): int(c[2]) for c in cached}
    if filled is None:
        filled = {}
        for x, y in empty:
            try:
                lat, lon = cell_to_latlon(x, y)
            except Exception:
                continue
            if mask.is_water_latlon(lat, lon):
                continue
            for name in names:
                if mask.in_region(name, lat, lon):
                    try:
                        pop = int(regions[name](lat, lon) or 0)
                    except Exception:
                        pop = 0
                    if pop > 0:
                        filled[(x, y)] = pop
                    break
        if key:
            _store_water_cells(key, empty, [[x, y, p] for (x, y), p in sorted(filled.items())], stamp)
    if not filled:
        return population, 0
    result = dict(population)
    result.update(filled)
    return result, len(filled)


def ai_ferry_segment_candidates(preset_name: str, bounds: dict, game_map, max_points: int = 12,
                                rng=None, min_population: int = 120, pop_radius: int = 3,
                                min_len: float = 0.8, max_len: float = 4.8) -> list:
    """Ferry route candidates for the AI: straight grid segments between water points that lie
    near populated places, kept mostly over water. Shared by presets that have water.

    Only water cells within ``pop_radius`` of a cell with ``min_population`` are considered, so
    large maps are not scanned cell by cell.
    """
    import math
    from map_preset_loader import preset_is_point_in_water

    w = max(1, int(getattr(game_map, "width", 1) or 1))
    h = max(1, int(getattr(game_map, "height", 1) or 1))
    pop_map = getattr(game_map, "population", None) or {}

    water_memo = {}

    def is_water(x: float, y: float) -> bool:
        key = (round(x, 4), round(y, 4))
        hit = water_memo.get(key)
        if hit is None:
            try:
                hit = bool(preset_is_point_in_water(preset_name, x, y, game_map, skip_osm=True))
            except Exception:
                hit = False
            water_memo[key] = hit
        return hit

    near_pop = {}
    r2 = pop_radius * pop_radius
    for (px, py), pop in pop_map.items():
        if pop < min_population:
            continue
        for dx in range(-pop_radius, pop_radius + 1):
            for dy in range(-pop_radius, pop_radius + 1):
                if dx * dx + dy * dy > r2:
                    continue
                cell = (int(px) + dx, int(py) + dy)
                if 0 <= cell[0] <= w and 0 <= cell[1] <= h and near_pop.get(cell, 0) < pop:
                    near_pop[cell] = int(pop)
    water_points = [
        (float(x), float(y), pop) for (x, y), pop in near_pop.items() if is_water(float(x), float(y))
    ]
    water_points.sort(key=lambda t: (-t[2], t[0], t[1]))
    if rng:
        top = water_points[:180]
        rng.shuffle(top)
        water_points = top + water_points[180:220]
    else:
        water_points = water_points[:220]

    shore_cache = {}

    def to_shore(x: float, y: float):
        """Land point just ashore from (x, y): where the ferry's terminal stands."""
        key = (x, y)
        if key in shore_cache:
            return shore_cache[key]
        best = None  # (distance to land, (dx, dy))
        directions = [(math.cos(2.0 * math.pi * k / 12.0), math.sin(2.0 * math.pi * k / 12.0)) for k in range(12)]
        step = 0.15
        dist = step
        while best is None and dist <= 3.0:
            # Ring by ring outward: the first land found is (to within a step) the nearest.
            for dx, dy in directions:
                if not is_water(x + dx * dist, y + dy * dist):
                    lo, hi = dist - step, dist  # refine where water turns to land
                    for _ in range(5):
                        mid = (lo + hi) / 2.0
                        if is_water(x + dx * mid, y + dy * mid):
                            lo = mid
                        else:
                            hi = mid
                    best = (hi, (dx, dy))
                    break
            dist += step
        shore_cache[key] = None
        if best is not None:
            land_dist, (dx, dy) = best
            for inland in (shore_gap, shore_gap / 2.0, shore_gap * 2.0):
                px, py = x + dx * (land_dist + inland), y + dy * (land_dist + inland)
                if not is_water(px, py):
                    shore_cache[key] = (round(px, 6), round(py, 6))
                    break
        return shore_cache[key]

    # How far inland a terminal stands, in grid units (about 20 m).
    try:
        from map_preset_loader import get_preset_miles_per_grid_unit
        shore_gap = 20.0 / (1609.34 * float(get_preset_miles_per_grid_unit(preset_name) or 1.0))
    except Exception:
        shore_gap = 0.02

    out = []
    seen = set()
    ts = getattr(game_map, "tile_system", None)
    for i in range(len(water_points)):
        x1, y1, _p1 = water_points[i]
        for j in range(i + 1, len(water_points)):
            x2, y2, _p2 = water_points[j]
            d = math.hypot(x2 - x1, y2 - y1)
            if d < min_len or d > max_len:
                continue
            samples = (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)
            hits = sum(1 for t in samples if is_water(x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
            if hits / len(samples) < 0.70:
                continue
            # Terminals stand on the shore: end the route just ashore on each side.
            a, b = to_shore(x1, y1), to_shore(x2, y2)
            if a is None or b is None or math.hypot(b[0] - a[0], b[1] - a[1]) < min_len:
                continue
            x1s, y1s, x2s, y2s = a[0], a[1], b[0], b[1]
            hits = sum(1 for t in samples if is_water(x1s + t * (x2s - x1s), y1s + t * (y2s - y1s)))
            if hits / len(samples) < 0.70:
                continue
            if ts and hasattr(ts, "ferry_corridor_interior_mostly_water"):
                try:
                    if not ts.ferry_corridor_interior_mostly_water(x1s, y1s, x2s, y2s, skip_osm=False, min_fraction=0.88):
                        continue
                except Exception:
                    continue
            key = tuple(sorted(((int(round(x1s)), int(round(y1s))), (int(round(x2s)), int(round(y2s))))))
            if key in seen:
                continue
            seen.add(key)
            out.append([(x1s, y1s), (x2s, y2s)])
            if len(out) >= max(1, min(10, max_points)):
                return out
    return out
