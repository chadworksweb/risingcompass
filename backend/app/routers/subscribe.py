"""On-site subscriber capture (Hockey Stick Build 2b) -- RC's OWN list.

POST /api/subscribe          public, bot-protected, rate-limited: double-opt-in
GET  /api/subscribe/confirm  email-clicked link -> confirm + a promote-to-account nudge
GET  /api/unsubscribe        email-clicked one-click opt-out

The router mounts UNAUTHED (like geo.router): the POST is guarded by honeypot +
Turnstile + rate limit, and the GET links are clicked straight from an inbox, so
neither can carry an X-Api-Key. All emailing is scheduled off the request.
"""
import logging
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.analyzer import _check_bot_protection, limiter
from app.schemas import SubscribeIn, SubscribeOut, SubscriberPrefsIn
from app.services import subscribers as subs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["subscribe"])


def _site() -> str:
    return (settings.site_url or "https://risingcompass.net").rstrip("/")


def _page(title: str, body_html: str) -> str:
    """Minimal branded standalone page for the email-clicked GET links."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(title)} - The Rising Compass</title>
<style>
  body{{margin:0;background:#0e0f13;color:#e8e8ea;font-family:Georgia,serif;
       display:flex;min-height:100vh;align-items:center;justify-content:center}}
  .card{{max-width:480px;padding:40px;text-align:center;line-height:1.6}}
  h1{{font-weight:600;font-size:1.6rem;margin:0 0 12px}}
  p{{color:#b6b7bd}}
  a.btn{{display:inline-block;margin-top:22px;background:#3388ff;color:#fff;
        text-decoration:none;padding:12px 24px;border-radius:6px;
        font-family:Arial,sans-serif}}
  a.muted{{color:#7d7e85}}
</style></head>
<body><div class="card">{body_html}</div></body></html>"""


@router.post("/api/subscribe", response_model=SubscribeOut)
@limiter.limit("10/hour")
async def subscribe(
    data: SubscribeIn,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Capture an email into RC's subscriber list (double opt-in)."""
    await _check_bot_protection(data.hp_website, data.turnstile_token, request)

    email = subs.normalize_email(data.email)
    if "@" not in email or "." not in email.split("@")[-1]:
        return SubscribeOut(status="invalid", message="That email does not look right.")

    status, row = subs.subscribe(db, email, data.source, data.source_detail)

    if status == "already_subscribed":
        return SubscribeOut(
            status=status,
            message="You are already on the list. Watch your inbox for the readings.",
        )

    # pending_confirm | resent_confirm -> send the confirm email off-request
    if row is not None:
        background_tasks.add_task(subs.send_confirm_email, row)
    return SubscribeOut(
        status=status,
        message="Almost there. Check your email for a one-click confirmation.",
    )


@router.get("/api/subscribe/confirm", response_class=HTMLResponse)
def confirm(token: str = "", db: Session = Depends(get_db)):
    row = subs.confirm(db, token)
    site = _site()
    if row is None:
        body = (
            "<h1>Link expired</h1>"
            "<p>This confirmation link was already used or is no longer valid.</p>"
            f'<a class="btn" href="{site}/">Go to The Rising Compass</a>'
        )
        return HTMLResponse(_page("Link expired", body), status_code=200)

    # Confirmed -> nudge the promote-to-account step with the email prefilled.
    prefill = f"{site}/account/?mode=signup&prefill_email={quote(row.email)}"
    body = (
        "<h1>You are subscribed</h1>"
        "<p>The readings are on their way. When you are ready, turn this into a "
        "free account to save songs, comment, and unlock the full Library.</p>"
        f'<a class="btn" href="{escape(prefill)}">Create your free account</a>'
        f'<p style="margin-top:18px"><a class="muted" href="{site}/">No thanks, just the readings</a></p>'
    )
    return HTMLResponse(_page("Subscribed", body))


@router.get("/api/subscribe/preferences")
def get_preferences(token: str = "", db: Session = Depends(get_db)):
    """Read the category toggles + master state behind a manage token. Returns
    `found: false` (200) for an unknown token so the page can show a generic
    'link invalid' state without confirming whether an address is on the list."""
    public_categories = [
        {"key": c["key"], "label": c["label"], "desc": c["desc"]}
        for c in subs.NOTIFY_CATEGORIES
    ]
    row = subs.get_by_unsub_token(db, token)
    if row is None:
        return {"found": False, "categories": public_categories}
    return {
        "found": True,
        "email": row.email,
        "subscribed": row.status != "unsubscribed",
        "prefs": subs.prefs_dict(row),
        "categories": public_categories,
    }


@router.post("/api/subscribe/preferences")
def update_preferences(data: SubscriberPrefsIn, db: Session = Depends(get_db)):
    """Update toggles / master state for the row behind the manage token. Always
    returns `ok: true` (idempotent, no token-existence leak); when the token
    resolves, the new state rides back so the page can re-render."""
    row = subs.set_prefs_by_token(db, data.token, data.prefs or {}, data.subscribed)
    if row is None:
        return {"ok": True}
    return {
        "ok": True,
        "subscribed": row.status != "unsubscribed",
        "prefs": subs.prefs_dict(row),
    }


@router.get("/api/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str = "", db: Session = Depends(get_db)):
    row = subs.unsubscribe(db, token)
    site = _site()
    if row is None:
        body = (
            "<h1>Already off the list</h1>"
            "<p>We could not find an active subscription for that link.</p>"
            f'<a class="btn" href="{site}/">Go to The Rising Compass</a>'
        )
        return HTMLResponse(_page("Unsubscribed", body), status_code=200)
    body = (
        "<h1>You are unsubscribed</h1>"
        "<p>You will not receive the readings anymore. You can resubscribe any "
        "time from the site.</p>"
        f'<a class="btn" href="{site}/">Go to The Rising Compass</a>'
    )
    return HTMLResponse(_page("Unsubscribed", body))
