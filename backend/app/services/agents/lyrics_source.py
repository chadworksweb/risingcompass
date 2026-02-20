"""Lyrics fetcher — tries Genius API first, falls back to AZLyrics scrape."""

import logging
import re
import urllib.parse

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

GENIUS_SEARCH_URL = "https://api.genius.com/search"


def fetch_lyrics(title: str, artist: str) -> str | None:
    """Fetch lyrics for a song, trying Genius first then AZLyrics.

    Returns cleaned lyrics text or None if not found from any source.
    """
    # Try Genius first
    lyrics = _fetch_from_genius(title, artist)
    if lyrics:
        return lyrics

    # Fallback to AZLyrics
    lyrics = _fetch_from_azlyrics(title, artist)
    if lyrics:
        return lyrics

    logger.warning("No lyrics found from any source for %s by %s", title, artist)
    return None


def _fetch_from_genius(title: str, artist: str) -> str | None:
    """Try fetching lyrics from Genius API + scrape."""
    if not settings.genius_access_token:
        logger.debug("No Genius token configured — skipping Genius for %s by %s", title, artist)
        return None

    try:
        song_url = _search_song(title, artist)
        if not song_url:
            logger.info("No Genius result for %s by %s", title, artist)
            return None

        lyrics = _scrape_genius_lyrics(song_url)
        if lyrics:
            logger.info("Fetched lyrics from Genius for %s by %s (%d chars)", title, artist, len(lyrics))
        return lyrics

    except Exception:
        logger.exception("Failed to fetch lyrics from Genius for %s by %s", title, artist)
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


def _scrape_genius_lyrics(url: str) -> str | None:
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


def _azlyrics_slug(text: str) -> str:
    """Convert artist or title to AZLyrics URL slug (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _fetch_from_azlyrics(title: str, artist: str) -> str | None:
    """Try fetching lyrics from AZLyrics by constructing the expected URL."""
    try:
        # Strip leading "The " for AZLyrics artist slugs
        artist_clean = re.sub(r"^the\s+", "", artist, flags=re.IGNORECASE)
        artist_slug = _azlyrics_slug(artist_clean)
        title_slug = _azlyrics_slug(title)

        url = f"https://www.azlyrics.com/lyrics/{artist_slug}/{title_slug}.html"
        logger.info("Trying AZLyrics: %s", url)

        resp = httpx.get(
            url,
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RisingCompass/1.0)"},
        )
        if resp.status_code != 200:
            logger.info("AZLyrics returned %d for %s by %s", resp.status_code, title, artist)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # AZLyrics puts lyrics in a div that follows the comment "Usage of azlyrics.com..."
        # It's the div after ringtone div, with no class/id
        divs = soup.select("div.col-xs-12.col-lg-8.text-center div:not([class])")
        for div in divs:
            text = div.get_text(separator="\n").strip()
            if len(text) > 100:  # lyrics should be substantial
                lyrics = re.sub(r"\n{3,}", "\n\n", text)
                logger.info("Fetched lyrics from AZLyrics for %s by %s (%d chars)", title, artist, len(lyrics))
                return lyrics

        logger.info("No lyrics content found on AZLyrics page for %s by %s", title, artist)
        return None

    except Exception:
        logger.exception("Failed to fetch lyrics from AZLyrics for %s by %s", title, artist)
        return None
