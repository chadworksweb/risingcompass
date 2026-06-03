"""Chart anomaly annotations -- public read + admin CRUD.

Admin-authored markers that explain spikes/dips on the daily compass chart
(the first and only type so far is a major album release). The public read
is namespaced under /api/compass (same auth + frontend client as the rest of
the chart data); the admin CRUD lives on its own cookie-authed router.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChartAnomaly
from app.schemas import (
    ANOMALY_TYPES, ChartAnomalyCreate, ChartAnomalyOut, ChartAnomalyUpdate,
)
from app.routers.admin import verify_admin_key

logger = logging.getLogger(__name__)

# Public read sits under the compass namespace, like /api/compass/daily-chart.
router = APIRouter(prefix="/api/compass", tags=["chart-anomalies"])
# Admin CRUD: cookie auth, no X-Api-Key.
admin_router = APIRouter(tags=["chart-anomalies-admin"])


def _clean(s):
    s = (s or "").strip()
    return s or None


# --- Public ---

@router.get("/anomalies", response_model=list[ChartAnomalyOut])
def list_anomalies(db: Session = Depends(get_db)):
    """Active chart anomalies, oldest first. Drives the daily-chart markers."""
    return (
        db.query(ChartAnomaly)
        .filter(ChartAnomaly.active.is_(True))
        .order_by(ChartAnomaly.date.asc())
        .all()
    )


# --- Admin ---

@admin_router.get("/api/admin/chart-anomalies", response_model=list[ChartAnomalyOut],
                  dependencies=[Depends(verify_admin_key)])
def admin_list_anomalies(db: Session = Depends(get_db)):
    """Every anomaly (active or not), newest date first."""
    return (
        db.query(ChartAnomaly)
        .order_by(ChartAnomaly.date.desc(), ChartAnomaly.id.desc())
        .all()
    )


@admin_router.post("/api/admin/chart-anomalies", response_model=ChartAnomalyOut,
                   status_code=201, dependencies=[Depends(verify_admin_key)])
def admin_create_anomaly(data: ChartAnomalyCreate, db: Session = Depends(get_db)):
    """Create a chart anomaly marker."""
    atype = (data.anomaly_type or "album_release").strip().lower()
    if atype not in ANOMALY_TYPES:
        raise HTTPException(400, f"Invalid anomaly type. One of: {', '.join(sorted(ANOMALY_TYPES))}")
    if atype == "album_release" and not _clean(data.artist) and not _clean(data.album):
        raise HTTPException(422, "An album release needs an artist or album name.")
    row = ChartAnomaly(
        date=data.date,
        anomaly_type=atype,
        artist=_clean(data.artist),
        album=_clean(data.album),
        note=_clean(data.note),
        active=data.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@admin_router.patch("/api/admin/chart-anomalies/{anomaly_id}", response_model=ChartAnomalyOut,
                    dependencies=[Depends(verify_admin_key)])
def admin_update_anomaly(anomaly_id: int, data: ChartAnomalyUpdate, db: Session = Depends(get_db)):
    """Edit any field of an anomaly. Only supplied fields change."""
    row = db.query(ChartAnomaly).filter(ChartAnomaly.id == anomaly_id).first()
    if not row:
        raise HTTPException(404, "Anomaly not found")
    fields = data.model_dump(exclude_unset=True)
    if "anomaly_type" in fields and fields["anomaly_type"] is not None:
        atype = fields["anomaly_type"].strip().lower()
        if atype not in ANOMALY_TYPES:
            raise HTTPException(400, f"Invalid anomaly type. One of: {', '.join(sorted(ANOMALY_TYPES))}")
        row.anomaly_type = atype
    if "date" in fields and fields["date"] is not None:
        row.date = fields["date"]
    if "artist" in fields:
        row.artist = _clean(fields["artist"])
    if "album" in fields:
        row.album = _clean(fields["album"])
    if "note" in fields:
        row.note = _clean(fields["note"])
    if "active" in fields and fields["active"] is not None:
        row.active = fields["active"]
    db.commit()
    db.refresh(row)
    return row


@admin_router.delete("/api/admin/chart-anomalies/{anomaly_id}", status_code=204,
                     dependencies=[Depends(verify_admin_key)])
def admin_delete_anomaly(anomaly_id: int, db: Session = Depends(get_db)):
    """Permanently delete an anomaly marker."""
    row = db.query(ChartAnomaly).filter(ChartAnomaly.id == anomaly_id).first()
    if not row:
        raise HTTPException(404, "Anomaly not found")
    db.delete(row)
    db.commit()
    return None
