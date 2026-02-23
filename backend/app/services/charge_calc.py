"""Map compass degree to charge level tier."""

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


def degree_to_label(degree: float) -> str:
    """Return human-readable charge label for a given degree."""
    for threshold, _color, label in CHARGE_TIERS:
        if degree <= threshold:
            return label
    return "Corrupted"


def charge_color_to_label(color: str) -> str:
    """Convert a charge color to its label."""
    labels = {
        "violet": "Ascended",
        "blue": "Elevated",
        "green": "Decent",
        "orange": "Degraded",
        "red": "Corrupted",
    }
    return labels.get(color, "Unknown")
