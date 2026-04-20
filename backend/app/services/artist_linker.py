"""Artist linking — parse multi-artist credit strings, upsert artists,
write song→artist attribution via song_artists, and preserve the existing
release_songs catch-all linkage so artist trajectory pages keep working.

Two shapes of input supported:
  - a raw credit string ("Drake feat. 21 Savage & Metro Boomin")
  - a structured list ([{name, role, position}]) — overrides parsing

All ingestion paths (LC submit, compass agent approve, stream promotion,
admin library CRUD) route through `try_link_song`. Best-effort: failures
are logged but never raised, since linking is ancillary to classification.
"""

import logging
import re

from sqlalchemy import func

from app.models import Artist, Release, ReleaseSong, SongArtist
from app.services.artist_utils import generate_artist_slug

logger = logging.getLogger(__name__)


# Featured-role markers (case-insensitive). Ordered longest-first so
# "featuring" matches before "feat" inside the same string.
_FEATURE_MARKERS = [" featuring ", " feat. ", " feat ", " ft. ", " ft "]

# Multi-primary separators. Matched after features are stripped out.
# r'\s+x\s+' handles "Drake x Future"; comma and ampersand are the common pair.
_PRIMARY_SPLIT = re.compile(r"\s*(?:,|&)\s*|\s+[xX]\s+")


def parse_artist_string(raw: str | None) -> list[dict]:
    """Split a credit string into ordered {name, role, position} entries.

    "Lady Gaga & Doechi"             → [Gaga primary 0, Doechi primary 1]
    "Drake feat. 21 Savage"          → [Drake primary 0, 21 Savage featured 1]
    "Drake, Future & Metro ft. Thug" → [Drake p0, Future p1, Metro p2, Thug f3]
    """
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []

    primary_part = text
    featured_part = ""
    lowered = text.lower()
    for marker in _FEATURE_MARKERS:
        idx = lowered.find(marker)
        if idx >= 0:
            primary_part = text[:idx]
            featured_part = text[idx + len(marker):]
            break

    primaries = [n for n in _PRIMARY_SPLIT.split(primary_part) if n and n.strip()]
    features = [n for n in _PRIMARY_SPLIT.split(featured_part) if n and n.strip()] if featured_part else []

    entries: list[dict] = []
    pos = 0
    for name in primaries:
        entries.append({"name": name.strip(), "role": "primary", "position": pos})
        pos += 1
    for name in features:
        entries.append({"name": name.strip(), "role": "featured", "position": pos})
        pos += 1
    return entries


def format_artist_string(entries: list[dict]) -> str:
    """Inverse of parse: build a canonical credit string from structured entries.

    Primaries joined with " & ", then "feat." + features joined with " & ".
    """
    if not entries:
        return ""
    primaries = [e["name"].strip() for e in entries if e.get("role", "primary") == "primary" and e.get("name")]
    features = [e["name"].strip() for e in entries if e.get("role") == "featured" and e.get("name")]
    out = " & ".join(primaries)
    if features:
        out += " feat. " + " & ".join(features)
    return out


def upsert_artist(db, name: str) -> Artist:
    """Find an artist by case-insensitive name, or create with a deduped slug."""
    existing = (
        db.query(Artist)
        .filter(func.lower(Artist.name) == name.lower())
        .first()
    )
    if existing:
        return existing
    slug = generate_artist_slug(name, db)
    artist = Artist(name=name, slug=slug)
    db.add(artist)
    db.flush()
    return artist


def _link_to_catchall_release(db, artist: Artist, song_source: str, song_id: int):
    """Ensure the song appears on the artist's 'Singles & Uncategorized' release.

    Preserves the existing artist trajectory flow. No-op if a release_songs row
    for this song already exists (under any release) so we don't duplicate.
    """
    existing = (
        db.query(ReleaseSong)
        .filter(ReleaseSong.song_source == song_source)
        .filter(ReleaseSong.song_id == song_id)
        .first()
    )
    if existing:
        return

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

    db.add(ReleaseSong(
        release_id=catch_all.id,
        song_source=song_source,
        song_id=song_id,
    ))
    catch_all.track_count = (catch_all.track_count or 0) + 1
    catch_all.calibrated_count = (catch_all.calibrated_count or 0) + 1


def link_song_artists(
    db,
    *,
    song_source: str,
    song_id: int,
    entries: list[dict],
) -> None:
    """Upsert song_artists rows and link the first primary via release_songs."""
    if not entries:
        return

    first_primary_linked = False
    for e in entries:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        role = e.get("role") or "primary"
        if role not in ("primary", "featured"):
            role = "primary"
        position = int(e.get("position", 0))

        artist = upsert_artist(db, name)

        existing = (
            db.query(SongArtist)
            .filter(SongArtist.song_source == song_source)
            .filter(SongArtist.song_id == song_id)
            .filter(SongArtist.artist_id == artist.id)
            .first()
        )
        if not existing:
            db.add(SongArtist(
                song_source=song_source,
                song_id=song_id,
                artist_id=artist.id,
                role=role,
                position=position,
            ))

        if role == "primary" and not first_primary_linked:
            _link_to_catchall_release(db, artist, song_source, song_id)
            first_primary_linked = True


def try_link_song(
    title: str | None,
    artist_name: str | None,
    song_source: str,
    song_id: int,
    db,
    *,
    structured: list[dict] | None = None,
):
    """Link a newly calibrated song to one or more artists via song_artists.

    If `structured` is provided (LC form submits structured artist rows),
    it overrides parsing. Otherwise `artist_name` is parsed by
    parse_artist_string. Best-effort — exceptions are caught and logged.
    """
    if not title:
        return
    entries = structured if structured else parse_artist_string(artist_name or "")
    if not entries:
        return

    try:
        link_song_artists(
            db,
            song_source=song_source,
            song_id=song_id,
            entries=entries,
        )
        db.commit()
        names = ", ".join(f"{e['name']}({e['role']})" for e in entries)
        logger.info("Linked '%s' (%s:%d) to [%s]", title, song_source, song_id, names)
    except Exception:
        db.rollback()
        logger.exception("Auto-link failed for '%s' by '%s'", title, artist_name)
