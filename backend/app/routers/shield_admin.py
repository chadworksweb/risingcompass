"""Admin control plane for the Scrape Shield (anti-scrape stack).

  GET  /api/admin/shield           -> current flag state + tunables
  POST /api/admin/shield/toggle    -> flip enforcement (rate-limit / bot-score / token)
  POST /api/admin/shield/limits    -> set per-IP read caps + bot-score threshold
  GET  /api/admin/shield/events    -> recent shield decisions (the bot feed)

Each layer ships observe-only (enforcement OFF). Watch the events feed -- rows
are tagged shield_observe_* while observing, shield_block_* once enforced -- then
flip the matching toggle. Changes propagate within ~30s (the guard caches its
config in-process).
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LcEvent
from app.routers.admin import verify_admin_key
from app.schemas import ShieldStatusOut, ShieldToggleIn, ShieldLimitsIn
from app.services import feature_flags as ff
from app.services import scrape_shield as shield

router = APIRouter(prefix="/api/admin/shield", tags=["shield-admin"])

_SHIELD_EVENT_TYPES = [
    "shield_observe_botscore", "shield_block_botscore",
    "shield_observe_ratelimit", "shield_block_ratelimit",
    "shield_observe_token", "shield_block_token",
]


def _status(db: Session) -> ShieldStatusOut:
    return ShieldStatusOut(
        ratelimit_enabled=ff.is_shield_ratelimit_enabled(db),
        botscore_enabled=ff.is_shield_botscore_enabled(db),
        token_enabled=ff.is_shield_token_enabled(db),
        read_per_minute=ff.shield_read_per_minute(db),
        read_per_day=ff.shield_read_per_day(db),
        botscore_threshold=ff.shield_botscore_threshold(db),
    )


@router.get("", response_model=ShieldStatusOut, dependencies=[Depends(verify_admin_key)])
def get_shield(db: Session = Depends(get_db)):
    return _status(db)


@router.post("/toggle", response_model=ShieldStatusOut, dependencies=[Depends(verify_admin_key)])
def toggle(data: ShieldToggleIn, db: Session = Depends(get_db)):
    if data.ratelimit_enabled is not None:
        ff.set_shield_ratelimit_enabled(db, data.ratelimit_enabled)
    if data.botscore_enabled is not None:
        ff.set_shield_botscore_enabled(db, data.botscore_enabled)
    if data.token_enabled is not None:
        ff.set_shield_token_enabled(db, data.token_enabled)
    shield.invalidate_config_cache()
    return _status(db)


@router.post("/limits", response_model=ShieldStatusOut, dependencies=[Depends(verify_admin_key)])
def set_limits(data: ShieldLimitsIn, db: Session = Depends(get_db)):
    if data.read_per_minute is not None:
        if data.read_per_minute < 1:
            raise HTTPException(400, "read_per_minute must be >= 1")
        ff.set_shield_read_per_minute(db, data.read_per_minute)
    if data.read_per_day is not None:
        if data.read_per_day < 1:
            raise HTTPException(400, "read_per_day must be >= 1")
        ff.set_shield_read_per_day(db, data.read_per_day)
    if data.botscore_threshold is not None:
        if data.botscore_threshold < 1:
            raise HTTPException(400, "botscore_threshold must be >= 1")
        ff.set_shield_botscore_threshold(db, data.botscore_threshold)
    shield.invalidate_config_cache()
    return _status(db)


@router.get("/events", dependencies=[Depends(verify_admin_key)])
def list_events(
    limit: int = Query(100, ge=1, le=500),
    only_blocks: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Recent shield decisions (the bot feed). only_blocks filters to enforced
    blocks; otherwise observe + block rows are both returned."""
    types = [t for t in _SHIELD_EVENT_TYPES if not only_blocks or t.startswith("shield_block")]
    rows = (
        db.query(LcEvent)
        .filter(LcEvent.event_type.in_(types))
        .order_by(LcEvent.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        try:
            payload = json.loads(r.payload_json) if r.payload_json else None
        except (ValueError, TypeError):
            payload = None
        out.append({
            "id": r.id,
            "occurred_at": r.occurred_at,
            "event_type": r.event_type,
            "ip": r.ip_address,
            "user_agent": r.user_agent,
            "referrer": r.referrer,
            "payload": payload,
        })
    return {"events": out, "count": len(out)}
