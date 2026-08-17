"""RSS 2.0 feeds for the five daily/weekly chart readings.

One feed per chart, each item = one published chart-day: the reading's aggregate
(degree + charge label), its terminal-supplied editorial, and the ranked song
list with each song's tier, charge, and link.

    /feeds/spotify.xml            Spotify (US)        <- the daily reading
    /feeds/itunes.xml             iTunes
    /feeds/shazam.xml             Shazam
    /feeds/youtube.xml            YouTube
    /feeds/new-music-friday.xml   New Music Friday

Two data shapes sit behind those. The Spotify feed reads `daily_readings` +
`reading_songs` (the Tier 1 daily reading, which carries its own record and
whose songs hold a real song_id FK). The other four read published
`chart_snapshots` rows, whose per-song calibration is resolved live against the
unified songs table exactly as the public chart endpoint does -- a snapshot row
stores only (title, artist, position).

Served WITHOUT the X-Api-Key gate (registered plainly in main.py, like
`sitemap`), because a feed reader cannot send a header. Everything published
here is already public on the chart pages; nothing unapproved is served, since
both sources are filtered to their published/approved state.

Bodies are built on demand and held in a short TTL cache: the underlying data
changes at most once per chart per day, and a feed reader polling every few
minutes should never touch the DB.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ChartSnapshot, DailyReading, ReadingSong, Song
from app.routers.page_ssr import _SITE
from app.services.artist_utils import generate_song_slug
from app.services.listener_effects_prose import TIER_LABELS
from app.services.song_store import find_song_by_title_artist

router = APIRouter(tags=["feeds"])

# How many chart-days each feed carries. Daily charts: about a month of history.
# Deliberately small -- a feed is a recency surface, and the Calendar already
# holds the archive.
MAX_ITEMS = 30

# Feed bodies change once a day per chart. 15 minutes keeps a polling reader off
# the DB entirely while still surfacing a fresh approval promptly.
_CACHE_TTL = 900.0
_cache: dict[str, tuple[float, bytes]] = {}

# The five public chart feeds. `source` is None for the daily reading (which has
# its own table) and a chart_snapshots.chart_source slug for the rest. `page` is
# the canonical chart page each item links to.
FEEDS: dict[str, dict] = {
    "spotify": {
        "source": None,
        "title": "Spotify (US)",
        "sub": "Spotify Top 50 - USA",
        "page": "/charts/spotify/",
    },
    "itunes": {
        "source": "itunes_download_usa",
        "title": "iTunes",
        "sub": "iTunes Downloads - USA",
        "page": "/charts/itunes/",
    },
    "shazam": {
        "source": "shazam_top200_usa",
        "title": "Shazam",
        "sub": "Shazam Top 200 - USA",
        "page": "/charts/shazam/",
    },
    "youtube": {
        "source": "youtube_trending_usa",
        "title": "YouTube",
        "sub": "YouTube Trending - USA",
        "page": "/charts/youtube/",
    },
    "new-music-friday": {
        "source": "spotify_nmf_usa",
        "title": "New Music Friday",
        "sub": "New Music Friday - USA",
        "page": "/charts/new-music-friday/",
    },
}

# The two front-facing names the Calendar toggle uses, kept as aliases so a
# subscriber who guessed from the site's own vocabulary still lands on a feed.
_ALIASES = {"daily-listens": "spotify", "daily-downloads": "itunes"}


# --- item assembly --------------------------------------------------------

class _Item:
    """One chart-day, already flattened for rendering."""

    def __init__(self, day: date, degree: float | None, charge: str | None,
                 editorial: str | None, songs: list[dict]):
        self.day = day
        self.degree = degree
        self.charge = charge
        self.editorial = editorial
        self.songs = songs


def _song_row(title: str, artist: str, position: int, song: Song | None) -> dict:
    """The per-song fields a feed item shows. Mirrors the public chart payload,
    minus the prose (a feed points at the page; it does not reproduce it)."""
    return {
        "position": position,
        "title": title,
        "artist": artist,
        "color": song.rubric_color if song else None,
        "charge": song.charge_value if song else None,
        "contaminated": bool(song.contaminated) if song else False,
        "slug": generate_song_slug(title or "", artist or ""),
    }


def _reading_items(db: Session) -> list[_Item]:
    """The daily reading (Spotify Top 50). Songs carry a song_id FK, so one
    eager join covers the whole feed."""
    readings = (
        db.query(DailyReading)
        .order_by(DailyReading.date.desc())
        .limit(MAX_ITEMS)
        .all()
    )
    if not readings:
        return []

    ids = [r.id for r in readings]
    rows = (
        db.query(ReadingSong, Song)
        .outerjoin(Song, Song.id == ReadingSong.song_id)
        .filter(ReadingSong.reading_id.in_(ids))
        .order_by(ReadingSong.reading_id, ReadingSong.position.asc())
        .all()
    )
    by_reading: dict[int, list[dict]] = {}
    for rs, song in rows:
        by_reading.setdefault(rs.reading_id, []).append(
            _song_row(rs.title, rs.artist, rs.position, song)
        )

    return [
        _Item(
            day=r.date,
            degree=r.compass_degree,
            charge=r.charge_level,
            editorial=r.editorial_summary,
            songs=by_reading.get(r.id, []),
        )
        for r in readings
    ]


def _snapshot_items(db: Session, chart_source: str) -> list[_Item]:
    """A chart-snapshot chart. Only published (approved) rows, and the per-song
    calibration is resolved through the identity ladder the same way the public
    endpoint does. Resolutions are memoized across days, since a chart repeats
    most of its list from one day to the next."""
    days = [
        d for (d,) in (
            db.query(ChartSnapshot.date)
            .filter(
                ChartSnapshot.chart_source == chart_source,
                ChartSnapshot.published.is_(True),
            )
            .group_by(ChartSnapshot.date)
            .order_by(ChartSnapshot.date.desc())
            .limit(MAX_ITEMS)
            .all()
        )
    ]
    if not days:
        return []

    snaps = (
        db.query(ChartSnapshot)
        .filter(
            ChartSnapshot.chart_source == chart_source,
            ChartSnapshot.published.is_(True),
            ChartSnapshot.date.in_(days),
        )
        .order_by(ChartSnapshot.date.desc(), ChartSnapshot.position.asc())
        .all()
    )

    resolved: dict[tuple[str, str], Song | None] = {}

    def _lookup(title: str, artist: str) -> Song | None:
        key = ((title or "").strip().lower(), (artist or "").strip().lower())
        if key not in resolved:
            resolved[key] = find_song_by_title_artist(db, title, artist)
        return resolved[key]

    grouped: dict[date, list[ChartSnapshot]] = {}
    for snap in snaps:
        grouped.setdefault(snap.date, []).append(snap)

    items: list[_Item] = []
    for day in days:
        rows = grouped.get(day, [])
        if not rows:
            continue
        head = rows[0]
        items.append(_Item(
            day=day,
            degree=head.compass_degree,
            charge=head.charge_level,
            editorial=head.editorial,
            songs=[
                _song_row(s.title, s.artist, s.position, _lookup(s.title, s.artist))
                for s in rows
                if not s.preorder
            ],
        ))
    return items


# --- rendering ------------------------------------------------------------

def _pub_date(day: date) -> str:
    """RFC 2822 timestamp for a chart-day. The readings carry a date but no
    approval time, so every item is stamped at midday UTC: stable across
    rebuilds (a shifting pubDate makes readers re-flag old items as new) and
    safely after each chart's morning cron."""
    return format_datetime(datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc))


def _charge_label(charge: str | None) -> str | None:
    """`charge_level` is stored as the tier's color key (the frontend maps it at
    render time). A feed has no stylesheet, so map it to the public label here;
    anything unrecognized passes through untouched."""
    if not charge:
        return None
    return TIER_LABELS.get(charge.lower(), charge)


def _item_title(feed_title: str, item: _Item) -> str:
    # Built piecewise rather than with %-d, which is not portable to Windows.
    stamp = f"{item.day.strftime('%B')} {item.day.day}, {item.day.year}"
    charge = _charge_label(item.charge)
    if charge and item.degree is not None:
        return f"{feed_title}, {stamp}: {charge} ({item.degree:g})"
    return f"{feed_title}, {stamp}"


def _item_body(item: _Item) -> str:
    """The item description: the editorial, then the ranked list. HTML, wrapped
    in CDATA by the caller."""
    parts: list[str] = []
    if item.editorial:
        parts.append(f"<p>{escape(item.editorial)}</p>")
    if item.degree is not None:
        label = _charge_label(item.charge)
        charge = escape(label) if label else ""
        parts.append(f"<p><strong>Degree {item.degree:g}</strong>{f' ({charge})' if charge else ''}</p>")
    if item.songs:
        parts.append("<ol>")
        for s in item.songs:
            tier = TIER_LABELS.get(s["color"] or "", "")
            reading = ""
            if tier and s["charge"] is not None:
                reading = f" &mdash; {escape(tier)} {s['charge']:+d}"
            elif tier:
                reading = f" &mdash; {escape(tier)}"
            mark = " (contaminated)" if s["contaminated"] else ""
            link = f"{_SITE}/songs/{s['slug']}" if s["slug"] else None
            name = escape(s["title"] or "")
            if link:
                name = f'<a href="{escape(link)}">{name}</a>'
            parts.append(
                f"<li>{name} &ndash; {escape(s['artist'] or '')}{reading}{mark}</li>"
            )
        parts.append("</ol>")
    return "\n".join(parts) or "<p>No reading published for this day.</p>"


def _build(key: str) -> str:
    meta = FEEDS[key]
    with SessionLocal() as db:
        items = (
            _reading_items(db) if meta["source"] is None
            else _snapshot_items(db, meta["source"])
        )

    feed_url = f"{_SITE}/feeds/{key}.xml"
    page_url = f"{_SITE}{meta['page']}"
    title = f"The Rising Compass: {meta['title']}"
    desc = (
        f"Daily lyric readings of the {meta['sub']} chart: each day's charge, "
        "its editorial, and every song's tier."
    )
    built = items[0].day if items else None

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{escape(title)}</title>",
        f"    <link>{escape(page_url)}</link>",
        f"    <description>{escape(desc)}</description>",
        "    <language>en-us</language>",
        f'    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml" />',
    ]
    if built:
        out.append(f"    <lastBuildDate>{_pub_date(built)}</lastBuildDate>")
    for item in items:
        out += [
            "    <item>",
            f"      <title>{escape(_item_title(meta['title'], item))}</title>",
            f"      <link>{escape(page_url)}</link>",
            f'      <guid isPermaLink="false">rc-{key}-{item.day.isoformat()}</guid>',
            f"      <pubDate>{_pub_date(item.day)}</pubDate>",
            f"      <description><![CDATA[{_item_body(item)}]]></description>",
            "    </item>",
        ]
    out += ["  </channel>", "</rss>"]
    return "\n".join(out) + "\n"


def _rss(body: str) -> Response:
    return Response(
        content=body,
        media_type="application/rss+xml",
        headers={"Cache-Control": "public, max-age=900"},
    )


def _cached(key: str) -> Response:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return _rss(hit[1].decode("utf-8"))
    body = _build(key)
    _cache[key] = (now, body.encode("utf-8"))
    return _rss(body)


# --- routes ---------------------------------------------------------------

@router.get("/feeds/{key}.xml")
def chart_feed(key: str) -> Response:
    key = _ALIASES.get(key, key)
    if key not in FEEDS:
        return Response("Not found", status_code=404)
    return _cached(key)
