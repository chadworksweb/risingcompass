"""Lyrical Charger event logging — fire-and-forget activity tracking.

Every meaningful LC interaction (page view, submission, validation failure, bot
trip, search) is recorded in lc_events. Designed to never block the request.

Write path: the middleware/endpoint calls `write_event(...)` which enqueues
onto an in-process queue and returns immediately. A single daemon OS thread
drains the queue and commits via a direct libsql connection to the Turso
primary (bypassing the embedded replica session entirely, same trick
`api_call_log` uses in main.py).

Why not FastAPI BackgroundTask anymore: BackgroundTask runs *after* the
endpoint but is still awaited inside Starlette middleware before the response
leaves the server. Under the log_api_call middleware in main.py, every
background task ends up blocking the response for the duration of its Turso
commit (~1s). A raw thread + queue is the only approach that truly
fire-and-forgets under this stack.
"""

import json
import logging
import queue as _queue_mod
import threading
from typing import Any

from fastapi import Request
from slowapi.util import get_remote_address

from app.config import settings

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


# --- Background writer --------------------------------------------------

_event_queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=10000)
_writer_conn = None  # libsql.Connection | None — lazy-init inside thread
_writer_thread: threading.Thread | None = None
_writer_lock = threading.Lock()


def _get_writer_conn():
    """Direct libsql connection to Turso primary. Bypasses the embedded
    replica so writes don't contend with replica WAL reads."""
    global _writer_conn
    if _writer_conn is not None:
        return _writer_conn
    import libsql
    url = settings.database_url
    token = settings.turso_auth_token
    if url.startswith("libsql://") or url.startswith("https://"):
        _writer_conn = libsql.connect(database=url, auth_token=token)
    else:
        _writer_conn = libsql.connect(database=url)
    return _writer_conn


def _write_event_direct(
    event_type: str,
    ip: str | None,
    user_agent: str | None,
    referrer: str | None,
    payload_json: str | None,
    submission_id: int | None,
) -> None:
    """Execute one INSERT via the direct connection. Resets the connection
    on any error so the next write reconnects cleanly."""
    global _writer_conn
    conn = _get_writer_conn()
    try:
        conn.execute(
            "INSERT INTO lc_events "
            "(event_type, ip_address, user_agent, referrer, payload_json, submission_id, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            (event_type, ip, user_agent, referrer, payload_json, submission_id),
        )
        conn.commit()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        _writer_conn = None
        raise


def _writer_loop():
    while True:
        item = _event_queue.get()
        if item is None:
            break
        try:
            _write_event_direct(*item)
        except Exception:
            logger.exception("lc_event write failed in background thread")


def _ensure_writer() -> None:
    """Start the background writer thread once. Idempotent, thread-safe."""
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            _writer_thread = threading.Thread(
                target=_writer_loop, daemon=True, name="lc-events-writer"
            )
            _writer_thread.start()


# --- Public API ---------------------------------------------------------

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
    """Enqueue an event for the background writer. Non-blocking.

    Swallows all errors — telemetry failures must never mask the outcome of
    the endpoint that called us.
    """
    try:
        payload_json = json.dumps(payload, default=str) if payload else None
        _ensure_writer()
        try:
            _event_queue.put_nowait(
                (event_type, ip, user_agent, referrer, payload_json, submission_id)
            )
        except _queue_mod.Full:
            logger.warning("lc_event queue full — dropping %s", event_type)
    except Exception:
        logger.exception("Failed to enqueue lc_event %s", event_type)


def schedule_event(
    background_tasks,
    event_type: str,
    request: Request,
    payload: dict[str, Any] | None = None,
    submission_id: int | None = None,
) -> None:
    """Kept for call-site compatibility; `background_tasks` is unused.

    Previously wrapped write_event in a FastAPI BackgroundTask. That
    approach blocked responses under our middleware (see module docstring).
    Now calls write_event directly — which is already non-blocking via the
    queue + thread pattern.
    """
    meta = extract_request_meta(request)
    write_event(
        event_type,
        meta["ip"],
        meta["user_agent"],
        meta["referrer"],
        payload,
        submission_id,
    )
