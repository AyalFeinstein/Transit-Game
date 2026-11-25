"""Serialize and deserialize ruleset vehicles and events as JSON for the ruleset editor."""

from __future__ import annotations

import json
from typing import Any, Optional

from game_resource import Coal, Electricity, Food, Gasoline, Oil, Wood
from vehicle import Bus, Ferry, Locomotive, Railcar

_FUEL_BY_NAME = {
    "Coal": Coal,
    "Wood": Wood,
    "Oil": Oil,
    "Electricity": Electricity,
    "Food": Food,
    "Gasoline": Gasoline,
}


def _fuel_name(resource) -> str:
    if resource is None:
        return "Coal"
    cls = resource.__class__.__name__
    return cls if cls in _FUEL_BY_NAME else "Coal"


def _fuel_from_name(name: str):
    return _FUEL_BY_NAME.get(name, Coal)()


def _locomotive_to_dict(loco: Locomotive) -> dict:
    return {
        "kind": "locomotive",
        "name": loco.name,
        "object_id": loco.object_id,
        "fuel": _fuel_name(loco.fuel),
        "miles_per_unit": loco.miles_per_unit,
        "top_speed": loco.top_speed,
        "average_speed": loco.average_speed,
        "capacity": loco.capacity,
        "needs_third_rail": bool(loco.needs_third_rail),
        "needs_overhead_wire": bool(loco.needs_overhead_wire),
        "can_have_railcars": bool(loco.can_have_railcars),
        "max_cars": loco.max_cars,
        "associated_railcar_name": loco.associated_railcar_name,
        "quality": loco.quality,
        "maitnance_costs": loco.maitnance_costs,
        "year_introduced": loco.year_introduced,
        "base_price": loco.base_price,
        "obsolete_by": loco.obselete_by,
    }


def _railcar_to_dict(car: Railcar) -> dict:
    return {
        "kind": "railcar",
        "name": car.name,
        "object_id": car.object_id,
        "capacity": car.capacity,
        "maitnance_costs": car.maitnance_costs,
        "quality": car.quality,
        "year_introduced": car.year_introduced,
        "base_price": car.base_price,
        "obsolete_by": car.obselete_by,
    }


def _ferry_to_dict(ferry: Ferry) -> dict:
    return {
        "kind": "ferry",
        "name": ferry.name,
        "object_id": ferry.object_id,
        "fuel": _fuel_name(ferry.fuel),
        "miles_per_unit": ferry.miles_per_unit,
        "top_speed": ferry.top_speed,
        "average_speed": ferry.average_speed,
        "capacity": ferry.capacity,
        "quality": ferry.quality,
        "maitnance_costs": ferry.maitnance_costs,
        "year_introduced": ferry.year_introduced,
        "base_price": ferry.base_price,
        "obsolete_by": ferry.obselete_by,
    }


def _bus_to_dict(bus: Bus) -> dict:
    return {
        "kind": "bus",
        "name": bus.name,
        "object_id": bus.object_id,
        "fuel": _fuel_name(bus.fuel),
        "miles_per_unit": bus.miles_per_unit,
        "top_speed": bus.top_speed,
        "average_speed": bus.average_speed,
        "capacity": bus.capacity,
        "quality": bus.quality,
        "maitnance_costs": bus.maitnance_costs,
        "year_introduced": bus.year_introduced,
        "base_price": bus.base_price,
        "obsolete_by": bus.obselete_by,
    }


def registry_to_vehicles_data(registry) -> dict:
    """Export a VehicleRegistry to a JSON-serializable document."""
    return {
        "version": 1,
        "locomotives": [_locomotive_to_dict(v) for v in registry.get_all_locomotives()],
        "railcars": [_railcar_to_dict(v) for v in registry.get_all_railcars()],
        "ferries": [_ferry_to_dict(v) for v in registry.get_all_ferries()],
        "buses": [_bus_to_dict(v) for v in registry.get_all_buses()],
    }


def _dict_to_locomotive(d: dict) -> Locomotive:
    return Locomotive(
        name=d["name"],
        fuel=_fuel_from_name(d.get("fuel", "Coal")),
        miles_per_unit=float(d.get("miles_per_unit", 0.1)),
        top_speed=float(d.get("top_speed", 30)),
        average_speed=float(d.get("average_speed", 20)),
        capacity=int(d.get("capacity", 0)),
        object_id=d.get("object_id"),
        needs_third_rail=bool(d.get("needs_third_rail", False)),
        needs_overhead_wire=bool(d.get("needs_overhead_wire", False)),
        maitnance_costs=int(d.get("maitnance_costs", 100)),
        year_introduced=d.get("year_introduced"),
        can_have_railcars=bool(d.get("can_have_railcars", True)),
        max_cars=d.get("max_cars"),
        associated_railcar_name=d.get("associated_railcar_name"),
        quality=float(d.get("quality", 1.0)),
        base_price=d.get("base_price"),
        obselete_by=d.get("obsolete_by"),
    )


def _dict_to_railcar(d: dict) -> Railcar:
    return Railcar(
        name=d["name"],
        object_id=int(d.get("object_id", 0)),
        capacity=int(d.get("capacity", 60)),
        maitnance_costs=int(d.get("maitnance_costs", 50)),
        quality=float(d.get("quality", 1.0)),
        year_introduced=d.get("year_introduced"),
        base_price=d.get("base_price"),
        obselete_by=d.get("obsolete_by"),
    )


def _dict_to_ferry(d: dict) -> Ferry:
    return Ferry(
        name=d["name"],
        fuel=_fuel_from_name(d.get("fuel", "Coal")),
        miles_per_unit=float(d.get("miles_per_unit", 0.1)),
        top_speed=float(d.get("top_speed", 10)),
        average_speed=float(d.get("average_speed", 8)),
        capacity=int(d.get("capacity", 100)),
        object_id=d.get("object_id"),
        maitnance_costs=int(d.get("maitnance_costs", 100)),
        year_introduced=d.get("year_introduced"),
        quality=float(d.get("quality", 1.0)),
        base_price=d.get("base_price"),
        obselete_by=d.get("obsolete_by"),
    )


def _dict_to_bus(d: dict) -> Bus:
    return Bus(
        name=d["name"],
        fuel=_fuel_from_name(d.get("fuel", "Gasoline")),
        miles_per_unit=float(d.get("miles_per_unit", 0.2)),
        top_speed=float(d.get("top_speed", 30)),
        average_speed=float(d.get("average_speed", 18)),
        capacity=int(d.get("capacity", 50)),
        object_id=d.get("object_id"),
        maitnance_costs=int(d.get("maitnance_costs", 100)),
        year_introduced=d.get("year_introduced"),
        quality=float(d.get("quality", 1.0)),
        base_price=d.get("base_price"),
        obselete_by=d.get("obsolete_by"),
    )


def vehicles_data_to_registry(data: dict):
    """Build a VehicleRegistry from a vehicles.json document."""
    from vehicle_presets import VehicleRegistry

    reg = VehicleRegistry.__new__(VehicleRegistry)
    reg._locomotives = []
    reg._railcars = []
    reg._ferries = []
    reg._buses = []
    for item in data.get("locomotives") or []:
        if isinstance(item, dict):
            reg._locomotives.append(_dict_to_locomotive(item))
    for item in data.get("railcars") or []:
        if isinstance(item, dict):
            reg._railcars.append(_dict_to_railcar(item))
    for item in data.get("ferries") or []:
        if isinstance(item, dict):
            reg._ferries.append(_dict_to_ferry(item))
    for item in data.get("buses") or []:
        if isinstance(item, dict):
            reg._buses.append(_dict_to_bus(item))
    return reg


def builtin_vehicles_data() -> dict:
    """Full vehicle catalog from shipped vehicle_presets.py."""
    import vehicle_presets as vp
    from vehicle_presets import VehicleRegistry

    return registry_to_vehicles_data(VehicleRegistry.from_module(vp))


def _event_instance_to_dict(event) -> dict:
    from events import (
        AutomobileInventionEvent,
        Covid19Event,
        EnvironmentalProtectionActEvent,
        GovernmentAutomobilePreferenceEvent,
        PopulationEvent,
        TrustBustingEvent,
        WorkerProtectionActEvent,
    )

    if isinstance(event, AutomobileInventionEvent):
        etype = "automobile"
    elif isinstance(event, Covid19Event):
        etype = "covid19"
    elif isinstance(event, WorkerProtectionActEvent):
        etype = "worker_protection"
    elif isinstance(event, TrustBustingEvent):
        etype = "trust_busting"
    elif isinstance(event, EnvironmentalProtectionActEvent):
        etype = "environmental_protection"
    elif isinstance(event, GovernmentAutomobilePreferenceEvent):
        etype = "government_automobile_preference"
    elif isinstance(event, PopulationEvent):
        etype = "population"
    else:
        etype = "generic"
    d = {
        "type": etype,
        "name": event.name,
        "year": event.year,
        "description": event.description,
    }
    if isinstance(event, PopulationEvent):
        d["change_percent"] = event.change_percent
    if isinstance(event, GovernmentAutomobilePreferenceEvent):
        d["end_year"] = getattr(event, "end_year", None)
    return d


def builtin_events_data() -> dict:
    """Default timeline events from events.EventSystem."""
    from events import EventSystem

    sys = EventSystem(natural_disaster_frequency=1.0)
    return {"version": 1, "events": [_event_instance_to_dict(e) for e in sys.events]}


def event_from_dict(d: dict):
    """Instantiate a GameEvent from a ruleset events.json entry."""
    from events import (
        AutomobileInventionEvent,
        Covid19Event,
        EnvironmentalProtectionActEvent,
        GameEvent,
        GovernmentAutomobilePreferenceEvent,
        PopulationEvent,
        TrustBustingEvent,
        WorkerProtectionActEvent,
    )

    etype = (d.get("type") or "generic").lower()
    if etype == "automobile":
        e = AutomobileInventionEvent()
    elif etype == "covid19":
        e = Covid19Event()
    elif etype == "worker_protection":
        e = WorkerProtectionActEvent()
    elif etype == "trust_busting":
        e = TrustBustingEvent()
    elif etype == "environmental_protection":
        e = EnvironmentalProtectionActEvent()
    elif etype == "government_automobile_preference":
        e = GovernmentAutomobilePreferenceEvent()
    elif etype == "population":
        return PopulationEvent(
            year=int(d.get("year", 1900)),
            change_percent=float(d.get("change_percent", 0)),
            description=d.get("description") or d.get("name") or "",
        )
    else:
        return GameEvent(
            name=d.get("name") or "Event",
            year=int(d.get("year", 1900)),
            description=d.get("description") or "",
        )
    e.name = d.get("name") or e.name
    e.year = int(d.get("year", e.year))
    e.description = d.get("description") or e.description
    if etype == "government_automobile_preference" and d.get("end_year") is not None:
        e.end_year = int(d["end_year"])
    return e


def events_data_to_list(data: dict) -> list:
    raw = data.get("events") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(event_from_dict(item))
            except Exception as e:
                print(f"Warning: skip event {item!r}: {e}")
    return out


def format_vehicles_json(data: dict) -> str:
    return json.dumps(data, indent=2)


def format_events_json(data: dict) -> str:
    return json.dumps(data, indent=2)


def parse_vehicles_json(text: str) -> dict:
    data = json.loads(text)
    if isinstance(data, list):
        return {"version": 1, "locomotives": [], "railcars": [], "ferries": [], "buses": []}
    if not isinstance(data, dict):
        raise ValueError("vehicles.json must be an object with locomotives, railcars, ferries, buses.")
    return data


def parse_events_json(text: str) -> dict:
    data = json.loads(text)
    if isinstance(data, list):
        return {"version": 1, "events": data}
    if not isinstance(data, dict):
        raise ValueError("events.json must be an object with an events array.")
    if "events" not in data:
        data = {"version": 1, "events": []}
    return data


def _parse_agencies_list(raw) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("organizations.json 'agencies' must be an array of objects.")
    agencies = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"organizations.json agencies[{i}] must be an object.")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"organizations.json agencies[{i}] requires a non-empty 'name'.")
        try:
            activation_year = int(item.get("activation_year", 1830))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"organizations.json agencies[{i}] 'activation_year' must be an integer."
            ) from exc
        description = str(item.get("description", "")).strip()
        causes_raw = item.get("excuse_causes", ["*"])
        if isinstance(causes_raw, str):
            excuse_causes = [c.strip() for c in causes_raw.split(",") if c.strip()]
        elif isinstance(causes_raw, list):
            excuse_causes = [str(c).strip() for c in causes_raw if str(c).strip()]
        else:
            raise ValueError(
                f"organizations.json agencies[{i}] 'excuse_causes' must be a string or array."
            )
        if not excuse_causes:
            excuse_causes = ["*"]
        agencies.append({
            "name": name,
            "activation_year": activation_year,
            "description": description,
            "excuse_causes": excuse_causes,
        })
    return agencies


def builtin_organizations_data() -> dict:
    """Default organizations preset: agencies, transit company names, optional default line names."""
    from transit_company_names import TRANSIT_NAMES_GENERIC

    return {
        "version": 1,
        "agencies": [
            {
                "name": "NIMBYs",
                "activation_year": 1890,
                "description": "Not In My Backyard activists — can sue over new construction.",
                "excuse_causes": ["*"],
            },
            {
                "name": "EPA",
                "activation_year": 1970,
                "description": "Environmental Protection Agency — environmental damage suits.",
                "excuse_causes": ["Environmental Damage"],
            },
        ],
        "transit_companies": list(TRANSIT_NAMES_GENERIC),
        "default_line_names": [],
    }


def parse_organizations_json(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("organizations.json must be a JSON object.")
    out = {
        "version": int(data.get("version", 1)),
        "agencies": _parse_agencies_list(data.get("agencies")),
        "transit_companies": [],
        "default_line_names": [],
    }
    for key in ("transit_companies", "default_line_names"):
        raw = data.get(key)
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise ValueError(f"organizations.json '{key}' must be an array of strings.")
        out[key] = [str(x).strip() for x in raw if str(x).strip()]
    if not out["agencies"]:
        out["agencies"] = list(builtin_organizations_data()["agencies"])
    return out


def format_organizations_json(data: dict) -> str:
    return json.dumps(data, indent=2)


def builtin_news_data() -> dict:
    """Default news article pools (economy, politics, oddities)."""
    from game_presets import (
        NEWS_ECONOMY_ARTICLES,
        NEWS_ODDITIES_ARTICLES,
        NEWS_POLITICS_ARTICLES,
    )

    economy = [[h, b] for (h, b) in NEWS_ECONOMY_ARTICLES]
    politics = [[h, b] for (h, b) in NEWS_POLITICS_ARTICLES]
    oddities = [[h, b, int(min_year)] for (h, b, min_year) in NEWS_ODDITIES_ARTICLES]
    return {"version": 1, "economy": economy, "politics": politics, "oddities": oddities}


def _parse_article_pairs(raw, label: str) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"news.json '{label}' must be an array.")
    out = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise ValueError(f"Each {label} entry needs [headline, body] or [headline, body, min_year].")
        headline = str(item[0]).strip()
        body = str(item[1]).strip()
        if not headline or not body:
            continue
        if label == "oddities":
            min_year = int(item[2]) if len(item) > 2 else 0
            out.append((headline, body, min_year))
        else:
            out.append((headline, body))
    return out


def parse_news_json(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("news.json must be a JSON object.")
    return {
        "version": int(data.get("version", 1)),
        "economy": _parse_article_pairs(data.get("economy"), "economy"),
        "politics": _parse_article_pairs(data.get("politics"), "politics"),
        "oddities": _parse_article_pairs(data.get("oddities"), "oddities"),
    }


def format_news_json(data: dict) -> str:
    payload = {
        "version": int(data.get("version", 1)),
        "economy": [[h, b] for (h, b) in data.get("economy") or []],
        "politics": [[h, b] for (h, b) in data.get("politics") or []],
        "oddities": [
            [h, b, int(y)] for (h, b, y) in data.get("oddities") or []
        ],
    }
    return json.dumps(payload, indent=2)


def news_pools_from_data(data: dict) -> dict:
    """Return tuple pools for operations news generation."""
    economy = tuple((h, b) for (h, b) in data.get("economy") or [])
    politics = tuple((h, b) for (h, b) in data.get("politics") or [])
    oddities = tuple(
        (h, b, int(y)) for (h, b, y) in data.get("oddities") or []
    )
    return {"economy": economy, "politics": politics, "oddities": oddities}
