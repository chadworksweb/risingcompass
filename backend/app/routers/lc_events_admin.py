"""Admin endpoints for Lyrical Charger event log."""

import json
from datetime import datetime, date, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LcEvent, Release, Artist
from app.constants import COLOR_LABELS
from app.routers.admin import verify_admin_key

router = APIRouter(prefix="/api/admin/lc-events", tags=["lc-events-admin"])


def _serialize(evt: LcEvent) -> dict:
    return {
        "id": evt.id,
        "occurred_at": evt.occurred_at.isoformat() if evt.occurred_at else None,
        "event_type": evt.event_type,
        "ip_address": evt.ip_address,
        "user_agent": evt.user_agent,
        "referrer": evt.referrer,
        "payload": json.loads(evt.payload_json) if evt.payload_json else None,
        "song_id": evt.song_id,
    }


@router.get("", dependencies=[Depends(verify_admin_key)])
def list_events(
    event_type: str | None = Query(None),
    ip: str | None = Query(None),
    since: date | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Reverse-chronological event log with optional filters."""
    q = db.query(LcEvent)
    if event_type:
        q = q.filter(LcEvent.event_type == event_type)
    if ip:
        q = q.filter(LcEvent.ip_address == ip)
    if since:
        q = q.filter(LcEvent.occurred_at >= datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc))

    total = q.count()
    rows = q.order_by(desc(LcEvent.occurred_at)).offset(offset).limit(limit).all()
    return {"total": total, "events": [_serialize(r) for r in rows]}


@router.get("/stats", dependencies=[Depends(verify_admin_key)])
def event_stats(db: Session = Depends(get_db)):
    """Top-level activity stats for the dashboard cards."""
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
    week_start = now - timedelta(days=7)

    total = db.query(func.count(LcEvent.id)).scalar() or 0
    today = db.query(func.count(LcEvent.id)).filter(LcEvent.occurred_at >= today_start).scalar() or 0
    week = db.query(func.count(LcEvent.id)).filter(LcEvent.occurred_at >= week_start).scalar() or 0

    by_type_rows = (
        db.query(LcEvent.event_type, func.count(LcEvent.id))
        .filter(LcEvent.occurred_at >= week_start)
        .group_by(LcEvent.event_type)
        .all()
    )
    by_type = {r[0]: r[1] for r in by_type_rows}

    top_ips_rows = (
        db.query(LcEvent.ip_address, func.count(LcEvent.id))
        .filter(LcEvent.occurred_at >= week_start)
        .filter(LcEvent.ip_address.isnot(None))
        .group_by(LcEvent.ip_address)
        .order_by(desc(func.count(LcEvent.id)))
        .limit(10)
        .all()
    )
    top_ips = [{"ip": r[0], "count": r[1]} for r in top_ips_rows]

    top_refs_rows = (
        db.query(LcEvent.referrer, func.count(LcEvent.id))
        .filter(LcEvent.occurred_at >= week_start)
        .filter(LcEvent.referrer.isnot(None))
        .filter(LcEvent.referrer != "")
        .group_by(LcEvent.referrer)
        .order_by(desc(func.count(LcEvent.id)))
        .limit(10)
        .all()
    )
    top_refs = [{"referrer": r[0], "count": r[1]} for r in top_refs_rows]

    unique_ips_week = (
        db.query(func.count(func.distinct(LcEvent.ip_address)))
        .filter(LcEvent.occurred_at >= week_start)
        .filter(LcEvent.ip_address.isnot(None))
        .scalar() or 0
    )

    return {
        "total": total,
        "today": today,
        "week": week,
        "unique_ips_week": unique_ips_week,
        "by_type_week": by_type,
        "top_ips_week": top_ips,
        "top_referrers_week": top_refs,
    }


@router.get("/albums", dependencies=[Depends(verify_admin_key)])
def album_charger_activity(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Album Charger monitoring: counts of albums charged plus the most
    recent ones. Albums charged through the tool are Releases tagged
    source='album_charger' (distinct from MusicBrainz/Spotify-derived ones)."""
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
    week_start = now - timedelta(days=7)

    base = db.query(Release).filter(Release.source == "album_charger")
    total = base.count()
    today = base.filter(Release.submitted_at >= today_start).count()
    week = base.filter(Release.submitted_at >= week_start).count()

    rows = (
        db.query(Release, Artist.name, Artist.slug)
        .join(Artist, Artist.id == Release.artist_id)
        .filter(Release.source == "album_charger")
        .order_by(desc(Release.submitted_at))
        .limit(limit)
        .all()
    )
    recent = [{
        "release_id": rel.id,
        "title": rel.title,
        "artist": artist_name,
        "artist_slug": artist_slug,
        "release_type": rel.release_type,
        "release_date": rel.release_date.isoformat() if rel.release_date else None,
        "release_year": rel.release_year,
        "charge_value": rel.charge_value,
        "rubric_color": rel.rubric_color,
        "tier_label": COLOR_LABELS.get(rel.rubric_color) if rel.rubric_color else None,
        "track_count": rel.track_count,
        "calibrated_count": rel.calibrated_count,
        "contamination_count": rel.contamination_count,
        "submitted_at": rel.submitted_at.isoformat() if rel.submitted_at else None,
    } for rel, artist_name, artist_slug in rows]

    return {"total": total, "today": today, "week": week, "recent": recent}
