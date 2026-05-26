"""Server-side meta rendering for public detail pages (song, artist).

These pages are otherwise static HTML hydrated by client JS, which means the
per-entity <title>/<meta>/og:* tags only exist AFTER JS runs -- invisible to
social unfurlers and most LLM/answer-engine crawlers (the GEO audience), which
read the raw HTML and don't execute JS. This router serves the same static
template but bakes the per-entity meta + JSON-LD into the <head> from the live
DB row, so the specificity reaches every crawler. The body still hydrates via
the existing JS (the route returns the unchanged body).

Computed live from the current row, so recalibration / rename / merge are all
reflected automatically -- no regeneration hooks, no backfill, never stale.

Wiring (see docker-compose + nginx): the frontend dir is mounted read-only into
the backend, and nginx/dev_server route /songs/<slug> + /artists/<slug> here
(file assets like /songs/songs.js keep being served statically -- the routes
only match a single dotless path segment).
"""

import json
import logging
import os
import re
from html import escape as _esc
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import SessionLocal
from app.models import Artist

logger = logging.getLogger(__name__)

router = APIRouter(tags=["page-ssr"])

def _resolve_frontend_dir() -> Path:
    """Locate the frontend dir without depending on directory depth (which
    differs between local `repo/backend/app/...` and the container's flattened
    `/app/app/...`). Honors $FRONTEND_DIR, else walks up looking for the dir
    that actually contains the templates."""
    explicit = os.environ.get("FRONTEND_DIR", "").strip()
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "frontend"
        if (cand / "songs" / "song.html").is_file():
            return cand
    return here.parents[2] / "frontend"  # last-resort guess


_FRONTEND_DIR = _resolve_frontend_dir()

_SITE = (settings.site_url or "https://risingcompass.net").rstrip("/")

# In-memory template cache keyed by path; refreshed when the file mtime changes
# (so a frontend git pull is picked up without a backend restart).
_tpl_cache: dict[str, tuple[float, str]] = {}


def _load_template(rel_path: str) -> str | None:
    fp = _FRONTEND_DIR / rel_path
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        logger.warning("page_ssr: template not found at %s", fp)
        return None
    cached = _tpl_cache.get(rel_path)
    if cached and cached[0] == mtime:
        return cached[1]
    text = fp.read_text(encoding="utf-8")
    _tpl_cache[rel_path] = (mtime, text)
    return text


# --- head injection --------------------------------------------------------

def _set_title(html: str, value: str) -> str:
    # <title> is text content, not an attribute -- escape & and < only, leave
    # quotes raw (quote=True would emit &quot; which shows literally in source).
    return re.sub(
        r'(<title id="page-title">).*?(</title>)',
        lambda m: m.group(1) + _esc(value, quote=False) + m.group(2),
        html, count=1, flags=re.S,
    )


def _set_attr(html: str, tag_id: str, attr: str, value: str) -> str:
    # Replace `attr="..."` within the single tag bearing id="tag_id".
    pattern = re.compile(
        r'(<[^>]*\bid="' + re.escape(tag_id) + r'"[^>]*\b' + re.escape(attr) + r'=")[^"]*(")'
    )
    return pattern.sub(lambda m: m.group(1) + _esc(value) + m.group(2), html, count=1)


def _inject(html: str, *, title: str, description: str, canonical: str,
            json_ld: dict) -> str:
    html = _set_title(html, title)
    html = _set_attr(html, "meta-description", "content", description)
    html = _set_attr(html, "og-title", "content", title)
    html = _set_attr(html, "og-description", "content", description)
    html = _set_attr(html, "og-url", "content", canonical)
    html = _set_attr(html, "canonical-link", "href", canonical)
    # JSON-LD: escape "<" so a "</script>" inside content can't break out.
    payload = json.dumps(json_ld, ensure_ascii=False).replace("<", "\\u003c")
    script = f'<script type="application/ld+json">{payload}</script>\n</head>'
    return html.replace("</head>", script, 1)


def _faq_ld(question: str, answer: str, url: str) -> dict:
    """FAQPage schema: the natural-language question + its answer. This is the
    GEO payload answer engines lift directly."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "url": url,
        "mainEntity": [{
            "@type": "Question",
            "name": question,
            "acceptedAnswer": {"@type": "Answer", "text": answer},
        }],
    }


# --- charge spectrum (mirror of js/charge.js) ------------------------------
# Bake the song's exact border + inner-glow color into the SSR HTML so the hero
# panel paints correctly on first load, instead of popping in ~1s later when
# client JS recolors it after the data fetch.
_SPECTRUM = ["#aa54ff", "#3388ff", "#33cc55", "#ffbb33", "#ff3333"]


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _spectrum_rgb(score: float):
    t = max(0.0, min(1.0, (100 - score) / 200))  # +100 -> violet, -100 -> red
    seg = t * (len(_SPECTRUM) - 1)
    i = min(len(_SPECTRUM) - 2, int(seg))
    f = seg - i
    a = _hex_to_rgb(_SPECTRUM[i])
    b = _hex_to_rgb(_SPECTRUM[i + 1])
    return tuple(round(a[j] + (b[j] - a[j]) * f) for j in range(3))


def _hero_style(charge) -> str:
    """Inline --charge-color / --charge-glow for the hero section, or '' when
    uncalibrated (leave the default border)."""
    if charge is None:
        return ""
    r, g, b = _spectrum_rgb(charge)
    return f"--charge-color: #{r:02x}{g:02x}{b:02x}; --charge-glow: rgba({r}, {g}, {b}, 0.45);"


# --- routes ----------------------------------------------------------------

@router.get("/songs/{slug}", response_class=HTMLResponse)
def ssr_song(slug: str):
    tpl = _load_template("songs/song.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    # Reuse the canonical lookup. Returns the song dict, or raises 404 -> we
    # serve the generic template and let the client JS render not-found.
    from app.routers.songs import song_detail
    from fastapi import HTTPException
    try:
        song = song_detail(slug)
    except HTTPException:
        return HTMLResponse(tpl)  # generic meta; JS shows "Song not found"

    title = song.get("title") or "this song"
    artist = song.get("artist")
    tagline = f'"{title}" by {artist}' if artist else f'"{title}"'
    question = f"What is {tagline} about?"
    summary = song.get("charge_summary")
    answer = (
        f"{question} {summary}" if summary
        else f"This page reads the meaning behind the lyrics of {tagline}, classified by The Rising Compass."
    )
    description = (
        f"This page answers what {tagline} is about - the meaning behind the lyrics. {summary}"
        if summary else
        f"This page answers what {tagline} is about - the meaning behind the lyrics, classified by The Rising Compass."
    )
    canonical = f"{_SITE}/songs/{slug}"

    html = _inject(
        tpl,
        title=f"{question} - The Rising Compass",
        description=description,
        canonical=canonical,
        json_ld=_faq_ld(question, answer, canonical),
    )
    # Bake the hero panel's spectrum border + glow into first paint.
    style = _hero_style(song.get("charge_value"))
    if style:
        html = html.replace(
            '<section class="song-section song-section--hero"',
            f'<section class="song-section song-section--hero" style="{style}"',
            1,
        )
    return HTMLResponse(html)


@router.get("/artists/{slug}", response_class=HTMLResponse)
def ssr_artist(slug: str):
    tpl = _load_template("artists/artist.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
    finally:
        db.close()
    if artist is None:
        return HTMLResponse(tpl)  # generic meta; JS shows not-found

    name = artist.name
    question = f"What are {name}'s songs about?"
    description = (
        f"This page answers what {name}'s songs are about - the meaning behind "
        f"their lyrics, classified by The Rising Compass."
    )
    canonical = f"{_SITE}/artists/{slug}"

    html = _inject(
        tpl,
        title=f"{question} - The Rising Compass",
        description=description,
        canonical=canonical,
        json_ld=_faq_ld(question, description, canonical),
    )
    return HTMLResponse(html)
