"""Government entity: 50 seats + tiebreaker, controls taxes, re-elected every 6 years.
Seats have different influence: leader (tiebreaker) has most; new seats have less and gain over time."""
import random
from typing import Optional


# Small chance each year that laws override favor (pass favorable laws for unfavored, or vice versa)
# Low value = laws most likely favor the player the government favors
LAW_OVERRIDE_CHANCE = 0.01

# First election year - seats are unaffiliated (gray) until then; can be lobbyed before
FIRST_ELECTION_YEAR = 1832

# Influence: leader weight (tiebreaker counts as this many seats), base seat weight, new-seat penalty
LEADER_INFLUENCE_WEIGHT = 3.0  # Leader counts as ~3 seats
BASE_SEAT_INFLUENCE = 1.0
NEW_SEAT_INFLUENCE = 0.4  # Newly elected seats start at 40%
INFLUENCE_GROWTH_PER_YEAR = 0.1  # +10% per year until full


class Government:
    """Government with 50 seats + 1 tiebreaker leader.
    Each seat is favorable (True) or unfavorable (False) to a given player.
    Government controls tax rates. Re-elected every 6 years.
    Seats are unaffiliated (gray) until first election (1832); can be lobbyed before then."""

    NUM_SEATS = 50
    ELECTION_INTERVAL = 6

    def __init__(self, start_year: int, num_players: int):
        """Create government. start_year: game start year. num_players: for seat assignment."""
        self._election_year = FIRST_ELECTION_YEAR - self.ELECTION_INTERVAL  # So first election is 1832
        self._num_players = num_players
        self._seats: list[Optional[int]] = []
        self._tiebreaker: Optional[int] = None
        self._lobbied_hidden: set[int] = set()
        self._last_override_year: Optional[int] = None  # Override only lasts one turn; next year normal
        self._has_held_election = False
        # Per-seat influence: (elected_year, years_held) - new seats have less influence, grows over time
        self._seat_elected_year: list[Optional[int]] = [None] * self.NUM_SEATS
        self._tiebreaker_elected_year: Optional[int] = None
        self._current_year = start_year
        self._randomize_seats()

    def add_player(self, player_id: int) -> None:
        """Add a new player to government (e.g. from random AI spawn)."""
        self._num_players = max(self._num_players, player_id + 1)

    def _randomize_seats(self) -> None:
        """Randomly assign seats and tiebreaker."""
        options = list(range(self._num_players)) + [None]  # favor player 0, 1, ... or neutral
        self._seats = [random.choice(options) for _ in range(self.NUM_SEATS)]
        self._tiebreaker = random.choice(options) if options else None

    def _randomize_seats_with_retention(self, retention: dict, eliminated_player_ids: set = None) -> None:
        """Re-randomize seats. Good players keep favorable seats. Bad players (low retention):
        favorable seats change more, unfavorable seats (favoring others) stay more.
        Eliminated players are never assigned favorable seats."""
        eliminated = eliminated_player_ids or set()
        options = [p for p in range(self._num_players) if p not in eliminated] + [None]
        if not options:
            options = [None]
        bad_players = {p for p, r in retention.items() if r < 0.5 and p not in eliminated}
        badness = max(0.0, 0.5 - min(retention.values())) if retention else 0.0
        for i in range(self.NUM_SEATS):
            current = self._seats[i]
            if current in eliminated:
                self._seats[i] = random.choice(options)
                self._seat_elected_year[i] = self._current_year
                continue
            keep_chance = retention.get(current, 0.0) if current is not None else 0.0
            if badness > 0 and current is not None and current not in bad_players:
                keep_chance = min(1.0, keep_chance + badness)
            if current is not None and keep_chance > 0 and random.random() < keep_chance:
                self._seat_elected_year[i] = self._seat_elected_year[i] or self._current_year
                continue
            self._seats[i] = random.choice(options)
            self._seat_elected_year[i] = self._current_year  # New holder
        current_tb = self._tiebreaker
        if current_tb in eliminated:
            self._tiebreaker = random.choice(options) if options else None
            self._tiebreaker_elected_year = self._current_year
        else:
            keep_chance = retention.get(current_tb, 0.0) if current_tb is not None else 0.0
            if badness > 0 and current_tb is not None and current_tb not in bad_players:
                keep_chance = min(1.0, keep_chance + badness)
            if current_tb is not None and keep_chance > 0 and random.random() < keep_chance:
                self._tiebreaker_elected_year = self._tiebreaker_elected_year or self._current_year
            else:
                self._tiebreaker = random.choice(options) if options else None
                self._tiebreaker_elected_year = self._current_year

    def hold_election(self, current_year: int, player_retention_bonuses: dict = None, eliminated_player_ids: set = None) -> bool:
        """Hold election if due (every 6 years). Returns True if election was held.
        First election (1832) reveals seats; subsequent elections re-randomize.
        player_retention_bonuses: dict[player_id, float] 0-1; higher = less likely to change.
        eliminated_player_ids: players who are eliminated - never assigned favorable seats."""
        self._current_year = current_year
        eliminated = eliminated_player_ids or set()
        if not self._has_held_election:
            if current_year >= FIRST_ELECTION_YEAR:
                self._lobbied_hidden.clear()  # Reveal lobby results
                self._has_held_election = True
                self._election_year = current_year
                # Reassign any seats held by eliminated players to active players or neutral
                options = [p for p in range(self._num_players) if p not in eliminated] + [None]
                if not options:
                    options = [None]
                for i in range(self.NUM_SEATS):
                    if self._seats[i] in eliminated:
                        self._seats[i] = random.choice(options)
                    if self._seat_elected_year[i] is None:
                        self._seat_elected_year[i] = current_year
                if self._tiebreaker in eliminated:
                    self._tiebreaker = random.choice(options) if options else None
                if self._tiebreaker_elected_year is None:
                    self._tiebreaker_elected_year = current_year
                return True
            return False
        if current_year >= self._election_year + self.ELECTION_INTERVAL:
            self._lobbied_hidden.clear()  # Reveal all lobby results before re-election
            self._randomize_seats_with_retention(player_retention_bonuses or {}, eliminated)
            self._election_year = current_year
            return True
        return False

    def _get_seat_influence(self, seat_index: int) -> float:
        """Influence weight for a seat. New seats have less; grows over time."""
        if seat_index < 0 or seat_index >= self.NUM_SEATS:
            return 0.0
        elected = self._seat_elected_year[seat_index]
        if elected is None:
            return BASE_SEAT_INFLUENCE
        years_held = self._current_year - elected
        growth = min(1.0, years_held * INFLUENCE_GROWTH_PER_YEAR)
        return NEW_SEAT_INFLUENCE + (BASE_SEAT_INFLUENCE - NEW_SEAT_INFLUENCE) * growth

    def get_favorable_count(self, player_id: int) -> int:
        """Number of seats favorable to this player (legacy count)."""
        return sum(1 for s in self._seats if s == player_id)

    def get_favorable_influence(self, player_id: int) -> float:
        """Total influence of seats favorable to this player."""
        total = 0.0
        for i, s in enumerate(self._seats):
            if s == player_id:
                total += self._get_seat_influence(i)
        if self._tiebreaker == player_id:
            total += LEADER_INFLUENCE_WEIGHT
        return total

    def get_favorable_influence_for_players(self, player_ids: set) -> float:
        """Total influence of seats favorable to any of the given players (for multi-target good laws)."""
        if not player_ids:
            return 0.0
        total = 0.0
        for i, s in enumerate(self._seats):
            if s in player_ids:
                total += self._get_seat_influence(i)
        if self._tiebreaker in player_ids:
            total += LEADER_INFLUENCE_WEIGHT
        return total

    def get_total_influence(self) -> float:
        """Total influence of all seats (for computing unfavorable = total - favorable)."""
        total = 0.0
        for i in range(self.NUM_SEATS):
            total += self._get_seat_influence(i)
        return total + LEADER_INFLUENCE_WEIGHT

    def get_unfavorable_influence(self, player_id: int) -> float:
        """Total influence of seats unfavorable to this player."""
        total = 0.0
        for i, s in enumerate(self._seats):
            if s != player_id:
                total += self._get_seat_influence(i)
        if self._tiebreaker != player_id:
            total += LEADER_INFLUENCE_WEIGHT
        return total

    def get_tiebreaker_favorable(self, player_id: int) -> bool:
        """Whether tiebreaker favors this player."""
        return self._tiebreaker == player_id

    def get_tax_multiplier(self, player_id: int) -> float:
        """Tax multiplier based on government composition (weighted by influence).
        1.0 = normal, <1 = lower taxes, >1 = higher."""
        favorable = self.get_favorable_influence(player_id)
        unfavorable = self.get_unfavorable_influence(player_id)
        # Use influence-weighted comparison
        if favorable > unfavorable:
            return 0.75
        if unfavorable > favorable:
            return 1.25
        return 0.75 if self.get_tiebreaker_favorable(player_id) else 1.25

    def get_seats_for_display(self, player_id: int, current_year: int = 1830) -> list[tuple[Optional[bool], bool]]:
        """Return list of (is_favorable, is_tiebreaker) for UI. 50 seats + tiebreaker.
        is_favorable is None for unaffiliated/lobbied seats not yet revealed (until election).
        Before first election (1832), all seats show as unaffiliated (gray)."""
        if not self._has_held_election and current_year < FIRST_ELECTION_YEAR:
            return [(None, False)] * self.NUM_SEATS + [(None, True)]
        result = []
        for i, s in enumerate(self._seats):
            if i in self._lobbied_hidden:
                result.append((None, False))
            else:
                result.append((s == player_id, False))
        if 50 in self._lobbied_hidden:
            result.append((None, True))
        else:
            result.append((self._tiebreaker == player_id, True))
        return result

    def get_seats_with_allegiance(self, current_year: int = 1830) -> list[tuple[Optional[int], float, bool]]:
        """Return list of (allegiance_player_id, influence, is_tiebreaker) for UI. 50 seats + tiebreaker.
        allegiance_player_id is None for no allegiance (gray) or hidden/lobbied seats."""
        if not self._has_held_election and current_year < FIRST_ELECTION_YEAR:
            inf = self._get_seat_influence(0)
            return [(None, inf, False)] * self.NUM_SEATS + [(None, LEADER_INFLUENCE_WEIGHT, True)]
        result = []
        for i, s in enumerate(self._seats):
            if i in self._lobbied_hidden:
                result.append((None, self._get_seat_influence(i), False))
            else:
                result.append((s, self._get_seat_influence(i), False))
        if 50 in self._lobbied_hidden:
            result.append((None, LEADER_INFLUENCE_WEIGHT, True))
        else:
            result.append((self._tiebreaker, LEADER_INFLUENCE_WEIGHT, True))
        return result

    def get_next_election_year(self, current_year: int) -> int:
        """Year of next election."""
        if not self._has_held_election:
            return FIRST_ELECTION_YEAR
        next_elec = self._election_year + self.ELECTION_INTERVAL
        while next_elec <= current_year:
            next_elec += self.ELECTION_INTERVAL
        return next_elec

    def lobby_flip_seat(self, player_id: int) -> tuple[bool, int | None, int | None]:
        """Lobby to flip one unfavorable seat to favorable. Returns (success, seat_index, previous_owner_id).
        Result is hidden until next election."""
        unfavorable_indices = [i for i, s in enumerate(self._seats) if s != player_id]
        if not unfavorable_indices:
            return False, None, None
        idx = random.choice(unfavorable_indices)
        prev_owner = self._seats[idx]
        self._seats[idx] = player_id
        self._lobbied_hidden.add(idx)
        return True, idx, prev_owner

    def lobby_flip_seat_at_index(self, seat_index: int, player_id: int) -> bool:
        """Lobby to flip a specific seat to favorable. Returns True if it was unfavorable.
        Result is hidden until next election (seat added to _lobbied_hidden)."""
        if seat_index < 0 or seat_index >= self.NUM_SEATS:
            return False
        if self._seats[seat_index] == player_id:
            return False
        self._seats[seat_index] = player_id
        self._lobbied_hidden.add(seat_index)
        return True

    def lobby_flip_tiebreaker(self, player_id: int) -> bool:
        """Lobby to make tiebreaker favorable. Returns True if tiebreaker was unfavorable.
        Result is hidden until next election."""
        if self._tiebreaker == player_id:
            return False
        self._tiebreaker = player_id
        self._lobbied_hidden.add(50)
        return True

    def pass_annual_laws(self, year: int, eliminated_player_ids: set = None) -> dict:
        """Pass annual laws based on government composition. Usually favors majority-favored players.
        Eliminated players are never favored. With LAW_OVERRIDE_CHANCE, laws may override favor for one turn only."""
        eliminated = eliminated_player_ids or set()
        # After an override year, next year we never override (one-turn effect only)
        if self._last_override_year is not None and year == self._last_override_year + 1:
            override = False
        else:
            override = random.random() < LAW_OVERRIDE_CHANCE
            if override:
                self._last_override_year = year
        tax_modifiers: dict[int, float] = {}
        lawsuit_protection: list[int] = []
        lawsuit_target: list[int] = []
        maintenance_relief: dict[int, float] = {}
        grants: dict[int, int] = {}

        base_grant = 2000 + (year - 1830) // 10 * 500  # Scales with time

        for pid in range(self._num_players):
            if pid in eliminated:
                is_favored = False
            else:
                favorable = self.get_favorable_influence(pid)
                unfavorable = self.get_unfavorable_influence(pid)
                tiebreaker = self.get_tiebreaker_favorable(pid)
                is_favored = favorable > unfavorable or (favorable == unfavorable and tiebreaker)
                if override:
                    is_favored = not is_favored

            if is_favored:
                tax_modifiers[pid] = 0.85
                lawsuit_protection.append(pid)
                maintenance_relief[pid] = 0.9
                grants[pid] = base_grant
            else:
                tax_modifiers[pid] = 1.15
                lawsuit_target.append(pid)
                maintenance_relief[pid] = 1.1

        return {
            'tax_modifiers': tax_modifiers,
            'lawsuit_protection': lawsuit_protection,
            'lawsuit_target': lawsuit_target,
            'maintenance_relief': maintenance_relief,
            'grants': grants,
        }

    def pass_random_government_laws(self, year: int, eliminated_player_ids: set = None) -> list[dict]:
        """Randomly pass some lobby-style laws, favoring favored players and harming unfavored.
        Eliminated players are never favored. Returns list of effect dicts for operations to apply."""
        eliminated = eliminated_player_ids or set()
        effects = []
        favored = []
        unfavored = []
        for pid in range(self._num_players):
            if pid in eliminated:
                unfavored.append(pid)
            else:
                favorable = self.get_favorable_influence(pid)
                unfavorable = self.get_unfavorable_influence(pid)
                tiebreaker = self.get_tiebreaker_favorable(pid)
                is_favored = favorable > unfavorable or (favorable == unfavorable and tiebreaker)
                if is_favored:
                    favored.append(pid)
                else:
                    unfavored.append(pid)

        from game_presets import (
            GOVERNMENT_RANDOM_LAW_EXTRA_FAVORABLE_CHANCE,
            GOVERNMENT_RANDOM_LAW_EXTRA_UNFAVORABLE_CHANCE,
            GOVERNMENT_RANDOM_LAW_FAVORABLE_CHANCE,
            GOVERNMENT_RANDOM_LAW_UNFAVORABLE_CHANCE,
        )

        if favored and random.random() < GOVERNMENT_RANDOM_LAW_FAVORABLE_CHANCE:
            effects.append({'type': 'reduce_property_tax_15', 'player_ids': [random.choice(favored)]})
        if favored and random.random() < GOVERNMENT_RANDOM_LAW_EXTRA_FAVORABLE_CHANCE:
            effects.append({
                'type': 'tax_break',
                'player_id': random.choice(favored),
                'year': year + 1,
            })
        if unfavored and random.random() < GOVERNMENT_RANDOM_LAW_UNFAVORABLE_CHANCE:
            effects.append({'type': 'increase_property_tax_15', 'player_ids': [random.choice(unfavored)]})
        if unfavored and random.random() < GOVERNMENT_RANDOM_LAW_EXTRA_UNFAVORABLE_CHANCE:
            effects.append({
                'type': 'audit_target',
                'player_ids': [random.choice(unfavored)],
                'year': year + 1,
            })

        if not effects and (favored or unfavored):
            if favored:
                effects.append({'type': 'reduce_property_tax_15', 'player_ids': [random.choice(favored)]})
            else:
                effects.append({'type': 'increase_property_tax_15', 'player_ids': [random.choice(unfavored)]})

        return effects

    def get_favor_ranking(self) -> list[tuple[int, float]]:
        """Return list of (player_id, favorable_influence) sorted by influence descending. Who gov favors most first."""
        ranking = []
        for pid in range(self._num_players):
            inf = self.get_favorable_influence(pid)
            ranking.append((pid, inf))
        ranking.sort(key=lambda x: -x[1])
        return ranking
