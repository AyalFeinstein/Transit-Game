"""Game and player initialization helpers."""
from typing import Optional
from game import Game
from line import Line, Ferry, Bus as BusLine
from player import Player
from public_ta import PublicTA
from map import Map
from vehicle import Trainset, Locomotive, Railcar, Vehicle, Boat, Ferry, Bus
from timeline import Timeline
import math
import random
from marketplace import Marketplace, SaleItem
from game_presets import (
    AI_DIFFICULTIES,
    AI_FOCUSES,
    AI_MAX_STRAIGHT_LAND_SEGMENT_MILES,
    LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER,
    LINE_NIMBY_LAWSUIT_BASE_CHANCE,
    STARTING_MONEY,
    STATION_MAX_DISTANCE_FROM_LAND_M,
)

# NIMBY line-build excuses (noise / property / environment).
_LINE_NIMBY_EXCUSE_CAUSES = frozenset({
    'Noise Pollution',
    'Property Damage',
    'Environmental Damage',
})

# AI difficulty bonuses: hard=strongest (cost 0.75x, marketplace 0.75x, lobby +0.15), easy=weakest
AI_DIFFICULTY_COST_MULTIPLIER = {'easy': 1.15, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_MARKETPLACE_MULTIPLIER = {'easy': 1.1, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_LOBBY_BONUS = {'easy': -0.05, 'medium': 0.0, 'hard': 0.15}
# AI-built stops must be at least this straight-line distance from any existing or queued stop (meters).
AI_MIN_STOP_DIRECT_SEPARATION_METERS = 80.0



def generate_players(player_names: list[str], starting_money: int = 10000, ai_player_indices: list[int] = None, ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, map_preset_name: str = None) -> list[Player]:
    """Generate players. ai_player_indices: list of player indices that are AI-controlled.
    ai_focus_per_player: optional dict mapping player_index -> focus for AI players. Unset indices get random focus.
    ai_difficulty_per_player: optional dict mapping player_index -> difficulty ('easy','medium','hard') for AI players."""
    import random
    from vehicle_presets import get_vehicle_registry
    from vehicle import Trainset, Railcar
    registry = get_vehicle_registry()
    
    all_locomotives = registry.get_locomotives_by_year()
    earliest_loco = None
    for loco, year in all_locomotives:
        if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
            earliest_loco = loco
            break
    
    earliest_railcar = registry.get_earliest_railcar()
    
    if not earliest_loco or not earliest_railcar:
        raise ValueError("No suitable vehicles found in registry (need a locomotive that can have railcars and at least one railcar)")
    
    from transit_company_names import get_transit_company_name
    ai_indices = set(ai_player_indices or [])
    players = []
    used_names = set()
    for i, name in enumerate(player_names):
        starting_trainsets = []
        for _ in range(3):
            cars = [Railcar("Standard Passenger", 0, earliest_railcar.capacity, earliest_railcar.maitnance_costs)]
            starting_trainset = Trainset(locomotive=earliest_loco, cars=cars)
            starting_trainsets.append(starting_trainset)
        is_ai = i in ai_indices
        default_name = f"Player{i + 1}"
        stripped_name = (name or "").strip()
        if is_ai:
            if stripped_name and stripped_name != default_name:
                name = stripped_name
            else:
                name = get_transit_company_name(used_names, map_preset_name=map_preset_name)
            focus = (ai_focus_per_player or {}).get(i)
            ai_focus = focus if focus in AI_FOCUSES else random.choice(AI_FOCUSES)
            from game_presets import AI_DEFAULT_DIFFICULTY
            diff = (ai_difficulty_per_player or {}).get(i)
            ai_difficulty = diff if diff in AI_DIFFICULTIES else AI_DEFAULT_DIFFICULTY
        else:
            ai_focus = None
            ai_difficulty = None
        used_names.add(name)
        player = Player(player_id=i, money=starting_money, trainsets=starting_trainsets, name=name, line_names=[], is_ai=is_ai, ai_focus=ai_focus, ai_difficulty=ai_difficulty)
        players.append(player)
    from player_colors import assign_color_indices_for_new_players
    assign_color_indices_for_new_players(players)
    return players

def setup_game(player_names: list[str], map_width: int = 100, map_height: int = 75, starting_money: Optional[int] = None, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, use_osm: bool = False, map_center_lat: float = None, map_center_lon: float = None, marketplace_years_visible: int = None, map_preset_name: str = None, line_build_speed_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, ai_player_indices: list[int] = None, ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, random_ai_spawn_enabled: bool = True, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, profit_margin: float = 1.0, random_ai_spawn_interval_years: int = 30, default_ai_difficulty_for_spawns: str = 'hard', ai_min_population: int = 100, game_seed: int = None, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None, war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None, ruleset_name: str = None, idle_human_benchmark_enabled: bool = False, ai_debug_notifications_enabled: bool = True) -> Game:
    """Create game. Optional overrides for game_presets (line build speed, bulk discount).
    ai_player_indices: list of player indices (0-based) that are AI-controlled.
    ai_focus_per_player: optional dict mapping player_index -> focus for AI players.
    ai_difficulty_per_player: optional dict mapping player_index -> difficulty for AI players.
    random_ai_spawn_enabled: if True, new AI players spawn randomly every 50-100 years.
    merge_enabled: if True, mergers are allowed (master switch).
    merge_ai_ai_enabled: if True, AIs can merge with other AIs (doomed-merge, etc.).
    merge_ai_human_enabled: if True, humans can propose merger with desperate AIs.
    merge_human_human_enabled: if True, humans can propose merger with other humans (both control merged company)."""
    import game_presets
    from game_presets import AI_OUTLAST_IDLE_STARTING_MONEY
    if starting_money is None:
        starting_money = STARTING_MONEY
    if not idle_human_benchmark_enabled and int(starting_money) == int(AI_OUTLAST_IDLE_STARTING_MONEY):
        idle_human_benchmark_enabled = True
    if line_build_speed_multiplier is not None:
        game_presets.LINE_BUILD_SPEED_MULTIPLIER = line_build_speed_multiplier
    if marketplace_bulk_discount_percent is not None:
        game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT = marketplace_bulk_discount_percent
    if marketplace_bulk_discount_threshold is not None:
        game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD = marketplace_bulk_discount_threshold
    from ruleset_loader import apply_ruleset_to_new_game, apply_ruleset_events_to_game
    used_ruleset = apply_ruleset_to_new_game(ruleset_name)
    players = generate_players(player_names, starting_money, ai_player_indices=ai_player_indices, ai_focus_per_player=ai_focus_per_player, ai_difficulty_per_player=ai_difficulty_per_player, map_preset_name=map_preset_name)
    game_map = Map(width=map_width, height=map_height, use_osm=use_osm, map_center_lat=map_center_lat, map_center_lon=map_center_lon, map_preset_name=map_preset_name)

    start_year = game_map.start_year if hasattr(game_map, 'start_year') else 1830
    end_year = game_map.end_year if hasattr(game_map, 'end_year') else 2050
    timeline = Timeline(initial_year=start_year, end_year=end_year)

    if marketplace_years_visible is None:
        from game_presets import MARKETPLACE_YEARS_VISIBLE
        marketplace_years_visible = MARKETPLACE_YEARS_VISIBLE
    marketplace = Marketplace(marketplace_years_visible=marketplace_years_visible)
    initialize_marketplace_trainsets(marketplace, num_players=len(players))
    game = Game(players=players, game_map=game_map, timeline=timeline, marketplace=marketplace, worker_replenishment_rate=worker_replenishment_rate, natural_disaster_frequency=natural_disaster_frequency, random_ai_spawn_enabled=random_ai_spawn_enabled, merge_enabled=merge_enabled, merge_ai_ai_enabled=merge_ai_ai_enabled, merge_ai_human_enabled=merge_ai_human_enabled, merge_human_human_enabled=merge_human_human_enabled, inflation_rate=inflation_rate, profit_margin=profit_margin, random_ai_spawn_interval_years=random_ai_spawn_interval_years, default_ai_difficulty_for_spawns=default_ai_difficulty_for_spawns, ai_min_population=ai_min_population, game_seed=game_seed, merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct, merge_ai_reject_chance_pct=merge_ai_reject_chance_pct, merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept, merge_ai_min_years_before_consider=merge_ai_min_years_before_consider, merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years, war_start_chance_per_year=war_start_chance_per_year, war_end_chance_per_year=war_end_chance_per_year, war_worker_draft_chance_per_year=war_worker_draft_chance_per_year, war_new_worker_cost_multiplier=war_new_worker_cost_multiplier, ai_debug_notifications_enabled=ai_debug_notifications_enabled)
    game.ruleset_name = used_ruleset
    game.starting_money = int(starting_money)
    game.idle_human_benchmark_enabled = bool(idle_human_benchmark_enabled)
    apply_ruleset_events_to_game(game, used_ruleset)
    return game

def create_single_ai_player(player_id: int, name: str, starting_money: int, ai_focus: str = None, ai_difficulty: str = None) -> Player:
    """Create a single AI player (e.g. for random spawn)."""
    from vehicle_presets import get_vehicle_registry
    from vehicle import Trainset, Railcar
    registry = get_vehicle_registry()
    all_locomotives = registry.get_locomotives_by_year()
    earliest_loco = None
    for loco, year in all_locomotives:
        if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
            earliest_loco = loco
            break
    earliest_railcar = registry.get_earliest_railcar()
    if not earliest_loco or not earliest_railcar:
        raise ValueError("No suitable vehicles found in registry")
    starting_trainsets = []
    for _ in range(3):
        cars = [Railcar("Standard Passenger", 0, earliest_railcar.capacity, earliest_railcar.maitnance_costs)]
        starting_trainsets.append(Trainset(locomotive=earliest_loco, cars=cars))
    focus = ai_focus if ai_focus in AI_FOCUSES else random.choice(AI_FOCUSES)
    from game_presets import AI_DEFAULT_DIFFICULTY
    diff = ai_difficulty if ai_difficulty in AI_DIFFICULTIES else AI_DEFAULT_DIFFICULTY
    return Player(player_id=player_id, money=starting_money, trainsets=starting_trainsets, name=name, line_names=[], is_ai=True, ai_focus=focus, ai_difficulty=diff)


def initialize_marketplace_trainsets(marketplace: Marketplace, num_players: int = 2):
    """Initialize marketplace using the new category-based generation system.
    This ensures proper categorization and availability rules from the start."""
    current_year = 1830
    
    # Use the new category-based generation system
    marketplace.generate_trainsets_based_on_discoveries(current_year)
    
    from worker import ENGINEER, LAWYER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER
    marketplace.set_worker_supply(ENGINEER.name, num_players * 5)
    marketplace.set_worker_supply(LAWYER.name, num_players * 3)
    marketplace.set_worker_supply(CONSTRUCTION_WORKER.name, num_players * 2)
    marketplace.set_worker_supply(MAINTENANCE_WORKER.name, num_players * 10)


def get_game_params_for_preset(preset_name: str) -> dict:
    """Get game parameters (map dimensions, center, timeline) for a map preset.
    
    All game logic for map selection lives here - the game is playable without qt.py.
    Uses map_preset_loader for dynamic discovery so new maps work without code changes.
    
    Args:
        preset_name: Map preset identifier (e.g. 'standard', 'nyc', 'ny_metro')
        
    Returns:
        dict with: map_width, map_height, map_center_lat, map_center_lon,
                   start_year, end_year, use_osm
    """
    from map_preset_loader import (
        load_map_preset, get_map_bounds, get_map_timeline,
        get_default_center_lat, get_default_center_lon, list_available_presets
    )
    
    if preset_name not in list_available_presets():
        # Fallback for "Standard" or unknown - use generic grid with OSM
        from map_presets_standard import STANDARD_BOUNDS, STANDARD_TIMELINE
        bounds = STANDARD_BOUNDS
        timeline = STANDARD_TIMELINE
        use_osm = True
    else:
        bounds = get_map_bounds(preset_name)
        timeline = get_map_timeline(preset_name)
        use_osm = True  # All maps use OSM tiles for display
    
    if bounds is None:
        from map_presets_standard import STANDARD_BOUNDS, STANDARD_TIMELINE
        bounds = STANDARD_BOUNDS
        timeline = STANDARD_TIMELINE
    
    # Bounds may have width/height or default_width/default_height
    map_width = bounds.get("width", bounds.get("default_width", 100))
    map_height = bounds.get("height", bounds.get("default_height", 75))
    map_center_lat = get_default_center_lat(preset_name) if preset_name in list_available_presets() else bounds.get("center_lat", 0.0)
    map_center_lon = get_default_center_lon(preset_name) if preset_name in list_available_presets() else bounds.get("center_lon", 0.0)
    start_year = timeline.get("start_year", 1830) if timeline else 1830
    end_year = timeline.get("end_year", 2050) if timeline else 2050
    
    return {
        "map_width": map_width,
        "map_height": map_height,
        "map_center_lat": map_center_lat,
        "map_center_lon": map_center_lon,
        "start_year": start_year,
        "end_year": end_year,
        "use_osm": use_osm,
        "map_preset_name": preset_name,
    }


def _merge_company_names(name_a: str, name_b: str) -> str:
    """Combine two company names into one (e.g. Pennsylvania Railroad + New York Central -> Penn Central)."""
    a = (name_a or '').strip()
    b = (name_b or '').strip()
    if not a:
        return b or 'Merged Company'
    if not b:
        return a
    words_a = [w for w in a.split() if w]
    words_b = [w for w in b.split() if w]
    if not words_a:
        return b
    if not words_b:
        return a
    # First part: abbreviate long first word (e.g. Pennsylvania -> Penn), else use first word
    first_word = words_a[0]
    if len(first_word) > 5:
        first_part = first_word[:4].rstrip('s')  # Penn, New , etc.
    else:
        first_part = first_word
    # Second part: last word of target name (e.g. Central from New York Central)
    second_part = words_b[-1] if words_b else first_part
    if first_part.lower() == second_part.lower():
        second_part = words_b[0] if words_b else second_part
    return f"{first_part} {second_part}".strip()

