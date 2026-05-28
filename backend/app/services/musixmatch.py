"""Musixmatch API client -- song search and lyrics retrieval.

DISABLED 2026-05-28 for legal compliance. The Musixmatch API Terms of Service
(24 Jun 2025) clause 2.2.1 forbids commercial use / monetization of Musixmatch
Data without written approval, and clause 2.2.14 forbids using Musixmatch Data
"in conjunction with" or "to prompt any AI system" without written approval.
Rising Compass calibrates by prompting an AI (Claude), so feeding any Musixmatch
lyric or metadata into the pipeline breaches 2.2.14 (and 2.2.1 once monetized).
Until a separate WRITTEN commercial + AI license is signed, this service is
hard-disabled: is_configured() returns False -- so every gated search/fetch path
stays dark even if MUSIXMATCH_API_KEY is set. Do NOT flip MUSIXMATCH_ENABLED back
on without that signed license.
"""

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.musixmatch.com/ws/1.1"

# Legal kill switch -- see module docstring. Must stay False until a written
# Musixmatch commercial + AI license is in hand. While False, is_configured()
# reports False and every data-access function below refuses regardless of key.
MUSIXMATCH_ENABLED = False


def is_configured() -> bool:
    # Hard-disabled for ToS compliance (clauses 2.2.1 + 2.2.14). Even with a key
    # set, the service stays dark until MUSIXMATCH_ENABLED is flipped under a
    # signed license. Every search/fetch path in this module gates on this.
    if not MUSIXMATCH_ENABLED:
        return False
    return bool(settings.musixmatch_api_key)


async def search_tracks(query: str, artist: str = "", limit: int = 10) -> list[dict]:
    """Search for tracks by title (and optionally artist).

    Returns a list of dicts with: track_id, title, artist, album, has_lyrics.
    Returns empty list if API key is not configured.
    """
    if not is_configured():
        return []

    params = {
        "apikey": settings.musixmatch_api_key,
        "q_track": query,
        "page_size": min(limit, 20),
        "page": 1,
        "s_track_rating": "desc",
        "f_has_lyrics": 1,
    }
    if artist:
        params["q_artist"] = artist

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BASE_URL}/track.search", params=params)
            resp.raise_for_status()
            data = resp.json()

        status_code = data["message"]["header"]["status_code"]
        if status_code != 200:
            logger.warning("Musixmatch search returned status %d", status_code)
            return []

        track_list = data["message"]["body"]["track_list"]
        results = []
        for item in track_list:
            t = item["track"]
            results.append({
                "track_id": t["track_id"],
                "title": t["track_name"],
                "artist": t["artist_name"],
                "album": t.get("album_name", ""),
                "has_lyrics": bool(t.get("has_lyrics")),
            })
        return results

    except Exception:
        logger.exception("Musixmatch search failed")
        return []


async def search_albums(query: str, artist: str = "", limit: int = 10) -> list[dict]:
    """Search for albums by title (and optionally artist).

    Returns a list of dicts with: album_id, title, artist, release_year,
    track_count. Returns empty list if the API key is not configured (gated;
    ships dark until Musixmatch is live).
    """
    if not is_configured():
        return []

    params = {
        "apikey": settings.musixmatch_api_key,
        "q_album": query,
        "page_size": min(limit, 20),
        "page": 1,
        "s_album_rating": "desc",
    }
    if artist:
        params["q_artist"] = artist

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BASE_URL}/album.search", params=params)
            resp.raise_for_status()
            data = resp.json()

        status_code = data["message"]["header"]["status_code"]
        if status_code != 200:
            logger.warning("Musixmatch album.search returned status %d", status_code)
            return []

        album_list = data["message"]["body"]["album_list"]
        results = []
        for item in album_list:
            a = item["album"]
            release_date = a.get("album_release_date", "") or ""
            year = None
            if len(release_date) >= 4 and release_date[:4].isdigit():
                year = int(release_date[:4])
            # Pass a full yyyy-mm-dd through only when Musixmatch gives one;
            # the frontend prefers it over the bare year for the timeline.
            full_date = release_date if len(release_date) == 10 and release_date[4] == "-" else None
            results.append({
                "album_id": a["album_id"],
                "title": a["album_name"],
                "artist": a["artist_name"],
                "release_year": year,
                "release_date": full_date,
                "track_count": a.get("album_track_count"),
            })
        return results

    except Exception:
        logger.exception("Musixmatch album search failed")
        return []


async def get_album_tracks(album_id: int) -> list[dict]:
    """Fetch the tracklist for an album by Musixmatch album ID.

    Returns a list of dicts with: track_id, title, track_number, has_lyrics.
    Returns empty list if unavailable/not configured.
    """
    if not is_configured():
        return []

    params = {
        "apikey": settings.musixmatch_api_key,
        "album_id": album_id,
        "page_size": 100,
        "page": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BASE_URL}/album.tracks.get", params=params)
            resp.raise_for_status()
            data = resp.json()

        status_code = data["message"]["header"]["status_code"]
        if status_code != 200:
            logger.warning("Musixmatch album.tracks.get returned status %d for album %d",
                           status_code, album_id)
            return []

        track_list = data["message"]["body"]["track_list"]
        results = []
        for item in track_list:
            t = item["track"]
            results.append({
                "track_id": t["track_id"],
                "title": t["track_name"],
                "track_number": t.get("track_number"),
                "has_lyrics": bool(t.get("has_lyrics")),
            })
        return results

    except Exception:
        logger.exception("Musixmatch album tracks fetch failed for album %d", album_id)
        return []


async def get_lyrics(track_id: int) -> Optional[str]:
    """Fetch lyrics for a track by Musixmatch track ID.

    Returns lyrics text or None if unavailable/not configured.
    """
    # Hard block independent of is_configured(): Musixmatch lyrics must never
    # reach the AI calibrator (ToS 2.2.14). Belt-and-suspenders so a future edit
    # to is_configured() cannot reopen the lyrics->AI path.
    if not MUSIXMATCH_ENABLED:
        logger.warning("get_lyrics blocked: Musixmatch disabled for ToS compliance (2.2.14)")
        return None
    if not is_configured():
        return None

    params = {
        "apikey": settings.musixmatch_api_key,
        "track_id": track_id,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BASE_URL}/track.lyrics.get", params=params)
            resp.raise_for_status()
            data = resp.json()

        status_code = data["message"]["header"]["status_code"]
        if status_code != 200:
            logger.warning("Musixmatch lyrics.get returned status %d for track %d", status_code, track_id)
            return None

        lyrics_body = data["message"]["body"]["lyrics"]["lyrics_body"]
        if not lyrics_body:
            return None

        # Musixmatch appends a disclaimer footer — strip it
        marker = "******* This Lyrics is NOT for Commercial use *******"
        if marker in lyrics_body:
            lyrics_body = lyrics_body[:lyrics_body.index(marker)].rstrip()

        return lyrics_body if lyrics_body else None

    except Exception:
        logger.exception("Musixmatch lyrics fetch failed for track %d", track_id)
        return None
