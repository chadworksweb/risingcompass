"""Lyrical Charger — public endpoints for user-submitted song analysis."""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.database import SessionLocal
from app.schemas import (
    LyricsCalibrateIn, LyricsCalibrateOut,
    SongSearchIn, SongSearchOut, SearchCalibrateIn,
    LCAvailabilityOut, LCSubscribeIn, LCSubscribeOut,
    CalibrateJobOut, CalibrateStatusOut,
)
from app.models import LyricalChargerSubscriber, UserCalibration, User, CalibrateJob
from app.services.feature_flags import (
    is_lyrical_charger_disabled, lyrical_charger_disabled_message,
    lyrical_charger_anon_daily_limit, lyrical_charger_user_daily_limit,
    is_album_charger_disabled, album_charger_disabled_message,
)
from app.constants import COLOR_LABELS
from app.services.agents.calibrator import (
    calibrate_song_async, lookup_calibrated, ensure_full_calibration,
)
from app.services import musixmatch
from app.services.calibration_corpus import (
    record_and_reconcile, hash_lyrics, find_canonical_song,
    fetch_run_fingerprints, live_run_count, PUBLIC_RUN_CAP,
)
from app.services.lyrics_fingerprint import (
    compute_fingerprint, max_jaccard, DIVERGENCE_THRESHOLD,
)
from app.services.identity_guard import check_lyrics_identity
from app.services.clutter import record_clutter_finding
from app.services.lc_events import schedule_event, write_event, extract_request_meta
from app.routers.songs import _get_or_create_slug
from app.auth import verify_api_or_service_key, optional_clerk_user
from app import billing_config
from app.services import billing as billing_svc
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzer", tags=["analyzer"])

# The anon/user daily caps are admin-tunable (LC admin -> Daily rate limits),
# stored in system_flags. Reading them on every calibrate request would mean a
# DB hit inside the limit provider, so cache them in-process for a short TTL.
# An admin change propagates within _LC_LIMIT_TTL seconds.
_LC_LIMIT_CACHE: dict = {"anon": None, "user": None, "ts": 0.0}
_LC_LIMIT_TTL = 30.0


def _lc_daily_limits() -> tuple[int, int]:
    """(anon_per_ip, user_per_user) daily limits. Cached, fail-soft to defaults."""
    now = time.monotonic()
    if _LC_LIMIT_CACHE["anon"] is None or now - _LC_LIMIT_CACHE["ts"] > _LC_LIMIT_TTL:
        try:
            db = SessionLocal()
            try:
                anon = lyrical_charger_anon_daily_limit(db)
                user = lyrical_charger_user_daily_limit(db)
            finally:
                db.close()
            _LC_LIMIT_CACHE.update(anon=anon, user=user, ts=now)
        except Exception:
            logger.exception("Failed to read LC daily limits; using code defaults")
            return (billing_config.ANON_CHARGER_DAILY_LIMIT, 100)
    return (_LC_LIMIT_CACHE["anon"], _LC_LIMIT_CACHE["user"])


# Admin-comped (unlimited Charger) user IDs. Cached in-process so the limit
# provider doesn't hit the DB on every calibrate request; a comp toggle
# propagates within _COMP_TTL seconds (same pattern as _LC_LIMIT_CACHE).
_COMP_CACHE: dict = {"ids": frozenset(), "ts": 0.0}
_COMP_TTL = 30.0


def _comp_unlimited_ids() -> frozenset:
    """Set of user IDs carrying the unlimited-Charger comp. Cached, fail-soft
    to the last-known set (empty on first failure)."""
    now = time.monotonic()
    if now - _COMP_CACHE["ts"] > _COMP_TTL:
        try:
            db = SessionLocal()
            try:
                rows = db.query(User.id).filter(User.comp_unlimited.is_(True)).all()
            finally:
                db.close()
            _COMP_CACHE.update(ids=frozenset(r[0] for r in rows), ts=now)
        except Exception:
            logger.exception("Failed to read comp_unlimited ids; using cached set")
    return _COMP_CACHE["ids"]


def _calibrate_daily_limit(key: str) -> str:
    """Dynamic per-request daily limit for the calibrate-* endpoints (M5).

    slowapi only passes a value to a limit-provider callable whose parameter
    is named exactly `key` (LimitGroup.__iter__ in slowapi/wrappers.py); it
    then calls provider(key_function(request)), i.e. with the rate-limit KEY
    -- 'user:{id}' for signed-in callers, else the client IP (see
    _limiter_key). A provider with any other parameter name is invoked with
    NO arguments, which is why the previous `(request)` signature raised
    TypeError and 500'd every real calibrate request.

    Signed-in users get a generous per-user daily backstop -- the real gate
    for them is credits (check_credits / charge_credits inside the handler).
    Anonymous callers get the per-IP cost firewall. Both caps are tunable
    from the LC admin section; see _lc_daily_limits.
    """
    anon, user = _lc_daily_limits()
    # Service / first-party callers (RC_SERVICE_KEY) are trusted -- they already
    # skip bot protection -- so they are not gated by the anon per-IP cap.
    if key == "service":
        return "1000000/day"
    if isinstance(key, str) and key.startswith("user:"):
        # Admin-comped users get an effectively unlimited daily cap -- the
        # comp lifts the per-user backstop, not just the credit gate.
        try:
            uid = int(key.split(":", 1)[1])
            if uid in _comp_unlimited_ids():
                return "1000000/day"
        except (ValueError, IndexError):
            pass
        return f"{user}/day"
    return f"{anon}/day"


def _limiter_key(request: Request) -> str:
    """Identity-aware rate limit key (M5).

    Signed-in users get a per-user bucket (`user:{id}`) so two people
    sharing an IP (NAT, family wifi) don't share a daily allowance, and
    so a paying user moving between networks keeps their bucket. Anon
    requests fall back to IP -- the per-IP daily allowance is the cost
    firewall + funnel for unsigned-in traffic.

    request.state.user_id is set by optional_clerk_user / require_clerk_user
    if the Clerk JWT was valid -- the dependency runs before the limiter
    on the same request.
    """
    uid = getattr(request.state, "user_id", None)
    if uid:
        return f"user:{uid}"
    # Service/first-party callers (RC_SERVICE_KEY: chadlewine, internal scripts,
    # the local model seam) get their own bucket, lifted to an effectively
    # unlimited daily cap in _calibrate_daily_limit -- they are trusted and not
    # the anon-abuse vector the per-IP cap targets.
    if getattr(request.state, "client_behavior", None) == "service":
        return "service"
    return get_remote_address(request)


limiter = Limiter(key_func=_limiter_key)


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
                     song_id: int | None = None) -> None:
    """Synchronous event write for HTTPException paths (FastAPI drops BackgroundTasks
    on HTTPException, so error events must be persisted inline)."""
    meta = extract_request_meta(request)
    write_event(event_type, meta["ip"], meta["user_agent"], meta["referrer"],
                payload=payload, song_id=song_id)


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
    """Public config for the LC frontend (which bot protection to render, which
    Musixmatch-gated search surfaces to enable, whether the Album Charger tab is
    open, etc.)."""
    search_enabled = musixmatch.is_configured()
    db = SessionLocal()
    try:
        album_enabled = not is_album_charger_disabled(db)
    finally:
        db.close()
    return {
        "turnstile_site_key": settings.turnstile_site_key,
        # Musixmatch-gated surfaces. Both the song "Search by Song" tab and the
        # album "Search Album" sub-tab ship dark until the key is configured.
        "song_search_enabled": search_enabled,
        "album_search_enabled": search_enabled,
        # Album Charger kill switch (its own flag; fails closed). When false the
        # frontend hides the whole Album Charger top-tab.
        "album_charger_enabled": album_enabled,
    }


@router.get("/availability", response_model=LCAvailabilityOut)
async def analyzer_availability():
    """Public availability gate for the LC frontend.

    Returns `{available, message}`. When `available=false`, the frontend
    swaps in the unavailable screen with a subscribe form. When `true`,
    `message` is null and the regular UI renders.
    """
    db = SessionLocal()
    try:
        if is_lyrical_charger_disabled(db):
            return LCAvailabilityOut(
                available=False,
                message=lyrical_charger_disabled_message(db),
            )
        return LCAvailabilityOut(available=True, message=None)
    finally:
        db.close()


def _check_lc_available_or_503() -> None:
    """Raise 503 with a structured detail when LC is flagged as disabled."""
    db = SessionLocal()
    try:
        if is_lyrical_charger_disabled(db):
            raise HTTPException(
                status_code=503,
                detail={
                    "available": False,
                    "message": lyrical_charger_disabled_message(db),
                },
            )
    finally:
        db.close()


def _check_album_available_or_503() -> None:
    """Raise 503 when the Album Charger is closed (its own kill switch,
    independent of the whole-LC gate above). Single-song stays open."""
    db = SessionLocal()
    try:
        if is_album_charger_disabled(db):
            raise HTTPException(
                status_code=503,
                detail={
                    "available": False,
                    "message": album_charger_disabled_message(db),
                },
            )
    finally:
        db.close()


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@router.post("/subscribe", response_model=LCSubscribeOut)
@limiter.limit("10/hour")
async def lc_subscribe(
    body: LCSubscribeIn,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Capture an email so we can notify the user when LC comes back online.

    Always available (does NOT 503 when LC is disabled — that's the whole
    point of this endpoint). Honeypot + Turnstile still apply.
    """
    await _check_bot_protection(body.hp_website, body.turnstile_token, request)

    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(422, "Please enter a valid email address.")

    db = SessionLocal()
    try:
        existing = (
            db.query(LyricalChargerSubscriber)
            .filter(LyricalChargerSubscriber.email == email)
            .first()
        )
        if existing:
            schedule_event(background_tasks, "lc_subscribe_duplicate", request,
                           payload={"email_hash_prefix": email[:2]})
            return LCSubscribeOut(
                status="already_subscribed",
                message="You're already on the list. We'll email you when Lyrical Charger is back online.",
            )

        sub = LyricalChargerSubscriber(email=email)
        db.add(sub)
        db.commit()
        schedule_event(background_tasks, "lc_subscribe_new", request,
                       payload={"email_hash_prefix": email[:2]})
        return LCSubscribeOut(
            status="subscribed",
            message="Thanks. We'll email you when Lyrical Charger is back online.",
        )
    finally:
        db.close()


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
        logger.debug("analyzer: swallowed in page_view", exc_info=True)
    schedule_event(background_tasks, "page_view", request, payload=payload)
    return {"ok": True}

# ------------------------------------------------------------------
# Dead-on-arrival sessions/SSE/playlist-resolve design (LC v1, abandoned
# 2026-03-26 when LC v2 shipped) was removed 2026-05-12. Frontend uses
# the single-song /calibrate-lyrics + /search-songs + /calibrate-search
# endpoints below instead. The original 600-line spec
# (backend/docs/lyrical-charger-api-spec.md) was deleted with the code.
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# POST /api/analyzer/calibrate-lyrics — Direct lyrics calibration
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


def detect_prose_like(text: str) -> str | None:
    """Return a human-readable reason if the text reads as prose (essay, blog
    post, manifesto) rather than song lyrics, or None if it looks song-shaped.

    Heuristics — ANY one firing flags it:
      1. Any single line > 300 characters (prose paragraph).
      2. Average non-empty line length > 100 characters.
      3. No repeated lines AND word_count > 250 AND avg line length > 60.
      4. Line count / word count ratio < 0.05 (fewer than ~1 break per 20 words).

    These are suggestions, not rejections — the caller decides whether to
    warn-and-confirm or hard-block. LC warns and lets the user override;
    backend hard-blocks unless `confirm_not_lyrics=True` is set.
    """
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None
    word_count = len(re.findall(r"\S+", text))
    if word_count < 40:
        return None  # too short to meaningfully judge

    longest = max((len(l) for l in lines), default=0)
    if longest > 300:
        return "One or more lines are very long (over 300 characters). Song lyrics are usually broken into short lines."

    avg_line_len = sum(len(l) for l in lines) / len(lines)
    if avg_line_len > 100:
        return "The average line is very long. Song lyrics usually break every few words, not in paragraph-like chunks."

    line_word_ratio = len(lines) / max(word_count, 1)
    if line_word_ratio < 0.05:
        return "Line breaks are sparse — this reads more like prose than lyrics."

    # No repetition check — lyrics almost always repeat choruses/hooks
    lowered = [l.lower().strip() for l in lines]
    unique = set(lowered)
    duplicates = len(lowered) - len(unique)
    if duplicates == 0 and word_count > 250 and avg_line_len > 60:
        return "Nothing repeats here. Song lyrics typically have a refrain or repeated lines. This reads like prose."

    return None


def detect_noncommercial_signals(text: str, title: str, artist: str) -> str | None:
    """Cheap heuristic pre-filter for the LEIT clutter warning: return a short
    reason if the paste shows obvious signs of NOT being a commercially released
    song, or None. This is only a signal -- the Opus commercial verdict (folded
    into the identity guard) is authoritative; this is logged alongside it so the
    heuristic can be tuned against real data, and it never blocks on its own.

    Conservative on purpose (we only want strong signals here):
      1. Tiny vocabulary (< 15 unique words) -- word-salad / one-line repeats.
      2. A placeholder/non-artist name ("me", "test", "anonymous", "n/a", ...).
    """
    words = re.findall(r"[a-zA-Z']+", (text or "").lower())
    if words and len(set(words)) < 15:
        return "Very small vocabulary -- reads more like a fragment than a full released song."

    a = (artist or "").strip().lower()
    PLACEHOLDER_ARTISTS = {
        "me", "myself", "test", "testing", "anonymous", "anon", "n/a", "na",
        "none", "unknown", "self", "nobody", "asdf", "xxx", "tbd",
    }
    if a in PLACEHOLDER_ARTISTS:
        return f"\"{artist}\" doesn't look like a real recording artist."

    return None


def _resolve_source(tier: str, requested: str | None) -> str:
    """Public callers always get 'lyrical_charger'. Service callers can self-tag
    (e.g. 'chadlewine'); falls back to 'service' if they don't supply one.
    Truncated to 30 chars to fit the column."""
    if tier == "public":
        return "lyrical_charger"
    return ((requested or "service").strip() or "service")[:30]


def _run_capped_response(db, *, source: str, song_id: int, title: str,
                         artist: str, run_count: int) -> "LyricsCalibrateOut":
    """Build the public 'run_capped' response for a song that has hit
    PUBLIC_RUN_CAP. Resolves the existing slug (read-only -- the song has many
    prior runs, so a slug already exists) so the UI can link to its page."""
    slug = None
    try:
        slug = _get_or_create_slug(title, artist, source, song_id, db)
    except Exception:
        logger.exception("capped-slug resolution failed for %s/%s", source, song_id)
    return LyricsCalibrateOut(
        status="run_capped",
        title=title, artist=artist,
        run_count=run_count, run_cap=PUBLIC_RUN_CAP,
        song_slug=slug,
        block_reason=(
            f"This song has been calibrated {run_count} times and reached its "
            f"{PUBLIC_RUN_CAP}-run public limit. Its reading is settled -- open "
            "the song page to see the current calibration."
        ),
    )


def _song_persist_fields(calibration: dict) -> dict:
    """Map a complete calibration object to the SubmittedSong columns that
    carry the calibration path's full output -- rubric + prose + ether tags.
    topics / topic_audit are JSON-encoded to match the Text columns."""
    topics = calibration.get("topics")
    topic_audit = calibration.get("topic_audit")
    return {
        "rubric_color": calibration.get("rubric_color"),
        "charge_value": calibration.get("charge_value"),
        "contaminated": calibration.get("contaminated", False),
        "contamination_note": calibration.get("contamination_note"),
        "dogma_referenced": bool(calibration.get("dogma_referenced", False)),
        "dogma_note": calibration.get("dogma_note"),
        "charge_summary": calibration.get("charge_summary"),
        "confidence": calibration.get("confidence"),
        "listener_effects_prose": calibration.get("listener_effects_prose"),
        "societal_effects_prose": calibration.get("societal_effects_prose"),
        # Sealed generation provenance, kept in lockstep with the prose column.
        "societal_prose_generated_at": calibration.get("societal_prose_generated_at"),
        "societal_prose_model": calibration.get("societal_prose_model"),
        "deadpan_line": calibration.get("deadpan_line"),
        "topics": json.dumps(topics) if topics else None,
        "topic_audit": json.dumps(topic_audit) if topic_audit else None,
    }


def _record_user_calibration(
    db, *, user_id: int, song_id: int,
    song_slug: str | None, title: str, artist: str, calibration: dict,
) -> None:
    """Upsert the "songs you've calibrated" row for a signed-in LC user.
    `song_id` is the atomic songs.id (unified renovation 5c-2).

    One row per (user, canonical song); re-running refreshes the snapshot +
    calibrated_at. Fails soft -- a logging tool reading shouldn't 500 because
    the activity write hit a snag. The caller commits.
    """
    try:
        existing = (
            db.query(UserCalibration)
            .filter(
                UserCalibration.user_id == user_id,
                UserCalibration.song_id == song_id,
            )
            .first()
        )
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        if existing:
            existing.song_slug = song_slug or existing.song_slug
            existing.title = title or existing.title
            existing.artist = artist or existing.artist
            existing.rubric_color = calibration.get("rubric_color")
            existing.charge_value = calibration.get("charge_value")
            existing.charge_summary = calibration.get("charge_summary")
            existing.calibrated_at = now
        else:
            db.add(UserCalibration(
                user_id=user_id,
                song_id=song_id,
                song_slug=song_slug,
                title=title,
                artist=artist,
                rubric_color=calibration.get("rubric_color"),
                charge_value=calibration.get("charge_value"),
                charge_summary=calibration.get("charge_summary"),
                calibrated_at=now,
                created_at=now,
            ))
    except Exception:
        logger.exception("user_calibration upsert failed for user %s / %s by %s",
                         user_id, title, artist)


@router.post("/calibrate-lyrics", response_model=LyricsCalibrateOut)
@limiter.limit(_calibrate_daily_limit)
async def calibrate_lyrics_endpoint(
    body: LyricsCalibrateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str = Depends(verify_api_or_service_key),
    current_user: User | None = Depends(optional_clerk_user),
):
    """Synchronous single-song calibrate. Used by service/API callers
    (chadlewine.com, internal scripts) and as a fallback. The public web tool
    uses the async job path (POST /calibrate-lyrics/start) so it can drive a
    real progress bar; both share _calibrate_lyrics_impl below."""
    return await _calibrate_lyrics_impl(
        body=body, request=request, background_tasks=background_tasks,
        tier=tier, current_user=current_user,
    )


async def _calibrate_lyrics_impl(
    *,
    body: LyricsCalibrateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str,
    current_user: User | None,
    progress_cb: Callable[[str], None] | None = None,
    skip_bot_check: bool = False,
):
    """Calibrate raw lyrics text. Stores the calibration (not the lyrics).

    Public callers (RC_API_KEY, e.g. Lyrical Charger web tool) get bot
    protection + lc_events logging + forced source='lyrical_charger'.
    Service callers (RC_SERVICE_KEY, e.g. chadlewine.com, internal scripts)
    skip both and supply their own source tag.
    """
    is_public = tier == "public"

    # Capture the song identity into the call log before anything else can fail.
    request.state.call_context = {
        "title": (body.title or "")[:200],
        "artist": (body.artist or "")[:200],
        "source": (body.source or "")[:50],
    }

    if is_public:
        _check_lc_available_or_503()
        # skip_bot_check: the async job path (/calibrate-lyrics/start) already
        # ran bot protection synchronously before launching the worker, and the
        # Turnstile token is single-use, so the worker must NOT re-verify it.
        if not skip_bot_check:
            await _check_bot_protection(body.hp_website, body.turnstile_token, request)

    source = _resolve_source(tier, body.source)

    # Local "Claude Code is the model" seam. A supplied_calibration is honored
    # ONLY on a local backend with the flag on, and NEVER for public callers.
    # Fail-closed: the environment guard means it can't activate in prod even if
    # the flag were somehow set there.
    use_supplied = False
    if body.supplied_calibration is not None:
        if is_public or settings.environment == "prod" or not settings.local_model_supply_enabled:
            raise HTTPException(403, "supplied_calibration is not permitted here")
        if not isinstance(body.supplied_calibration, dict) or not body.supplied_calibration.get("rubric_color"):
            raise HTTPException(422, "supplied_calibration must be an object with at least rubric_color")
        use_supplied = True

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
            # Public callers get a structured detail with an appeal route to the
            # general-inquiry form (topic lyrics_rejected). Service/API callers
            # keep the plain-string detail below for programmatic handling.
            raise HTTPException(422, {
                "error": "lyrics_rejected",
                "reason": lyrics_error,
                "appeal_url": "/inquiry.html?topic=lyrics_rejected&source=lyrical_charger",
                "message": (
                    f"{lyrics_error} If these are accurate and complete lyrics, "
                    "appeal at /inquiry.html?topic=lyrics_rejected"
                ),
            })
        raise HTTPException(422, lyrics_error)

    # Prose detection — observe only, do NOT block. We log flagged submissions
    # so the heuristic can be tuned against real data before a future hard
    # block. Position A (compass = lyrics) is the eventual destination, but
    # we need to see what edge cases exist first.
    prose_reason = detect_prose_like(body.lyrics)
    if prose_reason and is_public:
        schedule_event(background_tasks, "submission_prose_flagged", request,
                       payload={"reason": prose_reason, "source": source,
                                "title": body.title[:100], "artist": body.artist[:100]})

    title = body.title.strip()
    artist = body.artist.strip()
    fingerprint = compute_fingerprint(body.lyrics)

    # Hoisted so the exception handler can tell a SAVED run (song committed ->
    # salvage, never refund) from a pre-save failure (refund any charge made).
    submitted_id = None
    song_slug = None
    charge_result = None
    charge_ref_id = None

    try:
        # Phase 1: read-only session. Gather everything needed before the
        # Opus calls, then close it, so no pooled connection sits idle in a
        # transaction through the 20-30s Opus call.
        read_db = SessionLocal()
        try:
            pre_canonical = find_canonical_song(title, artist, read_db)
            pre_canonical_info = (
                (pre_canonical[0], pre_canonical[1].id) if pre_canonical else None
            )

            # Public run cap: once a song has PUBLIC_RUN_CAP live runs, its
            # reading is settled and public callers can no longer run it (admin/
            # terminal only). Counted before the Opus call so a maxed song never
            # burns a run; service/terminal tiers (is_public False) bypass.
            if is_public and pre_canonical:
                _rc = live_run_count(read_db, pre_canonical[1].id)
                if _rc >= PUBLIC_RUN_CAP:
                    _log_error_event(
                        "submission_run_capped", request,
                        payload={"title": title, "artist": artist, "source": source,
                                 "run_count": _rc, "run_cap": PUBLIC_RUN_CAP},
                    )
                    return _run_capped_response(
                        read_db, source=pre_canonical[0], song_id=pre_canonical[1].id,
                        title=title, artist=artist, run_count=_rc,
                    )

            if pre_canonical and fingerprint:
                prior_fps = fetch_run_fingerprints(
                    read_db, pre_canonical[0], pre_canonical[1].id
                )
                if prior_fps:
                    similarity = max_jaccard(fingerprint, prior_fps)
                    if similarity < DIVERGENCE_THRESHOLD:
                        if is_public:
                            _log_error_event(
                                "submission_lyrics_diverge", request,
                                payload={"title": title, "artist": artist,
                                         "source": source, "similarity": round(similarity, 3),
                                         "prior_runs": len(prior_fps)},
                            )
                        return LyricsCalibrateOut(
                            status="lyrics_diverge_from_prior",
                            title=title, artist=artist,
                            block_reason=(
                                "These lyrics look very different from prior submissions "
                                "for this song. Double-check the title and artist, or paste "
                                "the lyrics for the correct version."
                            ),
                        )

            # Cache lookup before closing the session. calibrate_song_async
            # would do this internally, but we close the session before its
            # Opus call to avoid holding a pooled connection idle.
            cached_calibration = lookup_calibrated(title, artist, read_db)
        finally:
            read_db.close()

        # Credit pre-flight (M3). Signed-in users are gated by credits; anon
        # users keep the existing rate limit + lc_events flow. We pre-check
        # the worst-case (miss) cost so a known-low-balance wallet fails
        # fast before the Opus call; settle to the actual cost (hit vs miss)
        # in the write phase after success. check_credits inline-writes a
        # 'rejected' ledger row on 402 because BackgroundTasks are dropped
        # when the response is non-200 (same constraint we already handle
        # for error lc_events).
        if current_user is not None:
            # Pre-flight at the ACTUAL cost. cached_calibration is already
            # resolved above, so a cache hit (cost 0, the subscription benefit)
            # must NOT be gated behind a worst-case miss check -- otherwise a
            # signed-in user at zero balance is wrongly 402'd out of a free
            # re-read. check_credits is a no-op when cost <= 0.
            preflight_cost = (
                billing_config.COST_SONG_CACHE_HIT if cached_calibration
                else billing_config.COST_SONG_MISS
            )
            billing_svc.check_credits(
                current_user.id, preflight_cost,
                reason=("song_cache_hit" if cached_calibration else "song_miss"),
                allow_daily_free=True,
            )

        # Phase 2: no DB session held. Opus calls only.
        identity = await check_lyrics_identity(
            title=title, artist=artist, lyrics=body.lyrics
        )
        if identity["verdict"] == "no":
            if is_public:
                _log_error_event(
                    "submission_lyrics_mismatch", request,
                    payload={"title": title, "artist": artist, "source": source,
                             "guard_reason": identity["reason"]},
                )
            return LyricsCalibrateOut(
                status="lyrics_mismatch",
                title=title, artist=artist,
                block_reason=(
                    "These lyrics don't match this song. Check the title and artist, "
                    "or paste the lyrics for the correct track."
                ),
            )

        # LEIT clutter control: does this look like a commercially released
        # song? The commercial verdict is folded into the identity-guard Opus
        # call above (no extra call). A confident "no" WARNS (soft gate) -- the
        # Lyrical Charger is for released music; personal/AI/non-song text
        # belongs on the Creative/Curio Charger. "unsure" passes silently so
        # niche/indie artists are never nagged (same anti-false-reject stance as
        # the identity verdict). The heuristic pre-filter is logged as a signal.
        commercial_verdict = identity.get("commercial", "unsure")
        commercial_reason = (identity.get("commercial_reason") or "").strip()
        heuristic_signal = detect_noncommercial_signals(body.lyrics, title, artist)
        if commercial_verdict == "no" and is_public and not body.confirm_commercial:
            schedule_event(background_tasks, "submission_commercial_warned", request,
                           payload={"title": title[:100], "artist": artist[:100],
                                    "source": source, "reason": commercial_reason,
                                    "heuristic": heuristic_signal})
            return LyricsCalibrateOut(
                status="not_commercial_warning",
                title=title, artist=artist,
                commercial_reason=commercial_reason or (
                    "This doesn't look like a commercially released song."
                ),
                block_reason=(
                    "The Lyrical Charger reads commercially released music. This "
                    "looks like it might be something else -- if it IS a released "
                    "song, confirm to continue."
                ),
            )

        # Phase 2 still: run the one calibration path. A cache hit is completed
        # through the same generation step (ensure_full_calibration) so older
        # rows missing ether/prose get filled; a miss runs the full path. Either
        # way `calibration` comes back as one complete object -- rubric + prose
        # + ether tags. No DB session held through these model calls.
        if use_supplied:
            # Local seam: inject the supplied read at the scoring boundary,
            # skip the LEC/Anthropic call, and hard-gate generation OFF so no
            # prose/ether Anthropic call fires on a partial supply.
            calibration = await calibrate_song_async(
                title, artist, body.lyrics, db=None, skip_cache=True,
                progress_cb=progress_cb,
                supplied=body.supplied_calibration, allow_generation=False,
            )
        elif cached_calibration:
            calibration = await ensure_full_calibration(
                title, artist, body.lyrics, cached_calibration,
                progress_cb=progress_cb,
            )
        else:
            # skip_cache=True since Phase 1 already did the lookup; db=None
            # so calibrate_song_async does not open its own session.
            calibration = await calibrate_song_async(
                title, artist, body.lyrics, db=None, skip_cache=True,
                progress_cb=progress_cb,
            )

        color = calibration.get("rubric_color")
        if color is None:
            if is_public:
                schedule_event(background_tasks, "submission_other_error", request,
                               payload={"reason": "calibrator_returned_no_color",
                                        "title": title, "artist": artist, "source": source})
            return LyricsCalibrateOut(status="error", title=title, artist=artist)

        listener_effects_prose = calibration.get("listener_effects_prose")
        societal_effects_prose = calibration.get("societal_effects_prose")

        # Phase 3: open a fresh write session now (after the Opus call) and
        # own the whole write transaction here. Kept separate from Phase 1 so
        # no connection is held idle through Phase 2.
        write_db = SessionLocal()
        try:
            structured = (
                [{"name": a.name, "role": a.role, "position": i} for i, a in enumerate(body.artists)]
                if body.artists else None
            )
            # Native unified storage (Phase 5b): upsert the atomic songs row
            # (crowd lyrical_charger method -> never overrides an authoritative
            # calibration), log the submission ingestion (source + ip), link
            # artists onto the unified id. No legacy submitted_songs row.
            from app.services.song_sync import store_calibrated_song
            from app.services.artist_linker import parse_artist_string
            # A Claude-Code-supplied calibration (local seam) is authoritative
            # (source 'terminal'), so it can correct an existing chart/terminal
            # row; a normal crowd charger submission stays 'submitted'.
            store_source = "terminal" if use_supplied else "submitted"
            new_song_id, _created = store_calibrated_song(
                write_db, source=store_source,
                title=title, artist=artist, calibration=calibration,
                ip_address=get_remote_address(request),
                ingestion_detail={"source": source},
                artist_entries=(structured or parse_artist_string(artist or "")),
            )
            write_db.commit()
            # Mark the run "saved" only AFTER the commit succeeds, so the
            # exception handler never offers a song-page link (or skips a
            # refund) for a row that rolled back on a failed commit.
            submitted_id = new_song_id

            consensus_info = None
            try:
                # is_new_row: a song existing BEFORE this submission (pre_canonical)
                # has prior state worth seeding; a first-time song does not.
                result = record_and_reconcile(
                    write_db,
                    title=title,
                    artist=artist,
                    calibration=calibration,
                    triggered_by="lyrical_charger",
                    lyrics_hash=hash_lyrics(body.lyrics),
                    lyrics_fingerprint=fingerprint,
                    agent_model=calibration.get("agent_model"),
                    direct_song_source="songs",
                    direct_song_id=submitted_id,
                    is_new_row=(pre_canonical_info is None),
                    lyrics=body.lyrics,
                )
                write_db.commit()
                consensus_info = result.get("consensus")
            except Exception:
                write_db.rollback()
                logger.exception("Corpus/consensus step failed (non-fatal)")

            # Slug for the song detail page link returned to the frontend.
            # The submission always reconciles against the one atomic unified song.
            canonical_source, canonical_id = "songs", submitted_id
            song_slug = None
            try:
                song_slug = _get_or_create_slug(
                    title, artist, canonical_source, canonical_id, write_db,
                )
            except Exception:
                logger.exception("song_slug resolution failed (non-fatal)")

            # Attribute this reading to the signed-in user's account, if any.
            if current_user is not None:
                _record_user_calibration(
                    write_db, user_id=current_user.id,
                    song_id=canonical_id,
                    song_slug=song_slug, title=title, artist=artist,
                    calibration=calibration,
                )
                write_db.commit()

            # Charge credits on success (M3). Cost differs by cache hit vs
            # miss: hit is free (subscription benefit, zero marginal cost),
            # miss costs the Opus run. Failure paths above never reach here,
            # so the charge happens only on success. 402 here means a
            # concurrent spend drained the balance between pre-flight and
            # this point -- rare, but we log and continue: the engine has
            # already done the work and the result is going out either way.
            if current_user is not None:
                cost = (
                    billing_config.COST_SONG_CACHE_HIT if cached_calibration
                    else billing_config.COST_SONG_MISS
                )
                if cost > 0:
                    charge_ref_id = str(submitted_id)
                    try:
                        charge_result = billing_svc.charge_song(
                            current_user.id, cost,
                            reason=("song_cache_hit" if cached_calibration else "song_miss"),
                            ref_type="submitted_song", ref_id=charge_ref_id,
                            context={"title": title[:80], "artist": artist[:80]},
                        )
                    except HTTPException:
                        # Free pass spent AND a concurrent spend drained the
                        # wallet between the unlocked pre-flight and this charge.
                        # The result is going out either way; record a delta=0
                        # overrun marker so the uncompensated run is visible to
                        # reconciliation.
                        logger.warning(
                            "charge_song failed post-success user=%s song=%s",
                            current_user.id, submitted_id,
                        )
                        billing_svc.record_unbilled_overrun(
                            current_user.id, cost,
                            ref_type="submitted_song", ref_id=str(submitted_id),
                            context={"title": title[:80], "artist": artist[:80]},
                        )

            if is_public:
                # Optional telemetry -- must never fail a saved, scored run.
                try:
                    schedule_event(background_tasks, "submission_success", request,
                                   payload={"title": title, "artist": artist, "source": source,
                                            "tier": color, "charge": calibration.get("charge_value"),
                                            "contaminated": calibration.get("contaminated", False),
                                            "confidence": calibration.get("confidence")},
                                   song_id=submitted_id)
                except Exception:
                    logger.exception("submission_success event failed (non-fatal)")

                # LEIT clutter control: the submitter was warned this didn't look
                # like a released song and pushed through anyway -> queue it for
                # human audit. Fail-soft (own session) so a queue hiccup can never
                # fail this saved, delivered run.
                if body.confirm_commercial and commercial_verdict == "no":
                    try:
                        record_clutter_finding(
                            song_id=submitted_id,
                            source="lc_push",
                            category="non_commercial",
                            reason=commercial_reason or "Pushed past the non-commercial warning.",
                            suggested_action="review",
                            payload={"title": title[:120], "artist": artist[:120],
                                     "source": source, "heuristic": heuristic_signal,
                                     "ip": get_remote_address(request)},
                        )
                        schedule_event(background_tasks, "submission_commercial_flagged", request,
                                       payload={"title": title[:100], "artist": artist[:100],
                                                "source": source, "reason": commercial_reason},
                                       song_id=submitted_id)
                    except Exception:
                        logger.exception("clutter flag (lc_push) failed (non-fatal)")

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
                consensus=consensus_info if consensus_info else None,
                song_slug=song_slug,
                listener_effects_prose=listener_effects_prose,
                societal_effects_prose=societal_effects_prose,
                deadpan_line=calibration.get("deadpan_line"),
                topics=calibration.get("topics"),
            )
        finally:
            write_db.close()
    except HTTPException:
        # Let credit-gate (402), validation, and other HTTPExceptions through
        # untouched -- the blanket Exception handler below otherwise rewrites
        # them as 500 and the client loses the specific status.
        raise
    except Exception:
        logger.exception("Calibration failed for submitted lyrics")
        if submitted_id is not None and is_public:
            # The song was committed before this failure -- the reading is
            # durable on its song page. For the public charger we don't 500 or
            # refund (it was delivered): hand back a link to the saved result.
            # Machine API/service callers (is_public False) keep the 500 below
            # so their contract is unchanged -- their retry hits the cache.
            try:
                schedule_event(background_tasks, "submission_success", request,
                               payload={"title": title, "artist": artist,
                                        "source": source, "recovered": True},
                               song_id=submitted_id)
            except Exception:
                logger.debug("analyzer: swallowed in _calibrate_lyrics_impl", exc_info=True)
            return LyricsCalibrateOut(
                status="saved_view_on_page",
                title=title, artist=artist, song_slug=song_slug,
                block_reason=(
                    "Your reading finished and was saved, but we hit a snag "
                    "showing it here. It's safe on its song page."
                ),
            )
        # No saved song (or a machine caller) -- refund any charge made this
        # request (defensive: with the current ordering a charge implies a saved
        # song, so this is the belt-and-suspenders guarantee against a future
        # reordering).
        if charge_result is not None and charge_ref_id is not None and current_user is not None:
            billing_svc.refund_song(
                current_user.id, charge_result, ref_id=charge_ref_id,
                context={"title": title[:80], "artist": artist[:80],
                         "reason": "calibration_failed_no_save"},
            )
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "calibrator_exception", "title": title,
                                      "artist": artist, "source": source})
        raise HTTPException(500, "Calibration failed -- try again")


# ------------------------------------------------------------------
# 5b. Async job model for the PUBLIC single-song charge
# ------------------------------------------------------------------
# A public charge is several sequential Opus calls (identity -> calibrate ->
# listener -> ether -> societal -> save). Holding the HTTP connection open for
# the whole tail read as a hang, and the frontend bar could only fake progress
# (it parked at a fixed % until the response landed). The web tool now submits
# to /calibrate-lyrics/start (validates + bot-checks synchronously, creates a
# calibrate_jobs row, launches a worker, returns a token) and polls
# /calibrate-lyrics/status/{token}, driving a REAL bar from the worker's phase.
# The synchronous /calibrate-lyrics endpoint above is unchanged for service/API
# callers -- both share _calibrate_lyrics_impl, so billing / salvage / events
# have one source of truth. Mirrors the Album Charger job model.

# A job left running past this is treated as failed (covers a container restart
# mid-charge -- the in-process task is gone but the row still says "running").
CALIB_JOB_STALE_AFTER = timedelta(minutes=10)

# Hold references to in-flight worker tasks so the event loop doesn't GC them
# (asyncio keeps only weak refs to tasks).
_running_calib_tasks: set = set()


class _ShimRequest:
    """A request-shaped stand-in for the off-request worker. The single-song
    impl only reads ip / user-agent / referrer (via extract_request_meta /
    get_remote_address) and sets request.state.call_context -- this satisfies
    exactly that and nothing else."""

    def __init__(self, ip, user_agent, referrer):
        self.client = SimpleNamespace(host=ip or "127.0.0.1")
        self.headers = {"user-agent": user_agent or "", "referer": referrer or ""}
        self.state = SimpleNamespace()


def _update_calib_job(token: str, **fields) -> None:
    """Short-lived write to the job row. Bumps updated_at. Swallows errors --
    progress telemetry must never crash the worker."""
    fields["updated_at"] = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.query(CalibrateJob).filter(CalibrateJob.job_token == token).update(fields)
        db.commit()
    except Exception:
        logger.exception("calibrate job %s progress update failed", token)
    finally:
        db.close()


def _store_calib_result(token: str, result: LyricsCalibrateOut) -> None:
    """Persist the final LyricsCalibrateOut payload + mark the job done. The
    result's own `status` carries the real outcome (scored, run_capped, etc)."""
    _update_calib_job(
        token, status="done", phase="done",
        result_json=json.dumps(result.model_dump(mode="json")),
    )


async def _run_single_calibration(
    token: str,
    body: LyricsCalibrateIn,
    tier: str,
    current_user_id: int | None,
    meta: dict,
) -> None:
    """Worker: run the public single-song charge off the request, driving the
    job's phase as it goes. Reuses the exact synchronous impl (one source of
    truth) via a request shim, with bot-check skipped (already done at /start)
    and a progress callback that stamps the job phase. Never raises into the
    event loop -- any crash is recorded on the job so the poller stops."""

    def _phase(name: str) -> None:
        _update_calib_job(token, status="running", phase=name)

    shim = _ShimRequest(meta.get("ip"), meta.get("user_agent"), meta.get("referrer"))
    # current_user is only ever read for `.id`; a light stand-in is enough.
    user_obj = SimpleNamespace(id=current_user_id) if current_user_id is not None else None
    try:
        _phase("identity")
        result = await _calibrate_lyrics_impl(
            body=body, request=shim, background_tasks=BackgroundTasks(),
            tier=tier, current_user=user_obj,
            progress_cb=_phase, skip_bot_check=True,
        )
        _store_calib_result(token, result)
    except HTTPException as exc:
        # A credit 402 (balance drained between the /start pre-flight and the
        # worker) or any other HTTP error surfaces to the poller as an error,
        # not a silent stall.
        detail = exc.detail if isinstance(exc.detail, str) else "calibration_error"
        _update_calib_job(token, status="error", phase="done",
                          error_message=f"{exc.status_code}: {detail}")
    except Exception:
        logger.exception("single calibration worker failed (token=%s)", token)
        _update_calib_job(token, status="error", phase="done",
                          error_message="Calibration failed -- try again")


@router.post("/calibrate-lyrics/start", response_model=CalibrateJobOut)
@limiter.limit(_calibrate_daily_limit)
async def calibrate_lyrics_start(
    body: LyricsCalibrateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str = Depends(verify_api_or_service_key),
    current_user: User | None = Depends(optional_clerk_user),
):
    """Public async entry for the single-song charge. Validates + bot-checks +
    credit-pre-flights synchronously (so a bad submission / failed Turnstile /
    out-of-credits is a clean HTTP error here, before any token), then creates a
    job and launches the worker. The frontend polls
    /calibrate-lyrics/status/{token} and drives a real bar from the phase."""
    is_public = tier == "public"
    request.state.call_context = {
        "title": (body.title or "")[:200],
        "artist": (body.artist or "")[:200],
        "source": (body.source or "")[:50],
    }

    if is_public:
        _check_lc_available_or_503()
        await _check_bot_protection(body.hp_website, body.turnstile_token, request)

    source = _resolve_source(tier, body.source)

    # Synchronous validation -- a 422 must surface at submit, not mid-bar.
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
            raise HTTPException(422, {
                "error": "lyrics_rejected",
                "reason": lyrics_error,
                "appeal_url": "/inquiry.html?topic=lyrics_rejected&source=lyrical_charger",
                "message": (
                    f"{lyrics_error} If these are accurate and complete lyrics, "
                    "appeal at /inquiry.html?topic=lyrics_rejected"
                ),
            })
        raise HTTPException(422, lyrics_error)

    # Credit pre-flight (signed-in users) -- a clean 402 at submit. Cost depends
    # on cache status, so resolve it the same way the impl will (a cheap read).
    if current_user is not None:
        read_db = SessionLocal()
        try:
            cached = lookup_calibrated(body.title.strip(), body.artist.strip(), read_db)
        finally:
            read_db.close()
        preflight_cost = (
            billing_config.COST_SONG_CACHE_HIT if cached
            else billing_config.COST_SONG_MISS
        )
        billing_svc.check_credits(
            current_user.id, preflight_cost,
            reason=("song_cache_hit" if cached else "song_miss"),
            allow_daily_free=True,
        )

    token = uuid.uuid4().hex
    meta = extract_request_meta(request)
    db = SessionLocal()
    try:
        db.add(CalibrateJob(job_token=token, status="queued", phase="queued",
                            ip_address=meta.get("ip")))
        db.commit()
    finally:
        db.close()

    task = asyncio.create_task(_run_single_calibration(
        token, body, tier,
        current_user.id if current_user is not None else None,
        meta,
    ))
    _running_calib_tasks.add(task)
    task.add_done_callback(_running_calib_tasks.discard)

    return CalibrateJobOut(job_token=token, status="queued")


@router.get("/calibrate-lyrics/status/{job_token}", response_model=CalibrateStatusOut)
@limiter.limit("600/hour")
async def calibrate_lyrics_status(job_token: str, request: Request):
    """Poll a single-song calibrate job. On done, `result` is the full
    LyricsCalibrateOut the synchronous endpoint would have returned."""
    db = SessionLocal()
    try:
        job = db.query(CalibrateJob).filter(CalibrateJob.job_token == job_token).first()
    finally:
        db.close()
    if not job:
        raise HTTPException(404, "Unknown job.")

    # A job stuck running past the stale window (e.g. a redeploy mid-charge) is
    # reported as errored so the UI stops polling.
    if job.status in ("queued", "running"):
        ref = job.updated_at or job.created_at or datetime.now(timezone.utc)
        if datetime.now(timezone.utc) - ref > CALIB_JOB_STALE_AFTER:
            return CalibrateStatusOut(status="error", phase=job.phase,
                                      error="This calibration timed out. Please try again.")
        return CalibrateStatusOut(status=job.status, phase=job.phase)

    if job.status == "error":
        return CalibrateStatusOut(status="error", phase=job.phase,
                                  error=job.error_message or "Calibration failed -- try again.")

    result = None
    if job.result_json:
        try:
            result = LyricsCalibrateOut(**json.loads(job.result_json))
        except Exception:
            logger.exception("calibrate job %s result parse failed", job_token)
    return CalibrateStatusOut(status="done", phase="done", result=result)


# ------------------------------------------------------------------
# 6. POST /api/analyzer/search-songs — Musixmatch song search
# ------------------------------------------------------------------
@router.post("/search-songs", response_model=SongSearchOut)
@limiter.limit("20/hour")
async def search_songs(body: SongSearchIn, request: Request, background_tasks: BackgroundTasks):
    """Search for songs via Musixmatch. Returns empty list if API key not configured."""
    _check_lc_available_or_503()
    request.state.call_context = {
        "query": (body.query or "")[:200],
        "artist": (body.artist or "")[:200],
    }
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
@limiter.limit(_calibrate_daily_limit)
async def calibrate_search(
    body: SearchCalibrateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str = Depends(verify_api_or_service_key),
    current_user: User | None = Depends(optional_clerk_user),
):
    """Fetch lyrics for a Musixmatch track and calibrate them."""
    is_public = tier == "public"

    request.state.call_context = {
        "title": (body.title or "")[:200],
        "artist": (body.artist or "")[:200],
        "track_id": body.track_id,
        "source": (body.source or "")[:50],
    }

    if is_public:
        _check_lc_available_or_503()
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

    # Search-flow lyrics come from Musixmatch keyed by track_id, so there is
    # no surface for an identity/lyrics mismatch. We still fingerprint the run
    # so paste-path submissions of the same song later have something to
    # compare against (Layer 2 cross-path).
    fingerprint = compute_fingerprint(lyrics)

    # Hoisted so the exception handler can tell a SAVED run (song committed ->
    # salvage, never refund) from a pre-save failure (refund any charge made).
    submitted_id = None
    song_slug = None
    charge_result = None
    charge_ref_id = None

    try:
        # Phase 1: read-only session for the cache lookup, closed before the
        # Opus call so no pooled connection is held idle through it.
        read_db = SessionLocal()
        try:
            # Public run cap (see calibrate-lyrics): a maxed song is admin/
            # terminal-only. Counted before the Opus call; service tiers bypass.
            if is_public:
                _pc = find_canonical_song(title, artist, read_db)
                if _pc:
                    _rc = live_run_count(read_db, _pc[1].id)
                    if _rc >= PUBLIC_RUN_CAP:
                        _log_error_event(
                            "submission_run_capped", request,
                            payload={"title": title, "artist": artist, "source": source,
                                     "run_count": _rc, "run_cap": PUBLIC_RUN_CAP},
                        )
                        return _run_capped_response(
                            read_db, source=_pc[0], song_id=_pc[1].id,
                            title=title, artist=artist, run_count=_rc,
                        )
            cached_calibration = lookup_calibrated(title, artist, read_db)
        finally:
            read_db.close()

        # Credit pre-flight (M3). See calibrate-lyrics for the rationale --
        # signed-in users gated by credits, anon by per-IP rate limit; pre-check
        # at worst-case (miss), settle to actual cost in the write phase.
        if current_user is not None:
            # Pre-flight at the ACTUAL cost. cached_calibration is already
            # resolved above, so a cache hit (cost 0, the subscription benefit)
            # must NOT be gated behind a worst-case miss check -- otherwise a
            # signed-in user at zero balance is wrongly 402'd out of a free
            # re-read. check_credits is a no-op when cost <= 0.
            preflight_cost = (
                billing_config.COST_SONG_CACHE_HIT if cached_calibration
                else billing_config.COST_SONG_MISS
            )
            billing_svc.check_credits(
                current_user.id, preflight_cost,
                reason=("song_cache_hit" if cached_calibration else "song_miss"),
                allow_daily_free=True,
            )

        # Phase 2 still: run the one calibration path. A cache hit is completed
        # through the same generation step; a miss runs the full path. `calibration`
        # comes back complete -- rubric + prose + ether tags. No DB session held.
        if cached_calibration:
            calibration = await ensure_full_calibration(
                title, artist, lyrics, cached_calibration,
            )
        else:
            calibration = await calibrate_song_async(
                title, artist, lyrics, db=None, skip_cache=True,
            )

        color = calibration.get("rubric_color")
        if color is None:
            if is_public:
                schedule_event(background_tasks, "submission_other_error", request,
                               payload={"reason": "calibrator_returned_no_color",
                                        "title": title, "artist": artist, "source": source})
            return LyricsCalibrateOut(status="error", title=title, artist=artist)

        listener_effects_prose = calibration.get("listener_effects_prose")
        societal_effects_prose = calibration.get("societal_effects_prose")

        # Phase 3: fresh write session opened after the Opus call (see
        # calibrate-lyrics) so no connection is held idle through Phase 2.
        write_db = SessionLocal()
        try:
            structured = (
                [{"name": a.name, "role": a.role, "position": i} for i, a in enumerate(body.artists)]
                if body.artists else None
            )
            # Native unified storage (Phase 5b): see calibrate-lyrics. `created`
            # tells us whether this was the song's first appearance (seed vs not).
            from app.services.song_sync import store_calibrated_song
            from app.services.artist_linker import parse_artist_string
            new_song_id, created = store_calibrated_song(
                write_db, source="submitted",
                title=title, artist=artist, calibration=calibration,
                ip_address=get_remote_address(request),
                ingestion_detail={"source": source},
                artist_entries=(structured or parse_artist_string(artist or "")),
            )
            write_db.commit()
            # Mark the run "saved" only AFTER the commit succeeds, so the
            # exception handler never offers a song-page link (or skips a
            # refund) for a row that rolled back on a failed commit.
            submitted_id = new_song_id

            # The submission reconciles against the one atomic unified song.
            canonical_source, canonical_id = "songs", submitted_id
            consensus_info = None
            try:
                result = record_and_reconcile(
                    write_db,
                    title=title,
                    artist=artist,
                    calibration=calibration,
                    triggered_by="lyrical_charger_search",
                    lyrics_hash=hash_lyrics(lyrics),
                    lyrics_fingerprint=fingerprint,
                    agent_model=calibration.get("agent_model"),
                    direct_song_source="songs",
                    direct_song_id=submitted_id,
                    is_new_row=created,
                )
                write_db.commit()
                consensus_info = result.get("consensus")
            except Exception:
                write_db.rollback()
                logger.exception("Corpus/consensus step failed (non-fatal)")

            song_slug = None
            try:
                song_slug = _get_or_create_slug(
                    title, artist, canonical_source, canonical_id, write_db,
                )
            except Exception:
                logger.exception("song_slug resolution failed (non-fatal)")

            # Attribute this reading to the signed-in user's account, if any.
            if current_user is not None:
                _record_user_calibration(
                    write_db, user_id=current_user.id,
                    song_id=canonical_id,
                    song_slug=song_slug, title=title, artist=artist,
                    calibration=calibration,
                )
                write_db.commit()

            # Charge credits on success (M3). See calibrate-lyrics.
            if current_user is not None:
                cost = (
                    billing_config.COST_SONG_CACHE_HIT if cached_calibration
                    else billing_config.COST_SONG_MISS
                )
                if cost > 0:
                    charge_ref_id = str(submitted_id)
                    try:
                        charge_result = billing_svc.charge_song(
                            current_user.id, cost,
                            reason=("song_cache_hit" if cached_calibration else "song_miss"),
                            ref_type="submitted_song", ref_id=charge_ref_id,
                            context={"title": title[:80], "artist": artist[:80],
                                     "track_id": body.track_id},
                        )
                    except HTTPException:
                        # Free pass spent AND a concurrent spend drained the
                        # wallet between the unlocked pre-flight and this charge.
                        # The result is going out either way; record a delta=0
                        # overrun marker so the uncompensated run is visible to
                        # reconciliation.
                        logger.warning(
                            "charge_song failed post-success user=%s song=%s",
                            current_user.id, submitted_id,
                        )
                        billing_svc.record_unbilled_overrun(
                            current_user.id, cost,
                            ref_type="submitted_song", ref_id=str(submitted_id),
                            context={"title": title[:80], "artist": artist[:80]},
                        )

            if is_public:
                # Optional telemetry -- must never fail a saved, scored run.
                try:
                    schedule_event(background_tasks, "submission_success", request,
                                   payload={"title": title, "artist": artist, "source": source,
                                            "tier": color, "charge": calibration.get("charge_value"),
                                            "contaminated": calibration.get("contaminated", False),
                                            "confidence": calibration.get("confidence")},
                                   song_id=submitted_id)
                except Exception:
                    logger.exception("submission_success event failed (non-fatal)")

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
                consensus=consensus_info if consensus_info else None,
                song_slug=song_slug,
                listener_effects_prose=listener_effects_prose,
                societal_effects_prose=societal_effects_prose,
                deadpan_line=calibration.get("deadpan_line"),
                topics=calibration.get("topics"),
            )
        finally:
            write_db.close()
    except HTTPException:
        # Let credit-gate (402) and other HTTPExceptions through cleanly.
        raise
    except Exception:
        logger.exception("Calibration failed for search track %d", body.track_id)
        if submitted_id is not None and is_public:
            # The song was committed before this failure -- the reading is
            # durable on its song page. For the public charger we don't 500 or
            # refund (it was delivered): hand back a link to the saved result.
            # Machine API/service callers (is_public False) keep the 500 below
            # so their contract is unchanged -- their retry hits the cache.
            try:
                schedule_event(background_tasks, "submission_success", request,
                               payload={"title": title, "artist": artist,
                                        "source": source, "recovered": True},
                               song_id=submitted_id)
            except Exception:
                logger.debug("analyzer: swallowed in calibrate_search", exc_info=True)
            return LyricsCalibrateOut(
                status="saved_view_on_page",
                title=title, artist=artist, song_slug=song_slug,
                block_reason=(
                    "Your reading finished and was saved, but we hit a snag "
                    "showing it here. It's safe on its song page."
                ),
            )
        # No saved song (or a machine caller) -- refund any charge made this
        # request (defensive: with the current ordering a charge implies a saved
        # song, so this is the belt-and-suspenders guarantee against a future
        # reordering).
        if charge_result is not None and charge_ref_id is not None and current_user is not None:
            billing_svc.refund_song(
                current_user.id, charge_result, ref_id=charge_ref_id,
                context={"title": title[:80], "artist": artist[:80],
                         "reason": "calibration_failed_no_save"},
            )
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "calibrator_exception", "title": title,
                                      "artist": artist, "source": source})
        raise HTTPException(500, "Calibration failed -- try again")
