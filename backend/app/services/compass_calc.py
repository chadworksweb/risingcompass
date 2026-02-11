"""Compass degree calculation from song colors and chart positions."""

COLOR_DEGREES = {
    "bright_green": 0.0,
    "green": 45.0,
    "neutral": 90.0,
    "yellow": 90.0,
    "orange": 135.0,
    "red": 180.0,
}


def position_weight(position: int) -> int:
    """Higher chart position = more weight. Position 1 → 10, position 10 → 1."""
    return max(1, 11 - position)


def compute_degree(songs: list[dict]) -> float:
    """
    Compute weighted average compass degree from a list of songs.
    Each song dict needs 'rubric_color' and 'chart_position'.
    Returns degree 0-180.
    """
    if not songs:
        return 90.0  # neutral if no data

    total_weight = 0
    weighted_sum = 0.0

    for song in songs:
        color = song.get("rubric_color", "yellow")
        pos = song.get("chart_position") or song.get("position", 5)
        deg = COLOR_DEGREES.get(color, 90.0)
        w = position_weight(pos)
        weighted_sum += deg * w
        total_weight += w

    if total_weight == 0:
        return 90.0

    return round(weighted_sum / total_weight, 1)
