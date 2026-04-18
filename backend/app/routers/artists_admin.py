"""Artist admin API — bootstrap, manual creation, refresh, metadata resolution."""

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Artist, Release, ReleaseSong,
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

            # Link existing songs — create a single "Uncategorized" release
            # for now. Release metadata resolution (MusicBrainz/Spotify) is Phase 3.
            song_links = _find_songs_for_artist(normalized, db)
            if song_links:
                release = Release(
                    artist_id=artist.id,
                    title="Singles & Uncategorized",
                    release_type="single",
                    track_count=len(song_links),
                    calibrated_count=len(song_links),
                )
                db.add(release)
                db.flush()

                charges = []
                contam = 0
                for source, sid, charge, is_contam in song_links:
                    link = ReleaseSong(
                        release_id=release.id,
                        song_source=source,
                        song_id=sid,
                    )
                    db.add(link)
                    if charge is not None:
                        charges.append(charge)
                    if is_contam:
                        contam += 1

                if charges:
                    result = compute_release_charge(charges)
                    if result:
                        release.charge_value = result[0]
                        release.rubric_color = result[1]
                release.contamination_count = contam

            created.append({"name": normalized, "slug": slug, "songs": len(song_links)})

        db.commit()

        return {
            "created": len(created),
            "skipped": len(skipped),
            "artists": created,
        }
    finally:
        db.close()


@router.post("/{slug}/refresh-releases")
def refresh_releases(
    slug: str,
    x_admin_key: str = Header(...),
):
    """Re-link songs for an artist from the three song tables.

    This does NOT call MusicBrainz/Spotify (that's Phase 3).
    It re-scans the song tables and updates the catch-all release.
    """
    _require_admin(x_admin_key)
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            raise HTTPException(404, "Artist not found")

        # Find all songs for this artist
        song_links = _find_songs_for_artist(artist.name, db)

        # Get or create catch-all release
        catch_all = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .filter(Release.title == "Singles & Uncategorized")
            .first()
        )
        if not catch_all:
            catch_all = Release(
                artist_id=artist.id,
                title="Singles & Uncategorized",
                release_type="single",
            )
            db.add(catch_all)
            db.flush()

        # Get existing linked song IDs (across all releases for this artist)
        existing_links = set()
        for r in artist.releases:
            for link in r.songs:
                existing_links.add((link.song_source, link.song_id))

        # Add new songs to catch-all
        new_count = 0
        charges = []
        contam = 0
        for source, sid, charge, is_contam in song_links:
            if (source, sid) not in existing_links:
                db.add(ReleaseSong(
                    release_id=catch_all.id,
                    song_source=source,
                    song_id=sid,
                ))
                new_count += 1
            if charge is not None:
                charges.append(charge)
            if is_contam:
                contam += 1

        # Update catch-all stats
        total_linked = len(existing_links) + new_count
        catch_all.track_count = total_linked
        catch_all.calibrated_count = len(charges)
        catch_all.contamination_count = contam
        if charges:
            result = compute_release_charge(charges)
            if result:
                catch_all.charge_value = result[0]
                catch_all.rubric_color = result[1]

        db.commit()

        return {
            "artist": artist.name,
            "slug": artist.slug,
            "new_songs_linked": new_count,
            "total_songs": total_linked,
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
