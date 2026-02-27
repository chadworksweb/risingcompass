"""Claude classification engine for song rubric analysis."""

import json
import logging

from anthropic import Anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CompassSong
from app.services.agents.compass_agent_rubric import (
    build_few_shot_examples,
    build_classification_prompt,
)

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

VALID_COLORS = {"violet", "blue", "green", "orange", "red"}


def _lookup_existing(title: str, artist: str, db: Session) -> dict | None:
    """Check if a song already exists in the CompassSong table with a calibrated classification.

    Returns the existing classification dict if found, None otherwise.
    Only returns songs that are explicitly marked as calibrated (human-reviewed).
    """
    # Match on title + artist (case-insensitive)
    existing = (
        db.query(CompassSong)
        .filter(CompassSong.title.ilike(title))
        .filter(func.lower(CompassSong.artist) == artist.lower())
        .filter(CompassSong.calibrated == True)
        .order_by(CompassSong.id.desc())  # most recent calibration wins
        .first()
    )
    if existing:
        logger.info("Using existing calibration for '%s' by %s: %s %s",
                     title, artist, existing.rubric_color, existing.charge_value)
        return {
            "rubric_color": existing.rubric_color,
            "charge_value": existing.charge_value,
            "contaminated": existing.contaminated,
            "contamination_note": existing.contamination_note,
            "charge_summary": existing.charge_summary,
            "confidence": 1.0,  # human-calibrated = full confidence
        }
    return None


def classify_song(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
    target_year: int | None = None,
    skip_cache: bool = False,
) -> dict:
    """Classify a single song using the Rising Compass rubric via Claude.

    If the song already exists in the CompassSong table with a calibrated charge_value,
    returns the existing classification instead of reclassifying.
    Set skip_cache=True to force reclassification (backfill mode) while still
    using the db for few-shot examples.

    Returns a dict with rubric_color, charge_value, contaminated, contamination_note,
    charge_summary, confidence.
    """
    # Check for existing calibrated classification first
    if db and not skip_cache:
        existing = _lookup_existing(title, artist, db)
        if existing:
            return existing

    client = Anthropic(api_key=settings.anthropic_api_key)

    # Build few-shot examples from existing data
    examples = build_few_shot_examples(db, target_year=target_year) if db else ""

    system_prompt, user_prompt = build_classification_prompt(
        title, artist, lyrics=lyrics, examples=examples
    )

    response = client.messages.create(
        model=AGENT_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if Claude wraps the JSON
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response for %s by %s: %s", title, artist, raw)
        return _fallback_result(title, artist, raw)

    # Validate rubric_color
    if result.get("rubric_color") not in VALID_COLORS:
        result["rubric_color"] = "green"

    # Red and orange cannot be contaminated — they are inherently low-frequency
    color = result.get("rubric_color", "green")
    contaminated = bool(result.get("contaminated", False))
    if color in ("red", "orange"):
        contaminated = False
        result["contamination_note"] = None

    # Validate and clamp charge_value to tier range
    charge_value = _validate_charge_value(result.get("charge_value"), color)

    # Ensure all expected keys exist
    return {
        "rubric_color": color,
        "charge_value": charge_value,
        "contaminated": contaminated,
        "contamination_note": result.get("contamination_note"),
        "charge_summary": result.get("charge_summary", ""),
        "confidence": float(result.get("confidence", 0.5)),
    }


# Tier ranges for charge_value validation
TIER_RANGES = {
    "violet": (75, 100),
    "blue": (25, 74),
    "green": (-24, 24),
    "orange": (-74, -25),
    "red": (-100, -75),
}

# Default midpoints per tier (used when charge_value is missing)
TIER_MIDPOINTS = {
    "violet": 88,
    "blue": 50,
    "green": 0,
    "orange": -50,
    "red": -88,
}


def _validate_charge_value(raw_value, color: str) -> int:
    """Validate charge_value falls within the tier's range, or assign midpoint."""
    low, high = TIER_RANGES.get(color, (-24, 24))
    midpoint = TIER_MIDPOINTS.get(color, 0)

    if raw_value is None:
        return midpoint

    try:
        val = int(raw_value)
    except (TypeError, ValueError):
        return midpoint

    # Clamp to tier range
    return max(low, min(high, val))


def _fallback_result(title: str, artist: str, raw_response: str) -> dict:
    """Return an explicit failure when Claude's response can't be parsed.

    rubric_color=None signals the song needs human intervention rather than
    silently defaulting to green/0.
    """
    return {
        "rubric_color": None,
        "charge_value": None,
        "contaminated": False,
        "contamination_note": None,
        "charge_summary": f"Classification failed — manual review needed for {title} by {artist}",
        "confidence": 0.0,
    }
