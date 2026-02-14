"""Genius lyrics fetcher — searches Genius API and scrapes lyrics from song pages."""

import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

GENIUS_SEARCH_URL = "https://api.genius.com/search"


def fetch_lyrics(title: str, artist: str) -> str | None:
    """Fetch lyrics for a song from Genius.

    Searches the Genius API for the song, then scrapes lyrics from the song page.
    Returns cleaned lyrics text or None if not found.
    """
    if not settings.genius_access_token:
        logger.debug("No Genius token configured — skipping lyrics for %s by %s", title, artist)
        return None

    try:
        song_url = _search_song(title, artist)
        if not song_url:
            logger.info("No Genius result for %s by %s", title, artist)
            return None

        lyrics = _scrape_lyrics(song_url)
        if lyrics:
            logger.info("Fetched lyrics for %s by %s (%d chars)", title, artist, len(lyrics))
        return lyrics

    except Exception:
        logger.exception("Failed to fetch lyrics for %s by %s", title, artist)
        return None


def _search_song(title: str, artist: str) -> str | None:
    """Search Genius API and return the song page URL, or None."""
    headers = {"Authorization": f"Bearer {settings.genius_access_token}"}
    params = {"q": f"{title} {artist}"}

    resp = httpx.get(GENIUS_SEARCH_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()

    hits = resp.json().get("response", {}).get("hits", [])
    if not hits:
        return None

    # Find best match — check that artist name appears in the result
    artist_lower = artist.lower()
    for hit in hits[:5]:
        result = hit.get("result", {})
        result_artist = result.get("primary_artist", {}).get("name", "").lower()
        if artist_lower in result_artist or result_artist in artist_lower:
            return result.get("url")

    # Fall back to first result if no artist match
    return hits[0].get("result", {}).get("url")


def _scrape_lyrics(url: str) -> str | None:
    """Scrape lyrics from a Genius song page."""
    resp = httpx.get(url, timeout=10, follow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Genius wraps lyrics in div[data-lyrics-container]
    containers = soup.select("div[data-lyrics-container='true']")
    if not containers:
        return None

    parts = []
    for container in containers:
        # Replace <br> with newlines before getting text
        for br in container.find_all("br"):
            br.replace_with("\n")
        parts.append(container.get_text())

    lyrics = "\n".join(parts).strip()

    # Clean up: remove section headers like [Verse 1], [Chorus], etc.
    # Keep them — they provide structural context for the classifier
    # But remove excessive blank lines
    lyrics = re.sub(r"\n{3,}", "\n\n", lyrics)

    return lyrics if lyrics else None
