#!/usr/bin/env python3
"""
Message protocol definitions for TransitGame networking
Defines the structure of messages sent between client and server
"""

import re
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

_DEFAULT_SERVER_PORT = 8080
_HOST_WITH_PORT_RE = re.compile(
    r'^(?:(?P<scheme>https?)://)?(?P<host>[^:/\s]+)(?::(?P<port>\d+))?(?:/.*)?$'
)


def parse_server_address(host_or_url: str, port: Optional[int] = None) -> str:
    """Normalize host, IP, or URL into a Socket.IO server URL.

    Examples:
        "192.168.1.5" -> "http://192.168.1.5:8080"
        "192.168.1.5:9090" -> "http://192.168.1.5:9090"
        "http://example.com:3000" -> "http://example.com:3000"
        "localhost" with port=3000 -> "http://localhost:3000"
    """
    raw = (host_or_url or "").strip()
    if not raw:
        raise ValueError("Server address cannot be empty")

    match = _HOST_WITH_PORT_RE.match(raw)
    if not match:
        raise ValueError(f"Invalid server address: {host_or_url!r}")

    scheme = match.group("scheme") or "http"
    host = match.group("host")
    parsed_port = match.group("port")

    if parsed_port is not None:
        resolved_port = int(parsed_port)
    elif port is not None:
        resolved_port = int(port)
    else:
        resolved_port = _DEFAULT_SERVER_PORT

    if resolved_port < 1 or resolved_port > 65535:
        raise ValueError(f"Invalid port: {resolved_port}")

    return f"{scheme}://{host}:{resolved_port}"


class ActionType(Enum):
    """Types of player actions"""
    BUILD_LINE = "build_line"
    BUILD_STATION = "build_station"
    BUY_TRAINSET = "buy_trainset"
    BUY_LOCOMOTIVE = "buy_locomotive"
    BUY_RAILCAR = "buy_railcar"
    ASSIGN_TRAINSET = "assign_trainset"
    UNASSIGN_TRAINSET = "unassign_trainset"
    RENT_WORKER = "rent_worker"
    FIRE_WORKER = "fire_worker"
    ADVANCE_YEAR = "advance_year"
    END_TURN = "end_turn"
    SELL_LINE = "sell_line"
    SELL_TRAINSET = "sell_trainset"


@dataclass
class BuildLineAction:
    """Action to build a new line"""
    name: str
    points: list  # List of (x, y) tuples
    construction_workers: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'action_type': ActionType.BUILD_LINE.value,
            'action_data': {
                'name': self.name,
                'points': self.points,
                'construction_workers': self.construction_workers
            }
        }


@dataclass
class BuyTrainsetAction:
    """Action to buy a trainset from marketplace"""
    item_id: str  # Marketplace item identifier
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'action_type': ActionType.BUY_TRAINSET.value,
            'action_data': {
                'item_id': self.item_id
            }
        }


@dataclass
class AssignTrainsetAction:
    """Action to assign a trainset to a line"""
    trainset_index: int
    line_name: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'action_type': ActionType.ASSIGN_TRAINSET.value,
            'action_data': {
                'trainset_index': self.trainset_index,
                'line_name': self.line_name
            }
        }


@dataclass
class AdvanceYearAction:
    """Action to advance the game year"""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'action_type': ActionType.ADVANCE_YEAR.value,
            'action_data': {}
        }


@dataclass
class GameStateMessage:
    """Message containing game state"""
    game_data: str  # Base64 encoded pickled game
    year: int
    is_playing: bool
    num_players: int
    active_players: int
    current_turn: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GameStateMessage':
        return cls(
            game_data=data.get('game_data', ''),
            year=data.get('year', 1830),
            is_playing=data.get('is_playing', False),
            num_players=data.get('num_players', 0),
            active_players=data.get('active_players', 0),
            current_turn=data.get('current_turn')
        )


@dataclass
class ActionResult:
    """Result of a player action"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionResult':
        return cls(
            success=data.get('success', False),
            message=data.get('message', ''),
            data=data.get('data')
        )


# Server event names
class ServerEvents:
    """Server event names"""
    CONNECTED = "connected"
    DISCONNECTED = "disconnect"
    GAME_CREATED = "game_created"
    GAME_STATE = "game_state"
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    ACTION_RESULT = "action_result"
    ACTION_FAILED = "action_failed"
    ERROR = "error"
    GAMES_LIST = "games_list"


# Client event names
class ClientEvents:
    """Client event names"""
    CREATE_GAME = "create_game"
    JOIN_GAME = "join_game"
    GET_GAME_STATE = "get_game_state"
    PLAYER_ACTION = "player_action"
    LIST_GAMES = "list_games"

