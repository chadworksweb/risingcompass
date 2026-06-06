"""Charger Activity -- public feeds of what's moving through the Lyrical Charger.

Three read-only feeds, all derived from existing tables (no schema change):

- New Additions: songs born from a first-time LC run -- the song's earliest
  `song_ingestions` row is `method='lyrical_charger'` (chart songs later re-run
  through LC are excluded). Newest first.
- Recently Calibrated: distinct songs by the most recent successful LC run.
- Most Calibrated: distinct songs by count of successful LC runs, with an
  all-time / trailing-30-day window.

"A successful LC run" == an `lc_events` row with event_type='submission_success'
and song_id set (written for anon and signed-in alike, analyzer.py). So the
recent/most feeds count ALL public runs, not just signed-in attributions.

Public surface only: cards carry the already-public summary fields (tier, charge,
summary, contamination) -- never the paywalled prose. Backs
`/lyrical-charger/activity/`.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlalchemy.orm import aliased

from app.database import SessionLocal
from app.models import Song, SongIngestion, LcEvent
from app.services.song_search import _attach_slugs, _attach_artist_slugs

public_router = APIRouter(prefix="/api/charger-activity", tags=["charger-activity"])

LC_METHOD = "lyrical_charger"
SUCCESS_EVENT = "submission_success"


def _item(song: Song) -> dict:
    """Public song-card dict -- same field set the library/search cards consume,
    minus the paywalled prose. `_attach_slugs` adds `slug`; `_attach_artist_slugs`
    adds `artist_slug` (both read `id`/`title`/`artist`)."""
    return {
        "id": song.id,
        "title": song.title,
        "artist": song.artist,
        "rubric_color": song.rubric_color,
        "charge_value": song.charge_value,
        "charge_summary": song.charge_summary,
        "contaminated": bool(song.contaminated),
        "dogma_referenced": bool(song.dogma_referenced),
    }


def _hydrate(db, ordered_rows, metric_key: str) -> list[dict]:
    """ordered_rows: list of (song_id, metric_value) already in display order.
    Fetch the songs in one query, preserve order, attach slugs, stamp the
    feed-specific metric."""
    if not ordered_rows:
        return []
    ids = [r[0] for r in ordered_rows]
    metrics = {r[0]: r[1] for r in ordered_rows}
    song_map = {s.id: s for s in db.query(Song).filter(Song.id.in_(ids)).all()}
    items: list[dict] = []
    for sid in ids:  # preserve query order
        song = song_map.get(sid)
        if song is None:
            continue
        it = _item(song)
        val = metrics.get(sid)
        if isinstance(val, datetime):
            it[metric_key] = val.isoformat()
        else:
            it[metric_key] = val
        items.append(it)
    _attach_slugs(items, db)
    _attach_artist_slugs(items, db)
    return items


# --- New Additions -------------------------------------------------------

def _new_additions_query(db):
    """Songs whose earliest ingestion is the LC run -- i.e. the song was born
    from the Charger. A song qualifies if it has a lyrical_charger ingestion and
    NO non-LC ingestion predates it. Calibrated (rubric_color) only."""
    # Correlated EXISTS against a second reference to song_ingestions: is there
    # a non-LC ingestion for this song that predates the LC one?
    other = aliased(SongIngestion)
    earlier_other = (
        db.query(other.id)
        .filter(
            other.song_id == SongIngestion.song_id,
            other.method != LC_METHOD,
            other.created_at < SongIngestion.created_at,
        )
        .exists()
    )
    return (
        db.query(SongIngestion.song_id, SongIngestion.created_at)
        .join(Song, Song.id == SongIngestion.song_id)
        .filter(
            SongIngestion.method == LC_METHOD,
            Song.rubric_color.isnot(None),
            ~earlier_other,
        )
    )


def _new_additions(db, limit: int, offset: int):
    base = _new_additions_query(db)
    total = base.order_by(None).count()
    rows = (
        base.order_by(SongIngestion.created_at.desc())
        .limit(limit).offset(offset).all()
    )
    items = _hydrate(db, [(r[0], r[1]) for r in rows], "first_calibrated_at")
    return items, total


# --- Recently Calibrated -------------------------------------------------

def _recent(db, limit: int, offset: int):
    base = (
        db.query(LcEvent.song_id, func.max(LcEvent.occurred_at).label("last_at"))
        .join(Song, Song.id == LcEvent.song_id)
        .filter(
            LcEvent.event_type == SUCCESS_EVENT,
            LcEvent.song_id.isnot(None),
            Song.rubric_color.isnot(None),
        )
        .group_by(LcEvent.song_id)
    )
    total = base.order_by(None).count()
    rows = (
        base.order_by(func.max(LcEvent.occurred_at).desc())
        .limit(limit).offset(offset).all()
    )
    items = _hydrate(db, [(r[0], r[1]) for r in rows], "last_calibrated_at")
    return items, total


# --- Most Calibrated -----------------------------------------------------

def _most_run(db, window: str, limit: int, offset: int):
    base = (
        db.query(
            LcEvent.song_id,
            func.count().label("cnt"),
            func.max(LcEvent.occurred_at).label("last_at"),
        )
        .join(Song, Song.id == LcEvent.song_id)
        .filter(
            LcEvent.event_type == SUCCESS_EVENT,
            LcEvent.song_id.isnot(None),
            Song.rubric_color.isnot(None),
        )
    )
    if window == "30d":
        cutoff = datetime.utcnow() - timedelta(days=30)
        base = base.filter(LcEvent.occurred_at >= cutoff)
    base = base.group_by(LcEvent.song_id)
    total = base.order_by(None).count()
    rows = (
        base.order_by(func.count().desc(), func.max(LcEvent.occurred_at).desc())
        .limit(limit).offset(offset).all()
    )
    # row = (song_id, cnt, last_at) -- metric is the count.
    items = _hydrate(db, [(r[0], r[1]) for r in rows], "run_count")
    return items, total


# --- Endpoints -----------------------------------------------------------

def _feed_out(items, total, limit, offset):
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@public_router.get("/overview")
def overview(limit: int = Query(default=12, ge=1, le=50)):
    """One call for the initial page render: all three feeds (most_run all-time)."""
    db = SessionLocal()
    try:
        new_items, _ = _new_additions(db, limit, 0)
        recent_items, _ = _recent(db, limit, 0)
        most_items, _ = _most_run(db, "all", limit, 0)
        return {
            "new_additions": new_items,
            "recent": recent_items,
            "most_run": most_items,
            "limit": limit,
        }
    finally:
        db.close()


@public_router.get("/new-additions")
def new_additions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    db = SessionLocal()
    try:
        items, total = _new_additions(db, limit, offset)
        return _feed_out(items, total, limit, offset)
    finally:
        db.close()


@public_router.get("/recent")
def recent(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    db = SessionLocal()
    try:
        items, total = _recent(db, limit, offset)
        return _feed_out(items, total, limit, offset)
    finally:
        db.close()


@public_router.get("/most-run")
def most_run(
    window: str = Query(default="all", pattern="^(all|30d)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    db = SessionLocal()
    try:
        items, total = _most_run(db, window, limit, offset)
        out = _feed_out(items, total, limit, offset)
        out["window"] = window
        return out
    finally:
        db.close()
