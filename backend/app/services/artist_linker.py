"""Lightweight auto-linker: when a new song is calibrated, try to link it to an existing artist entity.

Non-blocking, best-effort. If the artist doesn't exist as an entity, does nothing.
If the artist exists but no matching release is found, links to the catch-all release.
"""

import logging

from sqlalchemy import func

from app.models import Artist, Release, ReleaseSong

logger = logging.getLogger(__name__)


def try_link_song(title: str | None, artist_name: str | None, song_source: str, song_id: int, db):
    """Attempt to link a newly calibrated song to an existing artist/release.

    Called after song insertion in analyzer, agent, and stream routers.
    Silently returns if artist doesn't exist as an entity or song is already linked.
    """
    if not title or not artist_name:
        return

    try:
        # Find artist entity
        artist = (
            db.query(Artist)
            .filter(func.lower(Artist.name) == artist_name.lower())
            .first()
        )
        if not artist:
            return  # Artist not indexed yet — nothing to link

        # Check if this song is already linked
        existing = (
            db.query(ReleaseSong)
            .filter(ReleaseSong.song_source == song_source)
            .filter(ReleaseSong.song_id == song_id)
            .first()
        )
        if existing:
            return  # Already linked

        # Find or create catch-all release
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
                track_count=0,
                calibrated_count=0,
            )
            db.add(catch_all)
            db.flush()

        # Link the song
        db.add(ReleaseSong(
            release_id=catch_all.id,
            song_source=song_source,
            song_id=song_id,
        ))

        # Update counts
        catch_all.track_count = (catch_all.track_count or 0) + 1
        catch_all.calibrated_count = (catch_all.calibrated_count or 0) + 1

        db.commit()
        logger.info("Auto-linked '%s' by '%s' (%s:%d) to artist '%s'",
                     title, artist_name, song_source, song_id, artist.name)

    except Exception:
        logger.exception("Auto-link failed for '%s' by '%s'", title, artist_name)
        # Don't re-raise — this is best-effort
