"""Spotify Top 50 - USA playlist scraper via Playwright."""

import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Top 50 - USA playlist
PLAYLIST_URL = "https://open.spotify.com/playlist/37i9dQZEVXbLRQDuF5jeBp"


def fetch_top_songs(count: int = 20) -> list[dict]:
    """Fetch current top songs from Spotify's Top 50 - USA playlist.

    Launches a headless browser to scrape the playlist page.
    Returns list of dicts with title, artist, position, chart_source.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(PLAYLIST_URL, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=20000)

            # Scroll to load enough tracks
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 1500)")
                page.wait_for_timeout(1000)

            rows = page.query_selector_all('[data-testid="tracklist-row"]')
            songs = []

            for row in rows[:count]:
                text = row.inner_text().strip()
                lines = [line.strip() for line in text.split("\n") if line.strip()]

                if len(lines) < 3:
                    continue

                # Position is always first
                try:
                    pos = int(lines[0])
                except ValueError:
                    continue

                # Title is second
                title = lines[1]

                # Skip 'E' (explicit) tag if present
                idx = 2
                if idx < len(lines) and lines[idx] == "E":
                    idx += 1

                # Artist is next
                artist = lines[idx] if idx < len(lines) else "Unknown"

                songs.append({
                    "title": title,
                    "artist": artist,
                    "position": pos,
                    "chart_source": "spotify_top50_usa",
                })

            browser.close()

            logger.info("Fetched %d songs from Spotify Top 50 - USA", len(songs))
            return songs

    except Exception:
        logger.exception("Failed to fetch Spotify playlist data")
        return []
