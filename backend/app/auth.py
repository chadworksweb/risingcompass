"""Auth utilities: API key verification, client resolution, HMAC tokens,
admin session dependency.

Every X-Api-Key is resolved via services.api_clients.resolve_key against the
api_client_keys table. The legacy RC_API_KEY / RC_SERVICE_KEY env values are
seeded into the table at startup so they keep working during rollout.

Both verify dependencies attach request.state.client_id for the API-call
logging middleware. verify_api_or_service_key also returns the behavior tier
("public" | "service") so calibrate endpoints can branch.

Admin auth uses session cookies (require_admin_session). The cron-driven
backup endpoint stays on a header secret (verify_backup_key), separated
from the human admin path so a leaked admin password doesn't grant
service-tier access and vice versa.
"""

import hashlib
import hmac
import time
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.services import admin_auth as admin_auth_svc
from app.services.api_clients import resolve_key


def _resolve(x_api_key: str):
    """Return the ApiClient that owns this key, or None."""
    db = SessionLocal()
    try:
        return resolve_key(db, x_api_key)
    finally:
        db.close()


def verify_api_key(request: Request, x_api_key: str = Header(...)):
    """Require a valid API key. Accepts any active client.

    Previously only accepted RC_API_KEY (public). With clients in a table, any
    active client key is acceptable — endpoint-level logic decides behavior.
    Public-only endpoints that need to reject service callers should depend on
    verify_api_or_service_key and check the tier.
    """
    client = _resolve(x_api_key)
    if not client:
        raise HTTPException(status_code=403, detail="Invalid API key")
    request.state.client_id = client.id
    request.state.client_slug = client.slug
    request.state.client_behavior = client.behavior
    request.state.plan_tier = client.plan_tier


def verify_api_or_service_key(request: Request, x_api_key: str = Header(...)) -> str:
    """Same auth as verify_api_key but returns the client's behavior tier.

    Tier values: "public" (bot protection + lc_events on calibrate) or
    "service" (skipped). Endpoints branch on the return value.
    """
    client = _resolve(x_api_key)
    if not client:
        raise HTTPException(status_code=403, detail="Invalid API key")
    request.state.client_id = client.id
    request.state.client_slug = client.slug
    request.state.client_behavior = client.behavior
    request.state.plan_tier = client.plan_tier
    return client.behavior


def create_approval_token(draft_ref: str, ttl: int = 86400) -> str:
    """Create an HMAC token valid for `ttl` seconds (default 24h).

    Format: {draft_ref}:{expires_timestamp}:{signature}
    """
    expires = int(time.time()) + ttl
    payload = f"{draft_ref}:{expires}"
    sig = hmac.new(
        settings.rc_admin_key.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_approval_token(draft_ref: str, token: str) -> bool:
    """Verify HMAC token. Returns False if expired, tampered, or wrong draft."""
    parts = token.split(":")
    if len(parts) != 3:
        return False
    ref, expires_str, sig = parts
    if ref != draft_ref:
        return False
    try:
        if int(expires_str) < time.time():
            return False
    except ValueError:
        return False
    expected_payload = f"{ref}:{expires_str}"
    expected_sig = hmac.new(
        settings.rc_admin_key.encode(),
        expected_payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return hmac.compare_digest(sig, expected_sig)


def require_admin_session(
    request: Request,
    rc_admin_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    """Cookie-based session auth for every admin API endpoint.

    Reads the rc_admin_session cookie, resolves it against admin_sessions
    (by SHA-256 hash), checks idle + absolute expiry, slides the idle
    window forward, and attaches admin user info to request.state. Raises
    401 on any failure — the frontend redirects to the obscured login URL.
    """
    if not rc_admin_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = admin_auth_svc.lookup_session(db, rc_admin_session)
    if not result:
        raise HTTPException(status_code=401, detail="Session invalid or expired")
    sess, user = result
    admin_auth_svc.touch_session(db, sess)
    request.state.admin_user_id = user.id
    request.state.admin_username = user.username
    request.state.admin_role = user.role
    return user


def optional_admin_session(
    request: Request,
    rc_admin_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    """Same as require_admin_session but returns None instead of 401.

    Used by public endpoints that show extra detail to authed admins
    (calibration log unpromoted entries, song history audit rows).
    """
    if not rc_admin_session:
        return None
    result = admin_auth_svc.lookup_session(db, rc_admin_session)
    if not result:
        return None
    sess, user = result
    admin_auth_svc.touch_session(db, sess)
    request.state.admin_user_id = user.id
    request.state.admin_username = user.username
    request.state.admin_role = user.role
    return user


def verify_backup_key(x_backup_key: Optional[str] = Header(default=None)):
    """Service auth for the daily backup cron. Distinct from human admin
    auth so a leaked admin password doesn't grant backup access (and a
    leaked backup key doesn't grant arbitrary admin access).

    Strictly RC_BACKUP_KEY now — the legacy fallback to RC_ADMIN_KEY was
    removed once the cron at le-projects-01 was migrated (2026-04-25).
    """
    expected = settings.rc_backup_key
    if not expected:
        raise HTTPException(status_code=503, detail="Backup key not configured")
    if not x_backup_key:
        raise HTTPException(status_code=403, detail="Missing backup key")
    if not hmac.compare_digest(x_backup_key, expected):
        raise HTTPException(status_code=403, detail="Invalid backup key")
