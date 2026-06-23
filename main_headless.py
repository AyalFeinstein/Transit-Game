#!/usr/bin/env python3
"""Headless entry point - run game without Qt GUI."""
import sys
from operations import Operations, get_game_params_for_preset
from map_preset_loader import list_available_presets, get_map_display_name


def main():
    print("Transit Game - Headless Mode")
    print("Available maps:", list_available_presets())
    for name in list_available_presets():
        display = get_map_display_name(name) or name
        params = get_game_params_for_preset(name)
        print(f"  {name}: {display} ({params['map_width']}x{params['map_height']})")
    
    # Start a minimal game
    ops = Operations(
        ["Player1", "Player2"],
        map_preset_name="standard",
        map_width=50,
        map_height=50
    )
    print("Game started. Year:", ops.get_year())
    print("Players:", [ops.get_player_name(p) for p in ops.get_players()])
    return 0


if __name__ == "__main__":
    sys.exit(main())
