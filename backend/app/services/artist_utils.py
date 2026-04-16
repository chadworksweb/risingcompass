"""Artist utility functions: slug generation, name normalization, charge computation, release resolution."""

import logging
import re
import unicodedata

from sqlalchemy import func

from app.models import Artist, Release, ReleaseSong, CompassSong, LibrarySong, SubmittedSong
from app.constants import COLOR_LABELS, COLOR_HEX

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def generate_artist_slug(name: str, db) -> str:
    """Generate a unique slug for an artist, appending -2, -3 etc on collision."""
    base = slugify(name)
    if not base:
        base = "artist"
    slug = base
    suffix = 2
    while db.query(Artist).filter(Artist.slug == slug).first():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def normalize_artist_name(name: str) -> str:
    """Normalize artist name for matching. Takes primary artist only."""
    name = name.strip()
    # Split on feat/ft/with/& and take primary
    for sep in [" feat.", " feat ", " ft.", " ft ", " with ", " & ", ", "]:
        if sep in name.lower():
            idx = name.lower().index(sep)
            name = name[:idx].strip()
            break
    return name


def derive_tier(charge: int) -> tuple[str, str, str]:
    """Derive rubric_color, tier_label, tier_hex from a charge value.

    Mirrors _derive_tier in badge.py — single source of truth candidate.
    """
    tiers = [
        (75, "violet"), (25, "blue"), (-24, "green"),
        (-74, "orange"), (-100, "red"),
    ]
    color = "red"
    for threshold, c in tiers:
        if charge >= threshold:
            color = c
            break
    return color, COLOR_LABELS[color], COLOR_HEX[color]


def compute_release_charge(charge_values: list[int]) -> tuple[int, str, str, str] | None:
    """Compute aggregate charge for a release from its song charges.

    Returns (avg_charge, rubric_color, tier_label, tier_hex) or None if no values.
    """
    if not charge_values:
        return None
    avg = round(sum(charge_values) / len(charge_values))
    color, label, hex_val = derive_tier(avg)
    return avg, color, label, hex_val


def count_songs_by_artist(artist_name: str, db) -> int:
    """Count total calibrated songs across all three tables for an artist."""
    name_lower = artist_name.lower()
    total = 0
    for Model in (CompassSong, LibrarySong, SubmittedSong):
        count = (
            db.query(func.count(Model.id))
            .filter(func.lower(Model.artist) == name_lower)
            .filter(Model.charge_value.isnot(None))
        )
        # SubmittedSong has nullable title/artist
        if Model is SubmittedSong:
            count = count.filter(Model.artist.isnot(None))
        total += count.scalar()
    return total


def generate_song_slug(title: str, artist: str) -> str:
    """Generate a URL slug from song title + artist."""
    return slugify(f"{title} {artist}")


async def resolve_artist_releases(artist: Artist, db) -> dict:
    """Resolve release metadata for an artist via MusicBrainz (primary) or Spotify (fallback).

    Creates Release rows, links existing calibrated songs to them, computes charges.
    Returns stats dict.
    """
    from app.services import musicbrainz

    stats = {"source": None, "releases_created": 0, "songs_linked": 0}

    # Collect all calibrated songs for this artist
    all_songs = _find_artist_songs(artist.name, db)
    if not all_songs:
        return stats

    # Try MusicBrainz first
    mb_releases = await _resolve_via_musicbrainz(artist, all_songs, db)
    if mb_releases:
        stats["source"] = "musicbrainz"
        stats["releases_created"] = mb_releases["releases_created"]
        stats["songs_linked"] = mb_releases["songs_linked"]
    else:
        # Try Spotify fallback
        sp_releases = await _resolve_via_spotify(artist, all_songs, db)
        if sp_releases:
            stats["source"] = "spotify"
            stats["releases_created"] = sp_releases["releases_created"]
            stats["songs_linked"] = sp_releases["songs_linked"]

    # Any remaining unlinked songs go into "Singles & Uncategorized"
    linked_song_keys = _get_linked_song_keys(artist, db)
    unlinked = [(s, sid, ch, co) for s, sid, ch, co in all_songs if (s, sid) not in linked_song_keys]

    if unlinked:
        catch_all = (
            db.query(Release)
            .filter(Release.artist_id == artist.id, Release.title == "Singles & Uncategorized")
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

        charges = []
        contam = 0
        for source, sid, charge, is_contam in unlinked:
            db.add(ReleaseSong(release_id=catch_all.id, song_source=source, song_id=sid))
            if charge is not None:
                charges.append(charge)
            if is_contam:
                contam += 1
            stats["songs_linked"] += 1

        catch_all.track_count = len(unlinked)
        catch_all.calibrated_count = len(charges)
        catch_all.contamination_count = contam
        if charges:
            result = compute_release_charge(charges)
            if result:
                catch_all.charge_value = result[0]
                catch_all.rubric_color = result[1]

    return stats


async def _resolve_via_musicbrainz(artist: Artist, all_songs, db) -> dict | None:
    """Try to resolve releases from MusicBrainz."""
    from app.services import musicbrainz

    # Search for the artist
    mb_artists = await musicbrainz.search_artist(artist.name, limit=3)
    if not mb_artists:
        return None

    # Pick best match (highest score)
    mb_artist = mb_artists[0]
    if mb_artist["score"] < 80:
        logger.info("MusicBrainz: best match for '%s' scored %d, skipping", artist.name, mb_artist["score"])
        return None

    # Store the MBID
    artist.musicbrainz_id = mb_artist["mbid"]

    # Get releases
    mb_releases = await musicbrainz.get_artist_releases(mb_artist["mbid"])
    if not mb_releases:
        return None

    stats = {"releases_created": 0, "songs_linked": 0}

    for mb_rel in mb_releases:
        # Check if release already exists
        existing = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .filter(func.lower(Release.title) == mb_rel["title"].lower())
            .first()
        )
        if existing:
            continue

        release = Release(
            artist_id=artist.id,
            title=mb_rel["title"],
            release_type=mb_rel["release_type"],
            release_date=mb_rel["release_date"],
            release_year=mb_rel["release_year"],
            musicbrainz_id=mb_rel["mbid"],
        )
        db.add(release)
        db.flush()
        stats["releases_created"] += 1

        # Try to get tracks and match against calibrated songs
        tracks = await musicbrainz.get_release_tracks(mb_rel["mbid"])
        charges = []
        contam = 0
        calibrated = 0

        for track in tracks:
            matched = _match_song_to_track(track["title"], artist.name, all_songs, db)
            if matched:
                source, sid, charge, is_contam = matched
                # Check not already linked
                existing_link = (
                    db.query(ReleaseSong)
                    .filter(ReleaseSong.song_source == source, ReleaseSong.song_id == sid)
                    .first()
                )
                if not existing_link:
                    db.add(ReleaseSong(
                        release_id=release.id,
                        song_source=source,
                        song_id=sid,
                        track_number=track["position"],
                    ))
                    stats["songs_linked"] += 1
                if charge is not None:
                    charges.append(charge)
                    calibrated += 1
                if is_contam:
                    contam += 1

        release.track_count = len(tracks)
        release.calibrated_count = calibrated
        release.contamination_count = contam
        if charges:
            result = compute_release_charge(charges)
            if result:
                release.charge_value = result[0]
                release.rubric_color = result[1]

    return stats if stats["releases_created"] > 0 else None


async def _resolve_via_spotify(artist: Artist, all_songs, db) -> dict | None:
    """Try to resolve releases from Spotify."""
    from app.config import settings

    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return None

    try:
        import httpx

        # Get token
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]

            # Search for artist
            search_resp = await client.get(
                "https://api.spotify.com/v1/search",
                params={"q": artist.name, "type": "artist", "limit": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
            search_resp.raise_for_status()
            artists_data = search_resp.json()["artists"]["items"]

            if not artists_data:
                return None

            sp_artist = artists_data[0]
            artist.spotify_id = sp_artist["id"]

            # Get albums
            albums_resp = await client.get(
                f"https://api.spotify.com/v1/artists/{sp_artist['id']}/albums",
                params={"include_groups": "album,single", "limit": 50},
                headers={"Authorization": f"Bearer {token}"},
            )
            albums_resp.raise_for_status()
            sp_albums = albums_resp.json()["items"]

        if not sp_albums:
            return None

        stats = {"releases_created": 0, "songs_linked": 0}

        for sp_album in sp_albums:
            # Check if release already exists
            existing = (
                db.query(Release)
                .filter(Release.artist_id == artist.id)
                .filter(func.lower(Release.title) == sp_album["name"].lower())
                .first()
            )
            if existing:
                continue

            # Map album_type
            sp_type = sp_album.get("album_type", "single").lower()
            release_type = {"album": "album", "single": "single", "compilation": "album"}.get(sp_type, "single")

            # Parse date
            from app.services.musicbrainz import _parse_mb_date
            release_date, release_year = _parse_mb_date(sp_album.get("release_date", ""))

            release = Release(
                artist_id=artist.id,
                title=sp_album["name"],
                release_type=release_type,
                release_date=release_date,
                release_year=release_year,
                spotify_id=sp_album["id"],
                track_count=sp_album.get("total_tracks", 0),
            )
            db.add(release)
            db.flush()
            stats["releases_created"] += 1

        return stats if stats["releases_created"] > 0 else None

    except Exception:
        logger.exception("Spotify release resolution failed for '%s'", artist.name)
        return None


def _find_artist_songs(artist_name: str, db) -> list[tuple[str, int, int | None, bool]]:
    """Find all calibrated songs for an artist. Returns (source, id, charge, contaminated)."""
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
            results.append((source, row.id, row.charge_value, getattr(row, "contaminated", False) or False))
    return results


def _get_linked_song_keys(artist: Artist, db) -> set[tuple[str, int]]:
    """Get set of (source, song_id) already linked to any of this artist's releases."""
    keys = set()
    for release in artist.releases:
        for link in release.songs:
            keys.add((link.song_source, link.song_id))
    return keys


def _match_song_to_track(track_title: str, artist_name: str, all_songs, db) -> tuple | None:
    """Try to match a track title against calibrated songs using fuzzy matching."""
    track_lower = track_title.lower()
    track_stripped = re.sub(r"[^\w\s]", "", track_lower)

    name_lower = artist_name.lower()

    for source, Model in [("compass", CompassSong), ("library", LibrarySong), ("submitted", SubmittedSong)]:
        # Exact match
        row = (
            db.query(Model)
            .filter(func.lower(Model.title) == track_lower)
            .filter(func.lower(Model.artist) == name_lower)
            .filter(Model.charge_value.isnot(None))
            .first()
        )
        if row:
            return (source, row.id, row.charge_value, getattr(row, "contaminated", False) or False)

        # Fuzzy match (strip punctuation)
        candidates = (
            db.query(Model)
            .filter(func.lower(Model.artist) == name_lower)
            .filter(Model.charge_value.isnot(None))
            .all()
        )
        for c in candidates:
            if re.sub(r"[^\w\s]", "", c.title.lower()) == track_stripped:
                return (source, c.id, c.charge_value, getattr(c, "contaminated", False) or False)

    return None
