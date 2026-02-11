from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AlbumDeepDive
from app.schemas import AlbumOut, AlbumSummary

router = APIRouter(prefix="/api/albums", tags=["albums"])


@router.get("", response_model=list[AlbumSummary])
def list_albums(db: Session = Depends(get_db)):
    """List all album deep dives."""
    return db.query(AlbumDeepDive).order_by(AlbumDeepDive.release_year.desc()).all()


@router.get("/{slug}", response_model=AlbumOut)
def get_album(slug: str, db: Session = Depends(get_db)):
    """Full album with tracks."""
    album = db.query(AlbumDeepDive).filter(AlbumDeepDive.slug == slug).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    return album
