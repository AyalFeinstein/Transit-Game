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
"""
from __future__ import annotations

import base64
import builtins
import io
import os
import pickle
import threading
from typing import Any, Callable

# Operations methods that change the game state. Everything else a client calls
# (getters, cost estimates, ...) only reads its local copy and stays local.
NETWORK_OPERATIONS = frozenset({
    'acquire_locomotive_for_player',
    'acquire_railcar_for_player',
    'acquire_trainset_for_player',
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
    paths: dict[int, tuple] = {}
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
                    paths[id(child)] = child_path
                next_frontier.append((child, child_path))
        frontier = next_frontier
    return paths


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


class _CallPickler(pickle.Pickler):
    def __init__(self, file, game, earlier_games=()):
        super().__init__(file, protocol=4)
        self._games = [game, *earlier_games]
        self._players = {}
        self._lines = {}
        for g in reversed(self._games):
            self._players.update({id(p): p.name for p in g.get_players()})
            self._lines.update({id(line): name for name, line in g.get_map().get_lines_by_name().items()})
        self._paths = None

    def persistent_id(self, obj):
        if isinstance(obj, _PRIMITIVES) or not _is_game_object(obj):
            return None
        # Players and built lines go by name so a widget still holding the object
        # from an earlier state broadcast refers to the same player or line.
        key = id(obj)
        if key in self._players:
            return ('player', self._players[key])
        if key in self._lines:
            return ('line', self._lines[key])
        if self._paths is None:
            # The current game first; objects a dialog kept from a replaced state are
            # found where they were then (the server's game has the same layout).
            self._paths = {}
            for g in reversed(self._games):
                self._paths.update(game_object_paths(g))
        path = self._paths.get(key)
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


def players_in_call(args: tuple, kwargs: dict) -> list:
    """Player objects passed directly as arguments, in argument order."""
    from player import Player
    values = list(args) + list(kwargs.values())
    return [v for v in values if isinstance(v, Player)]


def lines_in_call(args: tuple, kwargs: dict) -> list:
    from line import Line
    values = list(args) + list(kwargs.values())
    return [v for v in values if isinstance(v, Line)]


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

    cls = type(operations)
    operations.__class__ = type(cls.__name__, (cls,), {
        'game': property(lambda ops: ops.__dict__.get('game'), set_game),
    })

    def forward(name, method):
        def call(*args, **kwargs):
            depth = getattr(nesting, 'depth', 0)
            if depth:
                return method(*args, **kwargs)
            message = encode_operations_call(operations.game, name, args, kwargs, replaced_games)
            nesting.depth = depth + 1
            try:
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
