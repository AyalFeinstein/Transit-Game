# Transit Game Rules

## Overview
Transit Game is a strategic transportation simulation game where players build and operate transit lines to generate profit. The game spans from 1830 to 2050, allowing players to experience the evolution of transportation technology.

## Objective
Build profitable transit lines by connecting population centers with stations and operating trainsets. Manage your finances, workers, and infrastructure to maximize profits while avoiding bankruptcy.

## Game Mechanics

### Starting Conditions
- **Starting Money**: $30,000 (default, can be customized)
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

## Building Transit Lines

### Creating Lines
- Lines connect two or more points on the map
- Lines can be built at different elevations:
  - **Street level**: Cheapest, standard construction
  - **Above ground**: Elevated tracks, more expensive
  - **Underground**: Subway tunnels, most expensive
- Lines can traverse water using ferry lines
- **Ferry validation**: Ferry routes must be within 60 m of water; each point and segment is checked. The game uses map presets (e.g. NYC water areas) and OSM for water detection.
- Construction time depends on:
  - Number of construction workers assigned
  - Elevation type (underground/elevated take longer)
  - Terrain (water crossings take longer)
  - Minimum 1 turn required

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
- Worker salaries increase with inflation over time
- Workers demand higher salaries as time progresses (1x in 1830, up to 5x by 2050)
- If workers are underpaid by 0.5x or more, they may go on strike
- Strikes last 1-5 turns and prevent affected operations (engineers on strike = no trains run)

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

### Profit Generation
- Profit = (Fare × Capacity × Capacity Utilization × Number of Passes) - Maintenance Costs
- Each trainset passes each station twice per round trip (forward and backward)
- Number of passes depends on:
  - Trainset frequency (trains per hour)
  - Round trip time
  - Line schedule (peak, off-peak, night)
- Profit is limited by population at station locations

### Line Quality
- Line quality affects ridership (higher quality = more passengers)
- Quality components:
  - Base quality: 1.0 + (0.1 × maintenance workers)
  - Locomotive quality contribution (up to 0.3)
  - Railcar quality contribution (up to 0.5)
- Maximum line quality: 3.0
- Quality improves with more maintenance workers

### Fares
- Players can set fares for their lines
- Maximum acceptable fare increases with time ($2 in 1830, $15 in 2050)
- Fares above maximum reduce capacity utilization (fewer passengers)
- Higher fares reduce ridership but increase revenue per passenger

### Capacity Utilization
- Base capacity utilization: 60%
- Affected by:
  - Line quality (higher quality = more utilization)
  - Fare pricing (too high = less utilization)
  - Event effects (e.g., automobile competition)

## Events and Disasters

### Natural Disasters
- Frequency can be adjusted in game settings
- Disasters can damage lines and reduce population
- Players must repair damaged infrastructure

### Historical Events
- Events occur at specific years
- Can affect:
  - Population (growth or reduction)
  - Profit multipliers (e.g., automobile competition reduces transit profits)
  - Construction costs (e.g., government policies)
  - Resource availability

#### Major Historical Events

**Invention of the Automobile (1910)**
- Gradually reduces transit ridership as people move to suburbs and use cars
- Effect increases over time (0% to 60% population reduction by 2020)

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
- Protects workers on strike from being fired
- Prevents players from firing striking workers

**Trust Busting (1904)**
- Enables lawsuits against monopolistic practices
- Players can sue each other for monopolizing

**Environmental Protection Act (1970)**
- Establishes the Environmental Protection Agency (EPA)
- Enables EPA lawsuits for environmental damage

**COVID-19 Pandemic (2020)**
- Temporarily reduces transit ridership due to health concerns
- Effect decreases over 4 years as recovery progresses

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

### Lawsuits and Excuses
- Players and agencies can sue over various excuses (safety violations, property damage, noise, environmental damage, breach of contract, discrimination, accidents, service interruption, monopolizing, wrongful termination, wage disputes, emissions, accessibility, false advertising).
- Damages scale with inflation. More lawyers improve your chance of winning.
- **Agency lawsuits**: NIMBYs (after 1890) and EPA (after 1970) can bring cases; lobbying can delay these checks for multiple years.

## Financial Management

### Income Sources
- Line profits (fare revenue)
- Selling trainsets (half price)
- Selling to Historical Society (5x original price for items 100+ years old)

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

## Strategies

### Early Game
- Start with simple lines connecting nearby population centers
- Use early steam locomotives or horse-drawn vehicles
- Focus on high-population areas
- Build stations strategically

### Mid Game
- Upgrade to diesel or electric locomotives
- Expand network to cover more territory
- Add maintenance workers to improve quality
- Consider line upgrades for better vehicles

### Late Game
- Use modern high-speed vehicles
- Optimize schedules and frequencies
- Manage worker salaries to avoid strikes
- Expand to maximize coverage

## Tips
- Balance fare pricing: too high reduces ridership, too low reduces profit
- Assign maintenance workers to improve line quality
- Monitor worker demands to avoid strikes
- Plan construction carefully - underground/elevated lines are expensive
- Consider ferry lines for water crossings
- Keep enough engineers to operate all trainsets
- Watch for marketplace sales to save money
- Sell old vehicles to Historical Society for good returns
