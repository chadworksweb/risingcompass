"""Faultline -- triage + lifecycle service.

The shared mutation core for the error ledger. Both the admin panel (Phase 3)
and the agent API (Phase 4) drive faults through their fix lifecycle THROUGH
this module, so the rules (valid transitions, action audit, idempotency) live in
exactly one place. No HTTP, no auth here -- callers pass actor_type/actor_ref.

Lifecycle (see RISING-COMPASS-FAULTLINE-SCOPE.md S5):

    new -> triaged -> investigating -> fix_proposed -> fix_applied
         -> verifying -> resolved

  Side states: muted (orthogonal boolean flag), wont_fix, duplicate, and
  regressed (system-set: a new occurrence on a resolved fault reopens it).
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ErrorSignature, ErrorAction

logger = logging.getLogger(__name__)

DEFAULT_CLAIM_TTL_SECONDS = 900  # 15 min lease; agents heartbeat to extend.

WORKING_STATUSES = {"triaged", "investigating", "fix_proposed", "fix_applied", "verifying"}
ACTIVE_STATUSES = {"new", "regressed"} | WORKING_STATUSES
TERMINAL_STATUSES = {"resolved", "wont_fix", "duplicate"}
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES

SEVERITIES = {"low", "medium", "high", "critical"}

# Suggested triage areas (free-form; the UI offers these, the API accepts any).
SUGGESTED_AREAS = [
    "calibration", "billing", "album", "charts", "account",
    "admin", "api", "infra", "frontend", "other",
]


class TransitionError(ValueError):
    """Raised on an invalid status transition or bad input. Routers map this to
    a 400 so the caller (human or agent) sees why the move was rejected."""


class ClaimConflict(Exception):
    """Raised when an agent tries to claim/heartbeat/release a fault leased by a
    different worker. Routers map this to a 409 so a competing agent backs off."""


def allowed_targets(current: str) -> set[str]:
    """Statuses reachable from `current`. Active faults move freely among the
    working states and can close; terminal faults can only be reopened."""
    if current in ACTIVE_STATUSES:
        return WORKING_STATUSES | TERMINAL_STATUSES
    return {"investigating", "triaged"}  # reopen path out of a terminal state


def _find_idempotent(db: Session, sig_id: int, idempotency_key: str | None) -> ErrorAction | None:
    if not idempotency_key:
        return None
    return (
        db.query(ErrorAction)
        .filter(ErrorAction.signature_id == sig_id,
                ErrorAction.idempotency_key == idempotency_key)
        .first()
    )


def record_action(
    db: Session,
    sig: ErrorSignature,
    *,
    action_type: str,
    actor_type: str,
    actor_ref: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    payload: dict | None = None,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> ErrorAction:
    """Append one row to the action log (the phase-management timeline). Honors
    the (signature_id, idempotency_key) uniqueness so a re-fired agent call is a
    no-op that returns the original row."""
    existing = _find_idempotent(db, sig.id, idempotency_key)
    if existing is not None:
        return existing

    action = ErrorAction(
        signature_id=sig.id,
        action_type=action_type,
        actor_type=actor_type,
        actor_ref=(actor_ref or None),
        from_status=from_status,
        to_status=to_status,
        note=note,
        payload=json.dumps(payload, default=str) if payload is not None else None,
        idempotency_key=idempotency_key,
    )
    db.add(action)
    if commit:
        try:
            db.commit()
        except IntegrityError:
            # Concurrent same-key insert -- fall back to the existing row.
            db.rollback()
            existing = _find_idempotent(db, sig.id, idempotency_key)
            if existing is not None:
                return existing
            raise
        db.refresh(action)
    return action


def get_signature(db: Session, sig_id: int) -> ErrorSignature | None:
    return db.query(ErrorSignature).filter(ErrorSignature.id == sig_id).first()


def apply_status(
    db: Session,
    sig: ErrorSignature,
    to_status: str,
    *,
    actor_type: str,
    actor_ref: str | None = None,
    note: str | None = None,
    resolution: str | None = None,
    idempotency_key: str | None = None,
) -> ErrorSignature:
    """Validate + apply a status transition, recording a status_change action.
    A no-op (to_status == current) is allowed and idempotent."""
    if to_status not in ALL_STATUSES:
        raise TransitionError(f"Unknown status '{to_status}'.")

    # Idempotency short-circuit -- a replayed call returns current state.
    if _find_idempotent(db, sig.id, idempotency_key) is not None:
        return sig

    current = sig.status
    if to_status == current:
        # No movement, but still log the touch for the audit trail.
        record_action(db, sig, action_type="status_change", actor_type=actor_type,
                      actor_ref=actor_ref, from_status=current, to_status=to_status,
                      note=note or "(no-op)", idempotency_key=idempotency_key)
        return sig

    if to_status not in allowed_targets(current):
        raise TransitionError(
            f"Cannot move {current} -> {to_status}. "
            f"Allowed from {current}: {sorted(allowed_targets(current))}."
        )

    sig.status = to_status
    if to_status == "resolved":
        sig.resolved_at = datetime.utcnow()
        sig.resolved_by = actor_ref
        if resolution:
            sig.resolution = resolution
    elif current in TERMINAL_STATUSES and to_status not in TERMINAL_STATUSES:
        # Reopened -- clear the resolution stamp.
        sig.resolved_at = None
        sig.resolved_by = None

    record_action(db, sig, action_type="status_change", actor_type=actor_type,
                  actor_ref=actor_ref, from_status=current, to_status=to_status,
                  note=note, payload={"resolution": resolution} if resolution else None,
                  idempotency_key=idempotency_key)
    return sig


def apply_triage(
    db: Session,
    sig: ErrorSignature,
    *,
    severity: str | None = None,
    area: str | None = None,
    assigned_to: str | None = None,
    actor_type: str,
    actor_ref: str | None = None,
    idempotency_key: str | None = None,
) -> ErrorSignature:
    """Set severity / area / assignee. Records a triage action with the diff."""
    if _find_idempotent(db, sig.id, idempotency_key) is not None:
        return sig
    if severity is not None and severity not in SEVERITIES:
        raise TransitionError(f"Unknown severity '{severity}'. One of: {sorted(SEVERITIES)}.")

    changes = {}
    if severity is not None and severity != sig.severity:
        changes["severity"] = [sig.severity, severity]
        sig.severity = severity
    if area is not None and area != sig.area:
        changes["area"] = [sig.area, area]
        sig.area = area or None
    if assigned_to is not None and assigned_to != sig.assigned_to:
        changes["assigned_to"] = [sig.assigned_to, assigned_to]
        sig.assigned_to = assigned_to or None

    record_action(db, sig, action_type="triage", actor_type=actor_type,
                  actor_ref=actor_ref, note=None, payload={"changes": changes} or None,
                  idempotency_key=idempotency_key)

    # Alert seam: a fault just became critical. Fail-safe -- a delivery problem
    # must never break triage (send_alert is itself fire-and-forget).
    if changes.get("severity") and changes["severity"][1] == "critical":
        try:
            from app.services.alerts import emit_faultline_new_critical
            emit_faultline_new_critical(
                sig_id=sig.id, title=sig.title, component=sig.component,
                environment=sig.environment, occurrence_count=sig.occurrence_count,
            )
        except Exception:
            logger.exception("faultline_new_critical alert failed (non-fatal)")
    return sig


def set_mute(
    db: Session,
    sig: ErrorSignature,
    muted: bool,
    *,
    actor_type: str,
    actor_ref: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> ErrorSignature:
    """Mute/unmute -- orthogonal to status. Muted faults drop out of the default
    queue but keep capturing occurrences."""
    if _find_idempotent(db, sig.id, idempotency_key) is not None:
        return sig
    sig.muted = bool(muted)
    record_action(db, sig, action_type="comment" if note else "triage",
                  actor_type=actor_type, actor_ref=actor_ref,
                  note=note or ("muted" if muted else "unmuted"),
                  payload={"muted": bool(muted)}, idempotency_key=idempotency_key)
    return sig


def add_comment(
    db: Session,
    sig: ErrorSignature,
    note: str,
    *,
    actor_type: str,
    actor_ref: str | None = None,
    action_type: str = "comment",
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> ErrorAction:
    """Append a free-form note (or a typed agent action: diagnosis,
    proposed_fix, applied_fix, verification) to the timeline."""
    return record_action(db, sig, action_type=action_type, actor_type=actor_type,
                         actor_ref=actor_ref, note=note, payload=payload,
                         idempotency_key=idempotency_key)


# ---------------------------------------------------------------------------
# Claim / lease -- lets multiple agents work the ledger without colliding. A
# claim is a TTL lease; the holder heartbeats to extend it; an expired lease is
# freely reclaimable. NOT a status (orthogonal, like mute).
# ---------------------------------------------------------------------------

def _lease_active(sig: ErrorSignature, now: datetime) -> bool:
    return bool(sig.claimed_by and sig.claim_expires_at and sig.claim_expires_at > now)


def claim_signature(
    db: Session,
    sig: ErrorSignature,
    worker_id: str,
    *,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    idempotency_key: str | None = None,
) -> ErrorSignature:
    """Lease the fault to `worker_id`. Re-claiming your own active lease just
    extends it; claiming someone else's ACTIVE lease raises ClaimConflict. An
    expired lease is reclaimable by anyone."""
    now = datetime.utcnow()
    if _lease_active(sig, now) and sig.claimed_by != worker_id:
        raise ClaimConflict(
            f"Held by '{sig.claimed_by}' until {sig.claim_expires_at.isoformat()}."
        )
    sig.claimed_by = worker_id
    sig.claim_expires_at = now + timedelta(seconds=max(30, ttl_seconds))
    record_action(db, sig, action_type="claim", actor_type="agent",
                  actor_ref=worker_id,
                  payload={"ttl_seconds": ttl_seconds,
                           "expires_at": sig.claim_expires_at.isoformat()},
                  idempotency_key=idempotency_key)
    return sig


def heartbeat_claim(
    db: Session,
    sig: ErrorSignature,
    worker_id: str,
    *,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
) -> ErrorSignature:
    """Extend an active lease. The caller must currently hold it."""
    now = datetime.utcnow()
    if sig.claimed_by != worker_id or not _lease_active(sig, now):
        raise ClaimConflict("Lease not held by this worker (expired or reassigned).")
    sig.claim_expires_at = now + timedelta(seconds=max(30, ttl_seconds))
    db.commit()
    return sig


def release_claim(
    db: Session,
    sig: ErrorSignature,
    worker_id: str,
    *,
    force: bool = False,
) -> ErrorSignature:
    """Drop the lease. Only the holder may release unless force=True (admin)."""
    if not force and sig.claimed_by and sig.claimed_by != worker_id:
        raise ClaimConflict(f"Lease held by '{sig.claimed_by}', not '{worker_id}'.")
    sig.claimed_by = None
    sig.claim_expires_at = None
    record_action(db, sig, action_type="release", actor_type="agent",
                  actor_ref=worker_id)
    return sig


# ---------------------------------------------------------------------------
# Promote to Dev Ledger -- the ONE outbound seam. A confirmed fault becomes a
# tracked, public-pipeline bug. One-way: Faultline calls a thin Dev Ledger
# helper and stores the back-link; Dev Ledger never learns Faultline exists.
# Idempotent: a fault already linked to an item returns that item, no duplicate.
# ---------------------------------------------------------------------------

def promote_to_dev_ledger(
    db: Session,
    sig: ErrorSignature,
    *,
    severity: str | None = None,
    area: str | None = None,
    actor_type: str,
    actor_ref: str | None = None,
    admin_id: int | None = None,
    idempotency_key: str | None = None,
):
    """Create (or return the existing) Dev Ledger bug for this fault. Returns the
    DevLedgerItem."""
    from app.services import dev_ledger as devledger
    from app.models import DevLedgerItem

    if sig.dev_ledger_item_id:
        existing = db.query(DevLedgerItem).filter(
            DevLedgerItem.id == sig.dev_ledger_item_id).first()
        if existing is not None:
            return existing  # already promoted -- no duplicate

    sev = severity or sig.severity
    ar = area or sig.area
    tb = (sig.last_traceback or "").strip()
    tb_excerpt = tb[-2500:] if tb else "(no traceback captured)"
    body = (
        f"Promoted from Faultline signature #{sig.id} ({sig.environment}).\n\n"
        f"Component: {sig.component or 'unknown'}\n"
        f"Exception: {sig.exc_type or 'unknown'}\n"
        f"Occurrences: {sig.occurrence_count} "
        f"(first {sig.first_seen_at}, last {sig.last_seen_at})\n"
        f"Fingerprint: {sig.fingerprint}\n\n"
        f"Latest traceback:\n{tb_excerpt}"
    )
    item = devledger.create_internal_bug(
        db, title=sig.title, body=body, severity=sev, area=ar, admin_id=admin_id,
    )
    sig.dev_ledger_item_id = item.id
    record_action(db, sig, action_type="promote", actor_type=actor_type,
                  actor_ref=actor_ref, note=f"Promoted to Dev Ledger bug #{item.id}",
                  payload={"dev_ledger_item_id": item.id}, idempotency_key=idempotency_key)
    return item
