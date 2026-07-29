"""Game Presets
All game configuration values should be defined here."""

STARTING_MONEY = 50000
MARKETPLACE_YEARS_VISIBLE = 40
# Base fee to buy an unowned line; fee increases over time via marketplace inflation
UNOWNED_LINE_BASE_FEE = 500
MARKETPLACE_BULK_DISCOUNT_PERCENT = 0.25
MARKETPLACE_BULK_DISCOUNT_THRESHOLD = 10
MARKETPLACE_TARGET_TOTAL_ITEMS = 60
# Replenishment target scales with year (e.g. 1.0 at 1830, MARKETPLACE_TARGET_MAX_MULTIPLIER at 2050)
MARKETPLACE_REPLENISHMENT_START_YEAR = 1830
MARKETPLACE_REPLENISHMENT_END_YEAR = 2050
MARKETPLACE_TARGET_MAX_MULTIPLIER = 2.0
# Locomotive replenishment starts slow and increases over time (like railcars)
# At 1830: LOCOMOTIVE_REPLENISHMENT_START, at 2050: 1.0
# Early-game locomotive restock probability scales from this (1830) to 1.0 (2050).
MARKETPLACE_LOCOMOTIVE_REPLENISHMENT_START = 0.05
# Through this calendar year, only this fraction of replenishment shortfall is filled per turn.
MARKETPLACE_EARLY_REPLENISHMENT_END_YEAR = 1845
MARKETPLACE_EARLY_REPLENISHMENT_FACTOR = 0.35
# Line construction speed: 2.0 = lines build in half the time (expedited)
LINE_BUILD_SPEED_MULTIPLIER = 2.0
# Batch consecutive segments into one build-queue job (fewer turns, same progressive map display).
LINE_BUILD_SEGMENT_BATCH_SIZE = 16
LINE_BUILD_SEGMENT_BATCH_MAX_LENGTH_GRID = 3.0

# NIMBY lawsuit when a player queues a new line (after NIMBYs activate in 1890).
# Chance is multiplied by elevation (underground 0.4 = 60% lower, above_ground 0.8 = 20% lower).
LINE_NIMBY_LAWSUIT_BASE_CHANCE = 0.10
LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER = {
    'underground': 0.4,
    'above_ground': 0.8,
    'street': 1.0,
    'water': 1.0,
}

# Government supplemental laws (beyond annual slate) and persistent news log size.
GOVERNMENT_RANDOM_LAW_FAVORABLE_CHANCE = 0.55
GOVERNMENT_RANDOM_LAW_UNFAVORABLE_CHANCE = 0.55
GOVERNMENT_RANDOM_LAW_EXTRA_FAVORABLE_CHANCE = 0.40
GOVERNMENT_RANDOM_LAW_EXTRA_UNFAVORABLE_CHANCE = 0.40
NEWS_LOG_MAX_ENTRIES = 120

# --- AI players (canonical strings for focus/difficulty; used by operations, ai, ai_training) ---
AI_FOCUSES = ('lobbying', 'marketplace', 'line_expansion', 'balanced')
AI_DIFFICULTIES = ('easy', 'medium', 'hard')
AI_DEFAULT_FOCUS = 'balanced'
AI_DEFAULT_DIFFICULTY = 'hard'
# First N year-end settlements may run negative annual net while bootstrapping; after that every year must net positive.
AI_BOOTSTRAP_TURNS = 5
# Calendar years from map/timeline start: looser payback / reserve heuristics so AIs can bootstrap like humans.
AI_EARLY_GROWTH_YEARS = 25
# During early growth, require est. annual profit * this >= build cost (vs 10 years after).
AI_EARLY_PAYBACK_YEARS = 16
# After bootstrap, new lines/extensions must pay back within this many years (stricter than early growth).
AI_SUSTAINED_PROFIT_PAYBACK_YEARS = 6
# Profit-margin multiplier for the standard-map AI sustained-profit unit test only (gameplay default remains 1.0).
# Above the in-game UI cap (5.0) so the regression can assert post-bootstrap annual net > 0 on standard maps.
AI_PROFITABILITY_TEST_PROFIT_MARGIN = 20.0
# Standard-map benchmark: AI vs human who takes no actions (only pays upkeep/taxes on starting assets).
AI_OUTLAST_IDLE_STARTING_MONEY = 50000
AI_OUTLAST_IDLE_TRIALS = 5
AI_OUTLAST_IDLE_MIN_WINS = 2
# Stop before Public TA establishment (1930) on standard preset.
AI_OUTLAST_IDLE_MAX_CALENDAR_YEARS = 95
# Modest revenue boost for the idle-comparison regression only (default gameplay stays 1.0).
AI_OUTLAST_IDLE_TEST_PROFIT_MARGIN = 5.0
# Profitability profile: first positive annual net during turns [5, 15]; from turn 16 through 1900 mostly positive.
AI_PROFITABILITY_ONSET_MIN_TURN = 5
AI_PROFITABILITY_ONSET_MAX_TURN = 15
AI_PROFITABILITY_SUSTAIN_THROUGH_CALENDAR_YEAR = 1900
AI_PROFITABILITY_SUSTAIN_MIN_FRACTION = 0.65
AI_PROFITABILITY_TEST_TRIALS = 5
AI_PROFITABILITY_TEST_MIN_WINS = 4
# NYC benchmark: 1 human vs 2 hard + 1 medium AI; each AI should hold more cash than the human by turn 50.
AI_BEATS_HUMAN_MONEY_TURNS = 50
AI_BEATS_HUMAN_MONEY_TRIALS = 5
AI_BEATS_HUMAN_MONEY_MIN_WINS = 3
AI_BEATS_HUMAN_MONEY_HARD_COUNT = 2
AI_BEATS_HUMAN_MONEY_MEDIUM_COUNT = 1
# AI merger acceptance guard: if projected survival exceeds this many turns at current loss rate,
# AI auto-rejects merger offers even when otherwise desperate.
AI_MERGE_MIN_TURNS_TO_LIVE_FOR_ACCEPT = 10
# Calendar years since timeline start before AI runs any merge logic (proposals, doomed-merge, desperate merge).
AI_MERGE_MIN_YEARS_BEFORE_CONSIDER = 4
# Mergers involving at least one AI auto-fail while elapsed calendar years since start are strictly below this (first five years when start+0..4).
AI_MERGE_AUTO_FAIL_FIRST_N_YEARS = 5
AI_MERGE_HEALTHY_PROFIT_THRESHOLD_PCT = 25.0
AI_MERGE_REJECT_CHANCE_PCT = 90.0

# --- AI route geometry (grid units; ~1 unit ≈ 1 km) ---
# Straight land chords between interrupted street sections (water crossings and ferries exempt).
AI_MAX_STRAIGHT_STREET_CONNECTOR_M = 60.0
AI_MAX_STRAIGHT_LAND_SEGMENT_GRID = AI_MAX_STRAIGHT_STREET_CONNECTOR_M / 1000.0
AI_MAX_STRAIGHT_LAND_SEGMENT_MILES = AI_MAX_STRAIGHT_LAND_SEGMENT_GRID / 1.609344
# Bus off-street connectors between street segments (AI only).
AI_BUS_MAX_CONNECTOR_M = 2.0
AI_BUS_MAX_CONNECTOR_GRID = AI_BUS_MAX_CONNECTOR_M / 1000.0
AI_BUS_CONNECTOR_COST_MULTIPLIER = 5.0
# Bus routes must hug road centerlines (not just pass near them).
BUS_STREET_MAX_DISTANCE_GRID = 0.012
BUS_STREET_MIN_SEGMENT_COVERAGE = 0.98
# AI rail: segment must lie on an OSM road polyline (not a chord between polylines).
AI_RAIL_OSM_POLYLINE_MAX_DISTANCE_GRID = 0.012
AI_RAIL_OSM_POLYLINE_MIN_SEGMENT_COVERAGE = 0.92
# AI routes must keep inter-street straight connectors below this share of total length.
AI_MAX_INTER_STREET_CONNECTOR_ROUTE_FRACTION = 0.5
# Minimum route length (grid units) so AI can place two endpoint stations.
AI_BUS_MIN_TWO_STATION_ROUTE_LENGTH_GRID = 0.35
AI_RAIL_MIN_TWO_STATION_ROUTE_LENGTH_GRID = 0.60
# Reject AI routes whose path length exceeds start–end distance by more than this (V-shaped foldbacks).
AI_MAX_ROUTE_DETOUR_RATIO = 1.55
# AI routes that turn at least this sharply at an interior vertex count as doubling back (< 90°).
AI_ROUTE_BACKTRACK_TURN_DEGREES = 80.0
# Prefer street-following routes unless streets would be at least this many times longer than a chord.
# AI street routing: straight chords on road presets are limited to short connectors (see ai.py).

# AI station/line names may reference a street only when within this distance (meters).
AI_STATION_STREET_NAME_MAX_DISTANCE_M = 10.0
AI_STATION_STREET_NAME_MAX_DISTANCE_GRID = AI_STATION_STREET_NAME_MAX_DISTANCE_M / 1000.0
# When a new stop is this close to a station on another line, reuse that station's name (transfer hub).
AI_STATION_TRANSFER_NAME_MAX_DISTANCE_M = 20.0
AI_LINE_NAME_MAX_DISTANCE_M = 10.0
# A line is named after a street only when that street's polyline covers at least this fraction of route length.
AI_LINE_NAME_MIN_ROUTE_FRACTION = 0.5

# AI build scoring: routes running close and parallel to existing lines earn less (grid ≈ km).
AI_PARALLEL_COMPETITION_DISTANCE_M = 150.0
AI_PARALLEL_COMPETITION_DISTANCE_GRID = AI_PARALLEL_COMPETITION_DISTANCE_M / 1000.0

# AI appetite for additional lines after the first is revenue-positive.
AI_SECOND_LINE_BUILD_CHANCE_MIN = 0.52
AI_SECOND_LINE_BUILD_CHANCE_MAX = 0.78
AI_SECOND_LINE_MIN_YEARS_NYC = 2
AI_SECOND_LINE_MIN_YEARS_DEFAULT = 5
AI_SECOND_LINE_MONEY_NYC_WITH_PROFIT = 12000
AI_SECOND_LINE_MONEY_DEFAULT_WITH_PROFIT = 15000

# Small annual operating cost per train-hour of scheduled service (nominal; inflation applied in calc_maitnance).
SCHEDULE_OPERATING_COST_PER_TRAIN_HOUR = 0.004

# Schedule / traversal physics (hours added per station stop; terminal turnaround per round trip).
STATION_DWELL_MINUTES_PER_STOP = 1.5
TERMINAL_TURNAROUND_MINUTES = 10.0
# Each track supports up to this many trains/hour at minimum headway (scales with train speed).
TRACK_CAPACITY_TRAINS_PER_HOUR_BASE = 30.0

# --- Rail / bus route geometry (grid units; ~1 unit ≈ 1 km on standard maps) ---
# Maximum heading change at a vertex (degrees). Blocks U-turns and hairpins on rail lines.
MAX_TRAIN_ROUTE_TURN_DEGREES = 105
MAX_BUS_ROUTE_TURN_DEGREES = 145
# Minimum approximate turn radius for rail (meters-scale when 1 grid unit ≈ 1 km).
MIN_TRAIN_TURN_RADIUS_GRID = 0.035
# Ignore OSM micro-vertices shorter than this when checking turn angle / radius.
ROUTE_TURN_CHECK_MIN_SEGMENT_GRID = 0.025

# Stations placed in water must be within this distance of land (meters).
STATION_MAX_WATER_DISTANCE_TO_LAND_M = 30.0

MAJOR_TERMINAL_STATION_NAMES = (
    "Union Station",
    "Central Station",
    "Grand Central",
    "City Terminal",
    "Metropolitan Terminal",
    "Main Terminal",
    "Civic Center Station",
    "Terminal Station",
    "Exchange Station",
)
MAJOR_TERMINAL_STATION_INTERVAL = 50


def pick_major_terminal_station_name(seed_mix: int, existing_names: set) -> str | None:
    """Return an unused major-terminal name ~1/MAJOR_TERMINAL_STATION_INTERVAL of the time."""
    if (int(seed_mix) % MAJOR_TERMINAL_STATION_INTERVAL) != 0:
        return None
    import random
    rng = random.Random(int(seed_mix) & 0xFFFFFFFF)
    pool = list(MAJOR_TERMINAL_STATION_NAMES)
    rng.shuffle(pool)
    for base in pool:
        if base not in existing_names:
            return base
        for k in range(2, 10):
            cand = f"{base} ({k})"
            if cand not in existing_names:
                return cand
    return None

# Construction cost multiplier for segments that do not follow streets (endpoints + midpoint).
OFF_STREET_SEGMENT_COST_MULTIPLIER = 2.5
# Off-street segments at or below this grid length (0.02 = 20 m) incur no build penalty, NIMBY boost, or maintenance surcharge.
AI_OFF_STREET_FREE_SEGMENT_GRID = 0.02
# NIMBY lawsuit chance scales with off-street share: chance *= (1 + fraction * bonus).
OFF_STREET_NIMBY_CHANCE_BONUS_PER_FRACTION = 2.0
# Line maintenance scales with off-street share: cost *= (1 + fraction * bonus).
OFF_STREET_MAINTENANCE_COST_BONUS_PER_FRACTION = 1.0

# --- War (random world events; configurable in Game settings) ---
# Per-year probabilities after advancing the calendar (each End Year).
WAR_START_CHANCE_PER_YEAR = 0.02
WAR_END_CHANCE_PER_YEAR = 0.30
WAR_WORKER_DRAFT_CHANCE_PER_YEAR = 0.50
# Multiplier applied to marketplace worker hire cost while a war is active (new hires / expanded crews).
WAR_NEW_WORKER_COST_MULTIPLIER = 1.25

# Pacifism law (draft + committee): multiplies per-year war *start* chance for this many
# consecutive "enter calendar year" war rolls (first roll is when advancing into passage_year + 1).
PACIFISM_WAR_START_CHANCE_MULTIPLIER = 0.5
PACIFISM_DURATION_CALENDAR_YEARS = 10

# --- News article pools (defaults; custom rulesets override via news.json) ---
# Economy/history style blurbs used when a year is otherwise quiet.
NEWS_ECONOMY_ARTICLES = (
    (
        "Commuter Briefing: Demand Concentrates in Dense Districts",
        "Early counts show stronger ridership on high-population trunk links and weaker performance on fringe corridors.",
    ),
    (
        "Labor Desk: Skilled Transit Hiring Tightens",
        "Operators reported rising competition for engineers and maintenance staff as networks scale service.",
    ),
    (
        "City Desk: Transit-Oriented Growth Accelerates",
        "Developers cited station access as a key driver behind new residential and commercial projects.",
    ),
    (
        "Regional Snapshot: Inner-City Corridors Outperform Suburban Loops",
        "Analysts noted that short headways and dense stop spacing continue to favor central corridors.",
    ),
    (
        "Finance Watch: Operators Prioritize Cash-Positive Routes",
        "Boards are delaying prestige expansions in favor of links that recover construction costs faster.",
    ),
    (
        "Planning Desk: Station Upgrades Beat Long Detours",
        "Agencies reported better returns from station infill than from low-demand edge extensions.",
    ),
    (
        "Transit Brief: Weekend Ridership Climbs With Waterfront Events",
        "Event-driven demand gave several mixed-use districts their busiest non-workday since records began.",
    ),
    (
        "Economic Pulse: Freight Spurs Off-Peak Passenger Growth",
        "Shared corridor investments unexpectedly improved late-evening passenger reliability.",
    ),
    (
        "Metro Outlook: Faster Construction Timelines Shift Competitive Balance",
        "Firms that finish lines quickly are reaching stable cash flow ahead of rivals.",
    ),
    (
        "Market Minute: Vehicle Debuts Trigger Fleet Modernization",
        "Older rolling stock is being retired sooner as operators chase maintenance savings.",
    ),
    (
        "Urban Desk: Dense Borough Connectors Capture New Riders",
        "Cross-neighborhood links posted stronger adoption than long peripheral branches.",
    ),
    (
        "Operations Column: Timetable Discipline Improves Reliability",
        "Dispatch managers credited tighter peak schedules for fewer cascading delays.",
    ),
    (
        "Steel Desk: Bridge Rivets in Short Supply After Canal Boom",
        "Contractors warned that delayed deliveries could push elevated extensions into next season.",
    ),
    (
        "Harbor Report: Ferry Links Steal Riders From Peripheral Rail Spurs",
        "Analysts said water crossings remained cheaper to build than long inland detours.",
    ),
    (
        "Winter Ledger: Coal Contracts Lift Operating Costs for Steam Operators",
        "Several firms paused nonessential maintenance until fuel quotes stabilized.",
    ),
    (
        "Bond Desk: Municipal Notes Fund Two Elevated Spines",
        "Investors accepted modest yields after agencies pledged transparent ridership audits.",
    ),
    (
        "Workforce Column: Apprentice Programs Ease Engineer Shortage",
        "Graduates reportedly learned signaling faster when taught beside active yards.",
    ),
    (
        "Freight Brief: Shared Tracks Raise Nighttime Passenger Delays",
        "Dispatchers asked shippers for tighter windows after commuters filed formal complaints.",
    ),
    (
        "Retail Survey: Kiosks Near Gates Outsell Suburban Park-and-Rides",
        "Merchants credited foot traffic from timed transfer bonuses.",
    ),
    (
        "Capital Markets: Smaller Operators Tap Private Rail Loans",
        "Analysts warned covenant clauses could bite if ridership missed spring targets.",
    ),
)

# Capitol, city hall, and boardroom filler when event-driven politics is quiet.
NEWS_POLITICS_ARTICLES = (
    (
        "Committee Chair Demands 'Moral Velocity' on Every New Line",
        "Lobbyists promised faster approvals if operators agreed to publish weekly virtue bulletins.",
    ),
    (
        "Opposition Bloc Files Motion to Rename Rush Hour 'Patriotic Mobility Hour'",
        "Debate stalled when clerks could not find enough ink for revised timetables.",
    ),
    (
        "Transit Board Schedules Emergency Hearing on Hat Rules in First Class",
        "Witnesses testified that improper millinery caused measurable platform congestion.",
    ),
    (
        "Mayoral Aide Leaks Plan to Tax Whistles by Decibel",
        "Engineers called the proposal unworkable and requested an exemption for fog.",
    ),
    (
        "Senator Pledges Underground Network 'Visible From the Moon, Eventually'",
        "Cartographers noted the moon has not yet commented on the feasibility study.",
    ),
    (
        "City Council Split on Whether Pigeons Count as Stakeholders",
        "Legal counsel advised feeding pigeons does not constitute formal consultation.",
    ),
    (
        "Governor Signs Bill Requiring Two Signatures Per Ticket Punch",
        "Clerks union celebrated job security; riders formed longer queues overnight.",
    ),
    (
        "Patronage Inquiry Targets Firms That Painted Stations the Wrong Blue",
        "Investigators said the shade was 'insufficiently loyal to municipal tradition.'",
    ),
    (
        "Reform Caucus Proposes Public Ownership of Lost Umbrellas",
        "Analysts predicted fierce debate once someone locates the central umbrella vault.",
    ),
    (
        "Election Rumor Mill: Incumbent Promises Free Timetables, Opponent Promises Accurate Ones",
        "Pollsters reported voters undecided between optimism and punctuality.",
    ),
    (
        "Foreign Envoy Tours Depot, Mistakes Coal Pile for Diplomatic Reception",
        "Protocol officers recovered gracefully by serving tea on a flatcar.",
    ),
    (
        "Budget Office Cuts Ribbon Budget, Increases Subpoena Budget",
        "Agency spokesmen called the rebalancing 'a mature prioritization of accountability.'",
    ),
    (
        "Zoning Board Approves Station Next to Brewery, Denies Station Next to Library",
        "Members cited 'acceptable ambient cheering' versus 'unhelpful silence.'",
    ),
    (
        "Whistleblower Alleges Favoritism in Assignment of Ceremonial Shovels",
        "The comptroller promised a transparent lottery for all future groundbreakings.",
    ),
    (
        "Opposition Demands Public Inquiry Into Ghost Platforms",
        "Witnesses swore they saw timetables listing stops that cartographers denied existed.",
    ),
    (
        "Coalition Proposes Mileage Tax on Every Wheel Turn",
        "Engineers asked whether odometers on locomotives count as wheels for accounting purposes.",
    ),
    (
        "Ethics Panel Reviews Free Coffee at Board Hearings",
        "Lobbyists argued the beverage was ceremonial and therefore constitutionally protected.",
    ),
)

# Odd/comedic flavor pieces (headline, body, min_year).
NEWS_ODDITIES_ARTICLES = (
    (
        "Nose Guild Protests New Ventilation Standard at Underground Stops",
        "Delegates argued that 'adequate airflow' insults centuries of nasal craftsmanship; commuters sneezed in solidarity.",
        0,
    ),
    (
        "City Awards Contract for Platform Nose-Warmers, Budget Office Baffled",
        "Vendors promised wool-lined nostril cozies for winter riders; medical advisers requested a pilot program first.",
        1885,
    ),
    (
        "Cuts on cheese factory made, possibly causing a sulfer smell.",
        "City officials denied transport links were to blame, but commuters reported an unforgettable morning rush.",
        0,
    ),
    (
        "Pigeon Union Demands Platform Seating Rights",
        "Negotiations continue after birds occupied three benches and delayed two ceremonial ribbon cuttings.",
        0,
    ),
    (
        "Transit Police Recover 200 Umbrellas From One Depot",
        "Management called it an 'inventory miracle' and promised a transparent lost-and-found reform plan.",
        0,
    ),
    (
        "Mayor Unveils Ribbon-Cutting Scissors the Size of a Railcar",
        "The event ran twenty minutes late after crews needed a flatbed to deliver the ceremonial equipment.",
        0,
    ),
    (
        "Conductors Report Ghost Stop That Exists Only on One Paper Map",
        "Passengers were reassured after officials confirmed no one can buy a ticket to nowhere.",
        0,
    ),
    (
        "Station Cat Promoted to Assistant Schedule Auditor",
        "After repeatedly sitting on late train reports, the feline was credited with spotlighting delays.",
        0,
    ),
    (
        "Rush-Hour Brass Band Mistaken for New Warning Signal",
        "Commuters applauded, then filed formal feedback requesting fewer trumpets before coffee.",
        0,
    ),
    (
        "Ticket Booth Introduces 'Mystery Fare' Jar, Finance Team Panics",
        "Auditors clarified the jar is decorative and not a replacement for standard accounting.",
        0,
    ),
    (
        "Engineers Win Debate Over Whether Tea Kettle Counts as Steam Tech",
        "The committee adjourned after a demonstration set off every alarm in the annex.",
        0,
    ),
    (
        "Prototype Seat Cushion Rated More Popular Than Destination",
        "Survey respondents praised comfort while admitting they forgot where they were going.",
        0,
    ),
    (
        "Announcer Accidentally Reads Lunch Order Instead of Delay Notice",
        "Passengers now know someone wants two soups and no onions at trackside.",
        0,
    ),
    (
        "Commuters Form Queue to Thank Escalator for Functioning Correctly",
        "Maintenance crews described the celebration as 'unexpected but morale-boosting.'",
        0,
    ),
    (
        "Station Clock Apologizes for Being Seven Minutes Dramatic",
        "Technicians say the mechanism was fine; the issue was an enthusiastic paint job.",
        0,
    ),
    (
        "Lost-and-Found Returns One Violin, Two Boots, and a Mayoral Sash",
        "Officials declined to explain why the sash was tagged as 'musical equipment.'",
        0,
    ),
    (
        "Transit Mascot Declares Tuesday as Officially 'Almost Friday'",
        "Productivity rose briefly before staff remembered calendars are legally binding.",
        0,
    ),
    (
        "Platform Chalk Artist Draws Faster Service, Riders Call It Manifesting",
        "Within hours, dispatch said the drawing had no causal impact, but thanked everyone anyway.",
        0,
    ),
    (
        "Depot Cafeteria Names Sandwich After Most Delayed Route",
        "Sales surged, mostly from staff stress-testing the concept during shift changes.",
        0,
    ),
    (
        "TV show product places tranclucent clothing",
        "Broadcast sponsors celebrated a record audience while censors quietly updated the rulebook overnight.",
        1927,
    ),
    (
        "Late-Night Radio Claims Tunnels Are Perfect for Echo-Based Weather Forecasts",
        "Meteorologists disagreed, but admitted the acoustics were excellent.",
        1900,
    ),
    (
        "Cinema Newsreel Credits Train Whistle for Dramatic Box Office Recovery",
        "Producers denied any sponsorship while handing out complimentary timetables.",
        1910,
    ),
    (
        "Experimental Neon Timetable Causes Three Poets to Miss Their Stops",
        "Cultural critics praised the lighting and requested a second viewing.",
        1920,
    ),
    (
        "Portable Camera Crew Films Entire Platform Arguing About Hat Etiquette",
        "Editors called it a breakthrough in unscripted urban storytelling.",
        1895,
    ),
    (
        "Department Store Installs Miniature Train in Window, Adults Queue First",
        "Retail analysts called the display 'a triumph of civic-themed impulse shopping.'",
        1880,
    ),
    (
        "Perfume Counter at Terminus Declared 'Too Powerful for Timetables'",
        "Dispatchers reported three conductors detoured by involuntary nostalgia.",
        1890,
    ),
    (
        "Street Vendor Sells 'Authentic Tunnel Echo' in Tin Cans",
        "Buyers insisted the product was immersive; acousticians called it beans.",
        1905,
    ),
    (
        "Newspaper Runs Poll: Should Conductors Tip Hats to Puddles?",
        "Results were inconclusive after rain canceled the follow-up survey.",
        1875,
    ),
    (
        "Orphaned Parasol Found Riding Solo to End of Line",
        "Attendants adopted it as mascot until finance asked for a fare.",
        1888,
    ),
    (
        "City Declares War on Loose Buttons, Declares Truce by Lunch",
        "Tailors union credited the peace accord to superior thread.",
        1900,
    ),
    (
        "Inspector General Finds Depot Clock Running on Enthusiasm",
        "Technicians replaced the spring and apologized to punctual commuters.",
        1885,
    ),
    (
        "Museum Offers Free Admission to Anyone Who Can Pronounce 'Intermodal'",
        "Winners received commemorative tokens redeemable at exactly one kiosk.",
        1925,
    ),
    (
        "Citizens Petition to Replace Turnstiles With Polite Applause",
        "Finance officials noted applause does not yet integrate with fare collection.",
        1898,
    ),
    (
        "Snowstorm Traps Brass Band Inside Waiting Room for Six Hours",
        "Passengers reported the impromptu concert improved morale if not departure times.",
        1870,
    ),
)