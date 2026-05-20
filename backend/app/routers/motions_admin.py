"""Public Participation Phase 3.2 -- Motion Desk admin queue.

Motions deliberate the framework. Admin path is purely procedural:
review the filing, advance to deliberation, and resolve with a public
summary. No song-side hooks live here -- per-song work happens in
Misread Reports and the Recalibrate suite, not on motions.

Surface (all gated by require_admin_session):
  GET  /api/admin/motions                       queue
  GET  /api/admin/motions/{id}                  detail
  POST /api/admin/motions/{id}/move-to-deliberation
  POST /api/admin/motions/{id}/ratify
  POST /api/admin/motions/{id}/reject
  POST /api/admin/motions/{id}/covered
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Motion
from app.routers.admin import verify_admin_key
from app.services import motions as motions_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/motions", tags=["motions-admin"])


class ResolutionIn(BaseModel):
    resolution_summary: str = Field(..., min_length=10, max_length=4000)


@router.get("")
def admin_list_motions(
    status: Optional[str] = None,
    motion_type: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
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
    rows = (
        q.order_by(Motion.filed_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    status_priority = {
        "filed": 0,
        "in_deliberation": 1,
        "ratified": 2,
        "covered": 3,
        "rejected": 4,
    }
    rows.sort(
        key=lambda m: (
            status_priority.get(m.status, 99),
            -(m.filed_at.timestamp() if m.filed_at else 0),
        )
    )
    return [motions_svc.motion_to_dict(db, m) for m in rows]


@router.get("/{motion_id}")
def admin_get_motion(
    motion_id: int,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    return motions_svc.motion_to_dict(db, motion)


@router.post("/{motion_id}/move-to-deliberation")
def admin_move_to_deliberation(
    motion_id: int,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    motion = motions_svc.transition_status(
        db, motion, "in_deliberation", admin_user_id=admin.id,
    )
    return motions_svc.motion_to_dict(db, motion)


@router.post("/{motion_id}/ratify")
def admin_ratify(
    motion_id: int,
    data: ResolutionIn,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    motion = motions_svc.transition_status(
        db, motion, "ratified",
        admin_user_id=admin.id,
        resolution_summary=data.resolution_summary,
    )
    return motions_svc.motion_to_dict(db, motion)


@router.post("/{motion_id}/reject")
def admin_reject(
    motion_id: int,
    data: ResolutionIn,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    motion = motions_svc.transition_status(
        db, motion, "rejected",
        admin_user_id=admin.id,
        resolution_summary=data.resolution_summary,
    )
    return motions_svc.motion_to_dict(db, motion)


@router.post("/{motion_id}/covered")
def admin_covered(
    motion_id: int,
    data: ResolutionIn,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
    motion = db.query(Motion).get(motion_id)
    if motion is None:
        raise HTTPException(404, "Motion not found")
    motion = motions_svc.transition_status(
        db, motion, "covered",
        admin_user_id=admin.id,
        resolution_summary=data.resolution_summary,
    )
    return motions_svc.motion_to_dict(db, motion)
