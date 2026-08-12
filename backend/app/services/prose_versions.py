"""Append-only history for a song's generated prose.

The calibration side has `calibration_runs`; this is the same posture for the
generated text. One row per lane per write, recorded AFTER the song row has been
updated, so what lands in the history is exactly what the song now carries.

Why it exists: `songs.prior_listener_effects_prose` / `prior_societal_effects_prose`
are one slot each and `psyche_facts` has none, so consecutive regens push older
text out permanently (song 2797, 2026-08-12). Worse, the only two sites that
archive at all are the explicit regen routers; the chokepoint every authoritative
calibration goes through overwrites prose with no archive step.

Design notes:
- FAIL-SOFT. A history write can never break a calibration write. Every entry
  point swallows its own exceptions and logs.
- IDEMPOTENT BY VALUE. A lane is recorded only when its current text differs from
  the newest recorded version for that lane, so re-sending unchanged prose (a
  topics retag that re-posts the same blocks) logs nothing.
- The caller owns the transaction. Nothing here commits.

Spec: `Dropbox/Rising Compass/plans and docs/RISING-COMPASS-PROSE-VERSIONING-SCOPE.md`
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# lane -> (prose column, model column, generated_at column)
LANES: dict[str, tuple[str, Optional[str], Optional[str]]] = {
    "listener": ("listener_effects_prose", None, None),
    "societal": ("societal_effects_prose", "societal_prose_model", "societal_prose_generated_at"),
    # Stored as its JSON string, the same shape the column holds.
    "psyche_facts": ("psyche_facts", None, None),
}

_SONG_COLS = (
    "title, artist, rubric_color, charge_value, listener_effects_prose, "
    "societal_effects_prose, psyche_facts, societal_prose_model, "
    "societal_prose_generated_at"
)


def record_prose_versions(
    db,
    song_id: int | None,
    *,
    trigger: str,
    lanes: Iterable[str] | None = None,
) -> int:
    """Append a version row for each lane whose current value is new.

    Returns the number of rows written (0 is normal and not an error). Never
    raises: a failure here degrades history, it does not fail the write that
    prompted it.
    """
    if not song_id:
        return 0

    try:
        from app.config import settings

        wanted = [ln for ln in (lanes or LANES.keys()) if ln in LANES]
        if not wanted:
            return 0

        song = db.execute(
            text(f"SELECT {_SONG_COLS} FROM songs WHERE id = :sid"),
            {"sid": song_id},
        ).mappings().first()
        if not song:
            return 0

        # Newest recorded version per lane, in one pass.
        latest = {
            r["lane"]: r["prose"]
            for r in db.execute(
                text(
                    "SELECT DISTINCT ON (lane) lane, prose FROM song_prose_versions "
                    "WHERE song_id = :sid ORDER BY lane, id DESC"
                ),
                {"sid": song_id},
            ).mappings()
        }

        written = 0
        for lane in wanted:
            prose_col, model_col, gen_col = LANES[lane]
            current = song[prose_col]
            if not current or not str(current).strip():
                continue
            if latest.get(lane) == current:
                continue

            db.execute(
                text(
                    "INSERT INTO song_prose_versions "
                    "(song_id, title, artist, lane, prose, model, generated_at, "
                    " trigger, rubric_color, charge_value, environment) "
                    "VALUES (:song_id, :title, :artist, :lane, :prose, :model, "
                    " :generated_at, :trigger, :rubric_color, :charge_value, :environment)"
                ),
                {
                    "song_id": song_id,
                    "title": song["title"],
                    "artist": song["artist"],
                    "lane": lane,
                    "prose": current,
                    "model": song[model_col] if model_col else None,
                    "generated_at": song[gen_col] if gen_col else None,
                    "trigger": trigger,
                    "rubric_color": song["rubric_color"],
                    "charge_value": song["charge_value"],
                    "environment": settings.environment,
                },
            )
            written += 1

        return written
    except Exception:  # noqa: BLE001 -- history must never break a calibration write
        logger.exception("record_prose_versions failed for song_id=%s trigger=%s",
                         song_id, trigger)
        return 0
