"""MusicBrainz API client — artist search and release metadata.

Free API, no key required. Rate limit: 1 request/second.
User-Agent required per MB terms of service.
"""

import asyncio
import logging
import re
from datetime import date
from typing import Optional

import httpx

from app.services.song_identity import extract_primary_artist

logger = logging.getLogger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "RisingCompass/1.0 (https://risingcompass.net)"

# Cover-art resolution settings (see search_recording_release_group).
# MB read timeouts are frequent and transient, and a timeout that reads as "no
# match" gets RECORDED as one, so be generous rather than fast.
MB_TIMEOUT = 30.0
# Per song, how many candidate recordings may cost a second lookup call. Famous
# songs carry a long tail of per-compilation recording entries ahead of the
# studio one, so this has to be wide enough to reach past them.
MAX_RECORDING_LOOKUPS = 8
# ...and spend the whole budget rather than stopping at the first few that clear
# the filters. Clearing them is NOT evidence of being canonical: MB tags many
# reissues and archival sets with no secondary-type at all, and their titles dodge
# HITS_COMPILATION_RE, so an early stop banks whatever junk sorted first and never
# looks up the studio album at all. This was 3, and that truncation -- not the
# artist-credit check, which held -- is what put a 1990 single's art on a 1977
# album track and a 2022 archival set's art on a 1972 one. Scanning the full
# budget costs lookups only on songs that HAVE many candidates, which are exactly
# the catalogue songs that were failing.
ENOUGH_CANDIDATES = MAX_RECORDING_LOOKUPS
# Attempts for a catalogue-page fetch. Higher than the cover-art default: a
# dropped page here silently shortens an artist's whole catalogue, whereas a
# dropped cover-art lookup only costs one thumbnail.
MB_PAGE_ATTEMPTS = 5


class MusicBrainzUnavailable(Exception):
    """MusicBrainz could not be reached after retries.

    Deliberately NOT the same signal as an empty result. A partial catalogue
    written as though it were complete is indistinguishable from an artist who
    genuinely has fewer releases, so every truncation must surface as an error
    rather than a short list. (Real incident 2026-08-13: one 503 on page 2 of
    the Beatles' 1,017 release-groups silently reduced the catalogue to 18.)
    """

# Release-group secondary types we never want in an artist's Releases.
# MB uses lowercase "demo", "mixtape/street", "audio drama", "audiobook",
# "interview", "spokenword", "field recording" — all noise for a calibration
# site. NOTE: "soundtrack" is deliberately NOT here. A studio album that
# doubled as a film soundtrack (the Beatles' "A Hard Day's Night" / "Help!")
# carries only a "soundtrack" secondary tag and is a genuine first-appearance
# release that must survive. A soundtrack that's ALSO a compilation/live/etc.
# still gets dropped by the matching tag below; and a various-artists soundtrack
# comp is filed under "Various Artists", not under this artist, so it never
# reaches get_artist_releases in the first place.
SKIP_SECONDARY_TYPES = {
    "compilation", "live", "remix", "dj-mix",
    "demo", "mixtape/street", "mixtape", "field recording",
    "audio drama", "audiobook", "interview", "spokenword",
}

# Title substrings that mark bootlegs, outtakes, karaoke, etc.
# MB doesn't always tag these via secondary-type.
SKIP_TITLE_SUBSTRINGS = (
    "karaoke", "soundboard", "unauthorized", "bootleg",
    "alternate version", "alternate take", "alternate album",
    "tribute to", "in the style of",
)

# Outtake pattern: "(take 1)", "(take 17)", "take 3)" at end of title, etc.
OUTTAKE_RE = re.compile(r"\(take\s+\d+\)|take\s+\d+\)\s*$", re.IGNORECASE)

# Alternate-RECORDING markers, matched against a recording's `disambiguation`
# (MB's per-recording note: "live, 1969-01-30", "demo", "instrumental"). These
# are different performances of the same song, and they hang off different --
# usually art-less -- releases, so cover-art resolution skips past them to the
# studio recording. Song-level only; the release-group filters above are the
# album-level equivalent.
ALT_RECORDING_RE = re.compile(
    r"\b(live|demo|instrumental|karaoke|acoustic|remix|rehearsal|"
    r"radio edit|edit|mix|session|rework|a cappella|acapella)\b",
    re.IGNORECASE,
)

# A parenthesised feature credit inside a TITLE ("Shiesty (feat. Pooh Shiesty)").
# MusicBrainz files the recording under the bare title and carries the feature in
# the artist credit instead, so this has to come off before the title comparison.
FEAT_IN_TITLE_RE = re.compile(
    r"\s*[\(\[]\s*(?:feat|ft|featuring|w/|with)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)

# Derivative-edition markers. A Rising Compass release is the FIRST official
# issue of a tracklisting; a remaster / deluxe / anniversary / reissue re-issues
# an existing (or padded) tracklisting under an edition name, so the original
# already covers every song's first appearance. These re-releases are dropped.
DERIVATIVE_EDITION_RE = re.compile(
    r"\bremaster(ed)?\b|\bdeluxe\b|\banniversary\b|\bexpanded\b"
    r"|\breissue\b|\bre-?issue\b|\brepack(age|aged)?\b|\breprint\b"
    r"|\bre-?record(ed|ing)?s?\b|\bacoustic\b|\bcollector'?s?\b"
    r"|\bspecial\s+edition\b|\blimited\s+edition\b|\bbonus\s+track"
    r"|\(mono\)|\(stereo\)|\bin\s+mono\b|\bin\s+stereo\b",
    re.IGNORECASE,
)

# Greatest-hits / compilation title markers, for comps MusicBrainz failed to
# tag with a "compilation" secondary type (e.g. the Beatles' "The Beatles'
# Hits"). Title-based, so it carries some false-positive risk on a legitimately
# named release — flagged here so a bad drop is easy to trace back.
HITS_COMPILATION_RE = re.compile(
    r"\bgreatest\s+hits\b|\bbest\s+of\b|\bvery\s+best\b|\bhits\b"
    r"|\banthology\b|\bcollection\b|\bfavou?rites\b|\bessential\b",
    re.IGNORECASE,
)

# Simple rate limiter — track last request time
_last_request_time: float = 0


async def _rate_limit():
    """Enforce 1 request/second rate limit."""
    global _last_request_time
    import time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    _last_request_time = time.monotonic()


async def search_artist(name: str, limit: int = 5) -> list[dict]:
    """Search MusicBrainz for artists by name.

    Returns list of dicts with: mbid, name, sort_name, disambiguation, score.

    Retried like the catalogue fetches. This is the FIRST call of every resolve,
    so a single-shot 503 here aborts the entire rebuild before it starts -- it
    did exactly that twice on 2026-08-13 while the rest of the path was already
    hardened. Empty list still means "no such artist"; the caller treats that as
    nothing-to-resolve.
    """
    data = await _mb_get(
        "/artist",
        {"query": name, "fmt": "json", "limit": limit},
        attempts=MB_PAGE_ATTEMPTS,
    )
    if data is None:
        logger.warning("MusicBrainz artist search failed for '%s' after retries", name)
        return []

    return [
        {
            "mbid": a["id"],
            "name": a["name"],
            "sort_name": a.get("sort-name", ""),
            "disambiguation": a.get("disambiguation", ""),
            "score": a.get("score", 0),
        }
        for a in data.get("artists", [])
    ]


async def search_release_group(artist: str, title: str, limit: int = 8) -> list[dict]:
    """Search release-groups matching a title + artist, best score first.

    Used by the Album Charger to attach a release-group MBID to a user-charged
    album (which is what unlocks Cover Art Archive art for it). Returns dicts:
      mbid, title, primary_type, first_release_date, artist_credit, score.
    """
    await _rate_limit()
    query = f'releasegroup:"{title}" AND artist:"{artist}"'
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/release-group",
                params={"query": query, "fmt": "json", "limit": limit},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for rg in data.get("release-groups", []):
            credit_parts = []
            for c in (rg.get("artist-credit") or []):
                if isinstance(c, dict):
                    credit_parts.append((c.get("name") or "") + (c.get("joinphrase") or ""))
            results.append({
                "mbid": rg["id"],
                "title": rg.get("title", ""),
                "primary_type": (rg.get("primary-type") or "").lower(),
                "first_release_date": rg.get("first-release-date") or "",
                "artist_credit": "".join(credit_parts).strip(),
                "score": int(rg.get("score", 0) or 0),
            })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    except Exception:
        logger.exception("MusicBrainz release-group search failed: '%s' / '%s'", artist, title)
        return []


async def search_recording_release_group(
    artist: str, title: str, limit: int = 25,
    exclude_mbids: Optional[set[str]] = None,
) -> Optional[dict]:
    """Resolve a SONG (not an album) to the release-group its art should come from.

    Songs born on a chart or in the Lyrical Charger have no Release row, so they
    never inherit release-level cover art. This searches MB's RECORDING index --
    the song-level entity -- and returns the release-group of its best release,
    which is exactly the key mb_cover_art / Cover Art Archive use.

    Returns {mbid, release_group_title, primary_type, score, recording_title,
    artist_credit} for a CONFIDENT match, or None. Confidence is deliberately
    strict: a wrong MBID silently attaches the wrong album cover to a song page,
    which is worse than showing no art at all.

    SEARCH THEN LOOK UP, deliberately. The recording SEARCH endpoint returns an
    abbreviated release-group per hit -- no secondary-types, no
    first-release-date, no artist-credit -- which cannot tell an album from a
    various-artists medley. So the search only identifies candidate recordings,
    and each promising one is LOOKED UP (inc=releases+release-groups+
    artist-credits) to rank on real data.

    Every call is throttled to MB's 1 req/sec, and a song costs up to 2 searches
    plus MAX_RECORDING_LOOKUPS lookups, so budget a few seconds each and keep
    this OFFLINE (the backfill script) -- never the request path.

    `exclude_mbids` are release groups a HUMAN has already rejected for this song
    (confirmed `song_cover_art_reports`). They are dropped after the lookup rather
    than before, because a rejection is about a specific picture, not about the
    recording that led to it: the scan must keep going and offer the next-best
    group. Without this, a --recheck-misses pass would walk straight back to the
    cover a reader already said was wrong.

    KNOWN LIMIT: heavily-bootlegged catalogue artists can bury the studio
    recording past the lookup budget and come back as a miss (The Beatles is the
    reliable example). That is the intended failure direction -- no art beats
    wrong art -- and the backfill's --recheck-misses retries them later.
    """
    # The two query forms have complementary blind spots, so try the narrow one
    # and fall back. status:official skips the wall of live bootlegs that buries
    # a famous song's studio recording ("Smells Like Teen Spirit" returns seven
    # bootleg gigs before anything official), but MB's relevance reshuffles under
    # it and some album tracks that the plain query reaches ("Dreams" -> Rumours)
    # drop out. The second search only costs a call when the first found nothing.
    # Search on the PRIMARY artist and the bare title. A feeder credit like
    # "Young Money ft. Lloyd" is not an artist name MusicBrainz can match, and the
    # credit check downstream compared "youngmoneyftlloyd" against MB's
    # "youngmoneyfeatlloyd" -- an ft./feat. spelling difference that failed EVERY
    # featured-artist song (33 of 64 misses in the first 200-song batch, against
    # zero matches carrying a feature). extract_primary_artist is the same helper
    # disposition.detect_release_state uses for its Apple lookup.
    primary = extract_primary_artist(artist) or (artist or "")
    bare_title = _strip_feature(title)
    base = f'recording:"{bare_title}" AND artist:"{primary}"'
    for query in (f"{base} AND status:official", base):
        hit = await _resolve_from_query(query, bare_title, primary, limit, exclude_mbids)
        if hit:
            return hit
    return None


def _strip_feature(title: str) -> str:
    """Drop a parenthesised feature credit from a song title."""
    return FEAT_IN_TITLE_RE.sub("", title or "").strip()


async def _resolve_from_query(
    query: str, title: str, artist: str, limit: int,
    exclude_mbids: Optional[set[str]] = None,
) -> Optional[dict]:
    """Scan one recording-search query's hits for a usable release-group."""
    excluded = exclude_mbids or set()
    data = await _mb_get("/recording", {"query": query, "fmt": "json", "limit": limit})
    if data is None:
        logger.info("MusicBrainz recording search failed: '%s' / '%s'", artist, title)
        return None

    want_title = _normalize_match(title)
    want_artist = _normalize_match(artist)
    lookups = 0
    found: list[dict] = []

    for rec in data.get("recordings", []):
        # MusicBrainz files a SEPARATE recording entry for each compilation a
        # song appears on, and they all score 100, so the canonical studio
        # recording is rarely the first hit -- the top hits for "Come Together"
        # are bootlegs and medleys. That means scanning several, then choosing
        # across them, rather than taking the first that yields anything. Each
        # candidate costs a throttled lookup, so the scan is bounded both ways.
        if lookups >= MAX_RECORDING_LOOKUPS or len(found) >= ENOUGH_CANDIDATES:
            break
        score = int(rec.get("score", 0) or 0)
        # MB scores are 0-100. Below 90 the title/artist match is loose enough
        # that we'd be guessing at which song this even is.
        if score < 90:
            continue
        # Strip a feature off MB's title too, so the comparison is bare-vs-bare
        # whichever side happens to carry the credit.
        if _normalize_match(_strip_feature(rec.get("title"))) != want_title:
            continue
        # A recording's disambiguation is where MB files "live, 1969", "demo",
        # "instrumental". Those are different performances of the song and carry
        # different (often art-less) releases, so skip to the studio recording.
        if ALT_RECORDING_RE.search(rec.get("disambiguation") or ""):
            continue

        credit_parts = []
        for c in (rec.get("artist-credit") or []):
            if isinstance(c, dict):
                credit_parts.append((c.get("name") or "") + (c.get("joinphrase") or ""))
        credit = "".join(credit_parts).strip()
        # The credited string may carry features the stored artist doesn't
        # ("X feat. Y"), so require containment rather than equality.
        if want_artist and want_artist not in _normalize_match(credit):
            continue

        # FREE pre-filter, before spending a lookup. Search hits carry an
        # abbreviated release with its status, and the long tail of junk ahead of
        # a famous song's studio recording is mostly Bootleg. Dropping those here
        # is what lets the bounded lookup budget actually reach the real one.
        hit_releases = rec.get("releases") or []
        if hit_releases and not any(
            (r.get("status") or "").lower() == "official" for r in hit_releases
        ):
            continue

        lookups += 1
        releases = await _recording_releases(rec["id"])
        best = _pick_release_group(releases, want_artist, excluded)
        if best:
            best["score"] = score
            best["recording_title"] = rec.get("title", "")
            best["artist_credit"] = credit
            found.append(best)

    if not found:
        return None
    # Earliest wins, for the same reason it does within a single recording: the
    # first official issue is the cover the song is known by. Undated groups sort
    # last so any dated candidate beats them.
    found.sort(key=lambda r: (r.get("first_release_date") or "9999",
                              {"album": 0, "single": 1, "ep": 2}.get(r["primary_type"], 3)))
    return found[0]


async def _recording_releases(recording_mbid: str) -> list[dict]:
    """Every release of a recording, with FULL release-group data attached.

    The search endpoint's abbreviated release-groups omit exactly the two fields
    the ranking needs (secondary-types and first-release-date), so this second
    lookup is what makes the pick trustworthy. Returns [] on any failure -- a
    missing cover is always preferable to a wrong one.
    """
    data = await _mb_get(
        f"/recording/{recording_mbid}",
        {"inc": "releases+release-groups+artist-credits", "fmt": "json"},
    )
    if data is None:
        logger.info("MusicBrainz recording lookup failed for %s", recording_mbid)
        return []
    return data.get("releases") or []


async def _mb_get(path: str, params: dict, attempts: int = 2) -> Optional[dict]:
    """Throttled GET against MusicBrainz with retries, returning parsed JSON.

    MusicBrainz read timeouts and 503s are common and transient -- a plain 15s
    single-shot turned catalogue staples like "Come Together" into false
    no-matches, and a recorded miss is sticky (the backfill won't re-search it).
    A retry after a short pause converts almost all of them. Returns None when
    every attempt fails; callers decide whether that means "no data" (cover art)
    or an outright error (catalogue pages -- see MusicBrainzUnavailable).

    Backoff doubles from 2s so a rate-limited stretch is waited out rather than
    hammered, since hammering is what earns the 503 in the first place.
    """
    last_exc = None
    for attempt in range(attempts):
        await _rate_limit()
        try:
            async with httpx.AsyncClient(timeout=MB_TIMEOUT) as client:
                resp = await client.get(
                    f"{BASE_URL}{path}", params=params,
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                await asyncio.sleep(2.0 * (2 ** attempt))
    logger.info("MusicBrainz GET %s failed %d times: %s", path, attempts, last_exc)
    return None


def _normalize_match(s: Optional[str]) -> str:
    """Lowercase + strip punctuation/spacing for title/artist comparison.

    Deliberately blunt: MB's punctuation for apostrophes and dashes differs from
    the feeders', and a curly-vs-straight apostrophe should not lose a match.
    """
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _pick_release_group(
    releases: list[dict], want_artist: str = "",
    exclude: Optional[set[str]] = None,
) -> Optional[dict]:
    """Choose which of a recording's releases supplies the cover art.

    Ranked EARLIEST-FIRST, because the first place a song appeared is the cover a
    listener associates with it, and it is the one choice that behaves correctly
    at both ends of the catalogue: a 2026 chart single has only its own single
    art, while a 1969 album track resolves to the album rather than to whatever
    compilation reissued it decades later. Primary type only breaks ties.

    Reuses the module's existing compilation / derivative-edition / bootleg
    filters, which now actually fire because the caller supplies full
    release-group data (the search endpoint omits secondary-types entirely).
    Unofficial releases are dropped outright -- bootlegs are where the wrong-cover
    failures come from.
    """
    ranked = []
    for rel in releases:
        rg = rel.get("release-group") or {}
        mbid = rg.get("id")
        if not mbid:
            continue
        # A reader already looked at this cover on this song's page and said it
        # was wrong. Dropped here rather than after the pick, so the next-best
        # group from the SAME recording still gets its chance.
        if mbid in (exclude or ()):
            continue
        # "Official" is MB's own marker; Bootleg/Promotion/Pseudo-Release are not
        # what a song's cover should come from.
        if (rel.get("status") or "").lower() != "official":
            continue

        # The release must actually be CREDITED to this artist. Title and type
        # filters are heuristics that lose a whack-a-mole against oddly-named
        # compilations ("Top Medleys" is an Official Album with no compilation
        # tag), but those are credited to Various Artists, so the credit check
        # settles it structurally. This is the guard that keeps a wrong cover off
        # a song page; everything below it is only about picking the BEST of
        # several legitimate covers.
        if want_artist:
            credit = "".join(
                (c.get("name") or "") for c in (rel.get("artist-credit") or [])
                if isinstance(c, dict)
            )
            if want_artist not in _normalize_match(credit):
                continue

        title = rg.get("title") or rel.get("title") or ""
        low = title.lower()
        if any(s in low for s in SKIP_TITLE_SUBSTRINGS) or OUTTAKE_RE.search(title):
            continue
        if DERIVATIVE_EDITION_RE.search(title) or HITS_COMPILATION_RE.search(title):
            continue
        secondary = {(s or "").lower() for s in (rg.get("secondary-types") or [])}
        if secondary & SKIP_SECONDARY_TYPES:
            continue

        primary = (rg.get("primary-type") or "").lower()
        rank = {"album": 0, "single": 1, "ep": 2}.get(primary, 3)
        # Undated groups sort last so anything dated wins; MB dates are ISO-ish
        # ("1969", "1969-09", "1969-09-26") and compare correctly as strings
        # since a shorter prefix sorts before its own longer forms.
        rel_date = rg.get("first-release-date") or rel.get("date") or ""
        ranked.append(((rel_date or "9999"), rank, mbid, title, primary))

    if not ranked:
        return None
    ranked.sort(key=lambda r: (r[0], r[1]))
    rel_date, _, mbid, title, primary = ranked[0]
    return {
        "mbid": mbid,
        "release_group_title": title,
        "primary_type": primary,
        # Kept so the caller can rank ACROSS recordings, not just within one.
        "first_release_date": "" if rel_date == "9999" else rel_date,
    }


async def get_artist_releases(
    mbid: str,
    release_types: Optional[list[str]] = None,
) -> list[dict]:
    """Get all releases for an artist by MusicBrainz ID.

    Fetches release-groups (albums, singles, EPs) with dates and types.
    Paginates through all results (100 per page).

    Returns list of dicts with: mbid, title, release_type, release_date,
    release_year, primary_type, secondary_types.

    Raises MusicBrainzUnavailable if any page fails after its retries. An empty
    list means the artist genuinely has no qualifying releases; a short list is
    never returned for an artist who has more.
    """
    if release_types is None:
        release_types = ["album", "single", "ep"]

    all_releases = []
    offset = 0
    limit = 100

    while True:
        data = await _mb_get(
            "/release-group",
            {"artist": mbid, "fmt": "json", "limit": limit, "offset": offset},
            attempts=MB_PAGE_ATTEMPTS,
        )
        # A dead page must NEVER degrade into a short catalogue. Returning what
        # we had so far is indistinguishable from a complete result, and callers
        # write it as authoritative -- which is how one 503 deleted 87 of the
        # Beatles' 105 releases. Fail loudly and let the caller abort instead.
        if data is None:
            raise MusicBrainzUnavailable(
                f"release-group page at offset {offset} failed for artist {mbid} "
                f"after {MB_PAGE_ATTEMPTS} attempts"
            )

        groups = data.get("release-groups", [])
        if not groups:
            break

        for rg in groups:
            primary = (rg.get("primary-type") or "").lower()
            secondary = [s.lower() for s in (rg.get("secondary-types") or [])]
            title = rg.get("title") or ""

            # Secondary-type exclusion — covers MB-tagged noise.
            if any(s in SKIP_SECONDARY_TYPES for s in secondary):
                continue

            # Title-pattern exclusion — covers MB-untagged noise
            # (bootlegs, outtakes, karaoke, tribute albums).
            title_lower = title.lower()
            if any(p in title_lower for p in SKIP_TITLE_SUBSTRINGS):
                continue
            if OUTTAKE_RE.search(title_lower):
                continue
            # Derivative-edition exclusion (remaster / deluxe / anniversary /
            # reissue / acoustic / mono-stereo redux) — re-releases of an
            # existing tracklisting, never a first appearance.
            if DERIVATIVE_EDITION_RE.search(title):
                continue
            # Greatest-hits / comp exclusion for groups MB never tagged
            # "compilation" (e.g. "The Beatles' Hits").
            if HITS_COMPILATION_RE.search(title):
                continue

            # Map primary type to our release_type. Unknown primaries
            # (broadcast, other, etc.) are rejected rather than silently
            # bucketed as "single".
            release_type = _map_release_type(primary)
            if release_type is None or release_type not in release_types:
                continue

            release_date_str = rg.get("first-release-date", "")
            release_date, release_year = _parse_mb_date(release_date_str)

            all_releases.append({
                "mbid": rg["id"],
                "title": rg["title"],
                "release_type": release_type,
                "release_date": release_date,
                "release_year": release_year,
                "primary_type": primary,
                "secondary_types": secondary,
            })

        # Check if there are more pages
        total = data.get("release-group-count", 0)
        offset += limit
        if offset >= total:
            break

    # Sort by date
    all_releases.sort(key=lambda r: r["release_date"] or date.min)
    return all_releases


async def get_release_tracks(release_group_mbid: str) -> dict:
    """Fetch the first OFFICIAL release in a group and its track listing.

    Returns {"has_official": bool, "tracks": [{position, title, length_ms}, ...]}.

    `has_official` is the commercial-release gate (Filter v2). A release-group
    with zero `status=official` releases is a bootleg / broadcast / unofficial
    group -- MB leaves these untagged by secondary-type, so the title and
    secondary-type filters in get_artist_releases can't catch them. Callers
    drop the whole release when has_official is False.

    On a fetch error we fail OPEN (has_official=True, tracks=[]) so a transient
    MB hiccup never deletes a real commercial release -- the resolve is
    idempotent and re-runnable, whereas a clean empty official-releases list is
    the genuine bootleg signal.
    """
    # First, get the OFFICIAL releases in this release group. An empty list
    # means the group has no commercial release -> drop it. A FAILED list is a
    # different thing entirely, so retry before concluding anything: without
    # retries a rate-limited stretch strips the tracklist off every release in
    # the catalogue, and a release with no tracks links no songs.
    data = await _mb_get(
        "/release",
        {
            "release-group": release_group_mbid,
            "fmt": "json",
            "limit": 1,
            "status": "official",
        },
        attempts=MB_PAGE_ATTEMPTS,
    )
    if data is None:
        logger.warning(
            "MusicBrainz official-release lookup failed for release-group %s; "
            "failing open", release_group_mbid,
        )
        return {"has_official": True, "tracks": []}

    releases = data.get("releases", [])
    if not releases:
        return {"has_official": False, "tracks": []}

    release_mbid = releases[0]["id"]

    # Now get tracks for this release.
    data = await _mb_get(
        f"/release/{release_mbid}",
        {"inc": "recordings", "fmt": "json"},
        attempts=MB_PAGE_ATTEMPTS,
    )
    if data is None:
        logger.warning(
            "MusicBrainz tracklist fetch failed for release %s; failing open",
            release_mbid,
        )
        return {"has_official": True, "tracks": []}

    tracks = []
    for medium in data.get("media", []):
        for t in medium.get("tracks", []):
            tracks.append({
                "position": t.get("position", 0),
                "title": t.get("title", ""),
                "length_ms": t.get("length"),
            })

    return {"has_official": True, "tracks": tracks}


def _map_release_type(primary_type: str) -> Optional[str]:
    """Map MusicBrainz primary-type to our release_type enum.

    Returns None for unknown primaries — callers skip rather than bucket
    broadcasts, DJ mixes, "other", etc. as fake singles.
    """
    mapping = {
        "album": "album",
        "single": "single",
        "ep": "ep",
    }
    return mapping.get(primary_type)


def _parse_mb_date(date_str: str) -> tuple[Optional[date], Optional[int]]:
    """Parse a MusicBrainz date string (YYYY, YYYY-MM, or YYYY-MM-DD).

    Returns (date_object_or_None, year_int_or_None).
    """
    if not date_str:
        return None, None

    parts = date_str.split("-")
    year = None
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        return None, None

    if len(parts) == 3:
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2])), year
        except ValueError:
            return None, year
    return None, year
