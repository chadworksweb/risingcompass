"""HMAC token utilities for email approval links.

Generates time-limited, signed tokens so the admin key never appears in URLs.
"""

import hashlib
import hmac
import time

from app.config import settings


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
