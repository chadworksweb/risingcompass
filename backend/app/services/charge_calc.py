"""Compass degree conversion utilities — charge level and score."""

CHARGE_TIERS = [
    (22.5, "violet", "Ascended"),
    (67.5, "blue", "Elevated"),
    (112.5, "green", "Decent"),
    (157.5, "orange", "Degraded"),
    (180.0, "red", "Corrupted"),
]


def degree_to_charge(degree: float) -> str:
    """Return charge level color string for a given degree (0-180).

    Symmetric about the neutral center (degree 90 = score 0): a boundary score
    rounds toward the MORE EXTREME tier on both sides, so +25 -> Elevated mirrors
    -25 -> Degraded, and +75 -> Ascended mirrors -75 -> Corrupted. Decent is the
    symmetric center (-24 to +24). The positive thresholds are inclusive (<=) and
    the negative ones exclusive (<) -- that asymmetry in the comparison is exactly
    what makes the tier assignment symmetric in score.
    """
    if degree <= 22.5:
        return "violet"   # Ascended  (+75 to +100)
    if degree <= 67.5:
        return "blue"     # Elevated  (+25 to +74)
    if degree < 112.5:
        return "green"    # Decent    (-24 to +24)
    if degree < 157.5:
        return "orange"   # Degraded  (-25 to -74)
    return "red"          # Corrupted (-75 to -100)


def degree_to_score(degree: float) -> int:
    """Convert compass degree (0-180) to charge score (+100 to -100)."""
    return round((90 - degree) * 100 / 90)


def degree_to_score_display(degree: float) -> str:
    """Convert compass degree to display string with +/- prefix."""
    score = degree_to_score(degree)
    return f"{'+' if score > 0 else ''}{score}"
