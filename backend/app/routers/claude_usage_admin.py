"""Admin endpoints for outbound Claude API usage — totals, breakdowns, calls.

Backs the "Claude Usage" admin tab. Reads from claude_api_usage, populated by
app.services.claude_meter on every Anthropic messages.create() call.
"""

import logging
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClaudeApiUsage, Song, SongSlug, SongArtist, Artist
from app.routers.admin import verify_admin_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/claude-usage", tags=["claude-usage-admin"])


def _resolve_since(days: int) -> datetime:
    days = max(1, min(days, 365))
    return datetime.now(timezone.utc) - timedelta(days=days)


def _day_expr(tz: str | None):
    """Day-bucket expression for ClaudeApiUsage.ts. With a valid IANA tz, bucket
    by that zone's calendar day (ts is naive UTC -> interpret as UTC, convert to
    the zone, then take the date); otherwise bucket by UTC day. PG-specific
    (AT TIME ZONE via func.timezone); local dev is also Postgres."""
    if tz:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)  # validate; raises on a bad zone
            local_ts = func.timezone(tz, func.timezone("UTC", ClaudeApiUsage.ts))
            return func.date(local_ts)
        except Exception:
            logger.debug("claude_usage_admin: swallowed in _day_expr", exc_info=True)
    return func.date(ClaudeApiUsage.ts)


def _attach_song_links(calls, db):
    """Resolve song-page + artist-page slugs for calls whose context names a
    (title, artist), so the admin call list can deep-link. Batched: one query
    each for songs, slugs, and primary-artist. Calls that don't resolve are
    left without slug keys and render as plain text."""
    from app.services.song_identity import compute_canonical_key
    key_to_idxs = {}
    for i, c in enumerate(calls):
        ctx = c.get("context")
        if isinstance(ctx, dict) and ctx.get("title"):
            try:
                k = compute_canonical_key(ctx.get("title"), ctx.get("artist") or "")
            except Exception:
                logger.debug("claude_usage_admin: swallowed in _attach_song_links", exc_info=True)
                k = None
            if k:
                key_to_idxs.setdefault(k, []).append(i)
    if not key_to_idxs:
        return
    key_to_song = {ck: sid for sid, ck in
                   db.query(Song.id, Song.canonical_key)
                     .filter(Song.canonical_key.in_(list(key_to_idxs))).all()}
    ids = list(key_to_song.values())
    if not ids:
        return
    slug_by_id = {}
    for sid, slug in (db.query(SongSlug.song_id, SongSlug.slug)
                        .filter(SongSlug.song_id.in_(ids)).all()):
        slug_by_id.setdefault(sid, slug)
    artist_by_id = {}
    for sid, aslug in (db.query(SongArtist.song_id, Artist.slug)
                         .join(Artist, Artist.id == SongArtist.artist_id)
                         .filter(SongArtist.song_id.in_(ids))
                         .order_by(SongArtist.song_id, SongArtist.position).all()):
        artist_by_id.setdefault(sid, aslug)
    for k, idxs in key_to_idxs.items():
        sid = key_to_song.get(k)
        if not sid:
            continue
        for i in idxs:
            calls[i]["song_slug"] = slug_by_id.get(sid)
            calls[i]["artist_slug"] = artist_by_id.get(sid)


# --- Call-site registry -----------------------------------------------------
# Classifies known call_sites against the CURRENT pipeline so the usage meter
# reflects what actually runs today, not just what has ever run. ACTIVE = a live
# generator wraps tracked_create with this call_site (audited 2026-07-10: the
# `call_site=` set across app/services); RETIRED = no live code emits it anymore,
# so it only appears in historical rows. Keep in sync when a stage is added,
# renamed, or removed (grep `call_site=` to re-audit).
_ACTIVE_CALL_SITES = {
    "listener_effects_prose": "Listener effects prose",
    "societal_effects_prose": "Societal effects prose",
    "psyche_facts":           "Psyche Facts prescription",
    "ether_tagger":           "Ether tagger (topics)",
    "identity_guard":         "Identity + commercial guard",
    "prose_judge":            "Prose semantic judge",
    "resonance_slicer":       "Audience resonance slicer",
    "leit_sweep":             "LEIT clutter sweep",
}
_RETIRED_CALL_SITES = {
    "calibrator":          "In-process calibrator (retired; scoring moved to LEC over HTTP)",
    "editorial_summary":   "Editorial (retired; now terminal-supplied, no server gen path)",
    "effects_prose":       "Listener effects prose (old name; renamed listener_effects_prose)",
    "satire_recalibrator": "Satire recalibrator (legacy; no live generator)",
    "album_synthesis":     "Album synthesis (retired 2026-08-21 with the Album Charger; albums read by the rc-album lens)",
}


def _site_meta(call_site: str) -> dict:
    """Return {status, label, note} for a call_site against the registry above.
    Unknown call_sites (neither active nor retired) are flagged so a newly-added
    stage that was not registered here shows up as 'unclassified' rather than
    silently reading as current."""
    if call_site in _ACTIVE_CALL_SITES:
        return {"status": "active", "label": _ACTIVE_CALL_SITES[call_site], "note": None}
    if call_site in _RETIRED_CALL_SITES:
        return {"status": "retired", "label": call_site, "note": _RETIRED_CALL_SITES[call_site]}
    return {"status": "unknown", "label": call_site, "note": None}


@router.get("/summary", dependencies=[Depends(verify_admin_key)])
def usage_summary(days: int = Query(30, ge=1, le=365),
                  tz: str = Query(None, max_length=64),
                  db: Session = Depends(get_db)):
    """Aggregate spend over the trailing N days.

    Returns:
      - totals: calls, tokens, total_cost_usd
      - by_call_site: ordered by total_cost_usd desc
      - by_model: ordered by total_cost_usd desc
      - daily: total_cost_usd + calls per UTC day, ascending by date
      - all_time_cost_usd: sum across the full table (cheap; one row count)
    """
    since = _resolve_since(days)
    day_expr = _day_expr(tz)

    totals_row = (
        db.query(
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.cache_creation_tokens), 0).label("cache_creation_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.cache_read_tokens), 0).label("cache_read_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("total_cost_usd"),
            func.sum(case((ClaudeApiUsage.ok == 0, 1), else_=0)).label("error_calls"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .first()
    )

    by_site_rows = (
        db.query(
            ClaudeApiUsage.call_site,
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(ClaudeApiUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.output_tokens), 0).label("output_tokens"),
            func.avg(ClaudeApiUsage.total_cost_usd).label("avg_cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by(ClaudeApiUsage.call_site)
        .order_by(desc("cost_usd"))
        .all()
    )

    by_model_rows = (
        db.query(
            ClaudeApiUsage.model,
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by(ClaudeApiUsage.model)
        .order_by(desc("cost_usd"))
        .all()
    )

    daily_rows = (
        db.query(
            day_expr.label("day"),
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by("day")
        .order_by("day")
        .all()
    )

    daily_site_rows = (
        db.query(
            day_expr.label("day"),
            ClaudeApiUsage.call_site,
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by("day", ClaudeApiUsage.call_site)
        .order_by("day")
        .all()
    )

    all_time_cost = (
        db.query(func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0)).scalar() or 0.0
    )
    all_time_calls = db.query(func.count(ClaudeApiUsage.id)).scalar() or 0

    return {
        "since": since.isoformat(),
        "days": days,
        "totals": {
            "calls": int(totals_row.calls or 0),
            "error_calls": int(totals_row.error_calls or 0),
            "input_tokens": int(totals_row.input_tokens or 0),
            "output_tokens": int(totals_row.output_tokens or 0),
            "cache_creation_tokens": int(totals_row.cache_creation_tokens or 0),
            "cache_read_tokens": int(totals_row.cache_read_tokens or 0),
            "total_cost_usd": round(float(totals_row.total_cost_usd or 0.0), 6),
        },
        "by_call_site": [
            {
                "call_site": r.call_site,
                **_site_meta(r.call_site),
                "calls": int(r.calls),
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "avg_cost_usd": round(float(r.avg_cost_usd or 0.0), 6),
            }
            for r in by_site_rows
        ],
        # Current-pipeline lens over this window: how much of the spend is live
        # pipeline vs. retired stages, and which live stages logged nothing (e.g.
        # a just-shipped stage before any traffic, or a dark/low-volume lane).
        "pipeline": {
            "active_cost_usd": round(
                sum(float(r.cost_usd or 0.0) for r in by_site_rows
                    if r.call_site in _ACTIVE_CALL_SITES), 6),
            "retired_cost_usd": round(
                sum(float(r.cost_usd or 0.0) for r in by_site_rows
                    if r.call_site in _RETIRED_CALL_SITES), 6),
            "active_idle": sorted(
                set(_ACTIVE_CALL_SITES) - {r.call_site for r in by_site_rows}),
        },
        "by_model": [
            {
                "model": r.model,
                "calls": int(r.calls),
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
            }
            for r in by_model_rows
        ],
        "daily": [
            {
                "day": r.day,
                "calls": int(r.calls),
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
            }
            for r in daily_rows
        ],
        "daily_by_call_site": [
            {
                "day": r.day,
                "call_site": r.call_site,
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
            }
            for r in daily_site_rows
        ],
        "all_time": {
            "calls": int(all_time_calls),
            "total_cost_usd": round(float(all_time_cost), 6),
        },
    }


@router.get("/cache-advisor", dependencies=[Depends(verify_admin_key)])
def cache_advisor_status(db: Session = Depends(get_db)):
    """Live state of the prompt-cache advisor: would caching pay off right now,
    and has the one-time nudge already been sent. See app/services/cache_advisor.py."""
    from app.services import cache_advisor
    return cache_advisor.status(db)


@router.post("/cache-advisor/reset", dependencies=[Depends(verify_admin_key)])
def cache_advisor_reset(db: Session = Depends(get_db)):
    """Re-arm the nudge: clear the 'already notified' flag so it can fire again."""
    from app.services import cache_advisor
    cleared = cache_advisor.reset_notification(db)
    return {"reset": cleared}


@router.post("/cache-advisor/run", dependencies=[Depends(verify_admin_key)])
def cache_advisor_run(db: Session = Depends(get_db)):
    """Run the check on demand (instead of waiting for the daily cron). Sends
    the email if thresholds are met and it hasn't already fired. Returns the
    resulting status so the UI can refresh."""
    from app.services import cache_advisor
    cache_advisor.evaluate_and_notify()
    return cache_advisor.status(db)


@router.get("/calls", dependencies=[Depends(verify_admin_key)])
def list_calls(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    call_site: str | None = None,
    model: str | None = None,
    days: int = Query(30, ge=1, le=365),
    only_errors: bool = False,
    db: Session = Depends(get_db),
):
    """Paginated list of recent Claude API calls. Newest first."""
    since = _resolve_since(days)

    q = db.query(ClaudeApiUsage).filter(ClaudeApiUsage.ts >= since)
    if call_site:
        q = q.filter(ClaudeApiUsage.call_site == call_site)
    if model:
        q = q.filter(ClaudeApiUsage.model == model)
    if only_errors:
        q = q.filter(ClaudeApiUsage.ok == 0)

    total = q.count()
    rows = (
        q.order_by(desc(ClaudeApiUsage.ts))
        .offset(offset)
        .limit(limit)
        .all()
    )

    def _row(r: ClaudeApiUsage) -> dict:
        ctx = None
        if r.context_json:
            try:
                ctx = json.loads(r.context_json)
            except Exception:
                logger.debug("claude_usage_admin: swallowed in _row", exc_info=True)
                ctx = None
        return {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "call_site": r.call_site,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_creation_tokens": r.cache_creation_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "input_cost_usd": r.input_cost_usd,
            "output_cost_usd": r.output_cost_usd,
            "cache_creation_cost_usd": r.cache_creation_cost_usd,
            "cache_read_cost_usd": r.cache_read_cost_usd,
            "total_cost_usd": r.total_cost_usd,
            "duration_ms": r.duration_ms,
            "stop_reason": r.stop_reason,
            "ok": bool(r.ok),
            "error": r.error,
            "pricing_source": r.pricing_source,
            "context": ctx,
        }

    calls = [_row(r) for r in rows]
    _attach_song_links(calls, db)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "calls": calls,
    }
