"""Codified per-song draft disposition -- feeder-agnostic, NOT SOP prose.

Every feeder (spotify daily reading, youtube_trending_usa, shazam_top200_usa,
itunes_download_usa) builds a draft the same way, so the per-song disposition is
decided ONCE here, in the shared draft-build path, instead of being re-described
in each feeder's SOP. `resolve_draft_song_disposition` runs the ladder:

    identity (CACHE_LINK) -> release-state (PREORDER) -> non-song (DROP_NONSONG)
    -> NEEDS_LYRICS

- CACHE_LINK   already in the Library (identity resolution matched it). The
               cache lookup (calibrator.lookup_calibrated) owns this rung; the
               draft-build path passes is_cache_hit=True when it fired, so the
               resolver just confirms it.
- PREORDER     charting but UNRELEASED, so no lyrics exist yet. The NEW codified
               piece is AUTOMATIC release-state detection (detect_release_state):
               a confident future release date auto-marks the existing preorder
               disposition (column + endpoint + re-list already shipped), so a
               human no longer has to recognize and mark it.
- DROP_NONSONG not a song (film, trailer, skit). Phase 1 leaves this to the LEIT
               clutter audit queue (human-confirmed) -- the open decision is
               whether DROP is ever automatic; the resolver never auto-drops, it
               returns NEEDS_LYRICS and lets the clutter sweep flag it.
- NEEDS_LYRICS released, new, lyrics obtainable. The one genuinely human step.

FAIL-OPEN throughout: any uncertainty resolves to NEEDS_LYRICS, never silently
swallows a real song. Release-state detection is advisory + logged, so a wrong
auto-preorder is catchable.
"""

import logging
from datetime import date, datetime

import httpx

from app.services.song_search import normalize_for_search
from app.services.song_identity import extract_primary_artist

logger = logging.getLogger(__name__)

# Dispositions.
CACHE_LINK = "CACHE_LINK"
PREORDER = "PREORDER"
NEEDS_LYRICS = "NEEDS_LYRICS"
DROP_NONSONG = "DROP_NONSONG"

# Release states.
RELEASED = "released"
PRE = "preorder"
UNKNOWN = "unknown"

_ITUNES_SEARCH = "https://itunes.apple.com/search"
_UA = "RisingCompass/1.0 (https://risingcompass.net)"


def _parse_iso_date(s):
    """Parse an Apple ISO release date (e.g. '2026-06-20T12:00:00Z') to a date.
    Returns None on anything unparseable (fail-open)."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        # Some feeds return a bare 'YYYY-MM-DD'.
        try:
            return date.fromisoformat(s[:10])
        except (ValueError, TypeError):
            return None


def detect_release_state(title, artist, *, today=None, timeout=8.0):
    """Best-effort release-state via Apple's iTunes Search API (the same trusted
    Apple source the iTunes feeder already reads; no key, fast HTTP GET).

    Returns (state, detail) where state is RELEASED | PRE | UNKNOWN. FAIL-OPEN:
    a lookup error, no match, or a missing date all return UNKNOWN -- never PRE --
    so a real released song is never mistaken for a preorder placeholder. Only a
    matched track with a CONFIDENT future release date returns PRE.

    MusicBrainz / Musixmatch are the documented secondary sources (both carry
    release dates); UNKNOWN already fails open to NEEDS_LYRICS, so the iTunes-only
    Phase-1 detector never swallows a real song when Apple has no entry.
    """
    today = today or date.today()
    primary = extract_primary_artist(artist) or (artist or "")
    term = f"{primary} {title}".strip()
    if not normalize_for_search(term):
        return UNKNOWN, {"reason": "empty search term"}

    try:
        resp = httpx.get(
            _ITUNES_SEARCH,
            params={"term": term, "entity": "song", "limit": 10, "country": "US"},
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except Exception:
        logger.warning("release-state lookup failed for '%s' by %s", title, artist, exc_info=True)
        return UNKNOWN, {"reason": "lookup error"}

    nt = normalize_for_search(title)
    na = normalize_for_search(primary)
    best = None
    for r in results:
        if r.get("kind") and r.get("kind") != "song":
            continue
        if normalize_for_search(r.get("trackName")) != nt:
            continue
        # Artist must be present in the credited string (covers "feat." drift).
        if na and na not in normalize_for_search(r.get("artistName")):
            continue
        best = r
        break

    if not best:
        return UNKNOWN, {"reason": "no matching track"}

    rd = _parse_iso_date(best.get("releaseDate"))
    if rd is None:
        return UNKNOWN, {"reason": "no release date"}
    if rd > today:
        return PRE, {
            "release_date": rd.isoformat(),
            "track": best.get("trackName"),
            "artist": best.get("artistName"),
        }
    return RELEASED, {"release_date": rd.isoformat()}


def resolve_draft_song_disposition(
    title, artist, *, is_cache_hit, lyrics_available=False, detect_release=True, today=None,
):
    """Resolve one charted song to exactly one disposition. Feeder-agnostic --
    every draft-build path calls this so the four feeders behave identically.

    `is_cache_hit` -- the identity ladder already matched a calibrated Library row
        (the caller ran lookup_calibrated). True -> CACHE_LINK.
    `lyrics_available` -- lyrics are in hand at build time (rare for the crons).
    `detect_release` -- run the external release-state lookup (disable in tests /
        offline). When off, an un-cached song with no lyrics is NEEDS_LYRICS.

    Returns (disposition, detail). Pure decision + (optionally) one HTTP lookup;
    no DB, no writes.
    """
    if is_cache_hit:
        return CACHE_LINK, {}

    # Release-state rung: an unreleased charting single has no lyrics yet ->
    # auto-PREORDER. Only fires on a confident future date; fail-open otherwise.
    if not lyrics_available and detect_release:
        state, detail = detect_release_state(title, artist, today=today)
        if state == PRE:
            return PREORDER, detail

    # DROP_NONSONG is intentionally NOT automatic in Phase 1 (the LEIT clutter
    # audit queue confirms non-songs); the resolver never silently drops.
    if lyrics_available:
        # Lyrics in hand but not yet a cache hit -- the calibrate path will read
        # them; from the disposition ladder's view this is still NEEDS_LYRICS work.
        return NEEDS_LYRICS, {}
    return NEEDS_LYRICS, {}
