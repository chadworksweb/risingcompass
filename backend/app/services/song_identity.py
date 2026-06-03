"""Canonical song identity -- single source of truth for the unified `songs`
table's `canonical_key` (the UNIQUE dedup key).

Used by scripts/unify_songs.py (Phase 2 merge) AND every post-cutover write path
(compass_agent._store_calibration, analyzer Lyrical Charger, stream, backfill)
so a song resolves to the same entity no matter how it enters.

Key = normalize_for_search(title) + US + normalize_for_search(primary_artist),
where primary_artist is the first credit from parse_artist_string (so
"Post Malone featuring Morgan Wallen", "Post Malone ft. Morgan Wallen", and
"Post Malone & Morgan Wallen" all collapse to the primary "Post Malone").
US = unit separator (0x1f), an char that never appears in normalized text.
"""

from app.services.song_search import normalize_for_search
from app.services.artist_linker import parse_artist_string

CANON_SEP = "\x1f"


def extract_primary_artist(artist):
    """Return the primary (first-credited) artist name from a credit string."""
    if not artist:
        return ""
    try:
        entries = parse_artist_string(artist)
        if entries:
            return entries[0].get("name") or ""
    except Exception:
        pass
    return artist


def compute_canonical_key(title, artist):
    """Deterministic canonical identity for (title, artist). Pure; no DB."""
    nt = normalize_for_search(title or "")
    na = normalize_for_search(extract_primary_artist(artist))
    return f"{nt}{CANON_SEP}{na}"
