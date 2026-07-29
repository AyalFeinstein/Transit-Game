#!/usr/bin/env python3
"""
AI Training Program - Trains AIs on how to win the Transit Game.

Runs many AI-vs-AI games, records which strategies win, and outputs learned insights.
Use: python ai_training.py [--games N] [--end-year Y] [--map MAP]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from game_presets import AI_DIFFICULTIES, AI_FOCUSES, STARTING_MONEY
from map_preset_loader import preset_ai_uses_street_network, preset_has_population_init
from operations import Operations, get_game_params_for_preset
from ai import (
    ai_count_post_bootstrap_negative_years,
    ai_finance_year_requires_sustained_profit,
    run_ai_turn,
    AI_TURN_TIME_BUDGET_SEC,
)

# Same canonical sets as in-game AI (order preserved for reproducibility in training loops)
FOCUS_OPTIONS = list(AI_FOCUSES)
DIFFICULTY_OPTIONS = list(AI_DIFFICULTIES)


def compute_survival_score(
    years_survived: int,
    horizon_years: int,
    eliminated: bool,
    final_money: int,
    starting_money: int,
    *,
    post_bootstrap_positive_years: int = 0,
    post_bootstrap_negative_years: int = 0,
) -> float:
    """Training-only reward: higher when the AI survives longer and posts positive net after bootstrap.

    Used to rank focus/difficulty in survival training. Does **not** modify ``ai_focus`` or
    per-turn behavior in ``run_ai_turn`` — the in-game AI must keep its assigned focus.
    """
    h = max(1, int(horizon_years))
    y = max(0, int(years_survived))
    progress = min(1.0, y / h)
    score = 10_000.0 * progress
    if eliminated:
        score *= 0.4
    else:
        if y >= h:
            score += 5_000.0
    sm = max(1, int(starting_money))
    fm = max(0, int(final_money))
    ratio = min(3.0, fm / sm)
    score += min(3_000.0, 400.0 * ratio)
    score += 800.0 * max(0, int(post_bootstrap_positive_years))
    score -= 2_500.0 * max(0, int(post_bootstrap_negative_years))
    return round(score, 2)


def run_training_game(
    num_players: int = 4,
    map_preset: str = "standard",
    end_year_override: int = None,
    focus_per_player: dict = None,
    difficulty_per_player: dict = None,
    survival_mode: bool = False,
) -> dict:
    """
    Run one full game with AI-only players. Returns result dict with:
    - winner: player name or None if tie/all eliminated
    - rankings: list of dicts (name, money, focus, difficulty, eliminated, survival_score)
    - end_year, calendar_start, horizon_years, years_survived, starting_money
    - top_survival_score: training reward for top-ranked player (does not affect ai_focus)
    - end_reason: 'end_year' | 'one_winner' | 'all_eliminated'
    """
    player_names = [f"AI_{i+1}" for i in range(num_players)]
    ai_indices = list(range(num_players))
    focus_per_player = focus_per_player or {}
    difficulty_per_player = difficulty_per_player or {}

    # Use full preset map size when the preset defines fixed dimensions; cap standard for speed.
    params = get_game_params_for_preset(map_preset)
    fixed_w = params.get("map_width")
    fixed_h = params.get("map_height")
    if preset_has_population_init(map_preset) or preset_ai_uses_street_network(map_preset):
        map_width = fixed_w if fixed_w is not None else 100
        map_height = fixed_h if fixed_h is not None else 75
    else:
        map_width = min(fixed_w or 100, 80)
        map_height = min(fixed_h or 75, 60)

    # Default bankroll matches normal games (game_presets.STARTING_MONEY).
    if survival_mode:
        num_players = 1
        player_names = ["AI1"]
        ai_indices = [0]
        starting_money = STARTING_MONEY
        natural_disaster_frequency = 0.2
        random_ai_spawn_enabled = False
        ops_kwargs = dict(
            line_build_speed_multiplier=6.0,
            random_ai_spawn_enabled=random_ai_spawn_enabled,
            ai_min_population=100,
        )
    else:
        starting_money = STARTING_MONEY
        natural_disaster_frequency = 0.5
        ops_kwargs = {}
        # Street-network / OSM maps: faster build so lines complete sooner
        if preset_ai_uses_street_network(map_preset):
            ops_kwargs = dict(
                line_build_speed_multiplier=5.0,
                ai_min_population=100,
            )

    ops = Operations(
        player_names,
        map_width=map_width,
        map_height=map_height,
        starting_money=starting_money,
        worker_replenishment_rate=1.0,
        natural_disaster_frequency=natural_disaster_frequency,
        map_preset_name=map_preset,
        ai_player_indices=ai_indices,
        ai_focus_per_player=focus_per_player,
        ai_difficulty_per_player=difficulty_per_player,
        **ops_kwargs,
    )

    try:
        calendar_start = int(
            getattr(ops.game, "start_year", None)
            or getattr(ops.game.get_timeline(), "start_year", None)
            or 1830
        )
    except Exception:
        calendar_start = 1830

    # Override end year for faster training
    end_year = end_year_override or params.get("end_year", 2050)
    if end_year_override:
        ops.game.get_map().end_year = end_year
        ops.game.get_timeline().end_year = end_year

    deadline_per_ai = (AI_TURN_TIME_BUDGET_SEC - 5) / max(1, num_players)
    active = ops.get_active_players()

    while True:
        current_year = ops.get_year()
        if current_year >= end_year:
            break

        # Run AI turns
        import time
        start = time.time()
        for player in ops.get_players():
            if getattr(player, "is_ai", False) and not ops.is_player_eliminated(player):
                deadline = max(2, start + deadline_per_ai - time.time())
                try:
                    run_ai_turn(ops, player, deadline_sec=deadline)
                except Exception:
                    pass

        # Advance year
        try:
            result = ops.advance_year_complete(current_year)
        except Exception as e:
            return {
                "error": str(e),
                "winner": None,
                "rankings": [],
                "end_year": current_year,
                "end_reason": "error",
            }

        active = result.get("active_players", ops.get_active_players())
        if len(active) <= 1:
            break

    final_year = ops.get_year()
    horizon_years = max(1, int(end_year) - int(calendar_start))
    years_survived = max(0, min(int(final_year), int(end_year)) - int(calendar_start))

    # Compute final rankings (by money, eliminated last)
    all_players = ops.get_players()
    rankings = []
    for p in all_players:
        name = ops.get_player_name(p)
        money = ops.get_player_money(p)
        focus = getattr(p, "ai_focus", "balanced") or "balanced"
        difficulty = getattr(p, "ai_difficulty", "medium") or "medium"
        eliminated = ops.is_player_eliminated(p)
        hist = getattr(p, '_ai_annual_net_by_year', None) or {}
        post_pos = sum(
            1 for y, net in hist.items()
            if ai_finance_year_requires_sustained_profit(ops, y) and float(net) > 0
        )
        post_neg = ai_count_post_bootstrap_negative_years(ops, p)
        survival_score = compute_survival_score(
            years_survived=years_survived,
            horizon_years=horizon_years,
            eliminated=eliminated,
            final_money=money,
            starting_money=starting_money,
            post_bootstrap_positive_years=post_pos,
            post_bootstrap_negative_years=post_neg,
        )
        rankings.append(
            {
                "name": name,
                "money": money,
                "focus": focus,
                "difficulty": difficulty,
                "eliminated": eliminated,
                "survival_score": survival_score,
            }
        )

    # Sort: non-eliminated first, then by money desc
    rankings.sort(key=lambda r: (r["eliminated"], -r["money"]))

    # Winner = top non-eliminated, or None
    winner = None
    end_reason = "end_year"
    if current_year >= end_year:
        end_reason = "end_year"
    else:
        end_reason = "one_winner" if len(active) == 1 else "all_eliminated"

    if rankings and not rankings[0]["eliminated"]:
        winner = rankings[0]["name"]

    # Only attribute winning strategy when someone actually won (not all eliminated)
    winner_focus = rankings[0]["focus"] if (rankings and winner) else None
    winner_difficulty = rankings[0]["difficulty"] if (rankings and winner) else None

    # Survival mode: winner = survived (not eliminated)
    if survival_mode and rankings and not rankings[0]["eliminated"]:
        winner = rankings[0]["name"]
        winner_focus = rankings[0]["focus"]
        winner_difficulty = rankings[0]["difficulty"]

    top_score = rankings[0]["survival_score"] if rankings else 0.0

    return {
        "winner": winner,
        "winner_focus": winner_focus,
        "winner_difficulty": winner_difficulty,
        "rankings": rankings,
        "end_year": final_year,
        "end_reason": end_reason,
        "calendar_start": calendar_start,
        "horizon_years": horizon_years,
        "years_survived": years_survived,
        "starting_money": starting_money,
        "top_survival_score": top_score,
    }


def run_training(num_games: int = 20, end_year: int = 1860, map_preset: str = "standard", survival_mode: bool = False):
    """Run training games to learn which strategies win. Outputs insights for AI to use when playing.
    Goal: train the AI to win by identifying and recommending the best focus and difficulty."""
    focus_wins = {f: 0 for f in FOCUS_OPTIONS}
    difficulty_wins = {d: 0 for d in DIFFICULTY_OPTIONS}
    focus_counts = {f: 0 for f in FOCUS_OPTIONS}
    difficulty_counts = {d: 0 for d in DIFFICULTY_OPTIONS}
    focus_survival_scores = defaultdict(list)
    difficulty_survival_scores = defaultdict(list)
    results = []
    games_with_winner = 0

    goal = "SURVIVE 50 years" if survival_mode else "WIN"
    print(f"AI Training: {num_games} games (goal: {goal})")
    print(f"End year {end_year}, map {map_preset}")
    if survival_mode:
        print("Each game: 1 AI (survival test params); we record which focus/difficulty survives")
    else:
        print("Each game: 4 AI players with random focus/difficulty; we record which strategies win")
    print("-" * 50)

    import random
    for g in range(num_games):
        # Survival mode: single AI, random focus/difficulty. Win mode: 4 players.
        if survival_mode:
            focuses = [random.choice(FOCUS_OPTIONS)]
            difficulties = [random.choice(DIFFICULTY_OPTIONS)]
            focus_per = {0: focuses[0]}
            diff_per = {0: difficulties[0]}
        else:
            focuses = [random.choice(FOCUS_OPTIONS) for _ in range(4)]
            difficulties = [random.choice(DIFFICULTY_OPTIONS) for _ in range(4)]
            focus_per = {i: focuses[i] for i in range(4)}
            diff_per = {i: difficulties[i] for i in range(4)}

        result = run_training_game(
            num_players=1 if survival_mode else 4,
            map_preset=map_preset,
            end_year_override=end_year,
            focus_per_player=focus_per,
            difficulty_per_player=diff_per,
            survival_mode=survival_mode,
        )

        if "error" in result:
            print(f"  Game {g+1}: ERROR - {result['error']}")
            continue

        results.append(result)
        winner = result.get("winner")
        winner_focus = result.get("winner_focus")
        winner_diff = result.get("winner_difficulty")

        if winner:
            games_with_winner += 1
            if winner_focus:
                focus_wins[winner_focus] = focus_wins.get(winner_focus, 0) + 1
            if winner_diff:
                difficulty_wins[winner_diff] = difficulty_wins.get(winner_diff, 0) + 1

        for f in focuses:
            focus_counts[f] += 1
        for d in difficulties:
            difficulty_counts[d] += 1

        for r in result.get("rankings", []):
            focus_survival_scores[r["focus"]].append(r["survival_score"])
            difficulty_survival_scores[r["difficulty"]].append(r["survival_score"])

        if survival_mode:
            top_sc = result.get("top_survival_score", 0.0)
            ys = result.get("years_survived")
            print(
                f"  Game {g+1}: survival_score={top_sc:.1f} years_survived={ys} "
                f"(focus={focuses[0]}, diff={difficulties[0]})"
            )
        else:
            print(f"  Game {g+1}: Winner {winner or 'none'} (focus={winner_focus}, diff={winner_diff})")

    # Summary - focus on what wins
    print("\n" + "=" * 50)
    print("TRAINING RESULTS - How to Win")
    print("=" * 50)
    print(f"Games with a winner: {games_with_winner}/{num_games}")
    print("\nBy Focus (wins / times played):")
    for f in FOCUS_OPTIONS:
        count = focus_counts[f]
        wins = focus_wins[f]
        pct = (100 * wins / count) if count else 0
        print(f"  {f}: {wins}/{count} wins ({pct:.1f}%)")

    print("\nBy Difficulty (wins / times played):")
    for d in DIFFICULTY_OPTIONS:
        count = difficulty_counts[d]
        wins = difficulty_wins[d]
        pct = (100 * wins / count) if count else 0
        print(f"  {d}: {wins}/{count} wins ({pct:.1f}%)")

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    best_survival_focus = None
    best_survival_difficulty = None
    if survival_mode:
        print("\nSURVIVAL REWARDS (mean training score — longer survival + solvency; does not change ai_focus):")
        for f in FOCUS_OPTIONS:
            xs = focus_survival_scores[f]
            print(f"  {f}: mean_score={_mean(xs):.1f} (n={len(xs)})")
        print("\nBy difficulty (mean survival score):")
        for d in DIFFICULTY_OPTIONS:
            xs = difficulty_survival_scores[d]
            print(f"  {d}: mean_score={_mean(xs):.1f} (n={len(xs)})")
        best_survival_focus = max(FOCUS_OPTIONS, key=lambda f: _mean(focus_survival_scores[f]))
        best_survival_difficulty = max(
            DIFFICULTY_OPTIONS, key=lambda d: _mean(difficulty_survival_scores[d])
        )
        print(
            f"\nBest survival strategy by score: focus={best_survival_focus}, "
            f"difficulty={best_survival_difficulty}"
        )

    # Save learned insights for AI to use when playing - include multiple good strategies (top by win rate)
    def win_rate_focus(f):
        return focus_wins[f] / max(1, focus_counts[f])
    def win_rate_diff(d):
        return difficulty_wins[d] / max(1, difficulty_counts[d])
    best_focus = max(FOCUS_OPTIONS, key=win_rate_focus)
    best_difficulty = max(DIFFICULTY_OPTIONS, key=win_rate_diff)
    # Good strategies: those with win rate >= 15%, or top 3 by win rate (so AI can pick among several)
    WIN_RATE_THRESHOLD = 0.15
    good_focuses = [f for f in FOCUS_OPTIONS if win_rate_focus(f) >= WIN_RATE_THRESHOLD]
    good_focuses = sorted(good_focuses, key=lambda f: -win_rate_focus(f))
    if len(good_focuses) < 2:
        top3 = sorted(FOCUS_OPTIONS, key=lambda f: -win_rate_focus(f))[:3]
        good_focuses = list(dict.fromkeys([best_focus] + top3))
    good_difficulties = [d for d in DIFFICULTY_OPTIONS if win_rate_diff(d) >= WIN_RATE_THRESHOLD]
    good_difficulties = sorted(good_difficulties, key=lambda d: -win_rate_diff(d))
    if len(good_difficulties) < 2:
        top3 = sorted(DIFFICULTY_OPTIONS, key=lambda d: -win_rate_diff(d))[:3]
        good_difficulties = list(dict.fromkeys([best_difficulty] + top3))
    insights = {
        "focus_win_rates": {
            f: focus_wins[f] / focus_counts[f] if focus_counts[f] else 0.25
            for f in FOCUS_OPTIONS
        },
        "difficulty_win_rates": {
            d: difficulty_wins[d] / difficulty_counts[d] if difficulty_counts[d] else 0.33
            for d in DIFFICULTY_OPTIONS
        },
        "best_focus": best_focus,
        "best_difficulty": best_difficulty,
        "good_focuses": good_focuses,
        "good_difficulties": good_difficulties,
        "games_run": num_games,
        "games_with_winner": games_with_winner,
        "end_year": end_year,
        "training_goal": "survival" if survival_mode else "win",
        "build_strategy": "avoid_redundant_lines_prioritize_profit",
        "map_preset": map_preset,
        "survival_mode": survival_mode,
        "mean_survival_score_by_focus": {
            f: round(_mean(focus_survival_scores[f]), 2) for f in FOCUS_OPTIONS
        },
        "mean_survival_score_by_difficulty": {
            d: round(_mean(difficulty_survival_scores[d]), 2) for d in DIFFICULTY_OPTIONS
        },
        "best_survival_focus_by_score": best_survival_focus,
        "best_survival_difficulty_by_score": best_survival_difficulty,
    }
    insights_path = Path(__file__).parent / "ai_training_insights.json"
    insights_path.write_text(json.dumps(insights, indent=2), encoding="utf-8")
    print(f"\nTraining insights saved to {insights_path}")
    if survival_mode:
        print(
            f"Survival mode: see mean_survival_score_by_* and best_survival_*_by_score in JSON "
            f"(in-game ai_focus unchanged; scores are post-hoc rewards only)."
        )
    else:
        print(f"Recommended for AI: focus={best_focus}, difficulty={best_difficulty}")
        print(f"Good focuses (pick randomly): {good_focuses}")
        print(f"Good difficulties (pick randomly): {good_difficulties}")

    return insights


def main():
    parser = argparse.ArgumentParser(description="Train AI players on how to win the Transit Game")
    parser.add_argument("--games", type=int, default=20, help="Number of training games (default: 20)")
    parser.add_argument("--end-year", type=int, default=1860, help="End year for each game (default: 1860)")
    parser.add_argument("--map", type=str, default="nyc", help="Map preset: standard, nyc, ny_metro (default: nyc)")
    parser.add_argument("--survival", action="store_true", help="Survival mode: 1 AI, 50 years, match NYC 50-year test params")
    args = parser.parse_args()

    if args.survival:
        run_training(num_games=args.games, end_year=1880, map_preset="nyc", survival_mode=True)
    else:
        run_training(num_games=args.games, end_year=args.end_year, map_preset=args.map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
