"""Admin endpoints for library songs -- non-chart archive (manual + agent-scanned).

Unified song-entity renovation (Phase 5b): native. A "library song" is now a
`songs` row carrying a `catalog_backfill` song_ingestion (the workflow `source`
field lives in that ingestion's detail). album_id / track_number live on `songs`.
There is no library_songs row. Responses are rebuilt from songs + the
catalog_backfill ingestion.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Song
from app.schemas import LibrarySongCreate, LibrarySongUpdate, LibrarySongOut
from app.routers.admin import verify_admin_key
from app.services import song_removal
from app.services.artist_linker import parse_artist_string, link_song_artists
from app.services.song_identity import compute_canonical_key
from app.services.song_sync import store_calibrated_song

router = APIRouter(prefix="/api/admin/library", tags=["library-admin"])


def _catalog_source(db: Session, song_id: int) -> str:
    """The workflow `source` for a library song lives in its catalog_backfill
    ingestion detail. Defaults to 'manual'."""
    det = db.execute(
        text("SELECT detail FROM song_ingestions WHERE song_id = :i AND method = 'catalog_backfill' ORDER BY id LIMIT 1"),
        {"i": song_id},
    ).scalar()
    if det:
        try:
            return (json.loads(det) or {}).get("source") or "manual"
        except Exception:
            pass
    return "manual"


def _library_out(db: Session, song_id: int) -> dict:
    s = db.execute(text(
        "SELECT id, title, artist, rubric_color, charge_value, contaminated, "
        "contamination_note, charge_summary, album_id, track_number, created_at "
        "FROM songs WHERE id = :i"
    ), {"i": song_id}).mappings().first()
    out = dict(s)
    out["contaminated"] = bool(out["contaminated"])
    out["source"] = _catalog_source(db, song_id)
    return out


@router.get("/songs", response_model=list[LibrarySongOut], dependencies=[Depends(verify_admin_key)])
def list_library_songs(
    album_id: int | None = Query(None),
    artist: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List library songs (songs carrying a catalog_backfill ingestion), optionally
    filtered by album or artist."""
    sql = (
        "SELECT s.id FROM songs s "
        "WHERE EXISTS (SELECT 1 FROM song_ingestions i WHERE i.song_id = s.id AND i.method = 'catalog_backfill')"
    )
    params: dict = {}
    if album_id is not None:
        sql += " AND s.album_id = :album_id"
        params["album_id"] = album_id
    if artist is not None:
        sql += " AND s.artist ILIKE :artist"
        params["artist"] = f"%{artist}%"
    sql += " ORDER BY s.album_id, s.track_number, s.id"
    ids = [r[0] for r in db.execute(text(sql), params).all()]
    return [_library_out(db, sid) for sid in ids]


@router.post("/songs", response_model=LibrarySongOut, dependencies=[Depends(verify_admin_key)])
def create_library_song(data: LibrarySongCreate, db: Session = Depends(get_db)):
    """Add a song to the library (standalone or album-linked)."""
    title = data.title
    artist = data.artist
    key = compute_canonical_key(title, artist)
    existing = db.execute(
        text("SELECT id, title, artist FROM songs WHERE canonical_key = :k"), {"k": key}
    ).mappings().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Already in library as ID {existing['id']}: {existing['title']} -- {existing['artist']}",
        )

    calibration = {
        "rubric_color": data.rubric_color,
        "charge_value": data.charge_value,
        "contaminated": data.contaminated,
        "contamination_note": data.contamination_note,
        "charge_summary": data.charge_summary,
    }
    song_id, _created = store_calibrated_song(
        db, source="library",
        title=title, artist=artist, calibration=calibration,
        album_id=data.album_id, track_number=data.track_number,
        ingestion_detail={"source": data.source},
        artist_entries=parse_artist_string(artist or ""),
    )
    db.commit()
    return _library_out(db, song_id)


@router.put("/songs/{song_id}", response_model=LibrarySongOut, dependencies=[Depends(verify_admin_key)])
def update_library_song(song_id: int, data: LibrarySongUpdate, db: Session = Depends(get_db)):
    """Update a library song (the unified songs row)."""
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail=f"Library song ID {song_id} not found")

    fields = data.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(song, field, value)
    # Recompute the canonical key if the identity changed.
    if "title" in fields or "artist" in fields:
        song.canonical_key = compute_canonical_key(song.title, song.artist)

    db.commit()
    db.refresh(song)
    # Re-link artist credits onto the unified id.
    link_song_artists(
        db, song_id=song.id, entries=parse_artist_string(song.artist or ""),
    )
    db.commit()
    return _library_out(db, song.id)


@router.delete("/songs/{song_id}", dependencies=[Depends(verify_admin_key)])
def delete_library_song(song_id: int, db: Session = Depends(get_db)):
    """Delete a library song. Removes the catalog_backfill ingestion, then asks
    `song_removal.remove_song` whether anything real still holds the song: if it is
    now a pure library artifact the song and its directly-owned rows go too. A song
    that also charts or is on a release is kept and only the catalog_backfill
    ingestion drops. The hard-reference list lives in `song_removal`, not here."""
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail=f"Library song ID {song_id} not found")
    title, artist = song.title, song.artist

    db.execute(text("DELETE FROM song_ingestions WHERE song_id = :s AND method = 'catalog_backfill'"), {"s": song_id})

    result = song_removal.remove_song(db, song_id, ingestion_holds=True)
    db.commit()
    return {"deleted": song_id, "title": title, "artist": artist,
            "song_removed": result["song_removed"], "kept_reason": result["kept_reason"]}
