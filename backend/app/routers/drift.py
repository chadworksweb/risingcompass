from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from app.database import get_db
from app.models import CompassSong, DailyReading, ReadingSong
from app.schemas import DecadeAggregate, YearAggregate
from app.services.compass_calc import (
    compute_degree, position_weight, charge_to_degree, compute_live_year_degree,
)
from app.services.charge_calc import degree_to_charge
from app.constants import CHART_SOURCES

router = APIRouter(prefix="/api/drift", tags=["drift"])

DECADE_ORDER = ["1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"]

# Cutoff: years <= this use CompassSong table, years > this use ReadingSong/DailyReading
LIVE_YEAR_CUTOFF = 2025

# Historical data uses a 3-color system (violet, blue, red).
# "Blue" in the old system meant "not bad" — a catch-all for everything
# that wasn't violet or red. In the 5-color system, many of those
# songs would be green or orange. We adjust the blue mapping upward
# to reflect this coarseness honestly.
HISTORICAL_DEGREES = {
    "violet": 0.0,
    "blue": 65.0,  # old "not bad" ≈ upper Elevated, nearly Decent
    "green": 90.0,
    "orange": 135.0,
    "red": 180.0,
}


def compute_historical_degree(songs: list[dict]) -> float:
    """Weighted average using historical color mapping."""
    if not songs:
        return 90.0
    total_weight = 0
    weighted_sum = 0.0
    for song in songs:
        color = song.get("rubric_color", "green")
        pos = song.get("chart_position", 5)
        deg = HISTORICAL_DEGREES.get(color, 90.0)
        w = position_weight(pos)
        weighted_sum += deg * w
        total_weight += w
    if total_weight == 0:
        return 90.0
    return round(weighted_sum / total_weight, 1)


def _aggregate_live_year(db: Session, year: int) -> list[dict]:
    """
    Deduplicate ReadingSongs for a calendar year, computing days_on_chart
    and effective_weight for each unique song.

    Returns list of dicts with keys:
      title, artist, rubric_color, charge_value, contaminated, contamination_note,
      charge_summary, days_on_chart, effective_weight, position (most recent)
    """
    from datetime import date
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    # Get all reading songs for this year with their reading dates, joining CompassSong
    rows = (
        db.query(ReadingSong, DailyReading.date, CompassSong)
        .join(DailyReading, ReadingSong.reading_id == DailyReading.id)
        .outerjoin(CompassSong, ReadingSong.compass_song_id == CompassSong.id)
        .filter(DailyReading.date >= start, DailyReading.date <= end)
        .order_by(DailyReading.date)
        .all()
    )

    if not rows:
        return []

    # Precompute song count per reading to avoid N+1 queries
    reading_ids = set(rs.reading_id for rs, _, _ in rows)
    reading_sizes = {}
    if reading_ids:
        size_rows = (
            db.query(ReadingSong.reading_id, func.count(ReadingSong.id))
            .filter(ReadingSong.reading_id.in_(reading_ids))
            .group_by(ReadingSong.reading_id)
            .all()
        )
        reading_sizes = {rid: cnt for rid, cnt in size_rows}

    # Group by (title_lower, artist_lower)
    groups: dict[tuple[str, str], dict] = {}
    for rs, reading_date, cs in rows:
        key = (rs.title.strip().lower(), rs.artist.strip().lower())

        total_in_reading = reading_sizes.get(rs.reading_id, 20)
        pw = position_weight(rs.position, total_in_reading)

        if key not in groups:
            groups[key] = {
                "title": rs.title,
                "artist": rs.artist,
                "rubric_color": cs.rubric_color if cs else None,
                "charge_value": cs.charge_value if cs else None,
                "contaminated": cs.contaminated if cs else False,
                "contamination_note": cs.contamination_note if cs else None,
                "charge_summary": cs.charge_summary if cs else None,
                "days_on_chart": 1,
                "effective_weight": pw,
                "position": rs.position,
                "_latest_date": reading_date,
            }
        else:
            g = groups[key]
            g["days_on_chart"] += 1
            g["effective_weight"] += pw
            # Update to most recent appearance
            if reading_date >= g["_latest_date"]:
                g["_latest_date"] = reading_date
                g["rubric_color"] = cs.rubric_color if cs else g["rubric_color"]
                g["charge_value"] = cs.charge_value if cs else g["charge_value"]
                g["contaminated"] = cs.contaminated if cs else g["contaminated"]
                g["contamination_note"] = cs.contamination_note if cs else g["contamination_note"]
                g["charge_summary"] = cs.charge_summary if cs else g["charge_summary"]
                g["position"] = rs.position

    # Clean up internal field and sort by days_on_chart desc
    result = []
    for g in groups.values():
        del g["_latest_date"]
        result.append(g)

    result.sort(key=lambda x: x["effective_weight"], reverse=True)
    return result


@router.get("", response_model=list[DecadeAggregate])
def get_drift(db: Session = Depends(get_db)):
    """Decade-by-decade aggregate compass data for historical visualization."""
    results = []

    for decade in DECADE_ORDER:
        all_songs = db.query(CompassSong).filter(CompassSong.decade == decade).all()
        if not all_songs:
            continue

        chart_songs = [s for s in all_songs if s.chart_source in CHART_SOURCES]
        if not chart_songs:
            continue

        song_dicts = [{"rubric_color": s.rubric_color, "chart_position": s.chart_position} for s in chart_songs]
        deg = compute_historical_degree(song_dicts)
        contam = sum(1 for s in chart_songs if s.contaminated)

        # Count songs per color (charting songs only)
        color_counts = {}
        for s in chart_songs:
            color_counts[s.rubric_color] = color_counts.get(s.rubric_color, 0) + 1

        results.append(DecadeAggregate(
            decade=decade,
            compass_degree=deg,
            charge_level=degree_to_charge(deg),
            chart_song_count=len(chart_songs),
            total_song_count=len(all_songs),
            contamination_count=contam,
            color_counts=color_counts,
        ))

    return results


@router.get("/years/{year}/songs")
def get_year_songs(
    year: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Songs for a specific year with pagination.

    Historical years (<=2025): CompassSong table, ordered by chart_position.
    Live years (>=2026): ReadingSong table, deduplicated, ordered by days_on_chart.
    """
    if year > LIVE_YEAR_CUTOFF:
        # Live year — deduplicate from ReadingSong
        all_songs = _aggregate_live_year(db, year)
        total = len(all_songs)
        page = all_songs[offset:offset + limit]

        return {
            "songs": [
                {
                    "title": s["title"],
                    "artist": s["artist"],
                    "rubric_color": s["rubric_color"],
                    "charge_value": s["charge_value"],
                    "contaminated": s["contaminated"],
                    "contamination_note": s["contamination_note"],
                    "charge_summary": s["charge_summary"],
                    "position": s["position"],
                    "days_on_chart": s["days_on_chart"],
                }
                for s in page
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
    else:
        # Historical year — CompassSong table
        total = db.query(func.count(CompassSong.id)).filter(CompassSong.year == year).scalar()

        songs = (
            db.query(CompassSong)
            .filter(CompassSong.year == year)
            .order_by(CompassSong.chart_position)
            .offset(offset)
            .limit(limit)
            .all()
        )

        return {
            "songs": [
                {
                    "title": s.title,
                    "artist": s.artist,
                    "rubric_color": s.rubric_color,
                    "charge_value": s.charge_value,
                    "contaminated": s.contaminated,
                    "contamination_note": s.contamination_note,
                    "charge_summary": s.charge_summary,
                    "message_analysis": s.message_analysis,
                    "expression_analysis": s.expression_analysis,
                    "intention_analysis": s.intention_analysis,
                    "position": s.chart_position,
                    "days_on_chart": 1,
                }
                for s in songs
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }


@router.get("/years", response_model=list[YearAggregate])
def get_drift_years(db: Session = Depends(get_db)):
    """Year-by-year aggregate compass data for Time Machine.

    Includes both historical years (CompassSong table) and live years (ReadingSong).
    """
    results = []

    # Historical years from CompassSong table (up to cutoff)
    years = (
        db.query(CompassSong.year)
        .filter(CompassSong.year <= LIVE_YEAR_CUTOFF)
        .distinct()
        .order_by(CompassSong.year)
        .all()
    )

    for (year,) in years:
        all_songs = db.query(CompassSong).filter(CompassSong.year == year).all()
        if not all_songs:
            continue

        chart_songs = [s for s in all_songs if s.chart_source in CHART_SOURCES]
        if not chart_songs:
            continue

        song_dicts = [{"rubric_color": s.rubric_color, "chart_position": s.chart_position} for s in chart_songs]
        deg = compute_historical_degree(song_dicts)

        results.append(YearAggregate(
            year=year,
            compass_degree=deg,
            charge_level=degree_to_charge(deg),
            chart_song_count=len(chart_songs),
            total_song_count=len(all_songs),
        ))

    # Live years from DailyReading/ReadingSong
    live_years = (
        db.query(extract("year", DailyReading.date).label("yr"))
        .filter(extract("year", DailyReading.date) > LIVE_YEAR_CUTOFF)
        .distinct()
        .order_by("yr")
        .all()
    )

    for (yr,) in live_years:
        year = int(yr)
        deduped = _aggregate_live_year(db, year)
        if not deduped:
            continue

        deg = compute_live_year_degree(deduped)
        contam_count = sum(1 for s in deduped if s.get("contaminated"))

        results.append(YearAggregate(
            year=year,
            compass_degree=deg,
            charge_level=degree_to_charge(deg),
            chart_song_count=len(deduped),
            total_song_count=len(deduped),
        ))

    # Sort all results by year
    results.sort(key=lambda r: r.year)
    return results


@router.get("/years/{year}/dates")
def get_year_dates(year: int, db: Session = Depends(get_db)):
    """Available reading dates for a given year.

    Live years (>2025): returns sorted list of date strings from DailyReading.
    Historical years (<=2025): returns empty list (no daily data).
    """
    if year <= LIVE_YEAR_CUTOFF:
        return {"dates": []}

    from datetime import date
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    rows = (
        db.query(DailyReading.date)
        .filter(DailyReading.date >= start, DailyReading.date <= end)
        .order_by(DailyReading.date)
        .all()
    )

    return {"dates": [r[0].isoformat() for r in rows]}
