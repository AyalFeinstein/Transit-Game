"""Compatibility layer: UI = Operations. All game logic lives in operations.py."""
from operations import setup_game, Operations

# Server and tests use "UI" as the game logic class
UI = Operations

__all__ = ["setup_game", "UI", "Operations"]
