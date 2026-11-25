"""Settings shared by every map. Anything about one particular map lives in that map's
own ``map_presets_<id>.py`` file and is reached through ``map_preset_loader``."""

# OSM settings
OSM_SETTINGS = {
    'timeout': 10.0,  # Default timeout for OSM API calls
    'quick_timeout': 2.0,  # Quick timeout for line creation to prevent UI freeze
    'max_timeout_seconds': 3.0,  # Maximum time before returning empty results
    # Overpass usage policy: descriptive User-Agent with app id; generic python-requests UAs are rejected (403).
    # See https://operations.osmfoundation.org/policies/overpass/
    'base_url': 'https://overpass-api.de/api/interpreter',
    # Tried in order after 403/429/5xx on the primary endpoint.
    'mirror_urls': (
        'https://overpass.kumi.systems/api/interpreter',
        'https://overpass.openstreetmap.ru/cgi/interpreter',
    ),
}
