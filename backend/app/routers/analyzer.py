"""Music Frequency Analyzer — public endpoints for user-submitted song analysis."""

import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.database import SessionLocal
from app.schemas import (
    AnalyzerSessionCreate, AnalyzerSessionOut,
    AnalyzerSessionStatus, AnalyzerSongResult, AnalyzerAggregate,
    PlaylistResolveIn, PlaylistResolveOut, PlaylistTrackOut,
)
from app.services.analyzer_engine import run_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzer", tags=["analyzer"])

limiter = Limiter(key_func=get_remote_address)

# --- In-memory session storage ---
_sessions: dict[str, dict] = {}

# --- Spotify token cache ---
_spotify_token: dict = {"access_token": None, "expires_at": 0}


def _cleanup_expired_sessions():
    """Remove expired sessions from memory."""
    now = datetime.now(timezone.utc)
    expired = [sid for sid, s in _sessions.items() if s["expires_at"] < now]
    for sid in expired:
        del _sessions[sid]
    if expired:
        logger.info("Cleaned up %d expired analyzer sessions", len(expired))


async def session_cleanup_loop():
    """Background task that cleans up expired sessions every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        _cleanup_expired_sessions()


# ------------------------------------------------------------------
# 1. POST /api/analyzer/sessions — Create analysis session
# ------------------------------------------------------------------
@router.post("/sessions", status_code=201, response_model=AnalyzerSessionOut)
@limiter.limit("10/hour")
async def create_session(body: AnalyzerSessionCreate, request: Request):
    session_id = secrets.token_hex(5)  # 10-char hex string
    now = datetime.now(timezone.utc)
    ttl = settings.analyzer_session_ttl
    expires_at = now + timedelta(seconds=ttl)

    _sessions[session_id] = {
        "session_id": session_id,
        "songs_input": [{"title": s.title, "artist": s.artist} for s in body.songs],
        "weighted": body.weighted,
        "status": "pending",
        "results": [],
        "aggregate": None,
        "narrative": None,
        "created_at": now,
        "expires_at": expires_at,
        "streaming": False,
    }

    return AnalyzerSessionOut(
        session_id=session_id,
        song_count=len(body.songs),
        stream_url=f"/api/analyzer/sessions/{session_id}/stream",
        expires_at=expires_at,
    )


# ------------------------------------------------------------------
# 2. GET /api/analyzer/sessions/{session_id}/stream — SSE stream
# ------------------------------------------------------------------
@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    if session["status"] == "completed":
        raise HTTPException(410, "Session already completed — use GET /sessions/{session_id}")

    if session["streaming"]:
        raise HTTPException(409, "Session already streaming — use GET /sessions/{session_id}")

    session["streaming"] = True
    session["status"] = "processing"

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_event(event_type: str, data: dict):
            await queue.put((event_type, data))

        # Run analysis in background, feeding events to the queue
        db = SessionLocal()
        try:
            analysis_task = asyncio.create_task(
                _run_analysis_with_storage(session, db, on_event)
            )

            while True:
                # Check client disconnect
                if await request.is_disconnected():
                    logger.info("Client disconnected from session %s", session_id)
                    break

                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"comment": "keepalive"}
                    continue

                yield {"event": event_type, "data": json.dumps(data)}

                # Store result in session for reconnection
                if event_type == "song_result":
                    session["results"].append(data)

                if event_type in ("complete", "error"):
                    break

            # Ensure task completes even if client disconnects
            if not analysis_task.done():
                await analysis_task
        except Exception:
            logger.exception("Error in SSE stream for session %s", session_id)
            session["status"] = "error"
            yield {"event": "error", "data": json.dumps({"message": "Internal server error"})}
        finally:
            db.close()
            session["streaming"] = False

    return EventSourceResponse(event_generator())


async def _run_analysis_with_storage(session: dict, db, on_event):
    """Wrapper that catches exceptions and emits error events."""
    try:
        await run_analysis(session, db, on_event)
    except Exception as e:
        logger.exception("Analysis failed for session %s", session["session_id"])
        session["status"] = "error"
        await on_event("error", {"message": "Analysis failed"})


# ------------------------------------------------------------------
# 3. GET /api/analyzer/sessions/{session_id} — Status / reconnect
# ------------------------------------------------------------------
@router.get("/sessions/{session_id}", response_model=AnalyzerSessionStatus)
@limiter.limit("60/hour")
async def get_session(session_id: str, request: Request):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    return AnalyzerSessionStatus(
        session_id=session_id,
        status=session["status"],
        total_songs=len(session["songs_input"]),
        completed_songs=len(session["results"]),
        songs=[AnalyzerSongResult(**r) for r in session["results"]],
        aggregate=AnalyzerAggregate(**session["aggregate"]) if session["aggregate"] else None,
        narrative=session["narrative"],
    )


# ------------------------------------------------------------------
# 4. POST /api/analyzer/resolve-playlist — Spotify playlist resolution
# ------------------------------------------------------------------
@router.post("/resolve-playlist", response_model=PlaylistResolveOut)
@limiter.limit("20/hour")
async def resolve_playlist(body: PlaylistResolveIn, request: Request):
    playlist_id = _extract_playlist_id(body.spotify_url)
    if not playlist_id:
        raise HTTPException(400, "Invalid Spotify playlist URL, URI, or ID")

    token = await _get_spotify_token()
    if not token:
        raise HTTPException(502, "Failed to authenticate with Spotify API")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.spotify.com/v1/playlists/{playlist_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "name,owner(display_name),tracks.items(track(name,artists(name))),tracks.total"},
                timeout=15.0,
            )

        if resp.status_code == 404:
            raise HTTPException(404, "Playlist not found or is private")
        if resp.status_code == 401:
            # Token expired, clear cache and retry once
            _spotify_token["access_token"] = None
            _spotify_token["expires_at"] = 0
            token = await _get_spotify_token()
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.spotify.com/v1/playlists/{playlist_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "name,owner(display_name),tracks.items(track(name,artists(name))),tracks.total"},
                    timeout=15.0,
                )
        if resp.status_code != 200:
            raise HTTPException(502, f"Spotify API error (HTTP {resp.status_code})")

        data = resp.json()
        tracks = []
        for item in data.get("tracks", {}).get("items", [])[:50]:
            track = item.get("track")
            if not track or not track.get("name"):
                continue
            artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
            tracks.append(PlaylistTrackOut(title=track["name"], artist=artists))

        return PlaylistResolveOut(
            playlist_name=data.get("name", "Unknown"),
            playlist_owner=data.get("owner", {}).get("display_name", "Unknown"),
            track_count=len(tracks),
            tracks=tracks,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Spotify API request failed")
        raise HTTPException(502, "Spotify API error")


def _extract_playlist_id(url_or_id: str) -> str | None:
    """Extract Spotify playlist ID from URL, URI, or bare ID."""
    url_or_id = url_or_id.strip()

    # Full URL: https://open.spotify.com/playlist/{id}?...
    m = re.match(r"https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)", url_or_id)
    if m:
        return m.group(1)

    # URI: spotify:playlist:{id}
    m = re.match(r"spotify:playlist:([a-zA-Z0-9]+)", url_or_id)
    if m:
        return m.group(1)

    # Bare ID (alphanumeric, typically 22 chars)
    if re.match(r"^[a-zA-Z0-9]{15,}$", url_or_id):
        return url_or_id

    return None


async def _get_spotify_token() -> str | None:
    """Get a Spotify access token using client credentials flow. Cached in memory."""
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        logger.error("Spotify client credentials not configured")
        return None

    # Return cached token if still valid (with 60s buffer)
    if _spotify_token["access_token"] and time.time() < _spotify_token["expires_at"] - 60:
        return _spotify_token["access_token"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
                timeout=10.0,
            )
        if resp.status_code != 200:
            logger.error("Spotify token request failed: %d %s", resp.status_code, resp.text)
            return None

        data = resp.json()
        _spotify_token["access_token"] = data["access_token"]
        _spotify_token["expires_at"] = time.time() + data.get("expires_in", 3600)
        return _spotify_token["access_token"]
    except Exception:
        logger.exception("Failed to get Spotify token")
        return None
