"""Public Participation Phase 1.1 -- Tier 1 user endpoints.

GET  /api/users/me        -- return the calling user's row + onboarding flag
POST /api/users/me/setup  -- claim a handle + avatar (completes onboarding)

These endpoints are JWT-gated via require_clerk_user (Clerk session).
Auth lazily mints a users row on first sight with handle=NULL; the setup
endpoint is what flips tier from 'pending' to 'handled' and unlocks
posting privileges in the Lobby (and Misread reports in Phase 2).
"""

import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_clerk_user
from app.config import settings
from app.database import get_db
from app.models import AccountVerification, User, UserCalibration
from app.services import notifications as notifications_svc
from app.services import stripe_identity as stripe_identity_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])

HANDLE_PATTERN = re.compile(r"^[a-zA-Z0-9_]{3,20}$")

# Reserved handles -- system / admin / impersonation defenses. Lowercase
# for case-insensitive comparison.
RESERVED_HANDLES = {
    "admin", "administrator", "root", "system", "support", "help",
    "official", "staff", "moderator", "mod", "chadmin",
    "rising_compass",
    "lyrical_charger", "lyricalcharger", "anonymous", "anon", "null", "none",
    "tenets", "amendments", "motion_desk", "motiondesk", "chamber", "lobby",
    "deleted", "removed", "banned",
}


class UserOut(BaseModel):
    id: int
    handle: Optional[str]
    avatar_url: Optional[str]
    tier: str
    status: str
    onboarding_complete: bool
    # The verified legal name shown publicly in the Chamber, surfaced back to
    # the user so they can see what's on file. Null unless captured + consented.
    legal_name: Optional[str] = None


class UserSetupIn(BaseModel):
    handle: str = Field(..., min_length=3, max_length=20)
    avatar_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("handle")
    @classmethod
    def _validate_handle(cls, v: str) -> str:
        v = v.strip()
        if not HANDLE_PATTERN.match(v):
            raise ValueError("Handle must be 3-20 chars, letters / numbers / underscore only")
        if v.lower() in RESERVED_HANDLES:
            raise ValueError("That handle is reserved")
        return v


def _serialize(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        handle=user.handle,
        avatar_url=user.avatar_url,
        tier=user.tier,
        status=user.status,
        onboarding_complete=user.handle is not None,
        legal_name=(
            user.legal_name
            if user.legal_name and user.legal_name_public_consent_at
            else None
        ),
    )


@router.get("/me", response_model=UserOut)
def get_me(user: User = Depends(require_clerk_user)):
    """Return the calling user's profile + onboarding state. Frontend uses
    onboarding_complete=False to force the handle picker before allowing
    any post action."""
    return _serialize(user)


@router.post("/me/setup", response_model=UserOut)
def setup_me(
    payload: UserSetupIn,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """One-time handle claim + avatar set. Flips tier from 'pending' to
    'handled', which is the gate for posting in Phase 1 (Lobby).

    Re-running is rejected -- handle changes require admin intervention,
    per the build plan (stable identity is the point)."""
    if user.handle is not None:
        raise HTTPException(
            status_code=409,
            detail="Handle already set; contact an admin to change it.",
        )

    candidate = payload.handle
    # Case-insensitive uniqueness via lower() -- portable across Postgres
    # (the SQLite "NOCASE" collation used pre-migration does not exist on PG).
    existing = (
        db.query(User)
        .filter(User.handle.is_not(None))
        .filter(User.id != user.id)
        .filter(func.lower(User.handle) == candidate.lower())
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Handle is taken")

    user.handle = candidate
    if payload.avatar_url:
        user.avatar_url = payload.avatar_url
    user.tier = "handled"
    db.commit()
    db.refresh(user)

    logger.info("User %s completed onboarding with handle=%s", user.id, candidate)
    return _serialize(user)


class CalibrationItemOut(BaseModel):
    song_source: str
    song_id: int
    song_slug: Optional[str]
    title: Optional[str]
    artist: Optional[str]
    rubric_color: Optional[str]
    charge_value: Optional[int]
    charge_summary: Optional[str]
    calibrated_at: Optional[str]

    model_config = {"from_attributes": True}


class CalibrationsOut(BaseModel):
    items: list[CalibrationItemOut]


@router.get("/me/calibrations", response_model=CalibrationsOut)
def get_my_calibrations(
    limit: int = 100,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """The songs this user has run through Lyrical Charger while signed in.

    One entry per distinct song, most recently calibrated first. Anonymous
    runs aren't attributed, so they never appear here. Clerk token only --
    the account page's authedFetch sends no X-Api-Key."""
    limit = max(1, min(limit, 500))
    rows = (
        db.query(UserCalibration)
        .filter(UserCalibration.user_id == user.id)
        .order_by(UserCalibration.calibrated_at.desc())
        .limit(limit)
        .all()
    )
    items = [
        CalibrationItemOut(
            song_source=r.song_source,
            song_id=r.song_id,
            song_slug=r.song_slug,
            title=r.title,
            artist=r.artist,
            rubric_color=r.rubric_color,
            charge_value=r.charge_value,
            charge_summary=r.charge_summary,
            calibrated_at=r.calibrated_at.isoformat() if r.calibrated_at else None,
        )
        for r in rows
    ]
    return CalibrationsOut(items=items)


class VerifyIdentityStartOut(BaseModel):
    id: str        # Stripe VerificationSession id
    url: str       # hosted page to redirect to
    status: str    # 'requires_input' at this point


class VerifyIdentityStartIn(BaseModel):
    # Explicit consent to public display of the verified legal name in the
    # Deliberation Chamber. Required true -- the Chamber is the room where
    # accountability is the point, and we do not capture/show a name without
    # the user knowingly agreeing at this step.
    display_name_consent: bool = Field(
        ...,
        description="Must be true: consent to show verified legal name on Chamber posts.",
    )


@router.post("/me/verify-identity", response_model=VerifyIdentityStartOut)
def start_identity_verification(
    body: VerifyIdentityStartIn,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """Open a Stripe Identity verification session for this user. Returns
    the hosted URL the frontend should redirect to. On completion the
    webhook flips users.tier to 'id_verified' and writes verified_at on
    the matching account_verifications row.

    Tier 1 (handle) required. If the user is already id_verified, return
    409 -- no need to re-verify."""
    if user.handle is None:
        raise HTTPException(
            status_code=409,
            detail="Pick a handle before verifying your identity.",
        )
    if user.tier == "id_verified":
        raise HTTPException(status_code=409, detail="Already verified.")
    if user.status == "banned":
        raise HTTPException(status_code=403, detail="Account banned")
    if not body.display_name_consent:
        raise HTTPException(
            status_code=400,
            detail="Consent to public legal-name display is required to verify.",
        )

    return_url = (
        settings.stripe_identity_return_url
        or (settings.site_url.rstrip("/") + "/account/")
    )

    try:
        session = stripe_identity_svc.create_verification_session(
            rc_user_id=user.id,
            return_url=return_url,
        )
    except RuntimeError as exc:
        # Misconfigured server -- secret missing.
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        logger.exception("Stripe Identity session creation failed for user %s", user.id)
        raise HTTPException(status_code=502, detail="Could not start verification. Try again shortly.")

    # Persist the intent so the webhook has something to reconcile against.
    av = AccountVerification(
        user_id=user.id,
        provider="stripe_identity",
        provider_reference=session.id,
        status="requires_input",
    )
    db.add(av)
    # Record the public-display consent now, at the moment of opt-in. The
    # name itself is filled in later by the verified webhook; display is
    # gated on this timestamp being present.
    user.legal_name_public_consent_at = datetime.utcnow()
    db.commit()

    logger.info("Started Stripe Identity session %s for user %s", session.id, user.id)
    return VerifyIdentityStartOut(
        id=session.id,
        url=session.url,
        status=session.status or "requires_input",
    )


# ---------- comment notifications (Phase 1.5) ----------

@router.get("/me/notifications")
def list_notifications(
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """Recent reply + @mention notifications for this user, newest first,
    plus the current unread count. Backs the account-page panel."""
    return {
        "items": notifications_svc.list_for_user(db, user),
        "unread_count": notifications_svc.unread_count(db, user),
    }


@router.post("/me/notifications/mark-read")
def mark_notifications_read(
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """Mark all of this user's unread notifications read."""
    marked = notifications_svc.mark_all_read(db, user)
    return {"marked": marked}
