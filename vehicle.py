from game_resource import Resource


class Vehicle:
    def __init__(self,
                 name: str,
                 object_id: int = None,
                 maitnance_costs: int = 100,
                 year_introduced: int = None,
                 base_price: int = None,
                 obselete_by: str = None):
        self.name = name
        self.object_id = object_id
        self.maitnance_costs = maitnance_costs
        self.year_introduced = year_introduced
        self.purchase_year = None
        self.base_price = base_price  # Base price before inflation (older vehicles have lower base prices)
        self.obselete_by = obselete_by

    def __repr__(self):
        parts = [f"Vehicle(name={self.name!r}"]
        if self.year_introduced is not None:
            parts.append(f", year_introduced={self.year_introduced}")
        if self.base_price is not None:
            parts.append(f", base_price={self.base_price}")
        parts.append(f", maitnance_costs={self.maitnance_costs})")
        return "".join(parts)

    def get_maitnance_costs(self, current_year: int = None):
        """Get maintenance costs, with dramatic increase for outdated locomotives"""
        base_cost = self.maitnance_costs
        
        # Only apply outdated penalty to locomotives (not railcars)
        if not isinstance(self, Locomotive):
            return base_cost
        
        if current_year is None:
            return base_cost
        
        age_year = getattr(self, 'purchase_year', None)
        if age_year is None:
            age_year = self.year_introduced
        if age_year is None:
            return base_cost
        
        # Age from purchase (or introduction if legacy save); inflation is handled separately elsewhere.
        years_outdated = int(current_year) - int(age_year)
        
        # Dramatic increase: maintenance doubles every 20 years after introduction
        # After 40 years, it's 4x, after 60 years it's 8x, etc. (capped at 20x).
        if years_outdated > 20:
            exponent = (years_outdated - 20) / 20.0
            outdated_factor = 20.0 if exponent >= 4.32 else (2.0 ** exponent)
            return int(base_cost * outdated_factor)
        
        return base_cost


class Locomotive(Vehicle):
    def __init__(self, name: str,
                 fuel: Resource,
                 miles_per_unit: float,
                 top_speed: float,
                 average_speed: float,
                 capacity: int = 0,
                 object_id: int = None,
                 needs_third_rail: bool = False,
                 needs_overhead_wire: bool = False,
                 maitnance_costs: int = 100,
                 year_introduced: int = None,
                 can_have_railcars: bool = True,
                 max_cars: int = None,
                 associated_railcar_name: str = None,
                 quality: float = 1.0,
                 maintenance_workers: int = 0,
                 base_price: int = None,
                 obselete_by: str = None):
        super().__init__(name, object_id, maitnance_costs, year_introduced, base_price, obselete_by)
        self.fuel = fuel
        self.miles_per_unit = miles_per_unit
        self.top_speed = top_speed
        self.average_speed = average_speed
        self.capacity = capacity
        self.needs_third_rail = needs_third_rail
        self.needs_overhead_wire = needs_overhead_wire
        self.can_have_railcars = can_have_railcars
        self.max_cars = max_cars  # Maximum number of railcars this locomotive can pull (None = unlimited)
        self.associated_railcar_name = associated_railcar_name  # Name of associated railcar for DMU/EMU
        self.quality = quality  # Quality rating (1.0 base, can be improved with maintenance)
        self.maintenance_workers = maintenance_workers  # Maintenance workers assigned to this locomotive
        self.obselete_by = obselete_by
    
    def update_quality(self):
        """Update quality based on maintenance workers (1.0 base + 0.1 per worker, max 9.0 for locomotives).
        Note: Quality range is 1.0-15.0, but locomotives are capped at 9.0 (10.0-15.0 reserved for first class railcars)."""
        self.quality = min(9.0, 1.0 + (self.maintenance_workers * 0.1))
    
    def get_price(self, current_year=1830):
        """Calculate price for a locomotive with inflation"""
        if self.base_price is None:
            # Fallback: estimate price based on maintenance costs
            base_price = self.maitnance_costs * 3
        else:
            base_price = self.base_price
        from inflation_utils import get_inflation_factor
        return int(base_price * get_inflation_factor(current_year))

    def get_catalog_base_price(self) -> int:
        """Intro-era catalog price (sum of base_price fields, no inflation). Used for Historical Society."""
        if self.base_price is not None:
            return int(self.base_price)
        return int(self.maitnance_costs * 3)

    def get_historical_society_price(self, current_year: int) -> int:
        """Spare locomotive: 5x catalog base price if 100+ years since year_introduced (RULES.md)."""
        if self.year_introduced is None:
            return 0
        if current_year - self.year_introduced < 100:
            return 0
        return int(self.get_catalog_base_price() * 5)

class Railcar(Vehicle):
    def __init__(self, name: str,
                 object_id: int,
                 capacity: int,
                 maitnance_costs: int,
                 quality: float = 1.0,
                 year_introduced: int = None,
                 base_price: int = None,
                 obselete_by: str = None):
        super().__init__(name, object_id, maitnance_costs, year_introduced, base_price, obselete_by)
        self.capacity = capacity
        self.quality = quality  # Quality rating (fixed, cannot be improved with maintenance workers)

    def get_price(self, current_year=1830):
        """Calculate price for a railcar with inflation"""
        if self.base_price is None:
            # Fallback: estimate price based on maintenance costs
            base_price = self.maitnance_costs * 2
        else:
            base_price = self.base_price
        from inflation_utils import get_inflation_factor
        return int(base_price * get_inflation_factor(current_year))

    def __repr__(self):
        return f"Railcar(name={self.name!r}, capacity={self.capacity}, maitnance_costs={self.maitnance_costs}, base_price={self.base_price})"


class Trainset:
    DEFAULT_MAX_RAILCARS = 5  # Default maximum number of railcars per trainset (used if locomotive doesn't specify)
    
    def __init__(self,
                 locomotive: Locomotive,
                 cars: list[Railcar],
                 line_assignment: str = None):
        self.locomotive = locomotive
        
        # Check if locomotive can have railcars attached
        if cars and not locomotive.can_have_railcars:
            raise ValueError(f"{locomotive.name} cannot have railcars attached (it is a self-contained unit)")
        
        # Get max cars from locomotive, or use default
        max_allowed = locomotive.max_cars if locomotive.max_cars is not None else self.DEFAULT_MAX_RAILCARS
        if len(cars) > max_allowed:
            raise ValueError(f"{locomotive.name} can only have {max_allowed} railcars, but {len(cars)} were provided")
        self.cars = cars[:max_allowed] if len(cars) <= max_allowed else cars[:max_allowed]
        self.capacity = self.calc_capacity()
        self.line_assignment = line_assignment
        self.maitnance_costs = self.calc_maitnance_costs()
        # Store original price for Historical Society calculations
        self.original_price = None

    def get_capacity(self):
        return self.capacity
    
    def calc_maitnance_costs(self, current_year: int = None):
        """Calculate maintenance costs, accounting for outdated locomotives"""
        self.maitnance_costs = 0
        self.maitnance_costs += self.locomotive.get_maitnance_costs(current_year)
        for car in self.cars:
            self.maitnance_costs += car.get_maitnance_costs(current_year)
        return self.maitnance_costs
    
    def get_line_assignment(self):
        return self.line_assignment
    
    def assign_to_line(self, line_name: str):
        self.line_assignment = line_name

    def calc_capacity(self):
        capacity = 0
        for car in self.cars:
            capacity += car.capacity
        capacity += self.locomotive.capacity
        return capacity
    
    def get_maitnance_costs(self, current_year: int = None):
        """Get maintenance costs for trainset, recalculating if current_year provided"""
        if current_year is not None:
            # Recalculate with current year to account for outdated locomotives
            return self.calc_maitnance_costs(current_year)
        return self.maitnance_costs
    
    def add_railcar(self, railcar: Railcar) -> bool:
        """Add a railcar to this trainset. Returns True if successful, False if at max capacity or locomotive can't have railcars."""
        if not self.locomotive.can_have_railcars:
            return False
        max_allowed = self.locomotive.max_cars if self.locomotive.max_cars is not None else self.DEFAULT_MAX_RAILCARS
        if len(self.cars) >= max_allowed:
            return False
        self.cars.append(railcar)
        # Recalculate capacity and maintenance
        self.capacity = self.calc_capacity()
        self.maitnance_costs = self.calc_maitnance_costs()
        return True

    def remove_railcar(self, railcar: Railcar = None, index: int = -1):
        """Remove a railcar from this trainset. Returns the removed car, or None if none removed."""
        if not self.cars:
            return None
        if railcar is not None:
            if railcar not in self.cars:
                return None
            self.cars.remove(railcar)
            removed = railcar
        else:
            idx = index if index >= 0 else len(self.cars) - 1
            if idx < 0 or idx >= len(self.cars):
                return None
            removed = self.cars.pop(idx)
        self.capacity = self.calc_capacity()
        self.maitnance_costs = self.calc_maitnance_costs()
        return removed

    def get_catalog_base_price(self) -> int:
        """Sum of component catalog base prices (no inflation). Used for Historical Society (RULES.md: 5x original)."""
        total = self.locomotive.base_price
        if not total:
            total = self.locomotive.maitnance_costs * 3
        for car in self.cars:
            car_price = car.base_price
            if not car.base_price:
                car_price = car.maitnance_costs * 2
            total += car_price
        return int(total)
    
    def get_historical_society_price(self, current_year: int) -> int:
        """5x catalog base price if the trainset's locomotive is 100+ years old (RULES.md)."""
        if self.locomotive is None or self.locomotive.year_introduced is None:
            return 0
        years_old = current_year - self.locomotive.year_introduced
        if years_old < 100:
            return 0
        return int(self.get_catalog_base_price() * 5)
    
    def get_price(self, current_year=1830):
        """Calculate price for a trainset with inflation"""
        base_prices = self.locomotive.base_price  
        if not base_prices:
            base_prices = self.locomotive.maitnance_costs * 3
        for car in self.cars:
            car_price = car.base_price
            if not car.base_price:
                car_price = car.maitnance_costs * 2
            base_prices += car_price  
        price = base_prices
        from inflation_utils import get_inflation_factor
        return int(price * get_inflation_factor(current_year, max_factor=5.5))


class Ferry(Vehicle):
    """Ferry vehicle - water-based passenger transport"""
    def __init__(self, name: str,
                 fuel: Resource,
                 miles_per_unit: float,
                 top_speed: float,
                 average_speed: float,
                 capacity: int,
                 object_id: int = None,
                 maitnance_costs: int = 100,
                 year_introduced: int = None,
                 quality: float = 1.0,
                 maintenance_workers: int = 0,
                 base_price: int = None,
                 obselete_by: str = None):
        super().__init__(name, object_id, maitnance_costs, year_introduced, base_price, obselete_by)
        self.fuel = fuel
        self.miles_per_unit = miles_per_unit
        self.top_speed = top_speed
        self.average_speed = average_speed
        self.capacity = capacity
        self.quality = quality
        self.maintenance_workers = maintenance_workers
    
    def update_quality(self):
        """Update quality based on maintenance workers (1.0 base + 0.1 per worker, max 9.0 for ferries)"""
        self.quality = min(9.0, 1.0 + (self.maintenance_workers * 0.1))
    
    def get_price(self, current_year=1830):
        """Calculate price for a ferry with inflation"""
        if self.base_price is None:
            # Fallback: estimate price based on maintenance costs
            base_price = self.maitnance_costs * 3
        else:
            base_price = self.base_price
        
        # Inflation: prices increase over time (1830: 1x, 2050: 10x)
        inflation_factor = 1.0 + (current_year - 1830) * (10.0 - 1.0) / (2050 - 1830)
        inflation_factor = max(1.0, min(10.0, inflation_factor))
        
        return int(base_price * inflation_factor)

    def get_owner(self):
        return self.owner


class Bus(Vehicle):
    """Bus vehicle - road-based passenger transport"""
    def __init__(self, name: str,
                 fuel: Resource,
                 miles_per_unit: float,
                 top_speed: float,
                 average_speed: float,
                 capacity: int,
                 object_id: int = None,
                 maitnance_costs: int = 100,
                 year_introduced: int = None,
                 quality: float = 1.0,
                 maintenance_workers: int = 0,
                 base_price: int = None,
                 obselete_by: str = None):
        super().__init__(name, object_id, maitnance_costs, year_introduced, base_price, obselete_by)
        self.fuel = fuel
        self.miles_per_unit = miles_per_unit
        self.top_speed = top_speed
        self.average_speed = average_speed
        self.capacity = capacity
        self.quality = quality
        self.maintenance_workers = maintenance_workers
    
    def update_quality(self):
        """Update quality based on maintenance workers (1.0 base + 0.1 per worker, max 9.0 for buses)"""
        self.quality = min(9.0, 1.0 + (self.maintenance_workers * 0.1))
    
    def get_price(self, current_year=1830):
        """Calculate price for a bus with inflation"""
        if self.base_price is None:
            base_price = self.maitnance_costs * 3
        else:
            base_price = self.base_price
        from inflation_utils import get_inflation_factor
        return int(base_price * get_inflation_factor(current_year))

    def __repr__(self):
        fuel_name = getattr(self.fuel, 'name', str(self.fuel))
        return f"Bus(name={self.name!r}, fuel={fuel_name!r}, top_speed={self.top_speed}, capacity={self.capacity}, year_introduced={self.year_introduced}, maitnance_costs={self.maitnance_costs})"


class Boat(Vehicle):
    """Legacy Boat class - kept for backward compatibility"""
    def __init__(self, name: str,
                 object_id: int,
                 fuel: Resource,
                 miles_per_gallon: float,
                 top_speed: float,
                 capacity: int):
        super().__init__(name, object_id)
        self.fuel = fuel
        self.miles_per_gallon = miles_per_gallon
        self.top_speed = top_speed
        self.capacity = capacity

    def __repr__(self):
        fuel_name = getattr(self.fuel, 'name', str(self.fuel))
        return f"Boat(name={self.name!r}, fuel={fuel_name!r}, top_speed={self.top_speed}, capacity={self.capacity})"