"""Song identity-resolution Phase 2 -- merge-candidate queue + song-merge admin API.

The human-review surface for likely DUPLICATE song rows. Mirrors the clutter
audit queue (`clutter_admin.py`): list / stats / resolve, all session-gated and
env-filtered (local dev shares the prod DB via the tunnel). Resolving a candidate
'merge' invokes the destructive `song_merge.merge_songs` -- the SAME atomic
repoint+delete the direct `POST /songs/{id}/merge-into` endpoint runs.

NEVER auto-merges: a cover / remix / live version that shares a title is a
DISTINCT work (songs-not-artists), so every collapse is the admin's explicit call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Song, SongMergeCandidate, SongMergeEvent
from app.routers.admin import verify_admin_key
from app.services.song_merge import merge_songs, MergeError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["song-merge-admin"])

VALID_ACTIONS = {"merge", "keep_separate", "dismiss"}
_ACTION_TO_STATUS = {"keep_separate": "kept_separate", "dismiss": "dismissed"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _song_brief(s: Song | None) -> dict | None:
    if not s:
        return None
    return {
        "id": s.id, "title": s.title, "artist": s.artist,
        "rubric_color": s.rubric_color, "charge_value": s.charge_value,
        "canonical_calibration_method": s.canonical_calibration_method,
    }


def _cand_row(r: SongMergeCandidate, songs: dict) -> dict:
    return {
        "id": r.id,
        "source_song_id": r.source_song_id,
        "target_song_id": r.target_song_id,
        "reason": r.reason,
        "confidence": r.confidence,
        "detected_by": r.detected_by,
        "status": r.status,
        "environment": r.environment,
        "detected_at": _iso(r.detected_at),
        "reviewed_at": _iso(r.reviewed_at),
        "reviewed_by": r.reviewed_by,
        "review_notes": r.review_notes,
        "source": _song_brief(songs.get(r.source_song_id)),
        "target": _song_brief(songs.get(r.target_song_id)),
    }


@router.get("/song-merge-candidates", dependencies=[Depends(verify_admin_key)])
def list_candidates(
    status: str = "open",
    reason: Optional[str] = None,
    environment: str = "prod",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """List merge candidates, freshest first. Defaults to OPEN + prod env (the
    active prod worklist). Pass status='' for all, environment='local' to test."""
    limit = max(1, min(limit, 500))
    q = db.query(SongMergeCandidate)
    if environment:
        q = q.filter(SongMergeCandidate.environment == environment)
    if status:
        q = q.filter(SongMergeCandidate.status == status)
    if reason:
        q = q.filter(SongMergeCandidate.reason == reason)
    rows = q.order_by(SongMergeCandidate.detected_at.desc()).limit(limit).all()

    ids = {r.source_song_id for r in rows} | {r.target_song_id for r in rows}
    ids.discard(None)
    songs = {s.id: s for s in db.query(Song).filter(Song.id.in_(ids)).all()} if ids else {}
    return {"items": [_cand_row(r, songs) for r in rows]}


@router.get("/song-merge-candidates/stats", dependencies=[Depends(verify_admin_key)])
def candidate_stats(environment: str = "prod", db: Session = Depends(get_db)):
    base = db.query(SongMergeCandidate).filter(SongMergeCandidate.environment == environment)
    open_count = base.filter(SongMergeCandidate.status == "open").count()
    by_reason = dict(
        base.filter(SongMergeCandidate.status == "open")
        .with_entities(SongMergeCandidate.reason, func.count(SongMergeCandidate.id))
        .group_by(SongMergeCandidate.reason).all()
    )
    return {"open": open_count, "by_reason": by_reason}


class ResolveCandidateIn(BaseModel):
    action: str                      # merge | keep_separate | dismiss
    keep_id: Optional[int] = None    # required for 'merge': the song that SURVIVES
    notes: Optional[str] = None


@router.post("/song-merge-candidates/{cand_id}/resolve", dependencies=[Depends(verify_admin_key)])
def resolve_candidate(cand_id: int, data: ResolveCandidateIn, request: Request,
                      db: Session = Depends(get_db)):
    """Resolve a candidate:
      - merge         : collapse the pair (keep_id survives, the other is deleted).
      - keep_separate : confirmed distinct works (cover/remix/different song).
      - dismiss       : noise / already handled.
    """
    action = (data.action or "").strip()
    if action not in VALID_ACTIONS:
        raise HTTPException(400, f"action must be one of {sorted(VALID_ACTIONS)}")
    cand = db.query(SongMergeCandidate).filter(SongMergeCandidate.id == cand_id).first()
    if not cand:
        raise HTTPException(404, "candidate not found")
    if cand.status != "open":
        raise HTTPException(400, f"candidate already resolved ({cand.status})")

    actor = getattr(request.state, "admin_username", None) or "admin"
    result: dict = {"id": cand_id}

    if action == "merge":
        pair = {cand.source_song_id, cand.target_song_id}
        if data.keep_id not in pair:
            raise HTTPException(400, "keep_id must be one of the candidate's two song ids")
        keep_id = data.keep_id
        drop_id = (pair - {keep_id}).pop()
        # Supersede any OTHER open candidate that references the soon-deleted song
        # BEFORE the merge -- once merge_songs deletes it the FK nulls that column
        # and the rows become unfindable by id. Keeps the queue clean.
        for other in db.query(SongMergeCandidate).filter(
            SongMergeCandidate.status == "open",
            SongMergeCandidate.id != cand_id,
            (SongMergeCandidate.source_song_id == drop_id)
            | (SongMergeCandidate.target_song_id == drop_id),
        ).all():
            other.status = "superseded"
            other.reviewed_at = datetime.utcnow()
            other.reviewed_by = actor
        db.flush()
        try:
            rewrites = merge_songs(
                db.connection(), drop_id, keep_id,
                actor=actor, notes=data.notes, environment=settings.environment,
            )
        except MergeError as exc:
            raise HTTPException(400, str(exc))
        cand.status = "merged"
        result["rewrites"] = rewrites
        result["kept"] = keep_id
        result["dropped"] = drop_id
    else:
        cand.status = _ACTION_TO_STATUS[action]

    cand.reviewed_at = datetime.utcnow()
    cand.reviewed_by = actor
    cand.review_notes = data.notes or None
    db.commit()
    result["status"] = cand.status
    return result


class MergeIntoIn(BaseModel):
    target_id: int
    notes: Optional[str] = None


@router.post("/songs/{source_id}/merge-into", dependencies=[Depends(verify_admin_key)])
def merge_song_into(source_id: int, data: MergeIntoIn, request: Request,
                    db: Session = Depends(get_db)):
    """Directly merge `source_id` INTO `target_id` (source is deleted). The analog
    of the artist merge-into. Use this to clean a known duplicate without going
    through the candidate queue."""
    actor = getattr(request.state, "admin_username", None) or "admin"
    # Resolve any open candidate touching the soon-deleted source BEFORE the merge
    # (afterwards the FK nulls source_song_id and they're unfindable by id).
    for cand in db.query(SongMergeCandidate).filter(
        SongMergeCandidate.status == "open",
        (SongMergeCandidate.source_song_id == source_id)
        | (SongMergeCandidate.target_song_id == source_id),
    ).all():
        cand.status = "merged"
        cand.reviewed_at = datetime.utcnow()
        cand.reviewed_by = actor
    db.flush()
    try:
        rewrites = merge_songs(
            db.connection(), source_id, data.target_id,
            actor=actor, notes=data.notes, environment=settings.environment,
        )
    except MergeError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"source_id": source_id, "target_id": data.target_id, "rewrites": rewrites}


@router.get("/song-merge-events", dependencies=[Depends(verify_admin_key)])
def list_merge_events(environment: str = "prod", limit: int = 100,
                      db: Session = Depends(get_db)):
    """Recent applied merges (the permanent audit log)."""
    limit = max(1, min(limit, 500))
    rows = (
        db.query(SongMergeEvent)
        .filter(SongMergeEvent.environment == environment)
        .order_by(SongMergeEvent.occurred_at.desc())
        .limit(limit).all()
    )
    out = []
    for r in rows:
        try:
            rewrites = json.loads(r.rewrites_json) if r.rewrites_json else None
        except Exception:
            rewrites = None
        out.append({
            "id": r.id, "occurred_at": _iso(r.occurred_at), "actor": r.actor,
            "source_song_id": r.source_song_id, "source_title": r.source_title,
            "source_artist": r.source_artist, "target_song_id": r.target_song_id,
            "target_title": r.target_title, "target_artist": r.target_artist,
            "rewrites": rewrites, "notes": r.notes,
        })
    return {"items": out}
