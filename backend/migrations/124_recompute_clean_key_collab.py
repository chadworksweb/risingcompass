"""Re-backfill songs.canonical_key_clean after the collab-order fix.

The clean key's artist part now uses an ORDER-INDEPENDENT set of the co-primary
artists (song_identity.clean_artist_set_key) instead of the first-credited
artist alone. A multi-group collaboration that surfaces with its credits in a
different order on different feeds ("LE SSERAFIM x ILLIT x KATSEYE" via the MV
title vs. "ILLIT, LE SSERAFIM, KATSEYE" via the YouTube feed) used to compute a
DIFFERENT clean key each way and re-list as awaiting-lyrics every day. With the
set key both forms collapse to one identity.

This migration recomputes every row's canonical_key_clean with the updated
scheme so existing collab rows (calibrated before the fix) carry the new
order-stable key and resolve_song_identity Rung 2 starts matching them. Only
multi-PRIMARY rows change value; single-artist and primary+featured rows are
unchanged. New collision groups (a clean key now shared by >1 distinct raw
canonical_key) are pre-existing collab duplicates to surface for the Phase-2
merge endpoint -- logged, never auto-merged. Idempotent; PG-compatible.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _backfill(conn) -> dict[str, list[int]]:
    """Recompute every row's clean key; return collision groups (clean_key ->
    [ids] for any clean key shared by >1 DISTINCT raw canonical_key). Mirrors
    migration 122's helper but reflects the new clean_artist_set_key logic via
    the shared compute_canonical_key_clean."""
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
    collisions = _backfill(conn)
    if collisions:
        logger.warning(
            "migration 124: %d clean-key collision group(s) after collab-order "
            "recompute (pre-existing duplicate collab rows to merge): %s",
            len(collisions),
            "; ".join(f"ids={ids}" for ids in collisions.values()),
        )
    else:
        logger.info("migration 124: clean-key recompute complete; no collisions")
