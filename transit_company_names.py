"""Realistic transit company names for AI players.
Map-specific names: NYC/NY Metro use regional railroads; standard map uses generic names."""

import random

# Generic names for standard/generic maps (no specific region)
TRANSIT_NAMES_GENERIC = [
    "Central Transit Company",
    "Metropolitan Railway",
    "Union Street Railway",
    "City Transit Lines",
    "Regional Rail Company",
    "Metro Express",
    "Urban Transit Co",
    "City Line Railway",
    "Metropolitan Transit",
    "Central Railway",
    "Valley Transit",
    "Pacific Railway",
    "Northern Transit",
    "Southern Railway Co",
    "Eastern Transit",
    "Western Railway",
    "Midland Transit",
    "Coastal Railway",
    "River Transit Co",
    "Lakeside Railway",
]

# NYC-area specific (historical and plausible)
TRANSIT_NAMES_NYC = [
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

# NY Metro (includes NYC + regional)
TRANSIT_NAMES_NY_METRO = [
    *TRANSIT_NAMES_NYC,
    "Lehigh Valley Railroad",
    "Baltimore & Ohio",
    "Philadelphia Rapid Transit",
]

# Map preset -> name list (presets without entry use generic)
MAP_PRESET_NAMES = {
    "nyc": TRANSIT_NAMES_NYC,
    "ny_metro": TRANSIT_NAMES_NY_METRO,
}


def get_transit_company_name(existing_names: set = None, map_preset_name: str = None, rng: random.Random = None) -> str:
    """Return a unique transit company name. Map-specific when preset provided; generic for standard map."""
    existing = existing_names or set()
    rand = rng or random
    names = None
    try:
        from ruleset_loader import load_organizations_for_ruleset
        org = load_organizations_for_ruleset()
        ruleset_companies = org.get("transit_companies") or []
        if ruleset_companies:
            names = list(ruleset_companies)
    except Exception:
        names = None
    if not names:
        names = MAP_PRESET_NAMES.get(map_preset_name, TRANSIT_NAMES_GENERIC) if map_preset_name else TRANSIT_NAMES_GENERIC
    available = [n for n in names if n not in existing]
    if available:
        return rand.choice(available)
    qualifiers = (
        "Metropolitan", "Regional", "Central", "Union", "City",
        "Northern", "Southern", "Eastern", "Western", "Coastal",
    )
    for q in qualifiers:
        for n in names:
            cand = f"{q} {n}" if q.lower() not in n.lower() else f"{n} {q}"
            if cand not in existing:
                return cand
    for n in names:
        for suf in (" Railway", " Transit", " Lines", " Railroad"):
            cand = f"{n}{suf}"
            if cand not in existing:
                return cand
    return rand.choice(names)
