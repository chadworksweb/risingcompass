import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.migrate import run_migrations
from app.routers import compass, drift, albums, admin, weekly_albums, library, agent, misread, library_admin
from app.services.backup import run_backup

logger = logging.getLogger(__name__)

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
    """App lifespan — starts daily backup task."""
    task = asyncio.create_task(_daily_backup_loop())
    logger.info("Daily backup scheduler started")
    yield
    task.cancel()


app = FastAPI(title="The Rising Compass", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/api/health")
def health():
    return {"status": "ok"}
