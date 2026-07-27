"""Compass Agent orchestrator — runs the full calibration pipeline."""

import json
import logging
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentDraft, AgentDraftSong
from app.services.agents.calibrator import calibrate_song, lookup_calibrated, AGENT_MODEL
from app.services.agents.email_notifier import send_draft_email
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated, enforce_contamination_rule

logger = logging.getLogger(__name__)

# The null dispositions carried from the calibration dict onto the draft row.
# Sourced from constants so the WRITE and every READ (approval gate, approval
# email, terminal scripts) agree on the same set. Hand-copying these field by
# field is exactly how `instrumental` went missing: lookup_calibrated returned
# it, _write_draft_and_songs ignored it, the column fell to its default False,
# and every charting instrumental re-listed as awaiting-lyrics on every feeder
# run no matter how many times it had been marked.
from app.constants import NULL_DISPOSITIONS as _DISPOSITION_FIELDS, chart_weighting


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

        from app.services.feeder_clean import clean_feeder_display
        for s in calibrated_songs:
            # Display-clean the feeder title/artist so the draft (and the chart
            # snapshot / reading rebuilt from it at approval) shows a clean title
            # instead of raw upload cruft ("34. Title (Soundtrack) - Artist @Chan").
            # No-op on a non-upload string (is_feeder_upload gate); identity is
            # unaffected (the row is already linked via song_id / the clean key).
            disp_title, disp_artist = clean_feeder_display(s["title"], s["artist"])
            db.add(AgentDraftSong(
                draft_id=draft.id,
                song_id=s.get("song_id"),
                title=disp_title,
                artist=disp_artist,
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
                # Null dispositions (preorder / lyrics_unavailable /
                # instrumental): each exempts the song from the approval gate
                # and excludes it from the aggregates. They differ only in
                # lifecycle -- preorder re-lists until real lyrics drop, while
                # the other two are cache hits carried on the songs row and do
                # NOT re-list. See _DISPOSITION_FIELDS.
                **{f: bool(s.get(f, False)) for f in _DISPOSITION_FIELDS},
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
                          db: Session, *, lyrics: str | None = None,
                          year: int | None = None,
                          chart_position_letter: str = "",
                          allow_prose_generation: bool = True,
                          source: str = "compass",
                          triggered_by: str = "compass_daily") -> int | None:
    """Store or update a calibration on the unified `songs` row + a
    chart_appearance for this (chart, year, position).

    Returns the unified `songs.id` for the stored/updated row, or None if skipped.

    `year` defaults to the CURRENT year (the live daily-reading path). Historical
    backfill of a not-yet-done year MUST pass the chart year explicitly so the
    chart_appearance lands on the right year (e.g. `year=1977`); pass
    `chart_position_letter` for double-A-side splits.

    Connection hygiene: this function does only fast DB work — compass_songs
    upsert + corpus record_and_reconcile (also DB-only). The multi-second
    Anthropic round-trips (rubric calibration, ether tagging, prose) all happen
    earlier in the calibration path (calibrate_song_async), with no DB session
    held; by the time `result` reaches here it already carries the ether tags
    + prose, which are written below alongside the rubric fields.

    The `lyrics` kwarg drives the verbatim-lyric lock below -- this is the single
    storage chokepoint where every grading path converges.

    `allow_prose_generation=False` (terminal / Claude-Code-supplied path) hard-
    disables the Anthropic listener/societal prose-gen hooks in
    record_and_reconcile: terminal work supplies its own prose and must never draw
    on the public-traffic ANTHROPIC_API_KEY. See feedback_rc_no_api_in_terminal.

    `source` selects the storage lane (the chart pipeline passes the default
    "compass" = method chart_reading + a chart_appearance + origin_chart). A
    one-off Library add passes source="library" (method catalog_backfill): an
    authoritative write that creates NO chart_appearance and stamps NO
    origin_chart, so the song joins the Library without claiming a chart slot or
    entering the compass aggregate. `triggered_by` labels the calibration run.
    """
    # Skip storing if calibration failed (rubric_color is None)
    if result.get("rubric_color") is None:
        return None

    # Terminal/operator write path (allow_prose_generation=False -- the Claude-Code-
    # supplied calibrate_song.py / backfill / lyrics-supply lane) HARD-FAILS on a
    # charge_summary that trips the summary guard, so operator drift cannot land.
    # Guarded: absence/verdict framing, musical-genre words, tier color names, and
    # restatement of the song's own (multi-word) title. This is the terminal analog
    # of the live calibrator's retry: the operator IS the model, so rephrase to PURE
    # POSITIVE description of what the song IS and re-run. The live server path keeps
    # retry-then-warn in the LEC calibrator + a loud log in store_calibrated_song;
    # only the supplied terminal lane raises. The canonical rule + guard live in LEC
    # and reach the operator via /api/rubric's calibration_format. See summary_guard.py.
    if not allow_prose_generation:
        from app.services.agents.summary_guard import (
            CORRECTIVE_NUDGE,
            SUMMARY_RULES_NUDGE,
            summary_violations,
        )
        _cs = result.get("charge_summary")
        _viol = summary_violations(_cs, titles=[title], titles_multiword_only=True)
        if _viol:
            raise ValueError(
                "charge_summary tripped the summary guard (terminal hard-fail, no "
                "write performed): " + "; ".join(_viol) + ". "
                + SUMMARY_RULES_NUDGE + " " + CORRECTIVE_NUDGE
                + f" Offending summary: {_cs!r}"
            )

        # Same posture for deadpan_line: the placard spec (a flat naming FRAGMENT,
        # ~len(title)+len(artist) chars, no period, no leading article) lived only in
        # ether_tagger's prompt, and the terminal lane writes the column straight
        # through -- so operator drift into full sentences landed silently across
        # 2026-06/07. HARD-FAIL here; rewrite to placard form and re-run.
        # See deadpan_guard.py.
        from app.services.agents.deadpan_guard import (
            CORRECTIVE_NUDGE as DEADPAN_CORRECTIVE_NUDGE,
            DEADPAN_RULES_NUDGE,
            deadpan_violations,
        )
        _dp = result.get("deadpan_line")
        _dp_viol = deadpan_violations(_dp, title=title, artist=artist)
        if _dp_viol:
            raise ValueError(
                "deadpan_line tripped the placard guard (terminal hard-fail, no "
                "write performed): it " + "; it ".join(_dp_viol) + ". "
                + DEADPAN_RULES_NUDGE + " " + DEADPAN_CORRECTIVE_NUDGE
                + f" Offending deadpan: {_dp!r}"
            )

        # And the supplied effects prose. Same hole, same posture: the voice
        # constants were enforced only where the SERVER generates prose, so
        # operator-supplied blocks drifted on length, paragraph count, and the
        # stock tells. Lane + tier are passed explicitly (the collective-noun tell
        # is a listener-lane bleed; deficit language is honest on orange/red).
        # See prose_guard.py.
        from app.services.agents.prose_guard import (
            CORRECTIVE_NUDGE as PROSE_CORRECTIVE_NUDGE,
            PROSE_RULES_NUDGE,
            prose_violations,
        )
        _color = result.get("rubric_color") or ""
        for _lane, _key in (("listener", "listener_effects_prose"),
                            ("societal", "societal_effects_prose")):
            _viol_prose = prose_violations(result.get(_key), _lane, _color,
                                           title=title)
            if _viol_prose:
                raise ValueError(
                    f"{_key} tripped the prose guard (terminal hard-fail, no write "
                    "performed): it " + "; it ".join(_viol_prose) + ". "
                    + PROSE_RULES_NUDGE + " " + PROSE_CORRECTIVE_NUDGE
                )

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
    appearance_year = year if year is not None else date.today().year
    song_id, created = store_calibrated_song(
        db,
        source=source,
        title=title, artist=artist,
        calibration=result,
        chart_source=chart_source,
        year=appearance_year,
        chart_position=chart_position,
        chart_position_letter=chart_position_letter,
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
                    "reasoning": result.get("reasoning"),
                    # Calibrator v3 components + incoherence signals ride into
                    # the run ledger (log_run maps them to columns); absent on
                    # legacy/terminal-direct results and harmlessly NULL.
                    **{k: result[k] for k in (
                        "visceral_charge", "route", "harm", "transcendence",
                        "governing_axis", "center", "vernier", "precedent_refs",
                        "gut_divergence", "guard_trips", "parse_retries",
                        "escalation_flags", "escalated", "translated",
                        "calibration_failed",
                    ) if result.get(k) is not None},
                },
                triggered_by=triggered_by,
                direct_song_source="songs",
                direct_song_id=song_id,
                is_new_row=True,
                lyrics=lyrics,
                allow_prose_generation=allow_prose_generation,
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
        chart_source = song_in.get("chart_source", "spotify_top50_usa")

        # Cache lookup — short session
        cache_db = SessionLocal()
        try:
            cached = lookup_calibrated(title, artist, cache_db)
        finally:
            cache_db.close()

        if cached:
            enforce_contamination_rule(cached)
            logger.info("Cache hit: %s by %s", title, artist)
            # Record the chart appearance even on a cache hit, so a song that
            # first surfaces on a chart while already in the Library still gets
            # its chart_reading ingestion + origin_chart stamp. The cache-hit
            # branch skips the storage chokepoint, so without this the
            # gutter-migration signal (an existing song newly appearing on
            # Shazam/YouTube) would be invisible. Build 7. Fail-soft.
            if not draft_only and cached.get("song_id"):
                rec_db = SessionLocal()
                try:
                    from app.services.song_sync import record_chart_ingestion
                    record_chart_ingestion(rec_db, cached["song_id"], chart_source)
                    rec_db.commit()
                except Exception:
                    logger.exception("origin-chart record failed (cache hit): %s by %s", title, artist)
                    try:
                        rec_db.rollback()
                    except Exception:
                        pass
                finally:
                    rec_db.close()
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
            # Codified per-song disposition (feeder-agnostic): no cache hit + no
            # lyrics. Detect release-state -> auto-PREORDER for a charting-but-
            # unreleased single (no lyrics exist yet); else NEEDS_LYRICS. Fail-
            # open: any uncertainty stays NEEDS_LYRICS so a real released song is
            # never swallowed. Skipped under draft_only (case studies) + when the
            # detector errors. See app/services/disposition.py.
            is_preorder = False
            if not draft_only:
                try:
                    from app.services.disposition import (
                        resolve_draft_song_disposition, PREORDER,
                    )
                    disposition, detail = resolve_draft_song_disposition(
                        title, artist, is_cache_hit=False, lyrics_available=False,
                    )
                    if disposition == PREORDER:
                        is_preorder = True
                        logger.info("Auto-PREORDER: %s by %s (%s)", title, artist, detail)
                except Exception:
                    logger.exception("Disposition resolve failed for %s by %s", title, artist)

            if is_preorder:
                agent_notes_parts.append(
                    f"\"{title}\" detected as pre-order (unreleased) — auto-marked preorder")
                calibrated_songs.append({
                    "title": title,
                    "artist": artist,
                    "position": position,
                    "chart_source": chart_source,
                    "song_id": None,
                    "lyrics_available": False,
                    "preorder": True,
                    "rubric_color": None,
                    "charge_value": None,
                    "contaminated": False,
                    "contamination_note": None,
                    "charge_summary": "Charting on pre-order — awaiting release",
                    "confidence": 0.0,
                })
                continue

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
    degree = compute_degree(song_dicts, weighting=chart_weighting(draft_type))
    charge = degree_to_charge(degree)
    contam = count_contaminated(calibrated_songs)

    # Editorial is terminal-supplied (Claude Code); the server generates none.
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
    """Editorial is terminal-supplied (Claude Code), never generated server-side.

    The reading pipeline carries no rubric or editorial prompt: scoring runs
    through LEC and the editorial arrives via POST /drafts/{ref}/editorial
    (scripts/set_editorial.py). This stays a None-returning stub so its callers
    (draft creation + the approval regen) keep their existing fail-soft behavior.
    """
    return None
