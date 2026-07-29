"""Operations mixin: RidershipMapMixin."""
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


class RidershipMapMixin:
    def get_ridership_data_for_year(self, current_year: int) -> dict:
        """Get ridership data for a year, using cache if available"""
        if self._cached_ridership_data is not None and self._cached_ridership_year == current_year:
            return self._cached_ridership_data
        ridership_data = self.calculate_ridership_for_year(current_year)
        self._cached_ridership_data = ridership_data
        self._cached_ridership_year = current_year
        return ridership_data
    
    def invalidate_ridership_cache(self):
        """Invalidate cached ridership data (call when year advances or lines change)"""
        self._cached_ridership_data = None
        self._cached_ridership_year = None
    
    def calculate_ridership_for_year(self, current_year: int) -> dict:
        """Calculate ridership for the year based on new system:
        Each person takes a trip to another point with probability based on population.
        Returns dict mapping line_name -> passenger_count.
        Optimized to complete quickly (< 1 second for typical maps)."""
        import random
        import time
        
        line_passengers = {}  # line_name -> number of passengers
        game_map = self.game.get_map()
        
        # Skip if no stations on map - no routes/profits to calculate
        if not hasattr(game_map, 'stations') or not game_map.stations or len(game_map.stations) == 0:
            return line_passengers
        
        # Get all population points, excluding points with 0 population (water/excluded areas)
        # This ensures excluded points don't contribute to profit calculations
        population_points = [(x, y) for (x, y), pop in game_map.population.items() if pop > 0]
        if len(population_points) < 2:
            return line_passengers
        # Skip ridership sim on huge maps to prevent freeze; profit floor will be used per line
        if len(population_points) > 15000:
            return line_passengers
        
        # Cap population points for ridership sampling to avoid freeze on very large maps (e.g. NYC 50k+)
        MAX_POPULATION_POINTS = 3000
        if len(population_points) > MAX_POPULATION_POINTS:
            import random as _r
            population_points = _r.sample(population_points, MAX_POPULATION_POINTS)
        
        # Calculate total population
        total_population = sum(game_map.population.values())
        if total_population == 0:
            return line_passengers
        
        # Build destination weights lazily (on demand) to avoid O(n^2) freeze on large maps.
        # Precomputing for all origins caused freezes when population_points had 10k+ entries.
        destination_weights_cache = {}
        
        def get_destination_weights(origin):
            if origin not in destination_weights_cache:
                weights = [game_map.population[dest] for dest in population_points if dest != origin]
                destinations = [dest for dest in population_points if dest != origin]
                total_weight = sum(weights)
                destination_weights_cache[origin] = (destinations, weights, total_weight)
            return destination_weights_cache[origin]
        
        # Optimize sample size and enforce time budget to keep End Year responsive
        RIDERSHIP_TIME_BUDGET_SEC = 18.0
        map_size = game_map.width * game_map.height
        if map_size < 2500:  # < 50x50
            sample_size = min(total_population, 1800)
        elif map_size < 10000:  # < 100x100
            sample_size = min(total_population, 960)
        else:  # Large maps (e.g. NYC)
            sample_size = min(total_population, 480)
        
        start_time = time.time()
        self._route_stations_cache = {}
        # Pre-update line quality once per year (avoids per-trip checks in loop)
        for line in (game_map.get_lines_by_name() or {}).values():
            if not hasattr(line, '_quality_updated_year') or line._quality_updated_year != current_year:
                line.update_quality()
                line._quality_updated_year = current_year
        # Precompute cumulative origin weights once (O(n)) to avoid O(n) per trip on large maps
        origin_weights = [game_map.population[p] for p in population_points]
        total_origin_weight = sum(origin_weights)
        if total_origin_weight <= 0:
            return line_passengers
        origin_cumulative = []
        cum = 0
        for w in origin_weights:
            cum += w
            origin_cumulative.append(cum)
        
        for trip_idx in range(sample_size):
            if time.time() - start_time > RIDERSHIP_TIME_BUDGET_SEC:
                sample_size = trip_idx or 1
                break
            if trip_idx > 0 and trip_idx % 360 == 0:
                time.sleep(0.01)  # Yield GIL occasionally so main thread can update timer
            # Randomly select an origin weighted by population (binary search for large n)
            rand_origin = random.random() * total_origin_weight
            lo, hi = 0, len(origin_cumulative) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if rand_origin <= origin_cumulative[mid]:
                    hi = mid
                else:
                    lo = mid + 1
            origin = population_points[lo]
            
            # Get destination weights for this origin (lazy - only for sampled origins)
            destinations, weights, total_weight = get_destination_weights(origin)
            if not destinations or total_weight == 0:
                continue
            
            # Choose destination based on weighted probability
            rand_val = random.random() * total_weight
            cumulative = 0
            destination = destinations[0]
            for i, weight in enumerate(weights):
                cumulative += weight
                if rand_val <= cumulative:
                    destination = destinations[i]
                    break
            
            route = self.find_fastest_route(origin, destination)
            
            if route['method'] in ['train', 'train_transfer'] and route.get('lines'):
                for line in route['lines']:
                    line_name = line.name
                    quality_multiplier = line.quality
                    if random.random() < (0.5 + (quality_multiplier - 1.0) * 0.15):
                        line_passengers[line_name] = line_passengers.get(line_name, 0) + 1
        
        # Scale up results if we sampled
        if sample_size < total_population:
            scale_factor = total_population / sample_size
            line_passengers = {k: int(v * scale_factor) for k, v in line_passengers.items()}
        
        self._route_stations_cache = None
        return line_passengers
    
    def calculate_line_profit_with_ridership(self, line: Line, current_year: int, ridership_data: dict) -> int:
        """Calculate profit for a line based on ridership data.
        This replaces the old calc_profit method with the new trip-based system.
        Lines with incomplete segments do not generate profit."""
        total_profit = 0
        
        # Check if line has incomplete segments in build queue
        # Only block profit when the line does not exist on the map yet (initial build still in progress).
        # Once the line exists on the map (including after first segment), allow profit if it has 2+ stations.
        line_name = line.name
        has_incomplete_segments = False
        for build in self.game.build_queue:
            if build['type'] == 'line' and build['data'].get('name') == line_name:
                existing_line = self.game.get_map().get_lines_by_name().get(line_name)
                if existing_line is None:
                    has_incomplete_segments = True
                break
        
        if has_incomplete_segments:
            line.profit = 0
            return 0
        
        if len(line.stations) == 0:
            line.profit = 0
            return 0
        
        # Check if engineers are on strike
        try:
            if hasattr(line, 'owner') and line.owner:
                from worker import ENGINEER
                engineer_rental = line.owner.get_worker_rental(ENGINEER.name)
                if engineer_rental and engineer_rental.is_on_strike():
                    line.profit = 0
                    return 0
        except (AttributeError, ImportError):
            pass
        
        # Get number of passengers for this line
        passengers_per_year = ridership_data.get(line.name, 0)
        
        if not line.vehicles or len(line.vehicles) == 0:
            # No trainset assigned yet: give a small profit floor so players/AIs get income the first year after building
            stations_list = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
            if len(stations_list) >= 2 and not has_incomplete_segments:
                floor = self.inflate_economic_value(400, max_factor=5.5)
                line.profit = max(0, floor)
                return line.profit
            line.profit = 0
            return 0
        
        from inflation_utils import get_nominal_fare_cap

        sy = self.get_timeline_start_year()
        ey = self.get_timeline_end_year()
        _, max_acceptable_fare = get_nominal_fare_cap(
            int(current_year), start_year=sy, end_year=ey,
        )
        effective_fare = min(line.fare, max_acceptable_fare)
        
        # Calculate total capacity across all trainsets
        total_capacity_per_trip = 0
        for vehicle in line.vehicles:
            if vehicle and hasattr(vehicle, 'get_capacity'):
                total_capacity_per_trip += vehicle.get_capacity()
        
        if total_capacity_per_trip == 0:
            line.profit = 0
            return 0
        # When ridership sim returns 0 (sparse population, sampling), use minimum floor for completed lines
        if passengers_per_year == 0:
            stations_list = line.get_stations() if hasattr(line, 'get_stations') else (getattr(line, 'stations', None) or [])
            if len(stations_list) >= 2:
                passengers_per_year = max(
                    self.inflate_economic_value(200, max_factor=5.5),
                    int(total_capacity_per_trip * 0.25),
                )
        
        # Calculate how many trips can be made per year
        # Use the fastest trainset to determine service frequency
        fastest_trainset = None
        fastest_speed = 0
        for vehicle in line.vehicles:
            if vehicle and hasattr(vehicle, 'locomotive') and vehicle.locomotive:
                if vehicle.locomotive.average_speed > fastest_speed:
                    fastest_speed = vehicle.locomotive.average_speed
                    fastest_trainset = vehicle
        
        if not fastest_trainset:
            line.profit = 0
            return 0
        
        service_freq = line.service_frequency_per_year(trainset_avg_speed=fastest_speed)
        if service_freq == 0:
            line.profit = 0
            return 0
        
        max_passengers_served = service_freq * total_capacity_per_trip
        
        actual_passengers = min(passengers_per_year, max_passengers_served)
        
        line.update_quality()
        quality_multiplier = 1.0 + ((line.quality - 1.0) * 0.2)
        profit_multiplier = max(0.25, min(10.0, getattr(self.game, 'profit_margin', 1.0)))
        total_profit = actual_passengers * effective_fare * profit_multiplier * quality_multiplier
        
        # Apply event effects
        event_profit_multiplier = 1.0
        natural_disaster_reduction = 0.0
        try:
            if hasattr(line, 'owner') and line.owner:
                game = line.owner.__dict__.get('_game', None)
                if game and hasattr(game, 'event_system'):
                    event_effects = game.event_system.get_combined_effects(current_year)
                    event_profit_multiplier = event_effects.get('profit_multiplier', 1.0)
                    natural_disaster_reduction = 0.0
                    for event in game.event_system.get_active_events(current_year):
                        if hasattr(event, 'disaster_type'):
                            years_since = current_year - event.year
                            recovery_years = event.get_recovery_years() if hasattr(event, 'get_recovery_years') else 2
                            if 0 <= years_since < recovery_years:
                                effect = event.get_effect(current_year)
                                if effect and 'profit_reduction' in effect:
                                    natural_disaster_reduction = max(natural_disaster_reduction, effect['profit_reduction'])
        except:
            pass
        
        # Apply natural disaster profit reduction
        if natural_disaster_reduction > 0.0:
            total_profit = total_profit * (1.0 - natural_disaster_reduction)
        
        total_profit = total_profit * event_profit_multiplier
        
        line.profit = int(total_profit)
        return line.profit
    
    def apply_natural_disaster_damage(self, disaster_event, current_year: int):
        """Apply natural disaster damage to infrastructure (lines and stations)
        This method contains all game logic for disaster damage."""
        import random
        
        # Get disaster effect
        effect = disaster_event.get_effect(current_year)
        if not effect or not effect.get('natural_disaster'):
            return
        
        damage_percent = effect.get('infrastructure_damage', 0.3)
        disaster_type = effect.get('disaster_type', 'Unknown')
        
        # Track damaged infrastructure
        damaged_lines = []
        damaged_stations = []
        
        # Damage lines for all players
        all_lines = self.get_map_lines()
        all_line_list = list(all_lines.values())
        
        if all_line_list:
            # Calculate number of lines to damage
            num_lines_to_damage = max(1, int(len(all_line_list) * damage_percent))
            # Randomly select lines to damage
            lines_to_damage = random.sample(all_line_list, min(num_lines_to_damage, len(all_line_list)))
            
            for line in lines_to_damage:
                # Damage line by adding damaged segments back to build queue
                route = self.get_line_route(line)
                if not route or len(route) < 2:
                    continue
                
                owner = self.get_line_owner(line)
                if not owner:
                    continue
                
                # Determine how many segments to damage (20-60% of route)
                num_segments = len(route) - 1
                segments_to_damage = max(1, int(num_segments * random.uniform(0.2, 0.6)))
                
                # Randomly select which segments to damage
                segment_indices = random.sample(range(num_segments), min(segments_to_damage, num_segments))
                
                # Add damaged segments to build queue
                from line import Bus, Ferry
                is_bus = isinstance(line, Bus)
                is_ferry = isinstance(line, Ferry)
                line_elev = getattr(line, 'elevation_levels', None)
                game_map = self.game.get_map()
                preset_name = getattr(game_map, 'map_preset_name', None)
                for segment_idx in segment_indices:
                    segment_points = [route[segment_idx], route[segment_idx + 1]]
                    seg_elev = [line_elev[segment_idx]] if line_elev and segment_idx < len(line_elev) else (['street'] if is_bus else (['water'] if is_ferry else ['above_ground']))
                    # Use above_ground for segments in water when street would fail (prevents ValueError)
                    if seg_elev == ['street'] and self._segment_in_water_for_disaster(segment_points[0], segment_points[1], game_map, preset_name):
                        seg_elev = ['above_ground']
                    # Calculate repair cost (50% of original construction cost)
                    segment_cost_info = self.calculate_line_cost(
                        segment_points,
                        elevation_levels=seg_elev,
                        is_bus_line=is_bus,
                        is_ferry_line=is_ferry
                    )
                    repair_cost = int(segment_cost_info['cost'] * 0.5)
                    
                    # Check if player can afford repair, if not they'll need to pay later
                    if self.get_player_money(owner) >= repair_cost:
                        self.deduct_player_money(owner, repair_cost, reason='Disaster repair')
                    else:
                        repair_cost = 0
                    
                    # Add segment to build queue as repair job
                    segment_data = {
                        'name': line.name,
                        'points': segment_points,
                        'segment_index': segment_idx,
                        'total_segments': num_segments,
                        'disaster_repair': True,
                        'disaster_type': disaster_type,
                        'repair_cost': repair_cost,
                        'elevation_levels': seg_elev,
                        'is_bus_line': is_bus,
                        'is_ferry_line': is_ferry,
                    }
                    
                    # Get construction workers for repair
                    construction_workers = self.get_player_construction_worker_count(owner)
                    if construction_workers < 1:
                        construction_workers = 0
                    
                    self.add_to_build_queue('line', segment_data, owner, construction_workers)
                
                if not hasattr(line, '_disaster_damaged'):
                    line._disaster_damaged = True
                    line._disaster_damage_year = current_year
                    line._disaster_type = disaster_type
                    damaged_lines.append(line.name)
        
        # Damage stations
        game_map = self.game.get_map()
        if hasattr(game_map, 'stations') and game_map.stations:
            all_stations = game_map.stations
            num_stations_to_damage = max(1, int(len(all_stations) * damage_percent))
            stations_to_damage = random.sample(all_stations, min(num_stations_to_damage, len(all_stations)))
            
            for station in stations_to_damage:
                if not hasattr(station, '_disaster_damaged'):
                    station._disaster_damaged = True
                    station._disaster_damage_year = current_year
                    station._disaster_type = disaster_type
                    damaged_stations.append(station.name if hasattr(station, 'name') else 'Unnamed Station')
        
        # Store damage info for display
        if not hasattr(self, '_disaster_damage_history'):
            self._disaster_damage_history = []
        
        self._disaster_damage_history.append({
            'year': current_year,
            'disaster_type': disaster_type,
            'damaged_lines': damaged_lines,
            'damaged_stations': damaged_stations,
            'recovery_years': disaster_event.get_recovery_years()
        })
    
    def get_disaster_damage_history(self) -> list:
        """Get history of natural disaster damage"""
        if hasattr(self, '_disaster_damage_history'):
            return self._disaster_damage_history
        return []
    
    def format_natural_disaster_info(self, disaster_event, current_year: int) -> dict:
        """Format natural disaster information for UI display.
        This method contains all game logic for formatting disaster information."""
        disaster_effect = disaster_event.get_effect(current_year)
        if not disaster_effect or not disaster_effect.get('natural_disaster'):
            return None
        
        damage_percent = disaster_effect.get('infrastructure_damage', 0.3) * 100
        profit_reduction = disaster_effect.get('profit_reduction', 0.5) * 100
        recovery_years = disaster_event.get_recovery_years()
        
        # Get damage details from history
        damaged_lines = []
        damaged_stations = []
        damage_count = 0
        
        if hasattr(self, '_disaster_damage_history') and self._disaster_damage_history:
            latest_damage = self._disaster_damage_history[-1]
            if latest_damage['year'] == current_year:
                damaged_lines = latest_damage.get('damaged_lines', [])
                damaged_stations = latest_damage.get('damaged_stations', [])
                damage_count = len(damaged_lines) + len(damaged_stations)
        
        return {
            'name': disaster_event.name,
            'description': disaster_event.description,
            'disaster_type': disaster_effect.get('disaster_type', 'Unknown'),
            'damage_percent': damage_percent,
            'profit_reduction': profit_reduction,
            'recovery_years': recovery_years,
            'damaged_lines': damaged_lines,
            'damaged_stations': damaged_stations,
            'damage_count': damage_count
        }
    
    def get_public_ta_config(self) -> dict:
        game_map = self.get_map()
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name:
            from map_preset_loader import get_preset_public_ta_config
            config = get_preset_public_ta_config(preset_name)
            if config:
                return config
        from map_presets_standard import PUBLIC_TA_CONFIG
        return PUBLIC_TA_CONFIG
    
    def get_public_ta(self) -> PublicTA:
        return self.public_ta
    
    def establish_public_ta(self, current_year: int):
        if self.public_ta is not None:
            return
        
        config = self.get_public_ta_config()
        establishment_year = config.get('establishment_year', 1930)
        
        if current_year >= establishment_year:
            max_player_id = max([p.player_id for p in self.get_players()]) if self.get_players() else -1
            starting_money = config.get('starting_money', 100000)
            
            from vehicle_presets import get_vehicle_registry
            from vehicle import Trainset, Railcar
            registry = get_vehicle_registry()
            
            # Get earliest locomotive that CAN have railcars (exclude self-contained units like ferries, streetcars, etc.)
            all_locomotives = registry.get_locomotives_by_year()
            earliest_loco = None
            for loco, year in all_locomotives:
                if hasattr(loco, 'can_have_railcars') and loco.can_have_railcars:
                    earliest_loco = loco
                    break
            
            earliest_railcar = registry.get_earliest_railcar()
            
            starting_trainsets = []
            if earliest_loco and earliest_railcar:
                for _ in range(5):
                    cars = [Railcar("Standard Passenger", 0, earliest_railcar.capacity, earliest_railcar.maitnance_costs)]
                    starting_trainset = Trainset(locomotive=earliest_loco, cars=cars)
                    starting_trainsets.append(starting_trainset)
            
            self.public_ta = PublicTA(player_id=max_player_id + 1, money=starting_money, trainsets=starting_trainsets, name="Public Transit Authority")
            self.public_ta.established = True
            self.game.players.append(self.public_ta)
            self.game.players_by_name[self.public_ta.name] = self.public_ta
    
    def handle_public_ta(self, current_year: int):
        self.establish_public_ta(current_year)
        
        if self.public_ta is None or not self.public_ta.established:
            return
        
        config = self.get_public_ta_config()
        annual_funding = config.get('annual_funding', 50000)
        self.public_ta.add_money(annual_funding)
        
        self.public_ta_build_lines(current_year)
        self.public_ta_buy_lines_from_marketplace()
    
    def public_ta_build_lines(self, current_year: int):
        if self.public_ta is None or not self.public_ta.established:
            return
        
        if self.public_ta.get_money() < 10000:
            return
        
        game_map = self.get_map()
        map_width = game_map.width
        map_height = game_map.height
        
        import random
        if random.random() < 0.3:
            num_points = random.randint(3, 6)
            start_x = random.randint(5, max(5, map_width - 10))
            start_y = random.randint(5, max(5, map_height - 10))
            dx, dy = random.choice([(1, 0), (0, 1), (1, 1), (-1, 0), (0, -1), (-1, 1)])
            step = random.randint(2, 5)
            points = [(start_x, start_y)]
            x, y = float(start_x), float(start_y)
            for _ in range(num_points - 1):
                x = max(0, min(map_width, x + dx * step))
                y = max(0, min(map_height, y + dy * step))
                points.append((int(x), int(y)))

            if len(points) >= 2:
                base = f"Public Line {current_year}"
                line_name = base
                counter = 1
                while self.is_line_name_in_use(line_name):
                    line_name = f"{base}-{counter}"
                    counter += 1

                elevation_levels = ['street'] * max(0, len(points) - 1)
                cost_info = self.calculate_line_cost(points, elevation_levels=elevation_levels)
                if cost_info and cost_info.get('cost', 0) <= self.public_ta.get_money():
                    try:
                        self.create_line_complete(
                            line_name, points, self.public_ta, elevation_levels=elevation_levels
                        )
                    except ValueError:
                        pass
    
    def public_ta_buy_lines_from_marketplace(self):
        if self.public_ta is None or not self.public_ta.established:
            return
        
        marketplace = self.game.get_marketplace()
        available_lines = self.marketplace_get_available_lines()
        current_year = self.get_year()

        def _seller_priority(sale_item) -> int:
            seller = sale_item.get_seller() if hasattr(sale_item, 'get_seller') else ''
            if seller and seller not in ('Unowned', 'Manufacturer'):
                return 0
            return 1

        sorted_lines = sorted(
            (si for si in available_lines if isinstance(si, SaleItem)),
            key=lambda si: (_seller_priority(si), si.get_price(current_year=current_year)),
        )

        for sale_item in sorted_lines:
            line = sale_item.get_item()
            if not isinstance(line, Line):
                continue
            
            price = sale_item.get_price(current_year=current_year)
            if price > self.public_ta.get_money():
                continue
            self.public_ta.deduct_money(price)
            line.owner = self.public_ta
            self.public_ta.acquire_line(line)
            self.add_line_to_map(line)
            self.invalidate_ridership_cache()

            is_unowned = sale_item.get_seller() == "Unowned"
            if not is_unowned:
                seller = sale_item.get_salesman()
                if seller:
                    self.add_player_money(seller, price, reason='Public TA line sale')
                marketplace.remove_line(line)
    
    def get_stations_for_map_display(self, game_map, current_player=None) -> list[dict]:
        """Stations for OSM map overlay: completed map stations, line-attached stops, and queue jobs."""
        import math
        from map_preset_loader import get_map_bounds, get_default_center_lat, get_default_center_lon

        map_width_val = game_map.width if getattr(game_map, 'width', None) else 50
        map_height_val = game_map.height if getattr(game_map, 'height', None) else 50
        grid_size = max(map_width_val, map_height_val)
        if grid_size <= 0:
            return []

        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        center_lat = (
            game_map.map_center_lat
            if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None
            else get_default_center_lat(preset_name)
        )
        center_lon = (
            game_map.map_center_lon
            if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None
            else get_default_center_lon(preset_name)
        )
        use_preset_bounds = preset_bounds and all(
            k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')
        )
        if use_preset_bounds:
            min_lat, max_lat = preset_bounds['min_lat'], preset_bounds['max_lat']
            min_lon, max_lon = preset_bounds['min_lon'], preset_bounds['max_lon']
            preset_lat_scale = (max_lat - min_lat) / map_height_val if map_height_val > 0 else 0
            preset_lon_scale = (max_lon - min_lon) / map_width_val if map_width_val > 0 else 0
        else:
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat)) or 111.0
            min_lat, max_lat = center_lat - 0.5, center_lat + 0.5
            min_lon, max_lon = center_lon - 0.5, center_lon + 0.5

        def grid_to_lat_lon(x: float, y: float):
            if use_preset_bounds:
                lat = preset_bounds['min_lat'] + y * preset_lat_scale
                lon = preset_bounds['min_lon'] + x * preset_lon_scale
            else:
                lat_km = y - grid_size // 2
                lon_km = x - grid_size // 2
                lat = center_lat + (lat_km / 111.0)
                lon = center_lon + (lon_km / lon_deg_per_km)
            return lat, lon

        def can_close_station(line_names: list) -> bool:
            if not line_names or current_player is None:
                return True
            for ln in line_names:
                line = self.get_map_lines().get(ln)
                if line and self.get_line_owner(line) != current_player:
                    return False
            return True

        seen_keys = set()
        stations_data = []

        def append_station(x, y, name, under_construction, line_names):
            key = (round(float(x), 3), round(float(y), 3))
            if key in seen_keys:
                return
            lat, lon = grid_to_lat_lon(float(x), float(y))
            if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                return
            seen_keys.add(key)
            line_names = list(line_names or [])
            stations_data.append({
                'lat': lat,
                'lon': lon,
                'name': name or 'Station',
                'under_construction': bool(under_construction),
                'can_close': can_close_station(line_names),
                'line_names': line_names,
            })

        for build in self.game.build_queue or []:
            if build.get('type') != 'station':
                continue
            build_data = build.get('data') or {}
            x, y = build_data.get('x'), build_data.get('y')
            if x is None or y is None:
                continue
            append_station(
                x, y,
                build_data.get('name', 'Station'),
                True,
                build_data.get('line_names', []),
            )

        for station in getattr(game_map, 'stations', []) or []:
            x = station.get_x() if hasattr(station, 'get_x') else getattr(station, 'x', None)
            y = station.get_y() if hasattr(station, 'get_y') else getattr(station, 'y', None)
            if x is None or y is None:
                continue
            line_names = (
                station.get_line_names()
                if hasattr(station, 'get_line_names')
                else getattr(station, 'line_names', [])
            )
            name = station.get_name() if hasattr(station, 'get_name') else getattr(station, 'name', 'Station')
            append_station(x, y, name, False, line_names)

        lines = game_map.lines if hasattr(game_map, 'lines') and game_map.lines else {}
        for line in lines.values():
            line_stations = line.get_stations() if hasattr(line, 'get_stations') else getattr(line, 'stations', [])
            for station in line_stations or []:
                x = station.get_x() if hasattr(station, 'get_x') else getattr(station, 'x', None)
                y = station.get_y() if hasattr(station, 'get_y') else getattr(station, 'y', None)
                if x is None or y is None:
                    continue
                line_names = (
                    station.get_line_names()
                    if hasattr(station, 'get_line_names')
                    else getattr(station, 'line_names', [])
                )
                name = station.get_name() if hasattr(station, 'get_name') else getattr(station, 'name', 'Station')
                append_station(x, y, name, False, line_names)

        return stations_data

    def get_population_points_for_display(self, game_map) -> list[dict]:
        """Get all population points that should be displayed on the map.
        Returns a list of dicts with keys: 'grid_x', 'grid_y', 'pop', 'lat', 'lon', and optionally 'included'
        All game logic for determining which points to display is here.
        """
        population_data = []
        
        # Ensure population attribute exists - initialize if missing
        if not hasattr(game_map, 'population'):
            from map_preset_loader import initialize_preset_population
            preset_name = getattr(game_map, 'map_preset_name', None)
            pop_dict = initialize_preset_population(
                preset_name, game_map.width, game_map.height,
                game_map.map_center_lat, game_map.map_center_lon
            ) if preset_name else None
            if pop_dict is not None:
                game_map.population = pop_dict
                deleted = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
                for pt in deleted:
                    game_map.population.pop(pt, None)
            else:
                from map_presets_standard import get_standard_population_at_location
                game_map.population = {}
                for x in range(game_map.width + 1):
                    for y in range(game_map.height + 1):
                        pop = get_standard_population_at_location(x, y, game_map.width, game_map.height)
                        game_map.population[(x, y)] = pop
        
        if not game_map.population:
            return population_data
        
        # Get map dimensions
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width and game_map.width > 0 else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height and game_map.height > 0 else 50
        
        # Preset land cells come from INITIAL_POPULATION in the preset file (source of truth).
        # Uses preset_info (from map_preset_name) so Bay Area, NYC, NY Metro all work without core changes
        preset_info = None
        if hasattr(game_map, 'map_preset_name') and game_map.map_preset_name:
            from map_preset_utils import get_preset_file_info
            preset_info = get_preset_file_info(game_map)
        
        included_points = None
        if preset_info:
            from map_preset_utils import read_included_points_from_file
            file_path, variable_name = preset_info
            deleted_points = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
            for attempt in range(5):
                points_set = read_included_points_from_file(file_path, variable_name)
                if points_set is not None and len(points_set) > 0:
                    included_points = points_set - deleted_points
                    break
                elif points_set is not None and len(points_set) == 0:
                    included_points = set()
                    break
                import time
                time.sleep(0.02 * (attempt + 1))
            
            if included_points is None and game_map.population:
                deleted = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
                included_points = set(game_map.population.keys()) - deleted
        
        # Get bounds for coordinate conversion (preset-agnostic via map_preset_loader)
        bounds = None
        if preset_info and game_map.map_preset_name:
            from map_preset_loader import get_map_bounds
            bounds = get_map_bounds(game_map.map_preset_name)
        bounds_has_minmax = bounds and 'min_lat' in bounds and 'max_lat' in bounds
        center_lat = game_map.map_center_lat if hasattr(game_map, 'map_center_lat') and game_map.map_center_lat is not None else (bounds.get('center_lat', 0.0) if bounds else 0.0)
        center_lon = game_map.map_center_lon if hasattr(game_map, 'map_center_lon') and game_map.map_center_lon is not None else (bounds.get('center_lon', 0.0) if bounds else 0.0)
        grid_size = max(map_width, map_height)
        if bounds_has_minmax:
            min_lat = bounds['min_lat']
            max_lat = bounds['max_lat']
            min_lon = bounds['min_lon']
            max_lon = bounds['max_lon']
        else:
            lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
            if lon_deg_per_km == 0:
                lon_deg_per_km = 111.0
            min_lat = center_lat - (grid_size / 2) / 111.0
            max_lat = center_lat + (grid_size / 2) / 111.0
            min_lon = center_lon - (grid_size / 2) / lon_deg_per_km
            max_lon = center_lon + (grid_size / 2) / lon_deg_per_km
        lon_deg_per_km = 111.0 * math.cos(math.radians(center_lat))
        if lon_deg_per_km == 0:
            lon_deg_per_km = 111.0
        lat_scale = (max_lat - min_lat) / map_height if map_height > 0 else 0
        lon_scale = (max_lon - min_lon) / map_width if map_width > 0 else 0
        
        # Process population points
        # Points explicitly removed in edit mode - never show these (map + session-level for persistence)
        deleted_points = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
        
        preset_name = getattr(game_map, 'map_preset_name', None)
        if preset_name and bounds_has_minmax:
            from map_preset_loader import convert_preset_grid_to_latlon
            _grid_latlon = lambda gx, gy: convert_preset_grid_to_latlon(
                preset_name, gx, gy,
                {'min_lat': min_lat, 'max_lat': max_lat, 'min_lon': min_lon, 'max_lon': max_lon},
                map_width, map_height,
            )
        else:
            _grid_latlon = lambda gx, gy: (min_lat + gy * lat_scale, min_lon + gx * lon_scale)
        
        # Preset maps: land cells = INITIAL_POPULATION keys (population > 0).
        if preset_info and included_points is not None:
            for (grid_x, grid_y) in included_points:
                if (grid_x, grid_y) in deleted_points:
                    continue
                pop = game_map.population.get((grid_x, grid_y), 0)
                if pop <= 0:
                    continue
                lat, lon = _grid_latlon(grid_x, grid_y)
                population_data.append({
                    'grid_x': grid_x,
                    'grid_y': grid_y,
                    'pop': pop,
                    'lat': lat,
                    'lon': lon
                })
        else:
            # Standard maps or fallback: iterate over population
            points_processed = 0
            points_filtered_out = 0
            for (x, y), pop in game_map.population.items():
                points_processed += 1
                grid_x = int(round(x))
                grid_y = int(round(y))
                if (grid_x, grid_y) in deleted_points:
                    points_filtered_out += 1
                    continue
                if pop <= 0:
                    points_filtered_out += 1
                    continue
                lat = min_lat + grid_y * lat_scale
                lon = min_lon + grid_x * lon_scale
                population_data.append({
                    'grid_x': grid_x,
                    'grid_y': grid_y,
                    'pop': pop,
                    'lat': lat,
                    'lon': lon
                })
        
        # Final safety filter: never return deleted points (catches any edge case)
        deleted_points = (getattr(game_map, '_deleted_population_points', set()) or set()) | (getattr(self, '_session_deleted_population_points', set()) or set())
        population_data = [p for p in population_data if (p['grid_x'], p['grid_y']) not in deleted_points]
        
        # Mark included points for JS (default True when not specified)
        for p in population_data:
            if 'included' not in p:
                p['included'] = True
        
        return population_data