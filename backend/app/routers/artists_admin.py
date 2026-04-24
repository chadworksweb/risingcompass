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
# ============================================================

_SONG_TABLES = ("compass_songs", "library_songs", "submitted_songs")


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


def _recompute_release_aggregates_for_artist(db, artist: Artist) -> None:
    source_model = {"compass": CompassSong, "library": LibrarySong, "submitted": SubmittedSong}
    for release in db.query(Release).filter(Release.artist_id == artist.id).all():
        links = db.query(ReleaseSong).filter(ReleaseSong.release_id == release.id).all()
        charges: list[int] = []
        contam = 0
        for link in links:
            Model = source_model.get(link.song_source)
            if Model is None:
                continue
            row = db.query(Model).filter(Model.id == link.song_id).first()
            if row is None:
                continue
            if row.charge_value is not None:
                charges.append(row.charge_value)
            if getattr(row, "contaminated", False):
                contam += 1
        release.track_count = len(links)
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
    All of this runs in one transaction; an artist_admin_events audit row
    commits alongside so the log can never disagree with the DB.
    """
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        source = db.query(Artist).filter(Artist.slug == slug).first()
        if not source:
            raise HTTPException(404, f"Source artist '{slug}' not found")
        target = db.query(Artist).filter(Artist.slug == req.target_slug).first()
        if not target:
            raise HTTPException(404, f"Target artist '{req.target_slug}' not found")
        if source.id == target.id:
            raise HTTPException(400, "Cannot merge an artist into itself")

        source_snap = {"id": source.id, "name": source.name, "slug": source.slug}
        target_snap = {"id": target.id, "name": target.name, "slug": target.slug}

        rewrites: dict = {}

        # 1. song_artists: dedupe against target's existing credits, then rewrite.
        dup = db.execute(text(
            "DELETE FROM song_artists"
            " WHERE artist_id = :from_id"
            "   AND (song_source, song_id) IN ("
            "       SELECT song_source, song_id FROM song_artists WHERE artist_id = :to_id"
            "   )"
        ), {"from_id": source.id, "to_id": target.id}).rowcount
        rewrites["song_artists_dedup_dropped"] = dup

        sa_rewrites = db.execute(text(
            "UPDATE song_artists SET artist_id = :to_id WHERE artist_id = :from_id"
        ), {"from_id": source.id, "to_id": target.id}).rowcount
        rewrites["song_artists_reassigned"] = sa_rewrites

        # 2. releases: UNIQUE(artist_id, title) forces collision handling.
        target_title_to_id = {
            r.title: r.id
            for r in db.query(Release).filter(Release.artist_id == target.id).all()
        }
        releases_merged = 0
        releases_reassigned = 0
        release_songs_moved = 0
        release_songs_dropped_as_dup = 0
        for src_rel in db.query(Release).filter(Release.artist_id == source.id).all():
            if src_rel.title in target_title_to_id:
                to_rel_id = target_title_to_id[src_rel.title]
                existing = {
                    (s, i) for s, i in db.execute(text(
                        "SELECT song_source, song_id FROM release_songs WHERE release_id = :rid"
                    ), {"rid": to_rel_id}).all()
                }
                for link_id, ssrc, sid, _tn in db.execute(text(
                    "SELECT id, song_source, song_id, track_number FROM release_songs WHERE release_id = :rid"
                ), {"rid": src_rel.id}).all():
                    if (ssrc, sid) in existing:
                        db.execute(text("DELETE FROM release_songs WHERE id = :lid"), {"lid": link_id})
                        release_songs_dropped_as_dup += 1
                    else:
                        db.execute(
                            text("UPDATE release_songs SET release_id = :to_rid WHERE id = :lid"),
                            {"to_rid": to_rel_id, "lid": link_id},
                        )
                        existing.add((ssrc, sid))
                        release_songs_moved += 1
                db.delete(src_rel)
                releases_merged += 1
            else:
                src_rel.artist_id = target.id
                releases_reassigned += 1
        rewrites["releases_merged_into_existing"] = releases_merged
        rewrites["releases_reassigned"] = releases_reassigned
        rewrites["release_songs_moved"] = release_songs_moved
        rewrites["release_songs_dropped_as_dup"] = release_songs_dropped_as_dup

        # 3. Normalise the `artist` string column on each song table.
        for tbl in _SONG_TABLES:
            n = db.execute(
                text(f"UPDATE {tbl} SET artist = :to_name WHERE lower(artist) = lower(:from_name)"),
                {"to_name": target.name, "from_name": source.name},
            ).rowcount
            rewrites[f"{tbl}_artist_string_rewritten"] = n

        # 4. Drop the source Artist row.
        db.delete(source)
        db.flush()

        # 5. Recompute aggregates on every surviving release for the target.
        _recompute_release_aggregates_for_artist(db, target)

        # 6. Audit event — same transaction.
        event = ArtistAdminEvent(
            event_type="merge",
            actor="admin",
            artist_id=source_snap["id"],
            artist_name_before=source_snap["name"],
            artist_slug_before=source_snap["slug"],
            target_artist_id=target_snap["id"],
            target_artist_name=target_snap["name"],
            target_artist_slug=target_snap["slug"],
            rewrites_json=json.dumps(rewrites),
            notes=req.notes,
        )
        db.add(event)
        db.commit()
        event_id = event.id
    finally:
        db.close()

    _invalidate_artist_caches()

    return {
        "event_id": event_id,
        "merged": {"name": source_snap["name"], "slug": source_snap["slug"]},
        "into": {"name": target_snap["name"], "slug": target_snap["slug"]},
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
    `artist` string column on the three song tables to match the new name."""
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, f"Artist '{slug}' not found")

        new_name = (req.new_name or "").strip()
        if not new_name:
            raise HTTPException(400, "new_name is required")

        old_name, old_slug = artist.name, artist.slug

        new_slug = req.new_slug.strip() if req.new_slug else None
        if new_slug:
            collision = (
                db.query(Artist)
                .filter(Artist.slug == new_slug)
                .filter(Artist.id != artist.id)
                .first()
            )
            if collision:
                raise HTTPException(409, f"Slug '{new_slug}' already used by '{collision.name}'")
        elif new_name.lower() != old_name.lower():
            # Name changed but no slug provided — derive a fresh deduped slug.
            new_slug = generate_artist_slug(new_name, db)

        rewrites: dict = {}
        for tbl in _SONG_TABLES:
            n = db.execute(
                text(f"UPDATE {tbl} SET artist = :new_name WHERE lower(artist) = lower(:old_name)"),
                {"new_name": new_name, "old_name": old_name},
            ).rowcount
            rewrites[f"{tbl}_artist_string_rewritten"] = n

        artist.name = new_name
        if new_slug:
            artist.slug = new_slug

        event = ArtistAdminEvent(
            event_type="rename",
            actor="admin",
            artist_id=artist.id,
            artist_name_before=old_name,
            artist_slug_before=old_slug,
            artist_name_after=new_name,
            artist_slug_after=artist.slug,
            rewrites_json=json.dumps(rewrites),
            notes=req.notes,
        )
        db.add(event)
        db.commit()
        event_id = event.id
        result_slug = artist.slug
    finally:
        db.close()

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
