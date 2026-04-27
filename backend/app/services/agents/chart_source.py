"""Spotify playlist scrapers via Playwright.

Two charts in use:
  - Top 50 - USA   → 37i9dQZEVXbLRQDuF5jeBp  (the canonical daily reading source)
  - Viral 50 - USA → 37i9dQZEVXbKuaTI1Z1Afx  (TikTok-correlated proxy, second panel)

Both share the same playlist DOM, so one parser handles both.
"""

import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

TOP50_USA_URL = "https://open.spotify.com/playlist/37i9dQZEVXbLRQDuF5jeBp"
VIRAL50_USA_URL = "https://open.spotify.com/playlist/37i9dQZEVXbKuaTI1Z1Afx"


def _parse_rows(rows, count: int, chart_source: str) -> list[dict]:
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
            "chart_source": chart_source,
        })
    return songs


def _fetch_playlist(playlist_url: str, chart_source: str, count: int, retries: int) -> list[dict]:
    """Scrape a Spotify playlist page, return up to `count` songs."""
    songs: list[dict] = []
    for attempt in range(1, retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(playlist_url, timeout=45000)
                page.wait_for_load_state("networkidle", timeout=30000)

                for _ in range(6):
                    page.evaluate("window.scrollBy(0, 2000)")
                    page.wait_for_timeout(1500)

                rows = page.query_selector_all('[data-testid="tracklist-row"]')
                songs = _parse_rows(rows, count, chart_source)

                browser.close()

                if len(songs) >= count:
                    logger.info("Fetched %d songs from %s", len(songs), chart_source)
                    return songs

                logger.warning(
                    "Attempt %d/%d: %s only got %d/%d songs, retrying...",
                    attempt, retries, chart_source, len(songs), count,
                )

        except Exception:
            logger.exception("Attempt %d/%d: %s fetch failed", attempt, retries, chart_source)

    logger.error("All %d attempts failed for %s (last got %d)", retries, chart_source, len(songs))
    return songs


def fetch_top_songs(count: int = 20, _retries: int = 3) -> list[dict]:
    """Spotify Top 50 - USA. Source for the canonical daily compass reading."""
    return _fetch_playlist(TOP50_USA_URL, "spotify_top50_usa", count, _retries)


def fetch_viral_songs(count: int = 20, _retries: int = 3) -> list[dict]:
    """Spotify Viral 50 - USA. Source for the second-panel snapshot."""
    return _fetch_playlist(VIRAL50_USA_URL, "spotify_viral50_usa", count, _retries)
