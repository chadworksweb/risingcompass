"""Compass degree conversion utilities — charge level and score."""

CHARGE_TIERS = [
    (22.5, "violet", "Ascended"),
    (67.5, "blue", "Elevated"),
    (112.5, "green", "Decent"),
    (157.5, "orange", "Degraded"),
    (180.0, "red", "Corrupted"),
]


def degree_to_charge(degree: float) -> str:
    """Return charge level color string for a given degree (0-180)."""
    for threshold, color, _label in CHARGE_TIERS:
        if degree <= threshold:
            return color
    return "red"


def degree_to_score(degree: float) -> int:
    """Convert compass degree (0-180) to charge score (+100 to -100)."""
    return round((90 - degree) * 100 / 90)


def degree_to_score_display(degree: float) -> str:
    """Convert compass degree to display string with +/- prefix."""
    score = degree_to_score(degree)
    return f"{'+' if score > 0 else ''}{score}"
