"""Lyrical Charger — public endpoints for user-submitted song analysis."""

import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
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
    LyricsCalibrateIn, LyricsCalibrateOut,
    SongSearchIn, SongSearchOut, SearchCalibrateIn,
)
from app.models import SubmittedSong
from app.constants import COLOR_LABELS
from app.services.analyzer_engine import run_analysis
from app.services.agents.calibrator import calibrate_song
from app.services import musixmatch
from app.services.artist_linker import try_link_song
from app.services.lc_events import schedule_event, write_event, extract_request_meta
from app.auth import verify_api_or_service_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzer", tags=["analyzer"])

limiter = Limiter(key_func=get_remote_address)


# ------------------------------------------------------------------
# Bot protection: honeypot field + optional Cloudflare Turnstile
# ------------------------------------------------------------------
async def _verify_turnstile(token: str, remote_ip: str | None) -> bool:
    """Verify a Turnstile token with Cloudflare. Only called when secret is set."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            data = {"secret": settings.turnstile_secret, "response": token}
            if remote_ip:
                data["remoteip"] = remote_ip
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=data,
            )
        if resp.status_code != 200:
            logger.warning("Turnstile verify HTTP %d", resp.status_code)
            return False
        return bool(resp.json().get("success"))
    except Exception:
        logger.exception("Turnstile verification failed")
        return False


def _log_error_event(event_type: str, request: Request, payload: dict | None = None,
                     submission_id: int | None = None) -> None:
    """Synchronous event write for HTTPException paths (FastAPI drops BackgroundTasks
    on HTTPException, so error events must be persisted inline)."""
    meta = extract_request_meta(request)
    write_event(event_type, meta["ip"], meta["user_agent"], meta["referrer"],
                payload=payload, submission_id=submission_id)


async def _check_bot_protection(
    hp_website: str,
    turnstile_token: str,
    request: Request,
) -> None:
    """Raise HTTPException if the request fails honeypot or Turnstile checks."""
    if hp_website.strip():
        # Honeypot tripped — silent generic 422 so bots don't learn the signal.
        _log_error_event("submission_honeypot", request,
                         payload={"hp_value_length": len(hp_website)})
        raise HTTPException(422, "Submission rejected.")

    if settings.turnstile_secret:
        if not turnstile_token:
            _log_error_event("submission_turnstile_failed", request,
                             payload={"reason": "missing_token"})
            raise HTTPException(422, "Bot verification required.")
        ok = await _verify_turnstile(turnstile_token, get_remote_address(request))
        if not ok:
            _log_error_event("submission_turnstile_failed", request,
                             payload={"reason": "verification_failed"})
            raise HTTPException(422, "Bot verification failed — please try again.")


@router.get("/config")
async def analyzer_config():
    """Public config for the LC frontend (which bot protection to render, etc.)."""
    return {
        "turnstile_site_key": settings.turnstile_site_key,
    }


@router.post("/page-view")
@limiter.limit("60/hour")
async def page_view(request: Request, background_tasks: BackgroundTasks):
    """Frontend beacon — logs that someone landed on the LC page."""
    payload = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            payload = {
                "path": str(body.get("path", ""))[:200],
                "title": str(body.get("title", ""))[:200],
            }
    except Exception:
        pass
    schedule_event(background_tasks, "page_view", request, payload=payload)
    return {"ok": True}

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
async def create_session(body: AnalyzerSessionCreate, request: Request, background_tasks: BackgroundTasks):
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

    schedule_event(background_tasks, "session_create", request,
                   payload={"session_id": session_id, "song_count": len(body.songs), "weighted": body.weighted})

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
            if not token:
                raise HTTPException(502, "Spotify authentication failed — check API credentials")
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
            logger.error("Spotify token request failed: HTTP %d", resp.status_code)
            return None

        data = resp.json()
        _spotify_token["access_token"] = data["access_token"]
        _spotify_token["expires_at"] = time.time() + data.get("expires_in", 3600)
        return _spotify_token["access_token"]
    except Exception:
        logger.exception("Failed to get Spotify token")
        return None


# ------------------------------------------------------------------
# 5. POST /api/analyzer/calibrate-lyrics — Direct lyrics calibration
# ------------------------------------------------------------------
def _validate_lyrics(text: str) -> str | None:
    """Return an error message if the lyrics look bogus, or None if they pass."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 4:
        return "Lyrics must have at least 4 lines for a meaningful calibration."

    # Mostly alphabetic (at least 60% letters after stripping whitespace)
    alpha_chars = sum(1 for c in text if c.isalpha())
    total_chars = sum(1 for c in text if not c.isspace())
    if total_chars > 0 and alpha_chars / total_chars < 0.6:
        return "That doesn't look like song lyrics. Paste the actual words of the song."

    # Not all caps (allows some caps, blocks full walls of caps)
    upper_alpha = sum(1 for c in text if c.isupper())
    if alpha_chars > 20 and upper_alpha / alpha_chars > 0.85:
        return "Please paste lyrics in normal case, not all caps."

    # Minimum unique words (catches repeated gibberish)
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(set(words)) < 10:
        return "These lyrics don't have enough variety for a meaningful calibration."

    return None


def _resolve_source(tier: str, requested: str | None) -> str:
    """Public callers always get 'lyrical_charger'. Service callers can self-tag
    (e.g. 'chadlewine'); falls back to 'service' if they don't supply one.
    Truncated to 30 chars to fit the column."""
    if tier == "public":
        return "lyrical_charger"
    return ((requested or "service").strip() or "service")[:30]


@router.post("/calibrate-lyrics", response_model=LyricsCalibrateOut)
@limiter.limit("5/hour")
async def calibrate_lyrics_endpoint(
    body: LyricsCalibrateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str = Depends(verify_api_or_service_key),
):
    """Calibrate raw lyrics text. Stores the calibration (not the lyrics).

    Public callers (RC_API_KEY, e.g. Lyrical Charger web tool) get bot
    protection + lc_events logging + forced source='lyrical_charger'.
    Service callers (RC_SERVICE_KEY, e.g. chadlewine.com, internal scripts)
    skip both and supply their own source tag.
    """
    is_public = tier == "public"
    if is_public:
        await _check_bot_protection(body.hp_website, body.turnstile_token, request)

    source = _resolve_source(tier, body.source)

    if not body.title or not body.title.strip():
        if is_public:
            _log_error_event("submission_failed_validation", request,
                             payload={"reason": "missing_title", "source": source})
        raise HTTPException(422, "Song title is required.")
    if not body.artist or not body.artist.strip():
        if is_public:
            _log_error_event("submission_failed_validation", request,
                             payload={"reason": "missing_artist", "source": source})
        raise HTTPException(422, "Artist is required.")

    lyrics_error = _validate_lyrics(body.lyrics)
    if lyrics_error:
        if is_public:
            _log_error_event("submission_failed_validation", request,
                             payload={"reason": lyrics_error, "source": source,
                                      "title": body.title[:100], "artist": body.artist[:100]})
        raise HTTPException(422, lyrics_error)

    title = body.title.strip()
    artist = body.artist.strip()

    db = SessionLocal()
    try:
        calibration = await asyncio.to_thread(
            calibrate_song, title, artist, body.lyrics, db
        )

        color = calibration.get("rubric_color")
        if color is None:
            if is_public:
                schedule_event(background_tasks, "submission_other_error", request,
                               payload={"reason": "calibrator_returned_no_color",
                                        "title": title, "artist": artist, "source": source})
            return LyricsCalibrateOut(status="error", title=title, artist=artist)

        submitted = SubmittedSong(
            title=title,
            artist=artist,
            rubric_color=color,
            charge_value=calibration.get("charge_value"),
            contaminated=calibration.get("contaminated", False),
            contamination_note=calibration.get("contamination_note"),
            charge_summary=calibration.get("charge_summary"),
            confidence=calibration.get("confidence"),
            source=source,
            ip_address=get_remote_address(request),
        )
        db.add(submitted)
        db.commit()
        db.refresh(submitted)
        try_link_song(title, artist, "submitted", submitted.id, db)

        if is_public:
            schedule_event(background_tasks, "submission_success", request,
                           payload={"title": title, "artist": artist, "source": source,
                                    "tier": color, "charge": calibration.get("charge_value"),
                                    "contaminated": calibration.get("contaminated", False),
                                    "confidence": calibration.get("confidence")},
                           submission_id=submitted.id)

        return LyricsCalibrateOut(
            status="scored",
            tier=color,
            tier_label=COLOR_LABELS.get(color),
            charge=calibration.get("charge_value"),
            contaminated=calibration.get("contaminated", False),
            contamination_note=calibration.get("contamination_note"),
            charge_summary=calibration.get("charge_summary"),
            confidence=calibration.get("confidence", 0.0),
            title=title,
            artist=artist,
        )
    except Exception:
        logger.exception("Calibration failed for submitted lyrics")
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "calibrator_exception", "title": title,
                                      "artist": artist, "source": source})
        raise HTTPException(500, "Calibration failed — try again")
    finally:
        db.close()


# ------------------------------------------------------------------
# 6. POST /api/analyzer/search-songs — Musixmatch song search
# ------------------------------------------------------------------
@router.post("/search-songs", response_model=SongSearchOut)
@limiter.limit("20/hour")
async def search_songs(body: SongSearchIn, request: Request, background_tasks: BackgroundTasks):
    """Search for songs via Musixmatch. Returns empty list if API key not configured."""
    if not musixmatch.is_configured():
        schedule_event(background_tasks, "search_query", request,
                       payload={"query": body.query[:200], "artist": (body.artist or "")[:200],
                                "result_count": 0, "configured": False})
        return SongSearchOut(results=[], message="Song search is not yet available. Paste lyrics directly for now.")

    results = await musixmatch.search_tracks(body.query, body.artist)
    schedule_event(background_tasks, "search_query", request,
                   payload={"query": body.query[:200], "artist": (body.artist or "")[:200],
                            "result_count": len(results), "configured": True})
    if not results:
        return SongSearchOut(results=[], message="No songs found. Try a different search.")

    return SongSearchOut(results=results)


# ------------------------------------------------------------------
# 7. POST /api/analyzer/calibrate-search — Calibrate a song found via search
# ------------------------------------------------------------------
@router.post("/calibrate-search", response_model=LyricsCalibrateOut)
@limiter.limit("5/hour")
async def calibrate_search(
    body: SearchCalibrateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str = Depends(verify_api_or_service_key),
):
    """Fetch lyrics for a Musixmatch track and calibrate them."""
    is_public = tier == "public"
    if is_public:
        await _check_bot_protection(body.hp_website, body.turnstile_token, request)

    source = _resolve_source(tier, body.source)

    if not musixmatch.is_configured():
        raise HTTPException(501, "Song search is not yet available.")

    lyrics = await musixmatch.get_lyrics(body.track_id)
    if not lyrics:
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "lyrics_unavailable", "track_id": body.track_id, "source": source})
        raise HTTPException(404, "Lyrics not available for this track.")

    lyrics_error = _validate_lyrics(lyrics)
    if lyrics_error:
        if is_public:
            _log_error_event("submission_failed_validation", request,
                             payload={"reason": lyrics_error, "track_id": body.track_id, "source": source})
        raise HTTPException(422, f"Retrieved lyrics are too short or invalid: {lyrics_error}")

    title = body.title.strip()
    artist = body.artist.strip()

    db = SessionLocal()
    try:
        calibration = await asyncio.to_thread(
            calibrate_song, title, artist, lyrics, db
        )

        color = calibration.get("rubric_color")
        if color is None:
            if is_public:
                schedule_event(background_tasks, "submission_other_error", request,
                               payload={"reason": "calibrator_returned_no_color",
                                        "title": title, "artist": artist, "source": source})
            return LyricsCalibrateOut(status="error", title=title, artist=artist)

        submitted = SubmittedSong(
            title=title,
            artist=artist,
            rubric_color=color,
            charge_value=calibration.get("charge_value"),
            contaminated=calibration.get("contaminated", False),
            contamination_note=calibration.get("contamination_note"),
            charge_summary=calibration.get("charge_summary"),
            confidence=calibration.get("confidence"),
            source=source,
            ip_address=get_remote_address(request),
        )
        db.add(submitted)
        db.commit()
        db.refresh(submitted)
        try_link_song(title, artist, "submitted", submitted.id, db)

        if is_public:
            schedule_event(background_tasks, "submission_success", request,
                           payload={"title": title, "artist": artist, "source": source,
                                    "tier": color, "charge": calibration.get("charge_value"),
                                    "contaminated": calibration.get("contaminated", False),
                                    "confidence": calibration.get("confidence")},
                           submission_id=submitted.id)

        return LyricsCalibrateOut(
            status="scored",
            tier=color,
            tier_label=COLOR_LABELS.get(color),
            charge=calibration.get("charge_value"),
            contaminated=calibration.get("contaminated", False),
            contamination_note=calibration.get("contamination_note"),
            charge_summary=calibration.get("charge_summary"),
            confidence=calibration.get("confidence", 0.0),
            title=title,
            artist=artist,
        )
    except Exception:
        logger.exception("Calibration failed for search track %d", body.track_id)
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "calibrator_exception", "title": title,
                                      "artist": artist, "source": source})
        raise HTTPException(500, "Calibration failed — try again")
    finally:
        db.close()
