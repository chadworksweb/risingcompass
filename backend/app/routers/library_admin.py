"""Admin endpoints for library songs — non-chart archive (manual + agent-scanned)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LibrarySong, AlbumDeepDive
from app.schemas import LibrarySongCreate, LibrarySongUpdate, LibrarySongOut
from app.routers.admin import verify_admin_key

router = APIRouter(prefix="/api/admin/library", tags=["library-admin"])


@router.get("/songs", response_model=list[LibrarySongOut], dependencies=[Depends(verify_admin_key)])
def list_library_songs(
    album_id: int | None = Query(None),
    artist: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List library songs, optionally filtered by album or artist."""
    q = db.query(LibrarySong)
    if album_id is not None:
        q = q.filter(LibrarySong.album_id == album_id)
    if artist is not None:
        q = q.filter(LibrarySong.artist.ilike(f"%{artist}%"))
    return q.order_by(LibrarySong.album_id, LibrarySong.track_number, LibrarySong.id).all()


@router.post("/songs", response_model=LibrarySongOut, dependencies=[Depends(verify_admin_key)])
def create_library_song(data: LibrarySongCreate, db: Session = Depends(get_db)):
    """Add a song to the library (standalone or album-linked)."""
    if data.album_id is not None:
        album = db.query(AlbumDeepDive).filter(AlbumDeepDive.id == data.album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail=f"Album ID {data.album_id} not found")

    song = LibrarySong(**data.model_dump())
    db.add(song)
    db.commit()
    db.refresh(song)
    return song


@router.put("/songs/{song_id}", response_model=LibrarySongOut, dependencies=[Depends(verify_admin_key)])
def update_library_song(song_id: int, data: LibrarySongUpdate, db: Session = Depends(get_db)):
    """Update a library song."""
    song = db.query(LibrarySong).filter(LibrarySong.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail=f"Library song ID {song_id} not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(song, field, value)

    db.commit()
    db.refresh(song)
    return song


@router.delete("/songs/{song_id}", dependencies=[Depends(verify_admin_key)])
def delete_library_song(song_id: int, db: Session = Depends(get_db)):
    """Delete a library song."""
    song = db.query(LibrarySong).filter(LibrarySong.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail=f"Library song ID {song_id} not found")
    title, artist = song.title, song.artist
    db.delete(song)
    db.commit()
    return {"deleted": song_id, "title": title, "artist": artist}
