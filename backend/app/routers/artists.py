"""Artist Trajectory API — public endpoints for artist search and trajectory display."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func

from app.database import SessionLocal
from app.models import (
    Artist, Release, ReleaseSong,
    CompassSong, LibrarySong, SubmittedSong, SongSlug,
)
from app.constants import COLOR_LABELS, COLOR_HEX
from app.services.artist_utils import (
    count_songs_by_artist, derive_tier, generate_song_slug,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/artists", tags=["artists"])


@router.get("/search")
def artist_search(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=50),
):
    """Search artists by name. Also returns unindexed matches from song tables."""
    db = SessionLocal()
    try:
        q_lower = q.strip().lower()

        # Search indexed artists
        artists = (
            db.query(Artist)
            .filter(func.lower(Artist.name).contains(q_lower))
            .limit(limit)
            .all()
        )

        results = []
        indexed_names = set()

        for a in artists:
            release_count = (
                db.query(func.count(Release.id))
                .filter(Release.artist_id == a.id)
                .scalar()
            )
            song_count = count_songs_by_artist(a.name, db)
            results.append({
                "name": a.name,
                "slug": a.slug,
                "release_count": release_count,
                "calibrated_song_count": song_count,
                "indexed": True,
            })
            indexed_names.add(a.name.lower())

        # Find unindexed artist names in song tables (not yet bootstrapped)
        if len(results) < limit:
            remaining = limit - len(results)
            unindexed_names = set()

            for Model in (CompassSong, LibrarySong, SubmittedSong):
                query = (
                    db.query(func.distinct(Model.artist))
                    .filter(func.lower(Model.artist).contains(q_lower))
                    .filter(Model.charge_value.isnot(None))
                )
                if Model is SubmittedSong:
                    query = query.filter(Model.artist.isnot(None))
                for (name,) in query.limit(remaining).all():
                    if name and name.lower() not in indexed_names:
                        unindexed_names.add(name)

            for name in sorted(unindexed_names)[:remaining]:
                song_count = count_songs_by_artist(name, db)
                if song_count > 0:
                    results.append({
                        "name": name,
                        "slug": None,
                        "release_count": 0,
                        "calibrated_song_count": song_count,
                        "indexed": False,
                    })

        return {"results": results}
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
