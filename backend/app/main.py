import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import inspect, text

from app.config import settings
from app.database import engine, Base, SessionLocal
from app.routers import compass, drift, albums, admin, weekly_albums, library, agent, misread, library_admin
from app.services.backup import run_backup

logger = logging.getLogger(__name__)

# Create tables on startup
Base.metadata.create_all(bind=engine)


def _run_migrations():
    """Run lightweight migrations for schema changes that create_all won't handle."""
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("agent_drafts")}

    if "label" not in columns:
        logger.info("Migration: adding 'label' column to agent_drafts")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE agent_drafts ADD COLUMN label TEXT"))

        # Backfill labels for existing drafts
        db = SessionLocal()
        try:
            rows = db.execute(
                text("SELECT id, date FROM agent_drafts ORDER BY date, id")
            ).fetchall()
            seen: dict[str, int] = {}
            for row_id, row_date in rows:
                date_str = str(row_date)
                count = seen.get(date_str, 0)
                if count == 0:
                    label = f"compass_song_{date_str}_draft"
                else:
                    modifier = chr(ord("a") + count)
                    label = f"compass_song_{date_str}{modifier}_draft"
                seen[date_str] = count + 1
                db.execute(
                    text("UPDATE agent_drafts SET label = :label WHERE id = :id"),
                    {"label": label, "id": row_id},
                )
            db.commit()
            logger.info("Migration: backfilled %d draft labels", len(rows))
        finally:
            db.close()


_run_migrations()


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
