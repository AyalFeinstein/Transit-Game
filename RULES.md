# Transit Game Rules

## Introduction and overview
Transit Game is a strategic transportation simulation where you build and operate transit lines for profit. You connect population and demand with stations and rolling stock, manage money, workers, government, and events, and try to stay solvent to the end of the timeline. The default campaign spans **1830–2050**, so technology, competition, and costs evolve across the full modern transit era.

## Objective
Build profitable transit lines by connecting population centers with stations and operating trainsets. Manage your finances, workers, and infrastructure to maximize profits while avoiding bankruptcy.

## Game Mechanics

### Starting Conditions
- **Starting Money**: $50,000 (default from game presets; can be customized when starting a game)
- **Starting Year**: 1830
- **End Year**: 2050
- Players take turns building and managing their transit networks

### Player Elimination
- Players are eliminated if their money drops to $0 or below
- Eliminated players lose all their lines and trainsets
- The game continues until only one player remains or the end year is reached

### Turns and Years
- Each turn represents one year
- Players can perform multiple actions per turn
- At the end of each year:
  - Lines generate profit based on ridership
  - Maintenance costs are deducted
  - Worker salaries are paid
  - Marketplace items may go on sale or expire
  - Population may change due to events

## Building transit lines

You can build several kinds of lines depending on the map preset. Each mode has its own routing rules; construction time and cost still depend on length, elevation (where applicable), workers, and terrain.

**Trains (rail lines)** connect two or more points with fixed guideway. Segments can use different **elevations**: **street level** (cheapest), **above ground** (elevated, pricier), or **underground** (subways, most expensive). Rail is the default mode for long corridors and heavy capacity; you later add **overhead wire** or **third rail** if you want electric stock (see Line upgrades). Rail does not replace ferries for long water crossings—use ferry lines where the map allows.

**Bus lines** run on **streets and roads**. On supported presets (including many OSM maps), routes are checked against the road network; bus **stops** cannot be placed in water, while short segments over water are allowed when both ends are on land (e.g. bridges). Buses are usually cheaper to build than rail but face **traffic** penalties in later eras unless you invest in busways.

**Ferry lines** are for **water**. Terminals and the route must stay within **60 m** of navigable water; each vertex and segment is validated using the preset and, where available, OSM. Ferries use **water** elevation for segments; they cannot run over land as a substitute for rail or bus.

### Construction (all line types)
- Lines connect at least **two** points on the map
- Construction time depends on: number of **construction workers**, elevation (underground/elevated take longer), terrain (e.g. water crossings), and a **minimum of one turn**
- AI players refuse to queue a new line when a conservative model suggests **annual operating revenue would not cover minimum line maintenance plus one engineer’s base salary** once the line is in service. The estimate uses **endpoints and interior samples along the route**, the host **profit margin**, event **profit multipliers** (capped downward for the check), and **AI difficulty** (easier AIs accept slightly thinner projected margins than hard AIs). Humans are not blocked by this check.

### Stations
- Stations must be built on lines to generate revenue
- Stations connect to population points on the map
- Multiple stations can be built on a single line
- Stations can be extended to serve more population points
- Station costs increase with each additional station on a line

### Line Upgrades
- **Overhead Wire**: Required for electric locomotives and EMUs
- **Third Rail**: Required for subway cars
- Upgrades cost money and must be completed before using electric vehicles

## Vehicles and Trainsets

### Vehicle Types
- **Locomotives**: Pulling power, can have railcars attached (if `can_have_railcars=True`)
- **Railcars**: Passenger capacity, attached to locomotives
- **Trainsets**: Combination of locomotive + railcars
- **Self-contained Units**: EMUs, DMUs, streetcars, subway cars that don't need railcars

### Vehicle Categories
- **Steam Locomotives**: Early era (1820s-1900s), use Coal or Wood
- **Diesel Locomotives**: Mid era (1920s+), use Oil
- **Electric Locomotives**: Require overhead wire, use Electricity
- **EMUs**: Electric Multiple Units, self-contained or with associated railcars
- **Subway Cars**: Require third rail, self-contained
- **Trolleys/LRVs**: Require overhead wire, self-contained
- **Ferries**: Water-only vehicles, cannot traverse land
- **Horse-Drawn Vehicles**: Early era, use Food resource

### Assembling Trainsets
- Purchase locomotives and railcars from the marketplace
- Assemble them into trainsets in the Rolling Stock tab
- Locomotives with `can_have_railcars=False` cannot have railcars attached
- Some locomotives have associated railcars (EMUs, DMUs)
- Maximum number of railcars depends on locomotive's `max_cars` attribute

### Assigning Trainsets to Lines
- Trainsets must be assigned to lines to generate revenue
- Each trainset can only be assigned to one line
- Lines can have multiple trainsets assigned
- Trainsets require engineers to operate

## Workers

### Worker Types
- **Engineers**: Required to operate trainsets (1 per trainset)
- **Construction Workers**: Required to build lines and stations (more workers = faster construction)
- **Maintenance Workers**: Improve line quality, increasing ridership
- **Lawyers**: Help win lawsuits against other players

### Worker Management
- Workers are rented from the marketplace
- **Salary inflation**: Base worker rent uses the same calendar inflation curve as marketplace prices (about **1× in 1830 to 10× by 2050**), before your per-type salary multiplier setting
- **Demanded pay**: Workers also expect a higher multiplier over the era (**about 1× in 1830 up to 5× by 2050**, with small random variance)
- If workers are underpaid by **0.5× or more** relative to demand, they may go on strike
- Strikes last **1–5** turns. **Engineers** on strike: assigned trainsets cannot earn line revenue. **Construction workers** on strike: you have **no effective construction headcount** for building until the strike ends

## Marketplace

### Available Items
- **Lines**: Pre-built lines available for purchase
- **Trainsets**: Ready-to-use vehicle combinations
- **Locomotives**: Individual locomotives
- **Ferries**: Water-only vehicles
- **Railcars**: Passenger cars to attach to locomotives
- **Resources**: Fuel costs (Coal, Wood, Oil, Electricity, Food)
- **Workers**: Engineers, construction workers, maintenance workers, lawyers

### Sales and Pricing
- Items may go on sale (5-30% discount)
- Sales last for one turn only
- Once an item has been on sale, it will never be on sale again. When a sale appears, you might want to take it.
- Prices include inflation (1x in 1830, up to 10x by 2050)
- Supply and demand affects prices (lower quantity = higher price)
- Older items (20+ years) get age-based discounts

### Marketplace Categories
- Items are organized by category (Locomotives, Ferries, Railcars, Trainsets, etc.)
- Items must be within 40 years of the current year to appear
- Items within 20 years are always available

## Line Operations

### Profit generation (year-end, primary model)
- Each year the simulation allocates **ridership** among lines (population, transfers, and line quality influence who rides where). Your line’s share of riders is capped by **how many passengers your schedules could carry** (service frequency × total trainset capacity on the line).
- **Line revenue** (before track/rolling-stock maintenance in the yearly settlement) is driven by:
  - **Riders served** (minimum of allocated ridership and scheduled capacity; small floors apply when ridership sampling is sparse)
  - **Fare**, compared to a **maximum acceptable fare** that rises over time (**\$2 in 1830** to **\$15 in 2050**); the fare used for revenue is effectively scaled by an inflation factor (capped at **5.5×** by 2050) so nominal fares stay in a sensible “real” range
  - **Game profit margin** setting (host option; scales all line profits)
  - **Line quality**: `update_quality()` combines maintenance workers and vehicle quality into a score up to **3.0**; revenue uses a **quality multiplier** derived from that score (higher quality ⇒ higher profit, all else equal)
  - **Event profit multipliers** (e.g. **COVID-19** scales line profits down during the recovery window) and **natural-disaster recovery** can temporarily reduce line profits
- **Maintenance** for track segments, vehicles, and schedule-related operating cost is computed **separately** (`calc_maitnance` / year-end finances) and reduces cash along with other expenses.
- A **legacy per-station formula** still exists on the `Line` class for a few secondary calculations (e.g. some line valuation helpers); year-end cashflow uses the **ridership-based** model above.

### Line quality (maintenance and rolling stock)
- **Maintenance workers** on the line and **vehicle quality** raise the line’s quality score (components: base from maintenance workers, locomotive contribution up to **0.3**, railcar contribution up to **0.5**; **maximum 3.0**)
- Higher quality increases profit in the **ridership-based** year-end calculation and also influences **ridership assignment** in the simulation

### Fares
- Players set each line’s fare
- The **maximum acceptable fare** rises linearly from **\$2** (1830) to **\$15** (2050); in the legacy station-by-station helper, fares above that cap reduce modeled utilization. In the primary ridership model, fare is clamped to the acceptable cap before the inflation scaling described above

### Capacity and schedules
- **Schedules** (peak / off-peak / night frequencies) and trainset **speed** set how many trips per year the line can run; unused capacity does not earn revenue beyond what riders demand
- **Automobile** and other events that change **map population** indirectly change ridership; **COVID-19** applies a direct **profit multiplier** on lines (see Events)

## Events and Disasters

### Natural Disasters
- Frequency can be adjusted in game settings
- Disasters can **damage** lines (queued repair segments) and may **reduce population** in the event’s year
- While a disaster is in its **recovery window**, **line profits** are reduced by a type-specific fraction (e.g. earthquake up to **50%** reduction for **2** years) and repairs can cost more until recovery ends
- Players must repair damaged infrastructure

### War (random world events)
- Each time you advance the year, the simulation may **start** a war (default 2% per year), **end** an ongoing war (default 30% per year), and while a war is active may trigger a **draft** event (default 50% per year).
- **Pacifism (drafted law)**: If the **Pacifism** law passes committee, the per-year chance of a **new** war **starting** is multiplied by **50%** for the **next 10** war-start rolls—i.e. when advancing the calendar into years **(passage year + 1)** through **(passage year + 10)**. Passing again while active only extends coverage if the new end date would be later. End-war and draft rolls are unchanged.
- **Drafts**: When a draft fires, workers can be removed from operating companies. The government weights losses toward players with **less favorable** influence (higher unfavorable vs. favorable seat influence), so lobbying and elections matter.
- **Worker costs**: While a war is active, **new worker hires** (marketplace rentals that increase headcount) cost more (default 1.25×). Salaries for workers you already employ follow the normal inflation and pay rules.
- **News**: Wars, armistices, and draft impacts generate **news log** headlines.
- **Settings**: On the host **Game** tab (Game Settings), you can change the yearly start/end/draft percentages and the wartime new-hire cost multiplier before starting a game.

### Historical Events
- Events occur at specific years
- Can affect:
  - **Population** (growth, reduction, or regional shifts on preset maps)
  - **Line profit multipliers** (e.g. temporary demand shocks)
  - **Construction costs** (e.g. government policies)
  - **Lawsuit / agency rules** (flags the game checks, such as trust-busting or EPA)

#### Major Historical Events

**Invention of the Automobile (1910)**
- Models suburbanization and car competition by reducing **underlying population growth** used for ridership (not a separate “profit %” flag for this event)
- Effect ramps from **0%** at 1910 to **60%** maximum drag by **2020**, then continues from **60% to 70%** by **2050** (same mechanic: population growth adjustment)

**Government Automobile Preference (1945-1975)**
- Post-World War II government policies heavily favor automobile infrastructure
- Massive highway construction and suburbanization programs
- Reduced funding for public transportation
- Effects:
  - Reduces transit ridership (peaks at 25% reduction in 1960)
  - Increases construction costs for public transit (peaks at 30% increase in 1960)
  - Effect gradually decreases from 1960 to 1975 as environmental concerns rise
- This period represents the "highway era" when public transit was neglected in favor of car-centric development

**Worker Protection Act (1925)**
- You **cannot fire workers who are currently on strike**
- From 1925 onward you may fire **at most half** of each worker type you employ **per year** (rounded down; e.g. 1 worker of a type ⇒ 0 may be fired that year)

**Trust Busting (1904)**
- Enables lawsuits against monopolistic practices
- Players can sue each other for monopolizing

**Environmental Protection Act (1970)**
- Establishes the Environmental Protection Agency (EPA)
- Enables EPA lawsuits for environmental damage

**COVID-19 Pandemic (2020)**
- Temporarily reduces **transit line profit** (demand shock): profit multiplier starts around **35%** in 2020 and recovers **linearly to 100%** over **four** calendar years (no separate city-wide population penalty for this event)

## Government

### Government Chamber
- 50 seats plus 1 tiebreaker leader control taxes and annual laws
- First election at year 1832; subsequent elections every 6 years
- Seats are unaffiliated (gray) until first election; can be lobbied before then
- Green = favorable to you (lower taxes), red = unfavorable (higher taxes)
- **Seat influence**: Leader (tiebreaker) has most influence (~3 seats). Regular seats have base influence. Newly elected seats start at 40% influence and gain 10% per year until full.
- **Seat lobby**: Takes effect at end of turn (when you advance the year). Results revealed at next election.

### Election Behavior
- **Good behavior** (no strikes, paying workers well, not losing lawsuits): Government is less likely to change in election years. Favorable seats tend to stay favorable.
- **Bad behavior** (strikes, underpaying workers, losing agency lawsuits): Government is more likely to change when favorable to you, and less likely to change when unfavorable. Treating workers poorly and losing lawsuits makes it harder to keep a favorable government.

### Government Tab
- Refreshes automatically at the beginning of every year
- Right-click a seat to lobby it (takes effect at end of turn; results revealed at next election)

## Lobbying and Laws

### Lobbying
- Players can spend money to lobby for various outcomes (no minimum cost; success chance increases with amount).
- **Drop a lawsuit excuse**: Government drops one pending case against you.
- **Tax break**: Half taxes for one year (next year).
- **One-time grant**: Immediate government grant (half of annual funding amount).
- **Pass a law**: Delay agency lawsuit checks against you for 2 years.
- **Area protection**: Lobby for area protections (delay next 2 lawsuit checks in your favor).
- **Government attack**: Lobby for a one-time financial penalty on another player (applied next year).
- **Government funding (1970+)**: Be chosen for that year's government funding, or redirect it from another player to you.

### Law Drafting
- Lawyers can draft laws (1 year). Assign more lawyers to a draft for higher success chance when submitting to the committee (+5% per extra lawyer, max +25%).
- Occupied lawyers cannot fight lawsuits.
- **Pacifism**: A draftable law that, if passed, **halves** the yearly probability of a **new** war beginning for **10** years (see **War**). If passed again before it expires, the end date moves later only when that would lengthen the effect.

### Lawsuits and Excuses
- Players and agencies can sue over various excuses (safety violations, property damage, noise, environmental damage, breach of contract, discrimination, accidents, service interruption, monopolizing, wrongful termination, wage disputes, emissions, accessibility, false advertising).
- Damages scale with inflation. More lawyers improve your chance of winning.
- **Agency lawsuits**: NIMBYs (after 1890) and EPA (after 1970) can bring cases; lobbying can delay these checks for multiple years.

## Financial Management

### Income Sources
- Line profits (fare revenue, ridership-based year-end model)
- Selling trainsets (**half** of their current inflated marketplace-style price)
- **Historical Society**: **5×** the trainset’s or spare locomotive’s **catalog base price** (sum of component **base prices**, the same basis as new equipment before inflation) for rolling stock whose locomotive is **100+ years** old

### Expenses
- Line construction costs
- Station construction costs
- Line upgrades (overhead wire, third rail)
- Trainset purchases
- Worker salaries
- Maintenance costs (trainsets and lines)
- Resource costs (fuel for vehicles)

### Bankruptcy
- If money drops to $0 or below, player is eliminated
- All lines and trainsets are removed
- Player cannot continue playing

## Additional systems (not covered above)

These exist in the game and settings but are not spelled out in the core sections:

- **Bus lines** and **road-snapped** routing on supported OSM presets
- **Mergers** between players (optional rules) and **random AI** companies joining mid-campaign (interval and difficulty configurable)
- **Map presets** (e.g. standard grid, NYC, NY Metro): discovery-based loading—add new preset modules without editing core files
- **OSM** water/terrain for display and **ferry** validation (**60 m** near-water rule)
- **Host / game settings**: worker replenishment, natural disaster frequency, marketplace years visible, **line build speed multiplier**, **marketplace bulk discount**, **inflation rate** (scales lawsuit/lobby/etc. inflation caps), **profit margin**, **war** probabilities and wartime hire multiplier, **AI** options, optional **`game_seed`** for reproducible AI
- **News log** filler articles (economy/oddities pools) and war/draft headlines
- **Preset-specific timeline events** (e.g. NYC history) in addition to the global events in this document
- **Edit mode** (remove population points) and session-scoped deleted-point memory in networked play
- **Government funding** (1970+) and **law drafting** interactions with the chamber (see Lobbying / Government)

## Strategies and tips

### Early game
- Start with simple lines connecting nearby population centers
- Use early steam locomotives or horse-drawn vehicles
- Focus on high-population areas
- Build stations strategically

### Mid game
- Upgrade to diesel or electric locomotives
- Expand network to cover more territory
- Add maintenance workers to improve quality
- Consider line upgrades for better vehicles

### Late game
- Use modern high-speed vehicles
- Optimize schedules and frequencies
- Manage worker salaries to avoid strikes
- Expand to maximize coverage

### Ongoing play
- Balance fare pricing: too high reduces ridership, too low reduces profit
- Assign maintenance workers to improve line quality
- Monitor worker demands to avoid strikes
- Plan construction carefully—underground and elevated lines are expensive
- Use **ferry lines** for water crossings where rail or bus is impractical
- Keep enough **engineers** to operate all trainsets
- Watch for **marketplace sales** to save money
- Sell very old rolling stock to the **Historical Society** for strong returns (see Financial Management)
