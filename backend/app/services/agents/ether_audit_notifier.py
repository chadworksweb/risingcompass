"""Ether Art Chart audit-flag notifier.

Fires a single email to the admin inbox when the ether tagger emits a
`topic_audit` payload — the agent's signal that none of the 24 closed-
taxonomy slugs honestly fit the song. Mirrors the misread/satirical
notification pattern in `routers/misread.py`; sends through Resend.

Env wiring:
  ETHER_AUDIT_NOTIFY_EMAIL — primary recipient. Falls back to
  MISREAD_NOTIFY_EMAIL when unset so a single admin inbox can serve
  both flag types without duplicating config.

Fails soft — on any error the call returns False and the caller logs
without retrying. Skipping a notification never blocks calibration.
"""

from __future__ import annotations

import logging
from html import escape

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def _resolve_recipient(settings: Settings) -> str:
    return settings.ether_audit_notify_email or settings.misread_notify_email or ""


def send_ether_audit_email(
    *,
    song_id: int,
    title: str,
    artist: str,
    audit: dict,
    settings: Settings,
) -> bool:
    """Notify the admin inbox that a song needs ether-tag triage.

    `audit` is the dict the tagger writes to `compass_songs.topic_audit`:
        {"reason": str, "proposed_tag": str, "rationale": str}
    Any of those fields may be empty strings; the email renders what's there.
    """
    recipient = _resolve_recipient(settings)
    if not settings.resend_api_key or not recipient:
        logger.info(
            "Ether audit notify skipped for song %s (resend_api_key or recipient unset)",
            song_id,
        )
        return False

    reason = (audit.get("reason") or "").strip()
    proposed_tag = (audit.get("proposed_tag") or "").strip()
    rationale = (audit.get("rationale") or "").strip()

    admin_url = f"{settings.site_url.rstrip('/')}/api/admin/ether-audits"

    rows = []
    if reason:
        rows.append(f"<p><strong>Reason:</strong> {escape(reason)}</p>")
    if proposed_tag:
        rows.append(
            f"<p><strong>Proposed tag:</strong> "
            f"<code style=\"background:#f4f1e8;padding:2px 6px;border-radius:3px;\">{escape(proposed_tag)}</code></p>"
        )
    if rationale:
        rows.append(f"<p><strong>Rationale:</strong><br>{escape(rationale)}</p>")

    body_html = "\n      ".join(rows) if rows else "<p><em>No payload fields populated.</em></p>"

    html = f"""
    <div style="font-family:'Inter',-apple-system,sans-serif;max-width:600px;color:#1a1a2e;">
      <h2 style="margin:0 0 12px;">Ether Audit Needed</h2>
      <p><strong>Song:</strong> {escape(title)} &mdash; {escape(artist)}</p>
      <p><strong>compass_songs.id:</strong> {song_id}</p>
      {body_html}
      <p style="margin-top:18px;">
        <a href="{escape(admin_url)}" style="color:#c9a960;">Triage in admin &rarr; Ether Audits</a>
      </p>
      <p style="color:#666;font-size:13px;">
        The agent flagged this song because no slug in the 25-topic taxonomy
        honestly fits. Either accept the proposed tag (code change to
        <code>services/ether_taxonomy.py</code>) or remap to an existing slug.
      </p>
    </div>
    """

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [recipient],
                "subject": f"[RC] Ether Audit Needed: {title} — {artist}",
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Ether audit notify sent to %s for song %s", recipient, song_id)
        return True
    except Exception:
        logger.exception("Failed to send ether audit notify for song %s", song_id)
        return False
