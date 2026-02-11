"""Contamination counter — simple sum of contaminated flags."""


def count_contaminated(songs: list[dict]) -> int:
    """Count songs with contaminated=True."""
    return sum(1 for s in songs if s.get("contaminated", False))
