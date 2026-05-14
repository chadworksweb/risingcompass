from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AlbumDeepDive
from app.schemas import AlbumOut, AlbumSummary
from app.services.artist_utils import normalize_artist_name, resolve_artist_slugs

router = APIRouter(prefix="/api/albums", tags=["albums"])


@router.get("", response_model=list[AlbumSummary])
def list_albums(db: Session = Depends(get_db)):
    """List all album deep dives."""
    albums = db.query(AlbumDeepDive).order_by(AlbumDeepDive.release_year.desc()).all()
    slug_map = resolve_artist_slugs([a.artist for a in albums], db)
    out: list[AlbumSummary] = []
    for a in albums:
        primary = normalize_artist_name(a.artist or "").lower()
        out.append(AlbumSummary(
            id=a.id,
            title=a.title,
            artist=a.artist,
            slug=a.slug,
            release_year=a.release_year,
            overall_color=a.overall_color,
            artist_slug=slug_map.get(primary),
        ))
    return out


@router.get("/{slug}", response_model=AlbumOut)
def get_album(slug: str, db: Session = Depends(get_db)):
    """Full album with tracks."""
    album = db.query(AlbumDeepDive).filter(AlbumDeepDive.slug == slug).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    slug_map = resolve_artist_slugs([album.artist], db)
    primary = normalize_artist_name(album.artist or "").lower()
    return AlbumOut(
        id=album.id,
        title=album.title,
        artist=album.artist,
        slug=album.slug,
        release_year=album.release_year,
        overall_color=album.overall_color,
        summary=album.summary,
        tracks=album.tracks,
        artist_slug=slug_map.get(primary),
    )
