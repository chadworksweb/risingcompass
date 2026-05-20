"""Admin Users list -- see who exists, who's Tier 1 vs Tier 2, and who's
been moderated. Read-only for now; moderation actions still live on
individual comments via /api/admin/comments/{id}/{action}.

  GET /api/admin/users  paginated list with filters

Per build plan 1.7 the eventual default is to show anon_id with a
deliberate "Reveal handle" click that writes to admin_reveal_log. Until
that audit table exists, handle is exposed directly here.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.auth import require_admin_session
from app.database import get_db
from app.models import AccountVerification, Comment, MisreadSubmission, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/users", tags=["users-admin"])


class UserAdminOut(BaseModel):
    id: int
    handle: Optional[str]
    anon_id: str
    tier: str
    status: str
    avatar_url: Optional[str]
    created_at: str
    suspended_until: Optional[str]
    banned_at: Optional[str]
    banned_reason: Optional[str]
    last_verification_provider: Optional[str]
    last_verification_status: Optional[str]
    last_verified_at: Optional[str]
    comment_count: int
    misread_count: int


class UserListOut(BaseModel):
    items: list[UserAdminOut]
    total: int


@router.get("", response_model=UserListOut)
def list_users(
    tier: Optional[str] = Query(default=None, description="handled | id_verified | pending"),
    status: Optional[str] = Query(default=None, description="active | suspended | banned"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    q = db.query(User)
    if tier in ("handled", "id_verified", "pending"):
        q = q.filter(User.tier == tier)
    if status in ("active", "suspended", "banned"):
        q = q.filter(User.status == status)

    total = q.with_entities(func.count(User.id)).scalar() or 0
    rows = q.order_by(desc(User.created_at)).offset(offset).limit(limit).all()

    if not rows:
        return UserListOut(items=[], total=total)

    user_ids = [u.id for u in rows]

    # Latest verification per user (any status). Group then re-fetch.
    av_latest_per_user: dict[int, AccountVerification] = {}
    for av in (
        db.query(AccountVerification)
        .filter(AccountVerification.user_id.in_(user_ids))
        .order_by(AccountVerification.created_at.desc())
        .all()
    ):
        av_latest_per_user.setdefault(av.user_id, av)

    # Per-user counts for the table.
    # Count every comment the user has ever posted -- live, withdrawn,
    # hidden, admin-deleted. The detail page breaks down by state.
    comment_counts = dict(
        db.query(Comment.author_id, func.count(Comment.id))
        .filter(Comment.author_id.in_(user_ids))
        .group_by(Comment.author_id)
        .all()
    )
    misread_counts = dict(
        db.query(MisreadSubmission.user_id, func.count(MisreadSubmission.id))
        .filter(MisreadSubmission.user_id.in_(user_ids))
        .group_by(MisreadSubmission.user_id)
        .all()
    )

    items: list[UserAdminOut] = []
    for u in rows:
        av = av_latest_per_user.get(u.id)
        items.append(UserAdminOut(
            id=u.id,
            handle=u.handle,
            anon_id=u.anon_id,
            tier=u.tier,
            status=u.status,
            avatar_url=u.avatar_url,
            created_at=u.created_at.isoformat() + "Z",
            suspended_until=(u.suspended_until.isoformat() + "Z") if u.suspended_until else None,
            banned_at=(u.banned_at.isoformat() + "Z") if u.banned_at else None,
            banned_reason=u.banned_reason,
            last_verification_provider=av.provider if av else None,
            last_verification_status=av.status if av else None,
            last_verified_at=(av.verified_at.isoformat() + "Z") if av and av.verified_at else None,
            comment_count=comment_counts.get(u.id, 0),
            misread_count=misread_counts.get(u.id, 0),
        ))
    return UserListOut(items=items, total=total)


# ---------- per-user detail ----------

class VerificationOut(BaseModel):
    id: int
    provider: str
    provider_reference: str
    status: str
    verified_at: Optional[str]
    failure_reason: Optional[str]
    created_at: str


class UserDetailOut(BaseModel):
    user: UserAdminOut
    verifications: list[VerificationOut]


def _resolve_user(db: Session, ident: str) -> User:
    """Look up a user by anon_id (preferred) or numeric id (fallback for
    internal callers). Raises 404 if neither matches."""
    u = db.query(User).filter(User.anon_id == ident).first()
    if u is None and ident.isdigit():
        u = db.query(User).filter(User.id == int(ident)).first()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.get("/{anon_id}", response_model=UserDetailOut)
def get_user_detail(
    anon_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)
    av_rows = (
        db.query(AccountVerification)
        .filter(AccountVerification.user_id == u.id)
        .order_by(AccountVerification.created_at.desc())
        .all()
    )
    latest = av_rows[0] if av_rows else None
    comment_count = (
        db.query(func.count(Comment.id))
        .filter(Comment.author_id == u.id)
        .scalar()
    ) or 0
    misread_count = (
        db.query(func.count(MisreadSubmission.id))
        .filter(MisreadSubmission.user_id == u.id)
        .scalar()
    ) or 0

    return UserDetailOut(
        user=UserAdminOut(
            id=u.id,
            handle=u.handle,
            anon_id=u.anon_id,
            tier=u.tier,
            status=u.status,
            avatar_url=u.avatar_url,
            created_at=u.created_at.isoformat() + "Z",
            suspended_until=(u.suspended_until.isoformat() + "Z") if u.suspended_until else None,
            banned_at=(u.banned_at.isoformat() + "Z") if u.banned_at else None,
            banned_reason=u.banned_reason,
            last_verification_provider=latest.provider if latest else None,
            last_verification_status=latest.status if latest else None,
            last_verified_at=(latest.verified_at.isoformat() + "Z") if latest and latest.verified_at else None,
            comment_count=comment_count,
            misread_count=misread_count,
        ),
        verifications=[
            VerificationOut(
                id=av.id,
                provider=av.provider,
                provider_reference=av.provider_reference,
                status=av.status,
                verified_at=(av.verified_at.isoformat() + "Z") if av.verified_at else None,
                failure_reason=av.failure_reason,
                created_at=av.created_at.isoformat() + "Z",
            )
            for av in av_rows
        ],
    )


class UserCommentOut(BaseModel):
    id: int
    target_type: str
    target_source: Optional[str]
    target_id: int
    parent_id: Optional[int]
    thread_root_id: int
    content: str
    state: str  # 'live' | 'withdrawn' | 'hidden' | 'deleted'
    hidden_reason: Optional[str]
    created_at: str
    withdrawn_at: Optional[str]
    deleted_at: Optional[str]
    hidden_at: Optional[str]


class UserCommentsListOut(BaseModel):
    items: list[UserCommentOut]
    total: int


def _comment_state(c: Comment) -> str:
    if c.deleted_at is not None:
        return "deleted"
    if c.hidden_at is not None:
        return "hidden"
    if c.withdrawn_at is not None:
        return "withdrawn"
    return "live"


@router.get("/{anon_id}/comments", response_model=UserCommentsListOut)
def get_user_comments(
    anon_id: str,
    state: Optional[str] = Query(default=None, description="live | withdrawn | hidden | deleted"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)

    q = db.query(Comment).filter(Comment.author_id == u.id)
    if state == "deleted":
        q = q.filter(Comment.deleted_at.is_not(None))
    elif state == "hidden":
        q = q.filter(Comment.hidden_at.is_not(None)).filter(Comment.deleted_at.is_(None))
    elif state == "withdrawn":
        q = q.filter(Comment.withdrawn_at.is_not(None)).filter(Comment.deleted_at.is_(None)).filter(Comment.hidden_at.is_(None))
    elif state == "live":
        q = q.filter(Comment.deleted_at.is_(None)).filter(Comment.hidden_at.is_(None)).filter(Comment.withdrawn_at.is_(None))

    total = q.with_entities(func.count(Comment.id)).scalar() or 0
    rows = q.order_by(Comment.created_at.desc()).offset(offset).limit(limit).all()

    return UserCommentsListOut(
        items=[
            UserCommentOut(
                id=c.id,
                target_type=c.target_type,
                target_source=c.target_source,
                target_id=c.target_id,
                parent_id=c.parent_id,
                thread_root_id=c.thread_root_id,
                content=c.content,
                state=_comment_state(c),
                hidden_reason=c.hidden_reason,
                created_at=c.created_at.isoformat() + "Z",
                withdrawn_at=(c.withdrawn_at.isoformat() + "Z") if c.withdrawn_at else None,
                deleted_at=(c.deleted_at.isoformat() + "Z") if c.deleted_at else None,
                hidden_at=(c.hidden_at.isoformat() + "Z") if c.hidden_at else None,
            )
            for c in rows
        ],
        total=total,
    )
