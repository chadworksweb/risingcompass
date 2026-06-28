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

The 2026-06-15 miss this resolves:
  "BTS (<hangul>) 'Come Over' Lyric Video" / "BTS"
      -> "Come Over" / "BTS"   (lyric-video upload signal gates the quoted-title
         extraction; previously only "Official MV"-class titles did)
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
# ARTIST 'TITLE' (cruft): captures the prefix (the artist) + the quoted title.
# Applied ONLY when an upload signal is present (see clean_title_artist). The
# opening quote MUST be preceded by whitespace, so a mid-word apostrophe in a
# normal title ("Don't", "Believin'", "Rock 'n' Roll") never opens a group and
# gets mis-parsed; inner excludes quote chars so it captures one quoted segment.
_QUOTE_RE = re.compile(
    r"^(?P<pre>.*?\S)\s+[" + _QUOTE_CHARS + r"](?P<inner>[^" + _QUOTE_CHARS + r"]+?)[" + _QUOTE_CHARS + r"]"
)
# Upload-cruft signal: gates the quote extractor and the label-channel swap. Any
# platform "this is an upload" marker -- official video/mv/audio, lyric(s) video,
# visualizer, bare m/v -- not just an MV, so a K-pop lyric-video upload
# ("ARTIST (name) 'TITLE' Lyric Video") triggers the quote extraction too.
_UPLOAD_SIGNAL_RE = re.compile(
    r"(official\s*(music\s*)?(video|mv|audio)|lyrics?\s*video|m/v|\bmv\b|visualiser|visualizer)",
    re.I,
)

# Soundtrack provenance suffix: a trailing '- From "Movie"' / '(From "Movie")'
# tail that Spotify/Apple append to a single that ALSO exists as a plain song
# row (the 2026-06-27 "I Knew It, I Knew You - From \"Toy Story 5\"" miss, which
# re-listed an already-calibrated song because the clean key carried the suffix).
# Closed by the required `From "<quoted work>"` shape -- it strips ONLY when a
# quoted work follows "From", so a real title ending in the word "from" is safe.
_SOUNDTRACK_SUFFIX_RE = re.compile(
    r"\s*(?:[-:|]\s*|[\(\[]\s*)from\s+(?:the\s+)?[" + _QUOTE_CHARS
    + r"][^" + _QUOTE_CHARS + r"]+[" + _QUOTE_CHARS + r"]\s*[\)\]]?\s*$",
    re.I,
)

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


def _strip_soundtrack_suffix(title):
    """Drop a trailing soundtrack-provenance tail ('- From "Movie"' /
    '(From "Movie")'). Conservative: requires the quoted-work shape, and falls
    back to the input if stripping would blank the title."""
    out = _SOUNDTRACK_SUFFIX_RE.sub("", title)
    return out if out.strip() else title


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

    # 1. K-pop / quoted-title upload format: ARTIST 'TITLE' cruft. Gated on an
    #    upload signal (or a label-channel artist) so a normal apostrophe title
    #    is safe; the whitespace-before-quote rule in _QUOTE_RE is the backstop.
    if _UPLOAD_SIGNAL_RE.search(t) or _primary_is_label_channel(raw_artist):
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

    # 6. Soundtrack-provenance suffix ('- From "Movie"' / '(From "Movie")').
    t = _strip_soundtrack_suffix(t)

    t = _collapse_ws(t)

    # Fallbacks: never blank a field (a degenerate key would mis-merge).
    if not normalize_for_search(t):
        t = raw_title
    if not normalize_for_search(a):
        a = raw_artist
    return t.strip(), a.strip()


def is_feeder_upload(title, artist):
    """True when (title, artist) looks like a RAW platform-upload string -- the
    YouTube/Shazam shape that mints cruft rows: an upload signal in the title
    (Official Video/MV/Audio, Lyric Video, visualizer), a channel/label/VEVO/
    '- Topic' artist, or a leading 'ARTIST - ' prefix that matches the credited
    artist.

    This gates DISPLAY cleaning (clean_feeder_display) so a normal chart entry
    like 'GIRLS (feat. Kehlani)' -- which clean_title_artist WOULD strip the
    feature paren from -- is left untouched: only genuine upload cruft is
    rewritten. The clean *key* (canonical_key_clean) still collapses feature
    parens for dedup; only the stored display string + exact key are protected.
    Pure; no DB."""
    t = title or ""
    a = artist or ""
    if _UPLOAD_SIGNAL_RE.search(t):
        return True
    if _VEVO_RE.search(a) or _TOPIC_RE.search(a) or _OFFICIAL_ARTIST_RE.search(a):
        return True
    if _primary_is_label_channel(a):
        return True
    if " - " in t:
        prefix = t.split(" - ", 1)[0]
        pn = normalize_for_search(prefix)
        if pn and pn == normalize_for_search(_primary(clean_artist(a))):
            return True
    return False


def clean_feeder_display(title, artist):
    """Display-safe feeder cleaning for the write chokepoint.

    Rewrites (title, artist) with clean_title_artist ONLY when it looks like raw
    platform-upload cruft (is_feeder_upload); otherwise returns the raw strings
    unchanged. This is what callers use to store a clean display title + clean
    exact key for feeder rows, while leaving legitimate '(feat. X)' chart titles
    exactly as the chart published them. Pure; no DB."""
    if not is_feeder_upload(title, artist):
        return (title or "").strip(), (artist or "").strip()
    ct, ca = clean_title_artist(title, artist)
    # Recover the real artist spelling from a leading "ARTIST - " title prefix.
    # clean_artist strips a VEVO suffix but cannot re-space the channel handle
    # ("OliviaRodrigoVEVO" -> "OliviaRodrigo"), which would mint a near-duplicate
    # artist entity instead of matching "Olivia Rodrigo". When the title prefix
    # normalizes to the same artist, adopt its spacing/casing. Same normalized
    # canonical key (normalize drops spaces), so this only fixes the display +
    # the artist entity the credit links to.
    raw_t = title or ""
    if " - " in raw_t:
        prefix = raw_t.split(" - ", 1)[0].strip()
        if prefix and normalize_for_search(prefix) == normalize_for_search(_primary(ca)):
            ca = prefix
    return ct.strip(), ca.strip()
