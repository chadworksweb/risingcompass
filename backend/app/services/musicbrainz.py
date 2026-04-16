"""MusicBrainz API client — artist search and release metadata.

Free API, no key required. Rate limit: 1 request/second.
User-Agent required per MB terms of service.
"""

import asyncio
import logging
from datetime import date
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "RisingCompass/1.0 (https://risingcompass.net)"

# Simple rate limiter — track last request time
_last_request_time: float = 0


async def _rate_limit():
    """Enforce 1 request/second rate limit."""
    global _last_request_time
    import time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    _last_request_time = time.monotonic()


async def search_artist(name: str, limit: int = 5) -> list[dict]:
    """Search MusicBrainz for artists by name.

    Returns list of dicts with: mbid, name, sort_name, disambiguation, score.
    """
    await _rate_limit()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/artist",
                params={"query": name, "fmt": "json", "limit": limit},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for a in data.get("artists", []):
            results.append({
                "mbid": a["id"],
                "name": a["name"],
                "sort_name": a.get("sort-name", ""),
                "disambiguation": a.get("disambiguation", ""),
                "score": a.get("score", 0),
            })
        return results

    except Exception:
        logger.exception("MusicBrainz artist search failed for '%s'", name)
        return []


async def get_artist_releases(
    mbid: str,
    release_types: Optional[list[str]] = None,
) -> list[dict]:
    """Get all releases for an artist by MusicBrainz ID.

    Fetches release-groups (albums, singles, EPs) with dates and types.
    Paginates through all results (100 per page).

    Returns list of dicts with: mbid, title, release_type, release_date,
    release_year, primary_type, secondary_types.
    """
    if release_types is None:
        release_types = ["album", "single", "ep"]

    all_releases = []
    offset = 0
    limit = 100

    while True:
        await _rate_limit()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{BASE_URL}/release-group",
                    params={
                        "artist": mbid,
                        "fmt": "json",
                        "limit": limit,
                        "offset": offset,
                    },
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.json()

            groups = data.get("release-groups", [])
            if not groups:
                break

            for rg in groups:
                primary = (rg.get("primary-type") or "").lower()
                secondary = [s.lower() for s in (rg.get("secondary-types") or [])]

                # Skip compilations, live albums, remixes, etc.
                if any(s in secondary for s in ["compilation", "live", "remix", "dj-mix"]):
                    continue

                # Map primary type to our release_type
                release_type = _map_release_type(primary)
                if release_type not in release_types:
                    continue

                release_date_str = rg.get("first-release-date", "")
                release_date, release_year = _parse_mb_date(release_date_str)

                all_releases.append({
                    "mbid": rg["id"],
                    "title": rg["title"],
                    "release_type": release_type,
                    "release_date": release_date,
                    "release_year": release_year,
                    "primary_type": primary,
                    "secondary_types": secondary,
                })

            # Check if there are more pages
            total = data.get("release-group-count", 0)
            offset += limit
            if offset >= total:
                break

        except Exception:
            logger.exception("MusicBrainz release-group fetch failed for artist %s at offset %d", mbid, offset)
            break

    # Sort by date
    all_releases.sort(key=lambda r: r["release_date"] or date.min)
    return all_releases


async def get_release_tracks(release_group_mbid: str) -> list[dict]:
    """Get track listing for a release group.

    Fetches the first official release in the group and returns its tracks.
    Returns list of dicts with: position, title, length_ms.
    """
    await _rate_limit()
    try:
        # First, get releases in this release group
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/release",
                params={
                    "release-group": release_group_mbid,
                    "fmt": "json",
                    "limit": 1,
                    "status": "official",
                },
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        releases = data.get("releases", [])
        if not releases:
            return []

        release_mbid = releases[0]["id"]

        # Now get tracks for this release
        await _rate_limit()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/release/{release_mbid}",
                params={"inc": "recordings", "fmt": "json"},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        tracks = []
        for medium in data.get("media", []):
            for t in medium.get("tracks", []):
                tracks.append({
                    "position": t.get("position", 0),
                    "title": t.get("title", ""),
                    "length_ms": t.get("length"),
                })

        return tracks

    except Exception:
        logger.exception("MusicBrainz track fetch failed for release-group %s", release_group_mbid)
        return []


def _map_release_type(primary_type: str) -> str:
    """Map MusicBrainz primary-type to our release_type enum."""
    mapping = {
        "album": "album",
        "single": "single",
        "ep": "ep",
        "broadcast": "single",
        "other": "single",
    }
    return mapping.get(primary_type, "single")


def _parse_mb_date(date_str: str) -> tuple[Optional[date], Optional[int]]:
    """Parse a MusicBrainz date string (YYYY, YYYY-MM, or YYYY-MM-DD).

    Returns (date_object_or_None, year_int_or_None).
    """
    if not date_str:
        return None, None

    parts = date_str.split("-")
    year = None
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        return None, None

    if len(parts) == 3:
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2])), year
        except ValueError:
            return None, year
    return None, year
