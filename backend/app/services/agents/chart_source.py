"""Chart fetchers for the daily reading + the secondary homepage panel.

Three sources, three mechanisms:
  - Spotify Top 50 - USA (37i9dQZEVXbLRQDuF5jeBp) -- the canonical daily reading
    source. Scraped from the playlist DOM via Playwright (fetch_top_songs).
  - iTunes Download Chart - USA -- the secondary-panel source
    (fetch_itunes_songs). Pulled from Apple's public RSS JSON feed (no browser,
    no DOM, no key), so the secondary panel needs no Playwright at all.
  - Shazam Top 200 - USA -- an ingestion-discovery source (fetch_shazam_songs).
    Read from the kworb.net daily mirror of the Shazam chart (plain HTML table,
    no key, no DOM engine). A Shazam IS a "what is this song" question, so it is
    the strongest pre-validated demand proxy for what to calibrate next. The
    official Apple/Shazam amapi endpoints need a MusicKit token; the kworb mirror
    is the stable no-auth path (matches the iTunes plain-HTTP approach).
  - YouTube Trending - USA -- an ingestion-discovery source (fetch_youtube_songs).
    Read from YouTube Music's own Trending chart via ytmusicapi (clean,
    field-separated title/artist; the official YouTube Data API returns only raw
    video titles that need lossy parsing). Trending = what people are discovering
    right now, the same pre-mainstream intent the dead TikTok Billboard chart used
    to carry. Chart playlist ids rotate, so they're resolved fresh each run.
"""

import html
import logging
import re

import httpx
from playwright.sync_api import sync_playwright
from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

TOP50_USA_URL = "https://open.spotify.com/playlist/37i9dQZEVXbLRQDuF5jeBp"

# Apple's public download-chart RSS feed (Apple's path name for it is "topsongs").
# {limit} is templated in. Returns JSON: feed.entry[] ordered by rank, each with
# im:name / im:artist.
ITUNES_DOWNLOAD_FEED = "https://itunes.apple.com/us/rss/topsongs/limit={limit}/json"

# kworb's daily mirror of the Shazam Top 200 - USA. Plain HTML table; each data
# row is <td>rank</td><td>movement</td><td>Artist - Title</td>.
SHAZAM_USA_URL = "https://kworb.net/charts/shazam/us.html"

# kworb's all-time Most-Streamed Songs board (Spotify, GLOBAL lifetime totals).
# Different table shape than the Shazam mirror: NO rank column (rank = row order)
# and the streams live in the next two cells. Each data row is
# <td class="text"><div>Artist - Title</div></td><td>total streams</td><td>daily streams</td>.
KWORB_ALLTIME_SONGS_URL = "https://kworb.net/spotify/songs.html"

# kworb's all-time Most-Streamed ALBUMS board (Spotify, GLOBAL lifetime totals).
# Same table shape as the songs board, so the same parser handles it. This is
# the streaming-era twin of the RIAA best-sellers list -- it surfaces the modern
# albums (Bad Bunny, SZA, Olivia Rodrigo) that the physical-sales chart misses.
KWORB_ALLTIME_ALBUMS_URL = "https://kworb.net/spotify/albums.html"

# YouTube Music charts region + the chart we read. get_charts returns a few chart
# playlists ("Trending ...", "Top 100 ...", "Daily Top ..."); we take Trending as
# the discovery signal. Matched by substring since the country name is appended.
YOUTUBE_REGION = "US"
YOUTUBE_CHART_MATCH = "trending"

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


_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_kworb_rows(page_html: str, count: int, chart_source: str) -> list[dict]:
    """Parse a kworb chart table into song dicts. Each data row is
    rank / movement / 'Artist - Title'. Rows without a numeric rank (headers)
    or without the ' - ' artist/title separator are skipped."""
    songs: list[dict] = []
    for row in _TR_RE.findall(page_html):
        cells = [html.unescape(_TAG_RE.sub("", c)).strip() for c in _TD_RE.findall(row)]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        combo = cells[2]
        if " - " not in combo:
            continue
        artist, title = combo.split(" - ", 1)
        title = title.strip()
        artist = artist.strip()
        if not title or not artist:
            continue
        songs.append({
            "title": title,
            "artist": artist,
            "position": int(cells[0]),
            "chart_source": chart_source,
        })
        if len(songs) >= count:
            break
    return songs


def fetch_shazam_songs(count: int = 20, _retries: int = 3) -> list[dict]:
    """Shazam Top 200 - USA. Ingestion-discovery source: the songs people are
    actively trying to identify, the step before "what is it about."

    Reads kworb.net's daily HTML mirror of the Shazam chart -- no browser, no
    key (the official Apple/Shazam amapi endpoints require a MusicKit token).
    Returns up to `count` songs, or [] if every attempt fails (the caller 502s
    on empty). Mirrors fetch_itunes_songs: plain HTTP GET + retry loop.
    """
    chart_source = "shazam_top200_usa"
    songs: list[dict] = []

    for attempt in range(1, _retries + 1):
        try:
            resp = httpx.get(
                SHAZAM_USA_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
            # kworb omits a charset header, so httpx may guess latin-1 and mangle
            # apostrophes/accents in titles. Decode as UTF-8 explicitly.
            songs = _parse_kworb_rows(resp.content.decode("utf-8", "replace"), count, chart_source)

            if songs:
                logger.info("Fetched %d songs from %s", len(songs), chart_source)
                return songs

            logger.warning(
                "Attempt %d/%d: %s table returned no usable rows, retrying...",
                attempt, _retries, chart_source,
            )
        except Exception:
            logger.exception("Attempt %d/%d: %s fetch failed", attempt, _retries, chart_source)

    logger.error("All %d attempts failed for %s (last got %d)", _retries, chart_source, len(songs))
    return songs


def _parse_kworb_alltime_rows(page_html: str, count: int, chart_source: str) -> list[dict]:
    """Parse the kworb all-time Most-Streamed board into song dicts. Unlike the
    Shazam mirror this table has NO rank column -- rank is row order -- and each
    data row is 'Artist - Title' / total streams / daily streams. Stream counts
    are comma-grouped integers. Rows without the ' - ' separator or without a
    numeric total are skipped (headers / malformed)."""
    songs: list[dict] = []
    for row in _TR_RE.findall(page_html):
        cells = [html.unescape(_TAG_RE.sub("", c)).strip() for c in _TD_RE.findall(row)]
        if len(cells) < 3:
            continue
        combo = cells[0]
        if " - " not in combo:
            continue
        total_raw = cells[1].replace(",", "")
        daily_raw = cells[2].replace(",", "")
        if not total_raw.isdigit():
            continue
        artist, title = combo.split(" - ", 1)
        title = title.strip()
        artist = artist.strip()
        if not title or not artist:
            continue
        songs.append({
            "title": title,
            "artist": artist,
            "position": len(songs) + 1,
            "total_streams": int(total_raw),
            "daily_streams": int(daily_raw) if daily_raw.isdigit() else None,
            "chart_source": chart_source,
        })
        if len(songs) >= count:
            break
    return songs


def fetch_kworb_alltime_songs(count: int = 100, _retries: int = 3) -> list[dict]:
    """Most-Streamed Songs of All Time -- Spotify GLOBAL lifetime streams. Backs
    the all-time chart page (refreshed monthly), NOT the daily reading.

    Reads kworb.net's all-time board -- plain HTML table, no browser, no key
    (Spotify's own API exposes per-track popularity 0-100 but not lifetime
    stream counts, so kworb is the only machine-readable source). Returns up to
    `count` songs with total + daily stream counts, or [] if every attempt
    fails. Mirrors fetch_shazam_songs: plain HTTP GET + retry loop, explicit
    UTF-8 decode (kworb omits a charset header)."""
    chart_source = "spotify_alltime_global"
    songs: list[dict] = []

    for attempt in range(1, _retries + 1):
        try:
            resp = httpx.get(
                KWORB_ALLTIME_SONGS_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
            songs = _parse_kworb_alltime_rows(
                resp.content.decode("utf-8", "replace"), count, chart_source
            )

            if songs:
                logger.info("Fetched %d songs from %s", len(songs), chart_source)
                return songs

            logger.warning(
                "Attempt %d/%d: %s table returned no usable rows, retrying...",
                attempt, _retries, chart_source,
            )
        except Exception:
            logger.exception("Attempt %d/%d: %s fetch failed", attempt, _retries, chart_source)

    logger.error("All %d attempts failed for %s (last got %d)", _retries, chart_source, len(songs))
    return songs


def fetch_kworb_alltime_albums(count: int = 100, _retries: int = 3) -> list[dict]:
    """Most-Streamed Albums of All Time -- Spotify GLOBAL lifetime streams. The
    streaming-era twin of the RIAA best-sellers list. Reads kworb.net's all-time
    albums board, which shares the songs board's table shape (Artist - Title /
    total / daily), so `_parse_kworb_alltime_rows` parses it unchanged. The
    `title` it yields is the album title. Returns up to `count`, or [] on total
    failure."""
    chart_source = "spotify_alltime_albums_global"
    rows: list[dict] = []

    for attempt in range(1, _retries + 1):
        try:
            resp = httpx.get(
                KWORB_ALLTIME_ALBUMS_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
            rows = _parse_kworb_alltime_rows(
                resp.content.decode("utf-8", "replace"), count, chart_source
            )
            if rows:
                logger.info("Fetched %d albums from %s", len(rows), chart_source)
                return rows
            logger.warning(
                "Attempt %d/%d: %s table returned no usable rows, retrying...",
                attempt, _retries, chart_source,
            )
        except Exception:
            logger.exception("Attempt %d/%d: %s fetch failed", attempt, _retries, chart_source)

    logger.error("All %d attempts failed for %s (last got %d)", _retries, chart_source, len(rows))
    return rows


def _resolve_youtube_chart_playlist(yt: YTMusic) -> str | None:
    """Find the Trending chart playlist id from today's YouTube Music charts.
    The video-chart entries each carry a playlistId; ids rotate, so this resolves
    fresh. Falls back to the first chart playlist if no 'trending' match."""
    charts = yt.get_charts(country=YOUTUBE_REGION)
    vids = charts.get("videos") or []
    items = vids.get("items", []) if isinstance(vids, dict) else vids
    entries = [it for it in items if isinstance(it, dict) and it.get("playlistId")]
    for it in entries:
        if YOUTUBE_CHART_MATCH in (it.get("title") or "").lower():
            return it["playlistId"]
    return entries[0]["playlistId"] if entries else None


def fetch_youtube_songs(count: int = 20, _retries: int = 3) -> list[dict]:
    """YouTube Trending - USA. Ingestion-discovery source: what people are
    discovering right now on YouTube, the pre-mainstream intent the dead TikTok
    Billboard chart used to carry.

    Reads YouTube Music's Trending chart via ytmusicapi (clean field-separated
    title/artist; no key). Returns up to `count` songs, or [] if every attempt
    fails (the caller 502s on empty). Mirrors the other fetchers: retry loop,
    fail-soft. Two hops per attempt -- resolve the chart playlist id (it rotates),
    then read its tracks.
    """
    chart_source = "youtube_trending_usa"
    songs: list[dict] = []

    for attempt in range(1, _retries + 1):
        try:
            yt = YTMusic()
            pid = _resolve_youtube_chart_playlist(yt)
            if not pid:
                logger.warning("Attempt %d/%d: %s no chart playlist found, retrying...",
                               attempt, _retries, chart_source)
                continue
            tracks = (yt.get_playlist(pid, limit=count) or {}).get("tracks") or []

            songs = []
            for pos, t in enumerate(tracks[:count], start=1):
                title = (t.get("title") or "").strip()
                artist = ", ".join(
                    a.get("name", "").strip() for a in (t.get("artists") or []) if a.get("name")
                ).strip()
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
                "Attempt %d/%d: %s playlist returned no usable tracks, retrying...",
                attempt, _retries, chart_source,
            )
        except Exception:
            logger.exception("Attempt %d/%d: %s fetch failed", attempt, _retries, chart_source)

    logger.error("All %d attempts failed for %s (last got %d)", _retries, chart_source, len(songs))
    return songs
