"""Admin authentication: password hashing, sessions, rate limiting.

The session model is server-issued opaque tokens (32 random bytes,
url-safe base64 = 43 chars) stored only as SHA-256 hashes in the DB.
Cookies use HttpOnly + Secure + SameSite=Strict and are scoped to
/api/admin so they're never sent on public risingcompass.net requests.

Rate limit reads admin_login_attempts directly — no Redis needed; the
window is short (15min) and admin login volume is low.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminLoginAttempt, AdminSession, AdminUser


SESSION_COOKIE_NAME = "rc_admin_session"

# Module-level — argon2-cffi parameters are fine at defaults (memory=64MB,
# time=3, parallelism=4). Single instance is thread-safe.
_ph = PasswordHasher()

# Pre-computed hash for the dummy password used to keep login timing flat
# when the username doesn't exist. Computed once at import to avoid the
# ~80ms hashing cost on every unknown-user login attempt.
_DUMMY_HASH = _ph.hash("not-a-real-password-fixed-string")


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    try:
        _ph.verify(hashed, plain)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def waste_time_against_unknown_user(plain: str) -> None:
    """Match the timing of a real verify so 'unknown user' and 'wrong
    password' are indistinguishable to a remote attacker."""
    try:
        _ph.verify(_DUMMY_HASH, plain)
    except (VerifyMismatchError, InvalidHashError):
        pass


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def record_attempt(
    db: Session,
    *,
    username: Optional[str],
    ip: Optional[str],
    user_agent: Optional[str],
    success: bool,
    reason: Optional[str] = None,
) -> None:
    db.add(AdminLoginAttempt(
        username=(username or None),
        ip=(ip or None),
        user_agent=(user_agent or "")[:500] or None,
        success=success,
        reason=reason,
    ))
    db.commit()


def too_many_recent_failures(
    db: Session, *, username: Optional[str], ip: Optional[str]
) -> bool:
    """Return True if either the username or the IP has exceeded the
    failure cap inside the rolling window. Rejects login before any
    password verification when triggered."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.admin_login_window_seconds)
    cap = settings.admin_login_max_failures_per_window

    if username:
        n = (
            db.query(AdminLoginAttempt)
            .filter(AdminLoginAttempt.username == username)
            .filter(AdminLoginAttempt.success.is_(False))
            .filter(AdminLoginAttempt.attempted_at >= cutoff)
            .count()
        )
        if n >= cap:
            return True

    if ip:
        n = (
            db.query(AdminLoginAttempt)
            .filter(AdminLoginAttempt.ip == ip)
            .filter(AdminLoginAttempt.success.is_(False))
            .filter(AdminLoginAttempt.attempted_at >= cutoff)
            .count()
        )
        if n >= cap:
            return True

    return False


def create_session(
    db: Session,
    user: AdminUser,
    *,
    ip: Optional[str],
    user_agent: Optional[str],
) -> str:
    """Mint a new session and return the RAW token (caller sets cookie)."""
    raw = generate_session_token()
    now = datetime.now(timezone.utc)
    sess = AdminSession(
        user_id=user.id,
        token_hash=hash_token(raw),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.admin_session_idle_seconds),
        absolute_expires_at=now + timedelta(seconds=settings.admin_session_absolute_seconds),
        ip=(ip or None),
        user_agent=(user_agent or "")[:500] or None,
    )
    db.add(sess)
    db.commit()
    return raw


def lookup_session(
    db: Session, raw_token: str
) -> Optional[tuple[AdminSession, AdminUser]]:
    """Resolve a raw cookie value to (session, user) if valid, else None."""
    if not raw_token:
        return None
    th = hash_token(raw_token)
    sess = db.query(AdminSession).filter(AdminSession.token_hash == th).first()
    if not sess:
        return None
    if sess.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    if sess.expires_at < now or sess.absolute_expires_at < now:
        return None
    user = db.query(AdminUser).filter(AdminUser.id == sess.user_id).first()
    if not user or not user.is_active:
        return None
    return sess, user


def touch_session(db: Session, sess: AdminSession) -> None:
    """Slide the idle window forward, capped by absolute expiry."""
    now = datetime.now(timezone.utc)
    new_expires = now + timedelta(seconds=settings.admin_session_idle_seconds)
    if new_expires > sess.absolute_expires_at:
        new_expires = sess.absolute_expires_at
    sess.expires_at = new_expires
    sess.last_seen_at = now
    db.commit()


def revoke_session(db: Session, sess: AdminSession) -> None:
    sess.revoked_at = datetime.now(timezone.utc)
    db.commit()


def revoke_all_for_user(db: Session, user_id: int) -> int:
    """Revoke every active session for one user. Returns count revoked."""
    now = datetime.now(timezone.utc)
    n = (
        db.query(AdminSession)
        .filter(AdminSession.user_id == user_id)
        .filter(AdminSession.revoked_at.is_(None))
        .update({AdminSession.revoked_at: now}, synchronize_session=False)
    )
    db.commit()
    return n
