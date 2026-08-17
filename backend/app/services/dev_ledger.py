"""Dev Ledger ops -- the "dev side, exposed".

One pipeline drives changelog, roadmap, feature requests, and bug reports;
CalVer (YYYY.MM.DD[suffix]) is the spine. Walled from Motion Desk / Misread
Reports / Deliberation Chamber: those govern the tenet/framework layer, this is
the product/engineering layer.

item_type:
  feature -- community feature request
  bug     -- community bug report (the SOFTWARE broke; NOT a song misread)
  change  -- admin-authored roadmap/changelog work item

status:
  feature/bug: submitted -> triaging -> accepted -> in_progress -> shipped
                                      | -> declined | duplicate
  change:      planned -> in_progress -> shipped

Community submissions land PRIVATE (is_public=False); nothing a stranger typed
is served publicly until an admin publishes it. See
RISING-COMPASS-DEV-LEDGER-SCOPE.md.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import DevLedgerItem, DevLedgerVote, User

logger = logging.getLogger(__name__)

# Taxonomy
SUBMISSION_TYPES = {"feature", "bug"}            # community-submittable
ALL_ITEM_TYPES = SUBMISSION_TYPES | {"change"}   # change = admin-authored
VOTABLE_TYPES = {"feature", "bug"}

FEATURE_BUG_STATUSES = {
    "submitted", "triaging", "accepted", "in_progress", "shipped",
    "declined", "duplicate",
}
CHANGE_STATUSES = {"planned", "in_progress", "shipped"}
ALL_STATUSES = FEATURE_BUG_STATUSES | CHANGE_STATUSES

STAGES = {"now", "next", "later", "done"}  # 'done' renders in the Completed grid
SEVERITIES = {"low", "med", "high", "critical"}

# Field limits
MAX_TITLE = 200
MIN_TITLE = 5
MAX_BODY = 5000
MIN_BODY = 20
MAX_AREA = 40

# CalVer release id: YYYY.MM.DD with an optional lowercase letter suffix
# (2026.05.25, 2026.05.25b). Matches RC's same-day ship convention.
_CALVER_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}[a-z]?$")


def is_valid_calver(version: str) -> bool:
    return bool(version and _CALVER_RE.match(version))


# ---------------------------------------------------------------- submissions

def create_submission(
    db: Session,
    *,
    item_type: str,
    title: str,
    body: str,
    user: User,
    area: Optional[str] = None,
    severity: Optional[str] = None,
) -> DevLedgerItem:
    """Public feature request / bug report. Lands private (is_public=False),
    status='submitted'. Tier 1 (any active Clerk user) is enough."""
    if item_type not in SUBMISSION_TYPES:
        raise HTTPException(status_code=400, detail="item_type must be 'feature' or 'bug'")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Account not active")

    title = (title or "").strip()
    body = (body or "").strip()
    if not (MIN_TITLE <= len(title) <= MAX_TITLE):
        raise HTTPException(status_code=400, detail=f"Title must be {MIN_TITLE}-{MAX_TITLE} characters")
    if not (MIN_BODY <= len(body) <= MAX_BODY):
        raise HTTPException(status_code=400, detail=f"Details must be {MIN_BODY}-{MAX_BODY} characters")

    sev = None
    if item_type == "bug" and severity:
        if severity not in SEVERITIES:
            raise HTTPException(status_code=400, detail="Invalid severity")
        sev = severity

    item = DevLedgerItem(
        item_type=item_type,
        title=title,
        body=body,
        status="submitted",
        severity=sev,
        area=(area or "").strip()[:MAX_AREA] or None,
        submitted_by_user_id=user.id,
        is_public=False,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def create_change(
    db: Session,
    *,
    title: str,
    body: str,
    stage: Optional[str] = None,
    area: Optional[str] = None,
    admin_id: Optional[int] = None,
) -> DevLedgerItem:
    """Admin-authored roadmap/changelog work item ('change'). status='planned'."""
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="Title and body are required")
    if stage and stage not in STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage")

    item = DevLedgerItem(
        item_type="change",
        title=title,
        body=body,
        status="planned",
        stage=stage,
        area=(area or "").strip()[:MAX_AREA] or None,
        is_public=False,
        resolved_by_admin_id=admin_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# Map a caller's severity onto the Dev Ledger vocabulary ("medium" -> "med").
_SEVERITY_INBOUND = {"low": "low", "medium": "med", "med": "med", "high": "high", "critical": "critical"}


def create_internal_bug(
    db: Session,
    *,
    title: str,
    body: str,
    severity: Optional[str] = None,
    area: Optional[str] = None,
    admin_id: Optional[int] = None,
) -> DevLedgerItem:
    """Internal, system/admin/agent-authored bug -- e.g. a confirmed Faultline
    fault promoted into the tracked product/eng pipeline. Bypasses the public
    Clerk-user submission path (no submitter), lands private (is_public=False) at
    status='accepted' (it is already confirmed, not awaiting triage). The caller
    owns the title/body content; this function stays agnostic about its source so
    Dev Ledger never depends on Faultline."""
    title = (title or "").strip()[:MAX_TITLE] or "Untitled bug"
    body = (body or "").strip()[:MAX_BODY] or "(no detail)"
    sev = _SEVERITY_INBOUND.get((severity or "").lower()) if severity else None

    item = DevLedgerItem(
        item_type="bug",
        title=title,
        body=body,
        status="accepted",
        severity=sev,
        area=(area or "").strip()[:MAX_AREA] or None,
        is_public=False,
        resolved_by_admin_id=admin_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ---------------------------------------------------------------- voting

def toggle_vote(db: Session, item: DevLedgerItem, user: User) -> dict:
    """Toggle one user's vote on a feature/bug item; keep vote_count in sync."""
    if item.item_type not in VOTABLE_TYPES:
        raise HTTPException(status_code=400, detail="Only feature requests and bug reports are votable")

    existing = (
        db.query(DevLedgerVote)
        .filter(DevLedgerVote.item_id == item.id, DevLedgerVote.user_id == user.id)
        .first()
    )
    if existing:
        db.delete(existing)
        voted = False
    else:
        db.add(DevLedgerVote(item_id=item.id, user_id=user.id))
        voted = True
    db.flush()

    item.vote_count = (
        db.query(DevLedgerVote).filter(DevLedgerVote.item_id == item.id).count()
    )
    db.commit()
    return {"voted": voted, "vote_count": item.vote_count}


# ---------------------------------------------------------------- admin triage

def triage(
    db: Session,
    item: DevLedgerItem,
    *,
    admin_id: int,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    version: Optional[str] = None,
    severity: Optional[str] = None,
    area: Optional[str] = None,
    is_public: Optional[bool] = None,
    admin_note: Optional[str] = None,
    resolution: Optional[str] = None,
    duplicate_of_id: Optional[int] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
) -> DevLedgerItem:
    """Apply admin triage edits, including editing the public title/body. Sets
    published_at on first publish and shipped_at on transition to 'shipped'."""
    if title is not None:
        title = title.strip()
        if not (MIN_TITLE <= len(title) <= MAX_TITLE):
            raise HTTPException(status_code=400, detail=f"Title must be {MIN_TITLE}-{MAX_TITLE} characters")
        item.title = title

    if body is not None:
        body = body.strip()
        if not (MIN_BODY <= len(body) <= MAX_BODY):
            raise HTTPException(status_code=400, detail=f"Body must be {MIN_BODY}-{MAX_BODY} characters")
        item.body = body

    if status is not None:
        if status not in ALL_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        if item.item_type in SUBMISSION_TYPES and status not in FEATURE_BUG_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status for a feature/bug")
        if item.item_type == "change" and status not in CHANGE_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status for a change item")
        if status == "shipped" and item.shipped_at is None:
            item.shipped_at = datetime.now(timezone.utc)
        item.status = status

    if stage is not None:
        if stage and stage not in STAGES:
            raise HTTPException(status_code=400, detail="Invalid stage")
        item.stage = stage or None

    if version is not None:
        if version and not is_valid_calver(version):
            raise HTTPException(status_code=400, detail="version must be CalVer YYYY.MM.DD[suffix]")
        item.version = version or None

    if severity is not None:
        if severity and severity not in SEVERITIES:
            raise HTTPException(status_code=400, detail="Invalid severity")
        item.severity = severity or None

    if area is not None:
        item.area = (area or "").strip()[:MAX_AREA] or None

    if duplicate_of_id is not None:
        item.duplicate_of_id = duplicate_of_id or None

    if admin_note is not None:
        item.admin_note = admin_note or None

    if resolution is not None:
        item.resolution = resolution or None

    if is_public is not None:
        if is_public and item.published_at is None:
            item.published_at = datetime.now(timezone.utc)
        item.is_public = bool(is_public)

    item.resolved_by_admin_id = admin_id
    db.commit()
    db.refresh(item)
    return item


def delete_item(db: Session, item: DevLedgerItem) -> None:
    """Hard-delete an item (and its votes via cascade). Use for removing a
    mistaken or obsolete entry; prefer unpublish (is_public=False) to merely
    hide one from the public board while keeping the record."""
    db.delete(item)
    db.commit()


def cut_release(db: Session, *, item_ids: list[int], version: str, admin_id: int) -> list[DevLedgerItem]:
    """Stamp a set of items with a CalVer version, mark them shipped + public in
    one action -- this is what moves work onto the public changelog."""
    if not is_valid_calver(version):
        raise HTTPException(status_code=400, detail="version must be CalVer YYYY.MM.DD[suffix]")
    if not item_ids:
        raise HTTPException(status_code=400, detail="No items to release")

    now = datetime.now(timezone.utc)
    items = db.query(DevLedgerItem).filter(DevLedgerItem.id.in_(item_ids)).all()
    for item in items:
        item.version = version
        item.status = "shipped"
        if item.shipped_at is None:
            item.shipped_at = now
        if item.published_at is None:
            item.published_at = now
        item.is_public = True
        item.resolved_by_admin_id = admin_id
    db.commit()
    return items


def current_version(db: Session) -> Optional[str]:
    """Latest shipped CalVer release id (by shipped_at)."""
    row = (
        db.query(DevLedgerItem.version)
        .filter(DevLedgerItem.status == "shipped", DevLedgerItem.version.isnot(None))
        .order_by(DevLedgerItem.shipped_at.desc().nullslast())
        .first()
    )
    return row[0] if row else None


# ---------------------------------------------------------------- serialization

def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def item_to_dict(
    db: Session,
    item: DevLedgerItem,
    *,
    admin: bool = False,
    viewer_user_id: Optional[int] = None,
) -> dict:
    """Public-safe by default. admin=True adds the internal fields. Pass
    viewer_user_id to include whether that user has voted."""
    out: dict = {
        "id": item.id,
        "item_type": item.item_type,
        "title": item.title,
        "body": item.body,
        "status": item.status,
        "stage": item.stage,
        "version": item.version,
        "severity": item.severity,
        "area": item.area,
        "vote_count": item.vote_count,
        "resolution": item.resolution,
        "created_at": _iso(item.created_at),
        "published_at": _iso(item.published_at),
        "shipped_at": _iso(item.shipped_at),
    }

    # Submitter pseudonym (the handle IS the public identity, as in Motion Desk).
    if item.submitted_by_user_id:
        submitter = db.query(User).get(item.submitted_by_user_id)
        if submitter is not None:
            out["submitted_by_handle"] = submitter.handle
            out["submitted_by_anon_id"] = submitter.anon_id

    if viewer_user_id is not None and item.item_type in VOTABLE_TYPES:
        out["viewer_has_voted"] = (
            db.query(DevLedgerVote)
            .filter(DevLedgerVote.item_id == item.id, DevLedgerVote.user_id == viewer_user_id)
            .first()
            is not None
        )

    if admin:
        out["is_public"] = item.is_public
        out["admin_note"] = item.admin_note
        out["duplicate_of_id"] = item.duplicate_of_id
        out["submitted_by_user_id"] = item.submitted_by_user_id
        out["resolved_by_admin_id"] = item.resolved_by_admin_id

    return out
