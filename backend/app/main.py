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
from app.routers import compass, drift, albums, admin, weekly_albums, library, agent, misread, library_admin, analyzer, submissions_admin, badge, stream, artists, artists_admin, songs
from app.services.backup import run_backup

logger = logging.getLogger(__name__)


def _cleanup_orphan_drafts():
    """Delete drafts whose date already has a published reading.

    Drafts are transient — they should be deleted after approval. But if
    approval happens outside the normal endpoint (direct DB, manual fix),
    orphan drafts can linger. This runs once at startup as a safety net.
    """
    db = SessionLocal()
    try:
        published_dates = {r.date for r in db.query(DailyReading.date).all()}
        orphans = db.query(AgentDraft).filter(AgentDraft.date.in_(published_dates)).all()
        if not orphans:
            return
        for draft in orphans:
            db.query(AgentDraftSong).filter(AgentDraftSong.draft_id == draft.id).delete()
            db.delete(draft)
        db.commit()
        logger.info("Startup cleanup: deleted %d orphan draft(s)", len(orphans))
    finally:
        db.close()

# Create tables on startup (handles fresh installs)
Base.metadata.create_all(bind=engine)

# Apply versioned migrations (handles ALTER TABLE on existing tables)
run_migrations(engine)

# Bootstrap system API clients + migrate env keys into api_client_keys
from app.services.api_clients import bootstrap_system_clients
bootstrap_system_clients()


async def _daily_backup_loop():
    """Run a database backup once per day."""
    while True:
        run_backup()
        await asyncio.sleep(86400)  # 24 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan — starts daily backup task and Lyrical Charger session cleanup."""
    _cleanup_orphan_drafts()
    backup_task = asyncio.create_task(_daily_backup_loop())
    cleanup_task = asyncio.create_task(analyzer.session_cleanup_loop())
    logger.info("Daily backup scheduler started")
    logger.info("Lyrical Charger session cleanup scheduler started")
    yield
    backup_task.cancel()
    cleanup_task.cancel()


app = FastAPI(title="The Rising Compass", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key", "X-Api-Key", "Authorization"],
)


def _write_api_call_log(client_id, method, path, status, ip, user_agent, duration_ms, context_json):
    """Persist one api_call_log row. Runs off the event loop via asyncio.to_thread."""
    from app.database import SessionLocal as _SL
    from app.models import ApiCallLog
    db = _SL()
    try:
        db.add(ApiCallLog(
            client_id=client_id, method=method, path=path[:250],
            status=status, ip=ip, user_agent=user_agent, duration_ms=duration_ms,
            context_json=context_json,
        ))
        db.commit()
    finally:
        db.close()


@app.middleware("http")
async def log_api_call(request: Request, call_next):
    """Log every /api/* call (excluding /api/admin/* and /api/health) to api_call_log.

    The DB write is fire-and-forget — it runs in a background task so the HTTP
    response isn't blocked waiting for the Turso write to commit. Writes through
    the embedded replica round-trip to the primary (~1s), so blocking every
    public request on them is not acceptable.
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
        import asyncio as _asyncio
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

        async def _bg_write():
            try:
                await _asyncio.to_thread(
                    _write_api_call_log,
                    client_id, request.method, path, status, ip, ua, duration_ms, context_json,
                )
            except Exception:
                logger.exception("api_call_log write failed for %s %s", request.method, path)

        _asyncio.create_task(_bg_write())
    except Exception:
        logger.exception("api_call_log scheduling failed for %s %s", request.method, path)

    return response

# Public routers — require X-Api-Key header
_api_key_dep = [Depends(verify_api_key)]
app.include_router(compass.router, dependencies=_api_key_dep)
app.include_router(drift.router, dependencies=_api_key_dep)
app.include_router(albums.router, dependencies=_api_key_dep)
app.include_router(weekly_albums.router, dependencies=_api_key_dep)
app.include_router(library.router, dependencies=_api_key_dep)
app.include_router(misread.router, dependencies=_api_key_dep)
# Analyzer accepts either public RC_API_KEY (Lyrical Charger) or RC_SERVICE_KEY
# (first-party callers like chadlewine.com). Endpoints that distinguish
# behavior re-declare the dependency to capture the tier.
app.include_router(analyzer.router, dependencies=[Depends(verify_api_or_service_key)])
app.include_router(badge.router, dependencies=_api_key_dep)
app.include_router(artists.router, dependencies=_api_key_dep)
app.include_router(songs.router, dependencies=_api_key_dep)

# Admin routers — use X-Admin-Key (handled per-endpoint)
app.include_router(admin.router)
app.include_router(agent.router)
app.include_router(library_admin.router)
app.include_router(submissions_admin.router)
from app.routers import lc_events_admin
app.include_router(lc_events_admin.router)
from app.routers import api_clients_admin
app.include_router(api_clients_admin.router)
app.include_router(stream.router)
app.include_router(artists_admin.router)


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
