"""Operations mixin: YearGovMixin."""
from typing import Optional
from game import Game
from line import Line, Ferry, Bus as BusLine
from player import Player
from public_ta import PublicTA
from map import Map
from vehicle import Trainset, Locomotive, Railcar, Vehicle, Boat, Ferry, Bus
from timeline import Timeline
import math
import random
from marketplace import Marketplace, SaleItem
from game_presets import (
    AI_DIFFICULTIES,
    AI_FOCUSES,
    AI_MAX_STRAIGHT_LAND_SEGMENT_MILES,
    LINE_NIMBY_ELEVATION_CHANCE_MULTIPLIER,
    LINE_NIMBY_LAWSUIT_BASE_CHANCE,
    STARTING_MONEY,
    STATION_MAX_WATER_DISTANCE_TO_LAND_M,
)

# NIMBY line-build excuses (noise / property / environment).
_LINE_NIMBY_EXCUSE_CAUSES = frozenset({
    'Noise Pollution',
    'Property Damage',
    'Environmental Damage',
})

# AI difficulty bonuses: hard=strongest (cost 0.75x, marketplace 0.75x, lobby +0.15), easy=weakest
AI_DIFFICULTY_COST_MULTIPLIER = {'easy': 1.15, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_MARKETPLACE_MULTIPLIER = {'easy': 1.1, 'medium': 1.0, 'hard': 0.75}
AI_DIFFICULTY_LOBBY_BONUS = {'easy': -0.05, 'medium': 0.0, 'hard': 0.15}
# AI-built stops must be at least this straight-line distance from any existing or queued stop (meters).
AI_MIN_STOP_DIRECT_SEPARATION_METERS = 80.0



from route_validation import *


class YearGovMixin:
    def advance_year_complete(self, old_year: int):
        """Complete year advancement - handles all year-end operations and returns summary data"""
        new_year = old_year + 1
        gov = self.game.get_government()
        lobby_seat_notifications = []
        election_held = False
        if gov:
            # Apply pending seat lobbies (queued during the turn; take effect at end of turn)
            pending = getattr(self.game, '_pending_seat_lobbies', [])
            for action, seat_idx, player_id in pending:
                if action == 'tiebreaker':
                    prev_tb = gov._tiebreaker
                    gov.lobby_flip_tiebreaker(player_id)
                    if prev_tb is not None and prev_tb != player_id:
                        lobby_seat_notifications.append({
                            'type': 'tiebreaker',
                            'lobbyist_name': self.get_player_name(next((p for p in self.get_players() if self.get_player_id(p) == player_id), None)),
                            'previous_owner_id': prev_tb,
                            'success': True,
                        })
                elif action == 'seat':
                    if seat_idx >= 0:
                        prev_owner = gov._seats[seat_idx] if seat_idx < len(gov._seats) else None
                        gov.lobby_flip_seat_at_index(seat_idx, player_id)
                        if prev_owner is not None and prev_owner != player_id:
                            lobby_seat_notifications.append({
                                'type': 'seat',
                                'seat_index': seat_idx,
                                'lobbyist_name': self.get_player_name(next((p for p in self.get_players() if self.get_player_id(p) == player_id), None)),
                                'previous_owner_id': prev_owner,
                                'success': True,
                            })
                    else:
                        ok, idx, prev_owner = gov.lobby_flip_seat(player_id)
                        if ok and prev_owner is not None and prev_owner != player_id:
                            lobby_seat_notifications.append({
                                'type': 'seat',
                                'seat_index': idx,
                                'lobbyist_name': self.get_player_name(next((p for p in self.get_players() if self.get_player_id(p) == player_id), None)),
                                'previous_owner_id': prev_owner,
                                'success': True,
                            })
            self.game._pending_seat_lobbies = []
            retention = {}
            eliminated_ids = {self.get_player_id(p) for p in self.get_players() if self.is_player_eliminated(p)}
            for p in self.get_players():
                if not self.is_player_eliminated(p):
                    retention[self.get_player_id(p)] = self.get_player_government_retention_bonus(p, old_year)
            election_held = bool(
                gov.hold_election(
                    new_year,
                    player_retention_bonuses=retention,
                    eliminated_player_ids=eliminated_ids,
                )
            )
            self.game._government_laws = gov.pass_annual_laws(new_year, eliminated_player_ids=eliminated_ids)
            new_government_laws = []
            random_effects = gov.pass_random_government_laws(new_year, eliminated_player_ids=eliminated_ids)
            if random_effects:
                new_government_laws = random_effects
                self._apply_random_government_law_effects(new_year, random_effects)
            self.game._new_government_laws_this_year = new_government_laws
        else:
            self.game._government_laws = {}
            self.game._new_government_laws_this_year = []
            election_held = False
        war_events = self._process_war_events(new_year)
        strike_warnings_data = self.get_strike_warnings(old_year)
        population_event = self.event_system_check_and_create_population_event(old_year)
        event_population_change = 0.0
        if population_event:
            event_population_change = population_event.change_percent
        self.advance_turn(event_population_change)
        new_year = self.get_year()
        self.invalidate_ridership_cache()
        natural_disaster = self.event_system_check_and_create_natural_disaster(new_year)
        natural_disaster_info = None
        if natural_disaster:
            self.apply_natural_disaster_damage(natural_disaster, new_year)
            natural_disaster_info = self.format_natural_disaster_info(natural_disaster, new_year)
        
        # Check for new events
        active_events = self.event_system_get_active_events(new_year)
        newly_triggered_events = [e for e in active_events if e.year == new_year]
        newly_available = self.get_newly_available_vehicles(old_year, new_year)
        if newly_available:
            self.marketplace_add_trainsets_for_new_technology(new_year, newly_available)
        self.process_end_of_year(new_year)
        
        # Auto-assign unassigned construction workers to top jobs
        all_players = list(self.get_players())
        if self.public_ta is not None and self.public_ta.established:
            all_players.append(self.public_ta)
        for player in all_players:
            if not self.is_player_eliminated(player):
                self.auto_assign_unassigned_workers(player)
        # Complete construction from the year that just ended so profit/finances see the new lines and stations
        self.process_build_queue()
        game_map = self.game.get_map()
        has_stations = hasattr(game_map, 'stations') and game_map.stations and len(game_map.stations) > 0
        map_lines = self.get_map_lines()
        if has_stations:
            ridership_data = self.get_ridership_data_for_year(new_year)
            # Pre-calculate and cache profit for all lines now that ridership is available
            # This ensures get_line_stats and process_player_finances use cache only (no calculations)
            for _line_name, line in map_lines.items():
                profit = self.calculate_line_profit_with_ridership(line, new_year, ridership_data)
                line._cached_profit = profit
                line._cached_profit_year = new_year
        else:
            ridership_data = {}
            for _line_name, line in map_lines.items():
                line._cached_profit = 0
                line._cached_profit_year = new_year
        
        # Process finances for all players (now uses cached profit values only - fast)
        player_finances = []
        strike_messages = []
        for player in all_players:
            finances = self.process_player_finances(player, new_year)
            try:
                from ai import record_ai_settled_annual_net, record_player_money_at_year_end
                record_player_money_at_year_end(player, new_year, self.get_player_money(player))
                if getattr(player, 'is_ai', False):
                    record_ai_settled_annual_net(self, player, new_year, finances.get('net', 0))
            except Exception:
                pass
            player_finances.append({
                'player': player,
                'finances': finances
            })
            # Track strike messages (only for active players - no strikes for dead players)
            if not self.is_player_eliminated(player) and finances['is_on_strike']:
                from worker import ENGINEER
                engineer_rental = self.get_player_worker_rental(player, ENGINEER.name)
                if engineer_rental:
                    strike_messages.append({
                        'player_name': self.get_player_name(player),
                        'quantity': engineer_rental.quantity,
                        'worker_name': ENGINEER.name,
                        'turns_remaining': engineer_rental.strike_turns_remaining,
                        'demanded_multiplier': engineer_rental.demanded_multiplier
                    })

        # Before eliminating bankrupt players, allow doomed AIs a last-chance merger so two failing AIs
        # can combine instead of both being eliminated.
        if getattr(self.game, 'merge_enabled', False):
            self._process_ai_doomed_merges()

        # Now perform elimination and post-finance maintenance for all players (after potential AI-AI mergers)
        for player in all_players:
            if not self.is_player_eliminated(player):
                was_eliminated = self.check_and_eliminate_player(player)
                if was_eliminated:
                    # Player was eliminated this year
                    continue
                # Check engineer availability for surviving players
                self.check_and_unassign_trainsets_for_engineers(player, new_year)
        
        lawsuit_result = self.check_agency_lawsuits(new_year)
        
        self.handle_public_ta(new_year)
        government_funding = self.distribute_government_funding(new_year)
        self.game.apply_lobby_effects(new_year)
        self._complete_law_drafts(new_year)

        # Random AI spawn: add new AI every 50-100 years if enabled
        new_ai_spawned = None
        if getattr(self.game, 'random_ai_spawn_enabled', False):
            next_spawn = getattr(self.game, '_next_random_ai_spawn_year', None)
            if next_spawn is not None and new_year >= next_spawn:
                new_ai_spawned = self._try_spawn_random_ai(new_year)
                if new_ai_spawned:
                    interval = getattr(self.game, 'random_ai_spawn_interval_years', 30)
                    half = max(5, interval // 2)
                    self.game._next_random_ai_spawn_year = new_year + random.randint(interval - half, interval + half)

        # Merge: AIs merge with other AIs first (more than with humans); then remaining desperate AIs propose to humans
        merge_proposals = []
        ai_ai_merges = []
        if getattr(self.game, 'merge_enabled', False):
            # AI-AI merges first (repeat until no more pairs so AIs merge with AIs more than with humans)
            while True:
                performed = self._process_ai_ai_merges()
                if not performed:
                    break
                ai_ai_merges.extend(performed)
            # Remaining desperate AIs propose to human players
            merge_proposals = self.get_ai_merge_proposals()

        result = {
            'new_year': new_year,
            'new_ai_spawned': new_ai_spawned,
            'merge_proposals': merge_proposals,
            'ai_ai_merges': ai_ai_merges,
            'strike_warnings': strike_warnings_data,
            'population_event': population_event,
            'natural_disaster': natural_disaster,
            'natural_disaster_info': natural_disaster_info,
            'lawsuit_result': lawsuit_result,
            'newly_triggered_events': newly_triggered_events,
            'newly_available_vehicles': newly_available,
            'player_finances': player_finances,
            'strike_messages': strike_messages,
            'active_players': self.get_active_players(),
            'government_laws': self.game._government_laws,
            'new_government_laws': getattr(self.game, '_new_government_laws_this_year', []),
            'lobby_seat_notifications': lobby_seat_notifications,
            'election_held': election_held,
            'government_funding': government_funding,
            'war_events': war_events,
        }
        result['news_articles'] = self._generate_news_articles_for_year(new_year, result)
        return result

    def is_player_desperate(self, player: Player) -> bool:
        """True if player is in extremely desperate situation (eligible for merger).
        Desperate = money < 500 AND (no lines OR no profitable lines)."""
        if self.is_player_eliminated(player):
            return False
        money = self.get_player_money(player)
        if money >= 500:
            return False
        lines = player.get_line_names()
        if not lines:
            return True
        current_year = self.get_year()
        map_lines = self.get_map_lines()
        for line_name in lines:
            line = map_lines.get(line_name)
            if line:
                profit = self.calc_line_profit(line, current_year) if hasattr(self, 'calc_line_profit') else 0
                if profit > 0:
                    return False
        return True

    def try_ai_desperate_merge_no_lines(self, player: Player) -> Optional[dict]:
        """During an AI turn: if the AI is desperate and has no lines, attempt to merge into another AI.
        Uses the same stochastic reject chance as year-end AI–AI merges. Returns merge_players result on success,
        or {'success': False, 'rejected': True} when the merge is declined, or {'success': False, 'message': ...}."""
        if not getattr(player, 'is_ai', False) or self.is_player_eliminated(player):
            return {'success': False, 'message': 'Not an eligible AI'}
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_ai_enabled', True):
            return {'success': False, 'message': 'AI–AI merges disabled'}
        if not self.merge_ai_consideration_allowed_this_year():
            return {'success': False, 'message': 'AI mergers are not considered yet this early in the timeline.'}
        if player.get_line_names():
            return {'success': False, 'message': 'AI has lines'}
        if not self.is_player_desperate(player):
            return {'success': False, 'message': 'AI is not desperate'}
        others = [
            p for p in self.get_active_players()
            if getattr(p, 'is_ai', False) and p is not player and not self.is_player_eliminated(p)
        ]
        if not others:
            return {'success': False, 'message': 'No other AI to merge with'}
        import random as _r
        reject_chance = getattr(self.game, 'merge_ai_reject_chance_pct', None)
        if reject_chance is None:
            reject_chance = 90.0
        reject_chance = float(reject_chance)
        rng = _r.Random(
            (self.get_year() * 10007 + self.get_player_id(player) * 131 + len(others)) & 0xFFFFFFFF
        )
        if rng.random() < (reject_chance / 100.0):
            return {'success': False, 'rejected': True}
        acquirer = max(others, key=lambda p: (len(p.get_line_names()), self.get_player_money(p)))
        return self.merge_players(acquirer, player)

    def merge_players(self, acquirer: Player, target: Player) -> dict:
        """Merge target into acquirer. Acquirer gets all assets. Target is eliminated (or, if human, becomes co-controller via merged_into_player_id).
        Company name becomes a merge of both names (e.g. Pennsylvania Railroad + New York Central -> Penn Central).
        Valid when merge_enabled and the specific merge type is allowed (AI-AI, AI-human, human-human). Returns result dict."""
        if not getattr(self.game, 'merge_enabled', False):
            return {'success': False, 'message': 'Mergers are not enabled'}
        if self.is_player_eliminated(acquirer) or self.is_player_eliminated(target):
            return {'success': False, 'message': 'Cannot merge eliminated player'}
        acquirer_is_ai = getattr(acquirer, 'is_ai', False)
        target_is_ai = getattr(target, 'is_ai', False)
        if acquirer_is_ai and not target_is_ai:
            return {'success': False, 'message': 'Only human players can propose a merger (acquire another company)'}
        if acquirer is target:
            return {'success': False, 'message': 'Cannot merge with yourself'}
        if self.merge_ai_blocked_early_calendar_years(acquirer, target):
            return {'success': False, 'message': 'Mergers involving an AI are blocked during the first years of the timeline.'}
        # Enforce per-type merge settings
        if acquirer_is_ai and target_is_ai:
            if not getattr(self.game, 'merge_ai_ai_enabled', True):
                return {'success': False, 'message': 'Mergers between AIs are not allowed in this game'}
        elif not acquirer_is_ai and target_is_ai:
            if not getattr(self.game, 'merge_ai_human_enabled', True):
                return {'success': False, 'message': 'Mergers between humans and AIs are not allowed in this game'}
        elif not acquirer_is_ai and not target_is_ai:
            if not getattr(self.game, 'merge_human_human_enabled', True):
                return {'success': False, 'message': 'Mergers between humans are not allowed in this game'}
        current_year = self.get_year()
        map_lines = self.get_map_lines()
        for line_name in list(target.get_line_names()):
            line = map_lines.get(line_name)
            if line and line.get_owner() == target:
                line.owner = acquirer
                if line.name not in acquirer.get_line_names():
                    acquirer.acquire_line(line)
                target.remove_line(line_name)
        for ts in list(target.get_trainsets()):
            target.remove_trainset(ts)
            acquirer.aquire_trainset(ts)
        from worker import ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER
        for worker_type in (ENGINEER, CONSTRUCTION_WORKER, MAINTENANCE_WORKER, LAWYER):
            target_rental = target.get_worker_rental(worker_type.name)
            if target_rental and target_rental.quantity > 0:
                acquirer_rental = acquirer.get_worker_rental(worker_type.name)
                if acquirer_rental:
                    acquirer_rental.quantity += target_rental.quantity
                else:
                    acquirer.rent_workers(worker_type, target_rental.quantity, current_year)
        cash = int(target.get_money() or 0)
        if cash > 0:
            # Use Operations wrappers so AI money log records this transfer.
            self.add_player_money(acquirer, cash, reason=f"Merger: acquired cash from {target.get_name()}")
            self.deduct_player_money(target, cash, reason=f"Merger: cash transferred to {acquirer.get_name()}")
        merged_name = _merge_company_names(acquirer.get_name(), target.get_name())
        acquirer.rename(merged_name)
        target_eliminated_name = target.get_name()
        target_is_human = not target_is_ai
        if target_is_human:
            # Human merged into human: target keeps their seat and controls the same company (shared tab).
            target.merged_into_player_id = acquirer.get_player_id()
            # Do not eliminate; target still has a tab and both can control the merged company.
        else:
            target.eliminate()
            target.line_names.clear()
            target.trainsets.clear()
        return {'success': True, 'acquirer': acquirer, 'target': target, 'target_name': target_eliminated_name}

    def _process_ai_doomed_merges(self) -> list:
        """Before elimination, merge pairs of AIs that are effectively bankrupt.
        Any two AI players with money < 0 this year have a high (90%) chance to merge instead of both dying.
        Returns list of (acquirer, target) pairs that were merged."""
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_ai_enabled', True):
            return []
        if not self.merge_ai_consideration_allowed_this_year():
            return []
        import random as _r
        ais = [p for p in self.get_active_players() if getattr(p, 'is_ai', False)]
        # "Doomed" = negative cash after finances (same threshold as bankruptcy)
        doomed = [a for a in ais if self.get_player_money(a) < 0 and not self.is_player_eliminated(a)]
        if len(doomed) < 2:
            return []
        merged = []
        used = set()
        # Deterministic-ish per-year seed so behavior is stable across reruns of the same game state
        rng = _r.Random(self.get_year() * 10007 + len(doomed) * 17)
        for target in doomed:
            if target in used or self.is_player_eliminated(target):
                continue
            others = [a for a in doomed if a is not target and a not in used and not self.is_player_eliminated(a)]
            if not others:
                continue
            # merge_ai_reject_chance_pct chance to reject (e.g. 90% reject = 10% accept)
            reject_chance = getattr(self.game, 'merge_ai_reject_chance_pct', None)
            if reject_chance is None:
                reject_chance = 90.0
            reject_chance = float(reject_chance)
            if rng.random() < (reject_chance / 100.0):
                continue
            # Choose acquirer: the stronger of the pair (more lines, then more money)
            acquirer = max(others, key=lambda p: (len(p.get_line_names()), self.get_player_money(p)))
            result = self.merge_players(acquirer, target)
            if result.get('success'):
                merged.append((acquirer, target))
                used.add(target)
        return merged

    def _process_ai_ai_merges(self) -> list:
        """Process AI-AI merges: desperate AIs merge with each other or with struggling AIs (negative money).
        At least one of the pair must be desperate so two AIs in bad shape are inclined to merge.
        Returns list of (acquirer, target) for each merge performed."""
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_ai_enabled', True):
            return []
        if not self.merge_ai_consideration_allowed_this_year():
            return []
        ais = [p for p in self.get_active_players() if getattr(p, 'is_ai', False)]
        desperate = [a for a in ais if self.is_player_desperate(a)]
        # Struggling = negative money (or very low) - can merge with a desperate AI
        struggling = [a for a in ais if self.get_player_money(a) < 0 or self.get_player_money(a) < 500]
        # Merge targets: desperate or struggling; at least one in each pair must be desperate
        eligible = [a for a in ais if a in desperate or a in struggling]
        if len(eligible) < 2:
            return []
        performed = []
        merged_this_round = set()
        for target in eligible:
            if target in merged_this_round:
                continue
            others = [a for a in eligible if a != target and a not in merged_this_round]
            if not others:
                continue
            # At least one of (target, acquirer) must be desperate
            if not self.is_player_desperate(target) and not any(self.is_player_desperate(a) for a in others):
                continue
            acquirer = max(others, key=lambda p: (
                len(p.get_line_names()),
                self.get_player_money(p)
            ))
            result = self.merge_players(acquirer, target)
            if result.get('success'):
                performed.append((acquirer, target))
                merged_this_round.add(target)
        return performed

    def get_ai_merge_proposals(self) -> list:
        """Get list of (ai_player, human_player) where desperate AI wants to merge with human.
        Only includes AIs that did not already merge with another AI this round.
        Returns empty if merge disabled or AI-human mergers not allowed or no eligible pairs."""
        if not getattr(self.game, 'merge_enabled', False) or not getattr(self.game, 'merge_ai_human_enabled', True):
            return []
        if not self.merge_ai_consideration_allowed_this_year():
            return []
        humans = [p for p in self.get_active_players() if not getattr(p, 'is_ai', False)]
        ais = [p for p in self.get_active_players() if getattr(p, 'is_ai', False)]
        if not humans or not ais:
            return []
        proposals = []
        for ai in ais:
            if self.is_player_desperate(ai):
                for human in humans:
                    proposals.append((ai, human))
        return proposals

    def get_desperate_merge_targets(self, acquirer: Player) -> list:
        """Players that acquirer can propose a merge to (other desperate players).
        Used by human players: returns all other active desperate players (AI or human) per game merge settings.
        Excludes players who have merged into someone else (they are not a separate company)."""
        if not getattr(self.game, 'merge_enabled', False) or not acquirer:
            return []
        if self.is_player_eliminated(acquirer):
            return []
        merge_ai_human = getattr(self.game, 'merge_ai_human_enabled', True)
        merge_human_human = getattr(self.game, 'merge_human_human_enabled', True)
        others = []
        for p in self.get_players():
            if p is acquirer or self.is_player_eliminated(p):
                continue
            if getattr(p, 'merged_into_player_id', None) is not None:
                continue
            if not self.is_player_desperate(p):
                continue
            if getattr(p, 'is_ai', False):
                if merge_ai_human:
                    others.append(p)
            else:
                if merge_human_human:
                    others.append(p)
        return others

    def get_merge_targets(self, acquirer: Player) -> list:
        """Players that acquirer can propose a merge to (any other mergeable player).
        Used for 'propose merger at any time' in the Law tab. Same filters as get_desperate_merge_targets but does not require targets to be desperate."""
        if not getattr(self.game, 'merge_enabled', False) or not acquirer:
            return []
        if self.is_player_eliminated(acquirer):
            return []
        merge_ai_human = getattr(self.game, 'merge_ai_human_enabled', True)
        merge_human_human = getattr(self.game, 'merge_human_human_enabled', True)
        others = []
        for p in self.get_players():
            if p is acquirer or self.is_player_eliminated(p):
                continue
            if getattr(p, 'merged_into_player_id', None) is not None:
                continue
            if getattr(p, 'is_ai', False):
                if merge_ai_human:
                    others.append(p)
            else:
                if merge_human_human:
                    others.append(p)
        return others

    def ai_would_accept_merge(self, ai_player: Player, acquirer: Player = None) -> bool:
        """True if AI would accept a merge proposal.
        If AI is in good financial situation (not losing more than merge_ai_healthy_profit_threshold_pct of its profit), auto-reject.
        Otherwise reject with merge_ai_reject_chance_pct probability (e.g. 90% reject)."""
        n_early = getattr(self.game, 'merge_ai_auto_fail_first_n_years', None)
        if n_early is None:
            from game_presets import AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
            n_early = AI_MERGE_AUTO_FAIL_FIRST_N_YEARS
        if self._calendar_years_since_timeline_start() < int(n_early):
            return False
        try:
            summary = self.get_player_financial_summary(ai_player, self.get_year())
        except Exception:
            summary = {}
        profit = summary.get('profit', 0) or 0
        net = summary.get('net', 0) or 0
        threshold_pct = getattr(self.game, 'merge_ai_healthy_profit_threshold_pct', 25.0) or 25.0
        # Good situation: positive profit and not losing more than threshold% of it (net >= profit * (1 - threshold/100))
        if profit > 0:
            min_net = profit * (1.0 - threshold_pct / 100.0)
            if net >= min_net:
                return False  # Auto-reject when healthy
        # Survival gate: if AI can survive longer than configured turns at current net loss, reject merger.
        # Only evaluate when net is negative (losing money now); non-losing cases already reject above.
        if net < 0:
            turns_cfg = getattr(self.game, 'merge_ai_min_turns_to_live_for_accept', 10) or 10
            turns_cfg = max(0, int(turns_cfg))
            if turns_cfg > 0:
                try:
                    turns_to_live = float(self.get_player_money(ai_player)) / float(-net)
                except Exception:
                    turns_to_live = 0.0
                if turns_to_live > turns_cfg:
                    return False
        if not self.is_player_desperate(ai_player):
            return False
        if acquirer is None:
            return True
        combined = self.get_player_money(acquirer) + self.get_player_money(ai_player)
        if combined <= 0:
            return False
        reject_chance = getattr(self.game, 'merge_ai_reject_chance_pct', None)
        if reject_chance is None:
            reject_chance = 90.0
        reject_chance = float(reject_chance)
        import random as _r
        rng = _r.Random(self.get_year() * 10007 + self.get_player_id(ai_player) * 31)
        return rng.random() >= (reject_chance / 100.0)

    def _try_spawn_random_ai(self, current_year: int):
        """Spawn a new AI player if random AI spawn is enabled and it's time. Returns new player or None."""
        from game_presets import STARTING_MONEY
        from transit_company_names import get_transit_company_name
        existing_names = {p.get_name() for p in self.get_players()}
        map_preset = getattr(self.get_map(), 'map_preset_name', None)
        name = get_transit_company_name(existing_names, map_preset_name=map_preset)
        player_id = max((p.get_player_id() for p in self.get_players()), default=-1) + 1
        # Late-joining AIs get 50% of starting money
        starting_money = max(5000, STARTING_MONEY // 2)
        default_diff = getattr(self.game, 'default_ai_difficulty_for_spawns', 'hard')
        new_player = create_single_ai_player(player_id, name, starting_money, ai_difficulty=default_diff)
        from player_colors import next_available_color_index, assign_player_color_index
        assign_player_color_index(new_player, next_available_color_index(self.get_players()))
        # ai.run_ai_turn treats join year like a first-line opener (must queue/build a line on first turn).
        try:
            new_player._ai_join_game_year = int(current_year)
        except Exception:
            new_player._ai_join_game_year = current_year
        self.game.add_player(new_player)
        return {'player': new_player, 'name': name, 'money': starting_money}

    def _apply_random_government_law_effects(self, year: int, effects: list[dict]) -> None:
        """Apply random government-passed laws (benefits favored, harms unfavored)."""
        players_by_id = {self.get_player_id(p): p for p in self.get_players()}
        for eff in effects:
            t = eff.get('type')
            if t == 'reduce_property_tax_5' or t == 'reduce_property_tax_15':
                reduction = getattr(self.game, '_permanent_property_tax_reduction', set()) or set()
                if not isinstance(reduction, set):
                    reduction = set(reduction) if reduction else set()
                for pid in eff.get('player_ids', []):
                    reduction.add(pid)
                self.game._permanent_property_tax_reduction = reduction
            elif t == 'increase_property_tax_5' or t == 'increase_property_tax_15':
                increase = getattr(self.game, '_permanent_property_tax_increase', set()) or set()
                if not isinstance(increase, set):
                    increase = set(increase) if increase else set()
                for pid in eff.get('player_ids', []):
                    increase.add(pid)
                self.game._permanent_property_tax_increase = increase
            elif t == 'audit_target':
                targets = [players_by_id[pid] for pid in eff.get('player_ids', []) if pid in players_by_id]
                if targets:
                    self.game._lobby_audit_targets = targets
                    self.game._lobby_audit_year = eff.get('year', year + 1)
            elif t == 'tax_break':
                pid = eff.get('player_id')
                if pid in players_by_id:
                    self.game._lobby_tax_break_player = players_by_id[pid]
                    self.game._lobby_tax_break_year = eff.get('year', year + 1)

    # Player additional getters
    def get_player_engineer_count(self, player: Player) -> int:
        return player.get_engineer_count()
    
    def get_player_construction_worker_count(self, player: Player) -> int:
        """Get the number of construction workers a player has.
        Returns 0 if construction workers are on strike."""
        from worker import CONSTRUCTION_WORKER
        worker_rental = player.get_worker_rental(CONSTRUCTION_WORKER.name) if hasattr(player, 'get_worker_rental') else None
        if worker_rental and hasattr(worker_rental, 'is_on_strike') and worker_rental.is_on_strike():
            return 0
        return player.get_construction_worker_count()
    
    def get_player_assigned_trainsets_count(self, player: Player) -> int:
        """Get the number of trainsets assigned to lines for a player"""
        return player.get_assigned_trainsets_count()
    
    def get_player_lawyer_count(self, player: Player) -> int:
        """Get the number of lawyers a player has"""
        return player.get_lawyer_count()

    def get_player_available_lawyer_count(self, player: Player) -> int:
        """Lawyers available for lawsuits (total minus those drafting laws)."""
        return self.game._get_player_available_lawyer_count(player)
    
    def get_player_lawsuit_excuses(self, player: Player) -> list:
        """Get list of lawsuit excuses for a player"""
        return player.get_lawsuit_excuses()

    def get_lobby_inflation_factor(self) -> float:
        """Inflation factor for lobby success chance (1.0 in 1830, up to 10.0 by 2050)."""
        from inflation_utils import get_inflation_factor
        return get_inflation_factor(self.get_year(), max_factor=10.0)

    def get_lobby_minimum_cost(self) -> int:
        """Return minimum lobby cost (scales with inflation)."""
        return max(50, int(50 * self.get_lobby_inflation_factor()))

    def get_lobby_power_factor(self, option_id: str) -> float:
        """Power factor for lobby option. <1 = less powerful (cheaper), >1 = more powerful (costlier)."""
        factors = {
            'influence_seat': 0.6,       # One seat - weaker
            'influence_tiebreaker': 1.4,  # Leader ~3 seats - stronger
            'drop_excuse': 0.6,
            'tax_break': 0.8,
            'one_time_grant': 0.7,
            'pass_law': 0.9,
            'area_protection': 0.9,
            'gov_attack': 1.2,
            'add_lawsuit_excuse': 1.1,
            'audit_target': 1.2,
            'block_funding': 1.0,
            'receive_funding': 1.0,
            'redirect_funding': 1.0,
            'reduce_property_tax_5': 1.5,  # Permanent - most powerful
            'increase_property_tax_5': 1.5,  # Permanent - most powerful
        }
        return factors.get(option_id, 1.0)

    def get_lobby_success_chance(self, amount: int, option_id: str = None) -> float:
        """Return the probability (0.0–0.95) that lobbying succeeds for a given payment.
        Less powerful options (lower power factor) require less money for the same success chance."""
        if amount <= 0:
            return 0.10
        power = self.get_lobby_power_factor(option_id or 'drop_excuse')
        ref = 10000.0 * self.get_lobby_inflation_factor() * power
        return min(0.95, 0.10 + 0.85 * min(1.0, amount / ref))

    def get_government_lobby_options(self, player: Player) -> list:
        """Lobby options for government tab only: influence seats and tiebreaker."""
        return [
            {'id': 'influence_seat', 'label': 'Influence a seat: flip one unfavorable seat to favorable', 'requires_target': False},
            {'id': 'influence_tiebreaker', 'label': 'Influence tiebreaker: make leader favorable to you', 'requires_target': False},
        ]

    def get_law_draft_options(self, player: Player) -> list:
        """Law types that can be drafted (non-seat lobbies). Requires target for some."""
        year = self.get_year()
        options = [
            {'id': 'drop_excuse', 'label': 'Drop lawsuit excuse', 'requires_target': False},
            {'id': 'tax_break', 'label': 'Tax break (half taxes, 1 year)', 'requires_target': False},
            {'id': 'reduce_property_tax_5', 'label': 'Reduce property tax by 15% (permanent)', 'requires_target': False, 'allows_multi_target': True},
            {'id': 'increase_property_tax_5', 'label': 'Increase property tax by 15% on target(s) (permanent)', 'requires_target': True, 'allows_multi_target': True},
            {'id': 'one_time_grant', 'label': 'One-time government grant', 'requires_target': False},
            {'id': 'pass_law', 'label': 'Delay agency lawsuit checks (2 years)', 'requires_target': False},
            {'id': 'area_protection', 'label': 'Area protection: delay lawsuit checks (2 years)', 'requires_target': False},
            {'id': 'pacifism', 'label': 'Pacifism: reduce chance of war starting (temporary)', 'requires_target': False},
        ]
        others = [p for p in self.get_active_players() if p != player]
        if others:
            options.append({'id': 'gov_attack', 'label': 'Government penalty on target(s)', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'add_lawsuit_excuse', 'label': 'Add lawsuit excuse to target(s)', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'audit_target', 'label': 'Force audit: target(s) pay double taxes', 'requires_target': True, 'allows_multi_target': True})
        if year >= 1970:
            options.append({'id': 'receive_funding', 'label': 'Be chosen for government funding', 'requires_target': False})
            if others:
                options.append({'id': 'redirect_funding', 'label': 'Redirect funding from another player', 'requires_target': True})
                options.append({'id': 'block_funding', 'label': 'Block target(s) from government funding', 'requires_target': True, 'allows_multi_target': True})
        return options

    def get_lobby_options(self, player: Player) -> list:
        """Return list of lobby options: dict with id, label, requires_target (bool)."""
        year = self.get_year()
        options = [
            {'id': 'influence_seat', 'label': 'Influence a seat: flip one unfavorable seat to favorable', 'requires_target': False},
            {'id': 'influence_tiebreaker', 'label': 'Influence tiebreaker: make leader favorable to you', 'requires_target': False},
            {'id': 'drop_excuse', 'label': 'Drop a lawsuit excuse (government drops one case against you)', 'requires_target': False},
            {'id': 'tax_break', 'label': 'Tax break next year (half taxes for one year)', 'requires_target': False},
            {'id': 'reduce_property_tax_5', 'label': 'Reduce property tax by 15% (permanent)', 'requires_target': False},
            {'id': 'increase_property_tax_5', 'label': 'Increase property tax by 15% on target(s) (permanent)', 'requires_target': True},
            {'id': 'one_time_grant', 'label': 'One-time government grant', 'requires_target': False},
            {'id': 'pass_law', 'label': 'Delay agency lawsuit checks against you (2 years)', 'requires_target': False},
            {'id': 'area_protection', 'label': 'Area protection: delay lawsuit checks (2 years)', 'requires_target': False},
        ]
        others = [p for p in self.get_active_players() if p != player]
        # Attack options (require another player to target)
        if others:
            options.append({'id': 'gov_attack', 'label': 'Government penalty: target(s) pay a fine next year', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'add_lawsuit_excuse', 'label': 'Trigger audit: add lawsuit excuse to target(s)', 'requires_target': True, 'allows_multi_target': True})
            options.append({'id': 'audit_target', 'label': 'Force audit: target(s) pay double taxes next year', 'requires_target': True, 'allows_multi_target': True})
        if year >= 1970:
            options.append({'id': 'receive_funding', 'label': 'Be chosen for government funding this year', 'requires_target': False})
            if others:
                options.append({'id': 'redirect_funding', 'label': 'Redirect government funding from another player to you', 'requires_target': True})
                options.append({'id': 'block_funding', 'label': 'Block target(s) from government funding next year', 'requires_target': True, 'allows_multi_target': True})
        return options

    def try_lobby(self, player: Player, amount: int, option_id: str = 'drop_excuse', target_player: Player = None, target_players: list = None, seat_index: int = None) -> dict:
        """Pay money to lobby for an option. Returns dict with success, message, and option-specific keys.
        target_players: for options with allows_multi_target, list of targets."""
        min_cost = self.get_lobby_minimum_cost()
        if amount < min_cost:
            return {'success': False, 'message': f'Lobby cost must be positive and at least ${min_cost} (scales with inflation).'}
        money = self.get_player_money(player)
        if money < amount:
            return {'success': False, 'message': f'You need ${amount} but only have ${money}.'}
        opts = {o['id']: o for o in self.get_lobby_options(player)}
        if option_id not in opts:
            return {'success': False, 'message': f'Unknown lobby option: {option_id}.'}
        targets = list(target_players) if target_players else ([target_player] if target_player else [])
        if opts[option_id]['requires_target'] and not targets:
            return {'success': False, 'message': 'Please select at least one player to target.'}
        if opts[option_id]['requires_target'] and player in targets:
            return {'success': False, 'message': 'Cannot target yourself.'}

        gov = self.game.get_government()
        # Players cannot lobby for influence (seats/tiebreaker) until they have at least one seat allegiant to them
        if option_id in ('influence_seat', 'influence_tiebreaker') and gov:
            player_id = self.get_player_id(player)
            if gov.get_favorable_influence(player_id) <= 0:
                return {'success': False, 'message': 'You need at least one seat allegiant to you before you can lobby for more influence. Win an election first.'}

        chance = self.get_lobby_success_chance_for_player(player, amount, option_id)
        self.deduct_player_money(player, amount, reason='Lobby')
        if option_id == 'influence_seat' and gov:
            player_id = self.get_player_id(player)
            if seat_index is not None:
                if gov._seats[seat_index] == player_id:
                    return {'success': True, 'message': 'That seat was already favorable to you.'}
                pending = getattr(self.game, '_pending_seat_lobbies', [])
                pending.append(('seat', seat_index, player_id))
                self.game._pending_seat_lobbies = pending
                return {'success': True, 'message': 'Lobby submitted. Takes effect at end of turn; results revealed at next election.'}
            # Random seat - check if any unfavorable
            unfavorable = [i for i, s in enumerate(gov._seats) if s != player_id]
            if not unfavorable:
                return {'success': True, 'message': 'All seats already favorable to you.'}
            pending = getattr(self.game, '_pending_seat_lobbies', [])
            pending.append(('seat', -1, player_id))
            self.game._pending_seat_lobbies = pending
            return {'success': True, 'message': 'Lobby submitted. Takes effect at end of turn; results revealed at next election.'}

        if option_id == 'influence_tiebreaker' and gov:
            player_id = self.get_player_id(player)
            if gov._tiebreaker == player_id:
                return {'success': True, 'message': 'The tiebreaker was already favorable to you.'}
            pending = getattr(self.game, '_pending_seat_lobbies', [])
            pending.append(('tiebreaker', None, player_id))
            self.game._pending_seat_lobbies = pending
            return {'success': True, 'message': 'Lobby submitted. Takes effect at end of turn; results revealed at next election.'}

        if random.random() >= chance:
            return {'success': False, 'message': f'Lobbying failed. Your ${amount} did not sway the government.'}

        if option_id == 'drop_excuse':
            excuses = self.get_player_lawsuit_excuses(player)
            if excuses:
                excuse_removed = random.choice(excuses)
                self.remove_lawsuit_excuse_from_player(player, excuse_removed)
                return {'success': True, 'message': f'Lobbying succeeded! The government dropped one case: "{excuse_removed.cause}".', 'excuse_removed': excuse_removed}
            return {'success': True, 'message': 'Lobbying succeeded! The government is favorable (no pending cases to drop).'}

        if option_id == 'tax_break':
            self.game._lobby_tax_break_player = player
            self.game._lobby_tax_break_year = self.get_year() + 1
            return {'success': True, 'message': 'Lobbying succeeded! You will receive a tax break next year (half taxes).'}

        if option_id == 'reduce_property_tax_5':
            reduction = getattr(self.game, '_permanent_property_tax_reduction', set())
            if not isinstance(reduction, set):
                reduction = set(reduction) if reduction else set()
            reduction.add(self.get_player_id(player))
            self.game._permanent_property_tax_reduction = reduction
            return {'success': True, 'message': 'Lobbying succeeded! Your property tax is permanently reduced by 5%.'}

        if option_id == 'one_time_grant':
            grant = self.get_government_funding_amount() // 2
            self.add_player_money(player, grant, reason='Lobby grant')
            return {'success': True, 'message': f'Lobbying succeeded! You received a one-time grant of ${grant}.'}

        if option_id == 'pass_law':
            duration = 2
            self.game._lobby_pass_law_player = player
            self.game._lobby_pass_law_until_year = self.get_year() + duration
            return {'success': True, 'message': f'Lobbying succeeded! Agency lawsuit checks against you are delayed for {duration} years.'}

        if option_id == 'area_protection':
            duration = 2
            if getattr(self.game, '_lobby_pass_law_until_year', None) is not None and getattr(self.game, '_lobby_pass_law_player', None) == player:
                self.game._lobby_pass_law_until_year = max(self.game._lobby_pass_law_until_year, self.get_year() + duration)
            else:
                self.game._lobby_pass_law_player = player
                self.game._lobby_pass_law_until_year = self.get_year() + duration
            return {'success': True, 'message': f'Lobbying succeeded! Area protections delay lawsuit checks against you for {duration} years.'}

        if option_id == 'gov_attack':
            penalty = self.get_government_funding_amount()
            self.game._lobby_gov_attack_targets = targets
            self.game._lobby_gov_attack_penalty = penalty
            self.game._lobby_gov_attack_year = self.get_year() + 1
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! The government will impose a ${penalty} penalty on {names} next year.'}

        if option_id == 'add_lawsuit_excuse':
            from lawsuit import LAWSUIT_EXCUSES
            excuse = random.choice(LAWSUIT_EXCUSES)
            for t in targets:
                t.add_lawsuit_excuse(excuse)
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! {names} now have an additional lawsuit excuse: "{excuse.cause}".'}

        if option_id == 'audit_target':
            self.game._lobby_audit_targets = targets
            self.game._lobby_audit_year = self.get_year() + 1
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! {names} will pay double taxes next year.'}

        if option_id == 'block_funding':
            self.game._lobby_block_funding_targets = targets
            self.game._lobby_block_funding_year = self.get_year() + 1
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! {names} will be blocked from government funding next year.'}

        if option_id == 'increase_property_tax_5':
            increase = getattr(self.game, '_permanent_property_tax_increase', set())
            if not isinstance(increase, set):
                increase = set(increase) if increase else set()
            for t in targets:
                increase.add(self.get_player_id(t))
            self.game._permanent_property_tax_increase = increase
            names = ', '.join(self.get_player_name(t) for t in targets)
            return {'success': True, 'message': f'Lobbying succeeded! Property tax permanently increased by 5% for {names}.'}

        if option_id == 'receive_funding':
            self._government_funding_recipient = player
            amt = self.get_government_funding_amount()
            return {'success': True, 'message': f'Lobbying succeeded! You will receive this year\'s government funding (${amt}).'}

        if option_id == 'redirect_funding':
            if not targets:
                return {'success': False, 'message': 'Please select which player to redirect from.'}
            self._government_funding_recipient = player
            return {'success': True, 'message': f'Lobbying succeeded! Government funding will go to you instead of {self.get_player_name(targets[0])}.'}

        return {'success': False, 'message': 'Unknown option.'}

    def _complete_law_drafts(self, new_year: int) -> None:
        """Move drafts that finished this year to ready queue."""
        drafts = getattr(self.game, '_law_drafts', [])
        ready = getattr(self.game, '_law_drafts_ready', [])
        remaining = []
        for item in drafts:
            pid, law_type, year_started = item[0], item[1], item[2]
            num_lawyers = item[3] if len(item) > 3 else 1
            target_players = item[4] if len(item) > 4 else None
            if year_started + 1 == new_year:
                ready.append((pid, law_type, None, num_lawyers, target_players))
            else:
                remaining.append((pid, law_type, year_started, num_lawyers, target_players))
        self.game._law_drafts = remaining
        self.game._law_drafts_ready = ready

    def _get_beneficiaries_from_targets(self, law_type: str, target_players: list, drafter: Player) -> list:
        """Given law type and targets selected at draft time, return who the law applies to.
        For good laws (e.g. reduce_property_tax_5): they benefit. For bad laws (gov_attack, etc.): they are harmed.
        Seats allegiant to beneficiaries help pass good laws."""
        if not target_players:
            return [drafter] if law_type in ('reduce_property_tax_5', 'tax_break', 'pacifism') else []
        opts = {o['id']: o for o in self.get_law_draft_options(drafter)}
        if opts.get(law_type, {}).get('allows_multi_target', False):
            return [p for p in target_players if p is not None]
        return [target_players[0]] if target_players else []

    def start_law_draft(self, player: Player, law_type: str, num_lawyers: int = 1, target_players: list = None) -> dict:
        """Assign lawyers to draft a law. Takes 1 year. Targets are selected at draft time; game calculates who benefits; seats allegiant to beneficiaries help pass."""
        pid = self.get_player_id(player)
        total = player.get_lawyer_count()
        drafts = getattr(self.game, '_law_drafts', [])
        drafting = sum((d[3] if len(d) > 3 else 1) for d in drafts if d[0] == pid)
        if drafting + num_lawyers > total:
            return {'success': False, 'message': f'Not enough lawyers. You have {total}, {drafting} are drafting. Need {num_lawyers} more.'}
        opts = {o['id']: o for o in self.get_law_draft_options(player)}
        if law_type not in opts:
            return {'success': False, 'message': f'Unknown law type: {law_type}.'}
        self.game._law_drafts.append((pid, law_type, self.get_year(), num_lawyers, target_players))
        return {'success': True, 'message': f'{num_lawyers} lawyer(s) started drafting {opts[law_type]["label"]}. Will be ready next year.'}

    def get_player_drafts(self, player: Player) -> tuple:
        """Return (in_progress, ready) drafts. Each is (law_type, year_started or None, num_lawyers, target_players)."""
        pid = self.get_player_id(player)
        drafts = getattr(self.game, '_law_drafts', [])
        ready = getattr(self.game, '_law_drafts_ready', [])
        in_progress = [(d[1], d[2], d[3] if len(d) > 3 else 1, d[4] if len(d) > 4 else None) for d in drafts if d[0] == pid]
        ready_list = [(d[1], None, d[3] if len(d) > 3 else 1, d[4] if len(d) > 4 else None) for d in ready if d[0] == pid]
        return (in_progress, ready_list)

    def submit_law_draft(self, player: Player, law_type: str, target_player: Player = None, target_players: list = None) -> dict:
        """Submit a ready draft to government committee. Targets were selected at draft time; game calculates beneficiaries; seats allegiant to beneficiaries help pass."""
        pid = self.get_player_id(player)
        ready = getattr(self.game, '_law_drafts_ready', [])
        key = (pid, law_type)
        idx = next((i for i, d in enumerate(ready) if d[0] == pid and d[1] == law_type), None)
        if idx is None:
            return {'success': False, 'message': 'No ready draft of that type. Draft laws in the Law tab.'}
        item = ready.pop(idx)
        num_lawyers = item[3] if len(item) > 3 else 1
        draft_targets = item[4] if len(item) > 4 else None
        self.game._law_drafts_ready = ready

        attempts = getattr(self.game, '_law_attempts', {})
        last_year = attempts.get(key, -999)
        year = self.get_year()
        if year - last_year <= 3:
            return {'success': False, 'message': f'Similar law attempted recently (year {last_year}). Committee rejected it.'}
        attempts[key] = year
        self.game._law_attempts = attempts

        gov = self.game.get_government()
        if not gov:
            return {'success': False, 'message': 'No government.'}

        # Use targets from draft. For good laws (reduce_property_tax_5): seats allegiant to beneficiaries help.
        # For bad laws (gov_attack, audit_target, etc.): drafter's seats help (they benefit from harming targets).
        beneficiaries = self._get_beneficiaries_from_targets(law_type, draft_targets, player)
        bad_laws = {'gov_attack', 'add_lawsuit_excuse', 'audit_target', 'block_funding', 'increase_property_tax_5'}
        if law_type in bad_laws:
            favorable = gov.get_favorable_influence(pid)
            unfavorable = gov.get_unfavorable_influence(pid)
            tiebreaker = gov.get_tiebreaker_favorable(pid)
        elif beneficiaries:
            beneficiary_ids = {self.get_player_id(p) for p in beneficiaries if p is not None}
            favorable = gov.get_favorable_influence_for_players(beneficiary_ids)
            total_inf = gov.get_total_influence()
            unfavorable = total_inf - favorable
            tiebreaker = gov._tiebreaker in beneficiary_ids
        else:
            favorable = gov.get_favorable_influence(pid)
            unfavorable = gov.get_unfavorable_influence(pid)
            tiebreaker = gov.get_tiebreaker_favorable(pid)

        base = 0.30
        per_fav = 0.02
        per_unfav = -0.02
        chance = base + favorable * per_fav + unfavorable * per_unfav
        if favorable == unfavorable and tiebreaker:
            chance += 0.15
        # More lawyers on the draft = higher success chance (+5% per lawyer, max +25%)
        chance += min(0.25, (num_lawyers - 1) * 0.05)
        chance = max(0.05, min(0.95, chance))

        if random.random() >= chance:
            return {'success': False, 'message': f'Committee rejected the law. (Pass chance was {chance*100:.0f}%.)'}

        return self._apply_law_effect(player, law_type, target_player, beneficiaries)

    def _apply_law_effect(self, player: Player, law_type: str, target_player: Player = None, beneficiaries: list = None) -> dict:
        """Apply a passed law's effect. Same logic as try_lobby for non-seat options.
        beneficiaries: players who benefit (from targets selected at draft time)."""
        year = self.get_year()
        if law_type == 'drop_excuse':
            excuses = self.get_player_lawsuit_excuses(player)
            if excuses:
                excuse_removed = random.choice(excuses)
                self.remove_lawsuit_excuse_from_player(player, excuse_removed)
                return {'success': True, 'message': f'Law passed! Government dropped case: "{excuse_removed.cause}".'}
            return {'success': True, 'message': 'Law passed! No pending cases to drop.'}
        if law_type == 'tax_break':
            self.game._lobby_tax_break_player = player
            self.game._lobby_tax_break_year = year + 1
            return {'success': True, 'message': 'Law passed! Tax break next year (half taxes).'}
        if law_type == 'reduce_property_tax_5':
            reduction = getattr(self.game, '_permanent_property_tax_reduction', set())
            if not isinstance(reduction, set):
                reduction = set(reduction) if reduction else set()
            for p in (beneficiaries or [player]):
                if p is not None:
                    reduction.add(self.get_player_id(p))
            self.game._permanent_property_tax_reduction = reduction
            names = ', '.join(self.get_player_name(p) for p in beneficiaries if p)
            return {'success': True, 'message': f'Law passed! Property tax permanently reduced by 5% for: {names}.'}
        if law_type == 'one_time_grant':
            grant = self.get_government_funding_amount() // 2
            self.add_player_money(player, grant, reason='Law grant')
            return {'success': True, 'message': f'Law passed! One-time grant of ${grant}.'}
        if law_type == 'pass_law':
            self.game._lobby_pass_law_player = player
            self.game._lobby_pass_law_until_year = year + 2
            return {'success': True, 'message': 'Law passed! Agency lawsuit checks delayed 2 years.'}
        if law_type == 'area_protection':
            if getattr(self.game, '_lobby_pass_law_until_year', None) is not None and getattr(self.game, '_lobby_pass_law_player', None) == player:
                self.game._lobby_pass_law_until_year = max(self.game._lobby_pass_law_until_year, year + 2)
            else:
                self.game._lobby_pass_law_player = player
                self.game._lobby_pass_law_until_year = year + 2
            return {'success': True, 'message': 'Law passed! Area protection 2 years.'}
        if law_type == 'gov_attack' and beneficiaries:
            penalty = self.get_government_funding_amount()
            self.game._lobby_gov_attack_targets = beneficiaries
            self.game._lobby_gov_attack_penalty = penalty
            self.game._lobby_gov_attack_year = year + 1
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} will pay ${penalty} penalty next year.'}
        if law_type == 'add_lawsuit_excuse' and beneficiaries:
            from lawsuit import LAWSUIT_EXCUSES
            excuse = random.choice(LAWSUIT_EXCUSES)
            for t in beneficiaries:
                t.add_lawsuit_excuse(excuse)
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} have new excuse: "{excuse.cause}".'}
        if law_type == 'audit_target' and beneficiaries:
            self.game._lobby_audit_targets = beneficiaries
            self.game._lobby_audit_year = year + 1
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} pay double taxes next year.'}
        if law_type == 'block_funding' and beneficiaries:
            self.game._lobby_block_funding_targets = beneficiaries
            self.game._lobby_block_funding_year = year + 1
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! {names} blocked from funding.'}
        if law_type == 'increase_property_tax_5' and beneficiaries:
            increase = getattr(self.game, '_permanent_property_tax_increase', set())
            if not isinstance(increase, set):
                increase = set(increase) if increase else set()
            for t in beneficiaries:
                increase.add(self.get_player_id(t))
            self.game._permanent_property_tax_increase = increase
            names = ', '.join(self.get_player_name(t) for t in beneficiaries)
            return {'success': True, 'message': f'Law passed! Property tax permanently increased by 5% for {names}.'}
        if law_type == 'receive_funding':
            self._government_funding_recipient = player
            return {'success': True, 'message': "Law passed! You'll receive a government transit subsidy next year (based on your route km and open stations)."}
        if law_type == 'redirect_funding' and target_player:
            self._government_funding_recipient = player
            return {'success': True, 'message': f'Law passed! Funding redirected from {self.get_player_name(target_player)} to you.'}
        if law_type == 'pacifism':
            from game_presets import PACIFISM_DURATION_CALENDAR_YEARS, PACIFISM_WAR_START_CHANCE_MULTIPLIER
            # Exclusive calendar upper bound: war rolls use new_year when advancing; first affected
            # roll is entering year+1; we want D rolls → new_year runs year+1 .. year+D while new_year < year+D+1.
            new_until = year + PACIFISM_DURATION_CALENDAR_YEARS + 1
            prev = getattr(self.game, '_pacifism_until_year', None)
            if prev is not None and prev > year:
                new_until = max(new_until, prev)
            self.game._pacifism_until_year = new_until
            pct = int(round(PACIFISM_WAR_START_CHANCE_MULTIPLIER * 100))
            first_enter = year + 1
            last_enter = year + PACIFISM_DURATION_CALENDAR_YEARS
            return {
                'success': True,
                'message': (
                    f'Law passed! Pacifism halves the yearly chance of a new war starting for '
                    f'{PACIFISM_DURATION_CALENDAR_YEARS} war rolls (when advancing into {first_enter}–{last_enter}, '
                    f'~{pct}% of normal chance each roll).'
                ),
            }
        return {'success': False, 'message': 'Unknown law type.'}

    def get_government_funding_amount(self) -> int:
        base = getattr(self, '_government_funding_amount', 50000)
        return self.inflate_economic_value(base, max_factor=10.0)

    def get_government_seats_for_display(self, player: Player) -> list:
        """Return list of (is_favorable, is_tiebreaker) for government tab."""
        gov = self.game.get_government()
        if not gov:
            return []
        return gov.get_seats_for_display(self.get_player_id(player), self.get_year())

    def get_government_seats_with_allegiance(self) -> list:
        """Return list of (allegiance_player_id, influence, is_tiebreaker) for government tab."""
        gov = self.game.get_government()
        if not gov:
            return []
        return gov.get_seats_with_allegiance(self.get_year())

    def get_government_next_election_year(self) -> int:
        """Year of next government election."""
        gov = self.game.get_government()
        if not gov:
            return 0
        return gov.get_next_election_year(self.get_year())

    def get_government_tax_multiplier(self, player: Player) -> float:
        """Current tax multiplier for player based on government."""
        gov = self.game.get_government()
        if not gov:
            return 1.0
        return gov.get_tax_multiplier(self.get_player_id(player))

    def get_government_favor_ranking(self) -> list[tuple[int, float]]:
        """Return (player_id, influence) sorted by who government favors most (by influence)."""
        gov = self.game.get_government()
        if not gov:
            return []
        return gov.get_favor_ranking()

    def get_player_total_manpower(self, player: Player) -> int:
        """Total workers (engineers, construction workers, lawyers, maintenance workers) for a player."""
        total = 0
        for rental in getattr(player, 'worker_rentals', {}).values():
            total += getattr(rental, 'quantity', 0)
        return total

    def get_player_seats_controlled(self, player_id: int) -> int:
        """Number of government seats favorable to this player (people on their side)."""
        gov = self.game.get_government()
        if not gov:
            return 0
        return gov.get_favorable_count(player_id)

    def get_government_funding_recipient(self) -> Player:
        return getattr(self, '_government_funding_recipient', None)

    def distribute_government_funding(self, year: int) -> Optional[dict]:
        """If year >= 1970, give government funding to the chosen recipient (or random player).
        Funding is one turn only and equals transit subsidy (by km and open stations); no subsidy if no transit.
        Returns dict with recipient_name and amount when a grant is paid."""
        if year < 1970:
            return None
        active = [p for p in self.get_active_players() if not getattr(p, 'is_public_ta', False)]
        if not active:
            return None
        blocked_set = set()
        if getattr(self.game, '_lobby_block_funding_year', None) == year:
            targets = getattr(self.game, '_lobby_block_funding_targets', None) or (
                [getattr(self.game, '_lobby_block_funding_target', None)] if getattr(self.game, '_lobby_block_funding_target', None) is not None else [])
            blocked_set = {p for p in targets if p is not None}
            setattr(self.game, '_lobby_block_funding_targets', None)
            setattr(self.game, '_lobby_block_funding_target', None)
            setattr(self.game, '_lobby_block_funding_year', None)
        active = [p for p in active if p not in blocked_set]
        if not active:
            return None
        recipient = self._government_funding_recipient
        if recipient is None or recipient not in active or self.is_player_eliminated(recipient) or recipient in blocked_set:
            recipient = random.choice(active)
        amount = self._compute_transit_subsidy(recipient, year, is_favored=True)
        result = None
        if amount > 0:
            self.add_player_money(recipient, amount, reason='Government funding')
            result = {
                'recipient_name': self.get_player_name(recipient),
                'amount': int(amount),
            }
        self._government_funding_recipient = None
        return result

    # Trainset additional getters
    def get_trainset_locomotive_name(self, trainset) -> str:
        if trainset.locomotive:
            return trainset.locomotive.name
        return "Unknown"
    
    def get_trainset_locomotive_year(self, trainset):
        if trainset.locomotive:
            return getattr(trainset.locomotive, 'year_introduced', None)
        return None
    
    def get_trainset_capacity(self, trainset) -> int:
        return trainset.get_capacity()
    
    def get_trainset_maintenance_costs(self, trainset) -> int:
        return trainset.get_maitnance_costs(self.get_year())
    
    def get_vehicle_name(self, vehicle) -> str:
        return vehicle.name
    
    def get_vehicle_capacity(self, vehicle) -> int:
        return getattr(vehicle, 'capacity', 0)
    
    def get_player_id(self, player: Player) -> int:
        return player.get_player_id()
    
    def get_player_info(self, player: Player) -> str:
        """Get formatted player information string with all details"""
        current_year = self.get_year()
        out = []
        
        # Header
        out.append(f"{self.get_player_name(player)}: Money: ${self.get_player_money(player)}")
        
        # Lines owned
        line_names = self.get_player_line_names(player)
        if line_names:
            out.append("  Lines:")
            for lname in line_names:
                line = self.get_map_lines().get(lname)
                if line:
                    length = len(self.get_line_route(line))
                    profit = self.calc_line_profit(line, current_year)
                    mait = self.calc_line_maintenance(line, current_year)
                    out.append(f"    - {lname}: points={length}, profit=${int(profit)}, maintenance=${int(mait)}")
                else:
                    out.append(f"    - {lname}")
        else:
            out.append("  Lines: none")
        
        # Trainsets owned
        trainsets = self.get_player_trainsets(player)
        if trainsets:
            out.append("  Trainsets:")
            for i, ts in enumerate(trainsets):
                try:
                    if ts is None:
                        out.append(f"    - Trainset {i}: [None]")
                        continue
                    loco_name = self.get_trainset_locomotive_name(ts)
                    cars = len(self.get_trainset_cars(ts))
                    cap = self.get_trainset_capacity(ts)
                    mait = self.get_trainset_maintenance_costs(ts)
                    assign = self.get_trainset_line_assignment(ts)
                    out.append(f"    - Trainset {i}: loco={loco_name}, cars={cars}, cap={cap}, maintenance=${mait}, assigned_to={assign}")
                except Exception as e:
                    out.append(f"    - Trainset {i}: Error: {str(e)}")
        else:
            out.append("  Trainsets: none")
        
        player_profit = self.calc_player_profit(player)
        
        trainset_maintenance = 0
        for ts in self.get_player_trainsets(player):
            maint_cost = ts.get_maitnance_costs(current_year)
            if maint_cost is not None:
                trainset_maintenance += maint_cost
        
        # Resource charges
        # Use historical resource costs
        resource_costs = self.get_resource_costs(current_year)
        resource_charge = 0
        for trainset in player.get_trainsets():
            try:
                loco = getattr(trainset, 'locomotive', None)
                if loco and hasattr(loco, 'fuel') and loco.fuel:
                    fuel_name = getattr(loco.fuel, 'name', None)
                    if fuel_name and fuel_name in resource_costs:
                        resource_charge += resource_costs[fuel_name]
            except (AttributeError, TypeError):
                pass
        
        line_maintenance = self.game.calc_maitnance_by_player(player)
        
        # Taxes
        poll_tax, property_tax, income_tax = self.game.calc_taxes(player, current_year, player_profit or 0)
        
        # Worker costs
        worker_costs, _ = self.game.calc_worker_costs(player, current_year)
        
        total_costs = int(trainset_maintenance + resource_charge + line_maintenance + poll_tax + property_tax + income_tax + worker_costs)
        net = int(player_profit) - total_costs
        
        out.append("  Financial Summary:")
        out.append(f"    - Profit (lines): ${int(player_profit)}")
        out.append(f"    - Trainset maintenance: ${int(trainset_maintenance)}")
        out.append(f"    - Resource charges: ${int(resource_charge)}")
        out.append(f"    - Line maintenance: ${int(line_maintenance)}")
        out.append(f"    - Taxes: poll=${poll_tax}, property=${property_tax}, income=${income_tax}")
        if worker_costs > 0:
            out.append(f"    - Worker costs: ${int(worker_costs)}")
        out.append(f"    - Net this year: ${net}")
        
        return "\n".join(out)
    
    def get_all_players_info(self) -> list[str]:
        """Get formatted info for all active players"""
        active_players = self.get_active_players()
        return [self.get_player_info(p) for p in active_players]
    
    def convert_latlon_points_to_grid(self, latlon_points: list) -> list:
        """Convert list of (lat, lon) tuples to grid (x, y) coordinates"""
        game_map = self.game.get_map()
        if not game_map.use_osm or not game_map.osm_integration:
            raise ValueError("OSM integration required for lat/lon to grid conversion")
        
        center_lat = game_map.map_center_lat if game_map.map_center_lat is not None else 0.0
        center_lon = game_map.map_center_lon if game_map.map_center_lon is not None else 0.0
        # Ensure width and height are available
        map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
        map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
        grid_size = max(map_width, map_height)
        from map_preset_loader import get_map_bounds
        preset_name = getattr(game_map, 'map_preset_name', None)
        preset_bounds = get_map_bounds(preset_name) if preset_name else None
        use_preset_bounds = bool(
            preset_bounds
            and all(k in preset_bounds for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon'))
            and map_width and map_height
        )
        
        grid_points = []
        for lat, lon in latlon_points:
            if use_preset_bounds:
                y = (
                    (lat - preset_bounds['min_lat'])
                    / (preset_bounds['max_lat'] - preset_bounds['min_lat'])
                    * map_height
                )
                x = (
                    (lon - preset_bounds['min_lon'])
                    / (preset_bounds['max_lon'] - preset_bounds['min_lon'])
                    * map_width
                )
                x = max(0.0, min(float(map_width), float(x)))
                y = max(0.0, min(float(map_height), float(y)))
                grid_points.append((x, y))
            else:
                x, y = game_map.osm_integration.convert_latlon_to_grid(
                    lat, lon, center_lat, center_lon, grid_size,
                    map_width=game_map.width, map_height=game_map.height,
                )
                grid_points.append((x, y))
        
        return grid_points
    
    def _is_point_in_water_cached(self, x: float, y: float, game_map, water_cache: dict) -> bool:
        """Helper method to check if a point is in water, with caching to avoid redundant checks.
        Uses tile system if available, otherwise falls back to NYC/OSM methods.
        
        Args:
            x: Grid x coordinate
            y: Grid y coordinate
            game_map: Map object
            water_cache: Dictionary cache for water checks (key: (x, y), value: bool)
            
        Returns:
            True if point is in water, False otherwise
        """
        import math
        
        # OPTIMIZATION: Check cache first to avoid redundant checks
        cache_key = (round(x, 2), round(y, 2))  # Round to avoid floating point precision issues
        if cache_key in water_cache:
            return water_cache[cache_key]
        
        result = False
        # Use tile system if available (preferred method)
        # Tiles are already pre-cached, so this is fast
        if game_map.tile_system:
            result = game_map.tile_system.is_point_in_water(x, y)
        else:
            # Fallback: use preset's water detection if available
            preset_name = getattr(game_map, 'map_preset_name', None)
            if preset_name:
                from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
                if preset_has_water_areas(preset_name):
                    result = preset_is_point_in_water(preset_name, x, y, game_map)
            elif game_map.osm_integration:
                # Ensure width and height are available
                map_width = game_map.width if hasattr(game_map, 'width') and game_map.width else 50
                map_height = game_map.height if hasattr(game_map, 'height') and game_map.height else 50
                grid_size = max(map_width, map_height)
                center_lat = game_map.map_center_lat or 0.0
                center_lon = game_map.map_center_lon or 0.0
                lat, lon = game_map.osm_integration.convert_grid_to_latlon(x, y, center_lat, center_lon, grid_size)
                result = game_map.osm_integration.is_water_at_latlon(lat, lon)
            else:
                result = False
        
        # Cache the result
        water_cache[cache_key] = result
        return result

    def _segment_in_water_for_disaster(self, p1, p2, game_map, preset_name: str = None) -> bool:
        """True if segment from p1 to p2 is in water. Used for disaster repair to avoid street-on-water error.

        Uses preset/tile cache only (skip_osm=True) so UI elevation prompts never block on Overpass.
        """
        def _pt(p):
            if hasattr(p, 'x') and hasattr(p, 'y'):
                return float(p.x), float(p.y)
            return float(p[0]), float(p[1])
        x1, y1 = _pt(p1)
        x2, y2 = _pt(p2)
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        if game_map.tile_system:
            try:
                return (
                    game_map.tile_system.is_point_in_water(x1, y1, skip_osm=True)
                    or game_map.tile_system.is_point_in_water(x2, y2, skip_osm=True)
                    or game_map.tile_system.is_point_in_water(mid_x, mid_y, skip_osm=True)
                )
            except TypeError:
                return (
                    game_map.tile_system.is_point_in_water(x1, y1)
                    or game_map.tile_system.is_point_in_water(x2, y2)
                    or game_map.tile_system.is_point_in_water(mid_x, mid_y)
                )
        if preset_name:
            from map_preset_loader import preset_has_water_areas, preset_is_point_in_water
            if preset_has_water_areas(preset_name):
                return (
                    preset_is_point_in_water(preset_name, x1, y1, game_map, skip_osm=True)
                    or preset_is_point_in_water(preset_name, x2, y2, game_map, skip_osm=True)
                    or preset_is_point_in_water(preset_name, mid_x, mid_y, game_map, skip_osm=True)
                )
        return False

    def segment_is_over_water_not_bridge(self, p1, p2) -> bool:
        """True when a segment crosses open water (not a bridge). Preset-only (no OSM)."""
        game_map = self.game.get_map()
        preset_name = getattr(game_map, 'map_preset_name', None)
        if not self._segment_in_water_for_disaster(p1, p2, game_map, preset_name):
            return False
        def _pt(p):
            if hasattr(p, 'x') and hasattr(p, 'y'):
                return float(p.x), float(p.y)
            return float(p[0]), float(p[1])
        x1, y1 = _pt(p1)
        x2, y2 = _pt(p2)
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        if preset_name:
            from map_preset_loader import get_preset_is_point_on_bridge
            if get_preset_is_point_on_bridge(preset_name, mid_x, mid_y, self.get_year(), game_map):
                return False
            if _segment_on_bridge_or_tunnel(game_map, x1, y1, x2, y2, self.get_year()):
                return False
        return True

    def get_segment_elevation_options(self, p1, p2, *, is_bus_line: bool = False) -> tuple:
        """Return (allowed_elevations, default_elevation) for one route segment.

        When a segment cannot be at street level (e.g. over water) the constructor
        defaults it to 'underground' rather than an elevated structure.
        """
        if self.segment_is_over_water_not_bridge(p1, p2):
            return list(_WATER_SEGMENT_ELEVATIONS), 'underground'
        return list(_LAND_SEGMENT_ELEVATIONS), 'street'

    def get_line_creation_elevation_specs(self, points: list) -> list:
        """One elevation spec per segment for a new line."""
        specs = []
        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i + 1]
            specs.append({
                'label': (
                    f"Segment {i + 1} ({float(p1[0]):.1f}, {float(p1[1]):.1f})"
                    f" → ({float(p2[0]):.1f}, {float(p2[1]):.1f})"
                ),
                'p1': p1,
                'p2': p2,
            })
        return specs

    def get_extension_elevation_specs(
        self, line_name: str, new_points: list, extend_from_end: bool,
    ) -> list:
        """One elevation spec per extension point (segment starting at that point)."""
        route = self.get_line_route_including_extensions(line_name)
        if not route:
            line = self.get_map_lines().get(line_name)
            route = list(self.get_line_route(line) or []) if line else []
        specs = []
        if extend_from_end:
            if new_points:
                specs.append({
                    'label': (
                        f"Point 1 ({float(new_points[0][0]):.1f}, {float(new_points[0][1]):.1f})"
                        " — connection from line"
                    ),
                    'p1': route[-1] if route else new_points[0],
                    'p2': new_points[0],
                })
            for i in range(1, len(new_points)):
                p1, p2 = new_points[i - 1], new_points[i]
                specs.append({
                    'label': f"Point {i + 1} ({float(p2[0]):.1f}, {float(p2[1]):.1f})",
                    'p1': p1,
                    'p2': p2,
                })
        else:
            for i in range(len(new_points) - 1):
                p1, p2 = new_points[i], new_points[i + 1]
                specs.append({
                    'label': f"Point {i + 1} ({float(p1[0]):.1f}, {float(p1[1]):.1f})",
                    'p1': p1,
                    'p2': p2,
                })
            if new_points and route:
                p1 = new_points[-1]
                specs.append({
                    'label': (
                        f"Point {len(new_points)} ({float(p1[0]):.1f}, {float(p1[1]):.1f})"
                        " — connection to line"
                    ),
                    'p1': p1,
                    'p2': route[0],
                })
        return specs

