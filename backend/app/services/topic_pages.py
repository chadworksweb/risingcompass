"""Aggregates behind the public topic pages.

A topic page has to carry a READING, not a list. So everything here answers a
question about the subject rather than counting rows for their own sake: where
the compass lands on it, whether it reads differently when it leads the song
instead of riding along, and how often it comes with contamination. The song
list is the evidence under the finding, not the point of the page.

WHY THIS COMPUTES ON READ. The whole corpus is ~2.3k tagged songs, so the
aggregate is one grouped scan over a table Postgres already holds in memory.
Materialising it into a summary table would buy nothing measurable and would
introduce a staleness bug the first time a recalibration lands. A short
module-level cache absorbs the repeat reads instead. Revisit around 50k songs,
which is where the scan stops being free.

Topic slugs come from the DB taxonomy via `ether_taxonomy`, never from a
hardcoded list, so an admin edit to a label or a theme shows up here without a
deploy.
"""

import json
import logging
import time as _time
from typing import Optional

from sqlalchemy import text

from app.constants import COLOR_HEX, COLOR_LABELS
from app.services import ether_taxonomy

logger = logging.getLogger(__name__)

# Long enough to absorb a burst of page views, short enough that a
# recalibration shows up without anyone thinking about it.
_AGG_TTL = 60.0
_agg_cache: dict = {"data": None, "expires": 0.0}

# The dominant/incidental comparison is only worth stating when the two
# genuinely differ. Below this many points it is noise wearing a finding's
# clothes, and the page suppresses it.
DOMINANT_DELTA_FLOOR = 3

# How many topics must cross before two themes count as related.
MIN_THEME_CROSSINGS = 2

SONGS_PER_PAGE = 24

# Every calibrated, non-failed song that carries at least one topic. The same
# population for every figure on the page, so counts always reconcile.
_POPULATION = """
    FROM songs s
    WHERE s.topics IS NOT NULL AND s.topics <> '[]'
      AND s.charge_value IS NOT NULL
      AND s.calibration_failed IS NOT TRUE
"""

# Same population, with the slug joined on for anything that renders a link.
_POPULATION_LINKED = """
    FROM songs s
    LEFT JOIN song_slugs sl ON sl.song_id = s.id
    WHERE s.topics IS NOT NULL AND s.topics <> '[]'
      AND s.charge_value IS NOT NULL
      AND s.calibration_failed IS NOT TRUE
"""


def bust_topic_cache() -> None:
    """Drop the cached aggregate. Safe to call from anywhere that writes a
    calibration; the next read rebuilds."""
    _agg_cache["data"] = None
    _agg_cache["expires"] = 0.0


def _compute(db) -> dict:
    """One pass over the tagged corpus: per-topic rollups plus the library
    baseline they are compared against."""
    rows = db.execute(text(f"""
        WITH exploded AS (
            SELECT s.id,
                   s.charge_value,
                   s.rubric_color,
                   s.contaminated,
                   (s.topics::jsonb ->> 0) AS dominant,
                   jsonb_array_elements_text(s.topics::jsonb) AS topic
            {_POPULATION}
        )
        SELECT topic,
               COUNT(*)                                                    AS songs,
               ROUND(AVG(charge_value))                                    AS avg_charge,
               COUNT(*) FILTER (WHERE topic = dominant)                    AS dominant_songs,
               ROUND(AVG(charge_value) FILTER (WHERE topic = dominant))    AS avg_dominant,
               ROUND(AVG(charge_value) FILTER (WHERE topic <> dominant))   AS avg_incidental,
               COUNT(*) FILTER (WHERE contaminated)                        AS contaminated,
               MIN(charge_value)                                           AS min_charge,
               MAX(charge_value)                                           AS max_charge,
               COUNT(*) FILTER (WHERE rubric_color = 'violet')             AS n_violet,
               COUNT(*) FILTER (WHERE rubric_color = 'blue')               AS n_blue,
               COUNT(*) FILTER (WHERE rubric_color = 'green')              AS n_green,
               COUNT(*) FILTER (WHERE rubric_color = 'orange')             AS n_orange,
               COUNT(*) FILTER (WHERE rubric_color = 'red')                AS n_red
        FROM exploded
        GROUP BY topic
    """)).fetchall()

    baseline = db.execute(text(f"""
        SELECT COUNT(*) AS songs,
               ROUND(AVG(s.charge_value)) AS avg_charge,
               COUNT(*) FILTER (WHERE s.contaminated) AS contaminated
        {_POPULATION}
    """)).first()

    def _int(v):
        return int(v) if v is not None else None

    stats = {}
    for r in rows:
        stats[r.topic] = {
            "songs": int(r.songs),
            "avg_charge": _int(r.avg_charge),
            "dominant_songs": int(r.dominant_songs),
            "avg_dominant": _int(r.avg_dominant),
            "avg_incidental": _int(r.avg_incidental),
            "contaminated": int(r.contaminated),
            "contaminated_pct": round(100.0 * int(r.contaminated) / int(r.songs), 1)
            if r.songs else 0.0,
            "min_charge": _int(r.min_charge),
            "max_charge": _int(r.max_charge),
            "tiers": {
                "violet": int(r.n_violet), "blue": int(r.n_blue),
                "green": int(r.n_green), "orange": int(r.n_orange),
                "red": int(r.n_red),
            },
        }

    lib_songs = int(baseline.songs or 0)
    return {
        "topics": stats,
        "library": {
            "songs": lib_songs,
            "avg_charge": _int(baseline.avg_charge),
            "contaminated": int(baseline.contaminated or 0),
            "contaminated_pct": round(100.0 * int(baseline.contaminated or 0) / lib_songs, 1)
            if lib_songs else 0.0,
        },
    }


def aggregates(db) -> dict:
    """Cached per-topic rollups. Fails soft: a computation error returns empty
    stats so a page can still render its definition and its songs."""
    now = _time.monotonic()
    cached = _agg_cache["data"]
    if cached is not None and now < _agg_cache["expires"]:
        return cached
    try:
        data = _compute(db)
    except Exception:
        logger.exception("topic aggregates failed; serving empty stats")
        return {"topics": {}, "library": {"songs": 0, "avg_charge": None,
                                          "contaminated": 0, "contaminated_pct": 0.0}}
    _agg_cache["data"] = data
    _agg_cache["expires"] = now + _AGG_TTL
    return data


def _song_row(r) -> dict:
    return {
        "id": r.id,
        # Joined from song_slugs rather than minted here: a read path should not
        # write. A song with no slug row yet falls back to the query form, which
        # song.html already resolves.
        "slug": getattr(r, "slug", None) or "",
        "title": r.title,
        "artist": r.artist,
        "charge_value": r.charge_value,
        "rubric_color": r.rubric_color,
        "tier_label": COLOR_LABELS.get(r.rubric_color, "") if r.rubric_color else "",
        "tier_hex": COLOR_HEX.get(r.rubric_color, "#999") if r.rubric_color else "#999",
        "deadpan_line": getattr(r, "deadpan_line", None),
        "contaminated": bool(getattr(r, "contaminated", False)),
        "dominant": (r.dominant_topic or "") if hasattr(r, "dominant_topic") else None,
    }


def songs_for_topic(db, slug: str, *, limit: int = SONGS_PER_PAGE,
                    offset: int = 0, dominant_only: bool = False) -> list[dict]:
    """The evidence under the finding, charge-descending.

    Matched with jsonb_exists rather than the `?` operator: `?` is a paramstyle
    marker in several DBAPIs and would be eaten before Postgres saw it.
    """
    dom_clause = "AND (s.topics::jsonb ->> 0) = :slug" if dominant_only else ""
    rows = db.execute(text(f"""
        SELECT s.id, s.title, s.artist, s.charge_value, s.rubric_color,
               s.deadpan_line, s.contaminated, sl.slug,
               (s.topics::jsonb ->> 0) AS dominant_topic
        {_POPULATION_LINKED}
          AND jsonb_exists(s.topics::jsonb, :slug)
          {dom_clause}
        ORDER BY s.charge_value DESC, s.title ASC
        LIMIT :limit OFFSET :offset
    """), {"slug": slug, "limit": limit, "offset": offset}).fetchall()
    return [_song_row(r) for r in rows]


def extremes_for_topic(db, slug: str) -> dict:
    """The highest and lowest reading carrying this topic, named. Two rows, not
    a list: the point is the span, and the span needs its ends."""
    def _end(direction: str):
        rows = db.execute(text(f"""
            SELECT s.id, s.title, s.artist, s.charge_value, s.rubric_color,
                   s.deadpan_line, s.contaminated, sl.slug,
                   (s.topics::jsonb ->> 0) AS dominant_topic
            {_POPULATION_LINKED}
              AND jsonb_exists(s.topics::jsonb, :slug)
            ORDER BY s.charge_value {direction}, s.title ASC
            LIMIT 1
        """), {"slug": slug}).fetchall()
        return _song_row(rows[0]) if rows else None

    return {"highest": _end("DESC"), "lowest": _end("ASC")}


def topic_meta(db, slug: str) -> Optional[dict]:
    """The taxonomy's own record of a topic: label, definition, and where it
    sits. Returns None when the slug is not a real topic."""
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    meta = hierarchy.get("topics", {}).get(slug)
    if not meta:
        return None
    theme_labels = {t["slug"]: t["label"] for t in hierarchy.get("themes", [])}
    primary = meta.get("primary")
    row = db.execute(text(
        "SELECT scope, examples FROM ether_topics WHERE slug = :slug"
    ), {"slug": slug}).first()
    examples = []
    if row is not None and row.examples:
        try:
            examples = row.examples if isinstance(row.examples, list) else json.loads(row.examples)
        except (ValueError, TypeError):
            examples = []
    return {
        "slug": slug,
        "label": meta.get("label") or slug.replace("-", " "),
        # The tagger's own scope text. It was written as an instruction to the
        # classifier, so treat it as provisional reader copy until the
        # public_scope pass lands (see the build plan).
        "scope": (row.scope if row is not None else None),
        "examples": examples,
        "theme": {"slug": primary, "label": theme_labels.get(primary, primary)}
        if primary else None,
        "also": [
            {"slug": s, "label": theme_labels.get(s, s)}
            for s in (meta.get("also") or [])
        ],
    }


def siblings(db, slug: str, theme_slug: Optional[str]) -> list[dict]:
    """Other topics under the same theme. This is what makes the topic pages a
    connected set instead of 32 islands."""
    if not theme_slug:
        return []
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    stats = aggregates(db)["topics"]
    out = []
    for s, meta in hierarchy.get("topics", {}).items():
        if s == slug or meta.get("primary") != theme_slug:
            continue
        out.append({
            "slug": s,
            "label": meta.get("label") or s.replace("-", " "),
            "songs": stats.get(s, {}).get("songs", 0),
            "avg_charge": stats.get(s, {}).get("avg_charge"),
        })
    out.sort(key=lambda t: -t["songs"])
    return out


# --- themes: the parent of topics ------------------------------------------
#
# A theme is not just a bag of its topics. A song can carry two topics under
# the same theme (romance AND longing, say), so summing the topic counts
# double-counts songs. Every figure on a theme page therefore counts DISTINCT
# songs, and the sum of the topic counts is reported separately as "credits",
# which is a different and honest number.

def theme_meta(db, slug: str) -> Optional[dict]:
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    for t in hierarchy.get("themes", []):
        if t["slug"] == slug:
            return {"slug": t["slug"], "label": t["label"]}
    return None


def _theme_topic_slugs(db, slug: str) -> tuple[list[str], list[str]]:
    """(primary, secondary). Primary topics belong to the theme; secondary ones
    are filed elsewhere but touch it, which is a reach worth showing."""
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    primary, secondary = [], []
    for s, meta in hierarchy.get("topics", {}).items():
        if meta.get("primary") == slug:
            primary.append(s)
        elif slug in (meta.get("also") or []):
            secondary.append(s)
    return primary, secondary


def _theme_aggregate(db, slugs: list[str]) -> dict:
    """Distinct-song rollup across a set of topics. Slugs are joined into one
    string and split server-side rather than passed as an array, so no DBAPI
    has to adapt a list; topic slugs are kebab-case and can never contain a
    comma."""
    if not slugs:
        return {"songs": 0, "avg_charge": None, "min_charge": None,
                "max_charge": None, "contaminated": 0, "contaminated_pct": 0.0,
                "tiers": {}}
    row = db.execute(text(f"""
        SELECT COUNT(*) AS songs,
               ROUND(AVG(s.charge_value)) AS avg_charge,
               MIN(s.charge_value) AS min_charge,
               MAX(s.charge_value) AS max_charge,
               COUNT(*) FILTER (WHERE s.contaminated) AS contaminated,
               COUNT(*) FILTER (WHERE s.rubric_color = 'violet') AS n_violet,
               COUNT(*) FILTER (WHERE s.rubric_color = 'blue')   AS n_blue,
               COUNT(*) FILTER (WHERE s.rubric_color = 'green')  AS n_green,
               COUNT(*) FILTER (WHERE s.rubric_color = 'orange') AS n_orange,
               COUNT(*) FILTER (WHERE s.rubric_color = 'red')    AS n_red
        {_POPULATION}
          AND jsonb_exists_any(s.topics::jsonb, string_to_array(:slugs, ','))
    """), {"slugs": ",".join(slugs)}).first()
    n = int(row.songs or 0)
    return {
        "songs": n,
        "avg_charge": int(row.avg_charge) if row.avg_charge is not None else None,
        "min_charge": int(row.min_charge) if row.min_charge is not None else None,
        "max_charge": int(row.max_charge) if row.max_charge is not None else None,
        "contaminated": int(row.contaminated or 0),
        "contaminated_pct": round(100.0 * int(row.contaminated or 0) / n, 1) if n else 0.0,
        "tiers": {
            "violet": int(row.n_violet), "blue": int(row.n_blue),
            "green": int(row.n_green), "orange": int(row.n_orange),
            "red": int(row.n_red),
        },
    }


def theme_extremes(db, slugs: list[str]) -> dict:
    if not slugs:
        return {"highest": None, "lowest": None}

    def _end(direction: str):
        rows = db.execute(text(f"""
            SELECT s.id, s.title, s.artist, s.charge_value, s.rubric_color,
                   s.deadpan_line, s.contaminated, sl.slug,
                   (s.topics::jsonb ->> 0) AS dominant_topic
            {_POPULATION_LINKED}
              AND jsonb_exists_any(s.topics::jsonb, string_to_array(:slugs, ','))
            ORDER BY s.charge_value {direction}, s.title ASC
            LIMIT 1
        """), {"slugs": ",".join(slugs)}).fetchall()
        return _song_row(rows[0]) if rows else None

    return {"highest": _end("DESC"), "lowest": _end("ASC")}


def theme_detail(db, slug: str) -> Optional[dict]:
    meta = theme_meta(db, slug)
    if meta is None:
        return None
    primary, secondary = _theme_topic_slugs(db, slug)
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    stats_by_topic = aggregates(db)["topics"]

    def _topic_list(slugs):
        out = []
        for s in slugs:
            m = hierarchy["topics"].get(s, {})
            st = stats_by_topic.get(s, {})
            out.append({
                "slug": s,
                "label": m.get("label") or s.replace("-", " "),
                "songs": st.get("songs", 0),
                "avg_charge": st.get("avg_charge"),
                "contaminated_pct": st.get("contaminated_pct", 0.0),
            })
        out.sort(key=lambda t: -t["songs"])
        return out

    topics_out = _topic_list(primary)
    agg = _theme_aggregate(db, primary)
    # A song registers ONCE in a theme however many of its topics land here, so
    # the theme's song count is lower than the sum of its topic counts. That is
    # the rule, not a rounding artifact: `credits` is carried only so a caller
    # can explain the arithmetic, never as a second way to count songs.
    credits = sum(t["songs"] for t in topics_out)
    return {
        "theme": meta,
        "topics": topics_out,
        "also_topics": _topic_list(secondary),
        "stats": agg,
        "credits": credits,
        "library": aggregates(db)["library"],
        "extremes": theme_extremes(db, primary),
        "related_themes": related_themes(db, slug),
    }


def related_themes(db, slug: str) -> list[dict]:
    """Themes genuinely connected to this one, and by how many topics.

    A crossing exists when a topic sits primarily under one theme while naming
    the other as a secondary. Listing every other theme instead would be a
    navigation menu wearing the word "related", and the taxonomy already
    records which themes actually touch. Themes with no crossing are left out
    rather than padded in; /topics/ remains the full index.
    """
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    labels = {t["slug"]: t["label"] for t in hierarchy.get("themes", [])}
    counts: dict[str, int] = {}
    for _, meta in hierarchy.get("topics", {}).items():
        primary = meta.get("primary")
        also = meta.get("also") or []
        if primary == slug:
            for other in also:
                if other != slug:
                    counts[other] = counts.get(other, 0) + 1
        elif slug in also and primary and primary != slug:
            counts[primary] = counts.get(primary, 0) + 1
    # Two crossings, not one. At a single shared topic out of 32, five of the
    # eight other themes qualified for Meaning & Mortality alone, and a section
    # that links most of the set is a menu wearing the word "related".
    out = [
        {"slug": s, "label": labels.get(s, s), "shared_topics": n}
        for s, n in counts.items() if s in labels and n >= MIN_THEME_CROSSINGS
    ]
    out.sort(key=lambda t: (-t["shared_topics"], t["label"]))
    return out


def index(db) -> dict:
    """Themes in taxonomy order, each with its topics and their counts."""
    hierarchy = ether_taxonomy.topic_hierarchy(db)
    agg = aggregates(db)
    stats = agg["topics"]
    themes_out = []
    for theme in hierarchy.get("themes", []):
        topics = []
        for slug, meta in hierarchy.get("topics", {}).items():
            if meta.get("primary") != theme["slug"]:
                continue
            st = stats.get(slug, {})
            topics.append({
                "slug": slug,
                "label": meta.get("label") or slug.replace("-", " "),
                "songs": st.get("songs", 0),
                "avg_charge": st.get("avg_charge"),
            })
        topics.sort(key=lambda t: -t["songs"])
        themes_out.append({
            "slug": theme["slug"],
            "label": theme["label"],
            "topics": topics,
            "songs": sum(t["songs"] for t in topics),
        })
    return {"themes": themes_out, "library": agg["library"]}


def detail(db, slug: str, *, offset: int = 0, dominant_only: bool = False) -> Optional[dict]:
    """Everything a topic page renders, in one payload."""
    meta = topic_meta(db, slug)
    if meta is None:
        return None
    agg = aggregates(db)
    stats = agg["topics"].get(slug, {
        "songs": 0, "avg_charge": None, "dominant_songs": 0,
        "avg_dominant": None, "avg_incidental": None, "contaminated": 0,
        "contaminated_pct": 0.0, "min_charge": None, "max_charge": None,
        "tiers": {},
    })

    # Only claim a dominant/incidental split when there is one to claim.
    dom, inc = stats.get("avg_dominant"), stats.get("avg_incidental")
    split = None
    if dom is not None and inc is not None and abs(dom - inc) >= DOMINANT_DELTA_FLOOR:
        split = {
            "avg_dominant": dom,
            "avg_incidental": inc,
            "delta": dom - inc,
            "dominant_songs": stats.get("dominant_songs", 0),
        }

    return {
        "topic": meta,
        "stats": stats,
        "dominant_split": split,
        "library": agg["library"],
        "extremes": extremes_for_topic(db, slug),
        "siblings": siblings(db, slug, (meta.get("theme") or {}).get("slug")),
        "songs": songs_for_topic(db, slug, offset=offset, dominant_only=dominant_only),
        "offset": offset,
        "limit": SONGS_PER_PAGE,
        "dominant_only": dominant_only,
    }
