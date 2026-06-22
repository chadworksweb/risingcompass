"""Sentinel Auditor Team -- admin API.

The triage surface for the red-team program: review applications (approve / reject
/ revoke), and drive findings through their lifecycle (status transitions +
severity override + disposition). Plus the dark-launch flag toggle (mirrors
launch_admin). All session-cookie gated.

NOT gated by `sentinel_auditor.enabled` -- the admin side stays usable while the
public side is dark, so Chad can configure and review before launch.

Env-filtered by default (`environment='prod'`): local dev shares the prod DB via
the tunnel, so without this the findings queue would mix local test rows into the
prod worklist. The page passes `?environment=local` when testing locally.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SentinelAuditor, SentinelFinding, Song, SongSlug, User
from app.routers.admin import verify_admin_key
from app.services import sentinel
from app.services.feature_flags import (
    is_sentinel_auditor_enabled,
    set_sentinel_auditor_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/sentinel", tags=["sentinel-admin"])

_REVIEW_TO_STATUS = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------- applications ----------

def _app_row(a: SentinelAuditor, user: User | None, rep: dict | None) -> dict:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "handle": (user.handle if user else None) or a.handle_snapshot,
        "anon_id": user.anon_id if user else None,
        "status": a.status,
        "motivation": a.motivation,
        "focus_area": a.focus_area,
        "review_notes": a.review_notes,
        "reviewed_by": a.reviewed_by,
        "reviewed_at": _iso(a.reviewed_at),
        "applied_at": _iso(a.applied_at),
        "reputation": rep,
    }


@router.get("/applications", dependencies=[Depends(verify_admin_key)])
def list_applications(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List auditor applications, freshest first. Pass status=pending for the
    review worklist."""
    q = db.query(SentinelAuditor)
    if status:
        q = q.filter(SentinelAuditor.status == status)
    rows = q.order_by(SentinelAuditor.applied_at.desc()).limit(500).all()
    user_ids = [r.user_id for r in rows]
    users = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            users[u.id] = u
    return {"items": [
        _app_row(r, users.get(r.user_id),
                 sentinel.reputation(db, r.id) if r.status == "approved" else None)
        for r in rows
    ]}


@router.get("/applications/stats", dependencies=[Depends(verify_admin_key)])
def application_stats(db: Session = Depends(get_db)):
    by_status = dict(
        db.query(SentinelAuditor.status, func.count(SentinelAuditor.id))
        .group_by(SentinelAuditor.status).all()
    )
    return {"by_status": by_status, "pending": by_status.get("pending", 0)}


class ReviewIn(BaseModel):
    action: str  # approve | reject | revoke
    notes: Optional[str] = None


@router.post("/applications/{auditor_id}/review", dependencies=[Depends(verify_admin_key)])
def review_application(
    auditor_id: int,
    data: ReviewIn,
    request: Request,
    db: Session = Depends(get_db),
):
    action = (data.action or "").strip()
    if action not in _REVIEW_TO_STATUS:
        raise HTTPException(400, f"action must be one of {sorted(_REVIEW_TO_STATUS)}")
    row = db.query(SentinelAuditor).filter(SentinelAuditor.id == auditor_id).first()
    if not row:
        raise HTTPException(404, "application not found")
    row.status = _REVIEW_TO_STATUS[action]
    row.reviewed_at = datetime.utcnow()
    row.reviewed_by = getattr(request.state, "admin_username", None) or "admin"
    if data.notes is not None:
        row.review_notes = data.notes.strip() or None
    db.commit()
    return {"id": auditor_id, "status": row.status}


# ---------- findings ----------

def _finding_row(f: SentinelFinding, aud: SentinelAuditor | None,
                 user: User | None, song: Song | None, slug: str | None) -> dict:
    handle = None
    if user:
        handle = user.handle
    if not handle and aud:
        handle = aud.handle_snapshot
    return {
        "id": f.id,
        "auditor_id": f.auditor_id,
        "handle": handle,
        "scope": f.scope,
        "category": f.category,
        "title": f.title,
        "description": f.description,
        "evidence_url": f.evidence_url,
        "proposed_severity": f.proposed_severity,
        "accepted_severity": f.accepted_severity,
        "status": f.status,
        "disposition": f.disposition,
        "points_awarded": f.points_awarded,
        "environment": f.environment,
        "created_at": _iso(f.created_at),
        "reviewed_at": _iso(f.reviewed_at),
        "reviewed_by": f.reviewed_by,
        "song_id": f.song_id,
        "song_title": song.title if song else None,
        "song_artist": song.artist if song else None,
        "song_slug": slug,
    }


def _hydrate(db: Session, rows: list[SentinelFinding]) -> list[dict]:
    auditor_ids = [r.auditor_id for r in rows]
    song_ids = [r.song_id for r in rows if r.song_id is not None]
    auditors, users, songs, slugs = {}, {}, {}, {}
    if auditor_ids:
        for a in db.query(SentinelAuditor).filter(SentinelAuditor.id.in_(auditor_ids)).all():
            auditors[a.id] = a
        uids = [a.user_id for a in auditors.values()]
        if uids:
            for u in db.query(User).filter(User.id.in_(uids)).all():
                users[u.id] = u
    if song_ids:
        for s in db.query(Song).filter(Song.id.in_(song_ids)).all():
            songs[s.id] = s
        for sl in db.query(SongSlug).filter(SongSlug.song_id.in_(song_ids)).all():
            slugs.setdefault(sl.song_id, sl.slug)
    out = []
    for r in rows:
        aud = auditors.get(r.auditor_id)
        user = users.get(aud.user_id) if aud else None
        out.append(_finding_row(r, aud, user, songs.get(r.song_id), slugs.get(r.song_id)))
    return out


@router.get("/findings", dependencies=[Depends(verify_admin_key)])
def list_findings(
    status: Optional[str] = None,
    category: Optional[str] = None,
    scope: Optional[str] = None,
    environment: str = "prod",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """List findings, freshest first. Defaults to prod env (filter out local test
    rows). Pass status to scope the worklist."""
    limit = max(1, min(limit, 500))
    q = db.query(SentinelFinding)
    if environment:
        q = q.filter(SentinelFinding.environment == environment)
    if status:
        q = q.filter(SentinelFinding.status == status)
    if category:
        q = q.filter(SentinelFinding.category == category)
    if scope:
        q = q.filter(SentinelFinding.scope == scope)
    rows = q.order_by(SentinelFinding.created_at.desc()).limit(limit).all()
    return {"items": _hydrate(db, rows)}


@router.get("/findings/stats", dependencies=[Depends(verify_admin_key)])
def finding_stats(environment: str = "prod", db: Session = Depends(get_db)):
    base = db.query(SentinelFinding).filter(SentinelFinding.environment == environment)
    by_status = dict(
        base.with_entities(SentinelFinding.status, func.count(SentinelFinding.id))
        .group_by(SentinelFinding.status).all()
    )
    open_count = sum(by_status.get(s, 0) for s in sentinel.ACTIVE_STATUSES)
    return {"by_status": by_status, "open": open_count,
            "accepted": by_status.get("accepted", 0)}


@router.get("/findings/{finding_id}", dependencies=[Depends(verify_admin_key)])
def finding_detail(finding_id: int, db: Session = Depends(get_db)):
    row = db.query(SentinelFinding).filter(SentinelFinding.id == finding_id).first()
    if not row:
        raise HTTPException(404, "finding not found")
    return _hydrate(db, [row])[0]


class StatusIn(BaseModel):
    to_status: str
    disposition: Optional[str] = None


@router.post("/findings/{finding_id}/status", dependencies=[Depends(verify_admin_key)])
def set_status(
    finding_id: int,
    data: StatusIn,
    request: Request,
    db: Session = Depends(get_db),
):
    row = db.query(SentinelFinding).filter(SentinelFinding.id == finding_id).first()
    if not row:
        raise HTTPException(404, "finding not found")
    actor = getattr(request.state, "admin_username", None) or "admin"
    try:
        sentinel.apply_status(db, row, (data.to_status or "").strip(),
                              actor_ref=actor, disposition=data.disposition)
    except sentinel.TransitionError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"id": finding_id, "status": row.status, "points_awarded": row.points_awarded}


class SeverityIn(BaseModel):
    accepted_severity: str


@router.post("/findings/{finding_id}/severity", dependencies=[Depends(verify_admin_key)])
def set_severity(
    finding_id: int,
    data: SeverityIn,
    request: Request,
    db: Session = Depends(get_db),
):
    row = db.query(SentinelFinding).filter(SentinelFinding.id == finding_id).first()
    if not row:
        raise HTTPException(404, "finding not found")
    actor = getattr(request.state, "admin_username", None) or "admin"
    try:
        sentinel.set_severity(db, row, (data.accepted_severity or "").strip(),
                              actor_ref=actor)
    except sentinel.TransitionError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"id": finding_id, "accepted_severity": row.accepted_severity,
            "points_awarded": row.points_awarded}


# ---------- dark-launch flag toggle ----------

class FlagIn(BaseModel):
    enabled: bool


@router.get("/flag", dependencies=[Depends(verify_admin_key)])
def get_flag(db: Session = Depends(get_db)):
    return {"enabled": is_sentinel_auditor_enabled(db)}


@router.post("/flag/toggle", dependencies=[Depends(verify_admin_key)])
def toggle_flag(data: FlagIn, db: Session = Depends(get_db)):
    set_sentinel_auditor_enabled(db, data.enabled)
    return {"enabled": is_sentinel_auditor_enabled(db)}
