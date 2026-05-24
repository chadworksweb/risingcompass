"""Backfill Console worker.

Long-running async task per job. One row at a time, sleeps 1s between
Opus calls (per build pack §10 — "polite to the API"). Each row runs
through the standard SOP:

    classifier → effects_prose → ether tagger

For target=compass we insert/update a CompassSong; for target=library
we insert/update a LibrarySong. Both targets pick up the ether columns
that migrations 042 + 043 added.

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
from datetime import datetime, date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import BackfillJob, BackfillJobRow, CompassSong, LibrarySong

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
                 "track_number", "lyrics")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


async def _process_row(ctx: _RowCtx) -> None:
    """Run calibrator + tagger for one row, then persist. Fail-soft."""
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

    # Ether tagger pass.
    ether: Optional[dict] = None
    if ctx.passes in ("tag", "both"):
        _set_row_status(ctx.row_id, "tagging")
        try:
            ether = await asyncio.to_thread(_run_tagger, ctx, result_source, result_id)
        except Exception as e:
            logger.exception("Tagger failed for backfill row %s", ctx.row_id)
            _fail_row(ctx.row_id, f"tagger: {e}")
            return

        if ether:
            _persist_ether(result_source, result_id, ether, ctx)

    _complete_row(ctx, result_source, result_id, calibration, ether)


def _persist_song(ctx: _RowCtx, calibration: Optional[dict]) -> tuple[str, int]:
    """Insert or update the song row in the target table.

    For tag-only jobs, calibration is None and we look up an existing
    row instead of creating one. Returns (source, id).
    """
    db: Session = SessionLocal()
    try:
        if ctx.target == "compass":
            return _persist_compass(db, ctx, calibration)
        elif ctx.target == "library":
            return _persist_library(db, ctx, calibration)
        else:
            raise ValueError(f"unknown target: {ctx.target}")
    finally:
        db.close()


def _persist_compass(db: Session, ctx: _RowCtx, calibration: Optional[dict]) -> tuple[str, int]:
    """Insert or update a CompassSong row."""
    existing = _find_compass(db, ctx.title, ctx.artist)

    if calibration is None:
        # tag-only: existing row is required
        if not existing:
            raise RuntimeError("tag-only pass: no existing compass_song to tag")
        return "compass", existing.id

    if existing:
        existing.rubric_color = calibration["rubric_color"]
        existing.charge_value = calibration.get("charge_value")
        existing.contaminated = bool(calibration.get("contaminated", False))
        existing.contamination_note = calibration.get("contamination_note")
        existing.dogma_referenced = bool(calibration.get("dogma_referenced", False))
        existing.dogma_note = calibration.get("dogma_note")
        existing.charge_summary = calibration.get("charge_summary")
        if ctx.year:
            existing.year = ctx.year
            existing.decade = f"{(ctx.year // 10) * 10}s"
        if ctx.chart_position:
            existing.chart_position = ctx.chart_position
        db.commit()
        return "compass", existing.id

    year = ctx.year or date.today().year
    decade = f"{(year // 10) * 10}s"
    song = CompassSong(
        title=ctx.title,
        artist=ctx.artist,
        year=year,
        decade=decade,
        chart_position=ctx.chart_position or 0,
        rubric_color=calibration["rubric_color"],
        charge_value=calibration.get("charge_value"),
        contaminated=bool(calibration.get("contaminated", False)),
        contamination_note=calibration.get("contamination_note"),
        dogma_referenced=bool(calibration.get("dogma_referenced", False)),
        dogma_note=calibration.get("dogma_note"),
        charge_summary=calibration.get("charge_summary"),
        chart_source="backfill_console",
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    return "compass", song.id


def _persist_library(db: Session, ctx: _RowCtx, calibration: Optional[dict]) -> tuple[str, int]:
    """Insert or update a LibrarySong row. Album linkage is left for a
    later pass — v1 just lands the song on title+artist."""
    existing = _find_library(db, ctx.title, ctx.artist)

    if calibration is None:
        if not existing:
            raise RuntimeError("tag-only pass: no existing library_song to tag")
        return "library", existing.id

    if existing:
        existing.rubric_color = calibration["rubric_color"]
        existing.charge_value = calibration.get("charge_value")
        existing.contaminated = bool(calibration.get("contaminated", False))
        existing.contamination_note = calibration.get("contamination_note")
        existing.dogma_referenced = bool(calibration.get("dogma_referenced", False))
        existing.dogma_note = calibration.get("dogma_note")
        existing.charge_summary = calibration.get("charge_summary")
        if ctx.album_id:
            existing.album_id = ctx.album_id
        if ctx.track_number:
            existing.track_number = ctx.track_number
        db.commit()
        return "library", existing.id

    song = LibrarySong(
        title=ctx.title,
        artist=ctx.artist,
        rubric_color=calibration["rubric_color"],
        charge_value=calibration.get("charge_value"),
        contaminated=bool(calibration.get("contaminated", False)),
        contamination_note=calibration.get("contamination_note"),
        dogma_referenced=bool(calibration.get("dogma_referenced", False)),
        dogma_note=calibration.get("dogma_note"),
        charge_summary=calibration.get("charge_summary"),
        album_id=ctx.album_id,
        track_number=ctx.track_number,
        source="backfill_console",
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    return "library", song.id


def _find_compass(db: Session, title: str, artist: str) -> Optional[CompassSong]:
    return (
        db.query(CompassSong)
        .filter(func.lower(CompassSong.title) == title.lower())
        .filter(func.lower(CompassSong.artist) == artist.lower())
        .first()
    )


def _find_library(db: Session, title: str, artist: str) -> Optional[LibrarySong]:
    return (
        db.query(LibrarySong)
        .filter(func.lower(LibrarySong.title) == title.lower())
        .filter(func.lower(LibrarySong.artist) == artist.lower())
        .first()
    )


def _run_tagger(ctx: _RowCtx, source: str, song_id: int) -> Optional[dict]:
    """Synchronous wrapper that the engine awaits via asyncio.to_thread."""
    from app.services.agents.ether_tagger import tag_song

    # Pull current calibration + effects_prose from the DB to ground the tagger.
    db: Session = SessionLocal()
    try:
        if source == "compass":
            row = db.query(CompassSong).filter(CompassSong.id == song_id).first()
        else:
            row = db.query(LibrarySong).filter(LibrarySong.id == song_id).first()
        if not row:
            return None
        rubric_color = row.rubric_color
        charge_value = row.charge_value
        charge_summary = row.charge_summary
        effects_prose = getattr(row, "effects_prose", None)
    finally:
        db.close()

    return tag_song(
        title=ctx.title,
        artist=ctx.artist,
        lyrics=ctx.lyrics,
        rubric_color=rubric_color,
        charge_value=charge_value,
        charge_summary=charge_summary,
        effects_prose=effects_prose,
    )


def _persist_ether(source: str, song_id: int, ether: dict, ctx: _RowCtx) -> None:
    """Write the tagger output back to the song row + fire audit email
    if the agent flagged the row."""
    db: Session = SessionLocal()
    try:
        if source == "compass":
            row = db.query(CompassSong).filter(CompassSong.id == song_id).first()
        else:
            row = db.query(LibrarySong).filter(LibrarySong.id == song_id).first()
        if not row:
            return
        row.deadpan_line = ether["deadpan_line"]
        row.topics = json.dumps(ether["topics"])
        row.topic_audit = (
            json.dumps(ether["topic_audit"]) if ether["topic_audit"] else None
        )
        db.commit()

        if ether["topic_audit"]:
            try:
                from app.services.agents.ether_audit_notifier import send_ether_audit_email
                send_ether_audit_email(
                    song_id=song_id,
                    title=ctx.title,
                    artist=ctx.artist,
                    audit=ether["topic_audit"],
                    settings=settings,
                )
            except Exception:
                logger.exception("Audit notify failed for backfill song %s/%s", source, song_id)
    finally:
        db.close()


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
