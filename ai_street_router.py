"""Street-graph routing and validation for AI-built lines on OSM road presets.

Rules for AI land lines (ferries are exempt and never come through here):

* The route follows OSM street polylines.
* Moving from one polyline to another may use a short connector of at most
  ``MAX_CONNECTOR_M`` metres.
* A longer off-street segment is allowed only as a water crossing, and it must
  land on (snap to) a street on the far shore.

The road data merges same-named streets into one "polyline", which leaves bogus jump
segments (hundreds of metres to tens of kilometres) between unrelated ways.  Segments
longer than ``MAX_STREET_SEGMENT_M`` are therefore not treated as streets.
"""
from __future__ import annotations

import heapq
import math
from collections import OrderedDict

MAX_CONNECTOR_M = 10.0          # longest off-street link between two polylines
MAX_STREET_SEGMENT_M = 250.0    # longer "segments" are data jumps, not streets
ON_STREET_TOLERANCE_M = 5.0     # a sampled route point this close to a street is on it
SIMPLIFY_TOLERANCE_M = 1.5      # collinear-vertex removal tolerance for returned routes
MAX_ROUTE_SPAN_GRID = 12.0       # longer requests are refused (graph too large to build per query)
MAX_WATER_CROSSING_GRID = 8.0   # longest single water chord (grid units)
WATER_CROSSING_COST_FACTOR = 3.0
CONNECTOR_PENALTY_GRID = 0.02
MAX_DETOUR = 1.8               # longest street route considered, relative to the straight line
MIN_ROUTABLE_COMPONENT = 150    # vertices; smaller networks are isolated scraps
MIN_WATER_FRACTION = 0.4        # share of an off-street chord that must be over water
_SEG_HASH_M = 30.0
_GRAPH_CACHE_MAX = 6
_GRAPH_CACHE: "OrderedDict[tuple, StreetGraph]" = OrderedDict()


class _NoRoads:
    """Stand-in for maps without street data: plain bounds-based geometry, no roads."""
    _CELL_SIZE = 0.02
    _ROAD_SPATIAL_INDEX = None

    @staticmethod
    def _get_bounds(game_map, bounds=None):
        if bounds is not None:
            return bounds
        from map_preset_loader import get_map_bounds
        return get_map_bounds(getattr(game_map, 'map_preset_name', None)) or {}

    @staticmethod
    def _grid_to_latlon(x, y, game_map, bounds=None):
        b = _NoRoads._get_bounds(game_map, bounds)
        w, h = max(1, game_map.width), max(1, game_map.height)
        return (b['min_lat'] + (y / h) * (b['max_lat'] - b['min_lat']),
                b['min_lon'] + (x / w) * (b['max_lon'] - b['min_lon']))

    @staticmethod
    def _build_road_spatial_index(game_map):
        return None

    @staticmethod
    def get_all_roads():
        return {}


def _road_utils(game_map):
    """The street-data module of the map's preset (see map_preset_loader.get_preset_road_utils)."""
    from map_preset_loader import get_preset_road_utils
    return get_preset_road_utils(game_map) or _NoRoads


def grid_metric_scale(game_map, bounds=None) -> tuple[float, float]:
    """Metres per grid unit along x and y."""
    ru = _road_utils(game_map)
    b = ru._get_bounds(game_map, bounds)
    w = max(1, game_map.width)
    h = max(1, game_map.height)
    mid_lat = math.radians((b['min_lat'] + b['max_lat']) / 2.0)
    mx = (b['max_lon'] - b['min_lon']) * 111_320.0 * math.cos(mid_lat) / w
    my = (b['max_lat'] - b['min_lat']) * 110_574.0 / h
    return (mx or 1.0, my or 1.0)


class StreetGraph:
    """Street network in grid coordinates, grown incrementally one spatial-index cell at a time."""

    def __init__(self, mx: float, my: float):
        self.mx = mx
        self.my = my
        self.nodes: list[tuple[float, float]] = []
        self.adj: list[list[tuple[int, float]]] = []
        self._node_ids: dict[tuple[int, int], int] = {}
        self._node_hash: dict[tuple[int, int], list[int]] = {}
        self._seg_hash: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._street_pairs: set[tuple[int, int]] = set()
        self._linked: set[tuple[int, int]] = set()
        self._new_nodes: list[int] = []
        self._new_pairs: list[tuple[int, int]] = []
        self._comp = None
        self.loaded_cells: set[tuple[int, int]] = set()
        self.loaded_segments: set[tuple[int, int]] = set()
        self._seg_pairs: dict[tuple[int, int], tuple[int, int]] = {}  # (road id, index) -> pair
        self._recheck_pairs: list[tuple[int, int]] = []

    # -- construction -------------------------------------------------------
    def _dist_m(self, ax, ay, bx, by) -> float:
        return math.hypot((bx - ax) * self.mx, (by - ay) * self.my)

    def _node(self, x: float, y: float) -> int:
        key = (int(round(x * self.mx * 2.0)), int(round(y * self.my * 2.0)))  # 0.5 m
        nid = self._node_ids.get(key)
        if nid is None:
            nid = len(self.nodes)
            self._node_ids[key] = nid
            self.nodes.append((x, y))
            self.adj.append([])
            hk = (int(x * self.mx // MAX_CONNECTOR_M), int(y * self.my // MAX_CONNECTOR_M))
            self._node_hash.setdefault(hk, []).append(nid)
            self._new_nodes.append(nid)
        return nid

    def _add_edge(self, a: int, b: int, extra: float = 0.0) -> None:
        ax, ay = self.nodes[a]
        bx, by = self.nodes[b]
        w = math.hypot(bx - ax, by - ay) + extra
        self.adj[a].append((b, w))
        self.adj[b].append((a, w))

    def _add_street_pair(self, a: int, b: int) -> tuple[int, int] | None:
        if a == b:
            return None
        pair = (a, b) if a < b else (b, a)
        if pair in self._street_pairs:
            return None
        self._street_pairs.add(pair)
        self._add_edge(a, b)
        self._hash_segment(pair)
        return pair

    def add_street_segment(self, x1, y1, x2, y2) -> tuple[int, int] | None:
        length_m = self._dist_m(x1, y1, x2, y2)
        if length_m <= 0.0 or length_m > MAX_STREET_SEGMENT_M:
            return
        pair = self._add_street_pair(self._node(x1, y1), self._node(x2, y2))
        if pair is not None:
            self._new_pairs.append(pair)
        return pair

    def _hash_segment(self, pair: tuple[int, int]) -> None:
        (x1, y1), (x2, y2) = self.nodes[pair[0]], self.nodes[pair[1]]
        cx1, cx2 = sorted((int(x1 * self.mx // _SEG_HASH_M), int(x2 * self.mx // _SEG_HASH_M)))
        cy1, cy2 = sorted((int(y1 * self.my // _SEG_HASH_M), int(y2 * self.my // _SEG_HASH_M)))
        for cx in range(cx1, cx2 + 1):
            for cy in range(cy1, cy2 + 1):
                self._seg_hash.setdefault((cx, cy), []).append(pair)

    def _nodes_within(self, x: float, y: float, radius_m: float) -> list[tuple[float, int]]:
        hx = int(x * self.mx // MAX_CONNECTOR_M)
        hy = int(y * self.my // MAX_CONNECTOR_M)
        r = max(1, int(math.ceil(radius_m / MAX_CONNECTOR_M)))
        out = []
        for cx in range(hx - r, hx + r + 1):
            for cy in range(hy - r, hy + r + 1):
                for nid in self._node_hash.get((cx, cy), ()):
                    nx, ny = self.nodes[nid]
                    d = self._dist_m(x, y, nx, ny)
                    if d <= radius_m:
                        out.append((d, nid))
        return out

    def _segments_near(self, x: float, y: float, radius_m: float):
        """Street pairs whose hash cells are within radius_m of (x, y) (may include far ones)."""
        cx = int(x * self.mx // _SEG_HASH_M)
        cy = int(y * self.my // _SEG_HASH_M)
        r = max(1, int(math.ceil(radius_m / _SEG_HASH_M)))
        seen = set()
        for hx in range(cx - r, cx + r + 1):
            for hy in range(cy - r, cy + r + 1):
                for pair in self._seg_hash.get((hx, hy), ()):
                    if pair not in seen:
                        seen.add(pair)
                        yield pair

    def finalize(self) -> None:
        """Link the streets added since the last call to each other and to the existing graph.

        * a vertex within MAX_CONNECTOR_M of a segment's interior gets a junction there
          (T junctions / crossings that share no vertex);
        * vertices within MAX_CONNECTOR_M of each other get a connector edge.
        """
        nodes = self.nodes
        node_hash = self._node_hash
        mx, my = self.mx, self.my
        tol = MAX_CONNECTOR_M
        tol_sq = tol * tol
        new_pairs, self._new_pairs = self._new_pairs, []
        # Segments loaded earlier that also touch the newly loaded cells: their interior may
        # now have new vertices next to it, so sample them again too.
        recheck, self._recheck_pairs = self._recheck_pairs, []
        new_pairs = new_pairs + [p for p in recheck if p in self._street_pairs]
        # New segments: sample along them and add a junction wherever any vertex is close.
        for a, b in new_pairs:
            ax, ay = nodes[a]
            bx, by = nodes[b]
            length_m = math.hypot((bx - ax) * mx, (by - ay) * my)
            if length_m <= 2.0 * tol:
                continue
            n = int(length_m // tol)
            for k in range(1, n):
                t = k / n
                px, py = ax + (bx - ax) * t, ay + (by - ay) * t
                pxm, pym = px * mx, py * my
                hx, hy = int(pxm // tol), int(pym // tol)
                hit = False
                for cx in (hx - 1, hx, hx + 1):
                    for cy in (hy - 1, hy, hy + 1):
                        for nid in node_hash.get((cx, cy), ()):
                            if nid == a or nid == b:
                                continue
                            nx, ny = nodes[nid]
                            if (nx * mx - pxm) ** 2 + (ny * my - pym) ** 2 <= tol_sq:
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        break
                if not hit:
                    continue
                p = self._node(px, py)
                if p not in (a, b):
                    self._add_street_pair(a, p)
                    self._add_street_pair(p, b)
        new_nodes, self._new_nodes = self._new_nodes, []
        # Vertex to vertex connectors for every vertex added in this batch.
        for u in new_nodes:
            ux, uy = nodes[u]
            uxm, uym = ux * mx, uy * my
            hx, hy = int(uxm // tol), int(uym // tol)
            for cx in (hx - 1, hx, hx + 1):
                for cy in (hy - 1, hy, hy + 1):
                    for v in node_hash.get((cx, cy), ()):
                        if v == u:
                            continue
                        vx, vy = nodes[v]
                        if (vx * mx - uxm) ** 2 + (vy * my - uym) ** 2 > tol_sq:
                            continue
                        pair = (u, v) if u < v else (v, u)
                        if pair in self._street_pairs or pair in self._linked:
                            continue
                        self._linked.add(pair)
                        self._add_edge(u, v, CONNECTOR_PENALTY_GRID)
        self._comp = None

    # -- queries ------------------------------------------------------------
    def _components(self) -> tuple[list[int], list[int]]:
        """(component id per node, size per component); isolated street scraps get small sizes."""
        if self._comp is None:
            comp = [-1] * len(self.nodes)
            sizes = []
            adj = self.adj
            for seed in range(len(self.nodes)):
                if comp[seed] != -1:
                    continue
                cid = len(sizes)
                comp[seed] = cid
                stack = [seed]
                size = 0
                while stack:
                    u = stack.pop()
                    size += 1
                    for v, _w in adj[u]:
                        if comp[v] == -1:
                            comp[v] = cid
                            stack.append(v)
                sizes.append(size)
            self._comp = (comp, sizes)
        return self._comp

    def nearest_node(self, x: float, y: float, max_m: float, min_component: int = MIN_ROUTABLE_COMPONENT) -> int | None:
        """Nearest vertex that belongs to a usable street network (not an isolated scrap)."""
        comp, sizes = self._components()
        radius = MAX_CONNECTOR_M * 4.0
        while True:
            found = [
                (d, nid) for d, nid in self._nodes_within(x, y, min(radius, max_m))
                if sizes[comp[nid]] >= min_component
            ]
            if found:
                return min(found)[1]
            if radius >= max_m:
                return None
            radius *= 3.0

    def distance_to_street_m(self, x: float, y: float, limit_m: float) -> float:
        """Distance (metres) to the nearest street segment, or inf when farther than limit_m."""
        best = float('inf')
        px, py = x * self.mx, y * self.my
        nodes = self.nodes
        for a, b in self._segments_near(x, y, limit_m):
            (x1, y1), (x2, y2) = nodes[a], nodes[b]
            ax, ay, bx, by = x1 * self.mx, y1 * self.my, x2 * self.mx, y2 * self.my
            dx, dy = bx - ax, by - ay
            den = dx * dx + dy * dy
            t = 0.0 if den <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d < best:
                best = d
        return best if best <= limit_m else float('inf')

    def shortest_paths(
        self, source: int, target: int | None = None, max_dist: float | None = None,
        blocked: set | None = None,
    ) -> tuple[dict, dict]:
        """Dijkstra from source. Stops early when target is settled or max_dist is exceeded.
        Vertices in ``blocked`` are never entered."""
        dist = {source: 0.0}
        prev: dict[int, int] = {}
        heap = [(0.0, source)]
        done = set()
        adj = self.adj
        while heap:
            d, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            if u == target:
                break
            if max_dist is not None and d > max_dist:
                continue
            for v, w in adj[u]:
                if blocked is not None and v in blocked:
                    continue
                nd = d + w
                if nd < dist.get(v, float('inf')):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return dist, prev

    def path_to(self, prev: dict, node: int) -> list[int]:
        out = [node]
        while out[-1] in prev:
            out.append(prev[out[-1]])
        out.reverse()
        return out


def _cell_range_for_points(points, game_map, bounds, margin_cells: int = 1) -> tuple[int, int, int, int]:
    ru = _road_utils(game_map)
    lats, lons = [], []
    for x, y in points:
        lat, lon = ru._grid_to_latlon(x, y, game_map, bounds)
        lats.append(lat)
        lons.append(lon)
    cs = ru._CELL_SIZE
    return (
        int(min(lats) / cs) - margin_cells, int(max(lats) / cs) + margin_cells,
        int(min(lons) / cs) - margin_cells, int(max(lons) / cs) + margin_cells,
    )


_YEAR_THRESHOLDS: dict = {}   # road-data module name -> sorted years in which roads open/close
_MAX_GRAPH_NODES = 700_000   # past this the graph is dropped and rebuilt on demand (memory cap)


def _road_year_bucket(ru, year: int) -> int:
    """Index of the road-data era for a year: the graph only changes when a road opens/closes."""
    name = getattr(ru, '__name__', '')
    thresholds = _YEAR_THRESHOLDS.get(name)
    if thresholds is None:
        marks = set()
        for road in (ru.get_all_roads() or {}).values():
            built = road.get('year')
            if isinstance(built, (int, float)):
                marks.add(int(built))
            removed = road.get('removed_year')
            if isinstance(removed, (int, float)):
                marks.add(int(removed))
        thresholds = _YEAR_THRESHOLDS[name] = sorted(marks)
    import bisect
    return bisect.bisect_right(thresholds, int(year))


def get_street_graph(game_map, points, current_year: int, bounds=None, margin_cells: int = 1) -> StreetGraph | None:
    """Street graph covering the given grid points. None when no road data is loaded.

    One graph per map and road era is kept and grown as new areas are requested, so
    repeated AI route queries reuse the work done for earlier ones.
    """
    ru = _road_utils(game_map)
    ru._build_road_spatial_index(game_map)
    index = ru._ROAD_SPATIAL_INDEX
    if not index:
        return None
    b = ru._get_bounds(game_map, bounds)
    lat0, lat1, lon0, lon1 = _cell_range_for_points(points, game_map, b, margin_cells)
    sig = ru._spatial_index_bounds_signature(game_map)
    year = int(current_year)
    key = (sig, _road_year_bucket(ru, year))
    graph = _GRAPH_CACHE.get(key)
    if graph is not None and len(graph.nodes) > _MAX_GRAPH_NODES:
        graph = None
    if graph is None:
        _GRAPH_CACHE.pop(key, None)
        mx, my = grid_metric_scale(game_map, b)
        graph = StreetGraph(mx, my)
        _GRAPH_CACHE[key] = graph
        while len(_GRAPH_CACHE) > _GRAPH_CACHE_MAX:
            _GRAPH_CACHE.popitem(last=False)
    else:
        _GRAPH_CACHE.move_to_end(key)
    missing = [
        (clat, clon)
        for clat in range(lat0, lat1 + 1)
        for clon in range(lon0, lon1 + 1)
        if (clat, clon) not in graph.loaded_cells
    ]
    if not missing:
        return graph
    seen = graph.loaded_segments
    polys: dict = {}
    for cell in missing:
        graph.loaded_cells.add(cell)
        for _name, road, i in index.get(cell, ()):
            seg_key = (id(road), i)
            if seg_key in seen:
                pair = graph._seg_pairs.get(seg_key)
                if pair is not None:
                    graph._recheck_pairs.append(pair)
                continue
            seen.add(seg_key)
            if road.get('year', 1800) > year:
                continue
            removed = road.get('removed_year')
            if removed is not None and removed <= year:
                continue
            poly = polys.get(id(road))
            if poly is None:
                poly = polys[id(road)] = ru._road_grid_polyline(road, game_map, b)
            if i + 1 >= len(poly):
                continue
            (x1, y1), (x2, y2) = poly[i], poly[i + 1]
            pair = graph.add_street_segment(x1, y1, x2, y2)
            if pair is not None:
                graph._seg_pairs[seg_key] = pair
    graph.finalize()
    return graph


def _water_checker(game_map):
    """Offline point-in-water test in grid coordinates (the preset's water mask, else its water areas)."""
    ru = _road_utils(game_map)
    preset_name = getattr(game_map, 'map_preset_name', None)
    mask = None
    try:
        from map_preset_loader import get_preset_water_mask
        mask = get_preset_water_mask(preset_name)
        if mask is not None and not mask.has_data():
            mask = None
    except Exception:
        mask = None
    preset_check = None
    if mask is None and preset_name:
        try:
            from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
            if preset_has_water_areas(preset_name):
                preset_check = preset_is_point_in_water
        except Exception:
            preset_check = None
    bounds = ru._get_bounds(game_map, None)
    cache: dict[tuple[int, int], bool] = {}

    def _in_water(x: float, y: float) -> bool:
        key = (int(round(x * 200)), int(round(y * 200)))
        hit = cache.get(key)
        if hit is not None:
            return hit
        hit = False
        if mask is not None:
            lat, lon = ru._grid_to_latlon(x, y, game_map, bounds)
            hit = mask.is_water_latlon(lat, lon)
        elif preset_check is not None:
            try:
                hit = bool(preset_check(preset_name, x, y, game_map, skip_osm=True))
            except Exception:
                hit = False
        cache[key] = hit
        return hit

    return _in_water


def _chord_water_fraction(in_water, ax, ay, bx, by, samples: int = 9) -> float:
    hits = 0
    for k in range(1, samples + 1):
        t = k / (samples + 1)
        if in_water(ax + (bx - ax) * t, ay + (by - ay) * t):
            hits += 1
    return hits / samples


def _simplify(points: list[tuple[float, float]], keep: set[int], mx: float, my: float) -> list[tuple[float, float]]:
    """Drop vertices lying within SIMPLIFY_TOLERANCE_M of the straight line being kept."""
    if len(points) <= 2:
        return list(points)
    out = [points[0]]
    anchor = 0
    i = 2
    while i <= len(points):
        end = i if i < len(points) else len(points) - 1
        ax, ay = points[anchor][0] * mx, points[anchor][1] * my
        bx, by = points[end][0] * mx, points[end][1] * my
        dx, dy = bx - ax, by - ay
        den = math.hypot(dx, dy)
        ok = den > 0 and i < len(points)
        if ok:
            for j in range(anchor + 1, end):
                if j in keep:
                    ok = False
                    break
                px, py = points[j][0] * mx, points[j][1] * my
                if abs((px - ax) * dy - (py - ay) * dx) / den > SIMPLIFY_TOLERANCE_M:
                    ok = False
                    break
        if ok:
            i += 1
            continue
        anchor = i - 1
        out.append(points[anchor])
        if i >= len(points):
            break
        i = anchor + 2
        if i > len(points) - 1:
            out.append(points[-1])
            break
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def find_ai_street_route(
    game_map,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    current_year: int,
    bounds=None,
    *,
    max_snap_grid: float = 1.5,
    allow_water_crossing: bool = True,
    avoid_route: list | None = None,
    start_snap_grid: float | None = None,
) -> list[tuple[float, float]]:
    """Grid polyline from A to B along streets ([] when no compliant route exists).

    ``start_snap_grid``: tighter snap radius for A (an extension must start at the line end).
    ``avoid_route``: an existing line being extended from A. Street vertices within
    AVOID_LINE_M of it are not used (except right at A), so the extension leaves the line
    instead of running back along it.

    Endpoints are snapped to the nearest street vertex.  When the two ends are on
    different land masses, one straight water chord between shore streets is allowed.
    """
    ax, ay, bx, by = float(ax), float(ay), float(bx), float(by)
    span = math.hypot(bx - ax, by - ay)
    if span > MAX_ROUTE_SPAN_GRID:
        return []
    margin = 1 if span < 6.0 else 2
    graph = get_street_graph(game_map, [(ax, ay), (bx, by)], current_year, bounds, margin_cells=margin)
    if graph is None or not graph.nodes:
        return []
    snap_m = max_snap_grid * max(graph.mx, graph.my)
    start_snap_m = snap_m if start_snap_grid is None else start_snap_grid * max(graph.mx, graph.my)
    start = graph.nearest_node(ax, ay, start_snap_m, min_component=1 if start_snap_grid is not None else MIN_ROUTABLE_COMPONENT)
    goal = graph.nearest_node(bx, by, snap_m)
    if start is None or goal is None or start == goal:
        return []

    # Street routes longer than MAX_DETOUR x the straight line are rejected later anyway, so
    # the searches never need to look farther (and stay cheap when the goal is unreachable).
    sx0, sy0 = graph.nodes[start]
    gx0, gy0 = graph.nodes[goal]
    limit = MAX_DETOUR * math.hypot(gx0 - sx0, gy0 - sy0) + 0.5
    blocked = _vertices_near_route(graph, avoid_route, keep_near=(ax, ay)) if avoid_route else None
    if blocked:
        blocked.discard(start)
        blocked.discard(goal)
    dist_s, prev_s = graph.shortest_paths(start, goal, max_dist=limit, blocked=blocked)
    water_edges: set[int] = set()
    if goal in dist_s and dist_s[goal] <= limit:
        node_path = graph.path_to(prev_s, goal)
    else:
        if not allow_water_crossing:
            return []
        dist_g, prev_g = graph.shortest_paths(goal, max_dist=limit, blocked=blocked)
        gx, gy = graph.nodes[goal]
        sx, sy = graph.nodes[start]
        nodes = graph.nodes
        # Alternating refinement over all reachable nodes finds the short shore-to-shore chord.
        f = WATER_CROSSING_COST_FACTOR
        u, v = start, goal
        for _round in range(4):
            vx, vy = nodes[v]
            u = min(dist_s, key=lambda n: dist_s[n] + f * math.hypot(nodes[n][0] - vx, nodes[n][1] - vy))
            ux, uy = nodes[u]
            v = min(dist_g, key=lambda n: dist_g[n] + f * math.hypot(nodes[n][0] - ux, nodes[n][1] - uy))
        ux, uy = nodes[u]
        vx, vy = nodes[v]
        cand_s = heapq.nsmallest(
            60, dist_s, key=lambda n: dist_s[n] + f * math.hypot(nodes[n][0] - vx, nodes[n][1] - vy))
        cand_g = heapq.nsmallest(
            60, dist_g, key=lambda n: dist_g[n] + f * math.hypot(nodes[n][0] - ux, nodes[n][1] - uy))
        pairs = []
        for cu in cand_s[::3] + [u]:
            cux, cuy = nodes[cu]
            for cv in cand_g[::3] + [v]:
                chord = math.hypot(nodes[cv][0] - cux, nodes[cv][1] - cuy)
                if chord <= MAX_WATER_CROSSING_GRID:
                    pairs.append((dist_s[cu] + chord * f + dist_g[cv], cu, cv))
        pairs.sort()
        in_water = _water_checker(game_map)
        node_path = None
        for _cost, u, v in pairs[:120]:
            ux, uy = nodes[u]
            vx, vy = nodes[v]
            if _chord_water_fraction(in_water, ux, uy, vx, vy) < MIN_WATER_FRACTION:
                continue
            first = graph.path_to(prev_s, u)
            second = graph.path_to(prev_g, v)
            second.reverse()
            water_edges.add(len(first) - 1)
            node_path = first + second
            break
        if node_path is None:
            return []

    points = [graph.nodes[n] for n in node_path]
    keep = set()
    for idx in water_edges:  # keep both shore vertices of a water chord
        keep.add(idx)
        keep.add(idx + 1)
    return _simplify(points, keep, graph.mx, graph.my)


AVOID_LINE_M = 25.0
AVOID_LINE_KEEP_M = 40.0


def _vertices_near_route(graph: StreetGraph, route: list, keep_near=None) -> set:
    """Street vertices within AVOID_LINE_M of a polyline (excluding those near keep_near)."""
    out: set = set()
    pts = [(float(p[0]), float(p[1])) for p in route or []]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        length_m = graph._dist_m(x1, y1, x2, y2)
        n = max(1, int(length_m // 10.0))
        for k in range(n + 1):
            t = k / n
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            for _d, nid in graph._nodes_within(px, py, AVOID_LINE_M):
                out.add(nid)
    if keep_near is not None and out:
        kx, ky = keep_near
        out = {nid for nid in out if graph._dist_m(kx, ky, *graph.nodes[nid]) > AVOID_LINE_KEEP_M}
    return out


def find_ai_street_route_toward(
    game_map,
    ax: float,
    ay: float,
    tx: float,
    ty: float,
    current_year: int,
    min_len: float,
    max_len: float,
    bounds=None,
    *,
    max_snap_grid: float = 1.5,
    accept=None,
) -> list[tuple[float, float]]:
    """Street route starting at A and heading toward T, between min_len and max_len long.

    Used when A and T cannot be joined (too far apart, or no compliant route): the line
    still starts at A and ends on the reachable street vertex closest to T.  ``accept(x, y)``
    may veto end points (e.g. no population there).
    """
    ax, ay, tx, ty = float(ax), float(ay), float(tx), float(ty)
    graph = get_street_graph(game_map, [(ax - max_len, ay - max_len), (ax + max_len, ay + max_len)],
                             current_year, bounds, margin_cells=0)
    if graph is None or not graph.nodes:
        return []
    start = graph.nearest_node(ax, ay, max_snap_grid * max(graph.mx, graph.my))
    if start is None:
        return []
    dist, prev = graph.shortest_paths(start, max_dist=max_len)
    nodes = graph.nodes
    sx, sy = nodes[start]
    ranked = sorted(
        (n for n, d in dist.items()
         if min_len <= d <= max_len and d <= 1.6 * math.hypot(nodes[n][0] - sx, nodes[n][1] - sy)),
        key=lambda n: math.hypot(nodes[n][0] - tx, nodes[n][1] - ty),
    )
    for n in ranked[:200]:
        if accept is not None and not accept(nodes[n][0], nodes[n][1]):
            continue
        points = [nodes[k] for k in graph.path_to(prev, n)]
        return _simplify(points, set(), graph.mx, graph.my)
    return []


def ai_route_violations(
    game_map,
    route_points: list,
    current_year: int,
    bounds=None,
    *,
    allow_water_crossing: bool = True,
) -> list[tuple[int, str]]:
    """Segments breaking the AI street rules, as (segment_index, reason). [] when compliant.

    Returns [] as well when no road data is available for the map (nothing to check against).
    """
    pts = [(float(p[0]), float(p[1])) for p in (route_points or [])]
    if len(pts) < 2:
        return []
    graph = get_street_graph(game_map, pts, current_year, bounds)
    if graph is None or not graph.nodes:
        return []
    in_water = None
    out = []
    step_m = MAX_CONNECTOR_M
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        length_m = graph._dist_m(x1, y1, x2, y2)
        if length_m <= MAX_CONNECTOR_M:
            continue
        n = max(1, int(math.ceil(length_m / step_m)))
        off = []
        for k in range(n + 1):
            t = k / n
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            if graph.distance_to_street_m(px, py, ON_STREET_TOLERANCE_M) == float('inf'):
                off.append((k, px, py))
        if not off:
            continue
        # Off-street run no longer than a connector is fine (switching polylines mid-segment).
        ks = [k for k, _x, _y in off]
        longest_run = cur = 1
        for a, b in zip(ks, ks[1:]):
            cur = cur + 1 if b == a + 1 else 1
            longest_run = max(longest_run, cur)
        if (longest_run + 1) * (length_m / n) <= MAX_CONNECTOR_M:
            continue
        if not allow_water_crossing:
            out.append((i, "leaves the street network"))
            continue
        if in_water is None:
            in_water = _water_checker(game_map)
        interior = [(x, y) for k, x, y in off if 0 < k < n]
        wet = sum(1 for x, y in interior if in_water(x, y))
        wet_share = max(
            wet / len(interior) if interior else 0.0,
            _chord_water_fraction(in_water, x1, y1, x2, y2),  # same test the router applies
        )
        if wet_share < MIN_WATER_FRACTION:
            out.append((i, "off-street segment longer than 10 m that is not a water crossing"))
            continue
        ends_on_street = all(
            graph.distance_to_street_m(x, y, MAX_CONNECTOR_M) != float('inf')
            for x, y in ((x1, y1), (x2, y2))
        )
        if not ends_on_street:
            out.append((i, "water crossing does not start and end on a street"))
    return out


def ai_route_rule_error(
    game_map, route_points: list, current_year: int, *, allow_water_crossing: bool = True,
    for_extension: bool = False, segments: tuple | None = None,
) -> str | None:
    """Human-readable rule violation for an AI land route, or None when it complies.

    ``segments``: optional (first, end) segment-index range to judge; others are ignored.
    """
    bad = ai_route_violations(
        game_map, route_points, current_year, allow_water_crossing=allow_water_crossing,
    )
    if segments is not None:
        lo, hi = segments
        bad = [(i, reason) for i, reason in bad if lo <= i < hi]
    if not bad:
        return None
    idx, reason = bad[0]
    kind = "AI extensions" if for_extension else "AI lines"
    return (
        f"{kind} must follow OSM street polylines (connectors up to {MAX_CONNECTOR_M:.0f} m; "
        f"longer off-street segments only as water crossings that land on a street). "
        f"Segment {idx + 1} {reason} ({len(bad)} bad segment(s))."
    )


def route_doubles_back(
    points: list,
    mx: float,
    my: float,
    *,
    lateral_m: float = 300.0,
    min_run_m: float = 150.0,
    hairpin_deg: float = 150.0,
    step_m: float = 5.0,
    revisit_m: float = 60.0,
    min_end_ratio: float = 0.3,
) -> bool:
    """True when a route doubles back on itself.

    * loop: the route returns within ``revisit_m`` of a point it passed at least 300 m
      earlier (closing a loop or crossing itself), or its two ends are closer together
      than ``min_end_ratio`` of its length (curling back toward its own start);
    * hairpin: a turn of ``hairpin_deg`` or more between two segments longer than 3 m;
    * reversal: for at least ``min_run_m`` the route heads the opposite way (within 30
      degrees) within ``lateral_m`` of an earlier part of itself - back along the same
      street, the other carriageway, or a nearby parallel street or avenue (city
      avenues can be ~250 m apart).

    Same-direction parallel legs (a "staircase" through a street grid) are not doubling back.
    """
    pts = [(float(p[0]) * mx, float(p[1]) * my) for p in (points or [])]
    if len(pts) < 3:
        return False
    # Curling back toward its own start: on a line over 1 km, the two ends must be at least
    # ``min_end_ratio`` of the route length apart (an L is ~0.7, a wide U ~0.33, a loop ~0).
    total_m = sum(
        math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)
    )
    if total_m > 1000.0:
        ends_m = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if ends_m < min_end_ratio * total_m:
            return True
    cos_hairpin = math.cos(math.radians(hairpin_deg))
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la < 3.0 or lb < 3.0:
            continue
        if (ax * bx + ay * by) / (la * lb) <= cos_hairpin:
            return True

    samples = []  # (x, y, arc, ux, uy)
    arc = 0.0
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg <= 0:
            continue
        ux, uy = (x2 - x1) / seg, (y2 - y1) / seg
        n = max(1, int(seg // step_m))
        for k in range(n):
            t = k / n
            samples.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, arc + seg * t, ux, uy))
        arc += seg
    cos_rev = -math.cos(math.radians(30.0))
    lat2 = lateral_m * lateral_m
    revisit2 = revisit_m * revisit_m
    revisit_gap = max(4.0 * revisit_m, 300.0)  # arc distance that makes a nearby sample a "return"
    grid: dict[tuple[int, int], list[int]] = {}
    run = 0.0
    prev_flag = False
    for j, (x, y, s, ux, uy) in enumerate(samples):
        gx, gy = int(x // lateral_m), int(y // lateral_m)
        flagged = False
        for cx in (gx - 1, gx, gx + 1):
            for cy in (gy - 1, gy, gy + 1):
                for i in grid.get((cx, cy), ()):
                    xi, yi, si, uxi, uyi = samples[i]
                    d2 = (xi - x) ** 2 + (yi - y) ** 2
                    if d2 <= revisit2 and s - si >= revisit_gap:
                        return True  # came back to a place the route already passed (a loop)
                    if ux * uxi + uy * uyi > cos_rev:
                        continue  # not heading back the opposite way
                    if d2 <= lat2:
                        flagged = True
                        break
                if flagged:
                    break
            if flagged:
                break
        if flagged:
            run = run + step_m if prev_flag else step_m
            if run >= min_run_m:
                return True
        prev_flag = flagged
        grid.setdefault((gx, gy), []).append(j)
    return False
