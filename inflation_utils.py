"""Shared inflation calculation used across the game (taxes, lobby, damages, prices, etc.)."""

DEFAULT_START_YEAR = 1830
DEFAULT_END_YEAR = 2050

# Game-level override: when set (by Game.inflation_max_factor), scales all max_factor values.
# E.g. 0.5 = half inflation (5x by 2050), 2.0 = double (20x by 2050). Default 1.0 = 10x.
_inflation_rate_multiplier = 1.0


def set_inflation_rate_multiplier(multiplier: float) -> None:
    """Set inflation rate multiplier for current game. 1.0 = default (10x by 2050)."""
    global _inflation_rate_multiplier
    _inflation_rate_multiplier = max(0.1, min(5.0, multiplier))


def clear_inflation_rate_multiplier() -> None:
    """Reset to default when game ends."""
    global _inflation_rate_multiplier
    _inflation_rate_multiplier = 1.0


def get_inflation_factor(
    year: int,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    min_factor: float = 1.0,
    max_factor: float = 10.0,
) -> float:
    """Single place for inflation: min_factor at start_year, scales linearly to max_factor by end_year.
    Clamped to [min_factor, max_factor]. Uses _inflation_rate_multiplier when set by game."""
    if end_year <= start_year:
        return min_factor
    effective_max = max_factor * _inflation_rate_multiplier
    factor = min_factor + (year - start_year) * (effective_max - min_factor) / (end_year - start_year)
    return max(min_factor, min(effective_max, factor))


def inflate_economic_value(
    base_amount: float,
    year: int,
    *,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    max_factor: float = 10.0,
) -> int:
    """Scale a base-year dollar amount to nominal currency for ``year``."""
    factor = get_inflation_factor(
        int(year), start_year=start_year, end_year=end_year, max_factor=max_factor,
    )
    return max(1, int(round(float(base_amount) * factor)))


def get_nominal_fare_cap(
    year: int,
    *,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    min_real_fare: float = 2.0,
    max_real_fare: float = 15.0,
    inflation_max_factor: float = 5.5,
) -> tuple[float, float]:
    """Nominal min/max acceptable fare ($2 floor; ceiling rises to $15 by end_year).

    Fares are not inflation-scaled — only player/AI fare changes move the charged amount.
    ``inflation_max_factor`` is accepted for API compatibility and ignored.
    """
    _ = inflation_max_factor
    if end_year <= start_year:
        return min_real_fare, max_real_fare
    span = float(end_year - start_year)
    max_fare = min_real_fare + (float(year) - float(start_year)) * (max_real_fare - min_real_fare) / span
    max_fare = max(min_real_fare, min(max_real_fare, max_fare))
    return min_real_fare, max_fare


FARE_QUARTER_DOLLARS = 0.25


def round_fare_to_quarter(dollars: float) -> float:
    """Round a fare to the nearest $0.25 increment."""
    q = round(float(dollars) / FARE_QUARTER_DOLLARS)
    return round(q * FARE_QUARTER_DOLLARS, 2)


def format_fare_display(dollars: float) -> str:
    """Format a fare for UI labels ($2, $2.25, etc.)."""
    fare = round_fare_to_quarter(dollars)
    if abs(fare - round(fare)) < 1e-9:
        return f"${int(fare)}"
    return f"${fare:.2f}"


def get_timeline_fare_target(
    year: int,
    *,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
) -> int:
    """Nominal fare for a calendar year: low at game start, rising toward the timeline cap."""
    _, max_fare = get_nominal_fare_cap(year, start_year=start_year, end_year=end_year)
    return round_fare_to_quarter(max_fare)
