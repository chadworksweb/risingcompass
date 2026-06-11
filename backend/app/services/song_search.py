"""Unified song search -- one query shape, two consumers.

Searches the unified `songs` table (the whole Library) for both the admin
`all_songs` DB tab (full PII + unlimited) and the public `/api/songs/search`
endpoint (PII-scrubbed + free-tier cap). Songs are atomic, so a song that used
to be several legacy rows (e.g. "A Bar Song" 2024 + 2025) now appears ONCE.
Year/chart context comes from chart_appearances; ingestion `source` filtering
maps to song_ingestions.method.

In-memory post-filter for the normalized query is fine at current scale (~1k
rows). If the corpus grows past ~50k this wants a precomputed normalized column.
"""

import re
from datetime import datetime, date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Song, ChartAppearance, SongIngestion, SongSlug


_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_for_search(s: str | None) -> str:
    """Lowercase + canonicalize ampersand-vs-and + strip non-alphanumeric.

    "T.G.A." -> "tga"; "don't stop me now" -> "dontstopmenow"; "AC/DC" -> "acdc".
    "&" -> "and" before stripping, so "Ask & Tell" and "Ask and Tell" both
    normalize to "askandtell". Also the artist component of the canonical_key
    (see app.services.song_identity).
    """
    if not s:
        return ""
    return _NORM_RE.sub("", s.lower().replace("&", "and"))


_PII_FIELDS = {"ip_address", "device_id", "email", "confidence"}
_PROSE_FIELDS = {"listener_effects_prose", "societal_effects_prose"}

# Legacy `source` filter value -> ingestion method, so the admin source filter
# keeps working against the unified table.
_METHOD_FOR_SOURCE = {
    "compass": "chart_reading", "library": "catalog_backfill",
    "submitted": "lyrical_charger", "stream": "stream",
}


def _serialize_song(song: Song, year, include_pii: bool, include_prose: bool) -> dict:
    out: dict[str, Any] = {
        "song_source": "songs",  # unified: one Library table
        "id": song.id,
        "title": song.title,
        "artist": song.artist,
        "rubric_color": song.rubric_color,
        "charge_value": song.charge_value,
        "contaminated": bool(song.contaminated),
        "contamination_note": song.contamination_note,
        "charge_summary": song.charge_summary,
        "year": year,  # most-recent chart appearance year (None if non-charting)
        "album_id": song.album_id,
        "track_number": song.track_number,
        "created_at": song.created_at.isoformat()
        if isinstance(song.created_at, (datetime, date)) else song.created_at,
    }
    if include_prose:
        out["listener_effects_prose"] = song.listener_effects_prose
        out["societal_effects_prose"] = song.societal_effects_prose
    if include_pii:
        out["confidence"] = song.confidence
        out["instrumental"] = bool(song.instrumental)
        out["calibration_method"] = song.canonical_calibration_method
    return out


def _attach_slugs(items: list[dict], db: Session) -> None:
    """Batch-attach the canonical URL slug per song (one query). Falls back to a
    generated slug so the page never lacks a link."""
    from app.services.artist_utils import generate_song_slug
    ids = [it["id"] for it in items]
    slug_map: dict[int, str] = {}
    if ids:
        rows = (
            db.query(SongSlug.song_id, SongSlug.slug)
            .filter(SongSlug.song_id.in_(ids))
            .order_by(SongSlug.id.asc())
            .all()
        )
        for sid, slug in rows:
            slug_map.setdefault(sid, slug)  # first (canonical) slug per song
    for it in items:
        it["slug"] = slug_map.get(it["id"]) or generate_song_slug(it["title"], it["artist"] or "")


def _attach_artist_slugs(items: list[dict], db: Session) -> None:
    """Batch-attach artist slugs (one query). Multi-artist credits resolve to the primary."""
    from app.services.artist_utils import resolve_artist_slugs, normalize_artist_name
    slug_map = resolve_artist_slugs([it.get("artist") for it in items], db)
    for it in items:
        primary = normalize_artist_name(it.get("artist") or "").lower()
        it["artist_slug"] = slug_map.get(primary)


def search_unified(
    db: Session,
    *,
    q: str | None = None,
    source: str | None = None,
    tier: str | None = None,
    charge_min: int | None = None,
    charge_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    contaminated: bool | None = None,
    title_contains: str | None = None,
    artist_contains: str | None = None,
    calibrated_only: bool = True,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 25,
    include_pii: bool = False,
    include_prose: bool = True,
    attach_slugs: bool = True,
) -> dict:
    """Search the unified songs table; return a paged, sorted, filtered list."""
    query = db.query(Song)
    if calibrated_only:
        query = query.filter(Song.charge_value.isnot(None))
    if tier:
        query = query.filter(Song.rubric_color == tier)
    if charge_min is not None:
        query = query.filter(Song.charge_value >= charge_min)
    if charge_max is not None:
        query = query.filter(Song.charge_value <= charge_max)
    if contaminated is not None:
        query = query.filter(Song.contaminated == contaminated)
    if title_contains:
        query = query.filter(Song.title.ilike(f"%{title_contains}%"))
    if artist_contains:
        query = query.filter(Song.artist.ilike(f"%{artist_contains}%"))
    # year filter -> songs with a chart appearance in range
    if year_min is not None or year_max is not None:
        sub = db.query(ChartAppearance.song_id)
        if year_min is not None:
            sub = sub.filter(ChartAppearance.year >= year_min)
        if year_max is not None:
            sub = sub.filter(ChartAppearance.year <= year_max)
        query = query.filter(Song.id.in_(sub.subquery()))
    # legacy `source` filter -> ingestion method
    if source and source in _METHOD_FOR_SOURCE:
        ing = db.query(SongIngestion.song_id).filter(
            SongIngestion.method == _METHOD_FOR_SOURCE[source])
        query = query.filter(Song.id.in_(ing.subquery()))

    rows = query.all()

    # Normalized post-filter (strip non-alphanumeric both sides so "tga" matches
    # "T.G.A.", "dontstopmenow" matches "Don't Stop Me Now").
    if q:
        q_norm = normalize_for_search(q)
        if q_norm:
            rows = [
                r for r in rows
                if q_norm in normalize_for_search(r.title)
                or q_norm in normalize_for_search(r.artist)
            ]

    # Most-recent appearance year per matched song (for the `year` field + sort).
    ids = [r.id for r in rows]
    year_map: dict[int, int] = {}
    if ids:
        for sid, yr in (
            db.query(ChartAppearance.song_id, func.max(ChartAppearance.year))
            .filter(ChartAppearance.song_id.in_(ids))
            .group_by(ChartAppearance.song_id)
            .all()
        ):
            year_map[sid] = yr

    def _sort_key(row: Song):
        if sort_by == "charge_value":
            return (row.charge_value is None, row.charge_value or 0)
        if sort_by == "title":
            return ((row.title or "").lower(),)
        if sort_by == "artist":
            return ((row.artist or "").lower(),)
        if sort_by == "rubric_color":
            return (row.rubric_color or "",)
        if sort_by == "year":
            y = year_map.get(row.id)
            return (y is None, y or 0)
        # created_at: fall back to the appearance year so chart hits still order.
        if row.created_at:
            return (False, row.created_at)
        y = year_map.get(row.id)
        if y:
            try:
                return (False, datetime(int(y), 1, 1))
            except (ValueError, TypeError):
                pass
        return (True, datetime.min)

    rows.sort(key=_sort_key, reverse=(sort_dir == "desc"))

    total = len(rows)
    page = rows[offset:offset + limit]
    items = [_serialize_song(r, year_map.get(r.id), include_pii, include_prose) for r in page]

    if not include_pii:
        for it in items:
            for k in _PII_FIELDS:
                it.pop(k, None)
    if not include_prose:
        for it in items:
            for k in _PROSE_FIELDS:
                it.pop(k, None)

    if attach_slugs and items:
        _attach_slugs(items, db)
        _attach_artist_slugs(items, db)

    return {"total": total, "offset": offset, "limit": limit, "items": items}
