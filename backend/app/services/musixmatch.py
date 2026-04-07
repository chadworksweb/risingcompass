"""Musixmatch API client — song search and lyrics retrieval."""

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.musixmatch.com/ws/1.1"


def is_configured() -> bool:
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


async def get_lyrics(track_id: int) -> Optional[str]:
    """Fetch lyrics for a track by Musixmatch track ID.

    Returns lyrics text or None if unavailable/not configured.
    """
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
