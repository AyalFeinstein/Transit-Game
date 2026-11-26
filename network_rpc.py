"""Replay a networked client's game actions on the server.

In network play the server owns the game; each client holds a copy that is
replaced whenever the server broadcasts a new state. Client UI code calls
Operations methods exactly as in local play. ``install_network_operations`` wraps
the methods that change the game so each call also runs on the server.

Arguments that are part of the game (players, lines, stations, vehicles, ...)
travel as references - their path from the Game object - and are looked up in the
server's own game, so the server acts on its objects rather than on copies. Other
arguments travel by value. The server unpickles them with ``RestrictedUnpickler``,
which only builds plain data and classes defined by this project's modules.

State broadcasts (``pack_game_state``) carry, besides the Game, the server Operations' own state
(completed builds, news, global settings...) and, after End Year, the year-end summary. They keep
each player, line and station the same Python object on the client from one broadcast to the next,
so tabs and dialogs holding one stay current. Each call carries a random seed so the client's local
run and the server's run of it roll the same dice.
"""
from __future__ import annotations

import base64
import builtins
import contextlib
import io
import os
import pickle
import random
import threading
import weakref
from typing import Any, Callable

# Operations methods that change the game state. Everything else a client calls
# (getters, cost estimates, ...) only reads its local copy and stays local.
NETWORK_OPERATIONS = frozenset({
    'acquire_locomotive_for_player',
    'acquire_railcar_for_player',
    'acquire_trainset_for_player',
    'apply_runtime_game_settings',
    'add_railcar_to_trainset',
    'add_railcar_to_trainset_for_player',
    'assemble_trainset_complete',
    'assign_trainset_to_line',
    'assign_workers_to_build',
    'auto_assign_unassigned_workers',
    'buy_line_by_purchase',
    'buy_vehicle_complete',
    'cancel_build',
    'close_station',
    'create_line_complete',
    'create_station_complete',
    'create_station_immediate',
    'deduct_player_money',
    'enforce_line_schedule_limits',
    'expand_station_to_line',
    'extend_line_complete',
    'fire_workers',
    'marketplace_deduct_worker_supply',
    'merge_players',
    'modify_station_lines',
    'remove_player_trainset',
    'remove_railcar_from_trainset_for_player',
    'rename_line',
    'rename_station',
    'rent_workers_for_player',
    'repay_loan',
    'reorder_build_queue',
    'replace_trainset_locomotive_for_player',
    'sell_line_complete',
    'sell_line_segment_complete',
    'sell_locomotive_for_half_price',
    'sell_player_line_to_marketplace',
    'sell_railcar_for_half_price',
    'sell_to_historical_society_complete',
    'sell_trainset_for_half_price',
    'set_line_fare',
    'set_line_maintenance_workers',
    'set_line_schedule',
    'set_worker_salary_multiplier',
    'start_law_draft',
    'submit_law_draft',
    'take_loan',
    'try_file_lawsuit',
    'try_lobby',
    'unassign_trainset_complete',
    'unassign_trainset_from_line',
    'upgrade_line_complete',
})

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_PRIMITIVES = (str, bytes, int, float, bool, complex, type(None))
# Containers bigger than this are only searched when they hold game objects
# (skips population grids, road geometry and similar bulk data).
_BULK_CONTAINER_SIZE = 256


def _is_project_class(cls: type) -> bool:
    module = getattr(cls, '__module__', '') or ''
    cached = _project_module_cache.get(module)
    if cached is None:
        import sys
        mod = sys.modules.get(module)
        path = os.path.abspath(getattr(mod, '__file__', '') or '')
        cached = bool(path) and os.path.dirname(path) == _PROJECT_DIR
        _project_module_cache[module] = cached
    return cached


_project_module_cache: dict[str, bool] = {}


def _is_game_object(obj) -> bool:
    return not isinstance(obj, type) and _is_project_class(type(obj))


def _worth_walking(container) -> bool:
    """Big containers are only walked when they hold game objects."""
    if len(container) <= _BULK_CONTAINER_SIZE:
        return True
    sample = next(iter(container.values() if isinstance(container, dict) else container), None)
    return _is_game_object(sample) or isinstance(sample, (list, dict))


def _children(obj):
    """(step, child) pairs of an object in the game graph."""
    if isinstance(obj, dict):
        if _worth_walking(obj):
            for key, value in obj.items():
                if isinstance(key, (str, int)) or (
                    isinstance(key, tuple) and all(isinstance(k, _PRIMITIVES) for k in key)
                ):
                    yield ('k', key), value
    elif isinstance(obj, (list, tuple)):
        if _worth_walking(obj):
            for index, value in enumerate(obj):
                yield ('i', index), value
    elif _is_game_object(obj):
        state = getattr(obj, '__dict__', None)
        if isinstance(state, dict):
            for name, value in state.items():
                yield ('a', name), value


def game_object_paths(game) -> dict[int, tuple]:
    """id(object) -> shortest path from ``game`` for every game object reachable from it."""
    return {id(obj): path for obj, path in game_objects_with_paths(game)}


def game_objects_with_paths(game):
    """(object, shortest path from ``game``) for every game object reachable from it."""
    seen = {id(game)}
    frontier = [(game, ())]
    while frontier:
        next_frontier = []
        for obj, path in frontier:
            for step, child in _children(obj):
                if isinstance(child, _PRIMITIVES) or id(child) in seen:
                    continue
                seen.add(id(child))
                child_path = path + (step,)
                if _is_game_object(child):
                    yield child, child_path
                next_frontier.append((child, child_path))
        frontier = next_frontier


def resolve_game_path(game, path) -> Any:
    obj = game
    for kind, key in path:
        if kind == 'a':
            obj = getattr(obj, key)
        elif kind == 'i':
            obj = obj[key]
        elif kind == 'k':
            obj = obj[tuple(key) if isinstance(key, list) else key]
        else:
            raise ValueError(f"Bad path step {kind!r}")
    return obj


def _signature(obj):
    """What identifies a game object across state broadcasts when it is not the same Python object
    (a station by name and position, anything with an object id by that id), or None."""
    object_id = getattr(obj, 'object_id', None)
    if isinstance(object_id, int):
        return (type(obj).__name__, 'id', object_id)
    name, x, y = (getattr(obj, a, None) for a in ('name', 'x', 'y'))
    if isinstance(name, str) and isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return (type(obj).__name__, name, round(float(x), 6), round(float(y), 6))
    return None


class _CallPickler(pickle.Pickler):
    def __init__(self, file, game, earlier_games=()):
        super().__init__(file, protocol=4)
        self._game = game
        self._games = [game, *earlier_games]
        self._paths = None
        self._signature_paths = None

    def persistent_id(self, obj):
        if isinstance(obj, _PRIMITIVES) or not _is_game_object(obj):
            return None
        from player import Player
        from line import Line
        # Players and lines go by name, so a widget still holding the object from any
        # earlier state broadcast refers to the same player or line.
        if isinstance(obj, Player) and isinstance(getattr(obj, 'name', None), str):
            return ('player', obj.name)
        if isinstance(obj, Line) and isinstance(getattr(obj, 'name', None), str):
            if obj.name in self._game.get_map().get_lines_by_name():
                return ('line', obj.name)
        key = id(obj)
        if self._paths is None:
            # The current game first; objects a dialog kept from a replaced state are
            # found where they were then (the server's game has the same layout).
            self._paths = {}
            for g in reversed(self._games):
                self._paths.update(game_object_paths(g))
        path = self._paths.get(key)
        if path is None:
            # An object from an older state: the current game's object with the same identity.
            signature = _signature(obj)
            if signature is not None:
                if self._signature_paths is None:
                    self._signature_paths = {}
                    for found, found_path in game_objects_with_paths(self._game):
                        sig = _signature(found)
                        if sig is not None:
                            self._signature_paths.setdefault(sig, found_path)
                path = self._signature_paths.get(signature)
        return ('path', path) if path is not None else None


_SAFE_BUILTINS = frozenset({'set', 'frozenset', 'complex', 'slice', 'range', 'bytearray'})
_SAFE_MODULES = {'collections': frozenset({'OrderedDict', 'defaultdict', 'deque', 'Counter'})}


class RestrictedUnpickler(pickle.Unpickler):
    """Unpickles client data: plain data plus classes defined in this project."""

    def __init__(self, file, game):
        super().__init__(file)
        self._game = game

    def find_class(self, module, name):
        if module == 'builtins' and name in _SAFE_BUILTINS:
            return getattr(builtins, name)
        if name in _SAFE_MODULES.get(module, ()):
            import importlib
            return getattr(importlib.import_module(module), name)
        if '.' not in name:
            import sys
            mod = sys.modules.get(module)
            cls = getattr(mod, name, None) if mod is not None else None
            if isinstance(cls, type) and cls.__module__ == module and _is_project_class(cls):
                return cls
        raise pickle.UnpicklingError(f"{module}.{name} is not allowed in a network action")

    def persistent_load(self, pid):
        kind, value = pid
        if kind == 'player':
            for player in self._game.get_players():
                if player.name == value:
                    return player
            raise pickle.UnpicklingError(f"Unknown player {value!r}")
        if kind == 'line':
            line = self._game.get_map().get_lines_by_name().get(value)
            if line is None:
                raise pickle.UnpicklingError(f"Unknown line {value!r}")
            return line
        if kind == 'path':
            try:
                return resolve_game_path(self._game, value)
            except (AttributeError, IndexError, KeyError, TypeError) as e:
                raise pickle.UnpicklingError(f"Game object no longer exists: {e}") from e
        raise pickle.UnpicklingError(f"Unknown reference kind {kind!r}")


def encode_operations_call(game, method: str, args: tuple, kwargs: dict, earlier_games=()) -> dict:
    """Message body for one Operations call made against ``game``.

    ``earlier_games`` are states the client replaced recently; objects still held from
    them are sent as references too.
    """
    buf = io.BytesIO()
    _CallPickler(buf, game, earlier_games).dump((tuple(args), dict(kwargs)))
    return {'method': method, 'call': base64.b64encode(buf.getvalue()).decode('ascii')}


def decode_operations_call(game, data: dict) -> tuple[str, tuple, dict]:
    """(method, args, kwargs) with references resolved against the server's ``game``."""
    method = data.get('method')
    if method not in NETWORK_OPERATIONS:
        raise ValueError(f"{method!r} cannot be called over the network")
    raw = base64.b64decode(str(data.get('call') or '').encode('ascii'))
    args, kwargs = RestrictedUnpickler(io.BytesIO(raw), game).load()
    if not isinstance(args, tuple) or not isinstance(kwargs, dict):
        raise ValueError("Malformed network action")
    return method, args, kwargs


@contextlib.contextmanager
def seeded_random(seed):
    """Run with the global ``random`` generator seeded (then restored), so a client's local run of a
    call and the server's run of it roll the same dice (lawsuits, lobbying, NIMBY suits...)."""
    if seed is None:
        yield
        return
    saved = random.getstate()
    random.seed(seed)
    try:
        yield
    finally:
        random.setstate(saved)


def players_in_call(args: tuple, kwargs: dict) -> list:
    """Player objects passed directly as arguments, in argument order."""
    from player import Player
    values = list(args) + list(kwargs.values())
    return [v for v in values if isinstance(v, Player)]


def lines_in_call(args: tuple, kwargs: dict) -> list:
    from line import Line
    values = list(args) + list(kwargs.values())
    return [v for v in values if isinstance(v, Line)]


# Game state the server's Operations keeps outside the Game object; clients get it with
# every state broadcast so their displays match the server's.
OPERATIONS_STATE_ATTRIBUTES = (
    'completed_jobs',
    'public_ta',
    '_government_funding_recipient',
    '_government_funding_amount',
    '_news_log',
    '_ai_money_debug_log',
    '_disaster_damage_history',
    '_cached_ridership_data',
    '_cached_ridership_year',
    'last_ridership_sample_size',
    'ruleset_name',
)
# Game settings kept in module globals (game_presets / inflation) rather than on the Game.
GLOBAL_SETTING_KEYS = (
    'line_build_speed_multiplier',
    'construction_cost_multiplier',
    'marketplace_bulk_discount_percent',
    'marketplace_bulk_discount_threshold',
    'inflation_rate',
)


def operations_state(operations) -> dict:
    """The server Operations' state that is not on the Game (sent with the game)."""
    state = {name: operations.__dict__[name] for name in OPERATIONS_STATE_ATTRIBUTES if name in operations.__dict__}
    settings = operations.get_runtime_game_settings()
    state['global_settings'] = {k: settings[k] for k in GLOBAL_SETTING_KEYS if k in settings}
    return state


def apply_operations_state(operations, state: dict) -> None:
    """Make a client's Operations match the server's state sent with the game."""
    if not isinstance(state, dict):
        return
    for name in OPERATIONS_STATE_ATTRIBUTES:
        if name in state:
            operations.__dict__[name] = state[name]
    settings = state.get('global_settings') or {}
    import game_presets
    from inflation_utils import set_inflation_rate_multiplier
    if 'line_build_speed_multiplier' in settings:
        game_presets.LINE_BUILD_SPEED_MULTIPLIER = float(settings['line_build_speed_multiplier'])
    if 'construction_cost_multiplier' in settings:
        game_presets.CONSTRUCTION_COST_MULTIPLIER = float(settings['construction_cost_multiplier'])
    if 'marketplace_bulk_discount_percent' in settings:
        game_presets.MARKETPLACE_BULK_DISCOUNT_PERCENT = float(settings['marketplace_bulk_discount_percent']) / 100.0
    if 'marketplace_bulk_discount_threshold' in settings:
        game_presets.MARKETPLACE_BULK_DISCOUNT_THRESHOLD = int(settings['marketplace_bulk_discount_threshold'])
    if 'inflation_rate' in settings:
        set_inflation_rate_multiplier(float(settings['inflation_rate']))
    ruleset = state.get('ruleset_name')
    if ruleset and ruleset != _active_vehicle_ruleset[0]:
        try:
            from ruleset_loader import activate_vehicle_registry_for_ruleset
            activate_vehicle_registry_for_ruleset(ruleset)
            _active_vehicle_ruleset[0] = ruleset
        except Exception as e:
            print(f"Could not load ruleset {ruleset!r} from the server: {e}")


_active_vehicle_ruleset = [None]


def take_operations_state(game):
    """The Operations state the server sent with ``game`` (removed from it), or None."""
    return game.__dict__.pop(NETWORK_OPERATIONS_STATE, None) if game is not None else None


# Attribute on a client's copy of the game holding the Operations state sent with it.
NETWORK_OPERATIONS_STATE = '_network_operations_state'


def _identity_key(obj):
    """Key under which a client keeps one Python object for a player, line or station across state
    broadcasts, or None (other objects are simply replaced by each broadcast)."""
    from player import Player
    from line import Line
    from station import Station
    name = getattr(obj, 'name', None)
    if isinstance(obj, Player) and isinstance(name, str):
        return ('player', name)
    if isinstance(obj, Line) and isinstance(name, str):
        return ('line', name)
    if isinstance(obj, Station):
        return _signature(obj)
    return None


class _StatePickler(pickle.Pickler):
    """Writes players, lines and stations as references plus their state, so the client can update
    the objects it already has instead of replacing them (see unpack_game_state)."""

    def __init__(self, file):
        super().__init__(file, protocol=4)
        self.keyed = {}  # identity key -> object written under it
        self.pending = []  # (key, object) whose state is still to be written

    def persistent_id(self, obj):
        if isinstance(obj, _PRIMITIVES) or not _is_game_object(obj):
            return None
        key = _identity_key(obj)
        if key is None:
            return None
        known = self.keyed.get(key)
        if known is None:
            self.keyed[key] = obj
            self.pending.append((key, obj))
        elif known is not obj:
            return None  # two objects share the key: send this one as a plain copy
        cls = type(obj)
        return ('keep', key, cls.__module__, cls.__qualname__)


def pack_game_state(game, operations=None, year_end_result=None) -> bytes:
    """Pickle the game together with the server Operations' extra state and, after End Year, the
    year-end result (pickled together so the players and lines they name are the game's own).

    With ``operations`` (the server's broadcasts) players, lines and stations are written as
    references followed by their state; ``unpack_game_state`` updates the client's existing
    objects in place, so a tab or dialog holding one keeps seeing the current state.
    """
    if operations is None:
        return pickle.dumps(game, protocol=4)
    state = operations_state(operations)
    if year_end_result is not None:
        state['year_end_result'] = year_end_result
    buf = io.BytesIO()
    pickler = _StatePickler(buf)
    pickler.dump({'game': game, 'operations_state': state, 'kept_objects': True})
    while pickler.pending:
        key, obj = pickler.pending.pop()
        pickler.dump((key, obj.__dict__))
    pickler.dump(None)  # end of kept objects
    return buf.getvalue()


def year_end_result_of(game):
    """The year-end result the server sent with ``game`` (see pack_game_state), or None."""
    state = game.__dict__.get(NETWORK_OPERATIONS_STATE) if game is not None else None
    return state.get('year_end_result') if isinstance(state, dict) else None


# The client's player, line and station objects by identity key (kept while anything uses them).
_kept_objects = weakref.WeakValueDictionary()


class _StateUnpickler(pickle.Unpickler):
    def persistent_load(self, pid):
        kind, key, module, qualname = pid
        if kind != 'keep':
            raise pickle.UnpicklingError(f"Unknown reference kind {kind!r}")
        key = tuple(key)
        obj = _kept_objects.get(key)
        cls = self.find_class(module, qualname)
        if obj is None or type(obj) is not cls:
            obj = cls.__new__(cls)
            _kept_objects[key] = obj
        return obj


def unpack_game_state(data: bytes):
    """The Game from ``pack_game_state`` data, carrying its Operations state (see take_operations_state).

    Players, lines and stations the client already has are the same objects afterwards, with the
    server's current state.
    """
    stream = io.BytesIO(data)
    unpickler = _StateUnpickler(stream)
    loaded = unpickler.load()
    if not (isinstance(loaded, dict) and 'game' in loaded):
        return loaded
    if loaded.get('kept_objects'):
        states = []
        while True:
            item = unpickler.load()
            if item is None:
                break
            states.append(item)
        for key, state in states:
            obj = _kept_objects.get(tuple(key))
            if obj is None:
                continue
            obj.__dict__.clear()
            if hasattr(type(obj), '__setstate__'):
                obj.__setstate__(state)
            else:
                obj.__dict__.update(state)
    game = loaded['game']
    if game is not None:
        game.__dict__[NETWORK_OPERATIONS_STATE] = loaded.get('operations_state')
    return game


def install_network_operations(operations, send: Callable[[dict], None]) -> None:
    """Make ``operations`` forward every game-changing call to the server.

    Each call still runs locally first, so the UI gets its usual result and error
    messages at once; the server's next state broadcast replaces the local game.
    Calls that fail locally (raise, or return ``{'success': False}``) are not sent.
    Calls made from inside another forwarded call are part of it and are not sent.
    Games replaced by later broadcasts are remembered briefly, so objects a dialog still
    holds from them are sent as references to the matching server objects.
    """
    nesting = threading.local()
    replaced_games = []  # the last two games the server's broadcasts replaced, newest first

    def set_game(ops, game):
        old = ops.__dict__.get('game')
        if old is not None and old is not game:
            replaced_games.insert(0, old)
            del replaced_games[2:]
        ops.__dict__['game'] = game
        apply_operations_state(ops, take_operations_state(game))

    cls = type(operations)
    operations.__class__ = type(cls.__name__, (cls,), {
        'game': property(lambda ops: ops.__dict__.get('game'), set_game),
    })
    apply_operations_state(operations, take_operations_state(operations.__dict__.get('game')))

    def forward(name, method):
        def call(*args, **kwargs):
            depth = getattr(nesting, 'depth', 0)
            if depth:
                return method(*args, **kwargs)
            message = encode_operations_call(operations.game, name, args, kwargs, replaced_games)
            message['seed'] = random.SystemRandom().getrandbits(63)
            nesting.depth = depth + 1
            try:
                with seeded_random(message['seed']):
                    result = method(*args, **kwargs)
            finally:
                nesting.depth = depth
            if not (isinstance(result, dict) and result.get('success') is False):
                send(message)
            return result
        call.__name__ = name
        call.__wrapped__ = method
        return call

    for name in NETWORK_OPERATIONS:
        method = getattr(operations, name, None)
        if callable(method):
            setattr(operations, name, forward(name, method))
