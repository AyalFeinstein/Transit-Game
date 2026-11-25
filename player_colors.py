"""Distinct player colors for map lines, tabs, government UI, and legends."""

from PyQt6.QtGui import QColor

# Twelve visually distinct colors (no repeats for up to 12 players).
PLAYER_COLORS_HEX = [
    '#228B22',  # forest green
    '#4169E1',  # royal blue
    '#B22222',  # firebrick
    '#FF8C00',  # dark orange
    '#6A5ACD',  # slate blue
    '#008B8B',  # dark cyan
    '#C71585',  # medium violet red
    '#DAA520',  # goldenrod
    '#2F4F4F',  # dark slate gray
    '#DC143C',  # crimson
    '#4682B4',  # steel blue
    '#9ACD32',  # yellow green
]

PLAYER_COLORS = [QColor(int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)) for h in PLAYER_COLORS_HEX]

NO_ALLEGIANCE_HEX = '#C0C0C0'
NO_ALLEGIANCE_COLOR = QColor(192, 192, 192)


def get_player_color_index(player) -> int:
    """Stable color slot for a player; unique when assigned at join time."""
    if player is None:
        return -1
    idx = getattr(player, 'color_index', None)
    if idx is not None:
        return int(idx) % len(PLAYER_COLORS_HEX)
    return int(getattr(player, 'player_id', 0) or 0) % len(PLAYER_COLORS_HEX)


def assign_player_color_index(player, index: int) -> None:
    player.color_index = int(index) % len(PLAYER_COLORS_HEX)


def next_available_color_index(players) -> int:
    """Pick the first unused color index, or wrap if all are taken."""
    used = {get_player_color_index(p) for p in (players or [])}
    for i in range(len(PLAYER_COLORS_HEX)):
        if i not in used:
            return i
    return len(players or []) % len(PLAYER_COLORS_HEX)


def assign_color_indices_for_new_players(players) -> None:
    """Ensure every player in the list has a unique color when possible."""
    used = set()
    for player in players or []:
        idx = getattr(player, 'color_index', None)
        if idx is not None and int(idx) not in used:
            used.add(int(idx))
            continue
        for candidate in range(len(PLAYER_COLORS_HEX)):
            if candidate not in used:
                assign_player_color_index(player, candidate)
                used.add(candidate)
                break
