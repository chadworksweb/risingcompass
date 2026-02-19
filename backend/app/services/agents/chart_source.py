"""Spotify Top 50 - USA playlist scraper via Playwright."""

import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Top 50 - USA playlist
PLAYLIST_URL = "https://open.spotify.com/playlist/37i9dQZEVXbLRQDuF5jeBp"


def _parse_rows(rows, count: int) -> list[dict]:
    """Parse tracklist rows into song dicts."""
    songs = []
    for row in rows[:count]:
        text = row.inner_text().strip()
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        if len(lines) < 3:
            continue

        try:
            pos = int(lines[0])
        except ValueError:
            continue

        title = lines[1]

        idx = 2
        if idx < len(lines) and lines[idx] == "E":
            idx += 1

        artist = lines[idx] if idx < len(lines) else "Unknown"

        songs.append({
            "title": title,
            "artist": artist,
            "position": pos,
            "chart_source": "spotify_top50_usa",
        })
    return songs


def fetch_top_songs(count: int = 20, _retries: int = 3) -> list[dict]:
    """Fetch current top songs from Spotify's Top 50 - USA playlist.

    Launches a headless browser to scrape the playlist page.
    Retries up to _retries times if fewer than count songs are found.
    Returns list of dicts with title, artist, position, chart_source.
    """
    for attempt in range(1, _retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(PLAYLIST_URL, timeout=45000)
                page.wait_for_load_state("networkidle", timeout=30000)

                # Scroll aggressively to load all tracks
                for _ in range(6):
                    page.evaluate("window.scrollBy(0, 2000)")
                    page.wait_for_timeout(1500)

                rows = page.query_selector_all('[data-testid="tracklist-row"]')
                songs = _parse_rows(rows, count)

                browser.close()

                if len(songs) >= count:
                    logger.info("Fetched %d songs from Spotify Top 50 - USA", len(songs))
                    return songs

                logger.warning(
                    "Attempt %d/%d: only got %d/%d songs, retrying...",
                    attempt, _retries, len(songs), count,
                )

        except Exception:
            logger.exception("Attempt %d/%d: failed to fetch Spotify playlist", attempt, _retries)

    logger.error("All %d attempts failed to fetch %d songs (last got %d)", _retries, count, len(songs) if 'songs' in dir() else 0)
    return songs if 'songs' in dir() else []
