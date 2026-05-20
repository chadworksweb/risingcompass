"""Clerk JWT verification + user provisioning for Public Participation Tier 1.

Verifies session JWTs against Clerk's JWKS endpoint (RS256). Keys are
fetched once and cached for the process lifetime; an unknown kid triggers
a re-fetch in case Clerk has rotated.

ensure_user_for_clerk_id() lazily mints a row in the local `users` table
the first time a verified Clerk session hits the API. The row's handle is
NULL until the user calls POST /api/users/me/setup -- frontend treats that
as "onboarding incomplete" and forces the handle picker.

Verification semantics: the JWT signature being valid is the proof the
request comes from a Clerk session. Clerk dashboard config (Require email,
Require phone, Verify at sign-up) is what guarantees both factors were
verified before the session could exist. If you ever want belt-and-braces
in-token verification, add a JWT template with {{user.email_verified}} +
{{user.phone_number_verified}} claims and gate require_clerk_user on them.
"""

import logging
import secrets
import threading
from typing import Optional

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings
from app.models import User

logger = logging.getLogger(__name__)


_jwks_client_lock = threading.Lock()
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    """Return a process-wide PyJWKClient. Lazily constructed on first use so
    config validation failures don't crash unrelated startup paths."""
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client
    with _jwks_client_lock:
        if _jwks_client is None:
            if not settings.clerk_jwks_url:
                raise RuntimeError(
                    "CLERK_JWKS_URL is not set; Clerk verification is disabled. "
                    "Set it in backend/.env to enable Tier 1 auth."
                )
            _jwks_client = PyJWKClient(settings.clerk_jwks_url, cache_keys=True)
    return _jwks_client


def verify_clerk_jwt(token: str) -> dict:
    """Verify a Clerk session JWT and return the decoded claims.

    Raises jwt.PyJWTError subclasses on any failure (expired, bad signature,
    wrong issuer/azp, etc.). Caller maps to HTTPException.
    """
    if not token:
        raise jwt.InvalidTokenError("Empty token")

    signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key

    decode_kwargs: dict = {
        "algorithms": ["RS256"],
        "options": {"require": ["exp", "iat", "sub"]},
    }
    # Clerk issuer is the JWKS host minus /.well-known/jwks.json
    if settings.clerk_jwks_url:
        issuer = settings.clerk_jwks_url.rsplit("/.well-known/", 1)[0]
        decode_kwargs["issuer"] = issuer
    # Authorized party (azp) is the origin the SDK was loaded from -- set
    # this in prod to bind tokens to risingcompass.net. Leave empty in dev.
    if settings.clerk_authorized_party:
        # PyJWT has no azp option, so check manually after decode.
        pass

    claims = jwt.decode(token, signing_key, **decode_kwargs)

    if settings.clerk_authorized_party:
        azp = claims.get("azp")
        if azp and azp != settings.clerk_authorized_party:
            raise jwt.InvalidTokenError(f"Unexpected azp: {azp}")

    return claims


def _generate_anon_id() -> str:
    """Opaque, stable, non-sequential ID for admin analytics. Keeps handles
    out of default analytics views -- "User-47" style with random suffix so
    counts don't leak."""
    return f"User-{secrets.token_hex(4)}"


def ban_clerk_user(clerk_user_id: str) -> bool:
    """Ban a user at the Clerk API level. Revokes all sessions; the user
    can no longer sign in even with valid credentials. Returns True on
    success (including idempotent already-banned), False on misconfig or
    transient failure -- the local ban still stands either way.

    Uses the Clerk secret key; only callable from server-side code.
    Reference: POST https://api.clerk.com/v1/users/{user_id}/ban
    """
    if not settings.clerk_secret_key:
        logger.error("CLERK_SECRET_KEY not configured; cannot ban Clerk user %s", clerk_user_id)
        return False
    try:
        resp = httpx.post(
            f"https://api.clerk.com/v1/users/{clerk_user_id}/ban",
            headers={
                "Authorization": f"Bearer {settings.clerk_secret_key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("Clerk ban API call failed for %s: %s", clerk_user_id, exc)
        return False
    if resp.status_code in (200, 201):
        return True
    # 404 from Clerk = user already gone; treat as success.
    if resp.status_code == 404:
        return True
    logger.error(
        "Clerk ban API returned %s for %s: %s",
        resp.status_code, clerk_user_id, resp.text[:300],
    )
    return False


def ensure_user_for_clerk_id(db, clerk_user_id: str) -> User:
    """Return the local User row for this Clerk subject, creating it on
    first sight. handle is NULL until POST /api/users/me/setup completes."""
    user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
    if user is not None:
        return user

    # Generate an anon_id that doesn't collide. Random 8-hex space is
    # 4.3 billion, so a few retries is plenty.
    for _ in range(5):
        candidate = _generate_anon_id()
        if not db.query(User).filter(User.anon_id == candidate).first():
            break
    else:
        raise RuntimeError("Could not generate unique anon_id after 5 attempts")

    user = User(
        clerk_user_id=clerk_user_id,
        anon_id=candidate,
        tier="pending",
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Provisioned new local user for clerk_user_id=%s (anon=%s)", clerk_user_id, candidate)
    return user
