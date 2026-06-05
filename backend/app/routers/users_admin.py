"""Admin Users -- see who exists, and everything a user does.

The chosen handle IS the pseudonym, so admin sees it directly; there is no
real-name masking and no "reveal handle" audit gate (the old build-plan-1.7
anon_id reveal scheme was dropped). anon_id remains only as the stable,
non-sequential public ID and as the URL key for the detail page.

  GET /api/admin/users                      paginated list (filters + handle search)
  GET /api/admin/users/{ident}              profile + billing + counts
  GET /api/admin/users/{ident}/comments     lobby comments by state
  GET /api/admin/users/{ident}/calibrations Lyrical Charger runs (signed-in)
  GET /api/admin/users/{ident}/submissions  misread / satirical reports
  GET /api/admin/users/{ident}/motions       motions filed + chamber arguments
  GET /api/admin/users/{ident}/payments      billing summary + credit ledger
  GET /api/admin/users/{ident}/activity      unified reverse-chron timeline

{ident} resolves by anon_id, then handle, then numeric id.

Moderation actions still live on individual comments via
/api/admin/comments/{id}/{action}.
"""

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from app import billing_config
from app.auth import require_admin_session
from app.database import get_db
from app.services import billing as billing_svc
from app.services.feature_flags import lyrical_charger_free_daily_charges
from app.models import (
    AccountVerification,
    Comment,
    CreditLedger,
    MisreadSubmission,
    Motion,
    MotionArgument,
    User,
    UserCalibration,
)


def _posthog_person_url(clerk_user_id: Optional[str]) -> Optional[str]:
    """Deep link to a user's PostHog person profile. distinct_id is the Clerk
    id (auth.js calls posthog.identify(clerk.user.id, ...)). None unless
    POSTHOG_PROJECT_ID is set, so the admin button hides until configured."""
    project = os.environ.get("POSTHOG_PROJECT_ID")
    if not project or not clerk_user_id:
        return None
    ui_host = os.environ.get("POSTHOG_UI_HOST", "https://us.posthog.com").rstrip("/")
    return f"{ui_host}/project/{project}/person/{clerk_user_id}"

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
    # Detail-only enrichments (omitted / defaulted in the list view).
    calibration_count: int = 0
    motion_count: int = 0
    argument_count: int = 0
    subscription_tier: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_period_end: Optional[str] = None
    allowance_credits: int = 0
    purchased_credits: int = 0
    comp_unlimited: bool = False
    has_stripe_customer: bool = False
    posthog_person_url: Optional[str] = None


class UserListOut(BaseModel):
    items: list[UserAdminOut]
    total: int


@router.get("", response_model=UserListOut)
def list_users(
    tier: Optional[str] = Query(default=None, description="handled | id_verified | pending"),
    status: Optional[str] = Query(default=None, description="active | suspended | banned"),
    q: Optional[str] = Query(default=None, description="handle search (ILIKE)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    query = db.query(User)
    if tier in ("handled", "id_verified", "pending"):
        query = query.filter(User.tier == tier)
    if status in ("active", "suspended", "banned"):
        query = query.filter(User.status == status)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(User.handle.ilike(term), User.anon_id.ilike(term)))
    q = query  # keep the rest of the function unchanged

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
    """Look up a user by anon_id (preferred), then handle, then numeric id
    (fallback for internal callers). Raises 404 if none match."""
    u = db.query(User).filter(User.anon_id == ident).first()
    if u is None:
        u = db.query(User).filter(User.handle == ident).first()
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
    calibration_count = (
        db.query(func.count(UserCalibration.id))
        .filter(UserCalibration.user_id == u.id)
        .scalar()
    ) or 0
    motion_count = (
        db.query(func.count(Motion.id))
        .filter(Motion.filed_by_user_id == u.id)
        .scalar()
    ) or 0
    argument_count = (
        db.query(func.count(MotionArgument.id))
        .filter(MotionArgument.user_id == u.id)
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
            calibration_count=calibration_count,
            motion_count=motion_count,
            argument_count=argument_count,
            subscription_tier=u.subscription_tier,
            subscription_status=u.subscription_status,
            subscription_period_end=(u.subscription_period_end.isoformat() + "Z") if u.subscription_period_end else None,
            allowance_credits=u.allowance_credits or 0,
            purchased_credits=u.purchased_credits or 0,
            comp_unlimited=bool(u.comp_unlimited),
            has_stripe_customer=bool(u.stripe_customer_id),
            posthog_person_url=_posthog_person_url(u.clerk_user_id),
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


# ---------- calibrations (Lyrical Charger, signed-in only) ----------

class UserCalibrationOut(BaseModel):
    id: int
    song_source: str
    song_id: int
    song_slug: Optional[str]
    title: Optional[str]
    artist: Optional[str]
    rubric_color: Optional[str]
    charge_value: Optional[int]
    charge_summary: Optional[str]
    calibrated_at: Optional[str]
    created_at: Optional[str]


class UserCalibrationsListOut(BaseModel):
    items: list[UserCalibrationOut]
    total: int


@router.get("/{anon_id}/calibrations", response_model=UserCalibrationsListOut)
def get_user_calibrations(
    anon_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)
    base = db.query(UserCalibration).filter(UserCalibration.user_id == u.id)
    total = base.with_entities(func.count(UserCalibration.id)).scalar() or 0
    rows = (
        base.order_by(UserCalibration.calibrated_at.desc())
        .offset(offset).limit(limit).all()
    )
    return UserCalibrationsListOut(
        items=[
            UserCalibrationOut(
                id=r.id,
                song_source="songs",
                song_id=r.song_id,
                song_slug=r.song_slug,
                title=r.title,
                artist=r.artist,
                rubric_color=r.rubric_color,
                charge_value=r.charge_value,
                charge_summary=r.charge_summary,
                calibrated_at=(r.calibrated_at.isoformat() + "Z") if r.calibrated_at else None,
                created_at=(r.created_at.isoformat() + "Z") if r.created_at else None,
            )
            for r in rows
        ],
        total=total,
    )


# ---------- misread / satirical submissions ----------

class UserSubmissionOut(BaseModel):
    id: int
    report_type: str
    song_title: str
    song_artist: str
    song_color: str
    song_source: Optional[str]
    song_id: Optional[int]
    message: str
    status: str
    created_at: Optional[str]


class UserSubmissionsListOut(BaseModel):
    items: list[UserSubmissionOut]
    total: int


@router.get("/{anon_id}/submissions", response_model=UserSubmissionsListOut)
def get_user_submissions(
    anon_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)
    base = db.query(MisreadSubmission).filter(MisreadSubmission.user_id == u.id)
    total = base.with_entities(func.count(MisreadSubmission.id)).scalar() or 0
    rows = (
        base.order_by(MisreadSubmission.created_at.desc())
        .offset(offset).limit(limit).all()
    )
    return UserSubmissionsListOut(
        items=[
            UserSubmissionOut(
                id=r.id,
                report_type=r.report_type,
                song_title=r.song_title,
                song_artist=r.song_artist,
                song_color=r.song_color,
                song_source="songs" if r.song_id else None,
                song_id=r.song_id,
                message=r.message,
                status=r.status,
                created_at=(r.created_at.isoformat() + "Z") if r.created_at else None,
            )
            for r in rows
        ],
        total=total,
    )


# ---------- motions + chamber arguments ----------

class UserMotionOut(BaseModel):
    id: int
    motion_type: str
    target_kind: Optional[str]
    target_ref: Optional[str]
    claim: str
    status: str
    filed_at: Optional[str]


class UserArgumentOut(BaseModel):
    id: int
    motion_id: int
    post_type: str
    summary: str
    created_at: Optional[str]


class UserMotionsOut(BaseModel):
    motions: list[UserMotionOut]
    arguments: list[UserArgumentOut]


@router.get("/{anon_id}/motions", response_model=UserMotionsOut)
def get_user_motions(
    anon_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)
    motions = (
        db.query(Motion)
        .filter(Motion.filed_by_user_id == u.id)
        .order_by(Motion.filed_at.desc())
        .limit(limit).all()
    )
    arguments = (
        db.query(MotionArgument)
        .filter(MotionArgument.user_id == u.id)
        .order_by(MotionArgument.created_at.desc())
        .limit(limit).all()
    )
    return UserMotionsOut(
        motions=[
            UserMotionOut(
                id=m.id,
                motion_type=m.motion_type,
                target_kind=m.target_kind,
                target_ref=m.target_ref,
                claim=m.claim,
                status=m.status,
                filed_at=(m.filed_at.isoformat() + "Z") if m.filed_at else None,
            )
            for m in motions
        ],
        arguments=[
            UserArgumentOut(
                id=a.id,
                motion_id=a.motion_id,
                post_type=a.post_type,
                summary=a.summary,
                created_at=(a.created_at.isoformat() + "Z") if a.created_at else None,
            )
            for a in arguments
        ],
    )


# ---------- payments / credit ledger ----------

class CreditLedgerRowOut(BaseModel):
    id: int
    delta: int
    bucket: str
    reason: str
    ref_type: Optional[str]
    ref_id: Optional[str]
    created_at: Optional[str]


class UserPaymentsOut(BaseModel):
    subscription_tier: Optional[str]
    subscription_status: Optional[str]
    subscription_period_end: Optional[str]
    allowance_credits: int
    purchased_credits: int
    comp_unlimited: bool
    has_stripe_customer: bool
    # Free-tier daily-charge allotment (eligible only when tier is free).
    daily_free_eligible: bool
    daily_free_limit: int
    daily_free_used: int
    daily_free_remaining: int
    ledger: list[CreditLedgerRowOut]
    ledger_total: int


@router.get("/{anon_id}/payments", response_model=UserPaymentsOut)
def get_user_payments(
    anon_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)
    base = db.query(CreditLedger).filter(CreditLedger.user_id == u.id)
    ledger_total = base.with_entities(func.count(CreditLedger.id)).scalar() or 0
    rows = (
        base.order_by(CreditLedger.created_at.desc())
        .offset(offset).limit(limit).all()
    )
    df_eligible = not billing_config.is_paid_user(u.subscription_tier)
    df_limit = lyrical_charger_free_daily_charges(db) if df_eligible else 0
    df_used = billing_svc.daily_free_used_today(db, u.id) if df_eligible else 0
    return UserPaymentsOut(
        subscription_tier=u.subscription_tier,
        subscription_status=u.subscription_status,
        subscription_period_end=(u.subscription_period_end.isoformat() + "Z") if u.subscription_period_end else None,
        allowance_credits=u.allowance_credits or 0,
        purchased_credits=u.purchased_credits or 0,
        comp_unlimited=bool(u.comp_unlimited),
        has_stripe_customer=bool(u.stripe_customer_id),
        daily_free_eligible=df_eligible,
        daily_free_limit=df_limit,
        daily_free_used=df_used,
        daily_free_remaining=max(0, df_limit - df_used),
        ledger=[
            CreditLedgerRowOut(
                id=r.id,
                delta=r.delta,
                bucket=r.bucket,
                reason=r.reason,
                ref_type=r.ref_type,
                ref_id=r.ref_id,
                created_at=(r.created_at.isoformat() + "Z") if r.created_at else None,
            )
            for r in rows
        ],
        ledger_total=ledger_total,
    )


# ---------- admin credit grant / deduct + comp toggle ----------

class GrantCreditsIn(BaseModel):
    amount: int  # >0 grants, <0 deducts (purchased bucket first, then allowance)
    note: Optional[str] = None


class CompToggleIn(BaseModel):
    unlimited: bool
    note: Optional[str] = None


class CreditAdjustOut(BaseModel):
    granted: int = 0
    deducted: int = 0
    allowance_credits: int
    purchased_credits: int
    total_credits: int


@router.post("/{anon_id}/grant-credits", response_model=CreditAdjustOut)
def grant_user_credits(
    anon_id: str,
    body: GrantCreditsIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    """A-la-carte credit grant (+) or deduct (-) on the permanent purchased
    bucket. Every adjustment is recorded in credit_ledger (reason
    'admin_grant' / 'admin_deduct')."""
    if body.amount == 0:
        raise HTTPException(status_code=400, detail="amount must be non-zero")
    u = _resolve_user(db, anon_id)
    res = billing_svc.admin_adjust_credits(
        u.id, body.amount,
        ref_id=f"admin:{uuid.uuid4().hex}",
        note=(body.note or None),
        actor=getattr(_admin, "username", None),
    )
    db.refresh(u)
    logger.info(
        "admin credit adjust user=%s amount=%s by=%s",
        u.id, body.amount, getattr(_admin, "username", "?"),
    )
    return CreditAdjustOut(
        granted=res.get("granted", 0),
        deducted=res.get("deducted", 0),
        allowance_credits=u.allowance_credits or 0,
        purchased_credits=u.purchased_credits or 0,
        total_credits=(u.allowance_credits or 0) + (u.purchased_credits or 0),
    )


@router.post("/{anon_id}/comp", response_model=UserPaymentsOut)
def set_user_comp(
    anon_id: str,
    body: CompToggleIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    """Grant or revoke unlimited Lyrical Charger comp for a user. Charger-only:
    zero-cost runs + lifted per-user daily cap. Library entitlement unchanged."""
    u = _resolve_user(db, anon_id)
    billing_svc.set_comp_unlimited(
        u.id, body.unlimited,
        note=(body.note or None),
        actor=getattr(_admin, "username", None),
    )
    logger.info(
        "admin comp_unlimited=%s user=%s by=%s",
        body.unlimited, u.id, getattr(_admin, "username", "?"),
    )
    # Return the refreshed payments view so the UI can re-render in one round trip.
    return get_user_payments(anon_id, limit=200, offset=0, db=db, _admin=_admin)


# ---------- unified activity timeline ----------

class ActivityEntryOut(BaseModel):
    type: str          # comment | calibration | misread | motion | argument | payment | verification
    when: str          # ISO8601 Z
    summary: str
    link: Optional[str] = None  # admin sub-section / public path, when meaningful


class UserActivityOut(BaseModel):
    items: list[ActivityEntryOut]
    total: int


# How many recent rows to pull from EACH source before merging. The merged
# list is sliced by limit/offset. Generous enough that the timeline is
# faithful for any real user; this is admin-only and not hot.
_ACTIVITY_PER_SOURCE = 300


def _iso(dt) -> Optional[str]:
    return (dt.isoformat() + "Z") if dt else None


@router.get("/{anon_id}/activity", response_model=UserActivityOut)
def get_user_activity(
    anon_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_session),
):
    u = _resolve_user(db, anon_id)
    entries: list[ActivityEntryOut] = []

    for c in (
        db.query(Comment).filter(Comment.author_id == u.id)
        .order_by(Comment.created_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        target = f"{c.target_type}/{c.target_source}#{c.target_id}" if c.target_source else f"{c.target_type}#{c.target_id}"
        snippet = (c.content or "")[:120]
        entries.append(ActivityEntryOut(
            type="comment",
            when=_iso(c.created_at) or "",
            summary=f"[{_comment_state(c)}] on {target}: {snippet}",
        ))

    for r in (
        db.query(UserCalibration).filter(UserCalibration.user_id == u.id)
        .order_by(UserCalibration.calibrated_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        entries.append(ActivityEntryOut(
            type="calibration",
            when=_iso(r.calibrated_at) or _iso(r.created_at) or "",
            summary=f"Charged \"{r.title or '?'}\" by {r.artist or '?'} -> {r.rubric_color or '?'} ({r.charge_value if r.charge_value is not None else '?'})",
            link=(f"/songs/{r.song_slug}/" if r.song_slug else None),
        ))

    for r in (
        db.query(MisreadSubmission).filter(MisreadSubmission.user_id == u.id)
        .order_by(MisreadSubmission.created_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        entries.append(ActivityEntryOut(
            type="misread",
            when=_iso(r.created_at) or "",
            summary=f"[{r.status}] {r.report_type} report on \"{r.song_title}\" by {r.song_artist}",
        ))

    for m in (
        db.query(Motion).filter(Motion.filed_by_user_id == u.id)
        .order_by(Motion.filed_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        entries.append(ActivityEntryOut(
            type="motion",
            when=_iso(m.filed_at) or "",
            summary=f"[{m.status}] filed {m.motion_type}: {m.claim[:120]}",
        ))

    for a in (
        db.query(MotionArgument).filter(MotionArgument.user_id == u.id)
        .order_by(MotionArgument.created_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        entries.append(ActivityEntryOut(
            type="argument",
            when=_iso(a.created_at) or "",
            summary=f"{a.post_type} on motion #{a.motion_id}: {a.summary[:120]}",
            link=f"/motion-desk/deliberation-chamber/{a.motion_id}/",
        ))

    for r in (
        db.query(CreditLedger).filter(CreditLedger.user_id == u.id)
        .order_by(CreditLedger.created_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        sign = "+" if r.delta >= 0 else ""
        entries.append(ActivityEntryOut(
            type="payment",
            when=_iso(r.created_at) or "",
            summary=f"{sign}{r.delta} {r.bucket} ({r.reason})",
        ))

    for v in (
        db.query(AccountVerification).filter(AccountVerification.user_id == u.id)
        .order_by(AccountVerification.created_at.desc()).limit(_ACTIVITY_PER_SOURCE).all()
    ):
        entries.append(ActivityEntryOut(
            type="verification",
            when=_iso(v.created_at) or "",
            summary=f"[{v.status}] {v.provider} identity verification",
        ))

    # Merge: newest first. Entries with no timestamp sort last.
    entries.sort(key=lambda e: e.when or "", reverse=True)
    total = len(entries)
    return UserActivityOut(items=entries[offset:offset + limit], total=total)
