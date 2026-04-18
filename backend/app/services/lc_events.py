"""Lyrical Charger event logging — fire-and-forget activity tracking.

Every meaningful LC interaction (page view, submission, validation failure, bot
trip, search) is recorded in lc_events. Designed to never block the request:
the actual DB write happens in a FastAPI BackgroundTask using its own session.
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
}


def extract_request_meta(request: Request) -> dict:
    """Pull IP, UA, referrer from a request — safe to call before background scheduling."""
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
    """Persist a single event. Opens its own session, swallows all errors."""
    try:
        db = SessionLocal()
        try:
            evt = LcEvent(
                event_type=event_type,
                ip_address=ip,
                user_agent=user_agent,
                referrer=referrer,
                payload_json=json.dumps(payload, default=str) if payload else None,
                submission_id=submission_id,
            )
            db.add(evt)
            db.commit()
        finally:
            db.close()
    except Exception:
        # Logging failures must never bubble — they would mask the real outcome
        # of whatever endpoint just called us.
        logger.exception("Failed to write lc_event %s", event_type)


def schedule_event(
    background_tasks,
    event_type: str,
    request: Request,
    payload: dict[str, Any] | None = None,
    submission_id: int | None = None,
) -> None:
    """Convenience: extract meta + queue write_event as a BackgroundTask."""
    meta = extract_request_meta(request)
    background_tasks.add_task(
        write_event,
        event_type,
        meta["ip"],
        meta["user_agent"],
        meta["referrer"],
        payload,
        submission_id,
    )
