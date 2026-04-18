"""Auth utilities: API key verification, client resolution, HMAC tokens.

Every X-Api-Key is resolved via services.api_clients.resolve_key against the
api_client_keys table. The legacy RC_API_KEY / RC_SERVICE_KEY env values are
seeded into the table at startup so they keep working during rollout.

Both verify dependencies attach request.state.client_id for the API-call
logging middleware. verify_api_or_service_key also returns the behavior tier
("public" | "service") so calibrate endpoints can branch.
"""

import hashlib
import hmac
import time

from fastapi import Header, HTTPException, Request

from app.config import settings
from app.database import SessionLocal
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
