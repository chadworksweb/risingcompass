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
import logging
import time
from typing import Optional

import jwt as _jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import User
from app.services import admin_auth as admin_auth_svc
from app.services import clerk as clerk_svc
from app.services.api_clients import resolve_key

logger = logging.getLogger(__name__)


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


def verify_reading_cron_key(x_reading_cron_key: Optional[str] = Header(default=None)):
    """Service auth for the daily reading cron. Mirrors verify_backup_key:
    a separate header secret so leaks across cron lanes don't compose."""
    expected = settings.rc_reading_cron_key
    if not expected:
        raise HTTPException(status_code=503, detail="Reading cron key not configured")
    if not x_reading_cron_key:
        raise HTTPException(status_code=403, detail="Missing reading cron key")
    if not hmac.compare_digest(x_reading_cron_key, expected):
        raise HTTPException(status_code=403, detail="Invalid reading cron key")


def _extract_clerk_token(
    authorization: Optional[str], session_cookie: Optional[str]
) -> Optional[str]:
    """Pick the Clerk session JWT off the request. Authorization: Bearer wins
    over the __session cookie so an explicit header beats a stale cookie."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
        if token:
            return token
    if session_cookie:
        return session_cookie
    return None


def require_clerk_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    __session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Public Participation Tier 1 auth dependency. Verifies the Clerk
    session JWT, lazy-provisions a local users row on first sight, and
    attaches user metadata to request.state for downstream handlers.

    Returns the local User row (handle may be NULL pre-onboarding).
    Endpoints that require posting privileges should additionally check
    user.handle is not None and user.status == 'active'.

    401 on missing / invalid token. 403 on banned account.
    """
    token = _extract_clerk_token(authorization, __session)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        claims = clerk_svc.verify_clerk_jwt(token)
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except _jwt.PyJWTError as exc:
        logger.warning("Clerk JWT verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid session")
    except RuntimeError as exc:
        # Misconfiguration (e.g. CLERK_JWKS_URL not set). 503 so the client
        # knows this is a server-side issue, not their token.
        raise HTTPException(status_code=503, detail=str(exc))

    clerk_user_id = claims.get("sub")
    if not clerk_user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    user = clerk_svc.ensure_user_for_clerk_id(db, clerk_user_id)

    if user.status == "banned":
        raise HTTPException(status_code=403, detail="Account banned")

    request.state.user_id = user.id
    request.state.clerk_user_id = clerk_user_id
    request.state.user_tier = user.tier
    return user


def optional_clerk_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    __session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Same as require_clerk_user but returns None instead of raising. Used by
    public endpoints that show extra state to authed users (e.g. comment
    threads showing "you reported this" hints)."""
    token = _extract_clerk_token(authorization, __session)
    if not token:
        return None
    try:
        claims = clerk_svc.verify_clerk_jwt(token)
    except (_jwt.PyJWTError, RuntimeError):
        return None
    clerk_user_id = claims.get("sub")
    if not clerk_user_id:
        return None
    user = clerk_svc.ensure_user_for_clerk_id(db, clerk_user_id)
    if user.status == "banned":
        return None
    request.state.user_id = user.id
    request.state.clerk_user_id = clerk_user_id
    request.state.user_tier = user.tier
    return user


def verify_admin_or_lyrics_key(
    request: Request,
    rc_admin_session: Optional[str] = Cookie(default=None),
    x_lyrics_supply_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Dual auth for the supply-lyrics endpoint: browser session cookie OR
    RC_LYRICS_SUPPLY_KEY header. Browser admins keep the same flow; terminal
    scripts can curl with just the header. Header takes precedence when both
    are present so a stale cookie doesn't shadow an explicit service call."""
    if x_lyrics_supply_key is not None:
        expected = settings.rc_lyrics_supply_key
        if not expected:
            raise HTTPException(status_code=503, detail="Lyrics supply key not configured")
        if not hmac.compare_digest(x_lyrics_supply_key, expected):
            raise HTTPException(status_code=403, detail="Invalid lyrics supply key")
        return None
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
