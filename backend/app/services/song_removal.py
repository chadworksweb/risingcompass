"""THE one place that decides whether a song may be deleted from the Library.

Every caller that removes a song goes through `remove_song`. There is no second
implementation, and adding one is the bug this module exists to prevent.

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

  1. Check the HARD references: a chart appearance, a daily reading, an agent
     draft, a release track. Any one of them means a person or a published page
     depends on this song, and a clutter flag is not grounds to delete it. The
     song is kept and the caller is told why, in words it can show.
  2. Only when none of those exist, delete the song and its directly-owned rows.

THE ONE AXIS CALLERS DIFFER ON is whether a surviving `song_ingestions` row is
itself a reason to keep the song, which is what `ingestion_holds` selects.

  - The FEEDER endpoints (submissions, stream, library) each delete their OWN
    ingestion and then ask "is this song now a pure artifact of the feeder I just
    removed?" A row left by a different feeder means another surface still claims
    this song, so it is a hard reference and the song stays. `ingestion_holds=True`.
  - The CLUTTER queue removes a song a human judged to be clutter, whatever fed
    it. There the ingestion is the provenance of the thing being removed, not a
    reason to keep it, so it is deleted in step 2 instead. `ingestion_holds=False`,
    the default.

Nothing is deleted on the kept branch, so a wrong call costs nothing.

THE EXPLICIT DELETE LIST exists because most of the FKs onto songs.id are SET
NULL, not CASCADE. Without it a removal leaves a `song_slugs` row resolving to
NULL and a scatter of ownerless reference rows behind.

HISTORY (2026-08-17). This module used to be one of FOUR copies of the routine.
The other three lived inline in `submissions_admin`, `library_admin`, and
`stream`, each with its own hand-typed table list, and they had already drifted:
`stream.delete_stream_song` never checked `release_songs`, so deleting a stream
entry could delete a song that was still a track on a release and leave the
release pointing at nothing. That is what a hand-maintained duplicate costs. Add
the next `song_id` table HERE and every caller gets it.

The caller owns the transaction; nothing here commits.
"""
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# A reference that outranks a clutter flag. Each is something a person or a
# published surface points at, so deleting the song underneath it would break a
# page rather than tidy one. ADD NEW song_id TABLES HERE, not at a call site.
_HARD_REFERENCES = (
    ("chart_appearances", "it has charted"),
    ("reading_songs", "it appears in a daily reading"),
    ("agent_draft_songs", "it is in an agent draft"),
    ("release_songs", "it is a track on a release"),
)

# Consulted only when ingestion_holds is set -- see the module docstring. Checked
# FIRST so a feeder caller gets the most specific reason rather than a downstream
# one, and so the common case (another feeder still holds it) costs one query.
_INGESTION_REFERENCE = ("song_ingestions", "it was ingested by another feeder")

# Rows a song directly owns, which SET NULL would otherwise strand.
# `song_ingestions` is in this list unconditionally: under ingestion_holds the
# check above has already proven there are none, so the delete is a harmless
# no-op rather than a second contract to keep in sync.
_OWNED_TABLES = (
    "song_artists",
    "song_slugs",
    "user_calibrations",
    "calibration_runs",
    "misread_submissions",
    "song_ingestions",
)


def remove_song(db: Session, song_id: int, *, ingestion_holds: bool = False) -> dict:
    """Delete a song and its owned rows, unless something real still points at it.

    Returns {"song_removed": bool, "kept_reason": str | None, "title", "artist"}.
    Works on ANY song regardless of how it entered the Library. Caller commits.

    Set `ingestion_holds` when the caller has just deleted its OWN ingestion and a
    row left by a different feeder should keep the song alive. See the module
    docstring for which callers want which.
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

    checks = _HARD_REFERENCES
    if ingestion_holds:
        checks = (_INGESTION_REFERENCE, *checks)

    for table, reason in checks:
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
