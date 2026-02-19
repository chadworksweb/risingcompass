"""Compass degree calculation from song charge values and chart positions."""

# Legacy fixed degrees per color (used when charge_value is missing)
COLOR_DEGREES = {
    "violet": 0.0,
    "blue": 45.0,
    "green": 90.0,
    "yellow": 135.0,
    "red": 180.0,
}

# Midpoint charge_values per tier (for legacy conversion)
TIER_MIDPOINTS = {
    "violet": 88,
    "blue": 50,
    "green": 0,
    "yellow": -50,
    "red": -88,
}


def charge_to_degree(charge_value: int) -> float:
    """Convert a charge_value (+100 to -100) to internal degree (0 to 180)."""
    return round(90.0 - (charge_value * 0.9), 1)


def degree_to_charge(degree: float) -> int:
    """Convert internal degree (0-180) to charge_value (+100 to -100)."""
    return round((90.0 - degree) * 100.0 / 90.0)


def position_weight(position: int, total: int = 20) -> int:
    """Higher chart position = more weight. Position 1 → total, position N → 1."""
    return max(1, total + 1 - position)


def compute_degree(songs: list[dict]) -> float:
    """
    Compute weighted average compass degree from a list of songs.

    Uses charge_value when available (precise per-song scoring).
    Falls back to fixed degrees per color for legacy data.

    Each song dict needs 'rubric_color' and 'position' (or 'chart_position').
    Optionally 'charge_value' for precise scoring.

    Returns degree 0-180.
    """
    if not songs:
        return 90.0  # neutral if no data

    total_weight = 0
    weighted_sum = 0.0
    total = len(songs)

    for song in songs:
        pos = song.get("chart_position") or song.get("position", 5)
        w = position_weight(pos, total)

        # Use charge_value if available, otherwise fall back to fixed color degree
        cv = song.get("charge_value")
        if cv is not None:
            deg = charge_to_degree(cv)
        else:
            color = song.get("rubric_color", "green")
            deg = COLOR_DEGREES.get(color, 90.0)

        weighted_sum += deg * w
        total_weight += w

    if total_weight == 0:
        return 90.0

    return round(weighted_sum / total_weight, 1)


def compute_live_year_degree(songs: list[dict]) -> float:
    """
    Compute weighted average compass degree for a live year (2026+).

    Each song dict needs:
      - 'charge_value': the charge from its most recent appearance
      - 'effective_weight': sum of position_weight() across all daily appearances

    Songs with higher effective_weight (more appearances at higher positions)
    have more influence on the year aggregate.

    Returns degree 0-180.
    """
    if not songs:
        return 90.0

    total_weight = 0
    weighted_sum = 0.0

    for song in songs:
        cv = song.get("charge_value")
        w = song.get("effective_weight", 1)

        if cv is not None:
            deg = charge_to_degree(cv)
        else:
            color = song.get("rubric_color", "green")
            deg = COLOR_DEGREES.get(color, 90.0)

        weighted_sum += deg * w
        total_weight += w

    if total_weight == 0:
        return 90.0

    return round(weighted_sum / total_weight, 1)
