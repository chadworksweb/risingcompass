"""Sentinel Auditor Team -- triage + reputation service.

The shared mutation core for the bug-bounty-style red-team program. Both the
public router (auditors filing findings) and the admin router (triaging them)
drive findings through this module, so the rules -- valid status transitions,
severity points, the acceptance-point snapshot -- live in exactly one place. No
HTTP, no auth here; callers pass actor_ref (the admin username or the auditor
handle). Mirrors services/faultline_triage.py + services/clutter.py.

Finding lifecycle:

    new -> triaged -> investigating -> confirmed -> fixed -> accepted   (valid path)
    new -> rejected | duplicate | wont_fix                              (dismissals)

Reputation is DERIVED: `points_awarded` is a point-in-time snapshot stamped when
a finding ENTERS `accepted` (from accepted_severity, falling back to
proposed_severity) and zeroed if it is later reopened. The leaderboard sums
points_awarded over accepted findings -- no running counter to keep in sync.
"""

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SentinelAuditor, SentinelFinding, User

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

# --- reputation tiers (derived from total accepted points) ------------------
# Ordered high -> low so tier_for_points returns the first one cleared.
TIER_THRESHOLDS = [
    (120, "Vanguard"),
    (40, "Sentinel"),
    (10, "Scout"),
    (0, "Recruit"),
]


class TransitionError(ValueError):
    """Invalid status transition or bad input. Routers map this to a 400 so the
    caller (admin or auditor) sees why the move was rejected."""


def allowed_targets(current: str) -> set[str]:
    """Statuses reachable from `current`. Active findings move freely among the
    working states and can close; terminal findings can only be reopened."""
    if current in ACTIVE_STATUSES:
        return (ACTIVE_STATUSES - {current}) | TERMINAL_STATUSES
    return {"triaged", "investigating"}  # reopen path out of a terminal state


def tier_for_points(points: int) -> str:
    for threshold, name in TIER_THRESHOLDS:
        if points >= threshold:
            return name
    return "Recruit"


# --- enrollment lookups -----------------------------------------------------

def get_or_none_auditor(db: Session, user_id: int) -> SentinelAuditor | None:
    return (db.query(SentinelAuditor)
            .filter(SentinelAuditor.user_id == user_id).first())


def is_approved_auditor(db: Session, user_id: int) -> bool:
    aud = get_or_none_auditor(db, user_id)
    return bool(aud and aud.status == "approved")


def reputation(db: Session, auditor_id: int) -> dict:
    """Derived reputation for one auditor: summed accepted points + tier +
    accepted-finding count. Reads only live (DB) state, no stored counter."""
    row = (db.query(func.coalesce(func.sum(SentinelFinding.points_awarded), 0),
                    func.count(SentinelFinding.id))
           .filter(SentinelFinding.auditor_id == auditor_id,
                   SentinelFinding.status == "accepted")
           .one())
    points = int(row[0] or 0)
    return {"points": points, "tier": tier_for_points(points),
            "accepted_count": int(row[1] or 0)}


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
    from datetime import datetime
    sev = finding.accepted_severity or finding.proposed_severity
    finding.points_awarded = SEVERITY_POINTS.get(sev, 0)
    finding.reviewed_at = datetime.utcnow()
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


# --- leaderboard (public once live) -----------------------------------------

def leaderboard(db: Session, *, environment: str = "prod", limit: int = 50) -> list[dict]:
    """Auditors ranked by summed accepted points. Reputation-only program: this
    is the visible reward. Joins the auditor's handle (denormed snapshot, falling
    back to the live users.handle)."""
    limit = max(1, min(limit, 200))
    rows = (
        db.query(
            SentinelFinding.auditor_id,
            func.coalesce(func.sum(SentinelFinding.points_awarded), 0).label("points"),
            func.count(SentinelFinding.id).label("accepted_count"),
        )
        .filter(SentinelFinding.status == "accepted",
                SentinelFinding.environment == environment)
        .group_by(SentinelFinding.auditor_id)
        .order_by(func.sum(SentinelFinding.points_awarded).desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    auditor_ids = [r.auditor_id for r in rows]
    auditors = {a.id: a for a in db.query(SentinelAuditor)
                .filter(SentinelAuditor.id.in_(auditor_ids)).all()}
    user_ids = [a.user_id for a in auditors.values()]
    handles = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            handles[u.id] = u.handle
    out = []
    for rank, r in enumerate(rows, start=1):
        aud = auditors.get(r.auditor_id)
        handle = None
        if aud:
            handle = handles.get(aud.user_id) or aud.handle_snapshot
        points = int(r.points or 0)
        out.append({
            "rank": rank,
            "handle": handle or "auditor",
            "points": points,
            "tier": tier_for_points(points),
            "accepted_count": int(r.accepted_count or 0),
        })
    return out
