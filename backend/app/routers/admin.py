from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from pathlib import Path
import os
import shutil
import tempfile

from app.database import get_db
from app.models import CompassSong, DailyReading, ReadingSong, WeeklyAlbumReading, WeeklyAlbumEntry
from app.schemas import (
    ReadingCreate, ReadingUpdate, DailyReadingOut,
    WeeklyAlbumReadingCreate, WeeklyAlbumReadingUpdate, WeeklyAlbumReadingOut,
)
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated
from app.config import settings
from app.routers.compass import _reading_with_songs

router = APIRouter(prefix="/api/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != settings.rc_admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


def _find_compass_song(title: str, artist: str, db: Session) -> CompassSong | None:
    """Case-insensitive lookup of the most recent CompassSong by title + artist."""
    return (
        db.query(CompassSong)
        .filter(CompassSong.title.ilike(title), CompassSong.artist.ilike(artist))
        .order_by(CompassSong.id.desc())
        .first()
    )


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

    label = f"reading_{data.date.isoformat()}"
    reading = DailyReading(
        date=data.date,
        label=label,
        compass_degree=degree,
        charge_level=charge,
        contamination_count=contam,
        editorial_summary=data.editorial_summary,
    )
    db.add(reading)
    db.flush()

    for s in data.songs:
        cs = _find_compass_song(s.title, s.artist, db)
        rs = ReadingSong(
            reading_id=reading.id,
            compass_song_id=cs.id if cs else None,
            title=s.title,
            artist=s.artist,
            position=s.position,
            chart_source=s.chart_source,
        )
        db.add(rs)

    db.commit()

    # Re-query with eager loading for proper serialization
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.compass_song))
        .filter(DailyReading.id == reading.id)
        .first()
    )
    return _reading_with_songs(reading)


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
            cs = _find_compass_song(s.title, s.artist, db)
            rs = ReadingSong(
                reading_id=reading.id,
                compass_song_id=cs.id if cs else None,
                title=s.title,
                artist=s.artist,
                position=s.position,
                chart_source=s.chart_source,
            )
            db.add(rs)

    db.commit()

    # Re-query with eager loading for proper serialization
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.compass_song))
        .filter(DailyReading.date == reading_date)
        .first()
    )
    return _reading_with_songs(reading)


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


# --- Database Backup & Export ---

@router.post("/backup", dependencies=[Depends(verify_admin_key)])
def trigger_backup():
    """Manually trigger a database backup."""
    from app.services.backup import run_backup

    path = run_backup()
    if not path:
        raise HTTPException(status_code=500, detail="Backup failed")
    return {"status": "ok", "file": path.name}


@router.get("/db-export", dependencies=[Depends(verify_admin_key)])
def export_database(background_tasks: BackgroundTasks):
    """Download a snapshot of the current database file.

    Copies the DB to a temp file first to avoid locking issues.
    Temp file is cleaned up automatically after response is sent.
    Use: curl -H "X-Admin-Key: YOUR_KEY" https://api.risingcompass.net/api/admin/db-export -o rising_compass.db
    """
    from app.config import settings
    if settings.is_turso:
        raise HTTPException(status_code=410, detail="Database is on Turso — use Turso dashboard for exports")

    db_path = Path("data/rising_compass.db")
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found")

    # Copy to temp file to avoid read locks on the live DB
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    shutil.copy2(db_path, tmp.name)
    tmp.close()

    background_tasks.add_task(os.unlink, tmp.name)

    return FileResponse(
        tmp.name,
        media_type="application/octet-stream",
        filename="rising_compass.db",
    )


@router.post("/deploy", dependencies=[Depends(verify_admin_key)])
def deploy_frontend():
    """Pull latest code from git. Frontend is volume-mounted so git pull is enough."""
    import subprocess

    result = subprocess.run(
        ["git", "pull", "origin", "master"],
        cwd="/root/rising-compass",
        capture_output=True, text=True, timeout=30,
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
