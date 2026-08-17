"""Public Participation Phase 1.7 -- moderation queue + actions.

Cookie-session auth (require_admin_session) so this lives next to the
other /api/admin/* routers, not behind the Clerk JWT.

  GET    /api/admin/comments                       paginated queue with filters
  POST   /api/admin/comments/{id}/hide             manual hide w/ reason
  POST   /api/admin/comments/{id}/unhide           reverse a hide
  POST   /api/admin/comments/{id}/cooldown         24h posting suspension on author
  POST   /api/admin/comments/{id}/ban              close Clerk account + local ban
  POST   /api/admin/comments/{id}/dismiss-reports  mark all pending reports actioned

Every action writes a ModerationEvent row -- the audit log is the source
of truth for "what happened to this user" and "what did this admin do".
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.auth import require_admin_session
from app.database import get_db
from app.models import Comment, CommentReport, ModerationEvent, User
from app.services import clerk as clerk_svc
from app.services.feature_flags import is_comments_disabled, set_comments_disabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/comments", tags=["comments-admin"])

COOLDOWN_DURATION_HOURS = 24


# ---------- schemas ----------

class ReportSummaryOut(BaseModel):
    id: int
    reporter_anon_id: str
    reason: str
    notes: Optional[str]
    status: str
    created_at: str


class CommentAdminOut(BaseModel):
    id: int
    target_type: str
    target_source: Optional[str]
    target_id: int
    parent_id: Optional[int]
    thread_root_id: int
    content: str
    deleted: bool
    hidden: bool
    hidden_reason: Optional[str]
    created_at: str
    author_id: int
    author_handle: Optional[str]
    author_anon_id: str
    author_status: str
    pending_report_count: int
    reports: list[ReportSummaryOut]


class QueueOut(BaseModel):
    items: list[CommentAdminOut]
    total: int


class HideIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class CooldownIn(BaseModel):
    hours: int = Field(default=COOLDOWN_DURATION_HOURS, ge=1, le=24 * 30)
    reason: Optional[str] = Field(default=None, max_length=500)


class BanIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ActionOut(BaseModel):
    ok: bool
    clerk_ban_succeeded: Optional[bool] = None


# ---------- helpers ----------

def _serialize_admin(
    comment: Comment, author: User, reports: list[CommentReport]
) -> CommentAdminOut:
    pending = [r for r in reports if r.status == "pending"]
    return CommentAdminOut(
        id=comment.id,
        target_type=comment.target_type,
        target_source=comment.target_source,
        target_id=comment.target_id,
        parent_id=comment.parent_id,
        thread_root_id=comment.thread_root_id,
        content=comment.content,
        deleted=comment.deleted_at is not None,
        hidden=comment.hidden_at is not None,
        hidden_reason=comment.hidden_reason,
        created_at=comment.created_at.isoformat() + "Z",
        author_id=author.id,
        author_handle=author.handle,
        author_anon_id=author.anon_id,
        author_status=author.status,
        pending_report_count=len(pending),
        reports=[
            ReportSummaryOut(
                id=r.id,
                reporter_anon_id=_reporter_anon(r.reporter_id, _reporter_cache),
                reason=r.reason,
                notes=r.notes,
                status=r.status,
                created_at=r.created_at.isoformat() + "Z",
            )
            for r in reports
        ],
    )


_reporter_cache: dict[int, str] = {}


def _reporter_anon(reporter_id: int, cache: dict[int, str]) -> str:
    # Lazy because we serialize from a fresh request-scoped batch fetch.
    return cache.get(reporter_id, f"User-?{reporter_id}")


def _audit(
    db: Session,
    *,
    action: str,
    actor_admin_id: int,
    target_user_id: Optional[int],
    target_comment_id: Optional[int],
    reason: Optional[str],
    details: Optional[str] = None,
) -> None:
    db.add(ModerationEvent(
        action=action,
        actor_admin_id=actor_admin_id,
        target_user_id=target_user_id,
        target_comment_id=target_comment_id,
        reason=reason,
        details=details,
    ))


def _get_comment_or_404(db: Session, comment_id: int) -> Comment:
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


# ---------- dark switch (Discussion kill switch) ----------

class CommentsStatusOut(BaseModel):
    disabled: bool


class CommentsToggleIn(BaseModel):
    disabled: bool


@router.get("/status", response_model=CommentsStatusOut)
def comments_status(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    """Current Discussion open/closed state (fail-closed flag)."""
    return CommentsStatusOut(disabled=is_comments_disabled(db))


@router.post("/toggle", response_model=CommentsStatusOut)
def comments_toggle(
    payload: CommentsToggleIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    """Open / close public Discussion site-wide. Closed hides the widget on
    every song + artist page and 503s writes. Mirrors the Album Charger
    toggle; takes effect immediately (next page load / availability check)."""
    set_comments_disabled(db, payload.disabled)
    return CommentsStatusOut(disabled=is_comments_disabled(db))


# ---------- queue ----------

@router.get("", response_model=QueueOut)
def list_queue(
    request: Request,
    filter: str = Query(
        default="reported",
        description="reported | hidden | banned_authors | all",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    base_q = db.query(Comment).filter(Comment.deleted_at.is_(None))

    if filter == "reported":
        # Only comments with at least one pending report, ordered by most-recent report.
        sub = (
            db.query(
                CommentReport.comment_id,
                func.max(CommentReport.created_at).label("last_report"),
                func.count(CommentReport.id).label("pending"),
            )
            .filter(CommentReport.status == "pending")
            .group_by(CommentReport.comment_id)
            .subquery()
        )
        q = base_q.join(sub, Comment.id == sub.c.comment_id).order_by(desc(sub.c.last_report))
    elif filter == "hidden":
        q = base_q.filter(Comment.hidden_at.is_not(None)).order_by(desc(Comment.hidden_at))
    elif filter == "banned_authors":
        q = (
            base_q.join(User, User.id == Comment.author_id)
            .filter(User.status == "banned")
            .order_by(desc(Comment.created_at))
        )
    else:  # "all"
        q = base_q.order_by(desc(Comment.created_at))

    total = q.with_entities(func.count(Comment.id)).scalar() or 0
    rows = q.offset(offset).limit(limit).all()

    if not rows:
        return QueueOut(items=[], total=total)

    author_ids = {c.author_id for c in rows}
    authors = {u.id: u for u in db.query(User).filter(User.id.in_(author_ids)).all()}

    comment_ids = [c.id for c in rows]
    all_reports = (
        db.query(CommentReport)
        .filter(CommentReport.comment_id.in_(comment_ids))
        .order_by(CommentReport.created_at.desc())
        .all()
    )
    reports_by_comment: dict[int, list[CommentReport]] = {}
    reporter_ids = set()
    for r in all_reports:
        reports_by_comment.setdefault(r.comment_id, []).append(r)
        reporter_ids.add(r.reporter_id)

    # Resolve reporter anon_ids in one batch.
    _reporter_cache.clear()
    if reporter_ids:
        for u in db.query(User).filter(User.id.in_(reporter_ids)).all():
            _reporter_cache[u.id] = u.anon_id

    items = [
        _serialize_admin(c, authors[c.author_id], reports_by_comment.get(c.id, []))
        for c in rows
    ]
    return QueueOut(items=items, total=total)


# ---------- actions ----------

@router.post("/{comment_id}/hide", response_model=ActionOut)
def hide(
    comment_id: int,
    payload: HideIn,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    comment = _get_comment_or_404(db, comment_id)
    if comment.hidden_at is not None:
        return ActionOut(ok=True)
    comment.hidden_at = datetime.now(timezone.utc)
    comment.hidden_reason = payload.reason
    _audit(
        db,
        action="admin_hide",
        actor_admin_id=request.state.admin_user_id,
        target_user_id=comment.author_id,
        target_comment_id=comment.id,
        reason=payload.reason,
    )
    db.commit()
    return ActionOut(ok=True)


@router.post("/{comment_id}/unhide", response_model=ActionOut)
def unhide(
    comment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    comment = _get_comment_or_404(db, comment_id)
    if comment.hidden_at is None:
        return ActionOut(ok=True)
    comment.hidden_at = None
    comment.hidden_reason = None
    _audit(
        db,
        action="admin_unhide",
        actor_admin_id=request.state.admin_user_id,
        target_user_id=comment.author_id,
        target_comment_id=comment.id,
        reason=None,
    )
    db.commit()
    return ActionOut(ok=True)


@router.post("/{comment_id}/cooldown", response_model=ActionOut)
def cooldown(
    comment_id: int,
    payload: CooldownIn,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    comment = _get_comment_or_404(db, comment_id)
    author = db.query(User).filter(User.id == comment.author_id).first()
    if author is None:
        raise HTTPException(status_code=404, detail="Author not found")
    if author.status == "banned":
        raise HTTPException(status_code=409, detail="Cannot cooldown a banned account")
    until = datetime.now(timezone.utc) + timedelta(hours=payload.hours)
    author.suspended_until = until
    author.status = "suspended"
    _audit(
        db,
        action="cooldown",
        actor_admin_id=request.state.admin_user_id,
        target_user_id=author.id,
        target_comment_id=comment.id,
        reason=payload.reason,
        details=f"until={until.isoformat()}Z hours={payload.hours}",
    )
    db.commit()
    return ActionOut(ok=True)


@router.post("/{comment_id}/ban", response_model=ActionOut)
def ban(
    comment_id: int,
    payload: BanIn,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    comment = _get_comment_or_404(db, comment_id)
    author = db.query(User).filter(User.id == comment.author_id).first()
    if author is None:
        raise HTTPException(status_code=404, detail="Author not found")

    clerk_ok = clerk_svc.ban_clerk_user(author.clerk_user_id)
    author.status = "banned"
    author.banned_at = datetime.now(timezone.utc)
    author.banned_reason = payload.reason
    author.suspended_until = None
    _audit(
        db,
        action="ban",
        actor_admin_id=request.state.admin_user_id,
        target_user_id=author.id,
        target_comment_id=comment.id,
        reason=payload.reason,
        details=f"clerk_ban_succeeded={clerk_ok}",
    )
    db.commit()
    return ActionOut(ok=True, clerk_ban_succeeded=clerk_ok)


@router.post("/{comment_id}/delete", response_model=ActionOut)
def admin_delete(
    comment_id: int,
    payload: HideIn,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    """Admin moderation delete. Sets deleted_at; comment is removed from
    public view with no attribution and no reveal. Distinct from user
    Take Back (withdraw) which preserves attribution. The row stays for
    audit; nothing actually disappears from disk."""
    comment = _get_comment_or_404(db, comment_id)
    if comment.deleted_at is not None:
        return ActionOut(ok=True)
    comment.deleted_at = datetime.now(timezone.utc)
    _audit(
        db,
        action="admin_delete",
        actor_admin_id=request.state.admin_user_id,
        target_user_id=comment.author_id,
        target_comment_id=comment.id,
        reason=payload.reason,
    )
    db.commit()
    return ActionOut(ok=True)


@router.post("/{comment_id}/dismiss-reports", response_model=ActionOut)
def dismiss_reports(
    comment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    comment = _get_comment_or_404(db, comment_id)
    now = datetime.now(timezone.utc)
    updated = (
        db.query(CommentReport)
        .filter(CommentReport.comment_id == comment.id)
        .filter(CommentReport.status == "pending")
        .update({
            "status": "dismissed",
            "resolved_at": now,
            "resolved_by_admin_id": request.state.admin_user_id,
        }, synchronize_session=False)
    )
    _audit(
        db,
        action="dismiss_report",
        actor_admin_id=request.state.admin_user_id,
        target_user_id=comment.author_id,
        target_comment_id=comment.id,
        reason=None,
        details=f"dismissed={updated}",
    )
    db.commit()
    return ActionOut(ok=True)
