from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from pathlib import Path
import os
import shutil
import tempfile

from app.auth import (
    optional_admin_session,
    require_admin_session,
    verify_backup_key,
)
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


# Backwards-compatible alias: every admin router imports verify_admin_key
# from here. It now points at the session-cookie dependency, so the whole
# admin surface migrates without touching every router file.
verify_admin_key = require_admin_session


def _find_compass_song(title: str, artist: str, db: Session) -> CompassSong | None:
    """Case-insensitive lookup of the most recent CompassSong by title + artist."""
    return (
        db.query(CompassSong)
        .filter(CompassSong.title.ilike(title), CompassSong.artist.ilike(artist))
        .order_by(CompassSong.id.desc())
        .first()
    )


@router.get("/dashboard")
def admin_dashboard_root(admin=Depends(optional_admin_session)):
    """Redirect to the default landing section, or 404 when unauthed.

    Unauthed callers get a flat 404 — no redirect, no Location header
    pointing at the obscured login URL — so port scanners hitting
    /api/admin/* learn nothing about admin existence.
    """
    if admin is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url="/api/admin/dashboard/db", status_code=307)


_ADMIN_SECTIONS = {
    "db": "admin/db.html",
    "daily-songs": "admin/daily_songs.html",
    "weekly-albums": "admin/weekly_albums.html",
    "misread": "admin/misread.html",
    "artist-verified": "admin/artist_verified.html",
    "submissions": "admin/submissions.html",
    "lc-activity": "admin/lc_activity.html",
    "api-monitor": "admin/api_monitor.html",
    "claude-usage": "admin/claude_usage.html",
    "v1-test": "admin/v1_test.html",
    "lc-status": "admin/lc_status.html",
}


@router.get("/dashboard/{section}", response_class=HTMLResponse)
def admin_dashboard_section(
    section: str,
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve a specific admin section. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    template_name = _ADMIN_SECTIONS.get(section)
    if not template_name:
        raise HTTPException(status_code=404, detail="Unknown admin section")
    return templates.TemplateResponse(request=request, name=template_name)


@router.get("/recalibrate", response_class=HTMLResponse)
def recalibrate_dashboard(
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve the recalibrate suite admin UI. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request=request, name="recalibrate.html")


@router.get("/ether-audits", response_class=HTMLResponse)
def ether_audits_dashboard(
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve the Ether Audits triage UI. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    from app.services.ether_taxonomy import VALID_SLUGS
    return templates.TemplateResponse(
        request=request,
        name="ether_audits.html",
        context={"ether_slugs": sorted(VALID_SLUGS)},
    )


@router.get("/backfill", response_class=HTMLResponse)
def backfill_console(
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve the Backfill Console UI. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request=request, name="backfill_console.html")


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

@router.post("/backup", dependencies=[Depends(verify_backup_key)])
def trigger_backup():
    """Service endpoint — called by cron at 04:45 UTC with X-Backup-Key.

    Distinct from the human admin session so a leaked admin password
    never grants automated backup access (and vice versa). RC_BACKUP_KEY
    falls back to RC_ADMIN_KEY during the transition window.
    """
    from app.services.backup import run_backup

    result = run_backup()
    if not result:
        raise HTTPException(status_code=500, detail="Backup failed")
    return {
        "status": "ok",
        "key": result.key,
        "bytes": result.bytes,
        "verified": result.verified,
        "pruned": result.pruned,
    }


@router.get("/backup/list", dependencies=[Depends(verify_admin_key)])
def list_backups(limit: int = 30):
    """List recent backup objects in DO Spaces under the RC prefix."""
    from app.services.backup import list_backups as _list

    return {"backups": _list(limit=limit)}


@router.get("/db-export", dependencies=[Depends(verify_admin_key)])
def export_database(background_tasks: BackgroundTasks):
    """Download a fresh snapshot of the Turso database as a SQLite file.

    Opens a throwaway libsql embedded replica, syncs from the primary, and
    streams the resulting .db file back. Not a substitute for the daily
    backup (no verification, no S3 upload) — this is the admin ad-hoc
    export for a one-off local working copy.

    Authed via the admin session cookie (set by /api/rc-admin-{token}/login).
    Use the dashboard "Export DB" button or run from the browser while signed in.
    """
    from app.services.backup import _dump_turso_to_file

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        _dump_turso_to_file(tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Turso dump failed: {exc}") from exc

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
