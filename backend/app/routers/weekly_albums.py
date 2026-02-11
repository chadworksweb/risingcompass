from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from app.database import get_db
from app.models import WeeklyAlbumReading, WeeklyAlbumEntry
from app.schemas import (
    WeeklyAlbumReadingOut, WeeklyAlbumReadingSummary, PaginatedWeeklyAlbumReadings
)

router = APIRouter(prefix="/api/weekly-albums", tags=["weekly-albums"])


@router.get("/current", response_model=WeeklyAlbumReadingOut | None)
def get_current(db: Session = Depends(get_db)):
    """Most recent weekly album reading."""
    reading = db.query(WeeklyAlbumReading).order_by(WeeklyAlbumReading.week_date.desc()).first()
    if not reading:
        return None
    return reading


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
    return reading
