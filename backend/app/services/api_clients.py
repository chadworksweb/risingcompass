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
import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import ApiClient, ApiClientKey


# --- last_used_at tracking -------------------------------------------------
# Every /api/* request passes through verify_api_key -> resolve_key. We stamp
# last_used_at inline, but throttled in-memory to at most one write per key per
# _LAST_USED_THROTTLE_SECS so a busy key doesn't generate a DB write on every
# request. On Postgres an inline UPDATE no longer wedges reads (the old Turso
# embedded-replica WAL contention that forced a background flusher is gone);
# last_used_at is a telemetry hint, so a missed stamp is harmless.

_LAST_USED_THROTTLE_SECS = 60
_last_used_written: dict[int, float] = {}  # key_id -> monotonic of last write
_last_used_lock = threading.Lock()


@dataclass
class ResolvedClient:
    """Session-free client snapshot — safe to use after the DB session closes."""
    id: int
    slug: str
    behavior: str
    status: str
    plan_tier: str = "free"

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

    snapshot = ResolvedClient(
        id=client.id, slug=client.slug, behavior=client.behavior,
        status=client.status, plan_tier=client.plan_tier or "free",
    )

    # Stamp last_used inline, throttled in-memory. Telemetry only.
    _stamp_last_used(key.id)

    return snapshot


# --- last_used_at stamping -------------------------------------------------

def _stamp_last_used(key_id: int) -> None:
    """Update api_client_keys.last_used_at for this key, at most once per
    _LAST_USED_THROTTLE_SECS. Swallows errors (telemetry hint, not critical)."""
    mono = _time.monotonic()
    with _last_used_lock:
        if mono - _last_used_written.get(key_id, 0.0) < _LAST_USED_THROTTLE_SECS:
            return
        _last_used_written[key_id] = mono
    try:
        db = SessionLocal()
        try:
            db.query(ApiClientKey).filter(ApiClientKey.id == key_id).update(
                {ApiClientKey.last_used_at: datetime.utcnow()}
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("last_used_at update failed for key %s", key_id)


def _ensure_client(db: Session, *, slug: str, name: str, behavior: str,
                   plan_tier: str = "internal", notes: str | None = None,
                   sync_notes: bool = False) -> ApiClient:
    """Create the client if missing. If sync_notes=True, also overwrite the
    stored notes on existing rows when they differ — the code is source of
    truth for managed (system-seeded) clients so schema/behavior changes
    show up in the admin UI on next boot.
    """
    client = db.query(ApiClient).filter(ApiClient.slug == slug).first()
    if client:
        if sync_notes and notes is not None and client.notes != notes:
            client.notes = notes
            client.updated_at = datetime.utcnow()
            logger.info("Resynced notes for api_client %s", slug)
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
    """Run on every startup. Missing rows are created. For system-seeded
    clients (legacy-public, legacy-service, chadlewine) the `notes` field is
    resynced from code so changes to tier/rate-limit behavior surface in the
    admin UI without manual edits.
    """
    db = SessionLocal()
    try:
        legacy_public = _ensure_client(
            db, slug="legacy-public", name="RC Public (legacy env key)",
            behavior="public", plan_tier="system",
            notes=(
                "Backs RC_API_KEY env var. Used by the RC frontend, Lyrical "
                "Charger web tool, and any pre-migration API consumer.\n\n"
                "Tier: public — bot protection (honeypot + optional Turnstile) "
                "enforced on calibrate-* endpoints; source tag is forced to "
                "'lyrical_charger'; lc_events are logged.\n\n"
                "Rate limits (calibrate-lyrics, calibrate-search): 20/day "
                "per-IP (shared across all public callers from that IP). "
                "Other analyzer endpoints: 60/hour or 20/hour per-IP."
            ),
            sync_notes=True,
        )
        legacy_service = _ensure_client(
            db, slug="legacy-service", name="RC Service (legacy env key)",
            behavior="service", plan_tier="system",
            notes=(
                "Backs RC_SERVICE_KEY env var. Covers any first-party caller "
                "that hasn't been migrated to its own client row yet.\n\n"
                "Tier: service — bot protection skipped; source taken from "
                "request body (falls back to 'service'); no lc_events "
                "logging.\n\n"
                "Rate limits (calibrate-* endpoints): 1000/day on a bucket "
                "keyed by client slug (i.e. isolated from public IP traffic "
                "and from other service clients)."
            ),
            sync_notes=True,
        )
        _ensure_client(
            db, slug="chadlewine", name="Chad Lewine (chadlewine.com)",
            behavior="service", plan_tier="internal",
            notes=(
                "Artist catalog site. Batch-submits Chad Lewine's discography "
                "to the calibrator and consumes badge lookups for song detail "
                "pages.\n\n"
                "Tier: service — bypasses bot protection / Turnstile / "
                "lc_events logging; supplies its own `source: \"chadlewine\"` "
                "tag on submissions.\n\n"
                "Endpoints used:\n"
                "- POST /api/analyzer/calibrate-lyrics\n"
                "- GET  /api/compass/song-badge (via fetchBadge helper)\n\n"
                "Rate limits (calibrate-* endpoints): 1000/day on a per-client "
                "bucket keyed on slug `chadlewine`. Isolated from the public "
                "IP bucket so batch runs don't collide with Lyrical Charger "
                "traffic.\n\n"
                "Changelog:\n"
                "- 2026-04-22: tier-aware rate limiter wired into analyzer.py. "
                "Previously all calibrate-* calls shared the 20/day per-IP "
                "public bucket, which blocked any batch job larger than 20 "
                "songs regardless of key tier."
            ),
            sync_notes=True,
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
