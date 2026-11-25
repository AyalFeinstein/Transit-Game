"""New York City data shared by the maps that contain the city (NYC and NY Metro).

Bounds, the 1830 population model, neighbourhood and borough names, company names,
workplace density and the historical street list live here so that each map file can use
them without importing another map's preset module.
"""


# OSM relation for Governors Island landmass (included in buildable area with population).
GOVERNORS_ISLAND_OSM_RELATION_ID = 2389631
GOVERNORS_ISLAND_BOUNDS = {
    'min_lat': 40.684,
    'max_lat': 40.696,
    'min_lon': -74.028,
    'max_lon': -74.012,
}


def is_in_governors_island(lat: float, lon: float) -> bool:
    """True when a lat/lon lies on Governors Island (approximate bounds)."""
    return (
        GOVERNORS_ISLAND_BOUNDS['min_lat'] <= lat <= GOVERNORS_ISLAND_BOUNDS['max_lat']
        and GOVERNORS_ISLAND_BOUNDS['min_lon'] <= lon <= GOVERNORS_ISLAND_BOUNDS['max_lon']
    )

# NYC Proper bounds - covers all five boroughs including Staten Island
# NOTE: Keep this as the *default* bounds used by scripts unless they explicitly opt into
# a temporary extension.
NYC_BOUNDS = {
    'center_lat': 40.7128,  # NYC center (Manhattan)
    'center_lon': -73.9352,  # NYC center
    'min_lat': 40.4900,  # Southern edge (Staten Island)
    'max_lat': 40.9200,  # Northern edge (Bronx)
    'min_lon': -74.3000,  # Western edge (Staten Island - must include all of Staten Island)
    'max_lon': -73.7000,  # Eastern edge (Queens)
    'width': 50,
    'height': 50
}

# Temporary: extend the playable population grid slightly south and west of Brooklyn.
# This is useful when you want a bit of extra buildable space near Brooklyn's edge without
# redoing the full polygon point generation.
#
# IMPORTANT: This changes the (lat/lon)<->(x/y) mapping for NYC maps, so treat it as
# a temporary experimentation flag.
TEMP_EXTEND_BROOKLYN_SW_BOUNDS = True

NYC_BOUNDS_EXTENDED_BROOKLYN_SW = {
    **NYC_BOUNDS,
    # Extend a bit further south and west
    'min_lat': 40.4500,
    'min_lon': -74.3800,
}


def _get_active_nyc_bounds() -> dict:
    """Internal helper to pick the active NYC bounds."""
    if TEMP_EXTEND_BROOKLYN_SW_BOUNDS:
        return NYC_BOUNDS_EXTENDED_BROOKLYN_SW
    return NYC_BOUNDS


# AI company names for New York City (historical and plausible)
TRANSIT_COMPANY_NAMES = [
    "Hudson River Railroad",
    "Brooklyn & Manhattan Transit",
    "New York Central",
    "Erie Railroad",
    "Long Island Rail Road",
    "Pennsylvania Railroad",
    "Manhattan Railway Company",
    "Brooklyn Rapid Transit",
    "Interborough Rapid Transit",
    "Hudson & Manhattan Railroad",
    "New York & Harlem Railroad",
    "New York & New Haven Railroad",
    "Staten Island Railway",
    "Queens County Railroad",
    "Bronx & Westchester Railway",
    "Jersey Central Railroad",
    "Metropolitan Railway",
    "Delaware & Hudson",
    "Reading Railroad",
]


# Population figures count workplaces as well as residents. Manhattan's business districts
# hold far more jobs than homes, so its starting density is raised slightly (applied by
# map_preset_loader.apply_preset_workplace_density; first matching zone wins).
WORKPLACE_DENSITY_ZONES = [
    # Financial District / Battery
    {'name': 'Financial District', 'min_lat': 40.700, 'max_lat': 40.715,
     'min_lon': -74.020, 'max_lon': -73.995, 'multiplier': 1.09},
    # Lower Manhattan through Midtown (south of Central Park)
    {'name': 'Lower Manhattan and Midtown', 'min_lat': 40.715, 'max_lat': 40.765,
     'min_lon': -74.015, 'max_lon': -73.965, 'multiplier': 1.09},
    # Upper West and Upper East Side
    {'name': 'Upper Manhattan', 'min_lat': 40.765, 'max_lat': 40.800,
     'min_lon': -74.000, 'max_lon': -73.940, 'multiplier': 1.08},
    # Harlem, Washington Heights, Inwood
    {'name': 'Northern Manhattan', 'min_lat': 40.800, 'max_lat': 40.880,
     'min_lon': -73.965, 'max_lon': -73.925, 'multiplier': 1.08},
]


def get_nyc_population_at_location(lat: float, lon: float, is_water: bool) -> int:
    """Get population at a specific location for 1830 NYC.
    
    Args:
        lat: Latitude
        lon: Longitude
        is_water: Whether the location is in water
        
    Returns:
        Population count (0 if water, otherwise based on historical density)
    """
    if is_water:
        return 0
    
    import random
    
    if 40.70 <= lat <= 40.88 and -74.02 <= lon <= -73.93:
        if lat < 40.75:
            return random.randint(800, 1500)
        elif lat < 40.80:
            return random.randint(400, 800)
        elif 40.80 <= lat <= 40.82:
            return random.randint(50, 150)
        else:
            return random.randint(20, 80)
    
    # Brooklyn Heights (growing area in 1830)
    elif 40.68 <= lat <= 40.72 and -74.02 <= lon <= -73.95:
        return random.randint(200, 500)
    
    # Other Brooklyn (sparse, but includes southern and western Brooklyn)
    # Southern Brooklyn (Coney Island area) - sparse in 1830, but had some population
    elif 40.57 <= lat <= 40.65 and -74.02 <= lon <= -73.90:
        return random.randint(20, 100)  # Southern Brooklyn had some population
    # Western Brooklyn (near Manhattan) - more population
    elif 40.65 <= lat <= 40.68 and -74.02 <= lon <= -73.95:
        return random.randint(50, 200)
    # Eastern Brooklyn - sparse
    elif 40.65 <= lat <= 40.68 and -73.95 <= lon <= -73.90:
        return random.randint(30, 150)
    
    # Queens (very sparse in 1830)
    elif -73.98 <= lon <= -73.85:
        return random.randint(10, 100)
    elif 40.80 <= lat <= 40.92 and -73.95 <= lon <= -73.75:
        if 40.80 <= lat <= 40.85:
            return random.randint(30, 100) 
        elif 40.85 <= lat <= 40.88:
            return random.randint(10, 60) 
        else:
            return random.randint(5, 40)  
    elif lat <= 40.65 and lon <= -74.00:
        return random.randint(5, 50)
    
    # Roosevelt Island (Welfare Island in 1830) - had some population but sparse
    # Roosevelt Island is approximately 40.75-40.77 lat, -73.95 to -73.94 lon
    elif 40.75 <= lat <= 40.77 and -73.95 <= lon <= -73.94:
        return random.randint(20, 80)  # Sparse population in 1830

    # Governors Island — military garrison and sparse civilian settlement in 1830
    elif is_in_governors_island(lat, lon):
        return random.randint(50, 200)
    
    # Staten Island (Richmond County) - sparse population in 1830, mostly rural
    # Staten Island is approximately 40.49-40.65 lat, -74.30 to -74.02 lon
    elif 40.49 <= lat <= 40.65 and -74.30 <= lon <= -74.02:
        if 40.55 <= lat <= 40.62 and -74.20 <= lon <= -74.10:
            return random.randint(50, 150)  # More populated central area
        else:
            return random.randint(10, 80)  # Sparse rural areas
    
    # Long Island area (Brooklyn/Queens area between Long Island and New York relations)
    # This area includes parts of Brooklyn and Queens that were developing in 1830
    # Brooklyn Heights and nearby areas had more population
    elif 40.65 <= lat <= 40.75 and -74.00 <= lon <= -73.85:
        if 40.68 <= lat <= 40.72 and -74.00 <= lon <= -73.95:
            return random.randint(150, 400)  # Brooklyn Heights area
        elif 40.65 <= lat <= 40.68 and -74.00 <= lon <= -73.90:
            return random.randint(30, 150)  # Southern Brooklyn
        else:
            return random.randint(10, 100)  # Other Brooklyn/Queens areas
    
    # Default for other areas
    else:
        return random.randint(10, 150)


def get_nyc_bounds() -> dict:
    """Get NYC bounds dictionary. All NYC-specific bounds logic should use this function."""
    return _get_active_nyc_bounds().copy()


# Neighbourhood centres (name, lat, lon) used to name stations and lines. Centres are
# approximate; a point belongs to the nearest centre within geo_naming.NEIGHBORHOOD_MAX_KM.
PLACE_NAMES = (
    # Manhattan
    ("Battery", 40.7033, -74.0170), ("Financial District", 40.7075, -74.0090),
    ("Tribeca", 40.7163, -74.0086), ("Chinatown", 40.7158, -73.9970),
    ("Lower East Side", 40.7150, -73.9843), ("SoHo", 40.7233, -74.0030),
    ("Greenwich Village", 40.7336, -74.0027), ("East Village", 40.7265, -73.9815),
    ("Chelsea", 40.7465, -74.0014), ("Gramercy", 40.7368, -73.9845),
    ("Murray Hill", 40.7479, -73.9757), ("Midtown", 40.7549, -73.9840),
    ("Hell's Kitchen", 40.7638, -73.9918), ("Turtle Bay", 40.7540, -73.9680),
    ("Upper West Side", 40.7870, -73.9754), ("Upper East Side", 40.7736, -73.9566),
    ("Yorkville", 40.7762, -73.9492), ("Central Park", 40.7812, -73.9665),
    ("Morningside Heights", 40.8100, -73.9625), ("Harlem", 40.8116, -73.9465),
    ("East Harlem", 40.7957, -73.9389), ("Hamilton Heights", 40.8252, -73.9499),
    ("Washington Heights", 40.8417, -73.9394), ("Inwood", 40.8677, -73.9212),
    ("Roosevelt Island", 40.7620, -73.9500), ("Governors Island", 40.6895, -74.0168),
    # Bronx
    ("Mott Haven", 40.8091, -73.9229), ("Melrose", 40.8245, -73.9104),
    ("Hunts Point", 40.8094, -73.8803), ("Morrisania", 40.8293, -73.9065),
    ("Highbridge", 40.8370, -73.9260), ("Tremont", 40.8477, -73.8985),
    ("Fordham", 40.8615, -73.8905), ("Belmont", 40.8550, -73.8870),
    ("Kingsbridge", 40.8788, -73.9051), ("Riverdale", 40.9005, -73.9060),
    ("Norwood", 40.8770, -73.8790), ("Woodlawn", 40.8976, -73.8669),
    ("Williamsbridge", 40.8780, -73.8570), ("Wakefield", 40.8980, -73.8520),
    ("Morris Park", 40.8540, -73.8600), ("Parkchester", 40.8383, -73.8566),
    ("Soundview", 40.8250, -73.8680), ("Castle Hill", 40.8190, -73.8480),
    ("Throgs Neck", 40.8226, -73.8195), ("Pelham Bay", 40.8500, -73.8330),
    ("Co-op City", 40.8740, -73.8290), ("City Island", 40.8468, -73.7868),
    ("Eastchester", 40.8870, -73.8280), ("West Farms", 40.8400, -73.8780),
    # Brooklyn
    ("Brooklyn Heights", 40.6960, -73.9940), ("Downtown Brooklyn", 40.6930, -73.9860),
    ("DUMBO", 40.7033, -73.9881), ("Fort Greene", 40.6920, -73.9740),
    ("Williamsburg", 40.7081, -73.9571), ("Greenpoint", 40.7300, -73.9540),
    ("Bushwick", 40.6944, -73.9213), ("Bedford-Stuyvesant", 40.6872, -73.9418),
    ("Clinton Hill", 40.6890, -73.9660), ("Park Slope", 40.6710, -73.9814),
    ("Gowanus", 40.6733, -73.9903), ("Red Hook", 40.6734, -74.0083),
    ("Carroll Gardens", 40.6795, -73.9992), ("Prospect Heights", 40.6775, -73.9690),
    ("Crown Heights", 40.6694, -73.9422), ("Brownsville", 40.6630, -73.9100),
    ("East New York", 40.6590, -73.8759), ("Cypress Hills", 40.6830, -73.8760),
    ("Flatbush", 40.6409, -73.9624), ("East Flatbush", 40.6480, -73.9300),
    ("Canarsie", 40.6402, -73.9060), ("Flatlands", 40.6210, -73.9350),
    ("Midwood", 40.6204, -73.9600), ("Kensington", 40.6380, -73.9730),
    ("Borough Park", 40.6340, -73.9920), ("Sunset Park", 40.6450, -74.0120),
    ("Bay Ridge", 40.6340, -74.0280), ("Dyker Heights", 40.6190, -74.0100),
    ("Bensonhurst", 40.6030, -73.9950), ("Bath Beach", 40.6020, -74.0050),
    ("Gravesend", 40.5950, -73.9740), ("Sheepshead Bay", 40.5910, -73.9440),
    ("Marine Park", 40.6110, -73.9290), ("Mill Basin", 40.6100, -73.9100),
    ("Coney Island", 40.5755, -73.9707), ("Brighton Beach", 40.5780, -73.9597),
    ("Windsor Terrace", 40.6540, -73.9760), ("Prospect Park", 40.6602, -73.9690),
    # Queens
    ("Long Island City", 40.7447, -73.9485), ("Astoria", 40.7644, -73.9235),
    ("Sunnyside", 40.7433, -73.9196), ("Woodside", 40.7454, -73.9030),
    ("Jackson Heights", 40.7557, -73.8831), ("Elmhurst", 40.7370, -73.8800),
    ("Corona", 40.7450, -73.8640), ("East Elmhurst", 40.7640, -73.8670),
    ("Maspeth", 40.7230, -73.9120), ("Ridgewood", 40.7044, -73.9018),
    ("Middle Village", 40.7170, -73.8810), ("Glendale", 40.7010, -73.8780),
    ("Rego Park", 40.7260, -73.8620), ("Forest Hills", 40.7180, -73.8448),
    ("Kew Gardens", 40.7090, -73.8300), ("Richmond Hill", 40.6990, -73.8310),
    ("Woodhaven", 40.6890, -73.8570), ("Ozone Park", 40.6800, -73.8440),
    ("Howard Beach", 40.6571, -73.8430), ("Jamaica", 40.7027, -73.7890),
    ("South Jamaica", 40.6800, -73.7900), ("St. Albans", 40.6910, -73.7640),
    ("Hollis", 40.7130, -73.7620), ("Queens Village", 40.7157, -73.7419),
    ("Springfield Gardens", 40.6620, -73.7630), ("Laurelton", 40.6740, -73.7460),
    ("Rosedale", 40.6590, -73.7380), ("Flushing", 40.7654, -73.8318),
    ("College Point", 40.7860, -73.8450), ("Whitestone", 40.7920, -73.8100),
    ("Bayside", 40.7630, -73.7710), ("Douglaston", 40.7680, -73.7470),
    ("Fresh Meadows", 40.7350, -73.7850), ("Bellerose", 40.7320, -73.7180),
    ("Glen Oaks", 40.7470, -73.7120), ("Far Rockaway", 40.6050, -73.7550),
    ("Rockaway Beach", 40.5860, -73.8160), ("Breezy Point", 40.5560, -73.9260),
    ("Broad Channel", 40.6060, -73.8190),
    # Staten Island
    ("St. George", 40.6437, -74.0736), ("Stapleton", 40.6266, -74.0760),
    ("Port Richmond", 40.6340, -74.1340), ("West Brighton", 40.6320, -74.1100),
    ("Mariners Harbor", 40.6360, -74.1590), ("New Springville", 40.5930, -74.1640),
    ("Todt Hill", 40.6010, -74.1040), ("New Dorp", 40.5730, -74.1170),
    ("South Beach", 40.5850, -74.0750), ("Great Kills", 40.5540, -74.1510),
    ("Eltingville", 40.5450, -74.1650), ("Annadale", 40.5400, -74.1780),
    ("Rossville", 40.5560, -74.2130), ("Tottenville", 40.5110, -74.2430),
    ("Travis", 40.5890, -74.1900), ("Bulls Head", 40.6090, -74.1620),
)

# Boroughs (name, min_lat, max_lat, min_lon, max_lon), used when no neighbourhood is near.
AREA_BOXES = (
    ("Staten Island", 40.49, 40.65, -74.27, -74.04),
    ("Manhattan", 40.70, 40.88, -74.02, -73.93),
    ("Bronx", 40.80, 40.92, -73.93, -73.76),
    ("Brooklyn", 40.57, 40.70, -74.04, -73.86),
    ("Queens", 40.54, 40.80, -73.96, -73.70),
)


NYC_HISTORICAL_ROADS = {
    # Major NYC streets and their construction/removal years
    # Format: road_name: {'year': construction_year, 'removed_year': removal_year (optional), 'bounds': {...}}
    # If 'removed_year' is specified, the road is not available after that year
    # Broadway (Manhattan) - one of the oldest streets, existed before 1830, never removed
    'Broadway_Manhattan': {
        'year': 1600,  # Pre-colonial, existed before game start
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.70,
            'max_lat': 40.78,
            'min_lon': -74.02,
            'max_lon': -73.98
        }
    },
    # Wall Street - existed before 1830, never removed
    'Wall_Street': {
        'year': 1650,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.70,
            'max_lat': 40.71,
            'min_lon': -74.02,
            'max_lon': -74.00
        }
    },
    # Fifth Avenue - constructed 1824, never removed
    'Fifth_Avenue': {
        'year': 1824,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.70,
            'max_lat': 40.78,
            'min_lon': -74.00,
            'max_lon': -73.97
        }
    },
    # Lexington Avenue - constructed 1832, never removed
    'Lexington_Avenue': {
        'year': 1832,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.72,
            'max_lat': 40.78,
            'min_lon': -73.98,
            'max_lon': -73.96
        }
    },
    # Park Avenue - constructed 1850s, never removed
    'Park_Avenue': {
        'year': 1850,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.72,
            'max_lat': 40.78,
            'min_lon': -73.99,
            'max_lon': -73.97
        }
    },
    # Madison Avenue - constructed 1840s, never removed
    'Madison_Avenue': {
        'year': 1840,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.72,
            'max_lat': 40.78,
            'min_lon': -73.99,
            'max_lon': -73.97
        }
    },
    # Brooklyn Bridge approach roads - 1883, never removed
    'Brooklyn_Bridge_Approach': {
        'year': 1883,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.70,
            'max_lat': 40.72,
            'min_lon': -74.00,
            'max_lon': -73.98
        }
    },
    # Queens Boulevard - constructed 1900s, never removed
    'Queens_Boulevard': {
        'year': 1900,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.72,
            'max_lat': 40.75,
            'min_lon': -73.90,
            'max_lon': -73.85
        }
    },
    # Grand Concourse (Bronx) - constructed 1909, never removed
    'Grand_Concourse': {
        'year': 1909,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.82,
            'max_lat': 40.88,
            'min_lon': -73.92,
            'max_lon': -73.88
        }
    },
    # FDR Drive - constructed 1930s, never removed
    'FDR_Drive': {
        'year': 1934,
        'removed_year': None,  # Never removed
        'bounds': {
            'min_lat': 40.70,
            'max_lat': 40.78,
            'min_lon': -73.98,
            'max_lon': -73.95
        }
    },
    # West Side Highway - constructed 1930, removed 1973 (replaced by West Side Highway replacement)
    'West_Side_Highway': {
        'year': 1930,
        'removed_year': 1973,  # Removed in 1973 (collapsed, replaced)
        'bounds': {
            'min_lat': 40.70,
            'max_lat': 40.78,
            'min_lon': -74.02,
            'max_lon': -74.00
        }
    }
}

NYC_HISTORICAL_BRIDGES = {
    # Major NYC bridges and their construction years
    # Brooklyn Bridge: 1883
    # Manhattan Bridge: 1909
    # Williamsburg Bridge: 1903
    # Queensboro Bridge: 1909
    # etc.
}
