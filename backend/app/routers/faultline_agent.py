"""Faultline -- agent API (Phase 4).

The machine lane: a plugged-in agent (Claude Code session, Agent SDK worker, or
a /schedule routine) drives faults end-to-end through these endpoints. Same
lifecycle rules as the admin panel -- both go through `faultline_triage` -- so an
agent can never make a move a human couldn't.

Auth is a dedicated key lane (`X-Error-Agent-Key` == settings.rc_error_agent_key),
distinct from the admin session and every other cron key, so a leak stays scoped
to error triage. The whole router is INERT until the key is set: with no key
configured every endpoint returns 503, so it ships dark and arms only when you
put RC_ERROR_AGENT_KEY in the environment.

Concurrency: claim/heartbeat/release give each agent an exclusive lease on a
fault so two workers don't collide; expired leases are reclaimable. Every
mutating call takes an idempotency_key so a retried step is a no-op.

See RISING-COMPASS-FAULTLINE-SCOPE.md S6.
"""

import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ErrorSignature
from app.routers.faultline import _sig_summary, build_detail
from app.services import faultline_triage as triage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/faultline", tags=["faultline-agent"])

# Agent-typed action kinds the agent may append to the work log.
AGENT_ACTION_TYPES = {"comment", "diagnosis", "proposed_fix", "applied_fix", "verification"}

_TB_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>.+)')


def verify_error_agent_key(x_error_agent_key: str | None = Header(default=None)):
    """Dedicated agent-lane auth. 503 when the lane is unconfigured (ships dark),
    401 on a missing/wrong key."""
    configured = settings.rc_error_agent_key
    if not configured:
        raise HTTPException(503, "Faultline agent lane is not configured.")
    if not x_error_agent_key or x_error_agent_key != configured:
        raise HTTPException(401, "Invalid agent key.")
    return True


def _code_pointers(traceback: str | None) -> list[dict]:
    """Parse 'File ..., line N, in func' frames out of a traceback so the agent
    gets a ready list of code locations to open. Innermost last (as printed)."""
    if not traceback:
        return []
    out = []
    for m in _TB_FRAME.finditer(traceback):
        out.append({"file": m.group("file"), "line": int(m.group("line")), "func": m.group("func")})
    return out[-10:]


def _load(db: Session, sig_id: int) -> ErrorSignature:
    sig = triage.get_signature(db, sig_id)
    if sig is None:
        raise HTTPException(404, "Signature not found")
    return sig


# --- request bodies ---------------------------------------------------------

class ClaimIn(BaseModel):
    worker_id: str
    ttl_seconds: int = triage.DEFAULT_CLAIM_TTL_SECONDS
    idempotency_key: str | None = None


class WorkerIn(BaseModel):
    worker_id: str
    ttl_seconds: int = triage.DEFAULT_CLAIM_TTL_SECONDS


class ActionIn(BaseModel):
    action_type: str = "comment"
    note: str | None = None
    payload: dict | None = None
    agent_model: str | None = None
    idempotency_key: str | None = None


class AgentTriageIn(BaseModel):
    severity: str | None = None
    area: str | None = None
    assigned_to: str | None = None
    idempotency_key: str | None = None


class AgentStatusIn(BaseModel):
    to_status: str
    note: str | None = None
    resolution: str | None = None
    idempotency_key: str | None = None


# --- read -------------------------------------------------------------------

@router.get("/queue", dependencies=[Depends(verify_error_agent_key)])
def queue(
    environment: str | None = None,
    severity: str | None = None,
    area: str | None = None,
    include_claimed: bool = False,
    limit: int = 25,
    db: Session = Depends(get_db),
):
    """Prioritized worklist: active, unmuted faults, ranked critical-first then
    freshest then most-frequent. By default excludes faults under an active lease
    held by another worker (set include_claimed=true to see everything)."""
    limit = max(1, min(limit, 200))
    sev_rank = case(
        {"critical": 0, "high": 1, "medium": 2, "low": 3},
        value=ErrorSignature.severity, else_=4,
    )
    q = (
        db.query(ErrorSignature)
        .filter(ErrorSignature.muted.is_(False))
        .filter(ErrorSignature.status.in_(tuple(triage.ACTIVE_STATUSES)))
    )
    if environment:
        q = q.filter(ErrorSignature.environment == environment)
    if severity:
        q = q.filter(ErrorSignature.severity == severity)
    if area:
        q = q.filter(ErrorSignature.area == area)
    rows = (
        q.order_by(sev_rank.asc(),
                   ErrorSignature.last_seen_at.desc(),
                   ErrorSignature.occurrence_count.desc())
        .limit(limit * 2 if not include_claimed else limit)
        .all()
    )
    from datetime import datetime
    now = datetime.utcnow()
    out = []
    for s in rows:
        leased = triage._lease_active(s, now)
        if leased and not include_claimed:
            continue
        item = _sig_summary(s)
        item["leased"] = leased
        out.append(item)
        if len(out) >= limit:
            break
    return {"queue": out, "count": len(out)}


@router.get("/signatures/{sig_id}", dependencies=[Depends(verify_error_agent_key)])
def agent_detail(sig_id: int, db: Session = Depends(get_db)):
    """Full fault detail for the agent, plus parsed code pointers to open."""
    s = _load(db, sig_id)
    detail = build_detail(db, s, occ_limit=25)
    detail["code_pointers"] = _code_pointers(s.last_traceback)
    return detail


# --- claim / lease ----------------------------------------------------------

@router.post("/signatures/{sig_id}/claim", dependencies=[Depends(verify_error_agent_key)])
def claim(sig_id: int, body: ClaimIn, db: Session = Depends(get_db)):
    sig = _load(db, sig_id)
    try:
        triage.claim_signature(db, sig, body.worker_id, ttl_seconds=body.ttl_seconds,
                               idempotency_key=body.idempotency_key)
    except triage.ClaimConflict as e:
        raise HTTPException(409, str(e))
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/heartbeat", dependencies=[Depends(verify_error_agent_key)])
def heartbeat(sig_id: int, body: WorkerIn, db: Session = Depends(get_db)):
    sig = _load(db, sig_id)
    try:
        triage.heartbeat_claim(db, sig, body.worker_id, ttl_seconds=body.ttl_seconds)
    except triage.ClaimConflict as e:
        raise HTTPException(409, str(e))
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/release", dependencies=[Depends(verify_error_agent_key)])
def release(sig_id: int, body: WorkerIn, db: Session = Depends(get_db)):
    sig = _load(db, sig_id)
    try:
        triage.release_claim(db, sig, body.worker_id)
    except triage.ClaimConflict as e:
        raise HTTPException(409, str(e))
    return _sig_summary(sig)


# --- work -------------------------------------------------------------------

@router.post("/signatures/{sig_id}/actions", dependencies=[Depends(verify_error_agent_key)])
def append_action(sig_id: int, body: ActionIn, db: Session = Depends(get_db)):
    """Append a typed entry to the work log (diagnosis / proposed_fix /
    applied_fix / verification / comment). The agent's reasoning trail."""
    if body.action_type not in AGENT_ACTION_TYPES:
        raise HTTPException(400, f"action_type must be one of {sorted(AGENT_ACTION_TYPES)}")
    sig = _load(db, sig_id)
    payload = dict(body.payload or {})
    if body.agent_model:
        payload["agent_model"] = body.agent_model
    triage.add_comment(db, sig, body.note or "", actor_type="agent",
                       actor_ref=body.agent_model or "agent",
                       action_type=body.action_type,
                       payload=payload or None,
                       idempotency_key=body.idempotency_key)
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/triage", dependencies=[Depends(verify_error_agent_key)])
def agent_triage(sig_id: int, body: AgentTriageIn, db: Session = Depends(get_db)):
    sig = _load(db, sig_id)
    try:
        triage.apply_triage(db, sig, severity=body.severity, area=body.area,
                            assigned_to=body.assigned_to, actor_type="agent",
                            actor_ref=body.assigned_to or "agent",
                            idempotency_key=body.idempotency_key)
    except triage.TransitionError as e:
        raise HTTPException(400, str(e))
    return _sig_summary(sig)


@router.post("/signatures/{sig_id}/status", dependencies=[Depends(verify_error_agent_key)])
def agent_status(sig_id: int, body: AgentStatusIn, db: Session = Depends(get_db)):
    sig = _load(db, sig_id)
    try:
        triage.apply_status(db, sig, body.to_status, actor_type="agent",
                            actor_ref="agent", note=body.note,
                            resolution=body.resolution,
                            idempotency_key=body.idempotency_key)
    except triage.TransitionError as e:
        raise HTTPException(400, str(e))
    return _sig_summary(sig)


class AgentPromoteIn(BaseModel):
    severity: str | None = None
    area: str | None = None
    idempotency_key: str | None = None


@router.post("/signatures/{sig_id}/promote-dev-ledger", dependencies=[Depends(verify_error_agent_key)])
def agent_promote(sig_id: int, body: AgentPromoteIn, db: Session = Depends(get_db)):
    """Promote a confirmed fault to a tracked Dev Ledger bug (idempotent)."""
    sig = _load(db, sig_id)
    item = triage.promote_to_dev_ledger(
        db, sig, severity=body.severity, area=body.area,
        actor_type="agent", actor_ref="agent", idempotency_key=body.idempotency_key,
    )
    return {"dev_ledger_item_id": item.id, "signature": _sig_summary(sig)}


@router.post("/prune", dependencies=[Depends(verify_error_agent_key)])
def agent_prune(older_than_days: int = 30, db: Session = Depends(get_db)):
    """Retention run via the agent lane -- the endpoint a cron calls on a
    schedule. Trims occurrence tails; never deletes signatures."""
    from app.services.faultline import prune_occurrences
    return {"pruned": prune_occurrences(db, older_than_days=older_than_days)}
