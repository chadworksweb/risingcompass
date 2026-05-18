"""Per-chart daily snapshot endpoints — public read + admin/cron refresh.

The canonical daily reading flows through compass.py + agent.py and gets
its own DailyReading record + approval flow. This router is the lighter
"show today's chart top 20 as a panel" mechanism for secondary charts
(Spotify Viral 50 today; Apple Music / Billboard / etc. later). Charge
values come from compass_songs via case-insensitive lookup.

Adding a new chart = register one entry in CHART_REGISTRY plus a fetcher
in services/agents/chart_source.py. No schema change.

Calibration uses the SAME SOP as the daily reading: refresh fires
run_compass_agent which creates an AgentDraft (draft_type=<chart_slug>),
auto-fills cache hits from compass_songs, and emails the admin with a
list of songs awaiting lyrics. Admin pastes lyrics via the existing
supply-lyrics endpoint; each paste calibrates and writes to
compass_songs, and the panel auto-picks up the new colors.

Chart drafts don't get published as DailyReadings — approve_draft
branches on draft_type and skips the DailyReading creation for charts
(the compass_songs writes during supply-lyrics already do the user-
visible work).
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
from app.database import SessionLocal, get_db
from app.models import AgentDraft, ChartSnapshot, CompassSong
from app.schemas import ReadingSongOut
from app.services.agents.chart_source import fetch_top_songs, fetch_viral_songs
from app.services.agents.compass_agent import run_compass_agent
from app.services.artist_utils import generate_song_slug, normalize_artist_name, resolve_artist_slugs

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


def _build_song(snap: ChartSnapshot, cs: CompassSong | None, artist_slug: str | None = None) -> ReadingSongOut:
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
        artist_slug=artist_slug,
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

    slug_map = resolve_artist_slugs([snap.artist for snap in snaps], db)
    songs = [
        _build_song(
            snap,
            _lookup_compass_song(db, snap.title, snap.artist),
            slug_map.get(normalize_artist_name(snap.artist or "").lower()),
        )
        for snap in snaps
    ]

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
async def refresh_snapshot(key: str):
    """Fetch the chart, write today's snapshot, and run the compass agent.

    Mirrors the daily reading SOP — run_compass_agent calibrates whatever
    is already in compass_songs (cache hits) and creates an AgentDraft
    for the rest, sending an email with the list of songs awaiting lyrics.
    Admin pastes lyrics through the existing /drafts/{ref}/songs/{id}/lyrics
    endpoint; each paste calibrates and writes to compass_songs.

    Pinning: if a draft already exists for today + this chart, we skip the
    Spotify fetch and reuse the pinned song list. Same intent as the
    daily reading's chart pinning — the draft is the source of truth for
    "what songs are in today's reading," even if the chart has shifted
    since the cron first ran.

    Async so the blocking Playwright fetcher can run via run_in_executor.
    No dep-injected DB session: the fetch takes 15-30s and the embedded
    replica's Hrana stream times out by the time we'd write — fresh
    SessionLocal opened after the fetch, same pattern as artists_admin's
    long-running transactions.
    """
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")

    chart_slug = entry["slug"]
    today = date.today()

    # Pin: skip the fetch entirely if a draft already exists for today.
    db: Session = SessionLocal()
    try:
        existing_draft = (
            db.query(AgentDraft)
            .filter(AgentDraft.date == today, AgentDraft.draft_type == chart_slug)
            .order_by(AgentDraft.id.asc())
            .first()
        )
        if existing_draft and existing_draft.songs:
            logger.info(
                "Chart pinned: reusing draft %s with %d songs (skipping fetch)",
                existing_draft.label, len(existing_draft.songs),
            )
            return {
                "chart_source": chart_slug,
                "date": today.isoformat(),
                "draft_id": existing_draft.id,
                "draft_label": existing_draft.label,
                "note": "draft already exists; chart pinned",
            }
    finally:
        db.close()

    # First call of the day — fetch fresh.
    fetcher: Callable[..., list[dict]] = entry["fetcher"]
    songs = await asyncio.get_event_loop().run_in_executor(None, lambda: fetcher(count=20))
    if not songs:
        raise HTTPException(status_code=502, detail=f"Failed to fetch {entry['label']}")

    db = SessionLocal()
    try:
        _replace_snapshot(db, chart_slug, today, songs)
        # run_compass_agent: cache-hits auto-calibrate, the rest are left for
        # admin to supply lyrics via the existing /drafts/{ref}/songs/{id}/lyrics
        # endpoint. Email is sent automatically.
        draft = run_compass_agent(songs, reading_date=today, draft_type=chart_slug)
    finally:
        db.close()

    return {
        "chart_source": chart_slug,
        "date": today.isoformat(),
        "draft_id": draft.id,
        "draft_label": draft.label,
    }
