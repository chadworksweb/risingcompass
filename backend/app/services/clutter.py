"""LEIT clutter-control audit queue -- shared write helper.

One queue (`clutter_audits`), two feeders:
  - the Lyrical Charger non-commercial warning push-through (source='lc_push'),
  - the daily LEIT sweep agent (source='daily_sweep').

`record_clutter_finding` is the single insert path for both. It dedups to one
OPEN row per song (a re-sweep or repeat push won't stack duplicate open rows --
the partial unique index `uq_clutter_open_song` is the hard backstop) and stamps
`environment` so the admin queue can keep local-dev test rows out of the prod
worklist (local dev shares the prod DB via the tunnel, same as Faultline).
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models import ClutterAudit

logger = logging.getLogger(__name__)

VALID_SOURCES = {"lc_push", "daily_sweep"}
VALID_CATEGORIES = {"non_commercial", "gibberish", "unknown_person", "wrong_charger"}


def record_clutter_finding(
    *,
    song_id: int | None,
    source: str,
    category: str,
    reason: str | None = None,
    suggested_action: str | None = None,
    confidence: float | None = None,
    payload: dict | None = None,
    db=None,
) -> int | None:
    """Insert one clutter_audits row, deduped to one OPEN row per song.

    Returns the new row id, None if a finding for this song is already open
    (skipped), or None on a swallowed error in own-session mode.

    `db=None` -> own short-lived session, commit, swallow errors (telemetry-style,
    used by the LC hot path so a queue hiccup never fails a saved run).
    `db` passed -> insert on the caller's session; the caller owns the commit
    (used by the sweep so all findings land in one transaction).
    """
    if source not in VALID_SOURCES:
        logger.warning("record_clutter_finding: bad source %r", source)
        return None
    if category not in VALID_CATEGORIES:
        category = "non_commercial"

    payload_json = None
    if payload:
        try:
            payload_json = json.dumps(payload, default=str)[:4000]
        except Exception:
            logger.debug("clutter: swallowed in record_clutter_finding", exc_info=True)
            payload_json = None

    def _insert(session) -> int | None:
        # Pre-check the open dedup (the partial unique index is the hard
        # backstop; this avoids a noisy IntegrityError on the common path).
        if song_id is not None:
            existing = session.execute(
                text("SELECT 1 FROM clutter_audits "
                     "WHERE song_id = :s AND status = 'open' LIMIT 1"),
                {"s": song_id},
            ).scalar()
            if existing:
                return None
        row = ClutterAudit(
            song_id=song_id,
            source=source,
            category=category,
            reason=(reason or "")[:2000] or None,
            suggested_action=suggested_action,
            confidence=confidence,
            status="open",
            environment=settings.environment,
            payload_json=payload_json,
        )
        session.add(row)
        session.flush()
        return row.id

    if db is not None:
        # Caller owns the transaction. Let IntegrityError surface to the caller
        # so a concurrent open-dedup collision rolls back cleanly there.
        return _insert(db)

    session = SessionLocal()
    try:
        new_id = _insert(session)
        session.commit()
        return new_id
    except IntegrityError:
        session.rollback()
        return None  # an open finding already exists for this song
    except Exception:
        session.rollback()
        logger.exception("record_clutter_finding failed (song_id=%s)", song_id)
        return None
    finally:
        session.close()
