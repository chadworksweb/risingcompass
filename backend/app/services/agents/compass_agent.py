"""Compass Agent orchestrator — runs the full calibration pipeline."""

import json
import logging
import re
from datetime import date, datetime

from anthropic import Anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentDraft, AgentDraftSong, CompassSong
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
                compass_song_id=s.get("compass_song_id"),
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

    # Terminal-mode callers may include effects_prose / societal_effects_prose
    # in the result dict (Claude Code wrote them). Pre-writing them here gates
    # the Anthropic prose-gen hook in record_and_reconcile, which only fires
    # when those columns are missing. See feedback_rc_no_api_in_terminal.
    supplied_effects_prose = result.get("effects_prose")
    supplied_societal_prose = result.get("societal_effects_prose")
    # Sealed societal-prose provenance travels in the calibration dict from the
    # calibrator path (real generated_at + model); terminal supply leaves them
    # absent (None). Kept in lockstep with supplied_societal_prose below.
    supplied_societal_generated_at = result.get("societal_prose_generated_at")
    supplied_societal_model = result.get("societal_prose_model")

    # Terminal-mode callers may also supply the Ether Art Chart fields
    # (deadpan_line + topics + topic_audit) that the ether_tagger would
    # otherwise produce via Anthropic. Writing them here lets the supply-lyrics
    # path skip the tagger. topics/topic_audit are stored as JSON strings to
    # match the columns the tagger writes. See feedback_rc_no_api_in_terminal.
    supplied_deadpan = result.get("deadpan_line")
    supplied_topics = result.get("topics")
    supplied_topic_audit = result.get("topic_audit")
    supplied_topics_json = (
        json.dumps(supplied_topics) if supplied_topics is not None else None
    )
    supplied_topic_audit_json = (
        json.dumps(supplied_topic_audit) if supplied_topic_audit else None
    )

    if existing:
        existing.rubric_color = result["rubric_color"]
        existing.charge_value = result.get("charge_value")
        existing.contaminated = result["contaminated"]
        existing.contamination_note = result["contamination_note"]
        existing.dogma_referenced = bool(result.get("dogma_referenced", False))
        existing.dogma_note = result.get("dogma_note")
        existing.charge_summary = result["charge_summary"]
        existing.chart_source = chart_source
        if supplied_effects_prose is not None:
            existing.effects_prose = supplied_effects_prose
        if supplied_societal_prose is not None:
            existing.societal_effects_prose = supplied_societal_prose
            existing.societal_prose_generated_at = supplied_societal_generated_at
            existing.societal_prose_model = supplied_societal_model
        if supplied_deadpan is not None:
            existing.deadpan_line = supplied_deadpan
        if supplied_topics is not None:
            existing.topics = supplied_topics_json
        if supplied_topic_audit is not None:
            existing.topic_audit = supplied_topic_audit_json
        db.flush()
        # Commit the compass_song update before the linker. If the linker's
        # multi-statement work fails, the calibration data is already
        # persisted and the caller's session can be reset without losing
        # work. db.rollback() in the except clears the invalid transaction so
        # the caller can continue.
        db.commit()
        try:
            from app.services.artist_linker import link_song_artists, parse_artist_string
            link_song_artists(
                db,
                song_source="compass",
                song_id=existing.id,
                entries=parse_artist_string(existing.artist or ""),
            )
            db.commit()
        except Exception:
            logger.exception("artist link failed for existing compass song %d", existing.id)
            try:
                db.rollback()
            except Exception:
                pass
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
            effects_prose=supplied_effects_prose,
            societal_effects_prose=supplied_societal_prose,
            societal_prose_generated_at=supplied_societal_generated_at,
            societal_prose_model=supplied_societal_model,
            deadpan_line=supplied_deadpan,
            topics=supplied_topics_json,
            topic_audit=supplied_topic_audit_json,
        )
        db.add(song)
        db.flush()
        # Commit the compass_song insert before the risky post-work. If
        # record_and_reconcile or link_song_artists fail, the calibration row
        # is already persisted and the caller's session is reset via
        # db.rollback() in the except so it can keep working (mutating
        # draft_song, etc.) afterwards.
        db.commit()
        song_id = song.id

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
                direct_song_id=song_id,
                is_new_row=True,
            )
            db.commit()
        except Exception:
            logger.exception("Daily corpus log failed for compass song %d", song_id)
            try:
                db.rollback()
            except Exception:
                pass

        # Best-effort artist linking: upsert Artist rows + song_artists credits
        # using the same parser the LC submit and admin library paths use.
        try:
            from app.services.artist_linker import link_song_artists, parse_artist_string
            link_song_artists(
                db,
                song_source="compass",
                song_id=song_id,
                entries=parse_artist_string(song.artist or ""),
            )
            db.commit()
        except Exception:
            logger.exception("artist link failed for new compass song %d", song_id)
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
                s.get("compass_song_id"), s.get("title"),
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
