"""LEC rubric drift check -- cron entry point.

POST /api/admin/agent/cron/lec-rubric-drift-check polls LEC's published rubric
version and emails an admin alert when it has changed since RC last acknowledged
it (so RC's display-only tenets can be reconciled with LEC's scoring rubric).

Auth reuses the daily-reading cron lane (`X-Reading-Cron-Key` ==
RC_READING_CRON_KEY) so no new server secret is needed; it can ride the same
nightly lane as the reading + iTunes refresh + LEIT sweep. The orchestrator
lives in `services/lec_drift.py`.
"""

import logging

from fastapi import APIRouter, Depends

from app.auth import verify_reading_cron_key
from app.services import alerts
from app.services.lec_drift import run_lec_rubric_drift_check

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agent", tags=["lec-drift"])


@router.post("/cron/lec-rubric-drift-check", dependencies=[Depends(verify_reading_cron_key)])
async def cron_lec_rubric_drift_check():
    """Service endpoint for the LEC rubric drift check cron.

    Mirrors the daily-reading cron: gated by X-Reading-Cron-Key, returns a small
    summary. Emails an admin alert only when LEC's rubric version changed."""
    summary = await run_lec_rubric_drift_check()
    if summary.get("status") == "drifted":
        try:
            alerts.emit_lec_rubric_drift(
                old_version=summary["last_seen"],
                new_version=summary["current"],
            )
        except Exception:
            logger.exception("LEC rubric drift alert failed (non-fatal)")
    return summary
