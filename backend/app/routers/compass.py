from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, extract
from datetime import date, timedelta

from app.database import get_db
from app.models import Song, DailyReading, ReadingSong, WeeklyAlbumReading
from app.schemas import CompassCurrent, DailyChartPoint, DailyReadingOut, DailyReadingSummary, PaginatedReadings, ReadingSongOut
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated
from app.services.artist_utils import generate_song_slug, normalize_artist_name, resolve_artist_slugs
from app.constants import HISTORICAL_DEGREES
from app.services import song_store

router = APIRouter(prefix="/api/compass", tags=["compass"])


def _song_out(rs: ReadingSong, slug_map: dict[str, str] | None = None) -> ReadingSongOut:
    """Build ReadingSongOut by joining ReadingSong with its linked Song."""
    cs = rs.song
    primary = normalize_artist_name(rs.artist or "").lower()
    artist_slug = (slug_map or {}).get(primary)
    return ReadingSongOut(
        id=rs.id,
        title=rs.title,
        artist=rs.artist,
        position=rs.position,
        rubric_color=cs.rubric_color if cs else None,
        charge_value=cs.charge_value if cs else None,
        contaminated=bool(cs.contaminated) if cs else False,
        contamination_note=cs.contamination_note if cs else None,
        charge_summary=cs.charge_summary if cs else None,
        chart_source=rs.chart_source,
        instrumental=bool(cs.instrumental) if cs else False,
        song_slug=generate_song_slug(rs.title, rs.artist),
        artist_slug=artist_slug,
    )


def _reading_with_songs(reading: DailyReading, db: Session) -> DailyReadingOut:
    """Build DailyReadingOut with songs resolved through compass_song FK."""
    slug_map = resolve_artist_slugs([rs.artist for rs in reading.songs], db)
    return DailyReadingOut(
        id=reading.id,
        date=reading.date,
        compass_degree=reading.compass_degree,
        charge_level=reading.charge_level,
        contamination_count=reading.contamination_count,
        editorial_summary=reading.editorial_summary,
        songs=[_song_out(rs, slug_map) for rs in reading.songs],
    )


def _historical_aggregate(db: Session) -> tuple[float, str]:
    """Compute aggregate degree from charting songs only.

    Uses HISTORICAL_DEGREES for legacy songs (old 3-tier "blue" = 65,
    not the 5-tier center of 45) so the aggregate isn't artificially positive.
    """
    rows = song_store.all_aggregating_appearance_rows(db)  # list[(Song, position)]
    if not rows:
        return 90.0, "green"
    song_dicts = [
        {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "chart_position": pos}
        for (s, pos) in rows
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
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.song))
        .order_by(DailyReading.date.desc())
        .first()
    )

    # Most recent weekly album reading
    album_reading = db.query(WeeklyAlbumReading).order_by(WeeklyAlbumReading.week_date.desc()).first()

    # Build album fields
    album_kwargs = {}
    if album_reading:
        album_slug_map = resolve_artist_slugs([a.artist for a in album_reading.albums], db)
        album_entries = []
        from app.schemas import WeeklyAlbumEntryOut
        for a in album_reading.albums:
            primary = normalize_artist_name(a.artist or "").lower()
            album_entries.append(WeeklyAlbumEntryOut(
                id=a.id,
                title=a.title,
                artist=a.artist,
                position=a.position,
                rubric_color=a.rubric_color,
                contaminated=a.contaminated,
                contamination_note=a.contamination_note,
                charge_summary=a.charge_summary,
                chart_source=a.chart_source,
                artist_slug=album_slug_map.get(primary),
            ))
        album_kwargs = dict(
            has_album_reading=True,
            album_week_date=album_reading.week_date,
            album_compass_degree=album_reading.compass_degree,
            album_charge_level=album_reading.charge_level,
            album_contamination_count=album_reading.contamination_count,
            album_editorial_summary=album_reading.editorial_summary,
            album_entries=album_entries,
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

    slug_map = resolve_artist_slugs([rs.artist for rs in reading.songs], db)
    return CompassCurrent(
        has_reading=True,
        date=reading.date,
        compass_degree=reading.compass_degree,
        charge_level=reading.charge_level,
        contamination_count=reading.contamination_count,
        editorial_summary=reading.editorial_summary,
        songs=[_song_out(rs, slug_map) for rs in reading.songs],
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
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.song))
        .filter(DailyReading.date == reading_date)
        .first()
    )
    if not reading:
        raise HTTPException(status_code=404, detail="No reading for this date")
    return _reading_with_songs(reading, db)
