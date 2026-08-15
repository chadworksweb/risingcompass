"""Songs API — public endpoints for individual song pages (effects label)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import func, or_, and_

from app import billing_config
from app.auth import optional_admin_session, optional_clerk_user
from sqlalchemy import text
from app.database import SessionLocal
from app.models import (
    Song, SongSlug, SongArtist,
    ReleaseSong, Release, Artist, MisreadSubmission, SongRecalibration, SongReset,
    CalibrationRun, PrePublishCorrection, User,
)
from app.services.calibration_corpus import compute_consensus
from app.services.song_search import search_unified
from app.constants import COLOR_LABELS, COLOR_HEX, chart_source_label
from app.services.artist_utils import generate_song_slug
from app.services import coverart, ether_taxonomy

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


# --- Related by the reading ------------------------------------------------
#
# RC's relation is the lens, not the market. Two songs are related here because
# they carry the same ether topics and land near each other on the charge
# scale -- never because of genre, label, popularity, or a MusicBrainz
# adjacency, which is the relation every other music site already draws and the
# one thing this site is not for.
#
# Scoring, highest first:
#   shared topics  x100  -- the primary axis; more overlap always outranks less
#   dominant match  +40  -- topics[0] is the dominant read, so agreeing there
#                           beats agreeing on an incidental tag
#   proximity     0..100 -- 100 minus the charge gap, so within one tier of
#                           overlap the closer reading wins
# Proximity maxes below one shared topic on purpose: topics decide the set,
# charge only orders it.
RELATED_SONG_LIMIT = 6
RELATED_ARTIST_LIMIT = 6
_RELATED_CANDIDATE_CAP = 1200
_SHARED_TOPIC_WEIGHT = 100
_DOMINANT_TOPIC_BONUS = 40
# Fallback band for a song with no topics: same tier, within this many points.
_RELATED_CHARGE_BAND = 8


def _related_title_key(title: str) -> str:
    """Collapse a title to what it is a version OF. A remix, live cut, or
    radio edit shares every topic with its parent and lands beside it, so
    without this the list spends two of its six rows on the same song."""
    t = (title or "").lower()
    for opener, closer in (("(", ")"), ("[", "]")):
        while opener in t:
            head, _, rest = t.partition(opener)
            _, _, tail = rest.partition(closer)
            t = head + tail
    return "".join(c for c in t if c.isalnum() or c == " ").strip()


def _slug_safe_topics(raw_topics) -> list[str]:
    """Topic slugs safe to interpolate into a LIKE. The vocabulary is closed and
    kebab-case, so anything else is data rot and gets dropped rather than
    matched."""
    out = []
    for t in raw_topics or []:
        if isinstance(t, str) and t and all(c.isalnum() or c == "-" for c in t):
            out.append(t)
    return out


@router.get("/{slug}/related")
def song_related(slug: str):
    """Songs and artists related to this one BY THE READING.

    Returns `songs`, `artists`, and the `basis` the relation was drawn on, so
    the page can state why each one is there instead of asserting a bare
    resemblance.
    """
    import json as _json

    db = SessionLocal()
    try:
        slug_row = db.query(SongSlug).filter(SongSlug.slug == slug).first()
        base_id = _slug_unified_id(db, slug_row) if slug_row else None
        if not base_id:
            found = _find_by_generated_slug(slug, db)
            base_id = found.get("song_id") if found else None
        base = db.query(Song).get(base_id) if base_id else None
        if not base:
            raise HTTPException(404, "Song not found")

        try:
            base_topics = _slug_safe_topics(_json.loads(base.topics) if base.topics else [])
        except (ValueError, TypeError):
            base_topics = []
        base_charge = base.charge_value
        dominant = base_topics[0] if base_topics else None

        # The song's own credits, so the section surfaces other artists rather
        # than turning into a second copy of this artist's catalogue.
        base_artist_ids = {
            r[0] for r in db.query(SongArtist.artist_id)
            .filter(SongArtist.song_id == base.id).all()
        }

        cand_q = (
            db.query(Song.id, Song.title, Song.artist, Song.topics,
                     Song.charge_value, Song.rubric_color)
            .filter(Song.id != base.id)
            .filter(Song.charge_value.isnot(None))
            .filter(Song.calibration_failed.isnot(True))
        )
        if base_topics:
            basis_kind = "topics"
            cand_q = cand_q.filter(or_(*[
                Song.topics.like(f'%"{t}"%') for t in base_topics
            ]))
        else:
            # No topics tagged: the only reading left to relate on is where it
            # landed, so fall back to its own tier inside a tight charge band.
            basis_kind = "charge"
            if base_charge is None or not base.rubric_color:
                return {"songs": [], "artists": [], "basis": {"kind": "none"}}
            cand_q = (
                cand_q.filter(Song.rubric_color == base.rubric_color)
                .filter(Song.charge_value >= base_charge - _RELATED_CHARGE_BAND)
                .filter(Song.charge_value <= base_charge + _RELATED_CHARGE_BAND)
            )

        candidates = cand_q.limit(_RELATED_CANDIDATE_CAP).all()

        base_topic_set = set(base_topics)
        scored = []
        for row in candidates:
            try:
                cand_topics = _json.loads(row.topics) if row.topics else []
            except (ValueError, TypeError):
                cand_topics = []
            cand_topics = _slug_safe_topics(cand_topics)
            # Ordered by THIS song's reading, not the candidate's, so the same
            # overlap is phrased the same way down the whole list.
            cand_set = set(cand_topics)
            shared = [t for t in base_topics if t in cand_set]
            # The LIKE is a substring match, so a slug that is a prefix of
            # another ("power" inside "power-fantasy") can pull in a song that
            # shares nothing. The parsed intersection is the real test.
            if basis_kind == "topics" and not shared:
                continue
            gap = abs((row.charge_value or 0) - (base_charge or 0))
            score = (
                len(shared) * _SHARED_TOPIC_WEIGHT
                + (_DOMINANT_TOPIC_BONUS if dominant and cand_topics[:1] == [dominant] else 0)
                + (100 - min(gap, 100))
            )
            scored.append({
                "row": row,
                "shared": shared,
                "gap": gap,
                "score": score,
            })

        scored.sort(key=lambda s: (-s["score"], s["gap"], s["row"].title or ""))

        # --- artists: aggregated over the WHOLE candidate set, not just the
        # songs that made the cut, so an artist who reads this way across four
        # songs outranks one who did it once with a closer charge.
        artists_out = []
        if scored:
            by_song = {s["row"].id: s for s in scored}
            credit_rows = (
                db.query(SongArtist.song_id, Artist.id, Artist.name, Artist.slug)
                .join(Artist, Artist.id == SongArtist.artist_id)
                .filter(SongArtist.song_id.in_(list(by_song.keys())))
                .all()
            )
            agg: dict[int, dict] = {}
            for song_id, artist_id, name, artist_slug in credit_rows:
                if artist_id in base_artist_ids or not artist_slug:
                    continue
                s = by_song.get(song_id)
                if not s:
                    continue
                a = agg.setdefault(artist_id, {
                    "name": name,
                    "slug": artist_slug,
                    "song_count": 0,
                    "shared": set(),
                    "best_score": 0,
                    "charge_total": 0,
                })
                a["song_count"] += 1
                a["shared"].update(s["shared"])
                a["best_score"] = max(a["best_score"], s["score"])
                a["charge_total"] += s["row"].charge_value or 0
            # Rank on the closest song an artist actually has, with the count
            # as a pure tie-break. Counting first ranks by how much of an
            # artist happens to be ingested -- a 55-song catalogue then tops
            # every page it touches, which is a fact about the library, not
            # about this song. The count still rides along in the payload, so
            # the page can say "four songs read this way" without that number
            # deciding the order.
            ranked = sorted(
                agg.values(),
                key=lambda a: (-a["best_score"], -a["song_count"], a["name"]),
            )[:RELATED_ARTIST_LIMIT]
            for a in ranked:
                artists_out.append({
                    "name": a["name"],
                    "slug": a["slug"],
                    "song_count": a["song_count"],
                    "avg_charge": round(a["charge_total"] / a["song_count"]),
                    "shared_topics": [t for t in base_topics if t in a["shared"]],
                })

        # --- songs: one per artist and one per work, so six rows are six
        # different acts and no row is a remix of the row above it.
        songs_out = []
        seen_artists = set()
        seen_titles = set()
        for s in scored:
            row = s["row"]
            key = (row.artist or "").strip().lower()
            title_key = _related_title_key(row.title)
            if key and key in seen_artists:
                continue
            if title_key and title_key in seen_titles:
                continue
            seen_artists.add(key)
            seen_titles.add(title_key)
            songs_out.append({
                "slug": _get_or_create_slug(row.title, row.artist or "", "songs", row.id, db),
                "title": row.title,
                "artist": row.artist,
                "charge_value": row.charge_value,
                "rubric_color": row.rubric_color,
                "tier_label": COLOR_LABELS.get(row.rubric_color, "") if row.rubric_color else "",
                "tier_hex": COLOR_HEX.get(row.rubric_color, "#999") if row.rubric_color else "#999",
                "shared_topics": s["shared"],
                "charge_gap": s["gap"],
            })
            if len(songs_out) >= RELATED_SONG_LIMIT:
                break

        labels = {}
        try:
            labels = {
                slug_: meta.get("label") or slug_.replace("-", " ")
                for slug_, meta in ether_taxonomy.topics(db).items()
            }
        except Exception:  # taxonomy is presentation only -- never fail the lane
            logger.exception("related: topic label resolve failed")

        def _label(t: str) -> str:
            return labels.get(t, t.replace("-", " "))

        for item in songs_out:
            item["shared_topic_labels"] = [_label(t) for t in item["shared_topics"]]
        for item in artists_out:
            item["shared_topic_labels"] = [_label(t) for t in item["shared_topics"]]

        return {
            "songs": songs_out,
            "artists": artists_out,
            "basis": {
                "kind": basis_kind,
                "topics": base_topics,
                "topic_labels": [_label(t) for t in base_topics],
                "charge_value": base_charge,
                "tier_label": COLOR_LABELS.get(base.rubric_color, "") if base.rubric_color else "",
            },
        }
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


def _dominant_theme(topics, db) -> dict | None:
    """The theme the song's dominant topic sits under, or None. Fails soft: a
    taxonomy hiccup costs a chip, never the song page."""
    if not topics:
        return None
    try:
        hierarchy = ether_taxonomy.topic_hierarchy(db)
        meta = hierarchy.get("topics", {}).get(topics[0]) or {}
        slug = meta.get("primary")
        if not slug:
            return None
        label = next(
            (t["label"] for t in hierarchy.get("themes", []) if t["slug"] == slug),
            slug,
        )
        return {"slug": slug, "label": label}
    except Exception:
        logger.exception("dominant theme resolve failed")
        return None


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
        # The DOMINANT topic's theme, so the song page can lead its topic chips
        # with the parent tier. Resolved from the live taxonomy, never stored on
        # the song: a topic's theme is an editable classification, and caching
        # it here would let the two drift.
        "theme": _dominant_theme(topics, db),
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
    release_mbid = None
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
            release_mbid = release.musicbrainz_id
            artist = db.query(Artist).get(release.artist_id)
            if artist:
                song["artist_slug"] = artist.slug

    # Cover art (Cover Art Archive, hotlinked -- see services/coverart). Prefer
    # the song's release, which is the cover a listener actually associates with
    # it; fall back to the release-group the backfill resolved for songs that
    # have no Release row (every chart-born and Charger-born single). Cache-only,
    # so this adds one indexed lookup and never a network call.
    song_mbid = db.query(Song.release_group_mbid).filter(Song.id == unified_id).scalar()
    song["cover_url"] = coverart.cover_url_for_mbids(db, [release_mbid, song_mbid])

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
