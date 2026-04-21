"""Calibration Log — admin promote endpoint + public read endpoints.

The Calibration Log is the public record of how the compass is being refined.
Each capture table (pre_publish_corrections, song_recalibrations today) lands
new rows with promoted_to_feed=false by default. The admin router holds the
endpoint that flips that flag — the deliberate editorial act that puts an
event into the public feed. The public router exposes the promoted entries
as a unified feed + per-entry detail view.

The source_table path param dispatches to the right table. Adding future
capture tables (non-song rubric updates, vibe resolutions) is a matter of
extending _TABLE_REGISTRY; the endpoint shape stays stable.

See RISING-COMPASS-CALIBRATION-LOG.md.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PrePublishCorrection, SongRecalibration
from app.schemas import CalibrationLogPromoteIn, FeedEntry, FeedListOut
from app.routers.admin import verify_admin_key
from app.services.calibration_log_feed import (
    list_feed_entries,
    _correction_to_entry,
    _recalibration_to_entry,
)

router = APIRouter(prefix="/api/admin/calibration-log", tags=["calibration-log"])
public_router = APIRouter(prefix="/api/calibration-log", tags=["calibration-log"])


# Maps source_table path param → (model class, human-readable label for errors).
_TABLE_REGISTRY = {
    "pre_publish_corrections": (PrePublishCorrection, "pre-publish correction"),
    "song_recalibrations": (SongRecalibration, "recalibration"),
}


def _is_admin(x_admin_key: Optional[str]) -> bool:
    return bool(x_admin_key) and x_admin_key == settings.rc_admin_key


@router.post("/{source_table}/{event_id}/promote", dependencies=[Depends(verify_admin_key)])
def promote_event(
    source_table: str,
    event_id: int,
    data: CalibrationLogPromoteIn,
    db: Session = Depends(get_db),
):
    """Flip an audit row to promoted_to_feed=true, stamping promoted_at.

    Optionally updates human_rationale and tags at promote time so the admin
    can correct fast without writing prose, then come back later to promote
    with reasoning attached.

    Idempotent on already-promoted rows — re-calling updates prose/tags and
    refreshes promoted_at.
    """
    entry = _TABLE_REGISTRY.get(source_table)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown source_table '{source_table}'. "
                   f"Valid: {', '.join(_TABLE_REGISTRY.keys())}",
        )
    model_cls, label = entry

    row = db.query(model_cls).filter(model_cls.id == event_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"No {label} with id {event_id}")

    row.promoted_to_feed = True
    row.promoted_at = datetime.utcnow()
    if data.human_rationale is not None:
        row.human_rationale = data.human_rationale
    if data.tags is not None:
        row.tags = data.tags

    db.commit()
    db.refresh(row)

    return {
        "source_table": source_table,
        "event_id": event_id,
        "promoted_to_feed": True,
        "promoted_at": row.promoted_at.isoformat(),
    }


@public_router.get("", response_model=FeedListOut)
def list_calibration_log(
    event_type: Optional[str] = Query(default=None, description="Filter: pre_publish_correction, recalibration"),
    song_source: Optional[str] = Query(default=None, description="With song_id: restrict to one song's events"),
    song_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    x_admin_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Unified Calibration Log feed. Chronological (newest first) across
    every capture table.

    Public callers see only promoted entries. Admin key (X-Admin-Key) returns
    the full audit trail including un-promoted rows — same endpoint, two
    views of the same data.

    Filtering: event_type narrows to one capture type; song_source + song_id
    narrow to one song's events. Both filters compose.
    """
    include_unpromoted = _is_admin(x_admin_key)
    types = [event_type] if event_type else None
    entries, total = list_feed_entries(
        db,
        include_unpromoted=include_unpromoted,
        event_types=types,
        song_source=song_source,
        song_id=song_id,
        limit=limit,
        offset=offset,
    )
    return FeedListOut(
        items=[FeedEntry.model_validate(e) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )


@public_router.get("/{source_table}/{event_id}", response_model=FeedEntry)
def get_calibration_log_entry(
    source_table: str,
    event_id: int,
    x_admin_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Single feed entry. Matches the shape returned by the list endpoint.

    Public callers get 404 on un-promoted rows; admin key returns regardless.
    """
    entry = _TABLE_REGISTRY.get(source_table)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown source_table '{source_table}'. "
                   f"Valid: {', '.join(_TABLE_REGISTRY.keys())}",
        )
    model_cls, label = entry
    row = db.query(model_cls).filter(model_cls.id == event_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"No {label} with id {event_id}")

    if not row.promoted_to_feed and not _is_admin(x_admin_key):
        raise HTTPException(status_code=404, detail=f"No {label} with id {event_id}")

    slug_cache: dict = {}
    if source_table == "pre_publish_corrections":
        data = _correction_to_entry(row, db, slug_cache)
    else:
        data = _recalibration_to_entry(row, db, slug_cache)
    return FeedEntry.model_validate(data)
