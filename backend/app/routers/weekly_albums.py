from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from app.database import get_db
from app.models import WeeklyAlbumReading
from app.schemas import (
    WeeklyAlbumReadingOut, PaginatedWeeklyAlbumReadings,
    WeeklyAlbumEntryOut,
)
from app.services.artist_utils import normalize_artist_name, resolve_artist_slugs

router = APIRouter(prefix="/api/weekly-albums", tags=["weekly-albums"])


def _attach_artist_slugs(reading: WeeklyAlbumReading, db: Session) -> WeeklyAlbumReadingOut:
    slug_map = resolve_artist_slugs([a.artist for a in reading.albums], db)
    entries = []
    for a in reading.albums:
        primary = normalize_artist_name(a.artist or "").lower()
        entries.append(WeeklyAlbumEntryOut(
            id=a.id,
            title=a.title,
            artist=a.artist,
            position=a.position,
            rubric_color=a.rubric_color,
            contaminated=a.contaminated,
            contamination_note=a.contamination_note,
            charge_summary=a.charge_summary,
            chart_source=a.chart_source,
            artist_slug=slug_map.get(primary),
        ))
    return WeeklyAlbumReadingOut(
        id=reading.id,
        week_date=reading.week_date,
        compass_degree=reading.compass_degree,
        charge_level=reading.charge_level,
        contamination_count=reading.contamination_count,
        editorial_summary=reading.editorial_summary,
        albums=entries,
    )


@router.get("/current", response_model=WeeklyAlbumReadingOut | None)
def get_current(db: Session = Depends(get_db)):
    """Most recent weekly album reading."""
    reading = db.query(WeeklyAlbumReading).order_by(WeeklyAlbumReading.week_date.desc()).first()
    if not reading:
        return None
    return _attach_artist_slugs(reading, db)


@router.get("/history", response_model=PaginatedWeeklyAlbumReadings)
def get_history(page: int = 1, per_page: int = 10, db: Session = Depends(get_db)):
    """Paginated past weekly album readings."""
    total = db.query(func.count(WeeklyAlbumReading.id)).scalar()
    pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    items = (
        db.query(WeeklyAlbumReading)
        .order_by(WeeklyAlbumReading.week_date.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    return PaginatedWeeklyAlbumReadings(items=items, total=total, page=page, pages=pages)


@router.get("/reading/{week_date}", response_model=WeeklyAlbumReadingOut)
def get_reading(week_date: date, db: Session = Depends(get_db)):
    """Specific week's album reading."""
    reading = db.query(WeeklyAlbumReading).filter(WeeklyAlbumReading.week_date == week_date).first()
    if not reading:
        raise HTTPException(status_code=404, detail="No album reading for this week")
    return _attach_artist_slugs(reading, db)
