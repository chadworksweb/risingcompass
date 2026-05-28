"""Claude calibration engine for song rubric analysis."""

import asyncio
import json
import logging
import re

from anthropic import AsyncAnthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CompassSong
from app.services.claude_meter import tracked_create_async
from app.services.contamination import enforce_contamination_rule
from app.services.agents.compass_agent_rubric import (
    build_few_shot_examples,
    build_calibration_prompt,
)

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

VALID_COLORS = {"violet", "blue", "green", "orange", "red"}


def lookup_calibrated(title: str, artist: str, db: Session) -> dict | None:
    """Look up an existing calibration from the CompassSong table.

    Case-insensitive match on title + artist. Returns calibration dict or None.
    A song is fully calibrated when it has rubric_color, charge_value, and charge_summary.
    Incomplete records (missing any of the three) return None so the song gets recalibrated.
    """
    existing = (
        db.query(CompassSong)
        .filter(func.lower(CompassSong.title) == title.lower())
        .filter(func.lower(CompassSong.artist) == artist.lower())
        .order_by(CompassSong.id.desc())
        .first()
    )

    # Fallback: match ignoring punctuation (apostrophes stripped by shell escaping)
    if not existing:
        stripped = re.sub(r"[^\w\s]", "", title.lower())
        candidates = (
            db.query(CompassSong)
            .filter(func.lower(CompassSong.artist) == artist.lower())
            .order_by(CompassSong.id.desc())
            .all()
        )
        for c in candidates:
            if re.sub(r"[^\w\s]", "", c.title.lower()) == stripped:
                existing = c
                break

    if not existing:
        return None
    if not existing.rubric_color or existing.charge_value is None or existing.charge_summary is None:
        logger.warning("Incomplete calibration for '%s' by %s (id=%s) — missing %s",
                       title, artist, existing.id,
                       ", ".join(f for f, v in [
                           ("rubric_color", existing.rubric_color),
                           ("charge_value", existing.charge_value),
                           ("charge_summary", existing.charge_summary),
                       ] if not v and v != 0))
        return None
    logger.info("Using cached calibration for '%s' by %s: %s %s",
                title, artist, existing.rubric_color, existing.charge_value)

    # Return the full stored object, including the generated fields (ether
    # tags + prose). Callers that hit cache can persist a complete calibration
    # without re-running the model; ensure_full_calibration() fills any that
    # are still NULL on older rows. topics / topic_audit are stored as JSON
    # strings; parse them back to the list / dict shape tag_song emits.
    return {
        "compass_song_id": existing.id,
        "rubric_color": existing.rubric_color,
        "charge_value": existing.charge_value,
        "contaminated": existing.contaminated or False,
        "contamination_note": existing.contamination_note,
        "dogma_referenced": getattr(existing, "dogma_referenced", None) or False,
        "dogma_note": getattr(existing, "dogma_note", None),
        "charge_summary": existing.charge_summary,
        "confidence": 1.0,
        "effects_prose": getattr(existing, "effects_prose", None),
        "societal_effects_prose": getattr(existing, "societal_effects_prose", None),
        "deadpan_line": getattr(existing, "deadpan_line", None),
        "topics": _load_json(getattr(existing, "topics", None)),
        "topic_audit": _load_json(getattr(existing, "topic_audit", None)),
    }


def _load_json(raw):
    """Parse a JSON column value back to a Python object. Returns None on
    empty / invalid input so the generation gap-fill treats it as missing."""
    if not raw:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def _ensure_generation(title: str, artist: str, lyrics: str, calib: dict) -> None:
    """Complete a calibration's generated fields IN PLACE: effects prose,
    ether tagging (deadpan_line + topics + topic_audit), then societal prose.

    This is the one and only place the calibration's generation steps (effects,
    ether, societal) are orchestrated. Every in-road reaches it through
    calibrate_song_async / ensure_full_calibration -- nobody re-implements
    "calibrate then tag then prose" on the side.

    Idempotent: any field already present (terminal-supplied or cached) is
    left untouched, so re-running fills only what's missing. Each step fails
    soft -- on error the field stays as-is and the page falls back to
    tier-generic copy. The sync step functions run in threads so the event
    loop isn't blocked through the multi-second model calls; no DB session is
    held here (the caller owns persistence).
    """
    color = calib.get("rubric_color")
    if not color:
        return

    # 1. Effects prose -- what the words may do to a listener.
    if not calib.get("effects_prose"):
        try:
            from app.services.effects_prose import generate_effects_prose
            calib["effects_prose"] = await asyncio.to_thread(
                generate_effects_prose,
                title=title, artist=artist, rubric_color=color,
                charge_value=calib.get("charge_value"),
                charge_summary=calib.get("charge_summary"),
                contaminated=bool(calib.get("contaminated", False)),
                contamination_note=calib.get("contamination_note"),
                lyrics=lyrics,
            )
        except Exception:
            logger.exception("effects_prose step failed for %s / %s", title, artist)

    # 2. Ether tagging -- names what the song IS: deadpan_line + topic tags.
    if not calib.get("deadpan_line"):
        try:
            from app.services.agents.ether_tagger import tag_song
            ether = await asyncio.to_thread(
                tag_song,
                title=title, artist=artist, lyrics=lyrics,
                rubric_color=color, charge_value=calib.get("charge_value"),
                charge_summary=calib.get("charge_summary"),
                effects_prose=calib.get("effects_prose"),
            )
            if ether:
                calib["deadpan_line"] = ether.get("deadpan_line")
                calib["topics"] = ether.get("topics")
                calib["topic_audit"] = ether.get("topic_audit")
        except Exception:
            logger.exception("ether_tagging step failed for %s / %s", title, artist)

    # 3. Societal prose -- what running this program at scale does to a society.
    #    Grounded on the ether tags + listener prose produced above.
    if not calib.get("societal_effects_prose"):
        try:
            from app.services.societal_effects_prose import generate_societal_effects_prose
            calib["societal_effects_prose"] = await asyncio.to_thread(
                generate_societal_effects_prose,
                title=title, artist=artist, rubric_color=color,
                charge_value=calib.get("charge_value"),
                charge_summary=calib.get("charge_summary"),
                contaminated=bool(calib.get("contaminated", False)),
                contamination_note=calib.get("contamination_note"),
                lyrics=lyrics,
                deadpan_line=calib.get("deadpan_line"),
                topics=calib.get("topics"),
                effects_prose=calib.get("effects_prose"),
            )
        except Exception:
            logger.exception("societal_effects_prose step failed for %s / %s", title, artist)


async def ensure_full_calibration(
    title: str, artist: str, lyrics: str | None, calibration: dict,
) -> dict:
    """Gap-fill the generated fields on an existing calibration dict (e.g. a
    cache hit) through the one shared generation step. Returns the same dict,
    mutated. No-op without lyrics (ether + prose need them)."""
    if lyrics:
        await _ensure_generation(title, artist, lyrics, calibration)
    return calibration


async def calibrate_song_async(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
    target_year: int | None = None,
    skip_cache: bool = False,
) -> dict:
    """The calibration path. Calibrate a song against the rubric, then complete
    the generated fields (effects prose, ether tagging, societal prose) in one
    pass. Returns a single complete calibration object that every in-road
    persists as-is.

    Generation runs whenever lyrics are present and is idempotent -- a cache
    hit or terminal-supplied field is reused, never regenerated. Preferred
    entry point from async request handlers so asyncio.to_thread isn't needed.
    """
    # Check for existing calibration first. A cache hit still goes through
    # generation gap-fill so older rows missing ether/prose get completed.
    if db and not skip_cache:
        existing = lookup_calibrated(title, artist, db)
        if existing:
            return await ensure_full_calibration(title, artist, lyrics, existing)

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Few-shot examples disabled. Today's corpus skews 1960s and creates a
    # self-reinforcing loop (today's call becomes tomorrow's example), so the
    # rubric stands alone. The 58-tenet definition + per-tier sub-ranges in
    # RUBRIC_DEFINITION carry the anchoring without a corpus draw.
    examples = ""

    system_prompt, user_prompt = build_calibration_prompt(
        title, artist, lyrics=lyrics, examples=examples
    )

    response = await tracked_create_async(
        client,
        call_site="calibrator",
        context={"title": title, "artist": artist, "target_year": target_year},
        model=AGENT_MODEL,
        max_tokens=2048,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Split reasoning from JSON — reasoning comes first, JSON starts at first {
    reasoning = ""
    json_str = raw
    brace_idx = raw.find("{")
    if brace_idx > 0:
        reasoning = raw[:brace_idx].strip()
        json_str = raw[brace_idx:]

    if reasoning:
        logger.info("Agent reasoning for '%s' by %s:\n%s", title, artist, reasoning)

    # Strip trailing ``` if Claude wraps JSON in fences (opening fence is already
    # before the first { and was stripped by the reasoning split above)
    if json_str.rstrip().endswith("```"):
        json_str = json_str.rstrip()[:-3]
    json_str = json_str.strip()

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        logger.error("Failed to parse Claude response for %s by %s: %s", title, artist, raw)
        return _fallback_result(title, artist, raw)

    # Validate rubric_color
    if result.get("rubric_color") not in VALID_COLORS:
        result["rubric_color"] = "green"

    enforce_contamination_rule(result)
    color = result.get("rubric_color", "green")
    contaminated = bool(result.get("contaminated", False))

    # Validate and clamp charge_value to tier range
    charge_value = _validate_charge_value(result.get("charge_value"), color)

    # Ensure all expected keys exist
    calibration = {
        "rubric_color": color,
        "charge_value": charge_value,
        "contaminated": contaminated,
        "contamination_note": result.get("contamination_note"),
        "dogma_referenced": bool(result.get("dogma_referenced", False)),
        "dogma_note": result.get("dogma_note"),
        "charge_summary": result.get("charge_summary", ""),
        "confidence": float(result.get("confidence", 0.5)),
    }

    # Verbatim-lyric backstop on the calibrator's quote-prone short fields, both of
    # which render on the public song page. The rubric now asks for paraphrase; if a
    # verbatim run slips through anyway, clear the field so no copyrighted lyric text
    # ever ships. contaminated / dogma_referenced flags stay set, so the indicators
    # still show and the page falls back to generic copy.
    if lyrics:
        from app.services.lyric_quote_guard import has_verbatim_overlap
        for _field in ("contamination_note", "dogma_note"):
            if calibration.get(_field) and has_verbatim_overlap(calibration[_field], lyrics):
                logger.warning("%s carried verbatim lyric quotes for %s / %s; cleared",
                               _field, title, artist)
                calibration[_field] = None

    # Complete the generated fields (effects prose, ether tagging, societal
    # prose) in the same pass -- the calibration path returns one whole object.
    if lyrics:
        await _ensure_generation(title, artist, lyrics, calibration)

    return calibration


def calibrate_song(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
    target_year: int | None = None,
    skip_cache: bool = False,
) -> dict:
    """Sync wrapper around calibrate_song_async. For scripts and legacy sync
    callers (e.g. compass_agent.run_compass_agent)."""
    return asyncio.run(calibrate_song_async(
        title, artist, lyrics=lyrics, db=db,
        target_year=target_year, skip_cache=skip_cache,
    ))


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
        "dogma_referenced": False,
        "dogma_note": None,
        "charge_summary": f"Calibration failed — manual review needed for {title} by {artist}",
        "confidence": 0.0,
    }
