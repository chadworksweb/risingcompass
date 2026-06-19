"""LEC rubric drift notifier.

Post-decoupling, LEC owns the scoring rubric and RC's `core.json` is display /
governance only (the public tenets page, motion validation, the change
detector) -- it no longer drives any scoring read. So the two representations
can silently diverge. This polls LEC's published rubric version
(`GET /api/rubric` -> `version`) and, when it changes from the last-seen value,
emits an admin alert so RC's displayed tenets can be reconciled with LEC.

RC-only -- needs no LEC change. A full tenet-by-tenet structural compare would
need a structured-tenets endpoint on LEC (deferred with the satire work); this
ships the version-change signal, the achievable form of the plan's drift check.

Fail-soft: an unreachable LEC is reported as `unreachable`, never as drift, so a
transient blip can't raise a false alarm or repin the stored version.
"""

import logging

from app.database import SessionLocal
from app.models import SystemFlag
from app.services import lec_client

logger = logging.getLogger(__name__)

# system_flags row holding the last LEC rubric version RC has acknowledged.
LAST_SEEN_KEY = "lec_rubric_version.last_seen"


def _get(db, key: str) -> str | None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    return row.value if row else None


def _set(db, key: str, value: str) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemFlag(key=key, value=value))


async def run_lec_rubric_drift_check() -> dict:
    """Poll LEC's published rubric version; repin + flag drift on change.

    Returns a small summary the cron router reads to decide whether to alert:
      {status: unreachable|initialized|unchanged|drifted, current, last_seen}
    For `drifted`, `last_seen` is the OLD version and `current` is the new one.
    Repins `last_seen` to `current` on first run and on drift (so the alert
    fires once per change, not every poll)."""
    current = await lec_client.fetch_rubric_version()

    db = SessionLocal()
    try:
        last_seen = _get(db, LAST_SEEN_KEY)

        if current is None:
            logger.warning("LEC rubric drift check: /api/rubric unreachable; "
                           "last_seen=%s", last_seen)
            return {"status": "unreachable", "current": None, "last_seen": last_seen}

        if last_seen is None:
            _set(db, LAST_SEEN_KEY, current)
            db.commit()
            logger.info("LEC rubric drift check: first run, pinned version %s", current)
            return {"status": "initialized", "current": current, "last_seen": None}

        if current != last_seen:
            logger.info("LEC rubric drift detected: %s -> %s", last_seen, current)
            _set(db, LAST_SEEN_KEY, current)
            db.commit()
            return {"status": "drifted", "current": current, "last_seen": last_seen}

        return {"status": "unchanged", "current": current, "last_seen": last_seen}
    finally:
        db.close()
