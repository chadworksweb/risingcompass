"""Auth utilities: API key verification and HMAC token generation.

verify_api_key — dependency for public API endpoints (consumer access).
HMAC tokens — time-limited, signed tokens for email approval links.
"""

import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from app.config import settings


def verify_api_key(x_api_key: str = Header(...)):
    """Require a valid API key via X-Api-Key header.

    Used on all public endpoints. Consumers (RC frontend, Lyric Transformer,
    Lyrical Charger) must send this key. Admin endpoints use X-Admin-Key instead.
    """
    if not hmac.compare_digest(x_api_key, settings.rc_api_key):
        raise HTTPException(status_code=403, detail="Invalid API key")


def verify_api_or_service_key(x_api_key: str = Header(...)) -> str:
    """Accept either RC_API_KEY (public) or RC_SERVICE_KEY (first-party).

    Returns the tier name ("public" | "service") so endpoints can branch on it —
    service callers skip bot protection, set their own source, and bypass
    LC activity logging. Used on calibration endpoints that have both public
    (Lyrical Charger) and first-party (chadlewine.com, internal scripts) callers.
    """
    if hmac.compare_digest(x_api_key, settings.rc_api_key):
        return "public"
    if settings.rc_service_key and hmac.compare_digest(x_api_key, settings.rc_service_key):
        return "service"
    raise HTTPException(status_code=403, detail="Invalid API key")


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
