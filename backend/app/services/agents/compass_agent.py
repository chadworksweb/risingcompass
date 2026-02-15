"""Compass Agent orchestrator — runs the full classification pipeline."""

import logging
from datetime import date

from anthropic import Anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentDraft, AgentDraftSong, Song
from app.services.agents.classifier import classify_song, AGENT_MODEL
from app.services.agents.rising_compass_agent_rubric import build_editorial_prompt
from app.services.agents.email_notifier import send_draft_email
from app.services.agents.lyrics_source import fetch_lyrics
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated

logger = logging.getLogger(__name__)


def _lookup_cached(title: str, artist: str, db: Session) -> dict | None:
    """Check if a song has already been classified in the Song table.

    Uses case-insensitive match on title + artist.
    Returns classification dict or None.
    """
    existing = (
        db.query(Song)
        .filter(func.lower(Song.title) == title.lower())
        .filter(func.lower(Song.artist) == artist.lower())
        .first()
    )
    if not existing or not existing.rubric_color:
        return None

    return {
        "rubric_color": existing.rubric_color,
        "charge_value": existing.charge_value,
        "contaminated": existing.contaminated or False,
        "contamination_note": existing.contamination_note,
        "charge_summary": existing.charge_summary,
        "message_analysis": existing.message_analysis,
        "expression_analysis": existing.expression_analysis,
        "intention_analysis": existing.intention_analysis,
        "confidence": 1.0,  # human-reviewed or previously classified
    }


def _store_classification(title: str, artist: str, chart_position: int,
                          chart_source: str, result: dict, lyrics_available: bool,
                          db: Session) -> None:
    """Store a new classification in the Song table for future reuse."""
    current_year = date.today().year
    decade = f"{(current_year // 10) * 10}s"

    song = Song(
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
) -> AgentDraft:
    """Run the full agent pipeline: classify songs, compute compass, save draft, send email.

    Songs are classified once and cached. Returning chart songs reuse stored classifications.

    Args:
        songs_input: List of dicts with title, artist, position, chart_source.
        db: Database session.
        reading_date: Date for the reading (defaults to today).

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
        lyrics_available = lyrics is not None

        if not lyrics_available:
            agent_notes_parts.append(f"No lyrics for \"{title}\" — classified from training knowledge")

        result = classify_song(title, artist, lyrics=lyrics, db=db)

        # Store for future reuse
        _store_classification(title, artist, position, chart_source, result, lyrics_available, db)
        logger.info("Classified and cached: %s by %s → %s", title, artist, result["rubric_color"])

        classified_songs.append({
            "title": title,
            "artist": artist,
            "position": position,
            "chart_source": chart_source,
            "lyrics_available": lyrics_available,
            **result,
        })

    # Compute compass metrics (uses charge_value when available)
    song_dicts = [
        {"rubric_color": s["rubric_color"], "charge_value": s.get("charge_value"), "position": s["position"]}
        for s in classified_songs
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

    # Send email notification
    send_draft_email(draft, draft.songs, settings)

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
