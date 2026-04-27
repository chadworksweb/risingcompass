"""Per-chart daily snapshot endpoints — public read + admin/cron refresh.

The canonical daily reading still flows through compass.py + agent.py. This
router is the lighter "show today's chart top 20 as a panel" mechanism for
secondary charts (Spotify Viral 50 today; Apple Music / Billboard / etc.
later). Charge values come from compass_songs via case-insensitive lookup.

Adding a new chart = register one entry in CHART_REGISTRY plus a fetcher
in services/agents/chart_source.py. No schema change.

The refresh endpoint also auto-fills compass_songs for any chart entries
that aren't there yet — it spawns a Backfill Console job
(target=compass, passes=both) so every chart self-calibrates over time
without manual intervention.
"""

import asyncio
import logging
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import verify_reading_cron_key
from app.database import get_db
from app.models import BackfillJob, BackfillJobRow, ChartSnapshot, CompassSong
from app.schemas import ReadingSongOut
from app.services.agents.chart_source import fetch_top_songs, fetch_viral_songs
from app.services.artist_utils import generate_song_slug
from app.services.backfill import orchestrator

logger = logging.getLogger(__name__)


CHART_REGISTRY: dict[str, dict] = {
    "viral": {
        "slug": "spotify_viral50_usa",
        "label": "Spotify Viral 50 — USA",
        "fetcher": fetch_viral_songs,
    },
    "top50": {
        "slug": "spotify_top50_usa",
        "label": "Spotify Top 50 — USA",
        "fetcher": fetch_top_songs,
    },
}


class ChartSnapshotOut(BaseModel):
    chart_source: str
    label: str
    date: date
    songs: list[ReadingSongOut]


def _build_song(snap: ChartSnapshot, cs: CompassSong | None) -> ReadingSongOut:
    return ReadingSongOut(
        id=snap.id,
        title=snap.title,
        artist=snap.artist,
        position=snap.position,
        rubric_color=cs.rubric_color if cs else None,
        charge_value=cs.charge_value if cs else None,
        contaminated=bool(cs.contaminated) if cs else False,
        contamination_note=cs.contamination_note if cs else None,
        charge_summary=cs.charge_summary if cs else None,
        chart_source=snap.chart_source,
        instrumental=bool(cs.instrumental) if cs else False,
        song_slug=generate_song_slug(snap.title, snap.artist),
    )


def _lookup_compass_song(db: Session, title: str, artist: str) -> CompassSong | None:
    """Case-insensitive title+artist match against compass_songs."""
    return (
        db.query(CompassSong)
        .filter(func.lower(CompassSong.title) == title.strip().lower())
        .filter(func.lower(CompassSong.artist) == artist.strip().lower())
        .first()
    )


def _replace_snapshot(db: Session, chart_slug: str, snapshot_date: date, songs: list[dict]) -> int:
    """Wipe + rewrite today's rows for this chart. Idempotent across same-day reruns."""
    db.query(ChartSnapshot).filter(
        ChartSnapshot.chart_source == chart_slug,
        ChartSnapshot.date == snapshot_date,
    ).delete()
    db.flush()

    for s in songs:
        db.add(ChartSnapshot(
            date=snapshot_date,
            chart_source=chart_slug,
            position=int(s["position"]),
            title=s["title"],
            artist=s["artist"],
        ))
    db.commit()
    return len(songs)


# --- Public read endpoint -------------------------------------------------

public_router = APIRouter(prefix="/api/compass/chart", tags=["chart-snapshots"])


@public_router.get("/{key}/current", response_model=ChartSnapshotOut)
def get_current_snapshot(key: str, db: Session = Depends(get_db)):
    """Today's snapshot for the named chart, or the most recent if today's hasn't run."""
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")

    chart_slug = entry["slug"]

    most_recent_date = (
        db.query(func.max(ChartSnapshot.date))
        .filter(ChartSnapshot.chart_source == chart_slug)
        .scalar()
    )
    if not most_recent_date:
        raise HTTPException(status_code=404, detail="No snapshots yet for this chart")

    snaps = (
        db.query(ChartSnapshot)
        .filter(ChartSnapshot.chart_source == chart_slug, ChartSnapshot.date == most_recent_date)
        .order_by(ChartSnapshot.position.asc())
        .all()
    )

    songs = [_build_song(snap, _lookup_compass_song(db, snap.title, snap.artist)) for snap in snaps]

    return ChartSnapshotOut(
        chart_source=chart_slug,
        label=entry["label"],
        date=most_recent_date,
        songs=songs,
    )


# --- Admin/cron refresh endpoint -----------------------------------------

admin_router = APIRouter(prefix="/api/admin/agent/cron", tags=["chart-snapshots-admin"])


def _enqueue_backfill_for_uncalibrated(
    db: Session, chart_slug: str, snapshot_date: date, songs: list[dict]
) -> int | None:
    """If any snapshot songs aren't in compass_songs, spawn a Backfill job
    for them. Returns the new job_id, or None when nothing to backfill."""
    uncalibrated = [
        s for s in songs
        if not _lookup_compass_song(db, s["title"], s["artist"])
    ]
    if not uncalibrated:
        return None

    job = BackfillJob(
        label=f"{chart_slug}-uncal-{snapshot_date.isoformat()}",
        target_table="compass",
        passes="both",
        status="queued",
        paused_flag=0,
        total_rows=len(uncalibrated),
        completed_rows=0,
        failed_rows=0,
        note=f"Auto-created by refresh-chart-snapshot/{chart_slug} on {snapshot_date.isoformat()}",
    )
    db.add(job)
    db.flush()
    for idx, s in enumerate(uncalibrated, start=1):
        # status="needs_lyrics" so the orchestrator will fetch via Musixmatch
        db.add(BackfillJobRow(
            job_id=job.id,
            position=idx,
            title=s["title"],
            artist=s["artist"],
            status="needs_lyrics",
        ))
    db.commit()
    db.refresh(job)
    logger.info(
        "Auto-queued backfill job %s for %s (%d uncalibrated songs)",
        job.id, chart_slug, len(uncalibrated),
    )
    return job.id


@admin_router.post(
    "/refresh-chart-snapshot/{key}",
    dependencies=[Depends(verify_reading_cron_key)],
)
async def refresh_snapshot(key: str, db: Session = Depends(get_db)):
    """Fetch the chart, overwrite today's snapshot, and auto-spawn a backfill
    job for any songs not yet in compass_songs. X-Reading-Cron-Key authed.

    Async because the orchestrator's start_job uses asyncio.create_task to
    spawn the worker on the running event loop. The blocking Playwright
    fetcher runs in a thread executor.
    """
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")

    fetcher: Callable[..., list[dict]] = entry["fetcher"]
    songs = await asyncio.get_event_loop().run_in_executor(None, lambda: fetcher(count=20))
    if not songs:
        raise HTTPException(status_code=502, detail=f"Failed to fetch {entry['label']}")

    today = date.today()
    written = _replace_snapshot(db, entry["slug"], today, songs)
    logger.info("Wrote %d %s rows for %s", written, entry["slug"], today)

    job_id = _enqueue_backfill_for_uncalibrated(db, entry["slug"], today, songs)
    if job_id is not None:
        try:
            orchestrator.start_job(job_id)
        except Exception:
            logger.exception("Failed to start backfill job %s — leaving in queued state", job_id)

    return {
        "chart_source": entry["slug"],
        "date": today.isoformat(),
        "rows": written,
        "backfill_job_id": job_id,
    }
