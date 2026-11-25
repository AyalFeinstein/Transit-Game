from vehicle import Locomotive, Railcar, Ferry, Bus
from game_resource import Coal, Wood, Oil, Electricity, Food, Gasoline

# Local Passenger Rail Locomotives - Historically Accurate

""" Steam Locomotives """

# Early steam tank locomotives (1820s-1840s) - small local passenger service
rocket_coal = Locomotive(name="Rocket Coal", object_id=0, fuel=Coal(), miles_per_unit=0.08, top_speed=25, average_speed=12, maitnance_costs=30, year_introduced=1829, quality=1, max_cars=3, base_price=250, obselete_by="2-2-2 Coal")

c_2_2_2 = Locomotive(name="2-2-2 Coal", object_id=1, fuel=Coal(), miles_per_unit=0.05, top_speed=45, average_speed=25, maitnance_costs=45, year_introduced=1835, quality=1.5, max_cars=4, base_price=275)
w_2_2_2 = Locomotive(name="2-2-2 Wood", object_id=2, fuel=Wood(), miles_per_unit=0.05, top_speed=45, average_speed=25, maitnance_costs=45, year_introduced=1835, quality=1.5, max_cars=4, base_price=265)

n_0_6_0 = Locomotive(name="0-6-0 Tank Coal", object_id=3, fuel=Coal(), miles_per_unit=0.04, top_speed=35, average_speed=20, maitnance_costs=40, year_introduced=1850, quality=1.5, max_cars=4, base_price=287)

asc = Locomotive(name="4-4-0 American Standered Coal", object_id=4, fuel=Coal(), miles_per_unit=0.03, top_speed=60, average_speed=30, maitnance_costs=55, year_introduced=1870, quality=2.0, max_cars=6, base_price=309)
asw = Locomotive(name="4-4-0 American Standered Wood", object_id=4, fuel=Wood(), miles_per_unit=0.03, top_speed=60, average_speed=30, maitnance_costs=55, year_introduced=1870, quality=2.0, max_cars=6, base_price=299)

g5 = Locomotive(name="PRR G5", object_id=4, fuel=Coal(), miles_per_unit=0.03, top_speed=85, average_speed=50, maitnance_costs=75, year_introduced=1900, quality=2.0, max_cars=12, base_price=339)

""" Diesel Locomotives """

boxcab = Locomotive(name="GM Boxcab Switcher", object_id=7, fuel=Oil(), miles_per_unit=0.3, top_speed=50, average_speed=30, maitnance_costs=30, year_introduced=1925, can_have_railcars=True, quality=2.5, max_cars=3, base_price=270)

ft = Locomotive(name="EMD FT", object_id=7, fuel=Oil(), miles_per_unit=0.8, top_speed=100, average_speed=50, maitnance_costs=60, year_introduced=1939, can_have_railcars=True, quality=5.5, max_cars=7, base_price=305)

gp9 = Locomotive(name="EMD GP9", object_id=7, fuel=Oil(), miles_per_unit=1.0, top_speed=90, average_speed=50, maitnance_costs=90, year_introduced=1954, can_have_railcars=True, quality=6.5, max_cars=8, base_price=342)

f40ph = Locomotive(name="EMD F-40PH", object_id=7, fuel=Oil(), miles_per_unit=1.4, top_speed=140, average_speed=60, maitnance_costs=90, year_introduced=1973, can_have_railcars=True, quality=7.5, max_cars=10, base_price=390)

seimans_charger = Locomotive(name="Siemens Charger", object_id=7, fuel=Oil(), miles_per_unit=2.0, top_speed=180, average_speed=70, maitnance_costs=120, year_introduced=2017, can_have_railcars=True, quality=9.5, max_cars=12, base_price=500)

""" Diesel Railcars """

early_diesel_railcar = Locomotive(name="Diesel Railcar", object_id=7, fuel=Oil(), miles_per_unit=0.6, top_speed=60, average_speed=35, capacity=60, maitnance_costs=55, year_introduced=1925, can_have_railcars=False, quality=3.0, base_price=270)

pioneer_zephyr_additional_car = Railcar("Pioneer Zephyr Additional Car", 20, 77, maitnance_costs=65, base_price=66)
pioneer_zephyr_additional_car.year_introduced = 1934
pioneer_zephyr = Locomotive(name="Pioneer Zephyr", object_id=10, fuel=Oil(), miles_per_unit=0.8, top_speed=70, average_speed=60, capacity=77, maitnance_costs=65, year_introduced=1934, can_have_railcars=True, max_cars=2, associated_railcar_name="Pioneer Zephyr Additional Car", base_price=269)

dmu_additional_car = Railcar("Diesel Multiple Unit additional car", 20, 100, maitnance_costs=65, base_price=66)
dmu_additional_car.year_introduced = 1950
f_unit = Locomotive(name="Diesel Multiple Unit", object_id=10, fuel=Oil(), miles_per_unit=0.8, top_speed=70, average_speed=40, capacity=100, maitnance_costs=65, year_introduced=1950, can_have_railcars=True, max_cars=3, associated_railcar_name="Diesel Multiple Unit additional car", base_price=299)

""" Trolleys/LRVs Cars """

pcc = Locomotive(name="PCC Streetcar", fuel=Electricity(), miles_per_unit=2.2, top_speed=40, average_speed=20, capacity=80, needs_overhead_wire=True, maitnance_costs=30, year_introduced=1936, can_have_railcars=False, base_price=238)

birney_safety = Locomotive(name="Birney Safety Streetcar", object_id=5, fuel=Electricity(), miles_per_unit=2.0, top_speed=25, average_speed=15, capacity=50, needs_overhead_wire=True, maitnance_costs=25, year_introduced=1890, can_have_railcars=False, quality=2.5, base_price=200)

sd_lrv = Locomotive(name="Seimans SD LRV", object_id=5, fuel=Electricity(), miles_per_unit=2.0, top_speed=70, average_speed=35, capacity=180, needs_overhead_wire=True, maitnance_costs=60, year_introduced=1980, can_have_railcars=False, quality=5, base_price=326)

""" Subway Cars """

gate_stock = Locomotive(name="Gate Stock Subway Car", object_id=11, fuel=Electricity(), miles_per_unit=1.5, top_speed=40, average_speed=30, capacity=60, needs_third_rail=True, maitnance_costs=25, year_introduced=1890, can_have_railcars=True, max_cars=9, base_price=200, quality=3.0, associated_railcar_name="Gate Stock Subway additional car")
gate_stock_additional_car = Railcar("Gate Stock Subway additional car", 21, 60, maitnance_costs=25, base_price=104, year_introduced=1890)

gibbs = Locomotive(name="Gibbs Subway Car", object_id=11, fuel=Electricity(), miles_per_unit=1.5, top_speed=55, average_speed=45, capacity=40, needs_third_rail=True, maitnance_costs=50, year_introduced=1904, can_have_railcars=True, max_cars=9, base_price=230, quality=3.0, associated_railcar_name="Gibbs Subway additional car")
gibbs_additional_car = Railcar("Gibbs Subway additional car", 21, 250, maitnance_costs=40, base_price=104, year_introduced=1904)

modern_subway = Locomotive(name="Modern Subway Car", object_id=12, fuel=Electricity(), miles_per_unit=2.5, top_speed=55, average_speed=30, capacity=180, needs_third_rail=True, maitnance_costs=50, year_introduced=1930, can_have_railcars=False, max_cars=10, base_price=226)

contemporary_subway = Locomotive(name="Contemporary Subway Car", object_id=13, fuel=Electricity(), miles_per_unit=3.5, top_speed=65, average_speed=35, capacity=220, needs_third_rail=True, maitnance_costs=90, year_introduced=1980, can_have_railcars=False, max_cars=11, base_price=326)

""" Electric Locomotives """

gg1 = Locomotive(name="GG-1", object_id=7, fuel=Electricity(), miles_per_unit=1.4, top_speed=100, average_speed=65, needs_overhead_wire=True, maitnance_costs=75, year_introduced=1934, can_have_railcars=True, quality=7.5, max_cars=12, base_price=292)

aem_7 = Locomotive(name="AEM-7", object_id=7, fuel=Electricity(), miles_per_unit=2.1, top_speed=125, average_speed=105, needs_overhead_wire=True, maitnance_costs=50, year_introduced=1980, can_have_railcars=True, quality=8.5, max_cars=11, base_price=380)

acs_64 = Locomotive(name="ACS-64", object_id=7, fuel=Electricity(), miles_per_unit=2.4, top_speed=145, average_speed=115, needs_overhead_wire=True, maitnance_costs=55, year_introduced=2013, can_have_railcars=True, quality=9.5, max_cars=13, base_price=490)

""" EMUs """

interurban = Locomotive(name="Interurban Car", object_id=6, fuel=Electricity(), miles_per_unit=1.8, top_speed=50, average_speed=30, capacity=70, needs_overhead_wire=True, maitnance_costs=30, year_introduced=1900, can_have_railcars=True, quality=3.5, base_price=250, associated_railcar_name="Interurban additional car")
interurban_car = Railcar("Interurban additional car", 21, 150, maitnance_costs=75, base_price=76)
interurban_car.year_introduced = 1900

emu_additional_car = Railcar("Modern EMU additional car", 21, 150, maitnance_costs=75, base_price=76)
emu_additional_car.year_introduced = 1970
streamlined = Locomotive(name="Modern EMU", object_id=8, fuel=Electricity(), miles_per_unit=3.0, top_speed=50, average_speed=45, capacity=150, needs_overhead_wire=True, maitnance_costs=75, year_introduced=1970, can_have_railcars=True, max_cars=4, associated_railcar_name="Modern EMU additional car", base_price=344)

m1_additional_car = Railcar("Metropolitan M1 additional car", 21, 120, maitnance_costs=75, base_price=76)
m1_additional_car.year_introduced = 1968
m1 = Locomotive(name="Metropolitan M1 Commuter", object_id=8, fuel=Electricity(), miles_per_unit=4.0, top_speed=56, average_speed=50, capacity=120, needs_third_rail=True, maitnance_costs=75, year_introduced=1968, can_have_railcars=True, max_cars=9, associated_railcar_name="Metropolitan M1 additional car", base_price=341, quality=7.5)

m7_additional_car = Railcar("Metropolitan M7 additional car", 21, 116, maitnance_costs=88, base_price=90)
m7_additional_car.year_introduced = 1999
m7 = Locomotive(name="Metropolitan M1 Commuter", object_id=8, fuel=Electricity(), miles_per_unit=5.0, top_speed=90, average_speed=60, capacity=116, needs_third_rail=True, maitnance_costs=90, year_introduced=1999, can_have_railcars=True, max_cars=11, associated_railcar_name="Metropolitan M7 additional car", base_price=341, quality=8.0)

m9_additional_car = Railcar("Metropolitan M9 additional car", 21, 116, maitnance_costs=100, base_price=104)
m9_additional_car.year_introduced = 2019
m9 = Locomotive(name="Metropolitan M9 Commuter", object_id=8, fuel=Electricity(), miles_per_unit=6.0, top_speed=100, average_speed=70, capacity=116, needs_third_rail=True, maitnance_costs=100, year_introduced=2019, can_have_railcars=True, max_cars=13, associated_railcar_name="Metropolitan M9 additional car", base_price=395, quality=8.5)

stadler_kiss_additional_car = Railcar("Stadler KISS additional car", 22, 120, maitnance_costs=120, quality=9.5, base_price=95)
stadler_kiss_additional_car.year_introduced = 2008
stadler_kiss = Locomotive(name="Stadler KISS", object_id=9, fuel=Electricity(), miles_per_unit=4.5, top_speed=140, average_speed=60, capacity=120, needs_overhead_wire=True, maitnance_costs=120, year_introduced=2008, can_have_railcars=True, max_cars=11, associated_railcar_name="Stadler KISS additional car", base_price=477, quality=10.0)

avelia_horizon_additional_car = Railcar("Avelia Horizon additional car", 22, 105, maitnance_costs=150, quality=13.5, base_price=592)
avelia_horizon_additional_car.year_introduced = 2027
avelia_horizon = Locomotive(name="Avelia Horizon", object_id=9, fuel=Electricity(), miles_per_unit=6.5, top_speed=250, average_speed=200, capacity=105, needs_overhead_wire=True, maitnance_costs=150, year_introduced=2027, can_have_railcars=True, max_cars=11, associated_railcar_name="Avelia Horizon additional car", base_price=477, quality=10.0)

""" Passenger Railcars """

standard = Railcar("Wooden Coach Car", 0, 60, maitnance_costs=5, quality=2, base_price=50)
standard.year_introduced = 1830

# Improved passenger cars (1850s) - more comfortable coaches
improved_passenger = Railcar("Crackerbox Passenger Car", 1, 80, maitnance_costs=9, quality=4, base_price=50)
improved_passenger.year_introduced = 1850

# BU passenger cars (1900s) - steel coaches with better amenities
bu_passenger = Railcar("BU Passenger Car", 2, 80, maitnance_costs=15, quality=6, base_price=50)
bu_passenger.year_introduced = 1900

harriman_passenger = Railcar("Harriman Passenger Car", 2, 68, maitnance_costs=15, quality=6, base_price=60)
harriman_passenger.year_introduced = 1906

harriman_first_class = Railcar("Harriman First Class Car", 2, 45, maitnance_costs=10, quality=11.0, base_price=50)
harriman_first_class.year_introduced = 1906

suburban_passenger = Railcar("Suburban Passenger Car", 2, 100, maitnance_costs=15, quality=6.5, base_price=51)
suburban_passenger.year_introduced = 1920

pullman_gallery = Railcar("Pullman Gallery Car", 3, 150, maitnance_costs=150, quality=8.0, base_price=69, year_introduced=1955)

nippon_sharyo_gallery = Railcar("Nippon Sharyo Gallery Car", 3, 200, maitnance_costs=155, base_price=75, year_introduced=1984, quality=8.0)

kawasaki_c3 = Railcar("Kawasaki C3", 3, 143, maitnance_costs=165, quality=9, base_price=74, year_introduced=1973)

superliner_first_class = Railcar("Superliner First Class Car", 3, 74, maitnance_costs=135, base_price=69, quality=13.0)
superliner_first_class.year_introduced = 1979

bombardier_multi_level_coach = Railcar("Bombardier MultiLevel Coach", 3, 160, maitnance_costs=170, quality=9.0, base_price=82)
bombardier_multi_level_coach.year_introduced = 2006

horizon_passenger = Railcar("Horizon Passenger Car", 2, 75, maitnance_costs=100, quality=9.5, base_price=85)
horizon_passenger.year_introduced = 1988

horizon_first_class = Railcar("Horizon First Class Car", 2, 50, maitnance_costs=70, quality=13, base_price=128)
horizon_first_class.year_introduced = 1988

horizon_passenger = Railcar("Siemens Venture Car", 2, 74, maitnance_costs=110, base_price=109, year_introduced=2016, quality=10)

""" Horse-Drawn Vehicles """

horse_omnibus = Locomotive(name="Horse-Drawn Omnibus", object_id=14, fuel=Food(), miles_per_unit=0.02, top_speed=8, average_speed=5, capacity=20, maitnance_costs=15, year_introduced=1827, can_have_railcars=False, quality=1.0, base_price=150)
horse_omnibus.year_introduced = 1827

horse_streetcar = Locomotive(name="Horse-Drawn Streetcar", object_id=15, fuel=Food(), miles_per_unit=0.03, top_speed=10, average_speed=6, capacity=30, maitnance_costs=20, year_introduced=1832, can_have_railcars=False, quality=1.2, base_price=180)
horse_streetcar.year_introduced = 1832

# Horse-drawn carriage (pre-rail era) - early passenger transport, runs on Food resource
horse_carriage = Locomotive(name="Horse-Drawn Carriage", object_id=16, fuel=Food(), miles_per_unit=0.015, top_speed=12, average_speed=7, capacity=8, maitnance_costs=10, year_introduced=1800, can_have_railcars=False, quality=0.8, base_price=100)
horse_carriage.year_introduced = 1800

""" Ferry Vehicles """

# Early ferry (pre-1830) - basic passenger ferry
early_ferry = Ferry(name="Early Ferry", object_id=17, fuel=Wood(), miles_per_unit=0.1, top_speed=8, average_speed=5, capacity=100, maitnance_costs=25, year_introduced=1800, quality=1.0, base_price=200)

# Steam ferry (1830s-1900s) - steam-powered ferry
steam_ferry = Ferry(name="Steamboat", object_id=18, fuel=Coal(), miles_per_unit=0.15, top_speed=12, average_speed=8, capacity=200, maitnance_costs=40, year_introduced=1840, quality=1.5, base_price=300)

# Modern ferry (1900s+) - diesel/electric powered ferry
deisel_ferry = Ferry(name="Deisel Ferry", object_id=19, fuel=Oil(), miles_per_unit=0.4, top_speed=20, average_speed=15, capacity=400, maitnance_costs=60, year_introduced=1900, quality=2.5, base_price=400)

gas_ferry = Ferry(name="Gas Ferry", object_id=19, fuel=Oil(), miles_per_unit=0.5, top_speed=30, average_speed=20, capacity=450, maitnance_costs=70, year_introduced=1960, quality=3.5, base_price=600)

catamaran_ferry = Ferry(name="Large Catamaran", object_id=19, fuel=Oil(), miles_per_unit=0.6, top_speed=40, average_speed=22, capacity=600, maitnance_costs=70, year_introduced=1980, quality=3.5, base_price=600)

# High-speed ferry (1980s+) - fast passenger ferry
high_speed_ferry = Ferry(name="High-Speed Ferry", object_id=20, fuel=Oil(), miles_per_unit=0.6, top_speed=45, average_speed=25, capacity=300, maitnance_costs=80, year_introduced=1990, quality=5.0, base_price=500)

""" Bus Vehicles """
early_motor_bus = Bus(name="Early Motor Bus", object_id=21, fuel=Gasoline(), miles_per_unit=0.1, top_speed=15, average_speed=10, capacity=25, maitnance_costs=20, year_introduced=1895, quality=1.5, base_price=180)

double_decker_bus = Bus(name="Double-Decker Bus", object_id=22, fuel=Gasoline(), miles_per_unit=0.2, top_speed=25, average_speed=15, capacity=70, maitnance_costs=35, year_introduced=1920, quality=2.0, base_price=220)

standard_city_bus = Bus(name="Standard City Bus", object_id=23, fuel=Gasoline(), miles_per_unit=0.25, top_speed=30, average_speed=18, capacity=50, maitnance_costs=40, year_introduced=1930, quality=2.5, base_price=250)

articulated_bus = Bus(name="Articulated Bus", object_id=24, fuel=Gasoline(), miles_per_unit=0.3, top_speed=35, average_speed=20, capacity=120, maitnance_costs=50, year_introduced=1960, quality=3.0, base_price=280)

modern_low_floor_bus = Bus(name="Modern Low-Floor Bus", object_id=25, fuel=Gasoline(), miles_per_unit=0.35, top_speed=40, average_speed=22, capacity=80, maitnance_costs=55, year_introduced=1980, quality=3.5, base_price=320)

hybrid_bus = Bus(name="Hybrid Bus", object_id=26, fuel=Gasoline(), miles_per_unit=0.5, top_speed=45, average_speed=25, capacity=85, maitnance_costs=60, year_introduced=2000, quality=4.0, base_price=380)

electric_bus = Bus(name="Electric Bus", object_id=27, fuel=Electricity(), miles_per_unit=1.5, top_speed=50, average_speed=28, capacity=90, maitnance_costs=65, year_introduced=2010, quality=4.5, base_price=420)

brt_bus = Bus(name="BRT Bus", object_id=28, fuel=Gasoline(), miles_per_unit=0.4, top_speed=55, average_speed=30, capacity=150, maitnance_costs=70, year_introduced=2010, quality=5.0, base_price=450)

class VehicleRegistry:
    """Dynamic registry that automatically discovers all vehicles from a module"""
    
    def __init__(self, source_module=None):
        self._locomotives = []
        self._railcars = []
        self._ferries = []
        self._buses = []
        if source_module is None:
            import sys
            source_module = sys.modules[__name__]
        self._discover_vehicles(source_module)

    @classmethod
    def from_module(cls, module) -> "VehicleRegistry":
        return cls(source_module=module)
    
    def _discover_vehicles(self, current_module):
        """Discover all vehicles defined in the given module."""
        for attr_name in dir(current_module):
            if attr_name.startswith('_'):
                continue
            
            attr_value = getattr(current_module, attr_name)
            
            if isinstance(attr_value, Locomotive):
                self._locomotives.append(attr_value)
            elif isinstance(attr_value, Railcar):
                self._railcars.append(attr_value)
            elif isinstance(attr_value, Ferry):
                self._ferries.append(attr_value)
            elif isinstance(attr_value, Bus):
                self._buses.append(attr_value)
    
    def get_all_locomotives(self) -> list[Locomotive]:
        """Get all locomotives discovered in this module"""
        return self._locomotives.copy()
    
    def get_all_railcars(self) -> list[Railcar]:
        """Get all railcars discovered in this module"""
        return self._railcars.copy()
    
    def get_all_ferries(self) -> list[Ferry]:
        """Get all ferries discovered in this module"""
        return self._ferries.copy()
    
    def get_all_buses(self) -> list[Bus]:
        """Get all buses discovered in this module"""
        return self._buses.copy()
    
    def get_locomotives_by_year(self, max_year: int = None) -> list[tuple[Locomotive, int]]:
        """Get locomotives with their introduction years, optionally filtered by max_year"""
        result = []
        for loco in self._locomotives:
            year = getattr(loco, 'year_introduced', None)
            if year is not None:
                if max_year is None or year <= max_year:
                    result.append((loco, year))
        return sorted(result, key=lambda x: x[1])
    
    def get_railcars_by_year(self, max_year: int = None) -> list[tuple[Railcar, int]]:
        """Get railcars with their introduction years, optionally filtered by max_year"""
        result = []
        for car in self._railcars:
            year = getattr(car, 'year_introduced', None)
            if year is not None:
                if max_year is None or year <= max_year:
                    result.append((car, year))
        return sorted(result, key=lambda x: x[1])
    
    def get_ferries_by_year(self, max_year: int = None) -> list[tuple[Ferry, int]]:
        """Get ferries with their introduction years, optionally filtered by max_year"""
        result = []
        for ferry in self._ferries:
            year = getattr(ferry, 'year_introduced', None)
            if year is not None:
                if max_year is None or year <= max_year:
                    result.append((ferry, year))
        return sorted(result, key=lambda x: x[1])
    
    def get_buses_by_year(self, max_year: int = None) -> list[tuple[Bus, int]]:
        """Get buses with their introduction years, optionally filtered by max_year"""
        result = []
        for bus in self._buses:
            year = getattr(bus, 'year_introduced', None)
            if year is not None:
                if max_year is None or year <= max_year:
                    result.append((bus, year))
        return sorted(result, key=lambda x: x[1])
    
    def get_earliest_locomotive(self) -> Locomotive:
        """Get the earliest locomotive by introduction year"""
        locomotives = self.get_locomotives_by_year()
        if locomotives:
            return locomotives[0][0]
        return None
    
    def get_earliest_railcar(self) -> Railcar:
        """Get the earliest railcar by introduction year"""
        railcars = self.get_railcars_by_year()
        if railcars:
            return railcars[0][0]
        return None

_registry = None


def reset_vehicle_registry() -> None:
    """Clear cached registry (e.g. when switching rulesets)."""
    global _registry
    _registry = None


def set_vehicle_registry_from_module(module) -> VehicleRegistry:
    """Build registry from a Python module that defines Locomotive/Railcar/Ferry/Bus objects."""
    global _registry
    _registry = VehicleRegistry.from_module(module)
    return _registry


def set_vehicle_registry(registry: VehicleRegistry) -> VehicleRegistry:
    """Use a pre-built registry (e.g. from ruleset vehicles.json)."""
    global _registry
    _registry = registry
    return _registry


def get_vehicle_registry() -> VehicleRegistry:
    """Get the global vehicle registry instance"""
    global _registry
    if _registry is None:
        import vehicle_presets as vp
        _registry = VehicleRegistry.from_module(vp)
    return _registry


def write_vehicles_md(filepath: str = "VEHICLES.md") -> None:
    """Write VEHICLES.md using each vehicle's __repr__()."""
    registry = get_vehicle_registry()
    lines = [
        "# Vehicle Details",
        "",
        "This file is generated from the vehicle registry using each vehicle's `__repr__()`.",
        "",
    ]
    for section_name, getter, key in [
        ("## Locomotives", registry.get_locomotives_by_year, lambda x: (x[1], x[0].name)),
        ("## Railcars", registry.get_railcars_by_year, lambda x: (x[1], x[0].name)),
        ("## Ferries", registry.get_ferries_by_year, lambda x: (x[1], x[0].name)),
        ("## Buses", registry.get_buses_by_year, lambda x: (x[1], x[0].name)),
    ]:
        lines.append(section_name)
        lines.append("")
        items = sorted(getter(), key=key)
        for vehicle, intro_year in items:
            lines.append(f"- {repr(vehicle)}")
            lines.append(f"  - Year introduced: {intro_year}")
            lines.append("")
        if not items:
            lines.append("*(none)*")
            lines.append("")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
