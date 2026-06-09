"""LEIT daily clutter sweep -- cron entry point.

POST /api/admin/agent/cron/leit-sweep runs one sweep pass: it scans recent
public Lyrical Charger additions for clutter (gibberish, unknown non-artists,
content that belongs on the Creative/Curio Charger) and queues each finding in
`clutter_audits` for human audit. Flag-only -- it never changes the live site.

Auth reuses the daily-reading cron lane (`X-Reading-Cron-Key` ==
RC_READING_CRON_KEY) so no new server secret is needed; the sweep runs in the
same nightly cron lane as the reading + iTunes refresh. The orchestrator lives
in `services/agents/leit_sweep.py`; an admin-triggered run (session cookie) is
exposed from the clutter audit-queue admin router.
"""

import logging

from fastapi import APIRouter, Depends

from app.auth import verify_reading_cron_key
from app.services.agents.leit_sweep import run_leit_sweep
from app.services import alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agent", tags=["leit-sweep"])


@router.post("/cron/leit-sweep", dependencies=[Depends(verify_reading_cron_key)])
async def cron_leit_sweep():
    """Service endpoint for the daily LEIT clutter sweep cron.

    Mirrors the daily-reading cron: gated by X-Reading-Cron-Key, returns a small
    summary. Emails Chad a digest when anything new was flagged."""
    summary = await run_leit_sweep()
    if summary.get("flagged"):
        try:
            alerts.emit_leit_sweep_digest(
                scanned=summary["scanned"],
                findings=summary["findings"],
            )
        except Exception:
            logger.exception("leit_sweep digest alert failed (non-fatal)")
    return summary
