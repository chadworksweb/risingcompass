"""Calibration Log — public read endpoints.

The Calibration Log is the public record of how the compass is being refined.
Each capture table (pre_publish_corrections, song_recalibrations) lands new
rows already auto-promoted to the public feed (2026-04-23 — manual promote
step retired). The public router exposes the feed + per-entry detail view.

The source_table path param dispatches to the right table. Adding future
capture tables (non-song rubric updates, vibe resolutions) is a matter of
extending _TABLE_REGISTRY; the endpoint shape stays stable.

See RISING-COMPASS-CALIBRATION-LOG.md.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import optional_admin_session
from app.database import get_db
from app.models import PrePublishCorrection, SongRecalibration, RubricChange
from app.schemas import FeedEntry, FeedListOut
from app.services.calibration_log_feed import (
    list_feed_entries,
    _correction_to_entry,
    _recalibration_to_entry,
    _rubric_change_to_entry,
    linked_songs_by_slug,
)

router = APIRouter(prefix="/api/admin/calibration-log", tags=["calibration-log"])
public_router = APIRouter(prefix="/api/calibration-log", tags=["calibration-log"])


# Maps source_table path param → (model class, human-readable label for errors).
_TABLE_REGISTRY = {
    "pre_publish_corrections": (PrePublishCorrection, "pre-publish correction"),
    "song_recalibrations": (SongRecalibration, "recalibration"),
    "rubric_changes": (RubricChange, "rubric change"),
}


@public_router.get("", response_model=FeedListOut)
def list_calibration_log(
    event_type: Optional[str] = Query(default=None, description="Filter: pre_publish_correction, recalibration, rubric_change"),
    song_source: Optional[str] = Query(default=None, description="With song_id: restrict to one song's events"),
    song_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin=Depends(optional_admin_session),
    db: Session = Depends(get_db),
):
    """Unified Calibration Log feed. Chronological (newest first) across
    every capture table.

    Public callers see only promoted entries. Authed admins (rc_admin_session
    cookie) get the full audit trail including un-promoted rows — same
    endpoint, two views of the same data.

    Filtering: event_type narrows to one capture type; song_source + song_id
    narrow to one song's events. Both filters compose.
    """
    include_unpromoted = admin is not None
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
    db: Session = Depends(get_db),
):
    """Single feed entry. Matches the shape returned by the list endpoint.

    All events are auto-promoted (2026-04-23) so no auth gate is needed.
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

    # All tenet/algo events are auto-promoted (2026-04-23).
    slug_cache: dict = {}
    if source_table == "pre_publish_corrections":
        data = _correction_to_entry(row, db, slug_cache)
    elif source_table == "rubric_changes":
        linked = linked_songs_by_slug(db, [row.change_slug], slug_cache).get(row.change_slug)
        data = _rubric_change_to_entry(row, db, slug_cache, linked_songs=linked)
    else:
        data = _recalibration_to_entry(row, db, slug_cache)
    return FeedEntry.model_validate(data)
