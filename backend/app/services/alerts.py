"""Admin alert delivery + preference lookup.

Two-tier categorization mirrors the Alerts UI:

  category='activity'   -- general site heartbeat. Lower-priority but
                           higher-volume. Subject prefixed [RC-ACTIVITY].
  category='moderation' -- moderation events the admin needs to triage.
                           Subject prefixed [RC-MOD].

Both currently land in the same inbox (settings.admin_alert_email). The
category drives the subject prefix so Gmail filters can split them.

send_alert is fire-and-forget on a background thread -- comment writes
must not block on SMTP, and a Resend outage shouldn't break the feature
that triggered the alert.

Pref lookup is in-process cached for 60s so a comment-storm doesn't hammer
the DB for the same flag. The TTL is short enough that flipping a toggle
in the admin UI takes effect within a minute without a restart.
"""

import logging
import threading
import time
from html import escape
from typing import Optional

import httpx
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0  # seconds
_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_cache_lock = threading.Lock()


def get_pref(alert_key: str, channel: str = "email") -> bool:
    """Read enabled flag for (alert_key, channel) with a 60s cache. Returns
    False if the row doesn't exist -- alerts must be opted into, never
    accidentally on after a fresh deploy."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get((alert_key, channel))
        if hit and (now - hit[1]) < _CACHE_TTL:
            return hit[0]
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT enabled FROM admin_alert_prefs WHERE alert_key = :k AND channel = :c"
        ), {"k": alert_key, "c": channel}).fetchone()
        enabled = bool(row[0]) if row else False
    finally:
        db.close()
    with _cache_lock:
        _cache[(alert_key, channel)] = (enabled, now)
    return enabled


def set_pref(alert_key: str, enabled: bool, channel: str = "email") -> None:
    """Upsert the pref. Bypasses the cache by invalidating on write so the
    admin UI sees the new state immediately."""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO admin_alert_prefs (alert_key, channel, enabled, updated_at) "
            "VALUES (:k, :c, :e, now()) "
            "ON CONFLICT (alert_key, channel) DO UPDATE SET "
            "  enabled = excluded.enabled, updated_at = now()"
        ), {"k": alert_key, "c": channel, "e": enabled})
        db.commit()
    finally:
        db.close()
    with _cache_lock:
        _cache.pop((alert_key, channel), None)


def list_prefs() -> list[dict]:
    """All pref rows for the admin UI. Used by GET /api/admin/alerts."""
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT alert_key, channel, enabled, updated_at FROM admin_alert_prefs ORDER BY alert_key, channel"
        )).fetchall()
        return [
            {"alert_key": r[0], "channel": r[1], "enabled": bool(r[2]), "updated_at": r[3]}
            for r in rows
        ]
    finally:
        db.close()


def _category_subject_prefix(category: str) -> str:
    if category == "moderation":
        return "[RC-MOD]"
    return "[RC-ACTIVITY]"


def _send_resend(to_addr: str, subject: str, html: str) -> bool:
    if not settings.resend_api_key:
        logger.warning("Resend not configured; admin alert skipped (subject=%s)", subject)
        return False
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [to_addr],
                "subject": subject,
                "html": html,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("Resend POST failed: %s", exc)
        return False
    if 200 <= resp.status_code < 300:
        return True
    logger.error("Resend returned %s: %s", resp.status_code, resp.text[:300])
    return False


def send_alert(category: str, alert_key: str, subject: str, html_body: str) -> None:
    """Fire-and-forget alert delivery. Spawned on a daemon thread so the
    caller is never blocked by SMTP latency."""
    if not settings.admin_alert_email:
        return
    if not get_pref(alert_key):
        return
    full_subject = f"{_category_subject_prefix(category)} {subject}"
    target = settings.admin_alert_email

    def _run():
        try:
            _send_resend(target, full_subject, html_body)
        except Exception:
            logger.exception("Admin alert delivery crashed (key=%s)", alert_key)

    threading.Thread(target=_run, daemon=True, name=f"alert-{alert_key}").start()


# ---------- canned alert formatters ----------

def emit_comment_created(*, handle: str, target_type: str, target_source: Optional[str],
                         target_id: int, content: str, comment_id: int) -> None:
    """Activity heartbeat -- a single comment was just posted. Body keeps
    the content short and links back to the admin queue + the public
    target page."""
    target_label = (
        f"{target_type}/{target_source}#{target_id}" if target_source
        else f"{target_type}#{target_id}"
    )
    snippet = content if len(content) <= 400 else content[:400] + "..."
    site = settings.site_url.rstrip("/")
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#555;">
        New comment by <strong>@{escape(handle)}</strong> on <code>{escape(target_label)}</code>
      </p>
      <blockquote style="margin:0 0 16px;padding:10px 14px;background:#f7f7f9;border-left:3px solid #008f72;border-radius:0 4px 4px 0;font-size:14px;color:#333;white-space:pre-wrap;">{escape(snippet)}</blockquote>
      <p style="margin:0;font-size:13px;color:#555;">
        Comment #{comment_id} &middot;
        <a href="{site}/api/admin/dashboard/lobby-mod" style="color:#008f72;">Lobby Mod queue</a>
      </p>
    </div>
    """
    send_alert(
        category="activity",
        alert_key="comment_created",
        subject=f"New comment by @{handle}",
        html_body=html,
    )
