"""Faultline -- admin read API (Phase 2).

Read-only endpoints behind the admin session that back the Site Admin > System >
Faultline panel: list captured fault signatures, drill into one (traceback,
recent occurrences, action history), and a small stats roll-up for the header.

Triage/lifecycle mutations (Phase 3) and the agent API (Phase 4) will be added
to this same router. Kept entirely separate from app logic -- this only reads
the error_* tables.
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ErrorSignature, ErrorOccurrence, ErrorAction
from app.routers.admin import verify_admin_key
from app.services import faultline_triage as triage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/faultline", tags=["faultline-admin"])

# Active = everything an operator still has to deal with (excludes terminal
# states), used as the default queue filter and for the "open" stat.
ACTIVE_STATUSES = ("new", "triaged", "investigating", "fix_proposed",
                   "fix_applied", "verifying", "regressed")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _loads(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s  # surface raw text rather than hide a malformed blob


def _sig_summary(s: ErrorSignature) -> dict:
    return {
        "id": s.id,
        "fingerprint": s.fingerprint,
        "exc_type": s.exc_type,
        "title": s.title,
        "component": s.component,
        "severity": s.severity,
        "area": s.area,
        "status": s.status,
        "environment": s.environment,
        "occurrence_count": s.occurrence_count,
        "first_seen_at": _iso(s.first_seen_at),
        "last_seen_at": _iso(s.last_seen_at),
        "assigned_to": s.assigned_to,
        "claimed_by": s.claimed_by,
        "claim_expires_at": _iso(s.claim_expires_at),
        "dev_ledger_item_id": s.dev_ledger_item_id,
        "muted": s.muted,
    }


@router.get("/signatures", dependencies=[Depends(verify_admin_key)])
def list_signatures(
    status: str | None = None,
    severity: str | None = None,
    area: str | None = None,
    environment: str | None = None,
    active_only: bool = False,
    include_muted: bool = False,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """List fault signatures, freshest first. Filters are all optional."""
    query = db.query(ErrorSignature)
    if status:
        query = query.filter(ErrorSignature.status == status)
    if active_only:
        query = query.filter(ErrorSignature.status.in_(ACTIVE_STATUSES))
    if severity:
        query = query.filter(ErrorSignature.severity == severity)
    if area:
        query = query.filter(ErrorSignature.area == area)
    if environment:
        query = query.filter(ErrorSignature.environment == environment)
    if not include_muted:
        query = query.filter(ErrorSignature.muted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter(
            ErrorSignature.title.ilike(like) | ErrorSignature.component.ilike(like)
        )
    rows = query.order_by(ErrorSignature.last_seen_at.desc()).limit(limit).all()
    return {"signatures": [_sig_summary(s) for s in rows]}


@router.get("/stats", dependencies=[Depends(verify_admin_key)])
def stats(db: Session = Depends(get_db)):
    """Header roll-up: open count, by-severity (active+unmuted), by-environment."""
    base = db.query(ErrorSignature).filter(ErrorSignature.muted.is_(False))
    open_count = base.filter(ErrorSignature.status.in_(ACTIVE_STATUSES)).count()

    by_sev = dict(
        db.query(ErrorSignature.severity, func.count(ErrorSignature.id))
        .filter(ErrorSignature.muted.is_(False))
        .filter(ErrorSignature.status.in_(ACTIVE_STATUSES))
        .group_by(ErrorSignature.severity)
        .all()
    )
    by_env = dict(
        db.query(ErrorSignature.environment, func.count(ErrorSignature.id))
        .filter(ErrorSignature.muted.is_(False))
        .filter(ErrorSignature.status.in_(ACTIVE_STATUSES))
        .group_by(ErrorSignature.environment)
        .all()
    )
    total = db.query(func.count(ErrorSignature.id)).scalar() or 0
    resolved = db.query(func.count(ErrorSignature.id)).filter(
        ErrorSignature.status == "resolved").scalar() or 0
    return {
        "open": open_count,
        "total": total,
        "resolved": resolved,
        "by_severity": {k: v for k, v in by_sev.items()},
        "by_environment": {k: v for k, v in by_env.items()},
    }


def build_detail(db: Session, s: ErrorSignature, occ_limit: int = 25) -> dict:
    """Full serialized detail for one signature: summary + resolution fields +
    traceback + recent occurrences + action log. Shared by the admin detail
    endpoint and the agent API so both speak the identical shape."""
    occ = (
        db.query(ErrorOccurrence)
        .filter(ErrorOccurrence.signature_id == s.id)
        .order_by(ErrorOccurrence.occurred_at.desc())
        .limit(occ_limit)
        .all()
    )
    actions = (
        db.query(ErrorAction)
        .filter(ErrorAction.signature_id == s.id)
        .order_by(ErrorAction.created_at.desc())
        .all()
    )
    detail = _sig_summary(s)
    detail.update({
        "resolution": s.resolution,
        "resolved_at": _iso(s.resolved_at),
        "resolved_by": s.resolved_by,
        "last_traceback": s.last_traceback,
        "last_context": _loads(s.last_context),
        "occurrences": [
            {
                "id": o.id,
                "occurred_at": _iso(o.occurred_at),
                "environment": o.environment,
                "context": _loads(o.context),
                "traceback": o.traceback,
            }
            for o in occ
        ],
        "actions": [
            {
                "id": a.id,
                "action_type": a.action_type,
                "actor_type": a.actor_type,
                "actor_ref": a.actor_ref,
                "from_status": a.from_status,
                "to_status": a.to_status,
                "note": a.note,
                "payload": _loads(a.payload),
                "created_at": _iso(a.created_at),
            }
            for a in actions
        ],
    })
    return detail


@router.get("/signatures/{sig_id}", dependencies=[Depends(verify_admin_key)])
def signature_detail(
    sig_id: int,
    occ_limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Full detail for one signature: traceback, recent occurrences, action log."""
    s = db.query(ErrorSignature).filter(ErrorSignature.id == sig_id).first()
    if s is None:
        raise HTTPException(404, "Signature not found")
    return build_detail(db, s, occ_limit)


# ---------------------------------------------------------------------------
# Phase 3 -- triage + lifecycle mutations (admin). All go through the shared
# faultline_triage service so the agent API (Phase 4) enforces identical rules.
# actor_type='admin', actor_ref=<admin username> for the audit trail.
# ---------------------------------------------------------------------------

class TriageIn(BaseModel):
    severity: str | None = None
    area: str | None = None
    assigned_to: str | None = None


class StatusIn(BaseModel):
    to_status: str
    note: str | None = None
    resolution: str | None = None


class MuteIn(BaseModel):
    muted: bool
    note: str | None = None


class ResolveIn(BaseModel):
    resolution: str | None = None


class CommentIn(BaseModel):
    note: str
    action_type: str = "comment"


def _load(db: Session, sig_id: int) -> ErrorSignature:
    sig = triage.get_signature(db, sig_id)
    if sig is None:
        raise HTTPException(404, "Signature not found")
    return sig


@router.post("/signatures/{sig_id}/triage")
def triage_signature(sig_id: int, body: TriageIn, db: Session = Depends(get_db),
                     admin=Depends(verify_admin_key)):
    sig = _load(db, sig_id)
    try:
        triage.apply_triage(db, sig, severity=body.severity, area=body.area,
                            assigned_to=body.assigned_to,
                            actor_type="admin", actor_ref=admin.username)
    except triage.TransitionError as e:
        raise HTTPException(400, str(e))
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/status")
def set_status(sig_id: int, body: StatusIn, db: Session = Depends(get_db),
               admin=Depends(verify_admin_key)):
    sig = _load(db, sig_id)
    try:
        triage.apply_status(db, sig, body.to_status, actor_type="admin",
                            actor_ref=admin.username, note=body.note,
                            resolution=body.resolution)
    except triage.TransitionError as e:
        raise HTTPException(400, str(e))
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/mute")
def mute_signature(sig_id: int, body: MuteIn, db: Session = Depends(get_db),
                   admin=Depends(verify_admin_key)):
    sig = _load(db, sig_id)
    triage.set_mute(db, sig, body.muted, actor_type="admin",
                    actor_ref=admin.username, note=body.note)
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/resolve")
def resolve_signature(sig_id: int, body: ResolveIn, db: Session = Depends(get_db),
                      admin=Depends(verify_admin_key)):
    sig = _load(db, sig_id)
    try:
        triage.apply_status(db, sig, "resolved", actor_type="admin",
                            actor_ref=admin.username, resolution=body.resolution)
    except triage.TransitionError as e:
        raise HTTPException(400, str(e))
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/comment")
def comment_signature(sig_id: int, body: CommentIn, db: Session = Depends(get_db),
                      admin=Depends(verify_admin_key)):
    sig = _load(db, sig_id)
    triage.add_comment(db, sig, body.note, actor_type="admin",
                       actor_ref=admin.username, action_type=body.action_type)
    return _sig_summary(sig)


class PromoteIn(BaseModel):
    severity: str | None = None
    area: str | None = None


@router.post("/signatures/{sig_id}/promote-dev-ledger")
def promote_signature(sig_id: int, body: PromoteIn, db: Session = Depends(get_db),
                      admin=Depends(verify_admin_key)):
    """Create (or return the existing) Dev Ledger bug for this fault and link it."""
    sig = _load(db, sig_id)
    item = triage.promote_to_dev_ledger(
        db, sig, severity=body.severity, area=body.area,
        actor_type="admin", actor_ref=admin.username, admin_id=admin.id,
    )
    return {"dev_ledger_item_id": item.id, "signature": _sig_summary(sig)}


@router.post("/prune", dependencies=[Depends(verify_admin_key)])
def prune(older_than_days: int = 30, keep_per_sig: int | None = None,
          db: Session = Depends(get_db)):
    """Manual retention run: trim occurrence history (keeps signatures)."""
    from app.services.faultline import prune_occurrences
    n = prune_occurrences(db, keep_per_sig=keep_per_sig, older_than_days=older_than_days)
    return {"pruned": n}
