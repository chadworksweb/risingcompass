"""Admin view for the Build 6 social broadcaster -- Site Admin -> Broadcasts.

Read-only window onto social_posts, grouped one card per broadcast ITEM (the
fan-out across platforms collapses into a per-platform status strip), plus the
config/dark state and a "Run broadcast now" trigger (same orchestrator as the
cron). Cookie-authed like the other Site Admin sections. Template:
admin/social.html (Community group).
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import SocialPost, Song
from app.routers.admin import verify_admin_key
from app.services.social import buffer_client
from app.services.social.broadcaster import DEFAULT_PLATFORMS, run_social_broadcast

logger = logging.getLogger(__name__)

router = APIRouter(tags=["social-admin"])

_ROW_CAP = 1200  # ~200 items at 6 platforms each


@router.get("/api/admin/social/broadcasts", dependencies=[Depends(verify_admin_key)])
def list_broadcasts(status: str = Query(""), db: Session = Depends(get_db)):
    # Status mix across every (item, platform) row.
    status_rows = (
        db.query(SocialPost.status, func.count(SocialPost.id))
        .group_by(SocialPost.status)
        .all()
    )
    by_status = {s: c for s, c in status_rows}

    q = db.query(SocialPost)
    if status in ("posted", "queued", "dark", "error", "skipped", "prepared"):
        q = q.filter(SocialPost.status == status)
    rows = q.order_by(SocialPost.created_at.desc()).limit(_ROW_CAP).all()

    # Collapse the per-platform rows into one entry per broadcast item.
    items: dict = {}
    order: list = []
    song_ids: set[int] = set()
    for r in rows:
        key = (r.scope, r.song_id if r.scope == "song" else r.reading_date)
        if key not in items:
            items[key] = {
                "scope": r.scope,
                "song_id": r.song_id,
                "reading_date": r.reading_date.isoformat() if r.reading_date else None,
                "post_text": r.post_text,
                "card_token": r.card_token,
                "charge_value": r.charge_value,
                "tier": r.tier,
                "trending_source": r.trending_source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "platforms": [],
            }
            order.append(key)
        if r.scope == "song" and r.song_id:
            song_ids.add(r.song_id)
        items[key]["platforms"].append({
            "platform": r.platform,
            "status": r.status,
            "post_external_id": r.post_external_id,
        })

    # Resolve song titles in one batch for the song items.
    titles: dict[int, tuple[str, str]] = {}
    if song_ids:
        for s in db.query(Song.id, Song.title, Song.artist).filter(Song.id.in_(song_ids)).all():
            titles[s.id] = (s.title, s.artist)

    out_items = []
    for key in order:
        it = items[key]
        if it["scope"] == "song" and it["song_id"] in titles:
            it["title"], it["artist"] = titles[it["song_id"]]
        elif it["scope"] == "charts":
            it["title"] = it.get("reading_date") and f"Daily charts {it['reading_date']}"
            it["artist"] = None
        else:
            it["title"] = it.get("reading_date") and f"Daily Listens {it['reading_date']}"
            it["artist"] = None
        it["platforms"].sort(key=lambda p: DEFAULT_PLATFORMS.index(p["platform"])
                             if p["platform"] in DEFAULT_PLATFORMS else 99)
        out_items.append(it)

    return {
        "config": {
            "enabled": settings.social_broadcast_enabled,
            "buffer_configured": buffer_client.is_configured(),
            "platforms": list(buffer_client.profile_map().keys()) or DEFAULT_PLATFORMS,
            "trending_count": settings.social_trending_count,
            "card_render_base": settings.card_render_base_url,
        },
        "stats": {
            "total": sum(by_status.values()),
            "posted": by_status.get("posted", 0),
            "queued": by_status.get("queued", 0),
            "dark": by_status.get("dark", 0),
            "error": by_status.get("error", 0),
            "skipped": by_status.get("skipped", 0),
            "items": len(order),
        },
        "capped": len(rows) >= _ROW_CAP,
        "items": out_items,
    }


@router.post("/api/admin/social/run", dependencies=[Depends(verify_admin_key)])
async def admin_run_broadcast():
    """Admin 'Run broadcast now' trigger (cookie auth). Same orchestrator as the
    cron -- selects, renders, ledgers, and (if Buffer is configured) pushes."""
    return await run_social_broadcast(trigger="admin")
