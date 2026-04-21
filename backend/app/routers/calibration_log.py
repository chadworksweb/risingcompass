"""Calibration Log — unified promote endpoint across all capture tables.

The Calibration Log is the public record of how the compass is being refined.
Each capture table (pre_publish_corrections today, song_recalibrations in
Phase 2) lands new rows with promoted_to_feed=false by default. This router
holds the one endpoint that flips that flag — the deliberate editorial act
that puts an event into the public feed.

The source_table path param dispatches to the right table. Adding future
capture tables (non-song rubric updates, vibe resolutions) is a matter of
extending _TABLE_REGISTRY; the endpoint shape stays stable.

See RISING-COMPASS-CALIBRATION-LOG.md.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PrePublishCorrection
from app.schemas import CalibrationLogPromoteIn
from app.routers.admin import verify_admin_key

router = APIRouter(prefix="/api/admin/calibration-log", tags=["calibration-log"])


# Maps source_table path param → (model class, human-readable label for errors).
# Phase 2 will add "song_recalibrations": (SongRecalibration, "recalibration").
_TABLE_REGISTRY = {
    "pre_publish_corrections": (PrePublishCorrection, "pre-publish correction"),
}


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
