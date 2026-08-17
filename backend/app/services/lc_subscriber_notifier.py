"""Resend dispatcher: notify Lyrical Charger subscribers when LC is back online.

Triggered manually from the admin dashboard. Sends one email per
unnotified subscriber, marks each row's `notified_at` on success.
Failures stay unnotified so a retry will pick them up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import LyricalChargerSubscriber

logger = logging.getLogger(__name__)


_SUBJECT = "Lyrical Charger is back online"

_BODY_HTML = """\
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#ffffff; color:#222; padding:24px; line-height:1.55;">
  <div style="max-width:520px; margin:0 auto;">
    <h2 style="margin:0 0 16px; font-size:22px;">Lyrical Charger is back online.</h2>
    <p>Thanks for waiting. The engine is restocked and ready to read.</p>
    <p style="margin:24px 0;">
      <a href="https://risingcompass.net/lyrical-charger/"
         style="display:inline-block; padding:12px 20px; background:#9933ff; color:#ffffff; text-decoration:none; border-radius:6px; font-weight:600;">
        Open Lyrical Charger
      </a>
    </p>
    <p style="font-size:13px; color:#666; margin-top:32px;">
      You're receiving this because you signed up to be notified when Lyrical Charger came back.
      If you'd rather not hear about it again, ignore this email — we don't add you to anything else.
    </p>
    <p style="font-size:13px; color:#666;">
      &mdash; The Rising Compass
    </p>
  </div>
</body>
</html>
"""


_BODY_TEXT = (
    "Lyrical Charger is back online.\n\n"
    "Thanks for waiting. The engine is restocked and ready to read.\n\n"
    "Open Lyrical Charger: https://risingcompass.net/lyrical-charger/\n\n"
    "You're receiving this because you signed up to be notified when Lyrical Charger came back. "
    "If you'd rather not hear about it again, ignore this email -- we don't add you to anything else.\n\n"
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
        logger.exception("LC return-online email failed for %s", to_email)
        return False


def notify_subscribers(db: Session, config: Settings) -> dict:
    """Send the return-online email to every subscriber without notified_at.

    Returns counts: sent, skipped (no resend config), failed.
    """
    if not config.resend_api_key or not config.email_from:
        logger.warning("Resend not configured — cannot notify LC subscribers")
        return {"sent": 0, "skipped": 0, "failed": 0, "configured": False}

    pending: Iterable[LyricalChargerSubscriber] = (
        db.query(LyricalChargerSubscriber)
        .filter(LyricalChargerSubscriber.notified_at.is_(None))
        .order_by(LyricalChargerSubscriber.id.asc())
        .all()
    )

    sent = 0
    failed = 0
    now = datetime.now(timezone.utc)
    for sub in pending:
        if _send_one(sub.email, config):
            sub.notified_at = now
            db.commit()
            sent += 1
        else:
            failed += 1

    return {"sent": sent, "skipped": 0, "failed": failed, "configured": True}
