"""Outbound classification push to chadlewine.com.

When a song's canonical calibration is written, RC POSTs the full badge record
to chadlewine's `/api/webhooks/rc-classification` receiver, which stores it in
`rc_badge_cache` (and updates `songs.rc_*`). chadlewine then renders badges from
its own local state instead of calling RC live on every page render -- the push
half of the badge pipeline.

Ships DARK: a no-op unless both CHADLEWINE_WEBHOOK_URL and
CHADLEWINE_WEBHOOK_SECRET are set. Fully fail-soft and fire-and-forget (a daemon
thread with a short timeout), so a slow or down chadlewine can never block or
fail a calibration. chadlewine's read-through cache + TTL self-heals any missed
push, so best-effort delivery is sufficient.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

from app.constants import COLOR_LABELS, COLOR_HEX
from app.services.artist_utils import generate_song_slug

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5


def _config() -> tuple[str, str] | None:
    url = (os.getenv("CHADLEWINE_WEBHOOK_URL") or "").strip()
    secret = (os.getenv("CHADLEWINE_WEBHOOK_SECRET") or "").strip()
    if not url or not secret:
        return None
    return url, secret


def _parse_topics(raw) -> list[str] | None:
    if not raw:
        return None
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else None
    except (ValueError, TypeError):
        return None


def build_badge_payload(song) -> dict:
    """Build the full badge record (chadlewine's RisingCompassBadgeData shape)
    from a canonical `songs` row. Self-contained: reads only loaded attributes,
    no further DB queries, so it is safe to call from any session/thread."""
    color = getattr(song, "rubric_color", None)
    return {
        "tier": color,
        "tier_label": COLOR_LABELS.get(color) if color else None,
        "tier_hex": COLOR_HEX.get(color) if color else None,
        "charge": getattr(song, "charge_value", None),
        "charge_summary": getattr(song, "charge_summary", None),
        "contaminated": bool(getattr(song, "contaminated", False)),
        "contamination_note": getattr(song, "contamination_note", None),
        "song_slug": generate_song_slug(
            getattr(song, "title", "") or "", getattr(song, "artist", "") or ""
        ),
        "deadpan_line": getattr(song, "deadpan_line", None),
        "topics": _parse_topics(getattr(song, "topics", None)),
        "listener_effects_prose": getattr(song, "listener_effects_prose", None),
        "societal_effects_prose": getattr(song, "societal_effects_prose", None),
        "confidence": getattr(song, "confidence", None),
        "song_source": "songs",
    }


def _post(url: str, secret: str, body: dict) -> None:
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-RC-Webhook-Secret": secret,
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            resp.read()  # drain; status is best-effort
    except Exception:
        # Best-effort: chadlewine's read-through cache + TTL recovers a missed push.
        logger.warning("chadlewine webhook push failed (non-fatal)", exc_info=True)


def push_song_classification(song, *, action: str = "webhook-update") -> None:
    """Fire-and-forget push of a canonical song's calibration to chadlewine.

    No-op unless configured. Builds the payload synchronously (off the live
    `song` object, before the thread starts, so there's no cross-thread session
    access) and sends it on a daemon thread."""
    cfg = _config()
    if cfg is None:
        return
    if getattr(song, "rubric_color", None) is None:
        return  # uncalibrated row -> nothing to push
    url, secret = cfg
    try:
        badge = build_badge_payload(song)
        body = {
            "rc_song_id": getattr(song, "id", None),
            "match": {
                "slug": badge["song_slug"],
                "title": getattr(song, "title", None),
                "artist": getattr(song, "artist", None),
            },
            "classification": {
                "tier": badge["tier"],
                "charge": badge["charge"],
                "charge_summary": badge["charge_summary"],
                "contaminated": badge["contaminated"],
                "confidence": badge["confidence"],
                "song_source": "songs",
            },
            "badge": badge,
            "action": action,
        }
    except Exception:
        logger.warning("chadlewine webhook payload build failed (non-fatal)", exc_info=True)
        return

    threading.Thread(
        target=_post, args=(url, secret, body), daemon=True
    ).start()
