"""Recalibration engine — re-reads a song's lyrics through a hypothesis lens.

Currently supports the satire lens (loaded from tenets/satire.md). The lens
is appended to the standard rubric so the agent applies the full 58-tenet
calibration first, then re-reads under the lens. Returns a proposal dict —
does not write anything to the database. Persistence + admin review is
handled by the admin recalibration router.
"""

import json
import logging
import os
import re

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.services.agents.calibrator import (
    VALID_COLORS, TIER_RANGES, TIER_MIDPOINTS, _validate_charge_value,
)
from app.services.agents.compass_agent_rubric import (
    RUBRIC_DEFINITION, CALIBRATION_FORMAT, build_few_shot_examples,
)
from app.services.contamination import enforce_contamination_rule

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

TENETS_DIR = os.path.join(os.path.dirname(__file__), "tenets")


def _load_tenet(name: str) -> str:
    path = os.path.join(TENETS_DIR, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


SATIRE_TENETS = _load_tenet("satire")


def _build_satire_prompt(
    title: str,
    artist: str,
    lyrics: str,
    examples: str,
    original_color: str | None,
    original_charge: int | None,
    original_summary: str | None,
) -> tuple[str, str]:
    """Compose the prompt for a satire recalibration.

    The standard rubric is loaded first so the agent has its full calibration
    framework. The satire tenets are appended as a parallel reading lens. The
    calibration format (which describes the JSON output) comes last but is
    extended to require the satire-specific reasoning fields.
    """
    system_parts = [
        RUBRIC_DEFINITION,
        examples,
        "\n---\n# SATIRE RECALIBRATION LENS — APPLIES TO THIS SONG ONLY\n\n",
        "An admin has verified a satirical flag on this song. You are now performing a recalibration through the satire lens defined below. The standard rubric above still applies. The satire lens sits alongside it as a parallel reading framework. Apply the standard rubric first, then re-read through the lens.\n\n",
        SATIRE_TENETS,
        "\n---\n",
        CALIBRATION_FORMAT,
    ]
    system_prompt = "\n".join(system_parts)

    user_parts = [
        f'Recalibrate this song through the satire lens: "{title}" by {artist}',
        "",
        "Original literal calibration (your starting point — the calibration produced before the satire flag was raised):",
    ]
    if original_color:
        chg = original_charge if original_charge is not None else "unknown"
        user_parts.append(f'  Tier: {original_color} | Charge: {chg}')
    if original_summary:
        user_parts.append(f'  Summary: "{original_summary}"')
    user_parts.append("")
    user_parts.append("Lyrics:")
    user_parts.append(lyrics)
    user_parts.append("")
    user_parts.append(
        "Run the satire recalibration procedure exactly as described in the satire tenets. "
        "Output the required reasoning fields (LITERAL_SUMMARY, FLIPPED_SUMMARY_TEST, "
        "MODE_BREAKDOWN, SATIRE_READING, CEILING_CHECK) BEFORE the JSON. Then produce the JSON. "
        "If the satire reading does not hold, return the literal calibration unchanged with a "
        "rationale explaining why."
    )
    user_prompt = "\n".join(user_parts)

    return system_prompt, user_prompt


def recalibrate_song_satire(
    title: str,
    artist: str,
    lyrics: str,
    db: Session,
    original_color: str | None = None,
    original_charge: int | None = None,
    original_summary: str | None = None,
) -> dict:
    """Run the satire recalibration. Returns a proposal dict — caller persists.

    Output dict:
      rubric_color, charge_value, contaminated, contamination_note, charge_summary,
      confidence, ai_rationale, ai_model, satire_reading_held (bool inferred from
      whether the proposed calibration differs from the original).
    """
    if not lyrics or not lyrics.strip():
        raise ValueError("Lyrics are required for satire recalibration.")

    client = Anthropic(api_key=settings.anthropic_api_key)
    examples = build_few_shot_examples(db) if db else ""

    system_prompt, user_prompt = _build_satire_prompt(
        title, artist, lyrics, examples,
        original_color, original_charge, original_summary,
    )

    response = client.messages.create(
        model=AGENT_MODEL,
        max_tokens=3072,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    reasoning = ""
    json_str = raw
    brace_idx = raw.find("{")
    if brace_idx > 0:
        reasoning = raw[:brace_idx].strip()
        json_str = raw[brace_idx:]

    if reasoning:
        logger.info("Satire recalibrator reasoning for '%s' by %s:\n%s", title, artist, reasoning)

    if json_str.rstrip().endswith("```"):
        json_str = json_str.rstrip()[:-3]
    json_str = json_str.strip()

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        logger.error("Failed to parse satire recalibration response for %s by %s: %s",
                     title, artist, raw)
        raise RuntimeError("Satire recalibration agent returned unparseable output.")

    if result.get("rubric_color") not in VALID_COLORS:
        result["rubric_color"] = original_color or "green"

    enforce_contamination_rule(result)
    color = result.get("rubric_color")
    contaminated = bool(result.get("contaminated", False))
    charge_value = _validate_charge_value(result.get("charge_value"), color)

    return {
        "rubric_color": color,
        "charge_value": charge_value,
        "contaminated": contaminated,
        "contamination_note": result.get("contamination_note"),
        "charge_summary": result.get("charge_summary", ""),
        "confidence": float(result.get("confidence", 0.5)),
        "ai_rationale": reasoning,
        "ai_model": AGENT_MODEL,
        "satire_reading_held": (
            color != original_color or charge_value != original_charge
        ) if original_color else True,
    }
