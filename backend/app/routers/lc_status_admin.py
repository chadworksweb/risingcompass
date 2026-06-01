"""Admin endpoints for the Lyrical Charger temporarily-unavailable mode.

Endpoints:
  GET  /api/admin/lc-status            -> current flag state + subscriber counts
  POST /api/admin/lc-status/toggle     -> set disabled flag (and optional message)
  GET  /api/admin/lc-status/subscribers -> paginated subscriber list
  POST /api/admin/lc-status/notify     -> send return-online email to unnotified
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import LyricalChargerSubscriber
from app.routers.admin import verify_admin_key
from app.schemas import (
    LCStatusOut, LCToggleIn, LCSubscriberOut, LCNotifyOut, LCLimitsIn,
)
from app.services.feature_flags import (
    is_lyrical_charger_disabled, lyrical_charger_disabled_message,
    set_lyrical_charger_disabled, set_lyrical_charger_disabled_message,
    DEFAULT_LC_DISABLED_MESSAGE,
    lyrical_charger_anon_daily_limit, lyrical_charger_user_daily_limit,
    set_lyrical_charger_anon_daily_limit, set_lyrical_charger_user_daily_limit,
)
from app.services.lc_subscriber_notifier import notify_subscribers

router = APIRouter(prefix="/api/admin/lc-status", tags=["lc-status-admin"])


def _status_payload(db: Session) -> LCStatusOut:
    total = db.query(LyricalChargerSubscriber).count()
    unnotified = (
        db.query(LyricalChargerSubscriber)
        .filter(LyricalChargerSubscriber.notified_at.is_(None))
        .count()
    )
    return LCStatusOut(
        disabled=is_lyrical_charger_disabled(db),
        message=lyrical_charger_disabled_message(db),
        subscribers_total=total,
        subscribers_unnotified=unnotified,
        anon_daily_limit=lyrical_charger_anon_daily_limit(db),
        user_daily_limit=lyrical_charger_user_daily_limit(db),
    )


@router.get("", response_model=LCStatusOut, dependencies=[Depends(verify_admin_key)])
def get_status(db: Session = Depends(get_db)):
    return _status_payload(db)


@router.post("/limits", response_model=LCStatusOut, dependencies=[Depends(verify_admin_key)])
def set_limits(data: LCLimitsIn, db: Session = Depends(get_db)):
    """Update the LC calibrate daily caps. Takes effect within ~30s (the
    limiter caches the values in-process)."""
    if data.anon_daily_limit is not None:
        if data.anon_daily_limit < 0:
            raise HTTPException(400, "anon_daily_limit must be >= 0")
        set_lyrical_charger_anon_daily_limit(db, data.anon_daily_limit)
    if data.user_daily_limit is not None:
        if data.user_daily_limit < 0:
            raise HTTPException(400, "user_daily_limit must be >= 0")
        set_lyrical_charger_user_daily_limit(db, data.user_daily_limit)
    return _status_payload(db)


@router.post("/toggle", response_model=LCStatusOut, dependencies=[Depends(verify_admin_key)])
def toggle(data: LCToggleIn, db: Session = Depends(get_db)):
    set_lyrical_charger_disabled(db, data.disabled)
    if data.message is not None:
        msg = data.message.strip()
        # Empty string -> reset to default
        set_lyrical_charger_disabled_message(db, msg if msg else None)
    return _status_payload(db)


@router.get("/subscribers", dependencies=[Depends(verify_admin_key)])
def list_subscribers(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    only_unnotified: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(LyricalChargerSubscriber)
    if only_unnotified:
        q = q.filter(LyricalChargerSubscriber.notified_at.is_(None))
    total = q.count()
    rows = (
        q.order_by(LyricalChargerSubscriber.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "subscribers": [
            LCSubscriberOut(
                id=r.id, email=r.email,
                created_at=r.created_at, notified_at=r.notified_at,
            )
            for r in rows
        ],
    }


@router.delete("/subscribers/{sub_id}", dependencies=[Depends(verify_admin_key)])
def delete_subscriber(sub_id: int, db: Session = Depends(get_db)):
    row = db.query(LyricalChargerSubscriber).filter(LyricalChargerSubscriber.id == sub_id).first()
    if not row:
        raise HTTPException(404, "Subscriber not found")
    db.delete(row)
    db.commit()
    return {"deleted": sub_id}


@router.post("/notify", response_model=LCNotifyOut, dependencies=[Depends(verify_admin_key)])
def notify(db: Session = Depends(get_db)):
    """Send the 'we're back online' email to every subscriber without notified_at."""
    if is_lyrical_charger_disabled(db):
        raise HTTPException(
            400,
            "Lyrical Charger is currently disabled. Toggle it back online before notifying subscribers.",
        )
    result = notify_subscribers(db, settings)
    if not result.get("configured", True):
        raise HTTPException(503, "Resend is not configured (RESEND_API_KEY / email_from missing).")
    return LCNotifyOut(
        sent=result["sent"],
        skipped=result["skipped"],
        failed=result["failed"],
    )
