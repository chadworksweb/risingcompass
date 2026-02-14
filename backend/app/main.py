from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.routers import compass, drift, albums, admin, weekly_albums, library, agent

# Create tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="The Rising Compass", version="1.0.0")

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


@app.get("/api/health")
def health():
    return {"status": "ok"}
