"""Public Topic Trends API -- the data behind "The Narrowing".

One read-only endpoint that returns a per-year topic time series across every
year that has tagged topics, plus two derived diversity measures per year:

  - shannon          -- Shannon entropy (bits) over that year's topic mix
  - effective_topics -- 2**shannon, the "effective number of topics" (a
                        count-like read: ~16 effective topics in a wide year,
                        ~5 in a narrow one). A falling effective_topics line
                        IS the narrowing, quantified.

Built SEPARATELY from ether_art_chart.py (which powers the esoteric Ether Art
Chart). This shares the taxonomy + the same distribution SQL shape, but is its
own surface: the homepage-branded Topic Trends page.

Honesty: the topic tagger is forward-only, so the historical Billboard catalog
is NULL-topic until the deferred backfill tags it. The response carries a
`coverage` block (`corpus_year_range` = the full span that COULD be tagged vs
`years_with_topics` = what is tagged today) so the page can render an honest
"early signal" caveat. The same endpoint fills in across the decades on its own
as historical tagging lands -- no shape change.

Mounted from main.py with `_public_read_dep`, like the other public RC reads.
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.constants import AGGREGATING_CHART_SLUGS
from app.database import get_db
from app.services.ether_taxonomy import topic_hierarchy

_AGG_SLUGS = sorted(AGGREGATING_CHART_SLUGS)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topic-trends", tags=["topic-trends"])


# --- Schemas ---

class ThemeEntry(BaseModel):
    slug: str
    label: str


class TaxonomyEntry(BaseModel):
    slug: str
    label: str            # human display: "self-affirmation" -> "self affirmation"
    primary: str          # the topic's single primary theme slug
    also: list[str] = []  # secondary theme facets (never summed)


class TopicCount(BaseModel):
    topic: str
    count: int
    percent: float


class YearPoint(BaseModel):
    year: int
    songs_with_topics: int      # distinct tagged songs in the year
    total_pairs: int            # sum of (song, topic) pairs = sum of counts
    distinct_topics: int        # how many of the 30 topics appear at all
    shannon: float              # entropy in bits
    effective_topics: float     # 2**shannon
    distribution: list[TopicCount]


class Coverage(BaseModel):
    corpus_year_range: Optional[list[int]]   # [min, max] of ALL years with any data
    topic_year_range: Optional[list[int]]    # [min, max] of years that have topics
    years_with_topics: int
    span_years: int                          # topic_year_range span, inclusive
    is_early_signal: bool                    # true while the span can't prove decades


class TopicTrendsOut(BaseModel):
    taxonomy: list[TaxonomyEntry]
    themes: list[ThemeEntry]
    years: list[YearPoint]
    coverage: Coverage


# --- Helpers ---

# Below this many years of tagged data, the page shows the "early signal"
# caveat -- a decades-long narrowing can't be claimed from a short window.
EARLY_SIGNAL_SPAN_YEARS = 10


def _shannon_bits(counts: list[int]) -> float:
    """Shannon entropy in bits over a list of category counts."""
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def _label_for(slug: str) -> str:
    return slug.replace("-", " ")


# --- Endpoint ---

@router.get("", response_model=TopicTrendsOut)
@router.get("/", response_model=TopicTrendsOut)
def get_topic_trends(db: Session = Depends(get_db)):
    """Per-year topic distribution + diversity measures across all tagged years.

    Mirrors the ether year endpoint's two data paths, but aggregated by year in
    two grouped queries instead of one-year-at-a-time:
      - Modern years (have daily_readings): distinct (year, song) from
        reading_songs, exploded by topic.
      - Historical years (no daily_readings): distinct (year, song) from
        chart_appearances on aggregating charts, exploded by topic.
    A year that has daily_readings is served by the modern path only, so the
    two paths never double-count the same year.
    """
    # Years that have a daily reading -- these are the "modern" years; the
    # historical path must exclude them so a year is counted once.
    modern_year_rows = db.execute(
        text(
            """
            SELECT DISTINCT CAST(to_char(dr.date, 'YYYY') AS INTEGER) AS yr
            FROM daily_readings dr
            WHERE dr.date IS NOT NULL
            """
        )
    ).fetchall()
    modern_years = sorted(int(r.yr) for r in modern_year_rows if r.yr is not None)

    # year -> {topic: count}
    per_year: dict[int, dict[str, int]] = {}
    # year -> set of distinct tagged song ids
    songs_by_year: dict[int, set[int]] = {}

    # Modern path: one row per (year, song); explode topics.
    modern_rows = db.execute(
        text(
            """
            WITH year_songs AS (
              SELECT DISTINCT
                CAST(to_char(dr.date, 'YYYY') AS INTEGER) AS yr,
                s.id, s.topics
              FROM songs s
              JOIN reading_songs rs ON rs.song_id = s.id
              JOIN daily_readings dr ON dr.id = rs.reading_id
              WHERE s.topics IS NOT NULL
            )
            SELECT ys.yr, ys.id AS song_id, je.value AS topic
            FROM year_songs ys,
                 json_array_elements_text(ys.topics::json) AS je(value)
            """
        )
    ).fetchall()
    for r in modern_rows:
        yr = int(r.yr)
        per_year.setdefault(yr, {})
        per_year[yr][r.topic] = per_year[yr].get(r.topic, 0) + 1
        songs_by_year.setdefault(yr, set()).add(int(r.song_id))

    # Historical path: exclude modern years so the same year isn't double-served.
    hist_rows = db.execute(
        text(
            """
            WITH year_songs AS (
              SELECT DISTINCT ca.year AS yr, s.id, s.topics
              FROM songs s
              JOIN chart_appearances ca ON ca.song_id = s.id
              JOIN charts c ON c.id = ca.chart_id
              WHERE c.slug = ANY(:agg_slugs)
                AND s.topics IS NOT NULL
                AND ca.year IS NOT NULL
            )
            SELECT ys.yr, ys.id AS song_id, je.value AS topic
            FROM year_songs ys,
                 json_array_elements_text(ys.topics::json) AS je(value)
            """
        ),
        {"agg_slugs": _AGG_SLUGS},
    ).fetchall()
    modern_set = set(modern_years)
    for r in hist_rows:
        yr = int(r.yr)
        if yr in modern_set:
            continue
        per_year.setdefault(yr, {})
        per_year[yr][r.topic] = per_year[yr].get(r.topic, 0) + 1
        songs_by_year.setdefault(yr, set()).add(int(r.song_id))

    # Build per-year points, oldest -> newest.
    years_out: list[YearPoint] = []
    for yr in sorted(per_year.keys()):
        counts = per_year[yr]
        total = sum(counts.values())
        if total <= 0:
            continue
        dist = sorted(
            counts.items(), key=lambda kv: (-kv[1], kv[0])
        )
        shannon = _shannon_bits([c for _, c in dist])
        years_out.append(YearPoint(
            year=yr,
            songs_with_topics=len(songs_by_year.get(yr, set())),
            total_pairs=total,
            distinct_topics=len(dist),
            shannon=round(shannon, 4),
            effective_topics=round(2.0 ** shannon, 3),
            distribution=[
                TopicCount(topic=t, count=c, percent=round(c / total, 4))
                for t, c in dist
            ],
        ))

    # Coverage: the full corpus span (any data, tagged or not) vs the tagged
    # span. corpus_year_range is what COULD be tagged; topic span is what is.
    corpus_rows = db.execute(
        text(
            """
            SELECT MIN(y) AS lo, MAX(y) AS hi FROM (
              SELECT CAST(to_char(dr.date, 'YYYY') AS INTEGER) AS y
              FROM daily_readings dr WHERE dr.date IS NOT NULL
              UNION
              SELECT ca.year AS y FROM chart_appearances ca WHERE ca.year IS NOT NULL
            ) AS u
            """
        )
    ).first()
    corpus_range = (
        [int(corpus_rows.lo), int(corpus_rows.hi)]
        if corpus_rows and corpus_rows.lo is not None
        else None
    )

    tagged_years = [p.year for p in years_out]
    topic_range = [min(tagged_years), max(tagged_years)] if tagged_years else None
    span = (topic_range[1] - topic_range[0] + 1) if topic_range else 0

    coverage = Coverage(
        corpus_year_range=corpus_range,
        topic_year_range=topic_range,
        years_with_topics=len(tagged_years),
        span_years=span,
        is_early_signal=span < EARLY_SIGNAL_SPAN_YEARS,
    )

    # Hierarchy from the DB resolver (Phase 1: presentation-authoritative),
    # falling back to the code constants when the tables are empty/unreachable.
    # Response shape is unchanged, so the frontend needs no change.
    hierarchy = topic_hierarchy(db)
    taxonomy = [
        TaxonomyEntry(
            slug=slug,
            label=meta.get("label") or _label_for(slug),
            primary=meta["primary"],
            also=meta.get("also", []),
        )
        for slug, meta in hierarchy["topics"].items()
    ]
    themes = [ThemeEntry(slug=t["slug"], label=t["label"]) for t in hierarchy["themes"]]

    return TopicTrendsOut(
        taxonomy=taxonomy, themes=themes, years=years_out, coverage=coverage,
    )


# --- Trailing window (sub-yearly) -----------------------------------------
#
# The per-year endpoint above powers the Historical tab. The Topic Trends panel
# also has a "Trailing 365 days" tab that needs sub-yearly resolution. The
# modern path already keys off daily_readings.date, so we can bucket by calendar
# month over the last 12 months. Historical chart appearances carry no fine date
# (year only), so they are intentionally excluded here -- the trailing window is
# a recent-mix view, fed by the live daily reading.


class PeriodPoint(BaseModel):
    key: str                    # "YYYY-MM"
    label: str                  # "Jul 2025"
    songs_with_topics: int
    total_pairs: int
    distinct_topics: int
    shannon: float
    effective_topics: float
    distribution: list[TopicCount]


class TopicTrendsTrailingOut(BaseModel):
    bucket: str                 # "month"
    window_start: str           # earliest bucket key, "YYYY-MM"
    periods: list[PeriodPoint]  # always 12, oldest -> newest (empty months included)


_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _last_n_month_keys(n: int) -> list[str]:
    """The last n 'YYYY-MM' keys ending with the current month, oldest first."""
    today = date.today()
    y, m = today.year, today.month
    keys: list[str] = []
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(keys))


@router.get("/trailing", response_model=TopicTrendsTrailingOut)
def get_topic_trends_trailing(db: Session = Depends(get_db)):
    """Topic distribution + diversity bucketed by calendar month over the last
    12 months (the "Trailing 365 days" tab). Modern path only; empty months are
    returned so the x-axis is a continuous 12-bucket span."""
    months = _last_n_month_keys(12)
    start_y, start_m = months[0].split("-")
    start_date = f"{start_y}-{start_m}-01"

    rows = db.execute(
        text(
            """
            WITH month_songs AS (
              SELECT DISTINCT
                to_char(dr.date, 'YYYY-MM') AS ym,
                s.id, s.topics
              FROM songs s
              JOIN reading_songs rs ON rs.song_id = s.id
              JOIN daily_readings dr ON dr.id = rs.reading_id
              WHERE s.topics IS NOT NULL
                AND dr.date >= CAST(:start AS date)
            )
            SELECT ms.ym, ms.id AS song_id, je.value AS topic
            FROM month_songs ms,
                 json_array_elements_text(ms.topics::json) AS je(value)
            """
        ),
        {"start": start_date},
    ).fetchall()

    per_month: dict[str, dict[str, int]] = {}
    songs_by_month: dict[str, set[int]] = {}
    for r in rows:
        per_month.setdefault(r.ym, {})
        per_month[r.ym][r.topic] = per_month[r.ym].get(r.topic, 0) + 1
        songs_by_month.setdefault(r.ym, set()).add(int(r.song_id))

    periods: list[PeriodPoint] = []
    for key in months:
        counts = per_month.get(key, {})
        total = sum(counts.values())
        dist = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        shannon = _shannon_bits([c for _, c in dist])
        _, mm = key.split("-")
        periods.append(PeriodPoint(
            key=key,
            label=f"{_MONTH_ABBR[int(mm)]} {key[:4]}",
            songs_with_topics=len(songs_by_month.get(key, set())),
            total_pairs=total,
            distinct_topics=len(dist),
            shannon=round(shannon, 4),
            effective_topics=round(2.0 ** shannon, 3),
            distribution=[
                TopicCount(topic=t, count=c, percent=round(c / total, 4) if total else 0.0)
                for t, c in dist
            ],
        ))

    return TopicTrendsTrailingOut(bucket="month", window_start=months[0], periods=periods)
