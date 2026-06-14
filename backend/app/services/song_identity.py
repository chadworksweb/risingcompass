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

from dataclasses import dataclass, field

from sqlalchemy import text

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


def clean_artist_set_key(artist):
    """Order-independent normalized key for the CLEAN identity's artist part.

    A multi-PRIMARY collaboration ("LE SSERAFIM x ILLIT x KATSEYE",
    "ILLIT, LE SSERAFIM, KATSEYE") surfaces with the co-credits in a different
    order on different feeds, so keying the clean identity on the first-credited
    artist alone misses itself every time the order flips -- the song re-lists as
    awaiting-lyrics daily. This collapses all co-primary credits to a sorted,
    deduped set so the key is identical regardless of credit order.

    Featured artists are EXCLUDED (preserving the prior primary-only behavior):
    "A" and "A feat. B" still key the same. A single-primary credit returns the
    same string the old extract_primary_artist(artist) produced, so single-artist
    rows are unchanged -- only multi-primary rows get a new (order-stable) key.

    Pure; no DB. parse_artist_string is imported lazily (it imports models)."""
    try:
        from app.services.artist_linker import parse_artist_string
        entries = parse_artist_string(artist or "")
    except Exception:
        entries = []
    primaries = []
    for e in entries:
        if (e.get("role") or "primary") == "primary":
            n = normalize_for_search(e.get("name") or "")
            if n:
                primaries.append(n)
    if not primaries:
        return normalize_for_search(extract_primary_artist(artist))
    return "".join(sorted(set(primaries)))


def compute_canonical_key_clean(title, artist):
    """The Phase-1 CLEAN identity: the canonical key computed AFTER the closed
    feeder-cruft cleaning pass (app.services.feeder_clean). Collapses MV/lyric-
    video formatting + VEVO/label-channel artists onto one key so a feeder re-
    entry of a song already in the Library resolves to it instead of minting a
    duplicate. When (title, artist) carry no cruft, this equals compute_canonical_key.

    The artist part uses clean_artist_set_key (order-independent over co-primary
    collaborators), so a multi-group collab that surfaces with its credits in a
    different order on different feeds still resolves to the one Library row.

    Pure; no DB. Imported lazily to avoid a feeder_clean <-> song_identity cycle
    at module load (feeder_clean imports song_search + artist_linker, same as us).
    """
    from app.services.feeder_clean import clean_title_artist
    ct, ca = clean_title_artist(title or "", artist or "")
    nt = normalize_for_search(ct)
    na = clean_artist_set_key(ca)
    return f"{nt}{CANON_SEP}{na}"


@dataclass
class Resolution:
    """Outcome of the layered identity-resolution ladder. `song_id` is the
    resolved unified songs.id (None = no match, mint a new row). `via` records
    which rung hit: 'exact' (canonical_key), 'clean' (canonical_key_clean), or
    'new'. `candidates` holds gray-band ids for the Phase-2 audit queue (always
    empty in Phase 1; the deterministic rungs auto-link or fall through)."""
    song_id: int | None
    via: str
    candidates: list[int] = field(default_factory=list)


def resolve_song_identity(db, title, artist, lyrics=None) -> Resolution:
    """The single identity-resolution ladder behind every write/read chokepoint.

    Rung 1 (exact canonical_key) is the unchanged fast path -- most chart-to-chart
    overlaps hit here, so there is ZERO regression to existing matches. Only on an
    exact miss does Rung 2 (the cleaned key) run, catching the feeder formatting
    drift that minted duplicates. `lyrics` is accepted for the Phase 2/3 fuzzy +
    semantic rungs (unused in Phase 1).

    Returns a Resolution. Pure read -- no writes, no commit.
    """
    # Rung 1: exact canonical_key (today's behavior, fast path).
    key = compute_canonical_key(title, artist)
    row = db.execute(
        text("SELECT id FROM songs WHERE canonical_key = :k LIMIT 1"), {"k": key}
    ).first()
    if row:
        return Resolution(song_id=row[0], via="exact")

    # Rung 2: cleaned canonical_key. Match an existing row whose stored
    # canonical_key_clean equals our clean key, OR whose RAW canonical_key equals
    # our clean key (covers a clean row that predates the clean-key backfill, or
    # a row that was ingested clean while today's string carries cruft). Excludes
    # the exact key already tried. Lowest id wins (the canonical/oldest row).
    #
    # This runs even when clean_key == key (a cruft-free incoming string): the
    # stored DUPLICATE is the cruft row, so the match is against ITS
    # canonical_key_clean -- exactly the 2026-06-13 "ICONIC BY MISTAKE" case,
    # where the incoming string is already clean but the Library row carries the
    # MV cruft + label artist.
    clean_key = compute_canonical_key_clean(title, artist)
    if clean_key:
        row = db.execute(
            text(
                "SELECT id FROM songs "
                "WHERE (canonical_key_clean = :ck OR canonical_key = :ck) "
                "AND canonical_key <> :k "
                "ORDER BY id ASC LIMIT 1"
            ),
            {"ck": clean_key, "k": key},
        ).first()
        if row:
            return Resolution(song_id=row[0], via="clean")

    # Rung 3: pg_trgm fuzzy fallback. Ships DARK behind identity_trgm.enabled
    # (fail-closed). Fully fail-soft: any error (flag table, missing extension,
    # non-PG dialect) leaves the exact+clean behavior unchanged.
    try:
        from app.services.feature_flags import (
            is_identity_trgm_enabled, is_identity_trgm_autolink_enabled,
        )
        if clean_key and is_identity_trgm_enabled(db):
            autolink = is_identity_trgm_autolink_enabled(db)
            res = _trgm_resolve(db, title, artist, key, clean_key, autolink=autolink)
            if res is not None:
                return res
    except Exception:
        logger.debug("trgm rung skipped (fail-soft)", exc_info=True)

    # Rung 4 (pgvector) lands here in Phase 3.
    return Resolution(song_id=None, via="new")


# Rung 3 thresholds (conservative -- false merge is the only real danger). A
# fuzzy match auto-links ONLY when BOTH the title and the primary artist are
# near-identical; the gray band below that becomes a human-confirmed candidate.
_TRGM_AUTO_TITLE = 0.92
_TRGM_AUTO_ARTIST = 0.90
_TRGM_GRAY_COMBINED = 1.30  # tsim + asim, below auto -> queue, don't link


def _trgm_resolve(db, title, artist, key, clean_key, autolink=False):
    """pg_trgm similarity over the clean key's title/artist parts. Returns a
    Resolution (auto-link via='trgm' when `autolink`, or new+candidates), or None
    to fall through. With autolink OFF (the watch posture) even a high-confidence
    match is returned as a CANDIDATE, not linked. PG-only (similarity/split_part);
    returns None on any DB error so non-PG / no-extension callers are unaffected."""
    from app.services.feeder_clean import clean_title_artist
    ct, ca = clean_title_artist(title, artist)
    nt = normalize_for_search(ct)
    # Match the stored canonical_key_clean artist part (order-independent set key)
    # so trgm compares like-for-like.
    na = clean_artist_set_key(ca)
    if not nt:
        return None
    # Uses similarity() (not the `%` operator) to avoid the psycopg `%`-escaping
    # gotcha; correct on any driver. At current corpus scale this is cheap, and
    # it only runs on a cache-miss with the flag enabled.
    try:
        rows = db.execute(
            text(
                "SELECT id, "
                "  similarity(split_part(canonical_key_clean, chr(31), 1), :nt) AS tsim, "
                "  similarity(split_part(canonical_key_clean, chr(31), 2), :na) AS asim "
                "FROM songs "
                "WHERE canonical_key_clean IS NOT NULL AND canonical_key <> :k "
                "  AND similarity(canonical_key_clean, :ck) >= 0.3 "
                "ORDER BY (similarity(split_part(canonical_key_clean, chr(31), 1), :nt) "
                "        + similarity(split_part(canonical_key_clean, chr(31), 2), :na)) DESC "
                "LIMIT 8"
            ),
            {"nt": nt, "na": na, "ck": clean_key, "k": key},
        ).fetchall()
    except Exception:
        return None  # pg_trgm unavailable -> no fuzzy rung
    candidates = []
    for sid, tsim, asim in rows:
        tsim = tsim or 0.0
        asim = asim or 0.0
        if tsim >= _TRGM_AUTO_TITLE and asim >= _TRGM_AUTO_ARTIST:
            if autolink:
                return Resolution(song_id=sid, via="trgm")
            candidates.append(sid)  # watch posture: queue, don't link
        elif (tsim + asim) >= _TRGM_GRAY_COMBINED:
            candidates.append(sid)
    if candidates:
        return Resolution(song_id=None, via="new", candidates=candidates)
    return None
