"""Song-merge service -- collapse a DUPLICATE song row into its canonical twin.

`merge_songs(conn, source_id, target_id, ...)` atomically repoints every
reference from the source song onto the target, preserves the richer calibration,
writes a `song_merge_events` audit row, and deletes the source. Mirrors the
artist-merge transaction pattern (`routers/artists_admin.py`): raw SQLAlchemy
Core `text()` over ONE connection, one pass per UNIQUE constraint, audit in the
same transaction. The CALLER owns commit/rollback (so the merge-candidate resolve
flow can flip the candidate row in the same txn).

DESTRUCTIVE: it deletes the source `songs` row. The songs-not-artists rule means
this is only ever invoked by an admin's explicit decision (direct endpoint or a
human-confirmed merge-candidate), never automatically.

prose_provenance_anchors are intentionally NOT repointed: each anchor is a
hash of a specific row's prose at a moment in time, so re-attributing it to the
target would corrupt the provenance recipe. The source's anchors stay as
historical records of the now-deleted row (no FK, harmless dangling id).
"""

import json
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Calibration columns copied from source -> target ONLY when the target has no
# calibration (rubric_color IS NULL), so a merge into a stub keeps the reading.
# Identity columns (title, artist, canonical_key, canonical_key_clean) are NEVER
# copied -- the target's identity survives.
_CALIB_COLS = [
    "rubric_color", "charge_value", "charge_summary", "contaminated",
    "contamination_note", "dogma_referenced", "dogma_note", "instrumental",
    "translated", "medley", "preorder", "confidence",
    "listener_effects_prose", "societal_effects_prose",
    "societal_prose_generated_at", "societal_prose_model",
    "prior_listener_effects_prose", "prior_societal_effects_prose",
    "prior_societal_prose_generated_at", "prior_societal_prose_model",
    "deadpan_line", "topics", "topic_audit", "activations",
    "calibration_failed", "message_analysis", "expression_analysis",
    "intention_analysis", "canonical_calibration_method", "origin_chart",
    "album_id", "track_number",
]


class MergeError(ValueError):
    """Raised on an invalid merge request (self-merge, missing row)."""


def _song_row(conn, sid):
    return conn.execute(
        text("SELECT id, title, artist, rubric_color FROM songs WHERE id = :id"),
        {"id": sid},
    ).mappings().first()


def merge_songs(conn, source_id: int, target_id: int, *, actor: str | None = None,
                notes: str | None = None, environment: str = "prod") -> dict:
    """Merge `source_id` INTO `target_id`. Returns the rewrites breakdown dict.
    Does NOT commit -- the caller owns the transaction. Raises MergeError on a
    bad request (the caller maps it to a 400)."""
    if source_id == target_id:
        raise MergeError("Cannot merge a song into itself")
    src = _song_row(conn, source_id)
    tgt = _song_row(conn, target_id)
    if not src:
        raise MergeError(f"Source song {source_id} not found")
    if not tgt:
        raise MergeError(f"Target song {target_id} not found")

    rw: dict = {}

    def _repoint(table, col="song_id"):
        cur = conn.execute(
            text(f"UPDATE {table} SET {col} = :tgt WHERE {col} = :src"),
            {"tgt": target_id, "src": source_id},
        )
        rw[table] = cur.rowcount or 0

    # --- 1. preserve the richer calibration --------------------------------- #
    # Only fill a STUB target (no rubric_color) from a calibrated source, so we
    # never overwrite the target's own reading or Frankenstein two calibrations.
    rw["calibration_copied_from_source"] = False
    if tgt["rubric_color"] is None and src["rubric_color"] is not None:
        sets = ", ".join(f"{c} = src.{c}" for c in _CALIB_COLS)
        conn.execute(
            text(f"UPDATE songs AS t SET {sets} FROM songs AS src "
                 f"WHERE t.id = :tgt AND src.id = :src"),
            {"tgt": target_id, "src": source_id},
        )
        rw["calibration_copied_from_source"] = True

    # --- 2. UNIQUE-constrained tables: dedupe source rows, then repoint ------ #
    # user_calibrations UNIQUE(song_id, user_id)
    conn.execute(text(
        "DELETE FROM user_calibrations WHERE song_id = :src "
        "AND user_id IN (SELECT user_id FROM user_calibrations WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("user_calibrations")

    # song_artists UNIQUE(song_id, artist_id)
    conn.execute(text(
        "DELETE FROM song_artists WHERE song_id = :src "
        "AND artist_id IN (SELECT artist_id FROM song_artists WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("song_artists")

    # audience_vibe_needles UNIQUE(song_id) -- at most one per song
    conn.execute(text(
        "DELETE FROM audience_vibe_needles WHERE song_id = :src "
        "AND EXISTS (SELECT 1 FROM audience_vibe_needles WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("audience_vibe_needles")

    # audience_vibe_pushes UNIQUE(song_id, device_id, push_year)
    conn.execute(text(
        "DELETE FROM audience_vibe_pushes WHERE song_id = :src AND (device_id, push_year) IN "
        "(SELECT device_id, push_year FROM audience_vibe_pushes WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("audience_vibe_pushes")

    # artist_verification_blocks UNIQUE(artist_id, song_id)
    conn.execute(text(
        "DELETE FROM artist_verification_blocks WHERE song_id = :src "
        "AND artist_id IN (SELECT artist_id FROM artist_verification_blocks WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("artist_verification_blocks")

    # chart_appearances UNIQUE(song_id, chart_id, year, position, position_letter)
    # -- ondelete CASCADE, so MUST repoint before deleting the source song.
    conn.execute(text(
        "DELETE FROM chart_appearances WHERE song_id = :src "
        "AND (chart_id, year, position, position_letter) IN "
        "(SELECT chart_id, year, position, position_letter FROM chart_appearances WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("chart_appearances")

    # song_ingestions -- no DB unique, but upsert treats (song_id, method) as one;
    # dedupe on method so the target keeps one ingestion per method.
    conn.execute(text(
        "DELETE FROM song_ingestions WHERE song_id = :src "
        "AND method IN (SELECT method FROM song_ingestions WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("song_ingestions")

    # clutter_audits partial UNIQUE(song_id) WHERE status='open'
    conn.execute(text(
        "DELETE FROM clutter_audits WHERE song_id = :src AND status = 'open' "
        "AND EXISTS (SELECT 1 FROM clutter_audits WHERE song_id = :tgt AND status = 'open')"
    ), {"src": source_id, "tgt": target_id})
    _repoint("clutter_audits")

    # release_songs UNIQUE(release_id, song_id): dedupe on release, then repoint.
    conn.execute(text(
        "DELETE FROM release_songs WHERE song_id = :src "
        "AND release_id IN (SELECT release_id FROM release_songs WHERE song_id = :tgt)"
    ), {"src": source_id, "tgt": target_id})
    _repoint("release_songs")

    # song_identity_aliases UNIQUE(alias_key) -- global, so source and target can
    # never share a key and a plain repoint cannot collide. MUST repoint (not
    # cascade-delete): each row is a human-confirmed "this feeder string IS that
    # song", and dropping it would resurrect the daily awaiting-lyrics relink the
    # alias exists to kill. The FK is ON DELETE CASCADE, so this has to happen
    # before the source row is deleted below.
    _repoint("song_identity_aliases")

    # --- 3. plain repoints (no per-song UNIQUE) ----------------------------- #
    for table in (
        "reading_songs", "agent_draft_songs", "lc_events", "misread_submissions",
        "song_recalibration_proposals", "song_recalibrations", "song_resets",
        "audience_vibe_review_cases", "artist_outreach", "comments",
        "backfill_job_rows", "alltime_stream_songs", "social_posts",
        "calibration_runs", "artist_verification_inquiries", "song_slugs",
    ):
        _repoint(table)
    _repoint("song_id_map", col="new_song_id")

    # --- 4. delete the source song (chart_appearances + song_ingestions were
    #        already repointed above; nothing left to cascade) --------------- #
    conn.execute(text("DELETE FROM songs WHERE id = :src"), {"src": source_id})

    # --- 5. audit event (same transaction) ---------------------------------- #
    conn.execute(text(
        "INSERT INTO song_merge_events "
        "(actor, source_song_id, source_title, source_artist, "
        " target_song_id, target_title, target_artist, rewrites_json, notes, environment) "
        "VALUES (:actor, :sid, :st, :sa, :tid, :tt, :ta, :rw, :notes, :env)"
    ), {
        "actor": actor or "admin",
        "sid": source_id, "st": src["title"], "sa": src["artist"],
        "tid": target_id, "tt": tgt["title"], "ta": tgt["artist"],
        "rw": json.dumps(rw), "notes": notes, "env": environment,
    })

    logger.info("song merge %s -> %s by %s: %s", source_id, target_id, actor or "admin", rw)
    return rw
