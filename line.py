from grid import GridPoint
from player import Player
from vehicle import Trainset
from typing import Optional
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
        # Clicks are in outward order (first adjacent to route start); prepend reversed.
        pts = list(reversed(additional_points or []))
        if pts and self.route:
            last_pt = pts[-1]
            first_route = self.route[0]
            if (hasattr(last_pt, '__len__') and hasattr(first_route, '__len__') and
                len(last_pt) >= 2 and len(first_route) >= 2 and
                abs(float(last_pt[0]) - float(first_route[0])) < 0.01 and
                abs(float(last_pt[1]) - float(first_route[1])) < 0.01):
                pts = pts[:-1]
        self.route = pts + self.route

    def extend_route_from_end(self,
                              additional_points: list[GridPoint]):
        pts = list(additional_points or [])
        if pts and self.route:
            first = pts[0]
            last = self.route[-1]
            if (hasattr(first, '__len__') and hasattr(last, '__len__') and
                len(first) >= 2 and len(last) >= 2 and
                abs(float(first[0]) - float(last[0])) < 0.01 and
                abs(float(first[1]) - float(last[1])) < 0.01):
                pts = pts[1:]
            # Never auto-close the route by connecting the new tip back to the start.
            route_start = self.route[0]
            tip = pts[-1]
            if (hasattr(tip, '__len__') and hasattr(route_start, '__len__') and
                len(tip) >= 2 and len(route_start) >= 2 and
                abs(float(tip[0]) - float(route_start[0])) < 0.01 and
                abs(float(tip[1]) - float(route_start[1])) < 0.01 and
                (len(self.route) < 2 or not (
                    abs(float(tip[0]) - float(last[0])) < 0.01 and
                    abs(float(tip[1]) - float(last[1])) < 0.01))):
                pts = pts[:-1]
        self.route.extend(pts)

class Line(RightOfWay):
    def __init__(self,
                 name,
                 object_id,
                 route,
                 owner: Optional[Player] = None,
                 vehicles: list[Trainset] = None,
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
        self.vehicles = vehicles if vehicles is not None else []
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
    
    def calc_traversal_time(self, avg_speed_mph=30, include_station_dwell=True):
        """Calculate time (hours) to traverse the full line at average speed.
        Includes dwell time at each station when include_station_dwell is True."""
        try:
            route = self.get_route()
            if not route or len(route) < 2:
                return 0
            total_distance = 0.0
            for i in range(len(route) - 1):
                x1, y1 = route[i]
                x2, y2 = route[i + 1]
                total_distance += math.hypot(x2 - x1, y2 - y1)
            speed = self._effective_train_speed_mph(avg_speed_mph)
            if speed <= 0:
                return 0.0
            travel_hours = total_distance / speed
            if include_station_dwell:
                from game_presets import STATION_DWELL_MINUTES_PER_STOP
                num_stations = len(self.get_stations()) if hasattr(self, 'get_stations') else 0
                travel_hours += num_stations * (STATION_DWELL_MINUTES_PER_STOP / 60.0)
            self.traversal_time_hours = travel_hours
            return travel_hours
        except Exception:
            return 0

    def _effective_train_speed_mph(self, fallback: float = 30.0) -> float:
        """Average speed from assigned trainsets (mph)."""
        speeds = []
        for vehicle in self.vehicles or []:
            loco = getattr(vehicle, 'locomotive', None)
            if loco is not None:
                for attr in ('average_speed', 'top_speed'):
                    s = getattr(loco, attr, None)
                    if isinstance(s, (int, float)) and not isinstance(s, bool):
                        speeds.append(float(s))
                        break
            else:
                s = getattr(vehicle, 'average_speed', None)
                if isinstance(s, (int, float)) and not isinstance(s, bool):
                    speeds.append(float(s))
        if not speeds:
            return float(fallback) if fallback else 30.0
        return sum(speeds) / len(speeds)
    
    def get_max_frequency_per_hour(self, time_period: str = 'peak') -> float:
        """Maximum physically feasible trains per hour (one direction).

        Limited by assigned trainsets, line length, train speed, station dwell,
        terminal turnaround, and track count.
        """
        from game_presets import (
            TERMINAL_TURNAROUND_MINUTES,
            TRACK_CAPACITY_TRAINS_PER_HOUR_BASE,
        )

        num_trains = len(self.vehicles) if self.vehicles else 0
        if num_trains <= 0:
            return 0.0

        speed = self._effective_train_speed_mph()
        one_way_hours = self.calc_traversal_time(avg_speed_mph=speed, include_station_dwell=True)
        if one_way_hours <= 0:
            return 0.0

        round_trip_hours = (2.0 * one_way_hours) + (TERMINAL_TURNAROUND_MINUTES / 60.0)
        max_by_trains = num_trains / round_trip_hours

        # Faster trains allow slightly higher track throughput (shorter headways).
        speed_factor = min(1.5, max(0.75, speed / 30.0))
        track_capacity = self.num_tracks * TRACK_CAPACITY_TRAINS_PER_HOUR_BASE * speed_factor

        return min(track_capacity, max_by_trains)
    
    def set_schedule_frequency(self, time_period: str, frequency: float):
        """Set schedule frequency for a time period.
        Frequency is capped at the physical limit from assigned trainsets and track capacity.
        Clearing profit cache ensures next profit calculation uses the new schedule."""
        max_frequency = self.get_max_frequency_per_hour(time_period)
        self.schedule[time_period] = max(0.0, min(float(frequency), max_frequency))
        if hasattr(self, '_cached_profit'):
            delattr(self, '_cached_profit')
        if hasattr(self, '_cached_profit_year'):
            delattr(self, '_cached_profit_year')
    
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
        if desired_frequency == 0:
            return 0

        # Get max frequency for peak (most restrictive)
        max_freq = self.get_max_frequency_per_hour('peak')
        if max_freq == 0:
            return 0
        
        # Actual frequency is minimum of desired and max capacity
        actual_frequency = min(desired_frequency, max_freq, 60.0 / round_trip_time)
        return int(actual_frequency * hours_per_year)

    def calc_schedule_operating_cost(self, current_year: int = None) -> int:
        """Small annual cost for running scheduled service (crew, fuel wear)."""
        if not self.vehicles:
            return 0
        from game_presets import SCHEDULE_OPERATING_COST_PER_TRAIN_HOUR
        from inflation_utils import get_inflation_factor

        trips_per_hour = (
            float(self.schedule.get('peak', 0) or 0) * 0.4
            + float(self.schedule.get('off_peak', 0) or 0) * 0.4
            + float(self.schedule.get('night', 0) or 0) * 0.2
        )
        if trips_per_hour <= 0:
            return 0
        cy = int(current_year) if current_year is not None else 1830
        infl = get_inflation_factor(cy, max_factor=5.5)
        train_hours = trips_per_hour * 8760.0 * len(self.vehicles)
        return max(0, int(round(train_hours * SCHEDULE_OPERATING_COST_PER_TRAIN_HOUR * infl)))

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
        
        # Calculate max acceptable fare: $2 floor, timeline cap up to $15 (not inflation-scaled).
        from inflation_utils import get_nominal_fare_cap
        _, max_acceptable_fare = get_nominal_fare_cap(int(current_year))
        
        # If fare is too high, reduce capacity utilization (people won't ride)
        effective_fare = min(self.fare, max_acceptable_fare)
        base_capacity_util = 0.6
        if self.fare > max_acceptable_fare:
            # Reduce utilization if fare is too high
            fare_penalty = (self.fare - max_acceptable_fare) / max_acceptable_fare
            base_capacity_util = max(0.1, base_capacity_util * (1 - fare_penalty * 0.8))  # Up to 80% reduction

        # Line quality (maintenance workers + rolling stock) increases ridership (RULES.md; max quality 3.0)
        if hasattr(self, 'update_quality'):
            try:
                self.update_quality()
            except (AttributeError, TypeError, ValueError):
                pass
        q = float(getattr(self, 'quality', 1.0) or 1.0)
        q = max(1.0, min(3.0, q))
        quality_ridership_mult = 1.0 + (q - 1.0) * 0.25  # 1.0 at quality 1, 1.5 at quality 3
        base_capacity_util = min(0.95, base_capacity_util * quality_ridership_mult)
        
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
                
                # Revenue uses the line fare as set (not inflation-adjusted).
                profit_per_pass = effective_fare * vehicle_capacity * actual_capacity_util * PROFIT_MULTIPLIER
                # Total profit from this trainset passing this station
                station_profit = passes_per_station_per_year * profit_per_pass
                total_profit += station_profit
        
        # Apply event effects (e.g., automobile competition)
        total_profit = total_profit * event_profit_multiplier
        
        self.profit = int(total_profit)
        return self.profit
    
    def calc_maitnance(self, current_year: int = None):
        """Calculate maintenance costs: vehicles, track length, and schedule operating cost.
        Track cost is per unit of route distance (not per polyline vertex), so OSM street
        polylines are not penalized for having more points than a straight chord.
        Schedule operating cost scales with trips run per year - running trains costs money (crew, wear).
        When trains are scheduled properly (match demand), profit exceeds this cost by a large margin."""
        import math
        total_maitnance = 0
        for vehicle in self.vehicles:
            total_maitnance += vehicle.get_maitnance_costs(current_year)
        # Distance-based track maintenance (grid units ≈ km). Vertex count must not matter.
        PER_UNIT_DISTANCE_COST = 50
        total_distance = 0.0
        route = self.route or []
        for i in range(len(route) - 1):
            x1, y1 = float(route[i][0]), float(route[i][1])
            x2, y2 = float(route[i + 1][0]), float(route[i + 1][1])
            total_distance += math.hypot(x2 - x1, y2 - y1)
        total_maitnance += int(round(total_distance * PER_UNIT_DISTANCE_COST))

        if self.vehicles and current_year is not None:
            total_maitnance += self.calc_schedule_operating_cost(current_year)

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
        
        # Inflation: use shared inflation so game setting applies
        from inflation_utils import get_inflation_factor
        inflation_factor = get_inflation_factor(current_year, max_factor=5.5)
        
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
        vehicles = vehicles if vehicles is not None else []
        super().__init__(name, object_id, route, owner, vehicles, fare=fare, profit=profit, stations=stations)
        self.owner = owner
        self.vehicles = vehicles
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
        vehicles = vehicles if vehicles is not None else []
        super().__init__(name, object_id, route, owner, vehicles, fare=fare, profit=profit, stations=stations)
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
    
    def calc_traversal_time(self, avg_speed_mph=20, current_year=1830, time_period='off_peak', include_station_dwell=True):
        """Calculate time (hours) to traverse the full bus line at average speed."""
        try:
            route = self.get_route()
            if not route or len(route) < 2:
                return 0
            total_distance = 0.0
            for i in range(len(route) - 1):
                x1, y1 = route[i]
                x2, y2 = route[i + 1]
                total_distance += math.hypot(x2 - x1, y2 - y1)

            self.update_traffic_multiplier(current_year, time_period)
            speed = self._effective_bus_speed_mph(avg_speed_mph)
            effective_speed = speed / self.traffic_multiplier

            travel_hours = total_distance / effective_speed if effective_speed > 0 else 0
            if include_station_dwell:
                from game_presets import STATION_DWELL_MINUTES_PER_STOP
                num_stations = len(self.get_stations()) if hasattr(self, 'get_stations') else 0
                travel_hours += num_stations * (STATION_DWELL_MINUTES_PER_STOP / 60.0)
            self.traversal_time_hours = travel_hours
            return travel_hours
        except Exception:
            return 0

    def _effective_bus_speed_mph(self, fallback: float = 20.0) -> float:
        speeds = []
        for vehicle in self.vehicles or []:
            s = getattr(vehicle, 'average_speed', None) or getattr(vehicle, 'top_speed', None)
            if s is not None:
                speeds.append(float(s))
        if not speeds:
            return float(fallback) if fallback else 20.0
        return sum(speeds) / len(speeds)
    
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
        """Maximum physically feasible buses per hour."""
        from game_presets import TERMINAL_TURNAROUND_MINUTES, TRACK_CAPACITY_TRAINS_PER_HOUR_BASE

        num_buses = len(self.vehicles) if self.vehicles else 0
        if num_buses <= 0:
            return 0.0

        trav_time = self.calc_traversal_time(current_year=1830, time_period=time_period, include_station_dwell=True)
        if trav_time <= 0:
            return 0.0

        round_trip_hours = (2.0 * trav_time) + (TERMINAL_TURNAROUND_MINUTES / 60.0)
        max_by_buses = num_buses / round_trip_hours

        if self.num_busways > 0:
            busway_capacity = self.num_busways * TRACK_CAPACITY_TRAINS_PER_HOUR_BASE
        else:
            busway_capacity = 15.0
        return min(busway_capacity, max_by_buses)
    
    def set_schedule_frequency(self, time_period: str, frequency: float):
        """Set schedule frequency for a time period.
        Frequency is capped at physical feasible limit.
        Clearing profit cache ensures next profit calculation uses the new schedule."""
        max_frequency = self.get_max_frequency_per_hour(time_period)
        self.schedule[time_period] = max(0.0, min(frequency, max_frequency))
        if hasattr(self, '_cached_profit'):
            delattr(self, '_cached_profit')
        if hasattr(self, '_cached_profit_year'):
            delattr(self, '_cached_profit_year')
    
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