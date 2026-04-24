"""Artist admin API — bootstrap, manual creation, refresh, metadata resolution,
merge + rename (audited)."""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Artist, ArtistAdminEvent, Release, ReleaseSong,
    CompassSong, LibrarySong, SubmittedSong,
)
from app.services.artist_utils import (
    generate_artist_slug, normalize_artist_name, compute_release_charge,
    count_songs_by_artist, resolve_artist_releases,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/artists", tags=["artists-admin"])


def _require_admin(key: str):
    if key != settings.rc_admin_key:
        raise HTTPException(403, "Invalid admin key")


class CreateArtistRequest(BaseModel):
    name: str


@router.post("")
def create_artist(
    req: CreateArtistRequest,
    x_admin_key: str = Header(...),
):
    """Manually create an artist entity."""
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "Name required")

        # Check if already exists
        existing = (
            db.query(Artist)
            .filter(func.lower(Artist.name) == name.lower())
            .first()
        )
        if existing:
            return {
                "message": "Artist already exists",
                "name": existing.name,
                "slug": existing.slug,
                "id": existing.id,
            }

        slug = generate_artist_slug(name, db)
        artist = Artist(name=name, slug=slug)
        db.add(artist)
        db.commit()
        db.refresh(artist)

        return {"message": "Created", "name": artist.name, "slug": artist.slug, "id": artist.id}
    finally:
        db.close()


@router.post("/bootstrap")
def bootstrap_artists(
    artist_name: Optional[str] = Query(None),
    min_songs: int = Query(3, ge=1),
    x_admin_key: str = Header(...),
):
    """Bootstrap artist entities from existing song data.

    If artist_name is given, bootstrap that one artist.
    Otherwise, bootstrap all distinct artists with >= min_songs calibrated songs.
    """
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        if artist_name:
            names = [artist_name.strip()]
        else:
            names = _get_distinct_artist_names(db, min_songs)

        created = []
        skipped = []

        for name in names:
            normalized = normalize_artist_name(name)
            if not normalized:
                continue

            # Skip if already indexed
            existing = (
                db.query(Artist)
                .filter(func.lower(Artist.name) == normalized.lower())
                .first()
            )
            if existing:
                skipped.append(normalized)
                continue

            slug = generate_artist_slug(normalized, db)
            artist = Artist(name=normalized, slug=slug)
            db.add(artist)
            db.flush()

            # No auto-release creation. Songs with this artist surface on the
            # artist page via song_artists / string match; Release records
            # only exist when real release metadata (title, date, type) is
            # known — set via the admin bootstrap discography flow or a
            # targeted backfill script.
            song_count = len(_find_songs_for_artist(normalized, db))
            created.append({"name": normalized, "slug": slug, "songs": song_count})

        db.commit()

        return {
            "created": len(created),
            "skipped": len(skipped),
            "artists": created,
        }
    finally:
        db.close()


@router.post("/{slug}/refresh-release-aggregates")
def refresh_release_aggregates(
    slug: str,
    x_admin_key: str = Header(...),
):
    """Recompute track_count / calibrated_count / charge_value / rubric_color
    on every real Release for this artist by walking its ReleaseSong links.

    No catch-all creation. Releases only exist when a real album/single/EP
    is defined; songs without release metadata stay unassigned and surface
    via song_artists + top-songs.
    """
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        SOURCE_MODEL = {
            "compass": CompassSong,
            "library": LibrarySong,
            "submitted": SubmittedSong,
        }

        updated = []
        for release in artist.releases:
            links = release.songs
            charges: list[int] = []
            contam = 0
            for link in links:
                Model = SOURCE_MODEL.get(link.song_source)
                if Model is None:
                    continue
                row = db.query(Model).filter(Model.id == link.song_id).first()
                if row is None:
                    continue
                if row.charge_value is not None:
                    charges.append(row.charge_value)
                if getattr(row, "contaminated", False):
                    contam += 1

            release.calibrated_count = len(charges)
            release.contamination_count = contam
            if charges:
                result = compute_release_charge(charges)
                if result:
                    release.charge_value = result[0]
                    release.rubric_color = result[1]
            else:
                release.charge_value = None
                release.rubric_color = None
            updated.append({
                "id": release.id,
                "title": release.title,
                "track_count": release.track_count,
                "calibrated_count": release.calibrated_count,
            })

        db.commit()
        return {
            "artist": artist.name,
            "slug": artist.slug,
            "releases_refreshed": len(updated),
            "releases": updated,
        }
    finally:
        db.close()


@router.post("/{slug}/resolve-metadata")
async def resolve_metadata(
    slug: str,
    x_admin_key: str = Header(...),
):
    """Resolve release metadata for an artist via MusicBrainz/Spotify.

    Creates proper Release rows with dates, types, and track listings.
    Links existing calibrated songs to the correct releases.
    Unmatched songs stay in "Singles & Uncategorized".
    """
    _require_admin(x_admin_key)

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")
        artist_id = artist.id
    finally:
        db.close()

    stats = await resolve_artist_releases(artist_id)

    db = SessionLocal()
    try:
        artist = db.get(Artist, artist_id)
        return {
            "artist": artist.name,
            "slug": artist.slug,
            "musicbrainz_id": artist.musicbrainz_id,
            "spotify_id": artist.spotify_id,
            **stats,
        }
    finally:
        db.close()


def _get_distinct_artist_names(db, min_songs: int) -> list[str]:
    """Get distinct artist names across all song tables with >= min_songs."""
    # Collect all artist names with counts
    counts: dict[str, int] = {}

    for Model in (CompassSong, LibrarySong, SubmittedSong):
        query = (
            db.query(Model.artist, func.count(Model.id))
            .filter(Model.charge_value.isnot(None))
        )
        if Model is SubmittedSong:
            query = query.filter(Model.artist.isnot(None))
        rows = query.group_by(func.lower(Model.artist)).all()
        for name, count in rows:
            if name:
                key = name.lower()
                counts[key] = counts.get(key, 0) + count
                # Keep the first casing we see
                if key not in counts:
                    counts[key] = count

    # We need to also keep original names — rebuild with proper casing
    name_map: dict[str, str] = {}  # lowercase → display name
    for Model in (CompassSong, LibrarySong, SubmittedSong):
        query = db.query(func.distinct(Model.artist)).filter(Model.charge_value.isnot(None))
        if Model is SubmittedSong:
            query = query.filter(Model.artist.isnot(None))
        for (name,) in query.all():
            if name and name.lower() not in name_map:
                name_map[name.lower()] = name

    return [
        name_map[key]
        for key, count in sorted(counts.items(), key=lambda x: -x[1])
        if count >= min_songs and key in name_map
    ]


def _find_songs_for_artist(artist_name: str, db) -> list[tuple[str, int, int | None, bool]]:
    """Find all calibrated songs for an artist across all tables.

    Returns list of (source, song_id, charge_value, contaminated).
    """
    name_lower = artist_name.lower()
    results = []

    for source, Model in [("compass", CompassSong), ("library", LibrarySong), ("submitted", SubmittedSong)]:
        query = (
            db.query(Model)
            .filter(func.lower(Model.artist) == name_lower)
            .filter(Model.charge_value.isnot(None))
        )
        if Model is SubmittedSong:
            query = query.filter(Model.artist.isnot(None))
        for row in query.all():
            results.append((
                source,
                row.id,
                row.charge_value,
                getattr(row, "contaminated", False) or False,
            ))

    return results


# ============================================================
# Merge + rename (audited)
#
# These operations run many statements in one transaction. The shared
# SQLAlchemy session talks to the Turso embedded replica, which uses
# Hrana streams that time out mid-transaction under load — we hit this
# live during the first merge attempt. Same class of problem that
# api_call_log + lc_events + last_used_at solved by writing through a
# direct libsql connection to the primary. We use that pattern here too.
# ============================================================

_SONG_TABLES = ("compass_songs", "library_songs", "submitted_songs")


def _open_primary_conn():
    """Direct libsql connection to the Turso primary. Bypasses the embedded
    replica session so long admin transactions don't lose their Hrana stream
    mid-flight. Caller owns the lifecycle."""
    import libsql
    url = settings.database_url
    token = settings.turso_auth_token
    if url.startswith("libsql://") or url.startswith("https://"):
        return libsql.connect(database=url, auth_token=token)
    return libsql.connect(database=url)


def _invalidate_artist_caches() -> None:
    """After a merge or rename, the shared artist cache in routers.artists
    is stale for multiple slugs. Nuke the whole cache rather than track
    which keys got invalidated — admin ops are rare."""
    try:
        from app.routers import artists as artists_router
        with artists_router._artist_cache_lock:
            artists_router._artist_cache.clear()
        with artists_router._search_cache_lock:
            artists_router._search_cache.clear()
    except Exception:
        logger.exception("failed to clear artist caches post-admin-op")


class MergeArtistRequest(BaseModel):
    target_slug: str
    notes: Optional[str] = None


@router.post("/{slug}/merge-into")
def merge_artist(
    slug: str,
    req: MergeArtistRequest,
    x_admin_key: str = Header(...),
):
    """Merge the artist at `slug` into the artist at `target_slug`.

    Rewrites every FK + denormalised string column so the target absorbs
    everything the source pointed at, then deletes the source Artist row.
    Everything commits atomically via a direct libsql connection to the
    Turso primary (the replica session can't hold a long transaction).
    The artist_admin_events audit row is written in the same transaction.

    Post-merge `releases.track_count / calibrated_count / charge_value /
    rubric_color` on the target may be slightly stale for any release that
    absorbed links from the source — call
    POST /api/admin/artists/{target_slug}/refresh-release-aggregates
    afterwards to normalise (fast, idempotent).
    """
    _require_admin(x_admin_key)

    conn = _open_primary_conn()
    try:
        row = conn.execute(
            "SELECT id, name, slug FROM artists WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Source artist '{slug}' not found")
        source_id, source_name, source_slug = row

        row = conn.execute(
            "SELECT id, name, slug FROM artists WHERE slug = ?", (req.target_slug,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Target artist '{req.target_slug}' not found")
        target_id, target_name, target_slug = row

        if source_id == target_id:
            raise HTTPException(400, "Cannot merge an artist into itself")

        rewrites: dict = {}

        # 0. Song-row collisions. Migration 037 puts a UNIQUE index on
        #    (lower(title), lower(artist)) per song table — so two artists
        #    with the same song title ("Sweet Caroline" under both
        #    "Neil Diamond" and "Neil Diamonds") have duplicate song rows
        #    that will trip the later UPDATE artist = ... step. For each
        #    collision, migrate the source song's FKs onto the target song
        #    then drop the source song row.
        song_source_map = {
            "compass_songs": "compass",
            "library_songs": "library",
            "submitted_songs": "submitted",
        }
        dup_song_rows_merged = 0
        dup_release_song_links_migrated = 0
        dup_release_song_links_dropped = 0
        dup_song_artist_links_migrated = 0
        dup_song_artist_links_dropped = 0
        dup_song_slugs_dropped = 0
        for tbl, src_type in song_source_map.items():
            collisions = conn.execute(
                f"SELECT src.id, tgt.id"
                f"  FROM {tbl} src"
                f"  JOIN {tbl} tgt ON lower(src.title) = lower(tgt.title)"
                f" WHERE lower(src.artist) = lower(?)"
                f"   AND lower(tgt.artist) = lower(?)"
                f"   AND src.id != tgt.id",
                (source_name, target_name),
            ).fetchall()
            for src_sid, tgt_sid in collisions:
                # release_songs: drop links where the target song is already
                # on the same release; rewrite the rest from src → tgt.
                cur = conn.execute(
                    "DELETE FROM release_songs"
                    " WHERE song_source = ? AND song_id = ?"
                    "   AND release_id IN ("
                    "       SELECT release_id FROM release_songs"
                    "        WHERE song_source = ? AND song_id = ?"
                    "   )",
                    (src_type, src_sid, src_type, tgt_sid),
                )
                dup_release_song_links_dropped += cur.rowcount or 0
                cur = conn.execute(
                    "UPDATE release_songs SET song_id = ?"
                    " WHERE song_source = ? AND song_id = ?",
                    (tgt_sid, src_type, src_sid),
                )
                dup_release_song_links_migrated += cur.rowcount or 0

                # song_artists: same dedupe-then-rewrite, on artist_id key.
                cur = conn.execute(
                    "DELETE FROM song_artists"
                    " WHERE song_source = ? AND song_id = ?"
                    "   AND artist_id IN ("
                    "       SELECT artist_id FROM song_artists"
                    "        WHERE song_source = ? AND song_id = ?"
                    "   )",
                    (src_type, src_sid, src_type, tgt_sid),
                )
                dup_song_artist_links_dropped += cur.rowcount or 0
                cur = conn.execute(
                    "UPDATE song_artists SET song_id = ?"
                    " WHERE song_source = ? AND song_id = ?",
                    (tgt_sid, src_type, src_sid),
                )
                dup_song_artist_links_migrated += cur.rowcount or 0

                # song_slugs: unique on slug; the src slug likely differs
                # from tgt's, but the target's is the canonical one going
                # forward. Drop any slug rows pointing at the src song row.
                cur = conn.execute(
                    "DELETE FROM song_slugs WHERE song_source = ? AND song_id = ?",
                    (src_type, src_sid),
                )
                dup_song_slugs_dropped += cur.rowcount or 0

                conn.execute(f"DELETE FROM {tbl} WHERE id = ?", (src_sid,))
                dup_song_rows_merged += 1
        rewrites["dup_song_rows_merged"] = dup_song_rows_merged
        rewrites["dup_release_song_links_migrated"] = dup_release_song_links_migrated
        rewrites["dup_release_song_links_dropped"] = dup_release_song_links_dropped
        rewrites["dup_song_artist_links_migrated"] = dup_song_artist_links_migrated
        rewrites["dup_song_artist_links_dropped"] = dup_song_artist_links_dropped
        rewrites["dup_song_slugs_dropped"] = dup_song_slugs_dropped

        # 1. song_artists: dedupe against target's credits, then rewrite.
        cur = conn.execute(
            "DELETE FROM song_artists"
            " WHERE artist_id = ?"
            "   AND (song_source, song_id) IN ("
            "       SELECT song_source, song_id FROM song_artists WHERE artist_id = ?"
            "   )",
            (source_id, target_id),
        )
        rewrites["song_artists_dedup_dropped"] = cur.rowcount or 0

        cur = conn.execute(
            "UPDATE song_artists SET artist_id = ? WHERE artist_id = ?",
            (target_id, source_id),
        )
        rewrites["song_artists_reassigned"] = cur.rowcount or 0

        # 2. releases: handle UNIQUE(artist_id, title) collisions.
        target_title_to_id = {
            t: rid for rid, t in conn.execute(
                "SELECT id, title FROM releases WHERE artist_id = ?", (target_id,)
            ).fetchall()
        }
        releases_merged = 0
        releases_reassigned = 0
        release_songs_moved = 0
        release_songs_dropped_as_dup = 0
        for src_rel_id, src_title in conn.execute(
            "SELECT id, title FROM releases WHERE artist_id = ?", (source_id,)
        ).fetchall():
            if src_title in target_title_to_id:
                to_rel_id = target_title_to_id[src_title]
                existing = {
                    (s, i) for s, i in conn.execute(
                        "SELECT song_source, song_id FROM release_songs WHERE release_id = ?",
                        (to_rel_id,),
                    ).fetchall()
                }
                for link_id, ssrc, sid in conn.execute(
                    "SELECT id, song_source, song_id FROM release_songs WHERE release_id = ?",
                    (src_rel_id,),
                ).fetchall():
                    if (ssrc, sid) in existing:
                        conn.execute("DELETE FROM release_songs WHERE id = ?", (link_id,))
                        release_songs_dropped_as_dup += 1
                    else:
                        conn.execute(
                            "UPDATE release_songs SET release_id = ? WHERE id = ?",
                            (to_rel_id, link_id),
                        )
                        existing.add((ssrc, sid))
                        release_songs_moved += 1
                conn.execute("DELETE FROM releases WHERE id = ?", (src_rel_id,))
                releases_merged += 1
            else:
                conn.execute(
                    "UPDATE releases SET artist_id = ? WHERE id = ?",
                    (target_id, src_rel_id),
                )
                releases_reassigned += 1
        rewrites["releases_merged_into_existing"] = releases_merged
        rewrites["releases_reassigned"] = releases_reassigned
        rewrites["release_songs_moved"] = release_songs_moved
        rewrites["release_songs_dropped_as_dup"] = release_songs_dropped_as_dup

        # 3. Normalise the denormalised `artist` string column on song tables.
        for tbl in _SONG_TABLES:
            cur = conn.execute(
                f"UPDATE {tbl} SET artist = ? WHERE lower(artist) = lower(?)",
                (target_name, source_name),
            )
            rewrites[f"{tbl}_artist_string_rewritten"] = cur.rowcount or 0

        # 4. Drop the source Artist row.
        conn.execute("DELETE FROM artists WHERE id = ?", (source_id,))

        # 5. Audit event — same transaction.
        conn.execute(
            "INSERT INTO artist_admin_events"
            " (event_type, actor, artist_id, artist_name_before, artist_slug_before,"
            "  target_artist_id, target_artist_name, target_artist_slug,"
            "  rewrites_json, notes, occurred_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                "merge", "admin",
                source_id, source_name, source_slug,
                target_id, target_name, target_slug,
                json.dumps(rewrites), req.notes,
            ),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.commit()
    except HTTPException:
        raise
    except Exception:
        try: conn.rollback()
        except Exception: pass
        logger.exception("artist merge failed (%s → %s)", slug, req.target_slug)
        raise HTTPException(500, "Merge failed — nothing committed. Check server logs.")
    finally:
        try: conn.close()
        except Exception: pass

    _invalidate_artist_caches()

    return {
        "event_id": event_id,
        "merged": {"name": source_name, "slug": source_slug},
        "into": {"name": target_name, "slug": target_slug},
        "rewrites": rewrites,
    }


class RenameArtistRequest(BaseModel):
    new_name: str
    new_slug: Optional[str] = None
    notes: Optional[str] = None


@router.post("/{slug}/rename")
def rename_artist(
    slug: str,
    req: RenameArtistRequest,
    x_admin_key: str = Header(...),
):
    """Rename an artist (and optionally change its slug). Normalises the
    `artist` string column on the three song tables to match the new name.

    Same direct-libsql pattern as merge — writes go straight to primary
    so the transaction isn't vulnerable to replica stream timeouts.
    """
    _require_admin(x_admin_key)

    new_name = (req.new_name or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")

    # Need generate_artist_slug which uses the SQLAlchemy session for its
    # uniqueness probe. Do that through the replica session before opening
    # the primary connection — it's a single read, no long transaction.
    derived_slug: Optional[str] = None

    conn = _open_primary_conn()
    try:
        row = conn.execute(
            "SELECT id, name, slug FROM artists WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Artist '{slug}' not found")
        artist_id, old_name, old_slug = row

        new_slug = req.new_slug.strip() if req.new_slug else None
        if new_slug:
            collision = conn.execute(
                "SELECT name FROM artists WHERE slug = ? AND id != ?",
                (new_slug, artist_id),
            ).fetchone()
            if collision:
                raise HTTPException(409, f"Slug '{new_slug}' already used by '{collision[0]}'")
        elif new_name.lower() != old_name.lower():
            # Derive a fresh deduped slug via the helper — it goes through
            # the replica session; worst case this reads a slightly stale
            # slug set and we pick a collision on the primary (409 later).
            db = SessionLocal()
            try:
                derived_slug = generate_artist_slug(new_name, db)
            finally:
                db.close()
            new_slug = derived_slug

        rewrites: dict = {}
        for tbl in _SONG_TABLES:
            cur = conn.execute(
                f"UPDATE {tbl} SET artist = ? WHERE lower(artist) = lower(?)",
                (new_name, old_name),
            )
            rewrites[f"{tbl}_artist_string_rewritten"] = cur.rowcount or 0

        if new_slug:
            conn.execute(
                "UPDATE artists SET name = ?, slug = ? WHERE id = ?",
                (new_name, new_slug, artist_id),
            )
        else:
            conn.execute(
                "UPDATE artists SET name = ? WHERE id = ?",
                (new_name, artist_id),
            )

        conn.execute(
            "INSERT INTO artist_admin_events"
            " (event_type, actor, artist_id, artist_name_before, artist_slug_before,"
            "  artist_name_after, artist_slug_after, rewrites_json, notes, occurred_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                "rename", "admin",
                artist_id, old_name, old_slug,
                new_name, new_slug or old_slug,
                json.dumps(rewrites), req.notes,
            ),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.commit()
        result_slug = new_slug or old_slug
    except HTTPException:
        raise
    except Exception:
        try: conn.rollback()
        except Exception: pass
        logger.exception("artist rename failed (%s)", slug)
        raise HTTPException(500, "Rename failed — nothing committed. Check server logs.")
    finally:
        try: conn.close()
        except Exception: pass

    _invalidate_artist_caches()

    return {
        "event_id": event_id,
        "before": {"name": old_name, "slug": old_slug},
        "after": {"name": new_name, "slug": result_slug},
        "rewrites": rewrites,
    }


@router.get("/events")
def list_artist_admin_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None, pattern="^(merge|rename)$"),
    x_admin_key: str = Header(...),
):
    """Audit log — most recent first."""
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        q = db.query(ArtistAdminEvent)
        if event_type:
            q = q.filter(ArtistAdminEvent.event_type == event_type)
        total = q.with_entities(func.count(ArtistAdminEvent.id)).scalar() or 0
        rows = (
            q.order_by(ArtistAdminEvent.occurred_at.desc())
             .offset(offset)
             .limit(limit)
             .all()
        )
        items = [{
            "id": r.id,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "event_type": r.event_type,
            "actor": r.actor,
            "artist_id": r.artist_id,
            "artist_name_before": r.artist_name_before,
            "artist_slug_before": r.artist_slug_before,
            "artist_name_after": r.artist_name_after,
            "artist_slug_after": r.artist_slug_after,
            "target_artist_id": r.target_artist_id,
            "target_artist_name": r.target_artist_name,
            "target_artist_slug": r.target_artist_slug,
            "rewrites": json.loads(r.rewrites_json) if r.rewrites_json else None,
            "notes": r.notes,
        } for r in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit}
    finally:
        db.close()
