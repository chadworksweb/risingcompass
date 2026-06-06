"""Artist Trajectory API — public endpoints for artist search and trajectory display."""

import logging
import time
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, text

from app.database import SessionLocal
from app.models import (
    Artist, Release, ReleaseSong, Song, SongSlug, MbCoverArt,
)
from app.constants import COLOR_LABELS, COLOR_HEX
from app.services.artist_utils import (
    count_songs_by_artist, derive_tier, generate_song_slug, slugify,
)
from app.services import coverart
from app.services.compass_calc import charge_to_degree
from app.services.charge_calc import degree_to_charge

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


@router.get("")
def list_artists():
    """All indexed artists, alphabetical, name + slug only.

    Drives the /artists/ A-Z index page on the frontend. Cached 60s in-process
    via the shared artist cache — the list changes rarely relative to how
    often the index page loads.
    """
    cache_key = ("list-artists",)
    cached = _artist_cache_get(cache_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        rows = (
            db.query(Artist.name, Artist.slug)
            .filter(Artist.slug.isnot(None))
            .order_by(func.lower(Artist.name))
            .all()
        )
        artists = [{"name": n, "slug": s} for n, s in rows]
        payload = {"artists": artists, "total": len(artists)}
        _artist_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


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

    # Match against both raw lowercase and an &->and-normalized form so
    # "Hall and Oates" finds "Hall & Oates" and vice versa.
    like_pat = f"%{q_lower}%"
    like_pat_norm = f"%{q_lower.replace('&', 'and')}%"

    db = SessionLocal()
    try:
        # --- Indexed artists, aggregated in one round trip -----------------
        indexed_sql = text(
            """
            WITH matched AS (
                SELECT id, name, slug
                FROM artists
                WHERE lower(name) LIKE :pat
                   OR replace(lower(name), '&', 'and') LIKE :pat_norm
                LIMIT :limit
            )
            SELECT
                m.name,
                m.slug,
                (SELECT COUNT(*) FROM releases r WHERE r.artist_id = m.id)
                    AS release_count,
                (SELECT COUNT(*) FROM songs
                   WHERE lower(artist) = lower(m.name)
                     AND charge_value IS NOT NULL) AS song_count
            FROM matched m
            """
        )
        indexed_rows = db.execute(
            indexed_sql, {"pat": like_pat, "pat_norm": like_pat_norm, "limit": limit}
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
                    SELECT artist FROM songs
                     WHERE (lower(artist) LIKE :pat
                            OR replace(lower(artist), '&', 'and') LIKE :pat_norm)
                       AND charge_value IS NOT NULL
                       AND artist IS NOT NULL
                ) sub
                GROUP BY lower(artist)
                ORDER BY name
                LIMIT :limit
                """
            )
            # Fetch a bit more than `remaining` to absorb rows that filter out
            # because they're already in `indexed_lower`.
            unindexed_rows = db.execute(
                unindexed_sql,
                {"pat": like_pat, "pat_norm": like_pat_norm,
                 "limit": remaining + len(indexed_lower) + 10},
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
    # Unified: distinct songs credited to the artist (via the repointed
    # song_id on release_songs + song_artists), each charge counted ONCE
    # -- so cross-year / cross-table duplicates no longer double-weight the mean.
    sql = text(
        """
        SELECT DISTINCT s.id, s.charge_value
          FROM songs s
         WHERE s.charge_value IS NOT NULL
           AND s.id IN (
                SELECT rs.song_id
                  FROM release_songs rs
                  JOIN releases r ON r.id = rs.release_id
                 WHERE r.artist_id = :aid AND rs.song_id IS NOT NULL
                UNION
                SELECT sa.song_id
                  FROM song_artists sa
                 WHERE sa.artist_id = :aid AND sa.song_id IS NOT NULL
           )
        """
    )
    rows = db.execute(sql, {"aid": artist_id}).all()
    return [r[1] for r in rows if r[1] is not None]


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
            "id": artist.id,
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


def _cover_thumbs_for(musicbrainz_ids, db) -> dict:
    """Map release-group MBID -> CAA thumbnail URL, for the ones CAA confirmed
    have art. MBIDs with no art (or not yet checked) are absent from the map."""
    mbids = [m for m in dict.fromkeys(musicbrainz_ids) if m]
    if not mbids:
        return {}
    rows = (
        db.query(MbCoverArt.musicbrainz_id)
        .filter(MbCoverArt.musicbrainz_id.in_(mbids), MbCoverArt.has_art.is_(True))
        .all()
    )
    return {mbid: coverart.coverart_urls(mbid)["thumb_url"] for (mbid,) in rows}


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

        # Cover-art thumbnails: one lookup for the page, keyed by release-group
        # MBID. Only release-groups CAA confirmed art for (has_art) get a URL;
        # the rest fall back to the tier dot on the frontend.
        thumb_by_mbid = _cover_thumbs_for(
            [r.musicbrainz_id for r in rows if r.musicbrainz_id], db
        )

        items = [
            {
                "id": r.id,
                "slug": slugify(r.title),
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
                "cover_thumb_url": thumb_by_mbid.get(r.musicbrainz_id),
            }
            for r in rows
        ]
        payload = {"items": items, "total": total, "offset": offset, "limit": limit}
        _artist_cache_set(cache_key, payload)
        return payload
    finally:
        db.close()


@router.get("/{slug}/releases/{release_slug}")
def release_detail(slug: str, release_slug: str):
    """A single release's reading page: hero charge + cover + summary / arc /
    listener / societal prose + tracklist.

    The release is resolved by slugify(title) within the artist, so the URL
    stays stable across release-row rebuilds. Keying the URL on releases.id
    would break every rebuild (the PK churns); the title slug does not.
    """
    cache_key = ("release", slug, release_slug)
    cached = _artist_cache_get(cache_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        release = None
        for r in db.query(Release).filter(Release.artist_id == artist.id).all():
            if slugify(r.title) == release_slug:
                release = r
                break
        if not release:
            raise HTTPException(404, "Release not found")

        # Tracklist, ordered by track number (nulls last), then resolved title.
        tracks = []
        for link in sorted(
            release.songs,
            key=lambda l: (l.track_number is None, l.track_number or 0),
        ):
            song = _resolve_song(link.song_id, db)
            if not song:
                continue
            song["track_number"] = link.track_number
            tracks.append(song)

        # Attach song-page slugs in one lookup so tracks can link out.
        ids = [t["song_id"] for t in tracks if t.get("song_id")]
        if ids:
            slug_map: dict[int, str] = {}
            for sid, sv in (
                db.query(SongSlug.song_id, SongSlug.slug)
                .filter(SongSlug.song_id.in_(ids))
                .all()
            ):
                slug_map.setdefault(sid, sv)
            for t in tracks:
                t["slug"] = slug_map.get(t["song_id"])

        # Hero cover (front-500), only when CAA confirmed art for this group.
        cover_url = None
        if release.musicbrainz_id:
            has_art = (
                db.query(MbCoverArt)
                .filter(
                    MbCoverArt.musicbrainz_id == release.musicbrainz_id,
                    MbCoverArt.has_art.is_(True),
                )
                .first()
            )
            if has_art:
                cover_url = coverart.coverart_urls(release.musicbrainz_id)["cover_url"]

        payload = {
            "id": release.id,
            "slug": release_slug,
            "title": release.title,
            "release_type": release.release_type,
            "release_date": release.release_date.isoformat() if release.release_date else None,
            "release_year": release.release_year,
            "charge_value": release.charge_value,
            "rubric_color": release.rubric_color,
            "tier_label": COLOR_LABELS.get(release.rubric_color, ""),
            "tier_hex": COLOR_HEX.get(release.rubric_color, "#999"),
            "track_count": release.track_count,
            "calibrated_count": release.calibrated_count,
            "contamination_count": release.contamination_count,
            "charge_summary": release.charge_summary,
            "arc_prose": release.arc_prose,
            "effects_prose": release.effects_prose,
            "societal_prose": release.societal_prose,
            "cover_url": cover_url,
            "artist": {"name": artist.name, "slug": artist.slug},
            "tracks": tracks,
        }
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

        # Unified: distinct atomic songs credited to this artist -- on a release
        # they own (release_songs.song_id) OR directly credited
        # (song_artists.song_id). One row per song; cross-year/cross-table
        # duplicates already collapsed into the unified entity.
        rows = db.execute(text(
            """
            SELECT s.id, s.title, s.artist, s.rubric_color, s.charge_value, s.contaminated
              FROM songs s
             WHERE s.charge_value IS NOT NULL
               AND s.id IN (
                    SELECT rs.song_id
                      FROM release_songs rs
                      JOIN releases r ON r.id = rs.release_id
                     WHERE r.artist_id = :aid AND rs.song_id IS NOT NULL
                    UNION
                    SELECT sa.song_id
                      FROM song_artists sa
                     WHERE sa.artist_id = :aid AND sa.song_id IS NOT NULL
               )
            """
        ), {"aid": artist.id}).all()

        items: list[dict] = []
        for song_id, title, song_artist, rubric_color, charge_value, contaminated in rows:
            items.append({
                "song_source": "songs",
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
        # this in-Python sort is cheaper than a SQL ORDER BY.
        items.sort(key=lambda x: (x["charge_value"] is None, -(x["charge_value"] or 0)))
        total = len(items)
        page = items[offset:offset + limit]

        # Attach song_slugs (by unified id) in one lookup for the page.
        if page:
            ids = [it["song_id"] for it in page]
            slug_map: dict[int, str] = {}
            for sid, slug_value in (
                db.query(SongSlug.song_id, SongSlug.slug)
                .filter(SongSlug.song_id.in_(ids))
                .all()
            ):
                slug_map.setdefault(sid, slug_value)
            for it in page:
                it["slug"] = slug_map.get(it["song_id"])

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
            song_data = _resolve_song(link.song_id, db)
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
        song = _resolve_song_row(link.song_id, db)
        if song and song.charge_value is not None:
            charges.append(song.charge_value)
    return charges


def _resolve_song_row(unified_id, db):
    """Resolve a unified song id to the songs row."""
    if not unified_id:
        return None
    return db.query(Song).get(unified_id)


def _resolve_song(unified_id, db) -> dict | None:
    """Resolve a unified song id to a display dict."""
    row = _resolve_song_row(unified_id, db)
    if not row:
        return None
    return {
        "title": row.title,
        "artist": row.artist,
        "rubric_color": row.rubric_color,
        "charge_value": row.charge_value,
        "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
        "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
        "contaminated": row.contaminated or False,
        "contamination_note": row.contamination_note,
        "charge_summary": row.charge_summary,
        "song_source": "songs",
        "song_id": unified_id,
    }
