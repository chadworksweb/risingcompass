"""Public Participation Phase 4 -- Deliberation Chamber public API.

The Chamber is a sub-room of Motion Desk that hosts the argument thread
on any motion in_deliberation. Tier 2 (id_verified) gates writes; reads
are public from the moment a motion enters deliberation.

Surface:
  POST /api/motions/{id}/arguments              post an argument (Tier 2)
  GET  /api/motions/{id}/arguments              public list (grouped client-side)
  GET  /api/motions/{id}/arguments/{arg_id}     public detail
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_clerk_user
from app.database import get_db
from app.models import Motion, MotionArgument, User
from app.services import chamber as chamber_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/motions/{motion_id}/arguments", tags=["chamber"])


class PostArgumentIn(BaseModel):
    post_type: str = Field(
        ...,
        description="argument_for | argument_against | rebuttal | citation | clarification",
    )
    parent_id: Optional[int] = Field(
        None,
        description="Required for rebuttal posts, NULL otherwise.",
    )
    summary: str = Field(..., min_length=1, max_length=chamber_svc.MAX_SUMMARY_LENGTH)
    body: str = Field(
        ...,
        min_length=chamber_svc.MIN_BODY_LENGTH,
        max_length=chamber_svc.MAX_BODY_LENGTH,
    )
    citations: Optional[list[str]] = None


def _get_motion_or_404(db: Session, motion_id: int) -> Motion:
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    return motion


@router.post("")
def post_argument(
    motion_id: int,
    data: PostArgumentIn,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    motion = _get_motion_or_404(db, motion_id)
    arg = chamber_svc.post_argument(
        db=db,
        user=user,
        motion=motion,
        post_type=data.post_type,
        parent_id=data.parent_id,
        summary=data.summary,
        body=data.body,
        citations=data.citations,
    )
    return chamber_svc.argument_to_dict(db, arg)


@router.get("")
def list_arguments(
    motion_id: int,
    db: Session = Depends(get_db),
):
    motion = _get_motion_or_404(db, motion_id)
    rows = (
        db.query(MotionArgument)
        .filter(MotionArgument.motion_id == motion.id)
        .order_by(MotionArgument.created_at.asc())
        .all()
    )
    return [chamber_svc.argument_to_dict(db, a) for a in rows]


@router.get("/{arg_id}")
def get_argument(
    motion_id: int,
    arg_id: int,
    db: Session = Depends(get_db),
):
    motion = _get_motion_or_404(db, motion_id)
    arg = db.query(MotionArgument).get(arg_id)
    if arg is None or arg.motion_id != motion.id:
        raise HTTPException(404, "Argument not found")
    return chamber_svc.argument_to_dict(db, arg)
