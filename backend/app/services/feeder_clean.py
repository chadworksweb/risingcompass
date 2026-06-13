"""Feeder-cruft cleaning for song identity resolution (Phase 1).

The social-discovery feeders (YouTube Trending, Shazam, iTunes) pull raw platform
titles + uploader/channel/label "artists": MV/lyric-video cruft in the title, a
VEVO or label channel in the artist field, K-pop "ARTIST 'TITLE' Official MV"
upload titles. The exact `canonical_key` (normalize_for_search of title +
primary artist) treats each formatting as a different song, so the same track
re-enters the Library as a duplicate row.

`clean_title_artist` runs a CLOSED, REVIEWED token list over (title, artist) so a
SECOND key (the "clean key", see app.services.song_identity) collapses those
formatting variants onto one identity. Pure + deterministic + fully testable; no
DB, no model call. The closed list is the guardrail: it strips only known cruft
tokens, so it can never eat real title content (e.g. a song actually titled
"Audio" or "Lyrics"), and it never touches version-meaningful words
(remix / live / acoustic / version) so covers + remixes stay distinct works.

The two 2026-06-13 misses this resolves:
  "ILLIT 'ICONIC BY MISTAKE' Official MV" / "HYBE LABELS"
      -> "ICONIC BY MISTAKE" / "ILLIT"   (quote-format + label->title artist)
  "Olivia Rodrigo - stupid song (Official Music Video)" / "OliviaRodrigoVEVO"
      -> "stupid song" / "Olivia Rodrigo"  (leading prefix + bracket + VEVO)
"""

import re

from app.services.song_search import normalize_for_search
from app.services.artist_linker import parse_artist_string


# --- closed token lists ---------------------------------------------------- #

# A bracketed/parenthetical group is DROPPED when its inner text matches one of
# these exactly (case-insensitive, trailing punctuation ignored). Exact-match
# only for these short ambiguous words so a real one-word title can never be
# eaten -- the strip applies ONLY inside brackets, never to the bare title.
_BRACKET_CRUFT_EXACT = {
    "official music video", "official video", "official mv", "official m/v",
    "official audio", "official lyric video", "official lyrics video",
    "official visualizer", "official visualiser", "official hd video",
    "official performance video", "performance video",
    "music video", "lyric video", "lyrics video", "lyric visualizer",
    "visualizer", "visualiser", "audio", "lyrics", "lyric",
    "video oficial", "videoclip oficial", "clip officiel",
    "mv", "m/v", "official", "explicit", "clean version", "hd", "4k",
}

# A bracketed group is also dropped when its inner text CONTAINS one of these
# phrases (multi-word, specific enough not to false-positive on real titles).
_BRACKET_CRUFT_CONTAINS = (
    "official music video", "official video", "official mv", "official m/v",
    "official audio", "official lyric", "lyric video", "lyrics video",
    "music video", "visualizer", "visualiser", "video oficial",
    "shot by", "prod by", "prod.", "produced by", "directed by", "dir.",
    "color coded", "han/rom/eng", "eng sub", "sub espanol", "legendado",
)

# Featured / production credit parentheticals: "(feat. X)", "(ft. X)",
# "(with X)", "(prod. X)". Matched at the START of the bracket inner text only.
_CREDIT_PAREN_RE = re.compile(r"^(feat\.?|ft\.?|featuring|with|prod\.?|produced by)\b", re.I)

# Trailing standalone cruft (NOT bracketed), e.g. "... Official MV". Stripped
# from the end, optionally preceded by a separator. Longest-first so the most
# specific phrase wins.
_TRAILING_CRUFT = sorted([
    "official music video", "official video", "official mv", "official m/v",
    "official audio", "official lyric video", "lyric video", "music video",
    "official visualizer", "visualizer", "video oficial", "m/v", "mv",
], key=len, reverse=True)

# Channel/label "artists" -- when the credited artist is one of these, the real
# performer (if derivable from the title's quote/prefix) is preferred. Stored
# normalized so comparison is punctuation/spacing-insensitive.
_LABEL_CHANNELS = {normalize_for_search(x) for x in [
    "HYBE LABELS", "HYBE", "SMTOWN", "JYP Entertainment", "YG Entertainment",
    "1theK", "Stone Music Entertainment", "Kakao Entertainment",
    "Starship Entertainment", "CUBE Entertainment", "RBW", "Pledis Entertainment",
    "ADOR", "SOURCE MUSIC", "BELIFT LAB", "Mnet K-POP",
    "The Orchard Music", "Believe Music", "Warner Music", "Sony Music",
    "Universal Music", "UMG", "Columbia Records", "Interscope Records",
    "Republic Records", "Atlantic Records", "Capitol Records", "RCA Records",
]}

_QUOTE_CHARS = "'\"‘’“”"
# ARTIST 'TITLE' (cruft): captures the prefix before the first quote + the
# quoted inner. Applied ONLY when an MV signal is present (see clean_title_artist),
# so an apostrophe inside a normal title (Rock 'n' Roll) is never mis-parsed.
_QUOTE_RE = re.compile(
    r"^(?P<pre>.*?)[" + _QUOTE_CHARS + r"]\s*(?P<inner>.+?)\s*[" + _QUOTE_CHARS + r"]"
)
_MV_SIGNAL_RE = re.compile(r"(official\s*(music\s*)?(video|mv)|m/v|\bmv\b)", re.I)

_VEVO_RE = re.compile(r"\s*vevo\s*$", re.I)
_TOPIC_RE = re.compile(r"\s*-\s*topic\s*$", re.I)
_OFFICIAL_ARTIST_RE = re.compile(r"\s*-\s*official\s*$", re.I)
_BRACKET_RE = re.compile(r"[\(\[]([^\(\)\[\]]*)[\)\]]")
_LANG_PAREN_RE = re.compile(r"[\(\[][^\(\)\[\]]*[\)\]]")
_WS_RE = re.compile(r"\s+")


def _primary(artist):
    """Primary (first-credited) artist name, mirroring song_identity."""
    if not artist:
        return ""
    try:
        entries = parse_artist_string(artist)
        if entries:
            return entries[0].get("name") or ""
    except Exception:
        pass
    return artist


def _primary_is_label_channel(artist):
    return normalize_for_search(_primary(artist)) in _LABEL_CHANNELS


def _collapse_ws(s):
    return _WS_RE.sub(" ", s).strip()


def _is_cruft_bracket(inner):
    s = inner.strip().lower().rstrip(".").strip()
    if not s:
        return True
    if s in _BRACKET_CRUFT_EXACT:
        return True
    for phrase in _BRACKET_CRUFT_CONTAINS:
        if phrase in s:
            return True
    if _CREDIT_PAREN_RE.match(s):
        return True
    return False


def _strip_brackets(title):
    def repl(m):
        return "" if _is_cruft_bracket(m.group(1)) else m.group(0)
    return _BRACKET_RE.sub(repl, title)


def _strip_trailing_cruft(title):
    changed = True
    out = title
    while changed:
        changed = False
        low = out.lower()
        for phrase in _TRAILING_CRUFT:
            # phrase at the end, optionally led by a separator
            m = re.search(r"(?:[\s|\-–—:.])*" + re.escape(phrase) + r"\s*$", low)
            if m and m.start() > 0:
                out = out[:m.start()].rstrip(" |-–—:.")
                changed = True
                break
    return out


def _strip_leading_artist(title, *artist_candidates):
    """Drop a leading "ARTIST - " prefix when ARTIST matches a known artist
    string (the credited primary or a title-derived hint). Conservative: only
    strips when the prefix actually equals an artist, so "New York - Paris"
    (no artist match) is left intact."""
    if " - " not in title:
        return title
    prefix, rest = title.split(" - ", 1)
    pn = normalize_for_search(prefix)
    if not pn:
        return title
    for cand in artist_candidates:
        if cand and normalize_for_search(cand) == pn and rest.strip():
            return rest.strip()
    return title


def _strip_pipe_tail(title):
    """Feeder titles use ' | <channel/credit>' as a tail (often '| @handle').
    Keep the first segment. A real song title containing a pipe is vanishingly
    rare, and the fallback in clean_title_artist re-uses the raw title if the
    result normalizes to empty."""
    if "|" in title:
        head = title.split("|", 1)[0].strip()
        if head:
            return head
    return title


def clean_artist(artist):
    """Strip channel-suffix cruft from an artist string. VEVO suffix, YouTube
    '- Topic' / '- Official' auto-channels. Label-channel -> real-artist
    reconciliation is done in clean_title_artist (it needs the title)."""
    a = (artist or "").strip()
    if not a:
        return a
    a = _TOPIC_RE.sub("", a).strip()
    a = _OFFICIAL_ARTIST_RE.sub("", a).strip()
    a = _VEVO_RE.sub("", a).strip()
    return a


def clean_title_artist(title, artist):
    """Return (clean_title, clean_artist) with the closed cruft list applied.

    Symmetric: the SAME cleaning runs on stored rows (at backfill) and on
    incoming feeder strings (at resolve), so a formatting variant of a song
    already in the Library produces the same clean key and resolves to it.

    Never returns an empty title/artist -- if cleaning would blank a field it
    falls back to the raw input, so identity can never collapse to a degenerate
    key.
    """
    raw_title = (title or "").strip()
    raw_artist = (artist or "").strip()
    primary_raw = _primary(raw_artist)

    # Clean the artist suffix (VEVO / "- Topic") FIRST so the leading-prefix
    # match below is symmetric across variants: a stored "OliviaRodrigoVEVO" and
    # an incoming "Olivia Rodrigo" must both strip the same "Olivia Rodrigo - "
    # title prefix, or their clean keys diverge.
    a = clean_artist(raw_artist)

    t = raw_title
    artist_hint = None

    # 1. K-pop / quoted-title MV format: ARTIST 'TITLE' cruft. Gated on an MV
    #    signal (or a label-channel artist) so a normal apostrophe title is safe.
    if _MV_SIGNAL_RE.search(t) or _primary_is_label_channel(raw_artist):
        m = _QUOTE_RE.match(t)
        if m:
            pre = m.group("pre").strip()
            inner = m.group("inner").strip()
            if pre and inner:
                artist_hint = _collapse_ws(_LANG_PAREN_RE.sub("", pre))
                t = inner

    # Label-channel -> real-artist swap (needs the title-derived hint).
    if artist_hint and _primary_is_label_channel(raw_artist):
        a = artist_hint
    primary_clean = _primary(a)

    # 2. Leading "ARTIST - " prefix (cleaned primary, raw primary, or the hint).
    t = _strip_leading_artist(t, primary_clean, artist_hint, primary_raw)

    # 3. Channel/credit pipe tail.
    t = _strip_pipe_tail(t)

    # 4. Bracketed cruft (MV/lyric-video/credit parentheticals).
    t = _strip_brackets(t)

    # 5. Trailing standalone cruft ("... Official MV").
    t = _strip_trailing_cruft(t)

    t = _collapse_ws(t)

    # Fallbacks: never blank a field (a degenerate key would mis-merge).
    if not normalize_for_search(t):
        t = raw_title
    if not normalize_for_search(a):
        a = raw_artist
    return t.strip(), a.strip()
