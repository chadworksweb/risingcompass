import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from fastapi import Depends

from app.auth import verify_api_key, verify_api_or_service_key
from app.config import settings
from app.database import engine, Base, SessionLocal
from app.logging_config import configure_logging
from app.migrate import run_migrations

# Attach the durable rotating file handler before anything else logs, so
# startup, migrations, and every swallowed "non-fatal" exception persist.
configure_logging()
from app.models import AgentDraft, AgentDraftSong, DailyReading, ApiCallLog
from app.routers import compass, drift, albums, admin, admin_auth, weekly_albums, agent, misread, library_admin, analyzer, submissions_admin, badge, stream, artists, artists_admin, songs, recalibrations, vibe, db_search, calibration_log, tenets, amendments, v1_test, artist_verification, ether_audits, ether_art_chart, backfill_admin, chart_snapshots, users, comments, comments_admin, alerts_admin, identity_webhook, users_admin, motions, motions_admin, chamber, prose_admin

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

# Seed the prompt-cache advisor alert on-by-default (one-time infra nudge, not a
# high-volume heartbeat). Stays toggleable in the Alerts UI; never overrides a
# later admin choice. See app/services/cache_advisor.py.
from app.services.alerts import ensure_pref_default
ensure_pref_default("prompt_cache_warranted", enabled=True)
# Album Charger: alert the admin by email whenever someone charges an album.
# On by default (the admin asked for it); toggleable in the Alerts UI.
ensure_pref_default("album_charged", enabled=True)
# General inquiry / contact form: alert the admin on each submission.
ensure_pref_default("general_inquiry", enabled=True)
# Faultline: a fault marked critical, or a resolved fault that recurred. Both
# on by default -- a critical or a regression should never sit unseen.
ensure_pref_default("faultline_new_critical", enabled=True)
ensure_pref_default("faultline_regression", enabled=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan — startup cleanup only.

    On Postgres there is no embedded replica to warm and no Hrana stream to
    keep alive, so the old warmup() + keepalive watchdog are gone. last_used_at
    and lc_events now write inline (throttled), so their background threads are
    gone too. Backups are triggered externally by cron on le-projects-01.
    """
    _cleanup_orphan_drafts()

    # Backfill Console: any job left in `running` from a prior process
    # gets demoted to `paused` so the admin has to explicitly resume.
    from app.services.backfill.orchestrator import reset_running_jobs_on_startup
    reset_running_jobs_on_startup()

    logger.info("startup complete")
    yield


app = FastAPI(title="The Rising Compass", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Key", "X-Backup-Key", "X-Reading-Cron-Key", "X-Provenance-Cron-Key", "Authorization"],
)


def _write_api_call_log(client_id, method, path, status, ip, user_agent, duration_ms, context_json):
    """Persist one api_call_log row via a short-lived ORM session. Swallows
    errors — request logging must never break the response. Run off the event
    loop via run_in_threadpool so the synchronous commit doesn't block it."""
    try:
        db = SessionLocal()
        try:
            db.add(ApiCallLog(
                client_id=client_id,
                method=method,
                path=path[:250],
                status=status,
                ip=ip,
                user_agent=user_agent,
                duration_ms=duration_ms,
                context_json=context_json,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("api_call_log write failed for %s %s", method, path)


@app.middleware("http")
async def log_api_call(request: Request, call_next):
    """Log every /api/* call (excluding /api/admin/* and /api/health) to api_call_log.

    The row is written by an ordinary ORM session, offloaded to a threadpool so
    the synchronous commit doesn't block the event loop. On Postgres the write
    is fast and never wedges reads, so the old direct-Turso connection + queue +
    background thread are gone.
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

        await run_in_threadpool(
            _write_api_call_log,
            client_id, request.method, path, status, ip, ua, duration_ms, context_json,
        )
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
from app.routers import inquiries
app.include_router(inquiries.router, dependencies=_api_key_dep)
from app.routers import chart_anomalies
app.include_router(chart_anomalies.router, dependencies=_api_key_dep)
# Analyzer accepts either public RC_API_KEY (Lyrical Charger) or RC_SERVICE_KEY
# (first-party callers like chadlewine.com). Endpoints that distinguish
# behavior re-declare the dependency to capture the tier.
app.include_router(analyzer.router, dependencies=[Depends(verify_api_or_service_key)])
from app.routers import album_charger
app.include_router(album_charger.router, dependencies=[Depends(verify_api_or_service_key)])
app.include_router(badge.router, dependencies=_api_key_dep)
app.include_router(artists.router, dependencies=_api_key_dep)
app.include_router(songs.router, dependencies=_api_key_dep)
# Public page SSR (/songs/<slug>, /artists/<slug>) -- bakes per-entity meta +
# JSON-LD into the head for crawlers. Browser/crawler page loads, so NO
# X-Api-Key dependency. nginx routes the dotless slug paths here.
from app.routers import page_ssr
app.include_router(page_ssr.router)
app.include_router(vibe.user_router)  # Clerk-authed, no X-Api-Key; before the gated router
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

# Public geo lookup for the cookie consent bar's geo-aware default. No
# X-Api-Key gate -- the consent bar loads standalone and calls it anonymously.
from app.routers import geo
app.include_router(geo.router)

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

# Dev Ledger -- the "dev side, exposed" (changelog / roadmap / feature requests
# / bug reports, CalVer-versioned). Reads are public; submit + vote require
# require_clerk_user (Tier 1). No X-Api-Key gate -- the JWT authorizes writes,
# mirroring Motion Desk. Walled from the tenet/framework surfaces above; this is
# the product/engineering layer. See RISING-COMPASS-DEV-LEDGER-SCOPE.md.
from app.routers import dev_ledger
app.include_router(dev_ledger.router)

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
app.include_router(dev_ledger.admin_router)
app.include_router(artist_verification.admin_router)
app.include_router(inquiries.admin_router)
app.include_router(chart_anomalies.admin_router)
app.include_router(agent.router)
app.include_router(library_admin.router)
app.include_router(submissions_admin.router)
from app.routers import lc_events_admin
app.include_router(lc_events_admin.router)
from app.routers import lc_status_admin
app.include_router(lc_status_admin.router)
from app.routers import launch_admin
app.include_router(launch_admin.router)
from app.routers import provenance
app.include_router(provenance.router)
from app.routers import faultline as faultline_router
app.include_router(faultline_router.router)
from app.routers import faultline_agent
app.include_router(faultline_agent.router)
from app.routers import donate
app.include_router(donate.router)
# Billing -- subscription/pack Checkout, wallet, estimate, billing webhook.
# Unauthed at the router level; individual routes use Depends(require_clerk_user)
# where needed. The webhook is signature-verified per-request.
from app.routers import billing as billing_router
app.include_router(billing_router.router)
from app.routers import api_clients_admin
app.include_router(api_clients_admin.router)
from app.routers import claude_usage_admin
app.include_router(claude_usage_admin.router)
app.include_router(stream.router)
app.include_router(db_search.router)
app.include_router(artists_admin.router)
app.include_router(recalibrations.router)
app.include_router(prose_admin.router)
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
