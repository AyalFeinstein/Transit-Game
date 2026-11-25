#!/usr/bin/env python3
"""Headless entry point - run game without Qt GUI."""
import sys
from operations import Operations, get_game_params_for_preset
from map_preset_loader import get_map_display_name, get_new_game_default_preset, list_available_presets


def main():
    print("GoCommuter - Headless Mode")
    print("Available maps:", list_available_presets())
    for name in list_available_presets():
        display = get_map_display_name(name) or name
        params = get_game_params_for_preset(name)
        print(f"  {name}: {display} ({params['map_width']}x{params['map_height']})")
    
    # Start a minimal game
    ops = Operations(["Player1", "Player2"], map_preset_name=get_new_game_default_preset())
    print("Game started. Year:", ops.get_year())
    print("Players:", [ops.get_player_name(p) for p in ops.get_players()])
    return 0


if __name__ == "__main__":
    sys.exit(main())
