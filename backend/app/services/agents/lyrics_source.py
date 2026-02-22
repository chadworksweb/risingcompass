"""Lyrics fetcher — tries lyrics.ovh API first, then Genius API + Playwright, then AZLyrics."""

import logging
import re
import urllib.parse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.config import settings

logger = logging.getLogger(__name__)

GENIUS_SEARCH_URL = "https://api.genius.com/search"
LYRICS_OVH_URL = "https://api.lyrics.ovh/v1"

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_lyrics(title: str, artist: str) -> str | None:
    """Fetch lyrics for a song. Tries sources in order: lyrics.ovh, Genius, AZLyrics.

    Returns cleaned lyrics text or None if not found from any source.
    """
    # Try lyrics.ovh first — fast API, no scraping, no Cloudflare
    lyrics = _fetch_from_lyrics_ovh(title, artist)
    if lyrics:
        return lyrics

    # Genius API search + Playwright scrape
    lyrics = _fetch_from_genius(title, artist)
    if lyrics:
        return lyrics

    # Last resort: AZLyrics
    lyrics = _fetch_from_azlyrics(title, artist)
    if lyrics:
        return lyrics

    logger.warning("No lyrics found from any source for %s by %s", title, artist)
    return None


def _clean_artist_for_search(artist: str) -> str:
    """Extract primary artist name for search APIs.

    Multi-artist strings like 'Baby Keem, Kendrick Lamar, Momo Boyd'
    often fail lookups. Use just the first artist.
    """
    # Split on comma, ampersand, "feat", "ft"
    primary = re.split(r"[,&]|\bfeat\.?\b|\bft\.?\b", artist, flags=re.IGNORECASE)[0].strip()
    return primary


def _clean_title_for_search(title: str) -> str:
    """Strip parenthetical/bracketed features from title for search APIs.

    'Good Flirts (feat. Kendrick Lamar & Momo Boyd)' -> 'Good Flirts'
    """
    return re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()


def _fetch_from_lyrics_ovh(title: str, artist: str) -> str | None:
    """Try lyrics.ovh — free API, returns plain text lyrics."""
    try:
        clean_artist = _clean_artist_for_search(artist)
        clean_title = _clean_title_for_search(title)

        url = f"{LYRICS_OVH_URL}/{urllib.parse.quote(clean_artist)}/{urllib.parse.quote(clean_title)}"
        logger.info("Trying lyrics.ovh: %s / %s", clean_artist, clean_title)

        resp = httpx.get(url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            logger.info("lyrics.ovh returned %d for %s by %s", resp.status_code, title, artist)
            return None

        data = resp.json()
        lyrics = data.get("lyrics", "").strip()

        # Strip the "Paroles de la chanson..." header line if present
        if lyrics.startswith("Paroles de la chanson"):
            lyrics = lyrics.split("\n", 1)[1].strip() if "\n" in lyrics else ""

        if len(lyrics) < 100:
            logger.info("lyrics.ovh result too short (%d chars) for %s by %s", len(lyrics), title, artist)
            return None

        # Clean up excessive blank lines
        lyrics = re.sub(r"\n{3,}", "\n\n", lyrics)
        logger.info("Fetched lyrics from lyrics.ovh for %s by %s (%d chars)", title, artist, len(lyrics))
        return lyrics

    except Exception:
        logger.exception("Failed to fetch lyrics from lyrics.ovh for %s by %s", title, artist)
        return None


def _fetch_from_genius(title: str, artist: str) -> str | None:
    """Try fetching lyrics from Genius API search + Playwright page scrape."""
    if not settings.genius_access_token:
        logger.debug("No Genius token configured — skipping Genius for %s by %s", title, artist)
        return None

    try:
        song_url = _search_genius(title, artist)
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


def _search_genius(title: str, artist: str) -> str | None:
    """Search Genius API and return the song page URL, or None."""
    headers = {"Authorization": f"Bearer {settings.genius_access_token}"}
    clean_artist = _clean_artist_for_search(artist)
    clean_title = _clean_title_for_search(title)
    params = {"q": f"{clean_title} {clean_artist}"}

    resp = httpx.get(GENIUS_SEARCH_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()

    hits = resp.json().get("response", {}).get("hits", [])
    if not hits:
        return None

    # Find best match — check that artist name appears in the result
    artist_lower = clean_artist.lower()
    for hit in hits[:5]:
        result = hit.get("result", {})
        result_artist = result.get("primary_artist", {}).get("name", "").lower()
        if artist_lower in result_artist or result_artist in artist_lower:
            return result.get("url")

    # Fall back to first result if no artist match
    return hits[0].get("result", {}).get("url")


def _scrape_genius_lyrics(url: str) -> str | None:
    """Scrape lyrics from a Genius song page using Playwright headless browser."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            page.wait_for_selector("div[data-lyrics-container='true']", timeout=15000)

            html = page.content()
            browser.close()
    except Exception:
        logger.exception("Playwright failed to load Genius page: %s", url)
        return None

    soup = BeautifulSoup(html, "html.parser")

    containers = soup.select("div[data-lyrics-container='true']")
    if not containers:
        return None

    parts = []
    for container in containers:
        for br in container.find_all("br"):
            br.replace_with("\n")
        parts.append(container.get_text())

    lyrics = "\n".join(parts).strip()
    lyrics = re.sub(r"\n{3,}", "\n\n", lyrics)

    return lyrics if lyrics else None


def _azlyrics_slug(text: str) -> str:
    """Convert artist or title to AZLyrics URL slug (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _fetch_from_azlyrics(title: str, artist: str) -> str | None:
    """Try fetching lyrics from AZLyrics by constructing the expected URL."""
    try:
        artist_clean = re.sub(r"^the\s+", "", artist, flags=re.IGNORECASE)
        artist_slug = _azlyrics_slug(_clean_artist_for_search(artist_clean))
        title_slug = _azlyrics_slug(_clean_title_for_search(title))

        url = f"https://www.azlyrics.com/lyrics/{artist_slug}/{title_slug}.html"
        logger.info("Trying AZLyrics: %s", url)

        resp = httpx.get(
            url,
            timeout=10,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )
        if resp.status_code != 200:
            logger.info("AZLyrics returned %d for %s by %s", resp.status_code, title, artist)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        divs = soup.select("div.col-xs-12.col-lg-8.text-center div:not([class])")
        for div in divs:
            text = div.get_text(separator="\n").strip()
            if len(text) > 100:
                lyrics = re.sub(r"\n{3,}", "\n\n", text)
                logger.info("Fetched lyrics from AZLyrics for %s by %s (%d chars)", title, artist, len(lyrics))
                return lyrics

        logger.info("No lyrics content found on AZLyrics page for %s by %s", title, artist)
        return None

    except Exception:
        logger.exception("Failed to fetch lyrics from AZLyrics for %s by %s", title, artist)
        return None
