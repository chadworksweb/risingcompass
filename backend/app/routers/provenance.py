"""Cron + admin endpoints for societal-prose provenance anchoring.

  POST /api/admin/provenance/sweep    -> anchor newly-sealed prose: append
                                         hash-only records to the public repo,
                                         OpenTimestamp the batch, commit + push.
  POST /api/admin/provenance/upgrade  -> confirm pending OTS proofs on Bitcoin.
  GET  /api/admin/provenance/status   -> anchor counts by ots_status (admin UI).

The two mutating endpoints are service-keyed (X-Provenance-Cron-Key), a
separate cron lane from backups / readings so a leak stays scoped. All three
are no-ops (status 'disabled'/'misconfigured') until the anchor repo + `ots`
CLI are provisioned and provenance_enabled is set -- see CLAUDE.md.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import verify_provenance_cron_key
from app.database import get_db
from app.routers.admin import verify_admin_key
from app.services import provenance_anchor

router = APIRouter(prefix="/api/admin/provenance", tags=["provenance"])


@router.post("/sweep", dependencies=[Depends(verify_provenance_cron_key)])
def sweep(db: Session = Depends(get_db)):
    return provenance_anchor.sweep(db)


@router.post("/upgrade", dependencies=[Depends(verify_provenance_cron_key)])
def upgrade(db: Session = Depends(get_db)):
    return provenance_anchor.upgrade(db)


@router.post("/health-check", dependencies=[Depends(verify_provenance_cron_key)])
def health_check(db: Session = Depends(get_db)):
    """Cron lane: evaluate health and email the admin only on a breach (the
    'provenance_health' activity alert, opt-in via the Alerts page). Returns the
    health payload + the breaches found so the cron run is self-documenting."""
    h = provenance_anchor.health(db)
    breaches = provenance_anchor.evaluate_breaches(db)
    if breaches:
        from app.services.alerts import emit_provenance_health
        emit_provenance_health(breaches=breaches, health=h)
    return {"health": h, "breaches": breaches, "alerted": bool(breaches)}


@router.get("/status", dependencies=[Depends(verify_admin_key)])
def status(db: Session = Depends(get_db)):
    """Full health payload for the admin Provenance page (counts, backlog,
    unpushed commits, oldest unconfirmed proof, last commit time)."""
    return provenance_anchor.health(db)
