"""Compass Agent orchestrator — runs the full calibration pipeline."""

import json
import logging
import re
from datetime import date, datetime

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentDraft, AgentDraftSong
from app.services.agents.calibrator import calibrate_song, lookup_calibrated, AGENT_MODEL
from app.services.agents.compass_agent_rubric import build_editorial_prompt
from app.services.agents.email_notifier import send_draft_email
from app.services.claude_meter import tracked_create
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated, enforce_contamination_rule

logger = logging.getLogger(__name__)


def _write_draft_and_songs(
    *,
    reading_date: date,
    draft_type: str,
    degree: float,
    charge: str,
    contam: int,
    editorial: str | None,
    agent_notes: str | None,
    warnings: list,
    calibrated_songs: list,
) -> int:
    """Insert the AgentDraft + AgentDraftSong rows via the ORM and commit.
    Returns the new draft id.

    Plain ORM on Postgres: no replica stream to lose mid-transaction, and the
    Date / Boolean columns are populated with native types (the old raw-SQL
    path stored isoformat strings and 1/0 ints, which Postgres would reject)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        label = _generate_draft_label(db, reading_date, draft_type)
        draft = AgentDraft(
            label=label,
            draft_type=draft_type,
            status="pending",
            date=reading_date,
            compass_degree=degree,
            charge_level=charge,
            contamination_count=contam,
            editorial_summary=editorial,
            agent_model=AGENT_MODEL,
            agent_notes=agent_notes,
            agent_warnings=json.dumps(warnings) if warnings else None,
        )
        db.add(draft)
        db.flush()  # populate draft.id

        for s in calibrated_songs:
            db.add(AgentDraftSong(
                draft_id=draft.id,
                song_id=s.get("song_id"),
                title=s["title"],
                artist=s["artist"],
                position=s["position"],
                rubric_color=s["rubric_color"],
                charge_value=s.get("charge_value"),
                contaminated=bool(s["contaminated"]),
                contamination_note=s["contamination_note"],
                dogma_referenced=bool(s.get("dogma_referenced", False)),
                dogma_note=s.get("dogma_note"),
                charge_summary=s["charge_summary"],
                chart_source=s["chart_source"],
                confidence=s["confidence"],
                lyrics_available=bool(s["lyrics_available"]),
            ))

        db.commit()
        return draft.id
    finally:
        db.close()


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
                          db: Session, *, lyrics: str | None = None) -> int | None:
    """Store or update a calibration in the CompassSong table for future reuse.

    Returns the compass_songs.id for the stored/updated row, or None if skipped.

    Connection hygiene: this function does only fast DB work — compass_songs
    upsert + corpus record_and_reconcile (also DB-only). The multi-second
    Anthropic round-trips (rubric calibration, ether tagging, prose) all happen
    earlier in the calibration path (calibrate_song_async), with no DB session
    held; by the time `result` reaches here it already carries the ether tags
    + prose, which are written below alongside the rubric fields.

    The `lyrics` kwarg drives the verbatim-lyric lock below -- this is the single
    storage chokepoint where every grading path converges.
    """
    # Skip storing if calibration failed (rubric_color is None)
    if result.get("rubric_color") is None:
        return None

    # Verbatim-lyric lock at the single storage chokepoint. EVERY grading path
    # converges here -- terminal (Claude-Code-supplied) and browser/admin (server
    # AI) both reach _store_calibration -- so this guarantees no copyrighted lyric
    # text lands in a stored, public, sellable field, no matter which path produced
    # it. Mutates `result` in place so the caller's draft-song mirror is consistent.
    if lyrics:
        from app.services.lyric_quote_guard import scrub_calibration_quotes
        altered = scrub_calibration_quotes(result, lyrics)
        if altered:
            logger.warning("Stripped verbatim lyric quotes from %s for '%s' by %s",
                           ", ".join(altered), title, artist)

    # Write-time FLOOR for terminal-supplied prose. Claude Code wrote this prose
    # (no server Anthropic call), so there is no generation seal to carry. Stamp
    # the moment it lands -- generated_at then means "existed by this time" (a
    # floor, not a sealed generation) and model 'terminal_supplied' marks it as
    # such, paralleling migration 075's 'legacy_unknown' proxy. Mutated onto
    # `result` so the native store + the caller's draft-song mirror agree, and
    # so the frozen provenance hash recipe stays unchanged + the row stays
    # anchorable. See RISING-COMPASS-PROSE-PROVENANCE.md and
    # feedback_rc_no_api_in_terminal.
    if (result.get("societal_effects_prose") is not None
            and result.get("societal_prose_generated_at") is None):
        result["societal_prose_generated_at"] = datetime.utcnow()
        result["societal_prose_model"] = result.get("societal_prose_model") or "terminal_supplied"

    # Native unified storage (Phase 5b): upsert the atomic songs row by
    # canonical_key + a chart_appearance for this (chart, year, position) + a
    # chart_reading ingestion + artist credits. Authoritative-first overwrite is
    # enforced in the chokepoint; only_set_present means a daily re-read never
    # nulls prose/analysis fields the calibration object doesn't carry.
    from app.services.song_sync import store_calibrated_song
    from app.services.artist_linker import parse_artist_string
    current_year = date.today().year
    song_id, created = store_calibrated_song(
        db,
        source="compass",
        title=title, artist=artist,
        calibration=result,
        chart_source=chart_source,
        year=current_year,
        chart_position=chart_position,
        artist_entries=parse_artist_string(artist or ""),
    )
    if song_id is None:
        return None
    db.commit()

    # First-ever appearance of this song -> log a calibration run so the corpus
    # grows on chart debuts. Re-reads of an already-known song do NOT re-log.
    if created:
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
                direct_song_source="songs",
                direct_song_id=song_id,
                is_new_row=True,
            )
            db.commit()
        except Exception:
            logger.exception("Daily corpus log failed for song %d", song_id)
            try:
                db.rollback()
            except Exception:
                pass

    return song_id


def _dispatch_ether_audit(cs_id: int | None, title: str | None,
                          artist: str | None, topic_audit) -> None:
    """Notify admins when the calibration path's ether tagging found no honest
    taxonomy match (topic_audit present). The tags themselves are already
    written by `_store_calibration` from the calibration result -- this is the
    notification only, kept on the chart pipeline (compass songs) the audit
    queue was built for. Fails soft.
    """
    if not topic_audit or cs_id is None:
        return
    try:
        from app.services.agents.ether_audit_notifier import send_ether_audit_email
        send_ether_audit_email(
            song_id=cs_id,
            title=title,
            artist=artist or "",
            audit=topic_audit,
            settings=settings,
        )
    except Exception:
        logger.exception("Ether audit notify dispatch failed for compass song %s", cs_id)


def run_compass_agent(
    songs_input: list[dict],
    db: Session | None = None,
    reading_date: date | None = None,
    draft_only: bool = False,
    draft_type: str = "daily",
) -> AgentDraft:
    """Run the full agent pipeline: calibrate songs, compute compass, save draft, send email.

    Songs are calibrated once and cached. Returning chart songs reuse stored calibrations.

    Session lifecycle: this function manages its own short-lived DB sessions
    per op. The `db` argument is kept for backwards compatibility but is not
    used internally — holding one session across the per-song Anthropic loop
    would pin a pooled connection idle for the whole cron. The pattern: open,
    do work, close, run the calibration path with no session held, open a
    fresh session for the next write.
    Returned `AgentDraft` is detached with `songs` eagerly loaded so
    `DraftOut` (from_attributes=True) serializes without a live session.

    Args:
        songs_input: List of dicts with title, artist, position, chart_source.
        db: Ignored. Accepted for backwards compatibility with existing callers.
        reading_date: Date for the reading (defaults to today).
        draft_only: If True, skip writing to the CompassSong table and skip email.
            Use for case studies / album deep dives that shouldn't pollute
            the compass or drift data.

    Returns:
        The created AgentDraft (detached, songs eagerly loaded).
    """
    from app.database import SessionLocal
    from sqlalchemy.orm import joinedload

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

        # Cache lookup — short session
        cache_db = SessionLocal()
        try:
            cached = lookup_calibrated(title, artist, cache_db)
        finally:
            cache_db.close()

        if cached:
            enforce_contamination_rule(cached)
            logger.info("Cache hit: %s by %s", title, artist)
            calibrated_songs.append({
                "title": title,
                "artist": artist,
                "position": position,
                "chart_source": chart_source,
                "lyrics_available": True,
                **cached,
            })
            continue

        lyrics = song_in.get("lyrics")
        if not lyrics:
            agent_notes_parts.append(f"No lyrics found for \"{title}\" — awaiting human calibration")
            logger.warning("No lyrics found for %s by %s — song left uncalibrated", title, artist)
            calibrated_songs.append({
                "title": title,
                "artist": artist,
                "position": position,
                "chart_source": chart_source,
                "song_id": None,
                "lyrics_available": False,
                "rubric_color": None,
                "charge_value": None,
                "contaminated": False,
                "contamination_note": None,
                "charge_summary": "Lyrics not found — awaiting human calibration",
                "confidence": 0.0,
            })
            continue

        # Anthropic call WITHOUT holding a DB session
        result = calibrate_song(title, artist, lyrics=lyrics, db=None)

        if not result.get("rubric_color") or result.get("charge_value") is None or not result.get("charge_summary"):
            missing = [f for f, v in [
                ("rubric_color", result.get("rubric_color")),
                ("charge_value", result.get("charge_value")),
                ("charge_summary", result.get("charge_summary")),
            ] if not v and v != 0]
            logger.error("INCOMPLETE calibration for %s by %s — missing: %s", title, artist, ", ".join(missing))
            warnings.append(f"incomplete: {title} (missing {', '.join(missing)})")

        # Store calibration — short session (commits before close so the row survives)
        cs_id = None
        if not draft_only:
            store_db = SessionLocal()
            try:
                cs_id = _store_calibration(title, artist, position, chart_source, result, True, store_db, lyrics=lyrics)
                store_db.commit()
            finally:
                store_db.close()
        logger.info("Calibrated and cached: %s by %s → %s", title, artist, result["rubric_color"])

        calibrated_songs.append({
            "title": title,
            "artist": artist,
            "position": position,
            "chart_source": chart_source,
            "song_id": cs_id,
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

    # Generate editorial summary (Anthropic call, no DB session held)
    editorial = _generate_editorial(calibrated_songs)
    agent_notes = "; ".join(agent_notes_parts) if agent_notes_parts else None

    # Persist draft + draft_songs via the ORM.
    draft_id = _write_draft_and_songs(
        reading_date=reading_date,
        draft_type=draft_type,
        degree=degree,
        charge=charge,
        contam=contam,
        editorial=editorial,
        agent_notes=agent_notes,
        warnings=warnings,
        calibrated_songs=calibrated_songs,
    )

    # The calibration path already produced + persisted the ether tags and
    # prose with each compass_songs row (via calibrate_song -> _store_calibration).
    # All that's left is the admin notification for any song the ether tagger
    # couldn't tag against the taxonomy.
    if not draft_only:
        for s in calibrated_songs:
            _dispatch_ether_audit(
                s.get("song_id"), s.get("title"),
                s.get("artist"), s.get("topic_audit"),
            )

    # Re-fetch with eagerly-loaded songs, send email, detach, return. The
    # write committed above is immediately visible (single Postgres DB, no
    # replica lag), so a plain fetch suffices.
    fetch_db = SessionLocal()
    try:
        draft = (
            fetch_db.query(AgentDraft)
            .options(joinedload(AgentDraft.songs))
            .filter(AgentDraft.id == draft_id)
            .one_or_none()
        )
        if draft is None:
            raise RuntimeError(f"draft id={draft_id} not found after write")
        if not draft_only:
            email_sent = send_draft_email(draft, draft.songs, settings, db=fetch_db)
            if not email_sent:
                warnings.append("email_failed: notification not sent")
                draft.agent_warnings = json.dumps(warnings) if warnings else None
                fetch_db.commit()
                fetch_db.refresh(draft)
        # cascade="all" on AgentDraft.songs expunges children automatically
        fetch_db.expunge(draft)
    finally:
        fetch_db.close()

    return draft


def _generate_editorial(calibrated_songs: list[dict]) -> str | None:
    """Generate a one-line editorial summary using Claude."""
    if not settings.anthropic_api_key:
        return None

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        system_prompt, user_prompt = build_editorial_prompt(calibrated_songs)

        response = tracked_create(
            client,
            call_site="editorial_summary",
            context={"song_count": len(calibrated_songs)},
            model=AGENT_MODEL,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        logger.exception("Failed to generate editorial summary")
        return None
