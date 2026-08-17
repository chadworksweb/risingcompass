"""Backfill Console admin endpoints.

Drives the unified backfill UI. Every endpoint is session-gated via
the legacy verify_admin_key alias (which now points at
require_admin_session per app/auth.py).

Cost note for future readers: every job that hits the engine fires
Opus calls (calibrate + tag = 2 per row). Recurring use should prefer
the Claude Code-driven manual-results path; this server-side
automation exists for hands-off runs you start and walk away from.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BackfillJob, BackfillJobRow
from app.routers.admin import verify_admin_key
from app.services.backfill import orchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/backfill", tags=["backfill-admin"])


VALID_TARGETS = {"compass", "library"}
VALID_PASSES = {"calibrate", "tag", "both"}


# --- Schemas ---

class JobRowIn(BaseModel):
    title: str = Field(..., min_length=1)
    artist: str = Field(..., min_length=1)
    year: Optional[int] = None
    chart_position: Optional[int] = None
    album_id: Optional[int] = None
    track_number: Optional[int] = None
    lyrics: Optional[str] = None  # paste-up-front; can be added later via PATCH


class JobCreateIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    target_table: str
    passes: str
    note: Optional[str] = None
    rows: list[JobRowIn]


class JobOut(BaseModel):
    id: int
    label: str
    target_table: str
    passes: str
    status: str
    paused_flag: int
    total_rows: int
    completed_rows: int
    failed_rows: int
    note: Optional[str]
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    task_state: Optional[str] = None  # 'alive' | 'done' | None


class JobListOut(BaseModel):
    items: list[JobOut]


class RowOut(BaseModel):
    id: int
    job_id: int
    position: int
    title: str
    artist: str
    year: Optional[int]
    chart_position: Optional[int]
    album_id: Optional[int]
    track_number: Optional[int]
    has_lyrics: bool
    lyrics_source: Optional[str]
    status: str
    error: Optional[str]
    result_song_source: Optional[str]
    result_song_id: Optional[int]
    rubric_color: Optional[str]
    charge_value: Optional[int]
    deadpan_line: Optional[str]
    topics_json: Optional[str]
    topic_audit_json: Optional[str]
    updated_at: Optional[str]


class JobDetailOut(BaseModel):
    job: JobOut
    rows: list[RowOut]


class LyricsIn(BaseModel):
    lyrics: str = Field(..., min_length=1)


class MusixmatchSearchOut(BaseModel):
    available: bool
    results: list[dict] = []


# --- Helpers ---

def _job_to_out(job: BackfillJob) -> JobOut:
    return JobOut(
        id=job.id,
        label=job.label,
        target_table=job.target_table,
        passes=job.passes,
        status=job.status,
        paused_flag=int(job.paused_flag or 0),
        total_rows=job.total_rows or 0,
        completed_rows=job.completed_rows or 0,
        failed_rows=job.failed_rows or 0,
        note=job.note,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        task_state=orchestrator.task_status(job.id),
    )


def _row_to_out(row: BackfillJobRow) -> RowOut:
    return RowOut(
        id=row.id,
        job_id=row.job_id,
        position=row.position,
        title=row.title,
        artist=row.artist,
        year=row.year,
        chart_position=row.chart_position,
        album_id=row.album_id,
        track_number=row.track_number,
        has_lyrics=bool(row.lyrics and row.lyrics.strip()),
        lyrics_source=row.lyrics_source,
        status=row.status,
        error=row.error,
        result_song_source=row.result_song_source,
        result_song_id=row.result_song_id,
        rubric_color=row.rubric_color,
        charge_value=row.charge_value,
        deadpan_line=row.deadpan_line,
        topics_json=row.topics,
        topic_audit_json=row.topic_audit,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _load_job(db: Session, job_id: int) -> BackfillJob:
    job = db.query(BackfillJob).filter(BackfillJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "backfill job not found")
    return job


def _load_row(db: Session, job_id: int, row_id: int) -> BackfillJobRow:
    row = (
        db.query(BackfillJobRow)
        .filter(BackfillJobRow.id == row_id)
        .filter(BackfillJobRow.job_id == job_id)
        .first()
    )
    if not row:
        raise HTTPException(404, "backfill row not found")
    return row


# --- Job CRUD ---

@router.get("/jobs", response_model=JobListOut, dependencies=[Depends(verify_admin_key)])
def list_jobs(db: Session = Depends(get_db)):
    jobs = (
        db.query(BackfillJob)
        .order_by(BackfillJob.created_at.desc().nullslast(), BackfillJob.id.desc())
        .limit(200)
        .all()
    )
    return JobListOut(items=[_job_to_out(j) for j in jobs])


@router.get("/jobs/{job_id}", response_model=JobDetailOut, dependencies=[Depends(verify_admin_key)])
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = _load_job(db, job_id)
    rows = (
        db.query(BackfillJobRow)
        .filter(BackfillJobRow.job_id == job_id)
        .order_by(BackfillJobRow.position.asc(), BackfillJobRow.id.asc())
        .all()
    )
    return JobDetailOut(job=_job_to_out(job), rows=[_row_to_out(r) for r in rows])


@router.post("/jobs", response_model=JobOut, dependencies=[Depends(verify_admin_key)])
def create_job(data: JobCreateIn, request: Request, db: Session = Depends(get_db)):
    if data.target_table not in VALID_TARGETS:
        raise HTTPException(400, f"target_table must be one of {sorted(VALID_TARGETS)}")
    if data.passes not in VALID_PASSES:
        raise HTTPException(400, f"passes must be one of {sorted(VALID_PASSES)}")
    if not data.rows:
        raise HTTPException(400, "rows must be non-empty")

    admin_user_id = getattr(request.state, "admin_user_id", None)

    job = BackfillJob(
        label=data.label.strip(),
        target_table=data.target_table,
        passes=data.passes,
        status="queued",
        paused_flag=0,
        total_rows=len(data.rows),
        completed_rows=0,
        failed_rows=0,
        note=(data.note or "").strip() or None,
        created_by=admin_user_id,
    )
    db.add(job)
    db.flush()

    for idx, r in enumerate(data.rows, start=1):
        lyrics_clean = (r.lyrics or "").strip() or None
        db.add(BackfillJobRow(
            job_id=job.id,
            position=idx,
            title=r.title.strip(),
            artist=r.artist.strip(),
            year=r.year,
            chart_position=r.chart_position,
            album_id=r.album_id,
            track_number=r.track_number,
            lyrics=lyrics_clean,
            lyrics_source="paste" if lyrics_clean else None,
            status="queued" if lyrics_clean else "needs_lyrics",
        ))

    db.commit()
    db.refresh(job)
    logger.info("Backfill job %s created (target=%s passes=%s rows=%d)",
                job.id, job.target_table, job.passes, job.total_rows)
    return _job_to_out(job)


# --- Lifecycle actions ---

@router.post("/jobs/{job_id}/start", response_model=JobOut, dependencies=[Depends(verify_admin_key)])
def start_job(job_id: int, db: Session = Depends(get_db)):
    job = _load_job(db, job_id)
    if job.status in ("completed", "cancelled", "failed"):
        raise HTTPException(409, f"job is in terminal state {job.status}")
    try:
        orchestrator.start_job(job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.refresh(job)
    return _job_to_out(job)


@router.post("/jobs/{job_id}/pause", response_model=JobOut, dependencies=[Depends(verify_admin_key)])
def pause_job(job_id: int, db: Session = Depends(get_db)):
    _load_job(db, job_id)
    orchestrator.pause_job(job_id)
    job = _load_job(db, job_id)
    return _job_to_out(job)


@router.post("/jobs/{job_id}/resume", response_model=JobOut, dependencies=[Depends(verify_admin_key)])
def resume_job(job_id: int, db: Session = Depends(get_db)):
    job = _load_job(db, job_id)
    if job.status in ("completed", "cancelled", "failed"):
        raise HTTPException(409, f"job is in terminal state {job.status}")
    orchestrator.start_job(job_id)
    db.refresh(job)
    return _job_to_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, dependencies=[Depends(verify_admin_key)])
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    _load_job(db, job_id)
    orchestrator.cancel_job(job_id)
    job = _load_job(db, job_id)
    return _job_to_out(job)


# --- Per-row actions ---

@router.post("/jobs/{job_id}/rows/{row_id}/lyrics",
             response_model=RowOut,
             dependencies=[Depends(verify_admin_key)])
def supply_lyrics(job_id: int, row_id: int, data: LyricsIn, db: Session = Depends(get_db)):
    """Admin paste-up. If the row was waiting in needs_lyrics, flip it
    back to queued so the engine picks it up on the next pass."""
    row = _load_row(db, job_id, row_id)
    row.lyrics = data.lyrics.strip()
    row.lyrics_source = "paste"
    if row.status == "needs_lyrics":
        row.status = "queued"
        row.error = None
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _row_to_out(row)


@router.post("/jobs/{job_id}/rows/{row_id}/skip",
             response_model=RowOut,
             dependencies=[Depends(verify_admin_key)])
def skip_row(job_id: int, row_id: int, db: Session = Depends(get_db)):
    row = _load_row(db, job_id, row_id)
    row.status = "skipped"
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _row_to_out(row)


@router.post("/jobs/{job_id}/rows/{row_id}/retry",
             response_model=RowOut,
             dependencies=[Depends(verify_admin_key)])
def retry_row(job_id: int, row_id: int, db: Session = Depends(get_db)):
    row = _load_row(db, job_id, row_id)
    if row.status not in ("failed", "skipped"):
        raise HTTPException(409, f"row is not in a retry-able state ({row.status})")
    if not row.lyrics or not row.lyrics.strip():
        row.status = "needs_lyrics"
    else:
        row.status = "queued"
    row.error = None
    row.updated_at = datetime.utcnow()
    # Don't decrement failed_rows — it counts attempts, not currently-failed.
    db.commit()
    db.refresh(row)
    return _row_to_out(row)


# --- Musixmatch helpers (wired but lazy-checked) ---

@router.get("/musixmatch/search",
            response_model=MusixmatchSearchOut,
            dependencies=[Depends(verify_admin_key)])
async def musixmatch_search(title: str, artist: str = ""):
    from app.services import musixmatch
    if not musixmatch.is_configured():
        return MusixmatchSearchOut(available=False, results=[])
    results = await musixmatch.search_tracks(title, artist)
    return MusixmatchSearchOut(available=True, results=results)


@router.post("/jobs/{job_id}/rows/{row_id}/musixmatch-fetch",
             response_model=RowOut,
             dependencies=[Depends(verify_admin_key)])
async def musixmatch_fetch(job_id: int, row_id: int, track_id: int,
                            db: Session = Depends(get_db)):
    from app.services import musixmatch
    if not musixmatch.is_configured():
        raise HTTPException(501, "Musixmatch is not configured on this server")

    row = _load_row(db, job_id, row_id)
    lyrics = await musixmatch.get_lyrics(track_id)
    if not lyrics:
        raise HTTPException(404, "Musixmatch returned no lyrics for that track")
    row.lyrics = lyrics.strip()
    row.lyrics_source = "musixmatch"
    if row.status == "needs_lyrics":
        row.status = "queued"
        row.error = None
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _row_to_out(row)
