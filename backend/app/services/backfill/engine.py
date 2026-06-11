"""Backfill Console worker.

Long-running async task per job. One row at a time, sleeps 1s between
Opus calls (per build pack §10 — "polite to the API"). Each row runs
through the one calibration path, which produces the whole object:

    rubric calibration → listener_effects_prose → ether tagging → societal_effects_prose

Native (Phase 5b): both targets land on the unified `songs` table via
store_calibrated_song -- compass = chart_reading method (the 'backfill_console'
source is non-chart, so no chart_appearance), library = catalog_backfill method with
album linkage. Both pick up the ether columns the calibration path produces.

State machine on the row:
    queued → calibrating → tagging → done
                       ↘ failed   (logged with error message)
                       ↘ skipped  (deliberately bypassed)
            → needs_lyrics  (paste-only mode and no lyrics yet)

The worker exits when:
  * status flips to cancelled / completed / failed
  * no actionable rows remain (all done, failed, or awaiting lyrics)

When rows are awaiting lyrics, the worker sets the job back to paused
and exits — admin pastes lyrics in the UI and re-resumes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import BackfillJob, BackfillJobRow, Song
from app.services.song_identity import compute_canonical_key
from app.services.song_sync import store_calibrated_song
from app.services.artist_linker import parse_artist_string

logger = logging.getLogger(__name__)

# How long the worker waits between rows when it can't act (no actionable
# row, or paused). Short enough that an admin pasting lyrics + resuming
# feels responsive.
IDLE_SLEEP_SEC = 2.0
# Sleep between rows that DID work — keeps Opus calls polite.
ROW_SLEEP_SEC = 1.0


# --- Public entry point -------------------------------------------------

async def process_job(job_id: int) -> None:
    """Drive a backfill job to terminal state.

    Safe to crash and restart — the loop reads job + row state from the
    DB on every iteration, so partial progress is not lost.
    """
    while True:
        try:
            outcome = await _step(job_id)
        except Exception:
            logger.exception("Backfill engine crashed mid-step (job=%s)", job_id)
            _mark_job_failed(job_id, "engine crash; see server logs")
            return

        if outcome == "exit":
            return
        if outcome == "idle":
            await asyncio.sleep(IDLE_SLEEP_SEC)
        else:  # outcome == "worked"
            await asyncio.sleep(ROW_SLEEP_SEC)


# --- Step ---------------------------------------------------------------

async def _step(job_id: int) -> str:
    """Process at most one row. Returns 'exit', 'idle', or 'worked'."""
    db: Session = SessionLocal()
    try:
        job = db.query(BackfillJob).filter(BackfillJob.id == job_id).first()
        if not job:
            return "exit"

        if job.status in ("cancelled", "completed", "failed"):
            return "exit"

        # Honour pause flag — stay alive but don't pick up new rows.
        if job.paused_flag:
            if job.status != "paused":
                job.status = "paused"
                db.commit()
            return "idle"

        # Mark running on the first iteration.
        if job.status != "running":
            job.status = "running"
            if not job.started_at:
                job.started_at = datetime.utcnow()
            db.commit()

        row = (
            db.query(BackfillJobRow)
            .filter(BackfillJobRow.job_id == job_id)
            .filter(BackfillJobRow.status.in_(("queued", "needs_lyrics")))
            .order_by(BackfillJobRow.position.asc())
            .first()
        )
        if not row:
            return _finish_or_park(db, job)

        # Lyrics are paste-only in v1. If a row has no lyrics, mark it
        # needs_lyrics and look at the next one — but if EVERY remaining
        # row is needs_lyrics, the worker has nothing to do and exits.
        if not row.lyrics or not row.lyrics.strip():
            if row.status != "needs_lyrics":
                row.status = "needs_lyrics"
                row.updated_at = datetime.utcnow()
                db.commit()
            # Are there any rows that ARE actionable?
            actionable = (
                db.query(BackfillJobRow)
                .filter(BackfillJobRow.job_id == job_id)
                .filter(BackfillJobRow.status == "queued")
                .filter(BackfillJobRow.lyrics.isnot(None))
                .filter(func.length(func.trim(BackfillJobRow.lyrics)) > 0)
                .count()
            )
            if actionable == 0:
                return _finish_or_park(db, job)
            # Some rows ARE actionable — but our query above ordered by
            # position and got a needs_lyrics row first. Return idle and
            # try again; on the next pass the actionable row gets picked
            # because needs_lyrics is filtered out by the order/state.
            return "idle"

        # Snapshot row context and close the session so we don't hold a
        # pooled connection idle during the Opus call.
        ctx = _RowCtx(
            row_id=row.id,
            job_id=job.id,
            target=job.target_table,
            passes=job.passes,
            title=row.title,
            artist=row.artist,
            year=row.year,
            chart_position=row.chart_position,
            album_id=row.album_id,
            track_number=row.track_number,
            lyrics=row.lyrics,
            lyrics_source=row.lyrics_source,
        )
    finally:
        db.close()

    await _process_row(ctx)
    return "worked"


def _finish_or_park(db: Session, job: BackfillJob) -> str:
    """No actionable row left. Either complete the job, or park it as
    paused if rows are still awaiting lyrics from the admin."""
    needs = (
        db.query(BackfillJobRow)
        .filter(BackfillJobRow.job_id == job.id)
        .filter(BackfillJobRow.status == "needs_lyrics")
        .count()
    )
    if needs > 0:
        job.status = "paused"
        job.paused_flag = 1
        db.commit()
        logger.info("Backfill job %s parked: %d rows awaiting lyrics", job.id, needs)
        return "exit"

    job.status = "completed"
    job.completed_at = datetime.utcnow()
    db.commit()
    logger.info("Backfill job %s completed", job.id)
    return "exit"


# --- Row processing -----------------------------------------------------

class _RowCtx:
    """Plain data carrier for row work outside a DB session."""
    __slots__ = ("row_id", "job_id", "target", "passes", "title",
                 "artist", "year", "chart_position", "album_id",
                 "track_number", "lyrics", "lyrics_source")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


async def _process_row(ctx: _RowCtx) -> None:
    """Run the calibration path (which now calibrates + tags + proses) for one
    row, then persist. Tag-only passes skip calibration and re-tag in place.
    Fail-soft."""
    # Legal guard: never feed Musixmatch-sourced lyrics to the AI calibrator.
    # Musixmatch API ToS (24 Jun 2025) 2.2.14 forbids using their data to prompt
    # an AI system. Lyrics for calibration must come from paste or manual admin
    # supply only. (The Musixmatch fetch path is itself disabled in
    # services/musixmatch.py; this is the defense at the calibration boundary.)
    if (ctx.lyrics_source or "").strip().lower() == "musixmatch":
        _fail_row(
            ctx.row_id,
            "blocked: Musixmatch-sourced lyrics cannot be fed to the AI calibrator (ToS 2.2.14)",
        )
        return

    _set_row_status(ctx.row_id, "calibrating")

    calibration: Optional[dict] = None
    try:
        if ctx.passes in ("calibrate", "both"):
            from app.services.agents.calibrator import calibrate_song_async
            calibration = await calibrate_song_async(
                ctx.title, ctx.artist, lyrics=ctx.lyrics,
                target_year=ctx.year, skip_cache=True,
            )
            if not calibration or not calibration.get("rubric_color"):
                _fail_row(ctx.row_id, "calibrator returned no rubric_color")
                return
    except Exception as e:
        logger.exception("Calibrator failed for backfill row %s", ctx.row_id)
        _fail_row(ctx.row_id, f"calibrator: {e}")
        return

    # Persist the song row (insert or update).
    try:
        result_source, result_id = _persist_song(ctx, calibration)
    except Exception as e:
        logger.exception("Song persist failed for backfill row %s", ctx.row_id)
        _fail_row(ctx.row_id, f"persist: {e}")
        return

    # Ether tagging.
    ether: Optional[dict] = None
    if ctx.passes == "tag":
        # Tag-only maintenance pass: re-tag an existing row without
        # recalibrating (e.g. after a taxonomy change). The standalone tagger
        # is the right tool here -- no fresh calibration to fold it into.
        _set_row_status(ctx.row_id, "tagging")
        try:
            ether = await asyncio.to_thread(_run_tagger, ctx, result_source, result_id)
        except Exception as e:
            logger.exception("Tagger failed for backfill row %s", ctx.row_id)
            _fail_row(ctx.row_id, f"tagger: {e}")
            return
        if ether:
            _persist_ether(result_source, result_id, ether, ctx)
    elif calibration is not None:
        # calibrate / both: the calibration path already produced + persisted
        # the ether tags (via _persist_song -> store_calibrated_song). Surface
        # them for the job-row record and fire the no-match audit if needed.
        ether = {
            "deadpan_line": calibration.get("deadpan_line"),
            "topics": calibration.get("topics") or [],
            "topic_audit": calibration.get("topic_audit"),
        }
        _send_ether_audit(result_source, result_id, ctx, calibration.get("topic_audit"))

    _complete_row(ctx, result_source, result_id, calibration, ether)


def _send_ether_audit(source: str, song_id: int, ctx: "_RowCtx", topic_audit) -> None:
    """Fire the admin notification when the ether tagger found no taxonomy match."""
    if not topic_audit:
        return
    try:
        from app.services.agents.ether_audit_notifier import send_ether_audit_email
        send_ether_audit_email(
            song_id=song_id, title=ctx.title, artist=ctx.artist,
            audit=topic_audit, settings=settings,
        )
    except Exception:
        logger.exception("Audit notify failed for backfill song %s/%s", source, song_id)


def _persist_song(ctx: _RowCtx, calibration: Optional[dict]) -> tuple[str, int]:
    """Insert or update the unified songs row for one backfill row.

    Native (Phase 5b): both targets land on the atomic `songs` table via
    store_calibrated_song -- compass = chart_reading method (chart_source
    'backfill_console' is non-chart, so no chart_appearance), library =
    catalog_backfill method with album linkage. For tag-only jobs (calibration is
    None) we look up the existing unified song instead of creating one.
    Returns ("songs", id)."""
    if ctx.target == "compass":
        source = "compass"
        extra = {"chart_source": "backfill_console", "year": ctx.year,
                 "chart_position": ctx.chart_position}
        ingestion_detail = {"chart_source": "backfill_console"}
    elif ctx.target == "library":
        source = "library"
        extra = {"album_id": ctx.album_id, "track_number": ctx.track_number}
        ingestion_detail = {"source": "backfill_console"}
    else:
        raise ValueError(f"unknown target: {ctx.target}")

    db: Session = SessionLocal()
    try:
        if calibration is None:
            # tag-only: existing row is required
            existing = _find_song(db, ctx.title, ctx.artist)
            if not existing:
                raise RuntimeError(f"tag-only pass: no existing song to tag ({ctx.target})")
            return "songs", existing.id

        song_id, _created = store_calibrated_song(
            db, source=source, title=ctx.title, artist=ctx.artist,
            calibration=calibration, ingestion_detail=ingestion_detail,
            artist_entries=parse_artist_string(ctx.artist or ""),
            **extra,
        )
        db.commit()
        if song_id is None:
            raise RuntimeError("store_calibrated_song produced no row (no rubric_color)")
        return "songs", song_id
    finally:
        db.close()


def _find_song(db: Session, title: str, artist: str) -> Optional[Song]:
    key = compute_canonical_key(title, artist)
    return db.query(Song).filter(Song.canonical_key == key).first()


def _run_tagger(ctx: _RowCtx, source: str, song_id: int) -> Optional[dict]:
    """Synchronous wrapper that the engine awaits via asyncio.to_thread."""
    from app.services.agents.ether_tagger import tag_song

    # Pull current calibration + listener_effects_prose from the DB to ground the tagger.
    db: Session = SessionLocal()
    try:
        row = db.query(Song).filter(Song.id == song_id).first()
        if not row:
            return None
        rubric_color = row.rubric_color
        charge_value = row.charge_value
        charge_summary = row.charge_summary
        listener_effects_prose = getattr(row, "listener_effects_prose", None)
    finally:
        db.close()

    return tag_song(
        title=ctx.title,
        artist=ctx.artist,
        lyrics=ctx.lyrics,
        rubric_color=rubric_color,
        charge_value=charge_value,
        charge_summary=charge_summary,
        listener_effects_prose=listener_effects_prose,
    )


def _persist_ether(source: str, song_id: int, ether: dict, ctx: _RowCtx) -> None:
    """Write the tagger output back to the song row + fire audit email
    if the agent flagged the row."""
    db: Session = SessionLocal()
    try:
        row = db.query(Song).filter(Song.id == song_id).first()
        if not row:
            return
        row.deadpan_line = ether["deadpan_line"]
        row.topics = json.dumps(ether["topics"])
        row.topic_audit = (
            json.dumps(ether["topic_audit"]) if ether["topic_audit"] else None
        )
        db.commit()
    finally:
        db.close()

    _send_ether_audit(source, song_id, ctx, ether.get("topic_audit"))


# --- Status helpers -----------------------------------------------------

def _set_row_status(row_id: int, status: str) -> None:
    db: Session = SessionLocal()
    try:
        row = db.query(BackfillJobRow).filter(BackfillJobRow.id == row_id).first()
        if not row:
            return
        row.status = status
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _fail_row(row_id: int, error: str) -> None:
    db: Session = SessionLocal()
    try:
        row = db.query(BackfillJobRow).filter(BackfillJobRow.id == row_id).first()
        if not row:
            return
        row.status = "failed"
        row.error = (error or "")[:1000]
        row.updated_at = datetime.utcnow()
        # Bump job counter
        job = db.query(BackfillJob).filter(BackfillJob.id == row.job_id).first()
        if job:
            job.failed_rows = (job.failed_rows or 0) + 1
        db.commit()
    finally:
        db.close()


def _complete_row(
    ctx: _RowCtx,
    source: str,
    song_id: int,
    calibration: Optional[dict],
    ether: Optional[dict],
) -> None:
    db: Session = SessionLocal()
    try:
        row = db.query(BackfillJobRow).filter(BackfillJobRow.id == ctx.row_id).first()
        if not row:
            return
        row.status = "done"
        row.result_song_source = source
        row.result_song_id = song_id
        if calibration:
            row.rubric_color = calibration.get("rubric_color")
            row.charge_value = calibration.get("charge_value")
        if ether:
            row.deadpan_line = ether.get("deadpan_line")
            row.topics = json.dumps(ether.get("topics") or [])
            row.topic_audit = (
                json.dumps(ether["topic_audit"]) if ether.get("topic_audit") else None
            )
        row.updated_at = datetime.utcnow()
        # Bump job counter
        job = db.query(BackfillJob).filter(BackfillJob.id == ctx.job_id).first()
        if job:
            job.completed_rows = (job.completed_rows or 0) + 1
        db.commit()
    finally:
        db.close()


def _mark_job_failed(job_id: int, reason: str) -> None:
    db: Session = SessionLocal()
    try:
        job = db.query(BackfillJob).filter(BackfillJob.id == job_id).first()
        if not job:
            return
        job.status = "failed"
        job.note = ((job.note or "") + f"\n[engine] {reason}").strip()[:4000]
        db.commit()
    finally:
        db.close()
