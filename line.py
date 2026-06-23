from grid import GridPoint
from player import Player
from vehicle import Trainset
import math


class RightOfWay:
    def __init__(self,
                 name: str,
                 object_id: int,
                 route: list = []):
        self.route = route
        self.name = name
        self.object_id = object_id

    def get_route(self):
        return self.route

    def get_object_id(self):
        return self.object_id

    def get_name(self):
        return self.name

    def __len__(self):
        return len(self.route)

    def rename(self,
               new_name: str):
        self.name = new_name

    def extend_route_from_beginning(self,
                              additional_points: list[GridPoint]):
        self.route = list(additional_points) + self.route

    def extend_route_from_end(self,
                              additional_points: list[GridPoint]):
        self.route.extend(additional_points)

class Line(RightOfWay):
    def __init__(self,
                 name,
                 object_id,
                 route,
                 owner: Player,
                 vehicles: list[Trainset],
                 fare: int = 10,
                 profit: int = 0,
                 has_third_rail: bool = False,
                 has_overhead_wire: bool = False,
                 stations: list = None,
                 elevation_levels: list = None):
        super().__init__(name,
                         object_id,
                         route)
        self.owner = owner
        self.has_third_rail = has_third_rail
        self.has_overhead_wire = has_overhead_wire
        self.vehicles = vehicles
        self.profit = profit
        self.fare = fare
        self.stations = stations if stations is not None else []
        self.traversal_time_hours = 0
        self.quality = 1.0
        self.maintenance_workers = 0
        self.num_tracks = 1  # Number of tracks (affects max frequency)
        # Elevation levels: list of 'street', 'above_ground', or 'underground' for each segment
        # Defaults to 'street' for all segments if not specified
        if elevation_levels is None:
            self.elevation_levels = ['street'] * max(1, len(route) - 1) if route else []
        else:
            self.elevation_levels = elevation_levels
        
        self.schedule = {
            'peak': 0.0,
            'off_peak': 0.0,
            'night': 0.0
        }
    
    def update_quality(self):
        """Update quality based on maintenance workers, locomotive quality, and railcar quality.
        Line quality = base (1.0 + 0.1 per maintenance worker) + locomotive quality contribution + railcar quality contribution.
        Locomotive quality contributes up to 0.3 to line quality (average of all locomotives on line).
        Railcar quality contributes up to 0.5 to line quality (average of all railcars on line).
        Quality range is now 1.0-15.0, with 10.0-15.0 reserved for first class cars.
        Max line quality is 3.0 (for ridership calculations), but individual vehicle quality can be up to 15.0."""
        base_quality = min(3.0, 1.0 + (self.maintenance_workers * 0.1))
        
        locomotive_quality_sum = 0.0
        locomotive_count = 0
        for trainset in self.vehicles:
            if trainset and hasattr(trainset, 'locomotive') and trainset.locomotive:
                loco = trainset.locomotive
                if hasattr(loco, 'quality'):
                    if hasattr(loco, 'update_quality'):
                        loco.update_quality()
                    loco_quality = min(9.0, max(1.0, loco.quality))
                    locomotive_quality_sum += loco_quality
                    locomotive_count += 1
        
        locomotive_contribution = 0.0
        if locomotive_count > 0:
            avg_locomotive_quality = locomotive_quality_sum / locomotive_count
            locomotive_contribution = min(0.3, (avg_locomotive_quality - 1.0) / 8.0 * 0.3)
        
        railcar_quality_sum = 0.0
        railcar_count = 0
        for trainset in self.vehicles:
            if trainset and hasattr(trainset, 'cars') and trainset.cars:
                for car in trainset.cars:
                    if hasattr(car, 'quality'):
                        car_quality = min(15.0, max(1.0, car.quality))
                        railcar_quality_sum += car_quality
                        railcar_count += 1
        
        railcar_contribution = 0.0
        if railcar_count > 0:
            avg_railcar_quality = railcar_quality_sum / railcar_count
            railcar_contribution = min(0.5, (avg_railcar_quality - 1.0) / 14.0 * 0.5)
        
        self.quality = min(3.0, base_quality + locomotive_contribution + railcar_contribution)

    def get_owner(self):
        return self.owner
    
    def get_vehicles(self):
        return self.vehicles

    def add_station(self, station):
        """Add a station to this line"""
        if station not in self.stations:
            self.stations.append(station)
    
    def remove_station(self, station):
        """Remove a station from this line"""
        if station in self.stations:
            self.stations.remove(station)
    
    def get_stations(self):
        """Return list of stations on this line"""
        return self.stations
    
    def calc_traversal_time(self, avg_speed_mph=30):
        """Calculate time (hours) to traverse the full line at average speed.
        Default average speed is 30 mph, adjustable per trainset type."""
        try:
            route = self.get_route()
            if not route or len(route) < 2:
                return 0
            total_distance = 0.0
            for i in range(len(route) - 1):
                x1, y1 = route[i]
                x2, y2 = route[i+1]
                total_distance += math.hypot(x2 - x1, y2 - y1)
            # Assume each unit is 1 mile; time = distance / speed
            self.traversal_time_hours = total_distance / avg_speed_mph
            return self.traversal_time_hours
        except Exception:
            return 0
    
    def get_max_frequency_per_hour(self, time_period: str = 'peak') -> float:
        """Get maximum frequency per hour for a time period based on trains and track capacity.
        
        Args:
            time_period: 'peak', 'off_peak', or 'night'
            
        Returns:
            Maximum trains per hour (limited by number of trains and track capacity)
        """
        # Base capacity: each track can handle ~30 trains per hour (2 min headway)
        # More tracks = more capacity
        track_capacity = self.num_tracks * 30.0
        
        # Number of trains available
        num_trains = len(self.vehicles) if self.vehicles else 0
        
        # Calculate max frequency based on trains and line length
        trav_time = self.calc_traversal_time()
        if trav_time == 0:
            return 0.0
        
        # Maximum frequency is limited by:
        # 1. Track capacity (trains per hour per track)
        # 2. Number of trains available
        # 3. Line traversal time (can't run more trains than line can handle)
        max_by_tracks = track_capacity
        max_by_trains = num_trains * (60.0 / (trav_time * 2)) if num_trains > 0 else 0  # Trains per hour
        max_by_line = 60.0 / (trav_time * 2)  # One train per round trip time
        
        # Take minimum of all constraints
        max_frequency = min(max_by_tracks, max_by_trains, max_by_line) if num_trains > 0 else 0.0
        
        # Apply schedule multiplier for time period
        schedule_multiplier = self.schedule.get(time_period, 1.0)
        return min(max_frequency, schedule_multiplier * max_frequency)
    
    def set_schedule_frequency(self, time_period: str, frequency: float):
        """Set schedule frequency for a time period.
        Frequency is stored as desired; actual service is limited by max capacity at runtime.
        
        Args:
            time_period: 'peak', 'off_peak', or 'night'
            frequency: Desired frequency (trains per hour)
        """
        self.schedule[time_period] = max(0.0, frequency)
    
    def upgrade_tracks(self, additional_tracks: int = 1):
        """Upgrade line by adding more tracks (increases capacity).
        
        Args:
            additional_tracks: Number of tracks to add (default 1)
        """
        self.num_tracks += additional_tracks
    
    def service_frequency_per_year(self, trainset_avg_speed=30, hours_per_year=8760, current_year=1830, time_period='off_peak'):
        """Calculate how many times a trainset can traverse the line in a year.
        Accounts for traversal time (including turnaround) and schedule.
        hours_per_year is hours the line operates per year (typically 8760 for 24/7).
        
        Args:
            trainset_avg_speed: Average speed of the trainset
            hours_per_year: Hours the line operates per year
            current_year: Current year (for bus traffic calculations)
            time_period: Time period ('peak', 'off_peak', 'night') for bus traffic
        """
        if isinstance(self, Bus):
            trav_time = self.calc_traversal_time(avg_speed_mph=trainset_avg_speed, current_year=current_year, time_period=time_period)
        else:
            trav_time = self.calc_traversal_time(avg_speed_mph=trainset_avg_speed)
        if trav_time == 0:
            return 0
        # Assume 2x traversal time to account for turnaround/loading
        round_trip_time = trav_time * 2
        
        # Use schedule to determine actual frequency
        desired_frequency = (self.schedule['peak'] * 0.4 + 
                            self.schedule['off_peak'] * 0.4 + 
                            self.schedule['night'] * 0.2)
        
        # Calculate service frequency based on schedule, limited by max capacity
        if desired_frequency == 0:
            return 0
        
        # Get max frequency for peak (most restrictive)
        max_freq = self.get_max_frequency_per_hour('peak')
        if max_freq == 0:
            return 0
        
        # Actual frequency is minimum of desired and max capacity
        actual_frequency = min(desired_frequency, max_freq, 60.0 / round_trip_time)
        return int(actual_frequency * hours_per_year)

    def calc_profit(self, current_year=1830):
        """Calculate profit based on the number of times each trainset passes each station each year.
        Each pass of a station by a trainset generates revenue = fare * capacity * capacity_util.
        A trainset passes each station twice per round trip (once forward, once backward).
        Profit is limited by the population at each station location.
        If engineers are on strike, no profit is generated (trains cannot run)."""
        total_profit = 0
        
        if len(self.stations) == 0:
            # No stations, no profit
            self.profit = 0
            return 0
        
        # Check if engineers are on strike - if so, no trains can run, so no profit
        try:
            if hasattr(self, 'owner') and self.owner:
                from worker import ENGINEER
                engineer_rental = self.owner.get_worker_rental(ENGINEER.name)
                if engineer_rental and engineer_rental.is_on_strike():
                    # Engineers on strike - no trains can run, no profit
                    self.profit = 0
                    return 0
        except (AttributeError, ImportError):
            pass
        
        # Get current year for inflation calculation
        if current_year is None:
            current_year = 1830  # Default
            try:
                if hasattr(self, 'owner') and self.owner:
                    game = self.owner.__dict__.get('_game', None)
                    if game:
                        current_year = game.get_year()
                elif hasattr(self, '_game'):
                    current_year = self._game.get_year()
            except:
                pass
        
        # Get event effects (e.g., automobile competition reducing profits)
        event_profit_multiplier = 1.0
        try:
            if hasattr(self, 'owner') and self.owner:
                game = self.owner.__dict__.get('_game', None)
                if game and hasattr(game, 'event_system'):
                    event_effects = game.event_system.get_combined_effects(current_year)
                    event_profit_multiplier = event_effects.get('profit_multiplier', 1.0)
            elif hasattr(self, '_game'):
                if hasattr(self._game, 'event_system'):
                    event_effects = self._game.event_system.get_combined_effects(current_year)
                    event_profit_multiplier = event_effects.get('profit_multiplier', 1.0)
        except:
            pass
        
        # Calculate max acceptable fare based on inflation (1830: $2, 2050: $15)
        # Linear interpolation: fare_max = 2 + (year - 1830) * (15 - 2) / (2050 - 1830)
        max_acceptable_fare = 2 + (current_year - 1830) * (15 - 2) / (2050 - 1830)
        max_acceptable_fare = max(2, min(15, max_acceptable_fare))  # Clamp between 2 and 15
        
        # If fare is too high, reduce capacity utilization (people won't ride)
        effective_fare = min(self.fare, max_acceptable_fare)
        base_capacity_util = 0.6
        if self.fare > max_acceptable_fare:
            # Reduce utilization if fare is too high
            fare_penalty = (self.fare - max_acceptable_fare) / max_acceptable_fare
            base_capacity_util = max(0.1, base_capacity_util * (1 - fare_penalty * 0.8))  # Up to 80% reduction
        
        # Calculate inflation factor to keep profits constant in real terms
        # Inflation: prices increase over time (1830: 1x, 2050: 10x)
        inflation_factor = 1.0 + (current_year - 1830) * (5.5 - 1.0) / (2050 - 1830)
        inflation_factor = max(1.0, min(10.0, inflation_factor))  # Clamp between 1 and 10
        
        # Adjust effective_fare by inflation to keep profits constant in real terms
        # This means a $15 fare in 2050 produces the same real profit as a $2 fare in 1830
        real_fare = effective_fare / inflation_factor
        
        # Get map to access population data
        game_map = None
        try:
            if hasattr(self, 'owner') and self.owner:
                game = self.owner.__dict__.get('_game', None)
                if game:
                    game_map = game.get_map()
            elif hasattr(self, '_game'):
                game_map = self._game.get_map()
        except:
            pass
        
        # Check if line has vehicles
        if not self.vehicles or len(self.vehicles) == 0:
            self.profit = 0
            return 0
        
        for vehicle in self.vehicles:
            if not vehicle or not hasattr(vehicle, 'locomotive') or not vehicle.locomotive:
                continue
            # Calculate how many round trips this trainset makes per year
            try:
                service_freq = self.service_frequency_per_year(trainset_avg_speed=vehicle.locomotive.average_speed)
            except (AttributeError, TypeError) as e:
                continue
            if service_freq == 0:
                continue
            passes_per_station_per_year = service_freq * 2
            PROFIT_MULTIPLIER = 0.02  
            
            for station in self.stations:
                station_x = station.get_x()
                station_y = station.get_y()
                max_passengers_per_year = 0
                if game_map and hasattr(game_map, 'get_available_passengers'):
                    # Approximate passenger availability by time-of-day:
                    # use a weighted average of peak/off-peak/night participation.
                    peak = game_map.get_available_passengers(station_x, station_y, "peak")
                    off_peak = game_map.get_available_passengers(station_x, station_y, "off_peak")
                    night = game_map.get_available_passengers(station_x, station_y, "night")
                    max_passengers_per_year = int(peak * 0.4 + off_peak * 0.4 + night * 0.2)
                vehicle_capacity = vehicle.get_capacity()
                potential_passengers_per_pass = vehicle_capacity * base_capacity_util
                total_potential_passengers = passes_per_station_per_year * potential_passengers_per_pass
                if max_passengers_per_year == 0:
                    actual_capacity_util = base_capacity_util
                else:
                    actual_passengers_per_year = min(total_potential_passengers, max_passengers_per_year)
                    # Calculate actual capacity utilization based on population limit
                    # Ensure minimum utilization of 10% even if population is very low
                    if total_potential_passengers > 0:
                        pop_utilization = actual_passengers_per_year / total_potential_passengers
                        # Use at least 10% of base capacity, even if population is limiting
                        actual_capacity_util = max(0.1, pop_utilization) * base_capacity_util
                    else:
                        actual_capacity_util = base_capacity_util * 0.1  # Minimum 10% of base
                
                # Each pass generates revenue based on real fare (adjusted for inflation), capacity, and actual utilization
                # Using real_fare ensures profits don't increase with inflation
                profit_per_pass = real_fare * vehicle_capacity * actual_capacity_util * PROFIT_MULTIPLIER
                # Total profit from this trainset passing this station
                station_profit = passes_per_station_per_year * profit_per_pass
                total_profit += station_profit
        
        # Apply event effects (e.g., automobile competition)
        total_profit = total_profit * event_profit_multiplier
        
        self.profit = int(total_profit)
        return self.profit
    
    def calc_maitnance(self, current_year: int = None):
        """Calculate maintenance costs, accounting for outdated locomotives"""
        total_maitnance = 0
        for vehicle in self.vehicles:
            total_maitnance += vehicle.get_maitnance_costs(current_year)
        length = len(self.route) if self.route else 0
        PER_SEGMENT_COST = 50
        total_maitnance += length * PER_SEGMENT_COST
        self.maitnance = total_maitnance
        return self.maitnance
    
    def get_price(self, current_year=None):
        # Get current year for inflation
        if current_year is None:
            current_year = 1830  # Default
            try:
                if hasattr(self, 'owner') and self.owner:
                    game = self.owner.__dict__.get('_game', None)
                    if game:
                        current_year = game.get_year()
                elif hasattr(self, '_game'):
                    current_year = self._game.get_year()
            except:
                pass
        
        # Inflation: prices increase over time (1830: 1x, 2050: 10x)
        inflation_factor = 1.0 + (current_year - 1830) * (5.5 - 1.0) / (2050 - 1830)
        inflation_factor = max(1.0, min(10.0, inflation_factor))  # Clamp between 1 and 10
        
        # Calculate actual distance of the line (not just number of points)
        import math
        total_distance = 0.0
        if len(self.route) > 1:
            for i in range(len(self.route) - 1):
                x1, y1 = self.route[i]
                x2, y2 = self.route[i + 1]
                total_distance += math.hypot(x2 - x1, y2 - y1)
        
        # Base price per unit distance
        BASE_PRICE_PER_UNIT = 150
        base_price = BASE_PRICE_PER_UNIT * total_distance
        
        profit = self.calc_profit(current_year=current_year)
        return int((base_price + profit * 10) * inflation_factor) 
    
    def __bool__(self):
        return len(self.route) > 0



class Ferry(Line):
    """Ferry line - can traverse water and areas within 60m of water.
    Only terminals need to be constructed, the route itself is free."""
    def __init__(self,
                 name: str,
                 object_id: int,
                 route: list,
                 owner: Player,
                 vehicles: list = None,
                 fare: int = 10,
                 profit: int = 0,
                 stations: list = None):
        super().__init__(name, object_id, route, owner, vehicles, fare, profit, stations)
        self.owner = owner
        self.vehicles = vehicles if vehicles is not None else []
        self.profit = profit
        self.fare = fare
        self.stations = stations if stations is not None else []
        self.traversal_time_hours = 0
        self.quality = 1.0
        self.maintenance_workers = 0
        
        # Schedule: frequencies per hour for different time periods
        self.schedule = {
            'peak': 0.0,
            'off_peak': 0.0,
            'night': 0.0
        }
    
    def can_traverse_water(self) -> bool:
        """Ferries can traverse water."""
        return True
    
    def can_traverse_land(self) -> bool:
        """Ferries cannot traverse land."""
        return False


class Bus(Line):
    """Bus line - can only traverse streets/roads, not water or off-road terrain.
    Buses are cheaper to build but get stuck in traffic when it exists."""
    def __init__(self,
                 name: str,
                 object_id: int,
                 route: list,
                 owner: Player,
                 vehicles: list = None,
                 fare: int = 10,
                 profit: int = 0,
                 stations: list = None):
        super().__init__(name, object_id, route)
        self.owner = owner
        self.vehicles = vehicles if vehicles is not None else []
        self.profit = profit
        self.fare = fare
        self.stations = stations if stations is not None else []
        self.traversal_time_hours = 0
        self.quality = 1.0
        self.maintenance_workers = 0
        
        self.schedule = {
            'peak': 0.0,
            'off_peak': 0.0,
            'night': 0.0
        }
        self.num_busways = 0
        self.traffic_multiplier = 1.0
    
    def can_traverse_water(self) -> bool:
        """Buses cannot traverse water."""
        return False
    
    def can_traverse_land(self) -> bool:
        """Buses can traverse land, but only on streets/roads."""
        return True
    
    def calc_traversal_time(self, avg_speed_mph=20, current_year=1830, time_period='off_peak'):
        """Calculate time (hours) to traverse the full bus line at average speed.
        Accounts for traffic when it exists (after 1900).
        
        Args:
            avg_speed_mph: Average speed in mph (default 20 for buses)
            current_year: Current year (used to determine if traffic exists)
            time_period: 'peak', 'off_peak', or 'night' (affects traffic multiplier)
        """
        try:
            route = self.get_route()
            if not route or len(route) < 2:
                return 0
            total_distance = 0.0
            for i in range(len(route) - 1):
                x1, y1 = route[i]
                x2, y2 = route[i+1]
                total_distance += math.hypot(x2 - x1, y2 - y1)
            
            # Update traffic multiplier based on current year and time period
            self.update_traffic_multiplier(current_year, time_period)
            
            effective_speed = avg_speed_mph / self.traffic_multiplier
            
            self.traversal_time_hours = total_distance / effective_speed if effective_speed > 0 else 0
            return self.traversal_time_hours
        except Exception:
            return 0
    
    def update_traffic_multiplier(self, current_year: int, time_period: str = 'off_peak'):
        """Update traffic multiplier based on current year and time period.
        Traffic exists when automobiles are common (after ~1900).
        Separated busways eliminate traffic impact.
        
        Args:
            current_year: Current year
            time_period: 'peak', 'off_peak', or 'night'
        """
        if self.num_busways > 0:
            self.traffic_multiplier = 1.0
        elif current_year >= 1900:
            if time_period == 'peak':
                self.traffic_multiplier = 2.0
            elif time_period == 'off_peak':
                self.traffic_multiplier = 1.5
            elif time_period == 'night':
                self.traffic_multiplier = 1.2
            else:
                self.traffic_multiplier = 1.5
        else:
            self.traffic_multiplier = 1.0
    
    def get_max_frequency_per_hour(self, time_period: str = 'peak') -> float:
        """Get maximum frequency per hour for a time period based on buses and busway capacity.
        
        Args:
            time_period: 'peak', 'off_peak', or 'night'
            
        Returns:
            Maximum buses per hour (limited by number of buses and busway capacity)
        """
        if self.num_busways > 0:
            busway_capacity = self.num_busways * 30.0
        else:
            busway_capacity = 15.0
        
        num_buses = len(self.vehicles) if self.vehicles else 0
        
        trav_time = self.calc_traversal_time(current_year=1830, time_period=time_period)
        if trav_time == 0:
            return 0.0
        
        max_by_busways = busway_capacity
        max_by_buses = num_buses * (60.0 / (trav_time * 2)) if num_buses > 0 else 0
        max_by_line = 60.0 / (trav_time * 2)
        
        max_frequency = min(max_by_busways, max_by_buses, max_by_line) if num_buses > 0 else 0.0
        
        schedule_multiplier = self.schedule.get(time_period, 1.0)
        return min(max_frequency, schedule_multiplier * max_frequency)
    
    def set_schedule_frequency(self, time_period: str, frequency: float):
        """Set schedule frequency for a time period.
        Frequency is capped at physical feasible limit.
        
        Args:
            time_period: 'peak', 'off_peak', or 'night'
            frequency: Desired frequency (buses per hour)
        """
        max_frequency = self.get_max_frequency_per_hour(time_period)
        self.schedule[time_period] = max(0.0, min(frequency, max_frequency))
    
    def upgrade_busways(self, additional_busways: int = 1):
        """Upgrade bus line by adding separated busways (increases capacity and eliminates traffic impact).
        
        Args:
            additional_busways: Number of busways to add (default 1)
        """
        self.num_busways += additional_busways


class Road(RightOfWay):
    def __init__(self,
                 name,
                 object_id,
                 route,
                 year_built: int = None):
        super().__init__(name,
                         object_id,
                         route)
        self.year_built = year_built 