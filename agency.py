"""Agency system for non-player entities that can sue players."""
import random
from typing import List, Optional

from lawsuit import LawsuitExcuse, LAWSUIT_EXCUSES


class Agency:
    """Represents a non-player agency that can file lawsuits."""

    def __init__(
        self,
        name: str,
        activation_year: int,
        description: str = "",
        excuse_causes: Optional[List[str]] = None,
    ):
        self.name = name
        self.activation_year = activation_year
        self.description = description
        self.excuse_causes = excuse_causes if excuse_causes is not None else ["*"]
        self.lawyers = 0

    @classmethod
    def from_dict(cls, data: dict) -> "Agency":
        causes = data.get("excuse_causes", ["*"])
        if isinstance(causes, str):
            causes = [c.strip() for c in causes.split(",") if c.strip()]
        return cls(
            name=str(data.get("name", "")).strip(),
            activation_year=int(data.get("activation_year", 1830)),
            description=str(data.get("description", "")),
            excuse_causes=causes or ["*"],
        )

    def is_active(self, current_year: int) -> bool:
        return current_year >= self.activation_year

    def get_lawyers(self) -> int:
        if self.lawyers == 0:
            self.lawyers = random.randint(1, 5)
        return self.lawyers

    def reset_lawyers(self):
        self.lawyers = 0

    def _may_sue_for_cause(self, cause: str) -> bool:
        if "*" in self.excuse_causes or "all" in self.excuse_causes:
            return True
        return cause in self.excuse_causes

    def can_sue_for(self, excuse: LawsuitExcuse, current_year: int) -> bool:
        if not self.is_active(current_year):
            return False
        return self._may_sue_for_cause(excuse.cause)

    def get_available_excuses(self, current_year: int) -> list:
        if not self.is_active(current_year):
            return []
        return [e for e in LAWSUIT_EXCUSES if self.can_sue_for(e, current_year)]


_BUILTIN_AGENCIES = [
    Agency("NIMBYs", 1890, "Not In My Backyard activists - can sue for any reason", ["*"]),
    Agency(
        "EPA",
        1970,
        "Environmental Protection Agency - can sue for environmental damage",
        ["Environmental Damage"],
    ),
]

_agencies_cache: dict = {}


def get_agencies(ruleset_name: Optional[str] = None) -> List[Agency]:
    """Agencies for the active or named ruleset (cached per ruleset name)."""
    cache_key = ruleset_name or "__active__"
    if cache_key in _agencies_cache:
        return _agencies_cache[cache_key]
    try:
        from ruleset_loader import get_active_ruleset, load_organizations_for_ruleset
        name = ruleset_name or get_active_ruleset()
        org = load_organizations_for_ruleset(name)
        raw = org.get("agencies") or []
        agencies = [Agency.from_dict(d) for d in raw if d.get("name")]
        if not agencies:
            agencies = list(_BUILTIN_AGENCIES)
    except Exception:
        agencies = list(_BUILTIN_AGENCIES)
    _agencies_cache[cache_key] = agencies
    return agencies


def get_agency_by_name(name: str, ruleset_name: Optional[str] = None) -> Optional[Agency]:
    for agency in get_agencies(ruleset_name):
        if agency.name == name:
            return agency
    return None


def clear_agencies_cache() -> None:
    _agencies_cache.clear()


EPA = _BUILTIN_AGENCIES[1]
NIMBYs = _BUILTIN_AGENCIES[0]
AGENCIES = _BUILTIN_AGENCIES
