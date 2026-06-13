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


def compute_canonical_key_clean(title, artist):
    """The Phase-1 CLEAN identity: the canonical key computed AFTER the closed
    feeder-cruft cleaning pass (app.services.feeder_clean). Collapses MV/lyric-
    video formatting + VEVO/label-channel artists onto one key so a feeder re-
    entry of a song already in the Library resolves to it instead of minting a
    duplicate. When (title, artist) carry no cruft, this equals compute_canonical_key.

    Pure; no DB. Imported lazily to avoid a feeder_clean <-> song_identity cycle
    at module load (feeder_clean imports song_search + artist_linker, same as us).
    """
    from app.services.feeder_clean import clean_title_artist
    ct, ca = clean_title_artist(title or "", artist or "")
    nt = normalize_for_search(ct)
    na = normalize_for_search(extract_primary_artist(ca))
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

    # Rungs 3 (pg_trgm) + 4 (pgvector) land here in Phases 2-3.
    return Resolution(song_id=None, via="new")
