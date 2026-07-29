"""Operations mixin: OpsGameCoreMixin."""
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
    STATION_MAX_WATER_DISTANCE_TO_LAND_M,
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



from route_validation import *


class OpsGameCoreMixin:
    def __init__(self, player_names: list[str], map_width: int = 100, map_height: int = 100, starting_money: Optional[int] = None, worker_replenishment_rate: float = 1.0, natural_disaster_frequency: float = 1.0, use_osm: bool = False, map_center_lat: float = None, map_center_lon: float = None, marketplace_years_visible: int = None, map_preset_name: str = None, line_build_speed_multiplier: float = None, marketplace_bulk_discount_percent: float = None, marketplace_bulk_discount_threshold: int = None, ai_player_indices: list[int] = None,
        ai_focus_per_player: dict = None, ai_difficulty_per_player: dict = None, random_ai_spawn_enabled: bool = True, merge_enabled: bool = True, merge_ai_ai_enabled: bool = True, merge_ai_human_enabled: bool = True, merge_human_human_enabled: bool = True, inflation_rate: float = 1.0, profit_margin: float = 1.0, random_ai_spawn_interval_years: int = 30, default_ai_difficulty_for_spawns: str = 'hard', ai_min_population: int = 100, game_seed: int = None, merge_ai_healthy_profit_threshold_pct: float = 25.0, merge_ai_reject_chance_pct: float = 90.0, merge_ai_min_turns_to_live_for_accept: int = 10, merge_ai_min_years_before_consider: int = None, merge_ai_auto_fail_first_n_years: int = None,         war_start_chance_per_year: float = None, war_end_chance_per_year: float = None, war_worker_draft_chance_per_year: float = None, war_new_worker_cost_multiplier: float = None, ruleset_name: str = None, idle_human_benchmark_enabled: bool = False, ai_debug_notifications_enabled: bool = True):
        """Create Operations (game logic). Game is playable without qt.py.
        
        map_preset_name: Optional preset id ('standard', 'nyc', 'ny_metro') - uses
        get_game_params_for_preset to derive map center and use_osm. Map dimensions
        come from params unless overridden by map_width/map_height (e.g. standard with custom size).
        line_build_speed_multiplier, marketplace_bulk_discount_percent, marketplace_bulk_discount_threshold:
        Optional overrides for game_presets (applied in setup_game).
        ai_player_indices: list of player indices (0-based) that are AI-controlled.
        ai_focus_per_player: optional dict mapping player_index -> focus for AI players.
        ai_difficulty_per_player: optional dict mapping player_index -> difficulty for AI players.
        game_seed: optional 32-bit seed for Game._ai_rng_seed (deterministic AI/unit tests). If None, seed is random each game."""
        if starting_money is None:
            starting_money = STARTING_MONEY
        if map_preset_name:
            params = get_game_params_for_preset(map_preset_name)
            map_center_lat = params["map_center_lat"]
            map_center_lon = params["map_center_lon"]
            use_osm = params["use_osm"]
            # Use preset dimensions only for non-standard (nyc, ny_metro have fixed size)
            if map_preset_name != "standard":
                map_width = params["map_width"]
                map_height = params["map_height"]
        # Preset-specific overrides for population-init maps (e.g. NYC, NY Metro)
        from map_preset_loader import preset_has_population_init
        if map_preset_name and preset_has_population_init(map_preset_name):
            if line_build_speed_multiplier is None:
                line_build_speed_multiplier = 4.0
            if not ai_difficulty_per_player and ai_player_indices:
                ai_difficulty_per_player = {i: 'hard' for i in ai_player_indices}
        from ruleset_loader import get_active_ruleset
        if ruleset_name is None:
            ruleset_name = get_active_ruleset()
        self.ruleset_name = ruleset_name
        self.game = setup_game(player_names, map_width, map_height, starting_money, worker_replenishment_rate, natural_disaster_frequency, use_osm=use_osm, map_center_lat=map_center_lat, map_center_lon=map_center_lon, marketplace_years_visible=marketplace_years_visible, map_preset_name=map_preset_name, line_build_speed_multiplier=line_build_speed_multiplier, marketplace_bulk_discount_percent=marketplace_bulk_discount_percent, marketplace_bulk_discount_threshold=marketplace_bulk_discount_threshold, ai_player_indices=ai_player_indices, ai_focus_per_player=ai_focus_per_player, ai_difficulty_per_player=ai_difficulty_per_player, random_ai_spawn_enabled=random_ai_spawn_enabled, merge_enabled=merge_enabled, merge_ai_ai_enabled=merge_ai_ai_enabled, merge_ai_human_enabled=merge_ai_human_enabled, merge_human_human_enabled=merge_human_human_enabled, inflation_rate=inflation_rate, profit_margin=profit_margin, random_ai_spawn_interval_years=random_ai_spawn_interval_years, default_ai_difficulty_for_spawns=default_ai_difficulty_for_spawns, ai_min_population=ai_min_population, game_seed=game_seed, merge_ai_healthy_profit_threshold_pct=merge_ai_healthy_profit_threshold_pct, merge_ai_reject_chance_pct=merge_ai_reject_chance_pct, merge_ai_min_turns_to_live_for_accept=merge_ai_min_turns_to_live_for_accept, merge_ai_min_years_before_consider=merge_ai_min_years_before_consider, merge_ai_auto_fail_first_n_years=merge_ai_auto_fail_first_n_years, war_start_chance_per_year=war_start_chance_per_year, war_end_chance_per_year=war_end_chance_per_year, war_worker_draft_chance_per_year=war_worker_draft_chance_per_year, war_new_worker_cost_multiplier=war_new_worker_cost_multiplier, ruleset_name=ruleset_name, idle_human_benchmark_enabled=idle_human_benchmark_enabled, ai_debug_notifications_enabled=ai_debug_notifications_enabled)
        # Warm road index, point-area map, AI road features, and population sort caches at init
        # (avoids multi-second cold loads on first year advance / first AI turn).
        _warm_preset = map_preset_name or getattr(self.game.get_map(), "map_preset_name", None)
        if _warm_preset:
            from map_preset_loader import warm_game_start_year_end_caches
            warm_game_start_year_end_caches(_warm_preset, self.game.get_map())
        self.current_player_index = 0
        self.completed_jobs = []
        self._cached_ridership_data = None
        self._cached_ridership_year = None
        self.public_ta = None
        # Session-level deleted points: survives game state replacement (e.g. networked mode)
        self._session_deleted_population_points = set()
        # Government funding (from 1970): one player receives funding each year. Set by lobbying or random.
        self._government_funding_recipient = None
        self._government_funding_amount = 50000
        # AI money log for AI Watch tab (all years)
        self._ai_money_debug_log = []
        # Generated yearly news articles for the News Log tab
        self._news_log = []
    
    def get_game(self) -> Game:
        return self.game

    def get_runtime_game_settings(self) -> dict:
        """Snapshot of game settings that can be changed during play."""
        import game_presets
        g = self.game
        mp = g.marketplace
        return {
            'ai_debug_notifications_enabled': bool(getattr(g, 'ai_debug_notifications_enabled', True)),
            'random_ai_spawn_enabled': bool(getattr(g, 'random_ai_spawn_enabled', False)),
            'random_ai_spawn_interval_years': int(getattr(g, 'random_ai_spawn_interval_years', 30)),
            'ai_min_population': int(getattr(g, 'ai_min_population', 100)),
            'merge_enabled': bool(getattr(g, 'merge_enabled', False)),
            'merge_ai_ai_enabled': bool(getattr(g, 'merge_ai_ai_enabled', True)),
            'merge_ai_human_enabled': bool(getattr(g, 'merge_ai_human_enabled', True)),
            'merge_human_human_enabled': bool(getattr(g, 'merge_human_human_enabled', True)),
            'merge_ai_healthy_profit_threshold_pct': float(getattr(g, 'merge_ai_healthy_profit_threshold_pct', 25.0)),
            'merge_ai_reject_chance_pct': float(getattr(g, 'merge_ai_reject_chance_pct', 90.0)),
            'merge_ai_min_turns_to_live_for_accept': int(getattr(g, 'merge_ai_min_turns_to_live_for_accept', 10)),
            'merge_ai_min_years_before_consider': int(getattr(g, 'merge_ai_min_years_before_consider', 0)),
            'merge_ai_auto_fail_first_n_years': int(getattr(g, 'merge_ai_auto_fail_first_n_years', 0)),
            'worker_replenishment_rate': float(getattr(g, 'worker_replenishment_rate', 1.0)),
            'natural_disaster_frequency': float(getattr(g.event_system, 'natural_disaster_frequency', 1.0)),
            'inflation_rate': float(getattr(g, 'inflation_rate', 1.0)),
            'profit_margin': float(getattr(g, 'profit_margin', 1.0)),
            'line_build_speed_multiplier': float(game_presets.LINE_BUILD_SPEED_MULTIPLIER),
            'marketplace_years_visible': int(getattr(mp, 'marketplace_years_visible', game_presets.MARKETPLACE_YEARS_VISIBLE)),
            'marketplace_bulk_discount_percent': float(game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT) * 100.0,
            'marketplace_bulk_discount_threshold': int(game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD),
            'war_start_chance_per_year': float(getattr(g, 'war_start_chance_per_year', 0.0)),
            'war_end_chance_per_year': float(getattr(g, 'war_end_chance_per_year', 0.0)),
            'war_worker_draft_chance_per_year': float(getattr(g, 'war_worker_draft_chance_per_year', 0.0)),
            'war_new_worker_cost_multiplier': float(getattr(g, 'war_new_worker_cost_multiplier', 1.0)),
        }

    def apply_runtime_game_settings(self, settings: dict) -> None:
        """Apply in-game settings changes to the live game."""
        import random
        import game_presets
        from inflation_utils import set_inflation_rate_multiplier

        g = self.game
        if 'ai_debug_notifications_enabled' in settings:
            g.ai_debug_notifications_enabled = bool(settings['ai_debug_notifications_enabled'])
        if 'ai_min_population' in settings:
            g.ai_min_population = max(0, int(settings['ai_min_population']))
        if 'random_ai_spawn_interval_years' in settings:
            g.random_ai_spawn_interval_years = max(5, min(200, int(settings['random_ai_spawn_interval_years'])))
        if 'random_ai_spawn_enabled' in settings:
            g.random_ai_spawn_enabled = bool(settings['random_ai_spawn_enabled'])
            if not g.random_ai_spawn_enabled:
                g._next_random_ai_spawn_year = None
            elif getattr(g, '_next_random_ai_spawn_year', None) is None:
                interval = int(getattr(g, 'random_ai_spawn_interval_years', 30))
                half = max(5, interval // 2)
                g._next_random_ai_spawn_year = int(self.get_year()) + random.randint(interval - half, interval + half)
        if 'merge_enabled' in settings:
            g.merge_enabled = bool(settings['merge_enabled'])
        if 'merge_ai_ai_enabled' in settings:
            g.merge_ai_ai_enabled = bool(settings['merge_ai_ai_enabled'])
        if 'merge_ai_human_enabled' in settings:
            g.merge_ai_human_enabled = bool(settings['merge_ai_human_enabled'])
        if 'merge_human_human_enabled' in settings:
            g.merge_human_human_enabled = bool(settings['merge_human_human_enabled'])
        if 'merge_ai_healthy_profit_threshold_pct' in settings:
            g.merge_ai_healthy_profit_threshold_pct = max(0.0, min(100.0, float(settings['merge_ai_healthy_profit_threshold_pct'])))
        if 'merge_ai_reject_chance_pct' in settings:
            g.merge_ai_reject_chance_pct = max(0.0, min(100.0, float(settings['merge_ai_reject_chance_pct'])))
        if 'merge_ai_min_turns_to_live_for_accept' in settings:
            g.merge_ai_min_turns_to_live_for_accept = max(0, min(200, int(settings['merge_ai_min_turns_to_live_for_accept'])))
        if 'merge_ai_min_years_before_consider' in settings:
            g.merge_ai_min_years_before_consider = max(0, min(50, int(settings['merge_ai_min_years_before_consider'])))
        if 'merge_ai_auto_fail_first_n_years' in settings:
            g.merge_ai_auto_fail_first_n_years = max(0, min(50, int(settings['merge_ai_auto_fail_first_n_years'])))
        if 'worker_replenishment_rate' in settings:
            g.worker_replenishment_rate = max(0.1, min(5.0, float(settings['worker_replenishment_rate'])))
        if 'natural_disaster_frequency' in settings:
            freq = max(0.1, min(5.0, float(settings['natural_disaster_frequency'])))
            g.event_system.natural_disaster_frequency = freq
        if 'inflation_rate' in settings:
            g.inflation_rate = max(0.1, min(5.0, float(settings['inflation_rate'])))
            set_inflation_rate_multiplier(g.inflation_rate)
        if 'profit_margin' in settings:
            g.profit_margin = max(0.25, min(5.0, float(settings['profit_margin'])))
        if 'line_build_speed_multiplier' in settings:
            game_presets.LINE_BUILD_SPEED_MULTIPLIER = max(0.5, min(5.0, float(settings['line_build_speed_multiplier'])))
        if 'marketplace_years_visible' in settings:
            g.marketplace.marketplace_years_visible = max(1, min(200, int(settings['marketplace_years_visible'])))
        if 'marketplace_bulk_discount_percent' in settings:
            game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT = max(0.0, min(100.0, float(settings['marketplace_bulk_discount_percent']))) / 100.0
        if 'marketplace_bulk_discount_threshold' in settings:
            game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD = max(1, int(settings['marketplace_bulk_discount_threshold']))
        if 'war_start_chance_per_year' in settings:
            g.war_start_chance_per_year = max(0.0, min(1.0, float(settings['war_start_chance_per_year'])))
        if 'war_end_chance_per_year' in settings:
            g.war_end_chance_per_year = max(0.0, min(1.0, float(settings['war_end_chance_per_year'])))
        if 'war_worker_draft_chance_per_year' in settings:
            g.war_worker_draft_chance_per_year = max(0.0, min(1.0, float(settings['war_worker_draft_chance_per_year'])))
        if 'war_new_worker_cost_multiplier' in settings:
            g.war_new_worker_cost_multiplier = max(1.0, min(3.0, float(settings['war_new_worker_cost_multiplier'])))
    
    def get_players(self) -> list[Player]:
        players = self.game.get_players()
        return [p for p in players if not (hasattr(p, 'is_public_ta') and p.is_public_ta)]
    
    def get_current_player(self, current_player_index: int) -> Player:
        """Get the current active player based on index, or first active player if index is invalid"""
        active_players = self.get_active_players()
        if not active_players:
            return None
        if current_player_index < len(self.get_players()):
            player = self.get_players()[current_player_index]
            if not self.is_player_eliminated(player):
                return player
        return active_players[0]

    def get_effective_player(self, player: Player) -> Player:
        """Return the player who actually controls this company. If player merged into another human, return that acquirer."""
        if player is None:
            return None
        mid = getattr(player, 'merged_into_player_id', None)
        if mid is None:
            return player
        for p in self.get_players():
            if getattr(p, 'player_id', None) == mid:
                return p
        return player
    
    def set_current_player_index(self, index: int):
        """Set the current player index"""
        self.current_player_index = index
    
    def get_current_player_index(self) -> int:
        """Get the current player index"""
        return self.current_player_index
    
    def get_active_players(self) -> list[Player]:
        active = self.game.get_active_players()
        return [p for p in active if not (hasattr(p, 'is_public_ta') and p.is_public_ta)]
    
    def eliminate_player(self, player: Player):
        """Eliminate a player from the game"""
        self.game.eliminate_player(player)
    
    def get_map(self) -> Map:
        return self.game.get_map()
    
    def get_event_system(self):
        """Get the event system"""
        return self.game.get_event_system()
    
    def calc_player_profit(self, player: Player, current_year: int = None) -> int:
        """Calculate total profit for a player using cached profit values.
        Uses cached profit per line calculated during year advance to avoid expensive calculations.
        current_year: if provided, use this year to match cache; otherwise use game.get_year()."""
        if current_year is None:
            current_year = self.game.get_year()
        
        total_profit = 0
        for line_name in player.get_line_names():
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line:
                total_profit += self.calc_line_profit(line, current_year)
        
        return total_profit
    
    def calc_player_maintenance(self, player: Player) -> int:
        """Calculate total maintenance costs for a player"""
        return self.game.calc_maitnance_by_player(player)
    
    def add_railcar_to_trainset(self, trainset: Trainset, railcar: Railcar) -> bool:
        """Add a railcar to a trainset. Returns True if successful, False if at max capacity or locomotive can't have railcars"""
        loco = getattr(trainset, 'locomotive', None)
        if loco and getattr(loco, 'associated_railcar_name', None):
            if railcar.name != loco.associated_railcar_name:
                raise ValueError(f"{loco.name} can only use '{loco.associated_railcar_name}' railcars, but '{railcar.name}' was provided")
        return trainset.add_railcar(railcar)

    def get_add_railcar_info(self, player: Player) -> dict:
        """Trainsets that can accept another railcar, and spare railcars in inventory."""
        eligible_trainsets = []
        for ts in self.get_player_trainsets(player):
            loco = self.get_trainset_locomotive(ts)
            if not loco or not getattr(loco, 'can_have_railcars', True):
                continue
            max_cars = loco.max_cars if loco.max_cars is not None else Trainset.DEFAULT_MAX_RAILCARS
            if len(self.get_trainset_cars(ts)) < max_cars:
                eligible_trainsets.append(ts)
        railcars = list(getattr(player, 'railcars', []) or [])
        return {'trainsets': eligible_trainsets, 'railcars': railcars}

    def _compatible_railcars_for_trainset_locomotive(self, locomotive, railcars: list) -> list:
        if not locomotive or not getattr(locomotive, 'can_have_railcars', True):
            return []
        assoc = getattr(locomotive, 'associated_railcar_name', None)
        if assoc:
            return [c for c in railcars if getattr(c, 'name', None) == assoc]
        return list(railcars)

    def _compatible_locomotives_for_trainset(self, trainset: Trainset, locomotives: list) -> list:
        cars = self.get_trainset_cars(trainset)
        compatible = []
        for loco in locomotives:
            if not getattr(loco, 'can_have_railcars', True):
                if cars:
                    continue
            else:
                max_cars = loco.max_cars if loco.max_cars is not None else Trainset.DEFAULT_MAX_RAILCARS
                if len(cars) > max_cars:
                    continue
                assoc = getattr(loco, 'associated_railcar_name', None)
                if assoc and any(getattr(c, 'name', None) != assoc for c in cars):
                    continue
            compatible.append(loco)
        return compatible

    def get_modify_trainset_info(self, player: Player, trainset: Trainset) -> dict:
        """Inventory and options for modifying a trainset in place."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        loco = self.get_trainset_locomotive(trainset)
        cars = list(self.get_trainset_cars(trainset))
        spare_locomotives = list(getattr(player, 'locomotives', []) or [])
        spare_railcars = list(getattr(player, 'railcars', []) or [])
        max_cars = Trainset.DEFAULT_MAX_RAILCARS
        can_add_cars = False
        if loco and getattr(loco, 'can_have_railcars', True):
            max_cars = loco.max_cars if loco.max_cars is not None else Trainset.DEFAULT_MAX_RAILCARS
            can_add_cars = len(cars) < max_cars
        return {
            'trainset': trainset,
            'locomotive': loco,
            'cars': cars,
            'max_cars': max_cars,
            'can_add_cars': can_add_cars,
            'can_remove_cars': bool(cars),
            'compatible_locomotives': self._compatible_locomotives_for_trainset(trainset, spare_locomotives),
            'compatible_railcars': self._compatible_railcars_for_trainset_locomotive(loco, spare_railcars) if can_add_cars else [],
        }

    def add_railcar_to_trainset_for_player(self, player: Player, trainset: Trainset, railcar: Railcar) -> dict:
        """Move a spare railcar from player inventory onto a trainset."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        railcars = getattr(player, 'railcars', None) or []
        if railcar not in railcars:
            raise ValueError("Railcar not in player inventory")
        if not self.add_railcar_to_trainset(trainset, railcar):
            raise ValueError("Cannot add railcar (trainset at capacity or locomotive cannot pull cars)")
        railcars.remove(railcar)
        return {
            'capacity': trainset.get_capacity(),
            'cars_count': len(self.get_trainset_cars(trainset)),
        }

    def remove_railcar_from_trainset_for_player(
        self, player: Player, trainset: Trainset, railcar: Railcar = None, index: int = -1,
    ) -> dict:
        """Remove a railcar from a trainset back into player inventory."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        removed = trainset.remove_railcar(railcar=railcar, index=index)
        if removed is None:
            raise ValueError("No railcar to remove")
        self.acquire_railcar_for_player(player, removed)
        return {
            'railcar_name': getattr(removed, 'name', 'Railcar'),
            'capacity': trainset.get_capacity(),
            'cars_count': len(self.get_trainset_cars(trainset)),
        }

    def replace_trainset_locomotive_for_player(
        self, player: Player, trainset: Trainset, new_locomotive: Locomotive,
    ) -> dict:
        """Swap the trainset locomotive with one from player inventory."""
        if trainset not in self.get_player_trainsets(player):
            raise ValueError("Trainset not owned by player")
        locomotives = getattr(player, 'locomotives', None) or []
        if new_locomotive not in locomotives:
            raise ValueError("Locomotive not in player inventory")
        if new_locomotive is trainset.locomotive:
            raise ValueError("Trainset already uses this locomotive")
        compatible = self._compatible_locomotives_for_trainset(trainset, [new_locomotive])
        if not compatible:
            raise ValueError("Locomotive is not compatible with this trainset's railcars")
        line_name = trainset.get_line_assignment()
        if line_name:
            target_line = self.get_map_lines().get(line_name)
            if target_line:
                if getattr(new_locomotive, 'needs_overhead_wire', False) and not getattr(target_line, 'has_overhead_wire', False):
                    raise ValueError("This locomotive requires overhead wire which the assigned line does not have.")
                if getattr(new_locomotive, 'needs_third_rail', False) and not getattr(target_line, 'has_third_rail', False):
                    raise ValueError("This locomotive requires a third rail which the assigned line does not have.")
        old_locomotive = trainset.locomotive
        locomotives.remove(new_locomotive)
        trainset.locomotive = new_locomotive
        trainset.capacity = trainset.calc_capacity()
        trainset.maitnance_costs = trainset.calc_maitnance_costs()
        self.acquire_locomotive_for_player(player, old_locomotive)
        return {
            'old_locomotive_name': getattr(old_locomotive, 'name', 'Locomotive'),
            'new_locomotive_name': getattr(new_locomotive, 'name', 'Locomotive'),
            'capacity': trainset.get_capacity(),
        }
    
    def assemble_trainset_complete(self, player: Player, locomotive: Locomotive, railcars: list[Railcar]) -> dict:
        """Complete trainset assembly operation. Returns dict with result info.
        Raises ValueError if locomotive cannot have railcars attached or if wrong railcars are used."""
        if railcars and not locomotive.can_have_railcars:
            raise ValueError(f"{locomotive.name} cannot have railcars attached (it is a self-contained unit like an EMU, DMU, streetcar, or subway car)")
        
        if locomotive.associated_railcar_name:
            for railcar in railcars:
                if railcar.name != locomotive.associated_railcar_name:
                    raise ValueError(f"{locomotive.name} can only use '{locomotive.associated_railcar_name}' railcars, but '{railcar.name}' was provided")
        
        trainset = self.create_trainset(locomotive, railcars)
        self.acquire_trainset_for_player(player, trainset)
        
        if locomotive in getattr(player, 'locomotives', []):
            player.locomotives.remove(locomotive)
        
        for railcar in railcars:
            if railcar in getattr(player, 'railcars', []):
                player.railcars.remove(railcar)
        
        return {
            'locomotive_name': locomotive.name,
            'railcars_count': len(railcars),
            'capacity': trainset.get_capacity()
        }
    
    def get_timeline(self) -> Timeline:
        return self.game.get_timeline()
    
    def get_year(self) -> int:
        return self.game.get_year()

    def ai_in_year_turn_completed(self, player) -> bool:
        """True if this AI already ran its build/actions for the current calendar year."""
        return getattr(player, '_ai_in_year_turn_year', None) == self.get_year()

    def mark_ai_in_year_turn_completed(self, player) -> None:
        player._ai_in_year_turn_year = self.get_year()

    def get_ai_players_needing_in_year_turn(self) -> list:
        """Living AIs that have not yet acted during the current calendar year."""
        year = self.get_year()
        return [
            p for p in self.get_players()
            if getattr(p, 'is_ai', False)
            and not self.is_player_eliminated(p)
            and getattr(p, '_ai_in_year_turn_year', None) != year
        ]

    def human_ready_for_year_end(self, player) -> bool:
        """True if this human player clicked End Year for the current calendar year."""
        return getattr(player, '_ready_for_end_year_year', None) == self.get_year()

    def mark_human_ready_for_year_end(self, player) -> None:
        player._ready_for_end_year_year = self.get_year()

    def player_year_actions_completed(self, player) -> bool:
        """True when this player has finished their portion of the current year (build/actions or End Year)."""
        if self.is_player_eliminated(player):
            return True
        if getattr(player, 'is_ai', False):
            return self.ai_in_year_turn_completed(player)
        return self.human_ready_for_year_end(player)

    def all_players_completed_year_actions(self) -> bool:
        active = self.get_active_players()
        if not active:
            return True
        return all(self.player_year_actions_completed(p) for p in active)

    def get_players_needing_year_actions(self) -> list:
        return [p for p in self.get_active_players() if not self.player_year_actions_completed(p)]
    
    def increase_year(self):
        self.game.increase_year()

    def decrease_year(self):
        self.game.decrease_year()

    def reset_year(self):
        self.game.reset_year()

    def start_game(self):
        self.game.start_game()

    def end_game(self):
        self.game.end_game()

    def create_line(self, name: str, points: list,  player: Player = None) -> Line:
        base = (name or "").strip()
        if not base:
            raise ValueError("Line name cannot be empty.")
        name = base
        suffix = 0
        while self.is_line_name_in_use(name):
            suffix += 1
            name = f"{base}_{suffix}"
        if not player:
            line = self.game.create_line(name, player, points)
            self.game.get_marketplace().add_line(line)
        else:
            total_length = 0.0
            if len(points) > 1:
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i+1]
                    total_length += math.hypot(x2 - x1, y2 - y1)

            COST_PER_UNIT_LENGTH = 150
            total_cost = int(COST_PER_UNIT_LENGTH * total_length)
            if total_cost < 15:
                total_cost = 15

            if player.get_money() < total_cost:
                raise ValueError(f"Player {player.get_name()} cannot afford line creation (needs ${total_cost}).")

            self.deduct_player_money(player, total_cost, reason='Line creation')
            line = self.game.create_line(name, player, points)
            player.acquire_line(line)
            self.game.get_map().add_line(line)           
        return line
    
    def buy_line_by_purchase(self, sale_item, player: Player) -> Line:
        current_year = self.get_year()
        base_price = sale_item.get_price(current_year=current_year)
        effective_price = self.get_effective_marketplace_price_for_player(player, base_price)
        if player.get_money() < effective_price:
            return None
        line = sale_item.get_item()
        old_line_name = line.name
        base = old_line_name
        new_name = base
        k = 0
        while self.is_line_name_in_use(new_name, exclude_line=line):
            k += 1
            new_name = f"{base}_{k}"
        if new_name != base:
            line.rename(new_name)
        old_owner = line.get_owner()
        if old_owner and old_owner != player:
            if old_line_name in old_owner.get_line_names():
                old_owner.get_line_names().remove(old_line_name)
        self.game.get_marketplace().remove_line(line)
        self.deduct_player_money(player, effective_price, reason='Marketplace line purchase')
        line.owner = player
        if line.name not in player.get_line_names():
            player.acquire_line(line)
        # Lines listed on the marketplace are removed from the map; always restore after purchase.
        self.add_line_to_map(line)
        self.invalidate_ridership_cache()
        return line
    
    def create_trainset(self, locomotive: Locomotive, cars: list[Railcar], line_assignment: str=None) -> Trainset:
        """Create a trainset from a locomotive and railcars.
        Raises ValueError if locomotive cannot have railcars attached."""
        if cars and not locomotive.can_have_railcars:
            raise ValueError(f"{locomotive.name} cannot have railcars attached (it is a self-contained unit like an EMU, DMU, streetcar, or subway car)")
        trainset = Trainset(locomotive=locomotive, cars=cars, line_assignment=line_assignment)
        return trainset
    
    def assign_trainset_to_line(self, trainset: Trainset, line_name: str):
        target_line = self.game.get_map().get_lines_by_name().get(line_name)
        if not target_line:
            raise ValueError(f"Line '{line_name}' not found")
        # Only rail lines accept trainsets; bus and ferry lines require bus/ferry vehicles
        if isinstance(target_line, (BusLine, Ferry)):
            raise ValueError("Cannot assign a trainset to a bus or ferry line. Assign buses to bus lines and ferries to ferry lines.")

        loco = trainset.locomotive
        if hasattr(loco, 'needs_overhead_wire') and loco.needs_overhead_wire:
            if not getattr(target_line, 'has_overhead_wire', False):
                raise ValueError("This trainset requires overhead wire which the line does not have.")
        if hasattr(loco, 'needs_third_rail') and loco.needs_third_rail:
            if not getattr(target_line, 'has_third_rail', False):
                raise ValueError("This trainset requires a third rail which the line does not have.")

        prev = trainset.get_line_assignment()
        if prev:
            prev_line = self.game.get_map().get_lines_by_name().get(prev)
            if prev_line and trainset in prev_line.vehicles:
                prev_line.vehicles.remove(trainset)

        trainset.assign_to_line(line_name)
        if trainset not in target_line.vehicles:
            target_line.vehicles.append(trainset)

    def unassign_trainset_complete(self, trainset: Trainset) -> dict:
        """Unassign a trainset from its current line and remove it from line vehicles."""
        line_name = trainset.get_line_assignment()
        if not line_name:
            raise ValueError("Trainset is not assigned to a line")
        line = self.game.get_map().get_lines_by_name().get(line_name)
        if line and trainset in line.vehicles:
            line.vehicles.remove(trainset)
        trainset.assign_to_line(None)
        return {'line_name': line_name}

    def get_player_by_id(self, player_id: int) -> Player:
        for p in self.game.get_players():
            if p.get_player_id() == player_id:
                return p
        raise KeyError(f"No player with id {player_id}")
    
    def get_players_by_name(self) -> dict:
        return self.game.get_players_by_name()
    
    def get_unassigned_trainsets(self, player: Player) -> list[Trainset]:
        return player.get_unassigned_trainsets()
    
    def get_trainsets_by_line(self, player: Player, line_name: str) -> list[Trainset]:
        return player.get_trainsets_by_line(line_name)
    
    def get_trainset_locomotive_name(self, trainset: Trainset) -> str:
        if trainset and hasattr(trainset, 'locomotive') and trainset.locomotive:
            return getattr(trainset.locomotive, 'name', 'Unknown')
        return 'Unknown'
    
    def get_trainset_cars(self, trainset: Trainset) -> list:
        if trainset and hasattr(trainset, 'cars'):
            return trainset.cars
        return []
    
    def get_trainset_capacity(self, trainset: Trainset) -> int:
        if trainset and hasattr(trainset, 'capacity'):
            return trainset.capacity
        return 0
    
    def get_trainset_line_assignment(self, trainset: Trainset) -> str:
        if trainset and hasattr(trainset, 'line_assignment'):
            return trainset.line_assignment or ''
        return ''
    
    def deduct_player_money(self, player: Player, amount: int, reason: str = None):
        """Deduct money from player. reason is used for AI money log (all years)."""
        if amount <= 0:
            return
        player.deduct_money(amount)
        if getattr(player, 'is_ai', False):
            self._log_ai_money(player, 'down', amount, reason or 'Spending')

    def add_player_money(self, player: Player, amount: int, reason: str = None):
        """Add money to player. reason is used for AI money log (all years)."""
        if amount <= 0:
            return
        player.add_money(amount)
        if getattr(player, 'is_ai', False):
            self._log_ai_money(player, 'up', amount, reason or 'Income')

    def _log_ai_money(self, player: Player, direction: str, amount: int, reason: str):
        """Append one AI money change to the log (all years, for AI Watch tab)."""
        if not hasattr(self, '_ai_money_debug_log'):
            self._ai_money_debug_log = []
        year = self.get_year()
        name = self.get_player_name(player)
        balance = self.get_player_money(player)  # balance after this change (we log after the change)
        self._ai_money_debug_log.append({
            'year': year,
            'player_name': name,
            'direction': direction,
            'amount': amount,
            'reason': reason,
            'balance_after': balance,
        })

    def get_ai_money_debug_summary(self):
        """Return summary of AI money changes (all years) for AI Watch tab.
        Returns dict with 'ups' and 'downs' lists of {year, player_name, amount, reason, balance_after}."""
        log = getattr(self, '_ai_money_debug_log', []) or []
        ups = [e for e in log if e['direction'] == 'up']
        downs = [e for e in log if e['direction'] == 'down']
        return {'ups': ups, 'downs': downs}

    def get_news_log(self, limit: int = None) -> list:
        """Return generated news articles (newest first)."""
        log = getattr(self, '_news_log', []) or []
        if limit is None or limit <= 0:
            return list(reversed(log))
        return list(reversed(log[-limit:]))

    def _news_article_pools(self) -> dict:
        """Economy/politics/oddities pools from the active game ruleset (or built-in defaults)."""
        from ruleset_loader import load_news_pools_for_ruleset

        ruleset = getattr(self, 'ruleset_name', None) or getattr(self.game, 'ruleset_name', None)
        return load_news_pools_for_ruleset(ruleset)

    def _generate_news_articles_for_year(self, new_year: int, payload: dict) -> list:
        """Generate mostly event-related yearly news copy and append to persistent log."""
        articles = []

        def _add(headline: str, body: str, category: str = "General"):
            if not headline or not body:
                return
            article = {
                'year': int(new_year),
                'category': category,
                'headline': str(headline),
                'body': str(body),
            }
            articles.append(article)
            self._news_log.append(article)

        natural = payload.get('natural_disaster_info')
        if natural:
            dname = natural.get('disaster_type', 'Disaster')
            sev = natural.get('severity', 0)
            _add(
                f"{dname} Disrupts Regional Network (Severity {sev})",
                "Authorities reported major disruption across affected corridors and temporary timetable cuts.",
                category="Disaster",
            )
        for ev in (payload.get('newly_triggered_events') or []):
            _add(
                f"Historical Shift: {getattr(ev, 'name', 'Market Event')}",
                getattr(ev, 'description', "Travel patterns and operating priorities are expected to adjust this year."),
                category="History",
            )
        for name, year in (payload.get('newly_available_vehicles') or []):
            _add(
                f"Technology Desk: {name} Debuts",
                f"New rolling stock class entered the market in {year}, with operators citing efficiency gains.",
                category="Technology",
            )
        spawned = payload.get('new_ai_spawned')
        if spawned:
            cname = spawned.get('name', 'New Transit Co')
            _add(
                f"{cname} Launches Service",
                f"The entrant opened with ${int(spawned.get('money', 0)):,} in capital and immediate expansion signals.",
                category="Market",
            )
        for acq, tgt in (payload.get('ai_ai_merges') or []):
            _add(
                f"Corporate Move: {self.get_player_name(acq)} Acquires {self.get_player_name(tgt)}",
                "Industry analysts expect route consolidation and lower duplicate service along overlapping corridors.",
                category="Corporate",
            )

        war = payload.get('war_events') or {}
        if war.get('war_started'):
            _add(
                "War Declared: Transit Labor and Steel Face Mobilization Pressure",
                "Operators braced for project delays as agencies competed for crews amid national call-ups.",
                category="World",
            )
        if war.get('war_ended'):
            _add(
                "Armistice: Agencies Expect Workers Back—At a Higher Price",
                "Analysts predicted a tight labor market and rising salaries for new hires after the conflict.",
                category="World",
            )
        for d in war.get('drafts', []) or []:
            pname = d.get('player_name', 'Operator')
            n = int(d.get('count', 0) or 0)
            if n > 0:
                _add(
                    f"Draft Orders: {pname} Loses {n} Transit Worker(s)",
                    "Unfavored operators reportedly bore heavier manpower losses as seats turned on the capital.",
                    category="World",
                )

        for w in payload.get('strike_warnings') or []:
            msg = str(w.get('message') or '').strip()
            if msg:
                first_line = msg.split('\n', 1)[0].strip()
                _add(
                    f"Labor Desk: {first_line}" if first_line else "Labor Desk: Strike Vote Looms",
                    msg.replace('\n', ' '),
                    category="Labor",
                )
        for sm in payload.get('strike_messages') or []:
            pname = sm.get('player_name', 'Operator')
            wname = sm.get('worker_name', 'Worker')
            qty = int(sm.get('quantity', 0) or 0)
            turns = int(sm.get('turns_remaining', 0) or 0)
            demand = float(sm.get('demanded_multiplier', 0) or 0)
            _add(
                f"Strike Halts Service at {pname}",
                (
                    f"{qty} {wname}(s) walked off the job for {turns} more turn(s), "
                    f"demanding {demand:.2f}x base pay."
                ),
                category="Labor",
                )

        self._add_company_ranking_and_venture_news(new_year, payload, _add)
        self._add_government_and_politics_news(new_year, payload, _add)

        pools = self._news_article_pools()
        economy_pool = pools.get("economy") or ()
        politics_pool = pools.get("politics") or ()
        oddities_pool = pools.get("oddities") or ()
        year = int(new_year)
        oddity_eligible = [
            (h, b) for (h, b, min_year) in oddities_pool if year >= int(min_year)
        ]

        # Filler columns from ruleset-owned pools so quiet years still get varied copy.
        filler_targets = max(0, 3 - len(articles))
        for _ in range(filler_targets):
            roll = random.random()
            if roll < 0.4 and politics_pool:
                h, b = random.choice(politics_pool)
                _add(h, b, category="Politics")
            elif roll < 0.7 and oddity_eligible:
                h, b = random.choice(oddity_eligible)
                _add(h, b, category="Oddities")
            elif economy_pool:
                h, b = random.choice(economy_pool)
                _add(h, b, category="Economy")

        if random.random() < 0.5 and politics_pool:
            h, b = random.choice(politics_pool)
            _add(h, b, category="Politics")

        if random.random() < 0.4 and oddity_eligible:
            h, b = random.choice(oddity_eligible)
            _add(h, b, category="Oddities")

        from game_presets import NEWS_LOG_MAX_ENTRIES
        max_entries = int(NEWS_LOG_MAX_ENTRIES)
        if max_entries > 0 and len(self._news_log) > max_entries:
            self._news_log = self._news_log[-max_entries:]

        return articles

    def _add_government_and_politics_news(self, new_year: int, payload: dict, _add) -> None:
        """Elections, lobbying, legislature, agency suits, and federal subsidies."""
        if payload.get('election_held'):
            leader_name = None
            leader_inf = 0.0
            gov = self.game.get_government() if self.game else None
            if gov:
                ranking = gov.get_favor_ranking()
                if ranking:
                    top_pid, leader_inf = ranking[0]
                    for player in self.get_players():
                        if self.get_player_id(player) == top_pid:
                            leader_name = self.get_player_name(player)
                            break
            body = (
                "Fifty transit-board seats and the tiebreaker post were filled after the six-year cycle. "
                "Hidden lobbying results from the prior term were revealed before ballots were counted."
            )
            if leader_name:
                body += (
                    f" {leader_name} heads the largest favorable bloc "
                    f"({leader_inf:.1f} weighted influence)."
                )
            _add(
                f"Election {new_year}: Transit Board Seated",
                body,
                category="Politics",
            )

        for notif in payload.get('lobby_seat_notifications', []) or []:
            lobbyist = notif.get('lobbyist_name', 'An operator')
            prev_id = notif.get('previous_owner_id')
            prev_name = 'an opponent'
            if prev_id is not None:
                for player in self.get_players():
                    if self.get_player_id(player) == prev_id:
                        prev_name = self.get_player_name(player)
                        break
            if notif.get('type') == 'tiebreaker':
                _add(
                    f"Lobbying: {lobbyist} Secures Tiebreaker Seat",
                    f"The tiebreaker post flipped from {prev_name}'s column after successful lobbying.",
                    category="Politics",
                )
            else:
                seat_idx = int(notif.get('seat_index', 0)) + 1
                _add(
                    f"Lobbying: {lobbyist} Flips Board Seat {seat_idx}",
                    f"Seat {seat_idx} moved out of {prev_name}'s column following a paid influence campaign.",
                    category="Politics",
                )

        players_by_id = {self.get_player_id(p): p for p in self.get_players()}

        gov_laws = payload.get('government_laws') or {}
        if gov_laws:
            favored_names = []
            unfavored_names = []
            grants = gov_laws.get('grants') or {}
            tax_mods = gov_laws.get('tax_modifiers') or {}
            for pid, grant in grants.items():
                if grant and pid in players_by_id:
                    favored_names.append(self.get_player_name(players_by_id[pid]))
            for pid, mod in tax_mods.items():
                if mod and mod > 1.0 and pid in players_by_id:
                    name = self.get_player_name(players_by_id[pid])
                    if name not in unfavored_names:
                        unfavored_names.append(name)
            if favored_names:
                _add(
                    "Legislature: Annual Transit Relief Package",
                    "The government enacted favorable property-tax relief, transit subsidies, "
                    f"maintenance credits, and lawsuit protections for {', '.join(favored_names)}.",
                    category="Politics",
                )
            if unfavored_names:
                _add(
                    "Legislature: Regulatory Burden Increased",
                    "Higher property taxes, heavier maintenance assessments, and increased "
                    f"lawsuit scrutiny target {', '.join(unfavored_names)} under the annual slate.",
                    category="Politics",
                )

        law_labels = {
            'reduce_property_tax_15': '15% property tax reduction',
            'reduce_property_tax_5': '5% property tax reduction',
            'increase_property_tax_15': '15% property tax increase',
            'increase_property_tax_5': '5% property tax increase',
            'tax_break': 'one-year tax holiday',
            'audit_target': 'tax audit order',
        }
        for eff in payload.get('new_government_laws', []) or []:
            law_type = eff.get('type', 'law')
            label = law_labels.get(law_type, law_type.replace('_', ' '))
            names = [
                self.get_player_name(players_by_id[pid])
                for pid in (eff.get('player_ids') or [])
                if pid in players_by_id
            ]
            if not names and eff.get('player_id') in players_by_id:
                names = [self.get_player_name(players_by_id[eff['player_id']])]
            target_text = ", ".join(names) if names else "selected operators"
            _add(
                f"Legislature: {label.title()}",
                f"The government enacted {label} affecting {target_text}.",
                category="Politics",
            )

        lawsuit = payload.get('lawsuit_result')
        if lawsuit:
            agency = lawsuit.get('agency', 'Agency')
            player = lawsuit.get('player', 'Operator')
            excuse = lawsuit.get('excuse', 'complaint')
            if lawsuit.get('agency_won'):
                penalty = int(lawsuit.get('penalty', 0) or 0)
                _add(
                    f"{agency} Wins Suit Against {player}",
                    f"{agency} prevailed on {excuse}; {player} paid ${penalty:,} in damages.",
                    category="Politics",
                )
            else:
                _add(
                    f"{player} Beats {agency} in Court",
                    f"{player} defeated a {agency} action over {excuse}.",
                    category="Politics",
                )

        funding = payload.get('government_funding')
        if funding and int(funding.get('amount', 0) or 0) > 0:
            recipient = funding.get('recipient_name', 'An operator')
            amount = int(funding['amount'])
            _add(
                f"Federal Subsidy: {recipient} Awarded ${amount:,}",
                "Congressional transit funding flowed to the designated carrier based on "
                "route-kilometers and open stations.",
                category="Politics",
            )

    def _add_company_ranking_and_venture_news(self, new_year: int, payload: dict, _add) -> None:
        """Publish company rankings and venture briefs (not every year — random cadence)."""
        import random
        if random.random() > 0.4:
            return
        player_finances = payload.get('player_finances') or []
        if not player_finances:
            return

        standings = []
        for entry in player_finances:
            player = entry.get('player')
            finances = entry.get('finances') or {}
            if player is None or self.is_player_eliminated(player):
                continue
            net = int(finances.get('net', 0) or 0)
            profit = int(finances.get('profit', 0) or 0)
            money = int(self.get_player_money(player))
            standings.append({
                'name': self.get_player_name(player),
                'player': player,
                'net': net,
                'profit': profit,
                'money': money,
            })
        if not standings:
            return

        standings.sort(key=lambda row: (row['net'], row['money']), reverse=True)
        ranking_lines = []
        for rank, row in enumerate(standings[:5], start=1):
            ranking_lines.append(
                f"{rank}. {row['name']} — net ${row['net']:,}, cash ${row['money']:,}, line profit ${row['profit']:,}"
            )
        _add(
            f"Transit Rankings {new_year}: {standings[0]['name']} Leads the Market",
            "Annual standings by net income:\n" + "\n".join(ranking_lines),
            category="Market",
        )

        leader = standings[0]
        laggard = standings[-1]
        if leader['net'] >= 1500 and random.random() < 0.55:
            _add(
                f"Investor Watch: {leader['name']} Posts a Strong Year",
                f"Analysts credited disciplined operations after net income of ${leader['net']:,} "
                f"and line profit of ${leader['profit']:,}.",
                category="Corporate",
            )
        if len(standings) > 1 and laggard['net'] <= -800 and random.random() < 0.55:
            _add(
                f"Warning: {laggard['name']} Faces a Difficult Year",
                f"The company reported net income of ${laggard['net']:,} with ${laggard['money']:,} "
                "in cash reserves; observers urged route consolidation or service cuts.",
                category="Corporate",
            )

        if random.random() > 0.35:
            return
        current_year = int(new_year)
        map_lines = self.get_map_lines() or {}
        for row in standings:
            player = row['player']
            for line_name in player.get_line_names():
                line = map_lines.get(line_name)
                if not line:
                    continue
                line_profit = 0
                if hasattr(line, '_cached_profit') and getattr(line, '_cached_profit_year', None) == current_year:
                    line_profit = int(line._cached_profit or 0)
                else:
                    line_profit = int(self.calc_line_profit(line, current_year))
                if line_profit >= 1200 and random.random() < 0.25:
                    _add(
                        f"Success: {row['name']}'s {line_name} Delivers",
                        f"The route contributed an estimated ${line_profit:,} in line profit this year, "
                        "outperforming peer corridors.",
                        category="Venture",
                    )
                elif line_profit <= -600 and random.random() < 0.25:
                    _add(
                        f"Setback: {row['name']}'s {line_name} Struggles",
                        f"Service on {line_name} lost an estimated ${abs(line_profit):,} after costs, "
                        "prompting timetable and fare reviews.",
                        category="Venture",
                    )

    def get_effective_cost_for_player(self, player: Player, base_cost: int) -> int:
        """Return effective construction cost for player. AI difficulty applies: hard=0.85x, easy=1.15x."""
        if not getattr(player, 'is_ai', False):
            return base_cost
        diff = getattr(player, 'ai_difficulty', None) or 'hard'
        mult = AI_DIFFICULTY_COST_MULTIPLIER.get(diff, 1.0)
        return max(1, int(base_cost * mult))

    def get_effective_marketplace_price_for_player(self, player: Player, base_price: int) -> int:
        """Return effective marketplace price for player. AI difficulty applies: hard=0.85x, easy=1.1x."""
        if not getattr(player, 'is_ai', False):
            return base_price
        diff = getattr(player, 'ai_difficulty', None) or 'hard'
        mult = AI_DIFFICULTY_MARKETPLACE_MULTIPLIER.get(diff, 1.0)
        return max(1, int(base_price * mult))

    def get_lobby_success_chance_for_player(self, player: Player, amount: int, option_id: str = None) -> float:
        """Return lobby success chance for player. Hard AI gets +10% bonus, easy gets -5%."""
        base = self.get_lobby_success_chance(amount, option_id)
        if not getattr(player, 'is_ai', False):
            return base
        diff = getattr(player, 'ai_difficulty', None) or 'hard'
        bonus = AI_DIFFICULTY_LOBBY_BONUS.get(diff, 0.0)
        return min(0.95, max(0.05, base + bonus))

    def acquire_line_for_player(self, player: Player, line: Line):
        player.acquire_line(line)

    @staticmethod
    def _stamp_vehicle_purchase_year(vehicle, purchase_year: int) -> None:
        """Record when rolling stock entered service (maintenance aging uses this, not invention year)."""
        try:
            py = int(purchase_year)
        except (TypeError, ValueError):
            return
        from vehicle import Trainset
        if isinstance(vehicle, Trainset):
            vehicle.purchase_year = py
            loco = getattr(vehicle, 'locomotive', None)
            if loco is not None:
                loco.purchase_year = py
            for rc in getattr(vehicle, 'railcars', None) or []:
                rc.purchase_year = py
            return
        if hasattr(vehicle, 'purchase_year'):
            vehicle.purchase_year = py

    def acquire_trainset_for_player(self, player: Player, trainset: Trainset):
        self._stamp_vehicle_purchase_year(trainset, self.get_year())
        player.aquire_trainset(trainset)
    
    def acquire_locomotive_for_player(self, player: Player, locomotive: Locomotive):
        """Add a locomotive to player's inventory (stored separately, can be assembled later)"""
        self._stamp_vehicle_purchase_year(locomotive, self.get_year())
        if not hasattr(player, 'locomotives'):
            player.locomotives = []
        player.locomotives.append(locomotive)
    
    def acquire_railcar_for_player(self, player: Player, railcar: Railcar):
        """Add a railcar to player's inventory (stored separately, can be assembled later)"""
        self._stamp_vehicle_purchase_year(railcar, self.get_year())
        if not hasattr(player, 'railcars'):
            player.railcars = []
        player.railcars.append(railcar)

    def acquire_bus_for_player(self, player: Player, bus):
        """Add a bus to player inventory (used on bus lines)."""
        self._stamp_vehicle_purchase_year(bus, self.get_year())
        if not hasattr(player, 'buses'):
            player.buses = []
        player.buses.append(bus)

    def acquire_ferry_for_player(self, player: Player, ferry):
        """Add a ferry vessel to player inventory (used on ferry lines)."""
        self._stamp_vehicle_purchase_year(ferry, self.get_year())
        if not hasattr(player, 'ferries'):
            player.ferries = []
        player.ferries.append(ferry)

    def get_player_money(self, player: Player) -> int:
        return player.get_money()
    
    def get_player_line_names(self, player: Player) -> list[str]:
        return player.get_line_names()

    def get_player_rail_line_names(self, player: Player) -> list[str]:
        """Return line names that accept trainsets (rail lines only; exclude bus and ferry lines)."""
        lines_by_name = self.game.get_map().get_lines_by_name() or {}
        return [ln for ln in player.get_line_names() if lines_by_name.get(ln) and not isinstance(lines_by_name[ln], (BusLine, Ferry))]

    def get_player_trainsets(self, player: Player) -> list[Trainset]:
        return player.get_trainsets()
    
    def get_disassemble_info(self, player: Player) -> dict:
        """Get information needed for disassembling trainsets"""
        trainsets = self.get_player_trainsets(player)
        return {'trainsets': trainsets}
    
    def get_player_id(self, player: Player) -> int:
        return player.get_player_id()

    def get_player_color_index(self, player: Player) -> int:
        from player_colors import get_player_color_index
        return get_player_color_index(player)
    
    def get_trainset_capacity(self, trainset: Trainset) -> int:
        return trainset.get_capacity()
    
    def get_trainset_line_assignment(self, trainset: Trainset) -> str:
        return trainset.get_line_assignment()
    
    def _line_profit_from_cache(self, line: Line, current_year: int):
        """Year-end cached profit for this calendar year or the prior year (in-year turns)."""
        if not hasattr(line, '_cached_profit') or not hasattr(line, '_cached_profit_year'):
            return None
        cy = int(current_year)
        cached_year = int(line._cached_profit_year)
        if cached_year == cy or cached_year == cy - 1:
            return int(line._cached_profit or 0)
        return None
    
    def calc_line_profit(self, line: Line, current_year: int = None) -> int:
        """Line profit from year-end cache (no ridership re-simulation here)."""
        if current_year is None:
            current_year = self.game.get_year()
        cached = self._line_profit_from_cache(line, current_year)
        if cached is not None:
            return cached
        stored = getattr(line, 'profit', None)
        if isinstance(stored, (int, float)) and not isinstance(stored, bool):
            return int(stored)
            return 0
    
    def extend_line_route_from_beginning(self, line: Line, additional_points: list):
        line.extend_route_from_beginning(additional_points)

    def extend_line_route_from_end(self, line: Line, additional_points: list):
        line.extend_route_from_end(additional_points)

    def is_line_name_in_use(self, name: str, exclude_line: Line = None) -> bool:
        """True if any completed line or queued line build uses this name (case-insensitive)."""
        key = _norm_display_name(name)
        if not key:
            return False
        for _nm, ln in self.game.get_map().get_lines_by_name().items():
            if _norm_display_name(_nm) == key:
                if exclude_line is not None and ln is exclude_line:
                    continue
                return True
        for build in self.game.build_queue or []:
            if build.get("type") != "line":
                continue
            bd = build.get("data") or {}
            if _norm_display_name(bd.get("name", "")) == key:
                return True
        return False

    def assert_line_name_globally_available(self, name: str, exclude_line: Line = None) -> None:
        if not (name or "").strip():
            raise ValueError("Line name cannot be empty.")
        if self.is_line_name_in_use(name, exclude_line=exclude_line):
            raise ValueError(
                f"Line name '{name.strip()}' is already in use or reserved in the build queue. "
                "Line names must be unique for all players (compared case-insensitively)."
            )

    def _update_line_name_references(self, old_name: str, new_name: str) -> None:
        """After renaming a line: trainset assignments, station line lists, and queued jobs."""
        for p in self.game.get_players():
            for ts in p.get_trainsets():
                if ts.get_line_assignment() == old_name:
                    ts.assign_to_line(new_name)
        for line in self.get_map_lines().values():
            for st in line.get_stations() if hasattr(line, "get_stations") else (getattr(line, "stations", None) or []):
                lns = getattr(st, "line_names", None)
                if not lns and hasattr(st, "get_line_names"):
                    lns = st.get_line_names()
                if not lns:
                    continue
                for i, ln in enumerate(list(lns)):
                    if ln == old_name:
                        lns[i] = new_name
        for build in self.game.build_queue or []:
            if build.get("type") == "line":
                bd = build.setdefault("data", {})
                if bd.get("name") == old_name:
                    bd["name"] = new_name
            elif build.get("type") == "line_extension":
                bd = build.setdefault("data", {})
                if bd.get("line_name") == old_name:
                    bd["line_name"] = new_name
            elif build.get("type") == "station":
                bd = build.setdefault("data", {})
                lns = bd.get("line_names") or []
                if old_name in lns:
                    bd["line_names"] = [new_name if x == old_name else x for x in lns]

    def rename_line(self, line: Line, new_name: str) -> None:
        """Rename a line and keep map keys, owners, trainsets, stations, and build queue consistent."""
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Line name cannot be empty.")
        old_name = line.get_name()
        if new_name == old_name:
            return
        if _norm_display_name(new_name) == _norm_display_name(old_name):
            lines_dict = self.game.get_map().get_lines_by_name()
            if old_name in lines_dict and lines_dict[old_name] is line:
                del lines_dict[old_name]
                line.rename(new_name)
                lines_dict[new_name] = line
                owner = line.get_owner()
                if owner and old_name in owner.get_line_names():
                    owner.line_names[owner.line_names.index(old_name)] = new_name
                self._update_line_name_references(old_name, new_name)
            return
        self.assert_line_name_globally_available(new_name, exclude_line=line)
        lines_dict = self.game.get_map().get_lines_by_name()
        if old_name in lines_dict and lines_dict.get(old_name) is line:
            del lines_dict[old_name]
        line.rename(new_name)
        lines_dict[new_name] = line
        owner = line.get_owner()
        if owner and old_name in owner.get_line_names():
            owner.line_names[owner.line_names.index(old_name)] = new_name
        self._update_line_name_references(old_name, new_name)

    def _collect_normalized_station_names_for_player(
        self, player: Player, exclude_station=None, exclude_pending_station_build=None
    ) -> set:
        keys = set()
        for ln in player.get_line_names() or []:
            line = self.get_map_lines().get(ln)
            if not line:
                continue
            stations = line.get_stations() if hasattr(line, "get_stations") else (getattr(line, "stations", None) or [])
            for st in stations:
                if st is exclude_station:
                    continue
                keys.add(_norm_display_name(self.get_station_name(st)))
        for build in self.game.build_queue or []:
            if build is exclude_pending_station_build:
                continue
            if build.get("type") != "station" or build.get("player") != player:
                continue
            keys.add(_norm_display_name((build.get("data") or {}).get("name", "")))
        return keys

    def _station_serves_line_outside(self, served_line_names: list, new_line_names: set) -> bool:
        return any(ln not in new_line_names for ln in (served_line_names or []))

    def find_nearby_transfer_station_name(
        self,
        x: float,
        y: float,
        line_names: list,
        max_distance_m: float = None,
    ) -> Optional[str]:
        """Return the name of the nearest station on another line within max_distance_m, or None."""
        from game_presets import AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M

        max_m = float(max_distance_m if max_distance_m is not None else AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M)
        new_lines = set(line_names or [])
        if not new_lines:
            return None
        best_name = None
        best_d = max_m + 1.0
        game_map = self.game.get_map()

        def _consider(st_name: str, sx: float, sy: float, served: list) -> None:
            nonlocal best_name, best_d
            if not self._station_serves_line_outside(served, new_lines):
                return
            name = (st_name or "").strip()
            if not name:
                return
            d = self._direct_distance_between_stop_positions_m(x, y, sx, sy)
            if d <= max_m and d < best_d:
                best_d = d
                best_name = name

        for line_name, line in (game_map.get_lines_by_name() or {}).items():
            for station in (line.get_stations() or []):
                served = getattr(station, "line_names", None) or (
                    station.get_line_names() if hasattr(station, "get_line_names") else [line_name]
                )
                sx = float(station.get_x() if hasattr(station, "get_x") else getattr(station, "x", 0))
                sy = float(station.get_y() if hasattr(station, "get_y") else getattr(station, "y", 0))
                _consider(getattr(station, "name", ""), sx, sy, served)

        for build in self.game.build_queue or []:
            if build.get("type") != "station":
                continue
            data = build.get("data") or {}
            served = data.get("line_names") or []
            try:
                qx = float(data.get("x"))
                qy = float(data.get("y"))
            except (TypeError, ValueError):
                continue
            _consider(data.get("name", ""), qx, qy, served)

        return best_name

    def station_transfer_name_reuse_allowed(
        self,
        player: Player,
        station_name: str,
        x: float,
        y: float,
        line_names: list,
        max_distance_m: float = None,
    ) -> bool:
        """True when station_name matches a nearby stop on another line (transfer-hub naming)."""
        from game_presets import AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M

        target = _norm_display_name(station_name)
        if not target:
            return False
        nearby = self.find_nearby_transfer_station_name(
            float(x), float(y), list(line_names or []), max_distance_m=max_distance_m,
        )
        return bool(nearby) and _norm_display_name(nearby) == target

    def assert_station_name_unique_for_player(
        self,
        player: Player,
        station_name: str,
        exclude_station=None,
        exclude_pending_station_build=None,
        x: float = None,
        y: float = None,
        line_names: list = None,
    ) -> None:
        if not (station_name or "").strip():
            raise ValueError("Station name cannot be empty.")
        nk = _norm_display_name(station_name)
        if nk in self._collect_normalized_station_names_for_player(
            player,
            exclude_station=exclude_station,
            exclude_pending_station_build=exclude_pending_station_build,
        ):
            if (
                x is not None
                and y is not None
                and line_names
                and self.station_transfer_name_reuse_allowed(
                    player, station_name, float(x), float(y), list(line_names),
                )
            ):
                return
            raise ValueError(
                "You already have a station or a station in your build queue with this name "
                "(names are compared case-insensitively)."
            )

    def _station_players(self, station) -> list:
        owners = []
        seen = set()
        lns = getattr(station, "line_names", None) or (
            station.get_line_names() if hasattr(station, "get_line_names") else []
        )
        for lname in lns or []:
            line = self.get_map_lines().get(lname)
            if not line:
                continue
            o = self.get_line_owner(line)
            if o and id(o) not in seen:
                seen.add(id(o))
                owners.append(o)
        return owners

    def get_line_route(self, line: Line) -> list:
        return line.get_route()
    
    def get_line_owner(self, line: Line) -> Player:
        return line.get_owner()
    
    def get_line_vehicles(self, line: Line) -> list[Trainset]:
        return line.get_vehicles()
    
    def get_line_name(self, line: Line) -> str:
        return line.get_name()
    
    def get_line_object_id(self, line: Line) -> int:
        return line.get_object_id()
    
    def get_line_length(self, line: Line) -> int:
        return len(line)
    
    def get_vehicle_name(self, vehicle: Vehicle) -> str:
        return vehicle.name
    
    def get_vehicle_object_id(self, vehicle: Vehicle) -> int:
        return vehicle.object_id
    
    def get_locomotive_specs(self, locomotive: Locomotive) -> dict:
        return {
            "fuel": locomotive.fuel,
            "miles_per_unit": locomotive.miles_per_unit,
            "top_speed": locomotive.top_speed,
            "average_speed": locomotive.average_speed,
            "capacity": locomotive.capacity,
            "needs_third_rail": locomotive.needs_third_rail,
            "needs_overhead_wire": locomotive.needs_overhead_wire
        }
    
    def get_railcar_specs(self, railcar: Railcar) -> dict:
        return {
            "capacity": railcar.capacity
        }
    
    def get_boat_specs(self, boat: Boat) -> dict:
        return {
            "capacity": boat.capacity,
            "top_speed": boat.top_speed,
            "average_speed": boat.average_speed
        }
    
    def get_trainset_specs(self, trainset: Trainset) -> dict:
        return {
            "locomotive": trainset.locomotive,
            "cars": trainset.cars,
            "capacity": trainset.get_capacity(),
            "line_assignment": trainset.get_line_assignment()
        }
    
    def assign_vehicle_to_line(self, vehicle: Vehicle, line_name: str):
        if isinstance(vehicle, Trainset):
            vehicle.assign_to_line(line_name)

    def get_map_width(self) -> int:
        return self.game.get_map().get_width()
    
    def get_map_height(self) -> int:
        return self.game.get_map().get_height()
    
    def get_map_grid(self) -> list[list]:
        return self.game.get_map().get_grid()
    
    def get_map_lines(self) -> dict:
        return self.game.get_map().get_lines_by_name()
    
    def get_map_roads(self) -> dict:
        return self.game.get_map().get_roads()
    
    def add_line_to_map(self, line: Line):
        self.game.get_map().add_line(line)

    def delete_line_from_map(self, line: Line):
        self.game.get_map().delete_line(line)

    def get_marketplace(self) -> Marketplace:
        return self.game.get_marketplace()

    def add_line_to_marketplace(self, line: Line, seller: str = None):
        owner = self.get_line_owner(line) if hasattr(self, 'get_line_owner') else getattr(line, 'owner', None)
        if seller is None and owner is not None:
            seller = self.get_player_name(owner)
        if seller is None:
            seller = "Manufacturer"
        current_year = self.get_year()
        self.game.get_marketplace().add_line(line, current_year=current_year, seller=seller)

    def sell_player_line_to_marketplace(self, player: Player, line_name: str) -> dict:
        """List a player-owned line on the marketplace and remove it from the map."""
        if not line_name:
            raise ValueError("Line name required")
        game_map = self.get_map()
        line = game_map.get_lines_by_name().get(line_name)
        if line is not None:
            owner = self.get_line_owner(line) if hasattr(self, 'get_line_owner') else getattr(line, 'owner', None)
            if owner is not None and owner != player:
                raise ValueError(f"Player does not own line '{line_name}'")
            for ts in list(player.get_trainsets()):
                if ts.get_line_assignment() == line_name:
                    try:
                        self.unassign_trainset_complete(ts)
                    except ValueError:
                        pass
        cancelled = []
        queue = self.get_build_queue() if hasattr(self, 'get_build_queue') else []
        for idx in range(len(queue) - 1, -1, -1):
            build = queue[idx]
            if build.get('player') != player:
                continue
            data = build.get('data') or {}
            matches = (
                (build.get('type') == 'line' and data.get('name') == line_name)
                or (build.get('type') == 'line_extension' and data.get('line_name') == line_name)
                or (build.get('type') == 'station' and line_name in (data.get('line_names') or []))
            )
            if matches:
                cancelled.append(self.cancel_build(idx))
        if line is None:
            return {
                'success': True,
                'line_name': line_name,
                'listed': False,
                'cancelled_builds': len(cancelled),
            }
        current_year = self.get_year()
        seller = self.get_player_name(player)
        self.get_marketplace().add_line(line, current_year=current_year, seller=seller)
        if line_name in player.get_line_names():
            player.remove_line(line_name)
        self.delete_line_from_map(line)
        return {
            'success': True,
            'line_name': line_name,
            'listed': True,
            'cancelled_builds': len(cancelled),
        }

    def get_timeline_start_year(self) -> int:
        return self.game.get_timeline().get_start_year()
    
    def get_timeline_end_year(self) -> int:
        return self.game.get_timeline().get_end_year()

    def get_inflation_factor_for_economy(self, max_factor: float = 10.0) -> float:
        from inflation_utils import get_inflation_factor
        return get_inflation_factor(
            self.get_year(),
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
            max_factor=max_factor,
        )

    def inflate_economic_value(self, base_amount: float, max_factor: float = 10.0) -> int:
        from inflation_utils import inflate_economic_value
        return inflate_economic_value(
            base_amount,
            self.get_year(),
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
            max_factor=max_factor,
        )

    def get_nominal_fare_cap(self, year: int = None) -> tuple[float, float]:
        from inflation_utils import get_nominal_fare_cap
        y = int(self.get_year() if year is None else year)
        return get_nominal_fare_cap(
            y,
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
        )

    def get_timeline_fare_target(self, year: int = None) -> int:
        from inflation_utils import get_timeline_fare_target
        y = int(self.get_year() if year is None else year)
        return get_timeline_fare_target(
            y,
            start_year=self.get_timeline_start_year(),
            end_year=self.get_timeline_end_year(),
        )

    def apply_timeline_fare_to_line(self, line, year: int = None) -> None:
        """Set a new line's fare to the low early-game target for the calendar year."""
        if line is not None and hasattr(line, 'fare'):
            line.fare = self.get_timeline_fare_target(year)

    def _calendar_years_since_timeline_start(self) -> int:
        return self.get_year() - self.get_timeline_start_year()

    def merge_ai_consideration_allowed_this_year(self) -> bool:
        min_y = getattr(self.game, 'merge_ai_min_years_before_consider', None)
        if min_y is None:
            from game_presets import AI_MERGE_MIN_YEARS_BEFORE_CONSIDER
            min_y = AI_MERGE_MIN_YEARS_BEFORE_CONSIDER
        return self._calendar_years_since_timeline_start() >= int(min_y)

    def merge_ai_blocked_early_calendar_years(self, acquirer: Player, target: Player) -> bool:
        n = getattr(self.game, 'merge_ai_auto_fail_first_n_years', None)
        if n is None:
            from game_presets import AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
            n = AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
        if self._calendar_years_since_timeline_start() >= int(n):
            return False
        return bool(getattr(acquirer, 'is_ai', False) or getattr(target, 'is_ai', False))

    def get_timeline_current_year(self) -> int:
        return self.game.get_timeline().get_year()
    
    def advance_timeline(self):
        self.game.get_timeline().advance()

    def go_back_timeline(self):
        self.game.get_timeline().go_back()

    def advance_turn(self, event_population_change: float = 0.0):
        self.game.increase_year(event_population_change)
        # to ensure it happens after all year-end operations
    
    def get_strike_warnings(self, current_year: int):
        """Get strike warnings for all players"""
        return self.game.get_strike_warnings(current_year)
    
    def process_player_finances(self, player, current_year: int):
        """Process all financial calculations for a player using cached profit values.
        Profit must be pre-calculated during year advance to avoid expensive calculations here."""
        player_profit = self.calc_player_profit(player, current_year)
        
        trainset_maintenance = self.game.calc_trainset_maintenance(player)
        resource_charge = self.game.calc_resource_costs(player, current_year)
        line_maintenance = self.game.calc_maitnance_by_player(player) or 0
        
        laws = getattr(self.game, '_government_laws', {})
        maintenance_relief = laws.get('maintenance_relief', {})
        player_id = player.get_player_id()
        if player_id in maintenance_relief:
            factor = maintenance_relief[player_id]
            trainset_maintenance = int(trainset_maintenance * factor)
            line_maintenance = int(line_maintenance * factor)
        
        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, current_year, player_profit)
        worker_costs, is_on_strike = self.game.calc_worker_costs(player, current_year)
        
        grants = laws.get('grants', {})
        is_favored = grants.get(player_id, 0) > 0
        government_grant = self._compute_transit_subsidy(player, current_year, is_favored)
        
        total_deductions = trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs
        net = player_profit - total_deductions + government_grant
        
        if not player.is_player_eliminated():
            # Profit must ADD to money (never subtract). Costs are deducted separately.
            if player_profit > 0:
                self.add_player_money(player, player_profit, reason='Line profit')
            elif player_profit < 0:
                self.deduct_player_money(player, -player_profit, reason='Line loss')
            if government_grant:
                self.add_player_money(player, government_grant, reason='Government grant')
            if total_deductions:
                self.deduct_player_money(player, total_deductions, reason='Taxes, maintenance, workers')
        
        return {
            'profit': player_profit,
            'trainset_maintenance': trainset_maintenance,
            'resource_charge': resource_charge,
            'line_maintenance': line_maintenance,
            'poll_tax': poll_tax,
            'property_tax': property_tax,
            'income_tax': income_tax,
            'worker_costs': worker_costs,
            'government_grant': government_grant,
            'net': net,
            'is_on_strike': is_on_strike
        }
    
    def _get_player_transit_km_and_stations(self, player) -> tuple:
        """Return (total_route_km, total_open_stations) for this player. Used for transit subsidy.
        total_route_km is sum of segment lengths (grid units, ~1 unit ≈ 1 km)."""
        import math
        total_km = 0.0
        total_stations = 0
        map_lines = self.get_map_lines() or {}
        for line_name in player.get_line_names():
            line = map_lines.get(line_name)
            if not line:
                continue
            route = self.get_line_route(line)
            if len(route) > 1:
                for i in range(len(route) - 1):
                    p1, p2 = route[i], route[i + 1]
                    x1, y1 = float(p1[0]) if isinstance(p1, (list, tuple)) else float(getattr(p1, 'x', 0)), float(p1[1]) if isinstance(p1, (list, tuple)) else float(getattr(p1, 'y', 0))
                    x2, y2 = float(p2[0]) if isinstance(p2, (list, tuple)) else float(getattr(p2, 'x', 0)), float(p2[1]) if isinstance(p2, (list, tuple)) else float(getattr(p2, 'y', 0))
                    total_km += math.hypot(x2 - x1, y2 - y1)
            stations = line.get_stations() if hasattr(line, 'get_stations') else []
            total_stations += len(stations or [])
        return (total_km, total_stations)
    
    def _compute_transit_subsidy(self, player, current_year: int, is_favored: bool) -> int:
        """Government subsidizes transit by km and open stations. One turn only; no subsidy if no transit (player cannot make money doing nothing)."""
        if not is_favored:
            return 0
        total_km, num_stations = self._get_player_transit_km_and_stations(player)
        if total_km <= 0 or num_stations <= 0:
            return 0
        per_km = 80 + (current_year - 1830) // 10 * 10
        per_station = 40 + (current_year - 1830) // 10 * 5
        return int(total_km * per_km + num_stations * per_station)
    
    def estimate_annual_costs_for_player(self, player, next_year: int, extra_workers_estimate: int = 2) -> int:
        """Estimate next year's total costs (taxes, maintenance, workers) without side effects.
        Used by AI for survival reserve. extra_workers_estimate: buffer for workers AI might rent this turn."""
        game = self.game
        current_year = int(next_year) - 1
        player_profit = self.calc_player_profit(player, current_year) if hasattr(self, 'calc_player_profit') else 0
        poll_tax, property_tax, income_tax = game.calc_taxes(player, next_year, player_profit)
        taxes = poll_tax + property_tax + income_tax
        line_maint = game.calc_maitnance_by_player(player) or 0
        trainset_maint = game.calc_trainset_maintenance(player) or 0
        resource = game.calc_resource_costs(player, next_year) or 0
        from worker import ENGINEER, CONSTRUCTION_WORKER
        worker_costs = 0
        for w in (ENGINEER, CONSTRUCTION_WORKER):
            rental = player.get_worker_rental(w.name)
            if rental:
                worker_costs += rental.get_yearly_cost(next_year)
        worker_costs += extra_workers_estimate * 600
        return taxes + line_maint + trainset_maint + resource + worker_costs

    def get_player_financial_summary(self, player, current_year: int):
        """Get financial summary for a player without modifying player state.
        Returns dict with all financial calculations (for Finance tab and display)."""
        player_profit = self.calc_player_profit(player)
        
        trainset_maintenance = self.game.calc_trainset_maintenance(player)
        resource_charge = self.game.calc_resource_costs(player)
        line_maintenance = self.game.calc_maitnance_by_player(player) or 0
        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, current_year, player_profit)
        worker_costs, is_on_strike = self.game.calc_worker_costs(player, current_year)
        
        total_deductions = trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs
        laws = getattr(self.game, '_government_laws', {})
        grants = laws.get('grants', {})
        is_favored = grants.get(self.get_player_id(player), 0) > 0
        government_grant = self._compute_transit_subsidy(player, current_year, is_favored)
        net = player_profit - total_deductions + government_grant
        
        return {
            'profit': player_profit,
            'trainset_maintenance': trainset_maintenance,
            'resource_charge': resource_charge,
            'line_maintenance': line_maintenance,
            'poll_tax': poll_tax,
            'property_tax': property_tax,
            'income_tax': income_tax,
            'worker_costs': worker_costs,
            'government_grant': government_grant,
            'total_deductions': total_deductions,
            'net': net,
            'is_on_strike': is_on_strike
        }
    
    def process_end_of_year(self, current_year: int):
        """Process end of year operations"""
        self.game.process_end_of_year(current_year)
    
    def check_and_unassign_trainsets_for_engineers(self, player, current_year: int):
        """Check engineer availability and unassign trainsets if needed"""
        self.game.check_and_unassign_trainsets_for_engineers(player, current_year)
    
    def check_and_eliminate_player(self, player):
        """Check if player should be eliminated and eliminate them"""
        return self.game.check_and_eliminate_player(player)
    
    def get_newly_available_vehicles(self, old_year: int, new_year: int):
        """Get newly available vehicles between two years"""
        return self.game.get_newly_available_vehicles(old_year, new_year)
    
    def check_agency_lawsuits(self, current_year: int):
        """Check for agency lawsuits"""
        return self.game.check_agency_lawsuits(current_year)
    
    def remove_player_trainset(self, player: Player, trainset):
        """Remove a trainset from a player"""
        player.remove_trainset(trainset)
    
    def remove_trainset_from_player_list(self, player: Player, trainset):
        """Remove a trainset from player's trainsets list"""
        if trainset in player.trainsets:
            player.trainsets.remove(trainset)
    
    def player_has_active_worker_strike(self, player: Player) -> bool:
        """True when any rented worker type is currently on strike."""
        rentals = getattr(player, 'worker_rentals', None) or {}
        for rental in rentals.values():
            if rental and hasattr(rental, 'is_on_strike') and rental.is_on_strike():
                return True
        return False
    
    def rent_workers_for_player(self, player: Player, worker_type, quantity: int, current_year: int):
        """Rent workers for a player. Blocked while any worker strike is in progress."""
        if self.player_has_active_worker_strike(player):
            raise ValueError(
                "Cannot hire workers while a strike is in progress. "
                "Raise salaries in the Workers tab to end the strike first."
            )
        player.rent_workers(worker_type, quantity, current_year)
        if getattr(self.game, 'war_active', False) and worker_type.name in player.worker_rentals:
            mult = float(getattr(self.game, 'war_new_worker_cost_multiplier', 1.25))
            rental = player.worker_rentals[worker_type.name]
            rental.demanded_multiplier = min(5.0, float(rental.demanded_multiplier) * mult)
    
    def calculate_worker_rental_cost(self, worker_type, quantity: int, current_year: int) -> int:
        """Calculate worker rental cost with supply and demand pricing"""
        base_yearly_cost = worker_type.get_yearly_cost(current_year)
        base_total = base_yearly_cost * quantity
        
        # Get available supply for supply/demand pricing
        available_supply = self.marketplace_get_worker_supply(worker_type.name)
        
        supply = max(1, available_supply)
        supply_demand_multiplier = 1.0 + (1.0 / supply) * 2.0
        supply_demand_multiplier = min(5.0, supply_demand_multiplier)
        
        adjusted_total = int(base_total * supply_demand_multiplier)
        if getattr(self.game, 'war_active', False):
            adjusted_total = int(adjusted_total * float(getattr(self.game, 'war_new_worker_cost_multiplier', 1.25)))
        return adjusted_total
    
    def fire_workers_for_player(self, player: Player, worker_type_name: str):
        """Fire workers for a player (remove from worker_rentals)"""
        if worker_type_name in player.worker_rentals:
            del player.worker_rentals[worker_type_name]
    
    def get_max_fireable_workers(self, player: Player, worker_type_name: str) -> int:
        """Max number of workers of this type the player can fire this turn.
        After Worker Protection Act (1925): at most half of that type can be fired;
        striking workers cannot be fired at all."""
        rental = self.get_player_worker_rental(player, worker_type_name)
        if not rental or rental.quantity == 0:
            return 0
        current_year = self.get_year()
        if current_year >= 1925:
            if rental.is_on_strike():
                return 0
            return max(0, rental.quantity // 2)
        return rental.quantity
    
    def get_war_start_roll_probability(self, calendar_year: int) -> float:
        """Per-year probability of a new war starting (after Pacifism law, if active for that year)."""
        g = self.game
        start_p = max(0.0, min(1.0, float(getattr(g, 'war_start_chance_per_year', 0.02))))
        until = getattr(g, '_pacifism_until_year', None)
        if until is not None and calendar_year < until:
            from game_presets import PACIFISM_WAR_START_CHANCE_MULTIPLIER
            start_p = max(0.0, min(1.0, start_p * PACIFISM_WAR_START_CHANCE_MULTIPLIER))
        return start_p

    def _war_remove_one_random_worker(self, player: Player) -> bool:
        """Remove one hired worker of a random type; return True if a worker was removed."""
        from worker import ENGINEER
        rentals = getattr(player, 'worker_rentals', None) or {}
        names = [wn for wn, rental in list(rentals.items()) if rental and rental.quantity > 0]
        if not names:
            return False
        wn = random.choice(names)
        rental = player.worker_rentals[wn]
        rental.quantity -= 1
        if rental.quantity <= 0:
            del player.worker_rentals[wn]
        self.marketplace_add_worker_supply(wn, 1)
        if wn == ENGINEER.name:
            self.check_and_unassign_trainsets_for_engineers(player, self.get_year())
        return True

    def _process_war_events(self, new_year: int) -> dict:
        """Start/end wars and run weighted drafts. Uses government influence: unfavored players lose more workers."""
        g = self.game
        out = {'war_started': False, 'war_ended': False, 'drafts': [], 'war_active': bool(getattr(g, 'war_active', False))}
        war_active = bool(getattr(g, 'war_active', False))
        start_p = self.get_war_start_roll_probability(new_year)
        end_p = max(0.0, min(1.0, float(getattr(g, 'war_end_chance_per_year', 0.3))))
        draft_p = max(0.0, min(1.0, float(getattr(g, 'war_worker_draft_chance_per_year', 0.5))))
        if not war_active:
            if random.random() < start_p:
                g.war_active = True
                out['war_started'] = True
        else:
            if random.random() < end_p:
                g.war_active = False
                out['war_ended'] = True
            elif random.random() < draft_p:
                players = [p for p in self.get_players() if not self.is_player_eliminated(p)]
                gov = g.get_government()
                if players:
                    weights = []
                    for p in players:
                        if gov:
                            pid = p.get_player_id()
                            u = float(gov.get_unfavorable_influence(pid)) + 0.25
                            f = float(gov.get_favorable_influence(pid)) + 0.25
                            weights.append(max(0.05, u / max(1e-6, f)))
                        else:
                            weights.append(1.0)
                    removals = random.randint(max(1, len(players)), max(2, 2 * len(players)))
                    lost = {p: 0 for p in players}
                    for _ in range(removals):
                        idx = random.choices(range(len(players)), weights=weights, k=1)[0]
                        p = players[idx]
                        if self._war_remove_one_random_worker(p):
                            lost[p] = lost.get(p, 0) + 1
                    for p, c in lost.items():
                        if c > 0:
                            out['drafts'].append({'player_name': self.get_player_name(p), 'count': c})
        out['war_active'] = bool(getattr(g, 'war_active', False))
        return out

    def fire_workers(self, player: Player, worker_type_name: str, quantity: int) -> dict:
        """Fire a number of workers of the given type. All game logic here (not in UI).
        Returns dict with success (bool), message (str), and optionally unassign_count (int).
        Validates max fireable (Worker Protection Act) and applies firing + marketplace + unassign."""
        rental = self.get_player_worker_rental(player, worker_type_name)
        if not rental or rental.quantity == 0:
            return {'success': False, 'message': f'No {worker_type_name}s to fire.'}
        max_fireable = self.get_max_fireable_workers(player, worker_type_name)
        if max_fireable == 0:
            return {'success': False, 'message': 'Cannot fire workers (Worker Protection Act: cannot fire striking workers).'}
        if quantity <= 0 or quantity > max_fireable:
            return {'success': False, 'message': f'You may fire at most {max_fireable} {worker_type_name}(s) per turn.'}
        from worker import ENGINEER
        assigned_count = self.get_player_assigned_trainsets_count(player) if worker_type_name == ENGINEER.name else 0
        new_count = rental.quantity - quantity
        unassign_count = 0
        if worker_type_name == ENGINEER.name and new_count < assigned_count:
            unassign_count = assigned_count - new_count
        rental.quantity = new_count
        if rental.quantity == 0:
            self.fire_workers_for_player(player, worker_type_name)
        self.marketplace_add_worker_supply(worker_type_name, quantity)
        if worker_type_name == ENGINEER.name and unassign_count > 0:
            self.check_and_unassign_trainsets_for_engineers(player, self.get_year())
        return {'success': True, 'message': f'Fired {quantity} {worker_type_name}(s).', 'quantity_fired': quantity, 'unassign_count': unassign_count}

    def ai_minimum_turn_action(self, player: Player, year: int) -> dict:
        """Guaranteed successful bookkeeping action when no other AI action is possible (e.g. broke, build queued).
        Keeps the invariant that every living AI registers at least one turn action. No gameplay state change."""
        return {
            'success': True,
            'year': year,
            'player_id': self.get_player_id(player),
        }

    def set_line_fare(self, line, fare: float):
        """Set the fare for a line (rounded to nearest $0.25)."""
        from inflation_utils import round_fare_to_quarter
        line.fare = round_fare_to_quarter(fare)

    def get_line_fare(self, line) -> float:
        from inflation_utils import round_fare_to_quarter
        return round_fare_to_quarter(getattr(line, 'fare', 0) or 0)

    def set_worker_salary_multiplier(self, worker_type_name: str, multiplier: float) -> None:
        """Set salary multiplier for a worker type (shared across players, same as Workers tab)."""
        from worker import CONSTRUCTION_WORKER, ENGINEER, LAWYER, MAINTENANCE_WORKER
        for wt in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
            if wt.name == worker_type_name:
                wt.set_salary_multiplier(float(multiplier))
                return
        raise ValueError(f"Unknown worker type: {worker_type_name!r}")
    
    def set_line_overhead_wire(self, line, has_wire: bool):
        """Set whether a line has overhead wire"""
        line.has_overhead_wire = has_wire
    
    def set_line_third_rail(self, line, has_rail: bool):
        """Set whether a line has third rail"""
        line.has_third_rail = has_rail
    
    def unassign_trainset_from_line(self, trainset, line_name: str = None):
        """Unassign a trainset from a line (or its current line when line_name is None)."""
        if line_name is None:
            line_name = trainset.get_line_assignment()
        if line_name:
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line and trainset in line.vehicles:
                line.vehicles.remove(trainset)
        trainset.assign_to_line(None)
    
    def remove_lawsuit_excuse_from_player(self, player: Player, excuse):
        """Remove a lawsuit excuse from a player"""
        player.remove_lawsuit_excuse(excuse)

    def try_file_lawsuit(self, plaintiff: Player, defendant: Player, excuse=None, made_up_cause: str = "") -> dict:
        """File and resolve a lawsuit between players. All game logic lives here.
        Returns dict with: winner, plaintiff_name, defendant_name, cause, damages, court_costs,
        plaintiff_lawyers, defendant_lawyers, result_message."""
        from lawsuit import Lawsuit
        if plaintiff == defendant:
            return {'success': False, 'message': 'Cannot sue yourself.'}
        if self.is_player_eliminated(plaintiff) or self.is_player_eliminated(defendant):
            return {'success': False, 'message': 'Cannot sue eliminated player.'}
        current_year = self.get_year()
        plaintiff_lawyers = self.get_player_available_lawyer_count(plaintiff)
        defendant_lawyers = self.get_player_available_lawyer_count(defendant)
        lawsuit = Lawsuit(plaintiff, defendant, excuse, current_year, made_up_cause)
        winner = lawsuit.resolve(plaintiff_lawyers, defendant_lawyers)
        if excuse:
            self.remove_lawsuit_excuse_from_player(plaintiff, excuse)
        court_costs = int(lawsuit.damages * 0.1)
        plaintiff_name = self.get_player_name(plaintiff)
        defendant_name = self.get_player_name(defendant)
        if winner == plaintiff:
            result_msg = (
                f"Lawsuit won by {plaintiff_name}!\n"
                f"Cause: {lawsuit.cause}\n"
                f"{defendant_name} must pay ${lawsuit.damages} in damages.\n"
                f"Both sides paid ${court_costs} in court costs.\n"
                f"Lawyers: {plaintiff_name} ({plaintiff_lawyers}) vs {defendant_name} ({defendant_lawyers})"
            )
        else:
            reparations = int(lawsuit.damages * 0.5)
            result_msg = (
                f"Lawsuit won by {defendant_name}!\n"
                f"Cause: {lawsuit.cause}\n"
                f"{plaintiff_name} must pay ${reparations} in reparations to {defendant_name}.\n"
                f"Both sides paid ${court_costs} in court costs.\n"
                f"Lawyers: {plaintiff_name} ({plaintiff_lawyers}) vs {defendant_name} ({defendant_lawyers})"
            )
        return {
            'success': True,
            'winner': winner,
            'plaintiff_name': plaintiff_name,
            'defendant_name': defendant_name,
            'cause': lawsuit.cause,
            'damages': lawsuit.damages,
            'court_costs': court_costs,
            'plaintiff_lawyers': plaintiff_lawyers,
            'defendant_lawyers': defendant_lawyers,
            'result_message': result_msg,
            'lawsuit': lawsuit,
        }

    def get_player_name(self, player: Player) -> str:
        return player.get_name()
    
    def get_player_money(self, player: Player) -> int:
        return player.get_money()
    
    def get_player_line_names(self, player: Player) -> list:
        return player.get_line_names()
    
    def is_player_eliminated(self, player: Player) -> bool:
        return player.is_player_eliminated()
    
    def get_player_worker_rental(self, player: Player, worker_type_name: str):
        return player.get_worker_rental(worker_type_name)
    
    def get_player_trainsets(self, player: Player) -> list:
        return player.get_trainsets()
    
    def get_line_route(self, line) -> list:
        return line.get_route()
    
    def get_line_stations(self, line) -> list:
        return line.get_stations()
    
    def get_line_frequency(self, line) -> int:
        """Get service frequency per year for a line"""
        if not line.vehicles:
            return 0
        fastest_speed = 0
        for vehicle in line.vehicles:
            if vehicle and hasattr(vehicle, 'locomotive') and vehicle.locomotive:
                if vehicle.locomotive.average_speed > fastest_speed:
                    fastest_speed = vehicle.locomotive.average_speed
        if fastest_speed == 0:
            return 0
        return line.service_frequency_per_year(trainset_avg_speed=fastest_speed)
    
    def get_stations_at_point(self, x: float, y: float, tolerance: float = 0.5) -> list:
        """Get all stations near a coordinate point (within tolerance distance)"""
        import math
        stations_at_point = []
        game_map = self.game.get_map()
        all_lines = game_map.get_lines_by_name()
        for line_name, line in all_lines.items():
            stations = line.get_stations()
            if not stations:
                continue
            for station in stations:
                # Use distance-based checking instead of exact match
                dist = math.hypot(station.get_x() - x, station.get_y() - y)
                if dist <= tolerance:
                    stations_at_point.append((station, line_name, line))
        return stations_at_point
    
    def rename_station(self, station, new_name: str):
        """Rename a station; name must be unique for each owning company (case-insensitive)."""
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Station name cannot be empty.")
        for p in self._station_players(station):
            self.assert_station_name_unique_for_player(p, new_name, exclude_station=station)
        station.name = new_name
    
    def get_station_name(self, station) -> str:
        """Get station name"""
        return station.name

    def get_station_throughput(self, station, current_year: int = None) -> int:
        """Estimated annual passenger throughput at a station (share of serving lines' ridership)."""
        line_names = getattr(station, 'line_names', None) or (
            station.get_line_names() if hasattr(station, 'get_line_names') else []
        )
        if not line_names:
            return 0
        if current_year is None:
            current_year = self.get_year()
        ridership_data = self.get_ridership_data_for_year(current_year)
        map_lines = self.get_map_lines() or {}
        total = 0
        for line_name in line_names:
            line = map_lines.get(line_name)
            if not line:
                continue
            passengers = int(ridership_data.get(line_name, 0) or 0)
            stations = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
            share = max(1, len(stations))
            total += passengers // share
        return total
    
    def close_station(self, player, station) -> dict:
        """Close a station. Fails if station serves a line owned by another player.
        Returns {'success': True} or {'success': False, 'message': str}."""
        line_names = getattr(station, 'line_names', None) or (getattr(station, 'get_line_names', lambda: [])() if callable(getattr(station, 'get_line_names', None)) else [])
        all_lines = self.get_map_lines()
        for line_name in (line_names or []):
            line = all_lines.get(line_name)
            if line:
                owner = self.get_line_owner(line)
                if owner is not None and owner != player:
                    return {'success': False, 'message': f"Cannot close station: it serves line '{line_name}' owned by another player."}
        for line_name, line in all_lines.items():
            if hasattr(line, 'stations') and station in line.stations:
                line.remove_station(station)
        game_map = self.game.get_map()
        if hasattr(game_map, 'stations') and station in game_map.stations:
            game_map.stations.remove(station)
        return {'success': True}

    def modify_station_lines(self, station, line_names_to_add: list = None, line_names_to_remove: list = None):
        """Add or remove lines from a station"""
        if line_names_to_add:
            for line_name in line_names_to_add:
                station.add_line(line_name)
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line and station not in line.get_stations():
                    line.add_station(station)
        if line_names_to_remove:
            for line_name in line_names_to_remove:
                if line_name in station.line_names:
                    station.line_names.remove(line_name)
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line:
                    line.remove_station(station)
    
    
    def calc_line_maintenance(self, line, current_year: int = None) -> int:
        """Full line maintenance including schedule operating cost when ``current_year`` is set (matches year-end finances)."""
        if current_year is None and self.game is not None:
            current_year = self.game.get_year()
        return calc_line_maintenance_with_off_street_surcharge(
            self.get_map(), line, int(current_year or 0),
        )
    
