"""Dynamic, DB-backed sitemap for risingcompass.net.

Replaces the old static `frontend/sitemap.xml` (top-level pages only,
regenerated on deploy) with a sitemap INDEX served live by the backend, so
songs added daily by the reading/iTunes crons + Lyrical Charger appear without
waiting for a deploy.

Layout (the canonical "sitemap index" shape large catalog sites use, e.g.
Genius):

    /sitemap.xml                 <- sitemap INDEX (this router)
       |-- /sitemap/pages.xml    <- the ~26 static pages (scanned from frontend)
       |-- /sitemap/songs-1.xml  <- calibrated songs 1..SHARD_SIZE, lastmod/row
       |-- /sitemap/songs-2.xml  <- auto-appears only once songs > SHARD_SIZE
       |-- ...

Each child sitemap is capped at the protocol limit (50,000 URLs / 50 MB). The
index itself can hold 50,000 children, so this scales to ~2.5B URLs without any
further change -- the song shard count is derived from the live row count on
every request, so new shards light up on their own as the corpus grows.

`robots.txt` already advertises `https://risingcompass.net/sitemap.xml`; that
line is unchanged -- it now points at this index.

Served WITHOUT the X-Api-Key gate (registered plainly in main.py, like
page_ssr) so crawlers can read it. nginx on the root host proxies
`= /sitemap.xml` and `/sitemap/` here (see deploy/nginx notes).
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import text

from app.database import SessionLocal
from app.routers.page_ssr import _FRONTEND_DIR, _SITE
from app.services.artist_utils import generate_song_slug

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sitemap"])

# Protocol cap is 50,000 URLs / 50 MB per child sitemap. Kept at the max so the
# index stays tiny; lower it only if a single shard's byte size approaches 50 MB.
SHARD_SIZE = 50_000

# Pages the top-level scan can't find (it never recurses). Mirrors the retired
# frontend/scripts/generate-sitemap.py EXTRA_PAGES -- keep the two in sync until
# that script is removed.
_EXTRA_PAGES = [
    "/lyrical-charger/activity/",
    "/charts/streamed-all-time/",
    "/charts/most-streamed-albums/",
    "/charts/best-selling-albums/",
]
# cards = the card-render harness (noindex); account = per-user sign-in;
# dev = internal roadmap/changelog. None belong in a crawl seed list.
_EXCLUDED_DIR_NAMES = {"css", "img", "js", "scripts", "songs", "cards", "account", "dev"}
_EXCLUDED_FILE_NAMES = {"sitemap.xml", "robots.txt", "_headers"}

# The historical backfill stamps created_at with the song's chart year (back
# to 1960). A page can't have been modified before the site existed, and GSC
# rejects such lastmod values as invalid dates -- omit the tag instead
# (lastmod is optional).
_LASTMOD_FLOOR = datetime(2025, 1, 1)

_DAILY_PATHS = {
    "/", "/calibration-log/", "/artists/", "/search/", "/library/",
    "/calendar/", "/lyrical-charger/activity/",
}
_MONTHLY_PATHS = {
    "/privacy.html", "/tenets/", "/misread-submission.html", "/amendments/",
}
_LOW_PRIORITY_PATHS = {"/privacy.html", "/misread-submission.html"}

# Small in-process TTL cache so a burst of crawler hits doesn't re-query the DB
# (or re-scan the frontend) on every request. Bodies are tiny; 10 min is plenty.
_CACHE_TTL = 600.0
_cache: dict[str, tuple[float, bytes]] = {}


def _xml(body: str) -> Response:
    return Response(
        content=body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _cached(key: str, build) -> Response:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return _xml(hit[1].decode("utf-8"))
    body = build()
    _cache[key] = (now, body.encode("utf-8"))
    return _xml(body)


# --- page scan (top-level frontend pages) ---------------------------------

def _page_paths() -> list[str]:
    """Top-level page URLs: '/', each direct subdir with an index.html, and each
    root-level *.html. Mirrors the old generate-sitemap.py scan."""
    paths: list[str] = ["/"]
    base = Path(_FRONTEND_DIR)
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        entries = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if entry.name in _EXCLUDED_DIR_NAMES:
                continue
            if (entry / "index.html").is_file():
                paths.append(f"/{entry.name}/")
        elif entry.is_file():
            if entry.name in _EXCLUDED_FILE_NAMES or entry.name == "index.html":
                continue
            if entry.suffix.lower() == ".html":
                paths.append(f"/{entry.name}")
    for extra in _EXTRA_PAGES:
        if extra not in paths:
            paths.append(extra)
    return paths


def _page_priority(path: str) -> str:
    if path == "/":
        return "1.0"
    if path in _LOW_PRIORITY_PATHS:
        return "0.5"
    return "0.8"


def _page_changefreq(path: str) -> str:
    if path == "/":
        return "daily"
    if path in _DAILY_PATHS:
        return "daily"
    if path in _MONTHLY_PATHS:
        return "monthly"
    return "weekly"


# --- song shards ----------------------------------------------------------

def _song_count() -> int:
    """Every calibrated song (rubric_color set) -- all are content-rich and
    resolve to a live page. Slug presence is NOT required: the chokepoint now
    persists one for every song, and any pre-fix gap falls back to the computed
    slug below, so keying the count off the slug table would undercount."""
    with SessionLocal() as db:
        return int(db.execute(text(
            "SELECT count(*) FROM songs WHERE rubric_color IS NOT NULL"
        )).scalar() or 0)


def _shard_count() -> int:
    return max(1, math.ceil(_song_count() / SHARD_SIZE))


def _song_rows(shard: int) -> list[tuple[str, datetime | None]]:
    """One canonical (slug, lastmod) per calibrated song in this shard, ordered
    by id so paging is stable. Prefer the persisted slug (the collision-resolved
    one the read paths use); fall back to the computed slug for any song not yet
    backfilled -- both resolve to the same page."""
    offset = (shard - 1) * SHARD_SIZE
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT s.title, s.artist, "
            "  (SELECT slug FROM song_slugs WHERE song_id = s.id ORDER BY id LIMIT 1) AS slug, "
            "  COALESCE(s.societal_prose_generated_at, s.created_at) AS lastmod "
            "FROM songs s "
            "WHERE s.rubric_color IS NOT NULL "
            "ORDER BY s.id "
            "LIMIT :lim OFFSET :off"
        ), {"lim": SHARD_SIZE, "off": offset}).all()
    out: list[tuple[str, datetime | None]] = []
    for title, artist, slug, lastmod in rows:
        slug = slug or generate_song_slug(title or "", artist or "")
        if slug:
            out.append((slug, lastmod))
    return out


# --- builders -------------------------------------------------------------

def _build_index() -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <sitemap>",
        f"    <loc>{escape(_SITE)}/sitemap/pages.xml</loc>",
        "  </sitemap>",
    ]
    for n in range(1, _shard_count() + 1):
        out.append("  <sitemap>")
        out.append(f"    <loc>{escape(_SITE)}/sitemap/songs-{n}.xml</loc>")
        out.append("  </sitemap>")
    out.append("</sitemapindex>")
    return "\n".join(out) + "\n"


def _topic_paths() -> list[str]:
    """One URL per ether topic. Read from the live taxonomy rather than a
    hardcoded list, so a topic added in admin appears here without a deploy.
    Slug rename is disabled by design, so these URLs cannot churn underneath a
    crawler. Fails soft: a taxonomy read error drops the topic block rather
    than breaking the whole sitemap."""
    try:
        from app.database import SessionLocal
        from app.services import ether_taxonomy
        with SessionLocal() as db:
            slugs = sorted(ether_taxonomy.topic_hierarchy(db).get("topics", {}).keys())
        return [f"/topics/{s}" for s in slugs]
    except Exception:
        logger.exception("sitemap: topic paths unavailable")
        return []


def _theme_paths() -> list[str]:
    """One URL per theme, the parent tier above topics. Same live-taxonomy
    read and the same fail-soft posture as the topics above."""
    try:
        from app.database import SessionLocal
        from app.services import ether_taxonomy
        with SessionLocal() as db:
            themes = ether_taxonomy.topic_hierarchy(db).get("themes", [])
        return [f"/themes/{t['slug']}" for t in themes]
    except Exception:
        logger.exception("sitemap: theme paths unavailable")
        return []


def _build_pages() -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?xml-stylesheet type="text/xsl" href="/sitemap-style.xml"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for p in _page_paths() + _theme_paths() + _topic_paths():
        out.append("  <url>")
        out.append(f"    <loc>{escape(_SITE + p)}</loc>")
        out.append(f"    <changefreq>{_page_changefreq(p)}</changefreq>")
        out.append(f"    <priority>{_page_priority(p)}</priority>")
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def _build_songs(shard: int) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    seen: set[str] = set()
    for slug, lastmod in _song_rows(shard):
        if slug in seen:
            continue
        seen.add(slug)
        out.append("  <url>")
        out.append(f"    <loc>{escape(f'{_SITE}/songs/{slug}')}</loc>")
        if lastmod is not None and lastmod >= _LASTMOD_FLOOR:
            out.append(f"    <lastmod>{lastmod.strftime('%Y-%m-%d')}</lastmod>")
        out.append("    <changefreq>weekly</changefreq>")
        out.append("    <priority>0.7</priority>")
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


# --- routes ---------------------------------------------------------------

@router.get("/sitemap.xml")
def sitemap_index() -> Response:
    return _cached("index", _build_index)


@router.get("/sitemap/pages.xml")
def sitemap_pages() -> Response:
    return _cached("pages", _build_pages)


@router.get("/sitemap/songs-{shard}.xml")
def sitemap_songs(shard: int) -> Response:
    if shard < 1 or shard > _shard_count():
        return Response("Not found", status_code=404)
    return _cached(f"songs-{shard}", lambda: _build_songs(shard))
