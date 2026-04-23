"""CL Stream — lowest-friction path from hearing a song to calibrated entry.

Flow: paste Tidal URL (or title+artist) + why note → auto-resolve metadata →
fetch lyrics → calibrate via rubric → store. One call, done.
"""

import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import StreamSong, LibrarySong, CompassSong
from app.schemas import StreamSongIn, StreamSongOut, StreamPromoteIn
from app.routers.admin import verify_admin_key
from app.services.agents.calibrator import calibrate_song_async
from app.services import musixmatch
from app.services.artist_linker import try_link_song
from app.services.calibration_corpus import record_and_reconcile, hash_lyrics, get_or_create_song

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/stream", tags=["cl-stream"])


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

    status = "calibrated" if result.get("rubric_color") else "failed"

    song, _created = get_or_create_song(
        db, StreamSong,
        title=title,
        artist=artist,
        note=body.note,
        source_url=body.source_url,
        source_platform=platform,
        rubric_color=result.get("rubric_color"),
        charge_value=result.get("charge_value"),
        contaminated=result.get("contaminated", False),
        contamination_note=result.get("contamination_note"),
        charge_summary=result.get("charge_summary"),
        confidence=result.get("confidence"),
        status=status,
    )
    db.commit()
    db.refresh(song)
    if status == "calibrated":
        try:
            record_and_reconcile(
                db,
                title=title,
                artist=artist,
                calibration=result,
                triggered_by="cl_stream",
                lyrics_hash=hash_lyrics(lyrics),
                agent_model=result.get("agent_model"),
                direct_song_source="stream",
                direct_song_id=song.id,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Corpus log failed for stream song %d", song.id)
    return song


@router.get("", response_model=list[StreamSongOut], dependencies=[Depends(verify_admin_key)])
def list_stream(
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """List CL Stream entries, newest first."""
    q = db.query(StreamSong)
    if status:
        q = q.filter(StreamSong.status == status)
    return q.order_by(StreamSong.created_at.desc()).limit(limit).all()


@router.get("/{song_id}", response_model=StreamSongOut, dependencies=[Depends(verify_admin_key)])
def get_stream_song(song_id: int, db: Session = Depends(get_db)):
    song = db.query(StreamSong).filter(StreamSong.id == song_id).first()
    if not song:
        raise HTTPException(404, "Stream song not found")
    return song


@router.post("/{song_id}/promote", response_model=StreamSongOut, dependencies=[Depends(verify_admin_key)])
def promote_stream_song(song_id: int, body: StreamPromoteIn, db: Session = Depends(get_db)):
    """Promote a stream song into library_songs or compass_songs."""
    song = db.query(StreamSong).filter(StreamSong.id == song_id).first()
    if not song:
        raise HTTPException(404, "Stream song not found")
    if song.status == "promoted":
        raise HTTPException(409, f"Already promoted to {song.promoted_to}")
    if not song.rubric_color:
        raise HTTPException(400, "Cannot promote — calibration failed. Re-calibrate first.")

    if body.target == "library":
        entry, _created = get_or_create_song(
            db, LibrarySong,
            title=song.title,
            artist=song.artist,
            rubric_color=song.rubric_color,
            charge_value=song.charge_value,
            contaminated=song.contaminated,
            contamination_note=song.contamination_note,
            charge_summary=song.charge_summary,
            source="stream",
        )

    elif body.target == "compass":
        import datetime
        entry, _created = get_or_create_song(
            db, CompassSong,
            title=song.title,
            artist=song.artist,
            year=datetime.datetime.utcnow().year,
            decade=f"{(datetime.datetime.utcnow().year // 10) * 10}s",
            chart_position=0,  # non-chart
            rubric_color=song.rubric_color,
            charge_value=song.charge_value,
            contaminated=song.contaminated,
            contamination_note=song.contamination_note,
            charge_summary=song.charge_summary,
            chart_source="cl_stream",
        )

    song.status = "promoted"
    song.promoted_to = body.target
    db.commit()
    db.refresh(song)
    db.refresh(entry)
    try_link_song(entry.title, entry.artist, body.target, entry.id, db)
    return song


@router.delete("/{song_id}", dependencies=[Depends(verify_admin_key)])
def delete_stream_song(song_id: int, db: Session = Depends(get_db)):
    song = db.query(StreamSong).filter(StreamSong.id == song_id).first()
    if not song:
        raise HTTPException(404, "Stream song not found")
    db.delete(song)
    db.commit()
    return {"deleted": song_id, "title": song.title, "artist": song.artist}
