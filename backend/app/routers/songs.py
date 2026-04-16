"""Songs API — public endpoints for individual song pages (effects label)."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import func

from app.database import SessionLocal
from app.models import (
    CompassSong, LibrarySong, SubmittedSong, SongSlug,
    ReleaseSong, Release, Artist,
)
from app.constants import COLOR_LABELS, COLOR_HEX
from app.services.artist_utils import generate_song_slug

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/songs", tags=["songs"])


@router.get("/{slug}")
def song_detail(slug: str):
    """Look up a song by slug and return full classification data for the effects label page."""
    db = SessionLocal()
    try:
        # Check slug lookup table first
        slug_row = db.query(SongSlug).filter(SongSlug.slug == slug).first()

        if slug_row:
            song = _resolve_song(slug_row.song_source, slug_row.song_id, db)
            if song:
                song["slug"] = slug
                _enrich_with_release_context(song, slug_row.song_source, slug_row.song_id, db)
                return song

        # Fallback: try to match slug against generated slugs
        song = _find_by_generated_slug(slug, db)
        if song:
            return song

        raise HTTPException(404, "Song not found")
    finally:
        db.close()


@router.get("")
def song_search(q: str = "", limit: int = 20):
    """Search songs by title across all tables. Returns matches with slugs."""
    db = SessionLocal()
    try:
        if len(q.strip()) < 2:
            return {"results": []}

        q_lower = q.strip().lower()
        results = []
        seen = set()  # (title_lower, artist_lower) to dedupe

        for source, Model in [("compass", CompassSong), ("library", LibrarySong), ("submitted", SubmittedSong)]:
            query = (
                db.query(Model)
                .filter(func.lower(Model.title).contains(q_lower))
                .filter(Model.charge_value.isnot(None))
            )
            if Model is SubmittedSong:
                query = query.filter(Model.title.isnot(None), Model.artist.isnot(None))
            for row in query.limit(limit).all():
                key = (row.title.lower(), (row.artist or "").lower())
                if key in seen:
                    continue
                seen.add(key)

                slug = _get_or_create_slug(row.title, row.artist or "", source, row.id, db)
                results.append({
                    "title": row.title,
                    "artist": getattr(row, "artist", None),
                    "slug": slug,
                    "rubric_color": row.rubric_color,
                    "charge_value": row.charge_value,
                    "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
                    "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        return {"results": results}
    finally:
        db.close()


def _resolve_song(source: str, song_id: int, db) -> dict | None:
    """Resolve a polymorphic song reference to a full display dict."""
    model_map = {"compass": CompassSong, "library": LibrarySong, "submitted": SubmittedSong}
    model = model_map.get(source)
    if not model:
        return None
    row = db.query(model).get(song_id)
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


def _enrich_with_release_context(song: dict, source: str, song_id: int, db):
    """Add release + artist context to a song dict if available."""
    link = (
        db.query(ReleaseSong)
        .filter(ReleaseSong.song_source == source)
        .filter(ReleaseSong.song_id == song_id)
        .first()
    )
    if link:
        release = db.query(Release).get(link.release_id)
        if release:
            song["release_title"] = release.title
            song["release_type"] = release.release_type
            song["release_date"] = release.release_date.isoformat() if release.release_date else None
            artist = db.query(Artist).get(release.artist_id)
            if artist:
                song["artist_slug"] = artist.slug


def _find_by_generated_slug(slug: str, db) -> dict | None:
    """Try to match a slug by generating slugs from all songs."""
    # This is a fallback for songs not yet in the slug table.
    # Search the most common tables first.
    for source, Model in [("compass", CompassSong), ("library", LibrarySong), ("submitted", SubmittedSong)]:
        query = db.query(Model).filter(Model.charge_value.isnot(None))
        if Model is SubmittedSong:
            query = query.filter(Model.title.isnot(None), Model.artist.isnot(None))
        for row in query.all():
            generated = generate_song_slug(row.title, row.artist or "")
            if generated == slug:
                # Create slug entry for faster future lookups
                _get_or_create_slug(row.title, row.artist or "", source, row.id, db)
                song = _resolve_song(source, row.id, db)
                if song:
                    song["slug"] = slug
                    _enrich_with_release_context(song, source, row.id, db)
                return song
    return None


def _get_or_create_slug(title: str, artist: str, source: str, song_id: int, db) -> str:
    """Get existing slug or create one for a song."""
    # Check if this song already has a slug
    existing = (
        db.query(SongSlug)
        .filter(SongSlug.song_source == source)
        .filter(SongSlug.song_id == song_id)
        .first()
    )
    if existing:
        return existing.slug

    slug = generate_song_slug(title, artist)

    # Handle collision
    base_slug = slug
    suffix = 2
    while db.query(SongSlug).filter(SongSlug.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    entry = SongSlug(
        slug=slug,
        title=title,
        artist=artist,
        song_source=source,
        song_id=song_id,
    )
    db.add(entry)
    db.commit()
    return slug
