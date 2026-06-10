"""Admin view for the on-site subscriber list (Build 2b). Read-only window onto
rc_subscribers: counts + a filterable recent list. Cookie-authed like the other
Site Admin sections. Section template: admin/subscribers.html (Community group).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import verify_reading_cron_key
from app.database import get_db
from app.models import RcSubscriber
from app.routers.admin import verify_admin_key
from app.services import subscriber_digest

router = APIRouter(tags=["subscribers-admin"])

_LIST_CAP = 500


@router.get("/api/admin/subscribers", dependencies=[Depends(verify_admin_key)])
def list_subscribers(status: str = Query(""), db: Session = Depends(get_db)):
    def count_where(*crit) -> int:
        q = db.query(func.count(RcSubscriber.id))
        for c in crit:
            q = q.filter(c)
        return q.scalar() or 0

    stats = {
        "total": count_where(),
        "confirmed": count_where(RcSubscriber.status == "confirmed"),
        "pending": count_where(RcSubscriber.status == "pending"),
        "unsubscribed": count_where(RcSubscriber.status == "unsubscribed"),
        "promoted": count_where(RcSubscriber.user_id.isnot(None)),
    }

    q = db.query(RcSubscriber)
    if status in ("pending", "confirmed", "unsubscribed"):
        q = q.filter(RcSubscriber.status == status)
    elif status == "promoted":
        q = q.filter(RcSubscriber.user_id.isnot(None))
    rows = q.order_by(RcSubscriber.created_at.desc()).limit(_LIST_CAP).all()

    return {
        "stats": stats,
        "capped": len(rows) >= _LIST_CAP,
        "rows": [
            {
                "id": r.id,
                "email": r.email,
                "status": r.status,
                "source": r.source,
                "source_detail": r.source_detail,
                "promoted": r.user_id is not None,
                "promoted_at": r.promoted_at.isoformat() if r.promoted_at else None,
                "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.post("/api/admin/agent/cron/subscriber-digest", dependencies=[Depends(verify_reading_cron_key)])
def cron_subscriber_digest(db: Session = Depends(get_db)):
    """Service endpoint for the digest cron (X-Reading-Cron-Key). Sends the
    latest reading digest to confirmed subscribers, deduped by reading date."""
    return subscriber_digest.send_digest(db)


@router.post("/api/admin/subscribers/send-digest", dependencies=[Depends(verify_admin_key)])
def admin_send_digest(dry_run: bool = Query(False), db: Session = Depends(get_db)):
    """Admin 'Send digest now' trigger (cookie auth). Same sender as the cron;
    pass dry_run=true to preview the eligible count without sending."""
    return subscriber_digest.send_digest(db, dry_run=dry_run)
