"""Artist utility functions: slug generation, name normalization, charge computation, release resolution."""

import logging
import re
import unicodedata

from sqlalchemy import func

from app.models import Artist, Release, ReleaseSong, SongArtist, Song
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
    """Count calibrated songs credited to this artist via string match OR
    song_artists credit. Dedupes across both paths per source.
    """
    name_lower = artist_name.lower()
    # Resolve to Artist entity once for the song_artists path.
    artist_row = (
        db.query(Artist)
        .filter(func.lower(Artist.name) == name_lower)
        .first()
    )
    # Unified: distinct calibrated songs credited to the artist via string match
    # OR a song_artists credit (joined on song_id).
    ids = {
        sid for (sid,) in (
            db.query(Song.id)
            .filter(func.lower(Song.artist) == name_lower)
            .filter(Song.charge_value.isnot(None))
            .all()
        )
    }
    if artist_row:
        ids |= {
            sid for (sid,) in (
                db.query(Song.id)
                .join(SongArtist, SongArtist.song_id == Song.id)
                .filter(SongArtist.artist_id == artist_row.id)
                .filter(Song.charge_value.isnot(None))
                .all()
            )
        }
    return len(ids)


def generate_song_slug(title: str, artist: str) -> str:
    """Generate a URL slug from song title + artist."""
    return slugify(f"{title} {artist}")


def resolve_artist_slugs(names, db) -> dict[str, str]:
    """Batch-resolve artist-name strings to Artist.slug.

    Returns {lowercased_primary_name: slug}. Multi-artist credits
    ("Justin Bieber, Nicki Minaj") collapse to the primary artist via
    normalize_artist_name — public link points to the lead.

    One query regardless of input size. Names with no Artist row are
    simply absent from the result; callers should treat absence as
    "render as plain text."
    """
    if not names:
        return {}
    primaries: set[str] = set()
    for raw in names:
        if not raw:
            continue
        primary = normalize_artist_name(raw).lower()
        if primary:
            primaries.add(primary)
    if not primaries:
        return {}
    rows = (
        db.query(Artist.name, Artist.slug)
        .filter(func.lower(Artist.name).in_(primaries))
        .filter(Artist.slug.isnot(None))
        .all()
    )
    return {row.name.lower(): row.slug for row in rows}


async def resolve_artist_releases(artist_id: int, mb_data: dict | None = None) -> dict:
    """Resolve release metadata for an artist via MusicBrainz.

    Fetches MB data into memory *without* holding a DB session, then opens a
    fresh session for writes. MB is rate-limited to 1 req/sec, so a
    Beatles-sized catalog is minutes of calls; separating the phases avoids
    holding a pooled connection idle across that window.

    Pass `mb_data` to skip the fetch and apply an already-fetched payload. That
    is what lets a caller which DELETES before resolving (rebuild-releases)
    prove MusicBrainz is answering before it destroys anything, instead of
    discovering the outage after the purge has committed.

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

    # Phase 2 — no DB: fetch MB release data into memory (unless pre-fetched)
    if mb_data is None:
        mb_data = await _fetch_musicbrainz_data(artist_name)

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

        stats["songs_linked"] += _apply_catch_all(artist, all_songs, db)
        db.commit()
    finally:
        db.close()

    # Cover art (best-effort, after the write): cache CAA existence for every
    # release-group MBID we just wrote, keyed by MBID so it survives rebuilds.
    # Rate-limited and fail-soft -- never blocks or breaks the resolve.
    if mb_data:
        try:
            from app.services import coverart
            mbids = [r["mbid"] for r in mb_data.get("releases", []) if r.get("mbid")]
            await coverart.ensure_cover_art(mbids)
        except Exception:
            logger.info("Cover-art ensure failed after resolve", exc_info=True)

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
        result = await musicbrainz.get_release_tracks(rel["mbid"])
        # Filter v2: a release-group with no official release is a
        # bootleg/broadcast that MB never tagged. Skip it entirely so no
        # Release row is created (the title/secondary filters in
        # get_artist_releases can't catch these innocuously-titled groups).
        if not result["has_official"]:
            continue
        releases_with_tracks.append({**rel, "tracks": result["tracks"]})

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

    # Tracklist identity: a Rising Compass release is the first official issue
    # of a DISTINCT set of songs. A song is one entity that may populate many
    # releases (its single AND its album), so we no longer stop after a song's
    # first link. We only collapse a later release-group that carries the
    # IDENTICAL song-set as one already created this pass (a reissue / repressing
    # under a new MBID) — a different B-side is a different tracklisting, hence a
    # different release, and is kept. Dedup is within a single resolve pass; the
    # existing-MBID guard below stops a prior pass's groups from re-adding.
    seen_track_sets: set[frozenset] = set()

    # Preload existing releases once. Disambiguating per-release via DB queries
    # costs hundreds of round-trips on catalogs like the Beatles'; resolve it
    # in memory instead.
    existing_rows = (
        db.query(Release.title, Release.musicbrainz_id)
        .filter(Release.artist_id == artist.id)
        .all()
    )
    existing_titles: set[str] = {row.title.lower() for row in existing_rows}
    existing_mbids: set[str] = {row.musicbrainz_id for row in existing_rows if row.musicbrainz_id}

    for mb_rel in mb_data.get("releases", []):
        if mb_rel["mbid"] in existing_mbids:
            continue

        # Tracklist-identity dedup: collapse a later group with the identical
        # song-set (a reissue under a new MBID). Built from the full MB track
        # titles, independent of which tracks match a calibrated song.
        track_set = frozenset(
            re.sub(r"[^a-z0-9]", "", (t.get("title") or "").lower())
            for t in mb_rel.get("tracks", [])
            if (t.get("title") or "").strip()
        )
        if track_set and track_set in seen_track_sets:
            continue

        title = _pick_unique_title(mb_rel, existing_titles)
        if title is None:
            continue

        release = Release(
            artist_id=artist.id,
            title=title,
            release_type=mb_rel["release_type"],
            release_date=mb_rel["release_date"],
            release_year=mb_rel["release_year"],
            musicbrainz_id=mb_rel["mbid"],
        )
        db.add(release)
        db.flush()
        existing_titles.add(title.lower())
        existing_mbids.add(mb_rel["mbid"])
        if track_set:
            seen_track_sets.add(track_set)
        stats["releases_created"] += 1

        charges: list[int] = []
        contam = 0
        calibrated = 0
        seen_in_release: set[int] = set()  # guard a song appearing twice on one release

        for track in mb_rel.get("tracks", []):
            matched = _match_track_in_memory(track["title"], all_songs)
            if not matched:
                continue
            key = matched["id"]  # unified songs.id
            if key in seen_in_release:
                continue
            db.add(ReleaseSong(
                release_id=release.id,
                song_id=matched["id"],
                track_number=track.get("position"),
            ))
            seen_in_release.add(key)
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


def _apply_catch_all(artist: Artist, all_songs: list[dict], db) -> int:
    """Link any still-unlinked songs to the 'Singles & Uncategorized' catch-all.

    Returns the count of songs newly linked.
    """
    linked_keys = _get_linked_song_keys(artist, db)
    unlinked = [s for s in all_songs if s["id"] not in linked_keys]
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
        db.add(ReleaseSong(release_id=catch_all.id, song_id=s["id"]))
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
    query = (
        db.query(Song)
        .filter(func.lower(Song.artist) == name_lower)
        .filter(Song.charge_value.isnot(None))
        .filter(Song.artist.isnot(None))
    )
    for row in query.all():
        results.append({
            "source": "songs",
            "id": row.id,
            "title": row.title or "",
            "charge": row.charge_value,
            "contaminated": row.contaminated or False,
        })
    return results


def _get_linked_song_keys(artist: Artist, db) -> set[int]:
    """Get set of unified song ids already linked to any of this artist's releases."""
    keys = set()
    for release in artist.releases:
        for link in release.songs:
            if link.song_id is not None:
                keys.add(link.song_id)
    return keys


def _pick_unique_title(mb_rel: dict, taken: set[str]) -> str | None:
    """Pick a title that doesn't collide with `taken` (lowercased titles
    already in use for this artist). Try bare title, then year-suffixed,
    then MBID-suffixed as a last resort.
    """
    base = mb_rel["title"]
    year = mb_rel.get("release_year")
    mbid_suffix = (mb_rel.get("mbid") or "")[:8]

    candidates = [base]
    if year:
        candidates.append(f"{base} ({year})")
    if mbid_suffix:
        candidates.append(f"{base} [{mbid_suffix}]")

    for candidate in candidates:
        if candidate.lower() not in taken:
            return candidate
    return None


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
