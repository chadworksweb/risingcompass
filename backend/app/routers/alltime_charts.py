"""All-Time charts -- two current-state ranked boards that sit OUTSIDE the daily
reading / chart_snapshots pipeline.

- Most-Streamed Songs of All Time (Spotify GLOBAL lifetime streams, top 100).
  Refreshed MONTHLY by scraping kworb.net. The cron makes NO Anthropic calls --
  it fills calibration off existing songs (cache hits) and flags misses for
  manual `calibrate_song.py`. Stream rank + counts are the real data and always
  render; tier/ether fill in as songs get calibrated (no approve gate).
- Best-Selling Albums of All Time (US / RIAA certified, top 100). MANUAL annual
  sweep via the admin editor; a data-driven staleness banner (>365d) replaces a
  cron. Album display fields are copied from an already-charged Release (Album
  Charger or terminal), which already persists rubric_color / charge_value /
  charge_summary / deadpan_line / topics.

Public reads: GET /api/charts/alltime/streams | /albums.
Cron: POST /api/admin/agent/cron/refresh-alltime-streams (X-Reading-Cron-Key).
Album admin: GET/POST/PATCH/DELETE /api/admin/alltime/albums (+ release link).
"""

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import verify_reading_cron_key
from app.database import SessionLocal, get_db
from app.models import AlltimeAlbum, AlltimeStreamSong, Artist, Release
from app.routers.admin import verify_admin_key
from app.services.agents.calibrator import lookup_calibrated
from app.services.agents.chart_source import fetch_kworb_alltime_songs
from app.services.artist_utils import slugify
from app.services.song_search import _attach_artist_slugs, _attach_slugs

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/charts/alltime", tags=["alltime-charts"])
router = APIRouter(tags=["alltime-charts-admin"])

TOP_N = 100          # streams board depth
ALBUM_TOP_N = 50     # albums board depth (per-album charging cost is high)

# Non-music audio that kworb's all-time board surfaces (white-noise / sleep /
# ASMR tracks). Conservative substring match on title+artist -- like an
# instrumental it carries NO charge, but it is not a song to read at all, so it
# is nulled AND tagged "non-music" instead of sitting in the awaiting-lyrics
# queue forever. Kept tight to avoid nuking real songs; extend as needed.
_NON_MUSIC_MARKERS = (
    "white noise", "pink noise", "brown noise", "static noise", "fan noise",
    "rain sounds", "rain sound", "rainfall", "ocean sounds", "ocean wave",
    "nature sounds", "sleep sounds", "sleep music", "baby sleep", "deep sleep",
    "asmr", "binaural", "guided meditation", "meditation music", "womb sound",
    "loopable",
)


def _is_non_music(title: str, artist: str) -> bool:
    blob = f"{title or ''} {artist or ''}".lower()
    return any(m in blob for m in _NON_MUSIC_MARKERS)


def _load_topics(raw) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


# ---------------------------------------------------------------- public reads

def _stream_row_out(r: AlltimeStreamSong) -> dict:
    return {
        "rank": r.rank,
        "title": r.title,
        "artist": r.artist,
        "total_streams": r.total_streams,
        "daily_streams": r.daily_streams,
        "rubric_color": r.rubric_color,
        "charge_value": r.charge_value,
        "deadpan_line": r.deadpan_line,
        "topics": _load_topics(r.topics),
        "song_slug": r.song_slug,
        "artist_slug": r.artist_slug,
        "non_music": bool(r.non_music),
    }


def _album_row_out(r: AlltimeAlbum) -> dict:
    return {
        "rank": r.rank,
        "album_title": r.album_title,
        "artist": r.artist,
        "certified_units": r.certified_units,
        "units_millions": r.units_millions,
        "release_year": r.release_year,
        "rubric_color": r.rubric_color,
        "charge_value": r.charge_value,
        "charge_summary": r.charge_summary,
        "deadpan_line": r.deadpan_line,
        "topics": _load_topics(r.topics),
        "artist_slug": r.artist_slug,
        "release_slug": r.release_slug,
        "non_music": bool(r.non_music),
    }


@public_router.get("/streams")
def get_alltime_streams(db: Session = Depends(get_db)):
    """The Most-Streamed Songs of All Time board (top 100, Spotify global)."""
    rows = (
        db.query(AlltimeStreamSong)
        .order_by(AlltimeStreamSong.rank.asc())
        .limit(TOP_N)
        .all()
    )
    return {"rows": [_stream_row_out(r) for r in rows]}


@public_router.get("/albums")
def get_alltime_albums(db: Session = Depends(get_db)):
    """The Best-Selling Albums of All Time board (top 50, US / RIAA)."""
    rows = (
        db.query(AlltimeAlbum)
        .order_by(AlltimeAlbum.rank.asc())
        .limit(ALBUM_TOP_N)
        .all()
    )
    return {"rows": [_album_row_out(r) for r in rows]}


# ------------------------------------------------- monthly stream-chart refresh

def _apply_stream_refresh(db: Session, songs: list[dict]) -> dict:
    """Upsert the 100 chart rows by rank (the chart slot), filling calibration
    off the unified songs table on a cache hit. Diff-on-change: a row is only
    rewritten when its song/streams/calibration actually changed. Returns a
    summary incl. the awaiting-lyrics list (songs not yet calibrated)."""
    existing = {r.rank: r for r in db.query(AlltimeStreamSong).all()}
    seen_ranks: set[int] = set()
    updated = 0
    awaiting: list[dict] = []
    # Rows that matched a calibrated song -- batch-resolve their slugs after.
    to_slug: list[dict] = []

    for s in songs[:TOP_N]:
        rank = s["position"]
        seen_ranks.add(rank)
        title, artist = s["title"], s["artist"]
        # Non-music (white-noise/sleep/ASMR) is nulled like an instrumental and
        # tagged -- never looked up, never queued for lyrics.
        nonmusic = _is_non_music(title, artist)
        cached = None if nonmusic else lookup_calibrated(title, artist, db)

        song_id = cached.get("song_id") if cached else None
        rubric_color = cached.get("rubric_color") if cached else None
        charge_value = cached.get("charge_value") if cached else None
        deadpan_line = cached.get("deadpan_line") if cached else None
        topics_json = (
            json.dumps(cached["topics"]) if cached and cached.get("topics") else None
        )
        if not nonmusic and not cached:
            awaiting.append({"rank": rank, "title": title, "artist": artist})

        row = existing.get(rank)
        if row is None:
            row = AlltimeStreamSong(rank=rank)
            db.add(row)

        # Diff-on-change: skip the write when nothing material moved.
        changed = (
            row.title != title
            or row.artist != artist
            or row.total_streams != s.get("total_streams")
            or row.daily_streams != s.get("daily_streams")
            or row.song_id != song_id
            or row.rubric_color != rubric_color
            or row.charge_value != charge_value
            or row.deadpan_line != deadpan_line
            or row.topics != topics_json
            or bool(row.non_music) != nonmusic
        )
        if changed:
            row.title = title
            row.artist = artist
            row.total_streams = s.get("total_streams")
            row.daily_streams = s.get("daily_streams")
            row.song_id = song_id
            row.rubric_color = rubric_color
            row.charge_value = charge_value
            row.deadpan_line = deadpan_line
            row.topics = topics_json
            row.non_music = nonmusic
            updated += 1

        # Always refresh slugs (artist slug for everyone; song slug when known).
        to_slug.append({
            "id": song_id, "title": title, "artist": artist, "_row": row,
        })

    # Drop any stale ranks beyond the current board (e.g. a prior >100 load).
    for rank, row in existing.items():
        if rank not in seen_ranks:
            db.delete(row)

    _attach_artist_slugs(to_slug, db)
    calibrated = [it for it in to_slug if it["id"]]
    if calibrated:
        _attach_slugs(calibrated, db)
    for it in to_slug:
        row = it["_row"]
        row.artist_slug = it.get("artist_slug")
        row.song_slug = it.get("slug") if it["id"] else None

    return {
        "fetched": len(songs),
        "updated": updated,
        "awaiting_count": len(awaiting),
        "awaiting": awaiting,
    }


@router.post(
    "/api/admin/agent/cron/refresh-alltime-streams",
    dependencies=[Depends(verify_reading_cron_key)],
)
async def refresh_alltime_streams():
    """Monthly cron (X-Reading-Cron-Key): scrape kworb's all-time board, upsert
    the 100 rows, fill calibration from cache hits, and email the awaiting-lyrics
    list. No Anthropic calls fire here -- misses are calibrated manually."""
    songs = await asyncio.get_event_loop().run_in_executor(
        None, lambda: fetch_kworb_alltime_songs(count=TOP_N)
    )
    if not songs:
        raise HTTPException(status_code=502, detail="Failed to fetch kworb all-time songs")

    db: Session = SessionLocal()
    try:
        result = _apply_stream_refresh(db, songs)
        db.commit()
    finally:
        db.close()

    if result["awaiting"]:
        try:
            from app.services.alerts import emit_alltime_streams_awaiting
            emit_alltime_streams_awaiting(
                updated=result["updated"], awaiting=result["awaiting"]
            )
        except Exception:
            logger.exception("alltime-streams awaiting-lyrics alert failed")

    return result


# --------------------------------------------------------- album admin (manual)

_STALE_DAYS = 365


def _staleness(db: Session) -> dict:
    """Data-driven banner state: when was any album row last reviewed, and is the
    board due for the annual sweep (>365 days)."""
    last = db.query(func.max(AlltimeAlbum.last_reviewed_at)).scalar()
    total = db.query(func.count(AlltimeAlbum.id)).scalar() or 0
    if not last:
        return {"last_reviewed_at": None, "days_since": None,
                "due": total > 0, "total": total}
    days = (datetime.utcnow() - last).days
    return {
        "last_reviewed_at": last.isoformat(),
        "days_since": days,
        "due": days > _STALE_DAYS,
        "total": total,
    }


def _admin_album_row(r: AlltimeAlbum) -> dict:
    return {
        "id": r.id,
        "rank": r.rank,
        "album_title": r.album_title,
        "artist": r.artist,
        "certified_units": r.certified_units,
        "units_millions": r.units_millions,
        "release_year": r.release_year,
        "release_id": r.release_id,
        "release_slug": r.release_slug,
        "artist_slug": r.artist_slug,
        "rubric_color": r.rubric_color,
        "charge_value": r.charge_value,
        "deadpan_line": r.deadpan_line,
        "topics": _load_topics(r.topics),
        "calibrated": r.rubric_color is not None,
        "last_reviewed_at": r.last_reviewed_at.isoformat() if r.last_reviewed_at else None,
    }


@router.get("/api/admin/alltime/albums", dependencies=[Depends(verify_admin_key)])
def admin_list_albums(db: Session = Depends(get_db)):
    rows = db.query(AlltimeAlbum).order_by(AlltimeAlbum.rank.asc()).all()
    return {"staleness": _staleness(db), "rows": [_admin_album_row(r) for r in rows]}


@router.post("/api/admin/alltime/albums", dependencies=[Depends(verify_admin_key)])
def admin_create_album(payload: dict = Body(...), db: Session = Depends(get_db)):
    if not payload.get("album_title") or not payload.get("artist"):
        raise HTTPException(status_code=400, detail="album_title and artist are required")
    row = AlltimeAlbum(
        rank=int(payload.get("rank") or 0),
        album_title=str(payload["album_title"]).strip(),
        artist=str(payload["artist"]).strip(),
        certified_units=(payload.get("certified_units") or None),
        units_millions=payload.get("units_millions"),
        release_year=payload.get("release_year"),
        last_reviewed_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return _admin_album_row(row)


@router.patch("/api/admin/alltime/albums/{album_id}", dependencies=[Depends(verify_admin_key)])
def admin_update_album(album_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    row = db.get(AlltimeAlbum, album_id)
    if not row:
        raise HTTPException(status_code=404, detail="Album row not found")
    for field in ("rank", "album_title", "artist", "certified_units",
                  "units_millions", "release_year"):
        if field in payload:
            setattr(row, field, payload[field])
    row.last_reviewed_at = datetime.utcnow()
    db.commit()
    return _admin_album_row(row)


@router.delete("/api/admin/alltime/albums/{album_id}", dependencies=[Depends(verify_admin_key)])
def admin_delete_album(album_id: int, db: Session = Depends(get_db)):
    row = db.get(AlltimeAlbum, album_id)
    if not row:
        raise HTTPException(status_code=404, detail="Album row not found")
    db.delete(row)
    db.commit()
    return {"deleted": album_id}


@router.get("/api/admin/alltime/albums/release-search", dependencies=[Depends(verify_admin_key)])
def admin_release_search(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    """Find already-charged Releases to link to an album chart row. Only releases
    that carry an album-level reading (rubric_color set) are useful here."""
    like = f"%{q.strip()}%"
    rows = (
        db.query(Release, Artist.name, Artist.slug)
        .join(Artist, Release.artist_id == Artist.id)
        .filter(Release.title.ilike(like))
        .order_by(Release.rubric_color.isnot(None).desc(), Release.title.asc())
        .limit(20)
        .all()
    )
    return {
        "rows": [
            {
                "release_id": rel.id,
                "title": rel.title,
                "artist": aname,
                "artist_slug": aslug,
                "release_year": rel.release_year,
                "rubric_color": rel.rubric_color,
                "charge_value": rel.charge_value,
                "has_reading": rel.rubric_color is not None,
            }
            for rel, aname, aslug in rows
        ]
    }


@router.post("/api/admin/alltime/albums/{album_id}/link-release", dependencies=[Depends(verify_admin_key)])
def admin_link_release(album_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Link a chart row to an already-charged Release and copy its display fields
    (charge + album-level deadpan + topics) onto the row. The Release is created
    by the Album Charger (or the terminal album path for >15-track comps), which
    already persists every field we need."""
    row = db.get(AlltimeAlbum, album_id)
    if not row:
        raise HTTPException(status_code=404, detail="Album row not found")
    release_id = payload.get("release_id")
    if not release_id:
        raise HTTPException(status_code=400, detail="release_id is required")
    rel = db.get(Release, int(release_id))
    if not rel:
        raise HTTPException(status_code=404, detail="Release not found")
    artist = db.get(Artist, rel.artist_id)

    row.release_id = rel.id
    row.release_slug = slugify(rel.title)
    row.artist_slug = artist.slug if artist else None
    row.rubric_color = rel.rubric_color
    row.charge_value = rel.charge_value
    row.charge_summary = rel.charge_summary
    row.deadpan_line = rel.deadpan_line
    row.topics = rel.topics  # already JSON-encoded on the Release
    row.last_reviewed_at = datetime.utcnow()
    db.commit()
    return _admin_album_row(row)
