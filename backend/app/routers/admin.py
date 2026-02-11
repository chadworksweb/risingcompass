from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from app.database import get_db
from app.models import DailyReading, ReadingSong, WeeklyAlbumReading, WeeklyAlbumEntry
from app.schemas import (
    ReadingCreate, ReadingUpdate, DailyReadingOut,
    WeeklyAlbumReadingCreate, WeeklyAlbumReadingUpdate, WeeklyAlbumReadingOut,
)
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated
from app.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != settings.rc_admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    """Serve admin HTML form for entering daily readings."""
    return templates.TemplateResponse("admin.html", {"request": request})


@router.post("/reading", response_model=DailyReadingOut, dependencies=[Depends(verify_admin_key)])
def create_reading(data: ReadingCreate, db: Session = Depends(get_db)):
    """Create a new daily reading."""
    existing = db.query(DailyReading).filter(DailyReading.date == data.date).first()
    if existing:
        raise HTTPException(status_code=409, detail="Reading already exists for this date. Use PUT to update.")

    song_dicts = [s.model_dump() for s in data.songs]
    degree = compute_degree(song_dicts)
    charge = degree_to_charge(degree)
    contam = count_contaminated(song_dicts)

    reading = DailyReading(
        date=data.date,
        compass_degree=degree,
        charge_level=charge,
        contamination_count=contam,
        editorial_summary=data.editorial_summary,
    )
    db.add(reading)
    db.flush()

    for s in data.songs:
        rs = ReadingSong(
            reading_id=reading.id,
            title=s.title,
            artist=s.artist,
            position=s.position,
            rubric_color=s.rubric_color,
            contaminated=s.contaminated,
            contamination_note=s.contamination_note,
            charge_summary=s.charge_summary,
            chart_source=s.chart_source,
        )
        db.add(rs)

    db.commit()
    db.refresh(reading)
    return reading


@router.put("/reading/{reading_date}", response_model=DailyReadingOut, dependencies=[Depends(verify_admin_key)])
def update_reading(reading_date: str, data: ReadingUpdate, db: Session = Depends(get_db)):
    """Update an existing daily reading."""
    reading = db.query(DailyReading).filter(DailyReading.date == reading_date).first()
    if not reading:
        raise HTTPException(status_code=404, detail="No reading for this date")

    if data.editorial_summary is not None:
        reading.editorial_summary = data.editorial_summary

    if data.songs is not None:
        # Replace all songs
        db.query(ReadingSong).filter(ReadingSong.reading_id == reading.id).delete()

        song_dicts = [s.model_dump() for s in data.songs]
        reading.compass_degree = compute_degree(song_dicts)
        reading.charge_level = degree_to_charge(reading.compass_degree)
        reading.contamination_count = count_contaminated(song_dicts)

        for s in data.songs:
            rs = ReadingSong(
                reading_id=reading.id,
                title=s.title,
                artist=s.artist,
                position=s.position,
                rubric_color=s.rubric_color,
                contaminated=s.contaminated,
                contamination_note=s.contamination_note,
                charge_summary=s.charge_summary,
                chart_source=s.chart_source,
            )
            db.add(rs)

    db.commit()
    db.refresh(reading)
    return reading


# --- Weekly Album Reading endpoints ---

@router.post("/weekly-album-reading", response_model=WeeklyAlbumReadingOut, dependencies=[Depends(verify_admin_key)])
def create_weekly_album_reading(data: WeeklyAlbumReadingCreate, db: Session = Depends(get_db)):
    """Create a new weekly album reading."""
    existing = db.query(WeeklyAlbumReading).filter(WeeklyAlbumReading.week_date == data.week_date).first()
    if existing:
        raise HTTPException(status_code=409, detail="Album reading already exists for this week. Use PUT to update.")

    album_dicts = [a.model_dump() for a in data.albums]
    degree = compute_degree(album_dicts)
    charge = degree_to_charge(degree)
    contam = count_contaminated(album_dicts)

    reading = WeeklyAlbumReading(
        week_date=data.week_date,
        compass_degree=degree,
        charge_level=charge,
        contamination_count=contam,
        editorial_summary=data.editorial_summary,
    )
    db.add(reading)
    db.flush()

    for a in data.albums:
        entry = WeeklyAlbumEntry(
            reading_id=reading.id,
            title=a.title,
            artist=a.artist,
            position=a.position,
            rubric_color=a.rubric_color,
            contaminated=a.contaminated,
            contamination_note=a.contamination_note,
            charge_summary=a.charge_summary,
            chart_source=a.chart_source,
        )
        db.add(entry)

    db.commit()
    db.refresh(reading)
    return reading


@router.put("/weekly-album-reading/{week_date}", response_model=WeeklyAlbumReadingOut, dependencies=[Depends(verify_admin_key)])
def update_weekly_album_reading(week_date: str, data: WeeklyAlbumReadingUpdate, db: Session = Depends(get_db)):
    """Update an existing weekly album reading."""
    reading = db.query(WeeklyAlbumReading).filter(WeeklyAlbumReading.week_date == week_date).first()
    if not reading:
        raise HTTPException(status_code=404, detail="No album reading for this week")

    if data.editorial_summary is not None:
        reading.editorial_summary = data.editorial_summary

    if data.albums is not None:
        db.query(WeeklyAlbumEntry).filter(WeeklyAlbumEntry.reading_id == reading.id).delete()

        album_dicts = [a.model_dump() for a in data.albums]
        reading.compass_degree = compute_degree(album_dicts)
        reading.charge_level = degree_to_charge(reading.compass_degree)
        reading.contamination_count = count_contaminated(album_dicts)

        for a in data.albums:
            entry = WeeklyAlbumEntry(
                reading_id=reading.id,
                title=a.title,
                artist=a.artist,
                position=a.position,
                rubric_color=a.rubric_color,
                contaminated=a.contaminated,
                contamination_note=a.contamination_note,
                charge_summary=a.charge_summary,
                chart_source=a.chart_source,
            )
            db.add(entry)

    db.commit()
    db.refresh(reading)
    return reading
