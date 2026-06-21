"""Songs API — public endpoints for individual song pages (effects label)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import func, or_, and_

from app import billing_config
from app.auth import optional_admin_session, optional_clerk_user
from sqlalchemy import text
from app.database import SessionLocal
from app.models import (
    Song, SongSlug,
    ReleaseSong, Release, Artist, MisreadSubmission, SongRecalibration, SongReset,
    CalibrationRun, PrePublishCorrection, User,
)
from app.services.calibration_corpus import compute_consensus
from app.services.song_search import search_unified
from app.constants import COLOR_LABELS, COLOR_HEX, chart_source_label
from app.services.artist_utils import generate_song_slug

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/songs", tags=["songs"])


def _slug_unified_id(db, slug_row) -> int | None:
    """The unified songs.id a slug points at -- the renamed `song_id` column
    (unified renovation 5c-2; migration 087 backfilled it for every resolvable
    slug before the legacy poly columns were dropped)."""
    return slug_row.song_id


@router.get("/{slug}/flag-counts")
def song_flag_counts(slug: str):
    """Public flag activity for a song. Counts distinct devices to keep the
    number an honest reflection of how many people flagged it, not how many
    submissions were made.

    Matches by polymorphic (song_source, song_id) when the misread row was
    resolved at submit time, OR by case-insensitive (title, artist) strings
    as a fallback (covers older submissions and edge cases where the slug
    matcher couldn't resolve).
    """
    db = SessionLocal()
    try:
        slug_row = db.query(SongSlug).filter(SongSlug.slug == slug).first()
        if slug_row:
            title, artist = slug_row.title, slug_row.artist
            unified_id = _slug_unified_id(db, slug_row)
        else:
            song = _find_by_generated_slug(slug, db)
            if not song:
                raise HTTPException(404, "Song not found")
            title = song["title"]
            artist = song.get("artist") or ""
            unified_id = song.get("song_id")

        title_l = (title or "").strip().lower()
        artist_l = (artist or "").strip().lower()

        match_clauses = [and_(
            func.lower(MisreadSubmission.song_title) == title_l,
            func.lower(MisreadSubmission.song_artist) == artist_l,
        )]
        if unified_id:
            match_clauses.append(MisreadSubmission.song_id == unified_id)
        match = or_(*match_clauses)

        def _count_distinct_devices(report_type: str) -> int:
            return db.query(func.count(func.distinct(MisreadSubmission.device_id))).filter(
                match,
                MisreadSubmission.report_type == report_type,
                MisreadSubmission.device_id.isnot(None),
            ).scalar() or 0

        return {
            "misread": _count_distinct_devices("misread"),
            "satirical": _count_distinct_devices("satirical"),
        }
    finally:
        db.close()


@router.get("/{slug}/history")
def song_history(slug: str, admin_user=Depends(optional_admin_session)):
    """Public recalibration history for a song. Renders as small print on the
    song page — the recalibrate suite is honest about its history because that
    honesty IS the proof of objectivity. Lists every applied recalibration
    chronologically with the admin-written public summary.

    Public callers see only rows with promoted_to_feed=true. Authed admins
    (rc_admin_session cookie) see every row — the full internal audit trail.
    """
    admin = admin_user is not None
    db = SessionLocal()
    try:
        slug_row = db.query(SongSlug).filter(SongSlug.slug == slug).first()
        if slug_row:
            unified_id = _slug_unified_id(db, slug_row)
        else:
            song = _find_by_generated_slug(slug, db)
            if not song:
                raise HTTPException(404, "Song not found")
            unified_id = song.get("song_id")

        if not unified_id:
            return {"recalibrations": []}

        q = (
            db.query(SongRecalibration)
            .filter(SongRecalibration.song_id == unified_id)
        )
        # All recalibrations are auto-promoted (2026-04-23); gate retired.
        rows = q.order_by(SongRecalibration.applied_at.desc()).all()

        import json as _json
        out = []
        for r in rows:
            entry = {
                "id": r.id,
                "lens": r.lens,
                "pipeline": r.pipeline,
                "applied_at": r.applied_at.isoformat() if r.applied_at else None,
                "before": {
                    "charge": r.before_charge,
                    "color": r.before_color,
                    "tier_label": COLOR_LABELS.get(r.before_color, ""),
                    "tier_hex": COLOR_HEX.get(r.before_color, "#999"),
                    "summary": r.before_summary,
                },
                "after": {
                    "charge": r.after_charge,
                    "color": r.after_color,
                    "tier_label": COLOR_LABELS.get(r.after_color, ""),
                    "tier_hex": COLOR_HEX.get(r.after_color, "#999"),
                },
                "public_summary": r.public_summary,
                "flag_count_snapshot": _json.loads(r.flag_count_snapshot) if r.flag_count_snapshot else None,
                "rubric_change_slug": r.rubric_change_slug,
                "rubric_change_note": r.rubric_change_note,
            }
            out.append(entry)

        reset_rows = (
            db.query(SongReset)
            .filter(SongReset.song_id == unified_id)
            .order_by(SongReset.reset_at.desc())
            .all()
        )
        resets = [
            {
                "id": r.id,
                "reset_at": r.reset_at.isoformat() if r.reset_at else None,
                "reason": r.reason,
                "before": {
                    "charge": r.before_charge,
                    "color": r.before_color,
                    "tier_label": COLOR_LABELS.get(r.before_color, ""),
                    "tier_hex": COLOR_HEX.get(r.before_color, "#999"),
                    "summary": r.before_summary,
                    "contaminated": bool(r.before_contaminated) if r.before_contaminated is not None else False,
                    "contamination_note": r.before_contamination_note,
                },
            }
            for r in reset_rows
        ]

        # Pre-publish corrections -- the capture table for admin overrides of
        # draft songs before publication. The audit pointer compass_song_id now
        # carries the unified songs.id (Phase 5b).
        corrections: list = []
        if True:
            cq = (
                db.query(PrePublishCorrection)
                .filter(PrePublishCorrection.compass_song_id == unified_id)
            )
            for r in cq.order_by(PrePublishCorrection.occurred_at.desc()).all():
                corrections.append({
                    "id": r.id,
                    "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                    "before": {
                        "charge": r.before_charge_value,
                        "color": r.before_rubric_color,
                        "tier_label": COLOR_LABELS.get(r.before_rubric_color, ""),
                        "tier_hex": COLOR_HEX.get(r.before_rubric_color, "#999"),
                        "contaminated": bool(r.before_contaminated) if r.before_contaminated is not None else False,
                        "contamination_note": r.before_contamination_note,
                        "summary": r.before_summary,
                    },
                    "after": {
                        "charge": r.after_charge_value,
                        "color": r.after_rubric_color,
                        "tier_label": COLOR_LABELS.get(r.after_rubric_color, ""),
                        "tier_hex": COLOR_HEX.get(r.after_rubric_color, "#999"),
                        "contaminated": bool(r.after_contaminated) if r.after_contaminated is not None else False,
                        "contamination_note": r.after_contamination_note,
                        "summary": r.after_summary,
                    },
                    "human_rationale": r.human_rationale,
                    "tags": r.tags,
                })

        return {
            "recalibrations": out,
            "resets": resets,
            "pre_publish_corrections": corrections,
        }
    finally:
        db.close()


@router.get("/{slug}/calibration-runs")
def song_calibration_runs(slug: str, limit: int = 50):
    """Public list of every agent run logged for this song + consensus stats.

    Shows the corpus behind the current calibration — each run's tier/charge,
    when it fired, and what triggered it. Consensus is the MEDIAN of live runs
    the canonical song row drifts toward as runs accumulate (Calibrator v3).
    Explicit-field serializer on purpose: the v3 axis/component columns are
    internal-only by ruling and must never ship here.
    """
    db = SessionLocal()
    try:
        slug_row = db.query(SongSlug).filter(SongSlug.slug == slug).first()
        if slug_row:
            unified_id = _slug_unified_id(db, slug_row)
        else:
            song = _find_by_generated_slug(slug, db)
            if not song:
                raise HTTPException(404, "Song not found")
            unified_id = song.get("song_id")

        if not unified_id:
            return {"runs": [], "consensus": None}

        rows = (
            db.query(CalibrationRun)
            .filter(CalibrationRun.song_id == unified_id)
            .order_by(CalibrationRun.run_at.desc())
            .limit(max(1, min(limit, 500)))
            .all()
        )
        runs = [
            {
                "id": r.id,
                "run_at": r.run_at.isoformat() if r.run_at else None,
                "rubric_color": r.rubric_color,
                "charge_value": r.charge_value,
                "tier_label": COLOR_LABELS.get(r.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(r.rubric_color, "#999"),
                "charge_summary": r.charge_summary,
                "contaminated": bool(r.contaminated) if r.contaminated is not None else False,
                "confidence": r.confidence,
                "triggered_by": r.triggered_by,
                "agent_model": r.agent_model,
                "superseded": bool(r.superseded) if r.superseded is not None else False,
                "superseded_reason": r.superseded_reason,
                "superseded_at": r.superseded_at.isoformat() if r.superseded_at else None,
            }
            for r in rows
        ]
        # Every calibrated song shows at least its initial calibration, even if
        # it predates run-logging (seeded / imported songs have no CalibrationRun
        # rows). Synthesize that first entry from the song's own reading.
        if not runs:
            seed = _synthesize_initial_run(unified_id, db)
            if seed:
                runs = [seed]

        consensus = compute_consensus(db, "songs", unified_id)
        if consensus:
            consensus["tier_label"] = COLOR_LABELS.get(consensus["rubric_color"], "")
            consensus["tier_hex"] = COLOR_HEX.get(consensus["rubric_color"], "#999")
        return {"runs": runs, "consensus": consensus}
    finally:
        db.close()


@router.get("/search")
def song_search_unified(
    request: Request,
    q: str | None = None,
    source: str | None = Query(None, description="compass | library | submitted | stream"),
    tier: str | None = Query(None, description="violet | blue | green | orange | red"),
    charge_min: int | None = None,
    charge_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    contaminated: bool | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 20,
    current_user: User | None = Depends(optional_clerk_user),
):
    """Unified song search across the four song tables.

    Same filter shape as the admin `all_songs` DB tab, minus PII. The
    free-tier 20-cap applies when BOTH paths are unpaid:
      - user_sub_tier in (None, 'free')
      - plan_tier not in PAID_API_TIERS

    Paid via either path lifts the cap. A signed-in Plus/Pro user calling
    with the legacy-public api key is paid (consumer wrapper); a B2B api
    client on plan_tier='plus'/'pro'/'internal'/'system'/'service' is
    also paid (B2B wrapper). Same gate, two identities -- the unification
    described in RISING-COMPASS-MONETIZATION-BUILD-PLAN section 1.

    Admin equivalent is `/api/admin/db/all_songs` (full PII, unlimited).
    """
    plan_tier = getattr(request.state, "plan_tier", None) or "free"
    client_behavior = getattr(request.state, "client_behavior", "public") or "public"
    user_sub_tier = current_user.subscription_tier if current_user else None
    is_paid = (
        billing_config.is_paid_user(user_sub_tier)
        or billing_config.is_paid_api_client(plan_tier, behavior=client_behavior)
    )
    is_free = not is_paid
    effective_limit = min(20, max(1, limit)) if is_free else min(200, max(1, limit))
    effective_offset = 0 if is_free else max(0, offset)

    db = SessionLocal()
    try:
        result = search_unified(
            db,
            q=q,
            source=source,
            tier=tier,
            charge_min=charge_min,
            charge_max=charge_max,
            year_min=year_min,
            year_max=year_max,
            contaminated=contaminated,
            sort_by=sort_by,
            sort_dir=sort_dir,
            offset=effective_offset,
            limit=effective_limit,
            include_pii=False,
            include_prose=is_paid,
            attach_slugs=True,
        )
        result["plan_tier"] = plan_tier
        result["user_subscription_tier"] = user_sub_tier or "free"
        result["free_tier"] = is_free
        result["hidden_count"] = max(0, result["total"] - (result["offset"] + len(result["items"])))
        return result
    finally:
        db.close()


@router.get("/{slug}")
def song_detail(slug: str):
    """Look up a song by slug and return full calibration data for the effects label page."""
    db = SessionLocal()
    try:
        # Check slug lookup table first
        slug_row = db.query(SongSlug).filter(SongSlug.slug == slug).first()

        if slug_row:
            unified_id = _slug_unified_id(db, slug_row)
            song = _resolve_song(unified_id, db) if unified_id else None
            if song:
                song["slug"] = slug
                _enrich_with_release_context(song, unified_id, db)
                return song

        # Fallback: try to match slug against generated slugs
        song = _find_by_generated_slug(slug, db)
        if song:
            return song

        raise HTTPException(404, "Song not found")
    finally:
        db.close()


@router.get("")
def song_search(q: str = "", limit: int = 20):
    """Search songs by title across all tables. Returns matches with slugs.

    Treats "&" and "and" as equivalent on both sides of the substring match,
    so "ask & tell" and "ask and tell" both find the same row.
    """
    db = SessionLocal()
    try:
        if len(q.strip()) < 2:
            return {"results": []}

        q_lower = q.strip().lower()
        q_norm = q_lower.replace("&", "and")
        results = []
        seen = set()  # (title_lower, artist_lower) to dedupe

        title_norm_expr = func.replace(func.lower(Song.title), "&", "and")
        query = (
            db.query(Song)
            .filter(or_(
                func.lower(Song.title).contains(q_lower),
                title_norm_expr.contains(q_norm),
            ))
            .filter(Song.charge_value.isnot(None))
        )
        for row in query.limit(limit * 2).all():
            key = (row.title.lower(), (row.artist or "").lower())
            if key in seen:
                continue
            seen.add(key)

            slug = _get_or_create_slug(row.title, row.artist or "", "songs", row.id, db)
            results.append({
                "id": row.id,
                "title": row.title,
                "artist": row.artist,
                "slug": slug,
                "rubric_color": row.rubric_color,
                "charge_value": row.charge_value,
                "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
            })
            if len(results) >= limit:
                break

        # Batch-attach artist_slug for each result row.
        from app.services.artist_utils import normalize_artist_name, resolve_artist_slugs
        slug_map = resolve_artist_slugs([r.get("artist") for r in results], db)
        for r in results:
            primary = normalize_artist_name(r.get("artist") or "").lower()
            r["artist_slug"] = slug_map.get(primary)

        return {"results": results}
    finally:
        db.close()


def _synthesize_initial_run(unified_id: int, db) -> dict | None:
    """Build a virtual 'initial calibration' run from the song's own reading.

    For songs with no logged CalibrationRun rows (seeded / imported before run
    logging existed), so the Calibration Runs panel always shows at least the
    reading that put the song on the compass. Returns None for uncalibrated
    songs (charge_value IS NULL) — there's nothing to show.
    """
    row = db.query(Song).get(unified_id)
    if not row or row.charge_value is None:
        return None
    # No run_at: these rows have no calibration timestamp. created_at is the
    # song's backfilled release/import date (e.g. 1985-01-01), NOT when the
    # compass read it — so showing it would be a lie. Better undated than wrong.
    return {
        "id": None,
        "run_at": None,
        "rubric_color": row.rubric_color,
        "charge_value": row.charge_value,
        "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
        "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
        "charge_summary": getattr(row, "charge_summary", None),
        "contaminated": bool(getattr(row, "contaminated", False)),
        "confidence": None,
        "triggered_by": "seed",
        "agent_model": None,
        "superseded": False,
        "superseded_reason": None,
        "superseded_at": None,
        "synthesized": True,
    }


def _resolve_song(unified_id: int, db) -> dict | None:
    """Resolve a unified song id to a full display dict."""
    row = db.query(Song).get(unified_id)
    if not row:
        return None

    # A reset song keeps its row but has charge_value=NULL and rubric_color="".
    is_uncalibrated = row.charge_value is None
    import json as _json
    try:
        topics = _json.loads(row.topics) if row.topics else None
    except (ValueError, TypeError):
        topics = None
    return {
        "title": row.title,
        "artist": row.artist,
        "rubric_color": row.rubric_color if row.rubric_color else None,
        "charge_value": row.charge_value,
        "tier_label": COLOR_LABELS.get(row.rubric_color, "") if row.rubric_color else "",
        "tier_hex": COLOR_HEX.get(row.rubric_color, "#999") if row.rubric_color else "#999",
        "contaminated": row.contaminated or False,
        "contamination_note": row.contamination_note,
        "dogma_referenced": row.dogma_referenced or False,
        "dogma_note": row.dogma_note,
        "translated": row.translated or False,
        "medley": row.medley or False,
        "lyrics_unavailable": row.lyrics_unavailable or False,
        "charge_summary": row.charge_summary,
        # Ether Art Chart fields -- feed the shareable charge card on the song page.
        "deadpan_line": row.deadpan_line,
        "topics": topics,
        "listener_effects_prose": row.listener_effects_prose,
        "societal_effects_prose": row.societal_effects_prose,
        "uncalibrated": is_uncalibrated,
        "origin_chart": row.origin_chart,
        "origin_chart_label": chart_source_label(row.origin_chart),
        "song_source": "songs",
        "song_id": unified_id,
    }


def _enrich_with_release_context(song: dict, unified_id: int, db):
    """Add release + artist context to a song dict if available."""
    link = (
        db.query(ReleaseSong)
        .filter(ReleaseSong.song_id == unified_id)
        .first()
    )
    if link:
        release = db.query(Release).get(link.release_id)
        if release:
            song["release_title"] = release.title
            song["release_type"] = release.release_type
            song["release_date"] = release.release_date.isoformat() if release.release_date else None
            artist = db.query(Artist).get(release.artist_id)
            if artist:
                song["artist_slug"] = artist.slug

    # Fallback: songs with no release link (daily/submitted) still have an
    # artist name — resolve it to the artist-page slug the same way the search
    # endpoint does, so the song page can link the artist.
    if not song.get("artist_slug") and song.get("artist"):
        from app.services.artist_utils import normalize_artist_name, resolve_artist_slugs
        slug_map = resolve_artist_slugs([song["artist"]], db)
        primary = normalize_artist_name(song["artist"]).lower()
        resolved = slug_map.get(primary)
        if resolved:
            song["artist_slug"] = resolved


def _find_by_generated_slug(slug: str, db) -> dict | None:
    """Try to match a slug by generating slugs from the unified Library."""
    # Fallback for songs not yet in the slug table. Iterates the atomic songs.
    for row in db.query(Song).filter(Song.charge_value.isnot(None)).all():
        generated = generate_song_slug(row.title, row.artist or "")
        if generated == slug:
            # Create slug entry for faster future lookups
            _get_or_create_slug(row.title, row.artist or "", "songs", row.id, db)
            song = _resolve_song(row.id, db)
            if song:
                song["slug"] = slug
                _enrich_with_release_context(song, row.id, db)
            return song
    return None


def _get_or_create_slug(title: str, artist: str, source: str, song_id: int, db) -> str:
    """Get existing slug or create one for a song. Native callers pass
    source='songs' with the unified id; the slug's song_id is set to the unified
    id so the read paths (and song_search._attach_slugs) resolve it."""
    # Resolve the unified id for the pointer (source='songs' -> the id directly,
    # any legacy pair via song_id_map).
    if source == "songs":
        unified_id = song_id
    else:
        unified_id = db.execute(
            text("SELECT new_song_id FROM song_id_map WHERE old_source = :s AND old_id = :i"),
            {"s": source, "i": song_id},
        ).scalar()

    # Check if this song already has a slug (by unified id).
    existing = None
    if unified_id:
        existing = db.query(SongSlug).filter(SongSlug.song_id == unified_id).first()
    if existing:
        return existing.slug

    slug = generate_song_slug(title, artist)

    # Handle collision
    base_slug = slug
    suffix = 2
    while db.query(SongSlug).filter(SongSlug.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    entry = SongSlug(
        slug=slug,
        title=title,
        artist=artist,
        song_id=unified_id,
    )
    db.add(entry)
    db.commit()
    return slug
