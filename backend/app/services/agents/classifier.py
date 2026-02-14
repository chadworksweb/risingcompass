"""Claude classification engine for song rubric analysis."""

import json
import logging

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.services.agents.prompts import (
    build_few_shot_examples,
    build_classification_prompt,
)

logger = logging.getLogger(__name__)

AGENT_MODEL = "claude-sonnet-4-5-20250929"

VALID_COLORS = {"bright_green", "green", "yellow", "orange", "red"}


def classify_song(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
) -> dict:
    """Classify a single song using the Rising Compass rubric via Claude.

    Returns a dict with rubric_color, contaminated, contamination_note,
    charge_summary, message_analysis, expression_analysis, intention_analysis, confidence.
    """
    client = Anthropic(api_key=settings.anthropic_api_key)

    # Build few-shot examples from existing data
    examples = build_few_shot_examples(db) if db else ""

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
        result["rubric_color"] = "yellow"

    # Red and orange cannot be contaminated — they are inherently low-frequency
    color = result.get("rubric_color", "yellow")
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
        "message_analysis": result.get("message_analysis", ""),
        "expression_analysis": result.get("expression_analysis", ""),
        "intention_analysis": result.get("intention_analysis", ""),
        "confidence": float(result.get("confidence", 0.5)),
    }


# Tier ranges for charge_value validation
TIER_RANGES = {
    "bright_green": (75, 100),
    "green": (25, 74),
    "yellow": (-24, 24),
    "orange": (-74, -25),
    "red": (-100, -75),
}

# Default midpoints per tier (used when charge_value is missing)
TIER_MIDPOINTS = {
    "bright_green": 88,
    "green": 50,
    "yellow": 0,
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
    """Return a safe fallback when Claude's response can't be parsed."""
    return {
        "rubric_color": "yellow",
        "charge_value": 0,
        "contaminated": False,
        "contamination_note": None,
        "charge_summary": f"Classification failed — manual review needed for {title} by {artist}",
        "message_analysis": None,
        "expression_analysis": None,
        "intention_analysis": None,
        "confidence": 0.0,
    }
