"""Unified song search — one query shape, two consumers.

Merges the four song tables (compass / library / submitted / stream) into a
single filtered + sorted + paginated result set. Powers both the admin
`all_songs` DB tab (full PII + unlimited) and the public `/api/songs/search`
endpoint (PII-scrubbed + free-tier cap).

In-memory merge is fine at current scale (~2k rows total). If the corpus
grows past ~50k this wants a SQL UNION ALL with ORDER BY + LIMIT pushed
into the database.
"""

import re
from datetime import datetime, date
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import (
    CompassSong, LibrarySong, SubmittedSong, StreamSong, SongSlug,
)


_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_for_search(s: str | None) -> str:
    """Lowercase + canonicalize ampersand-vs-and + strip non-alphanumeric.

    "T.G.A." -> "tga"; "don't stop me now" -> "dontstopmenow";
    "feat." -> "feat"; "AC/DC" -> "acdc". "&" is replaced with "and"
    before stripping, so "Ask & Tell" and "Ask and Tell" both normalize
    to "askandtell" — a search for either form finds both.
    """
    if not s:
        return ""
    return _NORM_RE.sub("", s.lower().replace("&", "and"))


_SONG_MODELS = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
    "stream": StreamSong,
}

_PII_FIELDS = {"ip_address", "device_id", "email", "confidence", "why_calibration"}

_COMMON_FIELDS = [
    "id", "title", "artist", "rubric_color", "charge_value",
    "contaminated", "contamination_note", "charge_summary", "effects_prose",
]


def _serialize_common(row, source: str, include_pii: bool) -> dict:
    out: dict[str, Any] = {"song_source": source}
    for field in _COMMON_FIELDS:
        if hasattr(row, field):
            v = getattr(row, field, None)
            if isinstance(v, (datetime, date)):
                v = v.isoformat()
            out[field] = v

    # Source-specific fields; public stays clean, admin gets everything
    if source == "compass":
        out["year"] = getattr(row, "year", None)
        out["chart_position"] = getattr(row, "chart_position", None)
        out["chart_source"] = getattr(row, "chart_source", None)
        if include_pii:
            out["why_calibration"] = getattr(row, "why_calibration", None)
            out["instrumental"] = getattr(row, "instrumental", None)
    elif source == "library":
        out["album_id"] = getattr(row, "album_id", None)
        out["track_number"] = getattr(row, "track_number", None)
        out["source_tag"] = getattr(row, "source", None)
        v = getattr(row, "created_at", None)
        out["created_at"] = v.isoformat() if isinstance(v, (datetime, date)) else v
    elif source == "submitted":
        v = getattr(row, "submitted_at", None)
        out["created_at"] = v.isoformat() if isinstance(v, (datetime, date)) else v
        out["source_tag"] = getattr(row, "source", None)
        if include_pii:
            out["ip_address"] = getattr(row, "ip_address", None)
            out["confidence"] = getattr(row, "confidence", None)
    elif source == "stream":
        v = getattr(row, "created_at", None)
        out["created_at"] = v.isoformat() if isinstance(v, (datetime, date)) else v
        out["note"] = getattr(row, "note", None)
        out["source_url"] = getattr(row, "source_url", None)
        out["source_platform"] = getattr(row, "source_platform", None)
        out["status"] = getattr(row, "status", None)
        if include_pii:
            out["confidence"] = getattr(row, "confidence", None)

    # Trim PII from the common pass — in case any Model has a field we listed
    if not include_pii:
        for k in list(out.keys()):
            if k in _PII_FIELDS:
                out.pop(k, None)

    return out


def _attach_slugs(items: list[dict], db: Session) -> None:
    """Batch-attach song slugs so each row has a URL. One query per source."""
    by_source: dict[str, list[int]] = {}
    for it in items:
        by_source.setdefault(it["song_source"], []).append(it["id"])
    slug_map: dict[tuple[str, int], str] = {}
    for src, ids in by_source.items():
        rows = (
            db.query(SongSlug.song_source, SongSlug.song_id, SongSlug.slug)
            .filter(SongSlug.song_source == src)
            .filter(SongSlug.song_id.in_(ids))
            .all()
        )
        for s, sid, slug in rows:
            slug_map[(s, sid)] = slug
    for it in items:
        it["slug"] = slug_map.get((it["song_source"], it["id"]))


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
    attach_slugs: bool = True,
) -> dict:
    """Run a unified search across the four song tables and return a paged,
    sorted, filtered list of rows serialized with or without PII.
    """
    sources = [source] if source in _SONG_MODELS else list(_SONG_MODELS.keys())

    # Normalize the query once. Post-SQL filter: we strip non-alphanumeric on
    # both sides so "tga" matches "T.G.A.", "dontstopmenow" matches "Don't
    # Stop Me Now", etc. SQL ILIKE can't do this without a precomputed column.
    q_norm = normalize_for_search(q) if q else ""

    merged: list[tuple[str, Any]] = []
    for src in sources:
        Model = _SONG_MODELS[src]
        query = db.query(Model)
        if calibrated_only:
            query = query.filter(Model.charge_value.isnot(None))
        if tier:
            query = query.filter(Model.rubric_color == tier)
        if charge_min is not None:
            query = query.filter(Model.charge_value >= charge_min)
        if charge_max is not None:
            query = query.filter(Model.charge_value <= charge_max)
        if contaminated is not None:
            query = query.filter(Model.contaminated == contaminated)
        if title_contains:
            query = query.filter(Model.title.ilike(f"%{title_contains}%"))
        if artist_contains:
            query = query.filter(Model.artist.ilike(f"%{artist_contains}%"))
        # year filters only apply to compass (only table with a `year` col)
        if src == "compass":
            if year_min is not None:
                query = query.filter(Model.year >= year_min)
            if year_max is not None:
                query = query.filter(Model.year <= year_max)
        for row in query.all():
            if q_norm:
                title_n = normalize_for_search(getattr(row, "title", None))
                artist_n = normalize_for_search(getattr(row, "artist", None))
                if q_norm not in title_n and q_norm not in artist_n:
                    continue
            merged.append((src, row))

    # Sort
    def _sort_key(pair):
        src, row = pair
        if sort_by == "charge_value":
            return (getattr(row, "charge_value", None) is None, getattr(row, "charge_value", 0))
        if sort_by == "title":
            return ((getattr(row, "title", "") or "").lower(),)
        if sort_by == "artist":
            return ((getattr(row, "artist", "") or "").lower(),)
        if sort_by == "rubric_color":
            return (getattr(row, "rubric_color", "") or "",)
        if sort_by == "year":
            return (getattr(row, "year", None) is None, getattr(row, "year", 0) or 0)
        # created_at: library/stream use created_at, submitted uses submitted_at.
        # compass has neither — fall back to its `year` so a 2024 chart hit
        # still sorts before a 1985 entry under "newest first".
        dt = getattr(row, "created_at", None) or getattr(row, "submitted_at", None)
        if dt:
            return (False, dt)
        yr = getattr(row, "year", None)
        if yr:
            try:
                return (False, datetime(int(yr), 1, 1))
            except (ValueError, TypeError):
                pass
        return (True, datetime.min)

    merged.sort(key=_sort_key, reverse=(sort_dir == "desc"))

    total = len(merged)
    page = merged[offset:offset + limit]
    items = [_serialize_common(row, src, include_pii) for src, row in page]

    if attach_slugs and items:
        _attach_slugs(items, db)

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }
