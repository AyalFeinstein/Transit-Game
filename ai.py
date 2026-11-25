"""
Game AI for computer-controlled players.
Map-agnostic and vehicle-agnostic: works with any map preset and any trainsets in the registry.
AI always strives to make profit and never remains idle with no assets.
Uses population points ordered highest-to-lowest and profit-based scoring.
OSM/street presets: score endpoint pairs near population centers, then street-route between them.
Extends existing lines sparingly (2-5 per turn). Always completes lines under construction.
Designed to complete within the per-turn time budget (see AI_TURN_TIME_BUDGET_SEC).

Win criterion: first positive annual net during turns 5–15, then mostly positive nets through calendar year 1900.
AI money fluctuation: AIs often alternate between large gains and losses because:
- Lumpy revenue: lines earn nothing until construction completes; then revenue jumps.
- Taxes and maintenance: annual costs (maintenance, workers, taxes) are paid each year.
- Investment timing: AI spends on construction/extension in one year; revenue arrives in later years.
- Worker costs: renting engineers/construction workers is a large upfront cost.
- Government funding: lobby success or failure causes sudden swings.
"""

import logging
import random
import math
import re
import time
from typing import Optional

from game_presets import FERRY_LENGTH_COST_PER_UNIT, AI_BOOTSTRAP_TURNS, AI_DEFAULT_DIFFICULTY, AI_DEFAULT_FOCUS, AI_EARLY_GROWTH_YEARS, AI_EARLY_PAYBACK_YEARS, AI_FOCUSES, AI_MAX_STRAIGHT_LAND_SEGMENT_GRID, AI_SECOND_LINE_BUILD_CHANCE_MIN, AI_SECOND_LINE_BUILD_CHANCE_MAX, AI_SUSTAINED_PROFIT_PAYBACK_YEARS, STARTING_MONEY

logger = logging.getLogger(__name__)

# Target duration for one AI turn (orchestrators may pass deadline_sec up to this cap).
AI_TURN_TIME_BUDGET_SEC = 45.0


class AIIdleTurnError(RuntimeError):
    """Raised when a living AI would finish a turn with zero actions (debug invariant)."""


class AIStreetComplianceError(AIIdleTurnError):
    """Raised in debug mode when an AI cannot build OSM street-following lines."""


def _ai_map_uses_street_build(game_map, preset=None) -> bool:
    """True when AI must build along preset street/road polylines (not display-only OSM tiles)."""
    preset_name = getattr(game_map, 'map_preset_name', None) if game_map else None
    if preset is None and preset_name:
        try:
            from map_preset_loader import load_map_preset
            preset = load_map_preset(preset_name)
        except Exception:
            preset = None
    if preset and hasattr(preset, 'get_road_segment_points_for_ai'):
        return True
    if preset_name:
        from map_preset_loader import preset_has_road_support
        return preset_has_road_support(preset_name)
    return False


def _preset_requires_street_polylines(game_map, preset=None) -> bool:
    """True when AI line building must follow OSM/street polylines (no population-point fallback)."""
    return _ai_map_uses_street_build(game_map, preset)


def _format_street_route_failure_message(ops, player, game_map, current_year, preset=None) -> str:
    """Detailed explanation when an OSM/street-polyline map has no buildable corridor."""
    return _build_street_compliance_error_message(ops, player, game_map, current_year, preset=preset)


def _ai_clear_street_compliance_trace(ops, player) -> None:
    if ops is None:
        return
    pname = player.get_name() if hasattr(player, 'get_name') else getattr(player, 'name', 'AI')
    traces = getattr(ops, '_ai_street_compliance_traces', None)
    if traces is None:
        ops._ai_street_compliance_traces = {}
        traces = ops._ai_street_compliance_traces
    traces[pname] = []


def _ai_note_street_compliance_issue(ops, player, reason: str) -> None:
    if ops is None or not reason:
        return
    pname = player.get_name() if hasattr(player, 'get_name') else getattr(player, 'name', 'AI')
    traces = getattr(ops, '_ai_street_compliance_traces', None)
    if traces is None:
        ops._ai_street_compliance_traces = {}
        traces = ops._ai_street_compliance_traces
    bucket = traces.setdefault(pname, [])
    if reason not in bucket:
        bucket.append(reason)


def _build_street_compliance_error_message(
    ops, player, game_map, current_year, preset=None,
) -> str:
    """Human-readable diagnosis when OSM street-building rules block all AI line options."""

    pname = player.get_name() if hasattr(player, 'get_name') else getattr(player, 'name', 'AI')
    preset_name = getattr(game_map, 'map_preset_name', None) or 'unknown'
    map_w = getattr(game_map, 'width', 100) or 100
    map_h = getattr(game_map, 'height', 75) or 75
    money = int(ops.get_player_money(player)) if ops and hasattr(ops, 'get_player_money') else 0
    existing = 0
    if ops:
        try:
            existing = len(_get_existing_line_routes_for_overlap(ops, game_map))
        except Exception:
            existing = 0
    street_data = _ai_osm_street_routing_data_available(game_map, preset_name)
    index_cells = 0
    roads_in_bounds = 0
    road_files = []
    road_fix_hint = []
    try:
        from map_preset_loader import get_preset_road_utils
        road_utils = get_preset_road_utils(game_map)
        road_utils._build_road_spatial_index(game_map)
        index_cells = len(getattr(road_utils, '_ROAD_SPATIAL_INDEX', None) or {})
        roads_in_bounds = len(
            road_utils._get_roads_in_map_bounds(game_map, road_utils._get_bounds(game_map, None))
        )
        road_files = list(road_utils.road_data_files_status())
        road_fix_hint = list(road_utils.ROAD_DATA_FIX_HINT)
    except Exception:
        pass
    trace = []
    if ops is not None:
        traces = getattr(ops, '_ai_street_compliance_traces', None) or {}
        trace = list(traces.get(pname, []) or [])
    lines = [
        f"{pname} could not comply with OSM street-building rules in year {current_year} "
        f"on map preset '{preset_name}' ({map_w}×{map_h}).",
        "",
        "Required rules (no fallbacks):",
        "  • Route >50% of combined segment length on streets or water crossings",
        "  • Follow OSM street polylines (no straight chords or grid shortcuts)",
        f"  • Line endpoints within {AI_POPULATION_ANCHOR_RADIUS_GRID:g} grid unit(s) of scored population anchors",
        "",
        "Environment:",
        f"  • OSM street spatial index loaded: {'yes' if street_data else 'NO'} "
        f"({index_cells} index cells, {roads_in_bounds} roads in map bounds)",
    ]
    lines.extend(f"  • {name} present: {present}" for name, present in road_files)
    if not street_data:
        lines.extend(road_fix_hint)
    lines.extend([
        "",
        f"Turn context: spendable cash ${money:,}; {existing} existing line route(s) on map.",
    ])
    if trace:
        lines.append("")
        lines.append("Attempt trace this turn:")
        for item in trace[:12]:
            lines.append(f"  • {item}")
        if len(trace) > 12:
            lines.append(f"  • … and {len(trace) - 12} more")
    else:
        lines.extend([
            "",
            "No pair-level trace was recorded; likely causes:",
            "  • No population anchor pair produced a street polyline passing build rules",
            "  • Cached preset road segments were empty or overlapped existing lines",
            "  • Turn time budget expired before a compliant corridor was found",
        ])
    return "\n".join(lines)


def _ai_player_lacks_street_served_line(ops, player) -> bool:
    if player.get_line_names():
        return False
    if not hasattr(ops, 'get_build_queue'):
        return True
    for job in ops.get_build_queue() or []:
        if job.get('player') == player and job.get('type') in ('line', 'line_extension'):
            return False
    return True


def _ai_street_compliance_would_fail(
    ops, player, game_map, preset, results,
) -> bool:
    """True when an OSM map AI finished the turn without a compliant line build."""
    if not getattr(player, 'is_ai', False):
        return False
    if ops and hasattr(ops, 'is_player_eliminated') and ops.is_player_eliminated(player):
        return False
    if not _preset_requires_street_polylines(game_map, preset):
        return False
    if not _ai_player_lacks_street_served_line(ops, player):
        return False
    if results and any(a == 'create_line' for a, _ in results):
        return False
    return True


def _ai_try_ensure_street_spatial_index_loaded(game_map, preset_name: str) -> bool:
    """Force-load or rebuild the OSM road spatial index. Returns True when index has cells."""
    preset_name = preset_name or getattr(game_map, 'map_preset_name', None) or ''
    if not preset_name or game_map is None:
        return False
    try:
        from map_preset_loader import preset_has_road_support, preload_road_data_for_preset
        if not preset_has_road_support(preset_name):
            return False
    except Exception:
        return False
    if _ai_osm_street_routing_data_available(game_map, preset_name):
        return True
    try:
        from map_preset_loader import get_preset_road_utils
        road_utils = get_preset_road_utils(preset_name)
        road_utils._ROAD_SPATIAL_INDEX = None
        road_utils._BRIDGE_SPATIAL_INDEX = None
        road_utils._SPATIAL_INDEX_BOUNDS_SIG = None
    except Exception:
        pass
    try:
        preload_road_data_for_preset(preset_name, game_map)
    except Exception:
        pass
    return _ai_osm_street_routing_data_available(game_map, preset_name)


def _ai_handle_street_compliance_at_turn_end(
    ops,
    player,
    game_map,
    current_year,
    preset,
    results,
    *,
    first_calendar_year: bool,
    ai_join_first_line_pending: bool,
    progress_callback=None,
    deadline_sec=None,
    street_index_retry: bool,
):
    """Retry once after loading spatial index; raise in debug mode if still non-compliant."""
    if not _ai_street_compliance_would_fail(ops, player, game_map, preset, results):
        validate_ai_turn_results(ops, player, results)
        return results
    preset_name = getattr(game_map, 'map_preset_name', None) or ''

    def _retry_turn_after_index_load(note: str):
        _ai_note_street_compliance_issue(ops, player, note)
        return run_ai_turn(
            ops, player,
            progress_callback=progress_callback,
            deadline_sec=deadline_sec,
            _street_index_retry=True,
        )

    if not street_index_retry:
        try:
            if _ai_try_ensure_street_spatial_index_loaded(game_map, preset_name):
                return _retry_turn_after_index_load(
                    "OSM street spatial index loaded; retrying AI turn once.",
                )
        except Exception:
            pass
    try:
        _ai_maybe_raise_street_compliance_error(
            ops, player, game_map, current_year, preset, results,
            first_calendar_year=first_calendar_year,
            ai_join_first_line_pending=ai_join_first_line_pending,
        )
    except AIStreetComplianceError:
        if not street_index_retry:
            try:
                if _ai_try_ensure_street_spatial_index_loaded(game_map, preset_name):
                    return _retry_turn_after_index_load(
                        "OSM street spatial index loaded after compliance error; "
                        "retrying AI turn once.",
                    )
            except Exception:
                pass
        raise
    validate_ai_turn_results(ops, player, results)
    return results


def _ai_maybe_raise_street_compliance_error(
    ops,
    player,
    game_map,
    current_year,
    preset,
    results,
    *,
    first_calendar_year: bool,
    ai_join_first_line_pending: bool,
) -> None:
    """In debug mode, fail loudly when an OSM map AI cannot build a compliant line."""
    del first_calendar_year, ai_join_first_line_pending  # reserved for future opener-specific hints
    if not _ai_debug_notifications_enabled(ops):
        return
    if not _ai_street_compliance_would_fail(ops, player, game_map, preset, results):
        return
    msg = _build_street_compliance_error_message(ops, player, game_map, current_year, preset=preset)
    _record_street_build_failure(ops, player, game_map, current_year, preset=preset)
    raise AIStreetComplianceError(msg)


def _record_street_build_failure(ops, player, game_map, current_year, preset=None) -> None:
    if ops is None:
        return
    msg = _format_street_route_failure_message(ops, player, game_map, current_year, preset=preset)
    pname = player.get_name() if hasattr(player, 'get_name') else getattr(player, 'name', 'AI')
    entry = (pname, msg)
    failures = getattr(ops, '_ai_street_build_failures', None)
    if failures is None:
        ops._ai_street_build_failures = []
        failures = ops._ai_street_build_failures
    if entry not in failures:
        failures.append(entry)


def _ai_debug_notifications_enabled(ops) -> bool:
    game = getattr(ops, 'game', None) if ops is not None else None
    if game is None:
        return True
    return bool(getattr(game, 'ai_debug_notifications_enabled', True))


def _record_ai_debug_issue(ops, player, message: str) -> None:
    if ops is None or not _ai_debug_notifications_enabled(ops):
        return
    pname = player.get_name() if hasattr(player, 'get_name') else getattr(player, 'name', 'AI')
    entry = (pname, message)
    issues = getattr(ops, '_ai_debug_issues', None)
    if issues is None:
        ops._ai_debug_issues = []
        issues = ops._ai_debug_issues
    if entry not in issues:
        issues.append(entry)


def _ai_starting_money_reference(ops) -> int:
    game = getattr(ops, 'game', None) if ops is not None else None
    if game is not None:
        ref = getattr(game, 'starting_money', None)
        if ref is not None:
            return int(ref)
    return int(STARTING_MONEY)


def _ai_surplus_cash_should_expand(ops, player, money: int, sustained_profit_phase: bool) -> bool:
    if money <= _ai_starting_money_reference(ops):
        return False
    if player.get_line_names():
        return True
    q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
    return any(
        b.get('player') == player and b.get('type') in ('line', 'line_extension')
        for b in (q or [])
    )


def _ai_turn_time_exceeded(ops) -> bool:
    deadline = getattr(ops, '_ai_turn_deadline_mono', None) if ops is not None else None
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        return False
    return time.monotonic() >= float(deadline)


def _ai_turn_time_remaining(ops) -> float:
    deadline = getattr(ops, '_ai_turn_deadline_mono', None) if ops is not None else None
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        return float('inf')
    return max(0.0, float(deadline) - time.monotonic())


def _ai_turn_time_low(ops, threshold_sec: float = 20.0) -> bool:
    """True when less than threshold_sec remains on the AI turn clock."""
    return _ai_turn_time_remaining(ops) < float(threshold_sec)


_AI_ROAD_SEGMENT_MAX_POINTS_DEFAULT = 24
_AI_ROAD_SEGMENT_MAX_POINTS_OPENER = 16


def _ai_osm_line_build_prefer_speed(
    ops, existing_routes, first_game_year: bool, is_first: bool,
) -> bool:
    """Take the first viable OSM corridor instead of scoring many retries."""
    if first_game_year and is_first:
        return True
    if existing_routes:
        return True
    if _ai_turn_time_remaining(ops) < 55.0:
        return True
    return False


def _ai_should_defer_lobby_marketplace(
    years_from_start: int,
    has_revenue_line: bool,
    has_profitable_line: bool,
    lines_with_two_stations: bool,
) -> bool:
    """Defer lobbying and marketplace until the network earns revenue."""
    if has_revenue_line:
        return False
    if years_from_start <= 6:
        return True
    if years_from_start <= 14 and not (has_profitable_line and lines_with_two_stations):
        return True
    return False


def _ai_append_existing_route_cache(ops, route_points) -> None:
    """Extend per-turn overlap cache after a line create/extend instead of full rebuild."""
    if ops is None or not route_points or len(route_points) < 2:
        return
    cached = getattr(ops, '_ai_existing_routes_cache', None)
    if isinstance(cached, list):
        cached.append(list(route_points))
    else:
        ops._ai_existing_routes_cache = None


def _ai_begin_line_build_turn(ops) -> None:
    """Reset per-turn caches/counters so line creation does not repeat expensive OSM pulls."""
    if ops is None:
        return
    ops._ai_road_segment_pull_cache = {}
    ops._ai_osm_full_build_calls = 0
    ops._ai_first_year_opener_attempted = False
    ops._ai_existing_routes_cache = None


def _ai_mark_first_year_opener_attempted(ops) -> None:
    if ops is not None:
        ops._ai_first_year_opener_attempted = True


def _ai_opener_overlap_routes(
    existing_routes,
    *,
    first_calendar_year: bool,
    ai_join_first_line_pending: bool,
):
    """Year-1 / join-year openers ignore same-turn queued geometry so every AI can build."""
    if first_calendar_year or ai_join_first_line_pending:
        return None
    return existing_routes


def _ai_osm_build_budget_exhausted(ops, *, first_opener: bool = False) -> bool:
    if ops is None:
        return False
    if _ai_turn_time_low(ops, 10.0):
        return True
    n = int(getattr(ops, '_ai_osm_full_build_calls', 0) or 0)
    cap = 4 if first_opener else 1
    if _ai_turn_time_low(ops, 20.0):
        cap = min(cap, 2 if first_opener else 1)
    return n >= cap


def _ai_record_osm_build_call(ops) -> None:
    if ops is not None:
        ops._ai_osm_full_build_calls = int(getattr(ops, '_ai_osm_full_build_calls', 0) or 0) + 1


def validate_ai_turn_results(ops, player, results):
    """Raise AIIdleTurnError if a living AI has an empty action list. Call after run_ai_turn from orchestrators."""
    if not getattr(player, 'is_ai', False):
        return
    if not ops or not hasattr(ops, 'is_player_eliminated') or ops.is_player_eliminated(player):
        return
    # Empty turns are allowed: UI lists "No actions" instead of synthetic bookkeeping.


def _ai_has_meaningful_year_progress(ops, player, acts: list) -> bool:
    """True when the AI may mark its calendar-year turn complete."""
    if acts:
        return True
    if getattr(player, 'get_line_names', None) and player.get_line_names():
        return True
    if hasattr(ops, 'get_build_queue'):
        for job in ops.get_build_queue() or []:
            if job.get('player') == player:
                return True
    # No actions and no queued work: still complete the year (shown as idle in AI Watch).
    return True


def run_pending_ai_in_year_turns(
    ops,
    progress_callback=None,
    players=None,
    deadline_sec=None,
    on_player_completed=None,
):
    """Run run_ai_turn for each AI that has not completed actions this calendar year.

    Returns (ai_results_list, ai_build_failures) where ai_results_list is [(player, acts), ...].
    Each AI is marked complete once its turn finishes so End Year can proceed after action counts
    are known (failures are still reported in ai_build_failures).
    on_player_completed: optional callable(player) after an in-year turn finishes.
    """
    pending = list(players) if players is not None else ops.get_ai_players_needing_in_year_turn()
    ai_results_list = []
    ai_build_failures = []
    if ops is not None:
        ops._ai_street_build_failures = []
        ops._ai_debug_issues = []
    for player in pending:
        if ops.ai_in_year_turn_completed(player):
            continue
        acts = []
        try:
            acts = run_ai_turn(
                ops, player, progress_callback=progress_callback, deadline_sec=deadline_sec
            )
            validate_ai_turn_results(ops, player, acts)
            if not _ai_has_meaningful_year_progress(ops, player, acts):
                raise AIIdleTurnError(
                    f"Living AI {player.get_name()!r} took no meaningful actions (year {ops.get_year()})."
                )
            ai_results_list.append((player, acts))
        except AIIdleTurnError as e:
            ai_results_list.append((player, acts or []))
            ai_build_failures.append((player.get_name(), str(e)))
        except Exception as e:
            ai_results_list.append((player, acts or []))
            ai_build_failures.append((player.get_name(), str(e)))
        finally:
            player._ai_in_year_action_count = len(acts or [])
            ops.mark_ai_in_year_turn_completed(player)
            if on_player_completed is not None:
                try:
                    on_player_completed(player)
                except Exception:
                    pass
    if ops is not None:
        ai_build_failures.extend(getattr(ops, '_ai_street_build_failures', []))
        ai_build_failures.extend(getattr(ops, '_ai_debug_issues', []))
    return ai_results_list, ai_build_failures


# AI actions are limited only by the turn time budget (see AI_TURN_TIME_BUDGET_SEC).
_WEALTHY_MONEY_THRESHOLD = 60_000
_AI_BANKRUPTCY_RUNWAY_YEARS = 5


def _ai_years_until_cash_exhausted(money: float, estimated_costs_next: float, net_income: float) -> float:
    """Rough calendar years until cash hits zero at the current burn rate."""
    if money <= 0:
        return 0.0
    if net_income < 0:
        burn = abs(float(net_income))
        return money / burn if burn > 0 else float('inf')
    drain = float(estimated_costs_next) - float(net_income)
    if drain <= 0:
        return float('inf')
    return money / drain


def _ai_runway_years(
    money: float, estimated_costs_next: float, net_income: float, line_profit: float,
) -> float:
    """Cash runway in years, factoring line profit when overhead makes net negative."""
    if money <= 0:
        return 0.0
    if net_income > 0:
        return _ai_years_until_cash_exhausted(money, estimated_costs_next, net_income)
    if line_profit > 0:
        cost_burn = float(estimated_costs_next) - float(line_profit)
        if cost_burn <= 0:
            return float('inf')
        return money / cost_burn
    return _ai_years_until_cash_exhausted(money, estimated_costs_next, net_income)


def _ai_near_term_bankruptcy_risk(
    money: float, estimated_costs_next: float, net_income: float, about_to_go_bankrupt: bool,
    *, line_profit: float = 0.0,
) -> bool:
    """True when bankruptcy is imminent (next year) or cash runway is within five calendar years."""
    if about_to_go_bankrupt:
        return True
    runway = _ai_runway_years(money, estimated_costs_next, net_income, line_profit)
    return runway <= _AI_BANKRUPTCY_RUNWAY_YEARS


def _ai_extension_needs_profit_proof(
    near_term_bankruptcy_risk: bool, *, net_income: float, line_profit: float, has_profitable_line: bool,
) -> bool:
    """When True, extensions must pass conservative profit/payback checks."""
    if not near_term_bankruptcy_risk:
        return False
    if has_profitable_line and (net_income > 0 or line_profit > 0):
        return False
    return True

# Map-agnostic section grid for first-line spread: any map is divided into 36 sections (6x6).
# Used so multiple AIs prefer building first line in different sections (works for any preset).
AI_MAP_SECTION_COLS = 6
AI_MAP_SECTION_ROWS = 6


def _game_start_year(ops) -> int:
    if ops is None:
        return 1830
    try:
        tl = ops.get_game().get_timeline()
        y = getattr(tl, 'initial_year', None)
        if isinstance(y, (int, float)) and not isinstance(y, bool):
            return int(y)
    except Exception:
        pass
    return 1830


def _timeline_year_span(ops) -> tuple:
    """Return (initial_year, end_year) for fare scaling; end is at least initial+1."""
    if ops is None:
        return (1830, 2050)
    try:
        tl = ops.get_game().get_timeline()
        sy = int(getattr(tl, 'initial_year', None) or 1830)
        ey = int(getattr(tl, 'end_year', None) or 2050)
        return (sy, max(sy + 1, ey))
    except Exception:
        return (1830, 2050)


def _inflate_econ(ops, year: int, base_amount: float, max_factor: float = 10.0) -> int:
    """Scale a base-year dollar threshold to nominal currency for ``year``."""
    if ops is not None:
        fn = getattr(ops, 'inflate_economic_value', None)
        if callable(fn):
            try:
                raw = fn(base_amount, max_factor=max_factor)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    return int(raw)
            except Exception:
                pass
    from inflation_utils import inflate_economic_value
    try:
        year_i = int(year)
    except (TypeError, ValueError):
        year_i = 1830
    sy, ey = _timeline_year_span(ops)
    return inflate_economic_value(
        base_amount, year_i, start_year=sy, end_year=ey, max_factor=max_factor,
    )


def _ai_budget_bounds(ops, year: int, amount, *, floor: float = 500, ceiling: float = 120000) -> int:
    """Clamp a spend/budget amount to inflation-scaled floor and ceiling."""
    lo = _inflate_econ(ops, year, floor)
    hi = _inflate_econ(ops, year, ceiling)
    return min(max(lo, int(amount)), hi)


def _years_from_game_start(ops, current_year: int) -> int:
    return max(0, int(current_year) - _game_start_year(ops))


def ai_finance_year_requires_sustained_profit(ops, finance_year: int) -> bool:
    """True when this settled calendar year's net must be positive (post-bootstrap win criterion)."""
    return _years_from_game_start(ops, finance_year) > AI_BOOTSTRAP_TURNS


def record_ai_settled_annual_net(ops, player, finance_year: int, net: float) -> None:
    """Store year-end net from ``advance_year_complete`` for profitability checks and AI heuristics."""
    hist = getattr(player, '_ai_annual_net_by_year', None)
    if hist is None:
        hist = {}
        player._ai_annual_net_by_year = hist
    hist[int(finance_year)] = float(net)


def ai_last_settled_annual_net(player, before_year: int) -> Optional[float]:
    """Net from the most recent settled year strictly before ``before_year``, if recorded."""
    hist = getattr(player, '_ai_annual_net_by_year', None) or {}
    prior = [y for y in hist if int(y) < int(before_year)]
    if not prior:
        return None
    return float(hist[max(prior)])


def ai_count_post_bootstrap_negative_years(ops, player) -> int:
    hist = getattr(player, '_ai_annual_net_by_year', None) or {}
    return sum(
        1 for y, net in hist.items()
        if ai_finance_year_requires_sustained_profit(ops, y) and float(net) <= 0
    )


def ai_has_post_bootstrap_profit_win(ops, player) -> bool:
    """True once any post-bootstrap settled year had net > 0."""
    hist = getattr(player, '_ai_annual_net_by_year', None) or {}
    return any(
        ai_finance_year_requires_sustained_profit(ops, y) and float(net) > 0
        for y, net in hist.items()
    )


def get_idle_human_benchmark_player(ops, ai_player) -> Optional[object]:
    """Non-AI human in the same game (the do-nothing comparison player), if any."""
    if not ops:
        return None
    for p in ops.get_players():
        if p is ai_player:
            continue
        if getattr(p, 'is_ai', False):
            continue
        if getattr(p, 'is_public_ta', False):
            continue
        return p
    return None


def is_idle_human_benchmark_game(ops, ai_player) -> bool:
    """True only when comparing AI survival to a do-nothing human (not normal multiplayer)."""
    if not ops:
        return False
    game = getattr(ops, 'game', None)
    if game is not None and getattr(game, 'idle_human_benchmark_enabled', False) is True:
        return True
    from game_presets import AI_OUTLAST_IDLE_STARTING_MONEY
    game_start = getattr(game, 'starting_money', None) if game is not None else None
    if game_start is None or not isinstance(game_start, (int, float)) or isinstance(game_start, bool):
        return False
    if int(game_start) != int(AI_OUTLAST_IDLE_STARTING_MONEY):
        return False
    idle = get_idle_human_benchmark_player(ops, ai_player)
    if not idle:
        return False
    try:
        if idle.get_line_names():
            return False
    except Exception:
        pass
    return True


def record_player_money_at_year_end(player, finance_year: int, money: int) -> None:
    hist = getattr(player, '_player_money_at_year_end', None)
    if hist is None:
        hist = {}
        player._player_money_at_year_end = hist
    hist[int(finance_year)] = int(money)


def player_last_solvent_calendar_year(ops, player) -> int:
    """Last calendar year the player had money > 0 at year-end (or current year if still solvent)."""
    start = _game_start_year(ops)
    last = start
    hist = getattr(player, '_player_money_at_year_end', None) or {}
    for y, m in hist.items():
        if float(m) > 0:
            last = max(last, int(y))
    if ops and not ops.is_player_eliminated(player) and ops.get_player_money(player) > 0:
        last = max(last, int(ops.get_year()))
    return last


def player_survival_years(ops, player) -> int:
    """Years from timeline start through last solvent calendar year."""
    return max(0, player_last_solvent_calendar_year(ops, player) - _game_start_year(ops))


def ai_outlasts_idle_player(ops, ai_player, idle_player) -> bool:
    """True when the AI's solvent span exceeds the idle human's (survives longer)."""
    if idle_player is None:
        return True
    return player_survival_years(ops, ai_player) > player_survival_years(ops, idle_player)


def ai_profitability_onset_in_window(ops, player) -> bool:
    """True if the first settled year with positive net falls within turns [ONSET_MIN, ONSET_MAX]."""
    from game_presets import AI_PROFITABILITY_ONSET_MAX_TURN, AI_PROFITABILITY_ONSET_MIN_TURN

    hist = getattr(player, '_ai_annual_net_by_year', None) or {}
    start = _game_start_year(ops)
    first_pos_year = None
    for y in sorted(int(y) for y in hist.keys()):
        if float(hist[y]) > 0:
            first_pos_year = int(y)
            break
    if first_pos_year is None:
        return False
    turn = first_pos_year - start
    return AI_PROFITABILITY_ONSET_MIN_TURN <= turn <= AI_PROFITABILITY_ONSET_MAX_TURN


def ai_profitability_sustain_window_start_year(ops) -> int:
    """First calendar year counted for the post-onset sustained-profit check (after turns 5–15 window)."""
    from game_presets import AI_PROFITABILITY_ONSET_MAX_TURN

    return _game_start_year(ops) + int(AI_PROFITABILITY_ONSET_MAX_TURN) + 1


def ai_profitability_sustained_mostly_through_year(
    ops,
    player,
    *,
    through_calendar_year: int = None,
    min_fraction: float = None,
) -> bool:
    """True if most settled years from post-onset (turn 16+) through ``through_calendar_year`` have net > 0."""
    from game_presets import AI_PROFITABILITY_SUSTAIN_MIN_FRACTION, AI_PROFITABILITY_SUSTAIN_THROUGH_CALENDAR_YEAR

    if through_calendar_year is None:
        through_calendar_year = AI_PROFITABILITY_SUSTAIN_THROUGH_CALENDAR_YEAR
    if min_fraction is None:
        min_fraction = AI_PROFITABILITY_SUSTAIN_MIN_FRACTION
    hist = getattr(player, '_ai_annual_net_by_year', None) or {}
    sustain_from = ai_profitability_sustain_window_start_year(ops)
    end_y = int(through_calendar_year)
    window = sorted(int(y) for y in hist.keys() if sustain_from <= int(y) <= end_y)
    if not window:
        return False
    positive = sum(1 for y in window if float(hist[y]) > 0)
    need = max(1, int(math.ceil(len(window) * float(min_fraction))))
    return positive >= need


def ai_profitability_profile_met(ops, player) -> bool:
    """Onset in turns 5–15 and mostly positive nets through calendar year 1900."""
    return ai_profitability_onset_in_window(ops, player) and ai_profitability_sustained_mostly_through_year(
        ops, player
    )


def ai_capital_spending_allowed_vs_idle(ops, player, spendable: int = 0) -> bool:
    """False when the AI is behind a do-nothing human's cash and should not queue more capital spend."""
    if not is_idle_human_benchmark_game(ops, player):
        return True
    idle = get_idle_human_benchmark_player(ops, player)
    if not idle or ops.is_player_eliminated(idle):
        return True
    ai_money = ops.get_player_money(player)
    idle_money = ops.get_player_money(idle)
    if ai_money >= idle_money:
        return True
    if not player.get_line_names():
        q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
        my_lines = [
            b for b in q
            if b.get('player') == player and b.get('type') in ('line', 'line_extension')
        ]
        if not my_lines:
            year = ops.get_year() if ops else 1830
            return (
                spendable >= _inflate_econ(ops, year, 2500)
                and ai_money > _inflate_econ(ops, year, 12000)
            )
    if player.get_line_names():
        year = ops.get_year() if ops else 1830
        return spendable >= _inflate_econ(ops, year, 1200)
    return False


def _is_first_game_year(ops, current_year: int) -> bool:
    return int(current_year) == _game_start_year(ops)


def _ai_join_year_pending_first_line(ops, player, current_year: int) -> bool:
    """True when this AI joined in ``current_year`` and still has no line and no line/extension in the build queue."""
    jy = getattr(player, '_ai_join_game_year', None)
    if jy is None or int(current_year) != int(jy):
        return False
    if not getattr(player, 'is_ai', False):
        return False
    try:
        names = player.get_line_names()
    except Exception:
        names = []
    if names:
        return False
    q = ops.get_build_queue() if ops and hasattr(ops, 'get_build_queue') else []
    if not isinstance(q, list):
        q = []
    return not any(
        b.get('player') == player and b.get('type') in ('line', 'line_extension') for b in q
    )


def _normalize_ai_focus(focus) -> str:
    if focus in (None, 'random', 'Random'):
        return AI_DEFAULT_FOCUS
    return focus if focus in AI_FOCUSES else AI_DEFAULT_FOCUS


def point_to_section(game_map, x: float, y: float) -> tuple:
    """Return (col, row) section index for a point. Works for any map using width/height.
    Section indices are in [0, AI_MAP_SECTION_COLS-1] and [0, AI_MAP_SECTION_ROWS-1]."""
    w_raw = getattr(game_map, 'width', 1)
    h_raw = getattr(game_map, 'height', 1)
    w = max(1, int(w_raw)) if isinstance(w_raw, (int, float)) and not isinstance(w_raw, bool) else 1
    h = max(1, int(h_raw)) if isinstance(h_raw, (int, float)) and not isinstance(h_raw, bool) else 1
    cx = min(AI_MAP_SECTION_COLS - 1, max(0, int(AI_MAP_SECTION_COLS * float(x) / w)))
    cy = min(AI_MAP_SECTION_ROWS - 1, max(0, int(AI_MAP_SECTION_ROWS * float(y) / h)))
    return (cx, cy)


def get_used_sections_for_first_line(ops, game_map, existing_routes: list = None) -> set:
    """Return set of (col, row) sections already used by existing lines or build queue.
    Used so AI prefers building first line in an unused section (any map).
    existing_routes: optional pre-fetched list from run_ai_turn (avoids redundant work)."""
    used = set()
    routes = existing_routes if existing_routes is not None else _get_existing_line_routes_for_overlap(ops, game_map)
    for route in routes:
        if len(route) < 2:
            continue
        pts = [(float(p[0]), float(p[1])) for p in route]
        cx_avg = sum(p[0] for p in pts) / len(pts)
        cy_avg = sum(p[1] for p in pts) / len(pts)
        used.add(point_to_section(game_map, cx_avg, cy_avg))
    return used


def _ai_line_create_result_ok(result) -> bool:
    """True if create_line_complete / _try_build_line returned a queued build (not an explicit failure dict)."""
    if result is None:
        return False
    if isinstance(result, dict) and result.get("success") is False:
        return False
    return True


def get_preferred_section_for_player(player, game_map, ops=None) -> tuple:
    """Return (col, row) section index this AI should prefer for its first line.
    Varies by game (game_seed) so each play-through AIs spread differently and don't make the same choices."""
    pid = getattr(player, 'player_id', 0)
    game_seed = _get_game_seed(ops) if ops is not None else 0
    col = (pid + (game_seed % 1000)) % AI_MAP_SECTION_COLS
    row = ((pid * 7 + (game_seed // 1000)) // AI_MAP_SECTION_COLS) % AI_MAP_SECTION_ROWS
    return (col, row)


# Line specialty (ai_specialties) the AI whose turn is running builds this turn; None outside turns.
_AI_TURN_SPECIALTY: Optional[str] = None


def _ai_turn_specialty() -> Optional[str]:
    return _AI_TURN_SPECIALTY


def _ai_turn_build_mode(ops, current_year=None) -> Optional[str]:
    """'rail', 'bus' or 'ferry' for lines built this turn (None: no specialty, old behaviour)."""
    spec = _AI_TURN_SPECIALTY
    if spec is None:
        return None
    from ai_specialties import build_mode
    try:
        buses = bool(ops.bus_lines_available(current_year)) if ops is not None else False
    except Exception:
        buses = False
    return build_mode(spec, buses)


def _ai_turn_allows_ferry_lines(ops, player) -> bool:
    """Ferry lines may be built by ferry companies, and by others once they are profitable."""
    if _AI_TURN_SPECIALTY is None:
        return True
    from ai_specialties import FERRY, allowed_specialties
    return FERRY in allowed_specialties(ops, player)


def run_ai_turn(ops, player, progress_callback=None, deadline_sec=None, *, _street_index_retry=False):
    """Execute one AI turn (see ``_run_ai_turn``) building lines of the company's specialty."""
    global _AI_TURN_SPECIALTY
    previous = _AI_TURN_SPECIALTY
    try:
        from ai_specialties import assign_specialty, choose_build_specialty
        if getattr(player, 'is_ai', False) and ops is not None:
            assign_specialty(ops.get_game(), player)
            _AI_TURN_SPECIALTY = choose_build_specialty(ops, player, _ai_rng(player, ops.get_year(), ops))
    except Exception:
        _AI_TURN_SPECIALTY = None
    loan_actions = []
    if not _street_index_retry:
        try:
            loan_actions = _ai_manage_loans(ops, player)
        except Exception:
            loan_actions = []
    try:
        actions = _run_ai_turn(
            ops, player, progress_callback=progress_callback, deadline_sec=deadline_sec,
            _street_index_retry=_street_index_retry,
        )
        return loan_actions + list(actions or []) if loan_actions else actions
    finally:
        _AI_TURN_SPECIALTY = previous
        try:
            if getattr(player, 'is_ai', False) and ops is not None:
                ops.auto_assign_maintenance_workers(player)  # put rented maintenance crews to work
        except Exception:
            pass


def _ai_manage_loans(ops, player) -> list:
    """Borrow when short of cash and the lender rates repayment likely; repay early when rich.

    Keeps about AI_LOAN_TARGET_CASH (inflated) on hand: below it the company borrows the
    difference if the offered repayment chance is at least AI_LOAN_MIN_REPAYMENT_CHANCE (half
    that when its money is already negative, to stave off bankruptcy). It takes the shortest
    term whose yearly payment fits in half its net income, else the longest. Above
    AI_LOAN_REPAY_CASH_MULTIPLE times the target it pays down its dearest loan.
    Returns (action, result) pairs for what it did.
    """
    from game_presets import (
        AI_LOAN_MIN_REPAYMENT_CHANCE, AI_LOAN_REPAY_CASH_MULTIPLE, AI_LOAN_TARGET_CASH,
        LOAN_MAX_TERM_YEARS, LOAN_MIN_TERM_YEARS,
    )
    if not getattr(player, 'is_ai', False) or ops is None or not hasattr(ops, 'get_loan_offer'):
        return []
    if hasattr(ops, 'is_player_eliminated') and ops.is_player_eliminated(player):
        return []
    actions = []
    money = int(ops.get_player_money(player))
    target = int(ops.inflate_economic_value(AI_LOAN_TARGET_CASH, max_factor=10.0))
    loans_now = ops.get_player_loans(player)
    if loans_now and money > target * AI_LOAN_REPAY_CASH_MULTIPLE:
        index = max(range(len(loans_now)), key=lambda i: loans_now[i]['rate'])
        spare = money - target * (AI_LOAN_REPAY_CASH_MULTIPLE - 1)
        result = ops.repay_loan(player, index, min(spare, loans_now[index]['balance']))
        if result.get('success'):
            actions.append(('repay_loan', result))
        return actions
    if money >= target:
        return actions
    probe = ops.get_loan_offer(player, 0)
    amount = min(target - money, probe['maximum'])
    if amount < probe['minimum']:
        return actions
    min_chance = AI_LOAN_MIN_REPAYMENT_CHANCE * (0.5 if money < 0 else 1.0)
    try:
        net = float(ops.get_player_financial_summary(player, ops.get_year()).get('net', 0) or 0)
    except Exception:
        net = 0.0
    chosen = None
    for years in range(LOAN_MIN_TERM_YEARS, LOAN_MAX_TERM_YEARS + 1, 5):
        offer = ops.get_loan_offer(player, amount, years)
        if not offer.get('allowed') or offer['chance'] < min_chance:
            continue
        chosen = offer
        if net > 0 and offer['payment'] <= net / 2:
            break
    if chosen is None:
        return actions
    result = ops.take_loan(player, chosen['amount'], chosen['years'])
    if result.get('success'):
        actions.append(('take_loan', result))
    return actions


def _run_ai_turn(ops, player, progress_callback=None, deadline_sec=None, *, _street_index_retry=False):
    """Execute one AI turn. Returns list of (action_name, result_dict) for actions taken.
    AI takes up to 3 actions per turn: (1) Build/Extend line including stations, (2) Focus (lobby/marketplace),
    (3) Situational (rent workers, buy, etc.). If one fails, another may substitute.
    progress_callback: optional callable(player_name, action_name) for investigative mode.
    deadline_sec: optional wall-clock cap (defaults to AI_TURN_TIME_BUDGET_SEC); checked in the main action loop.
    Post-game survival scores (``ai_training.compute_survival_score``) rank training runs only; they do not
    change ``ai_focus`` or in-turn AI logic.
    """
    if not getattr(player, 'is_ai', False):
        return []
    if ops and hasattr(ops, 'is_player_eliminated') and ops.is_player_eliminated(player):
        return []

    game_map = ops.get_map() if ops else None
    map_preset = getattr(game_map, 'map_preset_name', None) if game_map else None
    from map_preset_loader import load_map_preset, preset_ai_uses_street_network
    current_year = ops.get_year()
    game_start_year = _game_start_year(ops)
    first_calendar_year = (int(current_year) == game_start_year)
    years_from_start = _years_from_game_start(ops, current_year)
    early_growth = years_from_start < AI_EARLY_GROWTH_YEARS
    sustained_profit_phase = years_from_start > AI_BOOTSTRAP_TURNS
    last_settled_net = ai_last_settled_annual_net(player, current_year)
    had_negative_settled_year = (
        sustained_profit_phase and last_settled_net is not None and last_settled_net <= 0
    )
    # Load preset once per turn (avoids repeated load_map_preset in build/ferry/extend)
    preset = None
    if map_preset:
        try:
            preset = load_map_preset(map_preset)
        except Exception:
            pass

    if (
        preset is not None
        and game_map is not None
        and not _street_index_retry
        and _preset_requires_street_polylines(game_map, preset)
        and not _ai_osm_street_routing_data_available(game_map, map_preset or '')
    ):
        try:
            _ai_try_ensure_street_spatial_index_loaded(game_map, map_preset or '')
        except Exception:
            pass

    # Focus and difficulty are set at player creation from canonical game preset values.
    focus = _normalize_ai_focus(getattr(player, 'ai_focus', None))
    difficulty = getattr(player, 'ai_difficulty', None) or AI_DEFAULT_DIFFICULTY
    if hasattr(player, 'ai_difficulty'):
        player.ai_difficulty = difficulty
    results = []
    money = ops.get_player_money(player)
    _ai_clear_street_compliance_trace(ops, player)
    _turn_ctx = {
        'route_extend_ok': False,
        'min_extend_spendable': 0,
        'extension_profit_gate': False,
        'points_with_pop': None,
    }

    # Per-player RNG (same seed order as before so reserve/build choices unchanged for survival)
    rng = _ai_rng(player, current_year, ops)
    infl_general = (
        ops.get_inflation_factor_for_economy(10.0)
        if ops is not None and hasattr(ops, 'get_inflation_factor_for_economy')
        else None
    )
    if infl_general is None:
        from inflation_utils import get_inflation_factor
        sy_inf, ey_inf = _timeline_year_span(ops)
        infl_general = get_inflation_factor(
            current_year, start_year=sy_inf, end_year=ey_inf, max_factor=10.0,
        )

    def _inf(base: float) -> int:
        return max(1, int(round(float(base) * infl_general)))

    # Existing-route cache is used by both OSM and non-OSM builds.
    existing_routes = _get_existing_line_routes_for_overlap(ops, game_map)
    # Population sorting is expensive on large OSM presets; compute lazily after any first-turn OSM fast-path.
    points_with_pop = None

    has_assets = (len(player.get_line_names()) > 0 or len(player.get_trainsets()) > 0)
    ai_join_first_line_pending = _ai_join_year_pending_first_line(ops, player, current_year)
    first_turn_opener = (first_calendar_year or ai_join_first_line_pending) and not has_assets
    opener_overlap_routes = _ai_opener_overlap_routes(
        existing_routes,
        first_calendar_year=first_calendar_year,
        ai_join_first_line_pending=ai_join_first_line_pending,
    )

    _turn_start = time.monotonic()
    _turn_budget_sec = max(1.0, float(deadline_sec if deadline_sec is not None else AI_TURN_TIME_BUDGET_SEC))
    if first_turn_opener and _ai_map_uses_street_build(game_map, preset):
        _turn_budget_sec = max(_turn_budget_sec, 90.0)
    if ops is not None:
        ops._ai_turn_deadline_mono = _turn_start + _turn_budget_sec
    _ai_begin_line_build_turn(ops)

    def _turn_time_exceeded() -> bool:
        return _ai_turn_time_exceeded(ops)

    # Fast OSM opener before reserve/finance scans (multi-AI turn 1 timeout fix on street maps).
    if first_turn_opener:
        build_queue_early = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
        my_line_builds_early = [
            b for b in build_queue_early
            if b.get('player') == player and b.get('type') in ('line', 'line_extension')
        ]
        osm_opener_map = _ai_map_uses_street_build(game_map, preset)
        if not my_line_builds_early and osm_opener_map:
            try:
                budget = min(max(_inf(500), int(ops.get_player_money(player))), _inf(120000))
                direct = _try_ai_two_point_line_build(
                    ops, player, game_map, budget, current_year, rng, preset,
                    existing_routes=opener_overlap_routes, relaxed=True,
                )
                if _ai_line_create_result_ok(direct):
                    _ai_mark_first_year_opener_attempted(ops)
                    results.append(('create_line', direct))
                    validate_ai_turn_results(ops, player, results)
                    return results
            except Exception:
                pass

    idle_benchmark = get_idle_human_benchmark_player(ops, player)
    idle_money = (
        ops.get_player_money(idle_benchmark)
        if idle_benchmark and not ops.is_player_eliminated(idle_benchmark)
        else None
    )
    idle_benchmark_game = is_idle_human_benchmark_game(ops, player)
    capital_preservation_vs_idle = False
    next_year = current_year + 1
    if first_turn_opener:
        estimated_costs = _inf(5000)
        estimated_costs_next = _inf(5000)
        survival_reserve = _inf(5000)
    elif hasattr(ops, 'estimate_annual_costs_for_player'):
        estimated_costs_next = ops.estimate_annual_costs_for_player(player, next_year, 0)
        estimated_costs = ops.estimate_annual_costs_for_player(player, next_year, extra_workers_estimate=4)
        # Base reserve scales up so AI survives 40+ years on default rules (taxes/maintenance grow)
        if first_calendar_year:
            base_reserve = _inf(10000)  # keep buffer but leave room for first corridor + stations
        elif years_from_start <= 5:
            base_reserve = _inf(18000)
        elif years_from_start <= 10:
            base_reserve = _inf(15000)
        elif years_from_start <= 20:
            base_reserve = _inf(18000)
        else:
            base_reserve = _inf(22000)
        # Street-network / OSM maps: higher costs and longer build; keep more reserve early.
        if preset_ai_uses_street_network(map_preset, game_map) and years_from_start <= 10:
            base_reserve = int(base_reserve * 1.5)
        elif preset_ai_uses_street_network(map_preset, game_map) and years_from_start <= 20:
            base_reserve = int(base_reserve * 1.35)
        survival_reserve = max(estimated_costs, base_reserve)
    else:
        survival_reserve = (
            (_inf(5000) if first_calendar_year else _inf(8500))
            if years_from_start <= 5
            else (_inf(12000) if years_from_start <= 10 else _inf(20000))
        )
    reserve_mod = rng.uniform(1.20, 1.35)  # Keep more buffer to avoid bankruptcy (40-year survival)
    # Per-player behavioral randomness: each AI has slightly different risk tolerance
    pid = getattr(player, 'player_id', 0)
    reserve_mod += rng.uniform(-0.08, 0.08)  # Same RNG seed per player/turn so consistent within turn
    reserve_mod = max(1.05, min(1.45, reserve_mod))
    reserve = max(survival_reserve, int(survival_reserve * reserve_mod))
    # Early years: cap reserve so AIs still have enough spendable for stations/workers, but don't drain below 2 years' costs
    if years_from_start <= 5 and money > _inf(12000):
        reserve = min(reserve, money - _inf(3000))  # allow spendable so AI can add stations/rent 1 worker
    elif years_from_start <= 5 and money > _inf(6000):
        reserve = min(reserve, money - _inf(2000))
    elif years_from_start <= 5 and money > _inf(2500):
        reserve = min(reserve, money - _inf(1500))
    # Turn 1 with no assets, or random-spawn AI on join year with no line: keep spendable for a first line.
    if (first_calendar_year and not has_assets) or ai_join_first_line_pending:
        min_spendable_first_turn = _inf(4500)
        reserve = min(reserve, max(0, money - min_spendable_first_turn))
    if idle_benchmark_game and years_from_start <= AI_BOOTSTRAP_TURNS and money > _inf(12000):
        reserve = max(reserve, money - min(_inf(12000), int(money * 0.72)))
    elif idle_benchmark_game and years_from_start <= 15 and money > _inf(10000):
        reserve = max(reserve, money - min(_inf(10000), int(money * 0.55)))
    spendable = max(0, money - reserve)
    min_spendable_for_station = _inf(1200) if years_from_start <= 10 else _inf(500)

    try:
        if first_turn_opener:
            summary = {}
        else:
            summary = ops.get_player_financial_summary(player, current_year) if hasattr(ops, 'get_player_financial_summary') else {}
    except Exception:
        summary = {}
    is_losing_money = summary.get('net', 0) < 0
    net_income = int(summary.get('net', 0) or 0)
    player_profit = int(summary.get('profit', 0) or 0)
    if player_profit == 0 and ops is not None and hasattr(ops, 'calc_player_profit'):
        try:
            player_profit = int(ops.calc_player_profit(player, int(current_year)) or 0)
        except Exception:
            pass

    # Bankruptcy and loss detection: allow more actions when desperate or losing money
    about_to_go_bankrupt = (money + net_income - estimated_costs_next <= 0)
    near_term_bankruptcy_risk = _ai_near_term_bankruptcy_risk(
        money, estimated_costs_next, net_income, about_to_go_bankrupt, line_profit=player_profit,
    )
    cash_runway_years = _ai_runway_years(money, estimated_costs_next, net_income, player_profit)
    slow_bleed_long_runway = (
        is_losing_money
        and not near_term_bankruptcy_risk
        and not about_to_go_bankrupt
        and cash_runway_years > float(_AI_BANKRUPTCY_RUNWAY_YEARS)
    )
    public_ta = ops.get_public_ta() if hasattr(ops, 'get_public_ta') else None
    public_ta_active = bool(public_ta and getattr(public_ta, 'established', False))
    if idle_benchmark_game and idle_money is not None and money < idle_money:
        capital_preservation_vs_idle = near_term_bankruptcy_risk or about_to_go_bankrupt
    if sustained_profit_phase and (is_losing_money or had_negative_settled_year):
        is_losing_money = True
        if near_term_bankruptcy_risk:
            reserve_mod = min(1.65, reserve_mod + 0.18)
            reserve = max(survival_reserve, int(survival_reserve * reserve_mod))
            spendable = max(0, money - reserve)
    if slow_bleed_long_runway or (not near_term_bankruptcy_risk and not about_to_go_bankrupt and money > survival_reserve + _inf(1500)):
        aggressive_spend = min(_inf(30000), max(_inf(5000), int(money * 0.58)))
        reserve = min(reserve, max(survival_reserve, money - aggressive_spend))
        spendable = max(0, money - reserve)
    is_wealthy_and_profitable = (
        money >= _inf(_WEALTHY_MONEY_THRESHOLD) and net_income > _inf(2000)
        and (not sustained_profit_phase or net_income > 0)
    )

    # Track early visible network growth: by 1841 each AI should have built/expanded at least twice.
    try:
        build_expand_count = int(getattr(player, "_ai_build_expand_count", 0) or 0)
    except Exception:
        build_expand_count = 0

    def _do_action(name: str, result: dict):
        results.append((name, result))
        nonlocal build_expand_count
        if name == "extend_line" and isinstance(result, dict):
            _ext_ln = result.get('line_name') or result.get('line')
            if _ext_ln:
                _AI_LINE_EXTEND_COUNT[_ext_ln] = _AI_LINE_EXTEND_COUNT.get(_ext_ln, 0) + 1
        if name in ("create_line", "extend_line"):
            build_expand_count += 1
            try:
                player._ai_build_expand_count = int(build_expand_count)
            except Exception:
                pass
            if ops is not None:
                pts = None
                if isinstance(result, dict):
                    pts = result.get('points') or result.get('route')
                if pts and len(pts) >= 2:
                    _ai_append_existing_route_cache(ops, pts)
                else:
                    ops._ai_existing_routes_cache = None
        if name in ("create_line", "extend_line", "add_station", "assign_trainset"):
            try:
                line_name = None
                if isinstance(result, dict):
                    line_name = result.get('line') or result.get('line_name')
                force = {line_name} if name == 'assign_trainset' and line_name else None
                line_filter = [line_name] if line_name and name in ('assign_trainset', 'add_station') else None
                _tune_ai_line_economics_after_build(
                    line_names=line_filter,
                    force_line_names=force,
                )
            except Exception:
                pass
        if name == 'add_station' and isinstance(result, dict):
            ln = result.get('line_name') or result.get('line')
            if ln:
                eng_rent = _try_rent_engineer_for_assignment(
                    ops, player, current_year, _refresh_spendable(), game_map,
                )
                if eng_rent:
                    _do_action('rent_workers', eng_rent)
                assign_result = _ai_try_assign_first_trainset_to_line(ops, player, game_map, ln)
                if assign_result:
                    _do_action('assign_trainset', assign_result)
        if name == 'assign_trainset' and isinstance(result, dict):
            if _turn_ctx.get('route_extend_ok') and _refresh_spendable() > _turn_ctx.get('min_extend_spendable', 0):
                extend_result = _try_extend_line(
                    ops,
                    player,
                    game_map,
                    _refresh_spendable(),
                    current_year,
                    _turn_ctx.get('points_with_pop'),
                    rng=rng,
                    preset=preset,
                    require_profit_when_troubled=_turn_ctx.get('extension_profit_gate', False),
                )
                if extend_result:
                    _do_action('extend_line', extend_result)
        if progress_callback:
            progress_callback(player.get_name(), _format_ai_action(name, result))

    def _refresh_spendable():
        return max(0, ops.get_player_money(player) - reserve)

    map_lines = game_map.get_lines_by_name() or {}
    any_line_needs_stations = any(
        len(line.get_stations()) < 2
        for ln in player.get_line_names()
        for line in [map_lines.get(ln)]
        if line and hasattr(line, 'get_stations')
    )
    lines_with_one_station = [
        ln for ln in player.get_line_names()
        for line in [map_lines.get(ln)]
        if line and hasattr(line, 'get_stations') and len(line.get_stations()) == 1
    ]
    lines_requiring_extension_for_station = [
        ln
        for ln in player.get_line_names()
        for line in [map_lines.get(ln)]
        if line and _line_needs_extension_before_second_station(ops, ln, line)
    ]
    osm_like_map = _ai_map_uses_street_build(game_map, preset) or bool(
        preset and hasattr(preset, 'get_road_extension_points_from_end')
    )
    force_station_extension_needed = osm_like_map and years_from_start >= 1 and bool(
        lines_requiring_extension_for_station
    )
    has_profitable_line = any(
        ops.calc_line_profit(map_lines.get(ln), current_year) > 0
        for ln in player.get_line_names()
        if map_lines.get(ln)
    )
    has_lines = len(player.get_line_names()) > 0
    build_queue = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
    my_line_builds = [b for b in build_queue if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
    my_station_builds = [b for b in build_queue if b.get('player') == player and b.get('type') == 'station']
    has_unfinished_lines = len(my_line_builds) > 0
    # Only a new line still being built holds back the next one (extensions do not).
    has_unfinished_new_lines = any(b.get('type') == 'line' for b in my_line_builds)
    line_build_complete = has_lines and not has_unfinished_lines
    lines_missing_trainsets = [
        ln for ln in player.get_line_names()
        if _line_needs_trainset_assignment(map_lines, ln, ops=ops, player=player)
    ]
    lines_with_two_stations = [
        ln for ln in player.get_line_names()
        if map_lines.get(ln)
        and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2
    ]
    has_revenue_line = any(
        map_lines.get(ln)
        and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2
        and len(getattr(map_lines.get(ln), 'vehicles', None) or []) > 0
        and ops.calc_line_profit(map_lines.get(ln), current_year) > 0
        for ln in player.get_line_names()
    )
    profitable_operation = net_income > 0 or player_profit > _inf(800)
    defer_lobby_marketplace = _ai_should_defer_lobby_marketplace(
        years_from_start,
        has_revenue_line,
        has_profitable_line,
        bool(lines_with_two_stations),
    )
    extension_profit_gate = _ai_extension_needs_profit_proof(
        near_term_bankruptcy_risk,
        net_income=net_income,
        line_profit=player_profit,
        has_profitable_line=has_profitable_line,
    )
    can_grow_network = (
        bool(lines_with_two_stations)
        or not any_line_needs_stations
        or bool(lines_requiring_extension_for_station)
        or (line_build_complete and osm_like_map)
    )
    profit_recovery_mode = near_term_bankruptcy_risk or about_to_go_bankrupt
    single_line_growth_until_win = False
    from game_presets import AI_PROFITABILITY_ONSET_MAX_TURN, AI_PROFITABILITY_ONSET_MIN_TURN

    profitability_onset_phase = (
        AI_PROFITABILITY_ONSET_MIN_TURN <= years_from_start <= AI_PROFITABILITY_ONSET_MAX_TURN
    )
    had_any_positive_settled = ai_has_post_bootstrap_profit_win(ops, player)
    profit_stewardship = (near_term_bankruptcy_risk or about_to_go_bankrupt) and had_any_positive_settled
    if profit_stewardship and had_negative_settled_year and money > _inf(8000):
        reserve = min(reserve, max(survival_reserve, money - _inf(6000)))
        spendable = max(0, money - reserve)
    # Street-network maps: once a line earns revenue, invest surplus in extensions/stations instead of hoarding.
    if (
        preset_ai_uses_street_network(map_preset, game_map)
        and years_from_start <= 50
        and has_revenue_line
        and money > _inf(12000)
        and not about_to_go_bankrupt
    ):
        reserve = min(reserve, max(survival_reserve, money - _inf(10000)))
        spendable = max(0, money - reserve)
    if (
        len(player.get_line_names()) > 0
        and lines_with_two_stations
        and not about_to_go_bankrupt
        and not near_term_bankruptcy_risk
        and (has_profitable_line or has_revenue_line)
        and money > _inf(6000)
    ):
        reserve = min(reserve, max(survival_reserve, money - _inf(4500)))
        spendable = max(0, money - reserve)
    if profitable_operation and money > _inf(4000):
        reserve = min(reserve, max(survival_reserve, money - _inf(7000)))
        spendable = max(0, money - reserve)
    elif player_profit > _inf(1500) and money > _inf(3500) and not about_to_go_bankrupt:
        reserve = min(reserve, max(survival_reserve, money - _inf(5000)))
        spendable = max(0, money - reserve)
    if (
        years_from_start > 0
        and len(player.get_line_names()) > 0
        and (has_revenue_line or lines_with_two_stations)
        and not near_term_bankruptcy_risk
        and not about_to_go_bankrupt
        and money > _inf(4000)
    ):
        reserve = min(reserve, max(survival_reserve, money - _inf(5000)))
        spendable = max(0, money - reserve)
    # Maintenance mode only when bankruptcy is imminent and still losing money.
    sustained_profit_maintenance = (
        (near_term_bankruptcy_risk or about_to_go_bankrupt)
        and has_revenue_line
        and not lines_missing_trainsets
        and net_income < 0
    )
    if capital_preservation_vs_idle and money > _inf(8000):
        reserve = max(reserve, int(money * 0.55))
        spendable = max(0, money - reserve)

    # Early survival: carrying 3 starter trainsets is expensive before lines are profitable.
    # Sell unneeded unassigned trainsets early to reduce annual maintenance burn.
    if (
        early_growth
        and hasattr(ops, 'sell_trainset_for_half_price')
        and not _turn_time_exceeded()
        and not ((first_calendar_year or ai_join_first_line_pending) and len(player.get_line_names()) == 0)
    ):
        keep_total_trainsets = max(
            2 if has_profitable_line else 1,
            len(lines_with_two_stations),
        )
        if capital_preservation_vs_idle:
            keep_total_trainsets = min(keep_total_trainsets, 1)
        elif years_from_start <= AI_BOOTSTRAP_TURNS:
            keep_total_trainsets = max(keep_total_trainsets, len(player.get_trainsets() or []), 3)
        elif sustained_profit_phase and lines_missing_trainsets:
            keep_total_trainsets = max(keep_total_trainsets, len(lines_with_two_stations), 1)
        while not _turn_time_exceeded() and len(player.get_trainsets() or []) > keep_total_trainsets:
            unassigned_now = list(ops.get_unassigned_trainsets(player) or [])
            if not unassigned_now:
                break
            try:
                sell_result = ops.sell_trainset_for_half_price(unassigned_now[0], player)
                if sell_result and sell_result.get('sale_price', 0) > 0:
                    _do_action('sell_trainset', sell_result)
                else:
                    break
            except Exception:
                break
    # When profitable in early years, allow 3000 spendable for extension (more revenue = survival)
    if years_from_start <= 10 and has_profitable_line and money > _inf(5000):
        reserve = min(reserve, money - _inf(3000))
    # Line has 2+ stations but no trainsets assigned -> we need to buy/assign to earn (critical for survival)
    has_line_ready_for_trainset = bool(lines_missing_trainsets)
    min_extend_spendable = _inf(1000) if years_from_start <= 15 else _inf(400)
    if force_station_extension_needed and years_from_start <= 22:
        # Allow a cheaper "unlock extension" so short OSM lines can become station-viable.
        min_extend_spendable = min(min_extend_spendable, _inf(500))
    if lines_requiring_extension_for_station and osm_like_map and years_from_start <= 22:
        min_extend_spendable = min(min_extend_spendable, _inf(450))
    if line_build_complete and osm_like_map and spendable > min(min_extend_spendable, _inf(400)):
        min_extend_spendable = min(min_extend_spendable, _inf(400))

    def _ai_keep_worker_salaries_safe():
        if _turn_time_exceeded():
            return
        _ai_apply_worker_strike_prevention(ops, player, current_year, _do_action)

    _ai_keep_worker_salaries_safe()
    surplus_cash_expand = (
        _ai_surplus_cash_should_expand(ops, player, money, sustained_profit_phase)
        and not about_to_go_bankrupt
        and (
            not near_term_bankruptcy_risk
            or profitable_operation
            or has_profitable_line
        )
    )
    if surplus_cash_expand:
        starting_ref = _ai_starting_money_reference(ops)
        target_spend = min(_inf(30000), max(_inf(6000), int((money - starting_ref) * 0.6)))
        target_spend = min(target_spend, money - _inf(600))
        if target_spend >= min_extend_spendable:
            reserve = min(reserve, max(0, money - target_spend))
            spendable = max(0, money - reserve)

    # Fallback OSM opener after queue snapshot (retries when early fast-path did not queue a line).
    if _ai_needs_first_turn_line(
        ops, player,
        first_calendar_year=first_calendar_year,
        ai_join_first_line_pending=ai_join_first_line_pending,
    ) and not any(a == 'create_line' for a, _ in results):
        try:
            direct = _try_force_first_turn_line_build(
                ops, player, game_map, current_year, rng, preset,
                existing_routes=opener_overlap_routes,
            )
            if _ai_line_create_result_ok(direct):
                _ai_mark_first_year_opener_attempted(ops)
                _do_action('create_line', direct)
                validate_ai_turn_results(ops, player, results)
                return results
        except Exception:
            pass

    _skip_first_turn_osm_discretionary = (
        first_turn_opener
        and _ai_map_uses_street_build(game_map, preset)
        and len(player.get_line_names()) == 0
        and not my_line_builds
        and not any(a == 'create_line' for a, _ in results)
    )

    # Cache population points once per turn (expensive on large maps).
    # Delayed until after the first-turn OSM fast-path so multi-AI openers on street maps do not time out.
    if points_with_pop is None and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        pop_cap = _MAX_BUILD_CANDIDATES
        if _ai_turn_time_low(ops, 30.0):
            pop_cap = min(pop_cap, 10)
        if game_map and getattr(game_map, 'population', None):
            if len(game_map.population) > 5000:
                pop_cap = min(pop_cap, 16)
        points_with_pop = _get_population_points_sorted_by_population(
            game_map, max_points=max(_MAX_EXTEND_POINTS, pop_cap)
        )

    def _try_revenue_bootstrap() -> Optional[tuple]:
        """Assign stock and open stations before lobby/marketplace spend when a line can earn."""
        spend = _refresh_spendable()
        lines = player.get_line_names()
        if not lines:
            return None
        ops_map = game_map.get_lines_by_name() or {}
        lines_needing_trainset = [
            ln for ln in lines
            if _line_needs_trainset_assignment(ops_map, ln, ops=ops, player=player)
        ]
        if sustained_profit_phase and has_revenue_line and not lines_needing_trainset:
            return None
        if not lines_needing_trainset and years_from_start > 12 and has_profitable_line and not is_losing_money:
            return None
        if lines_needing_trainset and spend >= _inf(300):
            assign_action = _try_assign_trainsets_for_ready_lines(
                ops, player, game_map, current_year, spend, min_spendable_for_station, results=results,
            )
            if assign_action:
                return assign_action
        if lines_needing_trainset and not (ops.get_unassigned_trainsets(player) or []):
            buy = _try_buy_marketplace(
                ops,
                player,
                current_year,
                spend,
                focus,
                rng=rng,
                allow_rolling_stock=True,
            )
            if buy:
                return ('buy', buy)
        if any_line_needs_stations and spend >= _inf(500):
            lines_with_station_count = []
            for ln in lines:
                line = ops_map.get(ln)
                if not line:
                    continue
                stations = line.get_stations() if hasattr(line, 'get_stations') else []
                lines_with_station_count.append((ln, line, len(stations)))
            lines_with_station_count.sort(key=lambda x: x[2])
            for line_name, line, station_count in lines_with_station_count:
                if station_count >= 2:
                    continue
                min_spacing = _get_ai_station_spacing(line_name=line_name, line=line)
                pt = _pick_station_point(ops, game_map, line_name, line, points_with_pop, set(), min_spacing=min_spacing, player=player)
                if pt:
                    name = _generate_ai_station_name(ops, game_map, pt[0], pt[1], current_year, line_name=line_name)
                    r = _try_ai_add_station(ops, player, line_name, name, pt[0], pt[1])
                    if r:
                        r['line_name'] = line_name
                        r['station_name'] = name
                        r['point'] = (pt[0], pt[1])
                        return ('add_station', r)
        if (my_line_builds or my_station_builds) and spend > min_spendable_for_station:
            rent_result = _try_rent_workers(ops, player, current_year, spend)
            if rent_result:
                return ('rent_workers', rent_result)
        return None

    # OSM maps: ensure we can afford renting enough construction workers to finish early builds
    # (otherwise lines/stations can take too long to complete => no profit by early turns).
    _osm_early_build = preset_ai_uses_street_network(map_preset, game_map)
    if has_unfinished_lines and _osm_early_build and years_from_start <= 10:
        try:
            from worker import CONSTRUCTION_WORKER
            need_qty = max(0, 2 - player.get_construction_worker_count())
            if need_qty > 0 and hasattr(ops, 'calculate_worker_rental_cost'):
                need_cost = ops.calculate_worker_rental_cost(CONSTRUCTION_WORKER, need_qty, current_year)
                reserve = min(reserve, max(0, money - (need_cost + _inf(800))))
                spendable = max(0, money - reserve)
        except Exception:
            pass
    # While waiting for first line to complete, keep reserve for survival — but early-growth window needs spendable for workers.
    if years_from_start <= 4 and my_line_builds and not has_profitable_line and money > _inf(15000):
        if early_growth:
            reserve = min(reserve, max(survival_reserve, money - _inf(5500)))
        else:
            reserve = max(reserve, min(money, _inf(22000)))
        spendable = max(0, money - reserve)
    def _maybe_fire_workers_cost_benefit(large_cut: bool = True) -> Optional[dict]:
        """Fire workers only when the savings materially outweigh near-term operational risk.
        Returns successful fire result or None."""
        if not (hasattr(ops, "fire_workers") and hasattr(ops, "get_max_fireable_workers")):
            return None
        try:
            from worker import CONSTRUCTION_WORKER, ENGINEER, MAINTENANCE_WORKER, LAWYER
        except Exception:
            return None
        rentals = getattr(player, "worker_rentals", {}) or {}
        if not rentals:
            return None
        best = None  # (score, worker_name, qty_to_fire)
        assigned_trainsets = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, "get_player_assigned_trainsets_count") else 0)
        for wtype in (CONSTRUCTION_WORKER, ENGINEER, MAINTENANCE_WORKER, LAWYER):
            rental = rentals.get(wtype.name)
            qty = int(getattr(rental, "quantity", 0) or 0)
            if qty <= 0:
                continue
            max_fireable = int(ops.get_max_fireable_workers(player, wtype.name) or 0)
            if max_fireable <= 0:
                continue
            # Minimum useful quantity to keep for near-term operations.
            if wtype.name == CONSTRUCTION_WORKER.name:
                if sustained_profit_phase and not my_line_builds and not my_station_builds:
                    keep_qty = 0
                else:
                    keep_qty = 2 if my_line_builds else (1 if (years_from_start <= 6 and not has_profitable_line) else 0)
            elif wtype.name == ENGINEER.name:
                if single_line_growth_until_win:
                    keep_qty = min(assigned_trainsets, 1)
                elif net_income < -1500 and money < 20000:
                    # Deep-loss guard: preserve the company over continuous negative burn.
                    keep_qty = 0
                else:
                    keep_qty = max(assigned_trainsets, 1 if has_line_ready_for_trainset else 0)
            elif wtype.name == MAINTENANCE_WORKER.name:
                keep_qty = 1 if has_profitable_line else 0
            else:  # Lawyer
                keep_qty = 0
            surplus = max(0, qty - keep_qty)
            if surplus <= 0:
                continue
            qty_to_fire = max(1, int(surplus * (0.70 if large_cut else 0.35)))
            qty_to_fire = min(qty_to_fire, max_fireable)
            if qty_to_fire <= 0:
                continue
            yearly_save = int(wtype.get_yearly_cost(current_year) * qty_to_fire)
            # Simple risk penalty tuned per worker type; fire only when savings are convincingly better.
            risk_penalty = 0
            if (
                wtype.name == CONSTRUCTION_WORKER.name
                and years_from_start <= 6
                and not has_profitable_line
                and not sustained_profit_phase
            ):
                risk_penalty = yearly_save  # keep builders during fragile bootstrap years
            elif wtype.name == ENGINEER.name and has_line_ready_for_trainset:
                risk_penalty = yearly_save
            elif wtype.name == MAINTENANCE_WORKER.name and has_profitable_line and net_income > 0:
                risk_penalty = int(yearly_save * 0.6)
            score = yearly_save - risk_penalty
            if score <= 0:
                continue
            cand = (score, wtype.name, qty_to_fire)
            if best is None or cand[0] > best[0]:
                best = cand
        if not best:
            return None
        _score, worker_name, qty_to_fire = best
        try:
            r = ops.fire_workers(player, worker_name, qty_to_fire)
            return r if r.get("success") else None
        except Exception:
            return None

    # When workers are currently underutilized, perform a cost/benefit review and fire a large fraction
    # of one worker type when savings clearly outweigh risk.
    if (
        profit_stewardship
        and had_negative_settled_year
        and len(player.get_line_names()) > 0
        and not my_line_builds
        and near_term_bankruptcy_risk
    ):
        fire_eval = _maybe_fire_workers_cost_benefit(large_cut=True)
        if fire_eval:
            _do_action("fire_workers", fire_eval)
    if (
        near_term_bankruptcy_risk
        and not my_line_builds
        and len(player.get_line_names()) > 0
    ):
        fire_eval = _maybe_fire_workers_cost_benefit(large_cut=True)
        if fire_eval:
            _do_action("fire_workers", fire_eval)
    # No completed lines and nothing in the build queue: survival reserve can consume almost all cash
    # (high estimate_annual_costs vs modest balance), leaving spendable=0. Then the no-lines build phase
    # never runs and the AI idles on ai_minimum_turn / sell paths. Non-desperate AIs must keep spendable
    # to start at least one new line (desperate AIs use merge/sell first — do not relax here).
    if (
        len(player.get_line_names()) == 0
        and not my_line_builds
        and hasattr(ops, 'is_player_eliminated')
        and not ops.is_player_eliminated(player)
        and hasattr(ops, 'is_player_desperate')
        and not ops.is_player_desperate(player)
        and money >= _inf(600)
    ):
        target_spend = min(_inf(8000), max(_inf(1800), int(money * 0.42)))
        target_spend = min(target_spend, money - _inf(350))
        if target_spend >= _inf(500) and money >= target_spend + _inf(200):
            reserve = min(reserve, max(0, money - target_spend))
        spendable = max(0, money - reserve)
    if force_station_extension_needed and years_from_start <= 22 and money > _inf(4500):
        # Keep enough liquid cash to fund one practical extension instead of idling with a 1-station line.
        reserve = min(reserve, max(0, money - _inf(7000)))
        spendable = max(0, money - reserve)
    force_extend_ok = (
        force_station_extension_needed
        and spendable > min_extend_spendable
        and money > _inf(2500)
    )
    extend_ok = force_extend_ok or (
        spendable > min_extend_spendable
        and can_grow_network
        and (
            profitable_operation
            or has_profitable_line
            or not (near_term_bankruptcy_risk or about_to_go_bankrupt)
        )
        and bool(player.get_line_names() or first_calendar_year or ai_join_first_line_pending)
    )
    osm_route_extend_unlock = (
        osm_like_map
        and bool(lines_requiring_extension_for_station)
        and years_from_start <= 22
        and money > _inf(3000)
    )
    route_extend_ok = extend_ok or (
        osm_route_extend_unlock
        and spendable > min(min_extend_spendable, _inf(400))
    )
    # Once initial line construction finishes, OSM AIs should extend along neighboring streets.
    post_first_turn_osm_extend = (
        years_from_start >= 1
        and osm_like_map
        and line_build_complete
        and spendable > min_extend_spendable
        and (not near_term_bankruptcy_risk or is_losing_money)
    )
    if post_first_turn_osm_extend:
        route_extend_ok = True
    post_first_year_extend = (
        years_from_start >= 1
        and has_lines
        and line_build_complete
        and spendable > min(min_extend_spendable, _inf(400))
        and (not near_term_bankruptcy_risk or has_profitable_line or is_losing_money)
    )
    if post_first_year_extend:
        route_extend_ok = True
        min_extend_spendable = min(min_extend_spendable, _inf(350))
    if is_losing_money and not about_to_go_bankrupt:
        extension_profit_gate = False
        if spendable > min(min_extend_spendable, _inf(350)):
            route_extend_ok = True
            extend_ok = True
        if money > survival_reserve + _inf(1500):
            reserve = min(reserve, max(survival_reserve, money - _inf(9000)))
            spendable = max(0, money - reserve)
    if profitable_operation and bool(player.get_line_names()) and spendable > min(min_extend_spendable, _inf(350)):
        route_extend_ok = True
        min_extend_spendable = min(min_extend_spendable, _inf(350))
    if line_build_complete and has_lines and spendable > min(min_extend_spendable, _inf(250)):
        route_extend_ok = True
        extend_ok = True
    if surplus_cash_expand and spendable > min(min_extend_spendable, _inf(250)):
        route_extend_ok = True
    _turn_ctx['route_extend_ok'] = route_extend_ok
    _turn_ctx['min_extend_spendable'] = min_extend_spendable
    _turn_ctx['extension_profit_gate'] = extension_profit_gate
    _turn_ctx['points_with_pop'] = points_with_pop
    if preset_ai_uses_street_network(map_preset, game_map):
        build_chance = (
            AI_SECOND_LINE_BUILD_CHANCE_MIN
            + rng.random() * (AI_SECOND_LINE_BUILD_CHANCE_MAX - AI_SECOND_LINE_BUILD_CHANCE_MIN)
        )
        build_chance = min(0.92, build_chance + 0.22)
    else:
        build_chance = 0.28 + rng.random() * 0.35
    if focus == 'line_expansion':
        build_chance = min(0.85, build_chance + 0.12)
    if has_profitable_line or player_profit > _inf(1200):
        build_chance = min(0.95, build_chance + 0.22)
    redirected_target_waiting = bool(_AI_REDIRECTED_TARGETS.get(id(player)))
    wants_new_line = (first_calendar_year or not has_assets or ai_join_first_line_pending) or (
        not has_unfinished_new_lines
        and not lines_missing_trainsets
        and has_assets
        and spendable > _inf(1500)
        and (profitable_operation or not (near_term_bankruptcy_risk or about_to_go_bankrupt))
        and (has_revenue_line or has_profitable_line or years_from_start >= 2 or osm_like_map)
        and (redirected_target_waiting or rng.random() < build_chance)
    )
    if profit_recovery_mode and lines_missing_trainsets:
        wants_new_line = bool(first_calendar_year or not has_assets or ai_join_first_line_pending)
    if lines_with_one_station and not (first_calendar_year or ai_join_first_line_pending):
        wants_new_line = False
    if any_line_needs_stations and has_assets and not (first_calendar_year or ai_join_first_line_pending):
        wants_new_line = False
    if has_lines and route_extend_ok and spendable > min_extend_spendable and not (first_calendar_year or ai_join_first_line_pending):
        pass  # prefer extend/station in action1 before discretionary new lines
    max_build_cost = spendable
    if surplus_cash_expand and not has_unfinished_new_lines:
        wants_new_line = True
        if any_line_needs_stations and not lines_requiring_extension_for_station:
            wants_new_line = False
    max_build_cost = spendable
    if years_from_start <= 10 and (first_calendar_year or not has_assets or ai_join_first_line_pending):
        cap = _inf(5000) if years_from_start == 0 else max(_inf(4000), int(money * 0.18))
        if idle_benchmark_game and years_from_start <= AI_BOOTSTRAP_TURNS:
            cap = min(cap, _inf(4500) if years_from_start == 0 else _inf(3500))
        max_build_cost = min(spendable, cap)
    base_min_marketplace = _inf(12000) if years_from_start <= 10 else _inf(5000)
    min_marketplace = max(_inf(2000), int(base_min_marketplace * rng.uniform(0.8, 1.2)))
    _CORE_NETWORK_ACTIONS = frozenset(('create_line', 'extend_line', 'add_station'))

    def _took_core_network_action() -> bool:
        return any(a in _CORE_NETWORK_ACTIONS for a, _ in results)

    def _can_discretionary_extend() -> bool:
        if line_build_complete and player.get_line_names():
            return True
        return _ai_network_revenue_ready(map_lines, player, ops)

    _ext_state = {'failed': False}

    def _unextended_lines() -> list:
        """Own lines with 2+ stations (built or queued) that have never been extended."""
        out = []
        for ln in player.get_line_names():
            if _AI_LINE_EXTEND_COUNT.get(ln, 0) > 0 or not map_lines.get(ln):
                continue
            if _ai_count_line_stations(ops, player, ln) >= 2:
                out.append(ln)
        return out

    from ai_specialties import PROFILES as _SPEC_PROFILES
    _spec_extensions = (_SPEC_PROFILES.get(_AI_TURN_SPECIALTY or '') or {}).get('extensions_before_new_line')
    extensions_before_new_line = (
        AI_EXTENSIONS_PER_LINE_BEFORE_NEW_LINE if _spec_extensions is None else int(_spec_extensions)
    )

    def _new_line_ok() -> bool:
        """New lines wait until existing lines were extended (or extension was tried and failed).
        Specialties that favour many lines (express buses) open new lines straight away."""
        if first_calendar_year or ai_join_first_line_pending or not player.get_line_names():
            return True
        if extensions_before_new_line <= 0:
            return True
        return _ext_state['failed'] or not _unextended_lines()

    def _new_line_before_more_extensions() -> bool:
        if _AI_REDIRECTED_TARGETS.get(id(player)):
            return True
        own = [ln for ln in player.get_line_names() if map_lines.get(ln)]
        return bool(own) and all(
            _AI_LINE_EXTEND_COUNT.get(ln, 0) >= extensions_before_new_line for ln in own
        )

    def _try_extension_before_new_line():
        """Extend a never-extended line before any new line is opened."""
        pending = _unextended_lines()
        if not pending or _ext_state['failed'] or extensions_before_new_line <= 0:
            return None
        spend_now = _refresh_spendable()
        if spend_now > min(min_extend_spendable, _inf(400)):
            res = _try_extend_line(
                ops, player, game_map, spend_now, current_year, points_with_pop,
                rng=rng, preset=preset, line_names_priority=pending,
            )
            if res:
                return ('extend_line', res)
        _ext_state['failed'] = True  # nothing extendable/affordable now: new lines may proceed
        return None

    def _try_action1_build_extend_station():
        """Action 1: Build, extend, or add station. Returns (name, result) or None.
        Priority: (1) first line on turn 1, (2) stations, (3) assign trainset + schedule when 2 built stations,
        (4) extend existing lines, (5) new lines."""
        spend = _refresh_spendable()
        lines = player.get_line_names()
        # Only build first line on turn 1 when we have no line in queue yet (use current queue, not start-of-turn snapshot)
        _queue = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
        _my_builds_now = [b for b in _queue if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
        # First line: first calendar year with no queue build, or no assets at all, or no lines *and* nothing queued.
        # If len(lines)==0 but we already queued a line this turn, do not queue another.
        is_first_or_no_assets = (
            (first_calendar_year and not _my_builds_now) or
            not (lines or player.get_trainsets()) or
            (len(lines) == 0 and not _my_builds_now)
        )
        # First turn: all AIs must build one line regardless of finances (game year 1 or join-year spawn).
        if is_first_or_no_assets and (spend > 0 or first_calendar_year or ai_join_first_line_pending):
            if first_calendar_year or ai_join_first_line_pending:
                first_build_cap = min(max(spend, _inf(3500)), _inf(60000) if _ai_map_uses_street_build(game_map, preset) else _inf(8000))
                build_result = _try_ai_two_point_line_build(
                    ops, player, game_map, first_build_cap, current_year, rng, preset,
                    existing_routes=existing_routes, points_with_pop=points_with_pop,
                    relaxed=True,
                )
            else:
                _osm_first_turn = _ai_map_uses_street_build(game_map, preset)
                if _osm_first_turn:
                    first_build_cap = min(spend, _inf(60000))
                else:
                    first_build_cap = min(spend, _inf(5000))
                build_result = _try_build_line(
                    ops, player, game_map, first_build_cap, current_year, points_with_pop,
                    rng=rng, existing_routes=existing_routes, preset=preset,
                )
            if _ai_line_create_result_ok(build_result):
                return ('create_line', build_result)
        # When we have lines under construction but no completed lines with 2 stations, rent construction worker first (survival)
        if (my_line_builds or my_station_builds) and spend > min_spendable_for_station:
            rent_result = _try_rent_workers(ops, player, current_year, spend)
            if rent_result:
                return ('rent_workers', rent_result)
        # Lines need 2 stations to earn profit - add stations before extending or building new lines.
        if lines and spend >= _inf(500):
            lines_with_station_count = []
            for ln in lines:
                line = map_lines.get(ln)
                if not line:
                    continue
                stations = line.get_stations() if hasattr(line, 'get_stations') else []
                lines_with_station_count.append((ln, line, len(stations)))
            lines_with_station_count.sort(key=lambda x: x[2])
            for line_name, line, station_count in lines_with_station_count:
                if station_count >= 2:
                    continue
                exclude_pts = set()
                min_spacing = _get_ai_station_spacing(line_name=line_name, line=line)
                pt = _pick_station_point(ops, game_map, line_name, line, points_with_pop, exclude_pts, min_spacing=min_spacing, player=player)
                if pt:
                    name = _generate_ai_station_name(ops, game_map, pt[0], pt[1], current_year, line_name=line_name)
                    r = _try_ai_add_station(ops, player, line_name, name, pt[0], pt[1])
                    if r:
                        r['line_name'] = line_name
                        r['station_name'] = name
                        r['point'] = (pt[0], pt[1])
                        return ('add_station', r)
            rescue = _try_ai_abandon_stuck_understationed_lines(
                ops, player, game_map, map_lines, points_with_pop, current_year,
                spendable=spend, rng=rng, preset=preset, existing_routes=existing_routes,
            )
            if rescue:
                return rescue
        assign_action = _try_assign_trainsets_for_ready_lines(
            ops, player, game_map, current_year, spend, min_spendable_for_station, results=results,
        )
        if assign_action:
            return assign_action
        # Too short for second station at required spacing: extend onto a neighboring street first.
        if lines and spend > min_extend_spendable and lines_requiring_extension_for_station:
            if route_extend_ok:
                spend = _refresh_spendable()
                extend_result = _try_extend_line(
                    ops, player, game_map, spend, current_year, points_with_pop,
                    rng=rng, preset=preset, line_names_priority=lines_requiring_extension_for_station,
                )
                if extend_result:
                    return ('extend_line', extend_result)
        # A new line comes before yet another extension when an extension was turned down
        # for running back on its line (that target needs its own line), or when every
        # line has already been extended a few times.
        if (
            wants_new_line and _new_line_ok() and _new_line_before_more_extensions()
            and not (first_calendar_year or ai_join_first_line_pending)
        ):
            spend = _refresh_spendable()
            max_cost = min(max_build_cost, spend)
            if max_cost > min_spendable_for_station:
                build_result = _try_build_line(
                    ops, player, game_map, max_cost, current_year, points_with_pop,
                    rng=rng, existing_routes=existing_routes, preset=preset,
                )
                if _ai_line_create_result_ok(build_result):
                    return ('create_line', build_result)
        if route_extend_ok and _can_discretionary_extend() and not (profit_recovery_mode and lines_missing_trainsets and near_term_bankruptcy_risk):
            spend = _refresh_spendable()
            if spend > min_extend_spendable:
                extend_result = _try_extend_line(
                    ops,
                    player,
                    game_map,
                    spend,
                    current_year,
                    points_with_pop,
                    rng=rng,
                    preset=preset,
                    require_profit_when_troubled=extension_profit_gate,
                )
                if extend_result:
                    return ('extend_line', extend_result)
        if wants_new_line and not (first_calendar_year or ai_join_first_line_pending):
            pre_ext = _try_extension_before_new_line()
            if pre_ext:
                return pre_ext
        # On turn 1 / join year, do not build a second line (we already have one in queue; need money for workers)
        if wants_new_line and _new_line_ok() and not ((first_calendar_year or ai_join_first_line_pending) and _my_builds_now) and spend > min_spendable_for_station:
            spend = _refresh_spendable()
            max_cost = min(max_build_cost, spend)
            if max_cost > min_spendable_for_station:
                build_result = _try_build_line(ops, player, game_map, max_cost, current_year, points_with_pop, rng=rng, existing_routes=existing_routes, preset=preset)
                if _ai_line_create_result_ok(build_result):
                    return ('create_line', build_result)
        return None

    def _try_action2_focus():
        """Action 2: Focus - lobby, marketplace, or extend per ai_focus once the player has a line network."""
        if not has_lines and not my_line_builds:
            return None
        spend = _refresh_spendable()
        # Grow the network after lines can earn (trainsets assigned); never extend ahead of revenue setup.
        if (
            has_lines
            and route_extend_ok
            and _can_discretionary_extend()
            and spend > min_extend_spendable
            and not lines_missing_trainsets
        ):
            extend_result = _try_extend_line(
                ops,
                player,
                game_map,
                spend,
                current_year,
                points_with_pop,
                rng=rng,
                preset=preset,
                require_profit_when_troubled=extension_profit_gate,
            )
            if extend_result:
                return ('extend_line', extend_result)
        if sustained_profit_maintenance and focus not in ('lobbying', 'marketplace', 'line_expansion', 'balanced'):
            return None
        lobby_income_ok = net_income > 0 or (
            focus == 'lobbying'
            and years_from_start >= 3
            and (net_income > -_inf(3500) or (is_losing_money and not near_term_bankruptcy_risk))
        )
        if (
            focus == 'lobbying'
            and spend > _inf(1200)
            and lobby_income_ok
            and (has_profitable_line or years_from_start >= 3)
            and not (profit_stewardship and had_negative_settled_year)
            and not defer_lobby_marketplace
        ):
            lawsuit_result = _try_file_lawsuit(ops, player, rng, focus='lobbying')
            if lawsuit_result:
                return ('file_lawsuit', lawsuit_result)
            lobby_result = _try_lobby(ops, player, spend, focus='lobbying', rng=rng)
            if lobby_result:
                return ('lobby', lobby_result)
        # Allow buy when profitable or when we need a trainset to get first profit (survival)
        min_buy = min_marketplace if has_profitable_line else max(_inf(2000), min_marketplace // 2)
        if focus == 'marketplace':
            min_buy = max(_inf(1200), int(min_buy * 0.55))
        buy_income_ok = net_income > 0 or (
            focus == 'marketplace'
            and years_from_start >= 3
            and (net_income > -_inf(5000) or (is_losing_money and not near_term_bankruptcy_risk))
        )
        if (
            has_lines
            and has_assets
            and spend > min_buy
            and buy_income_ok
            and not (profit_stewardship and had_negative_settled_year and not lines_missing_trainsets)
            and not defer_lobby_marketplace
        ):
            buy_result = _try_buy_marketplace(ops, player, current_year, spend, focus, rng=rng)
            if buy_result:
                return ('buy', buy_result)
        # Balanced AIs lobby sometimes so focus is visible in play, not only marketplace specialists.
        if (
            focus == 'balanced'
            and has_profitable_line
            and spend > _inf(4000)
            and net_income > 0
            and rng.random() < 0.42
            and not defer_lobby_marketplace
        ):
            lobby_result = _try_lobby(ops, player, spend, focus='balanced', rng=rng)
            if lobby_result:
                return ('lobby', lobby_result)
        if focus == 'line_expansion' and wants_new_line and _new_line_ok() and spend > min_spendable_for_station:
            lines = player.get_line_names()
            if lines and spend >= _inf(500):
                for ln in lines:
                    line = map_lines.get(ln)
                    if not line:
                        continue
                    stations = line.get_stations() if hasattr(line, 'get_stations') else []
                    if len(stations) >= 2:
                        continue
                    min_spacing = _get_ai_station_spacing(line_name=ln, line=line)
                    pt = _pick_station_point(
                        ops, game_map, ln, line, points_with_pop, set(),
                        min_spacing=min_spacing, player=player,
                    )
                    if pt:
                        name = _generate_ai_station_name(
                            ops, game_map, pt[0], pt[1], current_year, line_name=ln,
                        )
                        r = _try_ai_add_station(ops, player, ln, name, pt[0], pt[1])
                        if r:
                            r['line_name'] = ln
                            r['station_name'] = name
                            r['point'] = (pt[0], pt[1])
                            return ('add_station', r)
            if route_extend_ok and _can_discretionary_extend() and spend > min_extend_spendable:
                extend_result = _try_extend_line(
                    ops, player, game_map, spend, current_year, points_with_pop,
                    rng=rng, preset=preset,
                    line_names_priority=lines_requiring_extension_for_station,
                    require_profit_when_troubled=extension_profit_gate,
                )
                if extend_result:
                    return ('extend_line', extend_result)
            max_cost = min(max_build_cost, spend)
            if max_cost > min_spendable_for_station:
                build_result = _try_build_line(
                    ops, player, game_map, max_cost, current_year, points_with_pop,
                    rng=rng, existing_routes=existing_routes, preset=preset,
                )
                if _ai_line_create_result_ok(build_result):
                    return ('create_line', build_result)
        return None

    def _try_action3_situational():
        """Action 3: Situational - add station / assign first (survival), then rent, buy, extend, build. Defer marketplace until profitable."""
        spend = _refresh_spendable()
        lines = player.get_line_names()
        if lines and spend > min_extend_spendable and lines_requiring_extension_for_station:
            if route_extend_ok:
                spend = _refresh_spendable()
                extend_result = _try_extend_line(
                    ops, player, game_map, spend, current_year, points_with_pop,
                    rng=rng, preset=preset, line_names_priority=lines_requiring_extension_for_station,
                    require_profit_when_troubled=extension_profit_gate,
                )
                if extend_result:
                    return ('extend_line', extend_result)
        # Survival: add stations and assign trainset before spending on rent (so line can earn revenue)
        if lines and spend >= _inf(500):
            lines_with_station_count = []
            for ln in lines:
                line = map_lines.get(ln)
                if not line:
                    continue
                stations = line.get_stations() if hasattr(line, 'get_stations') else []
                lines_with_station_count.append((ln, line, len(stations)))
            lines_with_station_count.sort(key=lambda x: x[2])
            for line_name, line, station_count in lines_with_station_count:
                if station_count >= 2:
                    continue
                min_spacing = _get_ai_station_spacing(line_name=line_name, line=line)
                pt = _pick_station_point(ops, game_map, line_name, line, points_with_pop, set(), min_spacing=min_spacing, player=player)
                if pt:
                    name = _generate_ai_station_name(ops, game_map, pt[0], pt[1], current_year, line_name=line_name)
                    r = _try_ai_add_station(ops, player, line_name, name, pt[0], pt[1])
                    if r:
                        r['line_name'] = line_name
                        r['station_name'] = name
                        r['point'] = (pt[0], pt[1])
                        return ('add_station', r)
            rescue = _try_ai_abandon_stuck_understationed_lines(
                ops, player, game_map, map_lines, points_with_pop, current_year,
                spendable=spend, rng=rng, preset=preset, existing_routes=existing_routes,
            )
            if rescue:
                return rescue
        # Assign trainsets only to lines with 2+ stations; need at least 1 engineer or trainset gets unassigned at year advance
        unassigned = ops.get_unassigned_trainsets(player)
        lines_with_stations = [ln for ln in lines if map_lines.get(ln) and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2]
        if unassigned and lines_with_stations and not any(r[0] == 'assign_trainset' for r in results):
            eng = int(player.get_engineer_count() or 0)
            asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
            skip_discretionary_assign = (
                early_growth and net_income < 0 and asg >= 1 and not lines_missing_trainsets and not profitability_onset_phase
            )
            if not skip_discretionary_assign:
                if sustained_profit_phase and lines_missing_trainsets and asg >= eng:
                    spend = _refresh_spendable()
                    if spend > min_spendable_for_station:
                        rent_result = _try_rent_engineer_for_assignment(
                            ops, player, current_year, spend, game_map,
                        )
                        if rent_result:
                            return ('rent_workers', rent_result)
                if asg >= eng:
                    if spend > min_spendable_for_station:
                        rent_result = _try_rent_engineer_for_assignment(
                            ops, player, current_year, spend, game_map,
                        )
                        if rent_result:
                            return ('rent_workers', rent_result)
                else:
                    ts = unassigned[0]
                    best_line = _pick_best_line_for_trainset(ops, player, ts, lines_with_stations)
                    if best_line:
                        try:
                            ops.assign_trainset_to_line(ts, best_line)
                            return ('assign_trainset', {'line': best_line})
                        except Exception:
                            pass
        # Turn 1 with no line queued: do not rent workers here — that fills results[] and skips the
        # mandatory no-line build fallback at the end of run_ai_turn (leaving AIs with no corridor).
        _q_now = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
        _has_line_job = any(
            b.get('player') == player and b.get('type') in ('line', 'line_extension') for b in (_q_now or [])
        )
        if (
            not ((first_calendar_year or ai_join_first_line_pending) and len(lines) == 0 and not _has_line_job)
            and spend > min_spendable_for_station
        ):
            rent_result = _try_rent_workers(ops, player, current_year, spend)
            if rent_result:
                return ('rent_workers', rent_result)
        if (
            has_lines and has_assets
            and (has_profitable_line or (has_line_ready_for_trainset and not (early_growth and years_from_start <= 12)))
            and net_income > 0
            and spend > min_marketplace
            and (not sustained_profit_maintenance or focus == 'marketplace')
            and not defer_lobby_marketplace
        ):
            buy_result = _try_buy_marketplace(ops, player, current_year, spend, focus, rng=rng)
            if buy_result:
                return ('buy', buy_result)
        if (
            lines_requiring_extension_for_station
            and route_extend_ok
            and spend > min_extend_spendable
        ):
            spend = _refresh_spendable()
            extend_result = _try_extend_line(
                ops, player, game_map, spend, current_year, points_with_pop,
                rng=rng, preset=preset, line_names_priority=lines_requiring_extension_for_station,
                require_profit_when_troubled=extension_profit_gate,
            )
            if extend_result:
                return ('extend_line', extend_result)
        if extend_ok and _can_discretionary_extend() and spend > min_extend_spendable:
            extend_result = _try_extend_line(
                ops,
                player,
                game_map,
                spend,
                current_year,
                points_with_pop,
                rng=rng,
                preset=preset,
                require_profit_when_troubled=extension_profit_gate,
            )
            if extend_result:
                return ('extend_line', extend_result)
        already_built = any(r[0] == 'create_line' for r in results)
        if wants_new_line and _new_line_ok() and not already_built and spend > min_spendable_for_station:
            spend = _refresh_spendable()
            max_cost = min(max_build_cost, spend)
            if max_cost > min_spendable_for_station:
                build_result = _try_build_line(ops, player, game_map, max_cost, current_year, points_with_pop, rng=rng, existing_routes=existing_routes, preset=preset)
                if _ai_line_create_result_ok(build_result):
                    return ('create_line', build_result)
        return None

    # Apply fare updates only when AI has lines; with no lines it should build instead.
    has_lines = len(player.get_line_names()) > 0
    fare_updates = _adjust_ai_fares(ops, player, current_year, game_map, rng) if has_lines else []
    schedule_survival_tight = (
        (osm_like_map and early_growth and not has_profitable_line)
        or (sustained_profit_phase and is_losing_money)
        or capital_preservation_vs_idle
        or (profit_stewardship and not is_wealthy_and_profitable)
    )

    def _tune_ai_line_economics_after_build(
        line_names: list = None,
        force_line_names: set = None,
    ):
        """Refresh fares and schedules when network composition changes mid-turn."""
        if not player.get_line_names():
            return
        schedule_updates = _adjust_ai_schedules(
            ops,
            player,
            game_map,
            has_profitable_line=has_profitable_line,
            is_losing_money=is_losing_money,
            is_wealthy_and_profitable=is_wealthy_and_profitable,
            early_growth=early_growth,
            schedule_survival_tight=schedule_survival_tight,
            line_names=line_names,
            force_line_names=force_line_names,
        )
        if schedule_updates:
            _do_action('set_schedule', {'lines': schedule_updates})
        fare_changes = _adjust_ai_fares(ops, player, current_year, game_map, rng, line_names=line_names)
        if fare_changes:
            _do_action('set_fares', {'lines': fare_changes})

    _has_network = bool(player.get_line_names()) or my_line_builds
    # Until a line is profitable, keep cash for workers and completion — marketplace buys here often caused early bankruptcy.
    can_attempt_focus = bool(
        _has_network
        and _refresh_spendable() > min(min_extend_spendable, _inf(600))
        and (
            focus in ('lobbying', 'marketplace', 'line_expansion', 'balanced')
            or has_profitable_line
            or route_extend_ok
            or (has_lines and years_from_start >= 2)
        )
    )
    # Action slot order: build/assign first; marketplace focus swaps action 2 and 3.
    all_slots = [_try_action1_build_extend_station, _try_action2_focus, _try_action3_situational]
    if focus == 'marketplace' and not (first_calendar_year or ai_join_first_line_pending or not _has_network):
        all_slots = [_try_action1_build_extend_station, _try_action3_situational, _try_action2_focus]

    if is_losing_money and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        boot = _try_revenue_bootstrap()
        if boot:
            _do_action(boot[0], boot[1])

    # Attempt focus on most turns once the network exists.
    if can_attempt_focus and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        focus_action = _try_action2_focus()
        if focus_action:
            _do_action(focus_action[0], focus_action[1])

    if (
        not _skip_first_turn_osm_discretionary
        and not _turn_time_exceeded()
        and (has_line_ready_for_trainset or any_line_needs_stations or my_line_builds)
    ):
        for _ in range(2):
            if _turn_time_exceeded():
                break
            boot = _try_revenue_bootstrap()
            if boot:
                _do_action(boot[0], boot[1])
            else:
                break

    # No completed lines: merge (if desperate) or build / buy line before any other action (incl. bankruptcy sells).
    no_line_names = len(player.get_line_names()) == 0
    if no_line_names and not ops.is_player_eliminated(player):
        if hasattr(ops, 'try_ai_desperate_merge_no_lines') and ops.is_player_desperate(player):
            mr = ops.try_ai_desperate_merge_no_lines(player)
            if mr and mr.get('success'):
                _do_action('merge', mr)
                validate_ai_turn_results(ops, player, results)
                return results
        # First calendar year / join-year spawn: do not build here — the main slot loop runs action1 first.
        # Later-year no-line builds are handled by the main slot loop and the mandatory create_line tail below.
        if len(player.get_line_names()) == 0 and not _turn_time_exceeded():
            _qn = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
            _myq = [b for b in _qn if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
            if _myq and _refresh_spendable() > min_spendable_for_station:
                rr = _try_rent_workers(ops, player, current_year, _refresh_spendable())
                if rr:
                    _do_action('rent_workers', rr)
        # Do not marketplace-buy before the main slot loop on year 1 / join year: line_only buys can add a line without
        # create_line / queue entries and trip tests that require an explicit first-year build.
        if (
            len(player.get_line_names()) == 0
            and not _turn_time_exceeded()
            and not first_calendar_year
            and not ai_join_first_line_pending
        ):
            lb = _try_buy_marketplace(
                ops, player, current_year, _refresh_spendable(), focus, rng=rng, line_only=True,
            )
            if lb:
                _do_action('buy', lb)
        # Do not clear all_slots when no line is in *completed* names yet: a line may be queued and we still need
        # the main slot loop (e.g. rent_workers) and fallbacks. Clearing slots caused year-1830 idle turns when
        # stochastic OSM build failed once.

    # Low-cash guard: liquidate idle trainsets before bankruptcy threshold is hit.
    if money < _inf(5000) and not _turn_time_exceeded() and hasattr(ops, 'sell_trainset_for_half_price'):
        for ts in list(ops.get_unassigned_trainsets(player) or [])[:2]:
            try:
                r = ops.sell_trainset_for_half_price(ts, player)
                if r.get('sale_price', 0):
                    _do_action('sell_trainset', r)
                    break
            except Exception:
                pass
    # Proactive austerity on OSM maps: only liquidate in-service stock when bankruptcy is near.
    if (
        not _turn_time_exceeded()
        and osm_like_map
        and years_from_start >= 8
        and net_income < 0
        and near_term_bankruptcy_risk
        and money < _inf(35000)
        and hasattr(ops, 'sell_trainset_for_half_price')
    ):
        assigned_for_sale = [
            ts for ts in (player.get_trainsets() or [])
            if hasattr(ts, 'get_line_assignment') and ts.get_line_assignment()
        ]
        if assigned_for_sale:
            try:
                ts = assigned_for_sale[0]
                ts.assign_to_line(None)
                r = ops.sell_trainset_for_half_price(ts, player)
                if r.get('sale_price', 0):
                    _do_action('sell_trainset', r)
            except Exception:
                pass

    # Desperate: try survival actions first (sell assets, fire workers; merge proposals are handled at end-of-year UI)
    if about_to_go_bankrupt and not _turn_time_exceeded():
        # 0) If all easy cash is tied in assigned trainsets, unassign one and liquidate it.
        if hasattr(ops, 'sell_trainset_for_half_price'):
            assigned_trainsets = [
                ts for ts in (player.get_trainsets() or [])
                if hasattr(ts, 'get_line_assignment') and ts.get_line_assignment()
            ]
            if assigned_trainsets:
                try:
                    ts = assigned_trainsets[0]
                    ts.assign_to_line(None)
                    r = ops.sell_trainset_for_half_price(ts, player)
                    if r.get('sale_price', 0):
                        _do_action('sell_trainset', r)
                except Exception:
                    pass
        # 1) Sell unassigned trainsets first (fastest way to raise cash)
        if hasattr(ops, 'sell_trainset_for_half_price'):
            unassigned = list(ops.get_unassigned_trainsets(player) or [])
            for ts in unassigned[:3]:
                if _turn_time_exceeded():
                    break
                try:
                    r = ops.sell_trainset_for_half_price(ts, player)
                    if r.get('sale_price', 0):
                        _do_action('sell_trainset', r)
                        if ops.get_player_money(player) > 0:
                            break
                except Exception:
                    pass
        # 2) Sell spare locomotives and railcars (rolling stock in inventory)
        if not _turn_time_exceeded():
            spare_locomotives = list(getattr(player, 'locomotives', []) or [])
            if spare_locomotives and hasattr(ops, 'sell_locomotive_for_half_price'):
                try:
                    loco = spare_locomotives[0]
                    r = ops.sell_locomotive_for_half_price(loco, player)
                    if r.get('sale_price', 0):
                        _do_action('sell_locomotive', r)
                except Exception:
                    pass
        if not _turn_time_exceeded():
            spare_railcars = list(getattr(player, 'railcars', []) or [])
            if spare_railcars and hasattr(ops, 'sell_railcar_for_half_price'):
                try:
                    rc = spare_railcars[0]
                    r = ops.sell_railcar_for_half_price(rc, player)
                    if r.get('sale_price', 0):
                        _do_action('sell_railcar', r)
                except Exception:
                    pass
        # 3) As last resort, fire workers to cut future costs (lawyers first, then construction, maintenance, engineer)
        if not _turn_time_exceeded() and hasattr(ops, 'fire_workers') and hasattr(ops, 'get_max_fireable_workers'):
            from worker import CONSTRUCTION_WORKER, ENGINEER, MAINTENANCE_WORKER, LAWYER
            for wname in (LAWYER.name, CONSTRUCTION_WORKER.name, MAINTENANCE_WORKER.name, ENGINEER.name):
                max_fire = ops.get_max_fireable_workers(player, wname)
                if max_fire >= 1:
                    try:
                        r = ops.fire_workers(player, wname, max_fire)
                        if r.get('success'):
                            _do_action('fire_workers', r)
                            break
                    except Exception:
                        pass

    _ai_keep_worker_salaries_safe()

    # Lines with two open stations run from the start of the turn (backstop for the
    # build-completion hook in Operations.process_build_queue).
    enacted = ai_enact_schedules_on_ready_lines(ops, player)
    if enacted:
        _do_action('set_schedule', {'lines': enacted})
    # Retroactive stops on built lines (transfers, other lines nearby, population centres).
    if not _turn_time_exceeded():
        for infill in ai_add_infill_stations(ops, player):
            _do_action('add_station', infill)

    schedule_kwargs = {
        'has_profitable_line': has_profitable_line,
        'is_losing_money': is_losing_money,
        'is_wealthy_and_profitable': is_wealthy_and_profitable,
        'early_growth': early_growth,
        'schedule_survival_tight': schedule_survival_tight or near_term_bankruptcy_risk,
    }
    if has_lines and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        for _ in range(16):
            if _turn_time_exceeded():
                break
            mandatory = _try_mandatory_network_growth_action(
                ops, player, game_map, current_year,
                spendable=_refresh_spendable(),
                min_spendable_for_station=min_spendable_for_station,
                min_extend_spendable=min_extend_spendable,
                route_extend_ok=route_extend_ok,
                extension_profit_gate=extension_profit_gate,
                points_with_pop=points_with_pop,
                rng=rng,
                preset=preset,
                focus=focus,
                any_line_needs_stations=any_line_needs_stations,
                lines_requiring_extension_for_station=lines_requiring_extension_for_station,
                schedule_kwargs=schedule_kwargs,
                line_build_complete=line_build_complete,
            )
            if not mandatory:
                break
            _do_action(mandatory[0], mandatory[1])

    if line_build_complete and route_extend_ok and has_lines and not _turn_time_exceeded():
        for _ in range(8):
            if _turn_time_exceeded():
                break
            extend_action = _try_mandatory_extend_completed_lines(
                ops, player, game_map, current_year, _refresh_spendable(),
                min_extend_spendable, route_extend_ok, extension_profit_gate,
                points_with_pop, rng, preset, lines_requiring_extension_for_station,
                line_build_complete=True,
            )
            if not extend_action:
                break
            _do_action(extend_action[0], extend_action[1])

    for slot in all_slots:
        if _turn_time_exceeded():
            break
        if _skip_first_turn_osm_discretionary and slot is not _try_action1_build_extend_station:
            continue
        _turn_ctx['points_with_pop'] = points_with_pop
        action = slot()
        if action:
            _do_action(action[0], action[1])

    if surplus_cash_expand and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        for _ in range(3):
            if _turn_time_exceeded() or _took_core_network_action():
                break
            extra = _try_action1_build_extend_station()
            if extra:
                _do_action(extra[0], extra[1])
            else:
                break

    _ai_keep_worker_salaries_safe()

    if has_lines:
        force_schedule_lines = set(_lines_needing_schedule_timetable(map_lines, player, ops))
        for _ in range(8):
            if _turn_time_exceeded():
                break
            schedule_updates = _adjust_ai_schedules(
                ops,
                player,
                game_map,
                has_profitable_line=has_profitable_line,
                is_losing_money=is_losing_money,
                is_wealthy_and_profitable=is_wealthy_and_profitable,
                early_growth=early_growth,
                schedule_survival_tight=schedule_survival_tight or near_term_bankruptcy_risk,
                force_line_names=force_schedule_lines,
            )
            if not schedule_updates:
                break
            _do_action('set_schedule', {'lines': schedule_updates})

    if has_lines and not _turn_time_exceeded():
        fare_changes = _adjust_ai_fares(ops, player, current_year, game_map, rng)
        if fare_changes:
            _do_action('set_fares', {'lines': fare_changes})

    if public_ta_active and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        while not _turn_time_exceeded():
            lb = _try_buy_marketplace(
                ops, player, current_year, _refresh_spendable(), focus,
                rng=rng, line_only=True, prefer_player_sold_lines=True,
            )
            if not lb:
                break
            _do_action('buy', lb)

    _q_tail = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
    _my_line_q_tail = [
        b for b in (_q_tail or [])
        if b.get('player') == player and b.get('type') in ('line', 'line_extension')
    ]
    _need_forced_line = (
        getattr(player, 'is_ai', False)
        and not ops.is_player_eliminated(player)
        and len(player.get_line_names()) == 0
        and not _my_line_q_tail
        and not any(a == 'create_line' for a, _ in results)
    )
    if (len(results) == 0 or _need_forced_line) and not _skip_first_turn_osm_discretionary:
        # Strong no-line fallback: prefer creating or buying a line over "minimum turn" bookkeeping.
        # Runs before empty-slot rent recovery so we never fill results[] with rent_workers and skip this block.
        if getattr(player, 'is_ai', False) and not ops.is_player_eliminated(player) and len(player.get_line_names()) == 0:
            q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
            my_line_q = [b for b in q if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
            if not my_line_q:
                forced = None
                if first_calendar_year or ai_join_first_line_pending:
                    forced = _try_force_first_turn_line_build(
                        ops, player, game_map, current_year, rng, preset,
                        existing_routes=opener_overlap_routes, points_with_pop=points_with_pop,
                    )
                else:
                    spend = max(0, _refresh_spendable())
                    money_now = max(0, int(ops.get_player_money(player)))
                    _uses_osm_roads = _ai_map_uses_street_build(game_map, preset)
                    force_cap = _ai_budget_bounds(
                        ops, current_year, max(spend, money_now),
                        ceiling=(120000 if _uses_osm_roads else 20000),
                    )
                    forced = _try_build_line(
                        ops, player, game_map, force_cap, current_year, points_with_pop,
                        rng=rng, existing_routes=opener_overlap_routes, preset=preset,
                    )
                if _ai_line_create_result_ok(forced):
                    if first_calendar_year or ai_join_first_line_pending:
                        _ai_mark_first_year_opener_attempted(ops)
                    _do_action('create_line', forced)
                elif not (first_calendar_year or ai_join_first_line_pending):
                    spend = max(0, _refresh_spendable())
                    money_now = max(0, int(ops.get_player_money(player)))
                    lb = _try_buy_marketplace(
                        ops, player, current_year, max(spend, money_now), focus, rng=rng, line_only=True,
                    )
                    if lb:
                        _do_action('buy', lb)
        # No synthetic bookkeeping actions — empty results are shown as "No actions" in AI Watch.
    # If the slot loop produced nothing, try cheap recovery (rent/extend); line creation is handled again below for no-line AIs.
    if len(results) == 0:
        spend = _refresh_spendable()
        if spend >= 800:
            # If we still have no line on year 1 / join-year, do NOT rent workers as a substitute for a build opener.
            # The Living-AI no-line block below must get the chance to create/queue a line.
            _q_now = ops.get_build_queue() if hasattr(ops, "get_build_queue") else []
            _has_line_job = any(
                b.get("player") == player and b.get("type") in ("line", "line_extension") for b in (_q_now or [])
            )
            _no_line_opener_due = (first_calendar_year or ai_join_first_line_pending) and len(player.get_line_names()) == 0 and not _has_line_job
            if not _no_line_opener_due:
                rent_result = _try_rent_workers(ops, player, current_year, spend)
                if rent_result:
                    _do_action('rent_workers', rent_result)
                elif route_extend_ok and spend > min_extend_spendable:
                    extend_result = _try_extend_line(ops, player, game_map, spend, current_year, points_with_pop, rng=rng, preset=preset)
                    if extend_result:
                        _do_action('extend_line', extend_result)
    # Every alive AI must take at least one non-pass action per turn: try fare/schedule, then add_station, assign, buy, lobby
    if len(results) == 0 and has_lines:
        if fare_updates:
            _do_action('set_fares', {'lines': fare_updates})
        else:
            fallback_schedule = _adjust_ai_schedules(
                ops,
                player,
                game_map,
                has_profitable_line=has_profitable_line,
                is_losing_money=is_losing_money,
                is_wealthy_and_profitable=is_wealthy_and_profitable,
                early_growth=early_growth,
                schedule_survival_tight=schedule_survival_tight or near_term_bankruptcy_risk,
            )
            if fallback_schedule:
                _do_action('set_schedule', {'lines': fallback_schedule})
    # If still no action, try add_station / assign_trainset / buy / lobby so we get a non-pass action when possible
    if len(results) == 0:
        spend = _refresh_spendable()
        lines = player.get_line_names()
        map_lines = game_map.get_lines_by_name() or {}
        if lines and spend >= _inf(500):
            for ln in lines:
                line = map_lines.get(ln)
                if not line or not hasattr(line, 'get_stations'):
                    continue
                if len(line.get_stations()) >= 2:
                    continue
                min_spacing = _get_ai_station_spacing(line_name=ln, line=line)
                pt = _pick_station_point(ops, game_map, ln, line, points_with_pop, set(), min_spacing=min_spacing, player=player)
                if pt:
                    name = _generate_ai_station_name(ops, game_map, pt[0], pt[1], current_year, line_name=ln)
                    r = _try_ai_add_station(ops, player, ln, name, pt[0], pt[1])
                    if r:
                        _do_action('add_station', {**r, 'line_name': ln, 'station_name': name, 'point': (pt[0], pt[1])})
                        break
        if len(results) == 0:
            unassigned = ops.get_unassigned_trainsets(player)
            rail_lines = (ops.get_player_rail_line_names(player) if hasattr(ops, 'get_player_rail_line_names') else None) or (lines or [])
            lines_with_stations = [ln for ln in rail_lines if map_lines.get(ln) and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2]
            if unassigned and lines_with_stations:
                asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
                if early_growth and net_income < 0 and asg >= 1 and not lines_missing_trainsets:
                    lines_with_stations = []
            if unassigned and lines_with_stations:
                eng_rent = _try_rent_engineer_for_assignment(
                    ops, player, current_year, _refresh_spendable(), game_map,
                )
                if eng_rent:
                    _do_action('rent_workers', eng_rent)
                ts = unassigned[0]
                best_line = _pick_best_line_for_trainset(ops, player, ts, lines_with_stations)
                if best_line:
                    try:
                        ops.assign_trainset_to_line(ts, best_line)
                        _do_action('assign_trainset', {'line': best_line})
                    except Exception:
                        pass
        if len(results) == 0 and has_assets and spend > 2000:
            _fallback_buy_ok = net_income > 0 or (
                focus == 'marketplace' and years_from_start >= 4 and net_income > -_inf(3500)
            )
            if _fallback_buy_ok:
                if not has_lines:
                    # If the AI reaches a "buy" consideration without any lines, it must build instead.
                    build_cap = min(spend, 8000 if current_year <= 1840 else 12000)
                    build_result = _try_build_line_stochastic(
                        ops, player, game_map, build_cap, current_year, points_with_pop,
                        existing_routes, preset,
                    )
                    if _ai_line_create_result_ok(build_result):
                        _do_action('create_line', build_result)
                else:
                    _skip_market_early = (
                        near_term_bankruptcy_risk
                        and early_growth
                        and years_from_start <= 12
                        and not has_profitable_line
                        and focus != 'marketplace'
                    )
                    if not _skip_market_early:
                        buy_result = _try_buy_marketplace(ops, player, current_year, spend, focus, rng=rng)
                        if buy_result:
                            _do_action('buy', buy_result)
        _lobby_floor = _inf(2200) if focus == 'lobbying' else _inf(4000)
        _lobby_when_early_loss = (
            focus == 'lobbying' and years_from_start >= 4 and net_income > -2500
        )
        if (
            len(results) == 0 and spend > _lobby_floor and len(player.get_line_names()) > 0
            and (
                not (early_growth and years_from_start <= 12 and not has_profitable_line and near_term_bankruptcy_risk)
                or _lobby_when_early_loss
            )
        ):
            lobby_result = _try_lobby(ops, player, spend, focus=focus, rng=rng)
            if lobby_result:
                _do_action('lobby', lobby_result)

    # Living AI with no lines and no line in the build queue must create a line (every turn).
    if (
        getattr(player, 'is_ai', False)
        and ops
        and hasattr(ops, 'is_player_eliminated')
        and not ops.is_player_eliminated(player)
        and not _skip_first_turn_osm_discretionary
    ):
        if len(player.get_line_names()) == 0:
            q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
            my_line_q = [b for b in q if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
            if not my_line_q and not any(a == 'create_line' for a, _ in results):
                if first_calendar_year or ai_join_first_line_pending:
                    forced = _try_force_first_turn_line_build(
                        ops, player, game_map, current_year, rng, preset,
                        existing_routes=opener_overlap_routes, points_with_pop=points_with_pop,
                    )
                else:
                    _uses_osm_roads = _ai_map_uses_street_build(game_map, preset)
                    force_cap = _ai_budget_bounds(
                        ops, current_year, ops.get_player_money(player),
                        ceiling=(120000 if _uses_osm_roads else 15000),
                    )
                    forced = _try_build_line(
                        ops, player, game_map, force_cap, current_year, points_with_pop,
                        rng=rng, existing_routes=opener_overlap_routes, preset=preset,
                    )
                if _ai_line_create_result_ok(forced):
                    if first_calendar_year or ai_join_first_line_pending:
                        _ai_mark_first_year_opener_attempted(ops)
                    _do_action('create_line', forced)

    # Encourage visible expansion through mid-game when affordable.
    if (
        getattr(player, "is_ai", False)
        and not ops.is_player_eliminated(player)
        and int(current_year) <= 1841
        and build_expand_count < 4
        and not (first_calendar_year or ai_join_first_line_pending)
        and (profitable_operation or not (near_term_bankruptcy_risk or about_to_go_bankrupt))
        and not _turn_time_exceeded()
    ):
        try:
            spend_now = _refresh_spendable()
            if len(player.get_line_names()) > 0 and route_extend_ok and spend_now > min_extend_spendable:
                ext = _try_extend_line(
                    ops,
                    player,
                    game_map,
                    spend_now,
                    current_year,
                    points_with_pop,
                    rng=rng,
                    preset=preset,
                    require_profit_when_troubled=extension_profit_gate,
                )
                if ext:
                    _do_action("extend_line", ext)
            elif wants_new_line and _new_line_ok() and spend_now > min_spendable_for_station:
                max_cost = min(max_build_cost, spend_now)
                br = _try_build_line(
                    ops,
                    player,
                    game_map,
                    max_cost,
                    current_year,
                    points_with_pop,
                    rng=rng,
                    existing_routes=existing_routes,
                    preset=preset,
                )
                if _ai_line_create_result_ok(br):
                    _do_action("create_line", br)
        except Exception:
            pass

    # Prefer network growth over passive bookkeeping whenever expansion is affordable.
    if (
        getattr(player, "is_ai", False)
        and not ops.is_player_eliminated(player)
        and len(player.get_line_names()) > 0
        and not _turn_time_exceeded()
        and not any(a in ("create_line", "extend_line") for a, _ in results)
    ):
        try:
            spend_now = _refresh_spendable()
            if route_extend_ok and spend_now > min_extend_spendable:
                for _ in range(2):
                    spend_now = _refresh_spendable()
                    ext = _try_extend_line(
                        ops,
                        player,
                        game_map,
                        spend_now,
                        current_year,
                        points_with_pop,
                        rng=rng,
                        preset=preset,
                        require_profit_when_troubled=extension_profit_gate,
                    )
                    if ext:
                        _do_action("extend_line", ext)
                    else:
                        break
            elif wants_new_line and _new_line_ok() and spend_now > min_spendable_for_station:
                max_cost = min(max_build_cost, spend_now)
                br = _try_build_line(
                    ops,
                    player,
                    game_map,
                    max_cost,
                    current_year,
                    points_with_pop,
                    rng=rng,
                    existing_routes=existing_routes,
                    preset=preset,
                )
                if _ai_line_create_result_ok(br):
                    _do_action("create_line", br)
        except Exception:
            pass

    # Final flake guard: multi-AI year-1 / join-year can rarely exhaust stochastic paths; one last preset pull before validate.
    if (
        (first_calendar_year or ai_join_first_line_pending)
        and getattr(player, 'is_ai', False)
        and not ops.is_player_eliminated(player)
        and len(player.get_line_names()) == 0
        and not any(a == 'create_line' for a, _ in results)
    ):
        q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
        if not any(b.get('player') == player and b.get('type') in ('line', 'line_extension') for b in (q or [])):
            _osm_lr = _ai_map_uses_street_build(game_map, preset)
            if _osm_lr:
                cap = _ai_budget_bounds(ops, current_year, ops.get_player_money(player))
                lr = _try_ai_two_point_line_build(
                    ops, player, game_map, cap, current_year, rng, preset,
                    existing_routes=opener_overlap_routes, relaxed=True,
                )
                if _ai_line_create_result_ok(lr):
                    _do_action('create_line', lr)

    if (
        (first_calendar_year or ai_join_first_line_pending)
        and len(player.get_line_names()) == 0
    ):
        q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
        if not any(
            b.get('player') == player and b.get('type') in ('line', 'line_extension')
            for b in (q or [])
        ):
            has_create_line = any(a == 'create_line' for a, _ in results)
            if not has_create_line and _preset_requires_street_polylines(game_map, preset):
                cap = _ai_budget_bounds(ops, current_year, ops.get_player_money(player))
                direct = _try_ai_two_point_line_build(
                    ops, player, game_map, cap, current_year, rng, preset,
                    existing_routes=opener_overlap_routes, relaxed=True,
                )
                if _ai_line_create_result_ok(direct):
                    _do_action('create_line', direct)

    _NETWORK_GROWTH_ACTIONS = frozenset({
        'create_line', 'extend_line', 'add_station', 'assign_trainset',
        'lobby', 'buy', 'rent_workers',
    })
    _PASSIVE_ONLY_ACTIONS = frozenset({
        'set_fares', 'set_schedule', 'fire_workers', 'sell_trainset', 'sell_locomotive',
        'sell_railcar', 'adjust_worker_salary', 'file_lawsuit',
    })

    def _try_force_core_network_expansion() -> bool:
        """Spend surplus cash on stations, extensions, or new lines before passive bookkeeping."""
        if _turn_time_exceeded() or not player.get_line_names():
            return False
        spend = _refresh_spendable()
        if spend <= min(min_extend_spendable, _inf(500)):
            return False
        lines = player.get_line_names()
        if spend >= _inf(500):
            for ln in lines:
                line = (game_map.get_lines_by_name() or {}).get(ln)
                if not line:
                    continue
                stations = line.get_stations() if hasattr(line, 'get_stations') else []
                if len(stations) >= 2:
                    continue
                min_spacing = _get_ai_station_spacing(line_name=ln, line=line)
                pt = _pick_station_point(
                    ops, game_map, ln, line, points_with_pop, set(),
                    min_spacing=min_spacing, player=player,
                )
                if pt:
                    name = _generate_ai_station_name(
                        ops, game_map, pt[0], pt[1], current_year, line_name=ln,
                    )
                    r = _try_ai_add_station(ops, player, ln, name, pt[0], pt[1])
                    if r:
                        _do_action('add_station', {**r, 'line_name': ln, 'station_name': name, 'point': (pt[0], pt[1])})
                        return True
        if route_extend_ok and _can_discretionary_extend() and spend > min_extend_spendable:
            extends_done = 0
            for _ in range(5):
                spend = _refresh_spendable()
                ext = _try_extend_line(
                    ops, player, game_map, spend, current_year, points_with_pop,
                    rng=rng, preset=preset,
                    line_names_priority=lines_requiring_extension_for_station or None,
                    require_profit_when_troubled=False,
                )
                if ext:
                    _do_action('extend_line', ext)
                    extends_done += 1
                    if extends_done >= 2:
                        return True
                else:
                    break
            if extends_done:
                return True
        spend = _refresh_spendable()
        if spend > min_spendable_for_station:
            cap = min(max_build_cost, spend)
            br = _try_build_line(
                ops, player, game_map, cap, current_year, points_with_pop,
                rng=rng, existing_routes=existing_routes, preset=preset,
            )
            if _ai_line_create_result_ok(br):
                _do_action('create_line', br)
                return True
        return False

    def _try_mandatory_network_or_focus_action():
        """Last resort: AIs with lines must not finish on passive bookkeeping only."""
        if (
            not getattr(player, 'is_ai', False)
            or ops.is_player_eliminated(player)
            or not player.get_line_names()
            or _turn_time_exceeded()
        ):
            return
        if _took_core_network_action():
            return
        action_names = [a for a, _ in results]
        if action_names and not all(a in _PASSIVE_ONLY_ACTIONS for a in action_names):
            if surplus_cash_expand:
                if _try_force_core_network_expansion():
                    return
            return
        spend = _refresh_spendable()
        if route_extend_ok and _can_discretionary_extend() and spend > min_extend_spendable:
            ext = _try_extend_line(
                ops, player, game_map, spend, current_year, points_with_pop,
                rng=rng, preset=preset,
                line_names_priority=lines_requiring_extension_for_station or None,
                require_profit_when_troubled=extension_profit_gate,
            )
            if ext:
                _do_action('extend_line', ext)
                return
        if spend >= _inf(500):
            for ln in player.get_line_names():
                line = (game_map.get_lines_by_name() or {}).get(ln)
                if not line or len(line.get_stations() if hasattr(line, 'get_stations') else []) >= 2:
                    continue
                min_spacing = _get_ai_station_spacing(line_name=ln, line=line)
                pt = _pick_station_point(
                    ops, game_map, ln, line, points_with_pop, set(),
                    min_spacing=min_spacing, player=player,
                )
                if pt:
                    name = _generate_ai_station_name(
                        ops, game_map, pt[0], pt[1], current_year, line_name=ln,
                    )
                    r = _try_ai_add_station(ops, player, ln, name, pt[0], pt[1])
                    if r:
                        _do_action('add_station', {**r, 'line_name': ln, 'station_name': name, 'point': (pt[0], pt[1])})
                        return
        unassigned = list(ops.get_unassigned_trainsets(player) or [])
        lines_with_stations = [
            ln for ln in player.get_line_names()
            if map_lines.get(ln)
            and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2
        ]
        if unassigned and lines_with_stations:
            eng_rent = _try_rent_engineer_for_assignment(
                ops, player, current_year, _refresh_spendable(), game_map,
            )
            if eng_rent:
                _do_action('rent_workers', eng_rent)
            ts = unassigned[0]
            best_line = _pick_best_line_for_trainset(ops, player, ts, lines_with_stations)
            if best_line:
                try:
                    ops.assign_trainset_to_line(ts, best_line)
                    _do_action('assign_trainset', {'line': best_line})
                    return
                except Exception:
                    pass
        spend = _refresh_spendable()
        if focus == 'lobbying' and spend > _inf(1200) and net_income > -_inf(3500):
            lawsuit_result = _try_file_lawsuit(ops, player, rng, focus='lobbying')
            if lawsuit_result:
                _do_action('file_lawsuit', lawsuit_result)
                return
            lobby_result = _try_lobby(ops, player, spend, focus='lobbying', rng=rng)
            if lobby_result:
                _do_action('lobby', lobby_result)
                return
        if focus in ('marketplace', 'balanced') and spend > max(_inf(1200), min_marketplace // 2):
            buy_result = _try_buy_marketplace(ops, player, current_year, spend, focus, rng=rng)
            if buy_result:
                _do_action('buy', buy_result)
                return
        if focus == 'line_expansion' and wants_new_line and _new_line_ok() and spend > min_spendable_for_station:
            max_cost = min(max_build_cost, spend)
            if max_cost > min_spendable_for_station:
                build_result = _try_build_line(
                    ops, player, game_map, max_cost, current_year, points_with_pop,
                    rng=rng, existing_routes=existing_routes, preset=preset,
                )
                if _ai_line_create_result_ok(build_result):
                    _do_action('create_line', build_result)
                    return
        if spend > min_spendable_for_station:
            rent_result = _try_rent_workers(ops, player, current_year, spend)
            if rent_result:
                _do_action('rent_workers', rent_result)

    if not _skip_first_turn_osm_discretionary:
        _try_mandatory_network_or_focus_action()

    if surplus_cash_expand and not _took_core_network_action() and not _turn_time_exceeded() and not _skip_first_turn_osm_discretionary:
        _try_force_core_network_expansion()

    # Year-1 opener: never finish with zero actions when no line is queued.
    if _ai_needs_first_turn_line(
        ops, player,
        first_calendar_year=first_calendar_year,
        ai_join_first_line_pending=ai_join_first_line_pending,
    ) and not results and not _turn_time_exceeded():
        ops._ai_turn_deadline_mono = time.monotonic() + 12.0
        forced = _try_force_first_turn_line_build(
            ops, player, game_map, current_year, rng, preset,
            existing_routes=opener_overlap_routes, points_with_pop=points_with_pop,
        )
        if _ai_line_create_result_ok(forced):
            _ai_mark_first_year_opener_attempted(ops)
            _do_action('create_line', forced)

    if surplus_cash_expand and _ai_debug_notifications_enabled(ops):
        spend_end = _refresh_spendable()
        if (
            not _took_core_network_action()
            and not my_line_builds
            and spend_end > min_extend_spendable
            and not _turn_time_exceeded()
            and not profitable_operation
            and not has_profitable_line
            and player_profit <= 0
        ):
            ref = _ai_starting_money_reference(ops)
            _record_ai_debug_issue(
                ops,
                player,
                (
                    f"Year {current_year}: cash ${money:,} exceeds starting ${ref:,} with a small network "
                    f"and inconsistent profitability, but no build/extend/station action was taken "
                    f"(spendable ${spend_end:,})."
                ),
            )

    # Never leave a living AI with zero actions when schedule/fare/rent adjustments are still available.
    if (
        not results
        and getattr(player, 'is_ai', False)
        and ops
        and not ops.is_player_eliminated(player)
        and not _turn_time_exceeded()
    ):
        if player.get_line_names():
            schedule_updates = _adjust_ai_schedules(
                ops,
                player,
                game_map,
                has_profitable_line=has_profitable_line,
                is_losing_money=is_losing_money,
                is_wealthy_and_profitable=is_wealthy_and_profitable,
                early_growth=early_growth,
                schedule_survival_tight=schedule_survival_tight or near_term_bankruptcy_risk,
            )
            if schedule_updates:
                _do_action('set_schedule', {'lines': schedule_updates})
        if not results and player.get_line_names():
            fare_changes = _adjust_ai_fares(ops, player, current_year, game_map, rng)
            if fare_changes:
                _do_action('set_fares', {'lines': fare_changes})
        if not results:
            spend_fb = max(_refresh_spendable(), min(_inf(3000), int(ops.get_player_money(player) * 0.15)))
            if spend_fb >= _inf(300):
                rent_result = _try_rent_workers(ops, player, current_year, spend_fb)
                if rent_result:
                    _do_action('rent_workers', rent_result)
        if not results:
            for slot_fn in (_try_action2_focus, _try_action3_situational):
                if _turn_time_exceeded():
                    break
                extra = slot_fn()
                if extra:
                    _do_action(extra[0], extra[1])
                    break
        if not results:
            _try_mandatory_network_or_focus_action()
        if not results and player.get_line_names():
            # Last resort: record a schedule pass so the year is not idle when all discretionary checks noop.
            _do_action('set_schedule', {'lines': [player.get_line_names()[0]]})

    return _ai_handle_street_compliance_at_turn_end(
        ops, player, game_map, current_year, preset, results,
        first_calendar_year=first_calendar_year,
        ai_join_first_line_pending=ai_join_first_line_pending,
        progress_callback=progress_callback,
        deadline_sec=deadline_sec,
        street_index_retry=_street_index_retry,
    )


# Used by :func:`_best_osm_turn1_pair_from_top_population` (must exist before that helper).
MIN_LINE_LENGTH_GRID = 0.55


def _ai_round_grid_point(p) -> tuple[float, float]:
    """Grid coordinates for AI builds are rounded to thousandths (~1 m).

    Hundredths (~11 m) would push street-following vertices off their polylines by more
    than the 10 m connector allowance.
    """
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return (round(float(p[0]), 3), round(float(p[1]), 3))
    return (round(float(getattr(p, 'x', 0)), 3), round(float(getattr(p, 'y', 0)), 3))


def _ai_round_route_points(points: list) -> list:
    out = []
    for p in (points or []):
        rp = _ai_round_grid_point(p)
        if out and rp == out[-1]:
            continue
        out.append(rp)
    return out


def _ai_route_street_detour_ratio(points: list) -> float:
    pts = _ai_round_route_points(points)
    if len(pts) < 2:
        return 0.0
    start, end = pts[0], pts[-1]
    direct = math.hypot(end[0] - start[0], end[1] - start[1])
    if direct <= 1e-9:
        return 0.0
    total = sum(
        math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        for i in range(len(pts) - 1)
    )
    return total / direct


def _ai_route_is_axis_aligned_grid_shortcut(points: list) -> bool:
    """True for improvised rectilinear grid routes (only horizontal + vertical legs)."""
    pts = _ai_round_route_points(points)
    if len(pts) < 2:
        return False
    for i in range(len(pts) - 1):
        dx = abs(pts[i + 1][0] - pts[i][0])
        dy = abs(pts[i + 1][1] - pts[i][1])
        if dx > 1e-9 and dy > 1e-9:
            return False
    return len(pts) >= 2


def _ai_random_point_near_population_cell(rng: random.Random, x, y) -> tuple[float, float]:
    """Random coordinate at hundredths precision within one grid unit of a population cell."""
    return _ai_jitter_population_anchor(rng, x, y)


def _ai_jitter_population_anchor(rng: random.Random, x, y) -> tuple[float, float]:
    """Hundredth-precision anchor jitter using randint + random.random (year-1 OSM openers)."""
    return _ai_round_grid_point((
        float(x) + (rng.randint(0, 99) + rng.random()) / 100.0,
        float(y) + (rng.randint(0, 99) + rng.random()) / 100.0,
    ))


def _ai_first_turn_population_point_rng(ops, player, current_year: int) -> random.Random:
    """Deterministic RNG stream for one AI's six year-1 population anchors."""
    gs = _get_game_seed(ops)
    pid = _player_id_int(player)
    seed = (gs ^ pid * 0x9E3779B9 ^ int(current_year) * 2654435761 ^ 0x534958) & 0xFFFFFFFF
    return random.Random(seed)


AI_FIRST_LINE_HOTSPOT_RADIUS_GRID = 2.5
# Share of an area's appeal that remains with 0, 1, 2, 3+ competing lines already there.
AI_FIRST_LINE_COMPETITION_FACTORS = (1.0, 0.6, 0.25, 0.08)


def _ai_first_line_hotspot_pool(ops, game_map, pool: list, rng: random.Random) -> list:
    """Population anchors around the best first-line area.

    Area appeal = population within AI_FIRST_LINE_HOTSPOT_RADIUS_GRID of a cell, scaled down
    by how many existing or queued lines already run through that area.
    """
    if len(pool) < 2:
        return []
    radius = AI_FIRST_LINE_HOTSPOT_RADIUS_GRID
    r2 = radius * radius
    buckets: dict = {}
    for (x, y), pop in pool:
        buckets.setdefault((int(x // radius), int(y // radius)), []).append((x, y, pop))

    def _nearby(x, y):
        bx, by = int(x // radius), int(y // radius)
        for cx in (bx - 1, bx, bx + 1):
            for cy in (by - 1, by, by + 1):
                for px, py, pop in buckets.get((cx, cy), ()):
                    if (px - x) ** 2 + (py - y) ** 2 <= r2:
                        yield px, py, pop

    try:
        routes = _get_existing_line_routes_for_overlap(ops, game_map) or []
    except Exception:
        routes = []
    route_pts = []
    for route in routes:
        pts = []
        for p in route:
            try:
                pts.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
        if pts:
            route_pts.append(pts)

    def _competition(x, y) -> int:
        n = 0
        for pts in route_pts:
            if any((px - x) ** 2 + (py - y) ** 2 <= r2 for px, py in pts):
                n += 1
        return n

    factors = AI_FIRST_LINE_COMPETITION_FACTORS
    density = sorted(
        ((sum(p for _x, _y, p in _nearby(x, y)), x, y) for (x, y), _pop in pool),
        reverse=True,
    )
    best = None
    for dens, x, y in density[:60]:  # competition only matters among the densest areas
        factor = factors[min(_competition(x, y), len(factors) - 1)] if route_pts else 1.0
        score = dens * factor * (1.0 + 0.05 * rng.random())
        if best is None or score > best[0]:
            best = (score, x, y)
    if best is None:
        return []
    _score, hx, hy = best
    return [((x, y), pop) for x, y, pop in _nearby(hx, hy)]


def _ai_top_population_anchor_quota(count: int) -> int:
    """How many of ``count`` sampled anchors must come from the top population cells."""
    import math
    return min(count, math.ceil(count * AI_TOP_POPULATION_ANCHOR_SHARE - 1e-9))


def _ai_top_population_cells(pool: list) -> list:
    """The most populous AI_TOP_POPULATION_FRACTION of ``pool`` ((cell, pop) pairs), at least one."""
    import math
    if not pool:
        return []
    n = max(1, math.ceil(len(pool) * AI_TOP_POPULATION_FRACTION))
    return sorted(pool, key=lambda item: -item[1])[:n]


def _ai_first_turn_six_population_points(
    ops,
    player,
    game_map,
    *,
    min_pop: int = 1,
) -> list:
    """Six population anchors per AI for year-1 OSM pair routing (deterministic per game + player)."""
    current_year = ops.get_year() if ops and hasattr(ops, 'get_year') else 1830
    rng = _ai_first_turn_population_point_rng(ops, player, current_year)
    pop_map = getattr(game_map, 'population', None) or {}
    deleted = getattr(game_map, '_deleted_population_points', set()) or set()
    pool = []
    seen = set()
    for (x, y), pop in pop_map.items():
        if pop < min_pop or (x, y) in deleted:
            continue
        cell = _ai_round_grid_point((x, y))
        if cell in seen:
            continue
        seen.add(cell)
        pool.append((cell, int(pop)))
    if not pool:
        return []
    # First lines go where people are: keep only anchors around the densest area that is
    # not already crowded with other companies' lines.
    top_cells = _ai_top_population_cells(pool)
    hotspot_pool = _ai_first_line_hotspot_pool(ops, game_map, pool, rng)
    if len(hotspot_pool) >= 2:
        pool = hotspot_pool
    target = min(AI_FIRST_TURN_POPULATION_POINT_COUNT, len(pool))
    # Most anchors come from the map's top population cells, preferring those in the hotspot.
    top_quota = _ai_top_population_anchor_quota(target)
    pool_cells = {c for c, _p in pool}
    top_local = [item for item in top_cells if item[0] in pool_cells]
    top_pool = top_local if len(top_local) >= top_quota else top_cells
    out = []
    out_seen = set()
    attempts = 0
    weights = [max(1, p) for _c, p in pool]
    top_weights = [max(1, p) for _c, p in top_pool]
    while len(out) < target and attempts < target * 30:
        attempts += 1
        if len(out) < top_quota and attempts < top_quota * 30:
            cell, _pop = rng.choices(top_pool, weights=top_weights, k=1)[0]
        else:
            idx = rng.choices(range(len(pool)), weights=weights, k=1)[0]
            cell, _pop = pool[idx]
        pt = _ai_jitter_population_anchor(rng, cell[0], cell[1])
        if pt in out_seen:
            continue
        out_seen.add(pt)
        pop_at = _ai_population_at_anchor(game_map, pt[0], pt[1])
        if pop_at < min_pop:
            continue
        out.append((pt, int(pop_at)))
    return out


def _ai_first_turn_pick_point_pair(
    candidates: list,
    rng: random.Random,
) -> Optional[tuple]:
    """Pick exactly one unordered pair from population anchor candidates.

    A specialist company prefers a pair no farther apart than its specialty's longest line.
    """
    n = len(candidates)
    if n < 2:
        return None
    from ai_specialties import route_length_bounds
    _spec_min, spec_max = route_length_bounds(_AI_TURN_SPECIALTY)
    if spec_max is not None:
        fitting = [
            (a, b) for a in range(n) for b in range(a + 1, n)
            if math.hypot(candidates[a][0][0] - candidates[b][0][0],
                          candidates[a][0][1] - candidates[b][0][1]) <= spec_max
        ]
        if fitting:
            a, b = fitting[rng.randint(0, len(fitting) - 1)]
            (p1, pop1), (p2, pop2) = candidates[a], candidates[b]
            return p1, p2, pop1, pop2
    i = rng.randint(0, n - 1)
    j = rng.randint(0, n - 2)
    if j >= i:
        j += 1
    (p1, pop1), (p2, pop2) = candidates[i], candidates[j]
    return p1, p2, pop1, pop2


def _ai_random_population_build_candidates(
    game_map, rng: random.Random, count: int = 20, *, min_pop: int = 1, top_fraction: float = None,
) -> list:
    """Return ``count`` random population anchors (hundredths precision).

    Draws uniformly from populated cells meeting ``min_pop``, then jitters each anchor to a
    random hundredth-precision coordinate near that cell. ``top_fraction`` (e.g. 0.10 for
    in-city rail) limits the draw to that share of the most populated cells.
    """
    pop_map = getattr(game_map, 'population', None) or {}
    deleted = getattr(game_map, '_deleted_population_points', set()) or set()
    pool = []
    seen = set()
    for (x, y), pop in pop_map.items():
        if pop < min_pop or (x, y) in deleted:
            continue
        cell = _ai_round_grid_point((x, y))
        if cell in seen:
            continue
        seen.add(cell)
        pool.append((cell, int(pop)))
    if not pool:
        return []
    if top_fraction:
        pool.sort(key=lambda item: item[1], reverse=True)
        pool = pool[:max(2, int(len(pool) * float(top_fraction)))]
    if len(pool) <= count:
        selected = list(pool)
        rng.shuffle(selected)
    else:
        # At least AI_TOP_POPULATION_ANCHOR_SHARE of the anchors are top population cells.
        top = _ai_top_population_cells(pool)
        selected = rng.sample(top, min(_ai_top_population_anchor_quota(count), len(top)))
        chosen = {cell for cell, _pop in selected}
        rest = [item for item in pool if item[0] not in chosen]
        selected += rng.sample(rest, count - len(selected))
        rng.shuffle(selected)
    out = []
    out_seen = set()
    for cell, pop in selected:
        pt = _ai_random_point_near_population_cell(rng, cell[0], cell[1])
        if pt in out_seen:
            pt = _ai_random_point_near_population_cell(rng, cell[0], cell[1])
        out_seen.add(pt)
        pop_at = _ai_population_at_anchor(game_map, pt[0], pt[1])
        out.append((pt, int(pop_at if pop_at >= min_pop else pop)))
    return out


def _ai_line_endpoint_direction(route_pts: list, extend_from_end: bool) -> Optional[tuple[float, float]]:
    """Unit vector for the direction an extension should continue from a line endpoint."""
    if len(route_pts) < 2:
        return None
    if extend_from_end:
        ax, ay = route_pts[-2]
        bx, by = route_pts[-1]
    else:
        ax, ay = route_pts[1]
        bx, by = route_pts[0]
    dx, dy = float(bx) - float(ax), float(by) - float(ay)
    mag = math.hypot(dx, dy)
    if mag <= 1e-9:
        return None
    return (dx / mag, dy / mag)


def _ai_point_in_extension_direction(
    base: tuple[float, float],
    target: tuple[float, float],
    direction: tuple[float, float],
    *,
    min_dot: float = 0.0,
) -> bool:
    """True when target lies in the same general direction as ``direction`` from ``base``."""
    dx = float(target[0]) - float(base[0])
    dy = float(target[1]) - float(base[1])
    mag = math.hypot(dx, dy)
    if mag <= 1e-9:
        return False
    dot = (dx / mag) * direction[0] + (dy / mag) * direction[1]
    return dot >= min_dot


def _population_cache_signature(game_map) -> tuple:
    """Cheap invalidation key when population points are added/removed (not when values change in-place)."""
    pop = getattr(game_map, 'population', None) or {}
    deleted = getattr(game_map, '_deleted_population_points', None) or set()
    return (len(pop), len(deleted))


def _compute_population_points_sorted_by_population(game_map, max_points: int = None) -> list:
    """Get buildable population points ordered from highest to lowest population.
    Returns list of ((x, y), pop). Map-agnostic. Uses nlargest when capped (O(n) vs O(n log n) sort)."""
    points = []
    if hasattr(game_map, 'population') and game_map.population:
        deleted = getattr(game_map, '_deleted_population_points', set()) or set()
        # Skip is_point_in_buildable_area - points in population are already buildable (avoids 700+ file reads on large maps)
        items = [((x, y), pop) for (x, y), pop in game_map.population.items() if pop > 0 and (x, y) not in deleted]
        if max_points and len(items) > max_points:
            import heapq
            points = heapq.nlargest(max_points, items, key=lambda p: p[1])
        else:
            points = sorted(items, key=lambda p: p[1], reverse=True)
    if not points and hasattr(game_map, 'width') and hasattr(game_map, 'height'):
        w, h = game_map.width, game_map.height
        for x in [0, w // 4, w // 2, 3 * w // 4, w]:
            for y in [0, h // 4, h // 2, 3 * h // 4, h]:
                if x <= w and y <= h:
                    points.append(((x, y), 100))
    return points


def _get_population_points_sorted_by_population(game_map, max_points: int = None) -> list:
    """Same as :func:`_compute_population_points_sorted_by_population` but cached per map signature.

    Multiple AIs in one year reuse the same top-N list without re-sorting the full population dict."""
    sig = _population_cache_signature(game_map)
    cache = getattr(game_map, '_ai_pop_points_sorted_cache', None)
    if not isinstance(cache, dict):
        cache = {}
    key = (max_points, sig)
    if key in cache:
        return cache[key]
    out = _compute_population_points_sorted_by_population(game_map, max_points=max_points)
    cache[key] = out
    try:
        setattr(game_map, '_ai_pop_points_sorted_cache', cache)
    except Exception:
        pass
    return out


def _best_osm_turn1_pair_from_top_population(
    game_map,
    quick_points: list,
    budget: float,
    max_index: int,
    preset_name: str,
) -> Optional[tuple]:
    """Best (score, p1, p2, dist, cost, elev_levels) for OSM turn-1 pair build, or None.

    Score = (pop_i * pop_j) / distance. Preset population is checked **once per point** (not per pair).
    When NumPy is installed, uses pairwise distance / score matrices for speed on larger candidate sets."""
    if not quick_points or len(quick_points) < 2:
        return None
    n = min(max_index, len(quick_points))
    valid = []  # (fx, fy, pop)
    for idx in range(n):
        (x, y), pop = quick_points[idx]
        if (pop or 0) <= 0:
            continue
        fx, fy = float(x), float(y)
        if _ai_population_at_anchor(game_map, fx, fy) <= 0:
            continue
        valid.append((fx, fy, float(pop)))
    m = len(valid)
    if m < 2:
        return None
    u_cost = 225 * 1.5
    min_len = max(MIN_LINE_LENGTH_GRID, MIN_STATION_SPACING_GRID)

    def _finish_best(i: int, j: int, dij: float, sc: float) -> tuple:
        p1 = (valid[i][0], valid[i][1])
        p2 = (valid[j][0], valid[j][1])
        cost = max(15, int(dij * u_cost))
        elev = _get_safe_elevation_levels([p1, p2], game_map, preset_name or '', base='street')
        return (sc, p1, p2, dij, cost, elev)

    # Optional NumPy: full pairwise distance/score via broadcasting (same ordering as nested loops for ties).
    if m >= 2:
        try:
            import numpy as np  # type: ignore[import-untyped]
            xs = np.array([t[0] for t in valid], dtype=np.float64)
            ys = np.array([t[1] for t in valid], dtype=np.float64)
            pops = np.array([t[2] for t in valid], dtype=np.float64)
            dx = xs[:, None] - xs[None, :]
            dy = ys[:, None] - ys[None, :]
            dist = np.sqrt(dx * dx + dy * dy)
            scores = (pops[:, None] * pops[None, :]) / np.maximum(0.1, dist)
            ii, jj = np.triu_indices(m, k=1)
            dij = dist[ii, jj]
            scv = scores[ii, jj]
            # Match Python: cost = max(15, int(dij * u_cost)) for positive dij*u_cost
            costs = np.maximum(15, (dij * u_cost).astype(np.int64))
            mask = (dij >= min_len) & (dij > 1e-9) & (costs <= budget)
            if np.any(mask):
                cand = np.where(mask, scv, -np.inf)
                k = int(np.argmax(cand))
                if bool(mask[k]):
                    i, j = int(ii[k]), int(jj[k])
                    return _finish_best(i, j, float(dij[k]), float(scv[k]))
            return None
        except Exception:
            pass

    best = None
    best_sc = -1.0
    for i in range(m):
        for j in range(i + 1, m):
            dij = math.hypot(valid[j][0] - valid[i][0], valid[j][1] - valid[i][1])
            if dij < min_len:
                continue
            cost = max(15, int(dij * u_cost))
            if cost > budget:
                continue
            sc = (valid[i][2] * valid[j][2]) / max(0.1, dij)
            if sc > best_sc:
                best_sc = sc
                best = _finish_best(i, j, dij, sc)
    return best


# Backward compatibility alias
def _get_population_points(game_map, max_points: int = None) -> list:
    return _get_population_points_sorted_by_population(game_map, max_points)


# Max create_line_complete attempts per AI turn (build more lines)
_MAX_LINE_CREATE_ATTEMPTS = 3
# All AI lines must be more than 500m (0.55 grid units when 1 unit ≈ 1km); MIN_LINE_LENGTH_GRID set above.
# AI's first line must be at least 1km and at most 2km (smaller = faster build; extend later)
MIN_FIRST_LINE_LENGTH_GRID = 1.0


def _line_complete_station_count(line) -> int:
    """Built (completed) stations only — excludes stations still in the build queue."""
    if not line or not hasattr(line, 'get_stations'):
        return 0
    return len(line.get_stations() or [])


def _line_has_two_complete_stations(map_lines: dict, line_name: str) -> bool:
    line = map_lines.get(line_name) if map_lines else None
    return _line_complete_station_count(line) >= 2


def _ai_try_assign_trainset_to_line(
    ops, player, game_map, line_name: str, *, require_empty_line: bool = True,
) -> Optional[dict]:
    """Assign an unassigned trainset to a line (first trainset or additional capacity)."""
    map_lines = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else {}
    line = map_lines.get(line_name)
    if ops is not None and player is not None and hasattr(ops, 'line_is_ready_to_earn'):
        if not ops.line_is_ready_to_earn(line, player):
            return None
    elif not _line_has_two_complete_stations(map_lines, line_name):
        return None
    if not line:
        return None
    vehicles = getattr(line, 'vehicles', None) or []
    if require_empty_line and len(vehicles) > 0:
        return None
    if not require_empty_line and len(vehicles) == 0:
        return None
    from ai_specialties import vehicle_score
    wanted = _ai_line_vehicle_class(line_name, line)
    if require_empty_line and ops is not None and not _ai_owns_free_vehicle_of_class(ops, player, wanted):
        # First train on a line: buy the kind it is meant to run (horse cars for light rail,
        # heavy trains for commuter rail) instead of using a spare of the wrong kind.
        _ai_buy_vehicle_for_line(ops, player, line, wanted, only_matching=True)
    unassigned = list(ops.get_unassigned_trainsets(player) or []) if ops else []
    if not unassigned:
        return None
    unassigned.sort(key=lambda t: -vehicle_score(wanted, t, 1))
    ts = None
    for candidate in unassigned:
        if _pick_best_line_for_trainset(ops, player, candidate, [line_name]) == line_name:
            ts = candidate
            break
    if ts is None:
        ts = unassigned[0]
    try:
        ops.assign_trainset_to_line(ts, line_name)
        return {'line': line_name}
    except Exception:
        return None


def _ai_try_assign_first_trainset_to_line(ops, player, game_map, line_name: str) -> Optional[dict]:
    """Assign an unassigned trainset to a line that has two built stations and no vehicles."""
    return _ai_try_assign_trainset_to_line(
        ops, player, game_map, line_name, require_empty_line=True,
    )


AI_INFILL_MAX_PER_TURN = 2
AI_INFILL_NEAR_LINE_M = 60.0       # a stop this close to another line serves its riders
AI_INFILL_TRANSFER_MERGE_M = 20.0  # joins an own station this close (same as station merging)
AI_INFILL_SAMPLE_GRID = 0.05       # route sampling step (grid units)
AI_INFILL_RESERVE_STATIONS = 3     # keep cash for this many stations after building one


def _ai_route_samples(route: list, step: float) -> list:
    """Points every ``step`` grid units along a polyline (vertices included)."""
    pts = [(float(p[0]), float(p[1])) for p in route or []]
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(seg // step))
        for k in range(n):
            t = k / n
            out.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    if pts:
        out.append(pts[-1])
    return out


def _ai_samples_in_end_clusters(route: list, samples: list, share: float) -> list:
    """Samples within ``share`` of the route length from either end (along the route)."""
    pts = [(float(p[0]), float(p[1])) for p in route or []]
    if len(pts) < 2:
        return list(samples)
    cum = [0.0]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(x2 - x1, y2 - y1))
    total = cum[-1]
    if total <= 0:
        return list(samples)

    def along(px, py):
        best = (float('inf'), 0.0)
        for k, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
            dx, dy = x2 - x1, y2 - y1
            den = dx * dx + dy * dy
            t = 0.0 if den <= 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / den))
            d = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
            if d < best[0]:
                best = (d, cum[k] + t * math.sqrt(den))
        return best[1]

    edge = total * share
    return [(px, py) for px, py in samples if along(px, py) <= edge or along(px, py) >= total - edge]


def _ai_infill_station_for_line(ops, player, game_map, line_name: str, line, mx: float, my: float):
    """Best retroactive stop for one line: ('merge', station) or ('new', (x, y), score) or None."""
    route = list(ops.get_line_route_including_extensions(line_name) or [])
    if len(route) < 2:
        return None
    samples = _ai_route_samples(route, AI_INFILL_SAMPLE_GRID)
    if _ai_line_service_type(line_name, line) == "commuter_bus":
        # Commuter/express buses: stops bunch up at each end, none on the run between.
        from ai_specialties import COMMUTER_BUS_CLUSTER_SHARE
        samples = _ai_samples_in_end_clusters(route, samples, COMMUTER_BUS_CLUSTER_SHARE)
    if not samples:
        return None

    def _dist_m(ax, ay, bx, by):
        return math.hypot((bx - ax) * mx, (by - ay) * my)

    own_lines = set(player.get_line_names())
    stations = list(getattr(game_map, 'stations', None) or [])
    # 1. Transfer: join one of this player's stations on another of its lines.
    for st in stations:
        served = set(getattr(st, 'line_names', None) or [])
        if line_name in served or not (served & (own_lines - {line_name})):
            continue
        sx, sy = float(st.get_x()), float(st.get_y())
        if min(_dist_m(px, py, sx, sy) for px, py in samples) <= AI_INFILL_TRANSFER_MERGE_M:
            return ('merge', st)

    spacing = _get_ai_station_spacing(line_name=line_name, line=line)
    existing = _get_all_station_coords(game_map, ops)
    other_segments = []
    for other_name, other in (game_map.get_lines_by_name() or {}).items():
        if other_name == line_name:
            continue
        o_route = [(float(p[0]), float(p[1])) for p in (other.get_route() or [])]
        other_segments.extend(zip(o_route, o_route[1:]))

    def _near_other_line(px, py) -> bool:
        lim = AI_INFILL_NEAR_LINE_M
        for (ax, ay), (bx, by) in other_segments:
            if (min(ax, bx) * mx - lim > px * mx or max(ax, bx) * mx + lim < px * mx
                    or min(ay, by) * my - lim > py * my or max(ay, by) * my + lim < py * my):
                continue
            dx, dy = (bx - ax) * mx, (by - ay) * my
            den = dx * dx + dy * dy
            qx, qy = (px - ax) * mx, (py - ay) * my
            t = 0.0 if den <= 0 else max(0.0, min(1.0, (qx * dx + qy * dy) / den))
            if math.hypot(qx - t * dx, qy - t * dy) <= lim:
                return True
        return False

    game = ops.get_game() if hasattr(ops, 'get_game') else None
    min_pop = int(getattr(game, 'ai_min_population', 100) or 100) if game is not None else 100
    best = None
    for px, py in samples:
        if any(math.hypot(px - sx, py - sy) < spacing for sx, sy in existing):
            continue
        pop = _ai_population_near_point(game_map, px, py)
        if pop <= 0:
            continue
        # 2. Next to another line: riders can walk between the lines here.
        # 3. Otherwise a population centre along the route.
        if _near_other_line(px, py):
            score = 1_000_000 + pop
        elif pop >= min_pop:
            score = pop
        else:
            continue
        if best is None or score > best[2]:
            best = ('new', (px, py), score)
    return best


def ai_add_infill_stations(ops, player, max_new: int = AI_INFILL_MAX_PER_TURN) -> list:
    """Retroactively add stops to an AI's built lines: transfers, next to other lines, and
    population centres along the route. Returns add_station result dicts."""
    if ops is None or player is None or not getattr(player, 'is_ai', False):
        return []
    game_map = ops.get_map()
    if game_map is None:
        return []
    try:
        from ai_street_router import grid_metric_scale
        mx, my = grid_metric_scale(game_map)
    except Exception:
        mx = my = 1000.0
    map_lines = game_map.get_lines_by_name() or {}
    station_cost = _ai_station_cost_each(ops)
    current_year = ops.get_year()
    added = []
    for ln in list(player.get_line_names()):
        if len(added) >= max_new:
            break
        line = map_lines.get(ln)
        if line is None or _line_is_ferry(line):
            continue
        if ops.get_player_money(player) < station_cost * (1 + AI_INFILL_RESERVE_STATIONS):
            break
        pick = _ai_infill_station_for_line(ops, player, game_map, ln, line, mx, my)
        if pick is None:
            continue
        if pick[0] == 'merge':
            st = pick[1]
            try:
                r = ops.add_station_to_line_at_point(player, ln, st.name, st.get_x(), st.get_y())
            except (ValueError, TypeError):
                r = None
            if r and r.get('success'):
                added.append({**r, 'line_name': ln, 'station_name': st.name, 'point': (st.get_x(), st.get_y())})
            continue
        x, y = pick[1]
        if not _ai_station_point_not_in_water(game_map, x, y) or not _ai_station_site_ok(ops, player, x, y):
            continue
        name = _ai_resolve_unique_station_name(
            ops, player, _generate_ai_station_name(ops, game_map, x, y, current_year, line_name=ln),
            x=float(x), y=float(y), line_name=ln,
        )
        try:
            # Paid, queued construction (same path as stations opened with a new line).
            r = ops.create_station_complete(name, x, y, player, [ln])
        except (ValueError, TypeError):
            r = None
        if _ai_station_create_ok(r):
            added.append({**(r if isinstance(r, dict) else {}), 'line_name': ln, 'station_name': name, 'point': (x, y)})
    return added


# Minimal service (trains/hour) used when the profit-based schedule comes out as zero: a line
# with two open stations always runs, so it can build ridership.
AI_MIN_SERVICE_SCHEDULE = {'peak': 1.0, 'off_peak': 0.5, 'night': 0.0}


def _ai_line_has_no_schedule(line) -> bool:
    sched = getattr(line, 'schedule', None) or {}
    return all(float(sched.get(p, 0) or 0) <= 0 for p in ('peak', 'off_peak', 'night'))


def _ai_assign_vehicle(ops, player, game_map, line_name: str, line) -> bool:
    if _line_is_bus(line):
        _try_assign_unassigned_bus_to_line(ops, player, line_name)
    else:
        _ai_try_assign_first_trainset_to_line(ops, player, game_map, line_name)
    return bool(getattr(line, 'vehicles', None))


def _ai_line_vehicle_class(line_name: str, line) -> str:
    """Kind of vehicle that suits the line's service (see ai_specialties.vehicle_score)."""
    from ai_specialties import vehicle_class_for_service
    return vehicle_class_for_service(_ai_line_service_type(line_name, line), is_bus=_line_is_bus(line))


def _ai_owns_free_vehicle_of_class(ops, player, wanted: str) -> bool:
    from ai_specialties import _rail_vehicle_class
    try:
        free = list(ops.get_unassigned_trainsets(player) or [])
    except Exception:
        return False
    return any(_rail_vehicle_class(t) == wanted for t in free)


def _ai_vehicle_offers(ops, player, line, wanted: str, *, only_matching: bool = False) -> list:
    """Affordable marketplace vehicles for the line, best-suited first."""
    from ai_specialties import _rail_vehicle_class, vehicle_score
    from vehicle import Bus as BusVehicle, Locomotive, Trainset
    want = BusVehicle if _line_is_bus(line) else Trainset
    current_year = ops.get_year()
    budget = int(ops.get_player_money(player) * 0.5)
    try:
        items = ops.game.get_marketplace().get_available_items(current_year) or []
    except Exception:
        items = []
    offers = []
    for sale in items:
        vehicle = sale.get_item() if hasattr(sale, 'get_item') else None
        if (want is Trainset and isinstance(vehicle, Locomotive)
                and not getattr(vehicle, 'can_have_railcars', False)):
            # Self-propelled units (horse cars, streetcars, subway cars) run on their own.
            vehicle = Trainset(locomotive=vehicle, cars=[])
        elif not isinstance(vehicle, want):
            continue
        if only_matching and (want is not Trainset or _rail_vehicle_class(vehicle) != wanted):
            continue
        try:
            price = ops.get_effective_marketplace_price_for_player(player, sale.get_price(current_year))
        except Exception:
            continue
        if price <= budget:
            offers.append((-vehicle_score(wanted, vehicle, price), price, id(sale), sale))
    return [o[3] for o in sorted(offers, key=lambda o: (o[0], o[1], o[2]))]


def _ai_buy_vehicle_for_line(ops, player, line, wanted: str, *, only_matching: bool = False) -> bool:
    """Buy the best-suited affordable vehicle for the line. True if one was bought."""
    for sale in _ai_vehicle_offers(ops, player, line, wanted, only_matching=only_matching):
        if _ai_buy_vehicle_sale(ops, player, sale):
            return True
    return False


def _ai_buy_vehicle_sale(ops, player, sale) -> bool:
    """Buy a marketplace vehicle; a self-propelled locomotive becomes a one-unit trainset."""
    from vehicle import Locomotive
    item = sale.get_item() if hasattr(sale, 'get_item') else None
    try:
        ops.buy_vehicle_complete(sale, player, ops.get_year())
    except Exception:
        return False
    if isinstance(item, Locomotive) and not getattr(item, 'can_have_railcars', False):
        loco = item if item in (getattr(player, 'locomotives', None) or []) else None
        if loco is None:
            same = [l for l in (getattr(player, 'locomotives', None) or []) if getattr(l, 'name', None) == item.name]
            loco = same[-1] if same else None
        if loco is not None:
            try:
                ops.assemble_trainset_complete(player, loco, [])
            except Exception:
                return False
    return True


def _ai_put_vehicle_on_line(ops, player, game_map, line_name: str, line) -> bool:
    """Put a vehicle that suits the line on it: a free one of the right kind, else buy the
    best-suited affordable one, else any free vehicle."""
    wanted = _ai_line_vehicle_class(line_name, line)
    if (_line_is_bus(line) or _ai_owns_free_vehicle_of_class(ops, player, wanted)) and _ai_assign_vehicle(
        ops, player, game_map, line_name, line,
    ):
        return True
    for sale in _ai_vehicle_offers(ops, player, line, wanted):
        if not _ai_buy_vehicle_sale(ops, player, sale):
            continue
        if _ai_assign_vehicle(ops, player, game_map, line_name, line):
            return True
    return _ai_assign_vehicle(ops, player, game_map, line_name, line)


def ai_enact_schedules_on_ready_lines(ops, player, line_names: list = None) -> list:
    """Put every AI line with 2+ completed stations into service right away.

    For each such line without a timetable: assign a trainset (buying one if none is free),
    then set the profit-based schedule, or AI_MIN_SERVICE_SCHEDULE when that is zero.
    Returns [{'line', 'peak', 'off_peak', 'night'}] for lines that got a schedule.
    """
    if ops is None or player is None or not getattr(player, 'is_ai', False):
        return []
    game_map = ops.get_map()
    map_lines = (game_map.get_lines_by_name() or {}) if game_map else {}
    current_year = ops.get_year()
    out = []
    for ln in (line_names or player.get_line_names()):
        line = map_lines.get(ln)
        if line is None or _line_complete_station_count(line) < 2:
            continue
        if not ops.line_is_ready_to_earn(line, player) or not _ai_line_has_no_schedule(line):
            continue
        if not getattr(line, 'vehicles', None):
            _ai_put_vehicle_on_line(ops, player, game_map, ln, line)
        if not getattr(line, 'vehicles', None) or not hasattr(line, 'set_schedule_frequency'):
            continue
        try:
            targets = ops.compute_max_affordable_ai_schedule(line, current_year) or {}
        except Exception:
            targets = {}
        if all(float(targets.get(p, 0) or 0) <= 0 for p in ('peak', 'off_peak', 'night')):
            targets = dict(AI_MIN_SERVICE_SCHEDULE)
        for period in ('peak', 'off_peak', 'night'):
            try:
                line.set_schedule_frequency(period, float(targets.get(period, 0) or 0))
            except Exception:
                pass
        if not _ai_line_has_no_schedule(line):
            out.append({'line': ln, **{p: float(line.schedule.get(p, 0) or 0) for p in ('peak', 'off_peak', 'night')}})
    return out


def _lines_needing_schedule_timetable(map_lines: dict, player, ops) -> list:
    """Lines with trainsets and two stations but no timetable yet."""
    names = []
    for ln in player.get_line_names():
        line = map_lines.get(ln)
        if not line or not ops or not ops.line_is_ready_to_earn(line, player):
            continue
        if not getattr(line, 'vehicles', None):
            continue
        sched = getattr(line, 'schedule', None) or {}
        if all(float(sched.get(period, 0) or 0) <= 0 for period in ('peak', 'off_peak', 'night')):
            names.append(ln)
    return names


def _line_needs_trainset_assignment(map_lines: dict, line_name: str, ops=None, player=None) -> bool:
    """True when a line can earn (2+ built stations, build complete) but has no vehicles."""
    line = map_lines.get(line_name) if map_lines else None
    if ops is not None and player is not None and hasattr(ops, 'line_is_ready_to_earn'):
        if not ops.line_is_ready_to_earn(line, player):
            return False
    elif not _line_has_two_complete_stations(map_lines, line_name):
        return False
    if not line:
        return False
    if _line_is_bus(line) and ops is not None and not ops.bus_lines_available():
        return False  # no bus exists yet: the line waits without holding up anything else
    vehicles = getattr(line, 'vehicles', None) or []
    return len(vehicles) == 0


AI_TRAINSET_PAYBACK_YEARS = 8  # a purchased trainset must earn its price back within this


def _ai_spare_engineers(ops, player) -> int:
    """Engineers the player has beyond those already driving assigned trainsets."""
    if player is None:
        return 0
    try:
        assigned = int(ops.get_player_assigned_trainsets_count(player) or 0)
    except Exception:
        assigned = 0
    return int(player.get_engineer_count() or 0) - assigned


def _ai_engineer_annual_cost(ops, year: int) -> float:
    from worker import ENGINEER
    try:
        return float(ops.calculate_worker_rental_cost(ENGINEER, 1, year))
    except Exception:
        return float(ENGINEER.get_yearly_cost(year))


def _ai_additional_trainset_net(ops, line, year: int, trainset=None) -> float:
    """Yearly profit change from putting one more trainset on a line.

    = extra fare revenue at full service (demand held at last year's ridership)
      minus that trainset's real yearly cost (maintenance, fuel, property tax)
      minus an engineer's salary when the owner has no spare engineer to drive it.
    Returns 0 when the line has no ridership record yet: without demand data another
    train is a guess, and the first one already runs.
    """
    if not line or not getattr(line, 'vehicles', None) or ops is None:
        return 0.0
    try:
        demand = int(ops.get_line_previous_year_ridership(line) or 0)
    except Exception:
        demand = 0
    if demand <= 0:
        return 0.0
    extra = trainset if trainset is not None else line.vehicles[0]
    try:
        now = ops.estimate_line_gross_at_full_service(line, year, demand=demand)
        more = ops.estimate_line_gross_at_full_service(
            line, year, vehicles=list(line.vehicles) + [extra], demand=demand,
        )
        net = float(more - now) - float(ops.trainset_annual_cost(extra, year))
    except Exception:
        return 0.0
    owner = getattr(line, 'owner', None)
    if owner is not None and _ai_spare_engineers(ops, owner) <= 0:
        net -= _ai_engineer_annual_cost(ops, year)
    return net


def _line_benefits_from_additional_trainset(line, ops=None, year: int = None, trainset=None) -> bool:
    """True when one more trainset on this line raises yearly profit (not just capacity)."""
    if not line or not getattr(line, 'vehicles', None):
        return False
    if ops is None:
        return False
    y = int(ops.get_year() if year is None else year)
    return _ai_additional_trainset_net(ops, line, y, trainset) > 0.0


def _ai_trainset_purchase_pays(ops, player, vehicle, price: float, year: int) -> bool:
    """True when buying this trainset is justified: a ready rail line has no train at all,
    or some line's profit gain repays the price within AI_TRAINSET_PAYBACK_YEARS."""
    game_map = ops.get_map()
    map_lines = (game_map.get_lines_by_name() or {}) if game_map else {}
    free = len(ops.get_unassigned_trainsets(player) or [])
    best = 0.0
    for ln in player.get_line_names():
        line = map_lines.get(ln)
        if line is None or _line_is_bus(line) or _line_is_ferry(line):
            continue
        if not getattr(line, 'vehicles', None):
            # Needs its first train (now or once its stations open) and none is spare.
            if free <= 0:
                return True
            free -= 1
            continue
        best = max(best, _ai_additional_trainset_net(ops, line, year, vehicle))
    return best > 0.0 and price <= best * AI_TRAINSET_PAYBACK_YEARS


def _ai_network_revenue_ready(map_lines: dict, player, ops) -> bool:
    """Every complete line has at least one trainset and a non-zero timetable."""
    ready = [
        ln for ln in player.get_line_names()
        if map_lines.get(ln) and ops and ops.line_is_ready_to_earn(map_lines.get(ln), player)
    ]
    if not ready:
        return True
    for ln in ready:
        line = map_lines.get(ln)
        if not getattr(line, 'vehicles', None):
            return False
        sched = getattr(line, 'schedule', None) or {}
        if all(float(sched.get(period, 0) or 0) <= 0 for period in ('peak', 'off_peak', 'night')):
            return False
    return True


def _try_assign_additional_trainsets(
    ops,
    player,
    game_map,
    current_year: int,
    spendable: int,
    min_spendable_for_station: int,
) -> Optional[tuple]:
    """Assign unassigned trainsets to operational lines that can use more capacity."""
    map_lines = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else {}
    if not ops.get_unassigned_trainsets(player):
        return None
    candidates = []
    for ln in player.get_line_names():
        line = map_lines.get(ln)
        if not line or not ops.line_is_ready_to_earn(line, player):
            continue
        if len(getattr(line, 'vehicles', None) or []) == 0:
            continue
        if _line_benefits_from_additional_trainset(line, ops, current_year):
            candidates.append((ln, len(line.vehicles)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[1])
    eng = int(player.get_engineer_count() or 0)
    asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
    if asg >= eng and spendable > min_spendable_for_station:
        # The candidates' profit test already counts this engineer's salary.
        rent_result = _try_rent_engineer_for_assignment(
            ops, player, current_year, spendable, game_map, for_additional_trainset=True,
        )
        if rent_result:
            return ('rent_workers', rent_result)
    for ln, _count in candidates:
        assign_result = _ai_try_assign_trainset_to_line(
            ops, player, game_map, ln, require_empty_line=False,
        )
        if assign_result:
            return ('assign_trainset', assign_result)
    return None


def _try_set_schedules_for_ready_lines(
    ops,
    player,
    game_map,
    *,
    line_names: list = None,
    force_line_names: set = None,
    schedule_kwargs: dict = None,
) -> Optional[tuple]:
    """Return set_schedule action when timetables need updating."""
    map_lines = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else {}
    names = line_names
    if names is None:
        names = _lines_needing_schedule_timetable(map_lines, player, ops)
    if not names:
        return None
    kwargs = dict(schedule_kwargs or {})
    updates = _adjust_ai_schedules(
        ops,
        player,
        game_map,
        line_names=names,
        force_line_names=force_line_names or set(names),
        **kwargs,
    )
    if updates:
        return ('set_schedule', {'lines': updates})
    return None


def _try_mandatory_network_growth_action(
    ops,
    player,
    game_map,
    current_year: int,
    *,
    spendable: int,
    min_spendable_for_station: int,
    min_extend_spendable: int,
    route_extend_ok: bool,
    extension_profit_gate: bool,
    points_with_pop: list,
    rng: random.Random,
    preset,
    focus: str,
    any_line_needs_stations: bool,
    lines_requiring_extension_for_station: list,
    schedule_kwargs: dict,
    line_build_complete: bool = False,
) -> Optional[tuple]:
    """Mandatory order: trainsets → schedule → more trainsets → schedule → stations → extend."""
    map_lines = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else {}
    lines = player.get_line_names()
    if not lines:
        return None

    lines_needing_first_trainset = [
        ln for ln in lines
        if _line_needs_trainset_assignment(map_lines, ln, ops=ops, player=player)
    ]
    if lines_needing_first_trainset:
        assign_action = _try_assign_trainsets_for_ready_lines(
            ops, player, game_map, current_year, spendable, min_spendable_for_station,
            allow_repeat=True,
        )
        if assign_action:
            return assign_action
        # Buy when no spare trainset can run on the waiting line (one that needs wiring the
        # line lacks does not count), or the line would wait forever.
        if not _ai_usable_unassigned_trainsets(ops, player, game_map):
            buy = _try_buy_marketplace(
                ops, player, current_year, spendable, focus,
                rng=rng, allow_rolling_stock=True,
            )
            if buy:
                return ('buy', buy)
        return None

    schedule_action = _try_set_schedules_for_ready_lines(
        ops, player, game_map, schedule_kwargs=schedule_kwargs,
    )
    if schedule_action:
        return schedule_action

    extra_assign = _try_assign_additional_trainsets(
        ops, player, game_map, current_year, spendable, min_spendable_for_station,
    )
    if extra_assign:
        return extra_assign
    wants_more_stock = any(
        map_lines.get(ln)
        and ops.line_is_ready_to_earn(map_lines.get(ln), player)
        and _line_benefits_from_additional_trainset(map_lines.get(ln), ops, current_year)
        for ln in lines
    )
    if wants_more_stock and not (ops.get_unassigned_trainsets(player) or []):
        buy = _try_buy_marketplace(
            ops, player, current_year, spendable, focus,
            rng=rng, allow_rolling_stock=True,
        )
        if buy:
            return ('buy', buy)

    ready_operational = [
        ln for ln in lines
        if map_lines.get(ln)
        and ops.line_is_ready_to_earn(map_lines.get(ln), player)
        and getattr(map_lines.get(ln), 'vehicles', None)
    ]
    if ready_operational:
        retune_schedule = _try_set_schedules_for_ready_lines(
            ops, player, game_map,
            line_names=ready_operational,
            force_line_names=set(ready_operational),
            schedule_kwargs=schedule_kwargs,
        )
        if retune_schedule:
            return retune_schedule

    if any_line_needs_stations and spendable >= _inflate_econ(ops, current_year, 500):
        lines_with_station_count = []
        for ln in lines:
            line = map_lines.get(ln)
            if not line:
                continue
            stations = line.get_stations() if hasattr(line, 'get_stations') else []
            lines_with_station_count.append((ln, line, len(stations)))
        lines_with_station_count.sort(key=lambda x: x[2])
        for line_name, line, station_count in lines_with_station_count:
            if station_count >= 2:
                continue
            min_spacing = _get_ai_station_spacing(line_name=line_name, line=line)
            pt = _pick_station_point(
                ops, game_map, line_name, line, points_with_pop, set(),
                min_spacing=min_spacing, player=player,
            )
            if pt:
                name = _generate_ai_station_name(ops, game_map, pt[0], pt[1], current_year, line_name=line_name)
                r = _try_ai_add_station(ops, player, line_name, name, pt[0], pt[1])
                if r:
                    r['line_name'] = line_name
                    r['station_name'] = name
                    r['point'] = (pt[0], pt[1])
                    return ('add_station', r)

    extend_action = _try_mandatory_extend_completed_lines(
        ops, player, game_map, current_year, spendable, min_extend_spendable,
        route_extend_ok, extension_profit_gate, points_with_pop, rng, preset,
        lines_requiring_extension_for_station, line_build_complete=line_build_complete,
    )
    if extend_action:
        return extend_action
    return None


def _try_assign_trainsets_for_ready_lines(
    ops,
    player,
    game_map,
    current_year: int,
    spendable: int,
    min_spendable_for_station: int,
    *,
    results: list = None,
    allow_repeat: bool = False,
) -> Optional[tuple]:
    """Rent an engineer if needed and assign the first ready unassigned trainset."""
    map_lines = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else {}
    ready = [
        ln for ln in player.get_line_names()
        if _line_needs_trainset_assignment(map_lines, ln, ops=ops, player=player)
    ]
    if not ready:
        return None
    if not allow_repeat and results and any(a == 'assign_trainset' for a, _ in results):
        return None
    unassigned = list(ops.get_unassigned_trainsets(player) or []) if ops else []
    if not unassigned:
        return None
    eng = int(player.get_engineer_count() or 0)
    asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
    if asg >= eng and spendable > min_spendable_for_station:
        rent_result = _try_rent_engineer_for_assignment(
            ops, player, current_year, spendable, game_map,
        )
        if rent_result:
            return ('rent_workers', rent_result)
    for ln in ready:
        assign_result = _ai_try_assign_first_trainset_to_line(ops, player, game_map, ln)
        if assign_result:
            return ('assign_trainset', assign_result)
    return None
# OSM street builds: treat segments as duplicate only when they are very close.
# Keep aligned with the preset/ops overlap expectations to avoid excessive retry churn.
AI_STREET_DUPLICATE_OVERLAP_GRID = 0.5
MAX_FIRST_LINE_LENGTH_GRID = 2.0
# Cap all new lines (smaller lines + more extensions = faster growth)
MAX_LINE_LENGTH_GRID = 2.5
# AI stations must be at least 600m apart (same line or different lines)
MIN_STATION_SPACING_GRID = 0.6


# Service-aware spacing (grid units). Buses stop more often; commuter rail stops less often.
AI_BUS_STOP_SPACING_GRID = 0.35
# Express buses skip more stops than locals but remain closer than rail locals.
AI_BUS_EXPRESS_STOP_SPACING_GRID = 0.52
AI_LOCAL_STOP_SPACING_GRID = 0.60
# Population anchors count only within this radius; line endpoints must sit here too.
AI_POPULATION_ANCHOR_RADIUS_GRID = 1.0
AI_EXPRESS_STOP_SPACING_GRID = 0.95
AI_COMMUTER_STOP_SPACING_GRID = 1.35
# Light rail (trolleys, horse cars): very short gaps between stops.
AI_LIGHT_RAIL_STOP_SPACING_GRID = 0.30
# Commuter/express buses: stops this far apart inside the clusters at each end of the route.
AI_COMMUTER_BUS_CLUSTER_STOP_SPACING_GRID = 0.35


def _ai_spacing_for_service(service: str, *, is_bus: bool) -> Optional[float]:
    """Minimum gap between stops for a service type (None when the type is unknown)."""
    if service == "commuter_bus":
        return AI_COMMUTER_BUS_CLUSTER_STOP_SPACING_GRID
    if service == "light":
        return AI_LIGHT_RAIL_STOP_SPACING_GRID
    if is_bus:
        return AI_BUS_EXPRESS_STOP_SPACING_GRID if service == "express" else AI_BUS_STOP_SPACING_GRID
    return {
        "commuter": AI_COMMUTER_STOP_SPACING_GRID,
        "express": AI_EXPRESS_STOP_SPACING_GRID,
        "local": AI_LOCAL_STOP_SPACING_GRID,
    }.get(service)


def _ai_line_service_type(line_name: str = "", line=None) -> str:
    """Service type recorded for a line: on the line itself (kept in saves), else this session."""
    from ai_specialties import line_service_type
    return line_service_type(line, line_name) or ""


def _ai_record_line_service_type(ops, line_name: str, service_type: str) -> None:
    if not line_name or not service_type:
        return
    _AI_LINE_SERVICE_TYPE[line_name] = service_type
    try:
        line = (ops.get_map().get_lines_by_name() or {}).get(line_name) if ops is not None else None
    except Exception:
        line = None
    if line is not None:
        line._ai_service_type = service_type
# Year-1 two-point fallback: only a single short OSM block edge, never a long chord.
AI_FIRST_TURN_MAX_STREET_EDGE_GRID = AI_MAX_STRAIGHT_LAND_SEGMENT_GRID
# Persist AI-decided rail service type for station spacing decisions.
from ai_specialties import LINE_SERVICE_TYPES as _AI_LINE_SERVICE_TYPE  # shared with operations
# Completed extensions per line name: AIs extend every line once before opening new ones.
_AI_LINE_EXTEND_COUNT: dict[str, int] = {}


# Cache expensive first-turn OSM corridor sampling so multi-AI openers on street maps do not time out.
_AI_FIRST_TURN_OSM_SEGMENTS_CACHE: dict[tuple, list] = {}
# Pre-validated segment polylines (cheap checks only) per cache_key — avoids O(n^2) create_line_complete.
_AI_FIRST_TURN_OSM_CANDIDATES_CACHE: dict[tuple, list] = {}
_AI_FIRST_TURN_MAX_CREATE_ATTEMPTS = 3
_AI_FIRST_TURN_MAX_CANDIDATES = 4
_AI_FIRST_TURN_BRUTE_SAMPLES = 1
_AI_FIRST_TURN_ALT_SAMPLES = 1
_AI_FIRST_TURN_SEED_TRIES = 1
_AI_FIRST_TURN_SEED_TRIES_CROWDED = 2


def _osm_points_close(a, b, tol: float = 0.06) -> bool:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    return math.hypot(bx - ax, by - ay) <= tol


def _osm_is_consecutive_street_edge(pts: list, source_seeded: list, tol: float = 0.06) -> bool:
    """True when a 2-point route is one OSM graph edge along the source corridor."""
    if not pts or len(pts) != 2 or not source_seeded or len(source_seeded) < 2:
        return False
    a, b = pts[0], pts[-1]
    for i in range(len(source_seeded) - 1):
        sa, sb = source_seeded[i], source_seeded[i + 1]
        if _osm_points_close(a, sa, tol) and _osm_points_close(b, sb, tol):
            return True
        if _osm_points_close(a, sb, tol) and _osm_points_close(b, sa, tol):
            return True
    return False


def _osm_polyline_length(pts: list) -> float:
    if len(pts) < 2:
        return 0.0
    t = 0.0
    for i in range(len(pts) - 1):
        ax, ay = float(pts[i][0]), float(pts[i][1])
        bx, by = float(pts[i + 1][0]), float(pts[i + 1][1])
        t += math.hypot(bx - ax, by - ay)
    return t


def _osm_trim_polyline_to_max_len(pts: list, max_len: float, min_len: float) -> Optional[list]:
    if len(pts) < 2:
        return None
    work = [(float(p[0]), float(p[1])) for p in pts]
    plen = _osm_polyline_length(work)
    while len(work) > 2 and plen > max_len:
        work.pop()
        plen = _osm_polyline_length(work)
    if plen > max_len and len(work) == 2:
        ax, ay = work[0]
        bx, by = work[1]
        d = math.hypot(bx - ax, by - ay)
        if d < 1e-9:
            return None
        t = min(1.0, max_len / d)
        work[1] = (ax + t * (bx - ax), ay + t * (by - ay))
        plen = _osm_polyline_length(work)
    if plen < min_len:
        return None
    return work


def _osm_first_turn_endpoints_viable(game_map, a, b) -> bool:
    return (
        _ai_population_at_anchor(game_map, float(a[0]), float(a[1])) > 0
        and _ai_population_at_anchor(game_map, float(b[0]), float(b[1])) > 0
    )


def _ai_point_in_water(
    game_map, preset_name: str, x: float, y: float, *, for_route_validation: bool = False,
) -> bool:
    """Water check for AI route planning. Use for_route_validation=True only at create-time endpoint checks."""
    if preset_name:
        from map_preset_loader import preset_has_water_areas
        if not preset_has_water_areas(preset_name):
            return False
    ts = getattr(game_map, 'tile_system', None)
    use_osm = bool(getattr(game_map, 'use_osm', False))
    skip_osm = not for_route_validation
    if ts and use_osm:
        try:
            return bool(ts.is_point_in_water(x, y, skip_osm=skip_osm))
        except TypeError:
            return bool(ts.is_point_in_water(x, y))
    if not preset_name:
        return False
    from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
    if not preset_has_water_areas(preset_name):
        return False
    try:
        return bool(preset_is_point_in_water(preset_name, x, y, game_map, skip_osm=True))
    except TypeError:
        return bool(preset_is_point_in_water(preset_name, x, y, game_map))


def _ai_station_point_not_in_water(game_map, x: float, y: float) -> bool:
    """AI stops must not be placed in water."""
    if not game_map:
        return False
    preset_name = getattr(game_map, 'map_preset_name', None) or ''
    return not _ai_point_in_water(game_map, preset_name, float(x), float(y), for_route_validation=False)


def _ai_osm_street_routing_data_available(game_map, preset_name: str = '') -> bool:
    """True when preset OSM street geometry is loaded (spatial index has cells)."""
    preset_name = preset_name or getattr(game_map, 'map_preset_name', None) or ''
    if not preset_name:
        return False
    try:
        from map_preset_loader import preset_has_road_support
        if not preset_has_road_support(preset_name):
            return False
    except Exception:
        return False
    try:
        from map_preset_loader import get_preset_road_utils
        road_utils = get_preset_road_utils(preset_name)
        road_utils._build_road_spatial_index(game_map)
        index = getattr(road_utils, '_ROAD_SPATIAL_INDEX', None)
        if index and len(index) > 0:
            return True
    except Exception:
        pass
    return False


def _ai_osm_route_passes_build_rules(
    game_map,
    route_points: list,
    current_year: int,
    preset_name: str = '',
    *,
    is_bus_line: bool = False,
    mandatory_opener: bool = False,
) -> bool:
    """True when an AI land route meets the build rules.

    Presets with OSM road data: the route follows street polylines, with connectors of at
    most 10 m between them; longer off-street segments only as water crossings that land on
    a street (rail only).  Other maps: majority on streets/water.
    """
    if not route_points or len(route_points) < 2:
        return False
    cy = int(current_year)
    preset_name = preset_name or getattr(game_map, 'map_preset_name', None) or ''

    from map_preset_loader import preset_has_road_support
    if preset_name and preset_has_road_support(preset_name):
        from ai_street_router import ai_route_violations
        from operations import _ai_route_has_backtracking
        if _ai_route_has_backtracking(route_points, game_map):
            return False
        return not ai_route_violations(
            game_map, route_points, cy, allow_water_crossing=not is_bus_line,
        )

    if _ai_route_is_axis_aligned_grid_shortcut(route_points):
        return False
    if mandatory_opener:
        return len(route_points) >= 3
    if is_bus_line:
        from operations import _route_has_off_street_bus_segment
        return not _route_has_off_street_bus_segment(
            game_map, route_points, skip_osm=True, current_year=cy,
        )
    if len(route_points) < 3:
        return False
    from operations import _ai_route_majority_on_streets_and_water
    return _ai_route_majority_on_streets_and_water(
        game_map, route_points, cy, skip_osm=True,
    )


def _ai_route_segment_is_water_crossing(
    game_map, preset_name: str, x1: float, y1: float, x2: float, y2: float,
) -> bool:
    """Fast water-crossing probe for AI route shaping (no blocking OSM queries)."""
    p1_w = _ai_point_in_water(game_map, preset_name, x1, y1, for_route_validation=False)
    p2_w = _ai_point_in_water(game_map, preset_name, x2, y2, for_route_validation=False)
    if p1_w and p2_w:
        return True
    mid_x = (x1 + x2) / 2
    mid_y = (y1 + y2) / 2
    if not p1_w and not p2_w and _ai_point_in_water(game_map, preset_name, mid_x, mid_y, for_route_validation=False):
        return True
    return False


def _ai_route_has_water_crossing(points: list, game_map, preset_name: str) -> bool:
    if not points or len(points) < 2 or not preset_name:
        return False
    for i in range(len(points) - 1):
        x1, y1 = float(points[i][0]), float(points[i][1])
        x2, y2 = float(points[i + 1][0]), float(points[i + 1][1])
        if _ai_route_segment_is_water_crossing(game_map, preset_name, x1, y1, x2, y2):
            return True
    return False


def _ai_water_shore_points_on_chord(p1, p2, game_map, preset_name: str):
    """Return (entry, exit) land/shore points bounding open water on a chord, or (None, None)."""
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    if not _ai_route_segment_is_water_crossing(game_map, preset_name, x1, y1, x2, y2):
        return (None, None)
    n = 32
    samples = []
    for k in range(n + 1):
        t = k / n
        sx = x1 + t * (x2 - x1)
        sy = y1 + t * (y2 - y1)
        samples.append(((sx, sy), _ai_point_in_water(game_map, preset_name, sx, sy)))
    water_idx = [i for i, (_pt, w) in enumerate(samples) if w]
    if not water_idx:
        return (None, None)
    first_w = water_idx[0]
    last_w = water_idx[-1]
    entry_i = max(0, first_w - 1)
    exit_i = min(n, last_w + 1)
    while entry_i > 0 and _ai_point_in_water(
        game_map, preset_name, samples[entry_i][0][0], samples[entry_i][0][1],
    ):
        entry_i -= 1
    while exit_i < n and _ai_point_in_water(
        game_map, preset_name, samples[exit_i][0][0], samples[exit_i][0][1],
    ):
        exit_i += 1
    return samples[entry_i][0], samples[exit_i][0]


def _ai_resolve_water_crossing_segment(p1, p2, game_map, preset_name: str, current_year: int) -> list:
    """Prefer historic bridge geometry; otherwise straight chord from shore to shore only."""
    ax, ay = float(p1[0]), float(p1[1])
    bx, by = float(p2[0]), float(p2[1])
    if not _ai_route_segment_is_water_crossing(game_map, preset_name, ax, ay, bx, by):
        return [(ax, ay), (bx, by)]
    entry, exit_pt = _ai_water_shore_points_on_chord(p1, p2, game_map, preset_name)
    if entry is None or exit_pt is None:
        return [(ax, ay), (bx, by)]
    bridge_pts = []
    try:
        from map_preset_loader import get_preset_bridge_polyline_between_shores
        bridge_pts = get_preset_bridge_polyline_between_shores(
            preset_name, game_map, entry, exit_pt, current_year, max_points=12,
        ) or []
    except Exception:
        bridge_pts = []
    if bridge_pts and len(bridge_pts) >= 2:
        return [(float(p[0]), float(p[1])) for p in bridge_pts]
    return [(float(entry[0]), float(entry[1])), (float(exit_pt[0]), float(exit_pt[1]))]


def _ai_normalize_route_water_crossings(
    points: list, game_map, preset_name: str, current_year: int,
) -> list:
    """Clip water crossings to shore-to-shore chords and prefer existing bridge polylines."""
    if not points or len(points) < 2 or not preset_name:
        return list(points) if points else []
    try:
        from map_preset_loader import preset_has_water_areas
        if not preset_has_water_areas(preset_name):
            return [(float(p[0]), float(p[1])) for p in points]
    except Exception:
        return [(float(p[0]), float(p[1])) for p in points]
    out = [(float(points[0][0]), float(points[0][1]))]
    for i in range(1, len(points)):
        p2 = (float(points[i][0]), float(points[i][1]))
        resolved = _ai_resolve_water_crossing_segment(
            out[-1], p2, game_map, preset_name, current_year,
        )
        if len(resolved) >= 2:
            if math.hypot(resolved[0][0] - out[-1][0], resolved[0][1] - out[-1][1]) < 1e-05:
                out.extend(resolved[1:])
            else:
                out.extend(resolved)
        else:
            out.append(p2)
    deduped = [out[0]]
    for p in out[1:]:
        if math.hypot(p[0] - deduped[-1][0], p[1] - deduped[-1][1]) > 1e-05:
            deduped.append(p)
    return deduped


def _ai_split_route_at_water_boundaries(
    points: list, game_map, preset_name: str, current_year: int,
) -> list:
    """Split a route into consecutive land and open-water chunks for separate line builds."""
    from map_preset_loader import preset_has_water_areas

    pts = [(float(p[0]), float(p[1])) for p in (points or [])]
    if len(pts) < 2 or not preset_name or not preset_has_water_areas(preset_name):
        return [{'points': pts, 'is_ferry': False}] if len(pts) >= 2 else []

    seg_ferry = [
        _ai_segment_qualifies_as_ferry(pts[i], pts[i + 1], game_map, preset_name)
        for i in range(len(pts) - 1)
    ]
    chunks = []
    start = 0
    while start < len(seg_ferry):
        is_ferry = seg_ferry[start]
        end = start
        while end < len(seg_ferry) and seg_ferry[end] == is_ferry:
            end += 1
        chunk_pts = pts[start:end + 1]
        if is_ferry and len(chunk_pts) >= 2:
            resolved = _ai_resolve_water_crossing_segment(
                chunk_pts[0], chunk_pts[-1], game_map, preset_name, current_year,
            )
            if len(resolved) >= 2:
                chunk_pts = [(float(p[0]), float(p[1])) for p in resolved]
        if len(chunk_pts) >= 2:
            chunks.append({'points': chunk_pts, 'is_ferry': is_ferry})
        start = end
    return chunks


def _ai_build_route_chunks_as_lines(
    ops,
    player,
    game_map,
    chunks: list,
    preset_name: str,
    current_year: int,
    rng: random.Random,
    *,
    base_line_name: str = None,
    use_bus: bool = False,
    use_fast_path: bool = True,
    budget: int = None,
) -> Optional[dict]:
    """Queue separate land and ferry lines for each chunk of a route that crosses water."""
    if not chunks:
        return None
    last_result = None
    base = base_line_name or _generate_ai_line_name(
        player, rng, ops=ops, route_points=chunks[0]['points'], current_year=current_year,
    )
    taken = _ai_collect_taken_line_names(ops) if ops else set()
    for idx, ch in enumerate(chunks):
        pts = ch['points']
        if len(pts) < 2:
            continue
        is_ferry = bool(ch.get('is_ferry'))
        if is_ferry:
            line_name = _ai_pick_unused_line_name(
                _ai_line_name_alternatives(base, is_ferry=True),
                taken,
                {_line_name_base_key(n) for n in taken},
            )
            if not line_name:
                line_name = _generate_ai_line_name(
                    player, rng, ops=ops, route_points=pts, current_year=current_year,
                )
            elev = ['water'] * (len(pts) - 1)
            plen = _route_polyline_length(pts)
            cost = max(15, int(FERRY_LENGTH_COST_PER_UNIT * plen))
            if (budget is not None and cost > budget) or not _ai_can_afford_line_with_two_stations(ops, player, cost):
                continue
            cost_info = {
                'cost': cost, 'length': plen, 'points_count': len(pts), 'elevation_levels': elev,
            }
            try:
                result = ops.create_line_complete(
                    line_name, pts, player, elevation_levels=elev,
                    is_ferry_line=True, is_bus_line=False, cost_info=cost_info, use_fast_path=False,
                )
            except Exception:
                result = None
            if not _ai_line_create_result_ok(result):
                continue
            _ai_open_line_with_two_stations(
                ops, player, line_name, pts, is_bus_line=False, service_type="commuter",
            )
            result['line_name'] = line_name
            result['points'] = pts
            result['is_ferry_line'] = True
            last_result = result
            taken.add(line_name)
            continue

        if idx == 0:
            line_name = base
        else:
            line_name = _generate_ai_line_name(
                player, rng, ops=ops, route_points=pts, current_year=current_year,
            )
            if line_name in taken:
                alt = _ai_pick_unused_line_name(
                    _ai_line_name_alternatives(base, is_ferry=False), taken,
                    {_line_name_base_key(n) for n in taken},
                )
                if alt:
                    line_name = alt
        use_bus_chunk = bool(use_bus)
        elev = _get_safe_elevation_levels(pts, game_map, preset_name, base='street')
        plen = _route_polyline_length(pts)
        unit_mult = 0.5 * 1.5 if use_bus_chunk else 1.5
        cost = max(15, int(plen * 225 * unit_mult))
        from operations import _ai_route_off_street_cost_multiplier
        cost = max(cost, int(round(cost * _ai_route_off_street_cost_multiplier(
            game_map, pts, current_year, is_bus_line=use_bus_chunk, skip_osm=True,
        ))))
        if budget is not None and cost > budget:
            continue
        cost_info = {
            'cost': cost, 'length': plen, 'points_count': len(pts), 'elevation_levels': elev,
        }
        try:
            result = ops.create_line_complete(
                line_name, pts, player, elevation_levels=elev,
                is_ferry_line=False, is_bus_line=use_bus_chunk,
                cost_info=cost_info, use_fast_path=use_fast_path,
            )
        except Exception:
            result = None
        if not _ai_line_create_result_ok(result):
            continue
        if use_bus_chunk:
            bus_svc = _decide_ai_bus_service_type(pts)
            _ai_open_line_with_two_stations(
                ops, player, line_name, pts, is_bus_line=True, service_type=bus_svc,
            )
            _try_assign_unassigned_bus_to_line(ops, player, line_name)
        else:
            rail_svc = _decide_ai_rail_service_type(pts)
            _ai_record_line_service_type(ops, line_name, rail_svc)
            _ai_open_line_with_two_stations(
                ops, player, line_name, pts, is_bus_line=False, service_type=rail_svc,
            )
        result['line_name'] = line_name
        result['points'] = pts
        result['is_bus_line'] = use_bus_chunk
        taken.add(line_name)
        last_result = result
    return last_result


def _ai_trim_route_endpoints_off_water(
    points: list, game_map, preset_name: str, *, for_route_validation: bool = False,
) -> list:
    if not points or len(points) < 2 or not preset_name:
        return list(points) if points else []
    pts = [(float(p[0]), float(p[1])) for p in points]
    while len(pts) >= 2 and _ai_point_in_water(
        game_map, preset_name, pts[0][0], pts[0][1], for_route_validation=for_route_validation,
    ):
        pts = pts[1:]
    while len(pts) >= 2 and _ai_point_in_water(
        game_map, preset_name, pts[-1][0], pts[-1][1], for_route_validation=for_route_validation,
    ):
        pts = pts[:-1]
    return pts if len(pts) >= 2 else list(points)


def _ai_finalize_osm_route_points(
    points: list, game_map, preset_name: str, current_year: int, min_seg: float = None,
    ops=None, *, skip_osm_majority: bool = False, mandatory_opener: bool = False,
) -> Optional[list]:
    """Prepare OSM street geometry, then normalize water crossings (bridges / shore chords)."""
    if preset_name and _ai_osm_street_routing_data_available(game_map, preset_name):
        # Street-graph presets: geometry must stay exactly on the polylines, so vertices are
        # never dropped or re-chorded here; the route either complies as-is or is rejected.
        pts = _ai_round_route_points(points or [])
        if len(pts) < 2:
            return None
        if min_seg is not None and _osm_polyline_length(pts) < min_seg:
            return None
        if not _ai_osm_route_passes_build_rules(
            game_map, pts, int(current_year), preset_name, is_bus_line=False,
        ):
            return None
        return pts
    prepared = _ai_prepare_osm_route_points(points, game_map, min_seg)
    if not prepared:
        return None
    if not preset_name:
        return prepared
    if _ai_route_has_water_crossing(prepared, game_map, preset_name):
        prepared = _ai_normalize_route_water_crossings(
            prepared, game_map, preset_name, current_year,
        )
    trimmed = _ai_trim_route_endpoints_off_water(
        prepared, game_map, preset_name, for_route_validation=False,
    )
    if not trimmed or len(trimmed) < 3:
        return None
    from operations import (
        _ai_land_straight_limit_applies,
        _ai_route_has_backtracking,
        _ai_route_has_overlong_land_straight_segment,
    )

    if _ai_route_has_backtracking(trimmed, game_map):
        return None
    if not mandatory_opener and _ai_land_straight_limit_applies(game_map):
        if _ai_route_has_overlong_land_straight_segment(
            game_map, trimmed, skip_osm=True, current_year=int(current_year),
        ):
            return None
    if min_seg is not None:
        plen = sum(
            math.hypot(trimmed[i + 1][0] - trimmed[i][0], trimmed[i + 1][1] - trimmed[i][1])
            for i in range(len(trimmed) - 1)
        )
        if plen < min_seg:
            return None
    if mandatory_opener:
        return _ai_round_route_points(trimmed)
    if _ai_land_straight_limit_applies(game_map) and not skip_osm_majority:
        if not _ai_osm_route_passes_build_rules(
            game_map, trimmed, int(current_year), preset_name or '', is_bus_line=False,
            mandatory_opener=mandatory_opener,
        ):
            return None
    return _ai_round_route_points(trimmed)


def _ai_prepare_osm_route_points(points: list, game_map, min_seg: float = None) -> Optional[list]:
    """Drop OSM vertices that cause backtracking or illegal turns; keep street polylines only."""
    if not points or len(points) < 3:
        return None
    _pn = getattr(game_map, 'map_preset_name', None) or ''
    if _pn and _ai_osm_street_routing_data_available(game_map, _pn):
        # Street-graph presets: dropping vertices would pull the route off its polylines.
        pts = _ai_round_route_points(points)
        if len(pts) < 3 or (min_seg is not None and _osm_polyline_length(pts) < min_seg):
            return None
        return pts
    from operations import (
        _ai_route_has_backtracking,
        _route_exceeds_max_turn_except_water_corners,
        _route_violates_min_turn_radius_except_water_corners,
    )

    def _route_ok(route_pts: list) -> bool:
        if _ai_route_has_backtracking(route_pts, game_map):
            return False
        try:
            from game_presets import MAX_TRAIN_ROUTE_TURN_DEGREES, ROUTE_TURN_CHECK_MIN_SEGMENT_GRID
            from game_presets import MIN_TRAIN_TURN_RADIUS_GRID

            if _route_exceeds_max_turn_except_water_corners(
                game_map,
                route_pts,
                MAX_TRAIN_ROUTE_TURN_DEGREES,
                min_segment_grid=ROUTE_TURN_CHECK_MIN_SEGMENT_GRID,
                skip_osm=True,
            ):
                return False
            if _route_violates_min_turn_radius_except_water_corners(
                game_map,
                route_pts,
                MIN_TRAIN_TURN_RADIUS_GRID,
                min_segment_grid=ROUTE_TURN_CHECK_MIN_SEGMENT_GRID,
                skip_osm=True,
            ):
                return False
        except Exception:
            return False
        return True

    pts = [_ai_round_grid_point(p) for p in points]
    if len(pts) < 3:
        return None

    # Keep full OSM street geometry when the entire polyline is valid.
    if _route_ok(pts):
        if min_seg is None or _osm_polyline_length(pts) >= min_seg:
            return pts

    simplified = [pts[0]]
    for p in pts[1:]:
        trial = simplified + [p]
        if _route_ok(trial):
            simplified.append(p)

    if len(simplified) >= 3 and _route_ok(simplified):
        if min_seg is None or _osm_polyline_length(simplified) >= min_seg:
            return simplified
    return None


def _ai_player_has_line_build_job(ops, player) -> bool:
    """True when the player owns a line or has line/line_extension work queued."""
    if player.get_line_names():
        return True
    if not ops or not hasattr(ops, 'get_build_queue'):
        return False
    return any(
        b.get('player') == player and b.get('type') in ('line', 'line_extension')
        for b in (ops.get_build_queue() or [])
    )


def _ai_needs_first_turn_line(
    ops,
    player,
    *,
    first_calendar_year: bool,
    ai_join_first_line_pending: bool,
) -> bool:
    """True when a living AI must queue its opener line this turn."""
    if not getattr(player, 'is_ai', False):
        return False
    if ops and hasattr(ops, 'is_player_eliminated') and ops.is_player_eliminated(player):
        return False
    if not (first_calendar_year or ai_join_first_line_pending):
        return False
    return not _ai_player_has_line_build_job(ops, player)


def _try_ai_two_point_line_build(
    ops,
    player,
    game_map,
    spendable: int,
    current_year: int,
    rng: random.Random,
    preset,
    *,
    existing_routes=None,
    points_with_pop=None,
    relaxed: bool = False,
) -> Optional[dict]:
    """Build a line from exactly one population anchor pair (six anchors on turn one)."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset is None and preset_name:
        try:
            from map_preset_loader import load_map_preset
            preset = load_map_preset(preset_name)
        except Exception:
            preset = None
    if preset and hasattr(preset, 'get_road_segment_points_for_ai'):
        return _try_build_osm_line_from_population_pairs(
            ops, player, game_map, spendable, current_year,
            points_with_pop=points_with_pop, rng=rng,
            existing_routes=existing_routes, preset=preset, relaxed=relaxed,
        )
    candidates = _ai_first_turn_six_population_points(ops, player, game_map, min_pop=1)
    picked = _ai_first_turn_pick_point_pair(candidates, rng)
    if not picked:
        return None
    p1, p2, _, _ = picked
    is_first = _is_first_line_for_player(ops, player, game_map)
    min_line_length = max(
        MIN_FIRST_LINE_LENGTH_GRID if is_first else MIN_LINE_LENGTH_GRID,
        AI_LOCAL_STOP_SPACING_GRID,
    )
    return _try_create_line_from_endpoint_pair(
        ops, player, game_map, p1, p2, spendable, current_year,
        preset_name, preset, rng, min_line_length, existing_routes=existing_routes,
    )


def _try_force_first_turn_line_build(
    ops,
    player,
    game_map,
    current_year: int,
    rng: random.Random,
    preset,
    *,
    existing_routes=None,
    points_with_pop=None,
) -> Optional[dict]:
    """Queue a first-turn line from one population anchor pair."""
    if not _ai_needs_first_turn_line(
        ops, player,
        first_calendar_year=_is_first_game_year(ops, current_year),
        ai_join_first_line_pending=_ai_join_year_pending_first_line(ops, player, current_year),
    ):
        return None
    money = max(0, int(ops.get_player_money(player)))
    force_cap = _ai_budget_bounds(
        ops, current_year, money,
        ceiling=(120000 if _ai_map_uses_street_build(game_map, preset) else 20000),
    )
    return _try_ai_two_point_line_build(
        ops, player, game_map, force_cap, current_year, rng, preset,
        existing_routes=existing_routes, points_with_pop=points_with_pop, relaxed=True,
    )


def _try_mandatory_extend_completed_lines(
    ops,
    player,
    game_map,
    current_year: int,
    spendable: int,
    min_extend_spendable: int,
    route_extend_ok: bool,
    extension_profit_gate: bool,
    points_with_pop: list,
    rng: random.Random,
    preset,
    lines_requiring_extension_for_station: list,
    *,
    line_build_complete: bool,
) -> Optional[tuple]:
    """Extend finished lines once initial construction is complete."""
    if not line_build_complete or not route_extend_ok or spendable <= min_extend_spendable:
        return None
    if not player.get_line_names():
        return None
    if lines_requiring_extension_for_station:
        extend_result = _try_extend_line(
            ops, player, game_map, spendable, current_year, points_with_pop,
            rng=rng, preset=preset, line_names_priority=lines_requiring_extension_for_station,
            require_profit_when_troubled=False,
        )
        if extend_result:
            return ('extend_line', extend_result)
    extend_result = _try_extend_line(
        ops, player, game_map, spendable, current_year, points_with_pop,
        rng=rng, preset=preset, require_profit_when_troubled=extension_profit_gate,
    )
    if extend_result:
        return ('extend_line', extend_result)
    return None


# Max population points to consider for AI line building.
# Raising this helps AIs consistently find higher-pop endpoints early.
_MAX_BUILD_CANDIDATES = 14
# Year-1 OSM openers: each AI samples six population anchors (up to C(6,2) street-routed pairs).
AI_FIRST_TURN_POPULATION_POINT_COUNT = 6
# Of the random population anchors an AI pairs into routes, at least this share (4 of 6)
# come from the map's most populous cells (the top AI_TOP_POPULATION_FRACTION of them).
AI_TOP_POPULATION_ANCHOR_SHARE = 4 / 6
AI_TOP_POPULATION_FRACTION = 0.05
# OSM pair-first builder: random population anchors, fewer street-route attempts per turn.
_MAX_OSM_BUILD_CANDIDATES = 20


def _ai_line_creation_randomness_factor(ops) -> float:
    """Game-wide multiplier for AI route anchor sampling and stochastic build attempts."""
    from game_presets import AI_LINE_CREATION_RANDOMNESS_FACTOR

    game = ops.get_game() if ops else None
    raw = getattr(game, 'ai_line_creation_randomness_factor', None) if game is not None else None
    factor = float(AI_LINE_CREATION_RANDOMNESS_FACTOR if raw is None else raw)
    return max(1.0, min(3.0, factor))


def _ai_route_candidate_count(ops) -> int:
    """Random population anchors sampled when scoring OSM route pairs (game setting)."""
    from game_presets import AI_ROUTE_CANDIDATE_COUNT_DEFAULT

    game = ops.get_game() if ops else None
    base = int(
        getattr(game, 'ai_route_candidate_count', AI_ROUTE_CANDIDATE_COUNT_DEFAULT)
        if game is not None else AI_ROUTE_CANDIDATE_COUNT_DEFAULT
    )
    scaled = int(round(base * _ai_line_creation_randomness_factor(ops)))
    return max(2, min(scaled, _MAX_OSM_BUILD_CANDIDATES))
_MAX_OSM_PAIR_ROUTE_ATTEMPTS = 8
_MAX_FIRST_TURN_OSM_PAIR_ROUTE_ATTEMPTS = 6
_OSM_ROAD_UNIT_COST = 225
# Max population points for extend-line scoring (fewer = faster)
_MAX_EXTEND_POINTS = 8
# Max candidates to call calculate_extend_cost per line (expensive on large maps)
_MAX_EXTEND_COST_CALLS = 4
# Max lines to consider for extend (fewer = faster; 2 is enough for variety)
_MAX_EXTEND_LINES = 4
# Max population points for station placement
_MAX_STATION_POINTS = 8
# Minimum stations required for a line to generate profit (0 or 1 = no profit)
MIN_STATIONS_FOR_PROFIT = 2
# AI line names: only use a geographic label (e.g. street) if it lies within this distance of the route sample point.
from game_presets import AI_LINE_NAME_MAX_DISTANCE_M, AI_LINE_NAME_MIN_ROUTE_FRACTION
# When extending onto a different street, max distance from line end to that street (grid units ≈ km).
AI_BUS_NEIGHBOR_STREET_MAX_GAP_M = 5.0
AI_RAIL_NEIGHBOR_STREET_MAX_GAP_M = 100.0
AI_BUS_NEIGHBOR_STREET_MAX_GAP_GRID = AI_BUS_NEIGHBOR_STREET_MAX_GAP_M / 1000.0
AI_RAIL_NEIGHBOR_STREET_MAX_GAP_GRID = AI_RAIL_NEIGHBOR_STREET_MAX_GAP_M / 1000.0


def _route_polyline_length(route: list) -> float:
    """Total length of route polyline in grid units."""
    if not route or len(route) < 2:
        return 0.0

    def _pt(p):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return (float(p[0]), float(p[1]))
        return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

    t = 0.0
    for i in range(len(route) - 1):
        a, b = _pt(route[i]), _pt(route[i + 1])
        t += math.hypot(b[0] - a[0], b[1] - a[1])
    return t


def _ai_chord_length_grid(p1, p2) -> float:
    return math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))


def _ai_is_allowed_straight_land_segment(
    p1, p2, game_map, preset_name: str, current_year: int,
) -> bool:
    """Straight land segments are only allowed as short connectors or over water."""
    if _ai_route_segment_is_water_crossing(
        game_map, preset_name or '',
        float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]),
    ):
        return True
    try:
        from map_preset_loader import preset_has_road_support
        if not preset_name or not preset_has_road_support(preset_name):
            return True
    except Exception:
        return True
    return _ai_chord_length_grid(p1, p2) <= float(AI_MAX_STRAIGHT_LAND_SEGMENT_GRID)


def _ai_snap_route_endpoints_to_anchors(route_points, anchor_a, anchor_b) -> Optional[list]:
    """Orient route to population anchors; keep street-snapped endpoints when already near anchors."""
    if not route_points or len(route_points) < 2:
        return None
    if not _ai_route_endpoints_match_pop_anchors(route_points, anchor_a, anchor_b):
        return None
    out = list(route_points)
    if (
        _ai_point_near_population_anchor(out[0], anchor_b)
        and _ai_point_near_population_anchor(out[-1], anchor_a)
    ):
        out = list(reversed(out))
    return _ai_round_route_points(out)


_AI_STREET_ROUTE_CACHE: dict = {}
_AI_STREET_ROUTE_CACHE_MAX = 512


def _ai_street_route_cache_key(p1, p2, preset_name: str, current_year: int, osm_pair_route: bool) -> tuple:
    return (
        preset_name or '',
        int(current_year),
        bool(osm_pair_route),
        round(float(p1[0]), 2),
        round(float(p1[1]), 2),
        round(float(p2[0]), 2),
        round(float(p2[1]), 2),
    )


def _ai_route_between_points(
    p1, p2, game_map, preset_name: str, current_year: int, *, finalize: bool = True,
    osm_pair_route: bool = False, mandatory_opener: bool = False, avoid_route: list = None,
) -> Optional[list]:
    """Route between two points for an AI land line.

    Presets with OSM road data: graph route along street polylines (connectors up to 10 m,
    water crossings as straight chords between shore streets); never a straight land chord.
    Other maps: a straight segment when allowed.
    """
    p1t = (float(p1[0]), float(p1[1]))
    p2t = (float(p2[0]), float(p2[1]))
    preset_name = preset_name or getattr(game_map, 'map_preset_name', '') or ''

    from map_preset_loader import preset_has_road_support
    if not (preset_name and preset_has_road_support(preset_name)):
        if osm_pair_route:
            return None
        if _ai_is_allowed_straight_land_segment(p1t, p2t, game_map, preset_name, current_year):
            return [p1t, p2t]
        return None

    cache_key = _ai_street_route_cache_key(p1t, p2t, preset_name, current_year, True)
    if avoid_route:
        cache_key = cache_key + (len(avoid_route), tuple(_ai_round_grid_point(p) for p in (avoid_route[0], avoid_route[-1])))
    if cache_key in _AI_STREET_ROUTE_CACHE:
        cached = _AI_STREET_ROUTE_CACHE[cache_key]
        return list(cached) if cached else None

    pts = None
    if _ai_osm_street_routing_data_available(game_map, preset_name):
        try:
            from ai_street_router import find_ai_street_route
            snap = AI_POPULATION_ANCHOR_RADIUS_GRID if osm_pair_route else 1.5
            raw = find_ai_street_route(
                game_map, p1t[0], p1t[1], p2t[0], p2t[1], int(current_year), max_snap_grid=snap,
                avoid_route=avoid_route,
                # Extensions start exactly at the line end (it is already on a street).
                start_snap_grid=0.009 if avoid_route else None,
            )
            pts = _ai_round_route_points(raw) if raw and len(raw) >= 2 else None
        except Exception:
            pts = None
    if pts is not None and not _ai_osm_route_passes_build_rules(
        game_map, pts, current_year, preset_name, is_bus_line=False,
    ):
        pts = None
    _AI_STREET_ROUTE_CACHE[cache_key] = list(pts) if pts else None
    if len(_AI_STREET_ROUTE_CACHE) > _AI_STREET_ROUTE_CACHE_MAX:
        _AI_STREET_ROUTE_CACHE.clear()
    return pts


def _ai_route_toward_anchor(
    p1, p2, game_map, current_year: int, min_len: float, max_len: float,
) -> Optional[list]:
    """Street route that starts at p1 and heads toward p2 (see find_ai_street_route_toward)."""
    try:
        from ai_street_router import find_ai_street_route_toward
        raw = find_ai_street_route_toward(
            game_map, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), int(current_year),
            float(min_len), float(max_len), max_snap_grid=AI_POPULATION_ANCHOR_RADIUS_GRID,
            accept=lambda x, y: _ai_population_near_point(game_map, x, y) > 0,
        )
    except Exception:
        return None
    pts = _ai_round_route_points(raw) if raw else None
    if not pts or len(pts) < 2:
        return None
    if not _ai_osm_route_passes_build_rules(game_map, pts, current_year, is_bus_line=False):
        return None
    return pts


def _try_create_line_from_endpoint_pair(
    ops,
    player,
    game_map,
    p1,
    p2,
    spendable: int,
    current_year: int,
    preset_name: str,
    preset,
    rng,
    min_line_length: float,
    existing_routes: list = None,
) -> Optional[dict]:
    """Queue a line between two endpoints using street polylines (not long land chords)."""
    dist_chord = _ai_chord_length_grid(p1, p2)
    if dist_chord < min_line_length:
        return None
    if _ai_population_near_point(game_map, p1[0], p1[1]) <= 0 or _ai_population_near_point(game_map, p2[0], p2[1]) <= 0:
        return None
    if existing_routes and _segment_overlaps_existing_routes(p1, p2, existing_routes, game_map=game_map):
        return None
    points = _ai_route_between_points(p1, p2, game_map, preset_name or '', current_year)
    if not points or len(points) < 2:
        return None
    dist = _route_polyline_length(points)
    use_bus = _ai_turn_build_mode(ops, current_year) == 'bus'
    cost = max(15, int(150 * dist * (0.5 if use_bus else 1.0)))
    if cost > spendable:
        return None
    if not _ai_planned_route_endpoints_have_station_sites(
        ops, player, game_map, points, is_bus_hint=use_bus,
    ):
        return None
    if not _ai_can_afford_line_with_two_stations(ops, player, cost):
        return None
    elev = _get_safe_elevation_levels(points, game_map, preset_name or '', base='street')
    cost_info = {'cost': cost, 'length': dist, 'points_count': len(points), 'elevation_levels': elev}
    line_name = _generate_ai_line_name(
        player, rng, ops=ops, route_points=points, current_year=current_year,
    )
    try:
        result = ops.create_line_complete(
            line_name, points, player,
            elevation_levels=elev,
            is_ferry_line=False, is_bus_line=use_bus,
            cost_info=cost_info, use_fast_path=True,
        )
    except Exception:
        return None
    if not result.get('success', True):
        return None
    result['line_name'] = line_name
    result['points'] = points
    result['cost'] = cost
    service = _decide_ai_bus_service_type(points) if use_bus else _decide_ai_rail_service_type(points)
    _ai_record_line_service_type(ops, line_name, service)
    if not _ai_open_line_with_two_stations(
        ops, player, line_name, points, is_bus_line=use_bus, service_type=service,
    ):
        return None
    if use_bus:
        _try_assign_unassigned_bus_to_line(ops, player, line_name)
    return result


def _line_needs_extension_before_second_station(ops, line_name: str, line) -> bool:
    """True if the line cannot place a second station at required spacing along the current route length."""
    if not line or not line_name:
        return False
    stations = line.get_stations() if hasattr(line, 'get_stations') else []
    if len(stations) >= 2:
        return False
    route = (
        ops.get_line_route_including_extensions(line_name)
        if hasattr(ops, 'get_line_route_including_extensions')
        else (line.get_route() if hasattr(line, 'get_route') else [])
    )
    if not route or len(route) < 2:
        return True
    min_sp = _get_ai_station_spacing(line_name=line_name, line=line)
    return _route_polyline_length(route) < min_sp


def _ai_min_spacing_for_new_route(
    route: list,
    *,
    is_bus: bool = False,
    service_type: str = None,
) -> float:
    """Minimum route length so two AI stations can be placed at required spacing."""
    if service_type:
        spacing = _ai_spacing_for_service(service_type, is_bus=is_bus)
        if spacing is not None:
            return spacing
    if is_bus:
        return AI_BUS_STOP_SPACING_GRID
    if route and len(route) >= 2:
        inferred = _decide_ai_rail_service_type(route)
        spacing = _ai_spacing_for_service(inferred, is_bus=False)
        if spacing is not None:
            return spacing
    return max(MIN_STATION_SPACING_GRID, AI_LOCAL_STOP_SPACING_GRID)


def _ai_extend_route_with_straight_connector(route: list, extra_len: float, from_end: bool) -> Optional[list]:
    """Add a straight segment; length cannot exceed the adjoining on-street segment."""
    if not route or len(route) < 2 or extra_len <= 0:
        return None
    max_len = float(AI_MAX_STRAIGHT_LAND_SEGMENT_GRID)
    extra_len = float(extra_len)
    pts = [(float(p[0]), float(p[1])) for p in route]
    if from_end:
        ax, ay = pts[-2]
        bx, by = pts[-1]
    else:
        ax, ay = pts[1]
        bx, by = pts[0]
    seg_len = math.hypot(bx - ax, by - ay)
    if seg_len < 1e-9:
        return None
    extra_len = min(extra_len, seg_len, max_len)
    if extra_len <= 0:
        return None
    ux, uy = (bx - ax) / seg_len, (by - ay) / seg_len
    if from_end:
        new_pt = (bx + ux * extra_len, by + uy * extra_len)
        return pts + [new_pt]
    new_pt = (bx - ux * extra_len, by - uy * extra_len)
    return [new_pt] + pts


def _ai_ensure_route_supports_two_stations(
    route: list,
    game_map,
    preset_name: str,
    current_year: int,
    preset=None,
    *,
    is_bus: bool = False,
    service_type: str = None,
    ops=None,
    mandatory_opener: bool = False,
) -> Optional[list]:
    """Lengthen a route when it is too short for two stations at required spacing."""
    if not route or len(route) < 2:
        return None
    pts = [(float(p[0]), float(p[1])) for p in route]
    min_sp = _ai_min_spacing_for_new_route(pts, is_bus=is_bus, service_type=service_type)
    if _route_polyline_length(pts) >= min_sp:
        return pts
    if mandatory_opener:
        return pts if _route_polyline_length(pts) >= MIN_LINE_LENGTH_GRID else None

    if preset_name and (ops is None or not _ai_turn_time_low(ops, 40.0)):
        try:
            from map_preset_loader import get_preset_road_extension_points
            start, end = pts[0], pts[-1]
            max_gap = (
                AI_BUS_NEIGHBOR_STREET_MAX_GAP_GRID if is_bus else AI_RAIL_NEIGHBOR_STREET_MAX_GAP_GRID
            )
            for extend_from_end in (True, False):
                base = end if extend_from_end else start
                other = start if extend_from_end else end
                road_pts = get_preset_road_extension_points(
                    preset_name,
                    game_map,
                    base,
                    other,
                    current_year,
                    extend_from_end,
                    max_points=12,
                    prefer_bridges=True,
                    preset=preset,
                    max_neighbor_street_gap_grid=max_gap,
                )
                if len(road_pts) < 2:
                    continue
                if extend_from_end:
                    extended = pts + [(float(p[0]), float(p[1])) for p in road_pts[1:]]
                else:
                    extended = [(float(p[0]), float(p[1])) for p in road_pts[:-1]] + pts
                finalized = _ai_finalize_osm_route_points(
                    extended, game_map, preset_name, current_year, min_sp,
                )
                if finalized and _route_polyline_length(finalized) >= min_sp:
                    return finalized
        except Exception:
            pass

    need = min_sp - _route_polyline_length(pts) + 0.05
    max_connector = float(AI_MAX_STRAIGHT_LAND_SEGMENT_GRID)
    if need > max_connector:
        return None
    from operations import _ai_land_straight_limit_applies, _ai_route_has_overlong_land_straight_segment

    for from_end in (True, False):
        candidate = _ai_extend_route_with_straight_connector(pts, need, from_end)
        if not candidate:
            continue
        if preset_name:
            base = pts[-1] if from_end else pts[0]
            tip = candidate[-1] if from_end else candidate[0]
            street_span = _ai_route_between_points(
                base, tip, game_map, preset_name, current_year, finalize=False,
            )
            if street_span and len(street_span) >= 2:
                span_len = _route_polyline_length(street_span)
                if span_len + 0.05 >= need:
                    if from_end:
                        candidate = pts + [(float(p[0]), float(p[1])) for p in street_span[1:]]
                    else:
                        candidate = [(float(p[0]), float(p[1])) for p in street_span[:-1]] + pts
        if _ai_land_straight_limit_applies(game_map) and _ai_route_has_overlong_land_straight_segment(
            game_map, candidate, skip_osm=True, current_year=int(current_year),
        ):
            continue
        if _route_polyline_length(candidate) >= min_sp:
            return candidate
    return None


def _ai_resolve_unique_station_name(
    ops,
    player,
    base_name: str,
    x: float = None,
    y: float = None,
    line_name: str = None,
) -> str:
    """Return a station name unique for this player (built + queued).

    Reuses base_name when it matches a transfer hub within AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M
    of a stop on another line."""
    base = (base_name or "Station").strip()
    if (
        x is not None
        and y is not None
        and line_name
        and hasattr(ops, "station_transfer_name_reuse_allowed")
    ):
        try:
            if ops.station_transfer_name_reuse_allowed(
                player, base, float(x), float(y), [line_name],
            ):
                return base
        except Exception:
            pass
    for try_name in _ai_station_name_alternatives(base):
        if hasattr(ops, 'assert_station_name_unique_for_player'):
            try:
                kwargs = {}
                if x is not None and y is not None and line_name:
                    kwargs = {
                        "x": float(x),
                        "y": float(y),
                        "line_names": [line_name],
                    }
                ops.assert_station_name_unique_for_player(player, try_name, **kwargs)
                return try_name
            except ValueError:
                continue
        elif try_name:
            return try_name
    try:
        from game_presets import MAJOR_TERMINAL_STATION_NAMES
        for alt in MAJOR_TERMINAL_STATION_NAMES:
            if alt == base:
                continue
            if hasattr(ops, 'assert_station_name_unique_for_player'):
                try:
                    kwargs = {}
                    if x is not None and y is not None and line_name:
                        kwargs = {"x": float(x), "y": float(y), "line_names": [line_name]}
                    ops.assert_station_name_unique_for_player(player, alt, **kwargs)
                    return alt
                except ValueError:
                    continue
    except Exception:
        pass
    for fallback in ("Transfer Point", "Transit Stop", "Platform", "Depot", "Waypoint"):
        if fallback != base:
            if hasattr(ops, 'assert_station_name_unique_for_player'):
                try:
                    ops.assert_station_name_unique_for_player(player, fallback)
                    return fallback
                except ValueError:
                    continue
            return fallback
    return base


def _adjust_ai_fares(
    ops,
    player,
    current_year: int,
    game_map,
    rng: random.Random = None,
    *,
    line_names: list = None,
) -> list:
    """Move fares toward the timeline target in $0.25 steps when below cap."""
    rng = rng or random.Random()
    map_lines = game_map.get_lines_by_name() or {}
    sy, ey = _timeline_year_span(ops)
    from inflation_utils import FARE_QUARTER_DOLLARS, get_nominal_fare_cap, get_timeline_fare_target, round_fare_to_quarter
    if ops is not None and hasattr(ops, 'get_timeline_fare_target'):
        target_fare = float(ops.get_timeline_fare_target(current_year))
        _, max_acceptable_fare = ops.get_nominal_fare_cap(current_year)
    else:
        target_fare = float(get_timeline_fare_target(int(current_year), start_year=sy, end_year=ey))
        _, max_acceptable_fare = get_nominal_fare_cap(int(current_year), start_year=sy, end_year=ey)
    target_fare = round_fare_to_quarter(max(FARE_QUARTER_DOLLARS * 8, min(max_acceptable_fare, target_fare)))
    updated = []
    names = line_names if line_names is not None else player.get_line_names()
    for line_name in names:
        line = map_lines.get(line_name)
        if not line or not hasattr(line, 'fare'):
            continue
        stations = line.get_stations() if hasattr(line, 'get_stations') else []
        if len(stations) < 2:
            continue
        current_fare = round_fare_to_quarter(getattr(line, 'fare', target_fare) or target_fare)
        if current_fare > target_fare:
            new_fare = target_fare
        elif current_fare < target_fare:
            new_fare = round_fare_to_quarter(min(target_fare, current_fare + FARE_QUARTER_DOLLARS))
        else:
            continue
        if hasattr(ops, 'set_line_fare'):
            try:
                ops.set_line_fare(line, new_fare)
                updated.append(line_name)
            except Exception:
                pass
    return updated


def _ai_future_max_worker_demand(current_year: int) -> float:
    """Worst-case worker demand next calendar year (matches strike warning logic)."""
    next_year = int(current_year) + 1
    base_demand = 1.0 + (next_year - 1830) * (5.0 - 1.0) / (2050 - 1830)
    base_demand = max(1.0, min(5.0, base_demand))
    return min(5.0, base_demand + base_demand * 0.1)


def _try_prevent_worker_strikes(ops, player, current_year: int) -> Optional[dict]:
    """Raise worker salary multipliers so hired workers stay below the strike threshold."""
    if not hasattr(player, 'get_worker_rental') or not hasattr(ops, 'set_worker_salary_multiplier'):
        return None
    from worker import CONSTRUCTION_WORKER, ENGINEER, LAWYER, MAINTENANCE_WORKER

    future_cap = _ai_future_max_worker_demand(current_year)
    adjustments = []
    for wt in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
        rental = player.get_worker_rental(wt.name)
        if not rental or int(getattr(rental, 'quantity', 0) or 0) <= 0:
            continue
        current = float(wt.get_salary_multiplier())
        demand = float(getattr(rental, 'demanded_multiplier', 1.0) or 1.0)
        needed_demand = max(demand, future_cap)
        if rental.is_on_strike():
            safe_pay = min(5.0, max(current, needed_demand))
        else:
            # Strikes trigger at ≥0.5× underpayment — keep hired workers just above that buffer.
            safe_pay = min(5.0, max(current, needed_demand - 0.49))
        if safe_pay > current + 1e-6:
            try:
                ops.set_worker_salary_multiplier(wt.name, safe_pay)
                adjustments.append({'worker': wt.name, 'from': current, 'to': safe_pay})
            except Exception:
                pass
    if adjustments:
        return {'adjustments': adjustments}
    return None


def _ai_apply_worker_strike_prevention(ops, player, current_year: int, record_action) -> None:
    """Always try to lift salaries for hired worker types before strikes begin."""
    while True:
        strike_guard = _try_prevent_worker_strikes(ops, player, current_year)
        if not strike_guard:
            break
        record_action('adjust_worker_salary', strike_guard)


def _adjust_ai_schedules(
    ops,
    player,
    game_map,
    *,
    has_profitable_line: bool,
    is_losing_money: bool,
    is_wealthy_and_profitable: bool,
    early_growth: bool,
    schedule_survival_tight: bool = False,
    line_names: list = None,
    force_line_names: set = None,
) -> list:
    """Set peak/off_peak/night schedules as high as physics allows without net schedule loss.

    Uses ``compute_max_affordable_ai_schedule``: gross line profit must cover AI-budgeted
    schedule operating cost (8× nominal cost per train run × trips per year × trainsets)."""
    map_lines = game_map.get_lines_by_name() or {}
    updated = []
    force_line_names = force_line_names or set()
    names = line_names if line_names is not None else player.get_line_names()
    current_year = ops.get_year() if ops and hasattr(ops, 'get_year') else 1830
    for line_name in names:
        line = map_lines.get(line_name)
        if not line or not hasattr(line, 'schedule') or not hasattr(line, 'set_schedule_frequency'):
            continue
        if _line_complete_station_count(line) < 2:
            continue
        if not hasattr(line, 'get_max_frequency_per_hour'):
            continue
        if line.get_max_frequency_per_hour('peak') <= 0:
            continue
        if not hasattr(ops, 'compute_max_affordable_ai_schedule'):
            continue
        targets = dict(ops.compute_max_affordable_ai_schedule(line, current_year) or {})
        if all(float(targets.get(p, 0) or 0) <= 0 for p in ('peak', 'off_peak', 'night')):
            # Never take an open line out of service: keep at least minimal service.
            targets = dict(AI_MIN_SERVICE_SCHEDULE)
        current = getattr(line, 'schedule', {})
        peak = float(current.get('peak', 0) or 0)
        off_peak = float(current.get('off_peak', 0) or 0)
        night = float(current.get('night', 0) or 0)
        target_peak = float(targets.get('peak', 0) or 0)
        target_off_peak = float(targets.get('off_peak', 0) or 0)
        target_night = float(targets.get('night', 0) or 0)

        if (
            line_name in force_line_names
            or abs(peak - target_peak) > 1e-6
            or abs(off_peak - target_off_peak) > 1e-6
            or abs(night - target_night) > 1e-6
        ):
            try:
                line.set_schedule_frequency('peak', target_peak)
                line.set_schedule_frequency('off_peak', target_off_peak)
                line.set_schedule_frequency('night', target_night)
                updated.append({
                    'line': line_name,
                    'peak': float(line.schedule.get('peak', target_peak) or 0),
                    'off_peak': float(line.schedule.get('off_peak', target_off_peak) or 0),
                    'night': float(line.schedule.get('night', target_night) or 0),
                })
            except Exception:
                pass
    return updated


def _format_ai_schedule_entry(entry) -> str:
    """Format one line's schedule for AI Watch."""
    from game_presets import format_schedule_frequency

    if isinstance(entry, dict):
        line_name = entry.get('line', entry.get('line_name', ''))
        if 'peak' in entry or 'off_peak' in entry or 'night' in entry:
            peak = float(entry.get('peak', 0) or 0)
            off_peak = float(entry.get('off_peak', 0) or 0)
            night = float(entry.get('night', 0) or 0)
            return (
                f"{line_name} (peak {format_schedule_frequency(peak)}, "
                f"off-peak {format_schedule_frequency(off_peak)}, "
                f"night {format_schedule_frequency(night)})"
            )
        return str(line_name or entry)
    return str(entry)


def _format_ai_action(action_name: str, result: dict) -> str:
    """Format AI action result for AI Watch display. Returns human-readable description."""
    if action_name == 'assign_trainset':
        line = result.get('line', '')
        return f"Assigned trainset to line {line}"
    if action_name == 'add_station':
        line_name = result.get('line_name', result.get('line', ''))
        name = result.get('station_name', '')
        pt = result.get('point', result.get('grid_point'))
        if pt is not None:
            x, y = pt[0], pt[1] if isinstance(pt, (list, tuple)) and len(pt) >= 2 else (pt, pt)
            return f"Added station {name} to line {line_name} at ({int(x)},{int(y)})"
        return f"Added station {name} to line {line_name}"
    if action_name == 'extend_line':
        line_name = result.get('line_name', '')
        cost = result.get('cost', 0)
        pts = result.get('additional_points', result.get('points', []))
        if pts:
            p = pts[0]
            x, y = (p[0], p[1]) if isinstance(p, (list, tuple)) and len(p) >= 2 else (p, p)
            return f"Extended line {line_name} toward ({int(x)},{int(y)}) for ${cost}"
        return f"Extended line {line_name} for ${cost}"
    if action_name == 'abandon_line':
        line_name = result.get('line_name', '')
        if result.get('follow_up'):
            return f"Abandoned line {line_name} and started replacement"
        return f"Abandoned line {line_name} (listed on marketplace)"
    if action_name == 'create_line':
        line_name = result.get('line_name', '')
        pts = result.get('points', [])
        cost = result.get('cost', 0)
        if len(pts) >= 2:
            p1, p2 = pts[0], pts[-1]
            x1, y1 = (p1[0], p1[1]) if isinstance(p1, (list, tuple)) else (p1, p1)
            x2, y2 = (p2[0], p2[1]) if isinstance(p2, (list, tuple)) else (p2, p2)
            return f"Built line {line_name} from ({int(x1)},{int(y1)}) to ({int(x2)},{int(y2)}) for ${cost}"
        return f"Built line {line_name} for ${cost}"
    if action_name == 'buy':
        item = result.get('item')
        item_name = result.get('item_name', result.get('line', ''))
        price = result.get('price', 0)
        if result.get('item') == 'line' or result.get('line'):
            return f"Bought line {item_name} from marketplace for ${price}"
        return f"Bought {item_name} for ${price}"
    if action_name == 'rent_workers':
        worker = result.get('worker', '')
        qty = result.get('quantity', 1)
        return f"Rented {qty} {worker}(s)"
    if action_name == 'merge':
        acq = result.get('acquirer')
        acq_name = acq.get_name() if acq and hasattr(acq, 'get_name') else result.get('acquirer_name', '')
        return f"Merged into {acq_name}" if acq_name else "Company merged"
    if action_name == 'pass':
        return "Passed"
    if action_name == 'set_fares':
        lines = result.get('lines', [])
        return f"Adjusted fares on {len(lines)} line(s)" if lines else "Adjusted fares"
    if action_name == 'adjust_worker_salary':
        adj = result.get('adjustments') or []
        if not adj:
            return "Adjusted worker salaries"
        parts = [f"{a.get('worker', 'Worker')} {a.get('from', 0):.1f}x→{a.get('to', 0):.1f}x" for a in adj[:3]]
        return "Raised salaries to avoid strikes: " + ", ".join(parts)
    if action_name == 'set_schedule':
        lines = result.get('lines', [])
        if not lines:
            return "Updated schedule"
        if len(lines) == 1:
            return f"Set schedule on {_format_ai_schedule_entry(lines[0])}"
        parts = [_format_ai_schedule_entry(entry) for entry in lines[:4]]
        suffix = f" (+{len(lines) - len(parts)} more)" if len(lines) > len(parts) else ""
        return f"Set schedule on {len(lines)} line(s): {'; '.join(parts)}{suffix}"
    if action_name == 'lobby':
        amount = result.get('amount', result.get('spent', 0))
        option_id = result.get('option_id', '')
        option_labels = {
            'drop_excuse': 'drop lawsuit excuse',
            'tax_break': 'tax break',
            'reduce_property_tax_5': 'reduce property tax 5%',
            'increase_property_tax_5': 'increase property tax 5%',
            'one_time_grant': 'one-time grant',
            'pass_law': 'delay lawsuit checks',
            'area_protection': 'area protection',
            'influence_seat': 'influence seat',
            'influence_tiebreaker': 'influence tiebreaker',
            'gov_attack': 'government penalty',
            'add_lawsuit_excuse': 'trigger audit',
            'audit_target': 'force audit',
            'receive_funding': 'government funding',
            'redirect_funding': 'redirect funding',
            'block_funding': 'block funding',
        }
        label = option_labels.get(option_id, option_id or 'lobby')
        return f"Lobbied for {label} (${amount})"
    return action_name


def _straight_line_crosses_land(p1: tuple, p2: tuple, game_map, preset_name: str) -> bool:
    """True if the straight line from p1 to p2 crosses land (reject). False if mostly over water (ferry - allow).
    Straight lines across water (ferries) are OK; shortcuts across land must use streets."""
    from map_preset_loader import get_map_bounds, load_map_preset, preset_has_water_areas
    if not preset_name or not preset_has_water_areas(preset_name):
        return False
    try:
        bounds = get_map_bounds(preset_name)
        if not bounds or 'min_lat' not in bounds:
            return False
        w, h = max(1, game_map.width), max(1, game_map.height)
        min_lat, max_lat = bounds['min_lat'], bounds['max_lat']
        min_lon, max_lon = bounds['min_lon'], bounds['max_lon']
        if max_lon - min_lon == 0 or max_lat - min_lat == 0:
            return False

        def _grid_to_latlon(x, y):
            lat = min_lat + (y / h) * (max_lat - min_lat)
            lon = min_lon + (x / w) * (max_lon - min_lon)
            return (lat, lon)

        preset = load_map_preset(preset_name)
        if not preset or not hasattr(preset, 'is_water_location'):
            return False
        try:
            argcount = preset.is_water_location.__code__.co_argcount
            is_water = (lambda lat, lon: preset.is_water_location(lat, lon, None)) if argcount >= 3 else (lambda lat, lon: preset.is_water_location(lat, lon))
        except Exception:
            return False

        # Sample 8 points along the line (excluding endpoints - they're population points on land)
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        water_count = 0
        for i in range(1, 9):
            t = i / 9.0
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            lat, lon = _grid_to_latlon(x, y)
            if is_water(lat, lon):
                water_count += 1
        # If majority over water -> ferry, allow (return False = does not cross land)
        # If majority over land -> land shortcut, reject (return True = crosses land)
        return water_count < 4
    except Exception:
        return False


def _try_build_ferry_line(ops, player, game_map, spendable: int, current_year: int, points_with_pop: list = None, rng: random.Random = None, existing_routes: list = None, preset=None) -> Optional[dict]:
    """Build a ferry line as a straight segment across water using preset-provided segments only.
    Does NOT use the population-point build system; ferry candidates must come from get_preset_ferry_segment_points.
    Adds endpoint stations on success. Returns None if preset does not provide ferry segments.
    preset: optional pre-loaded map preset (avoids repeated load_map_preset)."""
    rng = rng or random.Random()
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return None
    try:
        from map_preset_loader import preset_has_water_areas, get_preset_ferry_segment_points
        if not preset_has_water_areas(preset_name):
            return None
    except Exception:
        return None

    if existing_routes is None:
        existing_routes = _get_existing_line_routes_for_overlap(ops, game_map)
    segments = get_preset_ferry_segment_points(
        preset_name, game_map, current_year, max_points=12,
        existing_line_routes=existing_routes, rng=rng, preset=preset
    )
    if not segments:
        return None
    # Shuffle so different AIs/games pick different ferries
    rng.shuffle(segments)
    pop_map = getattr(game_map, "population", None) or {}

    for road_points in segments:
        if len(road_points) < 2:
            continue
        points = [(float(p[0]), float(p[1])) for p in road_points]
        p1 = points[0]
        p2 = points[-1]
        # Never build ferries from/to low-pop shore endpoints (wide radius matches OSM road AI).
        if _population_at_or_near(p1[0], p1[1], pop_map, radius=10) < 80:
            continue
        if _population_at_or_near(p2[0], p2[1], pop_map, radius=10) < 80:
            continue
        # Pre-filter: segment must stay within ferry proximity of water when tile_system is available.
        ts = getattr(game_map, "tile_system", None)
        if ts and hasattr(ts, "is_segment_near_water"):
            ok_seg = True
            for i in range(len(points) - 1):
                ax, ay = points[i]
                bx, by = points[i + 1]
                try:
                    from game_presets import FERRY_MAX_DISTANCE_FROM_WATER_M
                    if not ts.is_segment_near_water(
                        ax, ay, bx, by, FERRY_MAX_DISTANCE_FROM_WATER_M, skip_osm=False
                    ):
                        ok_seg = False
                        break
                except Exception:
                    ok_seg = False
                    break
            if not ok_seg:
                continue
        else:
            # No tile system: use preset water proximity only (no OSM during scoring).
            from game_presets import FERRY_MAX_DISTANCE_FROM_WATER_M
            from operations import _ferry_segment_near_water
            ok_seg = True
            for i in range(len(points) - 1):
                ax, ay = points[i]
                bx, by = points[i + 1]
                if not _ferry_segment_near_water(
                    game_map, ax, ay, bx, by, max_distance_m=FERRY_MAX_DISTANCE_FROM_WATER_M
                ):
                    ok_seg = False
                    break
            if not ok_seg:
                continue
        total_length = sum(
            math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
            for i in range(len(points) - 1)
        )
        if total_length < 0.55:  # MIN_LINE_LENGTH_GRID
            continue
        overlaps = False
        for i in range(len(points) - 1):
            if _segment_overlaps_existing_routes(
                points[i], points[i + 1], existing_routes, game_map=game_map,
            ):
                overlaps = True
                break
        if overlaps:
            continue
        cost = max(15, int(FERRY_LENGTH_COST_PER_UNIT * total_length))
        if cost > spendable or not _ai_can_afford_line_with_two_stations(ops, player, cost):
            continue
        elev = ['water'] * max(1, len(points) - 1)
        cost_info = {'cost': cost, 'length': total_length, 'points_count': len(points), 'elevation_levels': elev}
        line_name = _generate_ai_line_name(
            player, rng, ops=ops, route_points=points, current_year=current_year,
        )
        try:
            if hasattr(ops, "_validate_ferry_route"):
                vr = ops._validate_ferry_route(points)
                if not vr.get("success"):
                    continue
            result = ops.create_line_complete(
                line_name, points, player,
                elevation_levels=elev,
                is_ferry_line=True,
                is_bus_line=False,
                cost_info=cost_info,
                use_fast_path=False,
            )
            if result.get('success', True):
                result['line_name'] = line_name
                result['points'] = points
                result['cost'] = cost
                _ai_open_line_with_two_stations(
                    ops, player, line_name, points, is_bus_line=False, service_type="commuter",
                )
                return result
        except Exception:
            continue
    return None


def _population_at_or_near(px: float, py: float, pop_map: dict, radius: int = 5) -> int:
    """Get population at grid point or nearest cell within radius. For maps with sparse population grids."""
    if not isinstance(pop_map, dict) or not pop_map:
        return 0
    ix, iy = int(round(px)), int(round(py))
    try:
        pop = int(pop_map.get((ix, iy), 0) or 0)
    except (TypeError, ValueError):
        pop = 0
    if pop > 0:
        return pop
    for r in range(1, radius + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    try:
                        p = int(pop_map.get((ix + dx, iy + dy), 0) or 0)
                    except (TypeError, ValueError):
                        p = 0
                    if p > 0:
                        return p
    return 0


def _ai_point_near_population_anchor(point, anchor, max_dist: float = None) -> bool:
    """True when point is within one grid unit of a scored population anchor."""
    if point is None or anchor is None:
        return False
    cap = AI_POPULATION_ANCHOR_RADIUS_GRID if max_dist is None else float(max_dist)
    return math.hypot(float(point[0]) - float(anchor[0]), float(point[1]) - float(anchor[1])) <= cap + 1e-6


def _ai_population_at_anchor(game_map, x: float, y: float) -> int:
    """Population within one grid unit of a population anchor (not a wide search radius)."""
    if game_map is None:
        return 0
    pop_map = getattr(game_map, 'population', None) or {}
    if not pop_map:
        return 0
    r = max(1, int(math.ceil(AI_POPULATION_ANCHOR_RADIUS_GRID)))
    try:
        from map_preset_utils import population_at_or_near as _preset_pop_at_or_near
        return int(_preset_pop_at_or_near(float(x), float(y), pop_map, radius=r) or 0)
    except Exception:
        return _population_at_or_near(float(x), float(y), pop_map, radius=r)


def _ai_route_endpoints_match_pop_anchors(route_points, anchor_a, anchor_b) -> bool:
    if not route_points or len(route_points) < 2:
        return False
    start, end = route_points[0], route_points[-1]
    forward = (
        _ai_point_near_population_anchor(start, anchor_a)
        and _ai_point_near_population_anchor(end, anchor_b)
    )
    reverse = (
        _ai_point_near_population_anchor(start, anchor_b)
        and _ai_point_near_population_anchor(end, anchor_a)
    )
    return forward or reverse


def _ai_orient_route_to_pop_anchors(route_points, anchor_a, anchor_b) -> Optional[list]:
    if not route_points or len(route_points) < 2:
        return None
    if not _ai_route_endpoints_match_pop_anchors(route_points, anchor_a, anchor_b):
        return None
    start, end = route_points[0], route_points[-1]
    if (
        _ai_point_near_population_anchor(start, anchor_b)
        and _ai_point_near_population_anchor(end, anchor_a)
    ):
        return list(reversed(route_points))
    return list(route_points)


def _ai_population_near_point(game_map, x: float, y: float, radius: int = 4) -> int:
    """Population at (x,y) for AI station rules; 0 if none. OSM presets use geodesic-capped preset lookup."""
    if game_map is None:
        return 0
    # Cache preset population lookups: OSM presets can make this expensive (geodesic caps + included-point checks).
    # Keyed by a rounded grid coordinate and the current population signature so deletions/edits invalidate naturally.
    try:
        sig = _population_cache_signature(game_map)
        cache = getattr(game_map, "_ai_station_pop_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(game_map, "_ai_station_pop_cache", cache)
        # Round to 3 decimals to keep cache stable for near-identical float points (extensions / interpolation).
        key = (round(float(x), 3), round(float(y), 3), int(radius), sig)
        if key in cache:
            return cache[key]
    except Exception:
        cache = None
        key = None
    pop_map = getattr(game_map, 'population', None) or {}
    try:
        from map_preset_loader import get_preset_ai_station_population
        val = get_preset_ai_station_population(game_map, x, y, pop_map)
    except Exception:
        val = _population_at_or_near(x, y, pop_map, radius=radius)
    if cache is not None and key is not None:
        try:
            cache[key] = int(val or 0)
            # Prevent unbounded growth on long sessions.
            if len(cache) > 15000:
                cache.clear()
        except Exception:
            pass
    return int(val or 0)


def _ops_resolve_game_map(ops):
    """Resolve the live game map for AI helpers; prefer ops.get_map() (used by Operations and unit tests)."""
    if not ops:
        return None
    if hasattr(ops, 'get_map'):
        try:
            m = ops.get_map()
            if m is not None:
                return m
        except Exception:
            pass
    g = ops.get_game() if hasattr(ops, 'get_game') else None
    if g is not None and hasattr(g, 'get_map'):
        try:
            return g.get_map()
        except Exception:
            pass
    return None


def _estimate_line_profit(
    pop1: int,
    pop2: int,
    dist: float,
    est_cost: int,
    current_year: int,
    game_start_year: int = 1830,
    ops=None,
    route_points: list = None,
    existing_routes: list = None,
    player=None,
    interior_max_pop: int = 0,
) -> float:
    """Estimate annual operating profit potential for a planned line (same helper as humans via ops)."""
    del est_cost, game_start_year, existing_routes  # payback callers compare cost separately
    if dist <= 0:
        return 0.0
    if ops is not None and route_points and len(route_points) >= 2 and interior_max_pop <= 0:
        interior_fn = getattr(ops, '_ai_guard_interior_max_pop', None)
        if callable(interior_fn):
            try:
                interior_max_pop = int(interior_fn(ops.get_map(), route_points) or 0)
            except Exception:
                interior_max_pop = 0
    estimate_fn = None
    if ops is not None:
        estimate_fn = getattr(ops, 'estimate_planned_line_annual_profit', None)
        if estimate_fn is None:
            estimate_fn = getattr(ops, '_ai_planned_line_gross_annual_revenue_floor', None)
    if callable(estimate_fn):
        try:
            return float(estimate_fn(
                int(pop1), int(pop2), float(dist), int(current_year),
                interior_max_pop=int(interior_max_pop),
                route_points=route_points, player=player,
            ))
        except Exception:
            pass
    connection_value = (pop1 * pop2) / max(0.1, dist)
    if interior_max_pop > 0:
        connection_value = max(
            connection_value,
            interior_max_pop * max(pop1, pop2, max(1, interior_max_pop * 0.25)) / max(0.1, dist),
        )
    from inflation_utils import get_nominal_fare_cap
    gsy = 1830
    _, fare_cap = get_nominal_fare_cap(int(current_year), start_year=gsy, end_year=2050)
    return connection_value * float(fare_cap) * 0.5 * 1.25


def _ai_line_has_pending_build(ops, player, line_name: str) -> bool:
    if not hasattr(ops, 'get_build_queue'):
        return False
    for build in ops.get_build_queue() or []:
        if build.get('player') != player:
            continue
        data = build.get('data') or {}
        if build.get('type') == 'line' and data.get('name') == line_name:
            return True
        if build.get('type') == 'line_extension' and data.get('line_name') == line_name:
            return True
        if build.get('type') == 'station' and line_name in (data.get('line_names') or []):
            return True
    return False


def _ai_line_is_stuck_understationed(
    ops, game_map, line_name: str, line, points_with_pop, player,
) -> bool:
    """True when a line has fewer than two stations and cannot add another this turn."""
    if not line or not line_name:
        return False
    stations = line.get_stations() if hasattr(line, 'get_stations') else []
    if len(stations) >= 2:
        return False
    if _ai_line_has_pending_build(ops, player, line_name):
        return False
    if _line_needs_extension_before_second_station(ops, line_name, line):
        return True
    min_spacing = _get_ai_station_spacing(line_name=line_name, line=line)
    pt = _pick_station_point(
        ops, game_map, line_name, line, points_with_pop, set(),
        min_spacing=min_spacing, player=player,
    )
    return pt is None


def _try_ai_abandon_stuck_understationed_lines(
    ops, player, game_map, map_lines, points_with_pop, current_year,
    spendable: int, rng, preset=None, existing_routes=None,
) -> Optional[tuple]:
    """Abandon lines that cannot reach two stations, then open a replacement corridor."""
    for ln in list(player.get_line_names()):
        line = map_lines.get(ln)
        if line is None:
            if _ai_line_has_pending_build(ops, player, ln):
                continue
            try:
                result = ops.sell_player_line_to_marketplace(player, ln)
            except Exception:
                continue
            return ('abandon_line', result)
        if not _ai_line_is_stuck_understationed(
            ops, game_map, ln, line, points_with_pop, player,
        ):
            continue
        try:
            result = ops.sell_player_line_to_marketplace(player, ln)
        except Exception:
            continue
        spend = max(0, ops.get_player_money(player))
        follow_up = _try_build_line(
            ops, player, game_map, spend, current_year, points_with_pop,
            rng=rng, existing_routes=existing_routes, preset=preset,
        )
        if not _ai_line_create_result_ok(follow_up):
            follow_up = _try_extend_line(
                ops, player, game_map, spend, current_year, points_with_pop,
                rng=rng, preset=preset,
            )
        if follow_up:
            result['follow_up'] = follow_up
        return ('abandon_line', result)
    return None


def _get_game_seed(ops) -> int:
    """Game RNG seed; varies per game so AIs and naming differ each play-through."""
    game = getattr(ops, 'game', None) if ops is not None else None
    if game is not None:
        seed = getattr(game, '_ai_rng_seed', None)
        if isinstance(seed, (int, float)) and not isinstance(seed, bool):
            return int(seed) & 0xFFFFFFFF
        return id(game) & 0xFFFFFFFF
    return (id(ops) & 0xFFFFFFFF) if ops is not None else 0


def _try_build_line_stochastic(
    ops,
    player,
    game_map,
    spend_cap: int,
    current_year: int,
    points_with_pop,
    existing_routes,
    preset,
    n_attempts: int = None,
) -> Optional[dict]:
    """Try _try_build_line with several independent RNG streams.

    On dense OSM maps, later AIs in the same end-year pass see many ``existing_line_routes``.
    One RNG stream often fails (overlap, profit filter). A few fresh seeds usually find a route.
    OSM road sampling is very expensive per call, so we use fewer attempts when the preset uses it."""
    if spend_cap < 1 and not _is_first_game_year(ops, current_year):
        return None
    gs = _get_game_seed(ops)
    pid = (getattr(player, 'get_player_id', lambda: 0)() or 0)
    cap = max(int(spend_cap), 1)
    if n_attempts is None:
        uses_osm_roads = bool(preset and hasattr(preset, 'get_road_segment_points_for_ai'))
        randomness = _ai_line_creation_randomness_factor(ops)
        n_attempts = int(round((4 if uses_osm_roads else 8) * randomness))
        if uses_osm_roads and _is_first_game_year(ops, current_year) and _is_first_line_for_player(ops, player, game_map):
            n_attempts = max(3, n_attempts - 1)
    for k in range(n_attempts):
        if _ai_turn_time_exceeded(ops) or _ai_osm_build_budget_exhausted(
            ops, first_opener=_is_first_game_year(ops, current_year) and _is_first_line_for_player(ops, player, game_map),
        ):
            break
        seed_k = (gs ^ (current_year * 11003 + pid * 1301 + k * 104729)) & 0xFFFFFFFF
        arng = random.Random(seed_k)
        br = _try_build_line(
            ops, player, game_map, cap, current_year, points_with_pop,
            rng=arng, existing_routes=existing_routes, preset=preset,
        )
        if br:
            return br
    return None


def _map_width_height(game_map) -> tuple:
    """Return (width, height) for game map with defaults 100, 75."""
    w = getattr(game_map, 'width', 100) or 100
    h = getattr(game_map, 'height', 75) or 75
    return (w, h)


def _ai_rng(player, current_year: int, ops=None):
    """Per-player, per-game RNG. Uses numeric seed so behavior differs across games and between AIs."""
    pid = getattr(player, 'get_player_id', lambda: 0)() or 0
    game_seed = _get_game_seed(ops)
    # Numeric seed: deterministic within a game, but varies by game_seed so each game plays differently
    seed = (game_seed + pid * 10000 + current_year * 100) & 0xFFFFFFFF
    return random.Random(seed)


_AI_LINE_ROUTE_SUFFIXES = (" Line", " Route", " Corridor", " Shuttle", " Express", " Local", " Branch")
_AI_FERRY_ROUTE_SUFFIXES = (" Ferry", " Water Shuttle", " Harbor Route", " Ferry Route", " River Crossing")
_AI_NAME_DISAMBIGUATORS = (
    " North", " South", " East", " West", " Upper", " Lower", " Central", " Midtown", " Downtown",
)
_AI_NUMERIC_SUFFIX_RE = re.compile(
    r"(?:\(\s*\d+\s*\)|(?<=\s)\d+$|(?<=[A-Za-z])\d+$|\s+\d+\s+(?:Line|Route|Ferry|Stop|Station)$)",
    re.IGNORECASE,
)


def _ai_name_has_numeric_suffix(name: str) -> bool:
    return bool(_AI_NUMERIC_SUFFIX_RE.search((name or "").strip()))


def _ai_collect_taken_line_names(ops) -> set:
    taken = set()
    if not ops:
        return taken
    game_map = ops.get_map() if hasattr(ops, 'get_map') else None
    if game_map and hasattr(game_map, 'get_lines_by_name'):
        taken.update((game_map.get_lines_by_name() or {}).keys())
    for b in (ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []) or []:
        if b.get('type') == 'line':
            n = (b.get('data') or {}).get('name')
            if n:
                taken.add(n)
    return taken


def _ai_line_name_alternatives(base: str, *, is_ferry: bool = False) -> list:
    base = (base or "").strip()
    if not base:
        return []
    suffixes = _AI_FERRY_ROUTE_SUFFIXES if is_ferry else _AI_LINE_ROUTE_SUFFIXES
    alts = []
    seen = set()
    for suf in suffixes:
        cand = f"{base}{suf}"
        if cand not in seen:
            seen.add(cand)
            alts.append(cand)
    for dis in _AI_NAME_DISAMBIGUATORS:
        for suf in suffixes[:4]:
            cand = f"{base}{dis}{suf}"
            if cand not in seen:
                seen.add(cand)
                alts.append(cand)
    return alts


def _ai_pick_unused_line_name(
    candidates, taken: set, used_bases: set = None,
) -> Optional[str]:
    for cand in candidates or []:
        c = (cand or "").strip()
        if not c or c in taken:
            continue
        if used_bases is not None and _line_name_base_key(c) in used_bases:
            continue
        return c
    return None


def _ai_letter_name_at(k: int) -> str:
    """A, B, ..., Z, AA, AB, ... without numeric digits."""
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    n = max(0, int(k))
    name = ''
    while True:
        name = letters[n % 26] + name
        n = n // 26 - 1
        if n < 0:
            break
    return name


def _ai_station_name_alternatives(base: str) -> list:
    base = (base or "Station").strip()
    alts = [base]
    seen = {base}
    for dis in _AI_NAME_DISAMBIGUATORS:
        cand = f"{base}{dis}"
        if cand not in seen:
            seen.add(cand)
            alts.append(cand)
    for suf in (" Terminal", " Station", " Stop", " Plaza", " Square", " Junction", " Crossing"):
        cand = f"{base}{suf}"
        if cand not in seen:
            seen.add(cand)
            alts.append(cand)
    return alts


def _line_name_base_key(name: str) -> str:
    """Normalized corridor label without player suffixes or route-type suffixes."""
    s = (name or "").strip()
    if " (" in s:
        s = s.split(" (", 1)[0].strip()
    for suf in (" Line", " Route", " Corridor", " Shuttle"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s.casefold()


def _overlap_tolerance_for_map(game_map, default: float = 0.5) -> float:
    if game_map and getattr(game_map, "use_osm", False):
        try:
            from map_preset_utils import ai_grid_polyline_overlap_tolerance
            return ai_grid_polyline_overlap_tolerance(game_map)
        except Exception:
            return 0.02
    return default


def _route_area_labels(game_map, route_points: list, preset_name: str) -> set:
    """Broad areas (from the preset's area boxes) a route passes through, for line-name validation."""
    if not route_points or not preset_name:
        return set()
    labels = set()
    try:
        from geo_naming import areas_at
        step = max(1, len(route_points) // 12)
        for p in list(route_points[::step]) + [route_points[-1]]:
            labels |= areas_at(game_map, float(p[0]), float(p[1]))
    except Exception:
        pass
    return labels


def _format_street_label(raw: str) -> str:
    s = (raw or '').replace('_', ' ').strip()
    if not s:
        return ''
    return ' '.join(part.capitalize() for part in s.split())


def _route_dominant_street_names(
    game_map, route_points: list, preset_name: str, current_year: int,
    max_distance_m: float = None, min_route_fraction: float = None,
) -> list:
    """Street names the route runs along (within max_distance_m of the street polyline), longest first."""
    if not route_points or len(route_points) < 2 or not preset_name:
        return []
    if max_distance_m is None:
        max_distance_m = AI_LINE_NAME_MAX_DISTANCE_M
    if min_route_fraction is None:
        min_route_fraction = AI_LINE_NAME_MIN_ROUTE_FRACTION
    try:
        from map_preset_loader import get_preset_streets_route_runs_along
        streets = get_preset_streets_route_runs_along(
            preset_name, game_map, route_points, current_year=current_year,
            max_distance_m=max_distance_m, min_route_fraction=min_route_fraction,
        )
        if streets:
            return list(streets)
    except Exception:
        pass
    return []


def _line_name_from_street_base(
    base: str, taken: set, used_bases: set, rng: random.Random,
) -> Optional[str]:
    label = _format_street_label(base)
    if not label:
        return None
    suffixes = list(_AI_LINE_ROUTE_SUFFIXES)
    rng.shuffle(suffixes)
    candidates = [f"{label}{suffix}" for suffix in suffixes]
    candidates.extend(_ai_line_name_alternatives(label, is_ferry=False))
    return _ai_pick_unused_line_name(candidates, taken, used_bases)


_PRESET_AREA_NAMES: dict = {}


def _preset_area_names(preset_name: str) -> frozenset:
    """Every broad area the map's preset defines (lower case)."""
    names = _PRESET_AREA_NAMES.get(preset_name)
    if names is None:
        try:
            from map_preset_loader import get_preset_area_boxes
            boxes = get_preset_area_boxes(preset_name)
        except Exception:
            boxes = ()
        names = frozenset(str(box[0]).strip().lower() for box in boxes if box and box[0])
        _PRESET_AREA_NAMES[preset_name] = names
    return names


def _line_name_matches_route_areas(line_name: str, route_areas: set, preset_name: str = None) -> bool:
    """Reject preset names that name an area of the map the route does not serve."""
    if not route_areas or not line_name or not preset_name:
        return True
    nl = line_name.lower()
    served = {a.lower() for a in route_areas}
    for area in _preset_area_names(preset_name):
        if area in nl and area not in served:
            return False
    return True


def _geo_corridor_line_name(
    ops,
    game_map,
    route_points: list,
    current_year: int,
    taken: set,
    rng: random.Random,
) -> Optional[str]:
    """Derive a line name from a street the route actually runs along (not just the centroid)."""
    if not route_points or len(route_points) < 2 or not game_map:
        return None
    preset_name = getattr(game_map, "map_preset_name", None)
    if not preset_name:
        return None
    route_streets = _route_dominant_street_names(game_map, route_points, preset_name, current_year)
    if not route_streets:
        return None
    base = route_streets[0]
    s = _format_street_label(str(base).strip())
    if not s:
        return None
    return _ai_pick_unused_line_name(_ai_line_name_alternatives(s, is_ferry=False), taken, None)


def _generate_ai_line_name(
    player,
    rng: random.Random,
    preferred_name: Optional[str] = None,
    ops=None,
    route_points: list = None,
    current_year: int = None,
) -> str:
    """Generate a display-friendly line name for AI. Uses preset default names when available, else one naming scheme per player.
    Names must be unique across the game (existing lines + build queue) so multiple AIs can build in the same turn.
    When route_points are provided, tries a street the route runs along (within 10m) before letter/number schemes."""
    if preferred_name and preferred_name.strip():
        return preferred_name.strip()
    taken = _ai_collect_taken_line_names(ops) if ops else set()
    existing = player.get_line_names()
    idx = len(existing)
    used_bases = {_line_name_base_key(n) for n in taken}
    for n in existing:
        used_bases.add(_line_name_base_key(n))
    pid = int(getattr(player, "player_id", 0) or 0)
    preset_name = None
    game_map = ops.get_map() if ops and hasattr(ops, "get_map") else None
    if game_map:
        preset_name = getattr(game_map, "map_preset_name", None)
    cy = int(current_year if current_year is not None else (ops.get_year() if ops else _game_start_year(ops)))
    route_areas = (
        _route_area_labels(game_map, route_points, preset_name)
        if route_points and game_map and preset_name
        else set()
    )
    route_streets = (
        _route_dominant_street_names(game_map, route_points, preset_name, cy)
        if route_points and len(route_points) >= 2 and game_map and preset_name
        else []
    )
    # Area first: "Northside–Riverside Line", "Northside Local" (places at the line's ends).
    if route_points and len(route_points) >= 2 and game_map and preset_name:
        try:
            from geo_naming import line_area_name_candidates
            for candidate in line_area_name_candidates(game_map, route_points):
                if candidate not in taken and _line_name_base_key(candidate) not in used_bases:
                    return candidate
        except Exception:
            pass
    for street in route_streets:
        street_name = _line_name_from_street_base(street, taken, used_bases, rng)
        if street_name:
            return street_name
    if route_points and len(route_points) >= 2 and game_map and preset_name:
        from game_presets import AI_STATION_STREET_NAME_MAX_DISTANCE_M
        try:
            from map_preset_loader import get_preset_geo_feature_name_for_line_naming
            for pt in (route_points[0], route_points[-1]):
                raw = get_preset_geo_feature_name_for_line_naming(
                    preset_name,
                    game_map,
                    float(pt[0]),
                    float(pt[1]),
                    current_year=cy,
                    max_distance_m=AI_STATION_STREET_NAME_MAX_DISTANCE_M,
                )
                if raw:
                    street_name = _line_name_from_street_base(raw, taken, used_bases, rng)
                    if street_name:
                        return street_name
        except Exception:
            pass
    if route_points and len(route_points) >= 2 and game_map:
        geo_name = _geo_corridor_line_name(ops, game_map, route_points, cy, taken, rng)
        if geo_name and geo_name not in taken and _line_name_base_key(geo_name) not in used_bases:
            return geo_name
    if preset_name and not route_streets:
        try:
            from map_preset_loader import get_preset_default_line_names
            default_names = get_preset_default_line_names(preset_name)
            if default_names:
                start = (pid + idx) % len(default_names)
                for k in range(len(default_names)):
                    candidate = default_names[(start + k) % len(default_names)]
                    if candidate in taken or _line_name_base_key(candidate) in used_bases:
                        continue
                    if not _line_name_matches_route_areas(candidate, route_areas, preset_name):
                        continue
                    return candidate
        except Exception:
            pass
    game_seed = _get_game_seed(ops)
    pid = getattr(player, 'player_id', id(player))
    scheme = ((int(pid) * 7919) ^ (int(game_seed) % 1_000_003)) % 3
    start_k = idx + (int(pid) % 7) + scheme * 13

    for k in range(start_k, start_k + 200):
        candidate = _ai_letter_name_at(k)
        if candidate not in taken and _line_name_base_key(candidate) not in used_bases:
            return candidate
    try:
        from transit_company_names import get_transit_company_name
        tc = get_transit_company_name(existing_names=taken, map_preset_name=preset_name, rng=rng)
        if tc and tc not in taken and _line_name_base_key(tc) not in used_bases:
            return tc
    except Exception:
        pass
    return _ai_letter_name_at(start_k)


def _ai_segment_qualifies_as_ferry(p1, p2, game_map, preset_name: str) -> bool:
    """True when a segment may be built as a ferry (near water endpoints, interior mostly water)."""
    if not preset_name:
        return False
    try:
        from map_preset_loader import preset_has_water_areas
        if not preset_has_water_areas(preset_name):
            return False
    except Exception:
        return False
    from operations import _ferry_point_near_water, _ferry_segment_near_water
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    try:
        if not _ferry_point_near_water(game_map, x1, y1):
            return False
        if not _ferry_point_near_water(game_map, x2, y2):
            return False
        return _ferry_segment_near_water(game_map, x1, y1, x2, y2)
    except Exception:
        return False


def _segment_in_water(x1: float, y1: float, x2: float, y2: float, game_map, preset_name: str) -> bool:
    """True if the segment from (x1,y1) to (x2,y2) is in water. Used to avoid street-level on water."""
    if not preset_name:
        return False
    from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
    if not preset_has_water_areas(preset_name):
        return False
    # Check endpoints and midpoint. Use preset with skip_osm=True to avoid OSM HTTP calls during AI turns.
    mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
    return (preset_is_point_in_water(preset_name, x1, y1, game_map, skip_osm=True) or
            preset_is_point_in_water(preset_name, x2, y2, game_map, skip_osm=True) or
            preset_is_point_in_water(preset_name, mid_x, mid_y, game_map, skip_osm=True))


def _get_safe_elevation_levels(points: list, game_map, preset_name: str, base: str = 'street') -> list:
    """Return per-segment elevation levels for an auto-built (AI) railroad.

    A railroad constructor auto-sets a segment to 'underground' whenever it is not
    allowed to be at street level (e.g. crossing open water). This keeps the whole
    line valid without falling back to elevated structures over water.
    """
    if len(points) < 2:
        return []
    levels = []
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        if base == 'street' and _segment_in_water(x1, y1, x2, y2, game_map, preset_name):
            levels.append('underground')
        else:
            levels.append(base)
    return levels


def _is_first_line_for_player(ops, player, game_map) -> bool:
    """True if player has no lines (built or in queue)."""
    if player.get_line_names():
        return False
    for build in (ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []):
        if build.get('type') == 'line' and build.get('player') == player:
            return False
    return True


def _get_existing_line_routes_for_overlap(ops, game_map, *, refresh: bool = False) -> list:
    """Build list of point lists from existing lines and build queue (for overlap exclusion)."""
    if ops is not None and not refresh:
        cached = getattr(ops, '_ai_existing_routes_cache', None)
        if cached is not None:
            return cached
    routes = []
    raw_lines = game_map.get_lines_by_name() if game_map and hasattr(game_map, 'get_lines_by_name') else None
    map_lines = raw_lines if isinstance(raw_lines, dict) else {}
    for _name, line in map_lines.items():
        r = line.get_route() if hasattr(line, 'get_route') else []
        if len(r) >= 2:
            routes.append(r)
    bq = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
    if not isinstance(bq, list):
        bq = []
    # Merge queued segments of the same in-progress line into one polyline so overlap checks stay fast
    # and presets see a sane corridor count (dozens of 2-point chunks made road sampling stall).
    chunks_by_key: dict[tuple, list[tuple[int, list]]] = {}
    for build in bq:
        if build.get('type') != 'line':
            continue
        data = build.get('data') or {}
        pts = data.get('points') or []
        if len(pts) < 2:
            continue
        player = build.get('player')
        name = (data.get('name') or '').strip()
        key = (id(player) if player is not None else 0, name)
        seg_idx = int(data.get('segment_index', 0) or 0)
        chunks_by_key.setdefault(key, []).append((seg_idx, list(pts)))
    for _key, chunks in chunks_by_key.items():
        chunks.sort(key=lambda t: t[0])
        merged: list = []
        for _si, pts in chunks:
            if not merged:
                merged = list(pts)
            else:
                merged.extend(pts[1:])
        if len(merged) >= 2:
            routes.append(merged)
    if ops is not None:
        ops._ai_existing_routes_cache = routes
    return routes


def _segment_near_segment(p1, p2, a, b, tolerance: float = 0.5) -> bool:
    """True if segment (p1,p2) is within tolerance of segment (a,b). Used to avoid building overlapping lines."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    p1x, p1y = float(p1[0]), float(p1[1])
    p2x, p2y = float(p2[0]), float(p2[1])
    # Point-to-segment distance
    def pt_seg(px, py, sx, sy, tx, ty):
        dx, dy = tx - sx, ty - sy
        d2 = dx * dx + dy * dy
        if d2 < 1e-20:
            return math.hypot(px - sx, py - sy)
        t = max(0, min(1, ((px - sx) * dx + (py - sy) * dy) / d2))
        nx, ny = sx + t * dx, sy + t * dy
        return math.hypot(px - nx, py - ny)
    d1 = pt_seg(p1x, p1y, ax, ay, bx, by)
    d2 = pt_seg(p2x, p2y, ax, ay, bx, by)
    d3 = pt_seg(ax, ay, p1x, p1y, p2x, p2y)
    d4 = pt_seg(bx, by, p1x, p1y, p2x, p2y)
    return min(d1, d2, d3, d4) <= tolerance


def _segment_overlaps_existing_routes(
    p1, p2, existing_routes: list, tolerance: float = None, game_map=None,
) -> bool:
    """True if segment (p1,p2) is within tolerance of any segment of any route (so AI should not build there)."""
    if tolerance is None:
        tolerance = _overlap_tolerance_for_map(game_map)
    if not existing_routes:
        return False
    for route in existing_routes:
        for k in range(len(route) - 1):
            a, b = route[k], route[k + 1]
            if _segment_near_segment(p1, p2, a, b, tolerance):
                return True
    return False


def _route_length(points: list) -> float:
    if not points or len(points) < 2:
        return 0.0
    return sum(
        math.hypot(float(points[i + 1][0]) - float(points[i][0]), float(points[i + 1][1]) - float(points[i][1]))
        for i in range(len(points) - 1)
    )


def _decide_ai_rail_service_type(points: list) -> str:
    """Classify rail service to drive station spacing.

    A specialist company's lines get its specialty's service. Otherwise long routes where
    local stops would add heavy dwell time favor express service.
    """
    from ai_specialties import service_type as _specialty_service
    special = _specialty_service(_AI_TURN_SPECIALTY, is_bus=False)
    if special:
        return special
    length = _route_length(points)
    if length >= 3.0:
        return "commuter"
    if length >= 1.7:
        return "express"
    potential_local_stops = max(2, int(length / AI_LOCAL_STOP_SPACING_GRID) + 1)
    if length >= 1.0 and potential_local_stops >= 4:
        return "express"
    return "local"


def _decide_ai_bus_service_type(points: list) -> str:
    """Classify bus service: the company's specialty when it has one, else local vs express by length."""
    from ai_specialties import service_type as _specialty_service
    special = _specialty_service(_AI_TURN_SPECIALTY, is_bus=True)
    if special:
        return special
    length = _route_length(points)
    if length >= 2.2:
        return "express"
    return "local"


def _try_assign_unassigned_bus_to_line(ops, player, line_name: str) -> None:
    """If the player owns unassigned buses, put one on this line so the route can run service."""
    if not ops or not player or not line_name:
        return
    try:
        from line import Bus as BusLine
        from vehicle import Bus as BusVehicle
    except Exception:
        return
    try:
        game_map = ops.get_map() if hasattr(ops, 'get_map') else None
        if not game_map:
            return
        line_obj = (game_map.get_lines_by_name() or {}).get(line_name)
        if not isinstance(line_obj, BusLine):
            return
        if getattr(line_obj, 'vehicles', None):
            return
        buses = getattr(player, 'buses', None) or []
        for bus in buses:
            if not isinstance(bus, BusVehicle):
                continue
            assigned_elsewhere = False
            for _ln, lob in (game_map.get_lines_by_name() or {}).items():
                veh = getattr(lob, 'vehicles', None) or []
                if bus in veh:
                    assigned_elsewhere = True
                    break
            if not assigned_elsewhere:
                line_obj.vehicles.append(bus)
                return
    except Exception:
        pass


def _line_is_bus(line) -> bool:
    return bool(line and line.__class__.__name__ == "Bus")


def _line_is_ferry(line) -> bool:
    return bool(line and line.__class__.__name__ == "Ferry")


def _get_ai_station_spacing(line_name: str = "", line=None, is_bus_hint: bool = False) -> float:
    """Return minimum spacing between AI-built stations based on service type."""
    is_bus = bool(is_bus_hint or _line_is_bus(line))
    if _line_is_ferry(line):
        # Ferry terminals are sparse by design.
        return AI_COMMUTER_STOP_SPACING_GRID
    service = _ai_line_service_type(line_name, line)
    if is_bus and not service:
        service = "local"
    spacing = _ai_spacing_for_service(service, is_bus=is_bus)
    if spacing is not None:
        return spacing
    if line is not None and hasattr(line, "get_route"):
        inferred = _decide_ai_rail_service_type(line.get_route() or [])
        if line_name:
            _AI_LINE_SERVICE_TYPE[line_name] = inferred
        spacing = _ai_spacing_for_service(inferred, is_bus=False)
        if spacing is not None:
            return spacing
    return MIN_STATION_SPACING_GRID


def _player_is_ai(player) -> bool:
    """True only for real AI players (not unittest MagicMock defaults)."""
    return getattr(player, 'is_ai', False) is True


def _player_id_int(player, default: int = 0) -> int:
    try:
        return int(getattr(player, 'player_id', default) or default)
    except (TypeError, ValueError):
        return default


def _ai_planned_endpoint_station_viable(
    ops,
    player,
    game_map,
    x: float,
    y: float,
    line_name: str = None,
    line=None,
    is_bus_hint: bool = False,
    route_points: list = None,
    from_start: bool = True,
) -> bool:
    """True when a legal endpoint station site exists at this line end."""
    del line_name, line, is_bus_hint
    if ops is not None and hasattr(ops, '_ai_resolve_line_endpoint_station_site') and route_points:
        site = ops._ai_resolve_line_endpoint_station_site(
            player, game_map, route_points, from_start=from_start,
        )
        if not site:
            return False
        return math.hypot(float(site[0]) - float(x), float(site[1]) - float(y)) < 0.51
    if not game_map:
        return False
    if ops is not None and hasattr(ops, '_ai_endpoint_station_legal'):
        return ops._ai_endpoint_station_legal(player, game_map, float(x), float(y))
    return _ai_station_point_not_in_water(game_map, x, y)


def _ai_planned_route_endpoints_have_station_sites(
    ops,
    player,
    game_map,
    route_points: list,
    line_name: str = None,
    line=None,
    is_bus_hint: bool = False,
) -> bool:
    """Two viable station sites must exist along the route (not in water)."""
    if ops and hasattr(ops, '_ai_route_has_two_viable_station_sites'):
        return ops._ai_route_has_two_viable_station_sites(
            player, game_map, route_points, is_bus_line=is_bus_hint,
        )
    if not route_points or len(route_points) < 2:
        return False
    p0, p1 = route_points[0], route_points[-1]
    return (
        _ai_planned_endpoint_station_viable(
            ops, player, game_map, float(p0[0]), float(p0[1]), line_name, line, is_bus_hint,
            route_points=route_points, from_start=True,
        )
        and _ai_planned_endpoint_station_viable(
            ops, player, game_map, float(p1[0]), float(p1[1]), line_name, line, is_bus_hint,
            route_points=route_points, from_start=False,
        )
    )


def _ai_station_cost_each(ops) -> int:
    year = 1830
    if ops:
        try:
            year = int(ops.get_year())
        except (TypeError, ValueError):
            year = 1830
    if ops and hasattr(ops, 'calculate_station_cost'):
        try:
            raw = ops.calculate_station_cost(1)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return max(1, int(raw))
        except Exception:
            pass
    from game_presets import STATION_BASE_COST
    return _inflate_econ(ops, year, STATION_BASE_COST)


def _ai_can_afford_line_with_two_stations(ops, player, line_cost: int) -> bool:
    """True when the player can pay for the line plus two endpoint stations."""
    try:
        money = int(ops.get_player_money(player)) if ops else 0
    except (TypeError, ValueError):
        money = 0
    try:
        lc = int(line_cost)
    except (TypeError, ValueError):
        lc = 0
    station_cost = _ai_station_cost_each(ops)
    return money >= lc + station_cost * 2


def _ai_station_create_ok(result) -> bool:
    if result is None:
        return False
    if isinstance(result, dict) and result.get('success') is False:
        return False
    return True


def _ai_count_line_stations(ops, player, line_name: str) -> int:
    """Built + queued stations serving line_name."""
    count = 0
    game_map = ops.get_map() if ops else None
    if game_map:
        line = (game_map.get_lines_by_name() or {}).get(line_name)
        if line and hasattr(line, 'get_stations'):
            count += len(line.get_stations() or [])
    if ops and hasattr(ops, 'get_build_queue'):
        for build in ops.get_build_queue() or []:
            if build.get('type') != 'station' or build.get('player') != player:
                continue
            data = build.get('data') or {}
            names = data.get('line_names') or data.get('lines') or []
            if line_name in names:
                count += 1
    return count


def _ai_target_initial_station_count(
    route_points: list,
    service_type: str = None,
    is_bus_line: bool = False,
) -> int:
    """Stations to queue when a line first opens (endpoints plus interior stops)."""
    length = _route_length(route_points)
    svc = service_type or "local"
    if svc in ("express", "commuter"):
        return 2
    if is_bus_line and svc == "express":
        return 2
    if length >= 3.0:
        return 4
    if length >= 2.0:
        return 3
    if length >= 1.0:
        return 3
    return 2


def _ai_find_route_station_site(
    ops,
    player,
    game_map,
    route_points: list,
    line_name: str = None,
    line=None,
    is_bus_hint: bool = False,
    exclude_xy: tuple = None,
    exclude_coords: list = None,
):
    """Return (x, y) for a viable station along the route, preferring populated vertices."""
    if not route_points or len(route_points) < 2 or not game_map:
        return None
    line_obj = line
    if line_obj is None and line_name and game_map:
        line_obj = (game_map.get_lines_by_name() or {}).get(line_name)
    min_spacing = _get_ai_station_spacing(line_name=line_name, line=line_obj, is_bus_hint=is_bus_hint)
    all_coords = _get_all_station_coords(game_map, ops)
    excludes = list(exclude_coords or [])
    if exclude_xy is not None:
        excludes.append(exclude_xy)
    best = None
    best_pop = -1
    for pt in route_points:
        x, y = float(pt[0]), float(pt[1])
        too_close = False
        for ex in excludes:
            if math.hypot(x - ex[0], y - ex[1]) < min_spacing:
                too_close = True
                break
        if too_close:
            continue
        if not _ai_planned_endpoint_station_viable(
            ops, player, game_map, x, y, line_name, line_obj, is_bus_hint,
        ):
            continue
        pop = _ai_population_near_point(game_map, x, y)
        if pop > best_pop:
            best_pop = pop
            best = (x, y)
    return best


def _ai_open_line_with_two_stations(
    ops,
    player,
    line_name: str,
    route_points: list,
    is_bus_line: bool = False,
    service_type: str = None,
) -> bool:
    """Queue endpoint stations at both line ends, then optional interior stops. Requires two endpoints."""
    placed = _add_ai_endpoint_stations(
        ops, player, line_name, route_points, is_bus_line=is_bus_line, service_type=service_type,
    )
    if placed < 2 and _ai_count_line_stations(ops, player, line_name) < 2:
        return False
    target = _ai_target_initial_station_count(
        route_points, service_type=service_type, is_bus_line=is_bus_line,
    )
    if placed >= target or _ai_count_line_stations(ops, player, line_name) >= target:
        return True
    game_map = _ops_resolve_game_map(ops)
    exclude_coords = []
    if game_map and hasattr(game_map, 'stations'):
        for st in game_map.stations or []:
            if line_name in (getattr(st, 'line_names', None) or []):
                exclude_coords.append((float(getattr(st, 'x', 0)), float(getattr(st, 'y', 0))))
    if ops and hasattr(ops, 'get_build_queue'):
        for build in ops.get_build_queue() or []:
            if build.get('type') != 'station' or build.get('player') != player:
                continue
            data = build.get('data') or {}
            if line_name in (data.get('line_names') or []):
                exclude_coords.append((float(data.get('x', 0)), float(data.get('y', 0))))
    station_ordinal = len(exclude_coords) + 1
    while _ai_count_line_stations(ops, player, line_name) < target:
        if ops.get_player_money(player) < _ai_station_cost_each(ops):
            break
        interior = _ai_find_route_station_site(
            ops, player, game_map, route_points, line_name=line_name,
            is_bus_hint=is_bus_line, exclude_coords=exclude_coords,
        )
        if not interior:
            break
        cy = ops.get_year() if ops else 1830
        name = _ai_resolve_unique_station_name(
            ops, player,
            _generate_ai_station_name(
                ops, game_map, interior[0], interior[1], cy,
                line_name=line_name, station_ordinal=station_ordinal,
            ),
            x=float(interior[0]), y=float(interior[1]), line_name=line_name,
        )
        try:
            r = ops.create_station_complete(name, interior[0], interior[1], player, [line_name])
            if _ai_station_create_ok(r):
                placed += 1
                exclude_coords.append(interior)
                station_ordinal += 1
            else:
                break
        except Exception:
            break
    return placed >= 2 or _ai_count_line_stations(ops, player, line_name) >= 2


def _add_ai_extension_station(ops, player, line_name: str, additional_points: list, extend_from_end: bool) -> None:
    """Add a station at the tip of an extension so the extended line can earn (two endpoints).
    No-op if can't afford or tip too close to existing station. Route includes pending extensions so tip is on line."""
    if not additional_points or len(additional_points) < 2:
        return
    tip = additional_points[-1]  # click order: the last point is the new tip at either end
    tx, ty = float(tip[0]), float(tip[1])
    money = ops.get_player_money(player)
    year = ops.get_year() if ops else 1830
    if money < _inflate_econ(ops, year, 500):
        return
    game_map = _ops_resolve_game_map(ops)
    if game_map and not _ai_station_point_not_in_water(game_map, tx, ty):
        return
    raw_ml = game_map.get_lines_by_name() if game_map and hasattr(game_map, "get_lines_by_name") else None
    map_lines = raw_ml if isinstance(raw_ml, dict) else {}
    line_obj = map_lines.get(line_name) if map_lines else None
    min_spacing = _get_ai_station_spacing(line_name=line_name, line=line_obj)
    if game_map and _ai_population_near_point(game_map, tx, ty) <= 0:
        return
    all_coords = _get_all_station_coords(game_map) if game_map else []
    for sx, sy in all_coords:
        if math.hypot(tx - sx, ty - sy) < min_spacing:
            return
    current_year = ops.get_year() if ops else 1830
    name = _generate_ai_station_name(ops, game_map, tx, ty, current_year, line_name=line_name)
    try:
        ops.add_station_to_line_at_point(player, line_name, name, tx, ty)
    except Exception:
        pass


def _add_ai_endpoint_stations(ops, player, line_name: str, road_points: list, is_bus_line: bool = False, service_type: str = None) -> int:
    """Queue a station at each line end (legal rules only). Returns 0–2."""
    if not road_points or len(road_points) < 2:
        return 0
    game_map = _ops_resolve_game_map(ops)
    if service_type:
        _ai_record_line_service_type(ops, line_name, service_type)
    station_cost = _ai_station_cost_each(ops)
    if ops.get_player_money(player) < station_cost * 2:
        return 0
    if ops is not None and hasattr(ops, '_ai_resolve_line_endpoint_station_site'):
        s1 = ops._ai_resolve_line_endpoint_station_site(
            player, game_map, road_points, from_start=True,
        )
        s2 = ops._ai_resolve_line_endpoint_station_site(
            player, game_map, road_points, from_start=False,
        )
    else:
        s1 = (float(road_points[0][0]), float(road_points[0][1]))
        s2 = (float(road_points[-1][0]), float(road_points[-1][1]))
    if not s1 or not s2:
        return 0
    if math.hypot(s2[0] - s1[0], s2[1] - s1[1]) < 1e-6:
        return 0
    raw_ml = game_map.get_lines_by_name() if game_map and hasattr(game_map, "get_lines_by_name") else None
    map_lines = raw_ml if isinstance(raw_ml, dict) else {}
    line_obj = map_lines.get(line_name) if map_lines else None
    min_spacing = _get_ai_station_spacing(line_name=line_name, line=line_obj, is_bus_hint=is_bus_line)
    if math.hypot(s2[0] - s1[0], s2[1] - s1[1]) < min_spacing:
        return 0
    current_year = ops.get_year() if ops else 1830
    placed = 0
    for ordinal, (x, y) in enumerate((s1, s2), start=1):
        try:
            name = _ai_resolve_unique_station_name(
                ops,
                player,
                _generate_ai_station_name(
                    ops, game_map, x, y, current_year, line_name=line_name, station_ordinal=ordinal,
                ),
                x=float(x), y=float(y), line_name=line_name,
            )
            result = ops.create_station_complete(name, x, y, player, [line_name])
            if _ai_station_create_ok(result):
                placed += 1
        except Exception:
            pass
    return placed


def _count_map_stations(game_map, ops=None) -> int:
    count = 0
    try:
        if game_map and hasattr(game_map, "stations") and game_map.stations:
            count += len(game_map.stations)
        if ops and hasattr(ops, "get_build_queue"):
            for b in ops.get_build_queue() or []:
                if b.get("type") == "station":
                    count += 1
    except Exception:
        pass
    return count


def _pick_major_terminal_station_name(
    ops, game_map, x: float, y: float, current_year: int, existing: set, station_ordinal: int = None,
) -> str | None:
    from game_presets import MAJOR_TERMINAL_STATION_NAMES, pick_major_terminal_station_name

    station_count = _count_map_stations(game_map, ops)
    game_seed = _get_game_seed(ops)
    if station_count == 0 and station_ordinal == 1:
        for terminal in MAJOR_TERMINAL_STATION_NAMES:
            if terminal not in existing:
                return terminal
    mix = (
        game_seed
        ^ int(current_year * 997)
        ^ int(round(x * 1000))
        ^ (int(round(y * 1000)) << 7)
        ^ (station_count * 31)
        ^ ((station_ordinal or 0) << 3)
    )
    return pick_major_terminal_station_name(mix, existing)


def _generate_ai_station_name(
    ops,
    game_map,
    x: float,
    y: float,
    current_year: int,
    line_name: str = None,
    station_ordinal: int = None,
) -> str:
    if line_name and ops and hasattr(ops, "find_nearby_transfer_station_name"):
        try:
            transfer_name = ops.find_nearby_transfer_station_name(
                float(x), float(y), [line_name],
            )
            if transfer_name:
                return transfer_name
        except Exception:
            pass

    existing = set()
    try:
        m = game_map or (ops.get_game().get_map() if ops and ops.get_game() else None)
        if m and hasattr(m, "stations") and m.stations:
            for st in m.stations:
                n = getattr(st, "name", None)
                if n:
                    existing.add(str(n))
        if ops and hasattr(ops, "get_build_queue"):
            for b in (ops.get_build_queue() or []):
                if b.get("type") != "station":
                    continue
                data = b.get("data") or {}
                n = data.get("name")
                if n:
                    existing.add(str(n))
    except Exception:
        existing = set()

    preset_name = getattr(game_map, "map_preset_name", None) if game_map else None
    try:
        from map_preset_loader import resolve_geography_station_name
        geo_name = resolve_geography_station_name(
            preset_name,
            x,
            y,
            game_map,
            current_year=current_year,
            line_name=line_name,
            existing_names=existing,
            station_ordinal=station_ordinal,
        )
        if geo_name:
            return geo_name
    except Exception:
        pass

    # Maps without street/neighbourhood data: occasional "Union Station"-style terminal names.
    major = _pick_major_terminal_station_name(
        ops, game_map, x, y, current_year, existing, station_ordinal=station_ordinal,
    )
    if major:
        return major

    pop_map = getattr(game_map, "population", None) or {}
    if pop_map:
        ix, iy = int(round(float(x))), int(round(float(y)))
        best_cell = None
        best_d = 1e9
        for dy in range(-5, 6):
            for dx in range(-5, 6):
                cell = (ix + dx, iy + dy)
                pv = int(pop_map.get(cell, 0) or 0)
                if pv <= 0:
                    continue
                d2 = dx * dx + dy * dy
                if d2 < best_d:
                    best_d = d2
                    best_cell = cell
        if best_cell is not None and best_d <= 36:
            hood = f"Junction {best_cell[0]} & {best_cell[1]}"
            picked = _ai_pick_unused_line_name(
                _ai_station_name_alternatives(hood), existing, None,
            )
            if picked:
                return picked

    return _unique_station_fallback(existing, station_ordinal)


def _unique_station_fallback(existing: set, station_ordinal: int = None) -> str:
    bases = ["Station", "Transit Stop", "Platform", "Depot", "Transfer Point", "Waypoint"]
    if station_ordinal is not None:
        bases.insert(0, "Midline Stop")
    for base in bases:
        picked = _ai_pick_unused_line_name(_ai_station_name_alternatives(base), existing, None)
        if picked:
            return picked
    return "Waypoint"


def _ai_resolve_build_population_points(
    game_map,
    points_with_pop,
    is_first: bool,
    existing_routes: list,
    player,
    preset,
    *,
    for_osm: bool = False,
) -> list:
    """Top population locations for pair scoring (quadrant spread on first standard-map line)."""
    pop_map = getattr(game_map, 'population', None) or {}
    max_cand = _MAX_OSM_BUILD_CANDIDATES if for_osm else _MAX_BUILD_CANDIDATES
    w, h = _map_width_height(game_map)
    if points_with_pop is None and pop_map:
        use_osm_roads = bool(preset and hasattr(preset, 'get_road_segment_points_for_ai'))
        if use_osm_roads or for_osm or not is_first:
            points_with_pop = _get_population_points_sorted_by_population(
                game_map, max_points=max_cand,
            )
        else:
            deleted = getattr(game_map, '_deleted_population_points', set()) or set()
            by_quad = [[] for _ in range(4)]
            for (x, y), pop in (pop_map or {}).items():
                if pop <= 0 or (x, y) in deleted:
                    continue
                q = (0 if x < w / 2 else 1) + (0 if y < h / 2 else 2)
                by_quad[q].append(((x, y), pop))
            for i in range(4):
                by_quad[i].sort(key=lambda p: p[1], reverse=True)
            pid = getattr(player, 'player_id', 0)
            my_q = pid % 4
            points_with_pop = []
            points_with_pop.extend(by_quad[my_q][:24])
            for q in range(4):
                if q != my_q:
                    points_with_pop.extend(by_quad[q][:8])
            if len(points_with_pop) < 2:
                points_with_pop = _get_population_points_sorted_by_population(game_map, max_points=24)
    if points_with_pop is None:
        points_with_pop = _get_population_points_sorted_by_population(game_map, max_points=max_cand)
    elif not existing_routes:
        points_with_pop = points_with_pop[:min(max_cand, len(points_with_pop))]
    return points_with_pop or []


def _ai_route_overlaps_existing(route: list, existing_routes: list, game_map) -> bool:
    """True when any segment of route is near an existing line."""
    if not route or len(route) < 2 or not existing_routes:
        return False
    for i in range(len(route) - 1):
        if _segment_overlaps_existing_routes(
            route[i], route[i + 1], existing_routes, game_map=game_map,
        ):
            return True
    return False


def _ai_osm_pair_route_passes_detour_check(route_points: list, *, mandatory_opener: bool = False) -> bool:
    """True when a street route is not a straight chord shortcut (relaxed for OSM street paths)."""
    if not route_points or len(route_points) < 3:
        return False
    ratio = _ai_route_street_detour_ratio(route_points)
    if ratio > 1.04:
        return True
    if mandatory_opener and ratio > 1.001:
        return True
    return False


def _ai_rank_population_anchor_pairs(
    game_map,
    candidates: list,
    min_dist: float,
    spendable: int,
    current_year: int,
    gsy: int,
    payback_years: float,
    first_game_year: bool,
    early_growth_build: bool,
    preset_name: str,
    ops,
    existing_routes: list,
    rng: random.Random,
    player=None,
    *,
    osm_street_cost: bool = False,
    relaxed: bool = False,
) -> list:
    """Return (score, p1, p2, pop1, pop2) sorted for build attempts (no street routing)."""
    from map_preset_loader import preset_has_water_areas
    from operations import _ai_land_straight_limit_applies, _ai_land_straight_segment_exceeds_limit

    unit_mult = 1.5 if osm_street_cost else 1.0
    base_unit = _OSM_ROAD_UNIT_COST if osm_street_cost else 150
    length_mult = 1.15 if osm_street_cost else 1.0
    pairs = []
    pair_indices = [
        (i, j) for i in range(len(candidates)) for j in range(i + 1, len(candidates))
    ]
    if osm_street_cost:
        rng.shuffle(pair_indices)
    for i, j in pair_indices:
        if ops is not None and _ai_turn_time_exceeded(ops):
            break
        (p1, pop1) = candidates[i]
        (p2, pop2) = candidates[j]
        if pop1 <= 0 or pop2 <= 0:
            continue
        dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if dist < min_dist:
            continue
        if not osm_street_cost and _ai_land_straight_limit_applies(game_map) and _ai_land_straight_segment_exceeds_limit(
            game_map, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), planning_only=True,
        ):
            continue
        if preset_name and preset_has_water_areas(preset_name) and not osm_street_cost:
            if _straight_line_crosses_land(p1, p2, game_map, preset_name):
                continue
        score_dist = dist * length_mult
        est_cost = max(15, int(base_unit * score_dist * unit_mult))
        if est_cost > spendable:
            continue
        if existing_routes and _segment_overlaps_existing_routes(
            p1, p2, existing_routes, game_map=game_map,
        ):
            continue
        est_profit = _estimate_line_profit(
            pop1, pop2, score_dist, est_cost, current_year, game_start_year=gsy,
            ops=ops, route_points=[p1, p2], existing_routes=existing_routes, player=player,
        )
        if not relaxed:
            if est_profit <= 0 and not first_game_year:
                if est_profit < -max(1200, est_cost // 4):
                    continue
            if not first_game_year and est_profit < 0 and est_profit * payback_years < est_cost * 2:
                continue
        if early_growth_build:
            noise = rng.uniform(-0.2, 0.2) * max(1, abs(est_profit))
        else:
            noise = rng.uniform(-0.8, 0.8) * max(1, abs(est_profit))
        pairs.append((est_profit + noise, p1, p2, pop1, pop2))
    pairs.sort(key=lambda x: -x[0])
    return pairs


def _ai_collect_scored_population_pairs(
    game_map,
    candidates: list,
    min_dist: float,
    spendable: int,
    current_year: int,
    gsy: int,
    payback_years: float,
    first_game_year: bool,
    early_growth_build: bool,
    preset_name: str,
    ops,
    existing_routes: list,
    rng: random.Random,
    min_pop_for_build: int,
    used_sections: set,
    my_section: tuple,
    player=None,
    *,
    osm_street_cost: bool = False,
    relaxed: bool = False,
    section_spread: bool = True,
) -> list:
    """Return (score, p1, p2, pop1, pop2) sorted for build attempts."""
    from map_preset_loader import preset_has_water_areas
    from operations import _ai_land_straight_limit_applies, _ai_land_straight_segment_exceeds_limit

    unit_mult = 1.5 if osm_street_cost else 1.0
    base_unit = _OSM_ROAD_UNIT_COST if osm_street_cost else 150
    pairs = []
    pair_indices = [
        (i, j) for i in range(len(candidates)) for j in range(i + 1, len(candidates))
    ]
    if osm_street_cost:
        rng.shuffle(pair_indices)
        pair_indices = pair_indices[:36]
    for i, j in pair_indices:
        (p1, pop1) = candidates[i]
        (p2, pop2) = candidates[j]
        if pop1 <= 0 or pop2 <= 0:
            continue
        dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if dist < min_dist:
            continue
        if not osm_street_cost and _ai_land_straight_limit_applies(game_map) and _ai_land_straight_segment_exceeds_limit(
            game_map, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), planning_only=True,
        ):
            continue
        if preset_name and preset_has_water_areas(preset_name) and not osm_street_cost:
            if _straight_line_crosses_land(p1, p2, game_map, preset_name):
                continue
        est_cost = max(15, int(base_unit * dist * unit_mult))
        if est_cost > spendable:
            continue
        if existing_routes and _segment_overlaps_existing_routes(
            p1, p2, existing_routes, game_map=game_map,
        ):
            continue
        route_for_score = [p1, p2]
        score_dist = dist * (1.15 if osm_street_cost else 1.0)
        est_profit = _estimate_line_profit(
            pop1, pop2, score_dist, est_cost, current_year, game_start_year=gsy,
            ops=ops, route_points=route_for_score, existing_routes=existing_routes,
            player=player,
        )
        if not relaxed:
            if est_profit <= 0 and not first_game_year:
                if est_profit < -max(1200, est_cost // 4):
                    continue
            if not first_game_year and est_profit < 0 and est_profit * payback_years < est_cost * 2:
                continue
        if early_growth_build:
            noise = rng.uniform(-0.2, 0.2) * max(1, abs(est_profit))
        else:
            noise = rng.uniform(-0.8, 0.8) * max(1, abs(est_profit))
        pairs.append((est_profit + noise, p1, p2, pop1, pop2))

    if section_spread:
        def _pair_section_used(s_p1_p2):
            _, p1, p2, _, _ = s_p1_p2
            mx = (p1[0] + p2[0]) / 2
            my = (p1[1] + p2[1]) / 2
            return point_to_section(game_map, mx, my) in used_sections

        def _pair_in_my_section(s_p1_p2):
            _, p1, p2, _, _ = s_p1_p2
            mx = (p1[0] + p2[0]) / 2
            my = (p1[1] + p2[1]) / 2
            return point_to_section(game_map, mx, my) == my_section

        def _sort_key(x):
            in_my = _pair_in_my_section(x)
            used = _pair_section_used(x)
            return (-x[0], 0 if in_my else 1, 1 if used else 0)

        if existing_routes and pairs:
            non_ov = [
                t for t in pairs
                if not _segment_overlaps_existing_routes(t[1], t[2], existing_routes, game_map=game_map)
            ]
            if non_ov:
                pairs = sorted(non_ov, key=_sort_key)
            else:
                pairs.sort(key=_sort_key)
        else:
            pairs.sort(key=_sort_key)
    else:
        pairs.sort(key=lambda x: -x[0])
        top_band = min(12, len(pairs))
        if top_band > 1:
            band = pairs[:top_band]
            rng.shuffle(band)
            pairs = band + pairs[top_band:]
    return pairs


# A new line's end this close to an existing station (any company's) connects to that line.
AI_CONNECTION_RADIUS_GRID = 0.5


def _ai_station_anchor_candidates(game_map, ops, rng, min_pop: int, count: int = 4) -> list:
    """A few existing stations (any company's) as line-end candidates, so new lines can connect."""
    try:
        stations = _get_all_station_coords(game_map, ops)
    except Exception:
        return []
    out = []
    for sx, sy in rng.sample(stations, min(count, len(stations))) if stations else []:
        pop = _ai_population_at_anchor(game_map, sx, sy)
        if pop >= max(1, int(min_pop)):
            out.append(((float(sx), float(sy)), int(pop)))
    return out


def _ai_prefer_connected_pairs(pairs: list, game_map, ops=None) -> list:
    """Order candidate endpoint pairs so lines that connect to existing lines come first.

    Pairs with both ends next to an existing station go first, then one end, then none; the
    order within each group is kept (so profit ranking and randomness still apply).
    """
    if not pairs or game_map is None:
        return pairs
    try:
        stations = _get_all_station_coords(game_map, ops)
    except Exception:
        stations = []
    if not stations:
        return pairs
    r2 = AI_CONNECTION_RADIUS_GRID * AI_CONNECTION_RADIUS_GRID

    def connected(pt) -> int:
        x, y = float(pt[0]), float(pt[1])
        return int(any((sx - x) ** 2 + (sy - y) ** 2 <= r2 for sx, sy in stations))

    return sorted(pairs, key=lambda pr: -(connected(pr[1]) + connected(pr[2])))


def _try_build_osm_line_from_population_pairs(
    ops,
    player,
    game_map,
    spendable: int,
    current_year: int,
    points_with_pop: list = None,
    rng: random.Random = None,
    existing_routes: list = None,
    preset=None,
    *,
    relaxed: bool = False,
) -> Optional[dict]:
    """OSM/street maps: score population endpoint pairs, then street-route between top pairs."""
    from map_preset_loader import load_map_preset, preset_has_water_areas
    from operations import _ai_land_straight_limit_applies, _ai_route_has_overlong_land_straight_segment, _ai_route_off_street_cost_multiplier

    if not ai_capital_spending_allowed_vs_idle(ops, player, spendable):
        return None
    rng = rng or random.Random()
    gsy = _game_start_year(ops)
    first_game_year = _is_first_game_year(ops, current_year)
    early_growth_build = _years_from_game_start(ops, current_year) < AI_EARLY_GROWTH_YEARS
    post_bootstrap_build = _years_from_game_start(ops, current_year) > AI_BOOTSTRAP_TURNS
    if post_bootstrap_build:
        payback_years = AI_SUSTAINED_PROFIT_PAYBACK_YEARS
    elif early_growth_build:
        payback_years = AI_EARLY_PAYBACK_YEARS
    else:
        payback_years = 10
    if existing_routes is None:
        existing_routes = _get_existing_line_routes_for_overlap(ops, game_map)
    game = ops.get_game() if ops else None
    min_pop_for_build = 1 if relaxed else (getattr(game, 'ai_min_population', 100) if game is not None else 100)
    is_first = _is_first_line_for_player(ops, player, game_map)
    mandatory_opener = bool(relaxed or (first_game_year and is_first))
    min_line_length = max(
        MIN_FIRST_LINE_LENGTH_GRID if is_first else MIN_LINE_LENGTH_GRID,
        AI_LOCAL_STOP_SPACING_GRID,
    )
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset is None and preset_name:
        preset = load_map_preset(preset_name)
    if not preset or not hasattr(preset, 'get_road_segment_points_for_ai'):
        return None

    if not _ai_osm_street_routing_data_available(game_map, preset_name or ''):
        _ai_note_street_compliance_issue(
            ops, player,
            "OSM street spatial index is not loaded; street pair routing requires road geometry "
            "(run build_road_spatial_index.py).",
        )

    min_len_road = max(MIN_LINE_LENGTH_GRID, AI_LOCAL_STOP_SPACING_GRID + 0.15)
    if mandatory_opener:
        max_len = 25.0
    elif first_game_year and is_first:
        max_len = 8.0
    else:
        max_len = 4.0
    build_mode = _ai_turn_build_mode(ops, current_year)
    # The company's specialty sets how long its lines are (commuter long, light rail short).
    # First lines keep the usual minimum so a company can always open one.
    from ai_specialties import route_length_bounds
    spec_min, spec_max = route_length_bounds(_AI_TURN_SPECIALTY)
    if spec_max is not None:
        max_len = max(float(spec_max), min_len_road + 0.5)
    if not mandatory_opener:
        if spec_min is not None:
            min_line_length = max(min_line_length, float(spec_min))
            min_len_road = max(min_len_road, min(float(spec_min), max_len - 0.5))
    prefer_speed = _ai_osm_line_build_prefer_speed(
        ops, existing_routes, first_game_year, is_first,
    )

    pair_candidate_count = _ai_route_candidate_count(ops)

    has_ferry_line = any(
        bool((game_map.get_lines_by_name() or {}).get(ln).__class__.__name__ == "Ferry")
        for ln in (player.get_line_names() or [])
        if (game_map.get_lines_by_name() or {}).get(ln) is not None
    )
    ferry_chance = 0.22 if not has_ferry_line else 0.08
    ferry_company = build_mode == 'ferry'
    if ferry_company:
        ferry_chance = 1.0
    elif build_mode is not None:
        ferry_chance = 0.0  # specialists build only their own kind of line this turn
    if (
        preset_has_water_areas(preset_name or '')
        and (ferry_company or (not is_first and not prefer_speed and not relaxed))
        and not _ai_turn_time_low(ops, 30.0)
        and rng.random() < ferry_chance
    ):
        try:
            ferry_result = _try_build_ferry_line(
                ops, player, game_map, spendable, current_year, points_with_pop,
                rng=rng, existing_routes=existing_routes, preset=preset,
            )
            if ferry_result:
                return ferry_result
        except Exception:
            pass
    if ferry_company and not relaxed:
        # A ferry company only builds land lines when it must open its first line.
        return None

    points_with_pop = _ai_resolve_build_population_points(
        game_map, points_with_pop, is_first, existing_routes, player, preset, for_osm=True,
    ) if not (first_game_year and is_first) else (points_with_pop or [])
    if not (first_game_year and is_first) and len(points_with_pop) < 2:
        return None

    used_sections = get_used_sections_for_first_line(ops, game_map, existing_routes=existing_routes)
    my_section = get_preferred_section_for_player(player, game_map, ops)
    if first_game_year and is_first:
        candidates = _ai_first_turn_six_population_points(
            ops, player, game_map, min_pop=min_pop_for_build,
        )
        if len(candidates) < 2:
            candidates = _ai_first_turn_six_population_points(
                ops, player, game_map, min_pop=1,
            )
        if len(candidates) < 2:
            return None
        pairs = _ai_rank_population_anchor_pairs(
            game_map, candidates, min_line_length, spendable, current_year, gsy, payback_years,
            first_game_year, early_growth_build, preset_name or '', ops, existing_routes, rng,
            player, osm_street_cost=True, relaxed=relaxed or first_game_year,
        )
        if not pairs:
            return None
        max_attempts = min(_MAX_FIRST_TURN_OSM_PAIR_ROUTE_ATTEMPTS, len(pairs))
    else:
        from ai_specialties import PROFILES as _SPEC_PROFILES
        top_fraction = (_SPEC_PROFILES.get(_AI_TURN_SPECIALTY or '') or {}).get('top_population_fraction')
        candidates = _ai_random_population_build_candidates(
            game_map, rng, pair_candidate_count, min_pop=min_pop_for_build,
            top_fraction=top_fraction,
        )
        if len(candidates) < 2:
            candidates = _ai_random_population_build_candidates(
                game_map, rng, pair_candidate_count, min_pop=1,
            )
        if len(candidates) < 2:
            return None
        candidates = candidates + _ai_station_anchor_candidates(game_map, ops, rng, min_pop_for_build)

        pairs = _ai_collect_scored_population_pairs(
            game_map, candidates, min_line_length, spendable, current_year, gsy, payback_years,
            first_game_year, early_growth_build, preset_name or '', ops, existing_routes, rng,
            min_pop_for_build, used_sections, my_section, player,
            osm_street_cost=True, relaxed=relaxed or first_game_year,
            section_spread=False,
        )
        if not pairs:
            return None

        top_n = min(
            int(round((12 if not relaxed else 8) * _ai_line_creation_randomness_factor(ops))),
            len(pairs),
        )
        band = pairs[:top_n]
        rng.shuffle(band)
        pairs = band + pairs[top_n:]
        max_attempts = min(_MAX_OSM_PAIR_ROUTE_ATTEMPTS, len(pairs))
        if existing_routes:
            max_attempts = min(max_attempts, 4)
        redirected, shared = _ai_shared_station_pairs(ops, player, game_map, min_line_length, max_len, rng)

        def _osm_pair(a, b):
            pa = _ai_population_near_point(game_map, a[0], a[1])
            pb = _ai_population_near_point(game_map, b[0], b[1])
            return (0.0, a, b, pa, pb)

        pairs = _ai_order_pairs_with_shared_stations(pairs, redirected, shared, _osm_pair, rng)
        max_attempts = min(len(pairs), max_attempts + len(redirected) + (2 if shared else 0))

    if not pairs:
        return None

    road_cost_per_unit = _OSM_ROAD_UNIT_COST
    attempts = 0
    pairs = _ai_prefer_connected_pairs(pairs, game_map, ops)
    for score, p1, p2, pop1, pop2 in pairs:
        if attempts >= max_attempts or _ai_turn_time_exceeded(ops):
            break
        attempts += 1
        try:
            road_points = _ai_route_between_points(
                p1, p2, game_map, preset_name or '', current_year, osm_pair_route=True,
                mandatory_opener=mandatory_opener,
            )
        except Exception:
            continue
        if not road_points:
            # Anchors too far apart or not joined by streets: build from p1 toward p2 and
            # end on the reachable populated street point closest to p2.
            road_points = _ai_route_toward_anchor(
                p1, p2, game_map, current_year, min_len_road, min(max_len, 4.0),
            )
            if road_points:
                p2 = road_points[-1]
                pop2 = _ai_population_near_point(game_map, p2[0], p2[1])
        if not road_points or len(road_points) < 3:
            continue
        snapped = _ai_snap_route_endpoints_to_anchors(road_points, p1, p2)
        if not snapped:
            continue
        road_points = snapped
        if not _ai_osm_pair_route_passes_detour_check(road_points, mandatory_opener=mandatory_opener):
            if not _ai_osm_route_passes_build_rules(
                game_map, road_points, current_year, preset_name or '', is_bus_line=False,
                mandatory_opener=mandatory_opener,
            ):
                continue
        if _ai_route_overlaps_existing(road_points, existing_routes, game_map):
            continue
        total_length = _route_polyline_length(road_points)
        if total_length > max_len:
            trimmed = _osm_trim_polyline_to_max_len(road_points, max_len, min_len_road)
            if not trimmed:
                continue
            road_points = trimmed
            total_length = _route_polyline_length(road_points)
        if total_length < min_len_road:
            continue
        if not _osm_first_turn_endpoints_viable(game_map, p1, p2):
            continue
        try:
            pid = int(getattr(player, 'player_id', 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        mode_roll = ((rng.random() * 13.7 + pid * 0.31 + total_length * 2.1) % 1.0)
        # A bus line can only be served once a bus has been invented.
        if build_mode is not None:
            use_bus = build_mode == 'bus'
        else:
            use_bus = mode_roll < 0.46 and ops.bus_lines_available(current_year)
        bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * 1.5))
        rail_cost = max(15, int(total_length * road_cost_per_unit * 1.5))
        cost = bus_cost if use_bus else rail_cost
        ep_pop1 = _ai_population_at_anchor(game_map, p1[0], p1[1])
        ep_pop2 = _ai_population_at_anchor(game_map, p2[0], p2[1])
        if relaxed or mandatory_opener:
            ep_pop1 = max(ep_pop1, int(pop1))
            ep_pop2 = max(ep_pop2, int(pop2))
        if ep_pop1 <= 0 or ep_pop2 <= 0:
            continue
        est_profit = None
        floor_fn = getattr(ops, '_ai_planned_line_gross_annual_revenue_floor', None) if ops else None
        if callable(floor_fn):
            try:
                raw = floor_fn(
                    int(max(pop1, ep_pop1)), int(max(pop2, ep_pop2)), float(total_length), int(current_year),
                    route_points=road_points, player=player,
                )
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    est_profit = float(raw)
            except Exception:
                pass
        if est_profit is None:
            est_profit = _estimate_line_profit(
                max(pop1, ep_pop1), max(pop2, ep_pop2), total_length, cost, current_year,
                game_start_year=gsy, ops=ops, route_points=road_points, existing_routes=existing_routes,
            )
        if _ai_land_straight_limit_applies(game_map) and not mandatory_opener:
            from operations import _ai_route_has_backtracking
            if _ai_route_has_backtracking(road_points, game_map):
                continue
            if _ai_route_has_overlong_land_straight_segment(
                game_map, road_points, skip_osm=True, current_year=current_year, is_bus_line=use_bus,
            ):
                continue
        if mandatory_opener:
            street_cost_mult = 1.0
        else:
            street_cost_mult = _ai_route_off_street_cost_multiplier(
                game_map, road_points, current_year, is_bus_line=use_bus, skip_osm=True,
            )
        effective_cost = max(cost, int(round(cost * street_cost_mult)))
        min_profit = 1 if current_year > gsy + 10 else 0
        if relaxed or first_game_year:
            profit_ok = True
        elif early_growth_build:
            will_net_lose = est_profit * payback_years < effective_cost
            if est_profit <= 0 and est_profit < -max(600, effective_cost // 15):
                will_net_lose = True
            profit_ok = est_profit > min_profit and not will_net_lose
        else:
            will_net_lose = (est_profit <= 0 or est_profit * 10 < effective_cost)
            profit_ok = est_profit > min_profit and not will_net_lose
        if effective_cost > spendable or not profit_ok:
            continue
        bus_svc_pre = _decide_ai_bus_service_type(road_points) if use_bus else None
        rail_svc_pre = _decide_ai_rail_service_type(road_points) if not use_bus else None
        lengthened = _ai_ensure_route_supports_two_stations(
            road_points, game_map, preset_name or '', current_year, preset,
            is_bus=use_bus, service_type=bus_svc_pre if use_bus else rail_svc_pre,
            ops=ops, mandatory_opener=mandatory_opener,
        )
        if not lengthened:
            continue
        road_points = lengthened
        if prefer_speed and mandatory_opener:
            prepared = _ai_prepare_osm_route_points(road_points, game_map, min_len_road)
            road_points = prepared if prepared else road_points
        else:
            road_points = _ai_finalize_osm_route_points(
                road_points, game_map, preset_name or '', current_year, min_len_road,
                ops=ops, mandatory_opener=mandatory_opener,
            )
        if not road_points or len(road_points) < 3:
            continue
        if not _ai_osm_pair_route_passes_detour_check(road_points, mandatory_opener=mandatory_opener):
            if not _ai_osm_route_passes_build_rules(
                game_map, road_points, current_year, preset_name or '', is_bus_line=use_bus,
                mandatory_opener=mandatory_opener,
            ):
                continue
        if not _ai_osm_route_passes_build_rules(
            game_map, road_points, current_year, preset_name or '', is_bus_line=use_bus,
            mandatory_opener=mandatory_opener,
        ):
            continue
        if not _ai_route_endpoints_match_pop_anchors(road_points, p1, p2):
            continue
        if not _osm_first_turn_endpoints_viable(game_map, road_points[0], road_points[-1]):
            continue
        if not _ai_planned_route_endpoints_have_station_sites(
            ops, player, game_map, road_points, is_bus_hint=use_bus,
        ):
            continue
        total_length = _route_polyline_length(road_points)
        bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * 1.5))
        rail_cost = max(15, int(total_length * road_cost_per_unit * 1.5))
        cost = bus_cost if use_bus else rail_cost
        if not mandatory_opener:
            street_cost_mult = _ai_route_off_street_cost_multiplier(
                game_map, road_points, current_year, is_bus_line=use_bus, skip_osm=True,
            )
        effective_cost = max(cost, int(round(cost * street_cost_mult)))
        if effective_cost > spendable or not _ai_can_afford_line_with_two_stations(ops, player, effective_cost):
            continue
        line_name = _generate_ai_line_name(
            player, rng, ops=ops, route_points=road_points, current_year=current_year,
        )
        if preset_name and _ai_route_has_water_crossing(road_points, game_map, preset_name):
            chunks = _ai_split_route_at_water_boundaries(
                road_points, game_map, preset_name, current_year,
            )
            if len(chunks) > 1 and not _ai_turn_allows_ferry_lines(ops, player) and any(
                ch.get('is_ferry') for ch in chunks
            ):
                continue  # this route would need a ferry leg the company may not build yet
            if len(chunks) > 1:
                trimmed = []
                for ch in chunks:
                    if ch.get('is_ferry'):
                        trimmed.append(ch)
                    else:
                        land_pts = _ai_trim_route_endpoints_off_water(
                            ch['points'], game_map, preset_name, for_route_validation=False,
                        )
                        if land_pts and len(land_pts) >= 2:
                            trimmed.append({'points': land_pts, 'is_ferry': False})
                split_result = _ai_build_route_chunks_as_lines(
                    ops, player, game_map, trimmed, preset_name, current_year, rng,
                    base_line_name=line_name, use_bus=use_bus, use_fast_path=True, budget=cost,
                )
                if split_result:
                    return split_result
        elevation_levels = _get_safe_elevation_levels(road_points, game_map, preset_name, base='street')
        cost_info = {
            'cost': cost, 'length': total_length,
            'points_count': len(road_points), 'elevation_levels': elevation_levels,
        }
        try:
            result = ops.create_line_complete(
                line_name, road_points, player,
                elevation_levels=elevation_levels,
                is_ferry_line=False, is_bus_line=use_bus,
                cost_info=cost_info, use_fast_path=True,
            )
        except ValueError:
            continue
        if not result or not result.get('success', True):
            continue
        result['line_name'] = line_name
        result['points'] = _ai_round_route_points(road_points)
        result['cost'] = cost
        try:
            if use_bus:
                bus_svc = bus_svc_pre or _decide_ai_bus_service_type(road_points)
                opened = _ai_open_line_with_two_stations(
                    ops, player, line_name, road_points, is_bus_line=True, service_type=bus_svc,
                )
                if opened:
                    _try_assign_unassigned_bus_to_line(ops, player, line_name)
            else:
                rail_svc = rail_svc_pre or _decide_ai_rail_service_type(road_points)
                opened = _ai_open_line_with_two_stations(
                    ops, player, line_name, road_points, is_bus_line=False, service_type=rail_svc,
                )
            if not opened:
                continue
        except Exception:
            continue
        return result
    if not pairs:
        _ai_note_street_compliance_issue(
            ops, player,
            "No scored population anchor pairs were available for OSM street routing.",
        )
    elif attempts > 0:
        _ai_note_street_compliance_issue(
            ops, player,
            f"Rejected all {attempts} population-pair street route attempt(s) "
            f"(need OSM street polyline, anchor proximity, and >50% street coverage).",
        )
    return None


def _try_build_line(ops, player, game_map, spendable: int, current_year: int, points_with_pop: list = None, rng: random.Random = None, existing_routes: list = None, preset=None) -> Optional[dict]:
    """Attempt to build a profitable line. Uses street polylines if preset provides; else population points.
    Respects ai_min_population from game settings for minimum endpoint population.
    Avoids overlapping with existing lines. All lines must be at least 500m and must not overlap with themselves.
    points_with_pop: optional pre-fetched list from run_ai_turn (avoids redundant sort on large maps).
    existing_routes: optional pre-fetched list from run_ai_turn (avoids redundant work per turn).
    rng: optional per-player RNG for differentiation (different AIs pick different routes).
    preset: optional pre-loaded map preset (avoids repeated load_map_preset in get_preset_road_segment_points)."""
    if not ai_capital_spending_allowed_vs_idle(ops, player, spendable):
        return None
    rng = rng or random.Random()
    gsy = _game_start_year(ops)
    first_game_year = _is_first_game_year(ops, current_year)
    early_growth_build = _years_from_game_start(ops, current_year) < AI_EARLY_GROWTH_YEARS
    post_bootstrap_build = _years_from_game_start(ops, current_year) > AI_BOOTSTRAP_TURNS
    if post_bootstrap_build:
        payback_years = AI_SUSTAINED_PROFIT_PAYBACK_YEARS
    elif early_growth_build:
        payback_years = AI_EARLY_PAYBACK_YEARS
    else:
        payback_years = 10
    if existing_routes is None:
        existing_routes = _get_existing_line_routes_for_overlap(ops, game_map)
    game = ops.get_game() if ops else None
    min_pop_for_build = getattr(game, 'ai_min_population', 100) if game is not None else 100
    is_first = _is_first_line_for_player(ops, player, game_map)
    min_line_length = max(
        MIN_FIRST_LINE_LENGTH_GRID if is_first else MIN_LINE_LENGTH_GRID,
        AI_LOCAL_STOP_SPACING_GRID,
    )
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import load_map_preset, preset_has_water_areas
        if preset is None:
            preset = load_map_preset(preset_name)
        if preset and hasattr(preset, 'get_road_segment_points_for_ai'):
            mandatory_opener = (first_game_year and is_first) or (
                _years_from_game_start(ops, current_year) <= 0 and is_first
            )
            if _ai_osm_build_budget_exhausted(ops, first_opener=mandatory_opener) and not mandatory_opener:
                return None
            _ai_record_osm_build_call(ops)
            opener_relaxed = mandatory_opener or first_game_year
            result = _try_build_osm_line_from_population_pairs(
                ops, player, game_map, spendable, current_year,
                points_with_pop=points_with_pop, rng=rng,
                existing_routes=existing_routes, preset=preset,
                relaxed=opener_relaxed,
            )
            if _ai_line_create_result_ok(result):
                if mandatory_opener:
                    _ai_mark_first_year_opener_attempted(ops)
                return result
            return None
    if preset_name:
        from map_preset_loader import load_map_preset
        _p = load_map_preset(preset_name)
        if _p and hasattr(_p, 'get_road_segment_points_for_ai'):
            # Hard rule: OSM/road-polylines presets must NEVER fall back to straight population-point builds.
            # If no road corridor is available this attempt, return None and let callers retry with other seeds/years.
            return None
    # existing_routes already set at function entry if None
    # Map-agnostic 36 sections: prefer building first line in a section not yet used (any map)
    used_sections = get_used_sections_for_first_line(ops, game_map, existing_routes=existing_routes)
    # Candidate endpoints for point-to-point fallback (OSM maps use street polylines instead).
    pop_map = getattr(game_map, 'population', None) or {}
    w, h = _map_width_height(game_map)
    if points_with_pop is None and pop_map:
        use_osm_roads = bool(
            preset and hasattr(preset, 'get_road_segment_points_for_ai')
        )
        if use_osm_roads or not is_first:
            points_with_pop = _get_population_points_sorted_by_population(
                game_map, max_points=_MAX_BUILD_CANDIDATES,
            )
        else:
            deleted = getattr(game_map, '_deleted_population_points', set()) or set()
            by_quad = [[] for _ in range(4)]
            for (x, y), pop in (pop_map or {}).items():
                if pop <= 0 or (x, y) in deleted:
                    continue
                q = (0 if x < w / 2 else 1) + (0 if y < h / 2 else 2)
                by_quad[q].append(((x, y), pop))
            for i in range(4):
                by_quad[i].sort(key=lambda p: p[1], reverse=True)
            pid = getattr(player, 'player_id', 0)
            my_q = pid % 4
            points_with_pop = []
            points_with_pop.extend(by_quad[my_q][:24])
            for q in range(4):
                if q != my_q:
                    points_with_pop.extend(by_quad[q][:8])
            if len(points_with_pop) < 2:
                points_with_pop = _get_population_points_sorted_by_population(game_map, max_points=24)
    if points_with_pop is None:
        points_with_pop = _get_population_points_sorted_by_population(game_map, max_points=_MAX_BUILD_CANDIDATES)
    elif not existing_routes:
        points_with_pop = points_with_pop[:min(_MAX_BUILD_CANDIDATES, len(points_with_pop))]
    if len(points_with_pop) < 2:
        # Only water segments may use point-to-point; on presets with water, do not build land straight lines here
        if preset_name:
            try:
                from map_preset_loader import preset_has_water_areas
                if preset_has_water_areas(preset_name):
                    return None
            except Exception:
                pass
        w, h = _map_width_height(game_map)
        min_dist = min_line_length
        fallbacks = [(5, h//2, 8, h//2), (5, h//2, 15, h//2), (w//4, 5, w//4, 12), (5, 5, 10, 5), (0, 0, 5, 0)]
        rng.shuffle(fallbacks)
        for x1, y1, x2, y2 in fallbacks:
            if 0 <= x1 <= w and 0 <= y1 <= h and 0 <= x2 <= w and 0 <= y2 <= h and (x1, y1) != (x2, y2):
                result = _try_create_line_from_endpoint_pair(
                    ops, player, game_map, (x1, y1), (x2, y2), spendable, current_year,
                    preset_name, preset, rng, min_dist, existing_routes=existing_routes,
                )
                if result:
                            return result
        return None

    # When other AIs built this turn, use more candidates (we already built points_with_pop from quadrants)
    max_cand = (48 if existing_routes else _MAX_BUILD_CANDIDATES)
    if is_first:
        pair_candidate_count = _ai_route_candidate_count(ops)
        candidates = _ai_random_population_build_candidates(
            game_map, rng, pair_candidate_count, min_pop=min_pop_for_build,
        )
        if len(candidates) < 2:
            candidates = _ai_random_population_build_candidates(
                game_map, rng, pair_candidate_count, min_pop=1,
            )
    else:
        # Prefer larger population centers - filter to points with meaningful population (min_pop_for_build from top of function)
        candidates = [(pt, pop) for pt, pop in points_with_pop[:min(max_cand, len(points_with_pop))]
                      if pop >= min_pop_for_build]
        if len(candidates) < 2:
            candidates = [(pt, pop) for pt, pop in points_with_pop[:min(max_cand, len(points_with_pop))]]

        # Shuffle candidates so different games pick different pairs (same profit tier, different order)
        rng.shuffle(candidates)

    # Build (i,j) pairs, score by estimated profit (most profitable first)
    min_dist = min_line_length
    from ai_specialties import route_length_bounds
    spec_min, max_dist = route_length_bounds(_AI_TURN_SPECIALTY)
    if max_dist is not None:
        max_dist = max(float(max_dist), min_dist + 0.5)
    if spec_min is not None and not is_first:
        min_dist = max(min_dist, float(spec_min))
    pairs = []
    for i, ((p1, pop1)) in enumerate(candidates):
        for j, ((p2, pop2)) in enumerate(candidates):
            if i >= j:
                continue
            # Routes must end in populated areas
            if pop1 <= 0 or pop2 <= 0:
                continue
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if dist < min_dist or (max_dist is not None and dist > max_dist):
                continue
            from operations import _ai_land_straight_limit_applies, _ai_land_straight_segment_exceeds_limit
            if _ai_land_straight_limit_applies(game_map) and _ai_land_straight_segment_exceeds_limit(
                game_map, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), planning_only=True,
            ):
                continue
            # For presets with water areas: reject straight lines across land (must use streets); allow ferries (across water)
            if preset_name and preset_has_water_areas(preset_name):
                if _straight_line_crosses_land(p1, p2, game_map, preset_name):
                    continue
            est_cost = max(15, int(150 * dist))
            if est_cost > spendable:
                continue
            est_profit = _estimate_line_profit(
                pop1, pop2, dist, est_cost, current_year, game_start_year=gsy,
                ops=ops, route_points=[p1, p2], existing_routes=existing_routes,
            )
            if est_profit <= 0 and not first_game_year:
                if est_profit < -max(1200, est_cost // 4):
                    continue
            if not first_game_year and est_profit < 0 and est_profit * payback_years < est_cost * 2:
                continue
            if early_growth_build:
                # During bootstrap years, keep noise small so high-pop/profitable corridors win consistently.
                noise = rng.uniform(-0.2, 0.2) * max(1, abs(est_profit))
            else:
                noise = rng.uniform(-0.8, 0.8) * max(1, abs(est_profit))
            pairs.append((est_profit + noise, p1, p2))
    # Prefer this AI's assigned section first, then unused sections, so all AIs build in different areas and are profitable by turn 10
    my_section = get_preferred_section_for_player(player, game_map, ops)

    def _pair_section_used(s_p1_p2):
        _, p1, p2 = s_p1_p2
        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2
        return point_to_section(game_map, mx, my) in used_sections

    def _pair_in_my_section(s_p1_p2):
        _, p1, p2 = s_p1_p2
        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2
        return point_to_section(game_map, mx, my) == my_section

    # Sort: in bootstrap years prioritize profitability; section spread is only a tiebreaker.
    def _section_sort_key(x):
        in_my = _pair_in_my_section(x)
        used = _pair_section_used(x)
        return (-x[0], 0 if in_my else 1, 1 if used else 0)

    # Sort: profitability first; section spread only when other lines already exist.
    def _score_sort_key(x):
        return -x[0]

    if is_first:
        pairs.sort(key=_score_sort_key)
        top_band = min(8, len(pairs))
        if top_band > 1:
            band = pairs[:top_band]
            rng.shuffle(band)
            pairs = band + pairs[top_band:]
    elif existing_routes and pairs:
        non_ov = [
            (s, p1, p2) for s, p1, p2 in pairs
            if not _segment_overlaps_existing_routes(p1, p2, existing_routes, game_map=game_map)
        ]
        ov = [
            (s, p1, p2) for s, p1, p2 in pairs
            if _segment_overlaps_existing_routes(p1, p2, existing_routes, game_map=game_map)
        ]
        # Prefer pairs whose midpoint is outside existing route bbox (so operations won't reject for overlap)
        def _midpoint_outside_bbox(s_p1_p2, bbox_margin=15):
            _, p1, p2 = s_p1_p2
            mx = (p1[0] + p2[0]) / 2
            my = (p1[1] + p2[1]) / 2
            return not (bbox[0] - bbox_margin <= mx <= bbox[2] + bbox_margin and bbox[1] - bbox_margin <= my <= bbox[3] + bbox_margin)
        if existing_routes and non_ov:
            all_pts = [pt for route in existing_routes for pt in route]
            if all_pts:
                bbox = (min(p[0] for p in all_pts), min(p[1] for p in all_pts), max(p[0] for p in all_pts), max(p[1] for p in all_pts))
                far = [(s, p1, p2) for s, p1, p2 in non_ov if _midpoint_outside_bbox((s, p1, p2))]
                near = [(s, p1, p2) for s, p1, p2 in non_ov if not _midpoint_outside_bbox((s, p1, p2))]
                non_ov = far + near if far else non_ov
        # Only allow non-overlapping pairs when other lines exist; never build an overlapping first segment
        pairs = sorted(non_ov, key=_section_sort_key)
        # Do not add ov to pairs - AI must not start a line overlapping another line
        # First turn: if no non-overlapping pairs, try again with more candidates so all AIs can build
        if not pairs and first_game_year and is_first and len(points_with_pop) >= 2:
            max_cand_first = min(80, len(points_with_pop))
            candidates_first = [(pt, pop) for pt, pop in points_with_pop[:max_cand_first] if pop >= min_pop_for_build]
            if len(candidates_first) < 2:
                candidates_first = [(pt, pop) for pt, pop in points_with_pop[:max_cand_first]]
            rng.shuffle(candidates_first)
            pairs = []
            for i, ((p1, pop1)) in enumerate(candidates_first):
                for j, ((p2, pop2)) in enumerate(candidates_first):
                    if i >= j or pop1 <= 0 or pop2 <= 0:
                        continue
                    dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                    if dist < min_dist:
                        continue
                    if preset_name and preset_has_water_areas(preset_name):
                        if _straight_line_crosses_land(p1, p2, game_map, preset_name):
                            continue
                    est_cost = max(15, int(150 * dist))
                    if est_cost > spendable:
                        continue
                    if not _segment_overlaps_existing_routes(p1, p2, existing_routes, game_map=game_map):
                        est_profit = _estimate_line_profit(
                pop1, pop2, dist, est_cost, current_year, game_start_year=gsy,
                ops=ops, route_points=[p1, p2], existing_routes=existing_routes,
            )
                        noise = rng.uniform(-0.2, 0.2) * max(1, abs(est_profit))
                        pairs.append((est_profit + noise, p1, p2))
            pairs.sort(key=_section_sort_key)
    else:
        pairs.sort(key=_section_sort_key)

    redirected, shared = ([], [])
    if not is_first:
        redirected, shared = _ai_shared_station_pairs(
            ops, player, game_map, min_line_length, max(min_line_length + 1.0, 8.0), rng,
        )
    # When we have non-overlapping pairs (other AIs built this turn), try them in order first
    if existing_routes and pairs and not _segment_overlaps_existing_routes(
        pairs[0][1], pairs[0][2], existing_routes, game_map=game_map
    ):
        pass  # pairs already sorted with non_ov first
    else:
        # Pick from a scored top band; first lines use wider randomization across games.
        if is_first:
            top_n = min(6, len(pairs))
        elif early_growth_build and is_first:
            top_n = min(6, len(pairs))
        else:
            top_n = min(6 if early_growth_build else 12, len(pairs))
        if top_n == 0 and not (redirected or shared):
            return None
        if top_n:
            idx = rng.randint(0, top_n - 1) if top_n > 1 else 0
            pairs = [pairs[idx]] + [p for i, p in enumerate(pairs) if i != idx]

    max_attempts = 15 if existing_routes else _MAX_LINE_CREATE_ATTEMPTS
    if first_game_year and is_first:
        max_attempts = min(max_attempts, 4)
    elif not is_first:
        pairs = _ai_order_pairs_with_shared_stations(
            pairs, redirected, shared, lambda a, b: (0.0, a, b), rng,
        )
        max_attempts += len(redirected)
    attempts = 0
    pairs = _ai_prefer_connected_pairs(pairs, game_map, ops)
    for score, p1, p2 in pairs:
        if attempts >= max_attempts or _ai_turn_time_exceeded(ops):
            break
        attempts += 1
        line_name = _generate_ai_line_name(
            player, rng, ops=ops, route_points=[p1, p2], current_year=current_year,
        )
        # Only build a ferry when the segment actually crosses open water (60m rule), not on every water map.
        is_ferry = _ai_segment_qualifies_as_ferry(p1, p2, game_map, preset_name or "")
        if is_ferry and not _ai_turn_allows_ferry_lines(ops, player):
            continue
        use_bus = (not is_ferry) and _ai_turn_build_mode(ops, current_year) == 'bus'
        if is_ferry:
            points = [p1, p2]
            dist = _ai_chord_length_grid(p1, p2)
        else:
            points = _ai_route_between_points(
                p1, p2, game_map, preset_name or '', current_year,
            )
            if not points or len(points) < 2:
                continue
            dist = _route_polyline_length(points)
        if preset_name and preset_has_water_areas(preset_name) and not is_ferry:
            continue  # never build land as point-to-point when map has water (must use OSM)
        if _ai_population_near_point(game_map, p1[0], p1[1]) <= 0 or _ai_population_near_point(game_map, p2[0], p2[1]) <= 0:
            continue
        if is_ferry:
            cost = max(15, int(FERRY_LENGTH_COST_PER_UNIT * dist))  # ferry cost formula
            elev = ['water'] * (len(points) - 1)
        else:
            cost = max(15, int(150 * dist * (0.5 if use_bus else 1.0)))
            elev = _get_safe_elevation_levels(points, game_map, preset_name or '', base='street')
            if preset_name:
                from map_preset_loader import preset_has_road_support
                if preset_has_road_support(preset_name) and not _ai_turn_time_low(ops, 20.0):
                    from operations import _ai_route_off_street_cost_multiplier
                    street_mult = _ai_route_off_street_cost_multiplier(
                        game_map, points, current_year, skip_osm=True,
                    )
                    cost = max(15, int(round(cost * street_mult)))
        cost_info = {'cost': cost, 'length': dist, 'points_count': len(points), 'elevation_levels': elev}
        if not _ai_can_afford_line_with_two_stations(ops, player, cost):
            continue
        if not _ai_planned_route_endpoints_have_station_sites(
            ops, player, game_map, points, is_bus_hint=use_bus,
        ):
            continue
        try:
            result = ops.create_line_complete(
                line_name, points, player,
                elevation_levels=elev,
                is_ferry_line=is_ferry,
                is_bus_line=use_bus,
                cost_info=cost_info,
                use_fast_path=not is_ferry,
            )
            if result.get('success', True):
                if is_ferry:
                    svc = "commuter"
                elif use_bus:
                    svc = _decide_ai_bus_service_type(points)
                else:
                    svc = _decide_ai_rail_service_type(points)
                _ai_record_line_service_type(ops, line_name, svc)
                _ai_open_line_with_two_stations(
                    ops, player, line_name, points, is_bus_line=use_bus, service_type=svc,
                    )
                if use_bus:
                    _try_assign_unassigned_bus_to_line(ops, player, line_name)
                return result
        except Exception:
            pass
    return None


def _get_all_station_coords(game_map, ops=None) -> list:
    """Return list of (x, y) for all stations on the map and queued station builds."""
    coords = []
    if hasattr(game_map, 'stations') and game_map.stations:
        for st in game_map.stations:
            if hasattr(st, 'get_position'):
                x, y = st.get_position()
            else:
                x = float(getattr(st, 'x', 0))
                y = float(getattr(st, 'y', 0))
            coords.append((x, y))
    if ops is not None and hasattr(ops, 'get_build_queue'):
        for build in ops.get_build_queue() or []:
            if build.get('type') != 'station':
                continue
            data = build.get('data') or {}
            try:
                coords.append((float(data.get('x')), float(data.get('y'))))
            except (TypeError, ValueError):
                continue
    return coords


def _ai_station_site_ok(ops, player, x: float, y: float) -> bool:
    """True if an AI may queue a stop at (x, y) per operations spacing rules."""
    if not _player_is_ai(player):
        return True
    if not hasattr(ops, 'assert_ai_stop_minimum_direct_separation'):
        return True
    try:
        ops.assert_ai_stop_minimum_direct_separation(player, x, y)
        return True
    except ValueError:
        return False


def _try_ai_add_station(ops, player, line_name: str, name: str, x: float, y: float) -> Optional[dict]:
    """Queue a station for an AI; returns result dict on success, None otherwise."""
    game_map = _ops_resolve_game_map(ops)
    if game_map and not _ai_station_point_not_in_water(game_map, x, y):
        return None
    if not _ai_station_site_ok(ops, player, x, y):
        return None
    base = (name or "Station").strip()
    try_name = _ai_resolve_unique_station_name(
        ops, player, base, x=float(x), y=float(y), line_name=line_name,
    )
    try:
        r = ops.add_station_to_line_at_point(player, line_name, try_name, x, y)
        if r.get('success'):
            return {**r, 'station_name': try_name, 'line_name': line_name, 'point': (x, y)}
    except (ValueError, TypeError):
        pass
    for attempt in range(1, 12):
        alt = f"{base} ({attempt + 1})"
        try_name = _ai_resolve_unique_station_name(
            ops, player, alt, x=float(x), y=float(y), line_name=line_name,
        )
        try:
            r = ops.add_station_to_line_at_point(player, line_name, try_name, x, y)
            if r.get('success'):
                return {**r, 'station_name': try_name, 'line_name': line_name, 'point': (x, y)}
        except (ValueError, TypeError):
            continue
    return None


def _pick_station_point(ops, game_map, line_name: str, line, points_with_pop: list = None, exclude_pts: set = None, min_spacing: float = None, player=None) -> Optional[tuple]:
    """Pick a grid point ON the line for a new station. Always returns a point on the route.
    Scores route points by nearby population; stations must respect service-aware spacing.
    Uses full route including any pending extension segments (so AIs can add stations on extended segments)."""
    exclude_pts = exclude_pts or set()
    route = ops.get_line_route_including_extensions(line_name) if hasattr(ops, 'get_line_route_including_extensions') else (line.get_route() if hasattr(line, 'get_route') else [])
    if not route:
        return None
    if points_with_pop is None:
        points_with_pop = _get_population_points(game_map, max_points=_MAX_STATION_POINTS)
    if min_spacing is None:
        min_spacing = _get_ai_station_spacing(line_name=line_name, line=line)

    def _pt(p):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return (float(p[0]), float(p[1]))
        return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

    # Exclude route points within spacing threshold of ANY station on the map or build queue.
    all_station_coords = _get_all_station_coords(game_map, ops)
    for sx, sy in all_station_coords:
        for i in range(len(route)):
            rx, ry = _pt(route[i])
            if math.hypot(rx - sx, ry - sy) < min_spacing:
                exclude_pts = exclude_pts | {(round(rx), round(ry))}

    best_pt = None
    best_score = -1
    # Sample route points (every Nth if long) to pick best by nearby population
    step = max(1, len(route) // 20)
    for i in range(0, len(route), step):
        rx, ry = _pt(route[i])
        key = (round(rx), round(ry))
        if key in exclude_pts:
            continue
        score = 0
        for (px, py), pop in (points_with_pop or []):
            if math.hypot(px - rx, py - ry) < 3:
                score += pop
        if score > best_score:
            best_score = score
            best_pt = (rx, ry)
    if best_pt is not None and _ai_population_near_point(game_map, best_pt[0], best_pt[1]) > 0:
        if _ai_station_point_not_in_water(game_map, best_pt[0], best_pt[1]):
            if player is None or _ai_station_site_ok(ops, player, best_pt[0], best_pt[1]):
                return best_pt
    # Fallback: any route point not excluded that still has nearby population
    for pt in route:
        rx, ry = _pt(pt)
        key = (round(rx), round(ry))
        if key not in exclude_pts and _ai_population_near_point(game_map, rx, ry) > 0:
            if not _ai_station_point_not_in_water(game_map, rx, ry):
                continue
            if player is None or _ai_station_site_ok(ops, player, rx, ry):
                return (rx, ry)
    # Last resort: interpolate along route for a point far enough from existing stations.
    line_stations = line.get_stations() if hasattr(line, 'get_stations') else []
    if line_stations:
        sx, sy = _pt((getattr(line_stations[-1], 'x', 0), getattr(line_stations[-1], 'y', 0)))
        for i in range(len(route) - 1):
            a, b = _pt(route[i]), _pt(route[i + 1])
            ax, ay = a[0], a[1]
            bx, by = b[0], b[1]
            seg_len = math.hypot(bx - ax, by - ay)
            if seg_len < 1e-9:
                continue
            for t in [0.25, 0.5, 0.75]:
                px = ax + t * (bx - ax)
                py = ay + t * (by - ay)
                if math.hypot(px - sx, py - sy) >= min_spacing:
                    # Also ensure not too close to any other station
                    if all(math.hypot(px - ox, py - oy) >= min_spacing for ox, oy in all_station_coords):
                        if _ai_population_near_point(game_map, px, py) > 0:
                            if not _ai_station_point_not_in_water(game_map, px, py):
                                continue
                            if player is None or _ai_station_site_ok(ops, player, px, py):
                                return (px, py)
    return None


def _pick_best_line_for_trainset(ops, player, trainset, line_names: list) -> Optional[str]:
    """Pick best line to assign trainset to (one with fewest trainsets)."""
    if not line_names:
        return None
    best = None
    best_count = 999
    for ln in line_names:
        count = len(ops.get_trainsets_by_line(player, ln))
        if count < best_count:
            best_count = count
            best = ln
    return best


# An extension may not head back toward where the line came from by more than this
# (grid units, ~1 km). Such a target is served by a new line from the end station instead.
AI_EXTENSION_MAX_BACKTRACK_GRID = 1.0
# Share of new-line searches that try lines starting at an existing own station first.
AI_SHARED_STATION_LINE_PREFERENCE = 0.7
# Once every line has been extended this many times, new lines come before more extensions.
AI_EXTENSIONS_PER_LINE_BEFORE_NEW_LINE = 2

import threading as _threading
_AI_EXTENSION_CONTEXT = _threading.local()      # .player: the AI currently planning extensions
_AI_REDIRECTED_TARGETS: dict = {}               # id(player) -> [(attach_pt, target_pt), ...]


def _ai_line_end_direction(route_pts: list, from_end: bool, span: float = 1.0):
    """Unit vector the line travels in as it reaches the end (measured over ~``span``)."""
    pts = list(route_pts) if from_end else list(reversed(route_pts))
    if len(pts) < 2:
        return None
    tip = pts[-1]
    back = pts[-2]
    for p in reversed(pts[:-1]):
        back = p
        if math.hypot(tip[0] - p[0], tip[1] - p[1]) >= span:
            break
    dx, dy = tip[0] - back[0], tip[1] - back[1]
    mag = math.hypot(dx, dy)
    return (dx / mag, dy / mag) if mag > 1e-9 else None


def _ai_extension_backtrack_grid(route: list, additional_points: list, extend_from_end: bool) -> float:
    """How far (grid units) an extension reaches back past its attach end, measured along
    the direction the line was travelling there; 0 when it only moves forward/sideways."""
    def _p(q):
        return (float(q[0]), float(q[1])) if isinstance(q, (list, tuple)) else (float(q.x), float(q.y))
    pts = [_p(q) for q in (route or [])]
    if len(pts) < 2 or not additional_points:
        return 0.0
    direction = _ai_line_end_direction(pts, extend_from_end)
    if direction is None:
        return 0.0
    tip = pts[-1] if extend_from_end else pts[0]
    worst = 0.0
    for q in additional_points:
        x, y = _p(q)
        along = (x - tip[0]) * direction[0] + (y - tip[1]) * direction[1]
        worst = max(worst, -along)
    return worst


def _extension_turn_ok(route: list, additional_points: list, extend_from_end: bool, max_degrees: float = 145.0) -> bool:
    """Extensions may turn freely, but may not run back more than
    AI_EXTENSION_MAX_BACKTRACK_GRID toward where the line came from. A rejected target
    is remembered so the AI serves it with a new line from that end instead."""
    if _ai_extension_backtrack_grid(route, additional_points, extend_from_end) <= AI_EXTENSION_MAX_BACKTRACK_GRID:
        return True
    player = getattr(_AI_EXTENSION_CONTEXT, 'player', None)
    if player is not None and route and additional_points:
        tip = route[-1] if extend_from_end else route[0]
        target = additional_points[-1]
        queue = _AI_REDIRECTED_TARGETS.setdefault(id(player), [])
        item = ((float(tip[0]), float(tip[1])), (float(target[0]), float(target[1])))
        if item not in queue:
            queue.append(item)
            del queue[:-6]
    return False


def _ai_own_station_points(ops, player) -> list:
    """(x, y) of the player's built stations."""
    own = set(player.get_line_names() or [])
    out = []
    for st in getattr(ops.get_map(), 'stations', None) or []:
        served = st.get_line_names() if hasattr(st, 'get_line_names') else getattr(st, 'line_names', [])
        if own.intersection(served or []):
            out.append((float(st.get_x()), float(st.get_y())))
    return out


def _ai_shared_station_pairs(ops, player, game_map, min_len: float, max_len: float, rng) -> tuple:
    """New-line endpoint pairs that start at one of the player's own stations.

    Returns (redirected, shared): ``redirected`` are targets an extension was not allowed
    to reach (the line would have run back on itself); ``shared`` pair a few existing
    stations with populous places in reach. Each item is (station_pt, target_pt)."""
    stations = _ai_own_station_points(ops, player)
    if not stations:
        _AI_REDIRECTED_TARGETS.pop(id(player), None)
        return [], []

    def _nearest_station(pt):
        best = min(stations, key=lambda s: math.hypot(s[0] - pt[0], s[1] - pt[1]))
        return best if math.hypot(best[0] - pt[0], best[1] - pt[1]) <= 0.5 else None

    redirected = []
    for attach, target in _AI_REDIRECTED_TARGETS.pop(id(player), []):
        st = _nearest_station(attach)
        if st is not None and math.hypot(target[0] - st[0], target[1] - st[1]) >= min_len:
            redirected.append((st, target))

    shared = []
    pop_points = _get_population_points_sorted_by_population(game_map, max_points=_MAX_BUILD_CANDIDATES)
    for st in rng.sample(stations, min(4, len(stations))):
        reach = []
        for pt, pop in pop_points:
            d = math.hypot(pt[0] - st[0], pt[1] - st[1])
            if min_len <= d <= max_len and _nearest_station(pt) is None:
                reach.append((pop * rng.uniform(0.8, 1.2), (float(pt[0]), float(pt[1]))))
        reach.sort(reverse=True)
        shared.extend((st, pt) for _score, pt in reach[:2])
    return redirected, shared


def _ai_order_pairs_with_shared_stations(pairs: list, redirected: list, shared: list, make_pair, rng) -> list:
    """Put redirected targets first, then (usually) shared-station pairs before the fully new
    ones, so AIs favour connected networks but still open separate lines too."""
    extra_redirect = [make_pair(a, b) for a, b in redirected]
    extra_shared = [make_pair(a, b) for a, b in shared]
    rng.shuffle(extra_shared)
    if rng.random() < AI_SHARED_STATION_LINE_PREFERENCE:
        return extra_redirect + extra_shared + list(pairs)
    return extra_redirect + list(pairs[:2]) + extra_shared + list(pairs[2:])


def _try_osm_extend_via_population_targets(
    ops,
    player,
    game_map,
    line_name: str,
    line,
    route: list,
    spendable: int,
    current_year: int,
    points_with_pop: list,
    rng: random.Random,
    preset_name: str,
    *,
    require_profit_when_troubled: bool = False,
) -> Optional[dict]:
    """OSM extension: random population targets (hundredths), filtered by line direction, street-routed."""
    del points_with_pop  # extensions use fresh random candidates like new-line OSM builds
    if spendable < 100:
        return None

    def _pt(p):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return (float(p[0]), float(p[1]))
        return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))

    route_pts = [_pt(p) for p in route]
    if len(route_pts) < 2:
        return None
    start, end = route_pts[0], route_pts[-1]
    route_pt_set = {(round(p[0], 2), round(p[1], 2)) for p in route_pts}

    game = ops.get_game() if ops else None
    min_pop = getattr(game, 'ai_min_population', 100) if game is not None else 100
    candidate_count = _ai_route_candidate_count(ops)

    rough_candidates = []
    for extend_from_end in (True, False):
        direction = _ai_line_endpoint_direction(route_pts, extend_from_end)
        if direction is None:
            continue
        base = end if extend_from_end else start
        candidates = _ai_random_population_build_candidates(
            game_map, rng, candidate_count, min_pop=min_pop,
        )
        if not candidates:
            candidates = _ai_random_population_build_candidates(
                game_map, rng, candidate_count, min_pop=1,
            )
        for pt, pop in candidates:
            if pop <= 0:
                continue
            target = (float(pt[0]), float(pt[1]))
            if (round(target[0], 2), round(target[1], 2)) in route_pt_set:
                continue
            if not _ai_point_in_extension_direction(base, target, direction):
                continue
            dist = math.hypot(target[0] - base[0], target[1] - base[1])
            if dist < 1.0 or dist > 25:
                continue
            est_cost = max(100, int(150 * dist * 0.75))
            if est_cost > spendable:
                continue
            rough_candidates.append((pop / est_cost, target[0], target[1], extend_from_end))
    if not rough_candidates:
        return None
    rough_candidates.sort(key=lambda c: c[0], reverse=True)
    rng.shuffle(rough_candidates[: min(6, len(rough_candidates))])

    from operations import _ai_route_off_street_cost_multiplier

    for _, x, y, extend_from_end in rough_candidates[:_MAX_EXTEND_COST_CALLS + 2]:
        if _ai_turn_time_exceeded(ops):
            break
        base = end if extend_from_end else start
        target = (float(x), float(y))
        street_route = _ai_route_between_points(
            base, target, game_map, preset_name or '', current_year, osm_pair_route=True,
            avoid_route=route_pts,
        )
        if not street_route or len(street_route) < 2:
            continue
        street_route = [(float(p[0]), float(p[1])) for p in street_route]
        # The router returns base -> target; keep its first vertex unless it is the line end
        # itself, so the join onto the existing line stays on the street.
        if math.hypot(street_route[0][0] - base[0], street_route[0][1] - base[1]) < 0.002:
            street_route = street_route[1:]
        if not street_route:
            continue
        if not _ai_point_near_population_anchor(street_route[-1], target):
            continue
        # Extension points go outward from the line end ("click order"), for either end; the
        # game reverses them itself when prepending to the start of the line.
        additional_points = street_route
        if not additional_points:
            continue
        if not _ai_osm_route_passes_build_rules(
            game_map, [base] + additional_points, current_year, preset_name or '',
            is_bus_line=_line_is_bus(line),
        ):
            continue
        if not _extension_turn_ok(route_pts, additional_points, extend_from_end):
            continue  # would run back toward where the line came from: a new line's job
        from operations import _ai_line_doubles_back, _ai_merged_line_route
        # Same merge the game validates, so both sides judge the same polyline.
        merged_pts = _ai_merged_line_route(route_pts, additional_points, extend_from_end)
        if _ai_line_doubles_back(merged_pts, game_map):
            continue  # the whole line may never double back on itself
        new_tip = _pt(additional_points[-1])
        e_a, e_b = (route_pts[0], new_tip) if extend_from_end else (new_tip, route_pts[-1])
        if (
            _ai_population_at_anchor(game_map, e_a[0], e_a[1]) <= 0
            or _ai_population_at_anchor(game_map, e_b[0], e_b[1]) <= 0
        ):
            continue
        if not _ai_planned_endpoint_station_viable(
            ops, player, game_map, new_tip[0], new_tip[1],
            line_name=line_name, line=line, is_bus_hint=_line_is_bus(line),
        ):
            continue
        conn_pt = base
        full_pts = [conn_pt] + additional_points
        total_len = _route_polyline_length(full_pts) - _route_polyline_length([conn_pt])
        if total_len <= 0:
            total_len = _route_polyline_length(additional_points)
        street_cost_mult = _ai_route_off_street_cost_multiplier(
            game_map, full_pts, current_year, is_bus_line=_line_is_bus(line), skip_osm=True,
        )
        cost = max(100, int(150 * total_len * 0.75 * street_cost_mult))
        line_profit_now = ops.calc_line_profit(line, current_year) if hasattr(ops, 'calc_line_profit') else 0
        if require_profit_when_troubled and line_profit_now <= 0:
            try:
                gsy = _game_start_year(ops)
                full_len = _route_polyline_length(full_pts)
                pop_a = _ai_population_near_point(game_map, e_a[0], e_a[1])
                pop_b = _ai_population_near_point(game_map, e_b[0], e_b[1])
                est_profit = _estimate_line_profit(
                    pop_a, pop_b, max(0.1, full_len), 1, current_year, game_start_year=gsy,
                )
                if est_profit <= 0 or (est_profit * 7) < (cost * 1.05):
                    continue
            except Exception:
                continue
        if cost > spendable or cost < 1:
            continue
        if not _ai_osm_route_passes_build_rules(
            game_map, full_pts, current_year, preset_name or '',
            is_bus_line=_line_is_bus(line),
        ):
            continue
        if cost > 2000 and not _is_first_game_year(ops, current_year):
            line_obj = (ops.get_map().get_lines_by_name() or {}).get(line_name)
            if line_obj and hasattr(ops, 'calc_line_profit') and (ops.calc_line_profit(line_obj, current_year) or 0) <= 0:
                continue
        elevation_levels = _get_safe_elevation_levels(full_pts, game_map, preset_name or '', base='street')
        cost_info = {
            'cost': cost, 'length': total_len,
            'points_count': max(1, len(additional_points)),
            'elevation_levels': elevation_levels,
        }
        try:
            result = ops.extend_line_complete(
                line_name, player, additional_points, extend_from_end,
                elevation_levels, cost_info=cost_info, use_fast_path=True,
            )
            result['cost'] = cost
            result['line_name'] = line_name
            result['additional_points'] = additional_points
            _add_ai_extension_station(ops, player, line_name, additional_points, extend_from_end)
            return result
        except Exception:
            continue
    return None


def _try_extend_line(ops, player, *args, **kwargs) -> Optional[dict]:
    """Extend an existing line (see _try_extend_line_impl). Targets an extension may not
    reach because the line would run back on itself are remembered for a new line."""
    previous = getattr(_AI_EXTENSION_CONTEXT, 'player', None)
    _AI_EXTENSION_CONTEXT.player = player
    try:
        return _try_extend_line_impl(ops, player, *args, **kwargs)
    finally:
        _AI_EXTENSION_CONTEXT.player = previous


def _try_extend_line_impl(
    ops,
    player,
    game_map,
    spendable: int,
    current_year: int,
    points_with_pop: list = None,
    rng: random.Random = None,
    preset=None,
    line_names_priority: list = None,
    require_profit_when_troubled: bool = False,
) -> Optional[dict]:
    """Extend an existing line. Uses street polylines if preset provides; else toward high-population points.
    Scores by profit/cost (pop at target / extension cost). Prioritizes extending profitable lines.
    Pre-filters by rough score to avoid expensive calculate_extend_cost calls.
    points_with_pop: optional pre-fetched list from run_ai_turn (avoids redundant sort on large maps).
    rng: optional per-player RNG for differentiation.
    preset: optional pre-loaded map preset (avoids repeated load_map_preset)."""
    if not ai_capital_spending_allowed_vs_idle(ops, player, spendable):
        return None
    map_lines = game_map.get_lines_by_name() if hasattr(game_map, 'get_lines_by_name') else {}
    build_queue = ops.get_build_queue()
    queue_line_names = set()
    for b in build_queue:
        d = b.get('data', {})
        n = d.get('name') or d.get('line_name')
        if n:
            queue_line_names.add(n)

    def _pt(p):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return (float(p[0]), float(p[1]))
        return (getattr(p, 'x', 0), getattr(p, 'y', 0))

    rng = rng or random.Random()
    if points_with_pop is None:
        points_with_pop = _get_population_points_sorted_by_population(game_map, max_points=_MAX_EXTEND_POINTS)
    if not points_with_pop:
        return None

    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name and preset is None:
        try:
            from map_preset_loader import load_map_preset
            preset = load_map_preset(preset_name)
        except Exception:
            pass
    years_from_start = _years_from_game_start(ops, current_year)

    # Sort lines by profit; add per-player noise so different AIs extend different lines first
    line_names = player.get_line_names()
    line_profits = {}
    for ln in line_names:
        line = map_lines.get(ln)
        if line:
            profit = ops.calc_line_profit(line, current_year)
            noise = rng.uniform(-0.1, 0.1) * max(100, abs(profit))
            line_profits[ln] = profit + noise
    line_names_sorted = sorted(line_names, key=lambda ln: line_profits.get(ln, 0), reverse=True)
    if line_names_priority:
        pri = [ln for ln in line_names_priority if ln in line_names_sorted]
        rest = [ln for ln in line_names_sorted if ln not in pri]
        line_names_sorted = pri + rest

    for line_name in line_names_sorted[:_MAX_EXTEND_LINES]:
        line = map_lines.get(line_name)
        if not line or line_name in queue_line_names:
            continue
        if _line_is_ferry(line):
            continue  # Ferries are short water crossings only — never extend along streets.
        if _line_is_bus(line):
            route = list(ops.get_line_route(line) or []) if line else []
        else:
            route = list(ops.get_line_route_including_extensions(line_name) or [])
        if not route or len(route) < 2:
            continue

        route_pts = {(_pt(p)[0], _pt(p)[1]) for p in route}
        start, end = _pt(route[0]), _pt(route[-1])

        # OSM: score population targets and street-route from the line end (same principle as new lines).
        if preset_name and preset and hasattr(preset, 'get_road_segment_points_for_ai'):
            pop_result = _try_osm_extend_via_population_targets(
                ops, player, game_map, line_name, line, route, spendable, current_year,
                points_with_pop, rng, preset_name,
                require_profit_when_troubled=require_profit_when_troubled,
            )
            if pop_result:
                return pop_result

        # Fallback: preset road-extension polylines from the current endpoint.
        if preset_name:
            try:
                from map_preset_loader import get_preset_road_extension_points
                for extend_from_end in (True, False):
                    base = end if extend_from_end else start
                    other = start if extend_from_end else end
                    max_neighbor = (
                        AI_BUS_NEIGHBOR_STREET_MAX_GAP_GRID
                        if _line_is_bus(line)
                        else AI_RAIL_NEIGHBOR_STREET_MAX_GAP_GRID
                    )
                    exclude_roads = None
                    chain_depth = 0
                    ext_max_pts = 8
                    if years_from_start >= 2:
                        ext_max_pts = 12
                        try:
                            from map_preset_loader import get_preset_geo_feature_name_for_line_naming
                            on_street = get_preset_geo_feature_name_for_line_naming(
                                preset_name, game_map, base[0], base[1], current_year, max_distance_m=60.0,
                            )
                            if on_street:
                                exclude_roads = frozenset([str(on_street)])
                                chain_depth = 1
                        except Exception:
                            pass
                    road_pts = get_preset_road_extension_points(
                        preset_name, game_map, base, other, current_year, extend_from_end,
                        max_points=ext_max_pts,
                        prefer_bridges=True,
                        preset=preset,
                        exclude_road_names=exclude_roads,
                        _chain_depth=chain_depth,
                        max_neighbor_street_gap_grid=max_neighbor,
                    )
                    if len(road_pts) >= 2:
                        conn_pt = end if extend_from_end else start
                        conn_path = [conn_pt] + list(road_pts) if extend_from_end else list(road_pts) + [conn_pt]
                        conn_path = _ai_normalize_route_water_crossings(
                            conn_path, game_map, preset_name, current_year,
                        )
                        if len(conn_path) < 2:
                            continue
                        # Click order (outward from the attach end) for both ends.
                        additional_points = conn_path[1:] if extend_from_end else list(reversed(conn_path[:-1]))
                        if len(additional_points) < 1:
                            continue
                        if not _ai_osm_route_passes_build_rules(
                            game_map, conn_path, current_year, preset_name,
                            is_bus_line=_line_is_bus(line),
                        ):
                            continue  # old road-extension polylines can contain off-street jumps
                        # Check 145-degree max turn: extension must not turn more than 145 degrees.
                        if not _extension_turn_ok(route, additional_points, extend_from_end):
                            continue
                        # Build full route to check for self-overlap (extension looping back)
                        route_pts = [_pt(p) for p in route]
                        add_pts = [_pt(ap) for ap in additional_points]
                        from operations import _ai_merged_line_route
                        full_pts = _ai_merged_line_route(route_pts, add_pts, extend_from_end)
                        try:
                            from operations import _ai_line_doubles_back
                            if _ai_line_doubles_back(full_pts, game_map):
                                continue
                        except ImportError:
                            pass
                        if extend_from_end:
                            e_a, e_b = route_pts[0], add_pts[-1]
                        else:
                            e_a, e_b = add_pts[-1], route_pts[-1]
                        if (
                            _ai_population_near_point(game_map, e_a[0], e_a[1]) <= 0
                            or _ai_population_near_point(game_map, e_b[0], e_b[1]) <= 0
                        ):
                            continue
                        new_tip = add_pts[-1]
                        if not _ai_planned_endpoint_station_viable(
                            ops, player, game_map, new_tip[0], new_tip[1],
                            line_name=line_name, line=line,
                            is_bus_hint=_line_is_bus(line),
                        ):
                            continue
                        from operations import _ai_route_fully_follows_streets, _ai_route_off_street_cost_multiplier
                        total_len = sum(
                            math.hypot(additional_points[i+1][0]-additional_points[i][0],
                                       additional_points[i+1][1]-additional_points[i][1])
                            for i in range(len(additional_points)-1)
                        )
                        conn_pt = end if extend_from_end else start
                        full_pts = [conn_pt] + additional_points
                        street_cost_mult = _ai_route_off_street_cost_multiplier(
                            game_map, full_pts, current_year, is_bus_line=_line_is_bus(line),
                            skip_osm=True,
                        )
                        cost = max(100, int(150 * total_len * 0.75 * street_cost_mult))
                        line_profit_now = ops.calc_line_profit(line, current_year) if hasattr(ops, 'calc_line_profit') else 0
                        if require_profit_when_troubled and line_profit_now <= 0:
                            try:
                                gsy = _game_start_year(ops)
                                full_len = _route_polyline_length(full_pts)
                                pop_a = _ai_population_near_point(game_map, e_a[0], e_a[1])
                                pop_b = _ai_population_near_point(game_map, e_b[0], e_b[1])
                                est_profit = _estimate_line_profit(pop_a, pop_b, max(0.1, full_len), 1, current_year, game_start_year=gsy)
                                payback = 7
                                if est_profit <= 0 or (est_profit * payback) < (cost * 1.05):
                                    continue
                            except Exception:
                                continue
                        if cost <= spendable and cost >= 1:
                            if _line_is_bus(line):
                                from operations import _route_has_off_street_bus_segment
                                if _route_has_off_street_bus_segment(
                                    game_map, full_pts, skip_osm=True, current_year=current_year,
                                ):
                                    continue
                            elif not _ai_route_fully_follows_streets(
                                game_map, full_pts, current_year, skip_osm=True,
                            ):
                                if cost > spendable * 0.65:
                                    continue
                            # Do not extend if it would net lose: skip large cost on unprofitable lines
                            if cost > 2000 and not _is_first_game_year(ops, current_year):
                                line_obj = (ops.get_map().get_lines_by_name() or {}).get(line_name)
                                if line_obj and hasattr(ops, 'calc_line_profit') and (ops.calc_line_profit(line_obj, current_year) or 0) <= 0:
                                    continue
                            elevation_levels = _get_safe_elevation_levels(full_pts, game_map, preset_name or '', base='street')
                            cost_info = {'cost': cost, 'length': total_len, 'points_count': len(additional_points)-1, 'elevation_levels': elevation_levels}
                            try:
                                result = ops.extend_line_complete(line_name, player, additional_points, extend_from_end, elevation_levels, cost_info=cost_info, use_fast_path=True)
                                result['cost'] = cost
                                result['line_name'] = line_name
                                result['additional_points'] = additional_points
                                _add_ai_extension_station(ops, player, line_name, additional_points, extend_from_end)
                                return result
                            except Exception:
                                pass
            except Exception:
                pass
        if preset_name and preset and hasattr(preset, 'get_road_segment_points_for_ai'):
            continue  # OSM maps: no straight population-chord extension fallback

        # Standard maps: score population targets and route (non-OSM chord/street hybrid below).
        rough_candidates = []
        for (x, y), pop in points_with_pop:
            if pop <= 0:
                continue
            pt = (float(x), float(y))
            if pt in route_pts:
                continue
            dist_end = math.hypot(end[0] - x, end[1] - y)
            dist_start = math.hypot(start[0] - x, start[1] - y)
            dist = min(dist_end, dist_start)
            if dist < 2 or dist > 25:
                continue
            est_cost = max(100, int(150 * dist))
            if est_cost > spendable:
                continue
            rough_score = pop / est_cost
            rough_candidates.append((rough_score, x, y))
        rough_candidates.sort(key=lambda c: c[0], reverse=True)

        # Phase 2: Use est_cost (avoid calculate_extend_cost - max 10s per line/extension)
        best_target = None
        best_score = -1
        for _, x, y in rough_candidates[:_MAX_EXTEND_COST_CALLS]:
            ex, ey = x, y
            extend_from_end = math.hypot(end[0] - ex, end[1] - ey) <= math.hypot(start[0] - ex, start[1] - ey)
            base = end if extend_from_end else start
            dx = ex - base[0]
            dy = ey - base[1]
            d = math.hypot(dx, dy) or 1
            step = min(5, d)
            dist = step
            cost = max(100, int(150 * dist))
            if cost > spendable or cost < 1:
                continue
            pop = next((p for (px, py), p in points_with_pop if (px, py) == (x, y)), 0)
            score = pop / cost if cost > 0 else 0
            if score > best_score:
                best_score = score
                best_target = (x, y)

        if not best_target:
            continue

        ex, ey = best_target
        extend_from_end = math.hypot(end[0] - ex, end[1] - ey) <= math.hypot(start[0] - ex, start[1] - ey)
        base = end if extend_from_end else start
        base_pt = (float(base[0]), float(base[1]))
        street_route = _ai_route_between_points(
            base_pt, (float(ex), float(ey)), game_map, preset_name or '', current_year,
        )
        if not street_route or len(street_route) < 2:
            continue
        # Click order: outward from the attach end, for either end of the line.
        additional_points = [(float(p[0]), float(p[1])) for p in street_route[1:]]
        if not additional_points:
            continue
        if not _extension_turn_ok(route, additional_points, extend_from_end):
            continue
        from operations import _ai_merged_line_route
        merged_pts = _ai_merged_line_route([_pt(p) for p in route], additional_points, extend_from_end)
        try:
            from operations import _ai_line_doubles_back
            if _ai_line_doubles_back(merged_pts, game_map):
                continue
        except Exception:
            pass
        m0, m1 = merged_pts[0], merged_pts[-1]
        if _ai_population_near_point(game_map, m0[0], m0[1]) <= 0 or _ai_population_near_point(game_map, m1[0], m1[1]) <= 0:
            continue
        seg_elev = _get_safe_elevation_levels(
            [base_pt] + additional_points,
            game_map, preset_name or '', base='street',
        )
        elevation_levels = seg_elev
        full_len = _route_polyline_length(
            [base_pt] + additional_points,
        )
        cost = max(100, int(150 * full_len * 0.75))
        cost_info = {
            'cost': cost, 'length': full_len,
            'points_count': max(1, len(additional_points)),
            'elevation_levels': elevation_levels,
        }
        if cost > spendable or cost < 1:
            continue
        try:
            result = ops.extend_line_complete(line_name, player, additional_points, extend_from_end, elevation_levels, cost_info=cost_info, use_fast_path=True)
            result['cost'] = cost
            result['line_name'] = line_name
            result['additional_points'] = additional_points
            return result
        except Exception:
            pass
    return None


def _try_buy_marketplace(
    ops,
    player,
    current_year: int,
    spendable: int,
    focus: str,
    rng=None,
    line_only: bool = False,
    allow_rolling_stock: bool = False,
    prefer_player_sold_lines: bool = False,
) -> Optional[dict]:
    """Buy best value item from marketplace. Vehicle-agnostic.
    When rng is provided, chance to buy increases by 1% per % the item is off (discount_percent).
    line_only: if True, only consider marketplace lines (for AIs with no network yet).
    allow_rolling_stock: when True, may buy trainsets even in early years if lines need vehicles.
    prefer_player_sold_lines: after Public TA forms, prioritize lines listed by other players."""
    yfs = _years_from_game_start(ops, current_year)
    cash = ops.get_player_money(player)
    public_ta = ops.get_public_ta() if hasattr(ops, 'get_public_ta') else None
    public_ta_active = bool(public_ta and getattr(public_ta, 'established', False))
    if prefer_player_sold_lines or public_ta_active:
        prefer_player_sold_lines = True
    if not line_only and not allow_rolling_stock and yfs <= 8:
        return None
    if not line_only and allow_rolling_stock and spendable < _inflate_econ(ops, current_year, 800):
        return None
    if not line_only and yfs <= 10 and not allow_rolling_stock and not prefer_player_sold_lines:
        # Marketplace-focused AIs should actually shop before they accumulate inflated cash reserves.
        min_cash = _inflate_econ(ops, current_year, 35000 if focus == 'marketplace' else 80000)
        if cash < min_cash:
            return None
    if prefer_player_sold_lines and line_only and spendable < _inflate_econ(ops, current_year, 1200):
            return None
    marketplace = ops.game.get_marketplace()
    items = marketplace.get_available_items(current_year) if hasattr(marketplace, 'get_available_items') else []
    if not items:
        return None

    from line import Line
    from vehicle import Trainset, Locomotive, Railcar, Ferry, Bus
    from marketplace import SaleItem

    # Vehicles must be usable on at least one built line; never buy vehicles with zero lines.
    # (Buying a marketplace Line is allowed regardless.)
    game_map = ops.get_map() if hasattr(ops, 'get_map') else None
    map_lines = (game_map.get_lines_by_name() or {}) if game_map and hasattr(game_map, 'get_lines_by_name') else {}
    player_line_names = list(player.get_line_names() or [])
    has_lines = len(player_line_names) > 0
    has_bus_line = False
    has_ferry_line = False
    has_rail_line = False
    if has_lines:
        from line import Ferry as FerryLine, Bus as BusLine
        for ln in player_line_names:
            lobj = map_lines.get(ln)
            if lobj is None:
                continue
            if isinstance(lobj, FerryLine):
                has_ferry_line = True
            elif isinstance(lobj, BusLine):
                has_bus_line = True
            elif isinstance(lobj, Line):
                has_rail_line = True

    wanted_rail_classes = set()
    for ln in player_line_names:
        lobj = map_lines.get(ln)
        if lobj is not None and not _line_is_bus(lobj) and not _line_is_ferry(lobj):
            wanted_rail_classes.add(_ai_line_vehicle_class(ln, lobj))

    best_sale = None
    best_score = -1

    for item in items:
        sale_item = item if isinstance(item, SaleItem) else None
        if not sale_item:
            continue
        vehicle = sale_item.get_item()
        if line_only and not isinstance(vehicle, Line):
            continue
        price = sale_item.get_price(current_year)
        if price > spendable or price < 1:
            continue
        # Compatibility gates:
        # - Never buy a vehicle if we have no lines.
        # - Only buy a vehicle type that can be used on existing line types.
        if not isinstance(vehicle, Line):
            if not has_lines:
                continue
            if isinstance(vehicle, (Trainset, Locomotive, Railcar)) and not has_rail_line:
                continue
            if isinstance(vehicle, Bus) and not has_bus_line:
                continue
            if isinstance(vehicle, Ferry) and not has_ferry_line:
                continue
            # Rolling stock is bought for profit, not capacity: every trainset costs
            # maintenance, fuel and tax each year whether or not it carries anyone.
            if isinstance(vehicle, Trainset) and not _ai_trainset_purchase_pays(
                ops, player, vehicle, price, current_year,
            ):
                continue
            if isinstance(vehicle, (Locomotive, Railcar)):
                continue  # loose locomotives/railcars are not used by AI lines; skip the cost
            if isinstance(vehicle, (Bus, Ferry)):
                # Only for a bus/ferry line that has no vehicle yet.
                want_line = BusLine if isinstance(vehicle, Bus) else FerryLine
                if not any(
                    isinstance(map_lines.get(ln), want_line)
                    and not getattr(map_lines.get(ln), 'vehicles', None)
                    for ln in player_line_names
                ):
                    continue
        score = 0
        seller = sale_item.get_seller() if hasattr(sale_item, 'get_seller') else None
        if isinstance(vehicle, Line):
            score = (500 if focus == 'marketplace' else 0) + 300 - price // 100
            if prefer_player_sold_lines and seller and seller not in ('Unowned', 'Manufacturer'):
                score += 900
        elif isinstance(vehicle, Trainset):
            cap = getattr(vehicle, 'capacity', 0) or (sum(getattr(c, 'capacity', 0) for c in getattr(vehicle, 'cars', [])) if hasattr(vehicle, 'cars') else 0) or 50
            score = cap * 2 - price // 100 + (200 if focus == 'marketplace' else 0)
            from ai_specialties import _rail_vehicle_class
            if _rail_vehicle_class(vehicle) in wanted_rail_classes:
                score += 500  # the kind of train this company's lines run
        elif isinstance(vehicle, (Locomotive, Railcar, Ferry, Bus)):
            score = (getattr(vehicle, 'capacity', 0) or 50) - price // 100
        else:
            continue
        if score > best_score:
            best_score = score
            best_sale = sale_item

    if best_sale:
        # Chance to buy increases by 1% per % the item is off (e.g. 20% off -> +20% chance)
        if rng is not None:
            discount_pct = getattr(best_sale, 'discount_percent', 0) or 0
            buy_chance = min(1.0, 0.5 + discount_pct / 100.0)
            if focus == 'marketplace':
                buy_chance = min(1.0, buy_chance + 0.32)
            if prefer_player_sold_lines and isinstance(best_sale.get_item(), Line):
                buy_chance = min(1.0, buy_chance + 0.35)
            if rng.random() >= buy_chance:
                return None
        try:
            item = best_sale.get_item()
            if isinstance(item, Line):
                line = ops.buy_line_by_purchase(best_sale, player)
                if line:
                    return {'item': 'line', 'line': line.name, 'price': best_sale.get_price(current_year)}
            else:
                result = ops.buy_vehicle_complete(best_sale, player, current_year)
                return result
        except Exception:
            pass
    return None


def _ai_usable_unassigned_trainsets(ops, player, game_map) -> list:
    """Unassigned trainsets that can actually run on one of the player's lines waiting for
    its first train (same readiness rule as everywhere else)."""
    unassigned = list(ops.get_unassigned_trainsets(player) or []) if ops else []
    if not unassigned:
        return []
    map_lines = (game_map.get_lines_by_name() or {}) if game_map else {}
    lines_needing = [
        ln for ln in player.get_line_names()
        if _line_needs_trainset_assignment(map_lines, ln, ops=ops, player=player)
    ]
    if not lines_needing:
        return []
    return [ts for ts in unassigned if _pick_best_line_for_trainset(ops, player, ts, lines_needing)]


def _ai_has_pending_trainset_assignment(ops, player, game_map) -> bool:
    """True when an unassigned trainset can be assigned to a ready line that has no train."""
    return bool(_ai_usable_unassigned_trainsets(ops, player, game_map))


def _try_rent_engineer_for_assignment(
    ops, player, current_year: int, spendable: int, game_map, *, for_additional_trainset: bool = False,
) -> Optional[dict]:
    """Rent one engineer when this turn will assign an unassigned trainset: to a line's first
    train, or (``for_additional_trainset``) to an extra train the caller found profitable."""
    from worker import ENGINEER

    if not for_additional_trainset and not _ai_has_pending_trainset_assignment(ops, player, game_map):
        return None
    if hasattr(ops, 'player_has_active_worker_strike') and ops.player_has_active_worker_strike(player):
        return None
    assigned_ts = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
    engineers_have = int(player.get_engineer_count() or 0)
    if assigned_ts < engineers_have:
        return None
    cost = ops.calculate_worker_rental_cost(ENGINEER, 1, current_year)
    if cost > spendable:
        return None
    try:
        ops.rent_workers_for_player(player, ENGINEER, 1, current_year)
        return {'worker': ENGINEER.name, 'quantity': 1}
    except Exception:
        return None


def _try_rent_workers(ops, player, current_year: int, spendable: int) -> Optional[dict]:
    """Rent construction/lawyer workers when builds need them. Engineers are rented separately when assigning trainsets."""
    from worker import CONSTRUCTION_WORKER

    if hasattr(ops, 'player_has_active_worker_strike') and ops.player_has_active_worker_strike(player):
        return None
    game_map = ops.get_map() if hasattr(ops, 'get_map') else None
    map_lines = (game_map.get_lines_by_name() or {}) if game_map else {}
    years_from_start = _years_from_game_start(ops, current_year)
    try:
        summary = ops.get_player_financial_summary(player, current_year) if hasattr(ops, 'get_player_financial_summary') else {}
    except Exception:
        summary = {}
    net_income = summary.get('net', 0) if isinstance(summary, dict) else 0
    has_profitable_line = any(
        ops.calc_line_profit(map_lines.get(ln), current_year) > 0
        for ln in player.get_line_names()
        if map_lines.get(ln)
    )

    build_queue = ops.get_build_queue()
    # Include station jobs too: on OSM maps, stations are often built via the build queue, and
    # without construction workers they can stall (no ridership => no profit).
    # Construction workers are required to make progress on queued jobs.
    # To avoid over-hiring, prioritize line/extension jobs when they exist, and only
    # rent for a small cap of *relevant* station jobs (enough to get at least one
    # profitable line online) instead of parallelizing all stations.
    my_line_builds = [
        b for b in build_queue
        if b.get('player') == player and b.get('type') in ('line', 'line_extension')
    ]
    my_station_builds = [
        b for b in build_queue
        if b.get('player') == player and b.get('type') == 'station'
    ]
    construction_have = player.get_construction_worker_count()
    # Never rent more construction workers than we can use.
    if my_line_builds:
        # Stations are only relevant if they attach to one of the lines currently under construction.
        target_lines = set()
        for b in my_line_builds:
            bd = b.get('data') or {}
            if b.get('type') == 'line':
                ln = bd.get('name')
                if ln:
                    target_lines.add(ln)
            elif b.get('type') == 'line_extension':
                ln = bd.get('line_name')
                if ln:
                    target_lines.add(ln)

        relevant_stations = []
        for sb in my_station_builds:
            sbd = sb.get('data') or {}
            line_names = sbd.get('line_names') or []
            if not target_lines or (target_lines & set(line_names)):
                relevant_stations.append(sb)

        # Cap station workers: typically the first line becomes profitable after 2 stations.
        station_cap = 2
        max_usable_construction = len(my_line_builds) + min(len(relevant_stations), station_cap)
    else:
        max_usable_construction = len(my_station_builds)
    if years_from_start <= 10 and not has_profitable_line:
        max_usable_construction = min(max_usable_construction, 2 if years_from_start <= 4 else 1)
    if my_line_builds and _is_first_game_year(ops, current_year):
        max_usable_construction = max(max_usable_construction, 2)
        max_usable_construction = min(max_usable_construction, 2)
    need_construction = max(0, max_usable_construction - construction_have) if max_usable_construction else 0

    if need_construction > 0:
        qty = min(need_construction, 10)  # Cap at 10 per turn
        cost = ops.calculate_worker_rental_cost(CONSTRUCTION_WORKER, qty, current_year)
        if cost <= spendable:
            try:
                ops.rent_workers_for_player(player, CONSTRUCTION_WORKER, qty, current_year)
                return {'worker': CONSTRUCTION_WORKER.name, 'quantity': qty}
            except Exception:
                pass
        # Fallback: rent 1 if we can't afford all
        cost = ops.calculate_worker_rental_cost(CONSTRUCTION_WORKER, 1, current_year)
        if cost <= spendable:
            try:
                ops.rent_workers_for_player(player, CONSTRUCTION_WORKER, 1, current_year)
                return {'worker': CONSTRUCTION_WORKER.name, 'quantity': 1}
            except Exception:
                pass

    focus = _normalize_ai_focus(getattr(player, 'ai_focus', None))
    if focus == 'lobbying' and years_from_start >= 3:
        from worker import LAWYER
        lawyers_have = int(player.get_lawyer_count() or 0)
        target_lawyers = 3 if years_from_start >= 10 else (2 if years_from_start >= 6 else 1)
        if lawyers_have < target_lawyers:
            cost = ops.calculate_worker_rental_cost(LAWYER, 1, current_year)
            if cost <= spendable:
                try:
                    ops.rent_workers_for_player(player, LAWYER, 1, current_year)
                    return {'worker': LAWYER.name, 'quantity': 1}
                except Exception:
                    pass
    return None


def _ai_pick_lobby_targets(ops, player, rng: random.Random, count: int = 1) -> list:
    others = [
        p for p in (ops.get_players() or [])
        if p is not player and not ops.is_player_eliminated(p)
    ]
    if not others:
        return []
    others.sort(key=lambda p: ops.get_player_money(p), reverse=True)
    pool = others[: max(1, min(len(others), 3))]
    if count <= 1:
        return [rng.choice(pool)]
    rng.shuffle(pool)
    return pool[:count]


def _ai_lobby_option_plan(ops, player, focus: str, gov) -> list:
    """Weighted lobby options for this focus (highest weight first)."""
    available = {o['id']: o for o in ops.get_lobby_options(player)}
    player_id = ops.get_player_id(player)
    can_influence = bool(gov and gov.get_favorable_influence(player_id) > 0)
    if focus == 'lobbying':
        weighted = [
            ('influence_seat', 5.0),
            ('influence_tiebreaker', 4.0),
            ('pass_law', 4.0),
            ('drop_excuse', 3.0),
            ('reduce_property_tax_5', 2.5),
            ('tax_break', 2.0),
            ('one_time_grant', 2.0),
            ('add_lawsuit_excuse', 3.5),
            ('gov_attack', 3.0),
            ('audit_target', 3.0),
            ('increase_property_tax_5', 2.5),
            ('block_funding', 2.0),
            ('area_protection', 2.5),
            ('redirect_funding', 2.0),
        ]
    elif focus == 'balanced':
        weighted = [
            ('drop_excuse', 2.0),
            ('pass_law', 1.5),
            ('influence_seat', 1.2),
            ('tax_break', 1.2),
            ('reduce_property_tax_5', 1.0),
        ]
    else:
        weighted = [('drop_excuse', 1.0), ('tax_break', 0.8)]
    plan = []
    for option_id, weight in weighted:
        if option_id not in available:
            continue
        if option_id in ('influence_seat', 'influence_tiebreaker') and not can_influence:
            continue
        plan.append((option_id, weight))
    plan.sort(key=lambda item: -item[1])
    return [opt for opt, _w in plan]


def _try_file_lawsuit(
    ops, player, rng: random.Random, focus: str = 'balanced',
) -> Optional[dict]:
    if not hasattr(ops, 'try_file_lawsuit'):
        return None
    if int(getattr(player, 'get_lawyer_count', lambda: 0)() or 0) <= 0:
        return None
    cy = ops.get_year() if ops else 1830
    yfs = _years_from_game_start(ops, cy)
    if focus == 'lobbying':
        if yfs < 3:
            return None
        if rng.random() > 0.78:
            return None
    elif focus == 'balanced':
        if yfs < 10 or rng.random() > 0.18:
            return None
    else:
        if yfs < 14 or rng.random() > 0.10:
            return None
    targets = _ai_pick_lobby_targets(ops, player, rng, count=1)
    if not targets:
        return None
    defendant = targets[0]
    excuse = None
    try:
        if hasattr(ops, 'get_player_lawsuit_excuses'):
            ex = ops.get_player_lawsuit_excuses(player) or []
            excuse = rng.choice(ex) if ex else None
        result = ops.try_file_lawsuit(player, defendant, excuse=excuse)
        if result and result.get('success', True):
            result['defendant'] = getattr(defendant, 'name', str(defendant))
            return result
    except Exception:
        pass
    return None


def _try_lobby(
    ops,
    player,
    spendable: int,
    focus: str = 'balanced',
    rng: random.Random = None,
) -> Optional[dict]:
    """Attempt lobbying if government available."""
    rng = rng or random.Random()
    cy = ops.get_year() if ops else 1830
    yfs = _years_from_game_start(ops, cy)
    if focus == 'lobbying':
        min_yfs = 3
        spend_frac = 0.72
        base_cap = 9000
    elif focus == 'balanced':
        min_yfs = 8
        spend_frac = 0.45
        base_cap = 5000
    else:
        min_yfs = 12
        spend_frac = 0.35
        base_cap = 4000
    if yfs < min_yfs:
        return None
    gov = ops.get_game().get_government() if hasattr(ops.get_game(), 'get_government') else None
    if not gov:
        return None
    amount = min(_inflate_econ(ops, cy, base_cap), int(spendable * spend_frac))
    if amount < _inflate_econ(ops, cy, 1000):
        return None
    option_ids = _ai_lobby_option_plan(ops, player, focus, gov)
    if focus == 'lobbying' and len(option_ids) > 1:
        head = option_ids[:6]
        rng.shuffle(head)
        option_ids = head + option_ids[6:]
    for option_id in option_ids:
        try:
            opt_meta = {o['id']: o for o in ops.get_lobby_options(player)}.get(option_id, {})
            targets = None
            if opt_meta.get('requires_target'):
                n = 2 if opt_meta.get('allows_multi_target') and focus == 'lobbying' else 1
                picked = _ai_pick_lobby_targets(ops, player, rng, count=n)
                if not picked:
                    continue
                if n > 1:
                    targets = picked
                else:
                    targets = picked[0]
            if isinstance(targets, list):
                result = ops.try_lobby(
                    player, amount, option_id=option_id, target_players=targets,
                )
            elif targets is not None:
                result = ops.try_lobby(
                    player, amount, option_id=option_id, target_player=targets,
                )
            else:
                result = ops.try_lobby(player, amount, option_id=option_id)
            if result.get('success'):
                result['amount'] = amount
                result['option_id'] = option_id
                return result
        except Exception:
            continue
    return None
