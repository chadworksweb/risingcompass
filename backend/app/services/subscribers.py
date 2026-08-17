"""On-site email-subscriber layer (Hockey Stick Build 2b).

The top of RC's OWN subscriber funnel: a lightweight, double-opt-in email
capture that later promotes cleanly into a full Clerk account with no duplicate
identity. See migration 099 + models.RcSubscriber for the data model.

Linking (no-duplicate): a subscriber is keyed by email; a Clerk account is keyed
by clerk_user_id and stores NO plaintext email -- only `users.email_hash`
(sha256 of the normalized email, set fail-soft at provision from the Clerk API).
We match the two by that hash, in both directions:
  - account created after subscribing  -> link_subscribers_for_user (called at
    provision in services/clerk.py)
  - subscribe after the account exists  -> link_user_for_subscriber (called when
    a new subscriber row is created)

All sends go through Resend (services/alerts._send_resend), scheduled off the
request so the POST stays fast.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models import RcSubscriber, User

logger = logging.getLogger(__name__)

VALID_SOURCES = {"song_page", "homepage", "activity", "charger", "footer", "other"}

# Notification categories (opt-out model: every column defaults true). `key` is
# the public/admin token, `col` the RcSubscriber attribute. daily_reading is the
# existing reading digest; the other two are admin-composed broadcasts. Add a
# category here + a migration column and every surface (preference center, admin
# display, broadcast picker) picks it up.
NOTIFY_CATEGORIES = [
    {
        "key": "daily_reading",
        "col": "pref_daily_reading",
        "label": "Daily readings",
        "desc": "The daily compass reading: the day's charge and the songs behind it.",
        "broadcast": False,
    },
    {
        "key": "moments_of_notice",
        "col": "pref_moments_of_notice",
        "label": "Moments of notice",
        "desc": "Occasional notes when something moves: a sharp spike, an anomaly, a notable shift.",
        "broadcast": True,
    },
    {
        "key": "config_updates",
        "col": "pref_config_updates",
        "label": "Updates and releases",
        "desc": "Now and then: new features, framework changes, and version launches.",
        "broadcast": True,
    },
]
NOTIFY_BY_KEY = {c["key"]: c for c in NOTIFY_CATEGORIES}
# Categories an admin can send a one-off broadcast to (daily_reading has its own
# digest sender, so it is not a broadcast target).
BROADCAST_KEYS = {c["key"] for c in NOTIFY_CATEGORIES if c["broadcast"]}


def prefs_dict(sub: RcSubscriber) -> dict:
    """Serialize a row's category toggles as {key: bool} for the API/admin."""
    return {c["key"]: bool(getattr(sub, c["col"], True)) for c in NOTIFY_CATEGORIES}


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_email(email: str) -> str:
    """sha256 of the normalized email -- the cross-table link key. Literal
    normalization only (no plus/dot folding) so the match is predictable."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)[:64]


# --- lifecycle --------------------------------------------------------------

def clean_name(value: str) -> Optional[str]:
    """A supplied name, or None. Blank and whitespace-only both mean not given,
    which is the same thing as never having been asked."""
    name = (value or "").strip()[:80]
    return name or None


def subscribe(
    db: Session,
    email: str,
    source: str = "",
    source_detail: str = "",
    first_name: str = "",
    last_name: str = "",
) -> Tuple[str, Optional[RcSubscriber]]:
    """Upsert a subscriber and return (status, row). Statuses:
      pending_confirm    -- new row (or re-opened), confirm email should be sent
      resent_confirm     -- already pending, re-issue the confirm email
      already_subscribed -- already confirmed, no email needed
    The caller schedules the confirm send for the first two.
    """
    norm = normalize_email(email)
    src = source if source in VALID_SOURCES else "other"
    first = clean_name(first_name)
    last = clean_name(last_name)
    row = db.query(RcSubscriber).filter(RcSubscriber.email == norm).first()

    if row is None:
        row = RcSubscriber(
            email=norm,
            email_hash=hash_email(norm),
            first_name=first,
            last_name=last,
            status="pending",
            source=src,
            source_detail=(source_detail or "").strip()[:500] or None,
            confirm_token=_token(),
            unsubscribe_token=_token(),
        )
        db.add(row)
        db.flush()  # assign id before linking
        link_user_for_subscriber(db, row)
        db.commit()
        db.refresh(row)
        return "pending_confirm", row

    # A name given on a later attempt fills a blank, but a blank never erases a
    # name already on the row -- re-subscribing from a form left empty is not a
    # request to be forgotten by name.
    if first and not row.first_name:
        row.first_name = first
    if last and not row.last_name:
        row.last_name = last

    if row.status == "confirmed":
        if db.is_modified(row):
            db.commit()
        return "already_subscribed", row

    # pending or previously unsubscribed -> (re)open and re-issue a confirm
    row.status = "pending"
    row.unsubscribed_at = None
    if not row.confirm_token:
        row.confirm_token = _token()
    if not row.unsubscribe_token:
        row.unsubscribe_token = _token()
    if row.source is None:
        row.source = src
    db.commit()
    db.refresh(row)
    return "resent_confirm", row


def confirm(db: Session, token: str) -> Optional[RcSubscriber]:
    """Consume a confirm token -> mark confirmed. Idempotent-ish: an already
    confirmed row whose token was cleared just returns None (the link is spent),
    so the route shows a generic confirmed page either way."""
    if not token:
        return None
    row = db.query(RcSubscriber).filter(RcSubscriber.confirm_token == token).first()
    if row is None:
        return None
    row.status = "confirmed"
    row.confirmed_at = datetime.now(timezone.utc)
    row.confirm_token = None  # single-use
    db.commit()
    db.refresh(row)
    return row


def unsubscribe(db: Session, token: str) -> Optional[RcSubscriber]:
    if not token:
        return None
    row = db.query(RcSubscriber).filter(RcSubscriber.unsubscribe_token == token).first()
    if row is None:
        return None
    row.status = "unsubscribed"
    row.unsubscribed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


# --- preference center (tokenized, no login) --------------------------------

def get_by_unsub_token(db: Session, token: str) -> Optional[RcSubscriber]:
    """Resolve the stable manage/unsubscribe token to its row. The token comes
    from the subscriber's own inbox, so it doubles as the preference-center key
    (the same pattern chadlewine uses)."""
    if not token:
        return None
    return db.query(RcSubscriber).filter(RcSubscriber.unsubscribe_token == token).first()


def set_prefs_by_token(
    db: Session, token: str, prefs: dict, subscribed: Optional[bool] = None
) -> Optional[RcSubscriber]:
    """Update category toggles (and optionally the master subscribe state) for the
    row behind `token`. `prefs` is {category_key: bool}; unknown keys are ignored.
    `subscribed` True/False flips the master status (resubscribe / unsubscribe
    from everything). Returns the row, or None if the token does not resolve."""
    row = get_by_unsub_token(db, token)
    if row is None:
        return None
    for c in NOTIFY_CATEGORIES:
        if isinstance(prefs, dict) and c["key"] in prefs:
            setattr(row, c["col"], bool(prefs[c["key"]]))
    if subscribed is True:
        # Re-opt-in. The click came from their own email, so treat the address as
        # confirmed rather than bouncing them back through double opt-in.
        row.status = "confirmed"
        row.unsubscribed_at = None
        if row.confirmed_at is None:
            row.confirmed_at = datetime.now(timezone.utc)
    elif subscribed is False:
        row.status = "unsubscribed"
        row.unsubscribed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


# --- linking (no-duplicate / promote-to-Clerk) -----------------------------

def link_user_for_subscriber(db: Session, sub: RcSubscriber) -> bool:
    """A new subscriber row: if an account with the same email_hash already
    exists, link them. Returns True if linked. No commit (caller owns the txn)."""
    if sub.user_id is not None or not sub.email_hash:
        return False
    user = db.query(User).filter(User.email_hash == sub.email_hash).first()
    if user is None:
        return False
    sub.user_id = user.id
    sub.promoted_at = datetime.now(timezone.utc)
    logger.info("Linked existing user id=%s to new subscriber id=%s by email hash", user.id, sub.id)
    return True


def link_subscribers_for_user(db: Session, user: User, email: Optional[str]) -> int:
    """Called at Clerk provision. Stamps user.email_hash (the only email-derived
    value on the row) and links any matching subscriber(s). Returns the count
    linked. No commit -- the provision path owns the txn. Fail-soft: a missing
    email just means no hash, no link."""
    if not email:
        return 0
    h = hash_email(email)
    if user.email_hash != h:
        user.email_hash = h
    rows = (
        db.query(RcSubscriber)
        .filter(RcSubscriber.email_hash == h, RcSubscriber.user_id.is_(None))
        .all()
    )
    now = datetime.now(timezone.utc)
    for r in rows:
        r.user_id = user.id
        r.promoted_at = now
    if rows:
        logger.info("Linked %d subscriber(s) to user id=%s at provision", len(rows), user.id)
    return len(rows)


# --- email (Resend) ---------------------------------------------------------

def _confirm_url(sub: RcSubscriber) -> str:
    base = (settings.site_url or "https://risingcompass.net").rstrip("/")
    return f"{base}/api/subscribe/confirm?token={sub.confirm_token}"


def _unsub_url(sub: RcSubscriber) -> str:
    base = (settings.site_url or "https://risingcompass.net").rstrip("/")
    return f"{base}/api/unsubscribe?token={sub.unsubscribe_token}"


def manage_url(sub: RcSubscriber) -> str:
    """Tokenized preference-center link (choose which categories to receive)."""
    base = (settings.site_url or "https://risingcompass.net").rstrip("/")
    return f"{base}/subscribe/preferences/?token={sub.unsubscribe_token}"


def send_confirm_email(sub: RcSubscriber) -> bool:
    """Double-opt-in confirm email. Safe to call from a background task; returns
    False (logged) if Resend is unconfigured, never raises."""
    from app.services.alerts import _send_resend  # local import avoids a cycle

    confirm_link = _confirm_url(sub)
    unsub_link = _unsub_url(sub)
    manage_link = manage_url(sub)
    subject = "Confirm your Rising Compass subscription"
    html = f"""
      <div style="font-family:Georgia,serif;max-width:520px;margin:0 auto;color:#1a1a1a;line-height:1.55">
        <h2 style="font-weight:600">One click to confirm</h2>
        <p>You asked to follow The Rising Compass. Confirm the address and we will
        start sending you the readings.</p>
        <p style="margin:28px 0">
          <a href="{confirm_link}"
             style="background:#3388ff;color:#fff;text-decoration:none;padding:12px 22px;border-radius:6px;font-family:Arial,sans-serif">
            Confirm subscription
          </a>
        </p>
        <p style="font-size:13px;color:#666">If you did not request this, ignore
        this email and nothing happens. You can
        <a href="{manage_link}" style="color:#666">choose what you get</a> or
        <a href="{unsub_link}" style="color:#666">opt out here</a>.</p>
      </div>
    """
    try:
        return _send_resend(sub.email, subject, html)
    except Exception:  # never let a send fault escape a background task
        logger.exception("send_confirm_email failed for subscriber id=%s", sub.id)
        return False
