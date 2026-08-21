"""Admin Alerts preference API.

  GET  /api/admin/alerts          list all preference rows (DB-backed)
  PUT  /api/admin/alerts/{key}    upsert one (enabled true/false)

The page UI also surfaces 'coming soon' rows that don't exist in the DB
yet -- those are static placeholders on the frontend. Only wired alerts
should be writable here so flipping a placeholder can't lie about effect.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_admin_session
from app.config import settings
from app.models import User
from app.services import alerts as alerts_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/alerts", tags=["alerts-admin"])


# Only alert keys with backend hooks are writable. Anything else returns 422
# so the UI's coming-soon placeholders can't accidentally persist a fake
# preference.
WIRED_ALERT_KEYS = {"comment_created", "prompt_cache_warranted",
                    "general_inquiry", "provenance_health", "provenance_integrity",
                    "faultline_new_critical", "faultline_regression",
                    "faultline_new_signature", "alltime_streams_awaiting",
                    "lec_rubric_drift"}


class PrefOut(BaseModel):
    alert_key: str
    channel: str
    enabled: bool
    updated_at: Optional[str]


class PrefListOut(BaseModel):
    items: list[PrefOut]
    admin_alert_email: str  # echo back so UI can show where alerts are going
    resend_configured: bool  # warn the admin if alerts would silently no-op


class PrefIn(BaseModel):
    enabled: bool
    channel: str = "email"


@router.get("", response_model=PrefListOut)
def list_prefs(
    _admin: User = Depends(require_admin_session),
):
    rows = alerts_svc.list_prefs()
    return PrefListOut(
        items=[
            PrefOut(
                alert_key=r["alert_key"],
                channel=r["channel"],
                enabled=r["enabled"],
                updated_at=(str(r["updated_at"]) if r["updated_at"] else None),
            )
            for r in rows
        ],
        admin_alert_email=settings.admin_alert_email,
        resend_configured=bool(settings.resend_api_key),
    )


@router.put("/{alert_key}", response_model=PrefOut)
def set_pref(
    alert_key: str,
    payload: PrefIn,
    _admin: User = Depends(require_admin_session),
):
    if alert_key not in WIRED_ALERT_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Alert '{alert_key}' is not wired yet; toggle has no effect.",
        )
    alerts_svc.set_pref(alert_key=alert_key, enabled=payload.enabled, channel=payload.channel)
    # Read back so the UI gets canonical state.
    rows = [r for r in alerts_svc.list_prefs()
            if r["alert_key"] == alert_key and r["channel"] == payload.channel]
    if not rows:
        raise HTTPException(status_code=500, detail="Pref row vanished after upsert")
    r = rows[0]
    return PrefOut(
        alert_key=r["alert_key"],
        channel=r["channel"],
        enabled=r["enabled"],
        updated_at=(str(r["updated_at"]) if r["updated_at"] else None),
    )
