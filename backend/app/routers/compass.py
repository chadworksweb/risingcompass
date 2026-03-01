from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, extract
from datetime import date, timedelta

from app.database import get_db
from app.models import CompassSong, DailyReading, ReadingSong, WeeklyAlbumReading
from app.schemas import CompassCurrent, DailyChartPoint, DailyReadingOut, DailyReadingSummary, PaginatedReadings, ReadingSongOut
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated

router = APIRouter(prefix="/api/compass", tags=["compass"])


def _song_out(rs: ReadingSong) -> ReadingSongOut:
    """Build ReadingSongOut by joining ReadingSong with its linked CompassSong."""
    cs = rs.compass_song
    return ReadingSongOut(
        id=rs.id,
        title=rs.title,
        artist=rs.artist,
        position=rs.position,
        rubric_color=cs.rubric_color if cs else None,
        charge_value=cs.charge_value if cs else None,
        contaminated=cs.contaminated if cs else False,
        contamination_note=cs.contamination_note if cs else None,
        charge_summary=cs.charge_summary if cs else None,
        chart_source=rs.chart_source,
    )


def _reading_with_songs(reading: DailyReading) -> DailyReadingOut:
    """Build DailyReadingOut with songs resolved through compass_song FK."""
    return DailyReadingOut(
        id=reading.id,
        date=reading.date,
        compass_degree=reading.compass_degree,
        charge_level=reading.charge_level,
        contamination_count=reading.contamination_count,
        editorial_summary=reading.editorial_summary,
        songs=[_song_out(rs) for rs in reading.songs],
    )


def _historical_aggregate(db: Session) -> tuple[float, str]:
    """Compute aggregate degree from charting songs only.

    Uses HISTORICAL_DEGREES for uncalibrated songs (old 3-tier "blue" = 65,
    not the 5-tier center of 45) so the aggregate isn't artificially positive.
    """
    from app.constants import CHART_SOURCES
    HISTORICAL_DEGREES = {
        "violet": 0.0,
        "blue": 65.0,  # old 3-tier "not bad" ≈ upper Elevated, nearly Decent
        "green": 90.0,
        "orange": 135.0,
        "red": 180.0,
    }
    songs = db.query(CompassSong).filter(CompassSong.chart_source.in_(CHART_SOURCES)).all()
    if not songs:
        return 90.0, "green"
    song_dicts = [
        {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "chart_position": s.chart_position}
        for s in songs
    ]
    deg = compute_degree(song_dicts, color_degrees=HISTORICAL_DEGREES)
    return deg, degree_to_charge(deg)


@router.get("/current", response_model=CompassCurrent)
def get_current(db: Session = Depends(get_db)):
    """Today's reading (or most recent), plus historical aggregate."""
    hist_deg, hist_charge = _historical_aggregate(db)

    # Most recent daily song reading (eager-load songs → compass_song)
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.compass_song))
        .order_by(DailyReading.date.desc())
        .first()
    )

    # Most recent weekly album reading
    album_reading = db.query(WeeklyAlbumReading).order_by(WeeklyAlbumReading.week_date.desc()).first()

    # Build album fields
    album_kwargs = {}
    if album_reading:
        album_kwargs = dict(
            has_album_reading=True,
            album_week_date=album_reading.week_date,
            album_compass_degree=album_reading.compass_degree,
            album_charge_level=album_reading.charge_level,
            album_contamination_count=album_reading.contamination_count,
            album_editorial_summary=album_reading.editorial_summary,
            album_entries=album_reading.albums,
        )

    if not reading:
        return CompassCurrent(
            has_reading=False,
            compass_degree=hist_deg,
            charge_level=hist_charge,
            contamination_count=0,
            historical_degree=hist_deg,
            historical_charge=hist_charge,
            **album_kwargs,
        )

    return CompassCurrent(
        has_reading=True,
        date=reading.date,
        compass_degree=reading.compass_degree,
        charge_level=reading.charge_level,
        contamination_count=reading.contamination_count,
        editorial_summary=reading.editorial_summary,
        songs=[_song_out(rs) for rs in reading.songs],
        historical_degree=hist_deg,
        historical_charge=hist_charge,
        **album_kwargs,
    )


@router.get("/history", response_model=PaginatedReadings)
def get_history(page: int = 1, per_page: int = 10, db: Session = Depends(get_db)):
    """Paginated past readings."""
    total = db.query(func.count(DailyReading.id)).scalar()
    pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    items = (
        db.query(DailyReading)
        .order_by(DailyReading.date.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    # PaginatedReadings uses DailyReadingSummary (no songs), so from_attributes is fine
    return PaginatedReadings(items=items, total=total, page=page, pages=pages)


@router.get("/daily-chart", response_model=list[DailyChartPoint])
def get_daily_chart(days: int = 365, db: Session = Depends(get_db)):
    """Lightweight chart data: trailing N days of daily readings."""
    cutoff = date.today() - timedelta(days=days)

    readings = (
        db.query(DailyReading.date, DailyReading.compass_degree, DailyReading.charge_level)
        .filter(DailyReading.date >= cutoff)
        .order_by(DailyReading.date.asc())
        .all()
    )
    return [DailyChartPoint(date=r.date, compass_degree=r.compass_degree, charge_level=r.charge_level) for r in readings]


@router.get("/reading/{reading_date}", response_model=DailyReadingOut)
def get_reading(reading_date: date, db: Session = Depends(get_db)):
    """Specific date reading with songs."""
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.compass_song))
        .filter(DailyReading.date == reading_date)
        .first()
    )
    if not reading:
        raise HTTPException(status_code=404, detail="No reading for this date")
    return _reading_with_songs(reading)
