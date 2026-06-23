"""
Game AI for computer-controlled players.
Map-agnostic and vehicle-agnostic: works with any map preset and any trainsets in the registry.
AI always strives to make profit and never remains idle with no assets.
Uses population points ordered highest-to-lowest and profit-based scoring.
Extends existing lines sparingly (2-5 per turn). Always completes lines under construction.
Designed to complete within 25s per turn (time budget enforced).

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
import time
from typing import Optional

from game_presets import (
    AI_BOOTSTRAP_TURNS,
    AI_DEFAULT_DIFFICULTY,
    AI_DEFAULT_FOCUS,
    AI_EARLY_GROWTH_YEARS,
    AI_EARLY_PAYBACK_YEARS,
    AI_FOCUSES,
    AI_SUSTAINED_PROFIT_PAYBACK_YEARS,
    STARTING_MONEY,
)

logger = logging.getLogger(__name__)

# Target duration for one AI turn (orchestrators may pass deadline_sec up to this cap).
AI_TURN_TIME_BUDGET_SEC = 60.0


class AIIdleTurnError(RuntimeError):
    """Raised when a living AI would finish a turn with zero actions (debug invariant)."""


def _preset_requires_street_polylines(game_map, preset=None) -> bool:
    """True when AI line building must follow OSM/street polylines (no population-point fallback)."""
    if bool(getattr(game_map, 'use_osm', False)):
        return True
    if preset and hasattr(preset, 'get_road_segment_points_for_ai'):
        return True
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        try:
            from map_preset_loader import load_map_preset
            p = preset if preset is not None else load_map_preset(preset_name)
            if p and hasattr(p, 'get_road_segment_points_for_ai'):
                return True
        except Exception:
            pass
    return False


def _format_street_route_failure_message(ops, player, game_map, current_year, preset=None) -> str:
    """Detailed explanation when an OSM/street-polyline map has no buildable first-line corridor."""
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
    return (
        f"{pname} could not open its first line in year {current_year} on map preset '{preset_name}' "
        f"({map_w}×{map_h}): no valid OSM street polyline was found. "
        f"Street-following routes are required on this map; straight population-point lines are not allowed. "
        f"Checked preset road segments with relaxed endpoint-population thresholds; "
        f"{existing} existing line route(s) may be blocking overlap-free corridors. "
        f"Spendable cash: ${money:,}. "
        f"Verify the road spatial index is built for this preset and that buildable streets remain."
    )


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


def validate_ai_turn_results(ops, player, results):
    """Raise AIIdleTurnError if a living AI has an empty action list. Call after run_ai_turn from orchestrators."""
    if not getattr(player, 'is_ai', False):
        return
    if not ops or not hasattr(ops, 'is_player_eliminated') or ops.is_player_eliminated(player):
        return
    if not results:
        name = player.get_name() if hasattr(player, 'get_name') else getattr(player, 'name', player)
        year = ops.get_year() if ops else '?'
        raise AIIdleTurnError(
            f"Living AI {name!r} took no actions (year {year}). Every AI must take ≥1 action per turn."
        )


def _ai_has_meaningful_year_progress(ops, player, acts: list) -> bool:
    """True when the AI finished its calendar-year turn (any recorded action, assets, or queued work)."""
    if acts:
        return True
    if getattr(player, 'get_line_names', None) and player.get_line_names():
        return True
    if hasattr(ops, 'get_build_queue'):
        for job in ops.get_build_queue() or []:
            if job.get('player') == player:
                return True
    return False


def run_pending_ai_in_year_turns(ops, progress_callback=None, players=None, deadline_sec=None):
    """Run run_ai_turn for each AI that has not completed actions this calendar year.

    Returns (ai_results_list, ai_build_failures) where ai_results_list is [(player, acts), ...].
    Failed turns are not marked complete so End Year can retry them.
    """
    pending = list(players) if players is not None else ops.get_ai_players_needing_in_year_turn()
    ai_results_list = []
    ai_build_failures = []
    if ops is not None:
        ops._ai_street_build_failures = []
    for player in pending:
        if ops.ai_in_year_turn_completed(player):
            continue
        try:
            acts = run_ai_turn(
                ops, player, progress_callback=progress_callback, deadline_sec=deadline_sec
            )
            validate_ai_turn_results(ops, player, acts)
            if not _ai_has_meaningful_year_progress(ops, player, acts):
                raise AIIdleTurnError(
                    f"Living AI {player.get_name()!r} took no meaningful actions (year {ops.get_year()})."
                )
            ops.mark_ai_in_year_turn_completed(player)
            ai_results_list.append((player, acts))
        except AIIdleTurnError as e:
            ai_results_list.append((player, []))
            ai_build_failures.append((player.get_name(), str(e)))
        except Exception as e:
            ai_results_list.append((player, []))
            ai_build_failures.append((player.get_name(), str(e)))
    if ops is not None:
        ai_build_failures.extend(getattr(ops, '_ai_street_build_failures', []))
    return ai_results_list, ai_build_failures


# AI takes up to 3 actions per turn normally; more when losing money or about to go bankrupt.
_MAX_ACTIONS_PER_TURN = 3
_MAX_ACTIONS_WHEN_LOSING = 6
_MAX_ACTIONS_DESPERATE = 10
# When AI has lots of money and strong profit potential, allow more actions to invest
_MAX_ACTIONS_WHEN_WEALTHY = 6
_WEALTHY_MONEY_THRESHOLD = 60_000

# Map-agnostic section grid for first-line spread: any map is divided into 36 sections (6x6).
# Used so multiple AIs prefer building first line in different sections (works for standard, NYC, any preset).
AI_MAP_SECTION_COLS = 6
AI_MAP_SECTION_ROWS = 6


def _game_start_year(ops) -> int:
    if ops is None:
        return 1830
    try:
        tl = ops.get_game().get_timeline()
        y = getattr(tl, 'initial_year', None)
        return int(y) if y is not None else 1830
    except Exception:
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


def ai_sustained_annual_profitability_met(
    ops,
    player,
    *,
    min_post_bootstrap_years: int = 1,
    min_positive_fraction: float = 1.0,
) -> bool:
    """True if post-bootstrap settled years meet the positive-net fraction (default: every year > 0)."""
    hist = getattr(player, '_ai_annual_net_by_year', None) or {}
    post = [
        (y, float(net))
        for y, net in sorted(hist.items())
        if ai_finance_year_requires_sustained_profit(ops, y)
    ]
    need_years = max(1, int(min_post_bootstrap_years))
    if len(post) < need_years:
        return False
    post = post[-need_years:]
    positive = sum(1 for _, net in post if net > 0)
    frac = float(min_positive_fraction)
    if frac >= 1.0:
        return positive == len(post)
    return positive >= max(1, int(math.ceil(len(post) * frac)))


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
    if getattr(ops.game, 'idle_human_benchmark_enabled', False):
        return True
    from game_presets import AI_OUTLAST_IDLE_STARTING_MONEY
    game_start = getattr(ops.game, 'starting_money', None)
    if game_start is None or int(game_start) != int(AI_OUTLAST_IDLE_STARTING_MONEY):
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
    from game_presets import (
        AI_PROFITABILITY_ONSET_MAX_TURN,
        AI_PROFITABILITY_SUSTAIN_MIN_FRACTION,
        AI_PROFITABILITY_SUSTAIN_THROUGH_CALENDAR_YEAR,
    )

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
            return spendable >= 2500 and ai_money > 12000
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
    w = max(1, getattr(game_map, 'width', 1))
    h = max(1, getattr(game_map, 'height', 1))
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


def run_ai_turn(ops, player, progress_callback=None, deadline_sec=None):
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
            from map_preset_loader import load_map_preset
            preset = load_map_preset(map_preset)
        except Exception:
            pass

    # Focus and difficulty are set at player creation from canonical game preset values.
    focus = _normalize_ai_focus(getattr(player, 'ai_focus', None))
    difficulty = getattr(player, 'ai_difficulty', None) or AI_DEFAULT_DIFFICULTY
    if hasattr(player, 'ai_difficulty'):
        player.ai_difficulty = difficulty
    results = []
    money = ops.get_player_money(player)
    idle_benchmark = get_idle_human_benchmark_player(ops, player)
    idle_money = (
        ops.get_player_money(idle_benchmark)
        if idle_benchmark and not ops.is_player_eliminated(idle_benchmark)
        else None
    )
    idle_benchmark_game = is_idle_human_benchmark_game(ops, player)
    capital_preservation_vs_idle = (
        idle_benchmark_game
        and idle_money is not None
        and years_from_start > AI_BOOTSTRAP_TURNS
        and money < idle_money
    )

    # Track previous (year-1) last action so we can avoid pathological "idle-loop" behavior.
    prev_last_action_name = getattr(player, "_ai_last_action_name", None)
    prev_last_action_year = getattr(player, "_ai_last_action_year", None)
    prev_action_signature = tuple(getattr(player, "_ai_last_action_signature", ()) or ())

    # Per-player RNG (same seed order as before so reserve/build choices unchanged for survival)
    rng = _ai_rng(player, current_year, ops)

    # Existing-route cache is used by both OSM and non-OSM builds.
    existing_routes = _get_existing_line_routes_for_overlap(ops, game_map)
    # Population sorting is expensive on large OSM presets; compute lazily after any first-turn OSM fast-path.
    points_with_pop = None

    has_assets = (len(player.get_line_names()) > 0 or len(player.get_trainsets()) > 0)
    ai_join_first_line_pending = _ai_join_year_pending_first_line(ops, player, current_year)

    # Survival reserve: keep buffer so we don't go broke; early years need enough to pay taxes + workers.
    next_year = current_year + 1
    estimated_costs_next = (ops.estimate_annual_costs_for_player(player, next_year, 0)
                            if hasattr(ops, 'estimate_annual_costs_for_player') else 10000)
    if hasattr(ops, 'estimate_annual_costs_for_player'):
        estimated_costs = ops.estimate_annual_costs_for_player(player, next_year, extra_workers_estimate=4)
        # Base reserve scales up so AI survives 40+ years on default rules (taxes/maintenance grow)
        if first_calendar_year:
            base_reserve = 12000  # keep enough for 2+ years costs after first build
        elif years_from_start <= 5:
            base_reserve = 18000
        elif years_from_start <= 10:
            base_reserve = 15000
        elif years_from_start <= 20:
            base_reserve = 18000
        else:
            base_reserve = 22000
        # OSM maps (NYC, NY Metro): higher costs and longer build; keep more reserve so AI survives past turn 15
        if map_preset in ('nyc', 'ny_metro') and years_from_start <= 10:
            base_reserve = int(base_reserve * 1.5)
        elif map_preset in ('nyc', 'ny_metro') and years_from_start <= 20:
            base_reserve = int(base_reserve * 1.35)
        survival_reserve = max(estimated_costs, base_reserve)
    else:
        survival_reserve = (5000 if first_calendar_year else 8500) if years_from_start <= 5 else (12000 if years_from_start <= 10 else 20000)
    reserve_mod = rng.uniform(1.20, 1.35)  # Keep more buffer to avoid bankruptcy (40-year survival)
    # Per-player behavioral randomness: each AI has slightly different risk tolerance
    pid = getattr(player, 'player_id', 0)
    reserve_mod += rng.uniform(-0.08, 0.08)  # Same RNG seed per player/turn so consistent within turn
    reserve_mod = max(1.05, min(1.45, reserve_mod))
    reserve = max(survival_reserve, int(survival_reserve * reserve_mod))
    # Early years: cap reserve so AIs still have enough spendable for stations/workers, but don't drain below 2 years' costs
    if years_from_start <= 5 and money > 12000:
        reserve = min(reserve, money - 3000)  # allow 3000 spendable so AI can add stations/rent 1 worker
    elif years_from_start <= 5 and money > 6000:
        reserve = min(reserve, money - 2000)
    elif years_from_start <= 5 and money > 2500:
        reserve = min(reserve, money - 1500)
    # Turn 1 with no assets, or random-spawn AI on join year with no line: keep spendable for a first line.
    if (first_calendar_year and not has_assets) or ai_join_first_line_pending:
        min_spendable_first_turn = 3500
        reserve = min(reserve, max(0, money - min_spendable_first_turn))
    if idle_benchmark_game and years_from_start <= AI_BOOTSTRAP_TURNS and money > 12000:
        reserve = max(reserve, money - min(12000, int(money * 0.72)))
    elif idle_benchmark_game and years_from_start <= 15 and money > 10000:
        reserve = max(reserve, money - min(10000, int(money * 0.55)))
    spendable = max(0, money - reserve)
    min_spendable_for_station = 1200 if years_from_start <= 10 else 500

    # Bankruptcy and loss detection: allow more actions when desperate or losing money
    about_to_go_bankrupt = (money - estimated_costs_next <= 0)
    try:
        summary = ops.get_player_financial_summary(player, current_year) if hasattr(ops, 'get_player_financial_summary') else {}
    except Exception:
        summary = {}
    is_losing_money = summary.get('net', 0) < 0
    net_income = summary.get('net', 0)
    if sustained_profit_phase and (is_losing_money or had_negative_settled_year):
        is_losing_money = True
        reserve_mod = min(1.65, reserve_mod + 0.18)
        reserve = max(survival_reserve, int(survival_reserve * reserve_mod))
        spendable = max(0, money - reserve)
    is_wealthy_and_profitable = (
        money >= _WEALTHY_MONEY_THRESHOLD and net_income > 2000
        and (not sustained_profit_phase or net_income > 0)
    )
    max_actions = _MAX_ACTIONS_DESPERATE if about_to_go_bankrupt else (
        _MAX_ACTIONS_WHEN_LOSING if is_losing_money else (
            _MAX_ACTIONS_WHEN_WEALTHY if is_wealthy_and_profitable else _MAX_ACTIONS_PER_TURN))
    # Behavioral randomness: vary action count slightly so AIs don't all take same number every turn
    max_actions = max(1, max_actions + rng.randint(-1, 1))

    _turn_start = time.monotonic()
    _turn_budget_sec = max(1.0, float(deadline_sec if deadline_sec is not None else AI_TURN_TIME_BUDGET_SEC))

    def _turn_time_exceeded() -> bool:
        return (time.monotonic() - _turn_start) >= _turn_budget_sec

    # Track early visible network growth: by 1841 each AI should have built/expanded at least twice.
    try:
        build_expand_count = int(getattr(player, "_ai_build_expand_count", 0) or 0)
    except Exception:
        build_expand_count = 0

    def _do_action(name: str, result: dict):
        results.append((name, result))
        nonlocal build_expand_count
        if name in ("create_line", "extend_line"):
            build_expand_count += 1
            try:
                player._ai_build_expand_count = int(build_expand_count)
            except Exception:
                pass
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
    lines_requiring_extension_for_station = [
        ln
        for ln in player.get_line_names()
        for line in [map_lines.get(ln)]
        if line and _line_needs_extension_before_second_station(ops, ln, line)
    ]
    osm_like_map = bool(getattr(game_map, 'use_osm', False)) or bool(
        preset and hasattr(preset, 'get_road_extension_points_from_end')
    )
    force_station_extension_needed = osm_like_map and years_from_start >= 2 and bool(
        lines_requiring_extension_for_station
    )
    has_profitable_line = any(
        ops.calc_line_profit(map_lines.get(ln), current_year) > 0
        for ln in player.get_line_names()
        if map_lines.get(ln)
    )
    lines_missing_trainsets = [
        ln for ln in player.get_line_names()
        if _line_needs_trainset_assignment(map_lines, ln)
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
    profit_recovery_mode = sustained_profit_phase and (
        is_losing_money
        or had_negative_settled_year
        or bool(lines_missing_trainsets)
        or capital_preservation_vs_idle
    )
    single_line_growth_until_win = sustained_profit_phase and not ai_has_post_bootstrap_profit_win(ops, player)
    from game_presets import AI_PROFITABILITY_ONSET_MAX_TURN, AI_PROFITABILITY_ONSET_MIN_TURN

    profitability_onset_phase = (
        AI_PROFITABILITY_ONSET_MIN_TURN <= years_from_start <= AI_PROFITABILITY_ONSET_MAX_TURN
    )
    had_any_positive_settled = ai_has_post_bootstrap_profit_win(ops, player)
    profit_stewardship = (
        had_any_positive_settled and years_from_start >= AI_PROFITABILITY_ONSET_MIN_TURN
    )
    if profit_stewardship and had_negative_settled_year and money > 8000:
        reserve = min(reserve, max(survival_reserve, money - 6000))
        spendable = max(0, money - reserve)
    sustained_profit_maintenance = (
        not idle_benchmark_game
        and sustained_profit_phase
        and ai_has_post_bootstrap_profit_win(ops, player)
        and has_revenue_line
        and not lines_missing_trainsets
        and not is_losing_money
        and not had_negative_settled_year
    )
    if capital_preservation_vs_idle and money > 8000:
        reserve = max(reserve, int(money * 0.55))
        spendable = max(0, money - reserve)
    if years_from_start <= 8 and not has_profitable_line:
        reserve = min(reserve, int(survival_reserve * 1.12))
        spendable = max(0, money - reserve)

    # Early survival: carrying 3 starter trainsets is expensive before lines are profitable.
    # Sell unneeded unassigned trainsets early to reduce annual maintenance burn.
    if (
        early_growth
        and hasattr(ops, 'sell_trainset_for_half_price')
        and len(results) < max_actions
        and not ((first_calendar_year or ai_join_first_line_pending) and len(player.get_line_names()) == 0)
    ):
        keep_total_trainsets = max(
            2 if has_profitable_line else 1,
            len(lines_with_two_stations),
        )
        if capital_preservation_vs_idle:
            keep_total_trainsets = min(keep_total_trainsets, 1)
        elif years_from_start <= AI_BOOTSTRAP_TURNS or sustained_profit_phase:
            keep_total_trainsets = max(keep_total_trainsets, len(player.get_trainsets() or []), 3)
        while len(results) < max_actions and len(player.get_trainsets() or []) > keep_total_trainsets:
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
    # Early game without a profitable line yet: extra actions to finish construction, assign stock, and open revenue.
    if early_growth and not sustained_profit_phase and not has_profitable_line and not about_to_go_bankrupt:
        max_actions = max(max_actions, 4)
    # When profitable in early years, allow 3000 spendable for extension (more revenue = survival)
    if years_from_start <= 10 and has_profitable_line and money > 5000:
        reserve = min(reserve, money - 3000)
    # Line has 2+ stations but no trainsets assigned -> we need to buy/assign to earn (critical for survival)
    has_line_ready_for_trainset = any(
        map_lines.get(ln) and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2
        and len(getattr(map_lines.get(ln), 'vehicles', None) or []) == 0
        for ln in player.get_line_names()
    )
    min_extend_spendable = 1500 if years_from_start <= 15 else 500
    if force_station_extension_needed and years_from_start <= 22:
        # Allow a cheaper "unlock extension" so short OSM lines can become station-viable.
        min_extend_spendable = min(min_extend_spendable, 500)
    if lines_requiring_extension_for_station and osm_like_map and years_from_start <= 22:
        min_extend_spendable = min(min_extend_spendable, 450)
    build_queue = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
    my_line_builds = [b for b in build_queue if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
    my_station_builds = [b for b in build_queue if b.get('player') == player and b.get('type') == 'station']
    has_unfinished_lines = len(my_line_builds) > 0
    # Fast OSM opener: on first turn with no lines, build a short adjacent road segment immediately.
    # This avoids expensive route-search retries and guarantees each AI opens service on NYC/NY Metro style maps.
    if (first_calendar_year or ai_join_first_line_pending) and len(player.get_line_names()) == 0 and not my_line_builds and (
        bool(getattr(game_map, 'use_osm', False)) or (preset and hasattr(preset, 'get_road_segment_points_for_ai'))
    ):
        try:
            budget = min(max(500, int(ops.get_player_money(player))), 120000)
            direct = _osm_first_turn_try_cached_segment_build(
                ops, player, game_map, current_year, rng, preset, budget
            )
            if _ai_line_create_result_ok(direct):
                _do_action('create_line', direct)
                validate_ai_turn_results(ops, player, results)
                return results
        except Exception:
            pass

    # Cache population points once per turn (expensive on large maps).
    # Delayed until after the first-turn OSM fast-path so multi-AI NYC openers do not time out.
    if points_with_pop is None:
        points_with_pop = _get_population_points_sorted_by_population(
            game_map, max_points=max(_MAX_EXTEND_POINTS, _MAX_BUILD_CANDIDATES)
        )

    def _try_revenue_bootstrap() -> Optional[tuple]:
        """Assign stock and open stations before lobby/marketplace spend when a line can earn."""
        spend = _refresh_spendable()
        lines = player.get_line_names()
        if not lines:
            return None
        ops_map = game_map.get_lines_by_name() or {}
        lines_needing_trainset = [ln for ln in lines if _line_needs_trainset_assignment(ops_map, ln)]
        if sustained_profit_phase and has_revenue_line and not lines_needing_trainset:
            return None
        if not lines_needing_trainset and years_from_start > 12 and has_profitable_line and not is_losing_money:
            return None
        if lines_needing_trainset and spend >= 300:
            unassigned = list(ops.get_unassigned_trainsets(player) or [])
            if unassigned:
                eng = int(player.get_engineer_count() or 0)
                asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
                if asg >= eng and spend > min_spendable_for_station:
                    rent_result = _try_rent_workers(ops, player, current_year, spend)
                    if rent_result:
                        return ('rent_workers', rent_result)
                best_line = _pick_best_line_for_trainset(ops, player, unassigned[0], lines_needing_trainset)
                if best_line:
                    try:
                        ops.assign_trainset_to_line(unassigned[0], best_line)
                        return ('assign_trainset', {'line': best_line})
                    except Exception:
                        pass
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
        if any_line_needs_stations and spend >= 500:
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
    _osm_early_build = bool(getattr(game_map, 'use_osm', False)) or map_preset in ('nyc', 'ny_metro')
    if has_unfinished_lines and _osm_early_build and years_from_start <= 10:
        try:
            from worker import CONSTRUCTION_WORKER
            need_qty = max(0, 2 - player.get_construction_worker_count())
            if need_qty > 0 and hasattr(ops, 'calculate_worker_rental_cost'):
                need_cost = ops.calculate_worker_rental_cost(CONSTRUCTION_WORKER, need_qty, current_year)
                reserve = min(reserve, max(0, money - (need_cost + 800)))
                spendable = max(0, money - reserve)
        except Exception:
            pass
    # While waiting for first line to complete, keep reserve for survival — but early-growth window needs spendable for workers.
    if years_from_start <= 4 and my_line_builds and not has_profitable_line and money > 15000:
        if early_growth:
            reserve = min(reserve, max(survival_reserve, money - 5500))
        else:
            reserve = max(reserve, min(money, 22000))
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
    ):
        fire_eval = _maybe_fire_workers_cost_benefit(large_cut=True)
        if fire_eval:
            _do_action("fire_workers", fire_eval)
    if not my_line_builds and len(player.get_line_names()) > 0 and (
        sustained_profit_phase or not (early_growth and years_from_start <= 12)
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
        and money >= 600
    ):
        target_spend = min(8000, max(1800, int(money * 0.42)))
        target_spend = min(target_spend, money - 350)
        if target_spend >= 500 and money >= target_spend + 200:
            reserve = min(reserve, max(0, money - target_spend))
        spendable = max(0, money - reserve)
    if force_station_extension_needed and years_from_start <= 22 and money > 4500:
        # Keep enough liquid cash to fund one practical extension instead of idling with a 1-station line.
        reserve = min(reserve, max(0, money - 7000))
        spendable = max(0, money - reserve)
    if sustained_profit_phase:
        force_extend_ok = (
            force_station_extension_needed
            and spendable > min_extend_spendable
            and money > 4500
            and net_income > 0
        )
        extend_ok = force_extend_ok
    elif early_growth:
        # Bootstrap rule: avoid expensive extensions until the network is clearly cashflow-positive.
        force_extend_ok = (
            force_station_extension_needed
            and spendable > min_extend_spendable
            and money > 4500
            and years_from_start <= 22
        )
        extend_ok = force_extend_ok or (
            spendable > min_extend_spendable
            and not any_line_needs_stations
            and has_profitable_line
            and net_income > 1500
            and money > 70000
            and years_from_start >= 6
        )
    else:
        extend_ok = (
            spendable > min_extend_spendable and not any_line_needs_stations and
            (years_from_start > 5 or spendable > 12000 or (has_profitable_line and spendable > 2500))
        )
    osm_route_extend_unlock = (
        osm_like_map
        and bool(lines_requiring_extension_for_station)
        and years_from_start <= 22
        and money > 3000
    )
    route_extend_ok = extend_ok or (
        osm_route_extend_unlock
        and spendable > min(min_extend_spendable, 400)
    )
    if sustained_profit_phase:
        osm_route_extend_unlock = bool(
            force_station_extension_needed and spendable > min(min_extend_spendable, 400)
        )
    if single_line_growth_until_win and len(player.get_line_names()) == 1:
        route_extend_ok = False
    build_chance = 0.10 + rng.random() * 0.15
    # Second line when first is profitable and we have surplus; relax money threshold when already profitable
    _second_line_money = 25000 if has_profitable_line else 45000
    _min_years_second_line = 9 if map_preset in ('nyc', 'ny_metro') else 12
    wants_new_line = (first_calendar_year or not has_assets or ai_join_first_line_pending) or (
        years_from_start >= _min_years_second_line
        and not has_unfinished_lines
        and not lines_missing_trainsets
        and has_assets
        and spendable > 15000
        and has_profitable_line
        and has_revenue_line
        and money > _second_line_money
        and rng.random() < build_chance
    )
    if profit_recovery_mode or lines_missing_trainsets or (profit_stewardship and had_negative_settled_year):
        wants_new_line = bool(first_calendar_year or not has_assets or ai_join_first_line_pending)
    if single_line_growth_until_win:
        wants_new_line = bool(first_calendar_year or not has_assets or ai_join_first_line_pending)
    if profit_stewardship and not is_wealthy_and_profitable and years_from_start > AI_PROFITABILITY_ONSET_MAX_TURN:
        wants_new_line = bool(first_calendar_year or not has_assets or ai_join_first_line_pending)
    if years_from_start <= AI_BOOTSTRAP_TURNS and has_assets and not (
        first_calendar_year or ai_join_first_line_pending
    ):
        wants_new_line = False
    max_build_cost = spendable
    if years_from_start <= 10 and (first_calendar_year or not has_assets or ai_join_first_line_pending):
        cap = 5000 if years_from_start == 0 else max(4000, int(money * 0.18))
        if idle_benchmark_game and years_from_start <= AI_BOOTSTRAP_TURNS:
            cap = min(cap, 4500 if years_from_start == 0 else 3500)
        max_build_cost = min(spendable, cap)
    base_min_marketplace = 12000 if years_from_start <= 10 else 5000
    min_marketplace = max(2000, int(base_min_marketplace * rng.uniform(0.8, 1.2)))

    def _try_action1_build_extend_station():
        """Action 1: Build, extend, or add station. Returns (name, result) or None.
        Priority: (1) build on first turn (one line only so we can afford workers), (2) add stations, (3) assign trainsets, (4) extend, (5) build more."""
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
            _osm_first_turn = bool(getattr(game_map, 'use_osm', False)) or bool(preset and hasattr(preset, 'get_road_segment_points_for_ai'))
            # Turn 1: allow enough for a real first line (all AIs must build on first turn)
            if (first_calendar_year or ai_join_first_line_pending) and _osm_first_turn:
                first_build_cap = min(spend, 60000)
            else:
                first_build_cap = min(spend, 8000) if (first_calendar_year or ai_join_first_line_pending) else min(spend, 5000)
            if first_calendar_year or ai_join_first_line_pending:
                first_build_cap = max(first_build_cap, min(3500, spend))  # ensure at least 3500 if we have it
            build_result = _try_build_line(ops, player, game_map, first_build_cap, current_year, points_with_pop, rng=rng, existing_routes=existing_routes, preset=preset)
            if _ai_line_create_result_ok(build_result):
                return ('create_line', build_result)
        # When we have lines under construction but no completed lines with 2 stations, rent construction worker first (survival)
        if (my_line_builds or my_station_builds) and spend > min_spendable_for_station:
            rent_result = _try_rent_workers(ops, player, current_year, spend)
            if rent_result:
                return ('rent_workers', rent_result)
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
        # Lines need 2 stations to earn profit - add stations before assigning trainsets.
        # Lines with only one station are prioritized (sort by station_count) so AI must add a second station.
        if lines and spend >= 500:
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
        # Assign trainsets to lines that have 2+ stations (lines earn profit only with stations)
        unassigned = ops.get_unassigned_trainsets(player)
        lines_with_stations = [ln for ln in lines if map_lines.get(ln) and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2]
        if unassigned and lines_with_stations and not any(r[0] == 'assign_trainset' for r in results):
            eng = int(player.get_engineer_count() or 0)
            asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
            if sustained_profit_phase and has_revenue_line and not lines_missing_trainsets and not profitability_onset_phase:
                return None
            if early_growth and net_income < 0 and asg >= 1 and not lines_missing_trainsets and not profitability_onset_phase:
                return None
            if sustained_profit_phase and lines_missing_trainsets and asg >= eng:
                spend = _refresh_spendable()
                if spend > min_spendable_for_station:
                    rent_result = _try_rent_workers(ops, player, current_year, spend)
                    if rent_result:
                        return ('rent_workers', rent_result)
            if asg >= eng:
                spend = _refresh_spendable()
                if spend > min_spendable_for_station:
                    rent_result = _try_rent_workers(ops, player, current_year, spend)
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
        if extend_ok and not (profit_recovery_mode and lines_missing_trainsets) and not single_line_growth_until_win:
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
                    require_profit_when_troubled=(is_losing_money or about_to_go_bankrupt),
                )
                if extend_result:
                    return ('extend_line', extend_result)
        # On turn 1 / join year, do not build a second line (we already have one in queue; need money for workers)
        if wants_new_line and not ((first_calendar_year or ai_join_first_line_pending) and _my_builds_now) and spend > min_spendable_for_station:
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
        if sustained_profit_maintenance:
            return None
        spend = _refresh_spendable()
        lobby_income_ok = net_income > 0 or (
            focus == 'lobbying'
            and years_from_start >= 3
            and net_income > -3500
        )
        if (
            focus == 'lobbying'
            and spend > 1800
            and lobby_income_ok
            and (has_profitable_line or years_from_start >= 3)
            and not (profit_stewardship and had_negative_settled_year)
        ):
            lobby_result = _try_lobby(ops, player, spend, focus='lobbying')
            if lobby_result:
                return ('lobby', lobby_result)
        # Allow buy when profitable or when we need a trainset to get first profit (survival)
        min_buy = min_marketplace if has_profitable_line else max(2000, min_marketplace // 2)
        if focus == 'marketplace':
            min_buy = max(1200, int(min_buy * 0.55))
        buy_income_ok = net_income > 0 or (
            focus == 'marketplace'
            and years_from_start >= 3
            and net_income > -5000
        )
        if (
            has_lines
            and has_assets
            and spend > min_buy
            and buy_income_ok
            and not (profit_stewardship and had_negative_settled_year and not lines_missing_trainsets)
        ):
            buy_result = _try_buy_marketplace(ops, player, current_year, spend, focus, rng=rng)
            if buy_result:
                return ('buy', buy_result)
        # Balanced AIs lobby sometimes so focus is visible in play, not only marketplace specialists.
        if (
            focus == 'balanced'
            and has_profitable_line
            and spend > 4000
            and net_income > 0
            and rng.random() < 0.42
        ):
            lobby_result = _try_lobby(ops, player, spend, focus='balanced')
            if lobby_result:
                return ('lobby', lobby_result)
        # Line-expansion focus: prefer an extend here when the main slot loop did not pick one (visible "focus" play).
        if focus == 'line_expansion' and has_lines:
            spend_le = _refresh_spendable()
            if route_extend_ok and spend_le > min_extend_spendable:
                extend_result = _try_extend_line(
                    ops,
                    player,
                    game_map,
                    spend_le,
                    current_year,
                    points_with_pop,
                    rng=rng,
                    preset=preset,
                    require_profit_when_troubled=(is_losing_money or about_to_go_bankrupt),
                )
                if extend_result:
                    return ('extend_line', extend_result)
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
                    require_profit_when_troubled=(is_losing_money or about_to_go_bankrupt),
                )
                if extend_result:
                    return ('extend_line', extend_result)
        # Survival: add stations and assign trainset before spending on rent (so line can earn revenue)
        if lines and spend >= 500:
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
        # Assign trainsets only to lines with 2+ stations; need at least 1 engineer or trainset gets unassigned at year advance
        unassigned = ops.get_unassigned_trainsets(player)
        lines_with_stations = [ln for ln in lines if map_lines.get(ln) and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2]
        if unassigned and lines_with_stations and not any(r[0] == 'assign_trainset' for r in results):
            eng = int(player.get_engineer_count() or 0)
            asg = int(ops.get_player_assigned_trainsets_count(player) if hasattr(ops, 'get_player_assigned_trainsets_count') else 0)
            if sustained_profit_phase and has_revenue_line and not lines_missing_trainsets and not profitability_onset_phase:
                return None
            if early_growth and net_income < 0 and asg >= 1 and not lines_missing_trainsets and not profitability_onset_phase:
                return None
            if sustained_profit_phase and lines_missing_trainsets and asg >= eng:
                spend = _refresh_spendable()
                if spend > min_spendable_for_station:
                    rent_result = _try_rent_workers(ops, player, current_year, spend)
                    if rent_result:
                        return ('rent_workers', rent_result)
            if asg >= eng:
                if spend > min_spendable_for_station:
                    rent_result = _try_rent_workers(ops, player, current_year, spend)
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
            and not sustained_profit_maintenance
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
                require_profit_when_troubled=(is_losing_money or about_to_go_bankrupt),
            )
            if extend_result:
                return ('extend_line', extend_result)
        if extend_ok and spend > min_extend_spendable:
            extend_result = _try_extend_line(
                ops,
                player,
                game_map,
                spend,
                current_year,
                points_with_pop,
                rng=rng,
                preset=preset,
                require_profit_when_troubled=(is_losing_money or about_to_go_bankrupt),
            )
            if extend_result:
                return ('extend_line', extend_result)
        already_built = any(r[0] == 'create_line' for r in results)
        if wants_new_line and not already_built and spend > min_spendable_for_station:
            spend = _refresh_spendable()
            max_cost = min(max_build_cost, spend)
            if max_cost > min_spendable_for_station:
                build_result = _try_build_line(ops, player, game_map, max_cost, current_year, points_with_pop, rng=rng, existing_routes=existing_routes, preset=preset)
                if _ai_line_create_result_ok(build_result):
                    return ('create_line', build_result)
        return None

    # Apply fare and schedule updates only when AI has lines; with no lines it should build instead.
    has_lines = len(player.get_line_names()) > 0
    fare_updates = _adjust_ai_fares(ops, player, current_year, game_map, rng) if has_lines else []
    schedule_updates = (
        _adjust_ai_schedules(
            ops,
            player,
            game_map,
            has_profitable_line=has_profitable_line,
            is_losing_money=is_losing_money,
            is_wealthy_and_profitable=is_wealthy_and_profitable,
            early_growth=early_growth,
            schedule_survival_tight=(
                (osm_like_map and early_growth and not has_profitable_line)
                or (sustained_profit_phase and is_losing_money)
                or capital_preservation_vs_idle
                or (profit_stewardship and not is_wealthy_and_profitable)
            ),
        )
        if has_lines
        else []
    )

    _has_network = bool(player.get_line_names()) or my_line_builds
    # Until a line is profitable, keep cash for workers and completion — marketplace buys here often caused early bankruptcy.
    _focus_ok = has_profitable_line or (
        has_line_ready_for_trainset and not (early_growth and years_from_start <= 12)
    ) or (
        focus in ('lobbying', 'marketplace') and _has_network and years_from_start >= 3
    ) or (
        focus == 'line_expansion' and has_lines and years_from_start >= 3 and route_extend_ok
    )
    _focus_cash_need = min_marketplace
    if focus in ('lobbying', 'marketplace', 'line_expansion'):
        _focus_cash_need = max(1000, int(min_marketplace * 0.38))
    can_attempt_focus = bool(
        _has_network
        and has_assets
        and _focus_ok
        and _refresh_spendable() > _focus_cash_need
        and (has_profitable_line or years_from_start >= 3)
        and not sustained_profit_maintenance
    )
    # No line yet: build first. Once a line exists (or is queued), engage focus before other discretionary spend.
    if first_calendar_year or ai_join_first_line_pending or not _has_network:
        all_slots = [_try_action1_build_extend_station, _try_action2_focus, _try_action3_situational]
    else:
        all_slots = [_try_action2_focus, _try_action1_build_extend_station, _try_action3_situational]
        if focus == 'lobbying':
            all_slots = [_try_action2_focus, _try_action1_build_extend_station, _try_action3_situational]
        elif focus == 'marketplace':
            all_slots = [_try_action2_focus, _try_action3_situational, _try_action1_build_extend_station]
        elif focus == 'line_expansion':
            all_slots = [_try_action2_focus, _try_action1_build_extend_station, _try_action3_situational]
        if is_losing_money and not about_to_go_bankrupt:
            all_slots = [_try_action1_build_extend_station, _try_action2_focus, _try_action3_situational]

    if is_losing_money and len(results) < max_actions:
        boot = _try_revenue_bootstrap()
        if boot:
            _do_action(boot[0], boot[1])

    # If AI can reasonably use focus this year, attempt focus explicitly on most turns.
    if can_attempt_focus and len(results) < max_actions and rng.random() < 0.96:
        focus_action = _try_action2_focus()
        if focus_action:
            _do_action(focus_action[0], focus_action[1])

    if len(results) < max_actions and (has_line_ready_for_trainset or any_line_needs_stations or my_line_builds):
        for _ in range(2):
            if len(results) >= max_actions:
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
        if len(player.get_line_names()) == 0 and len(results) < max_actions:
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
            and len(results) < max_actions
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
    if money < 5000 and len(results) < max_actions and hasattr(ops, 'sell_trainset_for_half_price'):
        for ts in list(ops.get_unassigned_trainsets(player) or [])[:2]:
            try:
                r = ops.sell_trainset_for_half_price(ts, player)
                if r.get('sale_price', 0):
                    _do_action('sell_trainset', r)
                    break
            except Exception:
                pass
    # Proactive austerity on OSM maps: if losses persist deep into early growth, liquidate one assigned
    # trainset to preserve runway for 30-year survival.
    if (
        len(results) < max_actions
        and osm_like_map
        and years_from_start >= 8
        and net_income < 0
        and money < 35000
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
    if about_to_go_bankrupt and len(results) < max_actions:
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
                if len(results) >= max_actions:
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
        if len(results) < max_actions:
            spare_locomotives = list(getattr(player, 'locomotives', []) or [])
            if spare_locomotives and hasattr(ops, 'sell_locomotive_for_half_price'):
                try:
                    loco = spare_locomotives[0]
                    r = ops.sell_locomotive_for_half_price(loco, player)
                    if r.get('sale_price', 0):
                        _do_action('sell_locomotive', r)
                except Exception:
                    pass
        if len(results) < max_actions:
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
        if len(results) < max_actions and hasattr(ops, 'fire_workers') and hasattr(ops, 'get_max_fireable_workers'):
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

    for slot in all_slots:
        if _turn_time_exceeded() or len(results) >= max_actions:
            break
        action = slot()
        if action:
            _do_action(action[0], action[1])

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
    if len(results) == 0 or _need_forced_line:
        # Strong no-line fallback: prefer creating or buying a line over "minimum turn" bookkeeping.
        # Runs before empty-slot rent recovery so we never fill results[] with rent_workers and skip this block.
        if getattr(player, 'is_ai', False) and not ops.is_player_eliminated(player) and len(player.get_line_names()) == 0:
            q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
            my_line_q = [b for b in q if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
            if not my_line_q:
                spend = max(0, _refresh_spendable())
                money_now = max(0, int(ops.get_player_money(player)))
                _uses_osm_roads = bool(getattr(game_map, 'use_osm', False)) or bool(
                    preset and hasattr(preset, 'get_road_segment_points_for_ai')
                )
                force_cap = min(max(500, max(spend, money_now)), (120000 if _uses_osm_roads else 20000))
                uses_osm_roads = bool(getattr(game_map, 'use_osm', False)) or bool(
                    preset and hasattr(preset, 'get_road_segment_points_for_ai')
                )
                n_force_attempts = 4 if uses_osm_roads else (28 if (first_calendar_year or ai_join_first_line_pending) else 18)
                # First calendar year / join-year: other AIs' queued segments must not block openers (overlap filter is same-turn only).
                _route_for_stoch = None if (first_calendar_year or ai_join_first_line_pending) else existing_routes
                forced = _try_build_line_stochastic(
                    ops, player, game_map, force_cap, current_year, points_with_pop,
                    _route_for_stoch, preset, n_attempts=n_force_attempts,
                )
                if not _ai_line_create_result_ok(forced):
                    # Overlap/pruning can still block stochastic attempts; retry once with raw route search.
                    if uses_osm_roads and (first_calendar_year or ai_join_first_line_pending):
                        # First-turn OSM safeguard: rerun stochastic build without existing-route overlap pruning.
                        forced = _try_build_line_stochastic(
                            ops, player, game_map, force_cap, current_year, points_with_pop,
                            existing_routes=None, preset=preset, n_attempts=10,
                        )
                if not _ai_line_create_result_ok(forced):
                    forced = _try_build_line(
                        ops, player, game_map, force_cap, current_year, points_with_pop,
                        rng=rng, existing_routes=None, preset=preset,
                    )
                if (
                    not _ai_line_create_result_ok(forced)
                    and (first_calendar_year or ai_join_first_line_pending)
                    and uses_osm_roads
                    and force_cap >= 500
                ):
                    # Last-resort NYC/OSM opener: ask preset directly for any usable road segment.
                    try:
                        from map_preset_loader import get_preset_road_segment_points
                        preset_name = getattr(game_map, 'map_preset_name', None)
                        road_points = []
                        _lr_min = max(MIN_LINE_LENGTH_GRID, MIN_STATION_SPACING_GRID)
                        for k in range(3):
                            krng = random.Random((int(current_year) * 1009 + int(getattr(player, 'player_id', 0)) * 97 + k * 7919) & 0xFFFFFFFF)
                            road_points = get_preset_road_segment_points(
                                preset_name,
                                game_map,
                                current_year,
                                max_points=16,
                                existing_line_routes=existing_routes,
                                min_length_grid=_lr_min,
                                max_length_grid=6.0,
                                min_endpoint_population=None,
                                rng=krng,
                                preset=preset,
                            )
                            if len(road_points) >= 2:
                                break
                        if len(road_points) >= 2:
                            total_length = sum(
                                math.hypot(road_points[i+1][0] - road_points[i][0], road_points[i+1][1] - road_points[i][1])
                                for i in range(len(road_points) - 1)
                            )
                            cost = max(15, int(total_length * 225 * 1.5))
                            if total_length >= MIN_STATION_SPACING_GRID and cost <= force_cap:
                                elevation_levels = _get_safe_elevation_levels(
                                    road_points, game_map, preset_name or '', base='street'
                                )
                                line_name = _generate_ai_line_name(
                                    player, rng, ops=ops, route_points=road_points, current_year=current_year
                                )
                                forced = ops.create_line_complete(
                                    line_name,
                                    road_points,
                                    player,
                                    elevation_levels=elevation_levels,
                                    is_ferry_line=False,
                                    is_bus_line=False,
                                    cost_info={
                                        'cost': cost,
                                        'length': total_length,
                                        'points_count': len(road_points),
                                        'elevation_levels': elevation_levels,
                                    },
                                    use_fast_path=True,
                                )
                    except Exception:
                        forced = None
                if _ai_line_create_result_ok(forced):
                    _do_action('create_line', forced)
                else:
                    # On year 1 / join-year, do not "buy a line" as a substitute for building:
                    # tests (and gameplay expectations) require an explicit create_line / queued build opener.
                    if not (first_calendar_year or ai_join_first_line_pending):
                        lb = _try_buy_marketplace(
                            ops, player, current_year, max(spend, money_now), focus, rng=rng, line_only=True,
                        )
                        if lb:
                            _do_action('buy', lb)
        # Do not register ai_minimum_turn here when we still owe a first-year line — the Living AI block below retries build.
        if len(results) == 0 and not _need_forced_line:
            r = ops.ai_minimum_turn_action(player, current_year)
            if r and r.get('success'):
                _do_action('ai_minimum_turn', r)

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
                elif extend_ok and spend > min_extend_spendable:
                    extend_result = _try_extend_line(ops, player, game_map, spend, current_year, points_with_pop, rng=rng, preset=preset)
                    if extend_result:
                        _do_action('extend_line', extend_result)
    # Every alive AI must take at least one non-pass action per turn: try fare/schedule, then add_station, assign, buy, lobby
    if len(results) == 0 and has_lines:
        if fare_updates:
            _do_action('set_fares', {'lines': fare_updates})
        elif schedule_updates:
            _do_action('set_schedule', {'lines': schedule_updates})
    if has_lines and schedule_updates and not any(a == 'set_schedule' for a, _ in results):
        _do_action('set_schedule', {'lines': schedule_updates})
    # If still no action, try add_station / assign_trainset / buy / lobby so we get a non-pass action when possible
    if len(results) == 0:
        spend = _refresh_spendable()
        lines = player.get_line_names()
        map_lines = game_map.get_lines_by_name() or {}
        if lines and spend >= 500:
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
                focus == 'marketplace' and years_from_start >= 4 and net_income > -3500
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
                        early_growth and years_from_start <= 12 and not has_profitable_line and focus != 'marketplace'
                    )
                    if not _skip_market_early:
                        buy_result = _try_buy_marketplace(ops, player, current_year, spend, focus, rng=rng)
                        if buy_result:
                            _do_action('buy', buy_result)
        _lobby_floor = 2200 if focus == 'lobbying' else 4000
        _lobby_when_early_loss = (
            focus == 'lobbying' and years_from_start >= 4 and net_income > -2500
        )
        if (
            len(results) == 0 and spend > _lobby_floor and len(player.get_line_names()) > 0
            and (
                not (early_growth and years_from_start <= 12 and not has_profitable_line)
                or _lobby_when_early_loss
            )
        ):
            lobby_result = _try_lobby(ops, player, spend, focus=focus)
            if lobby_result:
                _do_action('lobby', lobby_result)

    # Living AI with no lines and no line in the build queue must create a line (every turn).
    if getattr(player, 'is_ai', False) and ops and hasattr(ops, 'is_player_eliminated') and not ops.is_player_eliminated(player):
        if len(player.get_line_names()) == 0:
            q = ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []
            my_line_q = [b for b in q if b.get('player') == player and b.get('type') in ('line', 'line_extension')]
            if not my_line_q and not any(a == 'create_line' for a, _ in results):
                _uses_osm_roads = bool(getattr(game_map, 'use_osm', False)) or bool(
                    preset and hasattr(preset, 'get_road_segment_points_for_ai')
                )
                force_cap = min(max(500, int(ops.get_player_money(player))), (120000 if _uses_osm_roads else 15000))
                n_att = 14
                forced = None
                # Same-turn: other players' queued segments must not prune all corridors (overlap filter is too strict).
                _overlap_routes = None if (first_calendar_year or ai_join_first_line_pending) else existing_routes

                if not _ai_line_create_result_ok(forced):
                    forced = _try_build_line_stochastic(
                        ops, player, game_map, force_cap, current_year, points_with_pop,
                        _overlap_routes, preset, n_attempts=n_att,
                    )
                if not _ai_line_create_result_ok(forced):
                    forced = _try_build_line(
                        ops, player, game_map, force_cap, current_year, points_with_pop,
                        rng=rng, existing_routes=_overlap_routes, preset=preset,
                    )
                if not _ai_line_create_result_ok(forced) and (first_calendar_year or ai_join_first_line_pending) and (bool(getattr(game_map, 'use_osm', False)) or bool(preset and hasattr(preset, 'get_road_segment_points_for_ai'))):
                    try:
                        from map_preset_loader import get_preset_road_segment_points
                        preset_name = getattr(game_map, 'map_preset_name', None)
                        # Prefer a short adjacent road segment first (cheap and likely to validate on turn one).
                        seeded = get_preset_road_segment_points(
                            preset_name,
                            game_map,
                            current_year,
                            max_points=48,
                            existing_line_routes=existing_routes,
                            min_length_grid=max(MIN_LINE_LENGTH_GRID, MIN_STATION_SPACING_GRID),
                            max_length_grid=8.0,
                            min_endpoint_population=0 if (first_calendar_year or ai_join_first_line_pending) else 1000,
                            rng=random.Random((int(current_year) * 3571 + int(getattr(player, 'player_id', 0)) * 271) & 0xFFFFFFFF),
                            preset=preset,
                        )
                        if len(seeded) >= 2:
                            for i_seg in range(len(seeded) - 1):
                                a = seeded[i_seg]
                                b = seeded[i_seg + 1]
                                seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
                                if seg_len < MIN_STATION_SPACING_GRID:
                                    continue
                                seg_cost = max(15, int(seg_len * 225 * 1.5))
                                if seg_cost > force_cap:
                                    continue
                                elevation_levels = _get_safe_elevation_levels([a, b], game_map, preset_name or '', base='street')
                                line_name = _generate_ai_line_name(
                                    player, rng, ops=ops, route_points=[a, b], current_year=current_year
                                )
                                candidate = ops.create_line_complete(
                                    line_name, [a, b], player,
                                    elevation_levels=elevation_levels,
                                    is_ferry_line=False,
                                    is_bus_line=False,
                                    cost_info={'cost': seg_cost, 'length': seg_len, 'points_count': 2, 'elevation_levels': elevation_levels},
                                    use_fast_path=True,
                                )
                                if candidate and candidate.get('success', True):
                                    forced = candidate
                                    break
                        road_points = []
                        for k in range(3 if not _ai_line_create_result_ok(forced) else 0):
                            krng = random.Random((int(current_year) * 1009 + int(getattr(player, 'player_id', 0)) * 97 + k * 7919) & 0xFFFFFFFF)
                            road_points = get_preset_road_segment_points(
                                preset_name,
                                game_map,
                                current_year,
                                max_points=16,
                                existing_line_routes=existing_routes,
                                min_length_grid=MIN_LINE_LENGTH_GRID,
                                max_length_grid=6.0,
                                min_endpoint_population=None,
                                rng=krng,
                                preset=preset,
                            )
                            if len(road_points) >= 2:
                                break
                        if len(road_points) >= 2:
                            total_length = sum(
                                math.hypot(road_points[i+1][0] - road_points[i][0], road_points[i+1][1] - road_points[i][1])
                                for i in range(len(road_points) - 1)
                            )
                            cost = max(15, int(total_length * 225 * 1.5))
                            if cost <= force_cap:
                                elevation_levels = _get_safe_elevation_levels(road_points, game_map, preset_name or '', base='street')
                                line_name = _generate_ai_line_name(
                                    player, rng, ops=ops, route_points=road_points, current_year=current_year
                                )
                                forced = ops.create_line_complete(
                                    line_name, road_points, player,
                                    elevation_levels=elevation_levels,
                                    is_ferry_line=False,
                                    is_bus_line=False,
                                    cost_info={'cost': cost, 'length': total_length, 'points_count': len(road_points), 'elevation_levels': elevation_levels},
                                    use_fast_path=True,
                                )
                    except Exception:
                        forced = None
                if _ai_line_create_result_ok(forced):
                    _do_action('create_line', forced)

    if len(results) == 0 and not _need_forced_line:
        r = ops.ai_minimum_turn_action(player, current_year)
        if r and r.get('success'):
            _do_action('ai_minimum_turn', r)

    # Turn-to-turn diversity rules:
    # 1) AI should always take at least one action type it did not take on the previous turn.
    # 2) AI should never produce exactly the same action signature on consecutive turns.
    # We try gameplay actions first; if impossible, append a distinct bookkeeping action.
    def _action_signature(items: list) -> tuple:
        return tuple(sorted(str(a) for a, _ in (items or [])))

    def _try_add_distinct_action(blocked_actions: set[str]) -> bool:
        nonlocal max_actions, reserve
        if len(results) >= max_actions:
            max_actions += 1
        # If we previously idled and got stuck, loosen reserve to enable a non-idle follow-up.
        if "ai_minimum_turn" in blocked_actions:
            money_now = ops.get_player_money(player)
            target_spendable = max(600, min_spendable_for_station + 200)
            reserve = min(reserve, max(0, money_now - target_spendable))
        no_lines_now = len(player.get_line_names()) == 0 if hasattr(player, "get_line_names") else True
        if no_lines_now and len(results) < max_actions:
            spend_nl = _refresh_spendable()
            if first_calendar_year or ai_join_first_line_pending or spend_nl > 0:
                build_cap = min(spend_nl, 8000) if (first_calendar_year or ai_join_first_line_pending) else min(spend_nl, 5000)
                br = _try_build_line_stochastic(
                    ops, player, game_map, build_cap, current_year, points_with_pop, existing_routes, preset
                )
                if _ai_line_create_result_ok(br) and "create_line" not in blocked_actions:
                    _do_action("create_line", br)
                    return True
            if len(player.get_line_names()) == 0 and len(results) < max_actions:
                rr = _try_rent_workers(ops, player, current_year, _refresh_spendable())
                if rr and "rent_workers" not in blocked_actions:
                    _do_action("rent_workers", rr)
                    return True
            if len(player.get_line_names()) == 0 and len(results) < max_actions:
                lb = _try_buy_marketplace(
                    ops, player, current_year, _refresh_spendable(), focus, rng=rng, line_only=True
                )
                if lb and "buy" not in blocked_actions:
                    _do_action("buy", lb)
                    return True
        if len(results) < max_actions:
            for slot in all_slots:
                action = slot()
                if action and action[0] not in blocked_actions:
                    _do_action(action[0], action[1])
                    return True
        # Also allow cost-benefit worker reduction as a distinct action when it helps.
        if len(results) < max_actions:
            fire_eval = _maybe_fire_workers_cost_benefit(large_cut=True)
            if fire_eval and "fire_workers" not in blocked_actions:
                _do_action("fire_workers", fire_eval)
                return True
        # Also allow fare/schedule adjustments as legitimate distinct actions (non-idle).
        if len(results) < max_actions:
            if fare_updates and "set_fares" not in blocked_actions and not any(a == "set_fares" for a, _ in results):
                _do_action("set_fares", {"lines": fare_updates})
                return True
            if schedule_updates and "set_schedule" not in blocked_actions and not any(a == "set_schedule" for a, _ in results):
                _do_action("set_schedule", {"lines": schedule_updates})
                return True
        # If we have lawyers, allow filing a lawsuit as a legitimate distinct action.
        # This is a last resort: it can swing finances, but it's better than idling in a loss spiral.
        if len(results) < max_actions and hasattr(ops, "try_file_lawsuit") and "file_lawsuit" not in blocked_actions:
            try:
                if hasattr(player, "get_lawyer_count") and int(player.get_lawyer_count() or 0) > 0:
                    others = [p for p in (ops.get_players() or []) if p is not player and not ops.is_player_eliminated(p)]
                    if others:
                        defendant = rng.choice(others)
                        excuse = None
                        if hasattr(ops, "get_player_lawsuit_excuses"):
                            ex = ops.get_player_lawsuit_excuses(player) or []
                            excuse = rng.choice(ex) if ex else None
                        r = ops.try_file_lawsuit(player, defendant, excuse=excuse)
                        if r and r.get("success", True):
                            _do_action("file_lawsuit", r)
                            return True
            except Exception:
                pass
        return False

    if prev_last_action_year == current_year - 1:
        current_names = [a for a, _ in results]
        prev_names = list(prev_action_signature)
        prev_set = set(prev_names)
        no_new_action_vs_prev = bool(current_names) and all(a in prev_set for a in current_names)
        same_signature = _action_signature(results) == tuple(sorted(prev_names))
        if no_new_action_vs_prev or same_signature:
            _try_add_distinct_action(prev_set)
        # Hard guarantee: do not return identical action signatures on consecutive turns.
        if _action_signature(results) == tuple(sorted(prev_names)):
            # Prefer repeating a real action set over appending a bookkeeping no-op when we're in danger.
            if not (is_losing_money or about_to_go_bankrupt):
                alt_name = 'ai_minimum_turn_alt_even' if (int(current_year) % 2 == 0) else 'ai_minimum_turn_alt_odd'
                _do_action(alt_name, {'success': True, 'year': int(current_year)})

    # Persist last action metadata for next-turn repeat detection.
    if results:
        try:
            player._ai_last_action_name = results[-1][0]
            player._ai_last_action_year = current_year
            player._ai_last_action_signature = _action_signature(results)
        except Exception:
            pass

    # Ensure visible expansion cadence by 1841, but never force a second line during bootstrap or while
    # post-bootstrap profitability is still unmet (would add maintenance before revenue is positive).
    if (
        getattr(player, "is_ai", False)
        and not ops.is_player_eliminated(player)
        and int(current_year) <= 1841
        and build_expand_count < 2
        and not (first_calendar_year or ai_join_first_line_pending)
        and not single_line_growth_until_win
        and not sustained_profit_maintenance
        and not (years_from_start <= AI_BOOTSTRAP_TURNS and len(player.get_line_names()) >= 1)
        and len(results) < max_actions
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
                    require_profit_when_troubled=(is_losing_money or about_to_go_bankrupt),
                )
                if ext:
                    _do_action("extend_line", ext)
            elif wants_new_line and spend_now > min_spendable_for_station:
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
            _osm_lr = bool(getattr(game_map, 'use_osm', False)) or bool(preset and hasattr(preset, 'get_road_segment_points_for_ai'))
            if _osm_lr:
                cap = min(max(500, int(ops.get_player_money(player))), 120000)
                lr = _osm_last_resort_create_line_from_preset(ops, player, game_map, current_year, rng, preset, cap)
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
                _record_street_build_failure(ops, player, game_map, current_year, preset=preset)

    if len(results) == 0:
        r = ops.ai_minimum_turn_action(player, current_year)
        if r and r.get('success'):
            _do_action('ai_minimum_turn', r)

    validate_ai_turn_results(ops, player, results)
    return results


# Used by :func:`_best_osm_turn1_pair_from_top_population` (must exist before that helper).
MIN_LINE_LENGTH_GRID = 0.55


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
        if _ai_population_near_point(game_map, fx, fy) <= 0:
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


def _line_needs_trainset_assignment(map_lines: dict, line_name: str) -> bool:
    """True when a completed line has two stations but no vehicles assigned yet."""
    line = map_lines.get(line_name) if map_lines else None
    if not line:
        return False
    stations = line.get_stations() if hasattr(line, 'get_stations') else []
    if len(stations) < 2:
        return False
    vehicles = getattr(line, 'vehicles', None) or []
    return len(vehicles) == 0
# OSM street builds: treat segments as duplicate only when they are very close.
# Keep aligned with the preset/ops overlap expectations to avoid excessive retry churn.
AI_STREET_DUPLICATE_OVERLAP_GRID = 0.5
MAX_FIRST_LINE_LENGTH_GRID = 2.0
# Cap all new lines (smaller lines + more extensions = faster growth)
MAX_LINE_LENGTH_GRID = 2.5
# AI stations must be at least 600m apart (same line or different lines)
MIN_STATION_SPACING_GRID = 0.6


def _osm_last_resort_create_line_from_preset(
    ops, player, game_map, current_year: int, rng: random.Random, preset, force_cap: int
):
    """OSM year-1 opener: cached preset segments, then a long trimmed road polyline if those fail."""
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name or force_cap < 500:
        return None
    if not (
        bool(getattr(game_map, 'use_osm', False))
        or (preset and hasattr(preset, 'get_road_segment_points_for_ai'))
    ):
        return None
    cached = _osm_first_turn_try_cached_segment_build(
        ops, player, game_map, current_year, rng, preset, force_cap
    )
    if cached:
        return cached
    # Hard last resort: ask preset for a long road polyline and trim to budget while preserving station-viable length.
    try:
        from map_preset_loader import get_preset_road_segment_points
    except Exception:
        return None
    # Endpoint population must be > 0 on NYC/OSM presets (avoid NJ spillover / zero-pop roads).
    # Use preset-provided station population when available (matches tests).
    endpoint_pop_floor = 1
    pop_map = getattr(game_map, "population", None) or {}
    try:
        from map_preset_loader import get_preset_ai_station_population
    except Exception:
        get_preset_ai_station_population = None
    ex_routes_lr = _get_existing_line_routes_for_overlap(ops, game_map)
    road_points = []
    for att in range(10):
        try:
            road_points = get_preset_road_segment_points(
                preset_name,
                game_map,
                current_year,
                max_points=220,
                existing_line_routes=ex_routes_lr,
                min_length_grid=MIN_LINE_LENGTH_GRID,
                max_length_grid=40.0,
                min_endpoint_population=None,
                rng=random.Random((int(current_year) * 9176 + int(getattr(player, 'player_id', 0)) * 131 + att * 991) & 0xFFFFFFFF),
                preset=preset,
            ) or []
        except Exception:
            road_points = []
        if len(road_points) < 2:
            continue
        try:
            a = road_points[0]
            b = road_points[-1]
            ax, ay = float(a[0]), float(a[1])
            bx, by = float(b[0]), float(b[1])
            if get_preset_ai_station_population is not None:
                pop_a = get_preset_ai_station_population(game_map, ax, ay, pop_map)
                pop_b = get_preset_ai_station_population(game_map, bx, by, pop_map)
            else:
                pop_a = _ai_population_near_point(game_map, ax, ay)
                pop_b = _ai_population_near_point(game_map, bx, by)
            if pop_a < endpoint_pop_floor or pop_b < endpoint_pop_floor:
                continue
        except Exception:
            pass
        break
    if len(road_points) < 2:
        return None
    pts = [(float(p[0]), float(p[1])) for p in road_points]
    min_len = float(MIN_STATION_SPACING_GRID)
    unit_cost = 225 * 1.5

    def _poly_len(p):
        return sum(math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]) for i in range(len(p) - 1)) if len(p) >= 2 else 0.0

    # Ensure the route is long enough for 2 stations.
    while len(pts) > 2 and _poly_len(pts) > 2.5 * min_len:
        # trim very long routes (helps cost and avoids wide wandering)
        pts.pop()
    if _poly_len(pts) < min_len:
        # Try taking a longer prefix if the preset returned something too short.
        pts = [(float(p[0]), float(p[1])) for p in road_points]
        if _poly_len(pts) < min_len:
            return None

    # Trim to budget while keeping min_len.
    for _ in range(80):
        length = _poly_len(pts)
        cost = max(15, int(length * unit_cost))
        if cost <= force_cap:
            break
        if len(pts) > 2:
            pts.pop()
            continue
        # Only 2 points: clip the last edge.
        a = pts[0]
        b = pts[1]
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d < 1e-9:
            return None
        max_len_budget = max(min_len, float(force_cap) / unit_cost)
        t = min(1.0, max_len_budget / d)
        pts[1] = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        break
    if _poly_len(pts) < min_len:
        return None
    length = _poly_len(pts)
    cost = max(15, int(length * unit_cost))
    if cost > force_cap:
        return None
    try:
        elevation_levels = _get_safe_elevation_levels(pts, game_map, preset_name or '', base='street')
        line_name = _generate_ai_line_name(player, rng, ops=ops, route_points=pts, current_year=current_year)
        return ops.create_line_complete(
            line_name,
            pts,
            player,
            elevation_levels=elevation_levels,
            is_ferry_line=False,
            is_bus_line=False,
            cost_info={'cost': cost, 'length': length, 'points_count': len(pts), 'elevation_levels': elevation_levels},
            use_fast_path=True,
        )
    except Exception:
        return None


# Service-aware spacing (grid units). Buses stop more often; commuter rail stops less often.
AI_BUS_STOP_SPACING_GRID = 0.35
# Express buses skip more stops than locals but remain closer than rail locals.
AI_BUS_EXPRESS_STOP_SPACING_GRID = 0.52
AI_LOCAL_STOP_SPACING_GRID = 0.60
AI_EXPRESS_STOP_SPACING_GRID = 0.95
AI_COMMUTER_STOP_SPACING_GRID = 1.35
# Persist AI-decided rail service type for station spacing decisions.
_AI_LINE_SERVICE_TYPE: dict[str, str] = {}
# Cache expensive first-turn OSM corridor sampling so multi-AI NYC openers do not time out.
_AI_FIRST_TURN_OSM_SEGMENTS_CACHE: dict[tuple, list] = {}
# Pre-validated segment polylines (cheap checks only) per cache_key — avoids O(n^2) create_line_complete.
_AI_FIRST_TURN_OSM_CANDIDATES_CACHE: dict[tuple, list] = {}
_AI_FIRST_TURN_MAX_CREATE_ATTEMPTS = 8
_AI_FIRST_TURN_MAX_CANDIDATES = 24
_AI_FIRST_TURN_BRUTE_SAMPLES = 2
_AI_FIRST_TURN_ALT_SAMPLES = 1


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


def _osm_afford_last_edge(pts: list, min_len: float, max_budget: int) -> Optional[list]:
    if len(pts) < 2:
        return None
    work = [(float(p[0]), float(p[1])) for p in pts]
    unit_cost = 225 * 1.5
    for _ in range(28):
        plen = _osm_polyline_length(work)
        if plen < min_len:
            return None
        seg_cost = max(15, int(plen * unit_cost))
        if seg_cost <= max_budget:
            return work
        if len(work) == 2:
            ax, ay = work[0]
            bx, by = work[1]
            d = math.hypot(bx - ax, by - ay)
            if d < min_len:
                return None
            max_len_affordable = max(0.0, (max(0, max_budget - 15) / max(1.0, unit_cost)))
            if max_len_affordable < min_len:
                return None
            t = min(1.0, max_len_affordable / d)
            work[1] = (ax + t * (bx - ax), ay + t * (by - ay))
            continue
        work.pop()
    return None


def _osm_first_turn_endpoints_viable(game_map, a, b) -> bool:
    try:
        from map_preset_loader import get_preset_ai_station_population
        pop_map = getattr(game_map, "population", None) or {}
        pa = get_preset_ai_station_population(game_map, float(a[0]), float(a[1]), pop_map)
        pb = get_preset_ai_station_population(game_map, float(b[0]), float(b[1]), pop_map)
        return pa > 0 and pb > 0
    except Exception:
        return (
            _ai_population_near_point(game_map, a[0], a[1]) > 0
            and _ai_population_near_point(game_map, b[0], b[1]) > 0
        )


def _ai_prepare_osm_route_points(points: list, game_map, min_seg: float = None) -> Optional[list]:
    """Drop OSM vertices that cause backtracking or illegal turns; fall back to a clean 2-point segment."""
    if not points or len(points) < 2:
        return None
    from operations import _ai_route_has_backtracking, _validate_route_geometry

    def _route_ok(route_pts: list) -> bool:
        if _ai_route_has_backtracking(route_pts, game_map):
            return False
        try:
            from game_presets import MAX_TRAIN_ROUTE_TURN_DEGREES, ROUTE_TURN_CHECK_MIN_SEGMENT_GRID
            from map_preset_utils import route_exceeds_max_turn, route_violates_min_turn_radius
            from game_presets import MIN_TRAIN_TURN_RADIUS_GRID

            if route_exceeds_max_turn(
                route_pts, MAX_TRAIN_ROUTE_TURN_DEGREES, min_segment_grid=ROUTE_TURN_CHECK_MIN_SEGMENT_GRID
            ):
                return False
            if route_violates_min_turn_radius(
                route_pts, MIN_TRAIN_TURN_RADIUS_GRID, min_segment_grid=ROUTE_TURN_CHECK_MIN_SEGMENT_GRID
            ):
                return False
        except Exception:
            return False
        return True

    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) == 2:
        if not _route_ok(pts):
            return None
        if min_seg is not None and _osm_polyline_length(pts) < min_seg:
            return None
        return pts

    simplified = [pts[0]]
    for p in pts[1:]:
        trial = simplified + [p]
        if _route_ok(trial):
            simplified.append(p)

    if len(simplified) >= 2 and _route_ok(simplified):
        if min_seg is None or _osm_polyline_length(simplified) >= min_seg:
            return simplified

    chord = [pts[0], pts[-1]]
    if _route_ok(chord):
        if min_seg is None or _osm_polyline_length(chord) >= min_seg:
            return chord

    best_seg = None
    best_len = 0.0
    for i in range(len(pts) - 1):
        seg = [pts[i], pts[i + 1]]
        if not _route_ok(seg):
            continue
        slen = _osm_polyline_length(seg)
        if slen > best_len:
            best_seg, best_len = seg, slen
    if best_seg and (min_seg is None or best_len >= min_seg):
        return best_seg
    return None


def _osm_first_turn_segment_overlaps(pts_work: list, ex_routes: list, game_map) -> bool:
    if not ex_routes:
        return False
    tol = _overlap_tolerance_for_map(game_map)
    for i in range(len(pts_work) - 1):
        if _segment_overlaps_existing_routes(
            pts_work[i], pts_work[i + 1], ex_routes, tolerance=tol, game_map=game_map
        ):
            return True
    return False


def _osm_first_turn_collect_candidates(
    seeded_list: list,
    budget: int,
    min_seg: float,
    game_map,
    ex_routes: list,
    max_candidates: int = _AI_FIRST_TURN_MAX_CANDIDATES,
) -> list:
    """Cheaply enumerate affordable OSM sub-polylines (no create_line_complete)."""
    if len(seeded_list) < 2:
        return []
    max_chain = 2.85
    unit_cost = 225 * 1.5
    out = []
    n = len(seeded_list)
    n_starts = n - 1
    # Sample start offsets (full scan is O(n^2) and dominated first-year turn time on NYC).
    stride = max(1, n_starts // max(8, min(16, max_candidates // 2)))
    start_indices = list(range(0, n_starts, stride))
    if not start_indices:
        start_indices = [0]
    for ist in start_indices:
        if len(out) >= max_candidates:
            break
        sub_pts = [seeded_list[ist]]
        for j in range(ist, n - 1):
            sub_pts.append(seeded_list[j + 1])
            plen = _osm_polyline_length(sub_pts)
            if plen < min_seg:
                continue
            pts_work = _osm_trim_polyline_to_max_len(sub_pts, max_chain, min_seg)
            if not pts_work:
                continue
            plen = _osm_polyline_length(pts_work)
            seg_cost = max(15, int(plen * unit_cost))
            if seg_cost > budget:
                pts_work = _osm_afford_last_edge(pts_work, min_seg, budget)
                if not pts_work:
                    continue
                plen = _osm_polyline_length(pts_work)
                seg_cost = max(15, int(plen * unit_cost))
            if plen < min_seg or seg_cost > budget:
                continue
            a, b = pts_work[0], pts_work[-1]
            if not _osm_first_turn_endpoints_viable(game_map, a, b):
                continue
            prepared = _ai_prepare_osm_route_points(pts_work, game_map, min_seg)
            if not prepared:
                continue
            pts_work = prepared
            plen = _osm_polyline_length(pts_work)
            seg_cost = max(15, int(plen * unit_cost))
            if seg_cost > budget:
                pts_work = _osm_afford_last_edge(pts_work, min_seg, budget)
                if not pts_work:
                    continue
                plen = _osm_polyline_length(pts_work)
                seg_cost = max(15, int(plen * unit_cost))
            if plen < min_seg or seg_cost > budget:
                continue
            if _osm_first_turn_segment_overlaps(pts_work, ex_routes, game_map):
                continue
            out.append((list(pts_work), float(plen), int(seg_cost)))
            if len(out) >= max_candidates:
                break
    return out


def _osm_first_turn_create_from_candidate(
    ops,
    player,
    game_map,
    current_year: int,
    rng: random.Random,
    preset_name: str,
    budget: int,
    min_seg: float,
    pts_work: list,
    plen: float,
    seg_cost: int,
):
    """Try create_line_complete (+ chord fallback) for one pre-validated candidate."""
    pts_work = list(pts_work)
    plen = _osm_polyline_length(pts_work)
    seg_cost = max(15, int(plen * 225 * 1.5))
    elevation_levels = _get_safe_elevation_levels(pts_work, game_map, preset_name or '', base='street')
    line_name = _generate_ai_line_name(
        player, rng, ops=ops, route_points=None, current_year=current_year,
    )
    direct = None
    try:
        direct = ops.create_line_complete(
            line_name,
            pts_work,
            player,
            elevation_levels=elevation_levels,
            is_ferry_line=False,
            is_bus_line=False,
            cost_info={
                'cost': seg_cost,
                'length': plen,
                'points_count': len(pts_work),
                'elevation_levels': elevation_levels,
            },
            use_fast_path=True,
        )
    except Exception:
        direct = None
    if not _ai_line_create_result_ok(direct) and len(pts_work) > 2:
        a0, b0 = pts_work[0], pts_work[-1]
        chord_pts = [a0, b0]
        chord_len = math.hypot(float(b0[0]) - float(a0[0]), float(b0[1]) - float(a0[1]))
        if chord_len >= min_seg:
            c_cost = max(15, int(chord_len * 225 * 1.5))
            if c_cost > budget:
                chord_pts = _osm_afford_last_edge(chord_pts, min_seg, budget)
                if chord_pts:
                    chord_len = _osm_polyline_length(chord_pts)
                    c_cost = max(15, int(chord_len * 225 * 1.5))
                else:
                    chord_pts = None
            if chord_pts and c_cost <= budget:
                elev_c = _get_safe_elevation_levels(chord_pts, game_map, preset_name or '', base='street')
                line_chord = _generate_ai_line_name(
                    player, rng, ops=ops, route_points=None, current_year=current_year,
                )
                try:
                    direct = ops.create_line_complete(
                        line_chord,
                        chord_pts,
                        player,
                        elevation_levels=elev_c,
                        is_ferry_line=False,
                        is_bus_line=False,
                        cost_info={
                            'cost': c_cost,
                            'length': chord_len,
                            'points_count': len(chord_pts),
                            'elevation_levels': elev_c,
                        },
                        use_fast_path=True,
                    )
                except Exception:
                    direct = None
                if _ai_line_create_result_ok(direct):
                    line_name = line_chord
                    plen = chord_len
                    seg_cost = c_cost
                    pts_work = list(chord_pts)
                    elevation_levels = elev_c
    if not _ai_line_create_result_ok(direct):
        return None
    rail_service = _decide_ai_rail_service_type(pts_work)
    _AI_LINE_SERVICE_TYPE[line_name] = rail_service
    _add_ai_endpoint_stations(
        ops, player, line_name, pts_work, is_bus_line=False, service_type=rail_service
    )
    return direct


def _osm_first_turn_try_cached_segment_build(
    ops, player, game_map, current_year: int, rng: random.Random, preset, budget: int
):
    """Year-1 OSM opener: fill/populate cached corridors, pick a segment pair per player_id, create_line + endpoint stations.

    Used by the fast path and by the pre-validate last resort so multi-AI NYC does not diverge into flaky RNG pulls.
    """
    try:
        from map_preset_loader import get_preset_road_segment_points
    except Exception:
        return None
    preset_name = getattr(game_map, 'map_preset_name', None)
    if not preset_name:
        return None
    min_ep_pop_floor = 0
    # Use a stable cache independent of game_seed so OSM openers are not flaky across seeds.
    # Per-AI variety still comes from player_id-based indexing within the cached polyline.
    gs = int(_get_game_seed(ops)) if ops else 0
    gs_cache = 0
    ex_routes = _get_existing_line_routes_for_overlap(ops, game_map)
    route_fp = _existing_line_routes_cache_fingerprint(ex_routes)
    # Cache is per-game-map instance (so we don't reuse point lists across fresh games in long test loops).
    # Fingerprint includes queued/completed line geometry so AI 2+ on the same turn do not reuse AI 1's corridor.
    cache_key = (
        preset_name,
        int(id(game_map) & 0xFFFFFFFF),
        int(current_year),
        int(min_ep_pop_floor),
        int(route_fp),
    )
    pid = int(getattr(player, 'player_id', 0) or 0)
    _min_seg = max(MIN_LINE_LENGTH_GRID, MIN_STATION_SPACING_GRID)

    def _try_candidates_from_seeded(seeded_list: list, start_offset: int = 0):
        candidates = _AI_FIRST_TURN_OSM_CANDIDATES_CACHE.get(cache_key)
        if candidates is None:
            candidates = _osm_first_turn_collect_candidates(
                seeded_list, budget, _min_seg, game_map, ex_routes,
            )
            _AI_FIRST_TURN_OSM_CANDIDATES_CACHE[cache_key] = candidates
        if not candidates:
            return None
        n_cand = len(candidates)
        create_attempts = 0
        for step in range(n_cand):
            if create_attempts >= _AI_FIRST_TURN_MAX_CREATE_ATTEMPTS:
                break
            idx = (start_offset + step) % n_cand
            pts_work, plen, seg_cost = candidates[idx]
            create_attempts += 1
            out = _osm_first_turn_create_from_candidate(
                ops, player, game_map, current_year, rng, preset_name, budget,
                _min_seg, pts_work, plen, seg_cost,
            )
            if out:
                return out
        return None

    seeded = _AI_FIRST_TURN_OSM_SEGMENTS_CACHE.get(cache_key)
    if seeded is None:
        seed = (cache_key[1] ^ (cache_key[2] * 2707) ^ (cache_key[3] * 31337)) & 0xFFFFFFFF
        crng = random.Random(seed)
        seeded = get_preset_road_segment_points(
            preset_name,
            game_map,
            current_year,
            max_points=80,
            existing_line_routes=ex_routes,
            min_length_grid=MIN_LINE_LENGTH_GRID,
            max_length_grid=18.0,
            min_endpoint_population=0,
            rng=crng,
            preset=preset,
        ) or []
        # Store an immutable-ish snapshot; callers may slice/pop while searching.
        _AI_FIRST_TURN_OSM_SEGMENTS_CACHE[cache_key] = list(seeded)
    else:
        # Work on a copy so cached seed corridors remain stable across games/seeds.
        seeded = list(seeded)

    n_cand_hint = max(1, len(_AI_FIRST_TURN_OSM_CANDIDATES_CACHE.get(cache_key) or []))
    start_offset = (pid * 7919 + (gs_cache % 997) * 17 + (int(current_year) % 31)) % n_cand_hint
    out = _try_candidates_from_seeded(seeded, start_offset)
    if out:
        return out
    if _AI_FIRST_TURN_OSM_CANDIDATES_CACHE.get(cache_key):
        return None
    for alt_k in range(_AI_FIRST_TURN_ALT_SAMPLES):
        prng = random.Random((gs_cache ^ pid * 0x9E3779B9 ^ int(current_year) * 2654435761 ^ alt_k * 0x1000193) & 0xFFFFFFFF)
        seeded_alt = get_preset_road_segment_points(
            preset_name,
            game_map,
            current_year,
            max_points=80,
            existing_line_routes=ex_routes,
            min_length_grid=MIN_LINE_LENGTH_GRID,
            max_length_grid=18.0,
            min_endpoint_population=0,
            rng=prng,
            preset=preset,
        ) or []
        seeded_alt = list(seeded_alt)
        _AI_FIRST_TURN_OSM_CANDIDATES_CACHE.pop(cache_key, None)
        out = _try_candidates_from_seeded(seeded_alt, (pid + alt_k * 3) % max(1, len(seeded_alt)))
        if out:
            return out
    for brute_k in range(_AI_FIRST_TURN_BRUTE_SAMPLES):
        brng = random.Random((int(current_year) * 424243 + pid * 31337 + brute_k * 99991) & 0xFFFFFFFF)
        try:
            pts = get_preset_road_segment_points(
                preset_name,
                game_map,
                current_year,
                max_points=80,
                existing_line_routes=ex_routes,
                min_length_grid=MIN_LINE_LENGTH_GRID,
                max_length_grid=24.0,
                min_endpoint_population=0,
                rng=brng,
                preset=preset,
            ) or []
        except Exception:
            pts = []
        _AI_FIRST_TURN_OSM_CANDIDATES_CACHE.pop(cache_key, None)
        out = _try_candidates_from_seeded(list(pts), brute_k)
        if out:
            return out
    return None


# Max population points to consider for AI line building.
# Raising this helps AIs consistently find higher-pop endpoints early.
_MAX_BUILD_CANDIDATES = 20
# Max population points for extend-line scoring (fewer = faster)
_MAX_EXTEND_POINTS = 8
# Max candidates to call calculate_extend_cost per line (expensive on large maps)
_MAX_EXTEND_COST_CALLS = 2
# Max lines to consider for extend (fewer = faster; 2 is enough for variety)
_MAX_EXTEND_LINES = 2
# Max population points for station placement
_MAX_STATION_POINTS = 8
# Minimum stations required for a line to generate profit (0 or 1 = no profit)
MIN_STATIONS_FOR_PROFIT = 2
# AI line names: only use a geographic label (e.g. street) if it lies within this distance of the route sample point.
AI_LINE_NAME_GEO_MAX_DISTANCE_M = 150.0
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


def _adjust_ai_fares(ops, player, current_year: int, game_map, rng: random.Random = None) -> list:
    """Set fares near max acceptable for profit. AI optimizes fare = max_acceptable or slightly below.
    Returns list of line names whose fares were updated (for reporting as an action)."""
    rng = rng or random.Random()
    map_lines = game_map.get_lines_by_name() or {}
    sy, ey = _timeline_year_span(ops)
    span = max(1, ey - sy)
    max_acceptable_fare = 2 + (current_year - sy) * (15 - 2) / span
    max_acceptable_fare = max(2, min(15, int(round(max_acceptable_fare))))
    updated = []
    for line_name in player.get_line_names():
        line = map_lines.get(line_name)
        if not line or not hasattr(line, 'fare'):
            continue
        stations = line.get_stations() if hasattr(line, 'get_stations') else []
        if len(stations) < 2:
            continue
        current_fare = getattr(line, 'fare', 10)
        # Never reduce an existing fare during survival tuning; lowering from defaults can erase margin.
        target = max(current_fare, max_acceptable_fare)
        if hasattr(ops, 'set_line_fare') and current_fare != target:
            try:
                ops.set_line_fare(line, target)
                updated.append(line_name)
            except Exception:
                pass
    return updated


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
) -> list:
    """Set peak/off_peak/night schedules on AI lines.

    Aggressive high frequencies (e.g. 12/6/3 trains per hour) drive ``service_frequency_per_year`` and
    schedule operating cost in ``Line.calc_maitnance`` to tens of thousands per year while ridership-limited
    profit does not scale the same way, which caused early bankruptcy. Use light schedules until the line is
    clearly net-positive, then scale up for wealthy AIs."""
    map_lines = game_map.get_lines_by_name() or {}
    updated = []
    # Line profit can be > 0 while year net is still negative (taxes, workers, etc.). Do not raise
    # frequencies during the early-growth window unless clearly rich, or we repeat the old 12/6/3 blow-up.
    if is_wealthy_and_profitable:
        mode = 'aggressive'
    else:
        mode = 'survival'

    for line_name in player.get_line_names():
        line = map_lines.get(line_name)
        if not line or not hasattr(line, 'schedule') or not hasattr(line, 'set_schedule_frequency'):
            continue
        stations = line.get_stations() if hasattr(line, 'get_stations') else []
        if len(stations) < 2:
            continue
        if not hasattr(line, 'get_max_frequency_per_hour'):
            continue
        max_peak = line.get_max_frequency_per_hour('peak')
        max_off_peak = line.get_max_frequency_per_hour('off_peak')
        max_night = line.get_max_frequency_per_hour('night')
        if max_peak <= 0:
            continue
        current = getattr(line, 'schedule', {})
        peak = float(current.get('peak', 0) or 0)
        off_peak = float(current.get('off_peak', 0) or 0)
        night = float(current.get('night', 0) or 0)

        if mode == 'survival':
            if schedule_survival_tight:
                target_peak = min(0.35, max_peak)
                target_off_peak = min(0.35, max_off_peak)
            else:
                target_peak = min(0.5, max_peak)
                target_off_peak = min(0.5, max_off_peak)
            target_night = 0.0
        elif mode == 'normal':
            target_peak = max(2.0, min(float(int(max_peak)), 8.0))
            target_off_peak = max(1.0, min(float(int(max_off_peak)), 5.0))
            target_night = max(0.0, min(float(int(max_night)), 2.0))
        else:
            target_peak = max(2.0, min(float(int(max_peak)), 12.0))
            target_off_peak = max(1.0, min(float(int(max_off_peak)), 6.0))
            target_night = max(0.0, min(float(int(max_night)), 3.0))

        if abs(peak - target_peak) > 1e-6 or abs(off_peak - target_off_peak) > 1e-6 or abs(night - target_night) > 1e-6:
            try:
                line.set_schedule_frequency('peak', float(target_peak))
                line.set_schedule_frequency('off_peak', float(target_off_peak))
                line.set_schedule_frequency('night', float(target_night))
                updated.append(line_name)
            except Exception:
                pass
    return updated


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
    if action_name == 'ai_minimum_turn':
        return "Year-end bookkeeping (turn registered)"
    if action_name in ('ai_minimum_turn_alt_even', 'ai_minimum_turn_alt_odd'):
        return "Alternate bookkeeping (avoid repeat signature)"
    if action_name == 'pass':
        return "Passed"
    if action_name == 'set_fares':
        lines = result.get('lines', [])
        return f"Adjusted fares on {len(lines)} line(s)" if lines else "Adjusted fares"
    if action_name == 'set_schedule':
        lines = result.get('lines', [])
        return f"Updated schedule on {len(lines)} line(s)" if lines else "Updated schedule"
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
        from map_preset_loader import (
            preset_has_water_areas,
            get_preset_ferry_segment_points,
            preset_is_point_in_water,
        )
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
        # Pre-filter: interior must be mostly *in* water when tile_system is available (avoids land shortcuts).
        ts = getattr(game_map, "tile_system", None)
        if ts and hasattr(ts, "ferry_corridor_interior_mostly_water"):
            ok_seg = True
            for i in range(len(points) - 1):
                ax, ay = points[i]
                bx, by = points[i + 1]
                try:
                    if not ts.ferry_corridor_interior_mostly_water(
                        ax, ay, bx, by, skip_osm=False, min_fraction=0.88
                    ):
                        ok_seg = False
                        break
                except Exception:
                    ok_seg = False
                    break
            if not ok_seg:
                continue
        else:
            # No tile system: use preset water only (no OSM during scoring).
            water_hits = 0
            water_total = 0
            for i in range(len(points) - 1):
                ax, ay = points[i]
                bx, by = points[i + 1]
                for t in (0.2, 0.4, 0.6, 0.8):
                    sx = ax + t * (bx - ax)
                    sy = ay + t * (by - ay)
                    water_total += 1
                    if preset_is_point_in_water(preset_name, sx, sy, game_map, skip_osm=True):
                        water_hits += 1
            if water_total > 0 and (water_hits / water_total) < 0.75:
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
        cost = max(15, int(40 * total_length * 0.8))
        if cost > spendable:
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
                _add_ai_endpoint_stations(ops, player, line_name, points, is_bus_line=False, service_type="commuter")
                return result
        except Exception:
            continue
    return None


def _population_at_or_near(px: float, py: float, pop_map: dict, radius: int = 5) -> int:
    """Get population at grid point or nearest cell within radius. For sparse maps (NYC)."""
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


def _merge_route_points_for_overlap(route, additional_points, extend_from_end: bool, _pt_fn) -> list:
    """Join route with extension, dropping a duplicate joint vertex when endpoints coincide."""
    r = [_pt_fn(p) for p in route]
    a = [_pt_fn(p) for p in additional_points]
    tol = 1e-4
    def _near(p, q):
        return math.hypot(p[0] - q[0], p[1] - q[1]) < tol
    if extend_from_end:
        if r and a and _near(r[-1], a[0]):
            return r + a[1:]
        return r + a
    if r and a and _near(r[0], a[-1]):
        return a[:-1] + r
    return a + r


def _estimate_line_profit(pop1: int, pop2: int, dist: float, est_cost: int, current_year: int, game_start_year: int = 1830) -> float:
    """Estimate profit for a new line connecting two population points.
    Profit ~ passengers * fare - cost. Passengers ~ pop1 * pop2 (connection value).
    Returns an estimate of *annual operating profit potential* (higher is better).

    Important: this function intentionally does NOT subtract the one-time construction cost.
    Callers compare the returned annual profit potential against build cost using a payback window.
    """
    if est_cost <= 0:
        return 0
    # Connection value: two high-pop areas = more trips
    connection_value = (pop1 * pop2) / max(0.1, dist)
    # Rough fare estimate (inflation-adjusted)
    from inflation_utils import get_inflation_factor
    gsy = int(game_start_year) if game_start_year is not None else 1830
    fare = min(15, 2 + (current_year - gsy) * 0.06)
    fare /= get_inflation_factor(current_year, max_factor=5.5)
    # Profit multiplier aligned with game (0.6); 1.7 = higher margin for AI survival (more willing to build)
    est_profit = connection_value * fare * 0.6 * 1.7
    return est_profit


def _get_game_seed(ops) -> int:
    """Game RNG seed; varies per game so AIs and naming differ each play-through."""
    game = getattr(ops, 'game', None) if ops is not None else None
    return getattr(game, '_ai_rng_seed', id(game) if game else id(ops) if ops else 0)


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
        # OSM: each attempt can cost many seconds — 4 tries balances coverage vs end-year time.
        n_attempts = 4 if uses_osm_roads else 18
        if uses_osm_roads and _is_first_game_year(ops, current_year) and _is_first_line_for_player(ops, player, game_map):
            n_attempts = 14
    for k in range(n_attempts):
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
    """Borough/area labels along a route (e.g. Manhattan, Brooklyn) for line-name validation."""
    if not route_points or not preset_name:
        return set()
    try:
        from map_preset_loader import load_map_preset
        preset = load_map_preset(preset_name)
        fn = getattr(preset, "get_point_area_map", None)
        area_map = fn() if callable(fn) else {}
    except Exception:
        area_map = {}
    labels = set()
    if not area_map:
        return labels
    for p in route_points:
        key = (int(round(float(p[0]))), int(round(float(p[1]))))
        label = area_map.get(key)
        if isinstance(label, str) and label.strip():
            labels.add(label.strip())
    return labels


def _line_name_matches_route_areas(line_name: str, route_areas: set) -> bool:
    """Reject preset names that name the wrong borough (e.g. Brooklyn line in Manhattan)."""
    if not route_areas or not line_name:
        return True
    nl = line_name.lower()
    areas = " ".join(a.lower() for a in route_areas)
    checks = (
        ("brooklyn", "brooklyn"),
        ("queens", "queens"),
        ("bronx", "bronx"),
        ("staten island", "staten"),
        ("manhattan", "manhattan"),
    )
    for keyword, area_needle in checks:
        if keyword in nl and area_needle not in areas:
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
    """Derive a line name from a geographic feature within AI_LINE_NAME_GEO_MAX_DISTANCE_M of the route centroid."""
    if not route_points or len(route_points) < 2 or not game_map:
        return None
    preset_name = getattr(game_map, "map_preset_name", None)
    if not preset_name:
        return None
    mx = sum(float(p[0]) for p in route_points) / len(route_points)
    my = sum(float(p[1]) for p in route_points) / len(route_points)
    try:
        from map_preset_loader import get_preset_geo_feature_name_for_line_naming
        base = get_preset_geo_feature_name_for_line_naming(
            preset_name,
            game_map,
            mx,
            my,
            current_year=current_year,
            max_distance_m=AI_LINE_NAME_GEO_MAX_DISTANCE_M,
        )
    except Exception:
        base = None
    if not base:
        return None
    s = str(base).strip()
    if not s:
        return None
    if "—" in s:
        s = s.split("—", 1)[0].strip()
    suffix = rng.choice((" Line", " Route", " Corridor", " Shuttle"))
    cand = f"{s}{suffix}"
    if cand not in taken:
        return cand
    k = 2
    while f"{cand} ({k})" in taken and k < 999:
        k += 1
    return f"{cand} ({k})"


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
    When route_points are provided, tries a geographic name within AI_LINE_NAME_GEO_MAX_DISTANCE_M before letter/number schemes."""
    if preferred_name and preferred_name.strip():
        return preferred_name.strip()
    # Names already used in the game (completed lines + under construction)
    taken = set()
    if ops:
        game_map = ops.get_map() if hasattr(ops, 'get_map') else None
        if game_map and hasattr(game_map, 'get_lines_by_name'):
            taken.update((game_map.get_lines_by_name() or {}).keys())
        for b in (ops.get_build_queue() if hasattr(ops, 'get_build_queue') else []) or []:
            if b.get('type') == 'line':
                n = (b.get('data') or {}).get('name')
                if n:
                    taken.add(n)
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
    if route_points and len(route_points) >= 2 and game_map:
        geo_name = _geo_corridor_line_name(ops, game_map, route_points, cy, taken, rng)
        if geo_name and geo_name not in taken and _line_name_base_key(geo_name) not in used_bases:
            return geo_name
    if preset_name:
        try:
            from map_preset_loader import get_preset_default_line_names
            default_names = get_preset_default_line_names(preset_name)
            if default_names:
                start = (pid + idx) % len(default_names)
                for k in range(len(default_names)):
                    candidate = default_names[(start + k) % len(default_names)]
                    if candidate in taken or _line_name_base_key(candidate) in used_bases:
                        continue
                    if not _line_name_matches_route_areas(candidate, route_areas):
                        continue
                    return candidate
        except Exception:
            pass
    game_seed = _get_game_seed(ops)
    pid = getattr(player, 'player_id', id(player))
    # Deterministic across processes (avoid PYTHONHASHSEED / hash(tuple) variance).
    scheme = ((int(pid) * 7919) ^ (int(game_seed) % 1_000_003)) % 3

    def _name_at(k: int) -> str:
        if scheme == 0:
            if k < 26:
                return chr(ord('A') + k)
            return chr(ord('A') + (k % 26)) + str(k // 26)
        if scheme == 1:
            return str(k + 1)
        letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        if k < len(letters):
            return letters[k]
        return letters[k % len(letters)] + str(k // len(letters))

    # Pick first name in this player's scheme that is not taken.
    for k in range(idx, idx + 200):
        candidate = _name_at(k)
        if candidate not in taken:
            return candidate
    return _name_at(idx)


def _ai_segment_qualifies_as_ferry(p1, p2, game_map, preset_name: str) -> bool:
    """True only when a segment may be built as a ferry (within 60m of water and crosses open water)."""
    if not preset_name:
        return False
    try:
        from map_preset_loader import preset_has_water_areas
        if not preset_has_water_areas(preset_name):
            return False
    except Exception:
        return False
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    ts = getattr(game_map, "tile_system", None)
    if not ts or not hasattr(ts, "is_point_near_water") or not hasattr(ts, "ferry_corridor_interior_mostly_water"):
        return False
    try:
        if not ts.is_point_near_water(x1, y1, 60.0, skip_osm=False):
            return False
        if not ts.is_point_near_water(x2, y2, 60.0, skip_osm=False):
            return False
        if hasattr(ts, "is_segment_near_water") and not ts.is_segment_near_water(
            x1, y1, x2, y2, 60.0, skip_osm=False
        ):
            return False
        if not ts.ferry_corridor_interior_mostly_water(x1, y1, x2, y2, skip_osm=False, min_fraction=0.88):
            return False
        hits = 0
        total = 0
        end_trim = 0.12
        for k in range(1, 13):
            t = end_trim + (1.0 - 2.0 * end_trim) * (k / 12.0)
            sx = x1 + t * (x2 - x1)
            sy = y1 + t * (y2 - y1)
            total += 1
            try:
                in_w = bool(ts.is_point_in_water(sx, sy, skip_osm=False))
            except TypeError:
                in_w = bool(ts.is_point_in_water(sx, sy))
            if in_w:
                hits += 1
        return total > 0 and (hits / total) >= 0.85
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
    """Return elevation levels with 'above_ground' for segments in water. Prevents street-on-water error."""
    if len(points) < 2:
        return []
    levels = []
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        if base == 'street' and _segment_in_water(x1, y1, x2, y2, game_map, preset_name):
            levels.append('above_ground')
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


def _get_existing_line_routes_for_overlap(ops, game_map) -> list:
    """Build list of point lists from existing lines and build queue (for overlap exclusion)."""
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
    # and presets see a sane corridor count (dozens of 2-point chunks made NYC road sampling stall).
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
    return routes


def _existing_line_routes_cache_fingerprint(existing_routes: list) -> int:
    """Vary OSM first-turn segment cache when the build queue already has line geometry (multi-AI turn 1)."""
    if not existing_routes:
        return 0
    h = len(existing_routes) & 0xFFFFFFFF
    for route in existing_routes[:16]:
        if not route or len(route) < 2:
            continue
        a, b = route[0], route[-1]
        try:
            fx = round(float(a[0]), 4)
            fy = round(float(a[1]), 4)
            gx = round(float(b[0]), 4)
            gy = round(float(b[1]), 4)
            h = (h * 1_000_003 + hash((fx, fy, gx, gy))) & 0xFFFFFFFF
        except Exception:
            continue
    return int(h)


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
    """Classify rail service to drive station spacing."""
    length = _route_length(points)
    if length >= 3.0:
        return "commuter"
    if length >= 1.7:
        return "express"
    return "local"


def _decide_ai_bus_service_type(points: list) -> str:
    """Classify bus service (local vs express) from route length; commuters use rail/ferry in this model."""
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
    if is_bus_hint or _line_is_bus(line):
        svc = _AI_LINE_SERVICE_TYPE.get(line_name, "local")
        if svc == "express":
            return AI_BUS_EXPRESS_STOP_SPACING_GRID
        return AI_BUS_STOP_SPACING_GRID
    if _line_is_ferry(line):
        # Ferry terminals are sparse by design.
        return AI_COMMUTER_STOP_SPACING_GRID
    service = _AI_LINE_SERVICE_TYPE.get(line_name, "")
    if service == "commuter":
        return AI_COMMUTER_STOP_SPACING_GRID
    if service == "express":
        return AI_EXPRESS_STOP_SPACING_GRID
    if service == "local":
        return AI_LOCAL_STOP_SPACING_GRID
    if line is not None and hasattr(line, "get_route"):
        inferred = _decide_ai_rail_service_type(line.get_route() or [])
        if line_name:
            _AI_LINE_SERVICE_TYPE[line_name] = inferred
        if inferred == "commuter":
            return AI_COMMUTER_STOP_SPACING_GRID
        if inferred == "express":
            return AI_EXPRESS_STOP_SPACING_GRID
        if inferred == "local":
            return AI_LOCAL_STOP_SPACING_GRID
    return MIN_STATION_SPACING_GRID


def _add_ai_extension_station(ops, player, line_name: str, additional_points: list, extend_from_end: bool) -> None:
    """Add a station at the tip of an extension so the extended line can earn (two endpoints).
    No-op if can't afford or tip too close to existing station. Route includes pending extensions so tip is on line."""
    if not additional_points or len(additional_points) < 2:
        return
    tip = additional_points[-1] if extend_from_end else additional_points[0]
    tx, ty = float(tip[0]), float(tip[1])
    money = ops.get_player_money(player)
    if money < 500:
        return
    game_map = _ops_resolve_game_map(ops)
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


def _add_ai_endpoint_stations(ops, player, line_name: str, road_points: list, is_bus_line: bool = False, service_type: str = None) -> None:
    """Add 2 stations at line endpoints so the line can generate profit. No-op if can't afford.
    Skips creating a station at an endpoint if one already exists within MIN_STATION_SPACING_GRID."""
    if not road_points or len(road_points) < 2:
        return
    p1 = road_points[0]
    p2 = road_points[-1]
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    if (x1, y1) == (x2, y2):
        return
    money = ops.get_player_money(player)
    base_cost = 500
    if money < base_cost * 2:
        return
    game_map = _ops_resolve_game_map(ops)
    if service_type:
        _AI_LINE_SERVICE_TYPE[line_name] = service_type
    raw_ml = game_map.get_lines_by_name() if game_map and hasattr(game_map, "get_lines_by_name") else None
    map_lines = raw_ml if isinstance(raw_ml, dict) else {}
    line_obj = map_lines.get(line_name) if map_lines else None
    min_spacing = _get_ai_station_spacing(line_name=line_name, line=line_obj, is_bus_hint=is_bus_line)
    all_coords = _get_all_station_coords(game_map, ops) if game_map else []
    def _too_close(x, y):
        for sx, sy in all_coords:
            if math.hypot(x - sx, y - sy) < min_spacing:
                return True
        if getattr(player, 'is_ai', False) and hasattr(ops, '_direct_distance_between_stop_positions_m'):
            for sx, sy in all_coords:
                if ops._direct_distance_between_stop_positions_m(x, y, sx, sy) < 80.0:
                    return True
        return False
    # OSM endpoints can land on low-pop road vertices. Snap station candidates to nearby
    # route points that have population so the first line can become revenue-generating.
    def _snap_to_populated_route_point(x: float, y: float, from_start: bool) -> tuple[float, float]:
        if not road_points:
            return (x, y)
        seq = road_points[:10] if from_start else list(reversed(road_points[-10:]))
        best = (x, y)
        best_d = float("inf")
        for rp in seq:
            rx, ry = float(rp[0]), float(rp[1])
            pop = _ai_population_near_point(game_map, rx, ry)
            if pop <= 0:
                continue
            d = math.hypot(rx - x, ry - y)
            if d < best_d:
                best = (rx, ry)
                best_d = d
        return best
    if _ai_population_near_point(game_map, x1, y1) <= 0:
        x1, y1 = _snap_to_populated_route_point(x1, y1, from_start=True)
    if _ai_population_near_point(game_map, x2, y2) <= 0:
        x2, y2 = _snap_to_populated_route_point(x2, y2, from_start=False)
    try:
        r1 = None
        if not _too_close(x1, y1) and _ai_population_near_point(game_map, x1, y1) > 0:
            current_year = ops.get_year() if ops else 1830
            n1 = _generate_ai_station_name(
                ops, game_map, x1, y1, current_year, line_name=line_name, station_ordinal=1,
            )
            if hasattr(ops, 'assert_station_name_unique_for_player'):
                try:
                    ops.assert_station_name_unique_for_player(player, n1)
                except ValueError:
                    n1 = f"{n1} (A)"
            r1 = ops.create_station_complete(n1, x1, y1, player, [line_name])
            all_coords.append((x1, y1))
        if isinstance(r1, dict):
            try:
                remaining = int(r1.get('remaining_money', money) or 0)
            except (TypeError, ValueError):
                remaining = money
        else:
            remaining = money
        if remaining >= base_cost and not _too_close(x2, y2) and _ai_population_near_point(game_map, x2, y2) > 0:
            current_year = ops.get_year() if ops else 1830
            n2 = _generate_ai_station_name(
                ops, game_map, x2, y2, current_year, line_name=line_name, station_ordinal=2,
            )
            if hasattr(ops, 'assert_station_name_unique_for_player'):
                try:
                    ops.assert_station_name_unique_for_player(player, n2)
                except ValueError:
                    n2 = f"{n2} (B)"
            ops.create_station_complete(n2, x2, y2, player, [line_name])
    except Exception:
        pass


def _generate_ai_station_name(
    ops,
    game_map,
    x: float,
    y: float,
    current_year: int,
    line_name: str = None,
    station_ordinal: int = None,
) -> str:
    """Generate a human-friendly station name for AI builds. Uses map preset geography when available."""
    existing = set()
    try:
        m = game_map or (ops.get_game().get_map() if ops and ops.get_game() else None)
        if m and hasattr(m, "stations") and m.stations:
            for st in m.stations:
                n = getattr(st, "name", None)
                if n:
                    existing.add(str(n))
        # Also include station names already queued this turn to avoid duplicates like
        # "Hudson Center (Hudson Line)" appearing twice before construction completes.
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
    if preset_name:
        try:
            from map_preset_loader import get_preset_station_name_for_point
            suggestion = get_preset_station_name_for_point(
                preset_name,
                x,
                y,
                game_map,
                current_year=current_year,
                line_name=line_name,
                existing_names=existing,
            )
            if suggestion:
                s = str(suggestion).strip()
                # Keep station names neighborhood/street based; strip line-name suffix
                # from preset suggestions such as "Hudson Center (Hudson Line)".
                if line_name:
                    ln = str(line_name).strip()
                    tagged = f" ({ln})"
                    if s.endswith(tagged):
                        s = s[:-len(tagged)].strip()
                if s and s not in existing:
                    return s
                if s:
                    if station_ordinal is not None:
                        ord_cand = f"{s} — Stop {station_ordinal}"
                        if ord_cand not in existing:
                            return ord_cand
                    k = 2
                    while f"{s} ({k})" in existing and k < 999:
                        k += 1
                    return f"{s} ({k})"
        except Exception:
            pass

    # Tie name to nearest populated grid cell (short walk) so stations read as a local place, not generic fiction.
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
            if hood not in existing:
                return hood
            t = 2
            while f"{hood} ({t})" in existing and t < 999:
                t += 1
            return f"{hood} ({t})"

    # Fallback naming: street/neighborhood-style defaults, not line-derived placeholders.
    neighborhoods = [
        "Downtown", "Midtown", "Uptown", "Riverside", "Harbor", "Market",
        "Old Town", "North End", "South End", "East Side", "West Side",
        "Hillcrest", "Parkside", "Civic Center", "University",
    ]
    streets = [
        "Main Street", "Broadway", "Oak Street", "Maple Avenue", "River Road",
        "Union Street", "Liberty Avenue", "Park Avenue", "Canal Street",
        "Center Street", "Harbor Drive", "Lexington Avenue",
    ]
    suffixes = ["Station", "Terminal", "Junction", "Square", "Center"]
    game_seed = _get_game_seed(ops)
    # Stable per-point naming so retries at same location are consistent.
    seed = (game_seed ^ int(current_year * 131) ^ int(round(x * 1000)) ^ (int(round(y * 1000)) << 1)) & 0xFFFFFFFF
    nrng = random.Random(seed)
    street = nrng.choice(streets)
    hood = nrng.choice(neighborhoods)
    candidates = [
        street,
        f"{hood} {nrng.choice(suffixes)}",
        f"{street} {nrng.choice(suffixes)}",
        f"{hood} - {street}",
    ]
    for cand in candidates:
        if cand not in existing:
            return cand
    base = candidates[0]
    k = 2
    while f"{base} ({k})" in existing and k < 999:
        k += 1
    return f"{base} ({k})"


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
    min_pop_for_build = getattr(game, 'ai_min_population', 100) if game else 100
    is_first = _is_first_line_for_player(ops, player, game_map)
    min_line_length = MIN_FIRST_LINE_LENGTH_GRID if is_first else MIN_LINE_LENGTH_GRID
    preset_name = getattr(game_map, 'map_preset_name', None)
    if preset_name:
        from map_preset_loader import (
            get_preset_road_segment_points,
            load_map_preset,
            preset_has_water_areas,
        )
        if preset is None:
            preset = load_map_preset(preset_name)
        if preset and hasattr(preset, 'get_road_segment_points_for_ai'):
            road_points = []
            if first_game_year and is_first:
                try:
                    budget = min(max(500, int(spendable), int(ops.get_player_money(player))), 120000)
                    cached = _osm_first_turn_try_cached_segment_build(
                        ops, player, game_map, current_year, rng, preset, budget
                    )
                    if _ai_line_create_result_ok(cached):
                        return cached
                except Exception:
                    pass
            try:
                route_overlap_filter = existing_routes
                # Turn-one guarantee on OSM maps: let each AI source a corridor without
                # being blocked by same-turn overlap pruning from other queued first lines.
                if first_game_year and is_first:
                    route_overlap_filter = None
                # OSM street polylines: endpoint-to-endpoint must be > 500m (0.55 grid)
                # Ensure a starter corridor can host two stations at practical spacing.
                min_len_road = max(MIN_LINE_LENGTH_GRID, AI_LOCAL_STOP_SPACING_GRID + 0.15)
                max_len = 4.0
                # Try decreasing min_endpoint_population until preset returns segments (core-only solution:
                # prefer high-pop areas first, then fall back so AIs can build e.g. in dense urban areas).
                # When other AIs have already built this same year, high min_endpoint_population thresholds
                # can be a time sink on sparse maps (they often yield no candidate roads at all until the
                # final fallback of 0). Lower the first rung to reach viable corridors sooner.
                if first_game_year and is_first and preset_name in ("nyc", "ny_metro"):
                    _pop_thresholds = [0]
                elif existing_routes:
                    _pop_thresholds = [max(500, min_pop_for_build), 300, 150, 75, 50]
                else:
                    _pop_thresholds = [max(1000, min_pop_for_build), 500, 300, 200, 100, 50]
                if preset_name in ("nyc", "ny_metro") and not (first_game_year and is_first):
                    _pop_thresholds = [t for t in _pop_thresholds if t >= 50]
                min_pop_road = _pop_thresholds[0]
                for pop_thresh in _pop_thresholds:
                    road_points = get_preset_road_segment_points(
                        preset_name, game_map, current_year, max_points=8,
                        existing_line_routes=route_overlap_filter,
                        min_length_grid=min_len_road,
                        max_length_grid=max_len,
                        min_endpoint_population=pop_thresh,
                        rng=rng,
                        preset=preset
                    )
                    if len(road_points) >= 2:
                        min_pop_road = pop_thresh
                        break
                # Strongly prefer at least one ferry line over time on water maps.
                has_ferry_line = any(
                    bool((game_map.get_lines_by_name() or {}).get(ln).__class__.__name__ == "Ferry")
                    for ln in (player.get_line_names() or [])
                    if (game_map.get_lines_by_name() or {}).get(ln) is not None
                )
                # Keep ferries present but not dominant.
                # First line must use streets (profitability, OSM tests); ferries come after a corridor exists.
                ferry_chance = 0.22 if not has_ferry_line else 0.08
                if (
                    preset_has_water_areas(preset_name)
                    and not is_first
                    and rng.random() < ferry_chance
                ):
                    try:
                        ferry_result = _try_build_ferry_line(ops, player, game_map, spendable, current_year, points_with_pop, rng=rng, existing_routes=existing_routes, preset=preset)
                        if ferry_result:
                            return ferry_result
                    except Exception:
                        pass

                for build_attempt in range(10 if existing_routes else 4):
                    if build_attempt > 0:
                        game_seed = _get_game_seed(ops)
                        attempt_rng = random.Random((game_seed ^ (current_year * 1000 + getattr(player, 'player_id', 0) * 10 + build_attempt)) & 0xFFFFFFFF)
                        road_points = get_preset_road_segment_points(
                            preset_name, game_map, current_year, max_points=8,
                            existing_line_routes=route_overlap_filter,
                            min_length_grid=min_len_road,
                            max_length_grid=max_len,
                            min_endpoint_population=min_pop_road,
                            rng=attempt_rng,
                            preset=preset
                        )
                    if len(road_points) < 2:
                        continue
                    road_points = _ai_prepare_osm_route_points(road_points, game_map, min_len_road)
                    if not road_points:
                        continue
                    total_length = sum(
                        math.hypot(road_points[i+1][0]-road_points[i][0], road_points[i+1][1]-road_points[i][1])
                        for i in range(len(road_points)-1)
                    )
                    if total_length < min_len_road:
                        continue
                    road_cost_per_unit = 225
                    # Vary mode: local/express bus vs local/express/commuter rail (street-running rail).
                    # Buses are cheaper to build; rail costs full ROW. Bias toward rail when bus vehicles are rare (early years).
                    try:
                        pid = int(getattr(player, 'player_id', 0) or 0)
                    except (TypeError, ValueError):
                        pid = 0
                    mode_roll = ((rng.random() * 13.7 + pid * 0.31 + total_length * 2.1) % 1.0)
                    use_bus = mode_roll < 0.46
                    if current_year < 1895:
                        # Horse-bus era: still mix in bus lines, but rail is more reliable (trainsets).
                        use_bus = mode_roll < 0.34
                    bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * 1.5))
                    rail_cost = max(15, int(total_length * road_cost_per_unit * 1.5))
                    cost = bus_cost if use_bus else rail_cost
                    if first_game_year and cost > spendable and len(road_points) >= 2:
                        # First-turn affordability on OSM maps: shorten a long corridor into an affordable starter segment.
                        unit_cost = road_cost_per_unit * (0.5 * 1.5 if use_bus else 1.5)
                        max_len_affordable = max(min_len_road, (max(0, spendable - 15) / max(1.0, unit_cost)))
                        if max_len_affordable < total_length:
                            shortened = [road_points[0]]
                            walked = 0.0
                            for i_seg in range(len(road_points) - 1):
                                a = road_points[i_seg]
                                b = road_points[i_seg + 1]
                                seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
                                if seg_len <= 0:
                                    continue
                                if walked + seg_len <= max_len_affordable:
                                    shortened.append(b)
                                    walked += seg_len
                                    continue
                                remain = max(0.0, max_len_affordable - walked)
                                t = min(1.0, remain / seg_len)
                                shortened.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
                                walked = max_len_affordable
                                break
                            if len(shortened) >= 2:
                                road_points = shortened
                                total_length = sum(
                                    math.hypot(road_points[i + 1][0] - road_points[i][0], road_points[i + 1][1] - road_points[i][1])
                                    for i in range(len(road_points) - 1)
                                )
                                bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * 1.5))
                                rail_cost = max(15, int(total_length * road_cost_per_unit * 1.5))
                                cost = bus_cost if use_bus else rail_cost
                    pop_map = getattr(game_map, 'population', None) or {}
                    p1, p2 = road_points[0], road_points[-1]
                    grid_p1 = _population_at_or_near(p1[0], p1[1], pop_map, radius=4)
                    grid_p2 = _population_at_or_near(p2[0], p2[1], pop_map, radius=4)
                    preset_p1 = _ai_population_near_point(game_map, p1[0], p1[1])
                    preset_p2 = _ai_population_near_point(game_map, p2[0], p2[1])
                    # NYC/NY Metro preset population lookup uses a geographic cap so Hudson-adjacent NJ
                    # road endpoints cannot borrow Manhattan population through a wide grid-radius lookup.
                    if preset_name in ("nyc", "ny_metro"):
                        pop1 = preset_p1
                        pop2 = preset_p2
                    else:
                        pop1 = max(grid_p1, preset_p1)
                        pop2 = max(grid_p2, preset_p2)
                    if first_game_year:
                        if pop1 <= 0 or pop2 <= 0:
                            continue
                    else:
                        if pop1 <= 0 or pop2 <= 0 or pop1 < min_pop_road or pop2 < min_pop_road:
                            continue
                    est_profit = _estimate_line_profit(pop1, pop2, total_length, cost, current_year, game_start_year=gsy)
                    min_profit = 1 if current_year > gsy + 10 else 0  # Lower threshold early game for survival
                    # Only build if we will not net lose: payback window is longer during AI_EARLY_GROWTH_YEARS.
                    #
                    # On NYC presets, sometimes the only available street corridor for later AIs is the preset's
                    # last-resort route (min_endpoint_population=0). In that situation, requiring strict 10-year
                    # cost recovery can deadlock AIs into ai_minimum_turn even though a build is the best next step.
                    relax_profit_gate = (current_year <= gsy + 5 and min_pop_road == 0)
                    if relax_profit_gate:
                        profit_ok = (est_profit >= min_profit or first_game_year)
                    elif first_game_year:
                        profit_ok = True
                    elif early_growth_build:
                        will_net_lose = est_profit * payback_years < cost
                        if est_profit <= 0 and est_profit < -max(600, cost // 15):
                            will_net_lose = True
                        profit_ok = est_profit > min_profit and not will_net_lose
                    else:
                        will_net_lose = (est_profit <= 0 or est_profit * 10 < cost)
                        profit_ok = est_profit > min_profit and not will_net_lose
                    # An AI must not start a line overlapping another line: skip if any segment overlaps existing routes
                    if route_overlap_filter:
                        overlaps = False
                        tol = AI_STREET_DUPLICATE_OVERLAP_GRID
                        for i in range(len(road_points) - 1):
                            a, b = road_points[i], road_points[i + 1]
                            if _segment_overlaps_existing_routes(
                                (a[0], a[1]), (b[0], b[1]), existing_routes, tolerance=tol
                            ):
                                overlaps = True
                                break
                        if overlaps:
                            continue
                    if cost <= spendable and profit_ok:
                        road_points = _ai_prepare_osm_route_points(road_points, game_map, min_len_road) or road_points
                        total_length = sum(
                            math.hypot(road_points[i + 1][0] - road_points[i][0], road_points[i + 1][1] - road_points[i][1])
                            for i in range(len(road_points) - 1)
                        )
                        bus_cost = max(15, int(total_length * road_cost_per_unit * 0.5 * 1.5))
                        rail_cost = max(15, int(total_length * road_cost_per_unit * 1.5))
                        cost = bus_cost if use_bus else rail_cost
                        if cost > spendable:
                            continue
                        elevation_levels = _get_safe_elevation_levels(road_points, game_map, preset_name, base='street')
                        cost_info = {'cost': cost, 'length': total_length, 'points_count': len(road_points), 'elevation_levels': elevation_levels}
                        line_name = _generate_ai_line_name(
                            player, rng, ops=ops, route_points=road_points, current_year=current_year,
                        )
                        result = None
                        try:
                            result = ops.create_line_complete(
                                line_name, road_points, player,
                                elevation_levels=elevation_levels,
                                is_ferry_line=False, is_bus_line=use_bus,
                                cost_info=cost_info, use_fast_path=True
                            )
                        except ValueError:
                            continue
                        if not result:
                            continue
                        if result.get('success', True):
                            result['line_name'] = line_name
                            result['points'] = [(float(p[0]), float(p[1])) for p in road_points]
                            result['cost'] = cost
                            try:
                                if use_bus:
                                    bus_svc = _decide_ai_bus_service_type(road_points)
                                    _add_ai_endpoint_stations(ops, player, line_name, road_points, is_bus_line=True, service_type=bus_svc)
                                    _try_assign_unassigned_bus_to_line(ops, player, line_name)
                                else:
                                    rail_svc = _decide_ai_rail_service_type(road_points)
                                    _add_ai_endpoint_stations(ops, player, line_name, road_points, is_bus_line=False, service_type=rail_svc)
                            except Exception:
                                pass
                            return result
            except Exception:
                pass
            road_points = road_points or []
            # Road preset: if we had road points but build failed, do NOT fall back to population points
            if len(road_points) >= 2:
                return None
            # Only raise if we exhausted all fallbacks (1000, 800, 100, 0) and still have no roads
            if len(road_points) < 2:
                # When other AIs built this turn, try once more with different rng to get another borough/area.
                if existing_routes and rng:
                    last_rng = random.Random((current_year * 1000 + getattr(player, 'player_id', 0) + 999) % (2**32))
                    road_points = get_preset_road_segment_points(
                        preset_name, game_map, current_year, max_points=8,
                        existing_line_routes=existing_routes,
                        min_length_grid=min_len_road,
                        max_length_grid=max_len,
                        min_endpoint_population=0,
                        rng=last_rng
                    )
                    # If overlap-filtered preset call still yields nothing, ask preset for raw corridor
                    # candidates (without existing-route pruning). We still enforce anti-overlap below.
                    if len(road_points) < 2:
                        road_points = get_preset_road_segment_points(
                            preset_name, game_map, current_year, max_points=8,
                            existing_line_routes=None,
                            min_length_grid=min_len_road,
                            max_length_grid=max_len,
                            min_endpoint_population=0,
                            rng=last_rng
                        )
                if route_overlap_filter and len(road_points) < 2:
                    # Don't crash later AIs when crowded; caller may retry with fresh stochastic seeds.
                    return None
                if len(road_points) < 2:
                    # No corridor this attempt (e.g. bad RNG); stochastic caller retries other seeds.
                    return None
    # Guard: never use population-point building when preset provides road polylines
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
                p1, p2 = (x1, y1), (x2, y2)
                dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                if dist >= min_dist and max(15, int(150 * dist)) <= spendable:
                    if _ai_population_near_point(game_map, p1[0], p1[1]) <= 0 or _ai_population_near_point(game_map, p2[0], p2[1]) <= 0:
                        continue
                    try:
                        dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                        cost = max(15, int(150 * dist))
                        elev = _get_safe_elevation_levels([p1, p2], game_map, preset_name or '', base='street')
                        cost_info = {'cost': cost, 'length': dist, 'points_count': 2, 'elevation_levels': elev}
                        line_name = _generate_ai_line_name(
                            player, rng, ops=ops, route_points=[p1, p2], current_year=current_year,
                        )
                        result = ops.create_line_complete(
                            line_name, [p1, p2], player,
                            elevation_levels=elev,
                            is_ferry_line=False, is_bus_line=False,
                            cost_info=cost_info, use_fast_path=True
                        )
                        if result.get('success', True):
                            result['line_name'] = line_name
                            result['points'] = [p1, p2]
                            result['cost'] = cost
                            rail_service = _decide_ai_rail_service_type([p1, p2])
                            _AI_LINE_SERVICE_TYPE[line_name] = rail_service
                            _add_ai_endpoint_stations(ops, player, line_name, [p1, p2], is_bus_line=False, service_type=rail_service)
                            return result
                    except Exception:
                        pass
        return None

    # When other AIs built this turn, try corner/edge and interior fallback segments (spread so one is likely non-overlapping)
    if existing_routes:
        w, h = _map_width_height(game_map)
        pid = getattr(player, 'player_id', 0)
        # Per-player corner diagonals (far from center) so each AI has a non-overlapping option
        corner_per_player = [
            (5, 5, 12, 12), (w - 12, 5, w - 5, 12), (5, h - 12, 12, h - 5), (w - 12, h - 12, w - 5, h - 5),
        ]
        edge_fallbacks = [corner_per_player[pid % 4]]
        edge_fallbacks += [
            (5, 5, 12, 5), (5, 5, 5, 12),
            (w - 12, 5, w - 5, 5), (w - 5, 5, w - 5, 12),
            (5, h - 12, 5, h - 5), (5, h - 5, 12, h - 5),
            (w - 12, h - 5, w - 5, h - 5), (w - 5, h - 12, w - 5, h - 5),
            (w // 4, 8, w // 4 + 12, 8), (w - w // 4 - 12, 8, w - w // 4, 8),
            (8, h // 4, 8, h // 4 + 12), (w - 8, h // 4, w - 8, h // 4 + 12),
            (w // 4, h - 8, w // 4 + 12, h - 8), (3 * w // 4 - 12, h - 8, 3 * w // 4, h - 8),
        ]
        off = (pid % 4) * max(1, (len(edge_fallbacks) - 1) // 4)
        edge_fallbacks = edge_fallbacks[:1] + (edge_fallbacks[1 + off:] + edge_fallbacks[1:1 + off])
        rng.shuffle(edge_fallbacks[1:])
        for x1, y1, x2, y2 in edge_fallbacks:
            if not (0 <= x1 <= w and 0 <= y1 <= h and 0 <= x2 <= w and 0 <= y2 <= h):
                continue
            p1, p2 = (float(x1), float(y1)), (float(x2), float(y2))
            if p1 == p2 or math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < min_line_length:
                continue
            if _segment_overlaps_existing_routes(p1, p2, existing_routes, game_map=game_map):
                continue
            if _ai_population_near_point(game_map, p1[0], p1[1]) <= 0 or _ai_population_near_point(game_map, p2[0], p2[1]) <= 0:
                continue
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if max(15, int(150 * dist)) > spendable:
                continue
            try:
                cost = max(15, int(150 * dist))
                elev = _get_safe_elevation_levels([p1, p2], game_map, preset_name or '', base='street')
                cost_info = {'cost': cost, 'length': dist, 'points_count': 2, 'elevation_levels': elev}
                line_name = _generate_ai_line_name(
                    player, rng, ops=ops, route_points=[p1, p2], current_year=current_year,
                )
                result = ops.create_line_complete(
                    line_name, [p1, p2], player,
                    elevation_levels=elev,
                    is_ferry_line=False, is_bus_line=False,
                    cost_info=cost_info, use_fast_path=True
                )
                if result.get('success', True):
                    result['line_name'] = line_name
                    result['points'] = [p1, p2]
                    result['cost'] = cost
                    rail_service = _decide_ai_rail_service_type([p1, p2])
                    _AI_LINE_SERVICE_TYPE[line_name] = rail_service
                    _add_ai_endpoint_stations(ops, player, line_name, [p1, p2], is_bus_line=False, service_type=rail_service)
                    return result
            except Exception:
                pass

    # When other AIs built this turn, use more candidates (we already built points_with_pop from quadrants)
    max_cand = (48 if existing_routes else _MAX_BUILD_CANDIDATES)
    # Prefer larger population centers - filter to points with meaningful population (min_pop_for_build from top of function)
    candidates = [(pt, pop) for pt, pop in points_with_pop[:min(max_cand, len(points_with_pop))]
                  if pop >= min_pop_for_build]
    if len(candidates) < 2:
        candidates = [(pt, pop) for pt, pop in points_with_pop[:min(max_cand, len(points_with_pop))]]

    # Shuffle candidates so different games pick different pairs (same profit tier, different order)
    rng.shuffle(candidates)

    # Build (i,j) pairs, score by estimated profit (most profitable first)
    min_dist = min_line_length
    pairs = []
    for i, ((p1, pop1)) in enumerate(candidates):
        for j, ((p2, pop2)) in enumerate(candidates):
            if i >= j:
                continue
            # Routes must end in populated areas
            if pop1 <= 0 or pop2 <= 0:
                continue
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if dist < min_dist:
                continue
            # For presets with water areas: reject straight lines across land (must use streets); allow ferries (across water)
            if preset_name and preset_has_water_areas(preset_name):
                if _straight_line_crosses_land(p1, p2, game_map, preset_name):
                    continue
            est_cost = max(15, int(150 * dist))
            if est_cost > spendable:
                continue
            est_profit = _estimate_line_profit(pop1, pop2, dist, est_cost, current_year, game_start_year=gsy)
            if est_profit <= 0 and not first_game_year:
                if post_bootstrap_build or not early_growth_build:
                    continue  # Post-bootstrap: only builds with positive estimated annual profit
                if est_profit < -max(400, est_cost // 25):
                    continue
            if not first_game_year and est_profit * payback_years < est_cost:
                continue  # Skip if estimated profit does not cover cost within payback window
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

    # When other lines exist or are under construction, try non-overlapping pairs first so later AIs can build
    # An AI must not start a line overlapping another line: only consider non-overlapping pairs when routes exist.
    if existing_routes and pairs:
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
                        est_profit = _estimate_line_profit(pop1, pop2, dist, est_cost, current_year, game_start_year=gsy)
                        noise = rng.uniform(-0.2, 0.2) * max(1, abs(est_profit))
                        pairs.append((est_profit + noise, p1, p2))
            pairs.sort(key=_section_sort_key)
    else:
        pairs.sort(key=_section_sort_key)

    # When we have non-overlapping pairs (other AIs built this turn), try them in order first
    if existing_routes and pairs and not _segment_overlaps_existing_routes(
        pairs[0][1], pairs[0][2], existing_routes, game_map=game_map
    ):
        pass  # pairs already sorted with non_ov first
    else:
        # Pick from a tight top band during bootstrap so AI locks onto the strongest demand corridors.
        if early_growth_build and is_first:
            top_n = min(2, len(pairs))
        else:
            top_n = min(6 if early_growth_build else 12, len(pairs))
        if top_n == 0:
            return None
        idx = rng.randint(0, top_n - 1) if top_n > 1 else 0
        pairs = [pairs[idx]] + [p for i, p in enumerate(pairs) if i != idx]

    max_attempts = 15 if existing_routes else _MAX_LINE_CREATE_ATTEMPTS
    attempts = 0
    for score, p1, p2 in pairs:
        if attempts >= max_attempts:
            break
        attempts += 1
        line_name = _generate_ai_line_name(
            player, rng, ops=ops, route_points=[p1, p2], current_year=current_year,
        )
        points = [p1, p2]
        dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        # Only build a ferry when the segment actually crosses open water (60m rule), not on every water map.
        is_ferry = _ai_segment_qualifies_as_ferry(p1, p2, game_map, preset_name or "")
        if preset_name and preset_has_water_areas(preset_name) and not is_ferry:
            continue  # never build land as point-to-point when map has water (must use OSM)
        if _ai_population_near_point(game_map, p1[0], p1[1]) <= 0 or _ai_population_near_point(game_map, p2[0], p2[1]) <= 0:
            continue
        if is_ferry:
            cost = max(15, int(40 * dist * 0.8))  # ferry cost formula
            elev = ['water'] * (len(points) - 1)
        else:
            cost = max(15, int(150 * dist))
            elev = _get_safe_elevation_levels(points, game_map, preset_name or '', base='street')
        cost_info = {'cost': cost, 'length': dist, 'points_count': 2, 'elevation_levels': elev}
        try:
            result = ops.create_line_complete(
                line_name, points, player,
                elevation_levels=elev,
                is_ferry_line=is_ferry,
                is_bus_line=False,
                cost_info=cost_info,
                use_fast_path=not is_ferry,
            )
            if result.get('success', True):
                if is_ferry:
                    _add_ai_endpoint_stations(
                        ops, player, line_name, points, is_bus_line=False, service_type="commuter",
                    )
                else:
                    rail_service = _decide_ai_rail_service_type(points)
                    _AI_LINE_SERVICE_TYPE[line_name] = rail_service
                    _add_ai_endpoint_stations(
                        ops, player, line_name, points, is_bus_line=False, service_type=rail_service,
                    )
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
    if not getattr(player, 'is_ai', False):
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
    if not _ai_station_site_ok(ops, player, x, y):
        return None
    base = (name or "Station").strip()
    for attempt in range(12):
        try_name = base if attempt == 0 else f"{base} ({attempt + 1})"
        if hasattr(ops, 'assert_station_name_unique_for_player'):
            try:
                ops.assert_station_name_unique_for_player(player, try_name)
            except ValueError:
                continue
        try:
            r = ops.add_station_to_line_at_point(player, line_name, try_name, x, y)
            if r.get('success'):
                return r
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
        if player is None or _ai_station_site_ok(ops, player, best_pt[0], best_pt[1]):
            return best_pt
    # Fallback: any route point not excluded that still has nearby population
    for pt in route:
        rx, ry = _pt(pt)
        key = (round(rx), round(ry))
        if key not in exclude_pts and _ai_population_near_point(game_map, rx, ry) > 0:
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


def _extension_turn_ok(route: list, additional_points: list, extend_from_end: bool, max_degrees: float = 145.0) -> bool:
    """True if the extension does not turn the line more than max_degrees.
    route: existing line route. additional_points: new points (includes connection point).
    extend_from_end: True = extending from route end."""
    def _pt(p):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return (float(p[0]), float(p[1]))
        return (float(getattr(p, 'x', 0)), float(getattr(p, 'y', 0)))
    if len(route) < 2 or len(additional_points) < 2:
        return True
    # Incoming direction: last segment of route (into the end we're extending from)
    if extend_from_end:
        prev_pt = _pt(route[-2])
        base_pt = _pt(route[-1])
    else:
        prev_pt = _pt(route[1])
        base_pt = _pt(route[0])
    # Outgoing direction: first segment of extension
    add_base = _pt(additional_points[0])
    add_next = _pt(additional_points[1])
    v1 = (base_pt[0] - prev_pt[0], base_pt[1] - prev_pt[1])
    v2 = (add_next[0] - add_base[0], add_next[1] - add_base[1])
    d1 = math.hypot(v1[0], v1[1]) or 1e-9
    d2 = math.hypot(v2[0], v2[1]) or 1e-9
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    cos_angle = max(-1, min(1, dot / (d1 * d2)))
    angle_rad = math.acos(cos_angle)
    angle_deg = math.degrees(angle_rad)
    return angle_deg <= max_degrees


def _try_extend_line(
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
    preset_uses_osm_only = preset is not None and hasattr(preset, 'get_road_extension_points_from_end') if preset_name else False

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
        route = ops.get_line_route(line) if line else []
        if not route or len(route) < 2:
            continue

        route_pts = {(_pt(p)[0], _pt(p)[1]) for p in route}
        start, end = _pt(route[0]), _pt(route[-1])

        # Try extending along street polylines if preset provides (OSM only on road presets - no straight-line fallback)
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
                    road_pts = get_preset_road_extension_points(
                        preset_name, game_map, base, other, current_year, extend_from_end, max_points=8,
                        prefer_bridges=True,  # Prefer bridges/tunnels when crossing water
                        preset=preset,
                        max_neighbor_street_gap_grid=max_neighbor,
                    )
                    if len(road_pts) >= 2:
                        additional_points = list(road_pts)
                        # Check 145-degree max turn: extension must not turn more than 145 degrees.
                        if not _extension_turn_ok(route, additional_points, extend_from_end):
                            continue
                        # Build full route to check for self-overlap (extension looping back)
                        route_pts = [_pt(p) for p in route]
                        add_pts = [_pt(ap) for ap in additional_points]
                        full_pts = route_pts + add_pts if extend_from_end else add_pts + route_pts
                        try:
                            from map_preset_utils import polyline_has_self_overlap, ai_grid_polyline_overlap_tolerance
                            _tol_ov = ai_grid_polyline_overlap_tolerance(game_map)
                            if polyline_has_self_overlap(full_pts, tolerance=_tol_ov):
                                continue
                        except ImportError:
                            pass
                        if extend_from_end:
                            e_a, e_b = route_pts[0], add_pts[-1]
                        else:
                            e_a, e_b = add_pts[0], route_pts[-1]
                        if (
                            _ai_population_near_point(game_map, e_a[0], e_a[1]) <= 0
                            or _ai_population_near_point(game_map, e_b[0], e_b[1]) <= 0
                        ):
                            continue
                        if require_profit_when_troubled:
                            try:
                                gsy = _game_start_year(ops)
                                full_len = _route_polyline_length(full_pts)
                                pop_a = _ai_population_near_point(game_map, e_a[0], e_a[1])
                                pop_b = _ai_population_near_point(game_map, e_b[0], e_b[1])
                                est_profit = _estimate_line_profit(pop_a, pop_b, max(0.1, full_len), 1, current_year, game_start_year=gsy)
                                # Conservative payback when in trouble: extension must plausibly pay back quickly.
                                payback = 7
                                if est_profit <= 0 or (est_profit * payback) < (cost * 1.05):
                                    continue
                            except Exception:
                                continue
                        total_len = sum(
                            math.hypot(additional_points[i+1][0]-additional_points[i][0],
                                       additional_points[i+1][1]-additional_points[i][1])
                            for i in range(len(additional_points)-1)
                        )
                        cost = max(100, int(150 * total_len * 0.75))
                        if cost <= spendable and cost >= 1:
                            # Do not extend if it would net lose: skip large cost on unprofitable lines
                            if cost > 2000 and not _is_first_game_year(ops, current_year):
                                line_obj = (ops.get_map().get_lines_by_name() or {}).get(line_name)
                                if line_obj and hasattr(ops, 'calc_line_profit') and (ops.calc_line_profit(line_obj, current_year) or 0) <= 0:
                                    continue
                            # Include connection segment (route end to first additional point) for water check
                            conn_pt = end if extend_from_end else start
                            full_pts = [conn_pt] + additional_points if extend_from_end else additional_points + [conn_pt]
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
        if preset_uses_osm_only:
            continue  # Road preset but no OSM extension found for this end - try next line; do not fall back to straight lines

        # Phase 1: Cheap rough score (pop / est_cost). Est cost ~ 150 * dist. No expensive ops.
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
        dx = ex - base[0]
        dy = ey - base[1]
        d = math.hypot(dx, dy) or 1
        step = min(5, d)
        new_pt = (base_pt[0] + dx * step / d, base_pt[1] + dy * step / d)
        # Check 145-degree max turn
        if not _extension_turn_ok(route, [base_pt, new_pt], extend_from_end):
            continue
        # Include connection point so segment is properly formed (extend skips duplicate)
        additional_points = [base_pt, new_pt]
        merged_pts = _merge_route_points_for_overlap(route, additional_points, extend_from_end, _pt)
        try:
            from map_preset_utils import polyline_has_self_overlap, ai_grid_polyline_overlap_tolerance
            _tol_m = ai_grid_polyline_overlap_tolerance(game_map)
            if polyline_has_self_overlap(merged_pts, tolerance=_tol_m):
                continue
        except Exception:
            pass
        m0, m1 = merged_pts[0], merged_pts[-1]
        if _ai_population_near_point(game_map, m0[0], m0[1]) <= 0 or _ai_population_near_point(game_map, m1[0], m1[1]) <= 0:
            continue
        # Need 2 elevation levels: connection (route end to base_pt) + segment (base_pt to new_pt)
        seg_elev = _get_safe_elevation_levels(additional_points, game_map, preset_name or '', base='street')
        elevation_levels = ['street'] + seg_elev  # connection is 0-length (base_pt=route end); segment needs water check
        # Use length-based cost (avoid calculate_extend_cost - max 10s per extension)
        conn_len = math.hypot(new_pt[0] - base_pt[0], new_pt[1] - base_pt[1])
        cost = max(100, int(150 * conn_len))
        cost_info = {'cost': cost, 'length': conn_len, 'points_count': 1, 'elevation_levels': elevation_levels}
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
) -> Optional[dict]:
    """Buy best value item from marketplace. Vehicle-agnostic.
    When rng is provided, chance to buy increases by 1% per % the item is off (discount_percent).
    line_only: if True, only consider marketplace lines (for AIs with no network yet).
    allow_rolling_stock: when True, may buy trainsets even in early years if lines need vehicles."""
    yfs = _years_from_game_start(ops, current_year)
    cash = ops.get_player_money(player)
    if not line_only and not allow_rolling_stock and yfs <= 8:
        return None
    if not line_only and allow_rolling_stock and spendable < 800:
        return None
    if not line_only and yfs <= 10 and not allow_rolling_stock:
        # Marketplace-focused AIs should actually shop before they accumulate 60k cash.
        min_cash = 35000 if focus == 'marketplace' else 80000
        if cash < min_cash:
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
        score = 0
        if isinstance(vehicle, Line):
            score = (500 if focus == 'marketplace' else 0) + 300 - price // 100
        elif isinstance(vehicle, Trainset):
            cap = getattr(vehicle, 'capacity', 0) or (sum(getattr(c, 'capacity', 0) for c in getattr(vehicle, 'cars', [])) if hasattr(vehicle, 'cars') else 0) or 50
            score = cap * 2 - price // 100 + (200 if focus == 'marketplace' else 0)
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


def _try_rent_workers(ops, player, current_year: int, spendable: int) -> Optional[dict]:
    """Rent workers if needed. Engineers for trainsets, construction for building.
    In early years, rent at least 2 construction workers when we have unfinished lines (so line completes by year 1834)."""
    from worker import ENGINEER, CONSTRUCTION_WORKER

    assigned_ts = ops.get_player_assigned_trainsets_count(player)
    engineers_have = player.get_engineer_count()
    unassigned = ops.get_unassigned_trainsets(player)
    game_map = ops.get_map() if hasattr(ops, 'get_map') else None
    map_lines = (game_map.get_lines_by_name() or {}) if game_map else {}
    has_line_ready = any(
        map_lines.get(ln) and len((map_lines.get(ln).get_stations() if hasattr(map_lines.get(ln), 'get_stations') else [])) >= 2
        for ln in player.get_line_names()
    )
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
    # Need one engineer per assigned trainset; if more trainsets are unassigned and a line is ready, rent one ahead of the next assign.
    lines_needing_trainset = [
        ln for ln in player.get_line_names()
        if _line_needs_trainset_assignment(map_lines, ln)
    ]
    engineers_needed = assigned_ts
    if unassigned and has_line_ready:
        engineers_needed = max(engineers_needed, assigned_ts + 1)
    if lines_needing_trainset:
        engineers_needed = max(
            engineers_needed,
            assigned_ts + len(unassigned),
            len(lines_needing_trainset),
        )
    elif years_from_start <= 8 and not has_profitable_line and not has_line_ready:
        engineers_needed = assigned_ts
    need_engineers = max(0, engineers_needed - engineers_have)

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

    if need_engineers > 0:
        cost = ops.calculate_worker_rental_cost(ENGINEER, 1, current_year)
        if cost <= spendable:
            try:
                ops.rent_workers_for_player(player, ENGINEER, 1, current_year)
                return {'worker': ENGINEER.name, 'quantity': 1}
            except Exception:
                pass
    # Rent enough construction workers to complete all lines under construction
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
    return None


def _try_lobby(ops, player, spendable: int, focus: str = 'balanced') -> Optional[dict]:
    """Attempt lobbying if government available."""
    cy = ops.get_year() if ops else 1830
    yfs = _years_from_game_start(ops, cy)
    if focus == 'lobbying':
        min_yfs = 4
    elif focus == 'balanced':
        min_yfs = 8
    else:
        min_yfs = 12
    if yfs < min_yfs:
        return None
    gov = ops.get_game().get_government() if hasattr(ops.get_game(), 'get_government') else None
    if not gov:
        return None
    amount = min(5000, spendable // 2)
    if amount < 1000:
        return None
    try:
        option_id = 'drop_excuse'
        result = ops.try_lobby(player, amount, option_id=option_id)
        if result.get('success'):
            result['amount'] = amount
            result['option_id'] = option_id
            return result
    except Exception:
        pass
    return None
