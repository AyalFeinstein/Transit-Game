"""Line-type specialties for AI transit companies.

Every AI company specializes in one kind of line and builds only that kind until it is
profitable; after that it may also build the other kinds its map allows (mostly still its own).
Each map lists the specialties its AIs may have (``AI_LINE_SPECIALTIES`` in the map's preset
file, read through ``map_preset_loader.get_preset_ai_line_specialties``).

Distances are in grid units, like the rest of the AI code.
"""
from __future__ import annotations

import random
from typing import Optional

COMMUTER_RAIL = 'commuter_rail'
CITY_RAIL = 'city_rail'
LIGHT_RAIL = 'light_rail'
LOCAL_BUS = 'local_bus'
EXPRESS_BUS = 'express_bus'
ALL_BUS = 'all_bus'
FERRY = 'ferry'

SPECIALTIES = (COMMUTER_RAIL, CITY_RAIL, LIGHT_RAIL, LOCAL_BUS, EXPRESS_BUS, ALL_BUS, FERRY)

LABELS = {
    COMMUTER_RAIL: 'Commuter rail',
    CITY_RAIL: 'In-city rail',
    LIGHT_RAIL: 'Light rail',
    LOCAL_BUS: 'Local bus',
    EXPRESS_BUS: 'Commuter/express bus',
    ALL_BUS: 'All buses',
    FERRY: 'Ferries',
}

# Service types decide stop spacing (see ai._get_ai_station_spacing) and the timetable shape.
#   commuter      long gaps between stops (heavy rail)
#   local         medium gaps (in-city rail)
#   light         very short gaps (trolleys, horse cars)
#   express       longer bus gaps
#   commuter_bus  stops clustered at both ends, long run between the clusters
PEAK_SERVICE_TYPES = ('commuter', 'commuter_bus')

# Timetable as a share of the full feasible service in each period.
FULL_DAY_SCHEDULE = {'peak': 1.0, 'off_peak': 1.0, 'night': 1.0}
PEAK_FOCUSED_SCHEDULE = {'peak': 1.0, 'off_peak': 0.35, 'night': 0.1}

# Share of the route at each end where commuter/express bus stops cluster.
COMMUTER_BUS_CLUSTER_SHARE = 0.25

# Once profitable, this share of new lines is still of the company's own specialty.
OWN_SPECIALTY_SHARE_WHEN_EXPANDED = 0.65

PROFILES = {
    COMMUTER_RAIL: {
        'mode': 'rail', 'service': 'commuter', 'vehicle': 'heavy',
        'min_route': 2.5, 'max_route': 12.0, 'top_population_fraction': None,
        'extensions_before_new_line': None,
    },
    CITY_RAIL: {
        'mode': 'rail', 'service': 'local', 'vehicle': 'metro',
        'min_route': 1.0, 'max_route': 5.0, 'top_population_fraction': 0.10,
        'extensions_before_new_line': None,
    },
    LIGHT_RAIL: {
        'mode': 'rail', 'service': 'light', 'vehicle': 'light',
        'min_route': None, 'max_route': 3.0, 'top_population_fraction': None,
        'extensions_before_new_line': None,
    },
    LOCAL_BUS: {
        'mode': 'bus', 'service': 'local', 'rail_fallback_service': 'light', 'vehicle': 'light',
        'min_route': None, 'max_route': 3.0, 'top_population_fraction': None,
        'extensions_before_new_line': None,
    },
    EXPRESS_BUS: {
        'mode': 'bus', 'service': 'commuter_bus', 'rail_fallback_service': 'commuter_bus',
        'vehicle': 'express_bus', 'min_route': 2.5, 'max_route': 10.0,
        'top_population_fraction': None,
        # Many lines rather than long ones: open a new line before extending.
        'extensions_before_new_line': 0,
    },
    ALL_BUS: {
        'mode': 'bus', 'service': None, 'rail_fallback_service': 'light', 'vehicle': 'light',
        'min_route': None, 'max_route': None, 'top_population_fraction': None,
        'extensions_before_new_line': None,
    },
    FERRY: {
        'mode': 'ferry', 'service': 'commuter', 'vehicle': 'ferry',
        'min_route': None, 'max_route': None, 'top_population_fraction': None,
        'extensions_before_new_line': None,
    },
}


# Service type chosen for each AI line, by line name. A line only joins the map when its
# construction finishes, so the type is recorded here first and copied onto the line object
# (where saves keep it) the first time the line is looked up.
LINE_SERVICE_TYPES: dict = {}


def line_service_type(line, line_name: str = None) -> Optional[str]:
    """Service type of an AI line ('commuter', 'local', 'light', ...), or None."""
    svc = getattr(line, '_ai_service_type', None) if line is not None else None
    if svc:
        return svc
    name = line_name or (getattr(line, 'name', None) if line is not None else None)
    svc = LINE_SERVICE_TYPES.get(name) if name else None
    if svc and line is not None:
        try:
            line._ai_service_type = svc
        except Exception:
            pass
    return svc


def label(specialty: Optional[str]) -> str:
    return LABELS.get(specialty or '', '')


def map_allowed_specialties(preset_name: Optional[str]) -> tuple:
    """Specialties AI companies may have on this map (the map's own list; all when it has none)."""
    try:
        from map_preset_loader import get_preset_ai_line_specialties
        listed = get_preset_ai_line_specialties(preset_name)
    except Exception:
        listed = None
    if not listed:
        return SPECIALTIES
    return tuple(s for s in SPECIALTIES if s in listed)


def _game_preset_name(game) -> Optional[str]:
    game_map = game.get_map() if game is not None and hasattr(game, 'get_map') else None
    return getattr(game_map, 'map_preset_name', None) if game_map is not None else None


def specialty_of(player) -> Optional[str]:
    spec = getattr(player, 'ai_line_specialty', None)
    return spec if spec in SPECIALTIES else None


def assign_specialty(game, player, rng: random.Random = None) -> Optional[str]:
    """Give an AI player a specialty its map allows, preferring ones no other AI has yet."""
    if not getattr(player, 'is_ai', False):
        return None
    allowed = map_allowed_specialties(_game_preset_name(game))
    current = specialty_of(player)
    if current in allowed:
        return current
    if rng is None:
        seed = int(getattr(game, 'game_seed', 0) or 0) * 7919 + int(getattr(player, 'player_id', 0) or 0)
        rng = random.Random(seed)
    taken = {}
    for other in (game.get_players() if game is not None else []) or []:
        if other is player:
            continue
        spec = specialty_of(other)
        if spec:
            taken[spec] = taken.get(spec, 0) + 1
    fewest = min(taken.get(s, 0) for s in allowed)
    pool = [s for s in allowed if taken.get(s, 0) == fewest]
    player.ai_line_specialty = rng.choice(pool)
    return player.ai_line_specialty


def ensure_specialties(game) -> None:
    """Every AI player in the game has a specialty its map allows (new, spawned and loaded games)."""
    if game is None or not hasattr(game, 'get_players'):
        return
    for player in game.get_players() or []:
        if getattr(player, 'is_ai', False):
            assign_specialty(game, player)


def is_profitable(ops, player) -> bool:
    """True when the company's lines made money in total over the last finished year."""
    try:
        year = int(ops.get_year()) - 1
        lines = ops.get_map().get_lines_by_name() or {}
    except Exception:
        return False
    total = 0
    counted = False
    for name in player.get_line_names() or []:
        line = lines.get(name)
        if line is None:
            continue
        try:
            total += int(ops.get_line_profit_for_year(line, year) or 0)
            counted = True
        except Exception:
            continue
    return counted and total > 0


def allowed_specialties(ops, player) -> tuple:
    """What the company may build now: its own specialty, plus the map's others once profitable."""
    own = specialty_of(player)
    if own is None:
        return ()
    if is_profitable(ops, player):
        preset = getattr(ops.get_map(), 'map_preset_name', None)
        return (own,) + tuple(s for s in map_allowed_specialties(preset) if s != own)
    return (own,)


def choose_build_specialty(ops, player, rng: random.Random) -> Optional[str]:
    """The kind of line the company builds this turn."""
    allowed = allowed_specialties(ops, player)
    if not allowed:
        return None
    if len(allowed) == 1 or rng.random() < OWN_SPECIALTY_SHARE_WHEN_EXPANDED:
        return allowed[0]
    return rng.choice(allowed[1:])


def build_mode(specialty: Optional[str], buses_available: bool) -> Optional[str]:
    """'rail', 'bus' or 'ferry'. Bus companies run horse cars on rails until buses exist."""
    profile = PROFILES.get(specialty or '')
    if profile is None:
        return None
    if profile['mode'] == 'bus' and not buses_available:
        return 'rail'
    return profile['mode']


def service_type(specialty: Optional[str], *, is_bus: bool, route_length: float = None) -> Optional[str]:
    """Service type for a new line of this specialty (None: decide from the route as before)."""
    profile = PROFILES.get(specialty or '')
    if profile is None:
        return None
    if profile['mode'] == 'bus' and not is_bus:
        return profile.get('rail_fallback_service')
    return profile.get('service')


def schedule_shares(service: Optional[str]) -> dict:
    return dict(PEAK_FOCUSED_SCHEDULE if service in PEAK_SERVICE_TYPES else FULL_DAY_SCHEDULE)


def route_length_bounds(specialty: Optional[str]) -> tuple:
    profile = PROFILES.get(specialty or '') or {}
    return profile.get('min_route'), profile.get('max_route')


def _rail_vehicle_class(trainset) -> str:
    """'heavy', 'metro' or 'light' from the locomotive's attributes (no names)."""
    loco = getattr(trainset, 'locomotive', None)
    if loco is None:
        return 'metro'
    speed = float(getattr(loco, 'top_speed', 0) or 0)
    own_capacity = float(getattr(loco, 'capacity', 0) or 0)
    hauls_cars = bool(getattr(loco, 'can_have_railcars', False))
    if hauls_cars and (own_capacity <= 0 or speed >= 80):
        return 'heavy'
    if not hauls_cars and (speed <= 45 or own_capacity <= 80):
        return 'light'
    return 'metro'


def vehicle_class_for_service(service: Optional[str], *, is_bus: bool) -> str:
    if is_bus:
        return 'express_bus' if service in ('express', 'commuter_bus') else 'light'
    return {'commuter': 'heavy', 'commuter_bus': 'heavy', 'local': 'metro', 'light': 'light'}.get(service or '', 'metro')


def vehicle_score(wanted_class: str, vehicle, price: float) -> float:
    """Higher is better for a line wanting ``wanted_class`` vehicles."""
    capacity = float(getattr(vehicle, 'capacity', 0) or 0)
    if not capacity and hasattr(vehicle, 'get_capacity'):
        try:
            capacity = float(vehicle.get_capacity() or 0)
        except Exception:
            capacity = 0.0
    loco = getattr(vehicle, 'locomotive', None)
    speed = float(getattr(loco if loco is not None else vehicle, 'top_speed', 0) or 0)
    price = max(1.0, float(price or 1))
    if loco is None:  # bus
        if wanted_class == 'express_bus':
            return capacity * 2 + speed * 3 - price / 50.0
        return -price + capacity * 0.5
    match = 10_000 if _rail_vehicle_class(vehicle) == wanted_class else 0
    if wanted_class == 'heavy':
        return match + capacity * 2 + speed * 2 - price / 50.0
    if wanted_class == 'light':
        return match - price  # cheap and plentiful
    return match + capacity * 2 - price / 50.0
