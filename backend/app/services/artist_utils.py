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


async def resolve_artist_releases(artist_id: int) -> dict:
    """Resolve release metadata for an artist via MB (primary) or Spotify (fallback).

    Fetches MB/Spotify data into memory *without* holding a DB session, then
    opens a fresh session for writes. The Turso libSQL stream can close
    server-side during long idle windows (MB is rate-limited to 1 req/sec, so
    a Beatles-sized catalog is minutes of calls); separating phases prevents
    `stream not found` errors.

    Returns stats dict: {source, releases_created, songs_linked}.
    """
    from app.database import SessionLocal

    stats = {"source": None, "releases_created": 0, "songs_linked": 0}

    # Phase 1 — brief session: load artist name + all calibrated songs
    db = SessionLocal()
    try:
        artist = db.get(Artist, artist_id)
        if not artist:
            return stats
        artist_name = artist.name
        all_songs = _find_artist_songs_full(artist_name, db)
    finally:
        db.close()

    if not all_songs:
        return stats

    # Phase 2 — no DB: fetch MB (and Spotify fallback) into memory
    mb_data = await _fetch_musicbrainz_data(artist_name)
    sp_data = None
    if not mb_data:
        sp_data = await _fetch_spotify_data(artist_name)

    # Phase 3 — fresh session: apply all writes in one burst, commit
    db = SessionLocal()
    try:
        artist = db.get(Artist, artist_id)
        if not artist:
            return stats
        if mb_data:
            mb_stats = _apply_musicbrainz_data(artist, mb_data, all_songs, db)
            stats["source"] = "musicbrainz"
            stats["releases_created"] = mb_stats["releases_created"]
            stats["songs_linked"] = mb_stats["songs_linked"]
        elif sp_data:
            sp_stats = _apply_spotify_data(artist, sp_data, db)
            stats["source"] = "spotify"
            stats["releases_created"] = sp_stats["releases_created"]

        stats["songs_linked"] += _apply_catch_all(artist, all_songs, db)
        db.commit()
    finally:
        db.close()

    return stats


async def _fetch_musicbrainz_data(artist_name: str) -> dict | None:
    """Fetch MB artist + release-groups + tracks into memory. No DB access."""
    from app.services import musicbrainz

    mb_artists = await musicbrainz.search_artist(artist_name, limit=3)
    if not mb_artists:
        return None

    mb_artist = mb_artists[0]
    if mb_artist["score"] < 80:
        logger.info(
            "MusicBrainz: best match for '%s' scored %d, skipping",
            artist_name, mb_artist["score"],
        )
        return None

    mbid = mb_artist["mbid"]
    mb_releases = await musicbrainz.get_artist_releases(mbid)
    if not mb_releases:
        return {"mbid": mbid, "releases": []}

    releases_with_tracks = []
    for rel in mb_releases:
        tracks = await musicbrainz.get_release_tracks(rel["mbid"])
        releases_with_tracks.append({**rel, "tracks": tracks})

    return {
        "mbid": mbid,
        "name": mb_artist.get("name"),
        "disambiguation": mb_artist.get("disambiguation", ""),
        "releases": releases_with_tracks,
    }


def _apply_musicbrainz_data(artist: Artist, mb_data: dict, all_songs: list[dict], db) -> dict:
    """Write fetched MB data to the DB. Synchronous, fast — no network."""
    artist.musicbrainz_id = mb_data["mbid"]
    stats = {"releases_created": 0, "songs_linked": 0}

    linked_keys = _get_linked_song_keys(artist, db)

    for mb_rel in mb_data.get("releases", []):
        existing = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .filter(Release.musicbrainz_id == mb_rel["mbid"])
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

        charges: list[int] = []
        contam = 0
        calibrated = 0

        for track in mb_rel.get("tracks", []):
            matched = _match_track_in_memory(track["title"], all_songs)
            if not matched:
                continue
            key = (matched["source"], matched["id"])
            if key in linked_keys:
                continue
            db.add(ReleaseSong(
                release_id=release.id,
                song_source=matched["source"],
                song_id=matched["id"],
                track_number=track.get("position"),
            ))
            linked_keys.add(key)
            stats["songs_linked"] += 1
            if matched["charge"] is not None:
                charges.append(matched["charge"])
                calibrated += 1
            if matched["contaminated"]:
                contam += 1

        release.track_count = len(mb_rel.get("tracks", []))
        release.calibrated_count = calibrated
        release.contamination_count = contam
        if charges:
            result = compute_release_charge(charges)
            if result:
                release.charge_value = result[0]
                release.rubric_color = result[1]

    return stats


async def _fetch_spotify_data(artist_name: str) -> dict | None:
    """Fetch Spotify artist + albums into memory. No DB access."""
    from app.config import settings

    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return None

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]

            search_resp = await client.get(
                "https://api.spotify.com/v1/search",
                params={"q": artist_name, "type": "artist", "limit": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
            search_resp.raise_for_status()
            artists_data = search_resp.json()["artists"]["items"]
            if not artists_data:
                return None
            sp_artist = artists_data[0]

            albums_resp = await client.get(
                f"https://api.spotify.com/v1/artists/{sp_artist['id']}/albums",
                params={"include_groups": "album,single", "limit": 50},
                headers={"Authorization": f"Bearer {token}"},
            )
            albums_resp.raise_for_status()
            sp_albums = albums_resp.json()["items"]

        return {"spotify_id": sp_artist["id"], "albums": sp_albums}
    except Exception:
        logger.exception("Spotify fetch failed for '%s'", artist_name)
        return None


def _apply_spotify_data(artist: Artist, sp_data: dict, db) -> dict:
    """Write fetched Spotify data to the DB. Synchronous."""
    from app.services.musicbrainz import _parse_mb_date

    artist.spotify_id = sp_data["spotify_id"]
    stats = {"releases_created": 0}

    for sp_album in sp_data.get("albums", []):
        existing = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .filter(func.lower(Release.title) == sp_album["name"].lower())
            .first()
        )
        if existing:
            continue

        sp_type = sp_album.get("album_type", "single").lower()
        release_type = {"album": "album", "single": "single", "compilation": "album"}.get(sp_type, "single")
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

    return stats


def _apply_catch_all(artist: Artist, all_songs: list[dict], db) -> int:
    """Link any still-unlinked songs to the 'Singles & Uncategorized' catch-all.

    Returns the count of songs newly linked.
    """
    linked_keys = _get_linked_song_keys(artist, db)
    unlinked = [s for s in all_songs if (s["source"], s["id"]) not in linked_keys]
    if not unlinked:
        return 0

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

    charges: list[int] = []
    contam = 0
    for s in unlinked:
        db.add(ReleaseSong(release_id=catch_all.id, song_source=s["source"], song_id=s["id"]))
        if s["charge"] is not None:
            charges.append(s["charge"])
        if s["contaminated"]:
            contam += 1

    catch_all.track_count = (catch_all.track_count or 0) + len(unlinked)
    catch_all.calibrated_count = len(charges)
    catch_all.contamination_count = contam
    if charges:
        result = compute_release_charge(charges)
        if result:
            catch_all.charge_value = result[0]
            catch_all.rubric_color = result[1]

    return len(unlinked)


def _find_artist_songs_full(artist_name: str, db) -> list[dict]:
    """Find calibrated songs for an artist, including titles for in-memory matching.

    Loaded once during Phase 1 so track matching in Phase 3 needs no extra queries.
    """
    name_lower = artist_name.lower()
    results: list[dict] = []
    for source, Model in [("compass", CompassSong), ("library", LibrarySong), ("submitted", SubmittedSong)]:
        query = (
            db.query(Model)
            .filter(func.lower(Model.artist) == name_lower)
            .filter(Model.charge_value.isnot(None))
        )
        if Model is SubmittedSong:
            query = query.filter(Model.artist.isnot(None))
        for row in query.all():
            results.append({
                "source": source,
                "id": row.id,
                "title": row.title or "",
                "charge": row.charge_value,
                "contaminated": getattr(row, "contaminated", False) or False,
            })
    return results


def _get_linked_song_keys(artist: Artist, db) -> set[tuple[str, int]]:
    """Get set of (source, song_id) already linked to any of this artist's releases."""
    keys = set()
    for release in artist.releases:
        for link in release.songs:
            keys.add((link.song_source, link.song_id))
    return keys


def _match_track_in_memory(track_title: str, all_songs: list[dict]) -> dict | None:
    """Match a track title against pre-loaded songs. Exact then punctuation-stripped."""
    track_lower = track_title.lower()
    for s in all_songs:
        if s["title"].lower() == track_lower:
            return s
    track_stripped = re.sub(r"[^\w\s]", "", track_lower)
    for s in all_songs:
        if re.sub(r"[^\w\s]", "", s["title"].lower()) == track_stripped:
            return s
    return None
