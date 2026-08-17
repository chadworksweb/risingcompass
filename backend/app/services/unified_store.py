"""Persistence + recomposition for the Unified Charge Chart.

The write half. `unified_chart.py` stays pure and computes; this decides when a
composition becomes a stored row, what a re-run is allowed to overwrite, and what
it must not touch.

THE RECOMPOSE CONTRACT

A day is recomposed every time one of its constituents is approved, because the
four charts land at different times. Recomposition is idempotent and is allowed
to move the numbers, the ranking, and the source record. It is NOT allowed to:

  - Unpublish a published reading. Publication is a deliberate act (the editorial
    write) and a background recompose must never undo it.
  - Overwrite the editorial. The prose is terminal-supplied and expensive; a
    recompose that silently replaced it would discard work.
  - Republish stale prose over new numbers in silence. When the figures actually
    move on an already-published reading, `editorial_stale` is set so the change
    is visible and the editorial can be rewritten.

FAIL-SOFT AT THE CALL SITE

The approval hook wraps this in try/except. A unified recompose failing must
never break a chart approval: the constituent charts are the primary artifact and
the synthesis is downstream of them. A failed recompose leaves the previous
stored reading intact and is recoverable by re-running.

MINIMUM SOURCES

A "unified" chart composed from one constituent is not unified, it is that
constituent wearing a different name. `MIN_SOURCES` keeps a single-source day
from being stored at all. This is policy, which is why it lives here and not in
the pure composer, whose job is only to say what the arithmetic produces.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date as _date, timezone
from datetime import datetime

from sqlalchemy.orm import Session

from app.constants import UNIFIED_CONSTITUENT_SLUGS
from app.models import UnifiedChartWeight, UnifiedReading, UnifiedReadingSong
from app.services.unified_chart import ComposedReading, compose, default_weights

logger = logging.getLogger(__name__)

# Below this, the day is not stored. See MINIMUM SOURCES above.
MIN_SOURCES = 2

# A recompose has "moved the numbers" when the degree shifts by more than this.
# Float noise in a weighted mean should not flag an editorial as stale, but a
# real re-reading should. 0.05 degrees is well under a tenth of a charge point.
_DEGREE_EPSILON = 0.05


def load_weights(db: Session) -> dict[str, float]:
    """The live source-weight vector, falling back to equal weights.

    Fail-soft by design: an unreachable or empty table must read as "equal
    weights", never as "no chart". A missing row for one constituent falls back
    to that constituent's default rather than dropping it to zero, because a
    zero would silently remove a whole source from the reading.

    THE ROLLBACK IS NOT OPTIONAL. On Postgres any failed statement aborts the
    whole transaction, so swallowing the error without rolling back leaves the
    session unusable and every later query in the same request dies with
    "current transaction is aborted". That turns a soft fallback into a hard
    failure one call downstream, which is worse than not catching at all. Found
    exactly this way: running the backfill before migration 155 had been applied
    made load_weights log its fallback and then the next query blew up anyway.
    """
    weights = default_weights()
    try:
        for row in db.query(UnifiedChartWeight).all():
            if row.slug in weights and row.weight is not None:
                weights[row.slug] = float(row.weight)
    except Exception:
        logger.exception("unified: weight table unreadable, using equal weights")
        try:
            db.rollback()
        except Exception:
            logger.exception("unified: rollback after weight-read failure failed")
    return weights


def weights_version(weights: dict[str, float]) -> str:
    """Short stable hash of a weight vector.

    Lets a published number be reproduced, and makes a re-weight visible as a
    version change rather than as an unexplained step in the series. Sorted so
    the hash depends on the values and not on dict ordering.
    """
    payload = ";".join(f"{s}={float(weights.get(s, 0.0)):.6f}"
                       for s in sorted(UNIFIED_CONSTITUENT_SLUGS))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _serialize(reading: ComposedReading) -> dict:
    return {
        "compass_degree": reading.compass_degree,
        "charge_level": reading.charge_level,
        "contamination_count": reading.contamination_count,
        "song_count": reading.song_count,
        "sources_included": json.dumps([c.as_dict() for c in reading.sources_included]),
        "sources_excluded": json.dumps(reading.sources_excluded),
        "source_count": len(reading.sources_included),
        "weights": json.dumps(reading.weights),
        "weights_version": weights_version(reading.weights),
    }


def store(db: Session, reading: ComposedReading, commit: bool = True) -> UnifiedReading:
    """Upsert one composed day. Caller owns the compose; this owns the row."""
    fields = _serialize(reading)
    row = db.query(UnifiedReading).filter(UnifiedReading.date == reading.date).one_or_none()

    if row is None:
        row = UnifiedReading(date=reading.date, **fields)
        db.add(row)
        db.flush()
    else:
        moved = (
            abs((row.compass_degree or 0.0) - reading.compass_degree) > _DEGREE_EPSILON
            or row.song_count != reading.song_count
            or row.source_count != fields["source_count"]
        )
        for k, v in fields.items():
            setattr(row, k, v)
        row.composed_at = datetime.now(timezone.utc)
        # Published prose written against numbers that have since moved is stale,
        # not wrong to keep. Flag it; never silently republish over it, and never
        # unpublish (that is the editorial's decision, not a background job's).
        if row.published and row.editorial and moved:
            row.editorial_stale = True
            logger.info("unified: %s recomposed after publish, editorial flagged stale",
                        reading.date)
        # Replace the ranked chart wholesale. It is small (~65 rows) and a
        # diff-and-patch would have to handle songs leaving the union, which is
        # more ways to be subtly wrong than a rewrite is worth.
        db.query(UnifiedReadingSong).filter(
            UnifiedReadingSong.reading_id == row.id
        ).delete(synchronize_session=False)
        db.flush()

    for i, s in enumerate(reading.songs, start=1):
        db.add(UnifiedReadingSong(
            reading_id=row.id,
            song_id=s.song_id,
            position=i,
            unified_weight=s.unified_weight,
            chart_count=s.chart_count,
            sources=json.dumps(s.sources),
        ))

    if commit:
        db.commit()
        db.refresh(row)
    return row


def recompose(db: Session, on_date: _date, commit: bool = True) -> UnifiedReading | None:
    """Compose `on_date` and store it. Returns None when the day does not qualify.

    Not qualifying is a normal state, not an error: too few constituents approved
    so far, or nothing published at all. Nothing is written in that case, and in
    particular an existing stored reading is LEFT ALONE rather than deleted, so a
    transient gap cannot erase a good day.
    """
    weights = load_weights(db)
    composed = compose(db, on_date, weights=weights)

    if composed is None or len(composed.sources_included) < MIN_SOURCES:
        have = 0 if composed is None else len(composed.sources_included)
        logger.info("unified: %s not composed (%d source(s), need %d)",
                    on_date, have, MIN_SOURCES)
        return None

    return store(db, composed, commit=commit)


def recompose_safe(db: Session, on_date: _date) -> UnifiedReading | None:
    """recompose() that can never raise.

    For the approval hook. The constituent charts are the primary artifact and
    the synthesis is downstream, so a failure here must not fail an approval.
    Rolls back its own partial work so the caller's session stays usable.
    """
    try:
        return recompose(db, on_date)
    except Exception:
        logger.exception("unified: recompose failed for %s", on_date)
        try:
            db.rollback()
        except Exception:
            logger.exception("unified: rollback failed for %s", on_date)
        return None


def publish(db: Session, on_date: _date, editorial: str,
            commit: bool = True) -> UnifiedReading | None:
    """Attach the editorial and publish. THE EDITORIAL IS THE GATE (scope 8.6).

    Writing the editorial is what makes a reading public, which is what keeps the
    prose and the number in lockstep: there is no window in which a published
    figure has no reading attached to it, and no way to publish a number the
    editorial was not written against. Clears `editorial_stale`, since the prose
    is now current with the figures by definition.

    Returns None when there is no composed reading for the date. Callers should
    treat that as "compose it first", not as an error.
    """
    row = db.query(UnifiedReading).filter(UnifiedReading.date == on_date).one_or_none()
    if row is None:
        return None
    row.editorial = editorial
    row.editorial_stale = False
    if not row.published:
        row.published = True
        row.published_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
        db.refresh(row)
    return row
