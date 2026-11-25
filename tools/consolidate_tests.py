#!/usr/bin/env python3
"""Merge fragmented test_*.py files into one file per main module."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONSOLIDATION: dict[str, list[str]] = {
    'test_ai.py': [
        'test_ai_street_building.py',
        'test_ai_performance_and_zero_ai.py',
        'test_ai_survival_30_years.py',
        'test_ai_survival_nyc_50_years.py',
        'test_ai_training.py',
        'test_ai_road_polylines_not_fallback.py',
        'test_random_ai_and_merge.py',
    ],
    'test_operations.py': [
        'test_line_creation_comprehensive.py',
        'test_operations_ferry_and_workers.py',
        'test_ferry_validation_and_duplicate_names.py',
        'test_ferry_validation_comprehensive.py',
        'test_line_comprehensive.py',
        'test_line_extension.py',
        'test_lines_tab_and_construction.py',
        'test_disaster_segments.py',
        'test_ridership_routes.py',
        'test_edge_cases_and_integration.py',
        'test_marketplace_line_purchase.py',
        'test_marketplace_comprehensive.py',
        'test_lobby_and_government_funding.py',
        'test_government.py',
        'test_fixes.py',
        'test_recent_changes_comprehensive.py',
        'test_inflation_utils.py',
    ],
    'test_map.py': [
        'test_map_comprehensive.py',
        'test_map_initialization.py',
        'test_map_edit_mode_and_osm.py',
        'test_map_selection_and_ownership.py',
        'test_map_rendering_performance.py',
        'test_map_display.py',
        'test_map_html_crash.py',
        'test_map_sidebar.py',
        'test_nyc_map.py',
        'test_map_presets_nyc_road_utils.py',
        'test_edit_mode_population_points.py',
        'tests/test_map_preset_standard.py',
        'test_nyc_crash.py',
    ],
    'test_game.py': [
        'test_game_comprehensive.py',
        'test_game_player2_comprehensive.py',
        'test_timeline.py',
        'test_player_comprehensive.py',
        'test_player2_comprehensive.py',
        'test_line_player2_comprehensive.py',
        'test_marketplace_player2_comprehensive.py',
    ],
    'test_qt_networked.py': [
        'test_qt_networked_buttons.py',
        'test_qt_layout_lifecycle.py',
        'test_ui.py',
        'test_ui_player2.py',
        'test_qt.py',
    ],
    'test_osm.py': [
        'test_osm_integration.py',
        'test_osm_tile_system.py',
        'test_osm_map_line_overlays.py',
        'test_osm_map_display.py',
        'test_osm_map_display_actual.py',
        'test_osm_map_debug.py',
        'test_osm_nyc_crash.py',
        'tests/test_osm_tile_square_50m.py',
    ],
    'test_vehicle.py': [
        'test_vehicle_registry.py',
        'test_vehicle_railcar_constraint.py',
    ],
}

KEEP_SEPARATE = {'test_performance_timing.py'}


def _strip_module_docstring_and_main(text: str) -> str:
    lines = text.splitlines(keepends=True)
    i = 0
    if i < len(lines) and lines[i].strip().startswith(('"""', "'''")):
        quote = lines[i].strip()[:3]
        if lines[i].strip().count(quote) >= 2 and len(lines[i].strip()) > 6:
            i += 1
        else:
            i += 1
            while i < len(lines):
                if quote in lines[i]:
                    i += 1
                    break
                i += 1
    body = ''.join(lines[i:])
    body = re.sub(
        r'\nif __name__ == [\'"]__main__[\'"]:\s*\n\s*unittest\.main\(\)\s*\n?',
        '\n',
        body,
    )
    return body.strip() + '\n'


def merge_target(target: str, sources: list[str]) -> str:
    chunks = [
        f'"""Consolidated tests for {target.replace("test_", "").replace(".py", "")}."""\n',
        'import unittest\n',
    ]
    for rel in sources:
        path = ROOT / rel
        if not path.is_file():
            print(f'  skip missing: {rel}')
            continue
        body = _strip_module_docstring_and_main(path.read_text(encoding='utf-8'))
        chunks.append(f'\n# --- from {rel} ---\n')
        chunks.append(body)
    chunks.append("\nif __name__ == '__main__':\n    unittest.main()\n")
    return ''.join(chunks)


def main() -> None:
    for target, sources in CONSOLIDATION.items():
        out = ROOT / target
        print(f'Writing {target} ({len(sources)} sources)...')
        out.write_text(merge_target(target, sources), encoding='utf-8')

    for target, sources in CONSOLIDATION.items():
        for rel in sources:
            p = ROOT / rel
            if p.is_file() and p.name not in KEEP_SEPARATE and p.name != target:
                print(f'Delete {p.relative_to(ROOT)}')
                p.unlink()

    tests_dir = ROOT / 'tests'
    if tests_dir.is_dir() and not any(tests_dir.iterdir()):
        tests_dir.rmdir()
        print('Removed empty tests/')


if __name__ == '__main__':
    main()
