"""Public Ether Art Chart API.

Three read-only endpoints that surface what the ether tagger has named:

  - GET /today          — most recent daily reading, deadpan + topics
  - GET /year/{year}    — position-weighted top 20 + topic distribution
  - GET /years          — list of years with any classified data

API-key gated through the same dependency the rest of the public RC
endpoints use; mounted from main.py with `_api_key_dep`. No auth
inside this module.

Schema reference: build pack §5 (response shapes locked).
SQL reference: build pack §6 (annual rollup pseudocode).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import CompassSong, DailyReading, ReadingSong
from app.services.artist_utils import generate_song_slug, normalize_artist_name, resolve_artist_slugs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ether-art-chart", tags=["ether-art-chart"])


# --- Schemas ---

class EtherTodayItem(BaseModel):
    position: int
    title: str
    artist: str
    deadpan_line: Optional[str]
    topics: list[str]
    dominant_topic: Optional[str]
    compass_song_id: Optional[int]
    song_slug: str
    rubric_color: Optional[str]
    charge_value: Optional[int]
    artist_slug: Optional[str] = None


class EtherTodayOut(BaseModel):
    date: Optional[date]
    items: list[EtherTodayItem]


class EtherYearDeadpanItem(BaseModel):
    position: int
    position_letter: Optional[str] = None  # 'A' / 'B' for double-A-side splits
    title: str
    artist: str
    deadpan_line: Optional[str]
    dominant_topic: Optional[str]
    song_slug: str
    artist_slug: Optional[str] = None
    rubric_color: Optional[str] = None
    charge_value: Optional[int] = None


class TopicSong(BaseModel):
    title: str
    artist: str
    deadpan_line: Optional[str]
    song_slug: str
    position: Optional[int] = None  # rank within this topic, 1-based
    artist_slug: Optional[str] = None


class TopicDistributionItem(BaseModel):
    topic: str
    count: int
    percent: float
    top_songs: list[TopicSong] = []


class EtherYearOut(BaseModel):
    year: int
    top_20_deadpan: list[EtherYearDeadpanItem]
    topic_distribution: list[TopicDistributionItem]
    audit_flagged_count: int
    total_compass_songs_in_period: int
    period_start: date
    period_end: date


class EtherYearsOut(BaseModel):
    years: list[int]


# --- Helpers ---

def _decode_topics(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(s) for s in parsed if isinstance(s, str)]


# --- Endpoints ---

def _render_reading_as_ether(reading: Optional[DailyReading], db: Session) -> EtherTodayOut:
    if not reading:
        return EtherTodayOut(date=None, items=[])

    slug_map = resolve_artist_slugs([rs.artist for rs in reading.songs], db)
    items: list[EtherTodayItem] = []
    for rs in sorted(reading.songs, key=lambda r: r.position):
        cs: Optional[CompassSong] = rs.compass_song
        topics = _decode_topics(cs.topics if cs else None)
        primary = normalize_artist_name(rs.artist or "").lower()
        items.append(EtherTodayItem(
            position=rs.position,
            title=rs.title,
            artist=rs.artist,
            deadpan_line=cs.deadpan_line if cs else None,
            topics=topics,
            dominant_topic=topics[0] if topics else None,
            compass_song_id=cs.id if cs else None,
            song_slug=generate_song_slug(rs.title, rs.artist),
            rubric_color=cs.rubric_color if cs else None,
            charge_value=cs.charge_value if cs else None,
            artist_slug=slug_map.get(primary),
        ))
    return EtherTodayOut(date=reading.date, items=items)


@router.get("/today", response_model=EtherTodayOut)
def get_today(db: Session = Depends(get_db)):
    """Most recent daily reading rendered through the ether-art lens.

    Items mirror the Daily Top 20 ordering. `topics` may be empty when a
    song is audit-flagged; `deadpan_line` may be NULL on rows that
    predate the tagger (forward-only — the deferred backfill fills them).
    """
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.compass_song))
        .order_by(DailyReading.date.desc())
        .first()
    )
    return _render_reading_as_ether(reading, db)


@router.get("/date/{date_str}", response_model=EtherTodayOut)
def get_by_date(date_str: str, db: Session = Depends(get_db)):
    """Specific daily reading rendered through the ether-art lens.

    Same shape as /today, just pinned to the requested date. Used by the
    homepage card so it follows the calendar picker on the Daily Top 20
    panel.
    """
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "invalid date — expected YYYY-MM-DD")

    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.compass_song))
        .filter(DailyReading.date == target)
        .first()
    )
    if not reading:
        raise HTTPException(404, "no reading for that date")
    return _render_reading_as_ether(reading, db)


@router.get("/years", response_model=EtherYearsOut)
def list_years(db: Session = Depends(get_db)):
    """Years that have any coverage — daily-reading (modern) or
    compass_songs (historical backfill). Used by the year selector on
    the product page."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT y FROM (
              SELECT CAST(to_char(dr.date, 'YYYY') AS INTEGER) AS y
              FROM daily_readings dr WHERE dr.date IS NOT NULL
              UNION
              SELECT year AS y FROM compass_songs WHERE year IS NOT NULL
            )
            ORDER BY y ASC
            """
        )
    ).fetchall()
    years = [int(r[0]) for r in rows if r[0] is not None]
    return EtherYearsOut(years=years)


@router.get("/year/{year}", response_model=EtherYearOut)
def get_year(year: int, db: Session = Depends(get_db)):
    """Top 20 deadpan + topic distribution for a year.

    Two data paths:
      - Modern years (have daily_readings): position-weighted song-days
        from reading_songs. A #1 placement is worth 20 weight units, a
        #20 placement 1 unit.
      - Historical years (no daily_readings, e.g. backfilled Billboard
        Year-End Hot 100 top 10): straight query against compass_songs
        ordered by chart_position. Position-weighting isn't meaningful
        here because each song has one row per year, not one per day.

    Songs without a deadpan_line are excluded from the top 20 list but
    still counted in the totals. Topic distribution counts each
    (compass_song, topic) pair once per song — not once per song-day.
    """
    today = date.today()
    if year < 1900 or year > today.year + 1:
        raise HTTPException(400, "year out of range")

    period_start = date(year, 1, 1)
    period_end = today if year == today.year else date(year, 12, 31)

    year_str = f"{year:04d}"

    # Detect data path — does this year have any daily_readings rows?
    has_daily = db.execute(
        text("SELECT 1 FROM daily_readings WHERE to_char(date, 'YYYY') = :year LIMIT 1"),
        {"year": year_str},
    ).scalar()

    if has_daily:
        # Modern path: position-weighted song-days (build pack §6)
        top_rows = db.execute(
            text(
                """
                WITH song_year_appearances AS (
                  SELECT
                    rs.compass_song_id,
                    SUM(21 - rs.position) AS weight_sum
                  FROM reading_songs rs
                  JOIN daily_readings dr ON dr.id = rs.reading_id
                  WHERE to_char(dr.date, 'YYYY') = :year
                    AND rs.position BETWEEN 1 AND 20
                    AND rs.compass_song_id IS NOT NULL
                  GROUP BY rs.compass_song_id
                )
                SELECT
                  cs.id, cs.title, cs.artist, cs.deadpan_line, cs.topics,
                  cs.rubric_color, cs.charge_value, s.weight_sum
                FROM song_year_appearances s
                JOIN compass_songs cs ON cs.id = s.compass_song_id
                WHERE cs.deadpan_line IS NOT NULL
                ORDER BY s.weight_sum DESC
                LIMIT 20
                """
            ),
            {"year": year_str},
        ).fetchall()
    else:
        # Historical path: compass_songs has one row per song per year
        # with chart_position 1..N. Order by chart_position directly.
        # chart_position_letter ('A'/'B') marks double-A-side splits so
        # 9A and 9B both render under position 9 with letter suffixes.
        top_rows = db.execute(
            text(
                """
                SELECT
                  cs.id, cs.title, cs.artist, cs.deadpan_line, cs.topics,
                  cs.rubric_color, cs.charge_value,
                  cs.chart_position, cs.chart_position_letter
                FROM compass_songs cs
                WHERE cs.year = :year_int
                  AND cs.deadpan_line IS NOT NULL
                ORDER BY cs.chart_position ASC, cs.chart_position_letter ASC, cs.id ASC
                LIMIT 20
                """
            ),
            {"year_int": year},
        ).fetchall()

    top_20_slug_map = resolve_artist_slugs([row.artist for row in top_rows], db)
    top_20: list[EtherYearDeadpanItem] = []
    for idx, row in enumerate(top_rows, start=1):
        topics = _decode_topics(row.topics)
        primary = normalize_artist_name(row.artist or "").lower()
        # Modern uses sequential idx; historical uses cs.chart_position +
        # cs.chart_position_letter so 9A/9B read as the same chart slot.
        if has_daily:
            position_num = idx
            position_letter = None
        else:
            position_num = int(row.chart_position) if row.chart_position is not None else idx
            position_letter = row.chart_position_letter or None
        top_20.append(EtherYearDeadpanItem(
            position=position_num,
            position_letter=position_letter,
            title=row.title,
            artist=row.artist,
            deadpan_line=row.deadpan_line,
            dominant_topic=topics[0] if topics else None,
            song_slug=generate_song_slug(row.title, row.artist),
            artist_slug=top_20_slug_map.get(primary),
            rubric_color=row.rubric_color,
            charge_value=row.charge_value,
        ))

    # Topic distribution — distinct (song, topic) pairs across the year.
    if has_daily:
        dist_rows = db.execute(
            text(
                """
                WITH year_compass_songs AS (
                  SELECT DISTINCT cs.id, cs.topics
                  FROM compass_songs cs
                  JOIN reading_songs rs ON rs.compass_song_id = cs.id
                  JOIN daily_readings dr ON dr.id = rs.reading_id
                  WHERE to_char(dr.date, 'YYYY') = :year
                    AND cs.topics IS NOT NULL
                )
                SELECT je.value AS topic, COUNT(*) AS cnt
                FROM year_compass_songs ycs, json_array_elements_text(ycs.topics::json) AS je(value)
                GROUP BY je.value
                ORDER BY cnt DESC
                """
            ),
            {"year": year_str},
        ).fetchall()
    else:
        dist_rows = db.execute(
            text(
                """
                WITH year_compass_songs AS (
                  SELECT id, topics FROM compass_songs
                  WHERE year = :year_int AND topics IS NOT NULL
                )
                SELECT je.value AS topic, COUNT(*) AS cnt
                FROM year_compass_songs ycs, json_array_elements_text(ycs.topics::json) AS je(value)
                GROUP BY je.value
                ORDER BY cnt DESC
                """
            ),
            {"year_int": year},
        ).fetchall()

    total_count = sum(int(r.cnt) for r in dist_rows) or 1

    # Top songs per topic — for tooltip use. Ranked by song-days
    # (modern) or chart_position (historical). Capped at 8 per topic.
    TOP_SONGS_PER_TOPIC = 8
    if has_daily:
        song_rows = db.execute(
            text(
                """
                WITH song_year_appearances AS (
                  SELECT
                    rs.compass_song_id,
                    SUM(21 - rs.position) AS weight_sum
                  FROM reading_songs rs
                  JOIN daily_readings dr ON dr.id = rs.reading_id
                  WHERE to_char(dr.date, 'YYYY') = :year
                    AND rs.position BETWEEN 1 AND 20
                    AND rs.compass_song_id IS NOT NULL
                  GROUP BY rs.compass_song_id
                )
                SELECT
                  je.value AS topic,
                  cs.title, cs.artist, cs.deadpan_line,
                  s.weight_sum
                FROM song_year_appearances s
                JOIN compass_songs cs ON cs.id = s.compass_song_id,
                     json_array_elements_text(cs.topics::json) AS je(value)
                WHERE cs.deadpan_line IS NOT NULL
                  AND cs.topics IS NOT NULL
                ORDER BY je.value, s.weight_sum DESC
                """
            ),
            {"year": year_str},
        ).fetchall()
    else:
        song_rows = db.execute(
            text(
                """
                SELECT
                  je.value AS topic,
                  cs.title, cs.artist, cs.deadpan_line,
                  cs.chart_position AS weight_sum
                FROM compass_songs cs, json_array_elements_text(cs.topics::json) AS je(value)
                WHERE cs.year = :year_int
                  AND cs.deadpan_line IS NOT NULL
                  AND cs.topics IS NOT NULL
                ORDER BY je.value, cs.chart_position ASC
                """
            ),
            {"year_int": year},
        ).fetchall()

    topic_slug_map = resolve_artist_slugs([sr.artist for sr in song_rows], db)
    top_songs_by_topic: dict[str, list[TopicSong]] = {}
    for sr in song_rows:
        topic = str(sr.topic)
        bucket = top_songs_by_topic.setdefault(topic, [])
        if len(bucket) >= TOP_SONGS_PER_TOPIC:
            continue
        primary = normalize_artist_name(sr.artist or "").lower()
        bucket.append(TopicSong(
            title=sr.title,
            artist=sr.artist,
            deadpan_line=sr.deadpan_line,
            song_slug=generate_song_slug(sr.title, sr.artist),
            position=len(bucket) + 1,
            artist_slug=topic_slug_map.get(primary),
        ))

    distribution = [
        TopicDistributionItem(
            topic=str(r.topic),
            count=int(r.cnt),
            percent=round(int(r.cnt) / total_count, 4),
            top_songs=top_songs_by_topic.get(str(r.topic), []),
        )
        for r in dist_rows
    ]

    # Audit-flagged count + total compass_songs in period
    if has_daily:
        audit_count = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT cs.id)
                FROM compass_songs cs
                JOIN reading_songs rs ON rs.compass_song_id = cs.id
                JOIN daily_readings dr ON dr.id = rs.reading_id
                WHERE to_char(dr.date, 'YYYY') = :year
                  AND cs.topic_audit IS NOT NULL
                """
            ),
            {"year": year_str},
        ).scalar() or 0
    else:
        audit_count = db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM compass_songs cs
                WHERE cs.year = :year_int AND cs.topic_audit IS NOT NULL
                """
            ),
            {"year_int": year},
        ).scalar() or 0

    if has_daily:
        total_in_period = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT cs.id)
                FROM compass_songs cs
                JOIN reading_songs rs ON rs.compass_song_id = cs.id
                JOIN daily_readings dr ON dr.id = rs.reading_id
                WHERE to_char(dr.date, 'YYYY') = :year
                """
            ),
            {"year": year_str},
        ).scalar() or 0
    else:
        total_in_period = db.execute(
            text(
                """
                SELECT COUNT(*) FROM compass_songs cs WHERE cs.year = :year_int
                """
            ),
            {"year_int": year},
        ).scalar() or 0

    return EtherYearOut(
        year=year,
        top_20_deadpan=top_20,
        topic_distribution=distribution,
        audit_flagged_count=int(audit_count),
        total_compass_songs_in_period=int(total_in_period),
        period_start=period_start,
        period_end=period_end,
    )
