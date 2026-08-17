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
from urllib.parse import quote

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
            json_ld: dict | list) -> str:
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


def _breadcrumb_ld(items: list[tuple[str, str]]) -> dict:
    """BreadcrumbList from (name, url) pairs, root first. The visible crumbs and
    this list are built from the same pairs at the call site, so the structured
    hierarchy can never disagree with the one a reader sees."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(items)
        ],
    }


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


# --- body injection --------------------------------------------------------
#
# The head helpers above bake meta. These bake CONTENT. Used by the topics and
# themes family, whose every outbound link is client-rendered: the raw HTML of
# a topic page carried zero links into the 2,284 song pages it exists to reach,
# and its <h1> read "Loading...". Rendering runs only if a crawler executes the
# JS, which is a second queue and the weaker path.
#
# All four are surgical and IDEMPOTENT-SAFE: they only ever touch an element
# that is empty (or whose text they replace outright), so the client JS -- which
# overwrites the same nodes on load -- stays the source of truth for interactive
# readers. Nothing here changes what a reader sees; it changes when.

def _body_or_plain(renderer, html: str, data: dict) -> str:
    """Run a body renderer, falling back to the un-injected HTML on any error.

    The body render is an ENHANCEMENT: the page already works because the
    client JS fetches the same payload and fills the same nodes. So a bug in a
    renderer must cost a crawler its shortcut, never cost a reader the page.
    Without this, one unexpected None would 500 all 41 pages at once.
    """
    try:
        return renderer(html, data)
    except Exception:
        logger.exception("page_ssr: body render failed; serving meta-only HTML")
        return html


def _fill(html: str, elem_id: str, inner: str) -> str:
    """Render `inner` into the EMPTY element bearing this id.

    Matches only an empty element, so a template that somehow already carries
    markup is left alone rather than having a second copy nested inside it.
    """
    pattern = re.compile(
        r'(<(?P<tag>[a-zA-Z0-9]+)[^>]*\bid="' + re.escape(elem_id) + r'"[^>]*>)\s*(</(?P=tag)>)'
    )
    return pattern.sub(lambda m: m.group(1) + inner + m.group(3), html, count=1)


def _set_text(html: str, elem_id: str, value: str) -> str:
    """Replace the text content of the element bearing this id. For the plain
    single-text-node elements only (h1, p) -- it does not descend."""
    pattern = re.compile(
        r'(<(?P<tag>[a-zA-Z0-9]+)[^>]*\bid="' + re.escape(elem_id) + r'"[^>]*>).*?(</(?P=tag)>)',
        re.S,
    )
    return pattern.sub(lambda m: m.group(1) + _esc(value, quote=False) + m.group(3),
                       html, count=1)


def _show(html: str, elem_id: str) -> str:
    """Drop the `hidden` attribute from the element bearing this id.

    A section we just filled has something true to say, so it must not ship
    hidden -- hidden content is discounted, and the client JS reveals it a
    moment later anyway.
    """
    pattern = re.compile(
        r'(<[a-zA-Z0-9]+[^>]*\bid="' + re.escape(elem_id) + r'"[^>]*?)\s+hidden(\s*>)'
    )
    return pattern.sub(r"\1\2", html, count=1)


def _t(value) -> str:
    """Escape for a text node. Quotes stay raw, matching what the client's
    textContent -> innerHTML round-trip emits, so the two renders agree."""
    return _esc("" if value is None else str(value), quote=False)


def _u(slug) -> str:
    """A path segment, escaped for an href. Mirrors encodeURIComponent."""
    return _esc(quote(str(slug or ""), safe=""))


# The client scripts print an em dash where there is no reading. Kept as an
# escape so this file stays ASCII, and named so the intent survives the escape.
_NO_READING = chr(0x2014)


def _signed(v) -> str:
    """+N / N, or an em dash when there is no reading. Mirrors `signed()` in
    topics.js / themes.js -- the character matters, the two renders sit in the
    same DOM position one paint apart."""
    if v is None:
        return _NO_READING
    return f"+{v}" if v > 0 else str(v)


def _charge_hex(v) -> str:
    """The compass ramp as a hex string. Mirrors `chargeColor()` in the topic
    and theme scripts, over the same `_spectrum_rgb` the hero glow uses."""
    if v is None:
        return "#888"
    r, g, b = _spectrum_rgb(v)
    return f"#{r:02x}{g:02x}{b:02x}"


# The three row shapes the family shares. Each mirrors its counterpart in
# frontend/topics/topics.js or frontend/themes/themes.js EXACTLY -- same
# classes, same order, same text -- so hydration replaces like with like
# instead of visibly rewriting the page. Change one, change its twin.

def _ssr_song_card(s: dict) -> str:
    """`songCard` -- the span's two ends, one card each."""
    kicker = " ".join(x for x in [_t(s.get("tier_label") or ""),
                                  _signed(s.get("charge_value"))] if x)
    hexv = _t(s.get("tier_hex") or "#888")
    why = (f'<span class="related-card-why">{_t(s["deadpan_line"])}</span>'
           if s.get("deadpan_line") else "")
    return (
        '<li class="related-card">'
        f'<a class="related-card-link" href="/songs/{_u(s.get("slug"))}">'
        '<span class="related-card-kicker">'
        f'<span class="related-card-dot" style="background:{hexv}" aria-hidden="true"></span>'
        f'<span style="color:{hexv}">{kicker}</span>'
        '</span>'
        f'<span class="related-card-name">{_t(s.get("title"))}</span>'
        f'<span class="related-card-sub">{_t(s.get("artist"))}</span>'
        f'{why}</a></li>'
    )


def _ssr_song_row(s: dict) -> str:
    """`songRow` -- the ranking under the finding, charge first."""
    hexv = _t(s.get("tier_hex") or "#888")
    why = (f'<span class="topic-row-why">{_t(s["deadpan_line"])}</span>'
           if s.get("deadpan_line") else "")
    return (
        '<li class="topic-row">'
        f'<a class="topic-row-link" href="/songs/{_u(s.get("slug"))}">'
        f'<span class="topic-row-charge" style="color:{hexv}">{_signed(s.get("charge_value"))}</span>'
        '<span class="topic-row-id">'
        f'<span class="topic-row-title">{_t(s.get("title"))}</span>'
        f'<span class="topic-row-artist">{_t(s.get("artist"))}</span>'
        '</span>'
        f'{why}</a></li>'
    )


def _ssr_topic_card(t: dict) -> str:
    """`topicCard` -- a topic as a tile, on the theme page and both indexes."""
    return (
        '<li class="topic-index-card">'
        f'<a class="topic-index-link" href="/topics/{_u(t.get("slug"))}">'
        f'<span class="topic-index-name">{_t(t.get("label"))}</span>'
        '<span class="topic-index-meta">'
        f'<span class="topic-index-charge" style="color:{_charge_hex(t.get("avg_charge"))}">'
        f'{_signed(t.get("avg_charge"))} avg</span>'
        f'<span class="topic-index-songs">{t.get("songs", 0)} songs</span>'
        '</span></a></li>'
    )


def _ssr_tier_chips(tiers: dict) -> str:
    bits = [("Ascended", tiers.get("violet"), "#9933ff"),
            ("Elevated", tiers.get("blue"), "#3388ff"),
            ("Decent", tiers.get("green"), "#33cc55"),
            ("Degraded", tiers.get("orange"), "#ffbb33"),
            ("Corrupted", tiers.get("red"), "#ff3333")]
    return "".join(
        f'<span class="topic-tier-chip">'
        f'<span class="topic-tier-dot" style="background:{hexv}"></span>{n} {label}</span>'
        for label, n, hexv in bits if n
    )


def _ssr_stats_list(st: dict, lib: dict) -> str:
    pct = st.get("contaminated_pct") or 0
    contam = (f'{pct}% carry a contamination flag, against '
              f'{lib.get("contaminated_pct", 0)}% across the library'
              if pct > 0 else "None of them carry a contamination flag")
    return (
        '<li class="topic-stat"><span class="topic-stat-label">Contamination</span>'
        f'<span class="topic-stat-value">{_t(contam)}.</span></li>'
        '<li class="topic-stat"><span class="topic-stat-label">Where they land</span>'
        f'<span class="topic-stat-value">{_ssr_tier_chips(st.get("tiers") or {})}</span></li>'
    )


def _delta_clause(value, baseline, template: str) -> str:
    """The "N points above/below the library" clause, or nothing. Both pages
    suppress it under 3 points, which is where the difference stops being one."""
    if value is None or baseline is None:
        return ""
    delta = value - baseline
    if abs(delta) < 3:
        return ""
    return template.format(n=abs(delta), dir="above" if delta > 0 else "below")


def _ssr_topic_body(html: str, d: dict) -> str:
    """Server-render the topic page: its finding, its span, and the first page
    of its ranking. Mirrors `renderHead`/`renderExtremes`/`renderSiblings`/
    `renderSongs` in frontend/topics/topics.js."""
    topic, st, lib = d["topic"], d["stats"], d["library"]
    label = topic.get("label") or ""

    html = _set_text(html, "topic-title", label)
    html = _fill(html, "crumb-current", _t(label))
    if topic.get("scope"):
        html = _set_text(html, "topic-scope", topic["scope"])

    theme = topic.get("theme")
    if theme and theme.get("slug"):
        html = _fill(html, "topic-parent",
                     f'A topic under <a href="/themes/{_u(theme["slug"])}">'
                     f'{_t(theme.get("label"))}</a>')
        html = _show(html, "topic-parent")

    finding = (f'{st.get("songs", 0)} calibrated songs carry this topic. '
               f'They average {_signed(st.get("avg_charge"))}')
    finding += _delta_clause(st.get("avg_charge"), lib.get("avg_charge"),
                             ", which is {n} points {dir} the library as a whole")
    finding += (f', and they run from {_signed(st.get("min_charge"))} to '
                f'{_signed(st.get("max_charge"))}.')
    html = _set_text(html, "topic-finding", finding)
    html = _fill(html, "topic-stats", _ssr_stats_list(st, lib))

    sp = d.get("dominant_split")
    if sp:
        copy = (
            f'{sp["dominant_songs"]} of these songs are mostly about {label}, and those '
            f'average {_signed(sp["avg_dominant"])}. Where {label} is present but not the '
            f'point, the average is {_signed(sp["avg_incidental"])}. The topic reads '
            f'{"higher" if sp["delta"] > 0 else "lower"} by {abs(sp["delta"])} points '
            f'when it leads.'
        )
        html = _set_text(html, "topic-split-copy", copy)
        html = _show(html, "topic-split")

    ex = d.get("extremes") or {}
    high, low = ex.get("highest"), ex.get("lowest")
    ends = [s for s in (high, low) if s]
    if low and high and low["id"] == high["id"]:
        ends = [high]
    if ends:
        html = _fill(html, "topic-extremes-grid",
                     "".join(_ssr_song_card(s) for s in ends))
        html = _show(html, "topic-extremes")

    # The span already named these two, so the ranking skips them rather than
    # linking the same song twice on one page. Same rule as renderSongs().
    span_ids = {s["id"] for s in ends}
    rows = [s for s in (d.get("songs") or []) if s["id"] not in span_ids]
    html = _fill(html, "topic-song-grid", "".join(_ssr_song_row(s) for s in rows))

    sibs = d.get("siblings") or []
    if sibs:
        theme_label = (topic.get("theme") or {}).get("label")
        if theme_label:
            html = _set_text(html, "topic-siblings-h",
                             f"Other topics under {theme_label}")
        html = _fill(html, "topic-sibling-list", "".join(
            f'<li><a href="/topics/{_u(s["slug"])}" class="topic-sibling">'
            f'<span class="topic-sibling-name">{_t(s["label"])}</span>'
            f'<span class="topic-sibling-meta">{s["songs"]} songs, averaging '
            f'{_signed(s.get("avg_charge"))}</span></a></li>' for s in sibs))
        html = _show(html, "topic-siblings")

    return html


def _ssr_theme_body(html: str, d: dict) -> str:
    """Server-render the theme page. Mirrors `init()` in
    frontend/themes/themes.js. A theme carries no song list of its own -- its
    outbound links are its topics -- so those are what matter here."""
    st, lib = d["stats"], d["library"]
    label = d["theme"]["label"]
    topics = d.get("topics") or []

    html = _set_text(html, "theme-title", label)
    html = _fill(html, "crumb-theme", _t(label))

    n = len(topics)
    finding = ("One topic sits under this theme, carried by "
               if n == 1 else f"{n} topics sit under this theme, carried by ")
    finding += f'{st.get("songs", 0):,} calibrated songs'
    finding += f'. They average {_signed(st.get("avg_charge"))}'
    finding += _delta_clause(st.get("avg_charge"), lib.get("avg_charge"),
                             ", {n} points {dir} the library as a whole")
    finding += (f', and run from {_signed(st.get("min_charge"))} to '
                f'{_signed(st.get("max_charge"))}.')
    html = _set_text(html, "theme-finding", finding)
    html = _fill(html, "theme-stats", _ssr_stats_list(st, lib))

    html = _fill(html, "theme-topic-grid",
                 "".join(_ssr_topic_card(t) for t in topics))

    also = d.get("also_topics") or []
    if also:
        html = _fill(html, "theme-also-grid",
                     "".join(_ssr_topic_card(t) for t in also))
        html = _show(html, "theme-also")

    ex = d.get("extremes") or {}
    high, low = ex.get("highest"), ex.get("lowest")
    ends = [s for s in (high, low) if s]
    if low and high and low["id"] == high["id"]:
        ends = [high]
    if ends:
        html = _fill(html, "theme-extremes-grid",
                     "".join(_ssr_song_card(s) for s in ends))
        html = _show(html, "theme-extremes")

    rel = d.get("related_themes") or []
    if rel:
        html = _fill(html, "theme-related-list", "".join(
            f'<li><a href="/themes/{_u(s["slug"])}" class="topic-sibling">'
            f'<span class="topic-sibling-name">{_t(s["label"])}</span>'
            f'<span class="topic-sibling-meta">'
            + ("1 topic crosses between them" if s["shared_topics"] == 1
               else f'{s["shared_topics"]} topics cross between them')
            + '</span></a></li>' for s in rel))
        html = _show(html, "theme-related")

    return html


def _ssr_topics_index_body(html: str, data: dict) -> str:
    """The flat ranking of all 32 topics. This page and its sibling are the
    crawl entry to every detail page in the family, so shipping them empty put
    all 41 behind a render pass. Mirrors frontend/topics/topics-index.js."""
    rows = []
    for theme in data.get("themes") or []:
        for t in theme.get("topics") or []:
            rows.append(dict(t, theme=theme))
    rows.sort(key=lambda t: (-t["songs"], t["label"]))

    n_songs = (data.get("library") or {}).get("songs")
    if n_songs:
        html = _set_text(html, "index-intro",
                         f"All {len(rows)} topics the compass tracks, ranked by how many "
                         f"of {n_songs} calibrated songs carry each one.")

    body = "".join(
        '<li class="topic-rank-row">'
        f'<a class="topic-rank-link" href="/topics/{_u(t["slug"])}">'
        f'<span class="topic-rank-charge" style="color:{_charge_hex(t.get("avg_charge"))}">'
        f'{_signed(t.get("avg_charge"))}</span>'
        '<span class="topic-rank-id">'
        f'<span class="topic-rank-name">{_t(t["label"])}</span>'
        f'<span class="topic-rank-theme">{_t(t["theme"]["label"])}</span>'
        '</span>'
        f'<span class="topic-rank-songs">{t["songs"]} songs</span>'
        '</a></li>' for t in rows)
    return _fill(html, "topic-list",
                 '<section class="song-section song-section--full">'
                 f'<ul class="topic-rank" id="topic-rank">{body}</ul></section>')


def _ssr_themes_index_body(html: str, data: dict) -> str:
    """Every theme with its topics under it. Mirrors
    frontend/themes/themes-index.js."""
    themes = [t for t in (data.get("themes") or []) if t.get("topics")]
    n_topics = sum(len(t["topics"]) for t in themes)
    n_songs = (data.get("library") or {}).get("songs")
    if n_songs:
        html = _set_text(html, "index-intro",
                         f"The compass sorts what songs are about into {len(data['themes'])} "
                         f"themes, holding {n_topics} topics across {n_songs:,} calibrated "
                         f"songs. Each theme reads its own way.")

    body = "".join(
        '<section class="song-section song-section--full song-break">'
        f'<h2><a class="topic-theme-link" href="/themes/{_u(theme["slug"])}">'
        f'{_t(theme["label"])}</a></h2>'
        f'<p class="topic-theme-count">{len(theme["topics"])} topics, '
        f'{theme["songs"]} song credits.</p>'
        '<ul class="topic-index-grid">'
        + "".join(_ssr_topic_card(t) for t in theme["topics"])
        + '</ul></section>' for theme in themes)
    return _fill(html, "theme-list", body)


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


# --- topic pages -----------------------------------------------------------
# The whole point of a topic page is a finding a crawler can lift, so the
# finding goes in the meta description and the FAQ answer, not just in the DOM
# after JS runs. Both routes are single dotless segments, so /topics/topics.js
# and /topics/topics.css keep being served as static assets.

@router.get("/themes/", response_class=HTMLResponse)
@router.get("/themes", response_class=HTMLResponse)
def ssr_themes_index():
    tpl = _load_template("themes/index.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    from app.services import topic_pages
    with SessionLocal() as db:
        data = topic_pages.index(db)

    n_topics = sum(len(t["topics"]) for t in data["themes"])
    n_songs = data["library"]["songs"]
    question = "What are songs about?"
    answer = (
        f"The Rising Compass sorts what songs are about into {len(data['themes'])} themes "
        f"holding {n_topics} topics, read across {n_songs} calibrated songs against the "
        f"same lyric rubric."
    )
    canonical = f"{_SITE}/themes/"
    html = _inject(
        tpl,
        title="Song Themes | The Rising Compass",
        description=_clamp(answer),
        canonical=canonical,
        json_ld=_faq_ld(question, answer, canonical),
    )
    return HTMLResponse(_body_or_plain(_ssr_themes_index_body, html, data))


@router.get("/topics/{slug}", response_class=HTMLResponse)
def ssr_topic(slug: str):
    tpl = _load_template("topics/topic.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    from app.services import topic_pages
    with SessionLocal() as db:
        data = topic_pages.detail(db, slug)
    if data is None:
        # A slug the taxonomy does not know is a real 404, not an empty subject.
        return HTMLResponse(tpl, status_code=404)

    label = data["topic"]["label"]
    st = data["stats"]
    lib = data["library"]
    question = f"What do songs about {label} read?"

    # The answer is the finding itself, stated in the order the page states it.
    parts = [
        f"{st['songs']} calibrated songs carry {label}, averaging "
        f"{'+' if (st['avg_charge'] or 0) > 0 else ''}{st['avg_charge']}"
    ]
    if st["avg_charge"] is not None and lib["avg_charge"] is not None:
        delta = st["avg_charge"] - lib["avg_charge"]
        if abs(delta) >= 3:
            parts.append(f"{abs(delta)} points {'above' if delta > 0 else 'below'} the library")
    if st.get("min_charge") is not None:
        parts.append(
            f"running from {'+' if st['min_charge'] > 0 else ''}{st['min_charge']} to "
            f"{'+' if st['max_charge'] > 0 else ''}{st['max_charge']}"
        )
    answer = ", ".join(parts) + "."

    canonical = f"{_SITE}/topics/{slug}"
    # Topics / this topic. The crumb mirrors the URL, and the URL is a sibling
    # collection: a topic does not sit inside its theme's path, so the crumb
    # must not claim it does. The theme is surfaced on the page instead.
    crumbs = [("Topics", f"{_SITE}/topics/"), (label, canonical)]
    html = _inject(
        tpl,
        # The title states the topic; the FAQ question and the description
        # carry the finding. Keeping them distinct means the SERP line and the
        # answer-engine payload are not the same sentence twice.
        title=f"Songs about {label} | The Rising Compass",
        description=_clamp(answer),
        canonical=canonical,
        json_ld=[_faq_ld(question, answer, canonical), _breadcrumb_ld(crumbs)],
    )
    return HTMLResponse(_body_or_plain(_ssr_topic_body, html, data))


@router.get("/topics/", response_class=HTMLResponse)
@router.get("/topics", response_class=HTMLResponse)
def ssr_topics_index():
    """The topic collection's own front door.

    Themes are the parent of topics in the TAXONOMY, but nesting that in the
    path would make a topic URL read /themes/meaning-mortality/grief, so the two
    live as sibling collections and each keeps its own index. The parent theme
    travels as a label on the row and as a link on the topic page, which is
    where the relation belongs.
    """
    tpl = _load_template("topics/index.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    from app.services import topic_pages
    with SessionLocal() as db:
        data = topic_pages.index(db)

    n_topics = sum(len(t["topics"]) for t in data["themes"])
    n_songs = data["library"]["songs"]
    question = "What topics does The Rising Compass track?"
    answer = (
        f"All {n_topics} topics the compass reads for in lyrics, ranked by how many of "
        f"{n_songs} calibrated songs carry each one."
    )
    canonical = f"{_SITE}/topics/"
    html = _inject(
        tpl,
        title="Song Topics | The Rising Compass",
        description=_clamp(answer),
        canonical=canonical,
        json_ld=[
            _faq_ld(question, answer, canonical),
            _breadcrumb_ld([("Topics", canonical)]),
        ],
    )
    return HTMLResponse(_body_or_plain(_ssr_topics_index_body, html, data))


@router.get("/themes/{slug}", response_class=HTMLResponse)
def ssr_theme(slug: str):
    tpl = _load_template("themes/theme.html")
    if tpl is None:
        return HTMLResponse("Not found", status_code=404)

    from app.services import topic_pages
    with SessionLocal() as db:
        data = topic_pages.theme_detail(db, slug)
    if data is None:
        return HTMLResponse(tpl, status_code=404)

    label = data["theme"]["label"]
    st = data["stats"]
    question = f"What do songs about {label} read?"
    answer = (
        f"{len(data['topics'])} topics sit under {label}, carried by {st['songs']} "
        f"calibrated songs averaging "
        f"{'+' if (st['avg_charge'] or 0) > 0 else ''}{st['avg_charge']}"
    )
    if st.get("min_charge") is not None:
        answer += (
            f", running from {'+' if st['min_charge'] > 0 else ''}{st['min_charge']} to "
            f"{'+' if st['max_charge'] > 0 else ''}{st['max_charge']}"
        )
    answer += "."

    canonical = f"{_SITE}/themes/{slug}"
    # Two levels, because a theme IS the second tier.
    crumbs = [
        ("Themes", f"{_SITE}/themes/"),
        (label, canonical),
    ]
    html = _inject(
        tpl,
        title=f"Songs about {label} | The Rising Compass",
        description=_clamp(answer),
        canonical=canonical,
        json_ld=[_faq_ld(question, answer, canonical), _breadcrumb_ld(crumbs)],
    )
    return HTMLResponse(_body_or_plain(_ssr_theme_body, html, data))


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
