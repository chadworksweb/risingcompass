"""Contamination logic — counting and enforcement rules."""


def count_contaminated(songs: list[dict]) -> int:
    """Count songs with contaminated=True."""
    return sum(1 for s in songs if s.get("contaminated", False))


def enforce_contamination_rule(song: dict) -> dict:
    """Red/orange songs cannot be contaminated — they are inherently low-frequency.

    Mutates and returns the dict for convenience.
    """
    if song.get("rubric_color") in ("red", "orange"):
        song["contaminated"] = False
        song["contamination_note"] = None
    return song
