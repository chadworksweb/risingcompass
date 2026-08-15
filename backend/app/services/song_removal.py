"""Removing a song from the Library, for callers that are not the submissions tab.

WHY THIS IS NOT submissions_admin.delete_submission. That endpoint deletes a
Lyrical Charger SUBMISSION: it fetches the song through an INNER JOIN on the
song's `lyrical_charger` ingestion, drops that ingestion, and removes the song
only if nothing is left holding it. Correct for its own contract, and wrong for
every other caller -- a chart-born, stream-born or terminal-born song has no such
ingestion, so the fetch 404s and nothing happens at all.

The LEIT clutter queue was calling it anyway, swallowing the 404 as "nothing to
remove", and still writing status='removed' on the audit row. So the queue could
report a song removed while leaving it live on the site. That is worse than
failing: a queue whose successes are not real is a queue nobody can act on. It
was found on the `test / test` placeholder (stream-born), which the queue marked
removed and left in the Library.

WHAT THIS DOES INSTEAD. It asks the question that actually matters -- is anything
real still pointing at this song? -- and never touches provenance unless the
answer is no:

  1. Check the four HARD references: a chart appearance, a daily reading, an
     agent draft, a release track. Any one of them means a person or a published
     page depends on this song, and a clutter flag is not grounds to delete it.
     The song is kept and the caller is told why, in words it can show.
  2. Only when none of those exist, delete the song and its directly-owned rows.

Ingestions are deleted as part of step 2 rather than being consulted in step 1,
which is the substantive difference from the submissions path: for the clutter
queue an ingestion is the provenance of the thing being removed, not a reason to
keep it. Nothing is deleted on the kept branch, so a wrong call costs nothing.

The explicit delete list exists because most of the FKs onto songs.id are SET
NULL, not CASCADE. Without it a removal leaves a `song_slugs` row resolving to
NULL and a scatter of ownerless reference rows behind. It deliberately matches
the set submissions_admin.delete_submission already removes, so the two paths
agree on what a song directly owns.

The caller owns the transaction; nothing here commits.
"""
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# A reference that outranks a clutter flag. Each is something a person or a
# published surface points at, so deleting the song underneath it would break a
# page rather than tidy one.
_HARD_REFERENCES = (
    ("chart_appearances", "it has charted"),
    ("reading_songs", "it appears in a daily reading"),
    ("agent_draft_songs", "it is in an agent draft"),
    ("release_songs", "it is a track on a release"),
)

# Rows a song directly owns, which SET NULL would otherwise strand. Same set the
# submissions delete path removes.
_OWNED_TABLES = (
    "song_artists",
    "song_slugs",
    "user_calibrations",
    "calibration_runs",
    "misread_submissions",
    "song_ingestions",
)


def remove_song(db: Session, song_id: int) -> dict:
    """Delete a song and its owned rows, unless something real still points at it.

    Returns {"song_removed": bool, "kept_reason": str | None, "title", "artist"}.
    Works on ANY song regardless of how it entered the Library. Caller commits.
    """
    row = db.execute(
        text("SELECT title, artist FROM songs WHERE id = :s"), {"s": song_id},
    ).first()
    if row is None:
        # Already gone. Idempotent rather than an error: a double-resolve on the
        # same audit row should not fail.
        return {"song_removed": False, "kept_reason": "song no longer exists",
                "title": None, "artist": None}
    title, artist = row

    for table, reason in _HARD_REFERENCES:
        held = db.execute(
            text(f"SELECT 1 FROM {table} WHERE song_id = :s LIMIT 1"), {"s": song_id},
        ).scalar()
        if held:
            logger.info("song %s kept: %s", song_id, reason)
            return {"song_removed": False, "kept_reason": reason,
                    "title": title, "artist": artist}

    for table in _OWNED_TABLES:
        db.execute(text(f"DELETE FROM {table} WHERE song_id = :s"), {"s": song_id})
    db.execute(text("DELETE FROM song_id_map WHERE new_song_id = :s"), {"s": song_id})
    db.execute(text("DELETE FROM songs WHERE id = :s"), {"s": song_id})

    return {"song_removed": True, "kept_reason": None,
            "title": title, "artist": artist}
