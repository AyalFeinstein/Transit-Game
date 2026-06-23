from line import Line
from player import Player
from map import Map
from vehicle import Vehicle

class SaleItem:
    def __init__(self,
                 item,
                 price: int,
                 salesman: Player = None,
                 seller: str = "Manufacturer",
                 year_added: int = None,
                 sale_year: int = None):
        self.item = item
        self.price = price
        self.quantity_avalilable: int = 1
        self.salesman = salesman
        self.seller = seller  # String identifier for seller (default "Manufacturer")
        self.year_added = year_added  # Track when item was added to marketplace
        self.sale_year = sale_year  # Track when sale started (None if not on sale)

    def get_item(self):
        return self.item
    
    def get_price(self, current_year=1830):
        """Get price with supply and demand adjustment based on quantity and inflation"""
        if hasattr(self, 'original_price') and hasattr(self, 'sale_year') and self.sale_year == current_year:
            base_price = self.original_price
        else:
            base_price = self.price
        
        # Age-based discount: older items have cheaper BASE PRICE (applied before inflation)
        # Items older than 20 years get progressively cheaper (up to 30% discount for 40+ year old items)
        if self.year_added is not None:
            age_years = current_year - self.year_added
            if age_years > 20:
                # Linear discount from 0% at 20 years to 30% at 40+ years
                age_discount_percent = min(30.0, (age_years - 20) * 1.5)  # 1.5% per year after 20 years
                age_discount_factor = 1.0 - (age_discount_percent / 100.0)
                base_price = int(base_price * age_discount_factor)
        
        inflation_factor = 1.0 + (current_year - 1830) * (10.0 - 1.0) / (2050 - 1830)
        inflation_factor = max(1.0, min(10.0, inflation_factor))
        base_price = int(base_price * inflation_factor)
        if hasattr(self, 'discount_percent') and self.discount_percent > 0 and hasattr(self, 'sale_year') and self.sale_year == current_year:
            discount_factor = 1.0 - (self.discount_percent / 100.0)
            base_price = int(base_price * discount_factor)
        
        quantity = max(1, self.quantity_avalilable)
        supply_demand_multiplier = 1.0 + (1.0 / quantity) * 2.0
        supply_demand_multiplier = min(5.0, supply_demand_multiplier)
        
        from game_presets import MARKETPLACE_BULK_DISCOUNT_PERCENT, MARKETPLACE_BULK_DISCOUNT_THRESHOLD
        if quantity >= MARKETPLACE_BULK_DISCOUNT_THRESHOLD:
            additional_items = quantity - MARKETPLACE_BULK_DISCOUNT_THRESHOLD
            bulk_discount = additional_items * MARKETPLACE_BULK_DISCOUNT_PERCENT
            bulk_discount_factor = 1.0 - (bulk_discount / 100.0)
            supply_demand_multiplier = supply_demand_multiplier * bulk_discount_factor
        
        adjusted_price = int(base_price * supply_demand_multiplier)
        return adjusted_price
    
    def get_salesman(self):
        return self.salesman
    
    def get_seller(self):
        return self.seller
    
    def get_quantity_available(self):
        return self.quantity_avalilable
    
    def deduct_quantity(self, deduction: int = 1):
        self.quantity_avalilable -= deduction

    def add_quantity(self, addition: int = 1):
        self.quantity_avalilable += addition

    def __eq__(self, other):
        """Compare sale items by item properties, not price (since sales may vary)"""
        if isinstance(other, SaleItem):
            # Match by item, not price (allows for sales/discounts)
            if hasattr(self.item, 'name') and hasattr(other.item, 'name'):
                return self.item.name == other.item.name
            return self.item == other.item
        return False

class Marketplace:
    def __init__(self,
                 available_lines: list[Line] = [],
                 available_vehicles: list[Vehicle] = [],
                 marketplace_years_visible: int = 40):
        self.available_lines = available_lines
        self.available_vehicles = available_vehicles
        self.last_generation_year = None
        self.generation_interval = None
        self.worker_supply = {}
        self.marketplace_years_visible = marketplace_years_visible
        self.target_total_items = None
    
    def get_worker_supply(self, worker_type_name: str) -> int:
        """Get available supply of a worker type"""
        return self.worker_supply.get(worker_type_name, 0)
    
    def set_worker_supply(self, worker_type_name: str, quantity: int):
        """Set the available supply of a worker type"""
        self.worker_supply[worker_type_name] = max(0, quantity)
    
    def deduct_worker_supply(self, worker_type_name: str, quantity: int = 1):
        """Deduct from worker supply"""
        current = self.worker_supply.get(worker_type_name, 0)
        self.worker_supply[worker_type_name] = max(0, current - quantity)
    
    def add_worker_supply(self, worker_type_name: str, quantity: int = 1):
        """Add to worker supply"""
        current = self.worker_supply.get(worker_type_name, 0)
        self.worker_supply[worker_type_name] = current + quantity

    def get_available_lines(self):
        return self.available_lines

    def get_available_vehicles(self):
        return self.available_vehicles
    
    def get_available_items(self, current_year: int = None):
        """Get all available items (lines and vehicles) combined into a single list.
        Filters items based on current_year if provided (for trainsets)."""
        from vehicle import Trainset
        
        all_items = []
        
        # Add all lines
        all_items.extend(self.available_lines)
        
        # Add all vehicles, filtering by year if needed
        for sale_item in self.available_vehicles:
            vehicle = sale_item.get_item()
            if isinstance(vehicle, Trainset):
                # Check if trainset is available in current year
                if current_year is not None:
                    if hasattr(vehicle, 'locomotive') and vehicle.locomotive:
                        if hasattr(vehicle.locomotive, 'year_introduced'):
                            year_intro = vehicle.locomotive.year_introduced
                            if year_intro is not None and year_intro > current_year:
                                continue  # Skip trainsets not yet available
                all_items.append(sale_item)
            else:
                # Add non-trainset vehicles without year filtering
                all_items.append(sale_item)
        
        return all_items
    
    def add_line(self, line: Line, price: int=None, current_year: int=1830):
        if price is None:
            price = line.get_price(current_year=current_year)
        sale_item = SaleItem(item=line, price=price, year_added=current_year)
        if sale_item not in self.available_lines:
            self.available_lines.append(sale_item)
        else:
            for existing_item in self.available_lines:
                if existing_item == sale_item:
                    existing_item.add_quantity()

    def add_vehicle(self, vehicle, price: int=None, seller: str="Manufacturer", current_year: int=1830):
        if price is None:
            if hasattr(vehicle, 'get_price'):
                import inspect
                sig = inspect.signature(vehicle.get_price)
                if 'current_year' in sig.parameters:
                    price = vehicle.get_price(current_year=current_year)
                else:
                    price = vehicle.get_price()
            else:
                price = 500  # Default price
        sale_item = SaleItem(item=vehicle, price=price, seller=seller, year_added=current_year)
        if sale_item not in self.available_vehicles:
            self.available_vehicles.append(sale_item)
        else:
            for existing_item in self.available_vehicles:
                if existing_item == sale_item:
                    existing_item.add_quantity()

    def remove_line(self, line: Line):
        for existing_item in self.available_lines:
            if existing_item.item == line:
                existing_item.deduct_quantity()
                if existing_item.get_quantity_available() <= 0:
                    self.available_lines.remove(existing_item)
                break

    def remove_vehicle(self, vehicle: Vehicle):
        """Remove vehicle from marketplace, matching by properties for networked games"""
        from vehicle import Trainset, Locomotive, Railcar
        
        for existing_item in self.available_vehicles:
            existing_vehicle = existing_item.item
            
            # Match vehicles by properties (works across serialization/deserialization)
            # First try object_id if both have it
            if hasattr(existing_vehicle, 'object_id') and hasattr(vehicle, 'object_id'):
                if existing_vehicle.object_id is not None and vehicle.object_id is not None:
                    if existing_vehicle.object_id == vehicle.object_id and existing_vehicle.name == vehicle.name:
                        existing_item.deduct_quantity()
                        if existing_item.get_quantity_available() <= 0:
                            self.available_vehicles.remove(existing_item)
                        return
            
            # Fallback: match by type and properties
            if isinstance(vehicle, Trainset) and isinstance(existing_vehicle, Trainset):
                # Match trainsets by locomotive and cars
                if (hasattr(existing_vehicle.locomotive, 'name') and hasattr(vehicle.locomotive, 'name') and
                    existing_vehicle.locomotive.name == vehicle.locomotive.name and
                    len(existing_vehicle.cars) == len(vehicle.cars)):
                    # Check if all cars match
                    cars_match = all(
                        hasattr(c1, 'name') and hasattr(c2, 'name') and c1.name == c2.name
                        for c1, c2 in zip(existing_vehicle.cars, vehicle.cars)
                    )
                    if cars_match:
                        existing_item.deduct_quantity()
                        if existing_item.get_quantity_available() <= 0:
                            self.available_vehicles.remove(existing_item)
                        return
            elif isinstance(vehicle, Locomotive) and isinstance(existing_vehicle, Locomotive):
                # Match locomotives by name and key properties
                if (hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and
                    existing_vehicle.name == vehicle.name and
                    hasattr(existing_vehicle, 'capacity') and hasattr(vehicle, 'capacity') and
                    existing_vehicle.capacity == vehicle.capacity):
                    existing_item.deduct_quantity()
                    if existing_item.get_quantity_available() <= 0:
                        self.available_vehicles.remove(existing_item)
                    return
            elif isinstance(vehicle, (Ferry, Bus)) and isinstance(existing_vehicle, (Ferry, Bus)):
                # Match ferries and buses by name and key properties
                from vehicle import Ferry, Bus
                if (hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and
                    existing_vehicle.name == vehicle.name and
                    hasattr(existing_vehicle, 'capacity') and hasattr(vehicle, 'capacity') and
                    existing_vehicle.capacity == vehicle.capacity):
                    existing_item.deduct_quantity()
                    if existing_item.get_quantity_available() <= 0:
                        self.available_vehicles.remove(existing_item)
                    return
            elif isinstance(vehicle, Railcar) and isinstance(existing_vehicle, Railcar):
                # Match railcars by name and capacity
                if (hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and
                    existing_vehicle.name == vehicle.name and
                    hasattr(existing_vehicle, 'capacity') and hasattr(vehicle, 'capacity') and
                    existing_vehicle.capacity == vehicle.capacity):
                    existing_item.deduct_quantity()
                    if existing_item.get_quantity_available() <= 0:
                        self.available_vehicles.remove(existing_item)
                    return
            
            # Last resort: exact object match (works for local games)
            if existing_item.item == vehicle:
                existing_item.deduct_quantity()
                if existing_item.get_quantity_available() <= 0:
                    self.available_vehicles.remove(existing_item)
                break
    
    def _get_vehicle_categories(self, registry):
        """Organize vehicles into categories based on vehicle_presets.py structure.
        Categories match the multi-line string markers in vehicle_presets.py"""
        from game_resource import Coal, Wood, Oil, Electricity
        
        categories = {
            'Steam Locomotives': [],
            'Diesel Locomotives': [],
            'Diesel Railcars': [],
            'Trolleys/LRVs Cars': [],
            'Subway Cars': [],
            'Electric Locomotives': [],
            'EMUs': [],
            'Ferries': [],
            'Horse-Drawn Vehicles': [],
            'Passenger Railcars': []
        }
        
        # Get all locomotives and railcars
        all_locomotives = registry.get_all_locomotives()
        all_railcars = registry.get_all_railcars()
        
        # Categorize locomotives
        for loco in all_locomotives:
            loco_name = loco.name.lower()
            fuel_type = loco.fuel.__class__.__name__
            can_have_cars = getattr(loco, 'can_have_railcars', True)
            needs_third_rail = getattr(loco, 'needs_third_rail', False)
            needs_overhead_wire = getattr(loco, 'needs_overhead_wire', False)
            
            # Ferries - identified by name containing "ferry" (case-insensitive check)
            if 'ferry' in loco_name:
                categories['Ferries'].append(loco)
            # Horse-Drawn Vehicles - fuel is Food or name contains "horse"
            elif fuel_type == 'Food' or 'horse' in loco_name:
                categories['Horse-Drawn Vehicles'].append(loco)
            # Subway Cars - third rail, self-contained
            elif needs_third_rail and not can_have_cars:
                categories['Subway Cars'].append(loco)
            # Trolleys/LRVs - overhead wire, self-contained, no third rail
            elif needs_overhead_wire and not can_have_cars and not needs_third_rail:
                categories['Trolleys/LRVs Cars'].append(loco)
            # Diesel Railcars - diesel fuel, self-contained or multiple unit
            elif fuel_type == 'Oil' and ('railcar' in loco_name or 'multiple unit' in loco_name):
                categories['Diesel Railcars'].append(loco)
            # EMUs - electric, self-contained or multiple unit
            elif fuel_type == 'Electricity' and ('emu' in loco_name or 'interurban' in loco_name or (not can_have_cars and needs_overhead_wire and 'subway' not in loco_name)):
                categories['EMUs'].append(loco)
            # Electric Locomotives - electric, can have railcars, overhead wire
            elif fuel_type == 'Electricity' and can_have_cars and needs_overhead_wire:
                categories['Electric Locomotives'].append(loco)
            # Diesel Locomotives - diesel, can have railcars
            elif fuel_type == 'Oil' and can_have_cars:
                categories['Diesel Locomotives'].append(loco)
            # Steam Locomotives - coal or wood fuel
            elif fuel_type in ('Coal', 'Wood'):
                categories['Steam Locomotives'].append(loco)
        
        # All railcars go to Passenger Railcars category
        for railcar in all_railcars:
            categories['Passenger Railcars'].append(railcar)
        
        return categories
    
    def generate_trainsets_based_on_discoveries(self, current_year: int):
        """Generate or replenish trainsets organized by categories with proper availability rules.
        - Target total from MARKETPLACE_TARGET_TOTAL_ITEMS, distributed evenly among available item types
        - Items must be within 40 years before current year
        - Items within 20 years before current year are always on display
        - Random sales up to 30% off (sales last only one turn)
        - Replenishes items that were bought to maintain target total"""
        import random
        from vehicle import Trainset, Railcar
        from vehicle_presets import get_vehicle_registry
        from game_presets import MARKETPLACE_TARGET_TOTAL_ITEMS
        for item in self.available_vehicles:
            if hasattr(item, 'sale_year') and item.sale_year is not None:
                if item.sale_year < current_year:
                    if hasattr(item, 'original_price'):
                        item.price = item.original_price  
                        delattr(item, 'original_price')
                    if hasattr(item, 'discount_percent'):
                        delattr(item, 'discount_percent')
                    item.sale_year = None
                    item.has_had_sale = True
            else:
                if not hasattr(item, 'has_had_sale') or not item.has_had_sale:
                    if random.random() < 0.15:
                        # Store original price before applying discount
                        if not hasattr(item, 'original_price'):
                            item.original_price = item.price
                        item.discount_percent = int(random.uniform(0.05, 0.30) * 100)
                        item.sale_year = current_year
        
        registry = get_vehicle_registry()
        
        # 40 year window for availability
        min_year = current_year - 40
        # 20 year window for always-available items
        always_available_min_year = current_year - 20
        
        # Calculate current total items (sum of all quantities)
        current_total = sum(item.get_quantity_available() for item in self.available_vehicles)
        is_first_generation = (self.target_total_items is None)
        
        # First time: use target from presets and distribute among available types
        if is_first_generation:
            self.target_total_items = MARKETPLACE_TARGET_TOTAL_ITEMS
            # Clear existing vehicles (except lines and workers)
            self.available_vehicles = []
        
        # Get vehicles organized by category
        categories = self._get_vehicle_categories(registry)
        
        # Get all railcars for trainset creation
        all_railcars = registry.get_railcars_by_year()
        available_railcars = [(car, year) for car, year in all_railcars if min_year <= year <= current_year]
        if not available_railcars:
            available_railcars = [(registry.get_earliest_railcar(), 1830)]
        
        # First pass: collect all unique vehicle types and their "multiplier" (items added per quantity: 2 for loco+trainset, 1 else)
        from vehicle import Locomotive, Railcar, Ferry, Bus
        type_list = []  # (vehicle_name, vehicle, year, mult) where mult=2 if creates trainset too else 1
        for category_name, vehicles in categories.items():
            if not vehicles:
                continue
            valid_vehicles = []
            for vehicle in vehicles:
                year = getattr(vehicle, 'year_introduced', None)
                if year is not None and min_year <= year <= current_year:
                    valid_vehicles.append((vehicle, year))
            if not valid_vehicles:
                continue
            valid_vehicles.sort(key=lambda x: x[1], reverse=True)
            seen_names = set()
            for vehicle, year in valid_vehicles:
                if vehicle.name in seen_names:
                    continue
                seen_names.add(vehicle.name)
                is_loco = isinstance(vehicle, Locomotive)
                is_ferry = isinstance(vehicle, Ferry)
                is_bus = isinstance(vehicle, Bus)
                is_standalone = is_loco or is_ferry or is_bus
                creates_trainset = is_loco and getattr(vehicle, 'can_have_railcars', True)
                mult = 2 if (is_standalone and creates_trainset) else 1
                type_list.append((vehicle.name, vehicle, year, mult))
        
        # Distribute target total among types: each type gets at least 1, rest so sum(quantity*mult) = target
        target_total = MARKETPLACE_TARGET_TOTAL_ITEMS
        num_types = len(type_list)
        if num_types == 0:
            quantity_per_type = []
        else:
            mults = [t[3] for t in type_list]
            quantity_per_type = [1] * num_types  # each type gets at least 1
            used = sum(mults)
            extra = max(0, target_total - used)
            # Distribute extra items: each +1 to quantity[i] adds mult[i] items
            while extra > 0:
                added = False
                for i in range(num_types):
                    if extra >= mults[i]:
                        quantity_per_type[i] += 1
                        extra -= mults[i]
                        added = True
                        break
                if not added:
                    break
        
        type_index = 0
        
        # Process each category
        for category_name, vehicles in categories.items():
            if not vehicles:
                continue
            
            # Filter vehicles by 40-year window
            valid_vehicles = []
            for vehicle in vehicles:
                year = getattr(vehicle, 'year_introduced', None)
                if year is not None and min_year <= year <= current_year:
                    valid_vehicles.append((vehicle, year))
            
            if not valid_vehicles:
                continue
            
            # Sort by year (most recent first)
            valid_vehicles.sort(key=lambda x: x[1], reverse=True)
            
            # Get unique vehicle types (by name)
            vehicle_types = {}
            for vehicle, year in valid_vehicles:
                vehicle_name = vehicle.name
                if vehicle_name not in vehicle_types:
                    vehicle_types[vehicle_name] = (vehicle, year)
            
            for vehicle_name, (vehicle, year) in vehicle_types.items():
                is_locomotive = isinstance(vehicle, Locomotive)
                is_ferry = isinstance(vehicle, Ferry)
                is_bus = isinstance(vehicle, Bus)
                is_standalone_vehicle = is_locomotive or is_ferry or is_bus
                
                # Quantity from distributed target (same total items, spread across available types)
                if type_index < len(quantity_per_type):
                    quantity = max(1, quantity_per_type[type_index])
                    type_index += 1
                else:
                    quantity = max(1, target_total // max(1, num_types))
                
                # Add quantity of this type (first generation only)
                for q in range(quantity):
                    if is_standalone_vehicle:
                        can_have_cars = getattr(vehicle, 'can_have_railcars', True) if is_locomotive else False
                        
                        # Add standalone locomotive with its own sale status
                        loco_is_on_sale = random.random() < 0.15  # 15% chance of sale
                        loco_sale_discount = random.uniform(0.05, 0.30) if loco_is_on_sale else 0.0
                        
                        # Get base price from vehicle (if set), otherwise use default
                        if hasattr(vehicle, 'base_price') and vehicle.base_price is not None:
                            loco_base_price = vehicle.base_price
                        else:
                            # Fallback: calculate based on year (older = cheaper)
                            if vehicle.year_introduced:
                                years_old = current_year - vehicle.year_introduced
                                age_discount = min(0.5, years_old / 200.0)
                                loco_base_price = int(1000 * (1 - age_discount))
                            else:
                                loco_base_price = 500
                        
                        # Store base price (without inflation) - inflation will be applied in get_price() based on current_year
                        loco_sale_item = SaleItem(item=vehicle, price=loco_base_price, seller="Manufacturer", year_added=current_year, sale_year=current_year if loco_is_on_sale else None)
                        if loco_is_on_sale:
                            loco_sale_item.original_price = loco_base_price
                            loco_sale_item.discount_percent = int(loco_sale_discount * 100)
                        self.available_vehicles.append(loco_sale_item)
                        
                        # Also create trainset if locomotive can have railcars (only for locomotives, not ferries/buses)
                        if can_have_cars and is_locomotive:
                            trainset_is_on_sale = random.random() < 0.15  # 15% chance of sale
                            trainset_sale_discount = random.uniform(0.05, 0.30) if trainset_is_on_sale else 0.0
                            
                            cars = []
                            # Check if locomotive has an associated railcar (for EMUs/DMUs)
                            associated_railcar_name = getattr(vehicle, 'associated_railcar_name', None)
                            if associated_railcar_name:
                                # Find the associated railcar
                                chosen_car = None
                                for car, car_year in available_railcars:
                                    if car.name == associated_railcar_name and (min_year is None or min_year <= car_year) and car_year <= current_year:
                                        chosen_car = car
                                        break
                                
                                if not chosen_car:
                                    # Try to find it in all railcars (might be outside year window)
                                    all_railcars_list = registry.get_all_railcars()
                                    for car in all_railcars_list:
                                        if car.name == associated_railcar_name:
                                            chosen_car = car
                                            break
                                
                                if chosen_car:
                                    # Use associated railcar - random number up to max_cars
                                    max_cars = getattr(vehicle, 'max_cars', None) or 3
                                    num_cars = random.randint(1, min(max_cars, 3))
                                    for i in range(num_cars):
                                        car = Railcar(f"{chosen_car.name}_{i}", i, chosen_car.capacity, chosen_car.maitnance_costs)
                                        car.year_introduced = getattr(chosen_car, 'year_introduced', None)
                                        cars.append(car)
                                    
                                    # Also add the associated railcar as a standalone item in marketplace
                                    if chosen_car not in [item.item for item in self.available_vehicles if isinstance(item.item, Railcar) and item.item.name == chosen_car.name]:
                                        # Get base price from railcar (if set), otherwise use default
                                        if hasattr(chosen_car, 'base_price') and chosen_car.base_price is not None:
                                            car_base_price = chosen_car.base_price
                                        else:
                                            # Fallback: calculate based on year
                                            if chosen_car.year_introduced:
                                                years_old = current_year - chosen_car.year_introduced
                                                age_discount = min(0.5, years_old / 200.0)
                                                car_base_price = int(200 * (1 - age_discount))
                                            else:
                                                car_base_price = 100
                                        car_sale_item = SaleItem(item=chosen_car, price=car_base_price, seller="Manufacturer", year_added=current_year)
                                        self.available_vehicles.append(car_sale_item)
                                else:
                                    print(f"WARNING: Could not find associated railcar '{associated_railcar_name}' for {vehicle.name}")
                            else:
                                # No associated railcar - choose random railcar
                                # Choose railcar - prefer items within 20 year window if available
                                recent_cars = [(car, y) for car, y in available_railcars if always_available_min_year <= y <= current_year]
                                if recent_cars:
                                    chosen_car, car_year = random.choice(recent_cars)
                                else:
                                    chosen_car, car_year = random.choice(available_railcars)
                                
                                # Random number of cars (1-3)
                                num_cars = random.randint(1, 3)
                                for i in range(num_cars):
                                    car = Railcar(f"{chosen_car.name}_{i}", i, chosen_car.capacity, chosen_car.maitnance_costs)
                                    car.year_introduced = getattr(chosen_car, 'year_introduced', None)
                                    cars.append(car)
                            
                            trainset = Trainset(locomotive=vehicle, cars=cars)
                            # Get base price without inflation (reverse-calculate from inflated price)
                            if hasattr(trainset, 'get_price'):
                                inflated_price = trainset.get_price(current_year=current_year)
                                # Reverse inflation to get base price (trainset uses 5.5x max inflation)
                                inflation_factor = 1.0 + (current_year - 1830) * (5.5 - 1.0) / (2050 - 1830)
                                inflation_factor = max(1.0, min(10.0, inflation_factor))
                                base_price = int(inflated_price / inflation_factor) if inflation_factor > 0 else inflated_price
                            else:
                                base_price = 500
                            
                            # Store base price (without inflation) - inflation will be applied in get_price() based on current_year
                            sale_item = SaleItem(item=trainset, price=base_price, seller="Manufacturer", year_added=current_year, sale_year=current_year if trainset_is_on_sale else None)
                            if trainset_is_on_sale:
                                sale_item.original_price = base_price
                                sale_item.discount_percent = int(trainset_sale_discount * 100)
                            self.available_vehicles.append(sale_item)
                    else:
                        # Railcar - add with its own sale status
                        railcar_is_on_sale = random.random() < 0.15  # 15% chance of sale
                        railcar_sale_discount = random.uniform(0.05, 0.30) if railcar_is_on_sale else 0.0
                        
                        # Get base price without inflation (reverse-calculate from inflated price)
                        if hasattr(vehicle, 'get_price'):
                            inflated_price = vehicle.get_price(current_year=current_year)
                            # Reverse inflation to get base price (vehicles use 5.5x max inflation)
                            inflation_factor = 1.0 + (current_year - 1830) * (5.5 - 1.0) / (2050 - 1830)
                            inflation_factor = max(1.0, min(10.0, inflation_factor))
                            base_price = int(inflated_price / inflation_factor) if inflation_factor > 0 else inflated_price
                        else:
                            base_price = 200
                        
                        # Store base price (without inflation) - inflation will be applied in get_price() based on current_year
                        sale_item = SaleItem(item=vehicle, price=base_price, seller="Manufacturer", year_added=current_year, sale_year=current_year if railcar_is_on_sale else None)
                        if railcar_is_on_sale:
                            sale_item.original_price = base_price
                            sale_item.discount_percent = int(railcar_sale_discount * 100)
                        self.available_vehicles.append(sale_item)
        
        # Replenishment mode: maintain target total (only when not first generation)
        if not is_first_generation:
            from game_presets import (
                MARKETPLACE_REPLENISHMENT_START_YEAR,
                MARKETPLACE_REPLENISHMENT_END_YEAR,
                MARKETPLACE_TARGET_MAX_MULTIPLIER,
            )
            # Effective target increases over time (more replenishment per turn in later years)
            year_range = MARKETPLACE_REPLENISHMENT_END_YEAR - MARKETPLACE_REPLENISHMENT_START_YEAR
            progress = (current_year - MARKETPLACE_REPLENISHMENT_START_YEAR) / max(1, year_range)
            progress = max(0.0, min(1.0, progress))
            multiplier = 1.0 + progress * (MARKETPLACE_TARGET_MAX_MULTIPLIER - 1.0)
            effective_target = int(self.target_total_items * multiplier)
            # Replenishment: bring total up to effective target
            items_needed = effective_target - current_total

            if items_needed > 0:
                # Get vehicles organized by category
                categories = self._get_vehicle_categories(registry)
                
                # Get all railcars for trainset creation
                all_railcars = registry.get_railcars_by_year()
                available_railcars = [(car, year) for car, year in all_railcars if min_year <= year <= current_year]
                if not available_railcars:
                    available_railcars = [(registry.get_earliest_railcar(), 1830)]
                
                # Collect all valid vehicles for replenishment (deduplicated by name)
                all_valid_vehicles = []
                seen_vehicles = set()
                for category_name, vehicles in categories.items():
                    if not vehicles:
                        continue
                    for vehicle in vehicles:
                        year = getattr(vehicle, 'year_introduced', None)
                        if year is not None and min_year <= year <= current_year:
                            vehicle_name = getattr(vehicle, 'name', None)
                            if vehicle_name and vehicle_name not in seen_vehicles:
                                seen_vehicles.add(vehicle_name)
                                all_valid_vehicles.append((vehicle, year))
                
                # Replenish items - add quantity to existing items first, then create new ones
                items_added = 0
                from vehicle import Trainset, Locomotive, Railcar
                
                # First pass: add quantity to existing items that need it (random order)
                existing_items_list = list(self.available_vehicles)
                random.shuffle(existing_items_list)
                
                for existing_item in existing_items_list:
                    if items_added >= items_needed:
                        break
                    existing_vehicle = existing_item.get_item()
                    
                    # Find matching vehicle in all_valid_vehicles
                    for vehicle, year in all_valid_vehicles:
                        if items_added >= items_needed:
                            break
                        from vehicle import Locomotive, Railcar, Ferry, Bus
                        is_locomotive = isinstance(vehicle, Locomotive)
                        is_ferry = isinstance(vehicle, Ferry)
                        is_bus = isinstance(vehicle, Bus)
                        is_standalone_vehicle = is_locomotive or is_ferry or is_bus
                        
                        # Match standalone locomotives, ferries, or buses
                        if is_standalone_vehicle and isinstance(existing_vehicle, (Locomotive, Ferry, Bus)) and not isinstance(existing_vehicle, Trainset):
                            if hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and existing_vehicle.name == vehicle.name:
                                existing_item.add_quantity()
                                items_added += 1
                                break
                        # Match trainsets (by locomotive name) - only for locomotives
                        elif isinstance(existing_vehicle, Trainset) and is_locomotive:
                            if hasattr(existing_vehicle, 'locomotive') and existing_vehicle.locomotive:
                                if hasattr(existing_vehicle.locomotive, 'name') and hasattr(vehicle, 'name') and existing_vehicle.locomotive.name == vehicle.name:
                                    existing_item.add_quantity()
                                    items_added += 1
                                    break
                        # Match railcars
                        elif not is_standalone_vehicle and isinstance(existing_vehicle, Railcar):
                            if hasattr(existing_vehicle, 'name') and hasattr(vehicle, 'name') and existing_vehicle.name == vehicle.name:
                                existing_item.add_quantity()
                                items_added += 1
                                break
                
                # Second pass: if still need items, create new ones (only if they truly don't exist)
                # Build a set of existing vehicle names to avoid duplicates
                # Also track how many of each type we have to avoid creating too many
                existing_vehicle_names = set()
                vehicle_counts = {}  # Track quantity per vehicle type
                for existing_item in self.available_vehicles:
                    existing_vehicle = existing_item.get_item()
                    quantity = existing_item.get_quantity_available()
                    if isinstance(existing_vehicle, Locomotive) and not isinstance(existing_vehicle, Trainset):
                        if hasattr(existing_vehicle, 'name'):
                            key = ('locomotive', existing_vehicle.name)
                            existing_vehicle_names.add(key)
                            vehicle_counts[key] = vehicle_counts.get(key, 0) + quantity
                    elif isinstance(existing_vehicle, Trainset):
                        if hasattr(existing_vehicle, 'locomotive') and existing_vehicle.locomotive:
                            if hasattr(existing_vehicle.locomotive, 'name'):
                                key = ('trainset', existing_vehicle.locomotive.name)
                                existing_vehicle_names.add(key)
                                vehicle_counts[key] = vehicle_counts.get(key, 0) + quantity
                    elif isinstance(existing_vehicle, Railcar):
                        if hasattr(existing_vehicle, 'name'):
                            key = ('railcar', existing_vehicle.name)
                            existing_vehicle_names.add(key)
                            vehicle_counts[key] = vehicle_counts.get(key, 0) + quantity
                
                random.shuffle(all_valid_vehicles)
                for vehicle, year in all_valid_vehicles:
                    if items_added >= items_needed:
                        break
                    from vehicle import Locomotive, Ferry, Bus, Railcar
                    is_locomotive = isinstance(vehicle, Locomotive)
                    is_ferry = isinstance(vehicle, Ferry)
                    is_bus = isinstance(vehicle, Bus)
                    is_standalone_vehicle = is_locomotive or is_ferry or is_bus
                    
                    # Check if this vehicle type already exists
                    vehicle_key = None
                    if is_locomotive:
                        vehicle_key = ('locomotive', vehicle.name)
                    elif is_ferry:
                        vehicle_key = ('ferry', vehicle.name)
                    elif is_bus:
                        vehicle_key = ('bus', vehicle.name)
                    elif isinstance(vehicle, Railcar):
                        vehicle_key = ('railcar', vehicle.name)
                    
                    # Only create new item if it doesn't exist, we still need items, and we haven't exceeded target
                    # Also check if we've already created enough of this type (target is typically 2 per type)
                    if vehicle_key and vehicle_key not in existing_vehicle_names and items_added < items_needed:
                        # Check if we've already created enough of this type (prevent over-creation)
                        if vehicle_key in vehicle_counts:
                            if vehicle_counts[vehicle_key] >= 2:  # Already have 2 of this type
                                continue
                        if is_locomotive or is_ferry or is_bus:
                            can_have_cars = getattr(vehicle, 'can_have_railcars', True) if is_locomotive else False
                            
                            # Add standalone vehicle (locomotive, ferry, or bus)
                            vehicle_is_on_sale = random.random() < 0.15
                            vehicle_sale_discount = random.uniform(0.05, 0.30) if vehicle_is_on_sale else 0.0
                            
                            if hasattr(vehicle, 'get_price'):
                                vehicle_base_price = vehicle.get_price(current_year=current_year)
                            else:
                                vehicle_base_price = 300
                            
                            # Store original price (before discount) - discount will be applied in get_price() after inflation
                            vehicle_sale_item = SaleItem(item=vehicle, price=vehicle_base_price, seller="Manufacturer", year_added=current_year, sale_year=current_year if vehicle_is_on_sale else None)
                            if vehicle_is_on_sale:
                                vehicle_sale_item.original_price = vehicle_base_price
                                vehicle_sale_item.discount_percent = int(vehicle_sale_discount * 100)
                            self.available_vehicles.append(vehicle_sale_item)
                            items_added += 1
                            # Update tracking to prevent duplicates
                            existing_vehicle_names.add(vehicle_key)
                            vehicle_counts[vehicle_key] = vehicle_counts.get(vehicle_key, 0) + 1
                            
                            # Also create trainset if locomotive can have railcars (only for locomotives)
                            if can_have_cars and is_locomotive:
                                trainset_is_on_sale = random.random() < 0.15
                                trainset_sale_discount = random.uniform(0.05, 0.30) if trainset_is_on_sale else 0.0
                                
                                cars = []
                                associated_railcar_name = getattr(vehicle, 'associated_railcar_name', None)
                                if associated_railcar_name:
                                    # Find matching railcar
                                    for railcar, railcar_year in all_valid_vehicles:
                                        if isinstance(railcar, Railcar) and hasattr(railcar, 'name') and railcar.name == associated_railcar_name:
                                            cars = [railcar]
                                            break
                                
                                from vehicle import Trainset
                                trainset = Trainset(locomotive=vehicle, cars=cars)
                                
                                if hasattr(trainset, 'get_price'):
                                    trainset_base_price = trainset.get_price(current_year=current_year)
                                else:
                                    trainset_base_price = 500
                                
                                trainset_sale_item = SaleItem(item=trainset, price=trainset_base_price, seller="Manufacturer", year_added=current_year, sale_year=current_year if trainset_is_on_sale else None)
                                if trainset_is_on_sale:
                                    trainset_sale_item.original_price = trainset_base_price
                                    trainset_sale_item.discount_percent = int(trainset_sale_discount * 100)
                                self.available_vehicles.append(trainset_sale_item)
                        else:
                            # Railcar
                            railcar_is_on_sale = random.random() < 0.15
                            railcar_sale_discount = random.uniform(0.05, 0.30) if railcar_is_on_sale else 0.0
                            
                            if hasattr(vehicle, 'get_price'):
                                base_price = vehicle.get_price(current_year=current_year)
                            else:
                                base_price = 200
                            
                            # Store original price (before discount) - discount will be applied in get_price() after inflation
                            sale_item = SaleItem(item=vehicle, price=base_price, seller="Manufacturer", year_added=current_year, sale_year=current_year if railcar_is_on_sale else None)
                            if railcar_is_on_sale:
                                sale_item.original_price = base_price
                                sale_item.discount_percent = int(railcar_sale_discount * 100)
                            self.available_vehicles.append(sale_item)
                            items_added += 1
                            # Update tracking to prevent duplicates
                            if vehicle_key:
                                existing_vehicle_names.add(vehicle_key)
                                vehicle_counts[vehicle_key] = vehicle_counts.get(vehicle_key, 0) + 1
        self.last_generation_year = current_year
    
    def add_trainsets_for_new_technology(self, current_year: int, newly_researched_vehicles: list):
        """Regenerate marketplace when new technology is researched.
        This ensures the marketplace is updated with new categories and availability."""
        # Regenerate entire marketplace to ensure proper categorization and availability
        self.generate_trainsets_based_on_discoveries(current_year)

    
