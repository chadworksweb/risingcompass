"""Artist Trajectory API — public endpoints for artist search and trajectory display."""

import logging
import time
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, text

from app.database import SessionLocal
from app.models import (
    Artist, Release, ReleaseSong, SongArtist,
    CompassSong, LibrarySong, SubmittedSong, SongSlug,
)
from app.constants import COLOR_LABELS, COLOR_HEX
from app.services.artist_utils import (
    count_songs_by_artist, derive_tier, generate_song_slug,
)
from app.services.compass_calc import charge_to_degree
from app.services.charge_calc import degree_to_charge


_SONG_MODEL_MAP = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/artists", tags=["artists"])


# --- Search-result cache ---------------------------------------------------
# Typeahead hits the same prefixes repeatedly ("th", "the", "th"...). A short
# TTL in-process cache collapses these into a single DB hit per (q_lower, limit)
# inside the window. 60s is short enough that newly-bootstrapped artists appear
# quickly, long enough to absorb a typing burst.

_SEARCH_CACHE_TTL = 60.0
_SEARCH_CACHE_MAX = 256
_search_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_search_cache_lock = Lock()


def _search_cache_get(key: tuple[str, int]) -> Optional[dict]:
    with _search_cache_lock:
        entry = _search_cache.get(key)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > _SEARCH_CACHE_TTL:
            _search_cache.pop(key, None)
            return None
        return value


def _search_cache_set(key: tuple[str, int], value: dict) -> None:
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX:
            # Evict the oldest third — simple, no heap needed at this size.
            to_drop = sorted(_search_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in to_drop[: _SEARCH_CACHE_MAX // 3]:
                _search_cache.pop(k, None)
        _search_cache[key] = (time.time(), value)


# --- Per-slug endpoint cache ----------------------------------------------
# Applies to /summary, /trajectory, /releases, /top-songs. Keyed by a tuple
# (endpoint_name, slug, extra) so the same slug's different endpoints don't
# collide. Short TTL — releases and catalog charges change rarely relative
# to how often an artist page is loaded.

_ARTIST_CACHE_TTL = 60.0
_ARTIST_CACHE_MAX = 512
_artist_cache: dict[tuple, tuple[float, dict]] = {}
_artist_cache_lock = Lock()


def _artist_cache_get(key: tuple) -> Optional[dict]:
    with _artist_cache_lock:
        entry = _artist_cache.get(key)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > _ARTIST_CACHE_TTL:
            _artist_cache.pop(key, None)
            return None
        return value


def _artist_cache_set(key: tuple, value: dict) -> None:
    with _artist_cache_lock:
        if len(_artist_cache) >= _ARTIST_CACHE_MAX:
            to_drop = sorted(_artist_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in to_drop[: _ARTIST_CACHE_MAX // 3]:
                _artist_cache.pop(k, None)
        _artist_cache[key] = (time.time(), value)


@router.get("/search")
def artist_search(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=50),
):
    """Search artists by name. Also returns unindexed matches from song tables.

    Two SQL statements total: one aggregated query for indexed artists + their
    release/song counts (via scalar subqueries), one UNION query for unindexed
    names across the three song tables. Prior implementation ran 60-150+ round
    trips; this runs 2. Results are cached for 60s per (q_lower, limit).

    Counts use string-match only (no song_artists credit-path dedupe). The
    artist page itself still reports the accurate count via
    `count_songs_by_artist`; a small skew in the typeahead badge is acceptable.
    """
    q_lower = q.strip().lower()
    cache_key = (q_lower, limit)
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    like_pat = f"%{q_lower}%"

    db = SessionLocal()
    try:
        # --- Indexed artists, aggregated in one round trip -----------------
        indexed_sql = text(
            """
            WITH matched AS (
                SELECT id, name, slug
                FROM artists
                WHERE lower(name) LIKE :pat
                LIMIT :limit
            )
            SELECT
                m.name,
                m.slug,
                (SELECT COUNT(*) FROM releases r WHERE r.artist_id = m.id)
                    AS release_count,
                (
                    (SELECT COUNT(*) FROM compass_songs
                       WHERE lower(artist) = lower(m.name)
                         AND charge_value IS NOT NULL)
                  + (SELECT COUNT(*) FROM library_songs
                       WHERE lower(artist) = lower(m.name)
                         AND charge_value IS NOT NULL)
                  + (SELECT COUNT(*) FROM submitted_songs
                       WHERE lower(artist) = lower(m.name)
                         AND charge_value IS NOT NULL)
                ) AS song_count
            FROM matched m
            """
        )
        indexed_rows = db.execute(
            indexed_sql, {"pat": like_pat, "limit": limit}
        ).all()

        results: list[dict] = []
        indexed_lower: set[str] = set()
        for name, slug, release_count, song_count in indexed_rows:
            results.append({
                "name": name,
                "slug": slug,
                "release_count": int(release_count or 0),
                "calibrated_song_count": int(song_count or 0),
                "indexed": True,
            })
            indexed_lower.add(name.lower())

        # --- Unindexed names, one UNION query across the 3 song tables ----
        if len(results) < limit:
            remaining = limit - len(results)
            unindexed_sql = text(
                """
                SELECT MIN(artist) AS name, COUNT(*) AS song_count
                FROM (
                    SELECT artist FROM compass_songs
                     WHERE lower(artist) LIKE :pat
                       AND charge_value IS NOT NULL
                    UNION ALL
                    SELECT artist FROM library_songs
                     WHERE lower(artist) LIKE :pat
                       AND charge_value IS NOT NULL
                    UNION ALL
                    SELECT artist FROM submitted_songs
                     WHERE lower(artist) LIKE :pat
                       AND charge_value IS NOT NULL
                       AND artist IS NOT NULL
                )
                GROUP BY lower(artist)
                ORDER BY name
                LIMIT :limit
                """
            )
            # Fetch a bit more than `remaining` to absorb rows that filter out
            # because they're already in `indexed_lower`.
            unindexed_rows = db.execute(
                unindexed_sql,
                {"pat": like_pat, "limit": remaining + len(indexed_lower) + 10},
            ).all()

            added = 0
            for name, song_count in unindexed_rows:
                if added >= remaining:
                    break
                if not name or name.lower() in indexed_lower:
                    continue
                if song_count and song_count > 0:
                    results.append({
                        "name": name,
                        "slug": None,
                        "release_count": 0,
                        "calibrated_song_count": int(song_count),
                        "indexed": False,
                    })
                    indexed_lower.add(name.lower())
                    added += 1

        payload = {"results": results}
        _search_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


def _song_charges_for_artist(artist_id: int, db) -> list[int]:
    """All per-song charge_values credited to this artist, in one round trip.

    Two paths, deduplicated: songs on releases owned by this artist
    (release_songs → releases.artist_id), and songs where this artist is
    directly credited via song_artists (collabs + features on other artists'
    releases). Prior implementation ran 6 queries (2 paths × 3 source tables);
    this runs 1 — a CTE collects the (source, song_id) set for this artist
    via UNION (dedupes across the two paths), then joins each source table
    once with UNION ALL to pull their charge_values.
    """
    sql = text(
        """
        WITH artist_song_ids AS (
            SELECT rs.song_source AS src, rs.song_id AS sid
              FROM release_songs rs
              JOIN releases r ON r.id = rs.release_id
             WHERE r.artist_id = :aid
            UNION
            SELECT sa.song_source AS src, sa.song_id AS sid
              FROM song_artists sa
             WHERE sa.artist_id = :aid
        )
        SELECT cs.charge_value
          FROM compass_songs cs
          JOIN artist_song_ids a ON a.src = 'compass' AND a.sid = cs.id
         WHERE cs.charge_value IS NOT NULL
        UNION ALL
        SELECT ls.charge_value
          FROM library_songs ls
          JOIN artist_song_ids a ON a.src = 'library' AND a.sid = ls.id
         WHERE ls.charge_value IS NOT NULL
        UNION ALL
        SELECT ss.charge_value
          FROM submitted_songs ss
          JOIN artist_song_ids a ON a.src = 'submitted' AND a.sid = ss.id
         WHERE ss.charge_value IS NOT NULL
        """
    )
    rows = db.execute(sql, {"aid": artist_id}).all()
    return [r[0] for r in rows if r[0] is not None]


@router.get("/{slug}/summary")
def artist_summary(slug: str):
    """Lightweight summary — catalog charge, degree, tier, totals, breakdown.

    No release list, no song list. Keeps the artist page header fast to paint.
    """
    cache_key = ("summary", slug)
    cached = _artist_cache_get(cache_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        # Totals + tier breakdown — released only (release_date IS NOT NULL).
        # Unreleased rows exist in the DB but don't count toward the catalog:
        # they carry no release_date and sit in the "unreleased" bucket.
        release_rows = (
            db.query(Release.rubric_color)
            .filter(Release.artist_id == artist.id)
            .filter(Release.release_date.isnot(None))
            .all()
        )
        total_releases = len(release_rows)
        tier_breakdown = {"violet": 0, "blue": 0, "green": 0, "orange": 0, "red": 0}
        for (color,) in release_rows:
            if color in tier_breakdown:
                tier_breakdown[color] += 1

        total_unreleased = (
            db.query(func.count(Release.id))
            .filter(Release.artist_id == artist.id)
            .filter(Release.release_date.is_(None))
            .scalar()
        ) or 0

        # Catalog charge = mean of individual song charges (not mean-of-means)
        song_charges = _song_charges_for_artist(artist.id, db)
        total_calibrated = len(song_charges)

        catalog_charge = None
        catalog_degree = None
        catalog_tier = None
        catalog_tier_label = None
        catalog_tier_hex = None
        if song_charges:
            catalog_charge = round(sum(song_charges) / len(song_charges))
            catalog_degree = charge_to_degree(catalog_charge)
            catalog_tier = degree_to_charge(catalog_degree)
            catalog_tier_label = COLOR_LABELS.get(catalog_tier, "")
            catalog_tier_hex = COLOR_HEX.get(catalog_tier, "#999")

        payload = {
            "name": artist.name,
            "slug": artist.slug,
            "stats": {
                "total_releases": total_releases,
                "total_unreleased": total_unreleased,
                "total_calibrated_songs": total_calibrated,
                "catalog_charge": catalog_charge,
                "catalog_degree": catalog_degree,
                "catalog_tier": catalog_tier,
                "catalog_tier_label": catalog_tier_label,
                "catalog_tier_hex": catalog_tier_hex,
                "tier_breakdown": tier_breakdown,
            },
        }
        _artist_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


@router.get("/{slug}/trajectory")
def artist_trajectory_points(slug: str):
    """Released catalog formatted for the trajectory chart. No song resolution.

    Excludes releases with no release_date — those are "unreleased" and don't
    sit on the timeline.

    One SELECT — returns only fields stored on the Release row. Fast even for
    catalogs with hundreds of releases (Beatles etc).
    """
    cache_key = ("trajectory", slug)
    cached = _artist_cache_get(cache_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        rows = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .filter(Release.release_date.isnot(None))
            .order_by(
                Release.release_date.asc(),
                Release.title.asc(),
            )
            .all()
        )

        points = [
            {
                "id": r.id,
                "title": r.title,
                "release_type": r.release_type,
                "release_date": r.release_date.isoformat() if r.release_date else None,
                "release_year": r.release_year,
                "charge_value": r.charge_value,
                "rubric_color": r.rubric_color,
                "tier_label": COLOR_LABELS.get(r.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(r.rubric_color, "#999"),
            }
            for r in rows
        ]
        payload = {"points": points}
        _artist_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


@router.get("/{slug}/releases")
def artist_releases(
    slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    status: str = Query("released", pattern="^(released|unreleased|all)$"),
):
    """Paginated release list. Default newest-first for the right-column panel.

    status:
      released   — default: only releases with a release_date
      unreleased — only releases missing a release_date (sits in the
                   unreleased bucket on the artist page)
      all        — everything
    """
    cache_key = ("releases", slug, offset, limit, order, status)
    cached = _artist_cache_get(cache_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        base = db.query(Release).filter(Release.artist_id == artist.id)
        if status == "released":
            base = base.filter(Release.release_date.isnot(None))
        elif status == "unreleased":
            base = base.filter(Release.release_date.is_(None))

        total = base.with_entities(func.count(Release.id)).scalar() or 0

        if status == "unreleased":
            # No date to sort on — fall back to title.
            base = base.order_by(Release.title.asc())
        elif order == "desc":
            base = base.order_by(
                Release.release_date.desc(),
                Release.title.asc(),
            )
        else:
            base = base.order_by(
                Release.release_date.asc(),
                Release.title.asc(),
            )

        rows = base.offset(offset).limit(limit).all()

        items = [
            {
                "id": r.id,
                "title": r.title,
                "release_type": r.release_type,
                "release_date": r.release_date.isoformat() if r.release_date else None,
                "release_year": r.release_year,
                "charge_value": r.charge_value,
                "rubric_color": r.rubric_color,
                "tier_label": COLOR_LABELS.get(r.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(r.rubric_color, "#999"),
                "track_count": r.track_count,
                "calibrated_count": r.calibrated_count,
                "contamination_count": r.contamination_count,
            }
            for r in rows
        ]
        payload = {"items": items, "total": total, "offset": offset, "limit": limit}
        _artist_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


@router.get("/{slug}/top-songs")
def artist_top_songs(
    slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Songs across an artist's catalog ranked by charge_value DESC, paginated.

    Merges the three song source tables (compass / library / submitted) via
    release_songs → releases. Joins song_slugs so each item has a URL slug
    when one exists.
    """
    cache_key = ("top-songs", slug, offset, limit)
    cached = _artist_cache_get(cache_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        items: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for source, Model in _SONG_MODEL_MAP.items():
            columns = (
                Model.id,
                Model.title,
                Model.artist,
                Model.rubric_color,
                Model.charge_value,
                Model.contaminated,
            )
            # Path 1: songs on releases owned by this artist (release_songs).
            via_release = (
                db.query(*columns)
                .join(ReleaseSong, (ReleaseSong.song_source == source) & (ReleaseSong.song_id == Model.id))
                .join(Release, ReleaseSong.release_id == Release.id)
                .filter(Release.artist_id == artist.id)
                .filter(Model.charge_value.isnot(None))
                .distinct()
                .all()
            )
            # Path 2: songs where this artist is directly credited (collabs + features).
            via_credit = (
                db.query(*columns)
                .join(SongArtist, (SongArtist.song_source == source) & (SongArtist.song_id == Model.id))
                .filter(SongArtist.artist_id == artist.id)
                .filter(Model.charge_value.isnot(None))
                .distinct()
                .all()
            )
            for song_id, title, song_artist, rubric_color, charge_value, contaminated in list(via_release) + list(via_credit):
                key = (source, song_id)
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "song_source": source,
                    "song_id": song_id,
                    "title": title,
                    "artist": song_artist or artist.name,
                    "rubric_color": rubric_color,
                    "charge_value": charge_value,
                    "tier_label": COLOR_LABELS.get(rubric_color, ""),
                    "tier_hex": COLOR_HEX.get(rubric_color, "#999"),
                    "contaminated": bool(contaminated) if contaminated is not None else False,
                })

        # Rank merged list, then paginate. For typical catalogs (<=1000 songs)
        # this in-Python sort is cheaper than a SQL UNION ALL with ORDER BY.
        items.sort(key=lambda x: (x["charge_value"] is None, -(x["charge_value"] or 0)))
        total = len(items)
        page = items[offset:offset + limit]

        # Attach song_slugs in one lookup for the page we're returning
        if page:
            keys = [(it["song_source"], it["song_id"]) for it in page]
            # song_slugs indexes by (song_source, song_id) — query in one IN clause per source
            slug_map: dict[tuple, str] = {}
            for source in {k[0] for k in keys}:
                ids = [k[1] for k in keys if k[0] == source]
                rows = (
                    db.query(SongSlug.song_source, SongSlug.song_id, SongSlug.slug)
                    .filter(SongSlug.song_source == source)
                    .filter(SongSlug.song_id.in_(ids))
                    .all()
                )
                for s, sid, slug_value in rows:
                    slug_map[(s, sid)] = slug_value
            for it in page:
                it["slug"] = slug_map.get((it["song_source"], it["song_id"]))

        payload = {"items": page, "total": total, "offset": offset, "limit": limit}
        _artist_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


@router.get("/{slug}")
def artist_trajectory(slug: str):
    """Return full artist trajectory — releases sorted chronologically + stats."""
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        releases = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .order_by(
                Release.release_date.asc().nullslast(),
                Release.release_year.asc().nullslast(),
                Release.title.asc(),
            )
            .all()
        )

        trajectory = []
        all_song_charges = []
        tier_breakdown = {"violet": 0, "blue": 0, "green": 0, "orange": 0, "red": 0}

        for r in releases:
            # Collect individual song charges for catalog-level stats
            song_charges = _get_release_song_charges(r, db)
            all_song_charges.extend(song_charges)

            if r.rubric_color and r.rubric_color in tier_breakdown:
                tier_breakdown[r.rubric_color] += 1

            trajectory.append({
                "id": r.id,
                "title": r.title,
                "release_type": r.release_type,
                "release_date": r.release_date.isoformat() if r.release_date else None,
                "release_year": r.release_year,
                "charge_value": r.charge_value,
                "rubric_color": r.rubric_color,
                "tier_label": COLOR_LABELS.get(r.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(r.rubric_color, "#999"),
                "track_count": r.track_count,
                "calibrated_count": r.calibrated_count,
                "contamination_count": r.contamination_count,
            })

        # Catalog-level stats from individual song charges (not mean-of-means)
        catalog_charge = None
        catalog_tier = None
        catalog_tier_label = None
        catalog_tier_hex = None
        if all_song_charges:
            catalog_charge = round(sum(all_song_charges) / len(all_song_charges))
            catalog_tier, catalog_tier_label, catalog_tier_hex = derive_tier(catalog_charge)

        return {
            "name": artist.name,
            "slug": artist.slug,
            "trajectory": trajectory,
            "stats": {
                "total_releases": len(releases),
                "total_calibrated_songs": len(all_song_charges),
                "catalog_charge": catalog_charge,
                "catalog_tier": catalog_tier,
                "catalog_tier_label": catalog_tier_label,
                "catalog_tier_hex": catalog_tier_hex,
                "tier_breakdown": tier_breakdown,
            },
        }
    finally:
        db.close()


@router.get("/{slug}/songs")
def artist_songs(
    slug: str,
    release_id: Optional[int] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Paginated songs for an artist, optionally filtered by release."""
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        query = (
            db.query(ReleaseSong)
            .join(Release, ReleaseSong.release_id == Release.id)
            .filter(Release.artist_id == artist.id)
        )
        if release_id is not None:
            query = query.filter(ReleaseSong.release_id == release_id)

        total = query.count()
        links = (
            query
            .order_by(ReleaseSong.track_number.asc().nullslast(), ReleaseSong.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        items = []
        for link in links:
            song_data = _resolve_song(link.song_source, link.song_id, db)
            if song_data:
                song_data["release_id"] = link.release_id
                song_data["track_number"] = link.track_number
                release = db.query(Release).get(link.release_id)
                song_data["release_title"] = release.title if release else None
                items.append(song_data)

        return {"items": items, "total": total, "offset": offset, "limit": limit}
    finally:
        db.close()


def _get_release_song_charges(release: Release, db) -> list[int]:
    """Get all individual song charge values for a release."""
    charges = []
    for link in release.songs:
        song = _resolve_song_row(link.song_source, link.song_id, db)
        if song and song.charge_value is not None:
            charges.append(song.charge_value)
    return charges


def _resolve_song_row(source: str, song_id: int, db):
    """Resolve a polymorphic song reference to the actual row."""
    model_map = {"compass": CompassSong, "library": LibrarySong, "submitted": SubmittedSong}
    model = model_map.get(source)
    if not model:
        return None
    return db.query(model).get(song_id)


def _resolve_song(source: str, song_id: int, db) -> dict | None:
    """Resolve a song reference to a display dict."""
    row = _resolve_song_row(source, song_id, db)
    if not row:
        return None
    return {
        "title": row.title,
        "artist": getattr(row, "artist", None),
        "rubric_color": row.rubric_color,
        "charge_value": row.charge_value,
        "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
        "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
        "contaminated": getattr(row, "contaminated", False) or False,
        "contamination_note": getattr(row, "contamination_note", None),
        "charge_summary": getattr(row, "charge_summary", None),
        "song_source": source,
        "song_id": song_id,
    }
