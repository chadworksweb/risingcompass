"""Public Participation Phase 1.3 -- Lobby comments API.

  GET    /api/comments             list threads for a target (anonymous OK)
  POST   /api/comments              create top-level (Tier 1 + handle)
  POST   /api/comments/{id}/reply   reply to a top-level (Tier 1 + handle)
  POST   /api/comments/{id}/report  flag a comment (Tier 1 + handle)
  DELETE /api/comments/{id}         soft-delete own comment

Posting endpoints require a Tier 1 account with a claimed handle. The
require_clerk_user dep handles JWT verification + provisioning; we
additionally check user.handle is not None before any write -- a freshly
provisioned user without a handle is effectively read-only.

Hidden + deleted comments still appear in the thread list with placeholder
content, because skipping them would shuffle thread structure. The author
sees the hide reason (no shadow-banning per build plan 1.7).
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.auth import require_clerk_user, optional_clerk_user
from app.database import get_db
from app.models import Comment, User
from app.services import comments as comments_svc
from app.services.feature_flags import is_comments_disabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comments", tags=["comments"])


# ---------- request/response schemas ----------

class CommentAuthorOut(BaseModel):
    handle: Optional[str]
    avatar_url: Optional[str]
    anon_id: str


class CommentOut(BaseModel):
    id: int
    parent_id: Optional[int]
    thread_root_id: int
    content: str  # placeholder when deleted/hidden
    # Withdrawn comments still ship the original content so the frontend
    # can offer a press-and-hold reveal -- the record is honest about what
    # was said. Admin-hidden comments do NOT populate this; that content
    # stays in the moderation queue, never on a public response.
    original_content: Optional[str] = None
    created_at: str
    edited_at: Optional[str]
    # Three distinct removal flags. UI renders / interacts differently for each.
    deleted: bool      # admin moderation delete -- never reveals
    withdrawn: bool    # user "take back" -- press-and-hold reveals original_content
    hidden: bool       # admin suppression pending review -- reason shown to author
    hidden_reason: Optional[str]  # only populated for the author
    author: CommentAuthorOut
    is_own: bool
    replies: list["CommentOut"] = []


class ThreadListOut(BaseModel):
    threads: list[CommentOut]
    total_threads: int
    total_comments: int


class CommentCreateIn(BaseModel):
    target_type: str
    target_source: Optional[str] = None
    target_id: int
    content: str = Field(..., min_length=1, max_length=5000)


class CommentReplyIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class CommentReportIn(BaseModel):
    reason: str
    notes: Optional[str] = Field(default=None, max_length=1000)


# ---------- helpers ----------

def _require_handle(user: User) -> User:
    if user.handle is None:
        raise HTTPException(
            status_code=409,
            detail="Pick a handle on your account page before posting.",
        )
    return user


def _serialize(
    comment: Comment,
    author: User,
    current_user: Optional[User],
) -> CommentOut:
    is_own = current_user is not None and current_user.id == comment.author_id
    is_withdrawn = comment.withdrawn_at is not None
    is_deleted = comment.deleted_at is not None  # admin moderation delete
    is_hidden = comment.hidden_at is not None

    # Precedence (most-aggressive wins on display):
    #   admin delete > admin hide > user withdraw > live content.
    # This means an admin-deleted comment never reveals its content even if
    # the user had previously withdrawn it.
    if is_deleted:
        body = "[removed by moderator]"
        original_content = None
        keep_attribution = False
    elif is_hidden:
        body = "[hidden -- under review]"
        original_content = None
        keep_attribution = True
    elif is_withdrawn:
        handle_label = f"@{author.handle}" if author.handle else "this user"
        body = f"[withdrawn by {handle_label}]"
        # Withdrawn rows ship the original so the frontend can offer the
        # press-and-hold reveal. The record stays honest.
        original_content = comment.content
        keep_attribution = True
    else:
        body = comment.content
        original_content = None
        keep_attribution = True

    hidden_reason = None
    if is_hidden and is_own and comment.hidden_reason:
        hidden_reason = comment.hidden_reason

    return CommentOut(
        id=comment.id,
        parent_id=comment.parent_id,
        thread_root_id=comment.thread_root_id,
        content=body,
        original_content=original_content,
        created_at=comment.created_at.isoformat() + "Z",
        edited_at=(comment.edited_at.isoformat() + "Z") if comment.edited_at else None,
        deleted=is_deleted,
        withdrawn=is_withdrawn,
        hidden=is_hidden,
        hidden_reason=hidden_reason,
        author=CommentAuthorOut(
            handle=author.handle if keep_attribution else None,
            avatar_url=author.avatar_url if keep_attribution and not is_withdrawn else None,
            anon_id=author.anon_id,
        ),
        is_own=is_own,
        replies=[],
    )


def _validate_target_query(target_type: str, target_source: Optional[str]) -> None:
    comments_svc.validate_target(target_type, target_source)


def _check_comments_enabled_or_503(db: Session) -> None:
    """Block writes while Discussion is closed (the fail-closed dark switch).
    Defense-in-depth: the widget already hides itself via /availability."""
    if is_comments_disabled(db):
        raise HTTPException(status_code=503, detail="Discussion is closed right now.")


# ---------- endpoints ----------

class AvailabilityOut(BaseModel):
    enabled: bool


@router.get("/availability", response_model=AvailabilityOut)
def availability(db: Session = Depends(get_db)):
    """Public, auth-free gate for the Discussion widget (mirrors the Album
    Charger config check). When enabled=false the frontend leaves the widget
    hidden (dark). Fail-closed: absent flag => disabled."""
    return AvailabilityOut(enabled=not is_comments_disabled(db))

@router.get("", response_model=ThreadListOut)
def list_threads(
    target_type: str = Query(...),
    target_id: int = Query(...),
    target_source: Optional[str] = Query(default=None),
    sort: str = Query(default="new"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    me: Optional[User] = Depends(optional_clerk_user),
):
    """Anonymous read. Returns top-level threads (each with their replies
    nested) for the target. Sort options:

      new           -- thread roots by created_at DESC (default)
      most_replied  -- thread roots by reply count DESC
      most_active   -- thread roots by max(reply.created_at) DESC
    """
    _validate_target_query(target_type, target_source)

    # Dark switch: when Discussion is closed, read as empty (the widget hides
    # itself via /availability; this keeps direct reads consistent).
    if is_comments_disabled(db):
        return ThreadListOut(threads=[], total_threads=0, total_comments=0)

    target_filter = and_(
        Comment.target_type == target_type,
        Comment.target_id == target_id,
        Comment.target_source == target_source,
    )

    # Total tallies for the UI header (count includes deleted/hidden, like Reddit).
    total_threads = (
        db.query(func.count(Comment.id))
        .filter(target_filter)
        .filter(Comment.parent_id.is_(None))
        .scalar()
    )
    total_comments = (
        db.query(func.count(Comment.id))
        .filter(target_filter)
        .scalar()
    )

    roots_q = db.query(Comment).filter(target_filter).filter(Comment.parent_id.is_(None))

    if sort == "most_replied":
        reply_count = (
            db.query(Comment.thread_root_id, func.count(Comment.id).label("c"))
            .filter(target_filter)
            .filter(Comment.parent_id.is_not(None))
            .group_by(Comment.thread_root_id)
            .subquery()
        )
        roots = (
            roots_q.outerjoin(reply_count, Comment.id == reply_count.c.thread_root_id)
            .order_by(func.coalesce(reply_count.c.c, 0).desc(), Comment.created_at.desc())
            .offset(offset).limit(limit).all()
        )
    elif sort == "most_active":
        last_activity = (
            db.query(
                Comment.thread_root_id,
                func.max(Comment.created_at).label("last"),
            )
            .filter(target_filter)
            .group_by(Comment.thread_root_id)
            .subquery()
        )
        roots = (
            roots_q.outerjoin(last_activity, Comment.id == last_activity.c.thread_root_id)
            .order_by(func.coalesce(last_activity.c.last, Comment.created_at).desc())
            .offset(offset).limit(limit).all()
        )
    else:  # "new" (default)
        roots = roots_q.order_by(Comment.created_at.desc()).offset(offset).limit(limit).all()

    if not roots:
        return ThreadListOut(threads=[], total_threads=total_threads, total_comments=total_comments)

    root_ids = [r.id for r in roots]
    replies = (
        db.query(Comment)
        .filter(Comment.thread_root_id.in_(root_ids))
        .filter(Comment.parent_id.is_not(None))
        .order_by(Comment.created_at.asc())
        .all()
    )

    # Batch-fetch authors so we don't N+1.
    author_ids = {c.author_id for c in roots} | {c.author_id for c in replies}
    authors = {
        u.id: u
        for u in db.query(User).filter(User.id.in_(author_ids)).all()
    }

    threads: list[CommentOut] = []
    by_root: dict[int, CommentOut] = {}
    for r in roots:
        node = _serialize(r, authors[r.author_id], me)
        by_root[r.id] = node
        threads.append(node)
    for rep in replies:
        node = _serialize(rep, authors[rep.author_id], me)
        parent = by_root.get(rep.thread_root_id)
        if parent is not None:
            parent.replies.append(node)

    return ThreadListOut(
        threads=threads,
        total_threads=total_threads,
        total_comments=total_comments,
    )


@router.post("", response_model=CommentOut, status_code=201)
def post_comment(
    payload: CommentCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    _check_comments_enabled_or_503(db)
    _require_handle(user)
    comment = comments_svc.create_top_level(
        db=db,
        user=user,
        target_type=payload.target_type,
        target_source=payload.target_source,
        target_id=payload.target_id,
        raw_content=payload.content,
    )
    return _serialize(comment, user, user)


@router.post("/{comment_id}/reply", response_model=CommentOut, status_code=201)
def post_reply(
    comment_id: int,
    payload: CommentReplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    _check_comments_enabled_or_503(db)
    _require_handle(user)
    reply = comments_svc.create_reply(db, user, comment_id, payload.content)
    return _serialize(reply, user, user)


class ReportOut(BaseModel):
    ok: bool
    auto_hidden: bool


@router.post("/{comment_id}/report", response_model=ReportOut)
def post_report(
    comment_id: int,
    payload: CommentReportIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    _check_comments_enabled_or_503(db)
    _require_handle(user)
    _, auto_hidden = comments_svc.file_report(
        db=db,
        reporter=user,
        comment_id=comment_id,
        reason=payload.reason,
        notes=payload.notes,
    )
    return ReportOut(ok=True, auto_hidden=auto_hidden)


@router.delete("/{comment_id}", response_model=CommentOut)
def withdraw_own(
    comment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    """User Take Back. Sets withdrawn_at, preserves attribution. This is
    user sovereignty -- distinct from admin delete (which removes the
    comment from public view entirely)."""
    _require_handle(user)
    comment = comments_svc.withdraw_own(db, user, comment_id)
    author = db.query(User).filter(User.id == comment.author_id).first()
    return _serialize(comment, author, user)


CommentOut.model_rebuild()
