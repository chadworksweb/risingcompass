import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from fastapi import Depends

from app.auth import verify_api_key, verify_api_or_service_key
from app.config import settings
from app.database import engine, Base, SessionLocal
from app.migrate import run_migrations
from app.models import AgentDraft, AgentDraftSong, DailyReading
from app.routers import compass, drift, albums, admin, admin_auth, weekly_albums, agent, misread, library_admin, analyzer, submissions_admin, badge, stream, artists, artists_admin, songs, recalibrations, vibe, db_search, calibration_log, tenets, amendments, v1_test, artist_verification, ether_audits, ether_art_chart, backfill_admin, chart_snapshots, users, comments, comments_admin, alerts_admin, identity_webhook, users_admin, motions, motions_admin, chamber

logger = logging.getLogger(__name__)


def _cleanup_orphan_drafts():
    """Delete daily/manual drafts whose date already has a published reading.

    Drafts are transient — they should be deleted after approval. But if
    approval happens outside the normal endpoint (direct DB, manual fix),
    orphan drafts can linger. This runs once at startup as a safety net.

    Filtered to daily/manual draft types only — chart-snapshot drafts
    (Viral 50, etc.) live alongside the daily reading on the same date and
    are not orphaned by the existence of a DailyReading.
    """
    db = SessionLocal()
    try:
        published_dates = {r.date for r in db.query(DailyReading.date).all()}
        orphans = (
            db.query(AgentDraft)
            .filter(AgentDraft.date.in_(published_dates))
            .filter(AgentDraft.draft_type.in_(("daily", "manual")))
            .all()
        )
        if not orphans:
            return
        for draft in orphans:
            db.query(AgentDraftSong).filter(AgentDraftSong.draft_id == draft.id).delete()
            db.delete(draft)
        db.commit()
        logger.info("Startup cleanup: deleted %d orphan daily/manual draft(s)", len(orphans))
    finally:
        db.close()

# Create tables on startup (handles fresh installs)
Base.metadata.create_all(bind=engine)

# Apply versioned migrations (handles ALTER TABLE on existing tables)
run_migrations(engine)

# Bootstrap system API clients + migrate env keys into api_client_keys
from app.services.api_clients import bootstrap_system_clients
bootstrap_system_clients()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan — DB warmup + keepalive, Lyrical Charger session cleanup.

    Backups are triggered externally by cron on le-projects-01 (04:45 UTC
    via /root/risingcompass-backups/backup.sh → POST /api/admin/backup).
    No in-app scheduler to avoid double-running or unpredictable timing.
    """
    from app.database import warmup, keepalive_loop

    # Warm the pool before the first user request so Turso embedded-replica
    # sync happens during startup, not during a visitor's page load.
    warmup()

    _cleanup_orphan_drafts()
    keepalive_task = asyncio.create_task(keepalive_loop(interval_seconds=45))

    # Start the api-key last_used_at flusher. Writes go to Turso primary on
    # a background thread so no user request blocks on them.
    from app.services.api_clients import (
        ensure_last_used_flusher, flush_last_used_once,
    )
    ensure_last_used_flusher()

    # Start the lc_events writer thread for the same reason — every LC page
    # view / search / submission enqueues instead of blocking on a write.
    from app.services.lc_events import _ensure_writer as _ensure_lc_events_writer
    _ensure_lc_events_writer()

    # Backfill Console: any job left in `running` from a prior process
    # gets demoted to `paused` so the admin has to explicitly resume.
    from app.services.backfill.orchestrator import reset_running_jobs_on_startup
    reset_running_jobs_on_startup()

    logger.info("DB keepalive scheduler started")
    yield
    keepalive_task.cancel()
    # Final flush on shutdown so recently-seen keys don't lose their stamps.
    try:
        flush_last_used_once()
    except Exception:
        logger.exception("final last_used_at flush failed")


app = FastAPI(title="The Rising Compass", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Key", "X-Backup-Key", "X-Reading-Cron-Key", "Authorization"],
)


# Dedicated direct-Turso connection for the log writer thread. Bypasses the
# embedded replica on purpose: the replica's client-side WAL serializes writes
# against reads, so logging via the replica wedges every read query for the
# duration of the commit. Direct writes to the primary are ~100ms and don't
# touch the replica file at all.
_log_writer_conn = None


def _get_log_writer_conn():
    global _log_writer_conn
    if _log_writer_conn is not None:
        return _log_writer_conn
    import libsql
    url = settings.database_url
    token = settings.turso_auth_token
    if url.startswith("libsql://") or url.startswith("https://"):
        _log_writer_conn = libsql.connect(database=url, auth_token=token)
    else:
        _log_writer_conn = libsql.connect(database=url)
    return _log_writer_conn


def _write_api_call_log(client_id, method, path, status, ip, user_agent, duration_ms, context_json):
    """Persist one api_call_log row via a dedicated direct-Turso connection."""
    conn = _get_log_writer_conn()
    try:
        conn.execute(
            "INSERT INTO api_call_log (client_id, method, path, status, ip, user_agent, duration_ms, context_json, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (client_id, method, path[:250], status, ip, user_agent, duration_ms, context_json),
        )
        conn.commit()
    except Exception:
        # On any error, drop the connection so the next call reconnects.
        global _log_writer_conn
        try:
            conn.close()
        except Exception:
            pass
        _log_writer_conn = None
        raise


# Background log writer — one queue, one thread. The middleware enqueues and
# returns; the thread drains the queue serially. Necessary because embedded-
# replica writes take ~1s and Starlette BaseHTTPMiddleware awaits both asyncio
# tasks and Response.background before sending the response.
import queue as _queue_mod
import threading as _thr_mod
_LOG_QUEUE: _queue_mod.Queue = _queue_mod.Queue(maxsize=10000)
_log_writer_thread: _thr_mod.Thread | None = None
_log_writer_lock = _thr_mod.Lock()


def _log_writer_loop():
    while True:
        item = _LOG_QUEUE.get()
        if item is None:
            break
        try:
            _write_api_call_log(*item)
        except Exception:
            logger.exception("api_call_log write failed in background thread")


def _ensure_log_writer():
    global _log_writer_thread
    if _log_writer_thread is not None and _log_writer_thread.is_alive():
        return
    with _log_writer_lock:
        if _log_writer_thread is None or not _log_writer_thread.is_alive():
            _log_writer_thread = _thr_mod.Thread(
                target=_log_writer_loop, daemon=True, name="api-call-log-writer"
            )
            _log_writer_thread.start()


@app.middleware("http")
async def log_api_call(request: Request, call_next):
    """Log every /api/* call (excluding /api/admin/* and /api/health) to api_call_log.

    The DB write is handed off to a dedicated background thread via an in-
    memory queue. Neither asyncio.create_task nor Starlette's BackgroundTask
    reliably detach from BaseHTTPMiddleware — both were awaited before the
    response left the server. A raw OS thread draining a queue is the only
    approach that actually fire-and-forgets under Starlette.
    """
    import time as _time
    path = request.url.path
    should_log = (
        path.startswith("/api/")
        and not path.startswith("/api/admin/")
        and path != "/api/health"
    )
    if not should_log:
        return await call_next(request)

    start = _time.time()
    response = await call_next(request)
    status = response.status_code

    try:
        import json as _json
        duration_ms = int((_time.time() - start) * 1000)
        client_id = getattr(request.state, "client_id", None)
        ua = (request.headers.get("user-agent") or "")[:250]
        ip = request.client.host if request.client else None

        ctx: dict = {}
        try:
            qp = dict(request.query_params)
            for k, v in qp.items():
                ctx[k] = v[:200] if isinstance(v, str) else v
        except Exception:
            pass
        endpoint_ctx = getattr(request.state, "call_context", None)
        if isinstance(endpoint_ctx, dict):
            for k, v in endpoint_ctx.items():
                ctx[k] = v[:200] if isinstance(v, str) else v
        context_json = _json.dumps(ctx, default=str) if ctx else None

        _ensure_log_writer()
        try:
            _LOG_QUEUE.put_nowait(
                (client_id, request.method, path, status, ip, ua, duration_ms, context_json)
            )
        except _queue_mod.Full:
            logger.warning("api_call_log queue full — dropping log for %s %s", request.method, path)
    except Exception:
        logger.exception("api_call_log scheduling failed for %s %s", request.method, path)

    return response

# Public routers — require X-Api-Key header
_api_key_dep = [Depends(verify_api_key)]
app.include_router(compass.router, dependencies=_api_key_dep)
app.include_router(drift.router, dependencies=_api_key_dep)
app.include_router(albums.router, dependencies=_api_key_dep)
app.include_router(weekly_albums.router, dependencies=_api_key_dep)
app.include_router(misread.router, dependencies=_api_key_dep)
# misread admin endpoints are mounted separately below with the other admin routers
app.include_router(artist_verification.router, dependencies=_api_key_dep)
# Analyzer accepts either public RC_API_KEY (Lyrical Charger) or RC_SERVICE_KEY
# (first-party callers like chadlewine.com). Endpoints that distinguish
# behavior re-declare the dependency to capture the tier.
app.include_router(analyzer.router, dependencies=[Depends(verify_api_or_service_key)])
app.include_router(badge.router, dependencies=_api_key_dep)
app.include_router(artists.router, dependencies=_api_key_dep)
app.include_router(songs.router, dependencies=_api_key_dep)
app.include_router(vibe.router, dependencies=_api_key_dep)
app.include_router(tenets.router, dependencies=_api_key_dep)
app.include_router(amendments.router, dependencies=_api_key_dep)
app.include_router(ether_art_chart.router, dependencies=_api_key_dep)
app.include_router(chart_snapshots.public_router, dependencies=_api_key_dep)

# Public Participation Tier 1 user endpoints. Self-authenticating via
# Clerk session JWT (require_clerk_user) -- no X-Api-Key gate here, since
# the JWT itself is the authorization. Lazy-creates a users row on first
# authenticated hit.
app.include_router(users.router)

# Public Participation Lobby comments. Reads are anonymous; writes require
# require_clerk_user (Tier 1) and a claimed handle. No X-Api-Key gate --
# the JWT (when present) is the authorization for writes.
app.include_router(comments.router)
app.include_router(comments_admin.router)
app.include_router(alerts_admin.router)
app.include_router(users_admin.router)

# Public Participation Motion Desk (Phase 3.2). Filing requires Tier 2
# (id_verified); reads are public. Admin queue mounts below alongside
# the other admin routers.
app.include_router(motions.router)
app.include_router(motions_admin.router)

# Public Participation Deliberation Chamber (Phase 4). Posting requires
# Tier 2; reads are public. Mounted under each motion via the prefix
# /api/motions/{id}/arguments.
app.include_router(chamber.router)

# Stripe Identity webhook (Phase 3.1). Distinct from the donation webhook
# (/api/stripe-webhook) -- different signing secret so a leak on one
# stream can't forge events on the other.
app.include_router(identity_webhook.router)

# Admin auth — login page (obscured URL), POST /login, POST /logout, GET /me.
# Mounted before the other admin routers so it takes precedence on its
# /api/rc-admin-{token}/login path.
app.include_router(admin_auth.router)

# Admin routers — gated by the rc_admin_session cookie (set by login). The
# legacy verify_admin_key import in each router now points at
# require_admin_session, so X-Admin-Key headers are no longer accepted.
app.include_router(admin.router)
app.include_router(misread.admin_router)
app.include_router(artist_verification.admin_router)
app.include_router(agent.router)
app.include_router(library_admin.router)
app.include_router(submissions_admin.router)
from app.routers import lc_events_admin
app.include_router(lc_events_admin.router)
from app.routers import lc_status_admin
app.include_router(lc_status_admin.router)
from app.routers import donate
app.include_router(donate.router)
from app.routers import api_clients_admin
app.include_router(api_clients_admin.router)
from app.routers import claude_usage_admin
app.include_router(claude_usage_admin.router)
app.include_router(stream.router)
app.include_router(db_search.router)
app.include_router(artists_admin.router)
app.include_router(recalibrations.router)
app.include_router(ether_audits.router)
app.include_router(backfill_admin.router)
app.include_router(calibration_log.router)
app.include_router(calibration_log.public_router, dependencies=_api_key_dep)
app.include_router(chart_snapshots.admin_router)
app.include_router(v1_test.router)


# slowapi rate limiter (defined in analyzer.py, shared across routers)
app.state.limiter = analyzer.limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_with_logging(request: Request, exc: RateLimitExceeded):
    """Log rate-limit hits to lc_events for any /api/analyzer/* route, then defer to slowapi."""
    if request.url.path.startswith("/api/analyzer/"):
        try:
            from app.services.lc_events import write_event, extract_request_meta
            meta = extract_request_meta(request)
            write_event(
                "submission_rate_limited",
                meta["ip"], meta["user_agent"], meta["referrer"],
                payload={"path": request.url.path, "limit": str(exc.detail)},
            )
        except Exception:
            logger.exception("Failed to log rate-limit event")
    return _rate_limit_exceeded_handler(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all handler — log details server-side, return generic message to client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
def health():
    return {"status": "ok"}
