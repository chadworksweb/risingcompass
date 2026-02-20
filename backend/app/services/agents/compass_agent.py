"""Compass Agent orchestrator — runs the full classification pipeline."""

import logging
from datetime import date

from anthropic import Anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentDraft, AgentDraftSong, CompassSong
from app.services.agents.classifier import classify_song, AGENT_MODEL
from app.services.agents.rising_compass_agent_rubric import build_editorial_prompt, truncate_mei
from app.services.agents.email_notifier import send_draft_email
from app.services.agents.lyrics_source import fetch_lyrics
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated

logger = logging.getLogger(__name__)


def _lookup_cached(title: str, artist: str, db: Session) -> dict | None:
    """Check if a song has already been classified in the CompassSong table.

    Uses case-insensitive match on title + artist.
    Returns classification dict or None.
    """
    existing = (
        db.query(CompassSong)
        .filter(func.lower(CompassSong.title) == title.lower())
        .filter(func.lower(CompassSong.artist) == artist.lower())
        .first()
    )
    if not existing or not existing.rubric_color:
        return None

    # Only trust calibrated (human-reviewed) songs as cache hits
    if not existing.calibrated:
        return None

    return {
        "rubric_color": existing.rubric_color,
        "charge_value": existing.charge_value,
        "contaminated": existing.contaminated or False,
        "contamination_note": existing.contamination_note,
        "charge_summary": existing.charge_summary,
        "message_analysis": truncate_mei(existing.message_analysis),
        "expression_analysis": truncate_mei(existing.expression_analysis),
        "intention_analysis": truncate_mei(existing.intention_analysis),
        "confidence": 1.0,  # human-reviewed
    }


def _store_classification(title: str, artist: str, chart_position: int,
                          chart_source: str, result: dict, lyrics_available: bool,
                          db: Session) -> None:
    """Store or update a classification in the CompassSong table for future reuse."""
    existing = (
        db.query(CompassSong)
        .filter(func.lower(CompassSong.title) == title.lower())
        .filter(func.lower(CompassSong.artist) == artist.lower())
        .first()
    )

    if existing:
        existing.rubric_color = result["rubric_color"]
        existing.charge_value = result.get("charge_value")
        existing.contaminated = result["contaminated"]
        existing.contamination_note = result["contamination_note"]
        existing.charge_summary = result["charge_summary"]
        existing.message_analysis = result.get("message_analysis")
        existing.expression_analysis = result.get("expression_analysis")
        existing.intention_analysis = result.get("intention_analysis")
        existing.chart_source = chart_source
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
            charge_summary=result["charge_summary"],
            message_analysis=result.get("message_analysis"),
            expression_analysis=result.get("expression_analysis"),
            intention_analysis=result.get("intention_analysis"),
            chart_source=chart_source,
        )
        db.add(song)


def run_compass_agent(
    songs_input: list[dict],
    db: Session,
    reading_date: date | None = None,
    draft_only: bool = False,
) -> AgentDraft:
    """Run the full agent pipeline: classify songs, compute compass, save draft, send email.

    Songs are classified once and cached. Returning chart songs reuse stored classifications.

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

    classified_songs = []
    agent_notes_parts = []

    for song_in in songs_input:
        title = song_in["title"]
        artist = song_in["artist"]
        position = song_in["position"]
        chart_source = song_in.get("chart_source", "spotify")

        # Check cache first
        cached = _lookup_cached(title, artist, db)
        if cached:
            # Enforce red/yellow contamination rule on cached data too
            if cached["rubric_color"] in ("red", "yellow"):
                cached["contaminated"] = False
                cached["contamination_note"] = None

            # If M/E/I are missing, regenerate them via classifier but keep calibrated values
            if not cached.get("message_analysis"):
                logger.info("Cache hit but M/E/I missing, regenerating: %s by %s", title, artist)
                lyrics = fetch_lyrics(title, artist)
                fresh = classify_song(title, artist, lyrics=lyrics, db=None)  # db=None skips _lookup_existing
                cached["message_analysis"] = fresh.get("message_analysis")
                cached["expression_analysis"] = fresh.get("expression_analysis")
                cached["intention_analysis"] = fresh.get("intention_analysis")
            else:
                logger.info("Cache hit: %s by %s", title, artist)

            classified_songs.append({
                "title": title,
                "artist": artist,
                "position": position,
                "chart_source": chart_source,
                "lyrics_available": True,  # was classified before
                **cached,
            })
            continue

        # Cache miss — fetch lyrics and classify
        lyrics = fetch_lyrics(title, artist)

        if not lyrics:
            # No lyrics from any source — include song unclassified, needs human intervention
            agent_notes_parts.append(f"No lyrics found for \"{title}\" — awaiting human classification")
            logger.warning("No lyrics found for %s by %s — song left unclassified", title, artist)
            classified_songs.append({
                "title": title,
                "artist": artist,
                "position": position,
                "chart_source": chart_source,
                "lyrics_available": False,
                "rubric_color": None,
                "charge_value": None,
                "contaminated": False,
                "contamination_note": None,
                "charge_summary": "Lyrics not found — awaiting human classification",
                "message_analysis": None,
                "expression_analysis": None,
                "intention_analysis": None,
                "confidence": 0.0,
            })
            continue

        result = classify_song(title, artist, lyrics=lyrics, db=db)

        # Store for future reuse (skip for draft-only / case study mode)
        if not draft_only:
            _store_classification(title, artist, position, chart_source, result, True, db)
        logger.info("Classified and cached: %s by %s → %s", title, artist, result["rubric_color"])

        classified_songs.append({
            "title": title,
            "artist": artist,
            "position": position,
            "chart_source": chart_source,
            "lyrics_available": True,
            **result,
        })

    # Compute compass metrics — exclude unclassified songs (no lyrics found)
    song_dicts = [
        {"rubric_color": s["rubric_color"], "charge_value": s.get("charge_value"), "position": s["position"]}
        for s in classified_songs
        if s.get("rubric_color") is not None
    ]
    degree = compute_degree(song_dicts)
    charge = degree_to_charge(degree)
    contam = count_contaminated(classified_songs)

    # Generate editorial summary
    editorial = _generate_editorial(classified_songs)

    # Assemble agent notes
    agent_notes = "; ".join(agent_notes_parts) if agent_notes_parts else None

    # Save draft to DB
    draft = AgentDraft(
        date=reading_date,
        status="pending",
        compass_degree=degree,
        charge_level=charge,
        contamination_count=contam,
        editorial_summary=editorial,
        agent_model=AGENT_MODEL,
        agent_notes=agent_notes,
    )
    db.add(draft)
    db.flush()

    for s in classified_songs:
        draft_song = AgentDraftSong(
            draft_id=draft.id,
            title=s["title"],
            artist=s["artist"],
            position=s["position"],
            rubric_color=s["rubric_color"],
            charge_value=s.get("charge_value"),
            contaminated=s["contaminated"],
            contamination_note=s["contamination_note"],
            charge_summary=s["charge_summary"],
            message_analysis=s["message_analysis"],
            expression_analysis=s["expression_analysis"],
            intention_analysis=s["intention_analysis"],
            chart_source=s["chart_source"],
            confidence=s["confidence"],
            lyrics_available=s["lyrics_available"],
        )
        db.add(draft_song)

    db.commit()
    db.refresh(draft)

    # Send email notification (skip for draft-only / case study mode)
    if not draft_only:
        send_draft_email(draft, draft.songs, settings, db=db)

    return draft


def _generate_editorial(classified_songs: list[dict]) -> str | None:
    """Generate a one-line editorial summary using Claude."""
    if not settings.anthropic_api_key:
        return None

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        system_prompt, user_prompt = build_editorial_prompt(classified_songs)

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
