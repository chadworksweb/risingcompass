"""Artist admin API — bootstrap, manual creation, refresh, metadata resolution."""

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Artist, Release,
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
