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
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

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


def _clamp(text: str, limit: int = 165) -> str:
    """Collapse whitespace and trim a meta description to a SERP-safe length on
    a word boundary. Google renders ~155-165 chars; cutting mid-word looks
    broken, and a stray newline from prose would break the meta tag."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{cut}..."


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
    # serve the generic template with a REAL 404 status (a 200 here is a soft
    # 404 to crawlers) and let the client JS render not-found.
    from app.routers.songs import song_detail
    from fastapi import HTTPException
    try:
        song = song_detail(slug)
    except HTTPException:
        return HTMLResponse(tpl, status_code=404)

    # A song can carry alias slugs (collision suffixes, merges). Only the
    # persisted first slug -- the one the sitemap advertises -- may serve
    # content; 301 the rest so crawlers never see two self-canonical copies.
    sid = song.get("song_id")
    if sid:
        with SessionLocal() as db:
            canonical_slug = db.execute(text(
                "SELECT slug FROM song_slugs WHERE song_id = :i ORDER BY id LIMIT 1"
            ), {"i": sid}).scalar()
        if canonical_slug and canonical_slug != slug:
            return RedirectResponse(f"/songs/{canonical_slug}", status_code=301)

    title = song.get("title") or "this song"
    artist = song.get("artist")
    tagline = f'"{title}" by {artist}' if artist else f'"{title}"'
    question = f"What is {tagline} about?"
    summary = song.get("charge_summary")
    answer = (
        f"{question} {summary}" if summary
        else f"This page reads the meaning behind the lyrics of {tagline}, calibrated by The Rising Compass."
    )
    # Meta description: lead with the page-unique reading (charge_summary, drawn
    # from the prose) so every snippet differs and the searcher sees the actual
    # answer; open with the "what ... means" intent phrase so the query terms
    # bold in the SERP. Raw tier/charge stay OUT of the visible text -- "Degraded
    # -44" reads as jargon to a cold searcher and would depress CTR.
    description = _clamp(
        f"What {tagline} means: {summary}" if summary
        else f"What does {tagline} mean? The Rising Compass reads the meaning behind the lyrics."
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


@router.get("/artists/{slug}/{release_slug}", response_class=HTMLResponse)
def ssr_release(slug: str, release_slug: str):
    tpl = _load_template("artists/release.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    from app.routers.artists import release_detail
    from fastapi import HTTPException
    try:
        rel = release_detail(slug, release_slug)
    except HTTPException:
        # Real 404 status; generic meta, JS shows "Release not found".
        return HTMLResponse(tpl, status_code=404)

    title = rel.get("title") or "this release"
    artist = (rel.get("artist") or {}).get("name")
    rtype = rel.get("release_type")
    type_word = "album" if rtype == "album" else "EP" if rtype == "ep" else "release"
    tagline = f'"{title}" by {artist}' if artist else f'"{title}"'
    question = f"What is the {type_word} {tagline} about?"
    summary = rel.get("charge_summary")
    answer = (
        f"{question} {summary}" if summary
        else f"This page reads {tagline} as a whole - its lyrical charge across the "
             f"tracklist, calibrated by The Rising Compass."
    )
    description = (
        f"What {tagline} is about as a whole - its charge across the tracklist. {summary}"
        if summary else
        f"What {tagline} is about as a whole - its charge across the tracklist, "
        f"calibrated by The Rising Compass."
    )
    canonical = f"{_SITE}/artists/{slug}/{release_slug}"

    html = _inject(
        tpl,
        title=f"{question} - The Rising Compass",
        description=description,
        canonical=canonical,
        json_ld=_faq_ld(question, answer, canonical),
    )
    # Bake the hero panel's spectrum border + glow into first paint.
    style = _hero_style(rel.get("charge_value"))
    if style:
        html = html.replace(
            '<section class="song-section song-section--hero" id="release-hero"',
            f'<section class="song-section song-section--hero" id="release-hero" style="{style}"',
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
        # Real 404 status; generic meta, JS shows not-found.
        return HTMLResponse(tpl, status_code=404)

    name = artist.name
    question = f"What are {name}'s songs about?"
    description = (
        f"This page answers what {name}'s songs are about - the meaning behind "
        f"their lyrics, calibrated by The Rising Compass."
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


# --- all-time chart pages --------------------------------------------------
# Unlike the song/artist templates, the chart pages carry correct per-page meta
# already (they're fixed pages, not entity templates), so we don't rewrite the
# head meta. The GEO gap is that the ranked list is client-rendered -- the raw
# HTML body is an empty <div id="chart-root"></div>. So we (1) bake a schema.org
# ItemList + FAQPage into the head (the structured ranking answer engines lift)
# and (2) server-render the list into the body so non-JS crawlers see it. The
# existing alltime.js still overwrites #chart-root on load (progressive
# enhancement), so interactive users are unaffected.

def _fmt_streams(n) -> str:
    if n is None:
        return ""
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n:,}"


def _chart_configs():
    # Imported lazily so this module stays import-light and avoids any cycle.
    from app.models import AlltimeAlbum, AlltimeStreamAlbum, AlltimeStreamSong
    from app.routers.alltime_charts import (ALBUM_TOP_N, TOP_N, _album_row_out,
                                            _stream_album_row_out, _stream_row_out)

    def songs(db):
        rows = (db.query(AlltimeStreamSong)
                .order_by(AlltimeStreamSong.rank.asc()).limit(TOP_N).all())
        return [_stream_row_out(r) for r in rows]

    def stream_albums(db):
        rows = (db.query(AlltimeStreamAlbum)
                .order_by(AlltimeStreamAlbum.rank.asc()).limit(TOP_N).all())
        return [_stream_album_row_out(r) for r in rows]

    def riaa_albums(db):
        rows = (db.query(AlltimeAlbum)
                .order_by(AlltimeAlbum.rank.asc()).limit(ALBUM_TOP_N).all())
        return [_album_row_out(r) for r in rows]

    return {
        "streamed-all-time": {
            "template": "charts/streamed-all-time/index.html",
            "heading": "Most Streamed Songs of All Time",
            "noun": "song", "item_type": "MusicRecording",
            "lead": "the most-streamed songs of all time on Spotify (global lifetime streams)",
            "fetch": songs, "title_key": "title",
            "metric": lambda r: (f"{_fmt_streams(r['total_streams'])} streams"
                                 if r.get("total_streams") else ""),
            "detail": lambda r: (f"{_SITE}/songs/{r['song_slug']}"
                                 if r.get("song_slug") and r.get("rubric_color") else None),
        },
        "most-streamed-albums": {
            "template": "charts/most-streamed-albums/index.html",
            "heading": "Most Streamed Albums of All Time",
            "noun": "album", "item_type": "MusicAlbum",
            "lead": "the most-streamed albums of all time on Spotify (global lifetime streams)",
            "fetch": stream_albums, "title_key": "album_title",
            "metric": lambda r: (f"{_fmt_streams(r['total_streams'])} streams"
                                 if r.get("total_streams") else ""),
            "detail": lambda r: (f"{_SITE}/artists/{r['artist_slug']}/{r['release_slug']}"
                                 if r.get("artist_slug") and r.get("release_slug") else None),
        },
        "best-selling-albums": {
            "template": "charts/best-selling-albums/index.html",
            "heading": "Best-Selling Albums of All Time",
            "noun": "album", "item_type": "MusicAlbum",
            "lead": "the best-selling albums of all time in the US (RIAA certified units)",
            "fetch": riaa_albums, "title_key": "album_title",
            "metric": lambda r: " - ".join(
                [b for b in (r.get("certified_units"),
                             str(r["release_year"]) if r.get("release_year") else None) if b]),
            "detail": lambda r: (f"{_SITE}/artists/{r['artist_slug']}/{r['release_slug']}"
                                 if r.get("artist_slug") and r.get("release_slug") else None),
        },
    }


def _chart_itemlist_ld(cfg: dict, rows: list, canonical: str) -> dict:
    items = []
    for r in rows:
        name = r.get(cfg["title_key"]) or ""
        artist = r.get("artist") or ""
        music = {"@type": cfg["item_type"], "name": name}
        if artist:
            music["byArtist"] = {"@type": "MusicGroup", "name": artist}
        detail = cfg["detail"](r)
        if detail:
            music["url"] = detail
        items.append({"@type": "ListItem", "position": r["rank"], "item": music})
    return {
        "@type": "ItemList",
        "name": cfg["heading"],
        "url": canonical,
        "numberOfItems": len(rows),
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "itemListElement": items,
    }


def _chart_faq(cfg: dict, rows: list, canonical: str) -> dict:
    noun = cfg["noun"]
    question = f"What are {cfg['lead']}?"
    if rows:
        top = rows[0]
        nm = top.get(cfg["title_key"]) or ""
        art = top.get("artist") or ""
        metric = cfg["metric"](top)
        lead = f'The #1 is "{nm}" by {art}' + (f" ({metric})" if metric else "")
        seconds = ", ".join(f'"{x.get(cfg["title_key"])}"' for x in rows[1:4])
        if seconds:
            lead += f", followed by {seconds}"
        answer = (f"{lead}. This chart ranks the top {len(rows)} {noun}s, "
                  f"each read for the vibrational charge of its lyrics by The Rising Compass.")
    else:
        answer = f"This chart ranks {cfg['lead']}, each charged by The Rising Compass."
    return _faq_ld(question, answer, canonical)


def _chart_body_html(cfg: dict, rows: list) -> str:
    lis = []
    for r in rows:
        name = _esc(r.get(cfg["title_key"]) or "")
        artist = _esc(r.get("artist") or "")
        metric = _esc(cfg["metric"](r))
        detail = cfg["detail"](r)
        name_html = f'<a href="{_esc(detail)}">{name}</a>' if detail else name
        tier = r.get("rubric_color")
        charge_bits = ""
        if r.get("non_music"):
            charge_bits = ' <span class="ssr-tag">non-music</span>'
        elif tier:
            dp = _esc(r.get("deadpan_line") or "")
            charge_bits = f' <span class="ssr-tier">{_esc(tier)}</span>' + (f' <span class="ssr-deadpan">{dp}</span>' if dp else "")
        lis.append(
            f'<li><span class="ssr-rank">{r["rank"]}</span> '
            f'<span class="ssr-name">{name_html}</span> '
            f'<span class="ssr-artist">{artist}</span>'
            + (f' <span class="ssr-metric">{metric}</span>' if metric else "")
            + charge_bits + "</li>"
        )
    return (f'<ol class="ssr-chart-list" aria-label="{_esc(cfg["heading"])}">'
            + "".join(lis) + "</ol>")


def inject_chart_ssr(html: str, kind: str, cfg: dict, rows: list) -> str:
    """Bake the schema.org ItemList + FAQ into the head and server-render the
    ranked list into #chart-root. IDEMPOTENT: strips any prior injection first,
    so it's safe to run on an already-baked file (the static bake re-runs on each
    refresh) AND on a fresh template (dynamic render). One source of truth for
    both paths."""
    # Strip a prior bake so re-running never doubles up.
    html = re.sub(r'\s*<script type="application/ld\+json">.*?</script>', '', html, flags=re.S)
    html = re.sub(r'(<div id="chart-root">).*?(</div>)', r'\1\2', html, count=1, flags=re.S)

    canonical = f"{_SITE}/charts/{kind}/"
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            _chart_itemlist_ld(cfg, rows, canonical),
            _chart_faq(cfg, rows, canonical),
        ],
    }
    payload = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c")
    script = f'<script type="application/ld+json">{payload}</script>\n</head>'
    html = html.replace("</head>", script, 1)
    html = html.replace('<div id="chart-root"></div>',
                        f'<div id="chart-root">{_chart_body_html(cfg, rows)}</div>', 1)
    return html


def _render_chart(kind: str) -> HTMLResponse:
    cfg = _chart_configs().get(kind)
    if cfg is None:
        return HTMLResponse("Not found", status_code=404)
    tpl = _load_template(cfg["template"])
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    db = SessionLocal()
    try:
        rows = cfg["fetch"](db)
    finally:
        db.close()

    return HTMLResponse(inject_chart_ssr(tpl, kind, cfg, rows))


@router.get("/charts/streamed-all-time/", response_class=HTMLResponse)
def ssr_chart_streamed_all_time():
    return _render_chart("streamed-all-time")


@router.get("/charts/most-streamed-albums/", response_class=HTMLResponse)
def ssr_chart_most_streamed_albums():
    return _render_chart("most-streamed-albums")


@router.get("/charts/best-selling-albums/", response_class=HTMLResponse)
def ssr_chart_best_selling_albums():
    return _render_chart("best-selling-albums")
