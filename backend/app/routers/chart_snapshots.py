"""Per-chart daily snapshot endpoints — public read + admin/cron refresh.

The canonical daily reading still flows through compass.py + agent.py. This
router is the lighter "show today's chart top 20 as a panel" mechanism for
secondary charts (Spotify Viral 50 today; Apple Music / Billboard / etc.
later). Charge values come from compass_songs via case-insensitive lookup.

Adding a new chart = register one entry in CHART_REGISTRY plus a fetcher
in services/agents/chart_source.py. No schema change.
"""

import logging
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import verify_reading_cron_key
from app.database import get_db
from app.models import ChartSnapshot, CompassSong
from app.schemas import ReadingSongOut
from app.services.agents.chart_source import fetch_top_songs, fetch_viral_songs
from app.services.artist_utils import generate_song_slug

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


@admin_router.post(
    "/refresh-chart-snapshot/{key}",
    dependencies=[Depends(verify_reading_cron_key)],
)
def refresh_snapshot(key: str, db: Session = Depends(get_db)):
    """Fetch the chart and overwrite today's snapshot. X-Reading-Cron-Key authed."""
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")

    fetcher: Callable[..., list[dict]] = entry["fetcher"]
    songs = fetcher(count=20)
    if not songs:
        raise HTTPException(status_code=502, detail=f"Failed to fetch {entry['label']}")

    today = date.today()
    written = _replace_snapshot(db, entry["slug"], today, songs)
    logger.info("Wrote %d %s rows for %s", written, entry["slug"], today)
    return {"chart_source": entry["slug"], "date": today.isoformat(), "rows": written}
