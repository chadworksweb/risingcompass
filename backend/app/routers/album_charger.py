"""Album Charger -- public endpoints for user-submitted full-album analysis.

The Album Charger calibrates every track of an album, aggregates the track
charges into an album-level charge (mean), runs one album-level synthesis Opus
call, and -- crucially -- creates a real Release attached to the artist (the
first public path that does so, because the user supplies real release
metadata). Each track is calibrated cache-first through the exact same path the
single-song Lyrical Charger uses, so already-charged tracks cost no Opus calls.

Because a full album is minutes of sequential Opus work (too long to hold an
HTTP connection open for), charging is an ASYNC JOB:

  POST /calibrate        -- validate + bot-check synchronously, create an
                            album_charge_jobs row, kick off a background task,
                            return a job token immediately (202).
  GET  /status/{token}   -- poll job progress (phase + calibrated_tracks) and,
                            when done, the full result payload.

The background worker runs the phase split (mirrors analyzer.py, scaled to N
tracks):
  Phase A  -- resolve lyrics + per-track cache lookup in brief read sessions.
  Phase B  -- per-track calibration, concurrent (cap ALBUM_TRACK_CONCURRENCY);
              NO DB session held through any Opus call. Progress is persisted
              as each track completes.
  Phase C  -- one album-synthesis Opus call over the per-track readings.
  Phase D  -- one write transaction: persist tracks (submitted_songs), upsert
              the artist, create/update the Release, link tracks via
              release_songs, write aggregates + synthesis.

The work (and the Release write) completes server-side even if the client
disconnects -- the connection is never held for the duration.

Build Manually (pasted lyrics) works at launch. Search Album is gated behind
Musixmatch (musixmatch.is_configured()) and ships dark until that key is set.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.schemas import (
    AlbumCalibrateIn, AlbumCalibrateOut, AlbumTrackResult,
    AlbumChargeJobOut, AlbumChargeStatusOut,
    AlbumSearchIn, AlbumSearchOut, AlbumTracklistIn, AlbumTracklistOut,
)
from app.models import SubmittedSong, Release, ReleaseSong, User, AlbumChargeJob
from app.constants import COLOR_LABELS
from app.auth import verify_api_or_service_key, optional_clerk_user
from app import billing_config
from app.services import billing as billing_svc
from app.services import musixmatch
from app.services.agents.calibrator import (
    calibrate_song_async, lookup_calibrated, ensure_full_calibration,
)
from app.services.artist_linker import (
    try_link_song, upsert_artist, parse_artist_string, format_artist_string,
)
from app.services.artist_utils import compute_release_charge, normalize_artist_name
from app.services.album_synthesis import generate_album_synthesis
from app.services.calibration_corpus import (
    record_and_reconcile, hash_lyrics, find_canonical_song, get_or_create_song,
)
from app.services.lyrics_fingerprint import compute_fingerprint
from app.services.lc_events import schedule_event, write_event, extract_request_meta
from app.services.alerts import emit_album_charged

# Reuse the single-song endpoints' shared helpers verbatim -- one source of
# truth for bot protection, availability gating, lyric validation, source
# resolution, the SubmittedSong field map, user attribution, and the
# app-registered rate limiter.
from app.routers.analyzer import (
    limiter,
    _check_lc_available_or_503,
    _check_bot_protection,
    _validate_lyrics,
    _resolve_source,
    _song_persist_fields,
    _record_user_calibration,
    _log_error_event,
)
from app.routers.songs import _get_or_create_slug

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzer/album", tags=["album-charger"])

# Album calibration is the expensive lane. Cap it harder than the single-song
# lane (20/day). The frontend caps tracks at 15; the schema enforces it too.
ALBUM_CALIBRATE_LIMIT = "6/day"

# Max tracks calibrated concurrently. Each track is up to 4 sequential Anthropic
# calls; this caps how many tracks fan out at once so a long album can't open
# dozens of simultaneous model calls (rate limits + thread-pool pressure).
ALBUM_TRACK_CONCURRENCY = 5

# A job left running this long is treated as failed (covers a container restart
# mid-charge -- the in-process task is gone but the row would say "running").
JOB_STALE_AFTER = timedelta(minutes=15)

# Hold references to in-flight background tasks so the event loop doesn't GC
# them mid-run (asyncio only keeps weak references to tasks).
_running_tasks: set[asyncio.Task] = set()


# ------------------------------------------------------------------
# Search Album (Musixmatch-gated; ships dark)
# ------------------------------------------------------------------
_GATED_MSG = (
    "Album search isn't available yet. Switch to Build Manually to paste each "
    "track's lyrics."
)


@router.post("/search", response_model=AlbumSearchOut)
@limiter.limit("20/hour")
async def search_albums_endpoint(
    body: AlbumSearchIn, request: Request, background_tasks: BackgroundTasks,
):
    """Search albums via Musixmatch. Returns the gated message when the
    Musixmatch key is not configured (the whole Search Album tab ships dark)."""
    _check_lc_available_or_503()
    request.state.call_context = {
        "query": (body.query or "")[:200],
        "artist": (body.artist or "")[:200],
    }
    if not musixmatch.is_configured():
        schedule_event(background_tasks, "album_search_query", request,
                       payload={"query": body.query[:200], "artist": (body.artist or "")[:200],
                                "result_count": 0, "configured": False})
        return AlbumSearchOut(results=[], message=_GATED_MSG)

    results = await musixmatch.search_albums(body.query, body.artist)
    schedule_event(background_tasks, "album_search_query", request,
                   payload={"query": body.query[:200], "artist": (body.artist or "")[:200],
                            "result_count": len(results), "configured": True})
    if not results:
        return AlbumSearchOut(results=[], message="No albums found. Try a different search.")
    return AlbumSearchOut(results=results)


@router.post("/search-tracks", response_model=AlbumTracklistOut)
@limiter.limit("30/hour")
async def album_tracklist_endpoint(
    body: AlbumTracklistIn, request: Request,
):
    """Fetch an album's tracklist from Musixmatch. Gated like /search."""
    _check_lc_available_or_503()
    if not musixmatch.is_configured():
        return AlbumTracklistOut(message=_GATED_MSG)
    tracks = await musixmatch.get_album_tracks(body.album_id)
    if not tracks:
        return AlbumTracklistOut(message="Couldn't load that album's tracklist.")
    return AlbumTracklistOut(tracks=tracks)


# ------------------------------------------------------------------
# Job helpers
# ------------------------------------------------------------------
def _primary_artist_name(artist: str, structured: list[dict] | None) -> str:
    """Pick the lead artist the Release attaches to. First structured primary,
    else first parsed primary, else the normalized credit string."""
    if structured:
        for e in structured:
            if e.get("role", "primary") == "primary" and e.get("name"):
                return e["name"].strip()
    parsed = parse_artist_string(artist)
    for e in parsed:
        if e.get("role") == "primary" and e.get("name"):
            return e["name"].strip()
    return normalize_artist_name(artist) or artist.strip()


def _update_job(token: str, **fields) -> None:
    """Short-lived write to the job row. Always bumps updated_at (Core-style
    .update() doesn't fire the ORM onupdate). Swallows errors -- progress
    telemetry must never crash the worker."""
    fields["updated_at"] = datetime.utcnow()
    db = SessionLocal()
    try:
        db.query(AlbumChargeJob).filter(AlbumChargeJob.job_token == token).update(fields)
        db.commit()
    except Exception:
        logger.exception("album job %s progress update failed", token)
    finally:
        db.close()


def _store_result(token: str, result: AlbumCalibrateOut) -> None:
    """Persist the final album payload + mark the job done."""
    _update_job(
        token,
        status="done",
        phase="done",
        result_json=json.dumps(result.model_dump(mode="json")),
    )


# ------------------------------------------------------------------
# Background worker
# ------------------------------------------------------------------
async def _run_album_charge(
    token: str,
    body: AlbumCalibrateIn,
    is_public: bool,
    source: str,
    meta: dict,
    current_user_id: int | None,
    hold_cost: int = 0,
) -> None:
    """The heavy lifting, run off the request. Updates the job row as it goes
    and writes the final AlbumCalibrateOut into result_json. Never raises into
    the event loop -- any failure is recorded on the job."""
    album_title = (body.album_title or "").strip()
    artist = (body.artist or "").strip()
    ip = meta.get("ip")

    def _event(event_type: str, payload: dict | None = None) -> None:
        if is_public:
            write_event(event_type, meta.get("ip"), meta.get("user_agent"),
                        meta.get("referrer"), payload=payload)

    # Per-track miss flags captured from Phase A so the final settle_hold can
    # bill only for tracks that actually ran the Opus path. Mutable; updated
    # by Phase A. Reset to [] on exception so a crash refunds the full hold.
    miss_track_count = 0

    try:
        _update_job(token, status="running", phase="validating")

        # Album-level artist credit. Track-level featured artists are layered on
        # top of this per track (a guest on one track shouldn't be credited on
        # the whole album). The Release still attaches to the primary artist.
        album_entries = (
            [{"name": a.name, "role": a.role, "position": i} for i, a in enumerate(body.artists)]
            if body.artists else parse_artist_string(artist)
        )

        def _track_credit(features: list[str] | None) -> tuple[str, list[dict]]:
            entries = [dict(e) for e in album_entries]
            seen = {e["name"].strip().lower() for e in entries if e.get("name")}
            pos = len(entries)
            for raw in (features or []):
                name = (raw or "").strip()
                if not name or name.lower() in seen:
                    continue
                entries.append({"name": name, "role": "featured", "position": pos})
                seen.add(name.lower())
                pos += 1
            display = format_artist_string(entries) or artist
            return display, entries

        # ---- Phase A: resolve lyrics + cache lookup per track ----
        working: list[dict] = []
        track_results: list[AlbumTrackResult] = []

        for idx, t in enumerate(body.tracks):
            track_title = (t.title or "").strip()
            track_number = t.track_number if t.track_number is not None else idx + 1
            if not track_title:
                continue
            track_artist, track_structured = _track_credit(t.featured)

            lyrics = (t.lyrics or "").strip() if t.lyrics else ""
            if not lyrics and t.track_id is not None and musixmatch.is_configured():
                fetched = await musixmatch.get_lyrics(t.track_id)
                lyrics = (fetched or "").strip()

            if not lyrics:
                track_results.append(AlbumTrackResult(
                    track_number=track_number, title=track_title, artist=track_artist,
                    status="skipped", skip_reason="No lyrics available for this track.",
                ))
                continue

            lyrics_error = _validate_lyrics(lyrics)
            if lyrics_error:
                track_results.append(AlbumTrackResult(
                    track_number=track_number, title=track_title, artist=track_artist,
                    status="skipped", skip_reason=lyrics_error,
                ))
                continue

            read_db = SessionLocal()
            try:
                cached = lookup_calibrated(track_title, track_artist, read_db)
            finally:
                read_db.close()

            working.append({
                "title": track_title,
                "track_number": track_number,
                "artist": track_artist,
                "structured": track_structured,
                "lyrics": lyrics,
                "cached": cached,
                "fingerprint": compute_fingerprint(lyrics),
            })

        if not working:
            _event("album_no_tracks",
                   {"album": album_title, "artist": artist, "source": source})
            _store_result(token, AlbumCalibrateOut(
                status="no_tracks", album_title=album_title, artist=artist,
                track_count=len(body.tracks), tracks=track_results,
                block_reason="None of the tracks had usable lyrics to calibrate.",
            ))
            return

        # ---- Phase B: per-track calibration, concurrent, progress reported ----
        # The single-song identity guard is deliberately skipped per-track (it
        # would N-double model cost on a hand-built tracklist). No prompt
        # caching (see LC-ALBUM-CHARGER.md).
        _update_job(token, phase="calibrating", total_tracks=len(working), calibrated_tracks=0)
        sem = asyncio.Semaphore(ALBUM_TRACK_CONCURRENCY)
        done_count = 0
        done_lock = asyncio.Lock()

        async def _calibrate_one(w: dict):
            nonlocal done_count
            try:
                async with sem:
                    if w["cached"]:
                        return await ensure_full_calibration(
                            w["title"], w["artist"], w["lyrics"], w["cached"])
                    return await calibrate_song_async(
                        w["title"], w["artist"], w["lyrics"], db=None, skip_cache=True)
            finally:
                async with done_lock:
                    done_count += 1
                    _update_job(token, calibrated_tracks=done_count)

        results = await asyncio.gather(
            *[_calibrate_one(w) for w in working], return_exceptions=True)
        for w, res in zip(working, results):
            if isinstance(res, Exception):
                logger.error("Album track '%s' calibration failed: %r", w["title"], res)
                w["calibration"] = None
            else:
                w["calibration"] = res

        scored = [w for w in working if (w.get("calibration") or {}).get("rubric_color")]
        for w in working:
            if w not in scored:
                track_results.append(AlbumTrackResult(
                    track_number=w["track_number"], title=w["title"], artist=w["artist"],
                    status="skipped", skip_reason="Calibration returned no reading for this track.",
                ))

        # Cache-aware billing: a track that hit the cache costs 0 credits
        # (zero-marginal-cost engine read, the subscription benefit). A miss
        # ran the full Opus path and counts toward the actual cost. settle_hold
        # below refunds (hold_cost - actual) back to the original bucket split.
        miss_track_count = sum(1 for w in scored if not w.get("cached"))

        if not scored:
            _store_result(token, AlbumCalibrateOut(
                status="no_tracks", album_title=album_title, artist=artist,
                track_count=len(body.tracks), tracks=track_results,
                block_reason="None of the tracks could be calibrated.",
            ))
            return

        # ---- aggregate + Phase C: album synthesis ----
        _update_job(token, phase="synthesizing")
        charges = [w["calibration"]["charge_value"] for w in scored
                   if w["calibration"].get("charge_value") is not None]
        contamination_count = sum(1 for w in scored if w["calibration"].get("contaminated"))
        agg = compute_release_charge(charges) if charges else None
        agg_charge = agg[0] if agg else None
        agg_color = agg[1] if agg else None

        synthesis = generate_album_synthesis(
            album_title=album_title, artist=artist, release_type=body.release_type,
            aggregate_charge=agg_charge, aggregate_color=agg_color,
            tracks=[{
                "track_number": w["track_number"], "title": w["title"],
                "tier": w["calibration"].get("rubric_color"),
                "charge": w["calibration"].get("charge_value"),
                "charge_summary": w["calibration"].get("charge_summary"),
                "contaminated": w["calibration"].get("contaminated", False),
                "effects_prose": w["calibration"].get("effects_prose"),
                "societal_prose": w["calibration"].get("societal_effects_prose"),
            } for w in scored],
        )

        # ---- Phase D: one write transaction ----
        _update_job(token, phase="writing")
        release_id = None
        artist_slug = None
        write_db = SessionLocal()
        try:
            for w in scored:
                track_title = w["title"]
                track_artist = w["artist"]
                calibration = w["calibration"]
                submitted, _created = get_or_create_song(
                    write_db, SubmittedSong,
                    title=track_title, artist=track_artist, source=source,
                    ip_address=ip,
                    **_song_persist_fields(calibration),
                )
                write_db.commit()
                write_db.refresh(submitted)
                try_link_song(track_title, track_artist, "submitted", submitted.id,
                              write_db, structured=w["structured"])

                canonical_source, canonical_id = "submitted", submitted.id
                try:
                    pre_canonical = find_canonical_song(track_title, track_artist, write_db)
                    if pre_canonical and pre_canonical[0] == "submitted" and pre_canonical[1].id == submitted.id:
                        pre_canonical = None
                    if pre_canonical:
                        canonical_source, canonical_id = pre_canonical[0], pre_canonical[1].id
                    record_and_reconcile(
                        write_db,
                        title=track_title, artist=track_artist, calibration=calibration,
                        triggered_by="album_charger",
                        lyrics_hash=hash_lyrics(w["lyrics"]),
                        lyrics_fingerprint=w["fingerprint"],
                        agent_model=calibration.get("agent_model"),
                        direct_song_source=canonical_source,
                        direct_song_id=canonical_id,
                        is_new_row=(canonical_source == "submitted" and canonical_id == submitted.id),
                    )
                    write_db.commit()
                except Exception:
                    write_db.rollback()
                    logger.exception("Corpus step failed for album track '%s' (non-fatal)", track_title)
                    canonical_source, canonical_id = "submitted", submitted.id

                slug = None
                try:
                    slug = _get_or_create_slug(track_title, track_artist, canonical_source, canonical_id, write_db)
                except Exception:
                    logger.exception("slug resolution failed for album track '%s' (non-fatal)", track_title)

                if current_user_id is not None:
                    _record_user_calibration(
                        write_db, user_id=current_user_id,
                        song_source=canonical_source, song_id=canonical_id,
                        song_slug=slug, title=track_title, artist=track_artist,
                        calibration=calibration,
                    )
                    write_db.commit()

                w["canonical"] = (canonical_source, canonical_id)
                track_results.append(AlbumTrackResult(
                    track_number=w["track_number"], title=track_title, artist=track_artist,
                    status="scored",
                    tier=calibration.get("rubric_color"),
                    tier_label=COLOR_LABELS.get(calibration.get("rubric_color")),
                    charge=calibration.get("charge_value"),
                    contaminated=calibration.get("contaminated", False),
                    song_slug=slug,
                ))

            # Upsert the artist + create/update the Release.
            primary_name = _primary_artist_name(artist, album_entries)
            artist_entity = upsert_artist(write_db, primary_name)
            write_db.flush()
            artist_slug = artist_entity.slug

            release_year = body.release_year
            if release_year is None and body.release_date is not None:
                release_year = body.release_date.year

            release = (
                write_db.query(Release)
                .filter(Release.artist_id == artist_entity.id)
                .filter(Release.title == album_title)
                .first()
            )
            now = datetime.utcnow()
            if release is None:
                release = Release(artist_id=artist_entity.id, title=album_title,
                                  release_type=body.release_type)
                write_db.add(release)
            else:
                release.release_type = body.release_type
                write_db.query(ReleaseSong).filter(ReleaseSong.release_id == release.id).delete()

            if release_year is not None:
                release.release_year = release_year
            if body.release_date is not None:
                release.release_date = body.release_date
            release.charge_value = agg_charge
            release.rubric_color = agg_color
            release.track_count = len(body.tracks)
            release.calibrated_count = len(scored)
            release.contamination_count = contamination_count
            release.charge_summary = synthesis.get("charge_summary")
            release.arc_prose = synthesis.get("arc_prose")
            release.societal_prose = synthesis.get("societal_prose")
            release.source = "album_charger"
            release.submitted_at = now
            write_db.flush()

            for w in scored:
                c_source, c_id = w.get("canonical", ("submitted", None))
                if c_id is None:
                    continue
                write_db.add(ReleaseSong(
                    release_id=release.id, song_source=c_source,
                    song_id=c_id, track_number=w["track_number"],
                ))
            write_db.commit()
            release_id = release.id
        finally:
            write_db.close()

        _event("album_success",
               {"album": album_title, "artist": artist, "source": source,
                "tier": agg_color, "charge": agg_charge,
                "calibrated": len(scored), "tracks": len(body.tracks),
                "contaminated": contamination_count})

        emit_album_charged(
            album_title=album_title, artist=artist, artist_slug=artist_slug,
            release_type=body.release_type, charge=agg_charge,
            tier_label=COLOR_LABELS.get(agg_color) if agg_color else None,
            track_count=len(body.tracks), calibrated_count=len(scored),
            contamination_count=contamination_count,
        )

        track_results.sort(key=lambda r: (r.track_number is None, r.track_number or 0))
        _store_result(token, AlbumCalibrateOut(
            status="scored", album_title=album_title, artist=artist,
            artist_slug=artist_slug, release_id=release_id, release_type=body.release_type,
            tier=agg_color, tier_label=COLOR_LABELS.get(agg_color) if agg_color else None,
            charge=agg_charge, track_count=len(body.tracks), calibrated_count=len(scored),
            contamination_count=contamination_count,
            charge_summary=synthesis.get("charge_summary"),
            arc_prose=synthesis.get("arc_prose"),
            societal_prose=synthesis.get("societal_prose"),
            tracks=track_results,
        ))
    except Exception:
        logger.exception("Album charge job %s failed", token)
        _event("album_other_error",
               {"reason": "worker_exception", "album": album_title, "artist": artist, "source": source})
        _update_job(token, status="error",
                    error_message="Album calibration failed. Please try again.")
        # Crash path: nothing scored counts. miss_track_count stays at its
        # last value (0 if the crash was before Phase A; partial if mid-flight),
        # and the finally below refunds the rest. This biases toward
        # over-refunding on crash, which is the right side to err on.
        miss_track_count = 0
    finally:
        # Settle the hold (M3). Refunds (hold_cost - actual_cost) to the
        # bucket(s) the hold was drawn from; charges extra if actual > hold
        # (rare since holds are pessimistic). No-op when there was no hold.
        if current_user_id is not None and hold_cost > 0:
            actual_cost = miss_track_count * billing_config.COST_ALBUM_TRACK_MISS
            try:
                billing_svc.settle_hold(
                    current_user_id,
                    hold_cost=hold_cost, actual_cost=actual_cost,
                    ref_type="album_job", ref_id=token,
                    context={"album": album_title[:80], "artist": artist[:80]},
                )
            except Exception:
                logger.exception(
                    "settle_hold failed for album job=%s user=%s", token, current_user_id,
                )


# ------------------------------------------------------------------
# Submit + poll
# ------------------------------------------------------------------
@router.post("/calibrate", response_model=AlbumChargeJobOut, status_code=202)
@limiter.limit(ALBUM_CALIBRATE_LIMIT)
async def calibrate_album(
    body: AlbumCalibrateIn,
    request: Request,
    tier: str = Depends(verify_api_or_service_key),
    current_user: User | None = Depends(optional_clerk_user),
):
    """Queue an album charge. Validates + bot-checks synchronously, then runs
    the calibration in the background and returns a job token to poll."""
    is_public = tier == "public"
    album_title = (body.album_title or "").strip()
    artist = (body.artist or "").strip()
    request.state.call_context = {
        "album": album_title[:200], "artist": artist[:200],
        "source": (body.source or "")[:50], "tracks": len(body.tracks),
    }

    if is_public:
        _check_lc_available_or_503()
        await _check_bot_protection(body.hp_website, body.turnstile_token, request)

    source = _resolve_source(tier, body.source)

    if not album_title:
        raise HTTPException(422, "Album title is required.")
    if not artist:
        raise HTTPException(422, "Artist is required.")

    # Cheap pre-check so obviously-empty submissions fail fast (the real
    # per-track lyric validation happens in the worker). A track counts if it
    # has a title and either pasted lyrics or a Musixmatch track_id (when search
    # is live).
    search_live = musixmatch.is_configured()
    has_candidate = any(
        (t.title or "").strip() and (
            (t.lyrics or "").strip() or (t.track_id is not None and search_live)
        )
        for t in body.tracks
    )
    if not has_candidate:
        raise HTTPException(422, "Add at least one track with pasted lyrics.")

    # Credit pre-flight + hold (M3). Sized worst-case (every track a miss);
    # _run_album_charge settles to the actual miss count at the end and
    # refunds the difference. Anon callers (no current_user) keep the
    # existing rate-limit gate -- M5 tunes the limiter for that path.
    hold_cost = 0
    if current_user is not None:
        hold_cost = len(body.tracks) * billing_config.COST_ALBUM_TRACK_MISS
        if hold_cost > 0:
            billing_svc.check_credits(current_user.id, hold_cost, reason="album")

    meta = extract_request_meta(request)
    token = uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.add(AlbumChargeJob(
            job_token=token, status="queued", phase="queued",
            total_tracks=len(body.tracks), ip_address=meta.get("ip"),
        ))
        db.commit()
    finally:
        db.close()

    # Take the hold now -- token is the unique ref_id so a retry of /calibrate
    # (different token) holds independently; settle_hold inside the worker
    # reconciles by token. If hold_credits 402s here, the job row already
    # exists; mark it errored so polling clients see the failure quickly.
    if current_user is not None and hold_cost > 0:
        try:
            billing_svc.hold_credits(
                current_user.id, hold_cost,
                ref_type="album_job", ref_id=token,
                context={"tracks": len(body.tracks), "album": album_title[:80]},
            )
        except HTTPException:
            _update_job(token, status="error",
                        error_message="Insufficient credits for this album.")
            raise

    cuid = current_user.id if current_user is not None else None
    task = asyncio.create_task(
        _run_album_charge(token, body, is_public, source, meta, cuid, hold_cost))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

    return AlbumChargeJobOut(job_token=token, status="queued", total_tracks=len(body.tracks))


@router.get("/status/{job_token}", response_model=AlbumChargeStatusOut)
@limiter.limit("600/hour")
async def album_status(job_token: str, request: Request, db: Session = Depends(get_db)):
    """Poll an album charge job. Returns progress, and the full result once done."""
    job = db.query(AlbumChargeJob).filter(AlbumChargeJob.job_token == job_token).first()
    if not job:
        raise HTTPException(404, "Unknown job.")

    # Stale guard: a job stuck in queued/running past the cutoff (e.g. the
    # container restarted mid-charge) is reported as an error so the UI stops
    # polling forever.
    if job.status in ("queued", "running") and job.updated_at \
            and (datetime.utcnow() - job.updated_at) > JOB_STALE_AFTER:
        return AlbumChargeStatusOut(
            status="error", phase=job.phase,
            total_tracks=job.total_tracks or 0, calibrated_tracks=job.calibrated_tracks or 0,
            error="This album charge timed out. Please try again.",
        )

    result = None
    if job.status == "done" and job.result_json:
        try:
            result = json.loads(job.result_json)
        except Exception:
            logger.exception("album job %s result parse failed", job_token)

    return AlbumChargeStatusOut(
        status=job.status, phase=job.phase,
        total_tracks=job.total_tracks or 0, calibrated_tracks=job.calibrated_tracks or 0,
        result=result, error=job.error_message,
    )
