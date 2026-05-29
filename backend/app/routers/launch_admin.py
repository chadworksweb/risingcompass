"""Admin toggle for the pre-launch gate (public sign-up + billing checkout).

  GET  /api/admin/launch-lock         -> current lock state + message
  POST /api/admin/launch-lock/toggle  -> set locked (and optional message)

Mirrors lc_status_admin: session-cookie admin auth, system_flags-backed.
Default is LOCKED until an explicit toggle opens it, so opening the site to
the public is a deliberate admin action that needs no redeploy.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.admin import verify_admin_key
from app.services.feature_flags import (
    is_launch_locked,
    launch_locked_message,
    set_launch_locked,
    set_launch_locked_message,
)

router = APIRouter(prefix="/api/admin/launch-lock", tags=["launch-admin"])


class LaunchLockIn(BaseModel):
    locked: bool
    message: str | None = None


def _payload(db: Session) -> dict:
    return {"locked": is_launch_locked(db), "message": launch_locked_message(db)}


@router.get("", dependencies=[Depends(verify_admin_key)])
def get_launch_lock(db: Session = Depends(get_db)):
    return _payload(db)


@router.post("/toggle", dependencies=[Depends(verify_admin_key)])
def toggle_launch_lock(data: LaunchLockIn, db: Session = Depends(get_db)):
    set_launch_locked(db, data.locked)
    if data.message is not None:
        msg = data.message.strip()
        set_launch_locked_message(db, msg if msg else None)
    return _payload(db)
