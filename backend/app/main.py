import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import engine, Base, SessionLocal
from app.migrate import run_migrations
from app.models import AgentDraft, AgentDraftSong, DailyReading
from app.routers import compass, drift, albums, admin, weekly_albums, library, agent, misread, library_admin, analyzer
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


async def _daily_backup_loop():
    """Run a database backup once per day."""
    while True:
        run_backup()
        await asyncio.sleep(86400)  # 24 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan — starts daily backup task and analyzer session cleanup."""
    _cleanup_orphan_drafts()
    backup_task = asyncio.create_task(_daily_backup_loop())
    cleanup_task = asyncio.create_task(analyzer.session_cleanup_loop())
    logger.info("Daily backup scheduler started")
    logger.info("Analyzer session cleanup scheduler started")
    yield
    backup_task.cancel()
    cleanup_task.cancel()


app = FastAPI(title="The Rising Compass", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key", "Authorization"],
)

app.include_router(compass.router)
app.include_router(drift.router)
app.include_router(albums.router)
app.include_router(admin.router)
app.include_router(weekly_albums.router)
app.include_router(library.router)
app.include_router(agent.router)
app.include_router(misread.router)
app.include_router(library_admin.router)
app.include_router(analyzer.router)


# slowapi rate limiter (defined in analyzer.py, shared across routers)
app.state.limiter = analyzer.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all handler — log details server-side, return generic message to client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
def health():
    return {"status": "ok"}
