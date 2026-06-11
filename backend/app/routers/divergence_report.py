"""Calibrator v3 feedback organ -- cron + admin entry points.

POST /api/admin/agent/cron/divergence-report runs one report pass: songs whose
audience-vibe pushes or clustered misread reports systematically oppose the
stored verdict, ranked. The organ NOMINATES re-reads; it never moves a charge.
Reports zero rows until there is traffic -- that is the design, not a bug.

Auth reuses the daily-reading cron lane (X-Reading-Cron-Key ==
RC_READING_CRON_KEY), like the LEIT clutter sweep. The orchestrator lives in
services/agents/divergence_report.py. GET /api/admin/divergence-report serves
the same report on demand to a logged-in admin.
"""

import logging

from fastapi import APIRouter, Depends

from app.auth import require_admin_session, verify_reading_cron_key
from app.services.agents.divergence_report import run_divergence_report
from app.services import alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["divergence-report"])


@router.post("/agent/cron/divergence-report", dependencies=[Depends(verify_reading_cron_key)])
async def cron_divergence_report():
    """Service endpoint for the divergence-report cron (weekly suggested).

    Mirrors the leit-sweep cron: gated by X-Reading-Cron-Key, returns the
    summary. Emails Chad a digest only when anything was nominated, so the
    zero-traffic era stays silent."""
    summary = run_divergence_report()
    if summary.get("nominated"):
        try:
            alerts.emit_divergence_digest(
                scanned=summary["scanned"],
                nominations=summary["nominations"],
            )
        except Exception:
            logger.exception("divergence digest alert failed (non-fatal)")
    return summary


@router.get("/divergence-report", dependencies=[Depends(require_admin_session)])
async def divergence_report_view():
    """On-demand report for a logged-in admin (session cookie). Same data as
    the cron, no email."""
    return run_divergence_report(trigger="admin")
