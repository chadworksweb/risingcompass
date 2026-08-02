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

import json
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


class FracTopicCount(BaseModel):
    """Fractional-weight entry (recalibration Step 6): each song contributes
    1.0 total, split evenly across its tags (1/k per tag), so a 3-tag song
    stops out-voting a 1-tag song and a year's weights sum to its song count."""
    topic: str
    weight: float
    percent: float


class YearPoint(BaseModel):
    year: int
    songs_with_topics: int      # distinct tagged songs in the year
    total_pairs: int            # sum of (song, topic) pairs = sum of counts
    distinct_topics: int        # how many of the 30 topics appear at all
    shannon: float              # entropy in bits
    effective_topics: float     # 2**shannon
    distribution: list[TopicCount]
    # Parallel measures (recalibration Step 2, additive -- the chart still
    # renders from the all-pairs fields above). "Dominant" = the song's
    # FIRST-LISTED topic only, one vote per song, which removes the
    # tags-per-song drift confound; "themes" rolls topics to their single
    # primary theme (the strict tree), the altitude immune to the romance
    # shelf holding 7 of 31 slugs.
    effective_topics_dominant: float
    effective_themes: float
    effective_themes_dominant: float
    distribution_dominant: list[TopicCount]
    # Fixed-basis measures (recalibration Step 5, additive): computed over the
    # year's top-BASIS_N songs by prominence, so no year out-votes another on
    # sample size. n_available = TAGGED songs inside the top-BASIS_N window
    # (instrumentals and no-fit songs occupy their slot but cannot vote);
    # below_basis = the permanent honesty flag.
    n_available: int
    below_basis: bool
    effective_topics_basis: float
    effective_topics_dominant_basis: float
    effective_themes_dominant_basis: float
    distribution_dominant_basis: list[TopicCount]
    # Fractional weighting for the river (recalibration Step 6, additive).
    distribution_fractional: list[FracTopicCount]
    # Intra-romance share (recalibration Step 7): fraction of songs whose
    # DOMINANT topic files on the romance shelf (primary theme == romance).
    # The share the romance shelf's 7 slugs would otherwise hide at topic
    # level. _basis = computed over the fixed top-BASIS_N window.
    romance_share_dominant: float
    romance_share_dominant_basis: float


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
    basis_n: int


# --- Helpers ---

# Below this many years of tagged data, the page shows the "early signal"
# caveat -- a decades-long narrowing can't be claimed from a short window.
EARLY_SIGNAL_SPAN_YEARS = 10

# Recalibration Step 5: every year's basis-measures are computed over the SAME
# number of songs, ranked by prominence (year-end chart position for
# historical years; days-on-reading for modern years). A single global
# constant on purpose: it gates UPWARD with the Hot-100 backfill (bump it only
# when (nearly) every year supports the new depth -- top 10 + 11-20 done =>
# 20 now, 21-30 across all years licenses 30, on the road to the full 100).
# NEVER mix bases within one rendered series; a bump recomputes the whole
# line uniformly. Years that fall short carry below_basis=True so the chart
# renders them dashed/dimmed instead of lying by omission.
BASIS_N = 20


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


def _parse_topics(raw) -> list[str]:
    """songs.topics is a JSON-encoded Text column, dominant topic first."""
    if not raw:
        return []
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    return [t for t in val if isinstance(t, str)] if isinstance(val, list) else []


def _effective(counts: dict[str, int]) -> float:
    return round(2.0 ** _shannon_bits(list(counts.values())), 3)


def _roll_to_themes(counts: dict[str, int], primary_map: dict[str, str]) -> dict[str, int]:
    """Roll per-topic counts to primary-theme counts. A topic missing from the
    hierarchy (shouldn't happen; writes validate against the taxonomy) is
    skipped rather than invented into a theme."""
    out: dict[str, int] = {}
    for topic, n in counts.items():
        theme = primary_map.get(topic)
        if theme:
            out[theme] = out.get(theme, 0) + n
    return out


def _distribution(counts: dict[str, int]) -> list[TopicCount]:
    total = sum(counts.values())
    dist = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        TopicCount(topic=t, count=c, percent=round(c / total, 4) if total else 0.0)
        for t, c in dist
    ]


def _romance_share(dom_counts: dict[str, int], primary_map: dict[str, str]) -> float:
    """Fraction of dominant-topic votes that file on the romance shelf."""
    total = sum(dom_counts.values())
    if total <= 0:
        return 0.0
    rom = sum(c for t, c in dom_counts.items() if primary_map.get(t) == "romance")
    return round(rom / total, 4)


def _distribution_frac(weights: dict[str, float]) -> list[FracTopicCount]:
    total = sum(weights.values())
    dist = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        FracTopicCount(topic=t, weight=round(w, 4),
                       percent=round(w / total, 4) if total else 0.0)
        for t, w in dist
    ]


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

    # year -> {topic: count}, all (song, topic) pairs
    per_year: dict[int, dict[str, int]] = {}
    # year -> {topic: count}, dominant (first-listed) topic only, 1/song
    per_year_dom: dict[int, dict[str, int]] = {}
    # year -> {topic: weight}, each song worth 1.0 split 1/k across its tags
    per_year_frac: dict[int, dict[str, float]] = {}
    # year -> set of distinct tagged song ids
    songs_by_year: dict[int, set[int]] = {}

    def _ingest(yr: int, song_id: int, raw_topics) -> None:
        # A song can reach the same year via several rows (DISTINCT on the
        # SQL side keys on (yr, id, topics)); guard on the song-id set so a
        # song votes once per year on every measure.
        if song_id in songs_by_year.setdefault(yr, set()):
            return
        topics = _parse_topics(raw_topics)
        if not topics:
            return
        songs_by_year[yr].add(song_id)
        pairs = per_year.setdefault(yr, {})
        frac = per_year_frac.setdefault(yr, {})
        share = 1.0 / len(topics)
        for t in topics:
            pairs[t] = pairs.get(t, 0) + 1
            frac[t] = frac.get(t, 0.0) + share
        dom = per_year_dom.setdefault(yr, {})
        dom[topics[0]] = dom.get(topics[0], 0) + 1

    # Modern path: one row per (year, song); topics parsed here (the array
    # order is meaningful -- dominant topic first -- so no SQL explode).
    modern_rows = db.execute(
        text(
            """
            SELECT DISTINCT
              CAST(to_char(dr.date, 'YYYY') AS INTEGER) AS yr,
              s.id AS song_id, s.topics
            FROM songs s
            JOIN reading_songs rs ON rs.song_id = s.id
            JOIN daily_readings dr ON dr.id = rs.reading_id
            WHERE s.topics IS NOT NULL
            """
        )
    ).fetchall()
    for r in modern_rows:
        _ingest(int(r.yr), int(r.song_id), r.topics)

    # Historical path: exclude modern years so the same year isn't double-served.
    hist_rows = db.execute(
        text(
            """
            SELECT DISTINCT ca.year AS yr, s.id AS song_id, s.topics
            FROM songs s
            JOIN chart_appearances ca ON ca.song_id = s.id
            JOIN charts c ON c.id = ca.chart_id
            WHERE c.slug = ANY(:agg_slugs)
              AND s.topics IS NOT NULL
              AND ca.year IS NOT NULL
            """
        ),
        {"agg_slugs": _AGG_SLUGS},
    ).fetchall()
    modern_set = set(modern_years)
    for r in hist_rows:
        yr = int(r.yr)
        if yr in modern_set:
            continue
        _ingest(yr, int(r.song_id), r.topics)

    # --- Fixed-basis (top-BASIS_N by prominence) parallel aggregation -------
    # Same two paths, but each year's window is its BASIS_N most prominent
    # songs, tagged or not: an untagged song (instrumental, no-fit) occupies
    # its slot and simply cannot vote, which is what n_available reports.
    basis_pairs: dict[int, dict[str, int]] = {}
    basis_dom: dict[int, dict[str, int]] = {}
    basis_tagged: dict[int, set[int]] = {}
    basis_seen: dict[int, set[int]] = {}

    def _ingest_basis(yr: int, song_id: int, raw_topics) -> None:
        if song_id in basis_seen.setdefault(yr, set()):
            return
        basis_seen[yr].add(song_id)
        topics = _parse_topics(raw_topics)
        if not topics:
            return
        basis_tagged.setdefault(yr, set()).add(song_id)
        pairs = basis_pairs.setdefault(yr, {})
        for t in topics:
            pairs[t] = pairs.get(t, 0) + 1
        dom = basis_dom.setdefault(yr, {})
        dom[topics[0]] = dom.get(topics[0], 0) + 1

    # Modern years: prominence = days on the daily reading within the year.
    modern_basis_rows = db.execute(
        text(
            """
            WITH days AS (
              SELECT CAST(to_char(dr.date, 'YYYY') AS INTEGER) AS yr,
                     s.id AS song_id, s.topics,
                     COUNT(DISTINCT dr.date) AS days_on
              FROM songs s
              JOIN reading_songs rs ON rs.song_id = s.id
              JOIN daily_readings dr ON dr.id = rs.reading_id
              GROUP BY 1, s.id, s.topics
            ), ranked AS (
              SELECT yr, song_id, topics,
                     ROW_NUMBER() OVER (
                       PARTITION BY yr
                       ORDER BY days_on DESC, song_id ASC
                     ) AS rk
              FROM days
            )
            SELECT yr, song_id, topics FROM ranked WHERE rk <= :basis_n
            """
        ),
        {"basis_n": BASIS_N},
    ).fetchall()
    for r in modern_basis_rows:
        _ingest_basis(int(r.yr), int(r.song_id), r.topics)

    # Historical years: prominence = best year-end chart position.
    hist_basis_rows = db.execute(
        text(
            """
            WITH ranked AS (
              SELECT ca.year AS yr, s.id AS song_id, s.topics,
                     MIN(ca.position) AS best_pos
              FROM songs s
              JOIN chart_appearances ca ON ca.song_id = s.id
              JOIN charts c ON c.id = ca.chart_id
              WHERE c.slug = ANY(:agg_slugs)
                AND ca.year IS NOT NULL
                AND ca.position IS NOT NULL
              GROUP BY ca.year, s.id, s.topics
            )
            SELECT yr, song_id, topics FROM ranked WHERE best_pos <= :basis_n
            """
        ),
        {"agg_slugs": _AGG_SLUGS, "basis_n": BASIS_N},
    ).fetchall()
    for r in hist_basis_rows:
        yr = int(r.yr)
        if yr in modern_set:
            continue
        _ingest_basis(yr, int(r.song_id), r.topics)

    # Hierarchy from the DB resolver (Phase 1: presentation-authoritative),
    # falling back to the code constants when the tables are empty/unreachable.
    # Fetched before the points loop because the theme rollups need the
    # topic -> primary-theme map.
    hierarchy = topic_hierarchy(db)
    primary_map = {slug: meta["primary"] for slug, meta in hierarchy["topics"].items()}

    # Build per-year points, oldest -> newest. The in-progress current year is
    # excluded: its data is partial (only the days elapsed so far), so its topic
    # mix and diversity measures aren't comparable to a full year and would drag
    # the trend line on incomplete data. Topic Trends is a completed-year series.
    current_year = date.today().year
    years_out: list[YearPoint] = []
    for yr in sorted(per_year.keys()):
        if yr >= current_year:
            continue
        counts = per_year[yr]
        total = sum(counts.values())
        if total <= 0:
            continue
        dom = per_year_dom.get(yr, {})
        b_pairs = basis_pairs.get(yr, {})
        b_dom = basis_dom.get(yr, {})
        n_avail = len(basis_tagged.get(yr, set()))
        shannon = _shannon_bits(list(counts.values()))
        years_out.append(YearPoint(
            year=yr,
            songs_with_topics=len(songs_by_year.get(yr, set())),
            total_pairs=total,
            distinct_topics=len(counts),
            shannon=round(shannon, 4),
            effective_topics=round(2.0 ** shannon, 3),
            distribution=_distribution(counts),
            effective_topics_dominant=_effective(dom),
            effective_themes=_effective(_roll_to_themes(counts, primary_map)),
            effective_themes_dominant=_effective(_roll_to_themes(dom, primary_map)),
            distribution_dominant=_distribution(dom),
            distribution_fractional=_distribution_frac(per_year_frac.get(yr, {})),
            n_available=n_avail,
            below_basis=n_avail < BASIS_N,
            effective_topics_basis=_effective(b_pairs),
            effective_topics_dominant_basis=_effective(b_dom),
            effective_themes_dominant_basis=_effective(_roll_to_themes(b_dom, primary_map)),
            distribution_dominant_basis=_distribution(b_dom),
            romance_share_dominant=_romance_share(dom, primary_map),
            romance_share_dominant_basis=_romance_share(b_dom, primary_map),
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

    # Taxonomy block from the same hierarchy fetched above.
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
        basis_n=BASIS_N,
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
    # Parallel measures (recalibration Step 2) -- see YearPoint.
    effective_topics_dominant: float
    effective_themes: float
    effective_themes_dominant: float
    distribution_dominant: list[TopicCount]
    # Fractional weighting for the river (recalibration Step 6) -- see YearPoint.
    distribution_fractional: list[FracTopicCount]
    # Intra-romance share (recalibration Step 7) -- see YearPoint.
    romance_share_dominant: float


class TopicTrendsTrailingOut(BaseModel):
    bucket: str                 # "month"
    window_start: str           # earliest bucket key, "YYYY-MM"
    periods: list[PeriodPoint]  # oldest -> newest (empty months included)
    # Recalibration Step 8: which window this is. "ytd" = the in-progress
    # calendar year, always partial, never merged into the historical series.
    view: str = "trailing"      # "trailing" | "ytd"
    partial: bool = False


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


def _ytd_month_keys() -> list[str]:
    """January through the current month of the in-progress year."""
    today = date.today()
    return [f"{today.year:04d}-{m:02d}" for m in range(1, today.month + 1)]


@router.get("/trailing", response_model=TopicTrendsTrailingOut)
def get_topic_trends_trailing(period: str = "trailing", db: Session = Depends(get_db)):
    """Topic distribution + diversity bucketed by calendar month. Two windows:
    the default last-12-months ("Trailing 365 days" tab) and, with
    ?period=ytd, the in-progress calendar year (recalibration Step 8) --
    an ISOLATED partial-year view that is never merged into the historical
    series. Modern path only; empty months are returned so the x-axis is a
    continuous span."""
    is_ytd = period == "ytd"
    months = _ytd_month_keys() if is_ytd else _last_n_month_keys(12)
    start_y, start_m = months[0].split("-")
    start_date = f"{start_y}-{start_m}-01"

    rows = db.execute(
        text(
            """
            SELECT DISTINCT
              to_char(dr.date, 'YYYY-MM') AS ym,
              s.id AS song_id, s.topics
            FROM songs s
            JOIN reading_songs rs ON rs.song_id = s.id
            JOIN daily_readings dr ON dr.id = rs.reading_id
            WHERE s.topics IS NOT NULL
              AND dr.date >= CAST(:start AS date)
            """
        ),
        {"start": start_date},
    ).fetchall()

    per_month: dict[str, dict[str, int]] = {}
    per_month_dom: dict[str, dict[str, int]] = {}
    per_month_frac: dict[str, dict[str, float]] = {}
    songs_by_month: dict[str, set[int]] = {}
    for r in rows:
        if int(r.song_id) in songs_by_month.setdefault(r.ym, set()):
            continue
        topics = _parse_topics(r.topics)
        if not topics:
            continue
        songs_by_month[r.ym].add(int(r.song_id))
        pairs = per_month.setdefault(r.ym, {})
        frac = per_month_frac.setdefault(r.ym, {})
        share = 1.0 / len(topics)
        for t in topics:
            pairs[t] = pairs.get(t, 0) + 1
            frac[t] = frac.get(t, 0.0) + share
        dom = per_month_dom.setdefault(r.ym, {})
        dom[topics[0]] = dom.get(topics[0], 0) + 1

    hierarchy = topic_hierarchy(db)
    primary_map = {slug: meta["primary"] for slug, meta in hierarchy["topics"].items()}

    periods: list[PeriodPoint] = []
    for key in months:
        counts = per_month.get(key, {})
        dom = per_month_dom.get(key, {})
        total = sum(counts.values())
        shannon = _shannon_bits(list(counts.values()))
        _, mm = key.split("-")
        periods.append(PeriodPoint(
            key=key,
            label=f"{_MONTH_ABBR[int(mm)]} {key[:4]}",
            songs_with_topics=len(songs_by_month.get(key, set())),
            total_pairs=total,
            distinct_topics=len(counts),
            shannon=round(shannon, 4),
            effective_topics=round(2.0 ** shannon, 3),
            distribution=_distribution(counts),
            effective_topics_dominant=_effective(dom),
            effective_themes=_effective(_roll_to_themes(counts, primary_map)),
            effective_themes_dominant=_effective(_roll_to_themes(dom, primary_map)),
            distribution_dominant=_distribution(dom),
            distribution_fractional=_distribution_frac(per_month_frac.get(key, {})),
            romance_share_dominant=_romance_share(dom, primary_map),
        ))

    return TopicTrendsTrailingOut(
        bucket="month", window_start=months[0], periods=periods,
        view="ytd" if is_ytd else "trailing", partial=is_ytd,
    )
