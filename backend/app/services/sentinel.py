"""Sentinel Auditor Team -- triage service.

The shared mutation core for the mission-driven red-team program. Both the public
router (auditors filing findings) and the admin router (triaging them) drive
findings through this module, so the rules -- valid status transitions, the
acceptance stamp -- live in exactly one place. No HTTP, no auth here; callers pass
actor_ref. Mirrors services/faultline_triage.py + services/clutter.py.

This program is NOT gamified: no score, no rank, no leaderboard. The auditor-facing
metric is just `contribution()` (findings filed / confirmed). `points_awarded`
survives as an INTERNAL severity weight (admin-only signal of how serious a
confirmed finding was); it is stamped when a finding ENTERS `accepted` (from
accepted_severity, falling back to proposed_severity) and zeroed on reopen.

Finding lifecycle:

    new -> triaged -> investigating -> confirmed -> fixed -> accepted   (valid path)
    new -> rejected | duplicate | wont_fix                              (dismissals)
"""

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SentinelAuditor, SentinelFinding

logger = logging.getLogger(__name__)

# --- enums (UI offers these; the service validates against them) ------------
SEVERITIES = {"low", "medium", "high", "critical"}
SEVERITY_POINTS = {"low": 1, "medium": 3, "high": 8, "critical": 20}
CATEGORIES = {"algorithm", "methodology", "data", "ux", "other"}
FOCUS_AREAS = {"algorithm", "methodology", "data", "ux", "other"}
SCOPES = {"song", "general"}

# --- finding status lifecycle (mirrors faultline_triage) --------------------
ACTIVE_STATUSES = {"new", "triaged", "investigating", "confirmed", "fixed"}
TERMINAL_STATUSES = {"accepted", "rejected", "duplicate", "wont_fix"}
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES

class TransitionError(ValueError):
    """Invalid status transition or bad input. Routers map this to a 400 so the
    caller (admin or auditor) sees why the move was rejected."""


def allowed_targets(current: str) -> set[str]:
    """Statuses reachable from `current`. Active findings move freely among the
    working states and can close; terminal findings can only be reopened."""
    if current in ACTIVE_STATUSES:
        return (ACTIVE_STATUSES - {current}) | TERMINAL_STATUSES
    return {"triaged", "investigating"}  # reopen path out of a terminal state


# --- enrollment lookups -----------------------------------------------------

def get_or_none_auditor(db: Session, user_id: int) -> SentinelAuditor | None:
    return (db.query(SentinelAuditor)
            .filter(SentinelAuditor.user_id == user_id).first())


def is_approved_auditor(db: Session, user_id: int) -> bool:
    aud = get_or_none_auditor(db, user_id)
    return bool(aud and aud.status == "approved")


def contribution(db: Session, auditor_id: int) -> dict:
    """An auditor's plain contribution record -- NOT a score. How many findings
    they have filed and how many have been confirmed (status='accepted'). No
    points, no tier, no ranking: this program is stewardship, not a game."""
    filed = (db.query(func.count(SentinelFinding.id))
             .filter(SentinelFinding.auditor_id == auditor_id).scalar()) or 0
    confirmed = (db.query(func.count(SentinelFinding.id))
                 .filter(SentinelFinding.auditor_id == auditor_id,
                         SentinelFinding.status == "accepted").scalar()) or 0
    return {"filed": int(filed), "confirmed": int(confirmed)}


# --- finding creation -------------------------------------------------------

def record_finding(
    db: Session,
    *,
    auditor_id: int,
    scope: str,
    category: str,
    title: str,
    description: str,
    proposed_severity: str,
    song_id: int | None = None,
    evidence_url: str | None = None,
) -> SentinelFinding:
    """Insert one finding on the caller's session (caller owns the commit).
    Validates scope/category/severity and stamps `environment` so the admin
    queue keeps local-dev rows out of the prod worklist. Raises TransitionError
    on bad input."""
    if scope not in SCOPES:
        raise TransitionError(f"scope must be one of {sorted(SCOPES)}")
    if category not in CATEGORIES:
        raise TransitionError(f"category must be one of {sorted(CATEGORIES)}")
    if proposed_severity not in SEVERITIES:
        raise TransitionError(f"severity must be one of {sorted(SEVERITIES)}")
    if scope == "general":
        song_id = None  # general findings never carry a song
    title = (title or "").strip()[:200]
    description = (description or "").strip()
    if not title or not description:
        raise TransitionError("title and description are required")

    row = SentinelFinding(
        auditor_id=auditor_id,
        song_id=song_id,
        scope=scope,
        category=category,
        title=title,
        description=description,
        evidence_url=(evidence_url or "").strip()[:2000] or None,
        proposed_severity=proposed_severity,
        status="new",
        environment=settings.environment,
    )
    db.add(row)
    db.flush()
    return row


# --- triage (admin) ---------------------------------------------------------

def _stamp_accept(finding: SentinelFinding, *, actor_ref: str | None) -> None:
    from datetime import datetime, timezone
    sev = finding.accepted_severity or finding.proposed_severity
    finding.points_awarded = SEVERITY_POINTS.get(sev, 0)
    finding.reviewed_at = datetime.now(timezone.utc)
    finding.reviewed_by = actor_ref


def apply_status(
    db: Session,
    finding: SentinelFinding,
    to_status: str,
    *,
    actor_ref: str | None = None,
    disposition: str | None = None,
) -> SentinelFinding:
    """Validate + apply a status transition. Stamps the acceptance points snapshot
    on entry to `accepted`; zeroes it on reopen out of a terminal state. Caller
    owns the commit."""
    if to_status not in ALL_STATUSES:
        raise TransitionError(f"Unknown status '{to_status}'.")
    current = finding.status

    if disposition is not None:
        finding.disposition = disposition.strip() or None

    if to_status == current:
        return finding  # no movement (disposition edit may still have applied)

    if to_status not in allowed_targets(current):
        raise TransitionError(
            f"Cannot move {current} -> {to_status}. "
            f"Allowed from {current}: {sorted(allowed_targets(current))}."
        )

    finding.status = to_status
    if to_status == "accepted":
        _stamp_accept(finding, actor_ref=actor_ref)
    elif current in TERMINAL_STATUSES and to_status not in TERMINAL_STATUSES:
        # Reopened -- clear the acceptance stamp so reputation drops the points.
        finding.points_awarded = 0
        finding.reviewed_at = None
        finding.reviewed_by = None
    return finding


def set_severity(
    db: Session,
    finding: SentinelFinding,
    accepted_severity: str,
    *,
    actor_ref: str | None = None,
) -> SentinelFinding:
    """Admin override of the auditor's proposed severity. If the finding is
    already accepted, re-stamp its points from the new severity so the
    leaderboard stays consistent. Caller owns the commit."""
    if accepted_severity not in SEVERITIES:
        raise TransitionError(f"severity must be one of {sorted(SEVERITIES)}")
    finding.accepted_severity = accepted_severity
    if finding.status == "accepted":
        finding.points_awarded = SEVERITY_POINTS.get(accepted_severity, 0)
        finding.reviewed_by = actor_ref or finding.reviewed_by
    return finding
