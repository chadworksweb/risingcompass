"""Lyrical Charger event logging — fire-and-forget activity tracking.

Every meaningful LC interaction (page view, submission, validation failure, bot
trip, search) is recorded in lc_events. Designed to never mask the calling
endpoint's outcome: all write errors are swallowed.

On Postgres the write is an ordinary short-lived ORM session committed inline.
The old background queue + direct-libsql writer existed only because the Turso
embedded replica's WAL serialized writes against reads (a request-thread write
wedged user queries ~1s); Postgres MVCC has no such contention, so the machinery
is gone. See RISING-COMPASS-POSTGRES-MIGRATION.md.
"""

import json
import logging
from typing import Any

from fastapi import Request
from slowapi.util import get_remote_address

from app.database import SessionLocal
from app.models import LcEvent

logger = logging.getLogger(__name__)


# Known event types — kept here as documentation; not enforced.
EVENT_TYPES = {
    "page_view",
    "session_create",
    "search_query",
    "submission_success",
    "submission_failed_validation",
    "submission_rate_limited",
    "submission_honeypot",
    "submission_turnstile_failed",
    "submission_other_error",
    # Album Charger
    "album_search_query",
    "album_success",
    "album_no_tracks",
    "album_other_error",
}


# --- Public API ---------------------------------------------------------

def extract_request_meta(request: Request) -> dict:
    """Pull IP, UA, referrer from a request — safe to call before scheduling."""
    return {
        "ip": get_remote_address(request),
        "user_agent": (request.headers.get("user-agent") or "")[:500],
        "referrer": (request.headers.get("referer") or "")[:500],
    }


def write_event(
    event_type: str,
    ip: str | None,
    user_agent: str | None,
    referrer: str | None,
    payload: dict[str, Any] | None = None,
    submission_id: int | None = None,
) -> None:
    """Write one lc_events row. Swallows all errors — telemetry failures must
    never mask the outcome of the endpoint that called us."""
    try:
        payload_json = json.dumps(payload, default=str) if payload else None
        db = SessionLocal()
        try:
            db.add(LcEvent(
                event_type=event_type,
                ip_address=ip,
                user_agent=user_agent,
                referrer=referrer,
                payload_json=payload_json,
                submission_id=submission_id,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to write lc_event %s", event_type)


def schedule_event(
    background_tasks,
    event_type: str,
    request: Request,
    payload: dict[str, Any] | None = None,
    submission_id: int | None = None,
) -> None:
    """Kept for call-site compatibility; `background_tasks` is unused."""
    meta = extract_request_meta(request)
    write_event(
        event_type,
        meta["ip"],
        meta["user_agent"],
        meta["referrer"],
        payload,
        submission_id,
    )
