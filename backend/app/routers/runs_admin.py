"""Calibration Runs admin -- a read-only window onto `calibration_runs`.

calibration_runs is the run ledger / training corpus behind every song's
consensus calibration: one row per calibration pass (initial + every
additional run), keyed to the atomic songs.id. This admin section surfaces it
two ways:

- All Runs: a flat reverse-chron list across every song (filterable by song,
  trigger, time window, superseded state).
- By Song: songs ranked by how many runs they've accumulated (most-calibrated
  first), with the current canonical tier/charge.

`run_at` IS each run's creation time; it is surfaced to the UI as "Created".
Admin cookie auth only (verify_admin_key) -- never publicly exposed.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CalibrationRun, Song
from app.routers.admin import verify_admin_key
from app.services.calibration_corpus import PUBLIC_RUN_CAP
from app.services.song_search import _attach_slugs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs-admin"])

COLOR_LABELS = {
    "violet": "Ascended", "blue": "Elevated", "green": "Decent",
    "orange": "Degraded", "red": "Corrupted",
}

_WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _window_cutoff(window: str | None):
    if window and window in _WINDOWS:
        return datetime.utcnow() - _WINDOWS[window]
    return None


def _attach_song_slugs(items: list[dict], db: Session) -> None:
    """Attach a public slug per row using its song_id/title/artist."""
    slug_items = [
        {"id": it["song_id"], "title": it.get("title") or "", "artist": it.get("artist")}
        for it in items if it.get("song_id")
    ]
    _attach_slugs(slug_items, db)
    slug_map = {s["id"]: s["slug"] for s in slug_items}
    for it in items:
        it["slug"] = slug_map.get(it.get("song_id"))


def _run_dict(r: CalibrationRun) -> dict:
    return {
        "id": r.id,
        "song_id": r.song_id,
        # run_at is the run's creation time -- surfaced as "Created" in the UI.
        "created_at": r.run_at.isoformat() if r.run_at else None,
        "title": r.title,
        "artist": r.artist,
        "rubric_color": r.rubric_color,
        "tier_label": COLOR_LABELS.get(r.rubric_color or "", ""),
        "charge_value": r.charge_value,
        "confidence": r.confidence,
        "contaminated": bool(r.contaminated) if r.contaminated is not None else False,
        "triggered_by": r.triggered_by,
        "agent_model": r.agent_model,
        "charge_summary": r.charge_summary,
        # The agent's structured argument (lyric-scrubbed at write time in
        # log_run). NULL on legacy runs that predate reasoning capture.
        "reasoning": r.reasoning,
        "superseded": bool(r.superseded) if r.superseded is not None else False,
        "superseded_reason": r.superseded_reason,
        "superseded_at": r.superseded_at.isoformat() if r.superseded_at else None,
        "calibration_failed": (
            bool(r.calibration_failed)
            if getattr(r, "calibration_failed", None) is not None else False
        ),
    }


@router.get("/api/admin/runs", dependencies=[Depends(verify_admin_key)])
def list_runs(
    song_id: int | None = None,
    triggered_by: str | None = None,
    window: str | None = Query(None, description="24h | 7d | 30d | all"),
    include_superseded: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Flat list of calibration runs, newest first (ordered by run_at = Created)."""
    q = db.query(CalibrationRun)
    if song_id is not None:
        q = q.filter(CalibrationRun.song_id == song_id)
    if triggered_by:
        q = q.filter(CalibrationRun.triggered_by == triggered_by)
    if not include_superseded:
        q = q.filter(CalibrationRun.superseded.is_(False))
    cutoff = _window_cutoff(window)
    if cutoff is not None:
        q = q.filter(CalibrationRun.run_at >= cutoff)

    total = q.order_by(None).count()
    rows = (
        q.order_by(CalibrationRun.run_at.desc())
        .limit(limit).offset(offset).all()
    )
    items = [_run_dict(r) for r in rows]
    _attach_song_slugs(items, db)

    # Distinct triggers for the filter UI (small set -- cheap distinct).
    triggers = sorted(
        t[0] for t in db.query(CalibrationRun.triggered_by).distinct().all() if t[0]
    )
    return {
        "runs": items, "total": total, "limit": limit, "offset": offset,
        "triggers": triggers,
    }


@router.get("/api/admin/runs/by-song", dependencies=[Depends(verify_admin_key)])
def runs_by_song(
    window: str | None = Query(None, description="24h | 7d | 30d | all"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Songs ranked by number of logged runs (most-calibrated first), with the
    current canonical tier/charge and when the song was last run."""
    superseded_sum = func.sum(
        case((CalibrationRun.superseded.is_(True), 1), else_=0)
    ).label("superseded_count")
    base = (
        db.query(
            CalibrationRun.song_id,
            func.count().label("run_count"),
            func.max(CalibrationRun.run_at).label("last_at"),
            superseded_sum,
        )
        .filter(CalibrationRun.song_id.isnot(None))
    )
    cutoff = _window_cutoff(window)
    if cutoff is not None:
        base = base.filter(CalibrationRun.run_at >= cutoff)
    base = base.group_by(CalibrationRun.song_id)

    total = base.order_by(None).count()
    rows = (
        base.order_by(func.count().desc(), func.max(CalibrationRun.run_at).desc())
        .limit(limit).offset(offset).all()
    )
    song_ids = [r[0] for r in rows]
    song_map = (
        {s.id: s for s in db.query(Song).filter(Song.id.in_(song_ids)).all()}
        if song_ids else {}
    )
    items = []
    for sid, run_count, last_at, superseded_count in rows:
        s = song_map.get(sid)
        sup = int(superseded_count or 0)
        live = run_count - sup
        items.append({
            "song_id": sid,
            "title": getattr(s, "title", None),
            "artist": getattr(s, "artist", None),
            "rubric_color": getattr(s, "rubric_color", None),
            "tier_label": COLOR_LABELS.get(getattr(s, "rubric_color", "") or "", ""),
            "charge_value": getattr(s, "charge_value", None),
            "run_count": run_count,
            "superseded_count": sup,
            "live_count": live,
            "last_at": last_at.isoformat() if last_at else None,
            # Maxed for public consumption: at/over the cap on LIVE
            # (non-superseded) runs, the public LC refuses new runs
            # (admin/terminal only). Superseding prior runs reopens it.
            "capped": live >= PUBLIC_RUN_CAP,
        })
    _attach_song_slugs(items, db)
    return {"songs": items, "total": total, "limit": limit, "offset": offset,
            "run_cap": PUBLIC_RUN_CAP}
