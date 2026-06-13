"""Add songs.canonical_key_clean -- the Phase-1 identity-resolution clean key.

canonical_key_clean = the canonical key computed AFTER the closed feeder-cruft
cleaning pass (app.services.feeder_clean): MV/lyric-video title cruft stripped, a
leading "ARTIST - " prefix removed, the K-pop "ARTIST 'TITLE' Official MV" form
unwrapped, and VEVO/label-channel artists reconciled to the real performer. It
collapses the social-discovery feeders' formatting drift onto one identity so a
re-entry of a song already in the Library resolves to it (resolve_song_identity
Rung 2) instead of minting a duplicate row.

Indexed, NOT unique: a collision (two distinct rows sharing a clean key) is a
duplicate to SURFACE, not an error. The backfill recomputes clean keys for every
existing row and LOGS the collisions it finds -- those are the pre-existing
duplicate pairs (e.g. the 2026-06-13 ICONIC BY MISTAKE / stupid song misses) that
the Phase-2 song-merge endpoint cleans up. Phase 1 stops NEW dupes; it does not
auto-merge the historical tail.

The backfill is Python (the clean key is closed-list string logic, not SQL), run
inline against the migration's live connection. PG-compatible (063+).
Base.metadata.create_all() builds the column on fresh installs from the model.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _backfill(conn) -> dict[str, list[int]]:
    """Recompute every row's clean key with the closed cleaning pass; return the
    collision groups found (clean_key -> [song ids] for any clean key shared by
    >1 DISTINCT raw canonical_key, i.e. separately-minted rows that are the same
    song). Split out from up() so it can be exercised against a real session in
    verification without re-running the (proven) idempotent DDL above. Imported
    here (runtime, not bare import) so the closed token list stays the single
    source of truth shared with the live write/read path."""
    from app.services.song_identity import compute_canonical_key_clean

    rows = conn.execute(
        text("SELECT id, title, artist, canonical_key FROM songs")
    ).fetchall()

    clean_to_keys: dict[str, set[str]] = {}
    clean_to_ids: dict[str, list[int]] = {}
    for sid, title, artist, canon in rows:
        ck = compute_canonical_key_clean(title, artist)
        conn.execute(
            text("UPDATE songs SET canonical_key_clean = :ck WHERE id = :id"),
            {"ck": ck, "id": sid},
        )
        clean_to_keys.setdefault(ck, set()).add(canon)
        clean_to_ids.setdefault(ck, []).append(sid)

    return {
        ck: clean_to_ids[ck]
        for ck, keys in clean_to_keys.items()
        if len(keys) > 1
    }


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS canonical_key_clean TEXT"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_songs_canonical_key_clean "
        "ON songs(canonical_key_clean)"
    ))

    collisions = _backfill(conn)

    # Surface pre-existing collisions (logged, not auto-merged) so the Phase-2
    # merge endpoint resolves them deliberately.
    if collisions:
        logger.warning(
            "migration 122: %d clean-key collision group(s) found (pre-existing "
            "duplicate song rows): %s",
            len(collisions),
            "; ".join(f"ids={ids}" for ids in collisions.values()),
        )
    else:
        logger.info("migration 122: backfill complete; no collisions")
