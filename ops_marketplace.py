"""Operations mixin: MarketplaceMixin."""
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


class MarketplaceMixin:
    def marketplace_add_trainsets_for_new_technology(self, new_year: int, newly_available: list):
        marketplace = self.game.get_marketplace()
        marketplace.num_players = len(self.get_players())
        marketplace.add_trainsets_for_new_technology(new_year, newly_available)
    
    def marketplace_add_worker_supply(self, worker_type_name: str, quantity: int):
        marketplace = self.game.get_marketplace()
        marketplace.add_worker_supply(worker_type_name, quantity)
    
    def marketplace_deduct_worker_supply(self, worker_type_name: str, quantity: int):
        marketplace = self.game.get_marketplace()
        marketplace.deduct_worker_supply(worker_type_name, quantity)
    
    def event_system_check_and_create_population_event(self, year: int):
        event_system = self.game.get_event_system()
        return event_system.check_and_create_population_event(year)
    
    def event_system_get_active_events(self, year: int):
        event_system = self.game.get_event_system()
        return event_system.get_active_events(year)
    
    def event_system_check_and_create_natural_disaster(self, year: int):
        """Check and create natural disaster event if conditions are met"""
        return self.game.get_event_system().check_and_create_natural_disaster(year)
    
    def get_trainset_line_assignment(self, trainset) -> str:
        return trainset.get_line_assignment()
    
    def get_trainset_cars(self, trainset) -> list:
        return trainset.cars if hasattr(trainset, 'cars') else []
    
    def get_trainset_locomotive(self, trainset):
        return trainset.locomotive
    
    def marketplace_get_available_lines(self):
        marketplace = self.game.get_marketplace()
        from game_presets import UNOWNED_LINE_BASE_FEE, MARKETPLACE_REPLENISHMENT_START_YEAR
        result = list(marketplace.get_available_lines())
        game_map = self.game.get_map()
        for line in game_map.get_lines_by_name().values():
            if line.get_owner() is None:
                fee = UNOWNED_LINE_BASE_FEE
                sale_item = SaleItem(
                    item=line,
                    price=fee,
                    seller="Unowned",
                    year_added=MARKETPLACE_REPLENISHMENT_START_YEAR,
                )
                result.append(sale_item)
        return result
    
    def marketplace_get_available_vehicles(self):
        marketplace = self.game.get_marketplace()
        return marketplace.get_available_vehicles()
    
    def marketplace_get_worker_supply(self, worker_type_name: str) -> int:
        marketplace = self.game.get_marketplace()
        return marketplace.get_worker_supply(worker_type_name)
    
    def get_resource_costs(self, current_year: int = None):
        """Get current resource costs based on historical pricing. Returns dict of resource_name -> cost per unit."""
        if current_year is None:
            current_year = self.game.get_year()
        
        base_costs = {
            'Coal': 50,
            'Wood': 30,
            'Oil': 80,
            'Electricity': 20,  # Base cost - never free, will be adjusted by year
        }
        
        year_adjustments = {}
        
        if current_year < 1850:
            year_adjustments['Coal'] = 0.8  # 80% of base (cheaper early on)
        elif current_year < 1900:
            year_adjustments['Coal'] = 1.0  # Base price
        elif current_year < 1950:
            year_adjustments['Coal'] = 1.2  # 20% increase
        elif current_year < 1970:
            year_adjustments['Coal'] = 1.5  # 50% increase
        else:
            year_adjustments['Coal'] = 2.0  # 100% increase (environmental costs)
        
        if current_year < 1850:
            year_adjustments['Wood'] = 0.6  # Very cheap early
        elif current_year < 1900:
            year_adjustments['Wood'] = 1.0  # Base price
        elif current_year < 1950:
            year_adjustments['Wood'] = 1.8  # 80% increase
        else:
            year_adjustments['Wood'] = 3.0  # 200% increase
        
        if current_year < 1870:
            year_adjustments['Oil'] = 2.0  # Very expensive early (rare)
        elif current_year < 1900:
            year_adjustments['Oil'] = 1.5  # Still expensive
        elif current_year < 1940:
            year_adjustments['Oil'] = 1.0  # Base price (standardized production)
        elif current_year < 1970:
            year_adjustments['Oil'] = 0.9  # Slightly cheaper (post-war boom)
        else:
            year_adjustments['Oil'] = 1.5  # 50% increase (oil crisis era)
        
        # Electricity always costs money (never free)
        if current_year < 1880:
            year_adjustments['Electricity'] = 0.3  # Early experimental (cheap but not free)
        elif current_year < 1920:
            year_adjustments['Electricity'] = 0.7  # Early commercialization
        elif current_year < 1950:
            year_adjustments['Electricity'] = 1.0  # Base price
        else:
            year_adjustments['Electricity'] = 1.2  # 20% increase
        
        resource_costs = {}
        for resource, base_cost in base_costs.items():
            adjustment = year_adjustments.get(resource, 1.0)
            resource_costs[resource] = int(base_cost * adjustment)
        
        return resource_costs
    
    def marketplace_update_notify(self):
        """Notify that marketplace has changed. This should be called whenever marketplace state changes.
        Returns True if marketplace was updated, False otherwise."""
        return True
    
    def marketplace_remove_vehicle(self, vehicle):
        marketplace = self.game.get_marketplace()
        marketplace.remove_vehicle(vehicle)
    
    def buy_vehicle_complete(self, sale_item, player: Player, current_year: int) -> dict:
        """Complete vehicle purchase operation. All game logic in ui.py.
        Returns dict with purchase info. AI difficulty applies to marketplace price."""
        from vehicle import Trainset, Locomotive, Railcar
        
        vehicle = sale_item.get_item()
        base_price = sale_item.get_price()
        price = self.get_effective_marketplace_price_for_player(player, base_price)
        
        if self.get_player_money(player) < price:
            raise ValueError(f"Player {self.get_player_name(player)} cannot afford this purchase (needs ${price}, has ${self.get_player_money(player)})")
        
        if sale_item.get_quantity_available() <= 0:
            raise ValueError("This vehicle is no longer available.")
        
        if isinstance(vehicle, Trainset):
            year = self.get_trainset_locomotive_year(vehicle)
            if year and year > current_year:
                raise ValueError(f"This trainset is not available until year {year}.")
        
        self.deduct_player_money(player, price, reason='Marketplace purchase')
        
        marketplace = self.game.get_marketplace()
        
        from line import Line
        is_line = isinstance(vehicle, Line)
        target_list = marketplace.available_lines if is_line else marketplace.available_vehicles
        
        removed = False
        if sale_item in target_list:
            # Exact match - this works when sale_item is the actual object from marketplace
            sale_item.deduct_quantity()
            if sale_item.get_quantity_available() <= 0:
                target_list.remove(sale_item)
            removed = True
        else:
            # After deserialization, sale_item is a new object, so we need to find it in the correct list
            # by matching the vehicle AND finding the first SaleItem that matches (since quantities are synced)
            for existing_sale_item in list(target_list):  # Use list() to avoid modification during iteration
                existing_vehicle = existing_sale_item.get_item()
                
                # Match vehicles by properties
                vehicles_match = False
                if existing_vehicle == vehicle:
                    # Exact object match
                    vehicles_match = True
                elif hasattr(existing_vehicle, 'object_id') and hasattr(vehicle, 'object_id'):
                    # Match by object_id if available
                    if (existing_vehicle.object_id is not None and vehicle.object_id is not None and
                        existing_vehicle.object_id == vehicle.object_id and 
                        hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and
                        existing_vehicle.name == vehicle.name):
                        vehicles_match = True
                elif hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name'):
                    # Match by name and type as fallback
                    if existing_vehicle.name == vehicle.name and type(existing_vehicle) == type(vehicle):
                        vehicles_match = True
                
                if vehicles_match:
                    # Found matching SaleItem - deduct quantity
                    existing_sale_item.deduct_quantity()
                    if existing_sale_item.get_quantity_available() <= 0:
                        target_list.remove(existing_sale_item)  # Use target_list, not hardcoded available_vehicles
                    removed = True
                    break
            
            if not removed:
                # Last resort: try marketplace_remove_vehicle (which does more complex matching)
                self.marketplace_remove_vehicle(vehicle)
                removed = True  # Assume it worked
        
        # Add to player inventory
        if isinstance(vehicle, Trainset):
            self.acquire_trainset_for_player(player, vehicle)
            item_name = self.get_trainset_locomotive_name(vehicle)
            capacity = self.get_trainset_capacity(vehicle)
        elif isinstance(vehicle, Locomotive):
            self.acquire_locomotive_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        elif isinstance(vehicle, Ferry):
            self.acquire_ferry_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        elif isinstance(vehicle, Bus):
            self.acquire_bus_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        elif isinstance(vehicle, Railcar):
            self.acquire_railcar_for_player(player, vehicle)
            item_name = vehicle.name
            capacity = getattr(vehicle, 'capacity', 0)
        else:
            item_name = getattr(vehicle, 'name', 'Unknown')
            capacity = getattr(vehicle, 'capacity', 0)
        
        return {
            'item': vehicle,
            'item_name': item_name,
            'price': price,
            'remaining_money': self.get_player_money(player),
            'capacity': capacity
        }
    
    def marketplace_add_vehicle(self, vehicle, price: int, seller: str):
        marketplace = self.game.get_marketplace()
        marketplace.add_vehicle(vehicle, price=price, seller=seller, current_year=self.game.get_year())
    
    def sell_trainset_for_half_price(self, trainset, player: Player) -> dict:
        """Sell a trainset for half its original purchase price.
        All game logic in ui.py. Returns dict with sale info."""
        from vehicle import Trainset
        if not isinstance(trainset, Trainset):
            raise ValueError("Can only sell trainsets")
        player_trainsets = player.get_trainsets()
        if trainset not in player_trainsets:
            raise ValueError(f"Player {self.get_player_name(player)} does not own this trainset")
        current_year = self.get_year()
        original_price = trainset.get_price(current_year)
        sale_price = original_price // 2
        player_trainsets.remove(trainset)
        if trainset.get_line_assignment():
            line_name = trainset.get_line_assignment()
            line = self.game.get_map().get_lines_by_name().get(line_name)
            if line and trainset in line.vehicles:
                line.vehicles.remove(trainset)
            trainset.assign_to_line(None)
        self.add_player_money(player, sale_price, reason='Trainset sale')
        return {
            'trainset': trainset,
            'original_price': original_price,
            'sale_price': sale_price,
            'new_money': player.get_money()
        }
    
    def sell_locomotive_for_half_price(self, locomotive, player: Player) -> dict:
        """Sell a locomotive for half its original purchase price.
        All game logic in ui.py. Returns dict with sale info."""
        from vehicle import Locomotive
        if not isinstance(locomotive, Locomotive):
            raise ValueError("Can only sell locomotives")
        player_locomotives = getattr(player, 'locomotives', [])
        if locomotive not in player_locomotives:
            raise ValueError(f"Player {self.get_player_name(player)} does not own this locomotive")
        current_year = self.get_year()
        original_price = locomotive.get_price(current_year)
        sale_price = original_price // 2
        player_locomotives.remove(locomotive)
        self.add_player_money(player, sale_price, reason='Locomotive sale')
        return {
            'locomotive': locomotive,
            'original_price': original_price,
            'sale_price': sale_price,
            'new_money': player.get_money()
        }
    
    def sell_railcar_for_half_price(self, railcar, player: Player) -> dict:
        """Sell a railcar for half its original purchase price.
        All game logic in ui.py. Returns dict with sale info."""
        from vehicle import Railcar
        if not isinstance(railcar, Railcar):
            raise ValueError("Can only sell railcars")
        player_railcars = getattr(player, 'railcars', [])
        if railcar not in player_railcars:
            raise ValueError(f"Player {self.get_player_name(player)} does not own this railcar")
        current_year = self.get_year()
        original_price = railcar.get_price(current_year)
        sale_price = original_price // 2
        player_railcars.remove(railcar)
        self.add_player_money(player, sale_price, reason='Railcar sale')
        return {
            'railcar': railcar,
            'original_price': original_price,
            'sale_price': sale_price,
            'new_money': player.get_money()
        }

    def get_historical_society_items(self, player: Player, current_year: int) -> dict:
        """Eligible trainsets and spare locomotives (100+ years old). RULES.md: 5x catalog base price."""
        from vehicle import Trainset, Locomotive
        out_trainsets = []
        for ts in player.get_trainsets():
            loco = getattr(ts, 'locomotive', None)
            if loco is None or loco.year_introduced is None:
                continue
            years_old = current_year - loco.year_introduced
            if years_old < 100:
                continue
            price = ts.get_historical_society_price(current_year)
            if price <= 0:
                continue
            out_trainsets.append({
                'type': 'trainset',
                'item': ts,
                'name': getattr(ts.locomotive, 'name', 'Trainset'),
                'years_old': years_old,
                'price': price,
            })
        out_locos = []
        for loco in getattr(player, 'locomotives', []) or []:
            if not isinstance(loco, Locomotive) or loco.year_introduced is None:
                continue
            years_old = current_year - loco.year_introduced
            if years_old < 100:
                continue
            price = loco.get_historical_society_price(current_year)
            if price <= 0:
                continue
            out_locos.append({
                'type': 'locomotive',
                'item': loco,
                'name': loco.name,
                'years_old': years_old,
                'price': price,
            })
        return {'trainsets': out_trainsets, 'locomotives': out_locos}

    def sell_to_historical_society_complete(self, item, item_type: str, player: Player, current_year: int) -> dict:
        """Sell a trainset or spare locomotive to the Historical Society (5x catalog base, 100+ years)."""
        from vehicle import Trainset, Locomotive
        if current_year is None:
            current_year = self.get_year()
        if item_type == 'trainset':
            if not isinstance(item, Trainset):
                raise ValueError("Item must be a trainset")
            if item not in player.get_trainsets():
                raise ValueError(f"Player {self.get_player_name(player)} does not own this trainset")
            price = item.get_historical_society_price(current_year)
            if price <= 0:
                raise ValueError("Trainset is not eligible (must be 100+ years since locomotive introduction).")
            player.remove_trainset(item)
            if item.get_line_assignment():
                line_name = item.get_line_assignment()
                line = self.game.get_map().get_lines_by_name().get(line_name)
                if line and item in line.vehicles:
                    line.vehicles.remove(item)
                item.assign_to_line(None)
            self.add_player_money(player, price, reason='Historical Society')
            return {'price': price, 'new_money': player.get_money(), 'item_type': 'trainset'}
        if item_type == 'locomotive':
            if not isinstance(item, Locomotive):
                raise ValueError("Item must be a locomotive")
            locos = getattr(player, 'locomotives', []) or []
            if item not in locos:
                raise ValueError(f"Player {self.get_player_name(player)} does not own this locomotive")
            price = item.get_historical_society_price(current_year)
            if price <= 0:
                raise ValueError("Locomotive is not eligible (must be 100+ years since introduction).")
            locos.remove(item)
            self.add_player_money(player, price, reason='Historical Society')
            return {'price': price, 'new_money': player.get_money(), 'item_type': 'locomotive'}
        raise ValueError(f"Unknown item_type: {item_type!r}")

    def get_player_government_retention_bonus(self, player, prior_year: int) -> float:
        """Return 0.0-1.0: higher = government less likely to change in election year.
        Based on: no worker strikes, paying workers well, not losing lawsuits."""
        bonus = 0.5
        from worker import ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER
        for worker_type in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
            rental = player.get_worker_rental(worker_type.name) if hasattr(player, 'get_worker_rental') else None
            if not rental:
                continue
            if rental.is_on_strike():
                bonus -= 0.25
            elif rental.demanded_multiplier > 0:
                current = worker_type.get_salary_multiplier()
                if current < rental.demanded_multiplier - 0.3:
                    bonus -= 0.15
        last_loser = getattr(self.game, '_last_lawsuit_loser_player_id', None)
        if last_loser is not None and last_loser == self.get_player_id(player):
            bonus -= 0.2
        return max(0.0, min(1.0, bonus))

