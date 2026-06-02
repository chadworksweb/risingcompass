"""Retroactively seed pre-rubric-update calibration_runs.

When the rubric_update pipeline first shipped it superseded prior runs
but didn't seed the pre-mutation state if no runs existed yet. Result:
for songs whose first-ever calibration_runs entry was the post-update
run, the original Ascended (or whatever) state disappeared from the
public runs timeline. The SongRecalibration audit row still carries
before_charge + before_color + before_summary, so we can reconstruct it.

For every SongRecalibration audit row where:
  - pipeline = 'rubric_update', AND
  - the song has no calibration_runs rows from BEFORE applied_at,
insert a seed CalibrationRun with the before_* values, triggered_by
'seed_pre_rubric_update', superseded=True, superseded_reason=slug,
superseded_at=applied_at.

Idempotent — re-running is a no-op.
"""
import os
import sys

import libsql


_TIMESTAMP_COLS = {
    "compass": ("compass_songs", "created_at"),
    "library": ("library_songs", "created_at"),
    "submitted": ("submitted_songs", "submitted_at"),
    "stream": ("cl_stream_songs", "created_at"),
}


def _original_song_timestamp(conn, source: str, song_id: int):
    """Fetch the song's original creation/submission timestamp. Used as
    run_at for the seed so it sorts BEFORE the post-rubric_update run in
    the public timeline. Returns None if the song or column is missing.
    """
    entry = _TIMESTAMP_COLS.get(source)
    if not entry:
        return None
    table, col = entry
    try:
        row = conn.execute(
            f"SELECT {col} FROM {table} WHERE id = ?", (song_id,),
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def main() -> int:
    url = os.environ["DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    conn = libsql.connect(database=url, auth_token=token)

    rows = conn.execute("""
        SELECT id, song_source, song_id, before_charge, before_color,
               before_summary, rubric_change_slug, applied_at
        FROM song_recalibrations
        WHERE pipeline = 'rubric_update'
    """).fetchall()

    seeded = 0
    skipped = 0
    for audit_id, source, song_id, bc, bcol, bsum, slug, applied_at in rows:
        # Check if the pre-rubric-update state is already represented in
        # calibration_runs. We match on (song, color, charge) — if a row
        # already carries the before_* values it means the seed already
        # happened (either via a real prior run or a previous backfill run).
        existing = conn.execute(
            "SELECT COUNT(*) FROM calibration_runs "
            "WHERE song_source = ? AND song_id = ? "
            "  AND rubric_color IS ? AND charge_value IS ?",
            (source, song_id, bcol, bc),
        ).fetchone()
        if existing and existing[0] > 0:
            skipped += 1
            continue

        # Use the song's ORIGINAL creation/submission time as run_at so the
        # seed sorts before the post-update run in the timeline. Fall back
        # to applied_at if the song row has no timestamp (should be rare).
        run_at = _original_song_timestamp(conn, source, song_id) or applied_at
        conn.execute(
            "INSERT INTO calibration_runs "
            "(song_source, song_id, title, artist, rubric_color, charge_value, "
            " charge_summary, contaminated, contamination_note, confidence, "
            " agent_model, triggered_by, lyrics_hash, run_at, "
            " superseded, superseded_reason, superseded_at) "
            "VALUES (?, ?, NULL, NULL, ?, ?, ?, 0, NULL, NULL, NULL, "
            " 'seed_pre_rubric_update', NULL, ?, 1, ?, ?)",
            (source, song_id, bcol, bc, bsum, run_at, slug, applied_at),
        )
        conn.commit()
        seeded += 1
        print(f"seeded {source}/{song_id} (audit #{audit_id}): {bcol} {bc:+d}")

    print(f"\nTOTAL: seeded {seeded}, skipped {skipped} (already had prior runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
