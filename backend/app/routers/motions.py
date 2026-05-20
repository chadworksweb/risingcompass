"""Public Participation Phase 3.2 -- Motion Desk public API.

Motions deliberate the framework (tenets, rules, modifiers, methodology).
Per-song corrections live in MisreadSubmission, not here.

Surface:
  POST /api/motions              file a motion (Tier 2)
  GET  /api/motions              public list
  GET  /api/motions/{id}         public detail

Tier 2 (id_verified) required to file. Reads are public from the moment
of filing.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_clerk_user
from app.database import get_db
from app.models import Motion, User
from app.services import motions as motions_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/motions", tags=["motions"])


class FileMotionIn(BaseModel):
    motion_type: str = Field(
        ...,
        description="amend_tenet | new_tenet | remove_tenet | amend_rule | new_rule | remove_rule | process",
    )
    target_kind: Optional[str] = Field(
        None,
        description="tenet | rule | modifier. Omit for process or new_* motions.",
    )
    target_ref: Optional[str] = Field(
        None,
        description="Id within target_kind, e.g. 'violet-01' / 'R1' / 'contamination'. Required for amend_* / remove_*.",
    )
    claim: str = Field(..., min_length=1, max_length=motions_svc.MAX_CLAIM_LENGTH)
    reasoning: str = Field(
        ...,
        min_length=motions_svc.MIN_REASONING_LENGTH,
        max_length=motions_svc.MAX_REASONING_LENGTH,
    )
    citations: Optional[list[str]] = None


@router.post("")
def file_motion(
    data: FileMotionIn,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    motion = motions_svc.file_motion(
        db=db,
        user=user,
        motion_type=data.motion_type,
        target_kind=data.target_kind,
        target_ref=data.target_ref,
        claim=data.claim,
        reasoning=data.reasoning,
        citations=data.citations,
    )
    return motions_svc.motion_to_dict(db, motion)


@router.get("")
def list_motions(
    status: Optional[str] = Query(None),
    motion_type: Optional[str] = Query(None),
    target_kind: Optional[str] = Query(None),
    target_ref: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Motion)
    if status:
        if status not in motions_svc.VALID_STATUSES:
            raise HTTPException(422, f"Unknown status: {status}")
        q = q.filter(Motion.status == status)
    if motion_type:
        if motion_type not in motions_svc.VALID_MOTION_TYPES:
            raise HTTPException(422, f"Unknown motion_type: {motion_type}")
        q = q.filter(Motion.motion_type == motion_type)
    if target_kind:
        if target_kind not in motions_svc.VALID_TARGET_KINDS:
            raise HTTPException(422, "Invalid target_kind")
        q = q.filter(Motion.target_kind == target_kind)
    if target_ref:
        q = q.filter(Motion.target_ref == target_ref)
    rows = q.order_by(Motion.filed_at.desc()).limit(limit).all()
    return [motions_svc.motion_to_dict(db, m) for m in rows]


@router.get("/{motion_id}")
def get_motion(motion_id: int, db: Session = Depends(get_db)):
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    return motions_svc.motion_to_dict(db, motion)
