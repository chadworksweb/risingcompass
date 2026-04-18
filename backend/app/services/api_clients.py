"""API client key resolution + bootstrap.

Every /api/* request's X-Api-Key is SHA-256 hashed and looked up in
api_client_keys. On startup we ensure three system clients exist:

  - legacy-public  → holds the hash of RC_API_KEY env var (LC web tool, RC
                     frontend, any pre-migration consumer). behavior=public.
  - legacy-service → holds the hash of RC_SERVICE_KEY env var. behavior=service.
  - chadlewine     → first real-world SaaS client. A fresh key is generated on
                     seed if none exists; that key is the one chadlewine.com
                     should use going forward.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import ApiClient, ApiClientKey


@dataclass
class ResolvedClient:
    """Session-free client snapshot — safe to use after the DB session closes."""
    id: int
    slug: str
    behavior: str
    status: str

logger = logging.getLogger(__name__)


def hash_key(raw: str) -> str:
    """SHA-256 of the raw key — stored so the raw value never sits in the DB."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def key_prefix(raw: str) -> str:
    """First 8 chars — safe to show in the admin UI for identifying a key."""
    return raw[:8]


def generate_raw_key() -> str:
    """64-char hex — same shape as the existing RC_API_KEY / RC_SERVICE_KEY."""
    return secrets.token_hex(32)


def resolve_key(db: Session, raw_key: str) -> ResolvedClient | None:
    """Look up the client that owns this key. Updates last_used_at on hit.

    Returns a ResolvedClient snapshot (session-free) or None if the key is
    unknown, revoked, or the client is suspended/revoked.
    """
    if not raw_key:
        return None
    key = (
        db.query(ApiClientKey)
        .filter(ApiClientKey.key_hash == hash_key(raw_key))
        .filter(ApiClientKey.revoked_at.is_(None))
        .first()
    )
    if not key:
        return None

    client = db.query(ApiClient).filter(ApiClient.id == key.client_id).first()
    if not client or client.status != "active":
        return None

    snapshot = ResolvedClient(id=client.id, slug=client.slug, behavior=client.behavior, status=client.status)

    # Fire-and-forget last-used update
    try:
        key.last_used_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()

    return snapshot


def _ensure_client(db: Session, *, slug: str, name: str, behavior: str,
                   plan_tier: str = "internal", notes: str | None = None) -> ApiClient:
    client = db.query(ApiClient).filter(ApiClient.slug == slug).first()
    if client:
        return client
    client = ApiClient(slug=slug, name=name, behavior=behavior, plan_tier=plan_tier, notes=notes)
    db.add(client)
    db.flush()
    logger.info("Created api_client %s (behavior=%s)", slug, behavior)
    return client


def _ensure_key_for_raw(db: Session, client: ApiClient, raw: str, label: str) -> None:
    """Insert the hash of `raw` against `client` if not already present."""
    if not raw:
        return
    h = hash_key(raw)
    existing = db.query(ApiClientKey).filter(ApiClientKey.key_hash == h).first()
    if existing:
        return
    db.add(ApiClientKey(client_id=client.id, key_hash=h, key_prefix=key_prefix(raw), label=label))
    logger.info("Seeded key %s…%s for client %s", key_prefix(raw), "", client.slug)


def bootstrap_system_clients() -> None:
    """Run once on startup. Idempotent — missing rows are created, existing rows untouched."""
    db = SessionLocal()
    try:
        legacy_public = _ensure_client(
            db, slug="legacy-public", name="RC Public (legacy env key)",
            behavior="public", plan_tier="system",
            notes="Backs RC_API_KEY env var. Used by the RC frontend, Lyrical Charger, and any pre-migration API consumer.",
        )
        legacy_service = _ensure_client(
            db, slug="legacy-service", name="RC Service (legacy env key)",
            behavior="service", plan_tier="system",
            notes="Backs RC_SERVICE_KEY env var. Covers any first-party caller not yet migrated to its own client.",
        )
        chadlewine = _ensure_client(
            db, slug="chadlewine", name="Chad Lewine (chadlewine.com)",
            behavior="service", plan_tier="internal",
            notes="Artist site — consumes RC badge lookups and calibrate-lyrics.",
        )
        db.flush()

        if settings.rc_api_key:
            _ensure_key_for_raw(db, legacy_public, settings.rc_api_key, label="RC_API_KEY (env)")
        if settings.rc_service_key:
            _ensure_key_for_raw(db, legacy_service, settings.rc_service_key, label="RC_SERVICE_KEY (env)")

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("bootstrap_system_clients failed")
    finally:
        db.close()


def issue_key(db: Session, client: ApiClient, label: str | None = None) -> str:
    """Generate a new raw key for a client, persist only its hash, return the raw.
    The raw value is shown once to the admin UI and cannot be recovered later.
    """
    raw = generate_raw_key()
    db.add(ApiClientKey(
        client_id=client.id,
        key_hash=hash_key(raw),
        key_prefix=key_prefix(raw),
        label=label,
    ))
    return raw
