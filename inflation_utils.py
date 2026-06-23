"""Shared inflation calculation used across the game (taxes, lobby, damages, prices, etc.)."""

DEFAULT_START_YEAR = 1830
DEFAULT_END_YEAR = 2050


def get_inflation_factor(
    year: int,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    min_factor: float = 1.0,
    max_factor: float = 10.0,
) -> float:
    """Single place for inflation: min_factor at start_year, scales linearly to max_factor by end_year.
    Clamped to [min_factor, max_factor]."""
    if end_year <= start_year:
        return min_factor
    factor = min_factor + (year - start_year) * (max_factor - min_factor) / (end_year - start_year)
    return max(min_factor, min(max_factor, factor))
