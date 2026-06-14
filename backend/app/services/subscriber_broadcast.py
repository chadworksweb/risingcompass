"""One-off category broadcast (Hockey Stick Build 2b, preferences pass).

The admin-composed counterpart to the automated reading digest: a plain-text
note sent to confirmed subscribers who keep a given notification category on
(moments of notice / updates and releases). Same Resend path + fail-soft
per-recipient loop as the digest; no dedup key (these are hand-sent one-offs the
admin controls), so a re-send goes to everyone eligible again -- preview first.

Body is authored as plain text in the admin composer and rendered into the
branded email shell here (the operator never writes HTML); the footer always
carries the tokenized manage-preferences + unsubscribe links.
"""
import logging
from html import escape

from sqlalchemy.orm import Session

from app.config import settings
from app.models import RcSubscriber
from app.services.subscribers import NOTIFY_BY_KEY, BROADCAST_KEYS

logger = logging.getLogger(__name__)

_MAX_PER_RUN = 5000  # backstop; the list is small pre-launch


def _site() -> str:
    return (settings.site_url or "https://risingcompass.net").rstrip("/")


def _body_html(body: str) -> str:
    """Plain text -> safe HTML: blank-line-separated paragraphs, single newlines
    become <br>. Everything is escaped first (the operator writes text, not HTML)."""
    blocks = [b.strip() for b in (body or "").replace("\r\n", "\n").split("\n\n")]
    paras = []
    for b in blocks:
        if not b:
            continue
        paras.append(
            '<p style="font-size:15px;line-height:1.6;color:#1a1a2e;margin:0 0 16px;">'
            + escape(b).replace("\n", "<br>")
            + "</p>"
        )
    return "".join(paras)


def _render_html(category: dict, subject: str, body: str, sub: RcSubscriber) -> str:
    site = _site()
    unsub = f"{site}/api/unsubscribe?token={sub.unsubscribe_token}"
    manage = f"{site}/subscribe/preferences/?token={sub.unsubscribe_token}"
    eyebrow = escape(category["label"])
    return f"""
    <div style="background:#ffffff;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;color:#1a1a2e;">
      <div style="background:#0a0a14;padding:22px 26px;border-radius:8px 8px 0 0;">
        <div style="font-size:11px;letter-spacing:.3em;text-transform:uppercase;color:#00d4aa;margin-bottom:6px;">The Rising Compass</div>
        <div style="font-size:21px;font-weight:300;color:#eeeef4;">{eyebrow}</div>
      </div>
      <div style="padding:24px 26px;">
        <h1 style="font-size:20px;font-weight:700;color:#1a1a2e;margin:0 0 18px;line-height:1.3;">{escape(subject)}</h1>
        {_body_html(body)}
      </div>
      <div style="padding:18px 26px;border-top:1px solid #eee;text-align:center;">
        <a href="{site}/" style="display:inline-block;background:#0a0a14;color:#00d4aa;padding:12px 28px;text-decoration:none;font-weight:600;font-size:14px;border-radius:6px;">Go to The Rising Compass</a>
      </div>
      <div style="padding:14px 26px;border-top:1px solid #eee;text-align:center;">
        <span style="font-size:11px;color:#999;">You are getting this because you follow "{eyebrow}". <a href="{manage}" style="color:#999;">Manage preferences</a> &middot; <a href="{unsub}" style="color:#999;">Unsubscribe</a>.</span>
      </div>
    </div>
    """


def send_broadcast(
    db: Session, category_key: str, subject: str, body: str, dry_run: bool = False
) -> dict:
    """Send a one-off note to confirmed subscribers opted into `category_key`.

    Returns counts. dry_run reports who WOULD receive it without sending. Eligible
    = status 'confirmed' AND the category's toggle is true."""
    category = NOTIFY_BY_KEY.get(category_key)
    if category is None or category_key not in BROADCAST_KEYS:
        return {"status": "bad_category", "sent": 0, "failed": 0, "eligible": 0}
    subject = (subject or "").strip()
    if not subject or not (body or "").strip():
        return {"status": "empty", "sent": 0, "failed": 0, "eligible": 0}

    col = getattr(RcSubscriber, category["col"])
    eligible = (
        db.query(RcSubscriber)
        .filter(RcSubscriber.status == "confirmed", col.is_(True))
        .order_by(RcSubscriber.id.asc())
        .limit(_MAX_PER_RUN)
        .all()
    )

    if dry_run:
        return {"status": "dry_run", "category": category_key,
                "eligible": len(eligible), "sent": 0, "failed": 0}

    from app.services.alerts import _send_resend  # local import avoids a cycle
    sent = failed = 0
    for sub in eligible:
        try:
            ok = _send_resend(sub.email, subject, _render_html(category, subject, body, sub))
        except Exception:
            logger.exception("broadcast send raised for subscriber id=%s", sub.id)
            ok = False
        if ok:
            sent += 1
        else:
            failed += 1

    logger.info("subscriber broadcast %s: sent=%d failed=%d eligible=%d",
                category_key, sent, failed, len(eligible))
    return {"status": "ok", "category": category_key,
            "eligible": len(eligible), "sent": sent, "failed": failed}
