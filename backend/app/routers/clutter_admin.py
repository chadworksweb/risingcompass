"""LEIT clutter-control audit queue -- admin API.

The single human-review surface for `clutter_audits` rows from both feeders
(Lyrical Charger push-throughs and the daily sweep agent). List / detail /
resolve, plus a manual "run the sweep now" trigger. All session-gated.

Env-filtered by default (`environment='prod'`): local dev shares the prod DB via
the tunnel, so without this the queue would mix local test rows into the prod
worklist. The page passes `?environment=local` when testing locally.

"Remove" reuses the submissions delete path (orphan-aware song deletion) so a
confirmed clutter song is cleaned up exactly like an admin-deleted submission.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClutterAudit, Song
from app.routers.admin import verify_admin_key
from app.services import song_removal
from app.services.agents.leit_sweep import run_leit_sweep
from app.services import alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/clutter-audits", tags=["clutter-admin"])

VALID_ACTIONS = {"keep", "remove", "dismiss"}
_ACTION_TO_STATUS = {"keep": "kept", "remove": "removed", "dismiss": "dismissed"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _loads(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        logger.debug("clutter_admin: swallowed in _loads", exc_info=True)
        return s


def _row(r: ClutterAudit, song: Song | None) -> dict:
    return {
        "id": r.id,
        "song_id": r.song_id,
        "source": r.source,
        "category": r.category,
        "suggested_action": r.suggested_action,
        "reason": r.reason,
        "confidence": r.confidence,
        "status": r.status,
        "environment": r.environment,
        "payload": _loads(r.payload_json),
        "detected_at": _iso(r.detected_at),
        "reviewed_at": _iso(r.reviewed_at),
        "reviewed_by": r.reviewed_by,
        "review_notes": r.review_notes,
        # Live song fields (None if the song was already removed).
        "title": song.title if song else None,
        "artist": song.artist if song else None,
        "rubric_color": song.rubric_color if song else None,
    }


@router.get("", dependencies=[Depends(verify_admin_key)])
def list_audits(
    status: Optional[str] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
    environment: str = "prod",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """List clutter audits, freshest first. Defaults to prod env (filter out
    local test rows). Pass status=open for the active worklist."""
    limit = max(1, min(limit, 500))
    q = db.query(ClutterAudit)
    if environment:
        q = q.filter(ClutterAudit.environment == environment)
    if status:
        q = q.filter(ClutterAudit.status == status)
    if source:
        q = q.filter(ClutterAudit.source == source)
    if category:
        q = q.filter(ClutterAudit.category == category)
    rows = q.order_by(ClutterAudit.detected_at.desc()).limit(limit).all()

    song_ids = [r.song_id for r in rows if r.song_id is not None]
    songs = {}
    if song_ids:
        for s in db.query(Song).filter(Song.id.in_(song_ids)).all():
            songs[s.id] = s
    return {"items": [_row(r, songs.get(r.song_id)) for r in rows]}


@router.get("/stats", dependencies=[Depends(verify_admin_key)])
def stats(environment: str = "prod", db: Session = Depends(get_db)):
    """Header roll-up: open count + by-source + by-category (open only)."""
    base = db.query(ClutterAudit).filter(ClutterAudit.environment == environment)
    open_count = base.filter(ClutterAudit.status == "open").count()
    by_source = dict(
        base.filter(ClutterAudit.status == "open")
        .with_entities(ClutterAudit.source, func.count(ClutterAudit.id))
        .group_by(ClutterAudit.source).all()
    )
    by_category = dict(
        base.filter(ClutterAudit.status == "open")
        .with_entities(ClutterAudit.category, func.count(ClutterAudit.id))
        .group_by(ClutterAudit.category).all()
    )
    return {"open": open_count, "by_source": by_source, "by_category": by_category}


class ResolveIn(BaseModel):
    action: str  # keep | remove | dismiss
    notes: Optional[str] = None


@router.post("/{audit_id}/resolve", dependencies=[Depends(verify_admin_key)])
def resolve_audit(
    audit_id: int,
    data: ResolveIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Resolve a finding.
      - keep    : it's fine, leave the song -> status='kept'
      - remove  : delete the song (orphan-aware) -> status='removed'
      - dismiss : false positive -> status='dismissed'
    """
    action = (data.action or "").strip()
    if action not in VALID_ACTIONS:
        raise HTTPException(400, f"action must be one of {sorted(VALID_ACTIONS)}")

    row = db.query(ClutterAudit).filter(ClutterAudit.id == audit_id).first()
    if not row:
        raise HTTPException(404, "audit not found")

    song_removed = False
    kept_reason = None
    if action == "remove" and row.song_id is not None:
        # song_removal directly, NOT the submissions delete ENDPOINT. That one
        # fetches the song through an inner join on its lyrical_charger ingestion,
        # so it 404s on every chart-born, stream-born and terminal-born song -- and
        # this swallowed the 404 while still writing status='removed'. The queue
        # was therefore able to report a removal it had not performed, which is how
        # the `test / test` placeholder survived being removed twice.
        #
        # `ingestion_holds` stays FALSE here (the default). The feeder endpoints
        # set it because they delete their own ingestion first and a surviving one
        # means another feeder still claims the song. The clutter queue removes a
        # song a human judged to be clutter whatever fed it, so the ingestion is
        # the provenance of the thing being removed, not a reason to keep it.
        result = song_removal.remove_song(db, row.song_id)
        song_removed = bool(result["song_removed"])
        kept_reason = result["kept_reason"]

    # A remove that removed nothing is NOT resolved. Leaving it open is the whole
    # point: the queue's job is to be trustworthy about what is still in the
    # Library, and silently closing a row over a song that is still live is the
    # exact failure this replaced.
    if action == "remove" and not song_removed:
        # remove_song deletes nothing on the kept branch, so there is nothing to
        # roll back and nothing to commit.
        raise HTTPException(
            409,
            f"Song kept: {kept_reason}. The finding stays open -- resolve it as "
            f"'keep' or 'dismiss' if that reference is legitimate.",
        )

    row.status = _ACTION_TO_STATUS[action]
    row.reviewed_at = datetime.now(timezone.utc)
    row.reviewed_by = getattr(request.state, "admin_username", None) or "admin"
    row.review_notes = (data.notes or None)
    db.commit()
    return {"id": audit_id, "status": row.status, "song_removed": song_removed}


@router.post("/run-sweep", dependencies=[Depends(verify_admin_key)])
async def run_sweep_now():
    """Trigger the LEIT clutter sweep on demand (admin session). Same orchestrator
    the nightly cron runs; emails the digest if anything was flagged."""
    summary = await run_leit_sweep(trigger="admin")
    if summary.get("flagged"):
        try:
            alerts.emit_leit_sweep_digest(
                scanned=summary["scanned"], findings=summary["findings"],
            )
        except Exception:
            logger.exception("leit_sweep digest alert failed (non-fatal)")
    return summary
