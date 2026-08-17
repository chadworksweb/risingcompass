"""Backfill Console job orchestrator.

Holds the in-process registry of running asyncio tasks (one per
running BackfillJob) and exposes start / pause / resume / cancel.
Pause / resume is DB-driven (the worker checks `paused_flag` every
iteration); cancellation flips the job state and the worker exits on
its next state read.

On app startup, any job left in `running` from a prior process is
demoted to `paused`. The admin then explicitly resumes — we never
auto-resume because the admin may not want the job back.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import BackfillJob

from .engine import process_job

logger = logging.getLogger(__name__)


# Module-level registry: job_id → asyncio.Task. The Task may be done
# (worker exited because paused, completed, etc.) — we don't proactively
# remove finished tasks, just check `task.done()` when we look.
_TASKS: dict[int, asyncio.Task] = {}


def reset_running_jobs_on_startup() -> None:
    """Demote any job left in `running` to `paused`. Called from
    main.py during the FastAPI lifespan startup."""
    db: Session = SessionLocal()
    try:
        rows = db.query(BackfillJob).filter(BackfillJob.status == "running").all()
        for job in rows:
            job.status = "paused"
            job.paused_flag = 1
            job.note = ((job.note or "") + "\n[startup] auto-paused — process restart").strip()[:4000]
        if rows:
            db.commit()
            logger.info("Backfill: auto-paused %d running job(s) on startup", len(rows))
    finally:
        db.close()


def start_job(job_id: int) -> None:
    """Spawn an asyncio task to drive the job. Idempotent — if a live
    task already exists for this job, we leave it alone."""
    existing = _TASKS.get(job_id)
    if existing and not existing.done():
        logger.info("Backfill: job %s already running, skipping start", job_id)
        return

    db: Session = SessionLocal()
    try:
        job = db.query(BackfillJob).filter(BackfillJob.id == job_id).first()
        if not job:
            raise ValueError(f"backfill job {job_id} not found")
        # Always clear the pause flag when starting; status will be set by
        # the worker on its first iteration.
        job.paused_flag = 0
        if job.status in ("queued", "paused"):
            # leave to engine; it'll mark running
            pass
        elif job.status in ("completed", "cancelled", "failed"):
            raise ValueError(f"job {job_id} is in terminal state {job.status}")
        db.commit()
    finally:
        db.close()

    task = asyncio.create_task(process_job(job_id), name=f"backfill-job-{job_id}")
    _TASKS[job_id] = task
    logger.info("Backfill: started worker task for job %s", job_id)


def pause_job(job_id: int) -> None:
    """Set paused_flag = 1. Worker exits gracefully on its next state read."""
    db: Session = SessionLocal()
    try:
        job = db.query(BackfillJob).filter(BackfillJob.id == job_id).first()
        if not job:
            raise ValueError(f"backfill job {job_id} not found")
        job.paused_flag = 1
        if job.status == "running":
            job.status = "paused"
        db.commit()
    finally:
        db.close()


def cancel_job(job_id: int) -> None:
    """Mark job cancelled. Worker exits on next state read; in-flight
    Opus call (if any) finishes and writes its result before exit."""
    db: Session = SessionLocal()
    try:
        job = db.query(BackfillJob).filter(BackfillJob.id == job_id).first()
        if not job:
            raise ValueError(f"backfill job {job_id} not found")
        job.status = "cancelled"
        job.paused_flag = 1
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def task_status(job_id: int) -> Optional[str]:
    """Diagnostic: return the asyncio task's current state if known."""
    task = _TASKS.get(job_id)
    if task is None:
        return None
    if task.done():
        return "done"
    return "alive"
