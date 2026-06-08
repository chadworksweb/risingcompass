"""Chart fetchers for the daily reading + the secondary homepage panel.

Two sources, two mechanisms:
  - Spotify Top 50 - USA (37i9dQZEVXbLRQDuF5jeBp) -- the canonical daily reading
    source. Scraped from the playlist DOM via Playwright (fetch_top_songs).
  - iTunes Download Chart - USA -- the secondary-panel source
    (fetch_itunes_songs). Pulled from Apple's public RSS JSON feed (no browser,
    no DOM, no key), so the secondary panel needs no Playwright at all.
"""

import logging

import httpx
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

TOP50_USA_URL = "https://open.spotify.com/playlist/37i9dQZEVXbLRQDuF5jeBp"

# Apple's public download-chart RSS feed (Apple's path name for it is "topsongs").
# {limit} is templated in. Returns JSON: feed.entry[] ordered by rank, each with
# im:name / im:artist.
ITUNES_DOWNLOAD_FEED = "https://itunes.apple.com/us/rss/topsongs/limit={limit}/json"
USER_AGENT = "RisingCompass/1.0 (https://risingcompass.net)"


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


def fetch_itunes_songs(count: int = 20, _retries: int = 3) -> list[dict]:
    """iTunes Download Chart - USA. Source for the secondary homepage panel.
    Reads Apple's public RSS JSON feed -- no browser, no key.

    Rank is the feed order (the feed carries no explicit position field). Returns
    up to `count` songs, or [] if every attempt fails (the caller 502s on empty).
    """
    chart_source = "itunes_download_usa"
    url = ITUNES_DOWNLOAD_FEED.format(limit=count)
    songs: list[dict] = []

    for attempt in range(1, _retries + 1):
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
            entries = (resp.json().get("feed") or {}).get("entry") or []
            # Apple returns a bare object (not a list) when the feed has one entry.
            if isinstance(entries, dict):
                entries = [entries]

            songs = []
            for pos, entry in enumerate(entries[:count], start=1):
                title = ((entry.get("im:name") or {}).get("label") or "").strip()
                artist = ((entry.get("im:artist") or {}).get("label") or "Unknown").strip()
                if not title:
                    continue
                songs.append({
                    "title": title,
                    "artist": artist or "Unknown",
                    "position": pos,
                    "chart_source": chart_source,
                })

            if songs:
                logger.info("Fetched %d songs from %s", len(songs), chart_source)
                return songs

            logger.warning(
                "Attempt %d/%d: %s feed returned no usable entries, retrying...",
                attempt, _retries, chart_source,
            )
        except Exception:
            logger.exception("Attempt %d/%d: %s fetch failed", attempt, _retries, chart_source)

    logger.error("All %d attempts failed for %s (last got %d)", _retries, chart_source, len(songs))
    return songs
