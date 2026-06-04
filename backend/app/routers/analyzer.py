"""Lyrical Charger — public endpoints for user-submitted song analysis."""

import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.database import SessionLocal
from app.schemas import (
    LyricsCalibrateIn, LyricsCalibrateOut,
    SongSearchIn, SongSearchOut, SearchCalibrateIn,
    LCAvailabilityOut, LCSubscribeIn, LCSubscribeOut,
)
from app.models import LyricalChargerSubscriber, UserCalibration, User
from app.services.feature_flags import (
    is_lyrical_charger_disabled, lyrical_charger_disabled_message,
    lyrical_charger_anon_daily_limit, lyrical_charger_user_daily_limit,
)
from app.constants import COLOR_LABELS
from app.services.agents.calibrator import (
    calibrate_song_async, lookup_calibrated, ensure_full_calibration,
)
from app.services import musixmatch
from app.services.artist_linker import try_link_song
from app.services.calibration_corpus import (
    record_and_reconcile, hash_lyrics, find_canonical_song,
    get_or_create_song, fetch_run_fingerprints,
)
from app.services.lyrics_fingerprint import (
    compute_fingerprint, max_jaccard, DIVERGENCE_THRESHOLD,
)
from app.services.identity_guard import check_lyrics_identity
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
    if isinstance(key, str) and key.startswith("user:"):
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
    """Public config for the LC frontend (which bot protection to render, which
    Musixmatch-gated search surfaces to enable, etc.)."""
    search_enabled = musixmatch.is_configured()
    return {
        "turnstile_site_key": settings.turnstile_site_key,
        # Musixmatch-gated surfaces. Both the song "Search by Song" tab and the
        # album "Search Album" sub-tab ship dark until the key is configured.
        "song_search_enabled": search_enabled,
        "album_search_enabled": search_enabled,
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
        pass
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


def _resolve_source(tier: str, requested: str | None) -> str:
    """Public callers always get 'lyrical_charger'. Service callers can self-tag
    (e.g. 'chadlewine'); falls back to 'service' if they don't supply one.
    Truncated to 30 chars to fit the column."""
    if tier == "public":
        return "lyrical_charger"
    return ((requested or "service").strip() or "service")[:30]


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
        "effects_prose": calibration.get("effects_prose"),
        "societal_effects_prose": calibration.get("societal_effects_prose"),
        # Sealed generation provenance, kept in lockstep with the prose column.
        "societal_prose_generated_at": calibration.get("societal_prose_generated_at"),
        "societal_prose_model": calibration.get("societal_prose_model"),
        "deadpan_line": calibration.get("deadpan_line"),
        "topics": json.dumps(topics) if topics else None,
        "topic_audit": json.dumps(topic_audit) if topic_audit else None,
    }


def _record_user_calibration(
    db, *, user_id: int, song_source: str, song_id: int,
    song_slug: str | None, title: str, artist: str, calibration: dict,
) -> None:
    """Upsert the "songs you've calibrated" row for a signed-in LC user.

    One row per (user, canonical song); re-running refreshes the snapshot +
    calibrated_at. Fails soft -- a logging tool reading shouldn't 500 because
    the activity write hit a snag. The caller commits.
    """
    try:
        existing = (
            db.query(UserCalibration)
            .filter(
                UserCalibration.user_id == user_id,
                UserCalibration.song_source == song_source,
                UserCalibration.song_id == song_id,
            )
            .first()
        )
        from datetime import datetime as _dt
        now = _dt.utcnow()
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
                song_source=song_source,
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

        # Phase 2 still: run the one calibration path. A cache hit is completed
        # through the same generation step (ensure_full_calibration) so older
        # rows missing ether/prose get filled; a miss runs the full path. Either
        # way `calibration` comes back as one complete object -- rubric + prose
        # + ether tags. No DB session held through these model calls.
        if cached_calibration:
            calibration = await ensure_full_calibration(
                title, artist, body.lyrics, cached_calibration,
            )
        else:
            # skip_cache=True since Phase 1 already did the lookup; db=None
            # so calibrate_song_async does not open its own session.
            calibration = await calibrate_song_async(
                title, artist, body.lyrics, db=None, skip_cache=True,
            )

        color = calibration.get("rubric_color")
        if color is None:
            if is_public:
                schedule_event(background_tasks, "submission_other_error", request,
                               payload={"reason": "calibrator_returned_no_color",
                                        "title": title, "artist": artist, "source": source})
            return LyricsCalibrateOut(status="error", title=title, artist=artist)

        effects_prose = calibration.get("effects_prose")
        societal_prose = calibration.get("societal_effects_prose")

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
            submitted_id, _created = store_calibrated_song(
                write_db, source="submitted",
                title=title, artist=artist, calibration=calibration,
                ip_address=get_remote_address(request),
                ingestion_detail={"source": source},
                artist_entries=(structured or parse_artist_string(artist or "")),
            )
            write_db.commit()

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
                    song_source=canonical_source, song_id=canonical_id,
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
                    try:
                        billing_svc.charge_credits(
                            current_user.id, cost,
                            reason=("song_cache_hit" if cached_calibration else "song_miss"),
                            ref_type="submitted_song", ref_id=str(submitted_id),
                            context={"title": title[:80], "artist": artist[:80]},
                        )
                    except HTTPException:
                        # A concurrent spend drained the wallet between the
                        # unlocked pre-flight and this charge. The result is
                        # going out either way; record a delta=0 overrun marker
                        # so the uncompensated run is visible to reconciliation.
                        logger.warning(
                            "charge_credits failed post-success user=%s song=%s",
                            current_user.id, submitted_id,
                        )
                        billing_svc.record_unbilled_overrun(
                            current_user.id, cost,
                            ref_type="submitted_song", ref_id=str(submitted_id),
                            context={"title": title[:80], "artist": artist[:80]},
                        )

            if is_public:
                schedule_event(background_tasks, "submission_success", request,
                               payload={"title": title, "artist": artist, "source": source,
                                        "tier": color, "charge": calibration.get("charge_value"),
                                        "contaminated": calibration.get("contaminated", False),
                                        "confidence": calibration.get("confidence")},
                               submission_id=submitted_id)

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
                effects_prose=effects_prose,
                societal_effects_prose=societal_prose,
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
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "calibrator_exception", "title": title,
                                      "artist": artist, "source": source})
        raise HTTPException(500, "Calibration failed -- try again")


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

    try:
        # Phase 1: read-only session for the cache lookup, closed before the
        # Opus call so no pooled connection is held idle through it.
        read_db = SessionLocal()
        try:
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

        effects_prose = calibration.get("effects_prose")
        societal_prose = calibration.get("societal_effects_prose")

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
            submitted_id, created = store_calibrated_song(
                write_db, source="submitted",
                title=title, artist=artist, calibration=calibration,
                ip_address=get_remote_address(request),
                ingestion_detail={"source": source},
                artist_entries=(structured or parse_artist_string(artist or "")),
            )
            write_db.commit()

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
                    song_source=canonical_source, song_id=canonical_id,
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
                    try:
                        billing_svc.charge_credits(
                            current_user.id, cost,
                            reason=("song_cache_hit" if cached_calibration else "song_miss"),
                            ref_type="submitted_song", ref_id=str(submitted_id),
                            context={"title": title[:80], "artist": artist[:80],
                                     "track_id": body.track_id},
                        )
                    except HTTPException:
                        # A concurrent spend drained the wallet between the
                        # unlocked pre-flight and this charge. The result is
                        # going out either way; record a delta=0 overrun marker
                        # so the uncompensated run is visible to reconciliation.
                        logger.warning(
                            "charge_credits failed post-success user=%s song=%s",
                            current_user.id, submitted_id,
                        )
                        billing_svc.record_unbilled_overrun(
                            current_user.id, cost,
                            ref_type="submitted_song", ref_id=str(submitted_id),
                            context={"title": title[:80], "artist": artist[:80]},
                        )

            if is_public:
                schedule_event(background_tasks, "submission_success", request,
                               payload={"title": title, "artist": artist, "source": source,
                                        "tier": color, "charge": calibration.get("charge_value"),
                                        "contaminated": calibration.get("contaminated", False),
                                        "confidence": calibration.get("confidence")},
                               submission_id=submitted_id)

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
                effects_prose=effects_prose,
                societal_effects_prose=societal_prose,
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
        if is_public:
            _log_error_event("submission_other_error", request,
                             payload={"reason": "calibrator_exception", "title": title,
                                      "artist": artist, "source": source})
        raise HTTPException(500, "Calibration failed -- try again")
