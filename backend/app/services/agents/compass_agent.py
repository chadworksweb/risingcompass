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
from app.services.claude_meter import tracked_create
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
                          db: Session, *, lyrics: str | None = None) -> int | None:
    """Store or update a calibration in the CompassSong table for future reuse.

    Returns the compass_songs.id for the stored/updated row, or None if skipped.

    Hrana stream safety: this function does only fast DB work — compass_songs
    upsert + corpus record_and_reconcile (also DB-only). The ether tagger and
    societal-effects prose calls (multi-second Anthropic round-trips that
    used to outlast the libSQL Hrana stream TTL when held inside the same
    session) are split out into `_run_post_calibration_enrichment`. Callers
    must invoke that helper after committing the compass_songs row, so the
    long calls happen with no DB session held.

    The `lyrics` kwarg is preserved for signature compatibility but is no
    longer used here. Pass it to `_run_post_calibration_enrichment` instead.
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

    # Terminal-mode callers may include effects_prose / societal_effects_prose
    # in the result dict (Claude Code wrote them). Pre-writing them here gates
    # the Anthropic prose-gen hook in record_and_reconcile, which only fires
    # when those columns are missing. See feedback_rc_no_api_in_terminal.
    supplied_effects_prose = result.get("effects_prose")
    supplied_societal_prose = result.get("societal_effects_prose")

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
        db.flush()
        # Best-effort: ensure the artist entity + song_artists credit exist for
        # this row. Idempotent on re-runs since link_song_artists checks for
        # an existing (song, artist) pair before inserting.
        # SAVEPOINT-wrapped so any Hrana stream failure inside the linker (the
        # embedded-replica session can lose its stream mid-flight on
        # multi-statement writes) is contained — the outer transaction stays
        # intact and the caller's later queries don't hit PendingRollbackError.
        try:
            with db.begin_nested():
                from app.services.artist_linker import link_song_artists, parse_artist_string
                link_song_artists(
                    db,
                    song_source="compass",
                    song_id=existing.id,
                    entries=parse_artist_string(existing.artist or ""),
                )
        except Exception:
            logger.exception("artist link failed for existing compass song %d", existing.id)
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
        )
        db.add(song)
        db.flush()

        # First-ever appearance on the compass — log a calibration run so the
        # corpus grows on chart debuts. Subsequent days where the song stays
        # on the chart do NOT re-log: the corpus is agent practice on new
        # data, not redundant re-entries for the same song.
        # SAVEPOINT-wrapped: see comment in the existing-row branch above.
        try:
            with db.begin_nested():
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

        # Best-effort artist linking: upsert Artist rows + song_artists credits
        # using the same parser the LC submit and admin library paths use.
        # Caller's commit picks these inserts up.
        try:
            with db.begin_nested():
                from app.services.artist_linker import link_song_artists, parse_artist_string
                link_song_artists(
                    db,
                    song_source="compass",
                    song_id=song.id,
                    entries=parse_artist_string(song.artist or ""),
                )
        except Exception:
            logger.exception("artist link failed for new compass song %d", song.id)

        return song.id


def _run_post_calibration_enrichment(cs_id: int, lyrics: str | None) -> None:
    """Run the ether tagger and societal-effects prose for a compass_song row,
    each in its own short-lived DB session with the Anthropic call held
    OUTSIDE any session.

    Pattern per step:
        1. Open a read session, snapshot what the API call needs, close.
        2. Make the Anthropic call (no DB held).
        3. Open a fresh write session, persist the result, commit, close.

    This is what splits the multi-second API window from the libSQL Hrana
    stream so the stream TTL never elapses mid-transaction. Both steps fail
    soft — on error the corresponding columns stay NULL and the deferred
    backfill pass picks them up.

    Idempotent: skips work for any compass_song that already has the field
    populated. Safe to call multiple times for the same cs_id.

    Must be called AFTER the caller commits the compass_songs row that
    `_store_calibration` created/updated. Pre-commit calls will read the
    pre-image and write back stale state.
    """
    if cs_id is None:
        return

    from app.database import SessionLocal

    # ---------- Ether Art Chart ----------
    if lyrics:
        try:
            r_db = SessionLocal()
            try:
                song = r_db.get(CompassSong, cs_id)
                if not song:
                    return
                if getattr(song, "deadpan_line", None):
                    ether_snap = None
                else:
                    ether_snap = {
                        "title": song.title,
                        "artist": song.artist or "",
                        "rubric_color": song.rubric_color,
                        "charge_value": song.charge_value,
                        "charge_summary": song.charge_summary,
                        "effects_prose": getattr(song, "effects_prose", None),
                    }
            finally:
                r_db.close()

            if ether_snap is not None:
                from app.services.agents.ether_tagger import tag_song as _ether_tag
                ether = _ether_tag(
                    title=ether_snap["title"],
                    artist=ether_snap["artist"],
                    lyrics=lyrics,
                    rubric_color=ether_snap["rubric_color"],
                    charge_value=ether_snap["charge_value"],
                    charge_summary=ether_snap["charge_summary"],
                    effects_prose=ether_snap["effects_prose"],
                )
                if ether:
                    w_db = SessionLocal()
                    try:
                        song = w_db.get(CompassSong, cs_id)
                        if song:
                            song.deadpan_line = ether["deadpan_line"]
                            song.topics = json.dumps(ether["topics"])
                            song.topic_audit = (
                                json.dumps(ether["topic_audit"]) if ether["topic_audit"] else None
                            )
                            w_db.commit()
                    finally:
                        w_db.close()

                    if ether["topic_audit"]:
                        try:
                            from app.services.agents.ether_audit_notifier import send_ether_audit_email
                            send_ether_audit_email(
                                song_id=cs_id,
                                title=ether_snap["title"],
                                artist=ether_snap["artist"],
                                audit=ether["topic_audit"],
                                settings=settings,
                            )
                        except Exception:
                            logger.exception(
                                "Ether audit notify dispatch failed for compass song %d", cs_id
                            )
        except Exception:
            logger.exception("Ether tagger hook failed for compass song %d", cs_id)

    # ---------- Societal effects prose ----------
    try:
        r_db = SessionLocal()
        try:
            song = r_db.get(CompassSong, cs_id)
            if not song:
                return
            if getattr(song, "societal_effects_prose", None):
                soc_snap = None
            else:
                soc_snap = {
                    "title": song.title,
                    "artist": song.artist or "",
                    "rubric_color": song.rubric_color,
                    "charge_value": song.charge_value,
                    "charge_summary": song.charge_summary,
                    "contaminated": bool(getattr(song, "contaminated", False)),
                    "contamination_note": getattr(song, "contamination_note", None),
                    "deadpan_line": getattr(song, "deadpan_line", None),
                    "topics": getattr(song, "topics", None),
                    "effects_prose": getattr(song, "effects_prose", None),
                }
        finally:
            r_db.close()

        if soc_snap is not None:
            from app.services.societal_effects_prose import generate_societal_effects_prose
            soc_prose = generate_societal_effects_prose(
                title=soc_snap["title"],
                artist=soc_snap["artist"],
                rubric_color=soc_snap["rubric_color"],
                charge_value=soc_snap["charge_value"],
                charge_summary=soc_snap["charge_summary"],
                contaminated=soc_snap["contaminated"],
                contamination_note=soc_snap["contamination_note"],
                lyrics=lyrics,
                deadpan_line=soc_snap["deadpan_line"],
                topics=soc_snap["topics"],
                effects_prose=soc_snap["effects_prose"],
            )
            if soc_prose:
                w_db = SessionLocal()
                try:
                    song = w_db.get(CompassSong, cs_id)
                    if song:
                        song.societal_effects_prose = soc_prose
                        w_db.commit()
                finally:
                    w_db.close()
    except Exception:
        logger.exception("Societal effects prose hook failed for compass song %d", cs_id)


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
    is what let Hrana streams die mid-cron (incident 2026-05-17/18). Pattern
    mirrors `_run_post_calibration_enrichment`: open, do work, close, do
    Anthropic call with no session held, open fresh session for next write.
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
            "_enrichment_lyrics": lyrics if cs_id is not None else None,
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

    # Persist draft + draft_songs — short session
    write_db = SessionLocal()
    try:
        label = _generate_draft_label(write_db, reading_date, draft_type=draft_type)
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
        write_db.add(draft)
        write_db.flush()
        draft_id = draft.id

        for s in calibrated_songs:
            draft_song = AgentDraftSong(
                draft_id=draft_id,
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
            write_db.add(draft_song)
        write_db.commit()
    finally:
        write_db.close()

    # Run ether tagger + societal-effects enrichment now that the
    # compass_songs rows are committed. Each helper owns its own session.
    if not draft_only:
        for s in calibrated_songs:
            cs_id = s.get("compass_song_id")
            enrich_lyrics = s.get("_enrichment_lyrics")
            if cs_id is not None:
                _run_post_calibration_enrichment(cs_id, enrich_lyrics)

    # Re-fetch with eagerly-loaded songs, send email, detach, return
    fetch_db = SessionLocal()
    try:
        draft = (
            fetch_db.query(AgentDraft)
            .options(joinedload(AgentDraft.songs))
            .filter(AgentDraft.id == draft_id)
            .one()
        )
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
