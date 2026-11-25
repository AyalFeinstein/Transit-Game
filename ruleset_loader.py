"""Discover and load game rulesets (RULES.md, VEHICLES.md, vehicles.json, events.json).

Each ruleset lives under rulesets/<name>/ with markdown docs and JSON catalogs for
vehicles and timeline events (including built-in game content when first opened).
The active ruleset is stored in rulesets/active_ruleset.txt.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
RULESETS_DIR = PROJECT_ROOT / "rulesets"
ACTIVE_RULESET_FILE = RULESETS_DIR / "active_ruleset.txt"
DEFAULT_RULESET_NAME = "default"

def _sanitize_ruleset_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", (name or "").strip())
    if not cleaned:
        raise ValueError("Ruleset name cannot be empty.")
    return cleaned


def get_ruleset_dir(ruleset_name: str) -> Path:
    return RULESETS_DIR / _sanitize_ruleset_name(ruleset_name)


def list_rulesets() -> List[str]:
    names = []
    if RULESETS_DIR.is_dir():
        for path in sorted(RULESETS_DIR.iterdir()):
            if path.is_dir() and (path / "ruleset.json").is_file():
                names.append(path.name)
    return names or [DEFAULT_RULESET_NAME]


def get_active_ruleset() -> str:
    if ACTIVE_RULESET_FILE.is_file():
        name = ACTIVE_RULESET_FILE.read_text(encoding="utf-8").strip()
        if name and get_ruleset_dir(name).is_dir():
            return name
    return DEFAULT_RULESET_NAME


def set_active_ruleset(ruleset_name: str) -> None:
    name = _sanitize_ruleset_name(ruleset_name)
    if not get_ruleset_dir(name).is_dir():
        raise ValueError(f"Ruleset '{name}' does not exist.")
    RULESETS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_RULESET_FILE.write_text(name, encoding="utf-8")


def get_rules_path(ruleset_name: Optional[str] = None) -> Path:
    return get_ruleset_dir(ruleset_name or get_active_ruleset()) / "RULES.md"


def get_vehicles_md_path(ruleset_name: Optional[str] = None) -> Path:
    return get_ruleset_dir(ruleset_name or get_active_ruleset()) / "VEHICLES.md"


def get_vehicles_json_path(ruleset_name: Optional[str] = None) -> Path:
    return get_ruleset_dir(ruleset_name or get_active_ruleset()) / "vehicles.json"


def get_events_json_path(ruleset_name: Optional[str] = None) -> Path:
    return get_ruleset_dir(ruleset_name or get_active_ruleset()) / "events.json"


def get_organizations_json_path(ruleset_name: Optional[str] = None) -> Path:
    return get_ruleset_dir(ruleset_name or get_active_ruleset()) / "organizations.json"


def get_news_json_path(ruleset_name: Optional[str] = None) -> Path:
    return get_ruleset_dir(ruleset_name or get_active_ruleset()) / "news.json"


def get_ruleset_metadata(ruleset_name: str) -> dict:
    path = get_ruleset_dir(ruleset_name) / "ruleset.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"name": ruleset_name, "description": ""}


def is_default_ruleset(ruleset_name: str) -> bool:
    return _sanitize_ruleset_name(ruleset_name) == DEFAULT_RULESET_NAME


def save_ruleset_metadata(ruleset_name: str, description: Optional[str] = None, **extra) -> None:
    """Persist ruleset.json (always written so user changes are saved)."""
    name = _sanitize_ruleset_name(ruleset_name)
    meta = get_ruleset_metadata(name)
    meta["name"] = name
    if description is not None:
        meta["description"] = description
    meta.update(extra)
    from datetime import datetime, timezone
    meta["saved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = get_ruleset_dir(name) / "ruleset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _vehicles_json_has_catalog(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    for key in ("locomotives", "railcars", "ferries", "buses"):
        if data.get(key):
            return True
    return False


def _events_json_is_full_catalog(data: dict) -> bool:
    """True when events.json holds the full ruleset timeline (not a legacy snippet)."""
    if not isinstance(data, dict) or not data.get("version"):
        return False
    return len(data.get("events") or []) >= 6


def read_vehicles_json_text(ruleset_name: Optional[str] = None) -> str:
    """JSON text for editor: merges built-in vehicle_presets when file missing or empty."""
    from ruleset_catalog import (
        builtin_vehicles_data,
        format_vehicles_json,
        parse_vehicles_json,
    )

    name = _sanitize_ruleset_name(ruleset_name or get_active_ruleset())
    path = get_vehicles_json_path(name)
    if path.is_file():
        try:
            data = parse_vehicles_json(path.read_text(encoding="utf-8"))
            if _vehicles_json_has_catalog(data):
                return format_vehicles_json(data)
        except Exception:
            pass
    from ruleset_catalog import registry_to_vehicles_data

    activate_vehicle_registry_for_ruleset(name)
    from vehicle_presets import get_vehicle_registry

    return format_vehicles_json(registry_to_vehicles_data(get_vehicle_registry()))


def read_events_json_text(ruleset_name: Optional[str] = None) -> str:
    """JSON text for editor: includes built-in EventSystem timeline when file missing or legacy list-only."""
    from ruleset_catalog import (
        builtin_events_data,
        format_events_json,
        parse_events_json,
    )

    name = _sanitize_ruleset_name(ruleset_name or get_active_ruleset())
    path = get_events_json_path(name)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw if "events" in raw else parse_events_json(path.read_text(encoding="utf-8"))
            else:
                data = parse_events_json(path.read_text(encoding="utf-8"))
            if _events_json_is_full_catalog(data):
                return format_events_json(data)
        except Exception:
            pass
    return format_events_json(builtin_events_data())


def save_vehicles_json(text: str, ruleset_name: Optional[str] = None) -> Path:
    from ruleset_catalog import parse_vehicles_json

    name = ruleset_name or get_active_ruleset()
    data = parse_vehicles_json(text)
    path = get_vehicles_json_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def save_events_json(text: str, ruleset_name: Optional[str] = None) -> Path:
    from ruleset_catalog import parse_events_json

    name = ruleset_name or get_active_ruleset()
    data = parse_events_json(text)
    path = get_events_json_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def read_organizations_json_text(ruleset_name: Optional[str] = None) -> str:
    """JSON text for editor: organizations / transit companies for this ruleset."""
    from ruleset_catalog import (
        builtin_organizations_data,
        format_organizations_json,
        parse_organizations_json,
    )

    name = _sanitize_ruleset_name(ruleset_name or get_active_ruleset())
    path = get_organizations_json_path(name)
    if path.is_file():
        try:
            data = parse_organizations_json(path.read_text(encoding="utf-8"))
            return format_organizations_json(data)
        except Exception:
            pass
    return format_organizations_json(builtin_organizations_data())


def save_organizations_json(text: str, ruleset_name: Optional[str] = None) -> Path:
    from agency import clear_agencies_cache
    from ruleset_catalog import parse_organizations_json

    name = ruleset_name or get_active_ruleset()
    data = parse_organizations_json(text)
    clear_agencies_cache()
    path = get_organizations_json_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_organizations_for_ruleset(ruleset_name: Optional[str] = None) -> dict:
    """Parsed organizations.json for the active or named ruleset."""
    from ruleset_catalog import parse_organizations_json

    path = get_organizations_json_path(ruleset_name)
    if path.is_file():
        try:
            return parse_organizations_json(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    from ruleset_catalog import builtin_organizations_data

    return builtin_organizations_data()


def save_ruleset_bundle(
    ruleset_name: str,
    *,
    rules_md: Optional[str] = None,
    vehicles_md: Optional[str] = None,
    vehicles_json: Optional[str] = None,
    events_json: Optional[str] = None,
    organizations_json: Optional[str] = None,
    news_json: Optional[str] = None,
    description: Optional[str] = None,
    refresh_vehicles_md: bool = True,
) -> None:
    """Save all ruleset files to disk."""
    name = _sanitize_ruleset_name(ruleset_name)
    if is_default_ruleset(name):
        raise ValueError("The default ruleset cannot be edited. Duplicate it to create a custom ruleset.")
    dest = get_ruleset_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    if rules_md is not None:
        save_rules_markdown(rules_md, name)
    if vehicles_json is not None:
        save_vehicles_json(vehicles_json, name)
        if refresh_vehicles_md:
            activate_vehicle_registry_for_ruleset(name)
            regenerate_vehicles_md(name)
    elif vehicles_md is not None:
        save_vehicles_markdown(vehicles_md, name)
    if events_json is not None:
        save_events_json(events_json, name)
    if organizations_json is not None:
        save_organizations_json(organizations_json, name)
    if news_json is not None:
        save_news_json(news_json, name)
    save_ruleset_metadata(name, description=description)


def read_news_json_text(ruleset_name: Optional[str] = None) -> str:
    from ruleset_catalog import builtin_news_data, format_news_json

    name = ruleset_name or get_active_ruleset()
    path = get_news_json_path(name)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return format_news_json(builtin_news_data())


def save_news_json(text: str, ruleset_name: Optional[str] = None) -> Path:
    from ruleset_catalog import parse_news_json

    name = ruleset_name or get_active_ruleset()
    data = parse_news_json(text)
    path = get_news_json_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_news_pools_for_ruleset(ruleset_name: Optional[str] = None) -> dict:
    """Economy/politics/oddities article tuples for the named or active ruleset."""
    from ruleset_catalog import builtin_news_data, news_pools_from_data, parse_news_json

    path = get_news_json_path(ruleset_name)
    if path.is_file():
        try:
            return news_pools_from_data(parse_news_json(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return news_pools_from_data(builtin_news_data())


def read_ruleset_bundle(ruleset_name: str) -> dict:
    """Load all editable ruleset files for the editor."""
    name = _sanitize_ruleset_name(ruleset_name)
    meta = get_ruleset_metadata(name)
    return {
        "rules_md": read_rules_markdown(name),
        "vehicles_md": read_vehicles_markdown(name),
        "vehicles_json": read_vehicles_json_text(name),
        "events_json": read_events_json_text(name),
        "organizations_json": read_organizations_json_text(name),
        "news_json": read_news_json_text(name),
        "description": meta.get("description", ""),
    }


def delete_ruleset(ruleset_name: str) -> None:
    """Remove a custom ruleset. The default ruleset cannot be deleted."""
    name = _sanitize_ruleset_name(ruleset_name)
    if is_default_ruleset(name):
        raise ValueError("The default ruleset cannot be deleted.")
    dest = get_ruleset_dir(name)
    if not dest.is_dir():
        raise ValueError(f"Ruleset '{name}' does not exist.")
    shutil.rmtree(dest)
    if get_active_ruleset() == name:
        set_active_ruleset(DEFAULT_RULESET_NAME)


def ensure_rulesets_layout() -> None:
    """Create rulesets/ directory, seed default ruleset files if missing, fix active_ruleset.txt."""
    RULESETS_DIR.mkdir(parents=True, exist_ok=True)
    default_dir = get_ruleset_dir(DEFAULT_RULESET_NAME)
    default_dir.mkdir(parents=True, exist_ok=True)
    if not (default_dir / "ruleset.json").is_file():
        _write_default_ruleset_files(default_dir, DEFAULT_RULESET_NAME)
    else:
        for src_name in ("RULES.md", "VEHICLES.md"):
            dest_file = default_dir / src_name
            if not dest_file.is_file():
                src = PROJECT_ROOT / src_name
                if src.is_file():
                    shutil.copy2(src, dest_file)
        _migrate_ruleset_json_catalog(default_dir)
    if not ACTIVE_RULESET_FILE.is_file():
        ACTIVE_RULESET_FILE.write_text(DEFAULT_RULESET_NAME, encoding="utf-8")
        return
    active = ACTIVE_RULESET_FILE.read_text(encoding="utf-8").strip()
    if not active or not get_ruleset_dir(active).is_dir():
        ACTIVE_RULESET_FILE.write_text(DEFAULT_RULESET_NAME, encoding="utf-8")


def create_ruleset(name: str, copy_from: Optional[str] = None, set_active: bool = True) -> str:
    """Create a new ruleset folder. Returns sanitized name."""
    RULESETS_DIR.mkdir(parents=True, exist_ok=True)
    name = _sanitize_ruleset_name(name)
    dest = get_ruleset_dir(name)
    if dest.exists():
        raise ValueError(f"Ruleset '{name}' already exists.")
    source_name = copy_from or DEFAULT_RULESET_NAME
    source = get_ruleset_dir(source_name)
    if source.is_dir():
        shutil.copytree(source, dest)
        _migrate_ruleset_json_catalog(dest)
        meta = get_ruleset_metadata(name)
        meta["name"] = name
        meta["description"] = meta.get("description", "") or f"Copy of {source_name}"
        (dest / "ruleset.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    else:
        dest.mkdir(parents=True)
        _write_default_ruleset_files(dest, name)
    save_ruleset_metadata(name)
    if set_active:
        set_active_ruleset(name)
    return name


def _write_default_ruleset_files(dest: Path, name: str) -> None:
    for src_name in ("RULES.md", "VEHICLES.md"):
        src = PROJECT_ROOT / src_name
        if src.is_file():
            shutil.copy2(src, dest / src_name)
        else:
            (dest / src_name).write_text(f"# {src_name}\n\nEdit this file for ruleset '{name}'.\n", encoding="utf-8")
    (dest / "ruleset.json").write_text(
        json.dumps({"name": name, "description": "Default GoCommuter ruleset."}, indent=2),
        encoding="utf-8",
    )
    _migrate_ruleset_json_catalog(dest)


def _migrate_ruleset_json_catalog(dest: Path) -> None:
    """Ensure vehicles.json and events.json contain the full built-in catalogs."""
    from ruleset_catalog import (
        builtin_events_data,
        builtin_vehicles_data,
        format_events_json,
        format_vehicles_json,
        parse_events_json,
        parse_vehicles_json,
    )

    vpath = dest / "vehicles.json"
    need_vehicles = True
    if vpath.is_file():
        try:
            if _vehicles_json_has_catalog(parse_vehicles_json(vpath.read_text(encoding="utf-8"))):
                need_vehicles = False
        except Exception:
            pass
    if need_vehicles:
        vpath.write_text(format_vehicles_json(builtin_vehicles_data()), encoding="utf-8")

    epath = dest / "events.json"
    need_events = True
    if epath.is_file():
        try:
            raw = json.loads(epath.read_text(encoding="utf-8"))
            data = raw if isinstance(raw, dict) and "events" in raw else parse_events_json(epath.read_text(encoding="utf-8"))
            if _events_json_is_full_catalog(data):
                need_events = False
        except Exception:
            pass
    if need_events:
        epath.write_text(format_events_json(builtin_events_data()), encoding="utf-8")

    from ruleset_catalog import (
        builtin_organizations_data,
        format_organizations_json,
    )

    opath = dest / "organizations.json"
    if not opath.is_file():
        opath.write_text(format_organizations_json(builtin_organizations_data()), encoding="utf-8")

    from ruleset_catalog import builtin_news_data, format_news_json

    npath = dest / "news.json"
    if not npath.is_file():
        npath.write_text(format_news_json(builtin_news_data()), encoding="utf-8")


def read_rules_markdown(ruleset_name: Optional[str] = None) -> str:
    path = get_rules_path(ruleset_name)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "# Game Rules\n\nNo RULES.md found for this ruleset.\n"


def read_vehicles_markdown(ruleset_name: Optional[str] = None) -> str:
    path = get_vehicles_md_path(ruleset_name)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "# Vehicles\n\nNo VEHICLES.md found for this ruleset.\n"


def save_rules_markdown(content: str, ruleset_name: Optional[str] = None) -> Path:
    path = get_rules_path(ruleset_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def save_vehicles_markdown(content: str, ruleset_name: Optional[str] = None) -> Path:
    path = get_vehicles_md_path(ruleset_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def regenerate_vehicles_md(ruleset_name: Optional[str] = None) -> Path:
    """Regenerate VEHICLES.md from the active vehicle registry."""
    from vehicle_presets import write_vehicles_md

    name = ruleset_name or get_active_ruleset()
    activate_vehicle_registry_for_ruleset(name)
    path = get_vehicles_md_path(name)
    write_vehicles_md(str(path))
    return path


def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def activate_vehicle_registry_for_ruleset(ruleset_name: Optional[str] = None) -> None:
    """Load vehicles from ruleset vehicles.json, else legacy vehicles.py, else built-in presets."""
    from ruleset_catalog import parse_vehicles_json, vehicles_data_to_registry
    from vehicle_presets import (
        reset_vehicle_registry,
        set_vehicle_registry,
        set_vehicle_registry_from_module,
    )

    name = ruleset_name or get_active_ruleset()
    ruleset_dir = get_ruleset_dir(name)
    reset_vehicle_registry()
    vehicles_json = ruleset_dir / "vehicles.json"
    if vehicles_json.is_file():
        try:
            data = parse_vehicles_json(vehicles_json.read_text(encoding="utf-8"))
            if _vehicles_json_has_catalog(data):
                set_vehicle_registry(vehicles_data_to_registry(data))
                return
        except Exception as e:
            print(f"Warning: could not load {vehicles_json}: {e}")
    vehicles_py = ruleset_dir / "vehicles.py"
    if vehicles_py.is_file():
        mod = _load_module_from_path(f"ruleset_vehicles_{name}", vehicles_py)
        set_vehicle_registry_from_module(mod)
        return
    import vehicle_presets as vp

    set_vehicle_registry_from_module(vp)


def load_ruleset_events(ruleset_name: Optional[str] = None) -> tuple[str, list]:
    """Load ruleset timeline events.

    Returns ('replace', events) when events.json has version (full catalog),
    ('append', events) for legacy partial JSON, or ('none', []) when absent.
    """
    from ruleset_catalog import events_data_to_list, parse_events_json

    name = ruleset_name or get_active_ruleset()
    events_json = get_events_json_path(name)
    if not events_json.is_file():
        return ("none", [])

    try:
        data = parse_events_json(events_json.read_text(encoding="utf-8"))
        raw_events = data.get("events") or []
        if not raw_events:
            return ("none", [])
        parsed = events_data_to_list(data)
        if _events_json_is_full_catalog(data):
            return ("replace", parsed)
        return ("append", parsed)
    except Exception as e:
        print(f"Warning: could not load {events_json}: {e}")
        return ("none", [])


def _load_legacy_events_py_extras(ruleset_name: str) -> list:
    """Optional extras from legacy ruleset events.py (deprecated)."""
    name = ruleset_name or get_active_ruleset()
    events_py = get_ruleset_dir(name) / "events.py"
    if not events_py.is_file():
        return []
    try:
        mod = _load_module_from_path(f"ruleset_events_{name}", events_py)
        if hasattr(mod, "get_ruleset_events"):
            extra = mod.get_ruleset_events()
            return list(extra) if extra else []
    except Exception as e:
        print(f"Warning: could not load {events_py}: {e}")
    return []


def apply_ruleset_to_new_game(ruleset_name: Optional[str] = None) -> str:
    """Prepare vehicle registry for a new game. Returns ruleset name used."""
    ensure_rulesets_layout()
    name = ruleset_name or get_active_ruleset()
    activate_vehicle_registry_for_ruleset(name)
    return name


def apply_ruleset_events_to_game(game, ruleset_name: Optional[str] = None) -> None:
    if game is None or not hasattr(game, "event_system"):
        return
    mode, events = load_ruleset_events(ruleset_name)
    name = ruleset_name or get_active_ruleset()
    if mode == "replace":
        # The ruleset's catalogue replaces the global events; the map's own events stay.
        from map_preset_loader import get_preset_events
        game_map = getattr(game, 'map', None)
        map_events = get_preset_events(getattr(game_map, 'map_preset_name', None)) if game_map else []
        game.event_system.events = list(events) + list(map_events)
    elif mode == "append":
        for event in events:
            game.event_system.add_event(event)
    for event in _load_legacy_events_py_extras(name):
        game.event_system.add_event(event)
