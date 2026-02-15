from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Song
from app.schemas import DecadeAggregate, YearAggregate
from app.services.compass_calc import compute_degree, position_weight
from app.services.charge_calc import degree_to_charge
from app.constants import CHART_SOURCES

router = APIRouter(prefix="/api/drift", tags=["drift"])

DECADE_ORDER = ["1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"]

# Historical data uses a 3-color system (violet, blue, red).
# "Blue" in the old system meant "not bad" — a catch-all for everything
# that wasn't violet or red. In the 5-color system, many of those
# songs would be green or yellow. We adjust the blue mapping upward
# to reflect this coarseness honestly.
HISTORICAL_DEGREES = {
    "violet": 0.0,
    "blue": 65.0,  # old "not bad" ≈ upper Elevated, nearly Decent
    "green": 90.0,
    "yellow": 135.0,
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


@router.get("", response_model=list[DecadeAggregate])
def get_drift(db: Session = Depends(get_db)):
    """Decade-by-decade aggregate compass data for historical visualization."""
    results = []

    for decade in DECADE_ORDER:
        all_songs = db.query(Song).filter(Song.decade == decade).all()
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
def get_year_songs(year: int, db: Session = Depends(get_db)):
    """Top 10 songs for a specific year, ordered by chart position."""
    songs = (
        db.query(Song)
        .filter(Song.year == year)
        .order_by(Song.chart_position)
        .limit(10)
        .all()
    )
    return [
        {
            "title": s.title,
            "artist": s.artist,
            "rubric_color": s.rubric_color,
            "charge_value": s.charge_value,
            "contaminated": s.contaminated,
            "chart_position": s.chart_position,
        }
        for s in songs
    ]


@router.get("/years", response_model=list[YearAggregate])
def get_drift_years(db: Session = Depends(get_db)):
    """Year-by-year aggregate compass data for Time Machine."""
    years = (
        db.query(Song.year)
        .distinct()
        .order_by(Song.year)
        .all()
    )

    results = []
    for (year,) in years:
        all_songs = db.query(Song).filter(Song.year == year).all()
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

    return results
