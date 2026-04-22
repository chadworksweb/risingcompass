"""Compass Agent orchestrator — runs the full calibration pipeline."""

import json
import logging
import re
from datetime import date

from anthropic import Anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentDraft, AgentDraftSong, CompassSong
from app.services.agents.calibrator import calibrate_song, lookup_calibrated, AGENT_MODEL
from app.services.agents.compass_agent_rubric import build_editorial_prompt
from app.services.agents.email_notifier import send_draft_email
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated, enforce_contamination_rule

logger = logging.getLogger(__name__)


def _generate_draft_label(db: Session, reading_date: date, draft_type: str = "compass_song") -> str:
    """Generate a human-readable draft label like compass_song_2026-02-22_draft.

    Appends b, c, d... modifier when multiple drafts exist for the same date+type.
    """
    prefix = f"{draft_type}_{reading_date.isoformat()}"
    existing_count = (
        db.query(AgentDraft)
        .filter(AgentDraft.label.like(f"{prefix}%"))
        .count()
    )
    if existing_count == 0:
        return f"{prefix}_draft"
    modifier = chr(ord("a") + existing_count)
    return f"{prefix}{modifier}_draft"


def _store_calibration(title: str, artist: str, chart_position: int,
                          chart_source: str, result: dict, lyrics_available: bool,
                          db: Session) -> int | None:
    """Store or update a calibration in the CompassSong table for future reuse.

    Returns the compass_songs.id for the stored/updated row, or None if skipped.
    """
    # Skip storing if calibration failed (rubric_color is None)
    if result.get("rubric_color") is None:
        return None

    existing = (
        db.query(CompassSong)
        .filter(func.lower(CompassSong.title) == title.lower())
        .filter(func.lower(CompassSong.artist) == artist.lower())
        .first()
    )

    # Fallback: match ignoring punctuation (apostrophes stripped by shell escaping)
    if not existing:
        stripped = re.sub(r"[^\w\s]", "", title.lower())
        candidates = (
            db.query(CompassSong)
            .filter(func.lower(CompassSong.artist) == artist.lower())
            .all()
        )
        for c in candidates:
            if re.sub(r"[^\w\s]", "", c.title.lower()) == stripped:
                existing = c
                break

    if existing:
        existing.rubric_color = result["rubric_color"]
        existing.charge_value = result.get("charge_value")
        existing.contaminated = result["contaminated"]
        existing.contamination_note = result["contamination_note"]
        existing.dogma_referenced = bool(result.get("dogma_referenced", False))
        existing.dogma_note = result.get("dogma_note")
        existing.charge_summary = result["charge_summary"]
        existing.chart_source = chart_source
        db.flush()
        return existing.id
    else:
        current_year = date.today().year
        decade = f"{(current_year // 10) * 10}s"
        song = CompassSong(
            title=title,
            artist=artist,
            year=current_year,
            decade=decade,
            chart_position=chart_position,
            rubric_color=result["rubric_color"],
            charge_value=result.get("charge_value"),
            contaminated=result["contaminated"],
            contamination_note=result["contamination_note"],
            dogma_referenced=bool(result.get("dogma_referenced", False)),
            dogma_note=result.get("dogma_note"),
            charge_summary=result["charge_summary"],
            chart_source=chart_source,
        )
        db.add(song)
        db.flush()

        # First-ever appearance on the compass — log a calibration run so the
        # corpus grows on chart debuts. Subsequent days where the song stays
        # on the chart do NOT re-log: the corpus is agent practice on new
        # data, not redundant re-entries for the same song.
        try:
            from app.services.calibration_corpus import record_and_reconcile
            record_and_reconcile(
                db,
                title=title, artist=artist,
                calibration={
                    "rubric_color": result["rubric_color"],
                    "charge_value": result.get("charge_value"),
                    "charge_summary": result["charge_summary"],
                    "contaminated": result["contaminated"],
                    "contamination_note": result["contamination_note"],
                    "dogma_referenced": bool(result.get("dogma_referenced", False)),
                    "dogma_note": result.get("dogma_note"),
                    "confidence": result.get("confidence"),
                },
                triggered_by="compass_daily",
                direct_song_source="compass",
                direct_song_id=song.id,
                is_new_row=True,
            )
        except Exception:
            logger.exception("Daily corpus log failed for compass song %d", song.id)

        return song.id


def run_compass_agent(
    songs_input: list[dict],
    db: Session,
    reading_date: date | None = None,
    draft_only: bool = False,
    draft_type: str = "daily",
) -> AgentDraft:
    """Run the full agent pipeline: calibrate songs, compute compass, save draft, send email.

    Songs are calibrated once and cached. Returning chart songs reuse stored calibrations.

    Args:
        songs_input: List of dicts with title, artist, position, chart_source.
        db: Database session.
        reading_date: Date for the reading (defaults to today).
        draft_only: If True, skip writing to the CompassSong table and skip email.
            Use for case studies / album deep dives that shouldn't pollute
            the compass or drift data.

    Returns:
        The created AgentDraft.
    """
    if reading_date is None:
        reading_date = date.today()

    calibrated_songs = []
    agent_notes_parts = []
    warnings = []

    for song_in in songs_input:
        title = song_in["title"]
        artist = song_in["artist"]
        position = song_in["position"]
        chart_source = song_in.get("chart_source", "spotify")

        # Check cache first
        cached = lookup_calibrated(title, artist, db)
        if cached:
            enforce_contamination_rule(cached)

            logger.info("Cache hit: %s by %s", title, artist)

            calibrated_songs.append({
                "title": title,
                "artist": artist,
                "position": position,
                "chart_source": chart_source,
                "lyrics_available": True,  # was calibrated before
                **cached,
            })
            continue

        # Cache miss — use manual lyrics if provided
        lyrics = song_in.get("lyrics")

        if not lyrics:
            # No lyrics from any source — include song uncalibrated, needs human intervention
            agent_notes_parts.append(f"No lyrics found for \"{title}\" — awaiting human calibration")
            logger.warning("No lyrics found for %s by %s — song left uncalibrated", title, artist)
            calibrated_songs.append({
                "title": title,
                "artist": artist,
                "position": position,
                "chart_source": chart_source,
                "compass_song_id": None,
                "lyrics_available": False,
                "rubric_color": None,
                "charge_value": None,
                "contaminated": False,
                "contamination_note": None,
                "charge_summary": "Lyrics not found — awaiting human calibration",
                "confidence": 0.0,
            })
            continue

        result = calibrate_song(title, artist, lyrics=lyrics, db=db)

        # Flag incomplete calibrations — missing color, score, or summary
        if not result.get("rubric_color") or result.get("charge_value") is None or not result.get("charge_summary"):
            missing = [f for f, v in [
                ("rubric_color", result.get("rubric_color")),
                ("charge_value", result.get("charge_value")),
                ("charge_summary", result.get("charge_summary")),
            ] if not v and v != 0]
            logger.error("INCOMPLETE calibration for %s by %s — missing: %s", title, artist, ", ".join(missing))
            warnings.append(f"incomplete: {title} (missing {', '.join(missing)})")

        # Store for future reuse (skip for draft-only / case study mode)
        cs_id = None
        if not draft_only:
            cs_id = _store_calibration(title, artist, position, chart_source, result, True, db)
        logger.info("Calibrated and cached: %s by %s → %s", title, artist, result["rubric_color"])

        calibrated_songs.append({
            "title": title,
            "artist": artist,
            "position": position,
            "chart_source": chart_source,
            "compass_song_id": cs_id,
            "lyrics_available": True,
            **result,
        })

    # Collect per-song warnings
    for s in calibrated_songs:
        if not s["lyrics_available"]:
            warnings.append(f"no_lyrics: {s['title']}")
        if s.get("confidence") is not None and s["confidence"] < 0.5:
            warnings.append(f"low_confidence: {s['title']} ({s['confidence']})")

    # Compute compass metrics — exclude uncalibrated songs (no lyrics found)
    song_dicts = [
        {"rubric_color": s["rubric_color"], "charge_value": s.get("charge_value"), "position": s["position"]}
        for s in calibrated_songs
        if s.get("rubric_color") is not None
    ]
    degree = compute_degree(song_dicts)
    charge = degree_to_charge(degree)
    contam = count_contaminated(calibrated_songs)

    # Generate editorial summary
    editorial = _generate_editorial(calibrated_songs)

    # Assemble agent notes
    agent_notes = "; ".join(agent_notes_parts) if agent_notes_parts else None

    # Save draft to DB
    label = _generate_draft_label(db, reading_date, draft_type=draft_type)
    draft = AgentDraft(
        label=label,
        draft_type=draft_type,
        date=reading_date,
        status="pending",
        compass_degree=degree,
        charge_level=charge,
        contamination_count=contam,
        editorial_summary=editorial,
        agent_model=AGENT_MODEL,
        agent_notes=agent_notes,
        agent_warnings=json.dumps(warnings) if warnings else None,
    )
    db.add(draft)
    db.flush()

    for s in calibrated_songs:
        draft_song = AgentDraftSong(
            draft_id=draft.id,
            compass_song_id=s.get("compass_song_id"),
            title=s["title"],
            artist=s["artist"],
            position=s["position"],
            rubric_color=s["rubric_color"],
            charge_value=s.get("charge_value"),
            contaminated=s["contaminated"],
            contamination_note=s["contamination_note"],
            dogma_referenced=bool(s.get("dogma_referenced", False)),
            dogma_note=s.get("dogma_note"),
            charge_summary=s["charge_summary"],
            chart_source=s["chart_source"],
            confidence=s["confidence"],
            lyrics_available=s["lyrics_available"],
        )
        db.add(draft_song)

    db.commit()
    db.refresh(draft)

    # Send email notification (skip for draft-only / case study mode)
    if not draft_only:
        email_sent = send_draft_email(draft, draft.songs, settings, db=db)
        if not email_sent:
            warnings.append("email_failed: notification not sent")
            draft.agent_warnings = json.dumps(warnings) if warnings else None
            db.commit()
            db.refresh(draft)

    return draft


def _generate_editorial(calibrated_songs: list[dict]) -> str | None:
    """Generate a one-line editorial summary using Claude."""
    if not settings.anthropic_api_key:
        return None

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        system_prompt, user_prompt = build_editorial_prompt(calibrated_songs)

        response = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        logger.exception("Failed to generate editorial summary")
        return None
