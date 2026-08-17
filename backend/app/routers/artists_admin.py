"""Artist admin API — bootstrap, manual creation, refresh, metadata resolution,
merge + rename (audited)."""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text

from app.auth import require_admin_session
from app.database import SessionLocal, engine
from app.models import (
    Artist, ArtistAdminEvent, Release, ReleaseSong, ReleaseSuppression, Song,
    SongArtist,
)
from app.services.artist_utils import (
    generate_artist_slug, normalize_artist_name, compute_release_charge,
    resolve_artist_releases, _fetch_musicbrainz_data,
    normalize_release_title,
)
from app.services.musicbrainz import MusicBrainzUnavailable
from app.services.song_identity import compute_canonical_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/artists",
    tags=["artists-admin"],
    dependencies=[Depends(require_admin_session)],
)


class CreateArtistRequest(BaseModel):
    name: str


@router.post("")
def create_artist(
    req: CreateArtistRequest,
):
    """Manually create an artist entity."""
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
):
    """Bootstrap artist entities from existing song data.

    If artist_name is given, bootstrap that one artist.
    Otherwise, bootstrap all distinct artists with >= min_songs calibrated songs.
    """
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
):
    """Recompute track_count / calibrated_count / charge_value / rubric_color
    on every real Release for this artist by walking its ReleaseSong links.

    No catch-all creation. Releases only exist when a real album/single/EP
    is defined; songs without release metadata stay unassigned and surface
    via song_artists + top-songs.
    """
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        updated = []
        for release in artist.releases:
            links = release.songs
            charges: list[int] = []
            contam = 0
            for link in links:
                if link.song_id is None:
                    continue
                row = db.query(Song).get(link.song_id)
                if row is None:
                    continue
                if row.charge_value is not None:
                    charges.append(row.charge_value)
                if row.contaminated:
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
):
    """Resolve release metadata for an artist via MusicBrainz.

    Creates proper Release rows with dates, types, and track listings under the
    first-appearance rule (RISING-COMPASS-ARTIST-RELEASES.md). Links existing
    calibrated songs to every release they appear on. Unmatched songs stay in
    "Singles & Uncategorized".

    Additive: existing releases are kept (skipped by MBID). To re-apply the rule
    to an artist whose releases predate it, use rebuild-releases below.
    """

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")
        artist_id = artist.id
    finally:
        db.close()

    # Additive, so a truncated catalogue costs nothing already stored -- but it
    # would quietly add a partial one, and a later run skips by MBID and never
    # revisits the gap. Refuse rather than half-fill.
    try:
        stats = await resolve_artist_releases(artist_id)
    except MusicBrainzUnavailable as exc:
        logger.warning("resolve-metadata aborted for %s: %s", slug, exc)
        raise HTTPException(
            503,
            "MusicBrainz is not answering reliably right now, so the catalogue "
            "came back incomplete. Nothing was written -- retry later.",
        )

    db = SessionLocal()
    try:
        artist = db.get(Artist, artist_id)
        return {
            "artist": artist.name,
            "slug": artist.slug,
            "musicbrainz_id": artist.musicbrainz_id,
            **stats,
        }
    finally:
        db.close()


class SuppressReleaseRequest(BaseModel):
    title: str
    reason: Optional[str] = None


@router.get("/{slug}/suppressions")
def list_suppressions(slug: str):
    """Curated releases this artist should never carry (migration 147)."""
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")
        rows = (
            db.query(ReleaseSuppression)
            .filter(ReleaseSuppression.artist_id == artist.id)
            .order_by(ReleaseSuppression.title_snapshot)
            .all()
        )
        return {
            "artist": artist.name,
            "slug": artist.slug,
            "items": [
                {
                    "id": r.id,
                    "title": r.title_snapshot,
                    "title_norm": r.title_norm,
                    "reason": r.reason,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.post("/{slug}/suppressions")
def add_suppression(slug: str, req: SuppressReleaseRequest):
    """Suppress a release title AND delete any row currently carrying it.

    Deleting alone never sticks -- the next rebuild re-fetches the title from
    MusicBrainz and re-creates it. Suppressing alone leaves the existing row in
    place. Both together are what "remove this release" actually means, so this
    endpoint does both.

    Refuses while the release carries a calibrated song: that would strand a real
    reading. Unlink or re-file the song first.
    """
    title_norm = normalize_release_title(req.title)
    if not title_norm:
        raise HTTPException(400, "title is required")

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        matches = [
            r for r in db.query(Release).filter(Release.artist_id == artist.id).all()
            if normalize_release_title(r.title) == title_norm
        ]
        for rel in matches:
            calibrated = (
                db.query(ReleaseSong)
                .join(Song, Song.id == ReleaseSong.song_id)
                .filter(ReleaseSong.release_id == rel.id, Song.charge_value.isnot(None))
                .count()
            )
            if calibrated:
                raise HTTPException(
                    409,
                    f"'{rel.title}' carries {calibrated} calibrated song(s). "
                    "Deleting it would strand a real reading -- unlink them first.",
                )

        existing = (
            db.query(ReleaseSuppression)
            .filter(
                ReleaseSuppression.artist_id == artist.id,
                ReleaseSuppression.title_norm == title_norm,
            )
            .first()
        )
        if not existing:
            db.add(ReleaseSuppression(
                artist_id=artist.id,
                title_norm=title_norm,
                title_snapshot=req.title.strip(),
                reason=req.reason,
            ))

        deleted = 0
        for rel in matches:
            db.query(ReleaseSong).filter(ReleaseSong.release_id == rel.id).delete()
            db.delete(rel)
            deleted += 1
        db.commit()

        _invalidate_artist_caches()
        return {
            "artist": artist.name,
            "slug": artist.slug,
            "title": req.title.strip(),
            "title_norm": title_norm,
            "already_suppressed": bool(existing),
            "releases_deleted": deleted,
        }
    finally:
        db.close()


@router.delete("/{slug}/suppressions/{suppression_id}")
def remove_suppression(slug: str, suppression_id: int):
    """Lift a suppression. The release returns on the next rebuild, not now."""
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")
        row = (
            db.query(ReleaseSuppression)
            .filter(
                ReleaseSuppression.id == suppression_id,
                ReleaseSuppression.artist_id == artist.id,
            )
            .first()
        )
        if not row:
            raise HTTPException(404, "Suppression not found")
        title = row.title_snapshot
        db.delete(row)
        db.commit()
        return {"removed": title, "note": "Re-run rebuild-releases to bring it back."}
    finally:
        db.close()


class RebuildReleasesRequest(BaseModel):
    notes: Optional[str] = None


@router.post("/{slug}/rebuild-releases")
async def rebuild_releases(
    slug: str,
    req: RebuildReleasesRequest = Body(default=None),
):
    """Purge the artist's MusicBrainz-derived releases, then re-resolve under the
    current first-appearance rule (RISING-COMPASS-ARTIST-RELEASES.md).

    resolve-metadata is additive (it never deletes), so it can't retroactively
    apply a tightened filter to releases already in the DB. This action does:

    1. Fetch the full MusicBrainz catalogue FIRST and refuse to go further if it
       is unavailable or empty (see below).
    2. Delete every Release for the artist that is MB-sourced
       (musicbrainz_id IS NOT NULL) OR the "Singles & Uncategorized" catch-all,
       plus their release_songs links. Album-Charger releases
       (source='album_charger') are left untouched.
    3. Apply the already-fetched catalogue, which re-creates the MB releases
       under the codified type/edition/hits/bootleg filters + tracklist dedup
       and re-links each song to every release it appears on.

    **The fetch precedes the purge deliberately.** It used to follow it, so a
    transient MusicBrainz 503 deleted the catalogue and then replaced it with
    nothing -- exactly what happened to The Beatles on 2026-08-13 (105 releases
    -> 0, then a truncated 18 on the retry). Nothing is deleted now until the
    replacement data is in hand, so an outage is a no-op instead of data loss.

    Idempotent and re-runnable. Audited as event_type 'rebuild_releases'.
    """
    catchall = "Singles & Uncategorized"

    # Phase 1 -- look up artist.
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")
        artist_id, artist_name, artist_slug = artist.id, artist.name, artist.slug
    finally:
        db.close()

    # Phase 1.5 -- fetch BEFORE destroying anything. MusicBrainzUnavailable means
    # a catalogue page died after its retries, so what we hold is a truncated
    # catalogue; writing it would silently shorten the artist. Bail with the
    # existing data untouched.
    try:
        mb_data = await _fetch_musicbrainz_data(artist_name)
    except MusicBrainzUnavailable as exc:
        logger.warning("rebuild-releases aborted for %s: %s", slug, exc)
        raise HTTPException(
            503,
            "MusicBrainz is not answering reliably right now, so the catalogue "
            "came back incomplete. Nothing was deleted -- retry later.",
        )
    if not mb_data or not mb_data.get("releases"):
        raise HTTPException(
            503,
            "MusicBrainz returned no releases for this artist. Nothing was "
            "deleted -- retry later.",
        )

    # Phase 2 -- purge MB-sourced releases + catch-all atomically; keep
    # album_charger. Delete links first to respect the FK.
    where = (
        " WHERE artist_id = :aid"
        "   AND source IS DISTINCT FROM 'album_charger'"
        "   AND (musicbrainz_id IS NOT NULL OR title = :catchall)"
    )
    params = {"aid": artist_id, "catchall": catchall}
    conn = engine.connect()
    try:
        deleted_links = conn.execute(
            text("DELETE FROM release_songs WHERE release_id IN (SELECT id FROM releases" + where + ")"),
            params,
        ).rowcount or 0
        deleted_releases = conn.execute(
            text("DELETE FROM releases" + where),
            params,
        ).rowcount or 0
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass
        logger.exception("rebuild-releases purge failed for %s", slug)
        raise HTTPException(500, "Rebuild failed during purge -- nothing committed.")
    finally:
        try: conn.close()
        except Exception: pass

    # Phase 3 -- apply the catalogue fetched in Phase 1.5 (no second fetch).
    stats = await resolve_artist_releases(artist_id, mb_data=mb_data)

    # Phase 4 -- audit + return.
    summary = {
        "deleted_releases": deleted_releases,
        "deleted_release_songs": deleted_links,
        **stats,
    }
    db = SessionLocal()
    try:
        db.add(ArtistAdminEvent(
            event_type="rebuild_releases",
            actor="admin",
            artist_id=artist_id,
            artist_name_before=artist_name,
            artist_slug_before=artist_slug,
            rewrites_json=json.dumps(summary),
            notes=(req.notes if req else None),
        ))
        db.commit()
    finally:
        db.close()

    _invalidate_artist_caches()

    return {"artist": artist_name, "slug": artist_slug, **summary}


def _get_distinct_artist_names(db, min_songs: int) -> list[str]:
    """Get distinct artist names from the unified Library with >= min_songs."""
    counts: dict[str, int] = {}
    name_map: dict[str, str] = {}  # lowercase -> display name
    rows = (
        db.query(Song.artist, func.count(Song.id))
        .filter(Song.charge_value.isnot(None))
        .filter(Song.artist.isnot(None))
        .group_by(func.lower(Song.artist))
        .all()
    )
    for name, count in rows:
        if name:
            key = name.lower()
            counts[key] = count
            name_map.setdefault(key, name)

    return [
        name_map[key]
        for key, count in sorted(counts.items(), key=lambda x: -x[1])
        if count >= min_songs and key in name_map
    ]


def _find_songs_for_artist(artist_name: str, db) -> list[tuple[str, int, int | None, bool]]:
    """Find all calibrated songs for an artist in the unified Library.

    Returns list of (source, song_id, charge_value, contaminated).
    """
    name_lower = artist_name.lower()
    results = []
    rows = (
        db.query(Song)
        .filter(func.lower(Song.artist) == name_lower)
        .filter(Song.charge_value.isnot(None))
        .filter(Song.artist.isnot(None))
        .all()
    )
    for row in rows:
        results.append(("songs", row.id, row.charge_value, row.contaminated or False))
    return results


# ============================================================
# Merge + rename (audited)
#
# These operations run many statements in one transaction via a single
# SQLAlchemy Core connection (raw text() so the intricate, well-tested
# dedupe SQL stays verbatim). On Postgres the whole transaction commits
# atomically with no stream to lose mid-flight.
# ============================================================

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
):
    """Merge the artist at `slug` into the artist at `target_slug`.

    Rewrites every FK + denormalised string column so the target absorbs
    everything the source pointed at, then deletes the source Artist row.
    Everything commits atomically in one transaction; the artist_admin_events
    audit row is written in the same transaction.

    Post-merge `releases.track_count / calibrated_count / charge_value /
    rubric_color` on the target may be slightly stale for any release that
    absorbed links from the source — call
    POST /api/admin/artists/{target_slug}/refresh-release-aggregates
    afterwards to normalise (fast, idempotent).
    """

    conn = engine.connect()
    try:
        row = conn.execute(
            text("SELECT id, name, slug FROM artists WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Source artist '{slug}' not found")
        source_id, source_name, source_slug = row

        row = conn.execute(
            text("SELECT id, name, slug FROM artists WHERE slug = :slug"),
            {"slug": req.target_slug},
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Target artist '{req.target_slug}' not found")
        target_id, target_name, target_slug = row

        if source_id == target_id:
            raise HTTPException(400, "Cannot merge an artist into itself")

        rewrites: dict = {}

        # 0. Song-row collisions. songs.canonical_key is UNIQUE; renaming the
        #    source artist's songs to the target name (step 3) would collide on
        #    canonical_key for any same-title song already under the target
        #    ("Sweet Caroline" under both "Neil Diamond" and "Neil Diamonds").
        #    For each such pair, repoint the source song's references onto the
        #    target song (keyed by song_id), then drop the source song.
        dup_song_rows_merged = 0
        dup_release_song_links_migrated = 0
        dup_release_song_links_dropped = 0
        dup_song_artist_links_migrated = 0
        dup_song_artist_links_dropped = 0
        dup_song_slugs_dropped = 0
        collisions = conn.execute(
            text(
                "SELECT src.id, tgt.id"
                "  FROM songs src"
                "  JOIN songs tgt ON lower(src.title) = lower(tgt.title)"
                " WHERE lower(src.artist) = lower(:sname)"
                "   AND lower(tgt.artist) = lower(:tname)"
                "   AND src.id != tgt.id"
            ),
            {"sname": source_name, "tname": target_name},
        ).fetchall()
        for src_sid, tgt_sid in collisions:
            # release_songs: drop links where the target is already on the same
            # release; move the rest from src -> tgt.
            cur = conn.execute(
                text(
                    "DELETE FROM release_songs WHERE song_id = :src"
                    "   AND release_id IN (SELECT release_id FROM release_songs WHERE song_id = :tgt)"
                ),
                {"src": src_sid, "tgt": tgt_sid},
            )
            dup_release_song_links_dropped += cur.rowcount or 0
            cur = conn.execute(
                text("UPDATE release_songs SET song_id = :tgt WHERE song_id = :src"),
                {"tgt": tgt_sid, "src": src_sid},
            )
            dup_release_song_links_migrated += cur.rowcount or 0

            # song_artists: dedupe on artist_id, then move.
            cur = conn.execute(
                text(
                    "DELETE FROM song_artists WHERE song_id = :src"
                    "   AND artist_id IN (SELECT artist_id FROM song_artists WHERE song_id = :tgt)"
                ),
                {"src": src_sid, "tgt": tgt_sid},
            )
            dup_song_artist_links_dropped += cur.rowcount or 0
            cur = conn.execute(
                text("UPDATE song_artists SET song_id = :tgt WHERE song_id = :src"),
                {"tgt": tgt_sid, "src": src_sid},
            )
            dup_song_artist_links_migrated += cur.rowcount or 0

            # song_slugs: target's are canonical; drop the source's.
            cur = conn.execute(
                text("DELETE FROM song_slugs WHERE song_id = :src"), {"src": src_sid}
            )
            dup_song_slugs_dropped += cur.rowcount or 0

            # user_calibrations: dedupe on user_id, then move (UNIQUE user+song).
            conn.execute(
                text(
                    "DELETE FROM user_calibrations WHERE song_id = :src"
                    "   AND user_id IN (SELECT user_id FROM user_calibrations WHERE song_id = :tgt)"
                ),
                {"src": src_sid, "tgt": tgt_sid},
            )
            conn.execute(
                text("UPDATE user_calibrations SET song_id = :tgt WHERE song_id = :src"),
                {"tgt": tgt_sid, "src": src_sid},
            )

            # No-per-song-unique references: repoint by unified id.
            for t in ("calibration_runs", "song_recalibrations", "song_recalibration_proposals",
                      "song_resets", "misread_submissions"):
                conn.execute(
                    text(f"UPDATE {t} SET song_id = :tgt WHERE song_id = :src"),
                    {"tgt": tgt_sid, "src": src_sid},
                )
            # Audience vibe (per-song unique): keep the target's, drop the source's.
            for t in ("audience_vibe_needles", "audience_vibe_pushes", "audience_vibe_review_cases"):
                conn.execute(text(f"DELETE FROM {t} WHERE song_id = :src"), {"src": src_sid})
            # Unified hard-FK refs + the id map.
            conn.execute(text("UPDATE reading_songs SET song_id = :tgt WHERE song_id = :src"), {"tgt": tgt_sid, "src": src_sid})
            conn.execute(text("UPDATE agent_draft_songs SET song_id = :tgt WHERE song_id = :src"), {"tgt": tgt_sid, "src": src_sid})
            conn.execute(text("UPDATE lc_events SET song_id = :tgt WHERE song_id = :src"), {"tgt": tgt_sid, "src": src_sid})
            conn.execute(text("UPDATE song_id_map SET new_song_id = :tgt WHERE new_song_id = :src"), {"tgt": tgt_sid, "src": src_sid})

            # Drop the source song (cascades chart_appearances + song_ingestions).
            conn.execute(text("DELETE FROM songs WHERE id = :src"), {"src": src_sid})
            dup_song_rows_merged += 1
        rewrites["dup_song_rows_merged"] = dup_song_rows_merged
        rewrites["dup_release_song_links_migrated"] = dup_release_song_links_migrated
        rewrites["dup_release_song_links_dropped"] = dup_release_song_links_dropped
        rewrites["dup_song_artist_links_migrated"] = dup_song_artist_links_migrated
        rewrites["dup_song_artist_links_dropped"] = dup_song_artist_links_dropped
        rewrites["dup_song_slugs_dropped"] = dup_song_slugs_dropped

        # 1. song_artists: dedupe against target's credits, then rewrite.
        cur = conn.execute(
            text(
                "DELETE FROM song_artists"
                " WHERE artist_id = :source_id"
                "   AND song_id IN ("
                "       SELECT song_id FROM song_artists WHERE artist_id = :target_id"
                "   )"
            ),
            {"source_id": source_id, "target_id": target_id},
        )
        rewrites["song_artists_dedup_dropped"] = cur.rowcount or 0

        cur = conn.execute(
            text("UPDATE song_artists SET artist_id = :target_id WHERE artist_id = :source_id"),
            {"target_id": target_id, "source_id": source_id},
        )
        rewrites["song_artists_reassigned"] = cur.rowcount or 0

        # 2. releases: handle UNIQUE(artist_id, title) collisions.
        target_title_to_id = {
            t: rid for rid, t in conn.execute(
                text("SELECT id, title FROM releases WHERE artist_id = :target_id"),
                {"target_id": target_id},
            ).fetchall()
        }
        releases_merged = 0
        releases_reassigned = 0
        release_songs_moved = 0
        release_songs_dropped_as_dup = 0
        for src_rel_id, src_title in conn.execute(
            text("SELECT id, title FROM releases WHERE artist_id = :source_id"),
            {"source_id": source_id},
        ).fetchall():
            if src_title in target_title_to_id:
                to_rel_id = target_title_to_id[src_title]
                existing = {
                    i for (i,) in conn.execute(
                        text("SELECT song_id FROM release_songs WHERE release_id = :rid"),
                        {"rid": to_rel_id},
                    ).fetchall()
                }
                for link_id, sid in conn.execute(
                    text("SELECT id, song_id FROM release_songs WHERE release_id = :rid"),
                    {"rid": src_rel_id},
                ).fetchall():
                    if sid in existing:
                        conn.execute(text("DELETE FROM release_songs WHERE id = :lid"), {"lid": link_id})
                        release_songs_dropped_as_dup += 1
                    else:
                        conn.execute(
                            text("UPDATE release_songs SET release_id = :to_rid WHERE id = :lid"),
                            {"to_rid": to_rel_id, "lid": link_id},
                        )
                        existing.add(sid)
                        release_songs_moved += 1
                conn.execute(text("DELETE FROM releases WHERE id = :rid"), {"rid": src_rel_id})
                releases_merged += 1
            else:
                conn.execute(
                    text("UPDATE releases SET artist_id = :target_id WHERE id = :rid"),
                    {"target_id": target_id, "rid": src_rel_id},
                )
                releases_reassigned += 1
        rewrites["releases_merged_into_existing"] = releases_merged
        rewrites["releases_reassigned"] = releases_reassigned
        rewrites["release_songs_moved"] = release_songs_moved
        rewrites["release_songs_dropped_as_dup"] = release_songs_dropped_as_dup

        # 3. Normalise the denormalised `artist` string column on songs AND
        #    recompute canonical_key (it derives from title + artist; a stale key
        #    would hide the song from canonical_key lookups and let a future
        #    calibration create a duplicate). Step 0 already removed the rows that
        #    would collide on the new key, so the UNIQUE holds.
        song_rows = conn.execute(
            text("SELECT id, title FROM songs WHERE lower(artist) = lower(:sname)"),
            {"sname": source_name},
        ).fetchall()
        for sid, title in song_rows:
            conn.execute(
                text("UPDATE songs SET artist = :tname, canonical_key = :key WHERE id = :id"),
                {"tname": target_name, "key": compute_canonical_key(title or "", target_name), "id": sid},
            )
        rewrites["songs_artist_string_rewritten"] = len(song_rows)

        # 4. Drop the source Artist row.
        conn.execute(text("DELETE FROM artists WHERE id = :source_id"), {"source_id": source_id})

        # 5. Audit event — same transaction.
        event_id = conn.execute(
            text(
                "INSERT INTO artist_admin_events"
                " (event_type, actor, artist_id, artist_name_before, artist_slug_before,"
                "  target_artist_id, target_artist_name, target_artist_slug,"
                "  rewrites_json, notes, occurred_at)"
                " VALUES (:event_type, :actor, :artist_id, :anb, :asb,"
                "  :target_id, :tan, :tas, :rewrites, :notes, now())"
                " RETURNING id"
            ),
            {
                "event_type": "merge", "actor": "admin",
                "artist_id": source_id, "anb": source_name, "asb": source_slug,
                "target_id": target_id, "tan": target_name, "tas": target_slug,
                "rewrites": json.dumps(rewrites), "notes": req.notes,
            },
        ).scalar_one()

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
):
    """Rename an artist (and optionally change its slug). Normalises the
    `artist` string column on the three song tables to match the new name.

    Runs as one atomic transaction over a single SQLAlchemy Core connection.
    """

    new_name = (req.new_name or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")

    # generate_artist_slug uses a SQLAlchemy session for its uniqueness probe;
    # do that read before opening the transaction connection below.
    derived_slug: Optional[str] = None

    conn = engine.connect()
    try:
        row = conn.execute(
            text("SELECT id, name, slug FROM artists WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Artist '{slug}' not found")
        artist_id, old_name, old_slug = row

        new_slug = req.new_slug.strip() if req.new_slug else None
        if new_slug:
            collision = conn.execute(
                text("SELECT name FROM artists WHERE slug = :new_slug AND id != :aid"),
                {"new_slug": new_slug, "aid": artist_id},
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
        # Rewrite the denormalised artist string on songs AND recompute
        # canonical_key (derives from title + artist). A canonical_key collision
        # with an existing song raises IntegrityError -> 500 (same as the legacy
        # per-table UNIQUE behavior); use merge-into to resolve real duplicates.
        song_rows = conn.execute(
            text("SELECT id, title FROM songs WHERE lower(artist) = lower(:old_name)"),
            {"old_name": old_name},
        ).fetchall()
        for sid, title in song_rows:
            conn.execute(
                text("UPDATE songs SET artist = :new_name, canonical_key = :key WHERE id = :id"),
                {"new_name": new_name, "key": compute_canonical_key(title or "", new_name), "id": sid},
            )
        rewrites["songs_artist_string_rewritten"] = len(song_rows)

        if new_slug:
            conn.execute(
                text("UPDATE artists SET name = :new_name, slug = :new_slug WHERE id = :aid"),
                {"new_name": new_name, "new_slug": new_slug, "aid": artist_id},
            )
        else:
            conn.execute(
                text("UPDATE artists SET name = :new_name WHERE id = :aid"),
                {"new_name": new_name, "aid": artist_id},
            )

        event_id = conn.execute(
            text(
                "INSERT INTO artist_admin_events"
                " (event_type, actor, artist_id, artist_name_before, artist_slug_before,"
                "  artist_name_after, artist_slug_after, rewrites_json, notes, occurred_at)"
                " VALUES (:event_type, :actor, :artist_id, :anb, :asb,"
                "  :ana, :asa, :rewrites, :notes, now())"
                " RETURNING id"
            ),
            {
                "event_type": "rename", "actor": "admin",
                "artist_id": artist_id, "anb": old_name, "asb": old_slug,
                "ana": new_name, "asa": new_slug or old_slug,
                "rewrites": json.dumps(rewrites), "notes": req.notes,
            },
        ).scalar_one()

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
):
    """Audit log — most recent first."""
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


# --------------------------------------------------------------------------
# Read surface (added 2026-08-12)
#
# Until now this router was write-only: every endpoint above is a POST, plus
# GET /events. There was no way to LIST artists or look one up, so the admin
# had no page and every artist operation meant curl with a session cookie.
# These are pure additions; nothing above changed.
# --------------------------------------------------------------------------

@router.get("")
def list_artists(
    q: Optional[str] = Query(None, description="name substring"),
    has_releases: Optional[bool] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    """Artists with their release and song counts, newest-catalog first."""
    db = SessionLocal()
    try:
        rel_counts = (
            db.query(Release.artist_id, func.count(Release.id).label("n"))
            .group_by(Release.artist_id).subquery()
        )
        song_counts = (
            db.query(SongArtist.artist_id, func.count(SongArtist.song_id).label("n"))
            .group_by(SongArtist.artist_id).subquery()
        )
        query = (
            db.query(
                Artist,
                func.coalesce(rel_counts.c.n, 0).label("release_count"),
                func.coalesce(song_counts.c.n, 0).label("song_count"),
            )
            .outerjoin(rel_counts, rel_counts.c.artist_id == Artist.id)
            .outerjoin(song_counts, song_counts.c.artist_id == Artist.id)
        )
        if q:
            query = query.filter(func.lower(Artist.name).like(f"%{q.strip().lower()}%"))
        if has_releases is True:
            query = query.filter(func.coalesce(rel_counts.c.n, 0) > 0)
        elif has_releases is False:
            query = query.filter(func.coalesce(rel_counts.c.n, 0) == 0)

        total = query.count()
        rows = (query.order_by(func.coalesce(song_counts.c.n, 0).desc(), Artist.name)
                .offset(offset).limit(limit).all())
        return {
            "total": total,
            "artists": [{
                "id": a.id, "name": a.name, "slug": a.slug,
                "musicbrainz_id": a.musicbrainz_id, "spotify_id": a.spotify_id,
                "release_count": rc, "song_count": sc,
            } for a, rc, sc in rows],
        }
    finally:
        db.close()


@router.get("/{slug}")
def get_artist(slug: str):
    """One artist with every release and every credited song."""
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        releases = (
            db.query(Release).filter(Release.artist_id == artist.id)
            .order_by(Release.release_year.desc().nullslast(), Release.title).all()
        )
        songs = (
            db.query(Song).join(SongArtist, SongArtist.song_id == Song.id)
            .filter(SongArtist.artist_id == artist.id)
            .order_by(Song.title).all()
        )
        return {
            "id": artist.id, "name": artist.name, "slug": artist.slug,
            "musicbrainz_id": artist.musicbrainz_id, "spotify_id": artist.spotify_id,
            "releases": [{
                "id": r.id, "title": r.title, "release_type": r.release_type,
                "release_year": r.release_year,
                "release_date": r.release_date.isoformat() if r.release_date else None,
                "rubric_color": r.rubric_color, "charge_value": r.charge_value,
                "track_count": r.track_count, "calibrated_count": r.calibrated_count,
                "contamination_count": r.contamination_count,
                "source": r.source, "musicbrainz_id": r.musicbrainz_id,
                "has_reading": bool(r.charge_summary),
            } for r in releases],
            "songs": [{
                "id": s.id, "title": s.title, "artist": s.artist,
                "rubric_color": s.rubric_color, "charge_value": s.charge_value,
                "contaminated": bool(s.contaminated),
            } for s in songs],
        }
    finally:
        db.close()
