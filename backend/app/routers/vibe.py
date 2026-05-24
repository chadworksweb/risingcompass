"""Audience Vibe — public push + state endpoints, admin review-case endpoints.

Public endpoints sit on the standard X-Api-Key dependency (registered in
main.py). Admin endpoints carry verify_admin_key per-route per the existing
pattern.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import optional_clerk_user, require_clerk_user
from app.database import get_db
from app.models import AudienceVibeReviewCase, User
from app.routers.admin import verify_admin_key
from app.services.audience_vibe import (
    apply_push, get_state, get_user_activity, VibeError, _resolve_song,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audience-vibe"])

# User-authed endpoints (Clerk token only, NO X-Api-Key). The main router is
# gated on X-Api-Key, but authedFetch from the account page sends only the
# Clerk bearer — so these live on a separate router included without that dep.
user_router = APIRouter(tags=["audience-vibe"])

VALID_SOURCES = {"compass", "library", "submitted"}
VALID_CASE_STATUSES = {"open", "acknowledged", "recalibrated", "dismissed"}


class PushIn(BaseModel):
    direction: int = Field(..., description="+1 (push higher), 0 (agree), -1 (push lower)")
    device_id: str = Field(..., min_length=4, max_length=200)


class CaseStatusUpdate(BaseModel):
    status: str
    admin_notes: Optional[str] = Field(None, max_length=4000)


# --- User-authed (no X-Api-Key; on user_router, included before the gated
#     router so "me/activity" isn't captured by /{song_source}/{song_id}) ---

@user_router.get("/api/vibe/me/activity")
def my_vibe_activity(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    """The signed-in user's own vibe voting history. Tier 1 (any signed-in
    account) is enough — this is personal activity, not a privileged view.
    Clerk token only — the account page's authedFetch sends no X-Api-Key.
    """
    limit = max(1, min(limit, 500))
    return get_user_activity(db, user.id, limit)


# --- Public ---

@router.get("/api/vibe/{song_source}/{song_id}")
def vibe_state(
    song_source: str, song_id: int,
    device_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Public read of the vibe state for one song. Pass device_id to learn
    whether the caller is eligible to push this year.
    """
    if song_source not in VALID_SOURCES:
        raise HTTPException(400, f"Invalid song_source. Must be one of: {', '.join(sorted(VALID_SOURCES))}")
    try:
        return get_state(db, song_source, song_id, device_id)
    except VibeError as e:
        raise HTTPException(404, str(e))


@router.post("/api/vibe/{song_source}/{song_id}/push")
def vibe_push(
    song_source: str, song_id: int, data: PushIn,
    request: Request, db: Session = Depends(get_db),
    user: Optional[User] = Depends(optional_clerk_user),
):
    """Apply one push to the vibe needle. Enforces eligibility (one push per
    device per song per calendar year). Opens an admin review case if the
    gap with the compass charge crosses threshold.

    Anonymous votes work exactly as before; if the voter is signed in we stamp
    their user_id on the push so they can review it later under their account.
    """
    if song_source not in VALID_SOURCES:
        raise HTTPException(400, f"Invalid song_source. Must be one of: {', '.join(sorted(VALID_SOURCES))}")
    ip = request.client.host if request.client else None
    try:
        return apply_push(
            db, song_source, song_id, data.direction, data.device_id, ip,
            user_id=user.id if user else None,
        )
    except VibeError as e:
        # Eligibility violations are 409 (conflict / already-pushed); other
        # validation goes 400. Simpler: surface all VibeErrors as 409 since
        # they're domain rules the client can recover from.
        raise HTTPException(409, str(e))


# --- Admin ---

@router.get("/api/admin/vibe/cases", dependencies=[Depends(verify_admin_key)])
def list_review_cases(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(AudienceVibeReviewCase)
    if status:
        if status not in VALID_CASE_STATUSES:
            raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(VALID_CASE_STATUSES))}")
        q = q.filter(AudienceVibeReviewCase.status == status)
    rows = q.order_by(AudienceVibeReviewCase.created_at.desc()).limit(limit).all()

    out = []
    for c in rows:
        entry = {
            "id": c.id,
            "song_source": c.song_source,
            "song_id": c.song_id,
            "compass_charge": c.compass_charge,
            "compass_color": c.compass_color,
            "vibe_value": c.vibe_value,
            "gap": c.gap,
            "status": c.status,
            "admin_notes": c.admin_notes,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        }
        try:
            song = _resolve_song(db, c.song_source, c.song_id)
            entry["song_title"] = song.title
            entry["song_artist"] = getattr(song, "artist", None)
        except VibeError:
            pass
        out.append(entry)
    return out


@router.patch("/api/admin/vibe/cases/{case_id}", dependencies=[Depends(verify_admin_key)])
def update_review_case(case_id: int, data: CaseStatusUpdate, db: Session = Depends(get_db)):
    """Resolve a review case. The 'recalibrated' status is set by the admin
    after they've fired a public-interest recalibration via the recalibrate
    suite (workflow not yet wired — case status is updated manually for now).
    """
    if data.status not in VALID_CASE_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(VALID_CASE_STATUSES))}")
    case = db.query(AudienceVibeReviewCase).get(case_id)
    if not case:
        raise HTTPException(404, "Review case not found")

    case.status = data.status
    if data.admin_notes is not None:
        case.admin_notes = data.admin_notes
    if data.status != "open" and case.resolved_at is None:
        case.resolved_at = datetime.utcnow()
    db.commit()
    return {
        "id": case.id, "status": case.status,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
    }
