from map import Map
from player import Player
from timeline import Timeline
from line import Line
from marketplace import Marketplace
from events import EventSystem
from worker import ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER



class Game:
    def __init__(self,
                 players: list[Player],
                 game_map: Map,
                 timeline: Timeline,
                 marketplace: Marketplace,
                 is_playing: bool = False,
                 worker_replenishment_rate: float = 1.0,
                 natural_disaster_frequency: float = 1.0,
                 random_ai_spawn_enabled: bool = True,
                 merge_enabled: bool = True,
                 merge_ai_ai_enabled: bool = True,
                 merge_ai_human_enabled: bool = True,
                 merge_human_human_enabled: bool = True,
                 inflation_rate: float = 1.0,
                 profit_margin: float = None,
                 random_ai_spawn_interval_years: int = 30,
                 default_ai_difficulty_for_spawns: str = 'hard',
                 ai_min_population: int = 100,
                 game_seed: int = None,
                 merge_ai_healthy_profit_threshold_pct: float = 25.0,
                 merge_ai_reject_chance_pct: float = 90.0,
                 merge_ai_min_turns_to_live_for_accept: int = None,
                 merge_ai_min_years_before_consider: int = None,
                 merge_ai_auto_fail_first_n_years: int = None,
                 war_start_chance_per_year: float = None,
                 war_end_chance_per_year: float = None,
                 war_worker_draft_chance_per_year: float = None,
                 war_new_worker_cost_multiplier: float = None,
                 ai_debug_notifications_enabled: bool = True,
                 ):
        self.timeline = timeline
        self.marketplace = marketplace
        self.players = players
        self.map = game_map
        # Set game reference on map so it can access event system
        if hasattr(game_map, '_game'):
            game_map._game = self
        self.is_playing = is_playing
        self.players_by_name = self.calc_players_by_name()
        self.event_system = EventSystem(natural_disaster_frequency)
        
        if hasattr(game_map, 'is_nyc_proper') and game_map.is_nyc_proper:
            from nyc_events import get_nyc_events
            for event in get_nyc_events():
                self.event_system.add_event(event)
        elif hasattr(game_map, 'is_ny_metro') and game_map.is_ny_metro:
            # NY Metro uses same events as NYC
            from nyc_events import get_nyc_events
            for event in get_nyc_events():
                self.event_system.add_event(event)
        self.build_queue = []
        self.worker_replenishment_rate = worker_replenishment_rate
        self.random_ai_spawn_enabled = random_ai_spawn_enabled
        self.merge_enabled = merge_enabled
        self.merge_ai_ai_enabled = merge_ai_ai_enabled
        self.merge_ai_human_enabled = merge_ai_human_enabled
        self.merge_human_human_enabled = merge_human_human_enabled
        self.inflation_rate = max(0.1, min(5.0, inflation_rate))
        from game_presets import DEFAULT_PROFIT_MARGIN
        if profit_margin is None:
            profit_margin = DEFAULT_PROFIT_MARGIN
        self.profit_margin = max(0.25, min(5.0, profit_margin))
        self.random_ai_spawn_interval_years = max(5, min(200, random_ai_spawn_interval_years))
        self.default_ai_difficulty_for_spawns = default_ai_difficulty_for_spawns if default_ai_difficulty_for_spawns in ('easy', 'medium', 'hard') else 'hard'
        self.ai_min_population = max(0, ai_min_population)
        self.ai_debug_notifications_enabled = bool(ai_debug_notifications_enabled)
        self.merge_ai_healthy_profit_threshold_pct = max(0, min(100, merge_ai_healthy_profit_threshold_pct))
        self.merge_ai_reject_chance_pct = max(0, min(100, merge_ai_reject_chance_pct))
        from game_presets import (
            AI_MERGE_MIN_TURNS_TO_LIVE_FOR_ACCEPT,
            AI_MERGE_MIN_YEARS_BEFORE_CONSIDER,
            AI_MERGE_AUTO_FAIL_FIRST_N_YEARS,
        )
        _turns_live_default = AI_MERGE_MIN_TURNS_TO_LIVE_FOR_ACCEPT
        self.merge_ai_min_turns_to_live_for_accept = max(
            0, min(200, int(_turns_live_default if merge_ai_min_turns_to_live_for_accept is None else merge_ai_min_turns_to_live_for_accept))
        )
        self.merge_ai_min_years_before_consider = max(
            0, min(50, int(AI_MERGE_MIN_YEARS_BEFORE_CONSIDER if merge_ai_min_years_before_consider is None else merge_ai_min_years_before_consider))
        )
        self.merge_ai_auto_fail_first_n_years = max(
            0, min(50, int(AI_MERGE_AUTO_FAIL_FIRST_N_YEARS if merge_ai_auto_fail_first_n_years is None else merge_ai_auto_fail_first_n_years))
        )
        from game_presets import (
            WAR_END_CHANCE_PER_YEAR,
            WAR_NEW_WORKER_COST_MULTIPLIER,
            WAR_START_CHANCE_PER_YEAR,
            WAR_WORKER_DRAFT_CHANCE_PER_YEAR,
        )
        self.war_active = False
        self.war_start_chance_per_year = max(0.0, min(1.0, float(
            WAR_START_CHANCE_PER_YEAR if war_start_chance_per_year is None else war_start_chance_per_year)))
        self.war_end_chance_per_year = max(0.0, min(1.0, float(
            WAR_END_CHANCE_PER_YEAR if war_end_chance_per_year is None else war_end_chance_per_year)))
        self.war_worker_draft_chance_per_year = max(0.0, min(1.0, float(
            WAR_WORKER_DRAFT_CHANCE_PER_YEAR if war_worker_draft_chance_per_year is None else war_worker_draft_chance_per_year)))
        self.war_new_worker_cost_multiplier = max(1.0, min(3.0, float(
            WAR_NEW_WORKER_COST_MULTIPLIER if war_new_worker_cost_multiplier is None else war_new_worker_cost_multiplier)))
        # Pacifism law: calendar year through which war *start* chance is reduced (inclusive).
        self._pacifism_until_year = None
        from government import Government
        import random
        import time
        # Use provided seed for reproducibility, else different seed every game
        if game_seed is not None:
            self._ai_rng_seed = int(game_seed) & 0xFFFFFFFF
        else:
            self._ai_rng_seed = (random.getrandbits(32) ^ int(time.time() * 1000)) & 0xFFFFFFFF
        start_year = getattr(timeline, 'initial_year', 1830)
        self.government = Government(start_year, len(players))
        # Next random AI spawn. Only used if random_ai_spawn_enabled.
        spawn_interval = self.random_ai_spawn_interval_years
        half = max(5, spawn_interval // 2)
        self._next_random_ai_spawn_year = start_year + random.randint(spawn_interval - half, spawn_interval + half) if random_ai_spawn_enabled else None
        # Law drafting: lawyers draft laws; occupied lawyers cannot fight lawsuits
        self._law_drafts = []  # list of (player_id, law_type, year_started)
        self._law_drafts_ready = []  # list of (player_id, law_type, target_player_id or None)
        self._law_attempts = {}  # (player_id, law_type) -> last_attempt_year

    def get_government(self):
        return getattr(self, 'government', None)

    def _get_player_available_lawyer_count(self, player) -> int:
        """Lawyers available for lawsuits (total minus those drafting laws)."""
        total = player.get_lawyer_count()
        drafts = getattr(self, '_law_drafts', [])
        drafting = sum((d[3] if len(d) > 3 else 1) for d in drafts if d[0] == player.get_player_id())
        return max(0, total - drafting)

    def get_players(self):
        return self.players
    
    def get_active_players(self):
        """Return only players who are not eliminated"""
        return [p for p in self.players if not p.is_eliminated]
    
    def eliminate_player(self, player: Player):
        """Eliminate a player from the game"""
        player.eliminate()
        for line_name in list(player.line_names):
            line = self.map.get_lines_by_name().get(line_name)
            if line:
                self.map.delete_line(line)
        player.line_names.clear()
        player.trainsets.clear()

    def add_player(self, player: Player):
        """Add a new player to the game (e.g. from random AI spawn)."""
        self.players.append(player)
        self.players_by_name[player.name] = player
        gov = self.get_government()
        if gov and hasattr(gov, 'add_player'):
            gov.add_player(player.get_player_id())

    def calc_players_by_name(self):
        players_by_name = dict()
        for player in self.players:
            players_by_name[player.name] = player
        return players_by_name

    def get_players_by_name(self):
        return self.players_by_name
    
    def get_marketplace(self):
        return self.marketplace

    def get_map(self):
        return self.map

    def get_timeline(self):
        return self.timeline
    
    def get_event_system(self):
        return self.event_system

    def increase_year(self, event_population_change: float = 0.0):
        self.timeline.advance()
        current_year = self.get_year()
        
        # Get event effects for population reduction
        event_effects = self.event_system.get_combined_effects(current_year)
        population_reduction = event_effects.get('population_reduction', 0.0)
        
        if hasattr(self.map, 'update_population_over_time'):
            population_change = event_population_change - population_reduction
            self.map.update_population_over_time(current_year, population_change)
        
        pass
    
    def calculate_build_time(self, build_type: str, build_data: dict, construction_workers: int) -> int:
        """Calculate build time in turns based on construction workers, elevation, and terrain
        More workers = faster building (minimum 1 turn)
        Underground, elevated, and water construction take longer"""
        if build_type == 'line':
            import math
            points = build_data['points']
            elevation_levels = build_data.get('elevation_levels', None)
            if elevation_levels is None:
                elevation_levels = ['street'] * max(1, len(points) - 1) if points else []
            elif len(elevation_levels) < len(points) - 1:
                elevation_levels = elevation_levels + ['street'] * (len(points) - 1 - len(elevation_levels))
            total_length = 0.0
            total_time_multiplier = 0.0
            segment_count = 0
            if len(points) > 1:
                game_map = self.map
                def is_point_in_water(x, y):
                    if game_map.is_nyc_proper:
                        from map_presets_nyc import NYC_EXCLUDED_POINTS, is_water_location
                        from map_presets_nyc import get_nyc_default_center_lat, get_nyc_default_center_lon
                        grid_x, grid_y = int(round(x)), int(round(y))
                        if (grid_x, grid_y) in NYC_EXCLUDED_POINTS:
                            return True
                        grid_size = max(game_map.width, game_map.height)
                        lat_km = (y - grid_size // 2)
                        lon_km = (x - grid_size // 2)
                        center_lat = game_map.map_center_lat if game_map.map_center_lat is not None else get_nyc_default_center_lat()
                        center_lon = game_map.map_center_lon if game_map.map_center_lon is not None else get_nyc_default_center_lon()
                        lat = center_lat + (lat_km / 111.0)
                        lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
                        if lon_deg_per_km == 0:
                            lon_deg_per_km = 111.0
                        lon = center_lon + (lon_km / lon_deg_per_km)
                        return is_water_location(lat, lon, game_map.osm_integration)
                    elif game_map.osm_integration:
                        grid_size = max(game_map.width, game_map.height)
                        center_lat = game_map.map_center_lat or 0.0
                        center_lon = game_map.map_center_lon or 0.0
                        lat, lon = game_map.osm_integration.convert_grid_to_latlon(x, y, center_lat, center_lon, grid_size)
                        return game_map.osm_integration.is_water_at_latlon(lat, lon)
                    return False
                
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i+1]
                    segment_length = math.hypot(x2 - x1, y2 - y1)
                    total_length += segment_length
                    
                    # Time multipliers based on elevation and terrain
                    elevation = elevation_levels[i] if i < len(elevation_levels) else 'street'
                    segment_in_water = is_point_in_water(x1, y1) or is_point_in_water(x2, y2)
                    
                    time_mult = 1.0
                    if elevation == 'above_ground':
                        time_mult = 1.3
                    elif elevation == 'underground':
                        time_mult = 1.5
                    
                    if segment_in_water:
                        time_mult += 0.5 
                    
                    total_time_multiplier += time_mult * segment_length
                    segment_count += 1
            
            if total_length > 0:
                avg_time_multiplier = total_time_multiplier / total_length
            else:
                avg_time_multiplier = 1.0
            
            base_turns = max(1, int(total_length * avg_time_multiplier) + 1)
        elif build_type == 'station':
            num_lines = len(build_data.get('line_names', []))
            base_turns = max(1, num_lines)
        else:
            base_turns = 2
        
        reduction_factor = min(construction_workers * 0.2, 0.8)
        adjusted_turns = max(1, int(base_turns * (1.0 - reduction_factor)))
        
        return adjusted_turns

    def decrease_year(self):
        self.timeline.go_back()

    def reset_year(self):
        self.timeline.reset()

    def get_year(self):
        return self.timeline.get_year()

    def start_game(self):
        self.is_playing = True

    def end_game(self):
        self.is_playing = False

    def create_line(self, name, player, points):
        cy = int(self.get_year())
        tl = self.timeline
        sy = int(getattr(tl, 'initial_year', None) or 1830)
        ey = int(getattr(tl, 'end_year', None) or 2050)
        from inflation_utils import get_timeline_fare_target
        fare = get_timeline_fare_target(cy, start_year=sy, end_year=max(sy + 1, ey))
        the_line = Line(name=name, object_id=None, route=points, owner=player, vehicles=[], fare=fare)
        return the_line

    def buy_line(self, name, player, route):
        the_line = self.create_line(name, player, route)
        self.map.add_line(the_line)

    def __bool__(self):
        return self.is_playing
    
    def calc_maitnance_by_player(self, player: Player):
        from operations import calc_line_maintenance_with_off_street_surcharge
        total_maitnance = 0
        current_year = self.get_year()
        for line_name in player.line_names:
            line = self.map.get_lines_by_name().get(line_name)
            if line:
                total_maitnance += calc_line_maintenance_with_off_street_surcharge(
                    self.map, line, current_year,
                )
        return total_maitnance
    
    def calc_trainset_maintenance(self, player: Player):
        """Calculate total trainset maintenance costs for a player"""
        total = 0
        current_year = self.get_year()
        for trainset in player.get_trainsets():
            maint_cost = trainset.get_maitnance_costs(current_year)
            if maint_cost is not None:
                total += maint_cost
        return int(total) if total else 0
    
    def calc_resource_costs(self, player: Player, current_year: int = None):
        """Calculate resource costs (fuel) for a player's trainsets using historical pricing.
        Costs update annually based on current_year. Electricity is never free."""
        # Get current year if not provided
        if current_year is None:
            current_year = self.get_year()
        
        # Base costs per unit (these are multiplied by year adjustments)
        base_costs = {
            'Coal': 50,
            'Wood': 30,
            'Oil': 80,
            'Electricity': 50,  
            'Gasoline': 90, 
        }
        
        # Year-based adjustments (same logic as ui.py get_resource_costs)
        year_adjustments = {}
        
        # Coal: cheaper early on, more expensive later
        if current_year < 1850:
            year_adjustments['Coal'] = 0.8  # 20% cheaper
        elif current_year < 1900:
            year_adjustments['Coal'] = 1.0  # Base price
        elif current_year < 1950:
            year_adjustments['Coal'] = 1.2  # 20% increase
        else:
            year_adjustments['Coal'] = 1.5  # 50% increase (declining use)
        
        # Wood: cheaper early, more expensive later (deforestation concerns)
        if current_year < 1850:
            year_adjustments['Wood'] = 0.7  
        elif current_year < 1900:
            year_adjustments['Wood'] = 1.0  
        else:
            year_adjustments['Wood'] = 1.3  
        
        # Oil: more expensive over time
        if current_year < 1900:
            year_adjustments['Oil'] = 0.8  # 20% cheaper (early oil era)
        elif current_year < 1970:
            year_adjustments['Oil'] = 1.0  # Base price
        else:
            year_adjustments['Oil'] = 1.5  # 50% increase (oil crisis era)
        
        # Electricity: ALWAYS costs money (never free)
        if current_year < 1880:
            year_adjustments['Electricity'] = 0.3  # Early experimental (cheap but not free)
        elif current_year < 1920:
            year_adjustments['Electricity'] = 0.7  # Early commercialization
        elif current_year < 1950:
            year_adjustments['Electricity'] = 1.0  # Base price
        else:
            year_adjustments['Electricity'] = 1.2  # 20% increase
        
        # Gasoline: More expensive early on, then becomes standard
        if current_year < 1900:
            year_adjustments['Gasoline'] = 1.5  # Early gasoline era - expensive
        elif current_year < 1920:
            year_adjustments['Gasoline'] = 1.2  # Early mass production
        elif current_year < 1970:
            year_adjustments['Gasoline'] = 1.0  # Base price (golden age of automobiles)
        elif current_year < 2000:
            year_adjustments['Gasoline'] = 1.3  # Oil crisis era - more expensive
        else:
            year_adjustments['Gasoline'] = 1.5  # Modern era - environmental concerns
        
        # Calculate final resource costs
        resource_costs_table = {}
        for resource, base_cost in base_costs.items():
            adjustment = year_adjustments.get(resource, 1.0)
            resource_costs_table[resource] = int(base_cost * adjustment)
        
        total = 0
        for trainset in player.get_trainsets():
            try:
                loco = getattr(trainset, 'locomotive', None)
                if loco and hasattr(loco, 'fuel') and loco.fuel:
                    fuel_name = getattr(loco.fuel, 'name', None)
                    if fuel_name and fuel_name in resource_costs_table:
                        # Get per-unit cost for this fuel type
                        fuel_cost = resource_costs_table.get(fuel_name, 0)
                        total += fuel_cost
            except (AttributeError, TypeError):
                pass
        return int(total) if total else 0
    
    def calc_taxes(self, player: Player, current_year: int, player_profit: int = 0):
        """Calculate taxes for a player. Government composition affects tax multiplier.
        player_profit: optional pre-calculated profit for income tax (taxed on positive profit only)."""
        from inflation_utils import get_inflation_factor
        BASE_POLL_TAX = 100
        BASE_PROPERTY_TAX_PER_LINE = 100
        BASE_PROPERTY_TAX_PER_TRAINSET = 20
        INCOME_TAX_RATE = 0.10  # 10% on profits

        inflation_factor = get_inflation_factor(current_year, max_factor=5.5)
        poll_tax = int(BASE_POLL_TAX * inflation_factor)
        num_lines = len(player.get_line_names()) if player.get_line_names() else 0
        num_trainsets = len(player.get_trainsets()) if player.get_trainsets() else 0
        property_tax = int((BASE_PROPERTY_TAX_PER_LINE * num_lines + BASE_PROPERTY_TAX_PER_TRAINSET * num_trainsets) * inflation_factor)

        player_id = player.get_player_id()
        gov = getattr(self, 'government', None)
        if gov:
            tax_mult = gov.get_tax_multiplier(player_id)
            poll_tax = int(poll_tax * tax_mult)
            property_tax = int(property_tax * tax_mult)

        # Annual government laws: tax bills (favorable/unfavorable to agencies)
        laws = getattr(self, '_government_laws', {})
        tax_modifiers = laws.get('tax_modifiers', {})
        if player_id in tax_modifiers:
            mod = tax_modifiers[player_id]
            poll_tax = int(poll_tax * mod)
            property_tax = int(property_tax * mod)

        # Income tax on profits (10%, only on positive profit) - same modifiers as other taxes
        income_tax = max(0, int(player_profit * INCOME_TAX_RATE))
        if gov:
            income_tax = int(income_tax * gov.get_tax_multiplier(player_id))
        if player_id in tax_modifiers:
            income_tax = int(income_tax * tax_modifiers[player_id])

        # Lobby tax break: one-time half taxes for this player this year
        if getattr(self, '_lobby_tax_break_year', None) == current_year and getattr(self, '_lobby_tax_break_player', None) == player:
            poll_tax = poll_tax // 2
            property_tax = property_tax // 2
            income_tax = income_tax // 2
            setattr(self, '_lobby_tax_break_player', None)
            setattr(self, '_lobby_tax_break_year', None)
        # Lobby audit: one-time double taxes for targeted player(s) this year (cleared in apply_lobby_effects)
        audit_targets = getattr(self, '_lobby_audit_targets', None) or (
            [getattr(self, '_lobby_audit_target', None)] if getattr(self, '_lobby_audit_target', None) is not None else [])
        if getattr(self, '_lobby_audit_year', None) == current_year and player in audit_targets:
            poll_tax = poll_tax * 2
            property_tax = property_tax * 2
            income_tax = income_tax * 2

        # Permanent laws: reduce property tax by 15% (lasts forever)
        permanent_reduction = getattr(self, '_permanent_property_tax_reduction', set())
        if player_id in permanent_reduction:
            property_tax = int(property_tax * 0.85)

        # Permanent laws: increase property tax by 15% (lasts forever)
        permanent_increase = getattr(self, '_permanent_property_tax_increase', set())
        if player_id in permanent_increase:
            property_tax = int(property_tax * 1.15)

        return poll_tax, property_tax, income_tax
    
    def calc_worker_costs(self, player: Player, current_year: int):
        """Calculate worker costs and advance worker rentals. Returns (cost, is_on_strike)
        Eliminated players: return (0, False) and do not advance strike state."""
        from worker import ENGINEER, CONSTRUCTION_WORKER
        if player.is_player_eliminated():
            return 0, False
        worker_costs = 0
        is_on_strike = False
        engineer_rental = player.get_worker_rental(ENGINEER.name)
        
        if engineer_rental:
            engineer_rental.advance_turn(current_year)
            is_on_strike = engineer_rental.is_on_strike()
            worker_costs += engineer_rental.get_yearly_cost(current_year)
        
        construction_rental = player.get_worker_rental(CONSTRUCTION_WORKER.name)
        if construction_rental:
            construction_rental.advance_turn(current_year)
            worker_costs += construction_rental.get_yearly_cost(current_year)
        
        return worker_costs, is_on_strike
    
    def get_strike_warnings(self, current_year: int):
        """Get strike warnings for all players before advancing the year"""
        warnings = []
        for player in self.players:
            if player.is_player_eliminated():
                continue
            
            worker_types = [ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER]
            for worker_type in worker_types:
                worker_rental = player.get_worker_rental(worker_type.name)
                if worker_rental and worker_rental.will_strike_next_turn(current_year):
                    current_multiplier = worker_rental.worker_type.get_salary_multiplier()
                    warnings.append({
                        'player': player,
                        'message': (
                            f"{player.get_name()}: {worker_rental.quantity} {worker_type.name}s are threatening to strike!\n"
                            f"Current salary: {current_multiplier:.2f}x, Demanded: {worker_rental.demanded_multiplier:.2f}x\n"
                            f"Increase their salary in the Workers tab to prevent a strike."
                        )
                    })
        return warnings
    
    def process_end_of_year(self, current_year: int):
        """Process end of year operations: replenish supplies, generate excuses, etc."""
        import random
        from worker import ENGINEER, LAWYER
        from lawsuit import LAWSUIT_EXCUSES
        
        base_replenishment = random.randint(2, 5)
        replenishment = int(base_replenishment * self.worker_replenishment_rate)
        self.marketplace.add_worker_supply(ENGINEER.name, replenishment)
        
        base_construction_replenishment = random.randint(1, 3)
        construction_replenishment = int(base_construction_replenishment * self.worker_replenishment_rate)
        self.marketplace.add_worker_supply(CONSTRUCTION_WORKER.name, construction_replenishment)
      
        for player in self.players:
            if not player.is_player_eliminated() and random.random() < 0.1:
                excuse = random.choice(LAWSUIT_EXCUSES)
                player.add_lawsuit_excuse(excuse)
        
        base_chance = 0.10
        max_chance = 0.90
        year_range = 2050 - 1830
        # Make refresh faster later in game - exponential increase after 1900
        if current_year >= 1900:
            # After 1900, refresh chance increases exponentially
            years_since_1900 = current_year - 1900
            # Exponential growth: base_chance * (1.05 ^ years_since_1900)
            # This makes refresh much more frequent in later years
            exponential_factor = 1.05 ** (years_since_1900 / 10.0)  # Every 10 years = 1.05x multiplier
            replenishment_chance = base_chance * exponential_factor
            # Cap at 95% chance (almost always refresh)
            replenishment_chance = min(0.95, replenishment_chance)
        else:
            # Before 1900, use linear progression
            replenishment_chance = base_chance + (current_year - 1830) / year_range * (max_chance - base_chance)
            replenishment_chance = min(max_chance, max(base_chance, replenishment_chance))
        
        if random.random() < replenishment_chance:
            self.marketplace.generate_trainsets_based_on_discoveries(current_year)
    
    def check_and_unassign_trainsets_for_engineers(self, player: Player, current_year: int):
        """Check if player has enough engineers and unassign trainsets if needed"""
        from worker import ENGINEER
        engineer_rental = player.get_worker_rental(ENGINEER.name)
        
        if engineer_rental and not engineer_rental.is_on_strike():
            assigned_count = player.get_assigned_trainsets_count()
            engineer_count = player.get_engineer_count()
            
            if assigned_count > engineer_count:
                # Unassign excess trainsets (keep the most recently assigned ones)
                assigned_trainsets = player.get_assigned_trainsets()
                excess = assigned_count - engineer_count
                for i in range(excess):
                    if i < len(assigned_trainsets):
                        assigned_trainsets[i].assign_to_line(None)
        elif engineer_rental and engineer_rental.is_on_strike():
            # Unassign all trainsets if workers are on strike
            assigned_trainsets = player.get_assigned_trainsets()
            for ts in assigned_trainsets:
                ts.assign_to_line(None)
    
    def check_and_eliminate_player(
        self, player: Player, balance_before: int = None, net_change: int = None,
    ):
        """Eliminate only when cash is strictly negative after a net change. Exactly $0 is solvent.
        When balance_before and net_change are given, use their sum instead of player.money
        (avoids elimination on intermediate negatives during multi-step transactions)."""
        if balance_before is not None and net_change is not None:
            final_balance = int(balance_before) + int(net_change)
        else:
            final_balance = player.get_money()
        if final_balance < 0 and not player.is_player_eliminated():
            self.eliminate_player(player)
            return True
        return False
    
    def get_newly_available_vehicles(self, old_year: int, new_year: int):
        """Return list of (vehicle_name, year_introduced) for vehicles that became available between old_year and new_year"""
        from vehicle_presets import get_vehicle_registry
        
        registry = get_vehicle_registry()
        newly_available = []
        for vehicles_by_year in (
            registry.get_locomotives_by_year(),
            registry.get_railcars_by_year(),
            registry.get_ferries_by_year(),
            registry.get_buses_by_year(),
        ):
            for vehicle, year in vehicles_by_year:
                if year and old_year < year <= new_year:
                    newly_available.append((vehicle.name, year))
        
        return newly_available
    
    def _lawsuit_protection_skip_player_id(self, current_year: int):
        """Player id exempt from agency lawsuits this year (lobby pass-law / area protection)."""
        until = getattr(self, '_lobby_pass_law_until_year', None)
        if until is not None and current_year > until:
            setattr(self, '_lobby_pass_law_player', None)
            setattr(self, '_lobby_pass_law_until_year', None)
            return None
        if until is not None and current_year <= until:
            skip_player = getattr(self, '_lobby_pass_law_player', None)
            if skip_player is not None:
                return skip_player.get_player_id()
        return None

    def prosecute_excuse_with_agency(self, agency, player, excuse, current_year: int):
        """Resolve one agency lawsuit for a specific excuse. Returns result dict."""
        player_id = player.get_player_id()
        laws = getattr(self, '_government_laws', {})
        lawsuit_target = laws.get('lawsuit_target', [])
        agency_lawyers = agency.get_lawyers()
        player_lawyers = self._get_player_available_lawyer_count(player)
        if player_id in lawsuit_target:
            agency_lawyers += 2
        if agency_lawyers > player_lawyers:
            penalty = 1000 * (current_year - 1830) // 100
            player.deduct_money(penalty)
            player.remove_lawsuit_excuse(excuse)
            agency.reset_lawyers()
            setattr(self, '_last_lawsuit_loser_player_id', player_id)
            return {
                'agency': agency.name,
                'player': player.get_name(),
                'excuse': excuse.cause,
                'penalty': penalty,
                'agency_won': True,
            }
        player.remove_lawsuit_excuse(excuse)
        agency.reset_lawyers()
        setattr(self, '_last_lawsuit_loser_player_id', None)
        return {
            'agency': agency.name,
            'player': player.get_name(),
            'excuse': excuse.cause,
            'agency_won': False,
        }

    def check_agency_lawsuits(self, current_year: int):
        """Check for agency lawsuits (ruleset-defined agencies, e.g. NIMBYs, EPA)."""
        from agency import get_agencies
        agencies = get_agencies()
        setattr(self, '_last_lawsuit_loser_player_id', None)

        skip_player_id = self._lawsuit_protection_skip_player_id(current_year)

        laws = getattr(self, '_government_laws', {})
        lawsuit_protection = laws.get('lawsuit_protection', [])

        for agency in agencies:
            if not agency.is_active(current_year):
                continue

            for player in self.get_active_players():
                if skip_player_id is not None and player.get_player_id() == skip_player_id:
                    continue
                player_id = player.get_player_id()
                if player_id in lawsuit_protection:
                    continue
                excuses = player.get_lawsuit_excuses()
                available_excuses = agency.get_available_excuses(current_year)

                for excuse in excuses:
                    if excuse in available_excuses:
                        return self.prosecute_excuse_with_agency(agency, player, excuse, current_year)
        return None

    def apply_lobby_effects(self, current_year: int) -> None:
        """Apply multi-year lobby effects (e.g. government attack penalty)."""
        if getattr(self, '_lobby_gov_attack_year', None) == current_year:
            targets = getattr(self, '_lobby_gov_attack_targets', None) or (
                [getattr(self, '_lobby_gov_attack_target', None)] if getattr(self, '_lobby_gov_attack_target', None) is not None else [])
            penalty = getattr(self, '_lobby_gov_attack_penalty', 0)
            for target in targets:
                if target is not None and penalty and not target.is_player_eliminated():
                    target.deduct_money(penalty)
            setattr(self, '_lobby_gov_attack_targets', None)
            setattr(self, '_lobby_gov_attack_target', None)
            setattr(self, '_lobby_gov_attack_penalty', None)
            setattr(self, '_lobby_gov_attack_year', None)
        # Clear lobby audit after taxes applied (one-time effect for this year)
        if getattr(self, '_lobby_audit_year', None) == current_year:
            setattr(self, '_lobby_audit_targets', None)
            setattr(self, '_lobby_audit_target', None)
            setattr(self, '_lobby_audit_year', None)

    def __setstate__(self, state):
        self.__dict__.update(state)
        if not isinstance(getattr(self, 'build_queue', None), list):
            self.build_queue = []
    
    def __repr__(self):
        return f"Game(year={self.get_year()}, players={len(self.players)}, is_playing={self.is_playing})"