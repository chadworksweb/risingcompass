"""Per-chart daily snapshot endpoints — public read + admin/cron refresh.

The canonical daily reading flows through compass.py + agent.py and gets
its own DailyReading record + approval flow. This router is the lighter
"show today's chart top 20 as a panel" mechanism for secondary charts
(iTunes Download Chart today; Apple Music / Billboard / etc. later). Charge
values come from the unified songs table via case-insensitive lookup.

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
import json
import logging
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.auth import verify_reading_cron_key
from app.constants import AGGREGATING_CHART_SLUGS, chart_source_label
from app.database import SessionLocal, get_db
from app.models import AgentDraft, ChartSnapshot, Song
from app.schemas import ReadingSongOut
from app.services.agents.chart_source import (
    fetch_itunes_songs,
    fetch_shazam_songs,
    fetch_top_songs,
    fetch_youtube_songs,
)
from app.services.agents.compass_agent import run_compass_agent
from app.services.artist_utils import generate_song_slug, normalize_artist_name, resolve_artist_slugs
from app.services.charge_calc import degree_to_charge
from app.services.song_store import find_song_by_title_artist

logger = logging.getLogger(__name__)


# Registry of chart-snapshot charts. The key is the slot's wiring handle (cron
# URL, public endpoint key, frontend element ids). Metadata used by the public
# Calendar's auto-pickup (get_calendar_charts):
#   cadence        -- "daily" | "weekly"; only daily charts paint the day-grid.
#   calendar_label -- short toggle label (omit to keep a chart OUT of the toggle).
#   calendar_sub   -- the source line shown under the toggle.
# Tier is derived, not stored: a slug in constants.AGGREGATING_CHART_SLUGS feeds
# the daily charge (Tier 1) and is painted via the drift endpoints, not as a
# snapshot; everything else is Tier 2.
CHART_REGISTRY: dict[str, dict] = {
    # iTunes Download Chart - USA: the secondary homepage panel, refreshed daily.
    # Tier 2 (not in AGGREGATING_CHART_SLUGS) + daily -> the Calendar lists it.
    "itunes": {
        "slug": "itunes_download_usa",
        "label": "iTunes Download Chart - USA",
        "fetcher": fetch_itunes_songs,
        "cadence": "daily",
        "calendar_label": "iTunes",
        "calendar_sub": "iTunes Downloads - USA",
    },
    # Spotify Top 50 - USA: the daily reading source = the daily CHARGE (Tier 1,
    # slug in AGGREGATING_CHART_SLUGS). The Calendar paints it via the drift
    # endpoints, so get_calendar_charts adds it as the drift default and skips it
    # here -- no calendar_label, and the Tier-1 filter excludes it anyway.
    "top50": {
        "slug": "spotify_top50_usa",
        "label": "Spotify Top 50 - USA",
        "fetcher": fetch_top_songs,
        "cadence": "daily",
    },
    # Shazam Top 200 - USA: Tier 2 daily ingestion-discovery source. Auto-listed
    # in the Calendar once it has published, degree-stamped snapshots.
    "shazam": {
        "slug": "shazam_top200_usa",
        "label": "Shazam Top 200 - USA",
        "fetcher": fetch_shazam_songs,
        "cadence": "daily",
        "calendar_label": "Shazam",
        "calendar_sub": "Shazam Top 200 - USA",
    },
    # YouTube Trending - USA: Tier 2 daily ingestion-discovery source. Auto-listed
    # in the Calendar once it has published, degree-stamped snapshots.
    "youtube": {
        "slug": "youtube_trending_usa",
        "label": "YouTube Trending - USA",
        "fetcher": fetch_youtube_songs,
        "cadence": "daily",
        "calendar_label": "YouTube",
        "calendar_sub": "YouTube Trending - USA",
    },
}


class ChartSnapshotOut(BaseModel):
    chart_source: str
    label: str
    date: date
    # Snapshot aggregate (stamped on every row at approval, equal across the day's
    # rows). Lets a standalone chart page render the same charge + editorial shell
    # the homepage daily reading shows. Null on pre-aggregate snapshots.
    compass_degree: float | None = None
    charge_level: str | None = None
    contamination_count: int = 0
    editorial: str | None = None
    songs: list[ReadingSongOut]


def _dominant_topic(song: Song | None) -> str | None:
    """First taxonomy slug off the song's JSON-encoded topics list, mirroring
    the Ether Art Chart's `topics[0]` rule."""
    if not song or not song.topics:
        return None
    try:
        parsed = json.loads(song.topics)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, list):
        for t in parsed:
            if isinstance(t, str):
                return t
    return None


def _build_song(snap: ChartSnapshot, song: Song | None, artist_slug: str | None = None) -> ReadingSongOut:
    return ReadingSongOut(
        id=snap.id,
        title=snap.title,
        artist=snap.artist,
        position=snap.position,
        rubric_color=song.rubric_color if song else None,
        charge_value=song.charge_value if song else None,
        contaminated=bool(song.contaminated) if song else False,
        contamination_note=song.contamination_note if song else None,
        charge_summary=song.charge_summary if song else None,
        chart_source=snap.chart_source,
        instrumental=bool(song.instrumental) if song else False,
        preorder=bool(getattr(snap, "preorder", False)),
        song_slug=generate_song_slug(snap.title, snap.artist),
        artist_slug=artist_slug,
        deadpan_line=song.deadpan_line if song else None,
        dominant_topic=_dominant_topic(song),
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


@public_router.get("/calendar-charts")
def get_calendar_charts(db: Session = Depends(get_db)):
    """Charts the public Calendar can paint, in toggle order.

    The Tier-1 daily charge (Daily Listens = Spotify Top 50, served by the drift
    endpoints) is always first and is the default. After it come the Tier-2
    DAILY charts: every CHART_REGISTRY entry that does NOT feed the charge (slug
    not in AGGREGATING_CHART_SLUGS), is daily cadence, exposes a calendar_label,
    and already has at least one published, degree-stamped snapshot to paint. So
    registering a new Tier-2 daily chart makes it appear here automatically once
    its first snapshot is approved; Tier-1, weekly, and all-time charts never
    appear. Declared before the /{key}/... routes so the literal path wins.
    """
    charts = [{
        "key": "spotify",
        "label": "Spotify (US)",
        "sub": "Spotify Top 50 - USA",
        "source": "daily",
    }]
    for key, entry in CHART_REGISTRY.items():
        slug = entry["slug"]
        if slug in AGGREGATING_CHART_SLUGS:        # Tier 1 -- it IS the charge
            continue
        if entry.get("cadence") != "daily":         # only daily charts paint the day-grid
            continue
        if not entry.get("calendar_label"):         # not meant for the toggle
            continue
        painted = db.query(
            db.query(ChartSnapshot.id)
            .filter(
                ChartSnapshot.chart_source == slug,
                ChartSnapshot.published.is_(True),
                ChartSnapshot.compass_degree.isnot(None),
            )
            .exists()
        ).scalar()
        if not painted:                             # nothing to paint yet -- skip
            continue
        charts.append({
            "key": key,
            "label": entry["calendar_label"],
            "sub": entry.get("calendar_sub") or chart_source_label(slug) or entry["label"],
            "source": "chart",
        })
    return charts


@public_router.get("/{key}/current", response_model=ChartSnapshotOut)
def get_current_snapshot(key: str, db: Session = Depends(get_db)):
    """Today's snapshot for the named chart, or the most recent if today's hasn't run."""
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")

    chart_slug = entry["slug"]

    # Published gate: only approved snapshots are public. Unpublished rows are
    # written at fetch time but stay invisible until the chart draft is approved
    # (agent.approve_draft flips published=True), mirroring the daily reading.
    most_recent_date = (
        db.query(func.max(ChartSnapshot.date))
        .filter(
            ChartSnapshot.chart_source == chart_slug,
            ChartSnapshot.published.is_(True),
        )
        .scalar()
    )
    if not most_recent_date:
        raise HTTPException(status_code=404, detail="No snapshots yet for this chart")

    snaps = (
        db.query(ChartSnapshot)
        .filter(
            ChartSnapshot.chart_source == chart_slug,
            ChartSnapshot.date == most_recent_date,
            ChartSnapshot.published.is_(True),
        )
        .order_by(ChartSnapshot.position.asc())
        .all()
    )

    slug_map = resolve_artist_slugs([snap.artist for snap in snaps], db)
    songs = [
        _build_song(
            snap,
            find_song_by_title_artist(db, snap.title, snap.artist),
            slug_map.get(normalize_artist_name(snap.artist or "").lower()),
        )
        for snap in snaps
    ]

    # Degree/charge/editorial are denormalised across the day's rows, so any row
    # carries the snapshot aggregate; contamination is counted from the looked-up
    # songs (same as the daily reading + Calendar detail).
    return ChartSnapshotOut(
        chart_source=chart_slug,
        label=entry["label"],
        date=most_recent_date,
        compass_degree=snaps[0].compass_degree,
        charge_level=snaps[0].charge_level,
        contamination_count=sum(1 for s in songs if s.contaminated),
        editorial=snaps[0].editorial,
        songs=songs,
    )


# --- Calendar (chart-agnostic) read endpoints ----------------------------
# The Calendar is chart-agnostic: it paints each day by that day's aggregate
# compass_degree. For the daily reading that lives on daily_readings (served by
# /api/drift/*); these three mirror that contract for any chart-snapshot chart
# (key = CHART_REGISTRY key, e.g. "itunes"), reading the aggregate stamped on
# the published snapshot rows at approval. Only published rows are served.


class ChartReadingOut(BaseModel):
    """One chart's snapshot for a single date -- shaped like the daily reading's
    DailyReadingOut so the Calendar detail panel renders it unchanged."""
    date: date
    compass_degree: float | None
    charge_level: str | None
    contamination_count: int
    editorial_summary: str | None = None
    songs: list[ReadingSongOut]


def _chart_slug_or_404(key: str) -> str:
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")
    return entry["slug"]


@public_router.get("/{key}/years")
def get_chart_years(key: str, db: Session = Depends(get_db)):
    """Years with published snapshots for this chart, each with a year-mean
    aggregate degree + charge. Mirrors /api/drift/years so the Calendar can size
    its bounds and color the year/decade tiles."""
    chart_slug = _chart_slug_or_404(key)
    rows = (
        db.query(
            extract("year", ChartSnapshot.date).label("yr"),
            func.avg(ChartSnapshot.compass_degree).label("avg_deg"),
        )
        .filter(
            ChartSnapshot.chart_source == chart_slug,
            ChartSnapshot.published.is_(True),
            ChartSnapshot.compass_degree.isnot(None),
        )
        .group_by("yr")
        .order_by("yr")
        .all()
    )
    out = []
    for yr, avg_deg in rows:
        deg = round(float(avg_deg), 1)
        out.append({
            "year": int(yr),
            "compass_degree": deg,
            "charge_level": degree_to_charge(deg),
        })
    return out


@public_router.get("/{key}/years/{year}/dates")
def get_chart_year_dates(key: str, year: int, db: Session = Depends(get_db)):
    """Per-day aggregates for one chart + year. Same shape as
    /api/drift/years/{year}/dates so the Calendar swaps data sources cleanly.
    The 20 rows per (date) carry an identical aggregate, so DISTINCT collapses
    them to one reading per day."""
    chart_slug = _chart_slug_or_404(key)
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    rows = (
        db.query(
            ChartSnapshot.date,
            ChartSnapshot.compass_degree,
            ChartSnapshot.charge_level,
        )
        .filter(
            ChartSnapshot.chart_source == chart_slug,
            ChartSnapshot.published.is_(True),
            ChartSnapshot.compass_degree.isnot(None),
            ChartSnapshot.date >= start,
            ChartSnapshot.date <= end,
        )
        .distinct()
        .order_by(ChartSnapshot.date)
        .all()
    )
    return {
        "dates": [r[0].isoformat() for r in rows],
        "readings": [
            {"date": r[0].isoformat(), "compass_degree": r[1], "charge_level": r[2]}
            for r in rows
        ],
    }


@public_router.get("/{key}/reading/{reading_date}", response_model=ChartReadingOut)
def get_chart_reading(key: str, reading_date: date, db: Session = Depends(get_db)):
    """Full published snapshot for one chart + date, with per-song colors looked
    up live -- shaped like the daily reading detail so the Calendar panel reuses
    its renderer. Editorial is the one stamped on the snapshot rows at approval;
    contamination is counted from the looked-up songs."""
    chart_slug = _chart_slug_or_404(key)
    snaps = (
        db.query(ChartSnapshot)
        .filter(
            ChartSnapshot.chart_source == chart_slug,
            ChartSnapshot.date == reading_date,
            ChartSnapshot.published.is_(True),
        )
        .order_by(ChartSnapshot.position.asc())
        .all()
    )
    if not snaps:
        raise HTTPException(status_code=404, detail="No published snapshot for this date")

    slug_map = resolve_artist_slugs([snap.artist for snap in snaps], db)
    songs = [
        _build_song(
            snap,
            find_song_by_title_artist(db, snap.title, snap.artist),
            slug_map.get(normalize_artist_name(snap.artist or "").lower()),
        )
        for snap in snaps
    ]
    contamination_count = sum(1 for s in songs if s.contaminated)

    return ChartReadingOut(
        date=reading_date,
        compass_degree=snaps[0].compass_degree,
        charge_level=snaps[0].charge_level,
        contamination_count=contamination_count,
        editorial_summary=snaps[0].editorial,
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
    No dep-injected DB session: the fetch takes 15-30s, so we don't hold a
    pooled connection idle across it — a fresh SessionLocal is opened after
    the fetch for the write.
    """
    entry = CHART_REGISTRY.get(key)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown chart key")

    chart_slug = entry["slug"]
    today = date.today()

    # Pin: skip the fetch entirely if a draft already exists for today.
    db: Session = SessionLocal()
    try:
        # Already published today? Approval deletes the draft, so the draft pin
        # below can't catch a post-approval re-trigger. Guard on the published
        # snapshot so a same-day re-run doesn't wipe an approved chart back to
        # unpublished (which would pull today's panel until re-approval).
        already_published = (
            db.query(ChartSnapshot.id)
            .filter(
                ChartSnapshot.chart_source == chart_slug,
                ChartSnapshot.date == today,
                ChartSnapshot.published.is_(True),
            )
            .first()
        )
        if already_published:
            logger.info("Chart %s already published for %s; skipping refresh", chart_slug, today)
            return {
                "chart_source": chart_slug,
                "date": today.isoformat(),
                "note": "already published today; refresh skipped",
            }

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
