"""Server-side PostHog capture for events the browser can't reliably send:
revenue (the Stripe billing webhook) and the async album-charge worker (the
client may close the tab before the poll sees the job finish).

distinct_id is ALWAYS the user's Clerk user id (``User.clerk_user_id``) so
these events merge into the same PostHog person the frontend identifies on
sign-in. Anonymous actors are not captured server-side (no stable id that
merges with the browser person); the frontend handles the anon path.

Fire-and-forget and fail-soft: a PostHog outage, a missing key, or a bad
event must never break billing or the album worker. No-op when
``posthog_api_key`` is unset (e.g. local dev). The SDK batches on its own
background thread and flushes within ~0.5s, so capture() does not block the
request.
"""

import logging
import threading

from app.config import settings

logger = logging.getLogger(__name__)

_client = None
_init_done = False
_init_lock = threading.Lock()


def _get_client():
    """Lazily build the singleton client. Returns None when PostHog is
    unconfigured or initialization failed (both are permanent for the
    process, so we only try once)."""
    global _client, _init_done
    if _init_done:
        return _client
    with _init_lock:
        if _init_done:
            return _client
        _init_done = True
        if not settings.posthog_api_key:
            return None
        try:
            from posthog import Posthog

            _client = Posthog(
                project_api_key=settings.posthog_api_key,
                host=settings.posthog_host or "https://us.i.posthog.com",
            )
        except Exception:
            logger.exception("PostHog client init failed; server analytics disabled")
            _client = None
    return _client


def capture(distinct_id, event, properties=None):
    """Send a server-side event. No-op (never raises) if PostHog is
    unconfigured or distinct_id is missing."""
    if not distinct_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.capture(distinct_id, event, properties=properties or {})
    except Exception:
        logger.exception("PostHog capture failed for event=%s", event)
