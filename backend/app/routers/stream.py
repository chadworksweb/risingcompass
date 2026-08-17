"""CL Stream -- lowest-friction path from hearing a song to calibrated entry.

Flow: paste Tidal URL (or title+artist) + why note -> auto-resolve metadata ->
fetch lyrics -> calibrate via rubric -> store. One call, done.

Unified song-entity renovation (Phase 5b): native. A "stream entry" is now a
`song_ingestions` row with method='stream' hanging off the atomic `songs` row;
the workflow columns (note / source_url / source_platform / status /
promoted_to) live in that ingestion's `detail` JSON. There is no cl_stream_songs
row. The admin responses are rebuilt from songs + the stream ingestion. Promotion
no longer copies a row between tables -- the song already IS in the Library; it
only elevates the canonical calibration to authoritative (so a later crowd read
can't override it) and records the promotion.
"""

import json
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import StreamSongIn, StreamSongOut, StreamPromoteIn
from app.routers.admin import verify_admin_key
from app.services.agents.calibrator import calibrate_song_async
from app.services import musixmatch, song_removal
from app.services.artist_linker import parse_artist_string
from app.services.calibration_corpus import record_and_reconcile, hash_lyrics
from app.services.song_sync import store_calibrated_song

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/stream", tags=["cl-stream"])


# --- response rebuild (songs + stream ingestion) ---

# One stream entry = one song_ingestions row (method='stream') joined to its
# songs row. `id` is the ingestion id (stable per stream submission); the
# workflow fields come off the ingestion detail, the calibration off songs.
_STREAM_SELECT = """
    SELECT i.id AS id, i.detail AS detail, i.created_at AS created_at,
           s.id AS song_id, s.title AS title, s.artist AS artist,
           s.rubric_color AS rubric_color, s.charge_value AS charge_value,
           s.contaminated AS contaminated, s.contamination_note AS contamination_note,
           s.charge_summary AS charge_summary, s.confidence AS confidence
    FROM song_ingestions i
    JOIN songs s ON s.id = i.song_id
    WHERE i.method = 'stream'
"""


def _stream_out(row) -> dict:
    """Build a StreamSongOut-shaped dict from a stream ingestion row (joined to
    songs). The workflow columns live in ingestion.detail; the calibration on
    songs."""
    detail = row["detail"]
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {}
    detail = detail or {}
    return {
        "id": row["id"],
        "title": row["title"],
        "artist": row["artist"],
        "note": detail.get("note") or "",
        "source_url": detail.get("source_url"),
        "source_platform": detail.get("source_platform"),
        "rubric_color": row["rubric_color"],
        "charge_value": row["charge_value"],
        "contaminated": bool(row["contaminated"]),
        "contamination_note": row["contamination_note"],
        "charge_summary": row["charge_summary"],
        "confidence": row["confidence"],
        "status": detail.get("status") or ("calibrated" if row["rubric_color"] else "failed"),
        "promoted_to": detail.get("promoted_to"),
        "created_at": row["created_at"],
    }


def _fetch_stream_entry(db: Session, ingestion_id: int):
    return db.execute(
        text(_STREAM_SELECT + " AND i.id = :i"), {"i": ingestion_id}
    ).mappings().first()


# --- Tidal URL resolution ---

_TIDAL_TRACK_RE = re.compile(r"tidal\.com/(?:browse/)?track/(\d+)", re.IGNORECASE)


async def _resolve_tidal(url: str) -> dict | None:
    """Extract title + artist from a Tidal share URL via oEmbed."""
    match = _TIDAL_TRACK_RE.search(url)
    if not match:
        return None

    oembed_url = f"https://oembed.tidal.com/?url={url}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(oembed_url)
            resp.raise_for_status()
            data = resp.json()

        # oEmbed title format is typically "Title by Artist"
        title_raw = data.get("title", "")
        author = data.get("author_name", "")

        # Try splitting "Title by Artist" pattern
        if " by " in title_raw and author:
            title = title_raw.rsplit(" by ", 1)[0].strip()
        else:
            title = title_raw

        return {
            "title": title or "Unknown",
            "artist": author or "Unknown",
            "platform": "tidal",
        }
    except Exception:
        logger.exception("Tidal oEmbed resolution failed for %s", url)
        return None


def _detect_platform(url: str) -> str | None:
    if not url:
        return None
    if "tidal.com" in url:
        return "tidal"
    if "spotify.com" in url:
        return "spotify"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "other"


# --- Endpoints ---

@router.post("", response_model=StreamSongOut, dependencies=[Depends(verify_admin_key)])
async def add_to_stream(body: StreamSongIn, db: Session = Depends(get_db)):
    """Add a song to the CL Stream. Accepts Tidal URL or title+artist. Auto-calibrates."""

    title = body.title
    artist = body.artist
    platform = None

    # Resolve from URL if provided
    if body.source_url:
        platform = _detect_platform(body.source_url)

        if platform == "tidal" and (not title or not artist):
            resolved = await _resolve_tidal(body.source_url)
            if resolved:
                title = title or resolved["title"]
                artist = artist or resolved["artist"]

    if not title or not artist:
        raise HTTPException(400, "title and artist are required (or provide a resolvable Tidal URL)")

    # Fetch lyrics via Musixmatch
    lyrics = None
    tracks = await musixmatch.search_tracks(title, artist=artist, limit=1)
    if tracks:
        lyrics = await musixmatch.get_lyrics(tracks[0]["track_id"])

    # Calibrate
    result = await calibrate_song_async(title, artist, lyrics=lyrics, db=db)

    if result.get("rubric_color") is None:
        # Native model: a stream entry can't exist without a calibrated songs
        # row to hang its ingestion on (song_ingestions.song_id is NOT NULL).
        # A failed calibration is surfaced immediately instead of persisting a
        # status='failed' placeholder row to clean up later. Re-submit to retry.
        raise HTTPException(422, "Calibration failed -- no rubric color. Re-submit to retry.")

    # Native unified storage: upsert the atomic songs row (crowd 'stream' method
    # -> never overrides an existing authoritative calibration) + a stream
    # ingestion carrying the workflow detail (note/url/platform/status). No
    # cl_stream_songs row.
    detail = {
        "note": body.note,
        "source_url": body.source_url,
        "source_platform": platform,
        "status": "calibrated",
        "promoted_to": None,
    }
    song_id, created = store_calibrated_song(
        db, source="stream",
        title=title, artist=artist, calibration=result,
        ingestion_detail=detail,
        artist_entries=parse_artist_string(artist or ""),
    )
    db.commit()

    try:
        record_and_reconcile(
            db,
            title=title,
            artist=artist,
            calibration=result,
            triggered_by="cl_stream",
            lyrics_hash=hash_lyrics(lyrics),
            agent_model=result.get("agent_model"),
            direct_song_source="songs",
            direct_song_id=song_id,
            is_new_row=created,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Corpus log failed for stream song %s", song_id)

    row = db.execute(
        text(_STREAM_SELECT + " AND i.song_id = :s ORDER BY i.id DESC"),
        {"s": song_id},
    ).mappings().first()
    return _stream_out(row)


@router.get("", response_model=list[StreamSongOut], dependencies=[Depends(verify_admin_key)])
def list_stream(
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """List CL Stream entries, newest first."""
    sql = _STREAM_SELECT
    params: dict = {"lim": limit}
    if status:
        sql += " AND i.detail IS NOT NULL AND (i.detail::jsonb ->> 'status') = :status"
        params["status"] = status
    sql += " ORDER BY i.created_at DESC, i.id DESC LIMIT :lim"
    rows = db.execute(text(sql), params).mappings().all()
    return [_stream_out(r) for r in rows]


@router.get("/{song_id}", response_model=StreamSongOut, dependencies=[Depends(verify_admin_key)])
def get_stream_song(song_id: int, db: Session = Depends(get_db)):
    row = _fetch_stream_entry(db, song_id)
    if not row:
        raise HTTPException(404, "Stream song not found")
    return _stream_out(row)


@router.post("/{song_id}/promote", response_model=StreamSongOut, dependencies=[Depends(verify_admin_key)])
def promote_stream_song(song_id: int, body: StreamPromoteIn, db: Session = Depends(get_db)):
    """Promote a stream song into the authoritative Library.

    In the unified model the song already IS in the Library, so promotion no
    longer copies a row between tables. It (a) elevates the canonical
    calibration method to authoritative -- catalog_backfill (library) / chart_reading
    (compass) -- so a later crowd read can't override it, (b) records a
    catalog_backfill/chart_reading ingestion breadcrumb, and (c) marks the stream
    ingestion promoted. A 'cl_stream' pick maps to no aggregating chart, so it
    stays out of the compass charge structurally (matching the legacy
    chart_position=0 / chart_source='cl_stream' non-chart behavior)."""
    row = _fetch_stream_entry(db, song_id)
    if not row:
        raise HTTPException(404, "Stream song not found")
    detail = row["detail"]
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {}
    detail = detail or {}
    if detail.get("status") == "promoted":
        raise HTTPException(409, f"Already promoted to {detail.get('promoted_to')}")
    if not row["rubric_color"]:
        raise HTTPException(400, "Cannot promote -- calibration failed. Re-calibrate first.")

    sid = row["song_id"]
    method = "chart_reading" if body.target == "compass" else "catalog_backfill"
    db.execute(
        text("UPDATE songs SET canonical_calibration_method = :m WHERE id = :i"),
        {"m": method, "i": sid},
    )
    # catalog_backfill/chart_reading ingestion breadcrumb (one per song+method).
    if not db.execute(
        text("SELECT 1 FROM song_ingestions WHERE song_id = :s AND method = :m LIMIT 1"),
        {"s": sid, "m": method},
    ).scalar():
        db.execute(
            text("INSERT INTO song_ingestions (song_id, method, detail) VALUES (:s, :m, :d)"),
            {"s": sid, "m": method, "d": json.dumps({"promoted_from": "stream", "target": body.target})},
        )
    detail["status"] = "promoted"
    detail["promoted_to"] = body.target
    db.execute(
        text("UPDATE song_ingestions SET detail = :d WHERE id = :i"),
        {"d": json.dumps(detail), "i": song_id},
    )
    db.commit()
    return _stream_out(_fetch_stream_entry(db, song_id))


@router.delete("/{song_id}", dependencies=[Depends(verify_admin_key)])
def delete_stream_song(song_id: int, db: Session = Depends(get_db)):
    """Remove a stream entry. Deletes the stream ingestion, then asks
    `song_removal.remove_song` whether anything real still holds the song: if it
    is now a pure stream artifact the song and its directly-owned rows go too,
    mirroring the legacy 'the row disappears'. A song that has since charted, been
    read, drafted, released, or ingested by another feeder is kept and only the
    stream ingestion drops. The hard-reference list lives in `song_removal` --
    this endpoint used to carry its own copy and it was missing `release_songs`,
    so a stream delete could strand a release track."""
    row = _fetch_stream_entry(db, song_id)
    if not row:
        raise HTTPException(404, "Stream song not found")
    sid = row["song_id"]
    title, artist = row["title"], row["artist"]

    db.execute(text("DELETE FROM song_ingestions WHERE id = :i"), {"i": song_id})

    result = song_removal.remove_song(db, sid, ingestion_holds=True)
    db.commit()
    return {"deleted": song_id, "title": title, "artist": artist,
            "song_removed": result["song_removed"], "kept_reason": result["kept_reason"]}
