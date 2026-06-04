"""Unified-model read helpers (song-entity renovation, Phase 3).

The single place the read paths reach the unified `songs` + `chart_appearances`
model. "Charting" is a derived role: a song with >=1 appearance on an
AGGREGATING chart (the CHART_SOURCES equivalent -- excludes the Viral 50 side
snapshot, matching legacy behavior). The historical year/decade aggregates read
appearances (one per former compass_songs charting row); the live-year path
stays on DailyReading/ReadingSong but resolves the song via ReadingSong.song_id.

Landed dark first (imported by nothing live) so it can be validated against the
migrated data before the read-path cutover. See
RISING-COMPASS-SONG-ENTITY-RENOVATION.md.
"""

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.models import Song, Chart, ChartAppearance
from app.constants import AGGREGATING_CHART_SLUGS
from app.services.song_identity import compute_canonical_key


def decade_of(year: int) -> str:
    return f"{(year // 10) * 10}s"


def _aggregating_chart_ids(db: Session):
    """Subquery (selectable) of chart ids that count toward the compass charge."""
    return db.query(Chart.id).filter(Chart.slug.in_(AGGREGATING_CHART_SLUGS))


# --- common serialization ------------------------------------------------- #
def serialize_song(song: Song, *, position=None, days_on_chart=1, slug=None,
                   artist_slug=None) -> dict:
    """Public-facing song dict. Matches the shape the legacy routers returned
    (title/artist/rubric_color/charge_value/contaminated/.../position)."""
    return {
        "id": song.id,
        "title": song.title,
        "artist": song.artist,
        "rubric_color": song.rubric_color,
        "charge_value": song.charge_value,
        "contaminated": bool(song.contaminated),
        "contamination_note": song.contamination_note,
        "charge_summary": song.charge_summary,
        "instrumental": bool(song.instrumental),
        "position": position,
        "days_on_chart": days_on_chart,
        "song_slug": slug,
        "artist_slug": artist_slug,
    }


# --- charting role -------------------------------------------------------- #
def is_charting(db: Session, song_id: int) -> bool:
    """True if the song has >=1 appearance on an aggregating chart."""
    return db.query(
        db.query(ChartAppearance.id)
        .filter(ChartAppearance.song_id == song_id)
        .filter(ChartAppearance.chart_id.in_(_aggregating_chart_ids(db)))
        .exists()
    ).scalar()


# --- year + decade aggregates (historical, via appearances) --------------- #
def available_chart_years(db: Session, max_year: int | None = None) -> list[int]:
    """Distinct years with >=1 aggregating-chart appearance, ascending."""
    q = (
        db.query(distinct(ChartAppearance.year))
        .filter(ChartAppearance.year.isnot(None))
        .filter(ChartAppearance.chart_id.in_(_aggregating_chart_ids(db)))
    )
    if max_year is not None:
        q = q.filter(ChartAppearance.year <= max_year)
    return [r[0] for r in q.order_by(ChartAppearance.year).all()]


def year_song_rows(db: Session, year: int):
    """Distinct songs with an aggregating appearance in `year`, with their best
    (lowest) position. Returns list[(Song, position)] ordered by position
    (NULLs last). One entry per song (atomic) -- the public year list."""
    pos = func.min(ChartAppearance.position).label("pos")
    sub = (
        db.query(ChartAppearance.song_id.label("sid"), pos)
        .filter(ChartAppearance.year == year)
        .filter(ChartAppearance.chart_id.in_(_aggregating_chart_ids(db)))
        .group_by(ChartAppearance.song_id)
        .subquery()
    )
    return (
        db.query(Song, sub.c.pos)
        .join(sub, Song.id == sub.c.sid)
        .order_by(sub.c.pos.asc().nullslast())
        .all()
    )


def decade_appearance_rows(db: Session, decade: str):
    """One row per aggregating appearance whose year falls in `decade`:
    (Song, position). Matches the legacy per-(song,year)-row grain."""
    start = int(decade[:4])
    end = start + 9
    return (
        db.query(Song, ChartAppearance.position)
        .join(ChartAppearance, ChartAppearance.song_id == Song.id)
        .filter(ChartAppearance.year >= start, ChartAppearance.year <= end)
        .filter(ChartAppearance.chart_id.in_(_aggregating_chart_ids(db)))
        .all()
    )


def year_appearance_rows(db: Session, year: int):
    """One row per aggregating appearance in `year`: (Song, position).
    Per-appearance grain for the year CHARGE aggregate."""
    return (
        db.query(Song, ChartAppearance.position)
        .join(ChartAppearance, ChartAppearance.song_id == Song.id)
        .filter(ChartAppearance.year == year)
        .filter(ChartAppearance.chart_id.in_(_aggregating_chart_ids(db)))
        .all()
    )


def all_aggregating_appearance_rows(db: Session):
    """Every (Song, position) on an aggregating chart -- the HCI corpus. One row
    per appearance (the legacy per-charting-compass-row grain)."""
    return (
        db.query(Song, ChartAppearance.position)
        .join(ChartAppearance, ChartAppearance.song_id == Song.id)
        .filter(ChartAppearance.chart_id.in_(_aggregating_chart_ids(db)))
        .all()
    )


def get_song(db: Session, song_id: int) -> Song | None:
    return db.query(Song).get(song_id)


def find_song_by_title_artist(db: Session, title: str, artist: str) -> Song | None:
    """Resolve a (title, artist) pair to its unified Song via canonical_key.

    The unified replacement for the legacy case-insensitive title+artist
    CompassSong lookup. Matches on the canonical identity (primary artist,
    normalized) so credit-string variants ("X & Y" / "X feat. Y") still
    resolve to the one atomic song."""
    if not title:
        return None
    key = compute_canonical_key(title, artist)
    return db.query(Song).filter(Song.canonical_key == key).first()
