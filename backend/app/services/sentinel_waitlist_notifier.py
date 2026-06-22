"""Resend dispatcher: tell the Sentinel waitlist that applications have opened.

Triggered manually from the admin Sentinel section. Sends one email per
unnotified row, stamps `notified_at` on success. Failures stay unnotified so a
retry picks them up. Mirrors services/lc_subscriber_notifier.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SentinelWaitlist

logger = logging.getLogger(__name__)

_SUBJECT = "Sentinel Auditor applications are open"

_BODY_HTML = """\
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#ffffff; color:#222; padding:24px; line-height:1.55;">
  <div style="max-width:520px; margin:0 auto;">
    <h2 style="margin:0 0 16px; font-size:22px;">Applications are open.</h2>
    <p>You asked to hear when the Sentinel Auditor desk opened. It has. If you
       still want to help keep the readings honest, the intake form is ready.</p>
    <p style="margin:24px 0;">
      <a href="https://risingcompass.net/sentinel/"
         style="display:inline-block; padding:12px 20px; background:#00d4aa; color:#06121a; text-decoration:none; border-radius:6px; font-weight:600;">
        Open the intake
      </a>
    </p>
    <p style="font-size:13px; color:#666; margin-top:32px;">
      You're receiving this because you asked to be told when applications opened.
      Ignore this email and we won't write again.
    </p>
    <p style="font-size:13px; color:#666;">&mdash; The Rising Compass</p>
  </div>
</body>
</html>
"""

_BODY_TEXT = (
    "Applications are open.\n\n"
    "You asked to hear when the Sentinel Auditor desk opened. It has. If you "
    "still want to help keep the readings honest, the intake form is ready.\n\n"
    "Open the intake: https://risingcompass.net/sentinel/\n\n"
    "You're receiving this because you asked to be told when applications opened. "
    "Ignore this email and we won't write again.\n\n"
    "-- The Rising Compass\n"
)


def _send_one(to_email: str, config: Settings) -> bool:
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": config.email_from,
                "to": [to_email],
                "subject": _SUBJECT,
                "html": _BODY_HTML,
                "text": _BODY_TEXT,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception("sentinel waitlist email failed for %s", to_email)
        return False


def notify_waitlist(db: Session, config: Settings) -> dict:
    """Email every waitlist row without notified_at. Returns counts."""
    if not config.resend_api_key or not config.email_from:
        logger.warning("Resend not configured -- cannot notify the sentinel waitlist")
        return {"sent": 0, "failed": 0, "configured": False}

    pending: Iterable[SentinelWaitlist] = (
        db.query(SentinelWaitlist)
        .filter(SentinelWaitlist.notified_at.is_(None))
        .order_by(SentinelWaitlist.id.asc())
        .all()
    )
    sent = failed = 0
    now = datetime.utcnow()
    for sub in pending:
        if _send_one(sub.email, config):
            sub.notified_at = now
            db.commit()
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "configured": True}
